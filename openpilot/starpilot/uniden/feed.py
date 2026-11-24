"""Bounded polling and mute delivery off the rendering thread; no queued command replay."""
import threading
import time
from typing import Any

from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.uniden.protocol import alert_key

ENDPOINT = '/api/bluetooth/uniden'
OFFLINE_S = 10
NOTICE_S = 2.5
HOLD_S = .75     # a failed or empty read this soon after alerts keeps them, so one bad poll does not blink the card and frame
FAILURES = (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError)


def drive_context():
  """Position, speed and posted limit from the live services; None until there is a fix."""
  import capnp
  from openpilot.cereal import messaging
  services = (messaging.IpcError, capnp.KjException)
  try:
    sm = messaging.SubMaster(['gpsLocationExternal', 'carState', 'slcState'])
  except services as error:
    raise OSError(str(error)) from error
  fix = {}

  def read():
    try:
      sm.update(0)
      return current()
    except services as error:
      raise OSError(str(error)) from error

  def current():
    if sm.updated['gpsLocationExternal'] and sm['gpsLocationExternal'].hasFix:
      gps = sm['gpsLocationExternal']
      fix.update(lat=gps.latitude, lon=gps.longitude, heading=gps.bearingDeg, speed=gps.speed, at=time.monotonic())
    if not fix or not sm.alive['gpsLocationExternal']:
      return None
    limit = sm['slcState']
    return {**fix, 'speed': sm['carState'].vEgo if sm.alive['carState'] else fix['speed'],
            'limit': limit.speedLimit if sm.alive['slcState'] and limit.observationKind == 'valid' and limit.speedLimit > 0 else None}
  return read


class DetectorFeed:
  def __init__(self, call=None, clock=time.monotonic, context=None):
    self.call, self.clock, self.context = call, clock, context
    self.lock = threading.Lock()
    self.stop = threading.Event()
    self.thread = None
    self.enabled = False
    self.requested_at = 0
    self.live_at = 0
    self.alerts_at = None
    self.state: dict[str, Any] = {}
    self.pending = None
    self.notice = ('', 0)
    self.logged = None

  def set_enabled(self, enabled):
    with self.lock:
      if enabled and not self.enabled:
        self.live_at = self.clock()
      self.enabled = enabled
      self.requested_at = self.clock()
      if not enabled:
        self.state, self.pending = {}, None
      if enabled and self.thread is None:
        self.thread = threading.Thread(target=self.run, name='uniden-display', daemon=True)
        self.thread.start()

  def snapshot(self) -> dict[str, Any]:
    with self.lock:
      state: dict[str, Any] = self.state
      notice, live_at = self.notice, self.live_at
    at = state.get('observed_at', -1e9)
    if state.get('connected') and not 0 <= self.clock() - at < 3:
      state = {**state, 'connected': False, 'alerts': [], 'error': 'Detector data unavailable'}
    if self.clock() < notice[1]:
      state = {**state, 'notice': notice[0]}
    if state.get('config', {}).get('enabled') and not state.get('connected') and self.enabled and self.clock() - live_at >= OFFLINE_S:
      state = {**state, 'offline': True}
    return state

  def mute(self, state):
    with self.lock:
      if self.enabled and self.pending is None and state.get('connected') and state.get('can_mute') and state.get('alerts'):
        self.pending = self.request('mute', state)

  def lock_here(self, state):
    with self.lock:
      if self.enabled and self.pending is None and state.get('connected') and state.get('can_mute') and state.get('alerts'):
        self.pending = self.request('lock', state)

  @staticmethod
  def request(operation, state):
    return {'operation': operation, 'address': state['address'], 'observed_at': state['observed_at'], 'alerts': alert_key(state['alerts'])}

  def _log(self, where, error):
    if self.logged != (where, str(error)):
      self.logged = (where, str(error))
      cloudlog.exception(f'uniden feed: {where} failed')

  def drive(self):
    if self.context is None:
      try:
        self.context = drive_context()
      except FAILURES as error:
        self._log('drive context', error)
        self.context = lambda: None
    try:
      return self.context()
    except FAILURES as error:
      self._log('drive context', error)
      return None

  def poll(self):
    with self.lock:
      if not self.enabled or self.clock() - self.requested_at > 1:
        self.state, self.pending = {}, None
        return
      payload, self.pending = self.pending, None
    if payload is None:
      payload = {'operation': 'status'}
      if (context := self.drive()) is not None:
        payload['context'] = context
    try:
      if self.call is None:
        from openpilot.starpilot.galaxy_bluetooth.phone_ui import galaxy_call
        self.call = galaxy_call()
      status, result = self.call('POST', ENDPOINT, payload)
      if status != 200:
        raise RuntimeError(result.get('error', 'Detector unavailable'))
    except FAILURES as error:
      self._log(payload['operation'], error)
      result = {'connected': False, 'alerts': [], 'error': str(error)[:100]}
      if payload['operation'] == 'lock':
        with self.lock:
          self.notice = (str(error)[:40] or 'Could not lock out', self.clock() + NOTICE_S)
        return
    if payload['operation'] == 'lock':
      with self.lock:
        self.notice = (f"Locked out here ({result.get('locked', 0)})", self.clock() + NOTICE_S)
      return
    with self.lock:
      if self.enabled:
        if result.get('connected') and result.get('alerts'):
          self.alerts_at = self.clock()
        elif self.alerts_at is not None and self.clock() - self.alerts_at < HOLD_S and self.state.get('alerts'):
          return
        else:
          self.alerts_at = None
        self.state = {**result, 'config': result.get('config', self.state.get('config', {}))}
        if result.get('connected'):
          self.live_at = self.clock()

  def run(self):
    while not self.stop.is_set():
      self.poll()
      self.stop.wait(0.1 if self.state.get('config', {}).get('enabled') else 1.0)

  def close(self):
    self.set_enabled(False)
    self.stop.set()
    if self.thread is not None:
      self.thread.join(timeout=1)
