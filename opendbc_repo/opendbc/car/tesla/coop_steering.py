import math

import numpy as np

from opendbc.car import DT_CTRL, rate_limit
from opendbc.car.lateral import apply_steer_angle_limits_vm
from opendbc.car.tesla.values import CarControllerParams
from opendbc.car.vehicle_model import VehicleModel


DT_LAT_CTRL = DT_CTRL * CarControllerParams.STEER_STEP
STEER_RESUME_RATE_LIMIT_RAMP_RATE = 300.0  # deg/s^2

STEER_OVERRIDE_MIN_TORQUE = 0.5  # Nm
STEER_OVERRIDE_MAX_TORQUE = 2.5  # Nm
STEER_OVERRIDE_TORQUE_RANGE = STEER_OVERRIDE_MAX_TORQUE - STEER_OVERRIDE_MIN_TORQUE
STEER_OVERRIDE_MAX_LAT_ACCEL = 2.0  # m/s^2
STEER_OVERRIDE_DELTA_GAIN_LIMIT = 125.0  # deg/s/Nm


def apply_bounds(signal: float, limit: float) -> float:
  return float(np.clip(signal, -limit, limit))


def apply_deadzone(signal: float, deadzone: float) -> float:
  return signal - apply_bounds(signal, deadzone)


def get_steer_from_lat_accel(lat_accel: float, v_ego: float, VM: VehicleModel) -> float:
  curvature = lat_accel / max(1.0, v_ego) ** 2
  return math.degrees(VM.get_steer_from_curvature(curvature, v_ego, 0.0))


def get_override_torque_to_angle(v_ego: float, VM: VehicleModel) -> float:
  max_angle = CarControllerParams.ANGLE_LIMITS.STEER_ANGLE_MAX
  steer_from_lat_accel = apply_bounds(get_steer_from_lat_accel(STEER_OVERRIDE_MAX_LAT_ACCEL, v_ego, VM), max_angle)
  return steer_from_lat_accel / STEER_OVERRIDE_TORQUE_RANGE


def calc_override_angle_delta_limit(torque: float) -> float:
  max_gain = CarControllerParams.ANGLE_LIMITS.MAX_ANGLE_RATE / DT_LAT_CTRL / STEER_OVERRIDE_TORQUE_RANGE
  return torque * min(STEER_OVERRIDE_DELTA_GAIN_LIMIT, max_gain) * DT_LAT_CTRL


class SteerRateLimiter:
  def __init__(self):
    self.last = 0.0

  def reset(self, angle: float) -> None:
    self.last = angle

  def update(self, angle: float, angle_delta_limit: float) -> float:
    limited = rate_limit(angle, self.last, -angle_delta_limit, angle_delta_limit)
    self.last = limited
    return limited


class CooperativeSteeringController:
  def __init__(self):
    self.apply_angle_last = 0.0
    self.coop_apply_angle_last = 0.0
    self.angle_override = 0.0
    self.resume_rate_limiter_delta = SteerRateLimiter()
    self.resume_rate_limiter = SteerRateLimiter()
    self.resume_limit_error_deg = 0.0
    self.cooperative_limit_error_deg = 0.0
    self.cooperative_offset_deg = 0.0

  def reset_override_state(self, apply_angle: float) -> None:
    self.apply_angle_last = apply_angle
    self.angle_override = 0.0
    self.coop_apply_angle_last = apply_angle

  def reset_resume_state(self, apply_angle: float) -> None:
    self.resume_rate_limiter_delta.reset(0.0)
    self.resume_rate_limiter.reset(apply_angle)

  def update_override_angle(self, apply_angle_delta: float, driver_torque: float, v_ego: float, VM: VehicleModel) -> float:
    driver_torque = apply_deadzone(driver_torque, STEER_OVERRIDE_MIN_TORQUE)
    torque_to_angle = get_override_torque_to_angle(v_ego, VM)
    target_angle = driver_torque * torque_to_angle

    holding_torque = self.angle_override / torque_to_angle if abs(v_ego) > 0.1 else 0.0
    torque_delta = driver_torque - holding_torque
    angle_delta_limit = calc_override_angle_delta_limit(abs(torque_delta))
    angle_override_delta = float(np.clip(target_angle - self.angle_override, -angle_delta_limit, angle_delta_limit))

    # Avoid counting model-requested motion and driver-requested motion twice.
    if angle_override_delta * apply_angle_delta > 0.0:
      angle_override_delta -= apply_bounds(apply_angle_delta, abs(angle_override_delta))

    self.angle_override += angle_override_delta
    return self.angle_override

  def unwind_override_angle(self, saturation_error: float) -> None:
    if self.angle_override * saturation_error > 0.0:
      self.angle_override -= apply_bounds(saturation_error, abs(self.angle_override))

  def apply_resume_rate_limit(self, lat_active: bool, apply_angle: float) -> float:
    if not lat_active:
      self.reset_resume_state(apply_angle)
      return apply_angle

    angle_rate_delta = self.resume_rate_limiter_delta.update(
      CarControllerParams.ANGLE_LIMITS.MAX_ANGLE_RATE,
      STEER_RESUME_RATE_LIMIT_RAMP_RATE * DT_LAT_CTRL ** 2,
    )
    return self.resume_rate_limiter.update(apply_angle, angle_rate_delta)

  def update(self, apply_angle: float, lat_active: bool, enabled: bool, CS, VM: VehicleModel) -> tuple[float, bool]:
    self.resume_limit_error_deg = 0.0
    self.cooperative_limit_error_deg = 0.0
    self.cooperative_offset_deg = 0.0
    if not enabled:
      self.reset_resume_state(apply_angle)
      self.reset_override_state(apply_angle)
      return apply_angle, lat_active

    requested_angle = apply_angle
    apply_angle = self.apply_resume_rate_limit(lat_active, apply_angle)
    self.resume_limit_error_deg = abs(requested_angle - apply_angle)
    if not lat_active:
      self.reset_override_state(apply_angle)
      return apply_angle, False

    apply_angle_delta = apply_angle - self.apply_angle_last
    self.apply_angle_last = apply_angle
    self.cooperative_offset_deg = self.update_override_angle(apply_angle_delta, CS.out.steeringTorque, CS.out.vEgo, VM)
    apply_angle += self.cooperative_offset_deg

    limited_angle = apply_steer_angle_limits_vm(
      apply_angle,
      self.coop_apply_angle_last,
      CS.out.vEgoRaw,
      CS.out.steeringAngleDeg,
      True,
      CarControllerParams,
      VM,
    )
    self.coop_apply_angle_last = limited_angle
    self.cooperative_limit_error_deg = abs(apply_angle - limited_angle)
    self.unwind_override_angle(apply_angle - limited_angle)
    return limited_angle, True
