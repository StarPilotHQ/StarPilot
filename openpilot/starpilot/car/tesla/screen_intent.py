"""Modern screen gesture override layered over ordinary cruise availability."""
from dataclasses import replace
from opendbc.car import structs
from openpilot.starpilot.aol.intent import AolCardIntent, ButtonType


class TeslaScreenCardIntent(AolCardIntent):
  def __init__(self, cp, settings):
    super().__init__(settings, explicit_latch=True)
    self.screen_override = None
    self.brake_disengage = bool(cp.safetyConfigs[0].safetyParam & 1024)
    self.previous_enabled = False
    self.previous_available = False
    self.brake_blocked = False
    self.screen_held = False

  def update(self, CS, **kwargs):
    filtered = structs.CarState(**CS.to_dict())
    filtered.buttonEvents = [structs.CarState.ButtonEvent(**event.to_dict()) for event in CS.buttonEvents
                             if event.type != ButtonType.lkas]
    super().update(filtered.as_reader(), **kwargs)
    engaged = bool(CS.cruiseState.enabled)
    available = bool(CS.cruiseState.available)
    # Stock cruise availability does not revoke an explicit screen selection.
    if (engaged and not self.previous_enabled) or (self.previous_available and not available and not CS.brakePressed and
                                                  self.screen_override is not True):
      self.screen_override = None if engaged and not self.previous_enabled else False
      self.brake_blocked = False
      self.pause_lateral = False
    cancel = any(event.type == ButtonType.cancel and event.pressed for event in CS.buttonEvents)
    fault = bool(not CS.canValid or CS.canTimeout or kwargs.get('fault_active') or CS.accFaulted or CS.steerFaultPermanent or
                 CS.steeringDisengage or CS.doorOpen or CS.gearShifter not in
                 (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.reverse, structs.CarState.GearShifter.neutral))
    rejection = kwargs.get('native_rejection_ns', 0)
    if rejection and self._last_latch_edge_ns < rejection <= kwargs.get('now_ns', 0):
      self.screen_override = False
    if self.brake_disengage and CS.brakePressed:
      self.brake_blocked = True
      self.screen_override = False
    if fault or cancel or not self.settings.enabled:
      self.screen_override = False
    for event in CS.buttonEvents:
      if event.type != ButtonType.lkas:
        continue
      fresh_press = bool(event.pressed and not self.screen_held)
      self.screen_held = bool(event.pressed)
      if (fresh_press and CS.gearShifter == structs.CarState.GearShifter.drive and not CS.steerFaultTemporary and
          not fault and not cancel and self.settings.enabled and
          not (self.brake_disengage and CS.brakePressed)):
        current = (available if self.screen_override is None else self.screen_override)
        current = not self.pause_lateral and bool(current or kwargs.get('standard_enabled'))
        self.screen_override = not current
        self.brake_blocked = False
        self.pause_lateral = current
        self._last_latch_edge_ns = kwargs.get('now_ns', 0)
    self.allowed_latch = bool((available if self.screen_override is None else self.screen_override) and
                              not self.brake_blocked and not fault and not cancel and self.settings.enabled)
    self.previous_enabled, self.previous_available = engaged, available

  def _arm_from_full_engagement(self, CS, *, source_ns):
    if not (self.brake_blocked or self.brake_disengage and CS.brakePressed or
            CS.steeringDisengage or CS.doorOpen or CS.gearShifter != structs.CarState.GearShifter.drive):
      self.screen_override = True
      super()._arm_from_full_engagement(CS, source_ns=source_ns)

  @property
  def settings(self):
    return self._settings

  @settings.setter
  def settings(self, value):
    self._settings = replace(value, lkas_action=0)
