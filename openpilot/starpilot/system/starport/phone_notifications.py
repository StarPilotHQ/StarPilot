"""Phone notifications for the car screen. Content only ever lives in the short-lived /dev/shm handoff."""
from collections import deque
import json
import math
import re
import tempfile
import threading
import time
from pathlib import Path

from openpilot.common.utils import atomic_write
from openpilot.starpilot.navigation.phone import boot_offset_ns
from openpilot.starpilot.system.starport import identity as identity_store

PATH = '/api/companion/notification'
TOAST_NS = 8_000_000_000
MAX_AGE_MS = 60_000
MAX_APPS = 64
RECENT = 8
FIELDS = {'source', 'app', 'appName', 'sender', 'text', 'category', 'ageMs'}
SOURCES = ('android', 'ios')
CATEGORIES = ('message', 'call', 'email', 'social', 'other')
APP_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
SETTINGS_PATH = identity_store.DATA_DIR / 'phone_notifications.json'
DEFAULTS = {'enabled': True, 'hideWhileMoving': False}
TEST_TITLE = 'StarPilot test notification'
COMPANION_APPS = ('link.firestar.companion', 'com.starpilot.galaxy')
COMPANION_PREFIXES = ('link.galaxy.bluetooth.',)
TEST_TEXT = {'galaxy': 'Test from Galaxy. Phone messages will show here.',
             'android': 'Test from your Android phone. Notifications are working.',
             'ios': 'Test from your iPhone. Notifications are working.'}


def is_test(payload):
  app = payload['app']
  return payload.get('sender', '').strip() == TEST_TITLE and (app in COMPANION_APPS or app.startswith(COMPANION_PREFIXES))


def handoff_path():
  shm = Path('/dev/shm')
  return (shm if shm.is_dir() else Path(tempfile.gettempdir())) / 'starpilot_phone_notification.json'


def validate_payload(payload):
  if not isinstance(payload, dict) or set(payload) - FIELDS or not {'source', 'app', 'category', 'ageMs'} <= set(payload):
    raise ValueError('Unexpected notification fields')
  if payload['source'] not in SOURCES:
    raise ValueError('Invalid notification source')
  app = payload['app']
  if not isinstance(app, str) or APP_ID.fullmatch(app) is None:
    raise ValueError('Invalid notification app')
  for key, limit in (('appName', 80), ('sender', 120), ('text', 500)):
    if not isinstance(payload.get(key, ''), str) or len(payload.get(key, '')) > limit:
      raise ValueError(f'Invalid notification {key}')
  if not payload.get('sender', '').strip() and not payload.get('text', '').strip():
    raise ValueError('Notification has no sender or text')
  if payload['category'] not in CATEGORIES:
    raise ValueError('Invalid notification category')
  age = payload['ageMs']
  if type(age) not in (int, float) or not math.isfinite(age) or not 0 <= age <= MAX_AGE_MS:
    raise ValueError('Invalid notification age')
  return payload


def _app_entry(value):
  if (not isinstance(value, dict) or set(value) - {'name', 'source', 'messaging', 'allowed'} or
      not isinstance(value.get('name'), str) or len(value['name']) > 80 or value.get('source') not in SOURCES or
      type(value.get('messaging')) is not bool or ('allowed' in value and type(value['allowed']) is not bool)):
    return None
  return {key: value[key] for key in ('name', 'source', 'messaging', 'allowed') if key in value}


def read_settings(path=SETTINGS_PATH):
  """The saved switches and seen apps; defaults for anything missing or invalid."""
  try:
    value = json.loads(Path(path).read_text())
  except (OSError, ValueError):
    value = None
  value = value if isinstance(value, dict) else {}
  result = {key: value[key] if type(value.get(key)) is bool else default for key, default in DEFAULTS.items()}
  apps = value.get('apps')
  apps = apps if isinstance(apps, dict) else {}
  result['apps'] = {}
  for app, entry in apps.items():
    entry = _app_entry(entry)
    if entry is not None and isinstance(app, str) and APP_ID.fullmatch(app) and len(result['apps']) < MAX_APPS:
      result['apps'][app] = entry
  return result


def _read_handoff(path):
  try:
    value = json.loads(Path(path).read_text())
  except (OSError, ValueError):
    return None
  if (isinstance(value, dict) and all(isinstance(value.get(key), str) for key in ('app', 'appName', 'sender', 'text', 'boot')) and
      all(type(value.get(key)) is int for key in ('sequence', 'observed', 'bootOffset'))):
    return value
  return None


class PhoneNotifications:
  def __init__(self, settings_path=None, handoff=None, mono_clock=time.monotonic_ns, boot_id=None, boot_offset=boot_offset_ns,
               timer=threading.Timer):
    self.settings_path = Path(settings_path or SETTINGS_PATH)
    self.handoff = Path(handoff or handoff_path())
    self.clock = mono_clock
    self.boot_id = boot_id or identity_store.boot_id
    self.boot_offset = boot_offset
    self.timer = timer
    self.lock = threading.Lock()
    old = _read_handoff(self.handoff)
    self.sequence = old['sequence'] if old else 0
    # Outcomes only, in memory; never the sender or text.
    self.recent = deque(maxlen=RECENT)

  def _write_handoff(self, value):
    with atomic_write(str(self.handoff), overwrite=True) as handle:
      json.dump(value, handle)

  def _clear(self, sequence=None):
    # Keep the sequence but drop the content once the toast is over.
    with self.lock:
      old = _read_handoff(self.handoff)
      if old is not None and (sequence is None or old['sequence'] == sequence) and old['text'] + old['sender']:
        self._write_handoff({'app': '', 'appName': '', 'sender': '', 'text': '', 'sequence': old['sequence'], 'observed': 0,
                             'boot': '', 'bootOffset': 0})

  def _save(self, settings):
    self.settings_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with atomic_write(str(self.settings_path), overwrite=True) as handle:
      json.dump(settings, handle, allow_nan=False)

  def _record(self, settings, payload):
    apps = settings['apps']
    app, name = payload['app'], payload.get('appName', '').strip()[:80]
    old = apps.get(app)
    entry = dict(old) if old else {'name': (name or app)[:80], 'source': payload['source'], 'messaging': False}
    if name:
      entry['name'] = name
    entry['source'] = payload['source']
    entry['messaging'] = entry['messaging'] or payload['category'] == 'message'
    if entry == old:
      return False
    if old is None and len(apps) >= MAX_APPS:
      oldest = next((key for key, value in apps.items() if 'allowed' not in value), None)
      if oldest is None:
        return False
      del apps[oldest]
    apps[app] = entry
    return True

  def publish(self, payload):
    validate_payload(payload)
    if is_test(payload):
      return self.publish_test(payload['source'], payload['ageMs'])
    with self.lock:
      settings = read_settings(self.settings_path)
      if not settings['enabled']:
        self._note(payload, 'disabled')
        return {'ok': True, 'accepted': False, 'enabled': False}
      if self._record(settings, payload):
        self._save(settings)
      allowed = settings['apps'].get(payload['app'], {}).get('allowed')
      accepted = allowed if allowed is not None else payload['category'] == 'message'
      age_ns = int(payload['ageMs'] * 1e6)
      if accepted and age_ns < TOAST_NS:
        self._show(payload['app'], payload.get('appName', ''), payload.get('sender', ''), payload.get('text', ''), age_ns)
        self._note(payload, 'shown')
      else:
        self._note(payload, 'tooOld' if accepted else 'appOff' if allowed is False else 'notMessage')
        accepted = False
    return {'ok': True, 'accepted': bool(accepted), 'enabled': True}

  def _note(self, payload, outcome):
    app = payload['app']
    self.recent.appendleft({'app': app, 'appName': payload.get('appName', '').strip()[:80] or app, 'source': payload['source'],
                            'outcome': outcome, 'at': self.clock()})

  def publish_test(self, source, age_ms=0):
    """A fixed toast that skips the app list and filter, so a test proves the path without changing choices."""
    if source not in TEST_TEXT:
      raise ValueError('Invalid notification source')
    with self.lock:
      test = {'app': 'starpilot.test', 'appName': 'Test from ' + {'galaxy': 'Galaxy', 'android': 'Android', 'ios': 'iPhone'}[source],
              'source': source}
      if not read_settings(self.settings_path)['enabled']:
        self._note(test, 'disabled')
        return {'ok': True, 'accepted': False, 'enabled': False, 'test': True}
      age_ns = int(age_ms * 1e6)
      if age_ns >= TOAST_NS:
        self._note(test, 'tooOld')
        return {'ok': True, 'accepted': False, 'enabled': True, 'test': True}
      self._show('starpilot.test', 'StarPilot', TEST_TITLE, TEST_TEXT[source], age_ns)
      self._note(test, 'shown')
    return {'ok': True, 'accepted': True, 'enabled': True, 'test': True}

  def _show(self, app, app_name, sender, text, age_ns):
    self.sequence += 1
    self._write_handoff({'app': app, 'appName': app_name, 'sender': sender, 'text': text, 'sequence': self.sequence,
                         'observed': self.clock() - age_ns, 'boot': self.boot_id(), 'bootOffset': self.boot_offset()})
    expiry = self.timer(TOAST_NS / 1e9 + 1, self._clear, args=(self.sequence,))
    expiry.daemon = True
    expiry.start()

  def snapshot(self, content=False):
    """Settings plus live state; sender and text only when content is True (a direct local connection)."""
    with self.lock:
      result = self._snapshot(read_settings(self.settings_path))
      now = self.clock()
      result['recent'] = [{key: row[key] for key in ('app', 'appName', 'source', 'outcome')} |
                          {'ageSeconds': max(0, (now - row['at']) // 1_000_000_000)} for row in self.recent]
    shown = current(now, self.handoff, self.boot_id, self.boot_offset)
    result['showing'] = None if shown is None else {
      'appName': shown['appName'] or shown['app'], 'remainingSeconds': max(1, -(-(shown['observed'] + TOAST_NS - now) // 1_000_000_000)),
      **({'sender': shown['sender'], 'text': shown['text']} if content else {})}
    result['contentVisible'] = bool(content)
    return result

  @staticmethod
  def _snapshot(settings):
    apps = [{'id': app, 'name': entry['name'], 'source': entry['source'], 'messaging': entry['messaging'],
             'allowed': entry.get('allowed', entry['messaging']), 'chosen': 'allowed' in entry}
            for app, entry in settings['apps'].items()]
    apps.sort(key=lambda row: (str(row['name']).casefold(), str(row['id'])))
    return {'enabled': settings['enabled'], 'hideWhileMoving': settings['hideWhileMoving'], 'apps': apps}

  def update(self, patch):
    if not isinstance(patch, dict) or not patch or set(patch) - {'enabled', 'hideWhileMoving', 'apps'}:
      raise ValueError('Invalid notification settings')
    if any(key in patch and type(patch[key]) is not bool for key in DEFAULTS):
      raise ValueError('Invalid notification settings')
    choices = patch.get('apps', {})
    if not isinstance(choices, dict) or len(choices) > MAX_APPS or \
       any(value is not None and type(value) is not bool for value in choices.values()):
      raise ValueError('Invalid notification app choice')
    with self.lock:
      settings = read_settings(self.settings_path)
      if any(app not in settings['apps'] for app in choices):
        raise ValueError('Unknown notification app')
      before = json.dumps(settings, sort_keys=True)
      for key in DEFAULTS:
        if key in patch:
          settings[key] = patch[key]
      for app, value in choices.items():
        if value is None:
          settings['apps'][app].pop('allowed', None)
        else:
          settings['apps'][app]['allowed'] = value
      if json.dumps(settings, sort_keys=True) != before:
        self._save(settings)
      snapshot = self._snapshot(settings)
    if not settings['enabled']:
      self._clear()
    return snapshot


def current(now_ns=None, path=None, boot_id=None, boot_offset=boot_offset_ns):
  """The latest accepted notification while it is at most TOAST_NS old, else None."""
  now_ns = time.monotonic_ns() if now_ns is None else now_ns
  value = _read_handoff(path or handoff_path())
  if (value is None or value['boot'] != (boot_id or identity_store.boot_id)() or
      abs(value['bootOffset'] - boot_offset()) > 1_000_000_000 or not 0 < value['observed'] <= now_ns <= value['observed'] + TOAST_NS):
    return None
  return {key: value[key] for key in ('app', 'appName', 'sender', 'text', 'sequence', 'observed')}
