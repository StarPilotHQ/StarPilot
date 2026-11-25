"""Caller-qualified torque commands or lateral-acceleration demand for display."""

from dataclasses import dataclass
import math
from typing import Any, Literal

import numpy as np
from opendbc.car.honda.values import CarControllerParams as HondaParams, HondaFlags


DEFAULT_MAX_LATERAL_ACCELERATION = 3.0
LATERAL_STATES = frozenset(("pidState", "angleState", "debugState", "torqueState", "curvatureState",
                            "lqrStateDEPRECATED", "indiStateDEPRECATED"))


@dataclass(frozen=True)
class TorqueFeedback:
  utilization: float | None = None
  source: Literal["unavailable", "actuator_output", "lateral_acceleration_demand", "angle_error"] = "unavailable"
  reason: str = "car_state_unavailable"


def accord_angle_error_bar(car_params: Any) -> bool:
  try:
    return car_params.brand == "honda" and bool(car_params.flags & HondaFlags.EPS_ANGLE_LOOP_FW)
  except (AttributeError, TypeError):
    return False


def accord_angle_error_fraction(setpoint_deg: float, angle_deg: float, v_ego: float) -> float:
  """(setpoint - wheel) over the Accord error clip at this speed, clipped to +-1.

  Negated like the torque branch: openpilot angles are left-positive and the bar draws +x to the
  right, so the bar must point the way the EPS is pulling the wheel (left pull = bar left)."""
  error_max = float(np.interp(v_ego, HondaParams.ANGLE_ERROR_MAX_BP, HondaParams.ANGLE_ERROR_MAX_V))
  return float(np.clip(-(setpoint_deg - angle_deg) / error_max, -1.0, 1.0))


def _finite(value: Any) -> float | None:
  if type(value) not in (int, float):
    return None
  try:
    result = float(value)
    return result if math.isfinite(result) else None
  except (OverflowError, ValueError):
    return None


def observe_torque_feedback(car_state: Any, car_control: Any, controls_state: Any, car_output: Any,
                            vehicle_parameters: Any, car_params: Any) -> TorqueFeedback:
  """Pass None for invalid, stale, offroad or pre-drive messages; no IPC or writes."""
  car, controls, output, control = car_state, controls_state, car_output, car_control
  if (car is None or getattr(car, "canValid", None) is not True or getattr(car, "canTimeout", None) is not False):
    return TorqueFeedback(reason="car_state_unavailable")
  try:
    state = controls.lateralControlState.which()
  except (AttributeError, TypeError, ValueError):
    state = None
  if type(state) is not str or state not in LATERAL_STATES:
    return TorqueFeedback(reason="lateral_state_unavailable")

  angle = state in ("angleState", "curvatureState")
  if getattr(car_params, "brand", None) == "rivian" and not angle:
    if output is None:
      return TorqueFeedback(reason="actuator_output_unavailable")
    mode = getattr(getattr(output, "actuatorsOutput", None), "lateralControlMode", None)
    if mode is not None:
      mode = str(mode)
      if mode not in ("inactive", "angle", "torque", "torqueRecovering"):
        return TorqueFeedback(reason="output_mode_invalid")
      if control is None or type(getattr(control, "latActive", None)) is not bool:
        return TorqueFeedback(reason="lateral_activity_unavailable")
      angle = mode == "angle" and control.latActive

  if not angle:
    torque = _finite(getattr(getattr(output, "actuatorsOutput", None), "torque", None))
    if torque is None or not -1 <= torque <= 1:
      return TorqueFeedback(reason="actuator_output_unavailable")
    return TorqueFeedback(-torque, "actuator_output", "observed")

  if control is None or type(getattr(control, "latActive", None)) is not bool:
    return TorqueFeedback(reason="lateral_activity_unavailable")
  if not control.latActive:
    return TorqueFeedback(0.0, "lateral_acceleration_demand", "lateral_inactive")
  # Accord on the angle-loop EPS: show the angle error the setpoint carries, not the lateral-acceleration demand
  # (the estimate this replaced subtracted road roll, so the device's standing roll bias drew as a constant pull).
  if accord_angle_error_bar(car_params):
    setpoint = _finite(getattr(getattr(output, "actuatorsOutput", None), "steeringAngleDeg", None))
    angle = _finite(getattr(car, "steeringAngleDeg", None))
    speed = _finite(getattr(car, "vEgoRaw", None))
    if setpoint is None or angle is None or speed is None:
      return TorqueFeedback(reason="actuator_output_unavailable")
    return TorqueFeedback(accord_angle_error_fraction(setpoint, angle, speed), "angle_error", "observed")
  parameters = vehicle_parameters
  if parameters is None or getattr(parameters, "valid", None) is not True:
    return TorqueFeedback(reason="vehicle_parameters_unavailable")
  speed = _finite(getattr(car, "vEgo", None))
  curvature = _finite(getattr(controls, "curvature", None))
  desired_curvature = _finite(getattr(controls, "desiredCurvature", None))
  roll = _finite(getattr(parameters, "roll", None))
  maximum = DEFAULT_MAX_LATERAL_ACCELERATION if car_params is None else _finite(getattr(car_params, "maxLateralAccel", None))
  if (speed is None or speed < 0 or curvature is None or desired_curvature is None or roll is None or
      maximum is None or maximum <= 0):
    return TorqueFeedback(reason="estimate_inputs_invalid")
  speed_squared = speed * speed
  actual_acceleration = curvature * speed_squared
  desired_acceleration = desired_curvature * speed_squared
  if not math.isfinite(actual_acceleration):
    return TorqueFeedback(reason="estimate_inputs_invalid")
  utilization = desired_acceleration / maximum
  if not math.isfinite(utilization):
    return TorqueFeedback(reason="estimate_inputs_invalid")
  return TorqueFeedback(max(-1.0, min(1.0, utilization)), "lateral_acceleration_demand", "requested")
