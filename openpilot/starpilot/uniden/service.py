"""Configuration and request admission shared by Galaxy and both onroad displays."""
import math
import threading
import time
from datetime import UTC, datetime

from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.bluetooth.owner import BluetoothRejected, BluetoothUnavailable
from openpilot.starpilot.uniden.locks import LEARN_COUNT, LockStore, distance_m, matches_lock, new_lock, same_signal
from openpilot.starpilot.uniden.policy import fresh_context
from openpilot.starpilot.uniden.protocol import DEFAULT_CONFIG, LOCK_BANDS, alert_key, normalize_config
from openpilot.starpilot.uniden.tracker import PassTracker

CONTEXT_KEYS = {'lat', 'lon', 'heading', 'speed', 'limit', 'at'}
PASS_AGE_S = 120
NEAR_M = 2000
REQUIRED = {'configure': {'config'}, 'mute': {'address', 'observed_at', 'alerts'}, 'lock': {'address', 'observed_at', 'alerts'},
            'unlock': {'id'}}


def clean_context(context):
  if context is None:
    return None
  if type(context) is not dict or set(context) != CONTEXT_KEYS:
    raise ValueError('Invalid location context')
  def finite(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high
  if not (finite(context['lat'], -90, 90) and finite(context['lon'], -180, 180) and finite(context['speed'], 0, 120) and
          finite(context['at'], 0, 1e12) and (context['heading'] is None or finite(context['heading'], 0, 360)) and
          (context['limit'] is None or finite(context['limit'], 0, 120))):
    raise ValueError('Invalid location context')
  return context


class DetectorService:
  def __init__(self, params, parked, store=None, clock=time.monotonic, wall=lambda: datetime.now(UTC).timestamp()):
    self.params, self.parked = params, parked
    self.store, self.clock, self.wall = store or LockStore(), clock, wall
    self.tracker, self.last, self.passes, self.recent = PassTracker(), None, [], None
    self.lock = threading.Lock()

  def config(self):
    try:
      return normalize_config(self.params.get('UnidenConfig') or DEFAULT_CONFIG)
    except (TypeError, ValueError):
      return dict(DEFAULT_CONFIG)

  def metric(self):
    try:
      return self.params.get_bool('IsMetric') is True
    except (AttributeError, TypeError, ValueError):
      return False

  def situation(self, config, context):
    quiet = None
    if config['quiet_ride']:
      quiet = {'bands': config['quiet_bands'], 'margin': config['quiet_margin'] * (1 / 3.6 if self.metric() else .44704)}
    here = fresh_context(context, self.clock())
    near = [] if here is None else [lock for lock in self.store.active() if
                                     distance_m(here['lat'], here['lon'], lock['lat'], lock['lon']) <= NEAR_M + lock['radius_m']]
    timed = None
    if config['timed_mute']:
      timed = {'bands': config['timed_mute_bands'], 'seconds': config['timed_mute_seconds'],
               'strength': config['timed_mute_strength'], 'settings': config['timed_mute_settings']}
    return {'context': context, 'locks': near, 'quiet': quiet, 'timed': timed}

  def observe(self, result, config, context):
    """Follow passes of fixed sources; this runs for every reader but only a distinct pass counts."""
    if not result.get('connected'):
      return
    now, wall = self.clock(), self.wall()
    self.last = {'address': config['address'], 'alerts': result['alerts'], 'observed_at': result.get('observed_at'),
                 'context': fresh_context(context, now)}
    try:
      for event in self.tracker.update(result['alerts'], context, now, wall):
        self.passes = [p for p in self.passes if wall - p['at'] < PASS_AGE_S] + [event]
        self.store.recentre(event, wall, PASS_AGE_S)
        self.store.observe(event, learn=config['auto_learn'], bands=config['learn_bands'])
      self.store.visit(result['alerts'], fresh_context(context, now))
    except OSError:
      cloudlog.exception('uniden: could not save lockout learning')

  def listing(self):
    # Sources still being learned, closest to a lockout first; position stays on the device.
    learning = sorted(({'id': c['id'], 'band': c['band'], 'frequency': c['frequency'], 'count': c['count'],
                        'need': LEARN_COUNT[c['band']], 'misses': c['misses']} for c in self.store.candidates),
                      key=lambda c: (-c['count'] / c['need'], c['misses']))
    return {'locks': [dict(lock) for lock in self.store.locks], 'candidates': len(self.store.candidates), 'learning': learning,
            'metric': self.metric()}

  def lock_here(self, payload, config):
    last, now = self.last, self.clock()
    if not config['enabled'] or last is None or payload['address'] != config['address'] or last['address'] != config['address']:
      raise BluetoothRejected('Selected detector changed', code='changed')
    at = payload['observed_at']
    if type(at) not in (float, int) or not 0 <= now - at < 3 or payload['alerts'] != alert_key(last['alerts']):
      raise BluetoothRejected('Alert changed; hold the current alert', code='changed')
    context = last['context']
    if context is None:
      raise BluetoothRejected('Waiting for a GPS fix', code='no_fix')
    wall, added = self.wall(), 0
    for alert in last['alerts']:
      if alert['band'] not in LOCK_BANDS or alert.get('filtered') or any(matches_lock(alert, lock, context) for lock in self.store.locks):
        continue
      signal = {'band': alert['band'], 'frequency': alert['frequency']}
      near = next((p for p in reversed(self.passes) if same_signal(p, signal) and wall - p['at'] < PASS_AGE_S), None)
      here = near or context
      self.store.add(new_lock('tap', {**signal, 'lat': here['lat'], 'lon': here['lon']}, wall))
      added += 1
    if not added:
      raise BluetoothRejected('Already locked out here', code='changed')
    return {**self.listing(), 'locked': added}

  def request(self, payload, exchange, *, valid=lambda: True):
    if type(payload) is not dict or payload.get('operation') not in ('status', 'configure', 'mute', 'lock', 'locks', 'unlock'):
      raise ValueError('Invalid detector request')
    operation = payload['operation']
    required, keys = REQUIRED.get(operation, set()), set(payload) - {'operation'}
    if not required <= keys or keys - required - ({'context'} if operation == 'status' else set()):
      raise ValueError('Invalid detector request fields')
    context = clean_context(payload.get('context'))
    if context is not None:
      self.recent = context
    elif operation in ('status', 'mute'):
      context = fresh_context(self.recent, self.clock())
    with self.lock:
      if not valid():
        raise BluetoothRejected('Session expired', code='session_expired')
      config = self.config()
      if operation == 'locks':
        return self.listing()
      if operation == 'unlock':
        try:
          self.store.remove(payload['id'])
        except ValueError as error:
          raise BluetoothRejected(str(error), code='changed') from None
        return self.listing()
      if operation == 'lock':
        return self.lock_here(payload, config)
      if operation == 'configure':
        if not self.parked():
          raise BluetoothRejected('Park before selecting a detector', code='park_required')
        previous, config = config, normalize_config(payload['config'])
        self.params.put('UnidenConfig', config)
        if config['address'] != previous['address']:
          self.tracker, self.last, self.passes = PassTracker(), None, []
        return {'config': config, 'connected': False, 'alerts': []}
      if not config['enabled']:
        return {'config': config, 'connected': False, 'alerts': []}
      if operation == 'mute' and payload['address'] != config['address']:
        raise BluetoothRejected('Selected detector changed', code='changed')
      try:
        result = exchange(config, payload if operation == 'mute' else None, self.situation(config, context))
        self.observe(result, config, context)
      except (BluetoothRejected, BluetoothUnavailable, OSError, RuntimeError, ValueError) as error:
        if operation == 'mute':
          raise
        code = getattr(error, 'code', 'unavailable')
        if str(error) in ('org.bluez.Error.Failed', 'org.bluez.Error.InProgress', 'org.bluez.Error.NotReady'):
          code = 'reconnecting'
        elif str(error) in ('org.bluez.Error.NotAuthorized', 'org.bluez.Error.AuthenticationFailed'):
          code = 'detector_pairing_required'
        messages = {
          'reconnecting': 'Reconnecting to detector',
          'radio_off': 'Turn on Bluetooth to connect the detector',
          'busy': 'Waiting for Bluetooth to become available',
          'detector_pairing_required': 'Pair or select the detector in Bluetooth settings',
          'session_expired': 'Sign in to Galaxy again',
        }
        result = {'connected': False, 'alerts': [], 'connection_state': code if code in messages else 'unavailable',
                  'error': messages.get(code, 'Unable to read detector alerts. Check the detector connection.')}

      return {**result, 'config': config}
