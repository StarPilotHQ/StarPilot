"""Observed closed-loop tracking estimates for completed drives.

Desired is the recorded input, actual is the recorded response. This describes
tracking; it is NOT an independently identified EPS plant or live permission.
"""

from bisect import bisect_right
from dataclasses import dataclass
import hashlib
import math

from openpilot.starpilot.flm.offroad_evidence import RouteWindow, MAX_GAP_NS, tracking_metrics

LAGS_NS = tuple(range(0, 500_000_001, 10_000_000))


@dataclass(frozen=True)
class TrackingEstimate:
  context: str
  domain: str
  cohort: str
  gain: float
  lag_ns: int
  bias: float
  gain_interval: tuple[float, float]
  lag_interval_ns: tuple[int, int]
  bias_interval: tuple[float, float]
  reference_rms: float
  residual_rms: float
  windows: tuple[str, ...]
  source_sha256: str
  error_reversal_hz: float
  overshoot_fraction: float
  peak_error_fraction: float
  residual_interval: tuple[float, float] = (0.0, 0.0)
  reference_variability: float = 0.0


def _allowed(permitted):
  if not permitted():
    raise ValueError('Parked tracking analysis cancelled')


def _fit(rows):
  weight = sum(1.0 / count for _, _, count in rows)
  mx = sum(x / count for x, _, count in rows) / weight
  my = sum(y / count for _, y, count in rows) / weight
  variance = sum((x - mx) ** 2 / count for x, _, count in rows)
  if variance <= 1e-12 * weight:
    return None
  gain = sum((x - mx) * (y - my) / count for x, y, count in rows) / variance
  bias = my - gain * mx
  if not math.isfinite(gain) or not math.isfinite(bias) or not 0 < gain <= 4:
    return None
  loss = sum((y - gain * x - bias) ** 2 / count for x, y, count in rows) / weight
  return loss, gain, bias


def _select(table, ids, *, permitted=lambda: True):
  choices = []
  for lag in LAGS_NS:
    _allowed(permitted)
    rows = tuple(row for wid, values in table[lag].items() if wid in ids for row in values)
    fitted = _fit(rows)
    if fitted is not None:
      loss, gain, bias = fitted
      choices.append((loss, lag, gain, bias))
  return sorted(choices)


def _interval(values):
  mean = sum(values) / len(values)
  variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
  radius = 12.707 * math.sqrt(variance / len(values))
  return min(min(values), mean - radius), max(max(values), mean + radius)


def estimate(windows, *, cohort, permitted=lambda: True):
  _allowed(permitted)
  if (
    type(windows) is not tuple
    or not 3 <= len(windows) <= 24
    or type(cohort) is not str
    or not 0 < len(cohort) <= 96
    or any(type(w) is not RouteWindow or not w.valid() for w in windows)
    or len({w.identity for w in windows}) != len(windows)
    or len({(w.domain, w.points[0].context_key) for w in windows}) != 1
  ):
    raise ValueError('Whole distinct same-context tracking windows required')
  seen = set()
  table = {lag: {} for lag in LAGS_NS}
  rms = []
  for window in windows:
    _allowed(permitted)
    points = window.points
    if any(not p.active or not p.healthy or p.driver_override or p.saturated for p in points):
      raise ValueError('Ineligible original tracking sample')
    for p in points:
      key = (p.route, p.mono_ns)
      if key in seen:
        raise ValueError('Original tracking samples cannot be scored twice')
      seen.add(key)
    stamps = tuple(p.mono_ns for p in points)
    targets = tuple(p for p in points if p.mono_ns >= stamps[0] + LAGS_NS[-1])
    if len(targets) < 20:
      return None
    pairs = {lag: [] for lag in LAGS_NS}
    for index, p in enumerate(targets):
      if index % 32 == 0:
        _allowed(permitted)
      for lag in LAGS_NS:
        at = bisect_right(stamps, p.mono_ns - lag) - 1
        if at < 0 or p.mono_ns - lag - stamps[at] > MAX_GAP_NS:
          return None
        pairs[lag].append((points[at].desired, p.actual, len(targets)))
      rms.append(p.desired**2)
    for lag in LAGS_NS:
      table[lag][window.identity] = tuple(pairs[lag])
  ids = tuple(w.identity for w in windows)
  choices = _select(table, ids, permitted=permitted)
  if not choices:
    return None
  loss, lag, gain, bias = choices[0]
  alternatives = []
  for omitted in ids:
    selected = _select(table, tuple(wid for wid in ids if wid != omitted), permitted=permitted)
    if not selected:
      return None
    alternatives.append(selected[0])
  gain_interval = _interval(tuple(v[2] for v in alternatives))
  bias_interval = _interval(tuple(v[3] for v in alternatives))
  indistinguishable = [value[1] for value in choices if value[0] <= loss + max(1e-12, loss * 0.01)]
  lag_interval = (min(indistinguishable + [v[1] for v in alternatives]), max(indistinguishable + [v[1] for v in alternatives]))
  reference_rms = math.sqrt(sum(rms) / len(rms))
  if reference_rms <= 1e-6:
    return None
  residual_interval = _interval(tuple(math.sqrt(max(0.0, v[0])) for v in alternatives))
  residual_interval = (max(0.0, residual_interval[0]), residual_interval[1])
  variability = (
    math.sqrt(
      sum(sum((b.desired - a.desired) ** 2 / ((b.mono_ns - a.mono_ns) / 1e9) for a, b in zip(w.points, w.points[1:])) for w in windows)
      / sum((w.points[-1].mono_ns - w.points[0].mono_ns) / 1e9 for w in windows)
    )
    / reference_rms
  )
  source = hashlib.sha256(
    repr((cohort, ids, tuple((w.identity, tuple((p.mono_ns, p.desired, p.actual) for p in w.points)) for w in windows))).encode()
  ).hexdigest()
  metrics = tuple(tracking_metrics(w.points) for w in windows)
  reversal = sum(m['error_reversal_hz'] for m in metrics) / len(metrics)
  overshoot = math.sqrt(sum(m['overshoot_rms'] ** 2 for m in metrics) / len(metrics)) / reference_rms
  peak = max(m['worst_absolute_error'] for m in metrics) / reference_rms
  return TrackingEstimate(
    windows[0].points[0].context_key,
    windows[0].domain,
    cohort,
    gain,
    lag,
    bias,
    gain_interval,
    lag_interval,
    bias_interval,
    reference_rms,
    math.sqrt(loss),
    ids,
    source,
    reversal,
    overshoot,
    peak,
    residual_interval,
    variability,
  )


def _score_bounds(value, reference):
  low, high = value.gain_interval
  gain_low = 0.0 if low <= 1 <= high else min(abs(1 - low), abs(1 - high))
  gain_high = max(abs(1 - low), abs(1 - high))
  lo, hi = value.bias_interval
  bias_low = 0.0 if lo <= 0 <= hi else min(abs(lo), abs(hi))
  bias_high = max(abs(lo), abs(hi))
  return (
    gain_low**2 + (value.lag_interval_ns[0] / 200_000_000) ** 2 + (bias_low / reference) ** 2,
    gain_high**2 + (value.lag_interval_ns[1] / 200_000_000) ** 2 + (bias_high / reference) ** 2,
  )


def compare_observed(before, after):
  """Same units/context/cohort; compare gain and TIME lag, not raw route RMS.

  A smaller desired amplitude or lower reference frequency alone cannot earn
  confirmation. This remains observational evidence, not proof of causation.
  """
  if (
    type(before) is not TrackingEstimate
    or type(after) is not TrackingEstimate
    or (before.context, before.domain, before.cohort) != (after.context, after.domain, after.cohort)
    or set(before.windows) & set(after.windows)
  ):
    raise ValueError('Distinct drives in the exact same tracking cohort required')
  reference = min(before.reference_rms, after.reference_rms)
  old = _score_bounds(before, reference)
  new = _score_bounds(after, reference)
  fit_supported = before.residual_rms <= 0.25 * before.reference_rms and after.residual_rms <= 0.25 * after.reference_rms
  no_oscillation = after.error_reversal_hz <= 1.25 * before.error_reversal_hz
  no_overshoot = after.overshoot_fraction <= before.overshoot_fraction + 1e-9
  no_peak_regression = after.peak_error_fraction <= 1.25 * before.peak_error_fraction + 1e-9
  quality = no_oscillation and no_overshoot and no_peak_regression
  noise_floor = 0.015 if before.domain == 'lateral_acceleration' else 0.1
  old_residual = before.residual_interval
  new_residual = after.residual_interval
  old_fraction = (old_residual[0] / before.reference_rms, old_residual[1] / before.reference_rms)
  new_fraction = (new_residual[0] / after.reference_rms, new_residual[1] / after.reference_rms)

  def point_score(value):
    return (1 - value.gain) ** 2 + (value.lag_ns / 200_000_000) ** 2 + (value.bias / reference) ** 2

  # Separate unwanted residual motion from the response forced by the input.
  # Both PHYSICAL and input-normalized residual intervals must improve. This
  # avoids rewarding a smaller reference with unchanged fractional error/noise.
  # Comparable reference variability is required only for this residual rule;
  # the identified forced-response rule already compares TIME lag directly.
  comparable_input = (
    min(before.reference_variability, after.reference_variability) > 0
    and max(before.reference_variability, after.reference_variability) <= 1.05 * min(before.reference_variability, after.reference_variability)
    and max(before.reference_rms, after.reference_rms) <= 1.10 * min(before.reference_rms, after.reference_rms)
  )
  residual_improved = bool(
    quality
    and comparable_input
    and old_residual[0] >= noise_floor
    and new_residual[1] < old_residual[0] - max(0.0001, 0.005 * old_residual[0])
    and new_fraction[1] < old_fraction[0] - 1e-4
    and point_score(after) <= point_score(before) + 1e-8
  )
  forced_improved = bool(fit_supported and quality and new[1] < old[0] - 1e-8)
  return {
    'observed_only': True,
    'input_standardized': True,
    'no_oscillation_regression': no_oscillation,
    'no_overshoot_regression': no_overshoot,
    'no_peak_error_regression': no_peak_regression,
    'before_score_interval': old,
    'after_score_interval': new,
    'fit_supported': fit_supported,
    'improved': forced_improved or residual_improved,
    'improvement_kind': 'forced_tracking' if forced_improved else 'uncommanded_residual' if residual_improved else 'none',
    'residual_input_comparable': comparable_input,
    'before_physical_residual_interval': old_residual,
    'after_physical_residual_interval': new_residual,
    'worse': bool(not quality or fit_supported and new[0] > old[1] + 1e-8),
  }


def suggest(estimate, previous, *, side, phase):
  """A bounded NEXT-DRIVE CANDIDATE, never a confirmed tune or Params write."""
  from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, coordinate_bounds

  if (
    type(estimate) is not TrackingEstimate
    or type(previous) is not dict
    or set(previous) != set(NAMES)
    or side not in ('left', 'right', 'both')
    or phase not in ('steady', 'turn_in', 'unwind', 'center')
  ):
    raise ValueError('Exact canonical prior and original input cohort required')
  if phase != 'center' and estimate.residual_rms > 0.25 * estimate.reference_rms:
    return None
  name, step = None, 0.0
  if phase == 'center':
    floor = 0.015 if estimate.domain == 'lateral_acceleration' else 0.1
    if estimate.error_reversal_hz >= 1.0 and estimate.residual_interval[0] >= max(floor, 0.25 * estimate.reference_rms):
      name = 'highway_center_taper_max' if estimate.cohort.split(':')[0] == '2' else 'center_taper_max'
      step = 0.005
  elif phase == 'steady':
    if estimate.gain_interval[1] < 0.98:
      name, step = 'ff_gain_' + side, 0.005
    elif estimate.gain_interval[0] > 1.02:
      name, step = 'ff_gain_' + side, -0.005
  elif phase == 'turn_in' and estimate.lag_interval_ns[0] >= 100_000_000 and estimate.gain_interval[1] <= 1.2:
    name, step = 'turn_in_boost_' + side, 0.005
  elif phase == 'unwind' and estimate.lag_interval_ns[0] >= 100_000_000:
    name, step = 'unwind_taper_' + side, 0.005
  if name is None:
    return None
  low, high = coordinate_bounds(name)
  value = max(low, min(high, float(previous[name]) + step))
  if value == previous[name]:
    return None
  values = dict(previous)
  values[name] = value
  return {
    'values': values,
    'parameter': name,
    'delta': value - previous[name],
    'state': 'candidate_requires_controller_checks',
    'road_improvement_confirmed': False,
    'source_sha256': estimate.source_sha256,
  }
