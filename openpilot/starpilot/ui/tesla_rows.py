"""Tesla Model 3/Y settings rows, joined to the features the settings owner already builds.

feature_settings_owner.py sits at the repository's 120 KiB file limit, so the Tesla rows come in through these two
subclasses rather than new branches there. Vehicle Settings gets the CAN add-on cable and Comma can set speed on Tesla
dashboard above the existing Tesla screen rows; Wheel Controls gets the Tesla controls below the wheel buttons. Other
cars get exactly the rows they had: both additions are empty unless the car is a Tesla Model 3 or Model Y.
"""
from openpilot.starpilot.ui.tesla_controls_feature import KEYS as CONTROL_KEYS, TeslaControlsFeature
from openpilot.starpilot.ui.tesla_screen_feature import KEYS as SCREEN_KEYS, TeslaScreenFeature
from openpilot.starpilot.ui.tesla_set_speed_feature import KEYS as SET_SPEED_KEYS, TeslaSetSpeedFeature
from openpilot.starpilot.ui.wheel_feature import WheelFeature

# Every key the owner hands to TeslaVehicleRows.apply.
KEYS = SCREEN_KEYS | SET_SPEED_KEYS | CONTROL_KEYS
# The set-speed steps of a short press and a hold of a cruise button (feature_settings_owner._cruise_rows).
CRUISE_STEP_KEYS = ('CustomCruise', 'CustomCruiseLong')


class TeslaVehicleRows(TeslaScreenFeature):
  def __init__(self, owner):
    super().__init__(owner)
    self.set_speed = TeslaSetSpeedFeature(owner)
    self.controls = TeslaControlsFeature(owner)

  def rows(self, parked):
    return (*self.set_speed.rows(parked), *super().rows(parked))

  def apply(self, request):
    if request.key in SET_SPEED_KEYS:
      return self.set_speed.apply(request)
    if request.key in CONTROL_KEYS:
      return self.controls.apply(request)
    return super().apply(request)


class TeslaWheelRows(WheelFeature):
  def __init__(self, owner):
    super().__init__(owner)
    self.tesla = TeslaControlsFeature(owner)

  def rows(self):
    return [*super().rows(), *self.tesla.rows()]

  def page(self, rows):
    """The Wheel Controls page: the rows above the wheel's, then the wheel's and the Tesla's. StarPilot never steps a
    Model 3/Y's set speed from cruise buttons, so the two step rows (Short press, Hold) are left out there."""
    tesla = self.tesla.rows()
    if tesla:
      rows = [row for row in rows if row.key not in CRUISE_STEP_KEYS]
    return [*rows, *super().rows(), *tesla]
