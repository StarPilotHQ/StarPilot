"""Dom's HKG two-horizon stopping intent; acceleration remains planner-owned."""
import numpy as np

from opendbc.car.hyundai.values import CAR, HyundaiFlags

CLASSIC_STOP_SPEED = float(np.float32(.5))
CANFD_STOP_SPEED = float(np.float32(.3))
STOP_SPEED_OVERRIDES = {
  CAR.GENESIS_G90: float(np.float32(.8)),
  CAR.HYUNDAI_PALISADE_2023: float(np.float32(.35)),
  CAR.KIA_NIRO_PHEV_2022: float(np.float32(.7)),
}


def forecast_should_stop(cp, speeds, time_indices, action_t):
  if (cp.carFingerprint not in CAR.__members__ or not cp.openpilotLongitudinalControl or getattr(cp, 'pcmCruise', False) or
      any(getattr(cp, key, False) for key in ('passive', 'dashcamOnly', 'notCar'))):
    return None
  if len(speeds) != len(time_indices) or len(speeds) < 2:
    return None
  default = CANFD_STOP_SPEED if getattr(cp, 'flags', 0) & HyundaiFlags.CANFD else CLASSIC_STOP_SPEED
  threshold = STOP_SPEED_OVERRIDES.get(cp.carFingerprint, default)
  return bool(np.interp(action_t, time_indices, speeds) < threshold and
              np.interp(action_t + 1.0, time_indices, speeds) < threshold)
