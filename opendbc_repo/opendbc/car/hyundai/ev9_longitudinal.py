# EV9 calibration extracted from the reached Dom controller; isolated from Ioniq6 policy.
# Preserve source calibration, 20Hz update and 50Hz stop latch separately.
from dataclasses import dataclass
from capnp.lib.capnp import _DynamicStructBuilder, _DynamicStructReader
import numpy as np
from opendbc.car import DT_CTRL, structs
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.ccnc_ev_stock import qualified as stock_qualified, metadata_qualified

class CarControllerParams:
  ACCEL_MIN = -3.5
  ACCEL_MAX = 3.5

LongCtrlState = structs.CarControl.Actuators.LongControlState

IONIQ_6_RESPONSE_MULTIPLIER = 1.2

IONIQ_6_LONG_MIN_JERK = 0.5 * IONIQ_6_RESPONSE_MULTIPLIER

IONIQ_6_LONG_JERK_LIMIT = 4.8 * IONIQ_6_RESPONSE_MULTIPLIER

EV9_LONG_DECEL_JERK = 2.0

IONIQ_6_LONG_LOOKAHEAD_JERK_BP = [2.0, 5.0, 20.0]

IONIQ_6_LONG_LOOKAHEAD_JERK_V = [0.3 / IONIQ_6_RESPONSE_MULTIPLIER,
                                 0.45 / IONIQ_6_RESPONSE_MULTIPLIER,
                                 0.6 / IONIQ_6_RESPONSE_MULTIPLIER]

IONIQ_6_DYNAMIC_LOWER_JERK_BP = [-2.0, -1.5, -1.0, -0.25, -0.1, -0.025, -0.01, -0.005]

IONIQ_6_DYNAMIC_LOWER_JERK_V = [3.3, 1.5, 1.0, 0.8, 0.7, 0.65, 0.55, 0.5]

IONIQ_6_LAUNCH_HOLD_SPEED_BP = [0.0, 0.6, 1.25, 2.5]

IONIQ_6_LAUNCH_HOLD_SPEED_V = [0.75, 0.6, 0.4, 0.0]

IONIQ_6_STOP_BRAKE_CAP_MAX_SPEED = 2.0

IONIQ_6_STOP_BRAKE_CAP_SPEED_BP = [0.0, 0.08, 0.25, 0.6, 1.2, 2.0, 3.0]

IONIQ_6_STOP_BRAKE_CAP_ACCEL_V = [-0.15, -0.16, -0.22, -0.42, -0.78, -1.15, -1.40]

EV6_GT_LINE_STOP_BRAKE_CAP_MAX_SPEED = 1.2

IONIQ_6_STOP_HOLD_JERK_BP = [0.0, 0.15, 0.6, 1.2, 2.0, 3.0]

IONIQ_6_STOP_HOLD_JERK_V = [0.35, 0.40, 0.48, 0.65, 0.85, 1.10]

IONIQ_6_STOP_RELEASE_JERK_BP = [0.0, 0.15, 0.5]

IONIQ_6_STOP_RELEASE_JERK_V = [3.6 * IONIQ_6_RESPONSE_MULTIPLIER,
                               4.2 * IONIQ_6_RESPONSE_MULTIPLIER,
                               4.8 * IONIQ_6_RESPONSE_MULTIPLIER]

EV9_STOP_REQUEST_SPEED = 0.47

EV9_STANDSTILL_DELAY_FRAMES = 178

EV9_STOP_RELEASE_DELAY_FRAMES = 6

@dataclass
class Ioniq6LongitudinalTuningState:
  desired_accel: float = 0.0
  actual_accel: float = 0.0
  accel_last: float = 0.0
  jerk_upper: float = 0.0
  jerk_lower: float = 0.0
  launch_active: bool = False
  stopping: bool = False
  stopping_count: int = 0
  long_control_state_last: LongCtrlState = LongCtrlState.off

@dataclass(frozen=True)
class EV9LongitudinalTuningState:
  stop_request: bool = False
  cruise_standstill: bool = False
  stop_request_frames: int = 0
  release_frames: int = 0

def _jerk_limited_integrator(desired_accel: float, last_accel: float, jerk_upper: float, jerk_lower: float) -> float:
  step = (jerk_upper if desired_accel >= last_accel else jerk_lower) * DT_CTRL * 5.0
  return float(np.clip(desired_accel, last_accel - step, last_accel + step))

def _calculate_ioniq_6_dynamic_lower_jerk(accel_error: float) -> float:
  if accel_error < 0.0:
    scaled_values = np.array(IONIQ_6_DYNAMIC_LOWER_JERK_V) * (IONIQ_6_LONG_JERK_LIMIT / IONIQ_6_DYNAMIC_LOWER_JERK_V[0])
    return float(np.interp(accel_error, IONIQ_6_DYNAMIC_LOWER_JERK_BP, scaled_values))
  return IONIQ_6_LONG_MIN_JERK

def update_ev9_longitudinal_tuning(state: EV9LongitudinalTuningState, enabled: bool,
                                   stopping: bool, v_ego: float) -> EV9LongitudinalTuningState:
  if not enabled:
    return EV9LongitudinalTuningState()

  if stopping:
    if not state.stop_request and v_ego > EV9_STOP_REQUEST_SPEED:
      return EV9LongitudinalTuningState()
    frames = state.stop_request_frames + 1 if state.stop_request else 0
    return EV9LongitudinalTuningState(
      stop_request=True,
      cruise_standstill=frames >= EV9_STANDSTILL_DELAY_FRAMES,
      stop_request_frames=frames,
    )

  if state.stop_request:
    release_frames = state.release_frames + 1
    if release_frames <= EV9_STOP_RELEASE_DELAY_FRAMES:
      return EV9LongitudinalTuningState(
        stop_request=True,
        cruise_standstill=False,
        stop_request_frames=state.stop_request_frames,
        release_frames=release_frames,
      )

  return EV9LongitudinalTuningState()

def reset_egmp_longitudinal_tuning(state: Ioniq6LongitudinalTuningState) -> Ioniq6LongitudinalTuningState:
  state.desired_accel = 0.0
  state.actual_accel = 0.0
  state.accel_last = 0.0
  state.jerk_upper = 0.0
  state.jerk_lower = 0.0
  state.launch_active = False
  return state

def update_ioniq_6_longitudinal_tuning(state: Ioniq6LongitudinalTuningState, accel_cmd: float, v_ego: float, a_ego: float,
                                       long_control_state: LongCtrlState, long_active: bool,
                                       ev6_gt_line: bool = False, low_speed_stop_brake_cap: bool = False,
                                       ev9: bool = False) -> Ioniq6LongitudinalTuningState:
  starting = long_control_state == LongCtrlState.starting
  stopping = long_control_state == LongCtrlState.stopping
  restart_from_stop = state.long_control_state_last in (LongCtrlState.stopping, LongCtrlState.starting) and \
                      long_control_state in (LongCtrlState.starting, LongCtrlState.pid) and accel_cmd > 0.0 and v_ego < 0.5

  state.stopping = long_active and stopping
  state.stopping_count = state.stopping_count + 1 if state.stopping else 0

  if not long_active:
    state.desired_accel = 0.0
    state.actual_accel = 0.0
    state.accel_last = 0.0
    state.jerk_upper = 0.0
    state.jerk_lower = 0.0
    state.launch_active = False
    state.long_control_state_last = long_control_state
    return state

  if accel_cmd <= 0.0 or v_ego >= IONIQ_6_LAUNCH_HOLD_SPEED_BP[-1]:
    state.launch_active = False
  elif starting or (state.launch_active and v_ego < IONIQ_6_LAUNCH_HOLD_SPEED_BP[-1]) or \
      (state.long_control_state_last == LongCtrlState.starting and long_control_state == LongCtrlState.pid and v_ego < IONIQ_6_LAUNCH_HOLD_SPEED_BP[-1]):
    state.launch_active = True

  upper_speed_limit = (
    float(np.interp(v_ego, [0.0, 5.0, 20.0], [2.0, 3.0, 2.0])) * IONIQ_6_RESPONSE_MULTIPLIER if long_control_state == LongCtrlState.pid else
    IONIQ_6_LONG_MIN_JERK)
  lower_speed_limit = float(np.interp(v_ego, [0.0, 5.0, 20.0], [5.0, 3.5, 3.0])) * IONIQ_6_RESPONSE_MULTIPLIER

  future_t_upper = float(np.interp(v_ego, IONIQ_6_LONG_LOOKAHEAD_JERK_BP, IONIQ_6_LONG_LOOKAHEAD_JERK_V))

  accel_error = accel_cmd - state.accel_last
  j_ego_upper = float(np.clip(accel_error / future_t_upper, -IONIQ_6_LONG_JERK_LIMIT, IONIQ_6_LONG_JERK_LIMIT))
  desired_jerk_upper = min(max(j_ego_upper, IONIQ_6_LONG_MIN_JERK), upper_speed_limit)

  dynamic_accel_error = a_ego - state.accel_last
  dynamic_lower_jerk = _calculate_ioniq_6_dynamic_lower_jerk(dynamic_accel_error)
  state.jerk_upper = desired_jerk_upper
  state.jerk_lower = min(dynamic_lower_jerk, lower_speed_limit)
  if ev9:
    state.jerk_lower = min(state.jerk_lower, EV9_LONG_DECEL_JERK)

  if state.stopping:
    stop_brake_cap_max_speed = EV6_GT_LINE_STOP_BRAKE_CAP_MAX_SPEED if ev6_gt_line or low_speed_stop_brake_cap else \
      IONIQ_6_STOP_BRAKE_CAP_MAX_SPEED
    if v_ego <= stop_brake_cap_max_speed:
      stop_brake_cap = float(np.interp(v_ego, IONIQ_6_STOP_BRAKE_CAP_SPEED_BP, IONIQ_6_STOP_BRAKE_CAP_ACCEL_V))
      state.desired_accel = min(0.0, max(accel_cmd, stop_brake_cap))
      state.jerk_upper = min(state.jerk_upper, float(np.interp(v_ego, IONIQ_6_STOP_HOLD_JERK_BP, IONIQ_6_STOP_HOLD_JERK_V)) * IONIQ_6_RESPONSE_MULTIPLIER)
    else:
      state.desired_accel = float(np.clip(accel_cmd, CarControllerParams.ACCEL_MIN, 0.0))
  else:
    state.desired_accel = float(np.clip(accel_cmd, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))
    if state.launch_active:
      state.desired_accel = max(state.desired_accel, float(np.interp(v_ego, IONIQ_6_LAUNCH_HOLD_SPEED_BP, IONIQ_6_LAUNCH_HOLD_SPEED_V)))
      state.jerk_upper = max(state.jerk_upper, float(np.interp(v_ego, [0.0, 2.5], [4.8, 3.2])) * IONIQ_6_RESPONSE_MULTIPLIER)
      state.jerk_lower = max(state.jerk_lower, 1.0)
    if restart_from_stop:
      state.jerk_upper = min(state.jerk_upper, float(np.interp(v_ego, IONIQ_6_STOP_RELEASE_JERK_BP, IONIQ_6_STOP_RELEASE_JERK_V)))

  state.actual_accel = _jerk_limited_integrator(state.desired_accel, state.accel_last, state.jerk_upper, state.jerk_lower)
  state.accel_last = state.actual_accel
  state.long_control_state_last = long_control_state
  return state


BLINDSPOT_WARNING_FLASH_SAMPLES = 20

BLINDSPOT_WARNING_FLASH_ON_SAMPLES = 16

BLINDSPOT_WARNING_SOUND_SAMPLES = 36

@dataclass(frozen=True)
class BlindspotWarningOutput:
  mirror_lamp_active: bool = False
  sound_active: bool = False

@dataclass
class BlindspotWarningState:
  flash_phase: int = 0
  mirror_warning_active: bool = False
  escalated_prev: bool = False
  sound_remaining: int = 0
  sound_armed: bool = True

def update_blindspot_warning(state: BlindspotWarningState, escalated: bool,
                             blinker: bool) -> BlindspotWarningOutput:
  if not blinker:
    state.flash_phase = 0
    state.mirror_warning_active = False
    state.escalated_prev = False
    state.sound_remaining = 0
    state.sound_armed = True
    return BlindspotWarningOutput()

  rising = escalated and not state.escalated_prev
  if rising:
    state.flash_phase = 0
    state.mirror_warning_active = True
    if state.sound_armed:
      state.sound_remaining = BLINDSPOT_WARNING_SOUND_SAMPLES
      state.sound_armed = False
  elif escalated:
    state.flash_phase = (state.flash_phase + 1) % BLINDSPOT_WARNING_FLASH_SAMPLES
    state.mirror_warning_active = True
  elif state.mirror_warning_active and state.flash_phase < BLINDSPOT_WARNING_FLASH_ON_SAMPLES - 1:
    state.flash_phase += 1
  else:
    state.flash_phase = 0
    state.mirror_warning_active = False

  state.escalated_prev = escalated
  sound_active = state.sound_remaining > 0
  if state.sound_remaining > 0:
    state.sound_remaining -= 1
  return BlindspotWarningOutput(
    mirror_lamp_active=state.mirror_warning_active and state.flash_phase < BLINDSPOT_WARNING_FLASH_ON_SAMPLES,
    sound_active=sound_active,
  )


class EV9LongitudinalPolicy:
  def __init__(self):
    self.tuning = Ioniq6LongitudinalTuningState()
    self.stop = EV9LongitudinalTuningState()
    self.left_warning = BlindspotWarningState()
    self.right_warning = BlindspotWarningState()

  def update(self, frame, accel, speed, measured_accel, control_state, enabled, override):
    # Original reset runs at100Hz, calibration only every fifth controller frame.
    if self.stop.stop_request or not enabled or override:
      reset_egmp_longitudinal_tuning(self.tuning)
    active = control_state in (LongCtrlState.starting, LongCtrlState.pid, LongCtrlState.stopping)
    if active and frame % 5 == 0:
      update_ioniq_6_longitudinal_tuning(self.tuning, accel, speed, measured_accel, control_state, True,
                                       low_speed_stop_brake_cap=True, ev9=True)
    return self.tuning.actual_accel if active else accel

  def update_stop(self, enabled, override, stopping, speed):
    self.stop = update_ev9_longitudinal_tuning(self.stop, enabled and not override, stopping, speed)
    if self.stop.stop_request or not enabled or override:
      reset_egmp_longitudinal_tuning(self.tuning)
      return 0.0
    return None


STOCK_PARAM = 0x5c91
LONG_PARAM = 0x5c95


def copy_cp(cp):
  # Copy the message directly; dictionary reconstruction traverses every field.
  if type(cp) is _DynamicStructBuilder and cp.schema == structs.CarParams.schema:
    return cp.copy()
  if type(cp) is _DynamicStructReader and cp.schema == structs.CarParams.schema:
    return cp.as_builder()
  with structs.CarParams.from_bytes(structs.CarParams.new_message(**cp.to_dict()).to_bytes()) as reader:
    return reader.as_builder()


def stock_copy(cp):
  stock = copy_cp(cp)
  stock.safetyConfigs[0].safetyParam = STOCK_PARAM
  stock.openpilotLongitudinalControl = False
  stock.pcmCruise = True
  stock.longitudinalActuatorDelay = 0.5
  return stock


def candidate(stock, *, enabled, is_release):
  if not enabled or is_release or stock.carFingerprint != CAR.KIA_EV9 or not stock_qualified(stock):
    return stock
  result = copy_cp(stock)
  result.alphaLongitudinalAvailable = True
  result.openpilotLongitudinalControl = True
  result.pcmCruise = False
  result.safetyConfigs[0].safetyParam = LONG_PARAM
  result.longitudinalActuatorDelay = 0.3
  return result


def qualified(cp):
  if (cp.carFingerprint != CAR.KIA_EV9 or not cp.openpilotLongitudinalControl or cp.pcmCruise or
      cp.alternativeExperience not in (0, 32) or
      not cp.alphaLongitudinalAvailable or len(cp.safetyConfigs) != 1 or cp.safetyConfigs[0].safetyParam != LONG_PARAM):
    return False
  if type(cp) in (_DynamicStructBuilder, _DynamicStructReader) and cp.schema == structs.CarParams.schema:
    return metadata_qualified(cp)
  stock = stock_copy(cp)
  stock.alternativeExperience = 0
  return stock_qualified(stock)


class LegacyAngleEnvelope:
  """Original Sportage safety envelope, never vehicle identity or metadata.

  Original source VM: wheelbase2.756, steerRatio13.7, zero rear steering,
  slip factor -.0006085930193026732 (original native mode mirrors that CP).
  Only the EV9 LONG owner's original host calibration consumes this model.
  """
  def get_steer_from_curvature(self, curvature, speed, roll):
    assert roll == 0
    # Original CarParams Float32 stores precede VehicleModel construction.
    return curvature * 2.75600004196167 * 13.699999809265137 * (1.0 - (-0.0006085930193026732) * speed ** 2)


def lateral_request_allowed(control, state, angle_fault, *, independent=False):
  """Host half of the existing exact5c95 native actuation predicates.

  The paired exact EV9 AOL profile may request lateral-only control. Its
  native physical latch and current axis/session proof remain final authority.
  Ordinary control retains pedal refusal. Native-qualified independent lateral
  keeps its request through pedals; both retain standstill, gear, fault and CAN-health refusals.
  """
  return ((control.enabled or (independent and control.latActive)) and state.canValid and not state.canTimeout and not state.standstill and
          state.gearShifter == structs.CarState.GearShifter.drive and
          not (state.steerFaultTemporary or state.steerFaultPermanent or angle_fault) and
          ((independent and control.latActive) or not (state.brakePressed or state.gasPressed)))
