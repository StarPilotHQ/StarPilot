import math

from opendbc.car.tesla.values import CAR, TeslaSafetyFlags


STEER_ANGLE_SATURATION_THRESHOLD = 2.5
_MAX_STEERING_LIMIT_INFO_AGE_NANOS = 100_000_000


def _legacy_angle_steering_limited(requested_angle: float, car_output) -> bool:
  return abs(requested_angle - car_output.actuatorsOutput.steeringAngleDeg) > \
    STEER_ANGLE_SATURATION_THRESHOLD


def is_angle_steering_limited(CP, requested_angle: float, car_output, companion,
                              output_healthy: bool, now_nanos: int) -> bool:
  legacy_limited = _legacy_angle_steering_limited(requested_angle, car_output)

  try:
    cooperative_enabled = any(
      safety_config.safetyParam & TeslaSafetyFlags.COOP_STEERING.value
      for safety_config in CP.safetyConfigs
    )
    if CP.carFingerprint not in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y) or not cooperative_enabled or not output_healthy:
      return legacy_limited

    info = companion.steeringLimitInfo
    errors = (
      info.modelLimitErrorDeg,
      info.resumeLimitErrorDeg,
      info.cooperativeLimitErrorDeg,
      info.combinedLimitErrorDeg,
    )
    numeric_values = errors + (info.cooperativeOffsetDeg,)
    mono_time = info.monoTime

    if not info.valid or mono_time <= 0:
      return legacy_limited

    age_nanos = now_nanos - mono_time
    if age_nanos < 0 or age_nanos > _MAX_STEERING_LIMIT_INFO_AGE_NANOS:
      return legacy_limited

    if not all(math.isfinite(value) for value in numeric_values) or any(error < 0 for error in errors):
      return legacy_limited

    return max(errors) > STEER_ANGLE_SATURATION_THRESHOLD
  except (AttributeError, TypeError, ValueError, OverflowError):
    return legacy_limited
