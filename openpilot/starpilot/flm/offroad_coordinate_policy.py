"""Symptom-bound candidates in the controller's existing 30-coordinate units."""

import math
from statistics import median

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, DEADBANDS, RAW_DOMAINS, WINDOWS
from openpilot.starpilot.flm.offroad_store import MAX_ROUTE_STEP
from openpilot.starpilot.flm.offroad_surface import route_bounds, route_values


def recorded_observation(bucket, direction, peak_score, speed_mps):
  """Put the pinned classifier's unlike score units on one severity scale.

  Turn/tracking scores are acceleration errors; chatter already uses ratios.
  These are the existing classifier thresholds, not new event acceptance
  limits. Classification, independent support and native checks are unchanged.
  """
  if (
    type(peak_score) not in (int, float) or not math.isfinite(peak_score) or peak_score < 0
    or type(speed_mps) not in (int, float) or not math.isfinite(speed_mps) or speed_mps < 0
  ):
    raise ValueError('Actual finite classifier score and speed required')
  scale = {
    'understeer': 0.20, 'oversteer': 0.20,
    'late_turn_in': 0.16, 'early_turn_in': 0.16,
    'unwind_too_slow': 0.14, 'unwind_too_fast': 0.14,
    'low_speed_unwillingness': 0.18,
  }.get(bucket, 1.0)
  return {'bucket': bucket, 'direction': direction, 'severity': peak_score / scale, 'speed_mps': speed_mps,
          'raw_peak_score': peak_score, 'score_scale': scale, 'severity_units': 'classifier_threshold_multiple'}


def _symptom_steps(summary, previous):
  bucket, side = summary['bucket'], summary['direction']
  speed = summary.get('speed_mps')
  severity = summary['severity']
  if (
    type(speed) not in (int, float) or not math.isfinite(speed) or speed < 0
    or type(severity) not in (int, float) or not math.isfinite(severity) or severity <= 0
  ):
    return {}
  strength = 0.005 * min(severity, 1.2)
  steps = {}
  if bucket == 'center_chatter':
    steps['highway_center_taper_max' if speed >= 25 else 'center_taper_max'] = strength
    knots = (0, 5, 10, 15, 25)
    for index, name in enumerate(DEADBANDS):
      if (index == 0 and speed < knots[1] or index == 4 and speed > knots[3]
          or 0 < index < 4 and knots[index - 1] < speed < knots[index + 1]):
        steps[name] = strength
    return steps
  if side not in ('left', 'right'):
    return {}
  if bucket in ('understeer', 'oversteer'):
    steps['ff_gain_' + side] = strength * (1 if bucket == 'understeer' else -1)
  if bucket in ('late_turn_in', 'early_turn_in', 'low_speed_unwillingness'):
    increase = 1 if bucket != 'early_turn_in' else -1
    steps['turn_in_threshold_reduction_' + side] = strength * increase
    steps['turn_in_boost_' + side] = strength * increase
    if speed < 6:
      steps['crawl_turn_in_ff_boost_' + side] = strength * increase
      if bucket == 'low_speed_unwillingness':
        steps['low_speed_angle_assist_max_torque'] = strength
    steps['curvy_turn_in_trim_' + side] = -strength * increase
    if previous['curvy_turn_in_trim_' + side] > 0:
      _window_steps(steps, previous, 'curvy_turn_in_trim_speed', speed, -increase * strength)
  if bucket in ('unwind_too_slow', 'unwind_too_fast'):
    increase = 1 if bucket == 'unwind_too_slow' else -1
    if speed >= 23.2:
      steps['unwind_threshold_increase_' + side] = strength * increase
    for prefix in ('unwind_taper', 'curvy_unwind_floor_relief', 'curvy_unwind_extra_reduction'):
      steps[prefix + '_' + side] = strength * increase
    if previous['curvy_unwind_floor_relief_' + side] > 0 or previous['curvy_unwind_extra_reduction_' + side] > 0:
      _window_steps(steps, previous, 'curvy_speed', speed, increase * strength)
  return steps


def _window_steps(steps, previous, prefix, speed, expansion):
  originals = {name: value for name, value, _ in RAW_DOMAINS}
  edges = {edge: originals[prefix + '_' + edge] * (1 + previous[prefix + '_' + edge]) for edge in ('min', 'max')}
  edge = min(edges, key=lambda key: abs(speed - edges[key]))
  if abs(speed - edges[edge]) <= 3:
    steps[prefix + '_' + edge] = expansion * (-1 if edge == 'min' else 1)


def coordinate_candidates(observations, previous):
  """Freeze one-coordinate alternatives from at least three original windows.

  Deadbands/assist remain physical-normalized coordinates, and speed-window
  steps remain relative coordinates. Only the actual replay hooks convert them
  to vehicle degrees/torque/m/s and enforce the unchanged original hard limits.
  """
  previous = route_values(previous)
  votes = {}
  symptoms = {}
  for window, summary in observations:
    for name, step in _symptom_steps(summary, previous).items():
      votes.setdefault(name, {}).setdefault(window, []).append(step)
      symptoms.setdefault(name, set()).add((summary['bucket'], summary['direction']))
  candidates = []
  for name in NAMES:
    windows = votes.get(name, {})
    all_steps = [step for steps in windows.values() for step in steps]
    if len(windows) < 3 or not all_steps or min(all_steps) * max(all_steps) <= 0:
      continue
    step = median(median(steps) for steps in windows.values())
    low, high = route_bounds(name)
    target = round(max(low, min(high, previous[name] + max(-MAX_ROUTE_STEP, min(MAX_ROUTE_STEP, step)))), 12)
    if target == previous[name]:
      continue
    changed = dict(previous, **{name: target})
    candidates.append({
      'values': changed,
      'parameter': 'stable:' + name,
      'delta': target - previous[name],
      'policy': 'original_stable_recommended_bounded_route_v1',
      'coordinate_policy': 'symptom_bound_all30_v1',
      'original_windows': sorted(windows),
      'symptoms': [list(row) for row in sorted(symptoms[name])],
      'coordinate_units': (
        'relative_speed_window' if name in WINDOWS else
        'physical_normalized' if name in DEADBANDS or name == 'low_speed_angle_assist_max_torque' else 'amplitude'
      ),
    })
  return candidates


def angle_coordinate_candidates(estimate, windows, previous, *, side, phase):
  """Extend an identified PID angle symptom to its actual supported hooks.

  Use the existing angle gain/lag/chatter decision, not acceleration thresholds
  or an invented PID friction model. Each vote represents one source window
  in that estimate; automatic use still needs independent and native checks.
  """
  from openpilot.starpilot.flm.offroad_tracking import TrackingEstimate, suggest, route_side
  from openpilot.starpilot.flm.pid_stages import SUPPORTED

  if type(estimate) is not TrackingEstimate or estimate.domain != 'steering_angle':
    return []
  if tuple(w.identity for w in windows) != estimate.windows or any(w.domain != 'steering_angle' for w in windows):
    raise ValueError('The original PID estimate windows are required')
  # Only identify the existing symptom here. A capped primary knob must not
  # hide other useful hooks; all recipe targets below use the actual prior.
  primary = suggest(estimate, dict.fromkeys(previous, 0.0), side=side, phase=phase)
  if primary is None:
    return []
  bucket = {'center': 'center_chatter', 'turn_in': 'late_turn_in', 'unwind': 'unwind_too_slow'}.get(phase)
  if phase == 'steady':
    bucket = 'understeer' if primary['delta'] > 0 else 'oversteer'
  observations = []
  for window in windows:
    speed = sum(p.speed_mps for p in window.points) / len(window.points)
    row = {'bucket': bucket, 'direction': route_side(estimate.domain, side), 'severity': 1.0, 'speed_mps': speed}
    observations.append((window.identity, row))
    if phase == 'turn_in' and estimate.gain_interval[1] < .98 and speed < 6:
      # Existing identified lag plus deficient crawl-speed angle response.
      # No new score threshold; each source window still counts only once.
      observations.append((window.identity, dict(row, bucket='low_speed_unwillingness')))
  result = []
  # PID has no five-point friction curve. Expand the explicit 30-coordinate
  # prior with only those inapplicable zero fields for the shared recipe code.
  for candidate in coordinate_candidates(observations, route_values(previous, legacy_allowed=True)):
    if candidate['parameter'].removeprefix('stable:') in SUPPORTED:
      result.append(dict(candidate, policy='observed_angle_tracking_v1', coordinate_policy='angle_tracking_supported26_v1',
                         evidence_domain='steering_angle'))
  return result
