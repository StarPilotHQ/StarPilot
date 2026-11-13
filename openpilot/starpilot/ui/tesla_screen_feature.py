"""Saved screen gestures for a detected Tesla Vehicle CAN connection."""
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureRow


SCREEN_TAP, BRAKE = 'TeslaAOLScreenTap', 'TeslaAOLDisengageOnBrake'
KEYS = frozenset((SCREEN_TAP, BRAKE))
# The three-finger tap's Always On Lateral on/off is assigned in Wheel Controls (tesla_controls_feature); only the brake
# setting is a row here. It acts on Always On Lateral steering with either Tesla harness (ordinary AOL), default On.
BRAKE_HELP = ('On: pressing the brake turns Always On Lateral steering off; engage cruise or tap the screen to steer again. ' +
              'Off: Always On Lateral keeps steering while you brake. This applies with either Tesla harness.')
ROWS = ((BRAKE, 'Disengage on Brake', BRAKE_HELP + ' Applies after the next startup.'),)
AOL_NEEDED = 'Needs Always On Lateral turned on'


def aol_saved(params):
  # Same test as vehicle_preferences: Tesla screen AOL runs only with Always On Lateral saved on.
  return read_saved(params, 'AlwaysOnLateral', 8) == (b'1', True)


def brake_on(raw):
  return raw in (None, b'1')  # default On


class TeslaScreenFeature:
  def __init__(self, owner):
    self.params = owner.params
    self.authority = owner.authority
    self.vehicle_params = owner.vehicle_params
    self.vehicle_fingerprint = owner.vehicle_fingerprint

  def capability(self):
    from opendbc.car.tesla.screen_button import modern_aol_supported
    cp = self.vehicle_params()
    if not modern_aol_supported(cp):
      return None
    return (str(cp.carFingerprint), str(cp.brand), int(cp.flags), bool(cp.openpilotLongitudinalControl),
            bool(cp.pcmCruise), int(cp.alternativeExperience),
            tuple((str(config.safetyModel), int(config.safetyParam)) for config in cp.safetyConfigs))

  def rows(self, parked):
    capability = self.capability()
    if capability is None:
      from openpilot.starpilot.ui.tesla_set_speed_feature import CABLE_MISSING, addon_cable
      if addon_cable(self.vehicle_params()) is False:
        # Model 3/Y without the add-on cable: show what it would enable, grayed.
        return tuple(FeatureRow(key, label, 'On', choices=('Off', 'On'), default_value='On', available=False,
                                reason=CABLE_MISSING) for key, label, reason in ROWS)
      return ()
    rows = []
    from opendbc.car.tesla.screen_button import screen_tap_supported
    for key, label, reason in ROWS:
      if key == 'TeslaAOLScreenTap' and not screen_tap_supported(self.vehicle_params()):
        continue
      raw, readable = read_saved(self.params, key, 8)
      valid = readable and raw in (None, b'0', b'1')
      # With ordinary Tesla AOL the brake setting works with or without the three-finger tap.
      needed = AOL_NEEDED if not aol_saved(self.params) else None
      grayed = key == BRAKE and needed is not None
      rows.append(FeatureRow(key, label, ('On' if brake_on(raw) else 'Off') if valid else 'Invalid saved choice',
                             source=raw, choices=('Off', 'On') if valid else (), default_value='On',
                             available=parked and self.authority('parked_preferences') and valid and not grayed,
                             reason=needed if grayed and valid else reason if valid else 'Invalid saved choice',
                             capability=capability,
                             default_reset_available=grayed and parked and self.authority('parked_preferences')))
    return tuple(rows)

  def apply(self, request):
    if (request.key not in KEYS or request.value not in ('Off', 'On') or not request.confirmation or
        request.expected not in (None, b'0', b'1') or request.dependencies or request.related_source is not None or
        request.display_unit or request.direction):
      return False

    def authorized():
      return (self.authority('parked_preferences') and bool(request.vehicle_fingerprint) and
              self.vehicle_fingerprint() == request.vehicle_fingerprint and request.capability is not None and
              self.capability() == request.capability and
              (request.key != 'TeslaAOLScreenTap' or any(row.key == request.key for row in self.rows(True))))

    result = commit_exact(self.params, key=request.key, max_bytes=8, expected=request.expected,
                          raw=b'1' if request.value == 'On' else b'0', authorized=authorized, temp_prefix='.tesla-screen-')
    return result.committed and result.verified
