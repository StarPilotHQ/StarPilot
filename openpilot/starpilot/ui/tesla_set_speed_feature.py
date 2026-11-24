"""Tesla Model 3/Y vehicle settings: the CAN add-on cable status and Comma can set speed on Tesla dashboard.

Shared by native settings and Galaxy. The cable (Vehicle CAN) is detected when the car starts; the switch takes effect
within a second of being changed (openpilot/starpilot/car/tesla/set_speed.py).
"""
from openpilot.starpilot.car.tesla import set_speed
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureRow

SYNC = set_speed.PARAM
KEYS = frozenset((SYNC,))
MODELS = ('TESLA_MODEL_3', 'TESLA_MODEL_Y')
CABLE_MISSING = 'Needs the Tesla CAN add-on cable'
LONG_MISSING = 'Needs Alpha Longitudinal turned on'
RESTART_NEEDED = 'Available after the next startup'
CABLE_LINKS = (('Buy the cable', 'https://xnor.shop/products/model-3-y-hw3-hw4-vehicle-can-addon-cable'),
               ('Install guide', 'https://wiki.xnor.shop/docs/tesla/hw3-hw4/vehicle-can-bus-addon/'))
CABLE_HELP = ('Adds the right scroll wheel, right stalk and three-finger screen tap as controls, and lets the comma set the ' +
              "speed on the Tesla's dashboard. Model 3 and Model Y, HW3 or HW4. Checked when the car starts.")
SYNC_LABEL = 'Comma can set speed on Tesla dashboard'
SYNC_HELP = ("StarPilot keeps its own set speed and scrolls the Tesla's set speed to match, so the dashboard shows the same " +
             "speed. The Tesla's can lag a second or two behind. The comma and the Tesla must use the same units. Experimental.")


def addon_cable(cp) -> bool | None:
  """True/False for a Tesla Model 3/Y (cable detected at startup or not); None for other cars."""
  try:
    from opendbc.car.tesla.values import TeslaFlags
    if cp is None or cp.brand != 'tesla' or cp.carFingerprint not in MODELS:
      return None
    return bool(cp.flags & TeslaFlags.HAS_VEHICLE_BUS)
  except (AttributeError, TypeError, ImportError):
    return None


class TeslaSetSpeedFeature:
  def __init__(self, owner):
    self.params = owner.params
    self.authority = owner.authority
    self.vehicle_params = owner.vehicle_params
    self.vehicle_fingerprint = owner.vehicle_fingerprint

  def capability(self):
    cp = self.vehicle_params()
    if addon_cable(cp) is None:
      return None
    return (str(cp.carFingerprint), str(cp.brand), int(cp.flags), bool(cp.openpilotLongitudinalControl), bool(cp.pcmCruise),
            tuple(int(config.safetyParam) for config in cp.safetyConfigs))

  def active(self) -> bool:
    """The comma owns the set speed on this car now."""
    return set_speed.active(self.params, self.vehicle_params())

  def unavailable(self, cp) -> str:
    """Why the switch cannot be used on this car as it started, or ''."""
    if not addon_cable(cp):
      return CABLE_MISSING
    if not cp.openpilotLongitudinalControl or not cp.pcmCruise:
      return LONG_MISSING
    if read_saved(self.params, 'IsReleaseBranch', 8) not in ((None, True), (b'0', True)):
      return 'Not available on release builds'
    if not set_speed.selected(cp):
      return RESTART_NEEDED  # the car started without the scroll frame admitted (Safe Mode, or settings changed since)
    return ''

  def rows(self, parked):
    capability = self.capability()
    if capability is None:
      return ()
    cp = self.vehicle_params()
    cable = addon_cable(cp)
    raw, readable = read_saved(self.params, SYNC, 8)
    valid = readable and raw in (None, b'0', b'1')
    grayed = self.unavailable(cp)
    return (
      FeatureRow('', 'Tesla CAN add-on cable', 'Detected' if cable else 'Not detected', reason=CABLE_HELP, status=True,
                 links=CABLE_LINKS),
      FeatureRow(SYNC, SYNC_LABEL, ('On' if raw == b'1' else 'Off') if valid else 'Invalid saved choice', source=raw,
                 choices=('Off', 'On') if valid else (), default_value='Off',
                 available=parked and self.authority('parked_preferences') and valid and not grayed,
                 reason=(grayed + '.\n' + SYNC_HELP if grayed else SYNC_HELP) if valid else 'Invalid saved choice',
                 capability=capability),
    )

  def apply(self, request):
    if (request.key not in KEYS or request.value not in ('Off', 'On') or not request.confirmation or
        request.expected not in (None, b'0', b'1') or request.dependencies or request.related_source is not None or
        request.display_unit or request.direction):
      return False

    def authorized():
      cp = self.vehicle_params()
      return (self.authority('parked_preferences') and bool(request.vehicle_fingerprint) and
              self.vehicle_fingerprint() == request.vehicle_fingerprint and request.capability is not None and
              self.capability() == request.capability and (request.value == 'Off' or not self.unavailable(cp)))

    result = commit_exact(self.params, key=request.key, max_bytes=8, expected=request.expected,
                          raw=b'1' if request.value == 'On' else b'0', authorized=authorized, temp_prefix='.tesla-set-speed-')
    return result.committed and result.verified
