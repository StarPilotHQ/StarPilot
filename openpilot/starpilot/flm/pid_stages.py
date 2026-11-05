"""Optional physical-unit stages for existing torque-actuated angle PID laws.

Neutral stages never change original PID inputs/output or controller memory.
There is no inferred torque_params/friction baseline for a PID union.
"""

from dataclasses import dataclass
import math
from openpilot.starpilot.flm.selected_effects import _selected_source
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, DEADBANDS

REVISION = 'pid-selected-stage-snapshot-v2'
DEADBAND_UNIT_REVISION = 'pid-original-angle-error-relative-degrees-v1'
UNSUPPORTED = frozenset(
  (
    'turn_in_threshold_reduction_left',
    'turn_in_threshold_reduction_right',
    'unwind_threshold_increase_left',
    'unwind_threshold_increase_right',
  )
)
SUPPORTED = tuple(name for name in NAMES if name not in UNSUPPORTED)


@dataclass(frozen=True)
class PidFrame:
  speed: float
  setpoint: float
  jerk: float
  baseline_ff: float
  error_deg: float
  desired_angle_deg: float
  actual_angle_deg: float
  active: bool
  driver_override: bool
  limited: bool

  def valid(self):
    return bool(
      all(
        math.isfinite(value)
        for value in (self.speed, self.setpoint, self.jerk, self.baseline_ff, self.error_deg, self.desired_angle_deg, self.actual_angle_deg)
      )
      and 0 <= self.speed <= 90
    )


def coordinates_supported(values):
  return all(name in NAMES and math.isfinite(value) and (name not in UNSUPPORTED or value == 0.0) for name, value in values.items())


def canonical_feedforward(source, frame, *, original_anchor):
  """Same Dom universal FF equations, applied to this PID's own CI FF."""
  source._check(original_anchor)
  source.stage_calls += 1
  if source.stage_calls != 1 or not coordinates_supported(source.values):
    raise ValueError('unsupported or repeated PID effect stage')
  if not frame.valid() or not frame.active or frame.driver_override or frame.limited:
    return frame.baseline_ff
  math_source, surface = source.math, source.surface
  center = math_source._gm_get_flm_full_surface_center_taper_scale(surface, surface.profile, frame.setpoint, frame.speed, True)
  scale = math_source._gm_get_flm_full_surface_ff_scale(surface, surface.profile, frame.setpoint, frame.jerk, frame.speed, True) * center
  candidate = frame.baseline_ff if scale == 1.0 else frame.baseline_ff * scale
  if not math.isfinite(candidate) or candidate * frame.baseline_ff < 0.0 or abs(candidate - frame.baseline_ff) > 0.25 * abs(frame.baseline_ff) + 1e-12:
    raise ValueError('PID feedforward original effect envelope')
  return candidate


def canonical_error(source, frame, *, original_anchor):
  """PID-specific degree-error attenuation; never a fabricated friction model.

  The original same-frame angle error is the physical normalizer. The five
  canonical coordinates each contribute at most 25% of that error, intersected
  with the original Dom per-speed degree hardcaps. This differs deliberately
  from the torque controller's friction/VM-per-degree normalization.
  """
  source._check(original_anchor)
  source.deadzone_calls += 1
  if source.deadzone_calls != 1 or not coordinates_supported(source.values):
    raise ValueError('unsupported or repeated PID degree-error stage')
  if not frame.valid() or not frame.active or frame.driver_override or frame.limited:
    return frame.error_deg
  normalizer = abs(frame.error_deg)
  if normalizer == 0.0 or not any(source.values[name] for name in DEADBANDS):
    return frame.error_deg
  names = ('center_deadband_crawl_deg', 'center_deadband_low_deg', 'center_deadband_mid_deg', 'center_deadband_fast_deg', 'center_deadband_highway_deg')
  caps = (0.30, 0.30, 0.20, 0.12, 0.08)
  knots = (0.0, 5.0, 10.0, 15.0, 25.0)
  degree_values = []
  for name, cap in zip(names, caps, strict=True):
    coordinate = source.values[name]
    if not 0.0 <= coordinate <= 0.25:
      raise ValueError('PID degree-error coordinate envelope')
    degree_values.append(min(coordinate * normalizer, cap))
  speed = max(frame.speed, 0.0)
  deadband = degree_values[-1]
  for i in range(len(knots) - 1):
    if speed <= knots[i + 1]:
      fraction = max(0.0, min(1.0, (speed - knots[i]) / (knots[i + 1] - knots[i])))
      deadband = degree_values[i] + fraction * (degree_values[i + 1] - degree_values[i])
      break
  if not math.isfinite(deadband) or not 0.0 <= deadband <= 0.25 * normalizer:
    raise ValueError('PID original degree-error effect envelope')
  return math.copysign(normalizer - deadband, frame.error_deg)


def error_for_frame(parent, cs, baseline_error, *, active, limited):
  observation = getattr(parent, 'automatic_flm_observation', None) or getattr(parent, 'route_flm_observation', None)
  frame = observation.stage if observation is not None else None
  source = _selected_source(parent)
  if (
    source is None
    or observation is None
    or frame is None
    or not getattr(parent, 'automatic_pid_stage_admitted', False)
    or not frame.valid()
    or not active
    or cs.steeringPressed
    or limited
  ):
    return baseline_error
  try:
    candidate = source.selected_pid_error(frame, original_anchor=source.original_effect_anchor)
    if (
      type(candidate) not in (int, float)
      or not math.isfinite(candidate)
      or candidate * baseline_error < 0.0
      or not 0.75 * abs(baseline_error) <= abs(candidate) <= abs(baseline_error)
    ):
      return baseline_error
    observation.learned_error = candidate
    return candidate
  except (AttributeError, TypeError, ValueError, OverflowError):
    return baseline_error


class PidObservation:
  def __init__(self):
    self.stage = None
    self.learned_ff = 0.0
    self.output = 0.0
    self.learned_error = 0.0

  def begin_frame(self):
    self.stage = None

  def finish_frame(self, controller, log, *, selected_command, **unused):
    frame = self.stage
    if frame is None or not frame.valid():
      return None
    values = (
      ('speed', frame.speed),
      ('setpoint', frame.setpoint),
      ('jerk', frame.jerk),
      ('baseline_ff', frame.baseline_ff),
      ('learned_ff', self.learned_ff),
      ('angle_error_deg', frame.error_deg),
      ('learned_error_deg', self.learned_error),
      ('deadband_deg', abs(frame.error_deg) - abs(self.learned_error)),
      ('deadband_normalizer_deg', abs(frame.error_deg)),
      ('desired_angle_deg', frame.desired_angle_deg),
      ('actual_angle_deg', frame.actual_angle_deg),
      ('pid_p', float(log.p)),
      ('pid_i', float(log.i)),
      ('pid_d', 0.0),
      ('pid_f', float(log.f)),
      ('pid_pos_limit', float(controller.pid.pos_limit)),
      ('pid_neg_limit', float(controller.pid.neg_limit)),
      ('selected_command', float(selected_command)),
      ('ff_factor', float(controller.ff_factor)),
    )
    if not all(math.isfinite(value) for _, value in values):
      return None
    return REVISION, values, (0.0, 0.0)


def begin_frame(parent, cs, *, desired_curvature, desired_angle_deg, actual_angle_deg, error_deg, baseline_ff, active, limited):
  speed = float(cs.vEgo)
  setpoint = float(desired_curvature) * speed**2
  previous = getattr(parent, 'automatic_pid_previous_setpoint', None)
  jerk = 0.0 if previous is None else (setpoint - previous) / float(parent.dt)
  parent.automatic_pid_previous_setpoint = setpoint if active else None
  frame = PidFrame(
    speed,
    setpoint,
    jerk,
    float(baseline_ff),
    float(error_deg),
    float(desired_angle_deg),
    float(actual_angle_deg),
    bool(active),
    bool(cs.steeringPressed),
    bool(limited),
  )
  observation = getattr(parent, 'automatic_flm_observation', None) or getattr(parent, 'route_flm_observation', None)
  if observation is not None:
    observation.stage, observation.learned_ff = frame, baseline_ff
    observation.learned_error = frame.error_deg
  parent.automatic_pid_stage_admitted = False
  source = _selected_source(parent)
  if source is None or not frame.valid() or not active or frame.driver_override or limited:
    return baseline_ff
  try:
    candidate = source.selected_pid_stage(frame, original_anchor=source.original_effect_anchor)
    if (
      type(candidate) not in (float, int)
      or not math.isfinite(candidate)
      or candidate * baseline_ff < 0.0
      or abs(candidate - baseline_ff) > 0.25 * abs(baseline_ff) + 1e-12
    ):
      return baseline_ff
    parent.automatic_pid_stage_admitted = True
    if observation is not None:
      observation.learned_ff = candidate
    return candidate
  except (AttributeError, TypeError, ValueError, OverflowError):
    return baseline_ff


def finish_frame(parent, cs, *, desired_angle_deg, actual_angle_deg, baseline_output, active, limited):
  source = _selected_source(parent)
  observation = getattr(parent, 'automatic_flm_observation', None) or getattr(parent, 'route_flm_observation', None)
  frame = observation.stage if observation is not None else None
  if (
    source is None
    or not getattr(parent, 'automatic_pid_stage_admitted', False)
    or observation is None
    or frame is None
    or not frame.valid()
    or not active
    or cs.steeringPressed
    or limited
    or cs.vEgo < 0.1 * 0.44704
  ):
    return baseline_output
  try:
    # Dom helper's policy torque is opposite to positive PID angle-error output.
    # No invented PID friction term: actual normalized output is its normalizer.
    output = source.selected_output(
      float(cs.vEgo),
      frame.setpoint,
      float(desired_angle_deg),
      float(actual_angle_deg),
      -float(baseline_output),
      0.0,
      original_anchor=source.original_effect_anchor,
    )
    if not math.isfinite(output):
      return baseline_output
    return -max(-parent.steer_max, min(parent.steer_max, output))
  except (AttributeError, TypeError, ValueError, OverflowError):
    return baseline_output
