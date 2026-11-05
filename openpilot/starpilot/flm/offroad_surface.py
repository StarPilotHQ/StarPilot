"""Versioned automatic route effects relative to the selected native baseline.

Legacy/manual surfaces keep their existing composition. Here relative FF and
transition knobs act on actual native FF. Original phase/speed shapes,
per-coordinate bounds, sign and 25% stage envelopes remain authoritative.
"""

import hashlib
import math
from openpilot.starpilot.flm.canonical_surface_adapter import CanonicalSurfaceEffects, MANIFEST_SHA256, NAMES

LEGACY_REVISION = 'route-reference-relative-v2'
REVISION = 'route-reference-relative-v3'
CURVE_NAMES = ('friction_threshold_crawl', 'friction_threshold_low', 'friction_threshold_mid', 'friction_threshold_fast', 'friction_threshold_highway')
CURVE_SPEEDS = (0.0, 5.0, 10.0, 15.0, 25.0)
ROUTE_NAMES = NAMES + CURVE_NAMES
LEGACY_MANIFEST_SHA256 = hashlib.sha256((MANIFEST_SHA256 + ':' + LEGACY_REVISION).encode()).hexdigest()
ROUTE_MANIFEST_SHA256 = hashlib.sha256(
  repr((MANIFEST_SHA256, REVISION, CURVE_NAMES, CURVE_SPEEDS, 'fraction_of_actual_selected_threshold', (-0.25, 0.25))).encode()
).hexdigest()


def route_bounds(name):
  if name in CURVE_NAMES:
    return -0.25, 0.25
  from openpilot.starpilot.flm.canonical_surface_adapter import coordinate_bounds

  return coordinate_bounds(name)


def route_values(values, *, legacy_allowed=False):
  if type(values) is not dict or set(values) not in (set(ROUTE_NAMES), set(NAMES)):
    raise ValueError('Complete explicit route profile required')
  if set(values) == set(NAMES) and not legacy_allowed:
    raise ValueError('Explicit route curve fields required')
  result = {name: values.get(name, 0.0) for name in ROUTE_NAMES}
  for name, value in result.items():
    low, high = route_bounds(name)
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
      raise ValueError('Finite bounded route field required')
  return result


class RouteSurfaceEffects(CanonicalSurfaceEffects):
  def __init__(self, coordinates, baseline_revision, *, accumulated):
    values = route_values(accumulated, legacy_allowed=True)
    if type(coordinates) is not dict or any(name not in NAMES for name in coordinates):
      raise ValueError('Original canonical frame anchor required')
    super().__init__(coordinates, baseline_revision, accumulated={name: values[name] for name in NAMES})
    self.route_values = {name: self.values[name] if name in NAMES else values[name] for name in ROUTE_NAMES}
    self.curve_values = tuple(values[name] for name in CURVE_NAMES)
    self.full_profile_sha256 = hashlib.sha256(repr((ROUTE_MANIFEST_SHA256, self.full_profile_sha256, self.curve_values)).encode()).hexdigest()
    # Original threshold phase weights are in [0,1], with an existing .82..1.18
    # clamp. Refuse a profile whose curve could breach the composed 25% bound
    # at ANY phase, before admitting it to a real controller.
    reduction = max(self.values['turn_in_threshold_reduction_' + side] for side in ('left', 'right'))
    increase = max(self.values['unwind_threshold_increase_' + side] for side in ('left', 'right'))
    if (1.0 + max(self.curve_values)) * (1.0 + min(0.18, increase)) > 1.25 + 1e-12 or (1.0 + min(self.curve_values)) * (
      1.0 - min(0.18, reduction)
    ) < 0.75 - 1e-12:
      raise ValueError('Composed route threshold profile exceeds original 25% envelope')
    for side in ('left', 'right'):
      # Worst-case original phase weights are in [0,1]; reject combined excess.
      bound = (
        abs(self.values['ff_gain_' + side])
        + abs(self.values['turn_in_boost_' + side])
        + self.values['unwind_taper_' + side]
        + self.values['center_taper_max']
        + self.values['highway_center_taper_max']
        + self.values['curvy_turn_in_trim_' + side]
        + self.values['curvy_unwind_floor_relief_' + side]
        + self.values['curvy_unwind_extra_reduction_' + side]
        + self.values['crawl_turn_in_ff_boost_' + side]
      )
      if bound > 0.25:
        raise ValueError('Composed route FF envelope exceeds 25%')

  def _scale(self, frame):
    if frame.setpoint == 0.0:
      return 1.0
    m = self.math
    phase = m._gm__flm_full_surface_transition_phase(self.surface, frame.setpoint, frame.jerk)
    side = 'left' if frame.setpoint >= 0 else 'right'
    # These are relative settings of the ACTUAL selected FF, not an added
    # legacy FF term gated by an unrelated amplitude's onset/cutoff.
    relative = (
      1.0 + self.values['ff_gain_' + side] + self.values['turn_in_boost_' + side] * max(phase, 0.0) - self.values['unwind_taper_' + side] * max(-phase, 0.0)
    )
    remaining = m._gm_get_flm_full_surface_ff_scale(self.surface, self.surface.profile, frame.setpoint, frame.jerk, frame.speed, False)
    center = m._gm_get_flm_full_surface_center_taper_scale(self.surface, self.surface.profile, frame.setpoint, frame.speed, True)
    return relative * remaining * center

  def _ff(self, frame):
    value = frame.baseline_ff * self._scale(frame)
    if not math.isfinite(value) or value * frame.baseline_ff < 0 or abs(value - frame.baseline_ff) > 0.25 * abs(frame.baseline_ff) + 1e-12:
      raise ValueError('Route effect outside original stage envelope')
    return value

  def _threshold(self, frame, threshold):
    if not any(self.curve_values):
      return threshold
    speed = frame.speed
    if speed <= CURVE_SPEEDS[0]:
      fraction = self.curve_values[0]
    elif speed >= CURVE_SPEEDS[-1]:
      fraction = self.curve_values[-1]
    else:
      for i in range(len(CURVE_SPEEDS) - 1):
        if CURVE_SPEEDS[i] <= speed <= CURVE_SPEEDS[i + 1]:
          weight = (speed - CURVE_SPEEDS[i]) / (CURVE_SPEEDS[i + 1] - CURVE_SPEEDS[i])
          fraction = self.curve_values[i] * (1.0 - weight) + self.curve_values[i + 1] * weight
          break
    result = threshold * (1.0 + fraction)
    if not math.isfinite(result) or result <= 0 or abs(result - frame.baseline_threshold) > 0.25 * frame.baseline_threshold + 1e-12:
      raise ValueError('Composed route threshold exceeds original 25% stage envelope')
    return result

  def selected_stage(self, frame, *, original_anchor):
    # Retain original once-per-frame validation, threshold math and guards.
    baseline, threshold = super().selected_stage(frame, original_anchor=original_anchor)
    if not frame.valid() or not frame.active or frame.driver_override or frame.limited:
      return frame.baseline_ff, frame.baseline_threshold
    return self._ff(frame), self._threshold(frame, threshold)

  def selected_pid_stage(self, frame, *, original_anchor):
    # PID has no fabricated friction baseline; its own CI FF is the normalizer.
    if any(self.curve_values):
      raise ValueError('Torque-PID has no fabricated friction threshold')
    super().selected_pid_stage(frame, original_anchor=original_anchor)
    if not frame.valid() or not frame.active or frame.driver_override or frame.limited:
      return frame.baseline_ff
    return self._ff(frame)


def replay_source(values, baseline_revision):
  """Child-only numeric probe; never builds a live envelope or authority."""
  from openpilot.starpilot.flm.selected_effects import EffectReplaySource

  complete = route_values(values, legacy_allowed=True)
  obj = EffectReplaySource.from_coordinates({NAMES[0]: complete[NAMES[0]]}, baseline_revision, accumulated={name: complete[name] for name in NAMES})
  obj.source = RouteSurfaceEffects({NAMES[0]: complete[NAMES[0]]}, baseline_revision, accumulated=complete)
  obj.original_effect_anchor = obj.source.original_effect_anchor
  obj.applied_coordinates = obj.source.applied_coordinates
  return obj


STOP_REASONS = ('gross_tracking_or_oscillation_worse', 'oscillation_batch_worse', 'baseline_unavailable')


def stop_marker(scope_key, values, reason):
  import json
  import hashlib

  if reason not in STOP_REASONS:
    raise ValueError('Known route withdrawal reason required')
  digest = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
  return 'route_quality_stop:' + scope_key + ':' + digest + ':' + reason
