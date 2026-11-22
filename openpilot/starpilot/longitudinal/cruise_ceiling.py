"""Optional normalized cruise ceiling for the existing longitudinal planner."""

import math
from dataclasses import dataclass

from openpilot.starpilot.speed_limits.acceptance import Authority, LongitudinalOwner, authority_is_valid


@dataclass(frozen=True)
class CruiseCeiling:
  speed_mps: float
  authority: Authority


@dataclass(frozen=True)
class CurveCeiling:
  speed_mps: float
  model_ns: int


@dataclass(frozen=True)
class CeilingDecision:
  effective_mps: float | None
  status: str


def _positive_finite(value: object) -> bool:
  if type(value) not in (int, float):
    return False
  assert isinstance(value, (int, float))
  try:
    return math.isfinite(value) and value > 0
  except OverflowError:
    return False


def select_cruise_ceiling(ceiling: object, *, driver_v_cruise_kph: float, driver_cruise_mps: float,
                          system_long_available: bool, long_control_active: bool,
                          selfdrive_enabled: bool, force_decel: bool) -> CeilingDecision:
  """Qualify a supplied cap; None means retain the planner's ordinary cruise path."""
  if ceiling is None:
    return CeilingDecision(None, "absent")
  if not isinstance(ceiling, CruiseCeiling) or not _positive_finite(ceiling.speed_mps) or not authority_is_valid(ceiling.authority):
    return CeilingDecision(None, "invalid")
  if any(type(flag) is not bool for flag in (system_long_available, long_control_active, selfdrive_enabled, force_decel)):
    return CeilingDecision(None, "invalid")
  if (not system_long_available or not long_control_active or not selfdrive_enabled or
      not ceiling.authority.can_control or ceiling.authority.owner is LongitudinalOwner.CRUISE_BUTTONS):
    return CeilingDecision(None, "inactive")
  if not _positive_finite(driver_v_cruise_kph) or driver_v_cruise_kph == 255 or not _positive_finite(driver_cruise_mps):
    return CeilingDecision(None, "driver_cruise_unavailable")
  if force_decel:
    return CeilingDecision(None, "force_decel")
  if ceiling.speed_mps >= driver_cruise_mps:
    return CeilingDecision(None, "above_driver")
  return CeilingDecision(ceiling.speed_mps, "applied")


def select_curve_ceiling(ceiling: object, *, model_ns: int, driver_v_cruise_kph: float, effective_cruise_mps: float,
                         system_long_available: bool, long_control_active: bool, car_long_active: bool,
                         selfdrive_enabled: bool, force_decel: bool, driver_override: bool) -> CeilingDecision:
  """A host-qualified curve cap must still match this exact native model frame."""
  if ceiling is None:
    return CeilingDecision(None, "absent")
  if (not isinstance(ceiling, CurveCeiling) or not _positive_finite(ceiling.speed_mps) or
      type(ceiling.model_ns) is not int or ceiling.model_ns <= 0 or type(model_ns) is not int or model_ns <= 0):
    return CeilingDecision(None, "invalid")
  if ceiling.model_ns != model_ns:
    return CeilingDecision(None, "wrong_model_frame")
  if any(type(flag) is not bool for flag in (system_long_available, long_control_active, car_long_active,
                                           selfdrive_enabled, force_decel, driver_override)):
    return CeilingDecision(None, "invalid")
  if not system_long_available or not long_control_active or not car_long_active or not selfdrive_enabled or driver_override:
    return CeilingDecision(None, "inactive")
  if not _positive_finite(driver_v_cruise_kph) or driver_v_cruise_kph == 255 or not _positive_finite(effective_cruise_mps):
    return CeilingDecision(None, "driver_cruise_unavailable")
  if force_decel:
    return CeilingDecision(None, "force_decel")
  if ceiling.speed_mps >= effective_cruise_mps:
    return CeilingDecision(None, "not_binding")
  return CeilingDecision(ceiling.speed_mps, "selected")
