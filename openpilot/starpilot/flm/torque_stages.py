# Copyright (c) 2026, firestar5683 and StarPilot contributors
# SPDX-License-Identifier: MIT
"""Bounded torque stage hooks; the common owner admits profiles.

Existing selected tune/shaping/conversion/output caps remain authoritative.
No Params, PID, factor, table, inverse or final actuator writes.
"""

from dataclasses import dataclass
import math
from openpilot.starpilot.flm.selected_effects import (
  apply_selected_stage,
  bounded_profile_on_limited_frame,
  apply_selected_friction as apply_selected_friction,
  apply_selected_deadzone as apply_selected_deadzone,
  apply_selected_output as apply_selected_output,
)

REVISION = 'torque-post-baseline-prefriction-v1'


@dataclass(frozen=True)
class StageFrame:
  speed: float
  setpoint: float
  jerk: float
  baseline_ff: float
  baseline_threshold: float
  active: bool
  driver_override: bool
  limited: bool
  curvature_limited: bool = False

  def valid(self):
    return (
      all(math.isfinite(v) for v in (self.speed, self.setpoint, self.jerk, self.baseline_ff, self.baseline_threshold))
      and 0 <= self.speed <= 90
      and self.baseline_threshold > 0
    )


def apply_stages(parent, cs, setpoint, jerk, ff, threshold, *, active, limited, curvature_limited=False):
  """One selected-policy call after baseline FF/threshold, before friction.

  Missing source and inactive/manual/off source emit exact unchanged baseline.
  Source methods are owned by core and may only return admitted residuals.
  """
  source = getattr(parent, 'automatic_flm_stages', None)
  frame = StageFrame(
    float(cs.vEgo), float(setpoint), float(jerk), float(ff), float(threshold), bool(active), bool(cs.steeringPressed), bool(limited), bool(curvature_limited)
  )
  observer = getattr(parent, 'automatic_flm_observation', None)
  if observer is not None and frame.valid():
    observer.observe_stage(frame, ff, (0.0, 0.0))
  if getattr(parent, 'route_flm_effects', None) is not None:
    candidate_ff, candidate_threshold = apply_selected_stage(parent, frame)
    if observer is not None and frame.valid():
      observer.observe_stage(frame, candidate_ff, (0.0, 0.0), candidate_threshold)
    return candidate_ff, candidate_threshold
  if getattr(parent, 'manual_flm_effects', None) is not None:
    return apply_selected_stage(parent, frame)
  if source is None:
    return ff, threshold
  if not frame.valid() or not frame.active or frame.driver_override:
    return ff, threshold
  if frame.limited:
    # Preserve the limiter flag and PID freeze. Only an admitted bounded full
    # profile may shape its request before the unchanged native actuator limit.
    if curvature_limited or not bounded_profile_on_limited_frame(parent):
      return ff, threshold
    candidate_ff, candidate_threshold = apply_selected_stage(parent, frame)
    if observer is not None:
      observer.observe_stage(frame, candidate_ff, tuple(parent.automatic_flm_effects.applied_coordinates), candidate_threshold)
    return candidate_ff, candidate_threshold
  try:
    # Core owns monotonic tick, identity/manual/TTL/policy validation and slew.
    # delta is relative to this selected controller's fully shaped baseline.
    delta_ff, delta_threshold = source.torque_residual(frame, stage_revision=REVISION)
    if not all(type(v) in (float, int) and math.isfinite(v) for v in (delta_ff, delta_threshold)) or abs(delta_ff) > 0.03 or abs(delta_threshold) > 0.03:
      return ff, threshold
    candidate_ff = ff if delta_ff == 0 else ff * (1 + delta_ff)
    candidate_threshold = threshold if delta_threshold == 0 else threshold * (1 + delta_threshold)
    if not math.isfinite(candidate_ff) or not math.isfinite(candidate_threshold) or candidate_threshold <= 0:
      return ff, threshold
    effect = getattr(parent, 'automatic_flm_effects', None)
    if effect is not None:
      # Only one admitted profile exists; legacy stage residual is neutral for V2.
      candidate_ff, candidate_threshold = apply_selected_stage(parent, frame)
    if observer is not None:
      observer.observe_stage(frame, candidate_ff, tuple(effect.applied_coordinates if effect is not None else source.applied_coordinates), candidate_threshold)
    return candidate_ff, candidate_threshold
  except (AttributeError, TypeError, ValueError, OverflowError):
    return ff, threshold


def extra_deadband(parent, cs, *, active, limited):
  """Separate proven center coordinate; absent evidence is exactly zero.

  Core must not derive this from scalar response slope or ordinary FF evidence.
  Existing steering angle deadzone and any manual FLM contribution stay intact.
  """
  source = getattr(parent, 'automatic_flm_stages', None)
  if source is None or not active or limited or cs.steeringPressed:
    return 0.0
  try:
    delta = source.torque_deadband(float(cs.vEgo), stage_revision=REVISION)
    return float(delta) if type(delta) in (float, int) and math.isfinite(delta) and 0 <= delta <= 0.01 else 0.0
  except (AttributeError, TypeError, ValueError, OverflowError):
    return 0.0
