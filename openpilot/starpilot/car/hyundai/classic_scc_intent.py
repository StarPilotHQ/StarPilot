"""Stock SCC engagement intent over the shared physical lateral owner."""
from openpilot.starpilot.aol.intent import AolCardIntent, AOL_TOGGLE
from openpilot.starpilot.car.hyundai.forte_intent import ForteCardIntent


class ClassicSccCardIntent(ForteCardIntent):
  observe_stock_engagement = True

  def __init__(self, cp, settings):
    super().__init__(cp, settings)
    self.independent_cruise_buttons = True
    self.lkas_button_supported = self.has_lkas

  def update(self, CS, **kwargs):
    default_lkas = (self.has_lkas and self.settings.default_physical_buttons and
                    self.main_action == 0 and self.lkas_action == 0)
    if default_lkas or self.main_action == AOL_TOGGLE or self.lkas_action == AOL_TOGGLE:
      # Stock SCC uses the configured physical toggles. Forte's MAIN-derived
      # availability latch belongs to its NON_SCC owner.
      AolCardIntent.update(self, CS, **kwargs)
      self.physical_latch = self.allowed_latch
    else:
      super().update(CS, **kwargs)

  def _arm_from_full_engagement(self, CS, *, source_ns):
    if not (CS.steerFaultTemporary or CS.gasPressed or CS.brakePressed):
      self.fault_rearm = False
      super()._arm_from_full_engagement(CS, source_ns=source_ns)
