from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.selfdrive.car.cruise import CRUISE_LONG_PRESS
from openpilot.system.manager.process_health import driving_process_failures
from openpilot.starpilot.conditional_mode.manual import Button, ButtonTracker, Press

ButtonType = car.CarState.ButtonEvent.Type
GearShifter = car.CarState.GearShifter

AOL_TOGGLE = 9
PAUSE_LATERAL = 3
PAUSE_LONGITUDINAL = 4

@dataclass(frozen=True)
class AolSettings:
  enabled: bool
  pause_brake_mps: float
  lkas_action: int
  main_action: int
  distance_actions: tuple[int, int, int]
  cancel_actions: tuple[int, int, int]
  mode_actions: tuple[int, int, int] = (0, 0, 0)
  custom_actions: tuple[int, int, int] = (0, 0, 0)
  default_physical_buttons: bool = True


def _button_value(params, key: str) -> int | None:
  try:
    return int(params.get(key, return_default=True) or '0')
  except (TypeError, ValueError):
    return None


def _axis_action(value: int | None) -> int:
  return value if value in (0, PAUSE_LATERAL, PAUSE_LONGITUDINAL, AOL_TOGGLE) else 0


def _button_action(params, key: str) -> int:
  return _axis_action(_button_value(params, key))


def read_settings(params) -> AolSettings:
  # Legacy thresholds are effectively SI despite the mph label; preserve them until replaced.
  # Read raw bytes so malformed overrides cannot silently restore AOL.
  pause_mps = 0.0
  for key in ('AolBrakePauseSpeedMps', 'PauseAOLOnBrake'):
    try:
      pause_mps = float(Path(params.get_param_path(key)).read_bytes())
    except FileNotFoundError:
      continue
    except (OSError, ValueError):
      pause_mps = float('nan')
    break
  valid_pause = 0.0 <= pause_mps <= 100.0
  lkas = _button_value(params, 'LKASButtonControl')
  main = _button_value(params, 'MainCruiseButtonControl')
  return AolSettings(
    enabled=params.get_bool('AlwaysOnLateral') and valid_pause, pause_brake_mps=pause_mps if valid_pause else 0.0,
    lkas_action=_axis_action(lkas), main_action=_axis_action(main),
    default_physical_buttons=lkas == 0 and main == 0,
    distance_actions=(_button_action(params, 'DistanceButtonControl'),
                      _button_action(params, 'LongDistanceButtonControl'),
                      _button_action(params, 'VeryLongDistanceButtonControl')),
    mode_actions=(_button_action(params, 'ModeButtonControl'), _button_action(params, 'LongModeButtonControl'),
                         _button_action(params, 'VeryLongModeButtonControl')),
    custom_actions=(_button_action(params, 'StarButtonControl'), _button_action(params, 'LongStarButtonControl'),
                         _button_action(params, 'VeryLongStarButtonControl')),
    cancel_actions=(_button_action(params, 'CancelButtonControl'),
                    _button_action(params, 'LongCancelButtonControl'),
                    _button_action(params, 'VeryLongCancelButtonControl')),
  )


def independent_axis_requested(settings: AolSettings, *, include_auxiliary: bool = False) -> bool:
  pause = (PAUSE_LATERAL, PAUSE_LONGITUDINAL)
  actions = (settings.lkas_action, settings.main_action, *settings.distance_actions)
  if include_auxiliary:
    actions += (*settings.cancel_actions, *settings.mode_actions, *settings.custom_actions)
  return settings.enabled or any(action in pause for action in actions)


PROCESS_FAULT_MAX_AGE_NS = 1_500_000_000


class AolProcessFaultContext:
  def __init__(self):
    self.history = deque(maxlen=4)

  def observe(self, sm, now_ns: int) -> None:
    stamp = int(sm.logMonoTime.get('managerState', 0))
    if not (getattr(sm, 'seen', {}).get('managerState', False) and
            sm.valid.get('managerState', False) and sm.alive.get('managerState', False) and
            0 < stamp <= now_ns and now_ns - stamp <= PROCESS_FAULT_MAX_AGE_NS):
      self.history.clear()
      return
    processes = sm['managerState'].processes
    ui_requested = any(p.name == 'ui' and p.shouldBeRunning for p in processes)
    if not ui_requested or (self.history and stamp < self.history[-1][0]):
      self.history.clear()
      return
    if not self.history or stamp > self.history[-1][0]:
      self.history.append((stamp, frozenset(driving_process_failures(processes))))

  def temporary_ui_failure(self, event_ns: int, now_ns: int) -> bool:
    if not self.history or not 0 < event_ns <= now_ns:
      return False
    latest_stamp, latest_failures = self.history[-1]
    if not (latest_stamp <= now_ns and now_ns - latest_stamp <= PROCESS_FAULT_MAX_AGE_NS and
            latest_failures <= {'ui'}):
      return False
    # Recovery may arrive before the corresponding onroadEvents update. Use
    # the manager observation that produced this fault, never later UI health.
    for stamp, failures in reversed(self.history):
      if stamp <= event_ns:
        return now_ns - stamp <= PROCESS_FAULT_MAX_AGE_NS and failures == {'ui'}
    return False


def disarming_fault(events, CS, *, temporary_ui_process_failure: bool = False,
                    temporary_selfdrive_lagging: bool = False, temporary_source_pause: bool = False) -> bool:
  """Temporary steering/known non-driving gears pause output, not driver intent."""
  if CS.steerFaultPermanent:
    return True
  for event in events:
    if event.immediateDisable:
      if event.name == log.OnroadEvent.EventName.controlsMismatch and temporary_source_pause:
        continue
      return True
    if event.softDisable and event.name == log.OnroadEvent.EventName.wrongGear and CS.gearShifter == GearShifter.unknown:
      return True
    # SOFT warnings withdraw commands through the normal runtime/state machine.
    # They do not revoke the driver's separate armed lateral intent.
  return False


class AolCardIntent:
  def __init__(self, settings: AolSettings, *, explicit_latch: bool = False, lkas_button_supported: bool = True,
               independent_cruise_buttons: bool = False):
    self.settings = settings
    self.explicit_latch = explicit_latch
    self.independent_cruise_buttons = independent_cruise_buttons
    self.lkas_button_supported = lkas_button_supported
    self.allowed_latch = False
    # Observational only: records the exact branch that changed True to False.
    self.latch_clear_count = 0
    self.latch_clear_reason = ''
    self.pause_lateral = False
    self.pause_longitudinal = False
    self._gesture_neutral_seen = False
    self._distance_neutral_seen = False
    self._main_held = False
    self._lkas_held = False
    self._fault_inhibit = False
    self._last_latch_edge_ns = 0
    self.physical_arm_ns = 0
    self._native_rejection_ns = 0
    self._engagement_source_ns = 0
    self._engagement_consumed = True
    self._engagement_withdrawal_ns = 0
    self._engagement_observed_ns = 0
    self._calibration_inhibit = False
    self._calibration_fault = False
    self._calibration_rearm_required = False
    self._calibration_main = False
    self._calibration_engaged = False
    self._timers = {int(ButtonType.gapAdjustCruise): 0}
    self._held = {int(ButtonType.gapAdjustCruise): False}
    self._aux_cancel_tracker = ButtonTracker()
    self._aux_media_tracker = ButtonTracker()

  @property
  def main_action(self) -> int:
    return self.settings.main_action

  @property
  def lkas_action(self) -> int:
    return self.settings.lkas_action if self.lkas_button_supported else 0

  def _record_latch_clear(self, reason: str) -> None:
    if self.allowed_latch:
      self.latch_clear_count += 1
      self.latch_clear_reason = reason

  def _perform(self, action: int) -> None:
    if action == AOL_TOGGLE and self.settings.enabled and not self.explicit_latch:
      self._record_latch_clear('configured_toggle')
      self.allowed_latch = not self.allowed_latch
      if not self.allowed_latch:
        self._withdraw_full_engagement()
      if self.allowed_latch:
        self.pause_lateral = False
    elif action == PAUSE_LATERAL:
      self.pause_lateral = not self.pause_lateral
      if self.pause_lateral:
        self._withdraw_full_engagement()
    elif action == PAUSE_LONGITUDINAL:
      self.pause_longitudinal = not self.pause_longitudinal

  def _withdraw_full_engagement(self) -> None:
    self._engagement_consumed = True
    self._engagement_withdrawal_ns = max(self._engagement_withdrawal_ns, self._engagement_observed_ns)

  def observe_full_engagement(self, CS, *, enabled: bool, active: bool, source_ns: int,
                              observation_valid: bool, now_ns: int, fault_active: bool, native_rejection_ns: int = 0) -> None:
    """Consume one successful activation per freshly observed enabled epoch.

    Missing/reordered control packets do not invent a disengagement. This is
    driver intent only; the existing vehicle and native owners retain authority.
    """
    self._engagement_observed_ns = now_ns
    manual = any(event.type not in (ButtonType.setCruise, ButtonType.accelCruise,
                                     ButtonType.decelCruise, ButtonType.resumeCruise)
                 for event in CS.buttonEvents)
    blocked = bool(manual or self._main_held or self._lkas_held or
                   any(self._held.values()) or getattr(self, 'cancel_held', False) or
                   getattr(self, 'screen_held', False) or getattr(self, '_lkas_down', False) or
                   getattr(self, '_engagement_manual_withdrawal', False) or
                   not self.settings.enabled or fault_active or not CS.canValid or CS.canTimeout or CS.steerFaultPermanent or
                   CS.accFaulted or self._fault_inhibit or self._calibration_inhibit or
                   self._calibration_rearm_required or native_rejection_ns)
    # A local withdrawal also cancels pending preEnabled intent when Controls
    # has not published another packet yet. It cannot be undone by delayed ACKs.
    if blocked:
      self._withdraw_full_engagement()
    if not observation_valid or not self._engagement_source_ns < source_ns <= now_ns:
      return
    blocked = blocked or 0 < source_ns <= self._native_rejection_ns
    first = self._engagement_source_ns == 0
    self._engagement_source_ns = source_ns
    if first:
      # A process starting during engagement has no new driver gesture.
      self._engagement_consumed = bool(enabled)
    if not enabled:
      self._engagement_consumed = source_ns <= self._engagement_withdrawal_ns
      return
    if self._engagement_consumed or not active and not blocked:
      return
    self._engagement_consumed = True
    if (blocked or not active or not self.settings.enabled or not CS.cruiseState.available or
        CS.gearShifter in (GearShifter.park, GearShifter.neutral, GearShifter.reverse, GearShifter.unknown)):
      return
    self._arm_from_full_engagement(CS, source_ns=source_ns)

  def _arm_from_full_engagement(self, CS, *, source_ns: int) -> None:
    self.allowed_latch = True
    self.pause_lateral = False
    self._last_latch_edge_ns = source_ns

  def update(self, CS, *, consumed_buttons: frozenset[int] = frozenset(), fault_active: bool | None = None,
             now_ns: int = 0, native_rejection_ns: int = 0, standard_enabled: bool = False) -> None:
    if self.explicit_latch and self._native_rejection_ns < native_rejection_ns <= now_ns:
      self._native_rejection_ns = native_rejection_ns
      if self._last_latch_edge_ns < native_rejection_ns <= now_ns:
        # Native authorization was revoked. Keep neutral/held-button history so
        # the next fresh physical press arms once, without replaying a held one.
        self._record_latch_clear('native_rejection')
        self.allowed_latch = False
    if self.explicit_latch and (fault_active is not None or CS.steerFaultPermanent):
      self._fault_inhibit = bool(fault_active or CS.steerFaultPermanent)
      if self._fault_inhibit:
        self._record_latch_clear('disarming_fault')
        self.allowed_latch = False
        self._gesture_neutral_seen = False
    if not CS.canValid or CS.canTimeout:
      self._record_latch_clear('can_invalid_or_timeout')
      self.allowed_latch = False
      self._gesture_neutral_seen = False
      self._distance_neutral_seen = False
      self._main_held = False
      self._lkas_held = False
      for button in self._held:
        self._held[button] = False
        self._timers[button] = 0
      return

    if not self.settings.enabled:
      self._record_latch_clear('settings_disabled')
      self.allowed_latch = False
      if self.explicit_latch:
        self._gesture_neutral_seen = False
      if not independent_axis_requested(self.settings, include_auxiliary=self.auxiliary_supported()):
        self.pause_lateral = False
        self.pause_longitudinal = False

    lkas_managed = self.lkas_action == AOL_TOGGLE
    main_managed = self.main_action == AOL_TOGGLE
    if self.explicit_latch and CS.accFaulted:
      self._record_latch_clear('acc_fault')
      self.allowed_latch = False
      self._gesture_neutral_seen = False
    elif self.settings.enabled and not self.explicit_latch and not lkas_managed and not main_managed:
      if not CS.cruiseState.available:
        self._record_latch_clear('cruise_unavailable')
      self.allowed_latch = bool(CS.cruiseState.available)
    elif not self.explicit_latch and not CS.cruiseState.available:
      self._record_latch_clear('cruise_unavailable')
      self.allowed_latch = False

    for button in consumed_buttons:
      if button in self._held:
        self._held[button] = False
        self._timers[button] = 0

    distance_events = [event for event in CS.buttonEvents if event.type in (ButtonType.gapAdjustCruise, ButtonType.lkas)]
    if not self._distance_neutral_seen:
      self._distance_neutral_seen = not any(event.pressed for event in distance_events)

    if self.explicit_latch:
      independent_buttons = self.independent_cruise_buttons and (main_managed or lkas_managed)
      # The opt-in is bound to existing HKG torque owners. Other physical
      # owners keep their current event and native contracts.
      main_toggle = not independent_buttons or main_managed
      lkas_toggle = not independent_buttons or lkas_managed
      previously_held = (self._main_held and main_toggle) or (self._lkas_held and lkas_toggle)
      cancel_pressed = False
      for event in CS.buttonEvents:
        button = int(event.type.raw)
        if button == int(ButtonType.mainCruise):
          if (independent_buttons and event.pressed and not self._main_held and
              button not in consumed_buttons and self.settings.enabled and not CS.accFaulted and not self._fault_inhibit):
            self._perform(self.main_action if not main_managed else 0)
          self._main_held = bool(event.pressed)
        elif button == int(ButtonType.lkas):
          if (independent_buttons and event.pressed and not self._lkas_held and
              button not in consumed_buttons and self.settings.enabled and not CS.accFaulted and not self._fault_inhibit):
            self._perform(self.lkas_action if not lkas_managed else 0)
          self._lkas_held = bool(event.pressed)
        elif button == int(ButtonType.cancel) and event.pressed:
          cancel_pressed = True
      gesture_held = ((self._main_held and main_toggle and
                       (not independent_buttons or int(ButtonType.mainCruise) not in consumed_buttons)) or
                      (self._lkas_held and lkas_toggle and
                       (not independent_buttons or int(ButtonType.lkas) not in consumed_buttons)))
      if not previously_held and gesture_held and (not independent_buttons or not cancel_pressed):
        self._last_latch_edge_ns = now_ns
      if cancel_pressed:
        # An unassigned cruise CANCEL cannot toggle, arm or renew independent
        # lateral. Keep the existing withdrawal for configured CANCEL actions.
        if not self.independent_cruise_buttons or any(self.settings.cancel_actions):
          self._record_latch_clear('cancel_press')
          self.allowed_latch = False
      elif not self._gesture_neutral_seen:
        self._gesture_neutral_seen = not (self._main_held or self._lkas_held)
      elif not previously_held and gesture_held and self.settings.enabled and not CS.accFaulted and not self._fault_inhibit:
        # The physical gesture owns lateral intent even while ordinary cruise
        # is engaged. Dropping only the AOL latch leaves standard steering on.
        lateral_engaged = not self.pause_lateral and (self.allowed_latch or standard_enabled)
        if lateral_engaged:
          self._record_latch_clear('physical_toggle')
        self.allowed_latch = not lateral_engaged
        self.pause_lateral = lateral_engaged
        if not lateral_engaged:
          self.physical_arm_ns = now_ns

    for event in CS.buttonEvents:
      button = int(event.type.raw)
      if button in consumed_buttons:
        continue
      if self.explicit_latch and button in (int(ButtonType.cancel), int(ButtonType.lkas), int(ButtonType.mainCruise)):
        continue
      if button == int(ButtonType.gapAdjustCruise) and not self._distance_neutral_seen:
        continue
      if button == int(ButtonType.lkas) and event.pressed:
        self._perform(self.lkas_action)
      elif button == int(ButtonType.mainCruise) and event.pressed:
        self._perform(self.main_action)
      elif button in self._held:
        if event.pressed:
          self._held[button] = True
          self._timers[button] = 0
        else:
          ticks = self._timers[button]
          actions = self.settings.distance_actions if button == int(ButtonType.gapAdjustCruise) else self.settings.cancel_actions
          if 0 < ticks < CRUISE_LONG_PRESS:
            self._perform(actions[0])
          self._held[button] = False
          self._timers[button] = 0
    for button, held in self._held.items():
      if held:
        self._timers[button] += 1
        actions = self.settings.distance_actions if button == int(ButtonType.gapAdjustCruise) else self.settings.cancel_actions
        if self._timers[button] == CRUISE_LONG_PRESS:
          self._perform(actions[1])
        elif self._timers[button] == CRUISE_LONG_PRESS * 5:
          self._perform(actions[2])

  def observe_calibration(self, CS, *, events=None, standard_enabled: bool = False) -> None:
    if self.explicit_latch:
      return
    if events is not None:
      self._calibration_fault = disarming_fault(events, CS)
      self._calibration_inhibit = any(event.name in (
        log.OnroadEvent.EventName.calibrationInvalid,
        log.OnroadEvent.EventName.calibrationIncomplete,
        log.OnroadEvent.EventName.calibrationRecalibrating) for event in events)
    main = bool(CS.cruiseState.available)
    engaged = bool(CS.cruiseState.enabled or standard_enabled)
    main_derived = not self.explicit_latch and self.lkas_action != AOL_TOGGLE and self.main_action != AOL_TOGGLE
    if not main_derived:
      self._calibration_rearm_required = False
    fresh_edge = ((main and not self._calibration_main) or (engaged and not self._calibration_engaged))
    rearm_safe = (CS.canValid and not CS.canTimeout and not self._calibration_fault and
                  not CS.steerFaultPermanent and not CS.steerFaultTemporary and
                  CS.gearShifter not in (GearShifter.park, GearShifter.neutral, GearShifter.reverse, GearShifter.unknown))
    if self._calibration_inhibit and self._calibration_fault and main_derived:
      self._calibration_rearm_required = True
    elif self._calibration_rearm_required and fresh_edge and rearm_safe and self.settings.enabled:
      self._calibration_rearm_required = False
    if self._calibration_fault or self._calibration_rearm_required:
      self._record_latch_clear('calibration_inhibit')
      self.allowed_latch = False
    self._calibration_main = main
    self._calibration_engaged = engaged

  def auxiliary_supported(self) -> bool:
    return not self.explicit_latch

  def update_auxiliary(self, CS, *, media=None, media_eligible: bool = False, fault_active: bool | None = None) -> None:
    if (not self.auxiliary_supported() or not CS.canValid or CS.canTimeout or CS.steerFaultPermanent or
        fault_active is True or getattr(self, "_main_cycle_required", False)):
      self._aux_cancel_tracker = ButtonTracker()
      self._aux_media_tracker = ButtonTracker()
      return
    cancel = car.CarState(canValid=CS.canValid, canTimeout=CS.canTimeout, buttonEvents=[
      car.CarState.ButtonEvent(type=ButtonType.gapAdjustCruise, pressed=event.pressed)
      for event in CS.buttonEvents if event.type == ButtonType.cancel])
    for gesture in self._aux_cancel_tracker.observe(cancel.as_reader()):
      slot = (Press.SHORT, Press.LONG, Press.VERY_LONG).index(gesture.press)
      self._perform(self.settings.cancel_actions[slot])
    if not media_eligible:
      self._aux_media_tracker.invalidate_media()
      return
    for gesture in self._aux_media_tracker.observe(CS, media):
      if gesture.button not in (Button.MODE, Button.CUSTOM):
        continue
      slot = (Press.SHORT, Press.LONG, Press.VERY_LONG).index(gesture.press)
      actions = self.settings.mode_actions if gesture.button is Button.MODE else self.settings.custom_actions
      self._perform(actions[slot])

  def output(self, CS) -> tuple[bool, bool, bool]:
    driving = CS.gearShifter not in (GearShifter.neutral, GearShifter.park, GearShifter.reverse, GearShifter.unknown)
    return (self.allowed_latch and self.settings.enabled and CS.canValid and driving,
            self.pause_lateral or self._calibration_inhibit, self.pause_longitudinal or self._calibration_inhibit)
