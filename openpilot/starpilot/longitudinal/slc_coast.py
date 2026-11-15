"""Frozen named-profile SLC coast preference for the cruise candidate only."""

import math
from dataclasses import dataclass

from openpilot.starpilot.longitudinal.accel_profile import akima_interp

_WINDOW_BP = (0.0, 10.0, 20.0, 35.0)
_WINDOW = (0.20, 0.40, 0.65, 1.10)
_EXCESS = (0.8, 1.8, 3.5, 5.5)
_STYLE = {
  'eco': (1.20, -0.02),
  'standard': (1.00, -0.03),
  'sport': (0.75, -0.04),
}


def braking_lead_relevant(lead: object, v_ego: float) -> bool:
  if not getattr(lead, 'present', False):
    return False
  try:
    lead_speed = float(lead.vLead)
    lead_accel = float(lead.aLeadK)
    distance = float(lead.dRel)
  except (AttributeError, TypeError, ValueError, OverflowError):
    return True
  if not all(math.isfinite(value) for value in (v_ego, lead_speed, lead_accel, distance)):
    return True
  return v_ego - lead_speed > 0.5 or lead_accel < -0.4 or distance < max(18.0, 2.0 * v_ego)


def slc_coast_floor(*, v_ego: float, slc_target: float | None, driver_cruise: float,
                    full_brake_floor: float, relevant_lead: bool, stop_context: bool,
                    braking_style: str = 'standard') -> float | None:
  """Return a cruise-only brake floor; None preserves the ordinary planner."""
  if (slc_target is None or type(braking_style) is not str or braking_style not in _STYLE or
      any(type(flag) is not bool for flag in (relevant_lead, stop_context)) or
      not all(type(value) in (int, float) and math.isfinite(value)
              for value in (v_ego, slc_target, driver_cruise, full_brake_floor)) or
      slc_target <= 0.0 or v_ego <= 4.0 or v_ego <= slc_target + 0.05 or
      slc_target >= driver_cruise - 0.15 or relevant_lead or stop_context or
      not -3.5 <= full_brake_floor < 0.0):
    return None

  return shaped_slc_floor(v_ego, slc_target, full_brake_floor, braking_style)
def shaped_slc_floor(v_ego, slc_target, full_brake_floor, braking_style='standard', *, approach=False):
  """Mapped arrivals use native target-error easing; other SLC keeps coast easing."""
  # The cruise controller already tapers acceleration with target-speed error.
  # An extra coast tail lengthens the arrival forecast and is not available when
  # an adopted/remembered target loses its optional shaping qualification.
  if approach:
    return full_brake_floor
  if v_ego <= 4. or v_ego <= slc_target + .05:
    return full_brake_floor
  if v_ego - slc_target >= max(_EXCESS):
    return full_brake_floor
  window_multiplier, coast_floor = _STYLE.get(braking_style, _STYLE['standard'])
  coast_window = akima_interp(v_ego, _WINDOW_BP, _WINDOW) * window_multiplier
  excess_scale = max(akima_interp(v_ego, _WINDOW_BP, _EXCESS), coast_window + 0.1)
  excess = max(0.0, v_ego - slc_target)
  if excess <= coast_window:
    return max(full_brake_floor, coast_floor)
  t = min(max((excess - coast_window) / (excess_scale - coast_window), 0.0), 1.0) ** 2
  return max(full_brake_floor, coast_floor + t * (full_brake_floor - coast_floor))


@dataclass(frozen=True)
class EasingRequest:
  now_ns: int
  target: float
  approach: bool


def accepted_easing(result, frame, *, selected, acknowledged, manual, approach):
  accepted = result.state.acceptance.accepted
  candidate = result.selection.observation.candidate if result.selection is not None else None
  applied = result.state.override.applied_selected_mps
  if (not acknowledged or manual or result.errors or accepted is None or candidate is None or
      result.acceptance is None or result.acceptance.control_target_mps is None or result.acceptance.state.pending is not None or
      candidate.speed_mps != accepted.candidate.speed_mps or not frame.authority.can_control or
      frame.pedal_pressed or frame.host.force_decel or result.state.override.persistent_selected_mps is not None or
      result.domain is None or result.domain.context is None or applied is None or
      not all(math.isfinite(v) for v in (selected, applied)) or abs(selected-applied)>.01 or
      abs(selected-result.domain.context.effective_cluster_mps)>.5):
    return None
  return EasingRequest(frame.now_ns, selected, bool(approach))


def accepted_easing_floor(request, *, now_ns, selected, target, speed, full_brake_floor, style, blocked):
  # This is a same-cycle, acknowledged selected-speed request, not a second cap.
  if (not isinstance(request, EasingRequest) or blocked or type(request.approach) is not bool or
      type(request.now_ns) is not int or type(now_ns) is not int or not 0<=now_ns-request.now_ns<=100_000_000 or
      not all(type(v) in (int,float) and math.isfinite(v) for v in (request.target,selected,target,speed,full_brake_floor)) or
      abs(selected-request.target)>.01 or abs(target-selected)>.01 or not 1<=target<=70 or
      not -3.5<=full_brake_floor<0 or not 4<speed<=70 or speed<=target+.05):
    return None
  return shaped_slc_floor(speed,target,full_brake_floor,style,approach=request.approach)
