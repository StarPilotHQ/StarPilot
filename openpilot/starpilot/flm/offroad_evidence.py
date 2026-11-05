"""Historical route evidence for automatic parked tuning.

This module never creates live permission, renews a timestamp, writes Params or
applies a correction. Torque-actuated PID and torque laws keep distinct units.
"""

from dataclasses import dataclass
import hashlib
import math

MAX_JOIN_NS = 30_000_000
MAX_GAP_NS = 50_000_000
MAX_SAMPLES = 60_000
DOMAINS = {
  'torqueState': ('lateral_acceleration', 'desiredLateralAccel', 'actualLateralAccel'),
  'pidState': ('steering_angle', 'steeringAngleDesiredDeg', 'steeringAngleDeg'),
}


@dataclass(frozen=True)
class RoutePoint:
  route: str
  segment: int
  mono_ns: int
  domain: str
  context_key: str
  desired: float
  actual: float
  speed_mps: float
  commanded_torque: float | None
  emitted_torque: float | None
  control_header_ns: int
  output_header_ns: int
  active: bool
  driver_override: bool
  saturated: bool
  healthy: bool

  def valid(self):
    return (
      type(self.route) is str
      and 0 < len(self.route) <= 180
      and type(self.segment) is int
      and self.segment >= 0
      and type(self.mono_ns) is int
      and self.mono_ns > 0
      and self.domain in ('lateral_acceleration', 'steering_angle')
      and type(self.context_key) is str
      and len(self.context_key) == 64
      and not self.context_key.strip('0123456789abcdef')
      and all(type(v) in (int, float) and math.isfinite(v) for v in (self.desired, self.actual, self.speed_mps))
      and 0 <= self.speed_mps <= 90
      and all(v is None or type(v) in (int, float) and math.isfinite(v) for v in (self.commanded_torque, self.emitted_torque))
      and all(type(v) is int and v >= 0 for v in (self.control_header_ns, self.output_header_ns))
      and all(type(v) is bool for v in (self.active, self.driver_override, self.saturated, self.healthy))
    )


@dataclass(frozen=True)
class RouteWindow:
  identity: str
  points: tuple[RoutePoint, ...]

  @property
  def domain(self):
    return self.points[0].domain

  def valid(self):
    return (
      type(self.points) is tuple
      and 20 <= len(self.points) <= MAX_SAMPLES
      and all(p.valid() for p in self.points)
      and len({(p.route, p.segment, p.domain, p.context_key) for p in self.points}) == 1
      and all(0 < b.mono_ns - a.mono_ns <= MAX_GAP_NS for a, b in zip(self.points, self.points[1:], strict=False))
      and self.identity == window_identity(self.points)
    )


def window_identity(rows):
  if not rows:
    raise ValueError('nonempty original window required')
  key = (rows[0].route, rows[0].segment, rows[0].domain, rows[0].context_key, tuple((p.mono_ns, p.desired, p.actual) for p in rows))
  return hashlib.sha256(repr(key).encode()).hexdigest()


def torque_vehicle(cp):
  # An actuator enum alone is insufficient: failed startup can publish a
  # misleading torque CP for angle hardware. No passive/dashcam tune is allowed.
  return bool(
    str(cp.steerControlType) == 'torque' and not cp.passive and not cp.dashcamOnly and not cp.notCar and cp.lateralTuning.which() in ('torque', 'pid')
  )


def read_point(event, companions, *, route, segment, context_key):
  """Read recorded status and nearby recorded companions, never a live receipt.

  Companion headers are retained explicitly. This is diagnostic alignment,
  not a statement that Controls consumed the latest companion in the log.
  """
  if not event.valid or event.which() != 'controlsState':
    return None
  state = event.controlsState.lateralControlState
  kind = state.which()
  if kind not in DOMAINS:
    return None
  stamp = int(event.logMonoTime)
  for name in ('carState', 'carControl'):
    companion = companions.get(name)
    if companion is None or not companion.valid or abs(int(companion.logMonoTime) - stamp) > MAX_JOIN_NS:
      return None
  cs = companions['carState'].carState
  cc = companions['carControl'].carControl
  value = getattr(state, kind)
  domain, desired_name, actual_name = DOMAINS[kind]
  if any(name not in value.schema.fields for name in (desired_name, actual_name, 'active', 'saturated')):
    return None
  output = companions.get('carOutput')
  output_current = bool(output is not None and output.valid and abs(int(output.logMonoTime) - stamp) <= MAX_JOIN_NS)
  point = RoutePoint(
    route,
    segment,
    stamp,
    domain,
    context_key,
    float(getattr(value, desired_name)),
    float(getattr(value, actual_name)),
    float(cs.vEgo),
    float(cc.actuators.torque),
    float(output.carOutput.actuatorsOutput.torque) if output_current else None,
    int(companions['carControl'].logMonoTime),
    int(output.logMonoTime) if output_current else 0,
    bool(value.active and cc.latActive),
    bool(cs.steeringPressed or cs.brakePressed or cs.regenBraking),
    bool(value.saturated),
    bool(cs.canValid and not cs.canTimeout and not cs.steerFaultTemporary and not cs.steerFaultPermanent),
  )
  return point if point.valid() else None


def eligible_points(points):
  """Preserve the original override buffers; gaps are never interpolated."""
  if not isinstance(points, tuple) or len(points) > MAX_SAMPLES or any(not p.valid() for p in points):
    raise ValueError('bounded valid original route points required')
  if any(b.mono_ns <= a.mono_ns for a, b in zip(points, points[1:], strict=False)):
    raise ValueError('original route points must increase')
  if len({(p.route, p.domain, p.context_key) for p in points}) > 1:
    raise ValueError('process each original route/controller context separately')
  overrides = [p.mono_ns for p in points if p.driver_override]
  result = []
  cursor = 0
  for point in points:
    while cursor < len(overrides) and overrides[cursor] + 1_000_000_000 < point.mono_ns:
      cursor += 1
    excluded = cursor < len(overrides) and overrides[cursor] - 350_000_000 <= point.mono_ns
    if not excluded and point.active and point.healthy and not point.saturated and point.speed_mps >= 2.0:
      result.append(point)
  return tuple(result)


def whole_windows(points, *, duration_ns=2_000_000_000, minimum_samples=40):
  """Fixed source-time windows, selected before inspecting tracking errors."""
  if type(duration_ns) is not int or not 1_000_000_000 <= duration_ns <= 10_000_000_000:
    raise ValueError('window duration')
  if type(minimum_samples) is not int or not 20 <= minimum_samples <= 1000:
    raise ValueError('window sample bound')
  eligible = eligible_points(points)
  groups = []
  for point in eligible:
    previous = groups[-1][-1] if groups else None
    if (
      previous is None
      or point.route != previous.route
      or point.segment != previous.segment
      or point.domain != previous.domain
      or point.context_key != previous.context_key
      or point.mono_ns - previous.mono_ns > MAX_GAP_NS
    ):
      groups.append([])
    groups[-1].append(point)
  result = []
  for group in groups:
    start = group[0].mono_ns
    buckets = {}
    for point in group:
      bucket = (point.mono_ns - start) // duration_ns
      buckets.setdefault(bucket, []).append(point)
    for rows in buckets.values():
      if len(rows) < minimum_samples or rows[-1].mono_ns - rows[0].mono_ns < duration_ns - MAX_GAP_NS:
        continue
      result.append(RouteWindow(window_identity(rows), tuple(rows)))
  return tuple(result)


def tracking_metrics(points, *, model_rmse=0.0):
  if not points or any(not p.valid() for p in points) or len({(p.domain, p.context_key) for p in points}) != 1:
    raise ValueError('one real tracking domain required')
  if type(model_rmse) not in (int, float) or not math.isfinite(model_rmse) or model_rmse < 0:
    raise ValueError('finite same-domain model error required')
  from openpilot.starpilot.flm.live_tracking_episodes import tracking_reversal_rate

  errors = tuple(p.desired - p.actual for p in points)
  duration = (points[-1].mono_ns - points[0].mono_ns) / 1e9
  return {
    'domain': points[0].domain,
    'samples': len(points),
    'rmse': math.sqrt(sum(error * error for error in errors) / len(errors)),
    'mean_absolute_error': sum(abs(error) for error in errors) / len(errors),
    'worst_absolute_error': max(abs(error) for error in errors),
    'error_slew_rms': math.sqrt(
      sum(((right - left) / ((b.mono_ns - a.mono_ns) / 1e9)) ** 2 for a, b, left, right in zip(points, points[1:], errors, errors[1:], strict=False))
      / (len(points) - 1)
    )
    if len(points) > 1
    else 0.0,
    'error_reversal_hz': tracking_reversal_rate(errors, duration, model_rmse) if duration > 0 else 0.0,
    'overshoot_rms': math.sqrt(
      sum((abs(p.actual) if p.desired == 0 else max(0.0, math.copysign(1.0, p.desired) * (p.actual - p.desired))) ** 2 for p in points) / len(points)
    ),
  }


def compare_same_windows(windows, predicted_actual, *, minimum_windows=3, model_rmse=0.0):
  """Evaluate a model's candidate on the SAME untouched recorded targets.

  This is predicted held-out performance only. It cannot itself authorize
  saving or actuation: physical-model, whole-controller replay and bounds
  qualification are required by the offroad owner.
  """
  if (
    type(minimum_windows) is not int
    or not 3 <= minimum_windows <= 24
    or not minimum_windows <= len(windows) <= 24
    or any(not w.valid() for w in windows)
    or len({w.identity for w in windows}) != len(windows)
    or len({(w.domain, w.points[0].context_key) for w in windows}) != 1
  ):
    raise ValueError('independent original held-out windows required')
  seen = set()
  for window in windows:
    for point in window.points:
      key = (point.route, point.mono_ns, point.domain, point.context_key)
      if key in seen:
        raise ValueError('held-out windows cannot share original samples')
      seen.add(key)
  if set(predicted_actual) != {w.identity for w in windows}:
    raise ValueError('candidate must use every exact held-out window')
  improvements = []
  metrics = []
  for window in windows:
    values = predicted_actual[window.identity]
    if type(values) is not tuple or len(values) != len(window.points) or any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
      raise ValueError('complete finite same-window prediction required')
    baseline = tracking_metrics(window.points, model_rmse=model_rmse)
    from dataclasses import replace

    forecast = tuple(replace(point, actual=value) for point, value in zip(window.points, values, strict=True))
    candidate = tracking_metrics(forecast, model_rmse=model_rmse)
    loss = sum((p.desired - value) ** 2 for p, value in zip(window.points, values, strict=True)) / len(values)
    before = baseline['rmse'] ** 2
    improvement = before - loss
    improvements.append(improvement)
    metrics.append(
      {
        'window': window.identity,
        'baseline_loss': before,
        'candidate_loss': loss,
        'no_error_slew_regression': candidate['error_slew_rms'] <= 1.25 * baseline['error_slew_rms'] + 1e-12,
        'no_oscillation_regression': candidate['error_reversal_hz'] <= 1.25 * baseline['error_reversal_hz'],
        'no_overshoot_regression': candidate['overshoot_rms'] <= baseline['overshoot_rms'] + 1e-12,
        'no_peak_error_regression': candidate['worst_absolute_error'] <= baseline['worst_absolute_error'] + 1e-12,
      }
    )
  mean = sum(improvements) / len(improvements)
  variance = sum((value - mean) ** 2 for value in improvements) / (len(improvements) - 1)
  lower = mean - 12.707 * math.sqrt(variance / len(improvements))
  return {
    'predicted_only': True,
    'windows': metrics,
    'lower_improvement_bound': lower,
    'every_window_improved': all(value > 0 for value in improvements),
    'positive_improvement_bound': lower > 0,
    'no_error_slew_regression': all(row['no_error_slew_regression'] for row in metrics),
    'no_oscillation_regression': all(row['no_oscillation_regression'] for row in metrics),
    'no_overshoot_regression': all(row['no_overshoot_regression'] for row in metrics),
    'no_peak_error_regression': all(row['no_peak_error_regression'] for row in metrics),
  }
