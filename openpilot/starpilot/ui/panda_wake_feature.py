from panda import FW_PATH
from openpilot.common.hardware import HARDWARE
from openpilot.starpilot.panda_wake import firmware_ready, resolve, snapshot, supported_keys
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.ui.feature_settings_state import FeatureRow
from openpilot.starpilot.vehicle_selection import read_selection


LABELS = {
  'RemoteStartBootsComma': ('Remote Start Boots Comma', 'Wake comma during GM remote start.'),
  'HKGRemoteStartBootsComma': ('Remote Climate Boots Comma', 'Wake comma during Hyundai, Kia or Genesis remote climate.'),
  'IgnoreIgnitionLine': ('Use CAN Ignition Only', 'Use GM CAN ignition instead of the harness ignition signal.'),
  'TeslaWakeOnCAN': ('Wake Comma with Tesla', 'Wake comma when your Tesla wakes up, before you press the brake.'),
}


class PandaWakeFeature:
  def __init__(self, owner, *, galaxy, firmware_directory=FW_PATH, device_type=lambda: HARDWARE.get_device_type()):
    self.owner = owner
    self.galaxy = galaxy
    self.firmware_directory = firmware_directory
    self.device_type = device_type

  def capability(self):
    cp = self.owner.vehicle_params()
    device = self.device_type()
    keys = supported_keys(cp) if self.galaxy() and device in ('tici', 'tizi', 'mici') else ()
    if device == 'mici':
      keys = tuple(key for key in keys if key == 'TeslaWakeOnCAN')
    selection = read_selection(self.owner.params)
    if not keys or not selection.readable or not selection.valid or selection.platform is None:
      return None
    if selection.platform is not None and selection.platform != cp.carFingerprint:
      return None
    return (str(cp.carFingerprint), str(cp.brand), int(cp.flags), bool(cp.openpilotLongitudinalControl), selection.raw, keys)

  def rows(self, parked):
    binding = self.capability()
    if binding is None:
      return ()
    sources = snapshot(self.owner.params)
    valid = all(readable and raw in (None, b'0', b'1') for _, raw, readable in sources)
    rows = []
    for key in binding[-1]:
      raw = {name: value for name, value, _ in sources}[key]
      label, reason = LABELS[key]
      proposed = tuple((name, b'0' if raw == b'1' else b'1', readable) if name == key else (name, value, readable)
                       for name, value, readable in sources)
      ready = valid and firmware_ready(self.firmware_directory, resolve(self.owner.vehicle_params(), proposed))
      rows.append(FeatureRow(key, label, ('On' if raw == b'1' else 'Off') if valid else 'Invalid saved choice',
                             source=raw, choices=('Off', 'On') if valid else (), default_value='Off', capability=binding,
                             dependencies=tuple((name, value) for name, value, _ in sources if name != key),
                             available=parked and self.owner.authority('parked_preferences') and ready,
                             reason=(reason + ' Applies after a device restart.') if ready else
                             'Invalid saved choice' if not valid else 'Required Panda firmware is unavailable.'))
    return tuple(rows)

  def apply(self, request):
    binding = self.capability()
    if (binding is None or request.key not in binding[-1] or request.value not in ('Off', 'On') or
        not request.confirmation or request.expected not in (None, b'0', b'1') or
        request.related_source is not None or request.display_unit or request.direction):
      return False
    sources = snapshot(self.owner.params)
    dependencies = tuple((name, raw) for name, raw, _ in sources if name != request.key)
    if request.dependencies != dependencies or any(not readable or raw not in (None, b'0', b'1') for _, raw, readable in sources):
      return False
    desired = b'1' if request.value == 'On' else b'0'
    proposed = tuple((name, desired, readable) if name == request.key else (name, raw, readable)
                     for name, raw, readable in sources)

    def authorized():
      return (self.owner.authority('parked_preferences') and request.capability is not None and
              request.capability == self.capability() and bool(request.vehicle_fingerprint) and
              self.owner.vehicle_fingerprint() == request.vehicle_fingerprint and snapshot(self.owner.params) == sources and
              firmware_ready(self.firmware_directory, resolve(self.owner.vehicle_params(), proposed)))

    result = commit_exact(self.owner.params, key=request.key, max_bytes=8, expected=request.expected,
                          raw=desired, authorized=authorized, temp_prefix='.panda-wake-')
    return result.committed and result.verified
