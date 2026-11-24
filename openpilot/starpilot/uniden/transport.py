"""GATT operations admitted by the existing Bluetooth owner, including its AA lease."""
import time

from openpilot.starpilot.bluetooth.owner import DEVICE, BluetoothOwner, BluetoothRejected
from openpilot.starpilot.uniden.locks import normalize_lock
from openpilot.starpilot.uniden.policy import apply
from openpilot.starpilot.uniden.protocol import ALERT_UUID, WRITE_UUID, GATT, alert_key, model_for_name, parse_alerts, normalize_rules

STALE_S = 5


class DetectorState:
  """Per-detector exchange memory shared by both displays; replaced when the detector changes or goes unused."""

  def __init__(self, address, now):
    self.address, self.used_at = address, now
    self.retry_at = 0.
    self.seen = None
    self.auto_mute = (None, 0.)
    self.timed_mute = False
    self.unmute = (None, 0, 0.)


def detector_state(owner, address):
  now, state = time.monotonic(), getattr(owner, 'uniden', None)
  if state is None or state.address != address or now - state.used_at > STALE_S:
    state = owner.uniden = DetectorState(address, now)
  state.used_at = now
  return state


def exchange(owner, address, model, session, *, mute=None, auto_mute=False, frequency_rules=None, situation=None):
  rules = normalize_rules([] if frequency_rules is None else frequency_rules)
  situation = {**(situation or {})}
  situation['locks'] = [normalize_lock(lock) for lock in situation.get('locks', [])]
  if type(auto_mute) is not bool:
    raise ValueError('Invalid automatic mute setting')
  if model not in ('R4', 'R8'):
    raise ValueError('Unsupported detector model')
  if not owner.lock.acquire(blocking=False):
    raise BluetoothRejected('Bluetooth is busy', code='busy')
  admitted = False
  try:
    lease = getattr(owner, 'phone_lease', None)
    def validate():
      if not owner.session_valid(session):
        raise BluetoothRejected('Session expired', code='session_expired')
      if BluetoothOwner._admission_busy(owner):
        raise BluetoothRejected('Finish Bluetooth discovery or pairing first', code='busy')
      if lease is not None and (owner.phone_lease is not lease or lease.released or lease.session is not None or
                               lease.enabled is None or not lease.enabled() or lease.selected_receiver is None or
                               lease.selected_receiver().upper() != lease.receiver or address == lease.receiver):
        raise BluetoothRejected('Android Auto Bluetooth owner changed', code='busy')
    validate()
    if lease is None:
      owner._admission_enter()
      admitted = True
    client = owner._client()
    adapter, props, sender = client.adapter_identity()
    if props.get('Powered') is not True:
      raise BluetoothRejected('Turn on Bluetooth to connect the detector', code='radio_off')
    if props.get('Discovering') is True:
      raise BluetoothRejected('Finish Bluetooth discovery before connecting the detector', code='busy')
    if lease is not None and (adapter, sender) != (lease.adapter_path, lease.bluez_sender):
      raise BluetoothRejected('Android Auto adapter changed', code='changed')
    objects = client._objects()
    device = next(((p, i[DEVICE]) for p, i in objects.items() if DEVICE in i and
                   i[DEVICE].get('Address', '').upper() == address and p.startswith(adapter + '/')), None)
    if device is None:
      raise BluetoothRejected('Pair the selected detector first', code='detector_pairing_required')
    path, props = device
    if props.get('Paired') is not True or props.get('Blocked') is True or model_for_name(props.get('Name')) != model:
      raise BluetoothRejected('Choose a paired detector with the matching model', code='detector_pairing_required')
    state = detector_state(owner, address)
    if props.get('Connected') is not True:
      if mute is not None:
        raise BluetoothRejected('Detector disconnected; mute was not sent', code='changed')
      if time.monotonic() < state.retry_at:
        raise BluetoothRejected('Reconnecting to detector', code='reconnecting')
      state.retry_at = time.monotonic() + 15
      validate()
      try:
        client._call(path, DEVICE, 'Connect', timeout=3.0)
      except (BluetoothRejected, OSError) as error:
        if str(error) in ('org.bluez.Error.NotAuthorized', 'org.bluez.Error.AuthenticationFailed'):
          raise BluetoothRejected('Pair the detector again in Bluetooth settings', code='detector_pairing_required') from error
        raise BluetoothRejected('Reconnecting to detector', code='reconnecting') from error
      raise BluetoothRejected('Reconnecting to detector', code='reconnecting')
    if props.get('ServicesResolved') is not True:
      raise BluetoothRejected('Reconnecting to detector', code='reconnecting')
    paths = {p[GATT].get('UUID', '').lower(): name for name, p in objects.items() if name.startswith(path + '/') and GATT in p}
    if ALERT_UUID not in paths:
      raise BluetoothRejected('Detector alert characteristic is unavailable', code='changed')
    def read():
      return parse_alerts(client._call(paths[ALERT_UUID], GATT, 'ReadValue', 'a{sv}', ({},), timeout=2.0)[0], model)
    alerts = read()
    observed_at = time.monotonic()
    validate()
    current_adapter, current_props, current_sender = client.adapter_identity()
    if (current_adapter, current_sender) != (adapter, sender) or current_props.get('Powered') is not True:
      raise BluetoothRejected('Bluetooth adapter changed', code='changed')
    current = client._objects().get(path, {}).get(DEVICE, {})
    if current.get('Paired') is not True or current.get('Connected') is not True:
      raise BluetoothRejected('Detector connection changed', code='changed')
    state.seen = apply(alerts, rules, situation, state.seen, observed_at)
    automatic = bool(alerts) and all((auto_mute and a['filtered']) or a['quiet'] or a['timed'] for a in alerts)
    if not alerts:
      state.timed_mute = False
    # Retry an unacknowledged write at most twice per second, across both displays.
    key = (address, alert_key(alerts))
    last_key, last_at = state.auto_mute
    if not automatic:
      state.auto_mute = (None, 0.)
    automatic = automatic and (key != last_key or observed_at - last_at >= .5)
    command = ''
    if mute is not None:
      at = mute.get('observed_at')
      if type(at) not in (float, int) or not 0 <= time.monotonic() - at < 3 or not alerts or alert_key(alerts) != mute.get('alerts'):
        raise BluetoothRejected('Alert changed; tap the current alert', code='changed')
      if WRITE_UUID not in paths:
        raise BluetoothRejected('Detector mute characteristic is unavailable', code='changed')
    if mute is not None or (automatic and WRITE_UUID in paths):
      if not all(a['muted'] for a in alerts):
        validate()
        client._call(paths[WRITE_UUID], GATT, 'WriteValue', 'aya{sv}',
                     (b'BTreqMUTE:1', {'type': ('s', 'command')}), timeout=2.0)
        if mute is None:
          state.auto_mute = (key, observed_at)
          if any(a['timed'] for a in alerts):
            state.timed_mute = True
        command = 'sent_unconfirmed'
    if state.unmute[0] == key and not any(a['muted'] for a in alerts):
      state.timed_mute = False
    if (situation.get('timed') and mute is None and not command and WRITE_UUID in paths and state.timed_mute and
        any(a['breakthrough'] and a['muted'] and a['mute_mode'] == 'manual' for a in alerts)):
      # Undoes only a timer's mute, capped per alert set.
      last_key, attempts, last_at = state.unmute
      attempts = attempts if last_key == key else 0
      if attempts < 3 and (last_key != key or observed_at - last_at >= 1):
        validate()
        client._call(paths[WRITE_UUID], GATT, 'WriteValue', 'aya{sv}',
                     (b'BTreqMUTE:0', {'type': ('s', 'command')}), timeout=2.0)
        state.unmute = (key, attempts + 1, observed_at)
        command = 'unmute_sent_unconfirmed'
    return {'connected': True, 'address': address, 'model': model, 'alerts': alerts,
            'observed_at': observed_at, 'can_mute': WRITE_UUID in paths, 'command': command}
  finally:
    if admitted:
      owner._admission_leave()
    owner.lock.release()
