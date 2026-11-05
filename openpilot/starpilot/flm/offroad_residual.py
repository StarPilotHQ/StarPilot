"""Measured unwanted motion on calm inputs; no fictitious gain/lag model.

Stable's chatter detector does not require an excited reference. These records
describe actual complete windows, while independent current controller/native
checks and a later-drive comparison remain separate obligations.
"""

from dataclasses import dataclass
import hashlib
import math

from openpilot.starpilot.flm.offroad_evidence import RouteWindow, tracking_metrics


@dataclass(frozen=True)
class ResidualEstimate:
  context: str
  domain: str
  cohort: str
  windows: tuple
  source_sha256: str
  reference_rms: float
  reference_variability: float
  mean_reference: float
  residual_rms: float
  residual_interval: tuple
  normalized_residual_interval: tuple
  error_reversal_hz: float
  overshoot_rms: float
  peak_error: float
  error_slew_rms: float
  kind: str = 'uncommanded_residual_v1'

  def __post_init__(self):
    def digest(value):
      return type(value) is str and len(value) == 64 and not value.strip('0123456789abcdef')

    numeric = (
      self.reference_rms,
      self.reference_variability,
      self.residual_rms,
      self.error_reversal_hz,
      self.overshoot_rms,
      self.peak_error,
      self.error_slew_rms,
    )
    if (
      self.kind != 'uncommanded_residual_v1'
      or not digest(self.context)
      or not digest(self.source_sha256)
      or self.domain not in ('lateral_acceleration', 'steering_angle')
      or type(self.cohort) is not str
      or not 1 <= len(self.cohort) <= 96
      or type(self.windows) is not tuple
      or not 3 <= len(self.windows) <= 12
      or len(set(self.windows)) != len(self.windows)
      or any(not digest(w) for w in self.windows)
      or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in numeric)
      or type(self.mean_reference) not in (int, float)
      or not math.isfinite(self.mean_reference)
    ):
      raise ValueError('Bounded original residual estimate required')
    for interval in (self.residual_interval, self.normalized_residual_interval):
      if (
        type(interval) is not tuple
        or len(interval) != 2
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in interval)
        or not 0 <= interval[0] <= interval[1]
      ):
        raise ValueError('Finite actual residual interval required')


def _interval(values):
  mean = sum(values) / len(values)
  variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
  radius = 12.707 * math.sqrt(variance / len(values))
  return max(0.0, min(min(values), mean - radius)), max(max(values), mean + radius)


def estimate_residual(windows, *, cohort, permitted=lambda: True):
  if (
    type(windows) is not tuple
    or not 3 <= len(windows) <= 12
    or any(type(w) is not RouteWindow or not w.valid() for w in windows)
    or len({w.identity for w in windows}) != len(windows)
    or len({(w.domain, w.points[0].context_key) for w in windows}) != 1
  ):
    raise ValueError('Distinct complete same-context residual windows required')
  seen = set()
  metrics = []
  references = []
  normalized = []
  variations = []
  means = []
  for window in windows:
    if not permitted():
      raise ValueError('Parked tracking analysis cancelled')
    points = window.points
    if any(not p.active or not p.healthy or p.driver_override or p.saturated for p in points):
      raise ValueError('Original residual window is ineligible')
    for point in points:
      key = (point.route, point.mono_ns)
      if key in seen:
        raise ValueError('Residual observations cannot be scored twice')
      seen.add(key)
    metric = tracking_metrics(points)
    metrics.append(metric)
    reference = math.sqrt(sum(p.desired**2 for p in points) / len(points))
    references.append(reference)
    floor = 0.015 if window.domain == 'lateral_acceleration' else 0.1
    normalizer = max(reference, floor)
    normalized.append(metric['rmse'] / normalizer)
    duration = (points[-1].mono_ns - points[0].mono_ns) / 1e9
    variation = math.sqrt(sum((b.desired - a.desired) ** 2 / ((b.mono_ns - a.mono_ns) / 1e9) for a, b in zip(points, points[1:])) / duration)
    variations.append(variation / normalizer)
    means.append(sum(p.desired for p in points) / len(points))
  source = hashlib.sha256(repr((cohort, tuple(w.identity for w in windows))).encode()).hexdigest()
  return ResidualEstimate(
    windows[0].points[0].context_key,
    windows[0].domain,
    cohort,
    tuple(w.identity for w in windows),
    source,
    math.sqrt(sum(v * v for v in references) / len(references)),
    sum(variations) / len(variations),
    sum(means) / len(means),
    math.sqrt(sum(m['rmse'] ** 2 for m in metrics) / len(metrics)),
    _interval(tuple(m['rmse'] for m in metrics)),
    _interval(tuple(normalized)),
    sum(m['error_reversal_hz'] for m in metrics) / len(metrics),
    math.sqrt(sum(m['overshoot_rms'] ** 2 for m in metrics) / len(metrics)),
    max(m['worst_absolute_error'] for m in metrics),
    math.sqrt(sum(m['error_slew_rms'] ** 2 for m in metrics) / len(metrics)),
  )


def compare_residual(before, after):
  if (
    type(before) is not ResidualEstimate
    or type(after) is not ResidualEstimate
    or (before.context, before.domain, before.cohort) != (after.context, after.domain, after.cohort)
    or set(before.windows) & set(after.windows)
  ):
    raise ValueError('Independent actual residual observations required')
  floor = 0.015 if before.domain == 'lateral_acceleration' else 0.1

  def comparable(left, right, ratio, zero):
    return max(left, right) <= zero or min(left, right) > zero and max(left, right) <= ratio * min(left, right)

  inputs = (
    comparable(before.reference_rms, after.reference_rms, 1.10, 1e-6)
    and comparable(before.reference_variability, after.reference_variability, 1.05, 1e-6)
    and abs(before.mean_reference - after.mean_reference) <= 0.1 * max(floor, before.reference_rms, after.reference_rms)
  )
  quality = (
    after.error_reversal_hz <= 1.25 * before.error_reversal_hz + 1e-12
    and after.overshoot_rms <= before.overshoot_rms + 1e-9
    and after.peak_error <= 1.25 * before.peak_error + 1e-12
    and after.error_slew_rms <= 1.25 * before.error_slew_rms + 1e-12
  )
  physical = before.residual_interval[0] >= floor and after.residual_interval[1] < before.residual_interval[0] - max(
    0.0001, 0.005 * before.residual_interval[0]
  )
  normalized = after.normalized_residual_interval[1] < before.normalized_residual_interval[0] - 1e-4
  return {
    'observed_only': True,
    'input_standardized': bool(inputs),
    'kind': 'uncommanded_residual_v1',
    'improved': bool(inputs and quality and physical and normalized),
    'worse': not quality,
    'residual_input_comparable': bool(inputs),
    'no_smoothness_regression': bool(quality),
    'before_physical_residual_interval': before.residual_interval,
    'after_physical_residual_interval': after.residual_interval,
    'before_normalized_residual_interval': before.normalized_residual_interval,
    'after_normalized_residual_interval': after.normalized_residual_interval,
    'forced_response_identified': False,
    'causal_road_benefit_proven': False,
  }


def analyze_stable_windows(windows, *, permitted=lambda: True):
  """Fixed input-only cohorts; calm references need no fictitious gain fit."""
  from dataclasses import asdict
  from openpilot.starpilot.flm.offroad_analysis import cohort, _spread, MAX_COHORT_WINDOWS

  if type(windows) is not tuple or any(not w.valid() for w in windows):
    raise ValueError('Original Stable windows required')
  groups = {}
  for window in windows:
    key = cohort(window)
    if key is not None:
      groups.setdefault(key, []).append(window)
  results = []
  for key, rows in sorted(groups.items()):
    if not permitted():
      raise ValueError('Parked tracking analysis cancelled')
    chosen = _spread(sorted(rows, key=lambda w: (w.points[0].segment, w.points[0].mono_ns)), MAX_COHORT_WINDOWS)
    training, heldout = tuple(chosen[::2]), tuple(chosen[1::2])
    if len(training) < 3 or len(heldout) < 3:
      results.append({'cohort': key, 'whole_windows': len(rows), 'reason': 'more_independent_stable_windows_required'})
      continue
    results.append(
      {
        'cohort': key,
        'whole_windows': len(rows),
        'training': asdict(estimate_residual(training, cohort=key, permitted=permitted)),
        'heldout': asdict(estimate_residual(heldout, cohort=key, permitted=permitted)),
        'reason': 'original_stable_symptoms_require_replication',
        'proposal': None,
      }
    )
  return {'version': 1, 'cohorts': results, 'automatically_saved': False, 'physical_improvement_proven': False}
