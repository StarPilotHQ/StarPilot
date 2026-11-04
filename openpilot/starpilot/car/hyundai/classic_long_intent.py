from opendbc.car.hyundai.classic_long_aol import independent_buttons_qualified
from openpilot.starpilot.aol.intent import AolCardIntent


class ClassicLongCardIntent(AolCardIntent):
  defer_full_engagement_on_gas = True

  def __init__(self, cp, settings):
    super().__init__(settings, explicit_latch=True, independent_cruise_buttons=(
      independent_buttons_qualified(cp, marked_only=False)))
    self.requires_fault_observation = True
    self.observe_stock_engagement = True

  def _arm_from_full_engagement(self, CS, *, source_ns):
    if not (CS.steerFaultTemporary or CS.gasPressed or CS.brakePressed):
      super()._arm_from_full_engagement(CS, source_ns=source_ns)
