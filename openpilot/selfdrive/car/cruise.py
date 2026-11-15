import math
from time import monotonic_ns
from dataclasses import dataclass
import numpy as np

from opendbc.car.structs import car
from openpilot.common.constants import CV
from openpilot.starpilot.longitudinal.cruise_intervals import CruiseIntervals

# Tesla Model 3/Y with its own cruise: SLC always sets the software set speed (the Tesla set-speed rule).
TESLA_SET_SPEED_CARS = ('TESLA_MODEL_3', 'TESLA_MODEL_Y')


def slc_sets_set_speed(CP, option: bool = False) -> bool:
  """Whether Speed Limit Controller writes the software set speed (limit + offset becomes SET) instead of supplying a
  ceiling beneath the driver's SET. Always on a Tesla Model 3/Y with its own cruise; a saved choice (SLCSetsSetSpeed,
  off by default) on cars where StarPilot owns the set speed. Needs StarPilot longitudinal control."""
  tesla = CP.carFingerprint in TESLA_SET_SPEED_CARS
  if not (tesla or option) or not CP.openpilotLongitudinalControl:
    return False
  return tesla if CP.pcmCruise else bool(option)


# WARNING: this value was determined based on the model's training distribution,
#          model predictions above this speed can be unpredictable
# V_CRUISE's are in kph
V_CRUISE_MIN = 8
V_CRUISE_MAX = 145
V_CRUISE_UNSET = 255
V_CRUISE_INITIAL = 40
IMPERIAL_INCREMENT = round(CV.MPH_TO_KPH, 1)  # round here to avoid rounding errors incrementing set speed

ButtonEvent = car.CarState.ButtonEvent
ButtonType = car.CarState.ButtonEvent.Type
CRUISE_LONG_PRESS = 50
CRUISE_NEAREST_FUNC = {
  ButtonType.accelCruise: math.ceil,
  ButtonType.decelCruise: math.floor,
}
CRUISE_INTERVAL_SIGN = {
  ButtonType.accelCruise: +1,
  ButtonType.decelCruise: -1,
}


@dataclass(frozen=True)
class SlcPendingConfirmation:
  session_id: str
  decision_id: int
  presentation_id: int


@dataclass(frozen=True)
class SlcConsumedButton:
  pending: SlcPendingConfirmation
  button: str


SlcWheelContext = tuple[str, int]


def validated_initial_cruise_speed(value: object) -> int:
  if type(value) is int and V_CRUISE_INITIAL <= value <= V_CRUISE_MAX:
    return value
  return V_CRUISE_INITIAL


def slc_target_kph(target_mps: float, is_metric: bool) -> float:
  target = round(target_mps * CV.MS_TO_KPH) if is_metric else round(target_mps * CV.MS_TO_MPH) * CV.MPH_TO_KPH
  return float(np.clip(round(target, 1), V_CRUISE_MIN, V_CRUISE_MAX))


class VCruiseHelper:
  def __init__(self, CP):
    self.CP = CP
    self.intervals = CruiseIntervals()
    self.v_cruise_kph = V_CRUISE_UNSET
    self.v_cruise_cluster_kph = V_CRUISE_UNSET
    self.v_cruise_kph_last = 0
    self.pending_resume_ns = 0
    self.button_timers = {ButtonType.decelCruise: 0, ButtonType.accelCruise: 0}
    self.button_change_states = {btn: {"standstill": False, "enabled": False} for btn in self.button_timers}
    self.pcm_slc_sample: tuple[float, int] | None = None
    self.pcm_slc_button = ButtonType.unknown
    self.slc_cruise_change: tuple[float, float, str, bool] | None = None
    self.slc_consumed_button: SlcConsumedButton | None = None
    self.slc_suppressed_buttons: set[ButtonType] = set()
    # Which pending confirmation each consumed button answered (card matches SLC set-speed commands to it).
    self.slc_suppression_owner: dict[ButtonType, SlcPendingConfirmation] = {}
    self.slc_released_owner: dict[ButtonType, SlcPendingConfirmation] = {}
    self.slc_released_suppressed = False
    self.slc_pcm = False
    self.tesla_controls_pcm = False
    self.match_pcm = False
    self.slc_wheel_press: SlcWheelContext | None = None
    self.slc_wheel_tap: SlcWheelContext | None = None

  @property
  def v_cruise_initialized(self):
    return self.v_cruise_kph != V_CRUISE_UNSET

  def update_v_cruise(self, CS, enabled, is_metric, slc_pending: SlcPendingConfirmation | None = None, *,
                      slc_enabled: bool = False, wheel_context: SlcWheelContext | None = None, tesla_controls: bool = False,
                      pedal_sets_speed: bool = False, car_cap: bool = False, follow_car: bool = False, match_owner: bool = False):
    # Tesla set-speed rule: on a Tesla Model 3/Y with its own cruise, live SLC owns the software set speed; other PCM cars
    # keep the SLC ceiling beneath the driver's SET.
    self.slc_pcm = bool(slc_enabled and self.CP.pcmCruise and self.CP.openpilotLongitudinalControl and
                        self.CP.carFingerprint in TESLA_SET_SPEED_CARS)
    # Tesla controls own the set speed with live SLC (saved speed mappings) or, with SLC off, only through Match the
    # Tesla's set speed (match_owner); a saved mapping alone never does.
    self.tesla_controls_pcm = bool(tesla_controls and (self.slc_pcm or (match_owner and self.CP.pcmCruise and
                                                                        self.CP.openpilotLongitudinalControl)) and
                                   self.CP.carFingerprint in TESLA_SET_SPEED_CARS)
    # Owned through Match alone (SLC off): the only way a Tesla speed step applies without live SLC.
    self.match_pcm = bool(self.tesla_controls_pcm and not self.slc_pcm)
    from opendbc.car.subaru.software_cruise import selected as subaru_software_selected
    subaru_software = subaru_software_selected(self.CP)
    software_cruise = not self.CP.pcmCruise or self.slc_pcm or self.tesla_controls_pcm or subaru_software
    self.slc_cruise_change = None
    self.slc_consumed_button = None
    self.slc_released_suppressed = False
    self.slc_released_owner = {}
    self.slc_wheel_tap = None
    self.v_cruise_kph_last = self.v_cruise_kph
    now_ns = monotonic_ns()
    if not CS.cruiseState.available or now_ns - self.pending_resume_ns > 500_000_000:
      self.pending_resume_ns = 0
    if not self.CP.pcmCruise:
      for button in CS.buttonEvents:
        self.pending_resume_ns = 0
        resume_edge = not button.pressed or self.CP.brand == 'gm' and CS.buttonEnable
        if (not enabled and CS.cruiseState.available and resume_edge and
            button.type in (ButtonType.accelCruise, ButtonType.resumeCruise) and self.v_cruise_initialized):
          self.pending_resume_ns = now_ns

    if software_cruise or slc_enabled and self.CP.openpilotLongitudinalControl or self.slc_suppressed_buttons:
      self._update_slc_button_latches(CS, enabled, slc_pending)

    if (not slc_enabled or not enabled or self.CP.pcmCruise or slc_pending is not None or
        not CS.cruiseState.available or ButtonType.accelCruise in self.slc_suppressed_buttons):
      wheel_context = None
    if wheel_context != self.slc_wheel_press:
      self.slc_wheel_press = None
    if wheel_context is not None and self.button_timers[ButtonType.accelCruise] == 0:
      pressed = {b.type.raw for b in CS.buttonEvents if b.pressed}
      if ButtonType.accelCruise in pressed and ButtonType.resumeCruise not in pressed:
        self.slc_wheel_press = wheel_context

    if CS.cruiseState.available:
      if software_cruise:
        if (self.slc_pcm or self.tesla_controls_pcm or subaru_software) and (not enabled or not self.v_cruise_initialized):
          # PCM still owns engagement; SLC owns the software set speed while system long is selected.
          stock_kph = CS.cruiseState.speed * CV.MS_TO_KPH
          self.v_cruise_kph = stock_kph if 0 < stock_kph < V_CRUISE_UNSET else V_CRUISE_UNSET
        self._update_v_cruise_non_pcm(CS, enabled, is_metric)
        owned_pcm = self.slc_pcm or self.tesla_controls_pcm
        car_kph = CS.cruiseState.speed * CV.MS_TO_KPH
        car_valid = owned_pcm and 0 < car_kph < V_CRUISE_UNSET
        if car_valid and follow_car and not car_cap:
          self._follow_car_set_speed(car_kph, enabled, is_metric)
        self._pedal_set_speed(CS, enabled, is_metric, pedal_sets_speed, cap_kph=car_kph if car_valid and car_cap else None)
        if car_valid and car_cap and enabled and self.v_cruise_initialized:
          # The car's own set speed is the ceiling: the comma speed may go below it, never above.
          self.v_cruise_kph = min(self.v_cruise_kph, car_kph)
        self.car_set_kph = car_kph if car_valid else None
        self.car_cap_kph = car_kph if car_valid and car_cap else None
        self.v_cruise_cluster_kph = self.v_cruise_kph
        self.update_button_timers(CS, enabled)
      else:
        self.v_cruise_kph = CS.cruiseState.speed * CV.MS_TO_KPH
        self.v_cruise_cluster_kph = CS.cruiseState.speedCluster * CV.MS_TO_KPH
        self._update_toyota_pcm_slc_change(CS, enabled, now_ns)
        if CS.cruiseState.speed == 0:
          self.v_cruise_kph = V_CRUISE_UNSET
          self.v_cruise_cluster_kph = V_CRUISE_UNSET
        elif CS.cruiseState.speed == -1:
          self.v_cruise_kph = -1
          self.v_cruise_cluster_kph = -1
    else:
      self.v_cruise_kph = V_CRUISE_UNSET
      self.v_cruise_cluster_kph = V_CRUISE_UNSET
      self.pcm_slc_sample = None
      self.pcm_slc_button = ButtonType.unknown
      if software_cruise:
        self.update_button_timers(CS, enabled)
    if self.button_timers[ButtonType.accelCruise] == 0:
      self.slc_wheel_press = None

  def _update_toyota_pcm_slc_change(self, CS, enabled: bool, now_ns: int) -> None:
    """Observe a physical PCM speed change without writing stock selected speed."""
    previous = self.pcm_slc_sample
    button = self.pcm_slc_button
    self.pcm_slc_sample = None
    self.pcm_slc_button = ButtonType.unknown
    if (self.CP.brand != 'toyota' or not self.CP.openpilotLongitudinalControl or
        not enabled or not CS.canValid or CS.canTimeout or not CS.cruiseState.enabled or
        CS.standstill or CS.cruiseState.standstill or
        not math.isfinite(self.v_cruise_kph) or not V_CRUISE_MIN <= self.v_cruise_kph <= V_CRUISE_MAX):
      return
    continuous = previous is not None and 0 < now_ns - previous[1] <= 150_000_000
    if not continuous:
      button = ButtonType.unknown
    # A held stalk can generate multiple PCM steps. On release, the previous
    # held direction remains eligible for this frame only; no guessed time window.
    held = button
    for edge in CS.buttonEvents:
      if edge.type in (ButtonType.accelCruise, ButtonType.decelCruise):
        if edge.pressed:
          button = held = edge.type
        elif edge.type == held:
          held = ButtonType.unknown
    if continuous:
      delta = self.v_cruise_kph - previous[0]
      if (not self.slc_suppressed_buttons and not self.slc_released_suppressed and
          ((button == ButtonType.accelCruise and delta > 0.) or
           (button == ButtonType.decelCruise and delta < 0.))):
        self.slc_cruise_change = (previous[0] / 3.6, self.v_cruise_kph / 3.6,
                                  'accel' if delta > 0. else 'decel', False)
    self.pcm_slc_button = held
    self.pcm_slc_sample = (float(self.v_cruise_kph), now_ns)

  def _update_slc_button_latches(self, CS, enabled, slc_pending: SlcPendingConfirmation | None) -> None:
    for b in CS.buttonEvents:
      button = b.type.raw
      if button not in self.button_timers:
        continue
      if not b.pressed and button in self.slc_suppressed_buttons:
        self.slc_suppressed_buttons.remove(button)
        prior_owner = self.slc_suppression_owner.pop(button, None)
        if prior_owner is not None:
          self.slc_released_owner[button] = prior_owner
        self.slc_released_suppressed = True
        self.button_timers[button] = 0
      elif (b.pressed and enabled and CS.cruiseState.available and slc_pending is not None and
            not (self.CP.pcmCruise and button == ButtonType.accelCruise and
                 (CS.standstill or CS.cruiseState.standstill or not CS.cruiseState.enabled)) and
            button not in self.slc_suppressed_buttons):
        self.slc_suppressed_buttons.add(button)
        self.slc_suppression_owner[button] = slc_pending
        self.slc_consumed_button = SlcConsumedButton(slc_pending, 'accel' if button == ButtonType.accelCruise else 'decel')

  def _update_v_cruise_non_pcm(self, CS, enabled, is_metric):
    # handle button presses. TODO: this should be in state_control, but a decelCruise press
    # would have the effect of both enabling and changing speed is checked after the state transition
    if not enabled:
      return
    if self.slc_released_suppressed:
      return

    long_press = False
    button_type = None

    for b in CS.buttonEvents:
      if b.type.raw in self.button_timers and not b.pressed:
        if self.button_timers[b.type.raw] > CRUISE_LONG_PRESS:
          return  # end long press
        button_type = b.type.raw
        break
    else:
      for k, timer in self.button_timers.items():
        if timer and timer % CRUISE_LONG_PRESS == 0:
          button_type = k
          long_press = True
          break

    if button_type is None:
      return

    if button_type in self.slc_suppressed_buttons:
      return

    # Don't adjust speed when pressing resume to exit standstill
    cruise_standstill = self.button_change_states[button_type]["standstill"] or CS.cruiseState.standstill
    if button_type == ButtonType.accelCruise and cruise_standstill:
      return

    # Don't adjust speed if we've enabled since the button was depressed (some ports enable on rising edge)
    if not self.button_change_states[button_type]["enabled"]:
      return

    changed = self.adjust_v_cruise(button_type, CS, is_metric, long_press=long_press)
    if changed and button_type == ButtonType.accelCruise and not long_press and self.button_timers[button_type] > 0:
      self.slc_wheel_tap = self.slc_wheel_press

  # The car's reply to the comma's own change (the Tesla jumping to its limit after a stalk hold the comma also acted on)
  # lands within this long; Follow ignores car changes until it has passed.
  FOLLOW_QUIET_NS = 1_500_000_000

  def _follow_car_set_speed(self, car_kph: float, enabled: bool, is_metric: bool) -> None:
    """Follow the car's own set-speed changes that StarPilot did not make (Override Tesla set-speed changes turned off).
    Changes include a stalk hold to the speed limit or an unassigned scroll. A change that moves the car's set speed
    towards the comma's is StarPilot's own mirror and is ignored; any other change of at least one display unit becomes
    the driver's new set speed."""
    previous = getattr(self, 'car_set_kph', None)
    if monotonic_ns() - getattr(self, 'slc_target_applied_ns', 0) < self.FOLLOW_QUIET_NS:
      return
    unit = 1. if is_metric else CV.MPH_TO_KPH
    if (not enabled or not self.v_cruise_initialized or previous is None or self.slc_cruise_change is not None or
        abs(car_kph - previous) < unit - 0.05 or not 0 < self.v_cruise_kph < V_CRUISE_UNSET):
      return
    if abs(car_kph - self.v_cruise_kph) < abs(previous - self.v_cruise_kph) - 0.05 and \
       (car_kph - self.v_cruise_kph) * (previous - self.v_cruise_kph) > 0:
      return  # moved towards the comma speed without crossing it: the mirror
    target = float(np.clip(round(round(car_kph / unit) * unit, 1), V_CRUISE_MIN, V_CRUISE_MAX))
    if abs(target - self.v_cruise_kph) < 0.05:
      return
    before = self.v_cruise_kph
    self.v_cruise_kph = target
    self.slc_cruise_change = (float(before / 3.6), float(target / 3.6), 'accel' if target > before else 'decel', False)

  def _pedal_set_speed(self, CS, enabled, is_metric, active: bool, cap_kph: float | None = None) -> bool:
    """Set new speed with pedal override: releasing the accelerator above the set speed, or the brake below it while
    still engaged (where braking does not disengage), makes the speed at release the new set speed, rounded to whole
    display units. Reported like a button change, so Speed Limit Controller treats it as the driver's own speed."""
    gas, brake = bool(CS.gasPressed), bool(CS.brakePressed)
    gas_released = getattr(self, 'pedal_gas_was', False) and not gas
    brake_released = getattr(self, 'pedal_brake_was', False) and not brake
    self.pedal_gas_was, self.pedal_brake_was = gas, brake
    direction = 1 if gas_released else -1 if brake_released else 0
    if (not active or not direction or not enabled or self.slc_cruise_change is not None or
        not 0 < self.v_cruise_kph < V_CRUISE_UNSET or CS.cruiseState.standstill):
      return False
    speed = CS.vEgoCluster if CS.vEgoCluster > 0 else CS.vEgo
    unit = 1. if is_metric else CV.MPH_TO_KPH
    target = float(np.clip(round(round(speed * CV.MS_TO_KPH / unit) * unit, 1), V_CRUISE_MIN, V_CRUISE_MAX))
    if cap_kph is not None:
      # Stay within the cap in whole display units.
      target = min(target, float(np.floor(cap_kph / unit + 0.01) * unit))  # CAN speeds arrive as float32
    # Only a change of at least one display unit in the pedal's direction (accelerator up, brake down).
    if direction * (target - self.v_cruise_kph) < unit - 0.05:
      return False
    previous_kph = self.v_cruise_kph
    self.v_cruise_kph = target
    self.slc_cruise_change = (float(previous_kph / 3.6), float(target / 3.6), 'accel' if direction > 0 else 'decel', False)
    return True

  def adjust_v_cruise(self, button_type, CS, is_metric: bool, *, long_press: bool = False) -> bool:
    """Apply the normal software cruise step without synthesizing button events."""
    from opendbc.car.subaru.software_cruise import selected as subaru_software_selected
    subaru_software = subaru_software_selected(self.CP)
    if ((self.CP.pcmCruise and not self.slc_pcm and not self.tesla_controls_pcm and not subaru_software) or
        button_type not in (ButtonType.accelCruise, ButtonType.decelCruise) or
        not 0 < self.v_cruise_kph < V_CRUISE_UNSET or
        (button_type == ButtonType.accelCruise and CS.cruiseState.standstill)):
      return False
    v_cruise_delta = 1. if is_metric else IMPERIAL_INCREMENT
    previous_kph = self.v_cruise_kph
    interval = self.intervals.held if long_press else self.intervals.short
    v_cruise_delta = v_cruise_delta * interval
    display_speed = round(self.v_cruise_kph if is_metric else self.v_cruise_kph * CV.KPH_TO_MPH)
    if interval % 5 == 0 and display_speed % interval != 0:
      # partial interval
      self.v_cruise_kph = CRUISE_NEAREST_FUNC[button_type](display_speed / interval) * interval * (1. if is_metric else CV.MPH_TO_KPH)
    else:
      self.v_cruise_kph += v_cruise_delta * CRUISE_INTERVAL_SIGN[button_type]

    # If set is pressed while overriding, clip cruise speed to minimum of vEgo
    if CS.gasPressed and button_type in (ButtonType.decelCruise, ButtonType.setCruise):
      self.v_cruise_kph = max(self.v_cruise_kph, CS.vEgo * CV.MS_TO_KPH)

    self.v_cruise_kph = np.clip(round(self.v_cruise_kph, 1), V_CRUISE_MIN, V_CRUISE_MAX)
    self.v_cruise_cluster_kph = self.v_cruise_kph
    if 0 < previous_kph < V_CRUISE_UNSET and self.v_cruise_kph != previous_kph:
      button = "accel" if button_type == ButtonType.accelCruise else "decel"
      self.slc_cruise_change = (float(previous_kph / 3.6), float(self.v_cruise_kph / 3.6), button, long_press)

    self.v_cruise_cluster_kph = self.v_cruise_kph
    return self.v_cruise_kph != previous_kph

  def update_button_timers(self, CS, enabled):
    # increment timer for buttons still pressed
    for k in self.button_timers:
      if self.button_timers[k] > 0:
        self.button_timers[k] += 1

    for b in CS.buttonEvents:
      if b.type.raw in self.button_timers:
        # Start/end timer and store current state on change of button pressed
        self.button_timers[b.type.raw] = 1 if b.pressed else 0
        self.button_change_states[b.type.raw] = {"standstill": CS.cruiseState.standstill, "enabled": enabled}

  def apply_slc_target(self, target_mps: float, is_metric: bool) -> None:
    """Set the software cruise speed: engagement, a qualified driver request or a qualified SLC set-speed command."""
    # Match ordinary button precision so stepping away and back restores this target.
    before = self.v_cruise_kph
    self.v_cruise_kph = slc_target_kph(target_mps, is_metric)
    cap = getattr(self, 'car_cap_kph', None)
    if cap is not None:
      self.v_cruise_kph = min(self.v_cruise_kph, cap)  # Tesla Independent set speed off: the car's speed is the ceiling
    self.v_cruise_cluster_kph = self.v_cruise_kph
    if abs(self.v_cruise_kph - before) >= 0.05:
      self.slc_target_applied_ns = monotonic_ns()

  def initialize_v_cruise(self, CS, experimental_mode: bool, *, resume: bool | None = None,
                          initial_speed: object = V_CRUISE_INITIAL, slc_target_mps: float | None = None, is_metric: bool = False) -> bool:
    """Return whether engagement used the supplied SLC target."""
    if (not self.CP.openpilotLongitudinalControl or slc_target_mps is None or type(slc_target_mps) not in (int, float) or
        not math.isfinite(slc_target_mps) or not V_CRUISE_MIN / 3.6 - 1e-5 <= slc_target_mps <= V_CRUISE_MAX / 3.6 + 1e-5):
      slc_target_mps = None
    # Native cruise owns PCM SET, including when SLC supplies a planner cap; on the Tesla set-speed rule (slc_pcm) SLC
    # owns SET and engaging starts it at the current limit.
    if self.CP.pcmCruise and (not self.slc_pcm or slc_target_mps is None):
      return False
    # A car-owned acknowledgment can outlive the physical button event.
    if resume is None:
      age = monotonic_ns() - self.pending_resume_ns
      resume = (0 < self.pending_resume_ns and 0 <= age <= 500_000_000) or any(
        b.type in (ButtonType.accelCruise, ButtonType.resumeCruise) for b in CS.buttonEvents)
    self.pending_resume_ns = 0
    if resume and self.v_cruise_initialized and not (self.slc_pcm and slc_target_mps is not None):
      if not self.CP.pcmCruise:
        self.v_cruise_kph = self.v_cruise_kph_last
        self.v_cruise_cluster_kph = self.v_cruise_kph
      return False
    elif slc_target_mps is not None:
      self.apply_slc_target(slc_target_mps, is_metric)
      return True
    else:
      self.v_cruise_kph = int(round(np.clip(max(CS.vEgo * CV.MS_TO_KPH, validated_initial_cruise_speed(initial_speed)), V_CRUISE_INITIAL, V_CRUISE_MAX)))

    self.v_cruise_cluster_kph = self.v_cruise_kph
    return False
