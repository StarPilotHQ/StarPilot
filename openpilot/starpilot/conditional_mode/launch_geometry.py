"""Release-only candidate; never used to acquire a stop or validate road geometry."""

import math
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.starpilot.conditional_mode.turn_scene import MIN_STEERING_ANGLE_DEG


def qualified_launch_horizon(model, car, lead_present, *, gradual=False):
  if (
    car.standstill is not True
    or car.vEgoRaw != 0.0
    or not -0.01 <= car.vEgo <= 0.01
    or car.gasPressed
    or car.brakePressed
    or lead_present
    or model.action.shouldStop is not False
    or not math.isfinite(model.action.desiredAcceleration)
    or model.action.desiredAcceleration > 10.0
    or (model.action.desiredAcceleration <= 0.0 if gradual else model.action.desiredAcceleration < 0.3)
  ):
    return None
  x, v, t = tuple(model.position.x), tuple(model.velocity.x), tuple(model.position.t)
  vt = tuple(model.velocity.t)
  if (
    len(t) != len(ModelConstants.T_IDXS)
    or len(vt) != len(t)
    or any(not math.isfinite(a) or abs(a - b) > 1e-5 for grid in (t, vt) for a, b in zip(grid, ModelConstants.T_IDXS, strict=True))
  ):
    return None
  if len(x) != 33 or len(v) != 33 or len(t) != 33 or not all(math.isfinite(a) for a in (*x, *v, *t)):
    return None
  if abs(x[0]) > 1e-6 or t[0] != 0 or any(b <= a for a, b in zip(t, t[1:], strict=False)):
    return None
  # A bounded launch-prediction prefix, not a general backtracking allowance.
  debt = 0.0
  for i, (a, b) in enumerate(zip(x, x[1:], strict=False)):
    if b < a:
      debt += a - b
      if t[i + 1] > 0.75 or max(abs(a), abs(b)) > 0.05 or debt > 0.05:
        return None
  if min(x) < -0.05 or max(x) > 500 or min(v) < -0.15 or max(v) > 100 or x[-1] < 54:
    return None
  tail = [a for a, stamp in zip(v, t, strict=True) if stamp >= 2.0]
  if not tail or min(tail) < 0.3 or tail[-1] < 5 or any(b < a for a, b in zip(tail, tail[1:], strict=False)):
    return None
  if gradual:
    lateral = tuple(model.position.y)
    if (car.leftBlinker or car.rightBlinker or not math.isfinite(car.steeringAngleDeg) or
        abs(car.steeringAngleDeg) >= MIN_STEERING_ANGLE_DEG or len(lateral) != len(x) or
        not all(math.isfinite(y) and abs(y) <= 0.75 for y, distance in zip(lateral, x, strict=True) if distance <= 54.) or
        not all(math.isfinite(y) and abs(y) <= 1.5 for y in lateral) or min(tail) < 1.0 or tail[-1] < 8.0):
      return None
  return x[-1]
