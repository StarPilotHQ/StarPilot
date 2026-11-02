"""Saved screen gestures for a detected Tesla Vehicle CAN connection."""
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureRow


KEYS = frozenset(('TeslaCoopSteering',))
ROWS = (('TeslaCoopSteering', 'Tesla Co-op Steering',
         'Model 3 only. Light steering input adjusts the assisted steering angle within its limits. Applies after the next startup.'),)


class TeslaCoopFeature:
  def __init__(self, owner):
    self.params = owner.params
    self.authority = owner.authority
    self.vehicle_params = owner.vehicle_params
    self.vehicle_fingerprint = owner.vehicle_fingerprint

  def capability(self):
    from openpilot.starpilot.car.tesla.coop_preferences import supported
    cp = self.vehicle_params()
    if not supported(cp):
      return None
    return (str(cp.carFingerprint), str(cp.brand), int(cp.flags), bool(cp.openpilotLongitudinalControl),
            bool(cp.pcmCruise), int(cp.alternativeExperience),
            tuple((str(config.safetyModel), int(config.safetyParam)) for config in cp.safetyConfigs))

  def rows(self, parked):
    capability = self.capability()
    if capability is None:
      return ()
    rows = []
    for key, label, reason in ROWS:
      raw, readable = read_saved(self.params, key, 8)
      valid = readable and raw in (None, b'0', b'1')
      rows.append(FeatureRow(key, label, ('On' if raw == b'1' else 'Off') if valid else 'Invalid saved choice',
                             source=raw, choices=('Off', 'On') if valid else (), default_value='Off',
                             available=parked and self.authority('parked_preferences') and valid,
                             reason=reason if valid else 'Invalid saved choice', capability=capability))
    return tuple(rows)

  def apply(self, request):
    if (request.key not in KEYS or request.value not in ('Off', 'On') or not request.confirmation or
        request.expected not in (None, b'0', b'1') or request.dependencies or request.related_source is not None or
        request.display_unit or request.direction):
      return False

    def authorized():
      return (self.authority('parked_preferences') and bool(request.vehicle_fingerprint) and
              self.vehicle_fingerprint() == request.vehicle_fingerprint and request.capability is not None and
              self.capability() == request.capability)

    result = commit_exact(self.params, key=request.key, max_bytes=8, expected=request.expected,
                          raw=b'1' if request.value == 'On' else b'0', authorized=authorized, temp_prefix='.tesla-screen-')
    return result.committed and result.verified
