"""Conservative start-state admission for an exact tagged Ioniq 6 LONG session.

This does not qualify ECU ownership; the opt-in prearm still requires a verified
live handoff before longitudinal control is published.
Weak or unknown evidence keeps the ordinary longitudinal PID path.
"""

import math
from dataclasses import dataclass

from opendbc.car.hyundai.canfd_owner import config_index
from opendbc.car.hyundai.ioniq6_handoff import IONIQ6_DEBUG_LONG_PARAMS
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.structs import car

from openpilot.common.realtime import DT_CTRL


LongCtrlState = car.CarControl.Actuators.LongControlState
STRONG_TARGET_MPS2 = 0.75  # Existing Ioniq controller's standstill launch floor.
START_SPEED_MPS = 0.1
STRONG_SETTLE_FRAMES = round(0.35 / DT_CTRL)
MIN_STRONG_SPAN_NS = (STRONG_SETTLE_FRAMES - 1) * round(DT_CTRL * 1e9)
MAX_FRAME_GAP_NS = 20_000_000


@dataclass(frozen=True)
class StartEvidence:
  car_fresh: bool
  plan_fresh: bool
  lead_clear: bool | None
  drive_id: int | None
  observed_ns: int | None


def eligible(cp) -> bool:
  """A stock Ioniq CP never receives the optional starting state."""
  try:
    owner = config_index(cp)
    if owner is None:
      return False
    flags = int(cp.flags)
    required = int(HyundaiFlags.CANFD | HyundaiFlags.EV | HyundaiFlags.CANFD_LKA_STEER_MSG)
    forbidden = int(HyundaiFlags.CANFD_ALT_BUTTONS | HyundaiFlags.CANFD_ANGLE_STEERING | HyundaiFlags.CANFD_CAMERA_SCC)
    expected_raw = (0x8095, 0x8895) if flags & int(HyundaiFlags.CANFD_LKA_STEER_MSG_ALT) else (0x8015, 0x8815)
    return (str(cp.carFingerprint) == str(CAR.HYUNDAI_IONIQ_6) and cp.brand == 'hyundai' and
            cp.openpilotLongitudinalControl is True and cp.alphaLongitudinalAvailable is True and
            not cp.pcmCruise and not cp.radarUnavailable and
            not cp.passive and not cp.dashcamOnly and not cp.notCar and
            flags & required == required and not flags & forbidden and
            int(cp.safetyConfigs[owner].safetyParam) in expected_raw and
            int(cp.safetyConfigs[owner].safetyParam) in IONIQ6_DEBUG_LONG_PARAMS)
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


class Ioniq6StartPolicy:
  def __init__(self) -> None:
    self.strong_frames = 0
    self.drive_id: int | None = None
    self.last_ns: int | None = None
    self.first_ns: int | None = None

  def reset(self) -> None:
    self.strong_frames = 0
    self.drive_id = None
    self.last_ns = None
    self.first_ns = None

  def transition(self, native_state, previous_state, *, active: bool, should_stop: bool,
                 brake_pressed: bool, gas_pressed: bool, can_valid: bool, can_timeout: bool,
                 car_fresh: bool, plan_fresh: bool, lead_clear: bool | None,
                 drive_id: int | None, observed_ns: int | None,
                 speed_mps: float, target_mps2: float, accel_limits: tuple[float, float]):
    """Use native transition except for a fully evidenced, settled start window."""
    try:
      lower, upper = accel_limits
      physical = all(type(value) in (int, float) and math.isfinite(value)
                     for value in (speed_mps, target_mps2, lower, upper))
      strong = (physical and active is True and should_stop is False and brake_pressed is False and
                gas_pressed is False and can_valid is True and can_timeout is False and
                car_fresh is True and plan_fresh is True and lead_clear is True and
                type(drive_id) is int and drive_id > 0 and
                type(observed_ns) is int and observed_ns > drive_id and
                0.0 <= speed_mps <= START_SPEED_MPS and lower <= 0.0 <= upper and
                target_mps2 >= STRONG_TARGET_MPS2 and upper >= STRONG_TARGET_MPS2)
    except (TypeError, ValueError, OverflowError):
      strong = False

    if not strong or observed_ns is None or drive_id is None:
      self.reset()
      if previous_state == LongCtrlState.starting:
        return LongCtrlState.stopping if active and should_stop else (LongCtrlState.pid if active else LongCtrlState.off)
      return native_state

    if (self.drive_id != drive_id or self.last_ns is None or
        not 0 < observed_ns - self.last_ns <= MAX_FRAME_GAP_NS):
      self.strong_frames = 0
      self.drive_id = drive_id
      self.first_ns = observed_ns
    self.last_ns = observed_ns
    self.strong_frames = min(STRONG_SETTLE_FRAMES, self.strong_frames + 1)
    if self.strong_frames >= STRONG_SETTLE_FRAMES and self.first_ns is not None and observed_ns - self.first_ns >= MIN_STRONG_SPAN_NS:
      return LongCtrlState.starting
    return LongCtrlState.pid if previous_state == LongCtrlState.starting else native_state

  @staticmethod
  def starting_output(target_mps2: float, accel_limits: tuple[float, float]) -> float:
    """Never add a launch kick above the fresh planner target or native bound."""
    return max(0.0, min(1.0, target_mps2, accel_limits[1]))
