import time

from opendbc.car.hyundai.canfd_lead import CANFDLeadObservation, select_lead
from opendbc.car import structs
from opendbc.car.hyundai.canfd_stock_aol import qualified_long


def eligible(cp):
  from opendbc.car.hyundai.values import CAR
  # Genesis keeps its existing longitudinal controller.
  return cp.carFingerprint not in (CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN, CAR.GENESIS_GV60_EV_1ST_GEN, CAR.KIA_CARNIVAL_2025) and qualified_long(cp)


def options(available, control_state, lead):
  return {
    "direct_accel": True,
    "main_mode_acc": int(available),
    "jerk_lower": 5.0,
    "jerk_upper": 3.0 if control_state == structs.CarControl.Actuators.LongControlState.pid else 1.0,
    "lead_visible": lead.visible,
    "lead_distance": lead.distance,
    "lead_rel_speed": lead.relative_speed,
  }


def clock_boot_ns():
  try:
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)
  except (AttributeError, OSError):
    return 0


def fallback_lead(camera, hud_visible, floor_ns):
  now_ns = clock_boot_ns()
  observation = camera.current(now_ns, floor_ns) if camera is not None and now_ns > floor_ns > 0 else None
  camera_lead = (
    None
    if observation is None
    else CANFDLeadObservation(observation.visible, observation.distance_m, observation.relative_speed_mps, observation.producer_boot_ns)
  )
  return select_lead(None, camera_lead, hud_visible=hud_visible, observed_ns=max(now_ns, floor_ns + 1, 1), epoch_floor_ns=max(floor_ns, 0))
