"""Private configuration and durable, bounded event delivery outside motion detection.

A successful receiver response is deduplicated durably. An interrupted request can
have been accepted remotely: retries reuse the event/channel Idempotency-Key, but
arbitrary webhooks and ntfy cannot promise exactly-once display.
"""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from urllib.parse import urlsplit
import uuid

import requests

from openpilot.starpilot.storage import starpilot_storage_root
from openpilot.starpilot.sentry_mode.storage import BATTERY_FIELDS, EventStore, valid_tenths, volts
from openpilot.starpilot.sentry_mode import web_push, relay_enrollment
from openpilot.starpilot.sentry_mode.relay_config import DEFAULT_RELAY_URL

CHANNELS = ('webPush', 'discord', 'webhook', 'ntfy', 'appPush')
ALERT_TYPES = ('motion', 'battery')
APP_PUSH_RELAY_URL = ''
APP_IDS = ('com.starpilot.galaxy', 'link.firestar.companion', 'link.galaxy.bluetooth.natehuby76')
MAX_STATE_BYTES = 1024 * 1024
MAX_JOBS = 512
MAX_AGE = 86400
MAX_ATTEMPTS = 4
RETRY_SECONDS = (30, 120, 600, 600)
ID = re.compile(r'[0-9a-f]{32}\Z')
JOB_FIELDS = {'eventId', 'kind', 'channel', 'generation', 'subscriptionId', 'created', 'next', 'attempts', 'state', 'error'}


def alert_kind(kind: str) -> str | None:
  if kind in ('warning', 'alarm'):
    return 'motion'
  if kind == 'battery':
    return 'battery'
  return None


def message_text(job: dict) -> str:
  if job['kind'] == 'test':
    return 'This is a test StarPilot Sentry notification.'
  if job['kind'] == 'battery':
    return f"Car battery is low ({volts(job['voltage'])}). The device and Sentry turn off at {volts(job['cutoff'])}."
  return f"Parked motion detected ({job['kind']})."


class NotificationUnavailable(Exception):
  pass


def endpoint(value: object, *, allow_http: bool = False) -> str:
  if type(value) is not str or not 1 <= len(value) <= 2048 or any(ord(c) <= 32 for c in value):
    raise ValueError('Enter a valid notification URL')
  try:
    parsed = urlsplit(value)
    if (parsed.scheme not in (('https', 'http') if allow_http else ('https',)) or not parsed.hostname or
        parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.port == 0):
      raise ValueError
  except ValueError:
    raise ValueError('Use an HTTPS notification URL') from None
  return value


def fresh_state() -> dict:
  return {'version': 1, 'channels': {channel: {'enabled': False, 'url': '', 'token': '', 'generation': uuid.uuid4().hex,
                                              'since': 0} for channel in CHANNELS},
          'alerts': {alert: {'enabled': True, 'since': 0} for alert in ALERT_TYPES},
          'relayEnrollment': relay_enrollment.fresh(), 'appSubscriptions': [], 'subscriptions': [], 'vapid': '', 'jobs': {}, 'queueFull': False}


def wall_seconds() -> float:
  # Durable retry deadlines and event timestamps must survive a process/device reboot.
  return time.time_ns() / 1e9


class NotificationOwner:
  def __init__(self, root: Path | None = None, store=None, *, clock=wall_seconds, transport=None, allow_http: bool = False):
    self.root = Path(root) if root is not None else starpilot_storage_root() / "sentry/notifications"
    self.store = store if store is not None else EventStore()
    self.clock, self.allow_http = clock, allow_http
    self.transport = transport if transport is not None else self._send
    self.lock = threading.RLock()
    self.stop = threading.Event()
    self.thread = None
    self.state = fresh_state()
    self.available = True
    self.lock_fd = None
    self.saved_bytes = None
    try:
      directory = self._directory()
      try:
        self.lock_fd = os.open('.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        lock_info = os.fstat(self.lock_fd)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid() or stat.S_IMODE(lock_info.st_mode) & 0o077:
          raise OSError('Invalid notification lock')
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
      finally:
        os.close(directory)
      self._load()
    except (OSError, ValueError, TypeError, RecursionError):
      self.available = False
      if self.lock_fd is not None:
        os.close(self.lock_fd)
        self.lock_fd = None

  def _directory(self) -> int:
    # Walk every component without following symlinks. This private state contains credentials.
    if not self.root.is_absolute() or ".." in self.root.parts:
      raise OSError('Private storage requires an absolute directory')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
      for component in self.root.parts[1:]:
        try:
          os.mkdir(component, 0o700, dir_fd=fd)
        except FileExistsError:
          pass
        child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        fd = child
      info = os.fstat(fd)
      if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise OSError('Private storage permissions are invalid')
      return fd
    except BaseException:
      os.close(fd)
      raise

  def _load(self) -> None:
    directory = self._directory()
    try:
      try:
        fd = os.open('state.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
      except FileNotFoundError:
        return
      try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
          raise ValueError('Invalid private notification state')
        raw = os.read(fd, MAX_STATE_BYTES + 1)
      finally:
        os.close(fd)
      if len(raw) > MAX_STATE_BYTES:
        raise ValueError('Notification state exceeds limit')
      def unique(pairs):
        result = {}
        for key, value in pairs:
          if key in result:
            raise ValueError('Duplicate notification state')
          result[key] = value
        return result
      state = json.loads(raw, object_pairs_hook=unique)
      if type(state) is dict and 'appSubscriptions' not in state:
        state['appSubscriptions'] = []
        if type(state.get('channels')) is dict:
          state['channels']['appPush'] = fresh_state()['channels']['appPush']
      if type(state) is dict:
        state.setdefault('alerts', copy.deepcopy(fresh_state()['alerts']))
        state.setdefault('relayEnrollment', relay_enrollment.fresh())
        if type(state['relayEnrollment']) is dict:
          state['relayEnrollment'].setdefault('rejected', {})
      self._validate_state(state)
      self.state = state
      self.saved_bytes = json.dumps(state, separators=(',', ':'), allow_nan=False).encode()
    finally:
      os.close(directory)

  def _validate_state(self, state: dict) -> None:
    relay_enrollment.validate(state.get('relayEnrollment') if isinstance(state, dict) else None)
    if (type(state) is not dict or set(state) != set(fresh_state()) or type(state['version']) is not int or state['version'] != 1 or
        type(state['channels']) is not dict or set(state['channels']) != set(CHANNELS) or
        type(state['alerts']) is not dict or set(state['alerts']) != set(ALERT_TYPES) or
        type(state['subscriptions']) is not list or len(state['subscriptions']) > 16 or
        type(state['jobs']) is not dict or len(state['jobs']) > MAX_JOBS or type(state['queueFull']) is not bool):
      raise ValueError('Invalid notification state')
    for alert in state['alerts'].values():
      if (type(alert) is not dict or set(alert) != {'enabled', 'since'} or type(alert['enabled']) is not bool or
          type(alert['since']) not in (int, float) or not 0 <= alert['since'] < 2**63):
        raise ValueError('Invalid notification alert')
    if type(state['vapid']) is not str or len(state['vapid']) > 4096:
      raise ValueError('Invalid notification push key')
    if state['vapid']:
      web_push.load_key(state['vapid'])
    for name, channel in state['channels'].items():
      if (type(channel) is not dict or set(channel) != {'enabled', 'url', 'token', 'generation', 'since'} or
          type(channel['enabled']) is not bool or type(channel['generation']) is not str or ID.fullmatch(channel['generation']) is None or
          type(channel['since']) not in (int, float) or not 0 <= channel['since'] < 2**63 or
          type(channel['url']) is not str or len(channel['url']) > 2048 or
          type(channel['token']) is not str or len(channel['token']) > 512 or any(ord(c) < 32 for c in channel['token'])):
        raise ValueError('Invalid notification channel')
      if channel['url']:
        endpoint(channel['url'], allow_http=self.allow_http)
      if name not in ('webPush', 'appPush') and channel['enabled'] and not channel['url']:
        raise ValueError('Missing notification endpoint')
    if type(state['appSubscriptions']) is not list or len(state['appSubscriptions']) > 8:
      raise ValueError('Invalid app subscriptions')
    for sub in state['appSubscriptions']:
      if (type(sub) is not dict or set(sub) != {'id', 'token', 'appId', 'label', 'addedAt', 'since', 'lastResult', 'lastAt'} or
          type(sub['token']) is not str or not 1 <= len(sub['token']) <= 4096 or any(ord(c) <= 32 for c in sub['token']) or
          sub['id'] != hashlib.sha256(sub['token'].encode()).hexdigest()[:12] or sub['appId'] not in APP_IDS or
          type(sub['label']) is not str or len(sub['label']) > 64 or
          type(sub['lastResult']) is not str or len(sub['lastResult']) > 64 or
          any(type(sub[key]) not in (int, float) or not 0 <= sub[key] < 2**63 for key in ('addedAt', 'since', 'lastAt'))):
        raise ValueError('Invalid app subscription')
    for sub in state['subscriptions']:
      if type(sub) is not dict or set(sub) != {'id', 'subscription', 'since'} or type(sub['id']) is not str or ID.fullmatch(sub['id']) is None:
        raise ValueError('Invalid browser subscription')
      web_push.validate_subscription(sub['subscription'])
      endpoint(sub['subscription']['endpoint'], allow_http=self.allow_http)
      if type(sub['since']) not in (int, float) or not 0 <= sub['since'] < 2**63:
        raise ValueError('Invalid subscription time')
    for key, job in state['jobs'].items():
      if (type(job) is not dict or set(job) != JOB_FIELDS | (BATTERY_FIELDS if job.get('kind') == 'battery' else set()) or
          (job['kind'] == 'battery' and not all(valid_tenths(job[k]) for k in BATTERY_FIELDS)) or
          type(key) is not str or len(key) != 64 or type(job['eventId']) is not str or ID.fullmatch(job['eventId']) is None or
          job['kind'] not in ('warning', 'alarm', 'battery', 'test') or job['channel'] not in CHANNELS or
          type(job['generation']) is not str or ID.fullmatch(job['generation']) is None or
          (type(job['subscriptionId']) is not str or (re.fullmatch(r'[0-9a-f]{12}' if job['channel'] == 'appPush' else
                                                                  r'[0-9a-f]{32}' if job['channel'] == 'webPush' else r'', job['subscriptionId']) is None)) or
          type(job['attempts']) is not int or not 0 <= job['attempts'] <= MAX_ATTEMPTS or
          job['state'] not in ('queued', 'sending', 'sent', 'failed', 'cancelled') or
          job['error'] not in ('', 'http', 'expired', 'timeout', 'transport', 'delivery_unknown', 'stale') or
          any(type(job[k]) not in (float, int) or not 0 <= job[k] < 2**63 for k in ('created', 'next'))):
        raise ValueError('Invalid notification job')

  def _save(self) -> None:
    raw = json.dumps(self.state, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_STATE_BYTES:
      raise NotificationUnavailable('Notification storage is full')
    if raw == self.saved_bytes:
      return
    directory = self._directory()
    temporary = '.' + uuid.uuid4().hex + '.tmp'
    try:
      fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
      with os.fdopen(fd, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
      os.replace(temporary, 'state.json', src_dir_fd=directory, dst_dir_fd=directory)
      os.fsync(directory)
      self.saved_bytes = raw
    except OSError:
      self.available = False
      raise NotificationUnavailable('Notification storage is unavailable') from None
    finally:
      try:
        os.unlink(temporary, dir_fd=directory)
      except FileNotFoundError:
        pass
      os.close(directory)

  def snapshot(self) -> dict:
    with self.lock:
      if not self.available:
        raise NotificationUnavailable('Notification storage is unavailable')
      jobs = self.state['jobs']
      result = {'schemaVersion': 1, 'channels': {}, 'alerts': {name: cfg['enabled'] for name, cfg in self.state['alerts'].items()},
                'queueFull': self.state['queueFull'],
                'subscriptionCount': len(self.state['subscriptions']),
                'subscriptions': [{'id': sub['id']} for sub in self.state['subscriptions']],
                'deliverySemantics': 'Retries may repeat delivery after an interrupted remote response.'}
      rejected = self.state['relayEnrollment']['rejected']
      result['appSubscriptions'] = [{key: sub[key] for key in ('id', 'label', 'appId', 'addedAt', 'lastResult', 'lastAt')} |
                                    ({'lastResult': 'Relay does not accept this app'} if sub['id'] in rejected else {})
                                    for sub in self.state['appSubscriptions']]
      result['removedAppSubscriptions'] = [
        {'id': job['subscriptionId'], 'lastResult': 'Phone unregistered — removed', 'lastAt': job['next']}
        for job in jobs.values() if job['channel'] == 'appPush' and job['error'] == 'expired'][-8:]
      enrollment = self.state['relayEnrollment']
      result['relayEnrollment'] = {'supported': True, 'automaticConfigured': bool(DEFAULT_RELAY_URL), 'state': enrollment['state'],
                                   'installationId': enrollment['id'], 'registeredCount': len(enrollment['registered'])}
      result['appPushAvailable'] = self.state['channels']['appPush']['enabled'] and bool(self.state['channels']['appPush']['url'] or APP_PUSH_RELAY_URL)
      if enrollment['state'] != 'unenrolled':
        result['appPushAvailable'] = result['appPushAvailable'] and enrollment['state'] == 'enrolled'
      for name, cfg in self.state['channels'].items():
        relevant = [j for j in jobs.values() if j['channel'] == name and j['generation'] == cfg['generation']]
        latest = max(relevant, key=lambda j: j['created'], default=None)
        result['channels'][name] = {'enabled': cfg['enabled'],
                                    'configured': bool(self.state['subscriptions']) if name == 'webPush' else bool(cfg['url']),
                                    'pending': sum(j['state'] in ('queued', 'sending') for j in relevant),
                                    'lastState': latest['state'] if latest else 'idle',
                                    'lastError': latest['error'] if latest else ''}
      if self.state['vapid']:
        result['applicationServerKey'] = web_push.b64(web_push.public(web_push.load_key(self.state['vapid'])))
      return result

  def action(self, payload: dict, *, permitted=lambda: True) -> dict:
    with self.lock:
      if not self.available:
        raise NotificationUnavailable('Notification storage is unavailable')
      if not permitted():
        raise PermissionError('A current Galaxy session is required')
      if type(payload) is not dict or type(payload.get('action')) is not str:
        raise ValueError('Choose a notification action')
      previous = copy.deepcopy(self.state)
      action = payload['action']
      now = self.clock()
      if action == 'relayEnroll':
        automatic = set(payload) == {'action', 'confirmed'}
        if (not automatic and set(payload) != {'action', 'url', 'invitation', 'confirmed'}) or payload.get('confirmed') is not True:
          raise ValueError('Confirm relay enrollment')
        current = self.state['relayEnrollment']
        if automatic and current['state'] in ('enrolling', 'enrolled', 'rotating', 'confirming'):
          return self.snapshot()
        if current['state'] not in ('unenrolled', 'revoked'):
          raise ValueError('Revoke or forget the current enrollment first')
        url = DEFAULT_RELAY_URL if automatic else payload['url']
        if not url:
          raise ValueError('Hosted alerts are not configured in this release yet')
        self.state['relayEnrollment'] = relay_enrollment.begin(url, '' if automatic else payload['invitation'])
        self.state['channels']['appPush'].update(url=self.state['relayEnrollment']['url'], enabled=True, generation=uuid.uuid4().hex, since=now)
      elif action in ('relayRotate', 'relayRevoke', 'relayForget'):
        if set(payload) != {'action', 'confirmed'} or payload['confirmed'] is not True:
          raise ValueError('Confirm the relay action')
        enrollment = self.state['relayEnrollment']
        if action == 'relayForget':
          self.state['relayEnrollment'] = relay_enrollment.fresh()
          self.state['channels']['appPush'] = fresh_state()['channels']['appPush']
        elif action == 'relayRotate':
          if enrollment['state'] != 'enrolled':
            raise ValueError('Enroll the relay before rotating its credential')
          enrollment.update(state='rotating', candidate=secrets.token_hex(32), requestId=uuid.uuid4().hex, next=0, attempts=0)
        elif enrollment['id']:
          enrollment.update(state='revoking', next=0, attempts=0)
          self.state['channels']['appPush']['enabled'] = False
      elif action == 'configure':
        if set(payload) != {'action', 'channel', 'enabled', 'url', 'token'} or payload['channel'] not in CHANNELS:
          raise ValueError('Invalid notification channel')
        name = payload['channel']
        if name == 'appPush' and self.state['relayEnrollment']['state'] != 'unenrolled':
          raise ValueError('Use relay enrollment controls for this channel')
        if (type(payload['enabled']) is not bool or type(payload['token']) is not str or len(payload['token']) > 512 or
            any(ord(c) < 32 for c in payload['token'])):
          raise ValueError('Invalid notification setting')
        url = payload['url']
        if type(url) is not str:
          raise ValueError('Enter a valid notification URL')
        if url:
          endpoint(url, allow_http=self.allow_http)
        if name == 'webPush' and payload['enabled'] and not self.state['subscriptions']:
          raise ValueError('Subscribe a browser first')
        if name == 'webPush' and (url or payload['token']):
          raise ValueError('Browser push uses a browser subscription')
        old = self.state['channels'][name]
        # Empty fields preserve configured credentials; explicit disable does not delete them.
        url = url or old['url']
        token = payload['token'] or old['token']
        if payload['enabled'] and name != 'webPush' and not url:
          raise ValueError('Enter a notification URL')
        self.state['channels'][name] = {'enabled': payload['enabled'], 'url': url, 'token': token,
                                        'generation': uuid.uuid4().hex, 'since': now}
      elif action == 'forget':
        if set(payload) != {'action', 'channel'} or payload['channel'] not in CHANNELS:
          raise ValueError('Choose a notification channel')
        if payload['channel'] == 'appPush' and self.state['relayEnrollment']['state'] != 'unenrolled':
          raise ValueError('Revoke or forget the relay enrollment first')
        self.state['channels'][payload['channel']] = fresh_state()['channels'][payload['channel']]
        if payload['channel'] == 'webPush':
          self.state['subscriptions'] = []
        if payload['channel'] == 'appPush':
          self.state['appSubscriptions'] = []
      elif action == 'pushKey':
        if set(payload) != {'action'}:
          raise ValueError('Invalid push key request')
        if not self.state['vapid']:
          self.state['vapid'] = web_push.new_key()
      elif action == 'subscribe':
        if set(payload) != {'action', 'subscription'} or not self.state['vapid']:
          raise ValueError('Prepare browser push first')
        sub = payload['subscription']
        web_push.validate_subscription(sub)
        endpoint(sub['endpoint'], allow_http=self.allow_http)
        current = self.state['subscriptions']
        existing = next((s for s in current if s['subscription']['endpoint'] == sub['endpoint']), None)
        if existing:
          existing['subscription'] = sub
        elif len(current) < 16:
          current.append({'id': uuid.uuid4().hex, 'subscription': sub, 'since': now})
        else:
          raise ValueError('Remove a browser before adding another')
      elif action == 'unsubscribe':
        if set(payload) != {'action', 'id'} or type(payload['id']) is not str or ID.fullmatch(payload['id']) is None:
          raise ValueError('Choose a browser subscription')
        self.state['subscriptions'] = [s for s in self.state['subscriptions'] if s['id'] != payload['id']]
      elif action == 'appSubscribe':
        if (set(payload) != {'action', 'token', 'appId', 'label'} or type(payload['token']) is not str or
            not 1 <= len(payload['token']) <= 4096 or any(ord(c) <= 32 for c in payload['token']) or
            type(payload['appId']) is not str or payload['appId'] not in APP_IDS or
            type(payload['label']) is not str or len(payload['label']) > 64):
          raise ValueError('Invalid app subscription')
        current = self.state['appSubscriptions']
        existing = next((sub for sub in current if sub['token'] == payload['token']), None)
        self.state['relayEnrollment']['rejected'].pop(hashlib.sha256(payload['token'].encode()).hexdigest()[:12], None)
        if existing:
          existing.update(appId=payload['appId'], label=payload['label'])
        elif len(current) < 8:
          current.append({'id': hashlib.sha256(payload['token'].encode()).hexdigest()[:12], 'token': payload['token'],
                          'appId': payload['appId'], 'label': payload['label'], 'addedAt': now, 'since': now,
                          'lastResult': '', 'lastAt': 0})
        else:
          raise ValueError('Remove a phone before adding another')
        cfg = self.state['channels']['appPush']
        cfg['enabled'] = bool(cfg['url'] or APP_PUSH_RELAY_URL)
      elif action == 'appUnsubscribe':
        if set(payload) not in ({'action', 'token'}, {'action', 'id'}):
          raise ValueError('Choose a phone subscription')
        key = 'token' if 'token' in payload else 'id'
        if type(payload[key]) is not str:
          raise ValueError('Choose a phone subscription')
        self.state['appSubscriptions'] = [sub for sub in self.state['appSubscriptions'] if sub[key] != payload[key]]
      elif action == 'setAlert':
        if (set(payload) != {'action', 'alert', 'enabled'} or
            payload['alert'] not in ALERT_TYPES or type(payload['enabled']) is not bool):
          raise ValueError('Choose a notification alert type')
        alert = payload['alert']
        self.state['alerts'][alert] = {'enabled': payload['enabled'], 'since': now}
        if not payload['enabled']:
          for job in self.state['jobs'].values():
            if alert_kind(job['kind']) == alert and job['state'] in ('queued', 'sending'):
              job['state'] = 'cancelled'
      elif action == 'test' and 'channel' not in payload:
        if set(payload) - {'action', 'target', 'id'} or payload.get('target', 'all') not in ('all', 'app'):
          raise ValueError('Choose a test target')
        target_id = payload.get('id')
        if payload.get('target') == 'app' and not any(sub['id'] == target_id for sub in self.state['appSubscriptions']):
          raise LookupError('Phone subscription not found')
        if sum(j['kind'] == 'test' and j['created'] > now - 60 for j in self.state['jobs'].values()) >= 32:
          raise ValueError('Wait a minute before sending more tests')
        self._enqueue({'eventId': uuid.uuid4().hex, 'kind': 'test', 'wallTimeNs': int(now * 1e9)},
                      only='appPush' if payload.get('target') == 'app' else None, target_id=target_id)
      elif action == 'test':
        if set(payload) != {'action', 'channel'} or payload['channel'] not in CHANNELS:
          raise ValueError('Choose a test channel')
        cfg = self.state['channels'][payload['channel']]
        if not cfg['enabled']:
          raise ValueError('Enable this notification channel first')
        if payload['channel'] == 'webPush' and not self.state['subscriptions']:
          raise ValueError('Subscribe a browser first')
        if sum(j['kind'] == 'test' and j['created'] > now - 60 for j in self.state['jobs'].values()) >= 4:
          raise ValueError('Wait a minute before sending more tests')
        self._enqueue({'eventId': uuid.uuid4().hex, 'kind': 'test', 'wallTimeNs': int(now * 1e9)}, only=payload['channel'])
      else:
        raise ValueError('Choose a supported notification action')
      if not permitted():
        self.state = previous
        raise PermissionError('Galaxy session changed')
      self._save()
      return self.snapshot()

  def _enqueue(self, event: dict, *, only: str | None = None, target_id: str | None = None) -> None:
    if event['kind'] == 'selfie':
      return
    alert = alert_kind(event['kind'])
    if alert is not None and not self.state['alerts'][alert]['enabled']:
      return
    alert_since = self.state['alerts'][alert]['since'] if alert is not None else 0
    now = self.clock()
    for name, cfg in self.state['channels'].items():
      if not cfg['enabled'] or (only is not None and only != name):
        continue
      targets = (self.state['subscriptions'] if name == 'webPush' else self.state['appSubscriptions']
                 if name == 'appPush' else [{'id': '', 'since': cfg['since']}])
      for target in targets:
        if target_id is not None and target['id'] != target_id:
          continue
        if event['kind'] != 'test' and event['wallTimeNs'] / 1e9 <= max(cfg['since'], target['since'], alert_since):
          continue
        key = hashlib.sha256((event['eventId'] + name + cfg['generation'] + target['id']).encode()).hexdigest()
        if key in self.state['jobs']:
          continue
        if len(self.state['jobs']) >= MAX_JOBS:
          self.state['queueFull'] = True
          continue
        self.state['jobs'][key] = {'eventId': event['eventId'], 'kind': event['kind'], 'channel': name,
                                  'generation': cfg['generation'], 'subscriptionId': target['id'],
                                  'created': event['wallTimeNs'] / 1e9, 'next': now, 'attempts': 0,
                                  'state': 'queued', 'error': '',
                                  **({k: event[k] for k in BATTERY_FIELDS} if event['kind'] == 'battery' else {})}

  def delete_events(self, remove, event_id=None):
    with self.lock:
      if not self.available:
        raise NotificationUnavailable('Sentry notifications are unavailable')
      original_jobs = self.state['jobs']
      if event_id is None:
        self.state['jobs'] = {key: job for key, job in original_jobs.items() if job['kind'] == 'test'}
      else:
        self.state['jobs'] = {key: job for key, job in original_jobs.items() if job['eventId'] != event_id}
      try:
        self._save()  # Durable pruning precedes any event/media removal.
      except BaseException:
        self.state['jobs'] = original_jobs
        raise
      return remove()

  def tick(self) -> None:
    with self.lock:
      if not self.available:
        return
      now = self.clock()
      original_enrollment = copy.deepcopy(self.state['relayEnrollment'])
      updated_enrollment = copy.deepcopy(original_enrollment)
      subscriptions = copy.deepcopy(self.state['appSubscriptions'])
      self.lock.release()
      try:
        relay_enrollment.step(updated_enrollment, subscriptions, now)
      finally:
        self.lock.acquire()
      if self.state['relayEnrollment'] == original_enrollment:
        self.state['relayEnrollment'] = updated_enrollment
      self._save()
      self.state['jobs'] = {key: job for key, job in self.state['jobs'].items() if now - job['created'] <= MAX_AGE}
      self.state['queueFull'] = False
      if not any(cfg["enabled"] for cfg in self.state["channels"].values()):
        return
      snapshot = self.store.snapshot()
      for event in snapshot['events']:
        if 0 <= now - event['wallTimeNs'] / 1e9 <= MAX_AGE:
          self._enqueue(event)
      for key, job in self.state['jobs'].items():
        if key not in self.state['jobs']:
          continue
        if job['state'] not in ('queued', 'sending'):
          continue
        cfg = self.state['channels'][job['channel']]
        sub = next((s['subscription'] for s in self.state['subscriptions'] if s['id'] == job['subscriptionId']), None)
        if job['channel'] == 'appPush':
          enrollment = self.state['relayEnrollment']
          if enrollment['state'] != 'unenrolled' and (enrollment['state'] != 'enrolled' or job['subscriptionId'] not in enrollment['registered']):
            continue
          sub = next((s for s in self.state['appSubscriptions'] if s['id'] == job['subscriptionId']), None)
        alert = alert_kind(job['kind'])
        if (not cfg['enabled'] or cfg['generation'] != job['generation'] or
            (alert is not None and not self.state['alerts'][alert]['enabled']) or
            (job['channel'] in ('webPush', 'appPush') and sub is None)):
          job['state'] = 'cancelled'
          continue
        if now < job['created'] or now - job['created'] > MAX_AGE:
          job['state'], job['error'] = 'failed', 'stale'
          continue
        if job['state'] == 'sending':
          job['error'] = 'delivery_unknown'
          job['state'] = 'queued' if job['attempts'] < MAX_ATTEMPTS else 'failed'
          job['next'] = now + RETRY_SECONDS[min(job['attempts'], MAX_ATTEMPTS) - 1]
        if job['state'] != 'queued' or job['next'] > now:
          continue
        job['attempts'] += 1
        job['state'] = 'sending'
        self._save()  # Claim durably before issuing external IO; secrets stay in private config.
        try:
          # Keep status/configuration responsive while network IO is in flight.
          self.lock.release()
          try:
            code = self.transport(job['channel'], dict(cfg), sub, dict(job), key, self.state['vapid'])
          finally:
            self.lock.acquire()
          current_job = self.state['jobs'].get(key)
          if current_job is None:
            continue
          job = current_job
          if job['channel'] == 'appPush':
            current_sub = next((s for s in self.state['appSubscriptions'] if s['id'] == job['subscriptionId']), None)
            if current_sub is not None:
              current_sub['lastAt'] = self.clock()
              current_sub['lastResult'] = ('Delivered to relay' if 200 <= code < 300 else 'Relay not configured' if code == 0 else
                                           'Phone unregistered — removed' if code == 410 else 'Rate limited' if code == 429 else f'Relay error {code}')
            enrollment = self.state['relayEnrollment']
            if enrollment['state'] != 'unenrolled' and code in (401, 403):
              enrollment['state'] = 'actionRequired'
            if enrollment['state'] == 'enrolled' and code == 409:
              enrollment['registered'].pop(job['subscriptionId'], None)
              code = 503
            if code == 410:
              self.state['appSubscriptions'] = [s for s in self.state['appSubscriptions'] if s['id'] != job['subscriptionId']]
          if 200 <= code < 300:
            job['state'], job['error'] = 'sent', ''
          elif code == 410 and job['channel'] == 'appPush':
            job['state'], job['error'], job['next'] = 'failed', 'expired', now
          elif code in (404, 410) and job['channel'] == 'webPush':
            job['state'], job['error'] = 'failed', 'expired'
            self.state['subscriptions'] = [s for s in self.state['subscriptions']
                                           if s['id'] != job['subscriptionId'] or s['subscription'] != sub]
          else:
            job['state'], job['error'] = ('queued' if code in (408, 429) or code >= 500 else 'failed'), 'http'
        except (requests.Timeout, TimeoutError):
          job['state'], job['error'] = 'queued', 'timeout'
          if job['channel'] == 'appPush' and sub is not None:
            sub['lastAt'], sub['lastResult'] = self.clock(), 'Relay timeout'
        except Exception:
          # Never log response bodies, URLs, exception text, subscription secrets or auth headers.
          job['state'], job['error'] = 'queued', 'transport'
          if job['channel'] == 'appPush' and sub is not None:
            sub['lastAt'], sub['lastResult'] = self.clock(), 'Relay unavailable'
        if job['state'] == 'queued':
          if job['attempts'] >= MAX_ATTEMPTS:
            job['state'] = 'failed'
          job['next'] = now + RETRY_SECONDS[job['attempts'] - 1]
        self._save()
        return  # At most one HTTP attempt per tick, per owner.
      self._save()

  def _attachments(self, job: dict) -> list:
    """Camera images saved with the event, one files[n] field each (Discord keeps one file per field name)."""
    if job['kind'] == 'test':
      return []
    files = []
    try:
      cameras = self.store.images(job['eventId'])
    except Exception:
      return []
    for camera in cameras:
      try:
        body = self.store.image(job['eventId'], camera)
      except Exception:
        continue  # A missing or invalid image never blocks the text alert.
      files.append((f'files[{len(files)}]', (camera + '.jpg', body, 'image/jpeg')))
    return files

  def _send(self, name: str, cfg: dict, subscription: dict | None, job: dict, key: str, pem: str) -> int:
    message = message_text(job)
    event = {'eventId': job['eventId'], 'kind': job['kind'], 'systemTimeMs': int(job['created'] * 1000),
             **{k: job[k] for k in BATTERY_FIELDS if k in job}}
    headers = {'Idempotency-Key': key}
    if name == 'appPush':
      if subscription is None:
        raise ValueError('Missing app subscription')
      relay = cfg['url'] or APP_PUSH_RELAY_URL
      if not relay:
        return 0
      protocol = '/v1/sentry'
      with self.lock:
        enrollment = copy.deepcopy(self.state['relayEnrollment'])
      if enrollment['state'] != 'unenrolled':
        if enrollment['state'] != 'enrolled' or relay != enrollment['url']:
          return 401
        protocol = '/v2/sentry'
        headers['Authorization'] = 'Bearer ' + enrollment['id'] + '.' + enrollment['secret']
      response = requests.post(relay.rstrip('/') + protocol, json={
        'token': subscription['token'], 'appId': subscription['appId'], 'kind': job['kind'], 'eventId': job['eventId'],
        'wallTimeNs': int(job['created'] * 1e9), 'label': subscription['label'],
        **({'detail': message} if job['kind'] == 'battery' else {})}, headers=headers,
        timeout=(3, 10), allow_redirects=False, stream=True)
    elif name == 'webPush':
      if subscription is None:
        raise ValueError('Missing browser subscription')
      body, push_headers = web_push.request(subscription, {'title': 'StarPilot Sentry Mode', 'body': message,
                                            'eventId': job['eventId'], 'url': '/#/cameras/events'}, pem)
      headers.update(push_headers)
      response = requests.post(subscription['endpoint'], data=body, headers=headers, timeout=(3, 10), allow_redirects=False, stream=True)
    else:
      if cfg['token']:
        headers['Authorization'] = 'Bearer ' + cfg['token']
      files = self._attachments(job) if name in ('discord', 'webhook') else []
      if name == 'discord':
        content = {'content': 'StarPilot Sentry Mode: ' + message}
        if files:
          # Discord takes multipart attachments with the message in payload_json.
          response = requests.post(cfg['url'], data={'payload_json': json.dumps(content)}, files=files,
                                   headers=headers, timeout=(3, 10), allow_redirects=False, stream=True)
        else:
          response = requests.post(cfg['url'], json=content, headers=headers, timeout=(3, 10), allow_redirects=False, stream=True)
      elif name == 'webhook':
        # Preserve the original general-webhook content/event form contract.
        response = requests.post(cfg['url'], data={'content': 'StarPilot Sentry Mode: ' + message,
                                  'event': json.dumps(event, separators=(',', ':'))}, files=files or None,
                                 headers=headers, timeout=(3, 10), allow_redirects=False, stream=True)
      else:
        headers.update({'Title': 'StarPilot Sentry Mode', 'Priority': 'high' if job['kind'] == 'battery' else 'urgent',
                        'Tags': 'battery,car' if job['kind'] == 'battery' else 'warning,car'})
        response = requests.post(cfg['url'], data=message.encode(), headers=headers, timeout=(3, 10), allow_redirects=False, stream=True)
    try:
      return response.status_code
    finally:
      response.close()

  def start(self) -> None:
    with self.lock:
      if self.thread is not None:
        return
      def run():
        while not self.stop.is_set():
          try:
            self.tick()
          except Exception:
            # Storage/scanner failure is visible, never rewritten as a successful delivery.
            with self.lock:
              self.available = False
            return
          self.stop.wait(2.0)
      self.thread = threading.Thread(target=run, name='sentry-notifications', daemon=True)
      self.thread.start()

  def close(self) -> None:
    self.stop.set()
    if self.thread is not None:
      self.thread.join(timeout=15)
      if self.thread.is_alive():
        return
    if self.lock_fd is not None:
      os.close(self.lock_fd)
      self.lock_fd = None
