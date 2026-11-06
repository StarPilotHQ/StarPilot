"""Revocation-only onroad check for a parked-derived bounded map.

No fitting, candidate selection, persistence, acceptance or new permission runs
here. The original normalized gross-error and reversal thresholds are retained.
Only complete, input-matched original windows can trigger a withdrawal.
"""

from collections import deque
from collections.abc import Callable
import math
import os

from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES as NAMES
from openpilot.starpilot.flm.offroad_analysis import cohort
from openpilot.starpilot.flm.offroad_evidence import read_point, RouteWindow, window_identity, tracking_metrics
from openpilot.starpilot.flm.offroad_processor import window_demand
from openpilot.starpilot.flm.offroad_surface import REVISION

from openpilot.starpilot.flm.offroad_surface import stop_marker


def _matches(left, right):
  return (
    left['domain'] == right['domain']
    and max(left['duration_ns'], right['duration_ns']) <= 1.15 * min(left['duration_ns'], right['duration_ns'])
    and max(left['speed_high'], right['speed_high']) <= 1.15 * min(left['speed_low'], right['speed_low'])
    and min(left['goal_rms'], right['goal_rms']) >= 0
    # Revocation only: source-feedback gross guards compare this same road
    # regime without allowing a different input shape to evade a withdrawal.
    # Acceptance remains the separate independent standardized offroad rule.
    and len(left['goal_shape']) == len(right['goal_shape']) == 8
  )


class TrialGuard:
  def __init__(self, scope_key, values, trial):
    self.scope_key, self.values = scope_key, dict(values)
    self.rows = deque(maxlen=256)
    self.future = []
    self.last_ns = 0
    self.override_ns = 0
    self.stopped = None
    self.start_trial: Callable[[], bool] | None = None
    self.cohort = None
    self.baseline = ()
    try:
      if type(values) is not dict or set(values) != set(NAMES):
        raise ValueError('Full current map')
      baseline = tuple(trial['gross_baseline'])
      ids = set(trial['before']['training']['windows']) | set(trial['before']['heldout']['windows'])
      if trial.get('gross_policy') == 'separate_original_2s_v1':
        roles = tuple(tuple(trial['gross_original_roles'][role]['windows']) for role in ('training', 'heldout'))
        if any(not 3 <= len(role) <= 12 for role in roles) or len(set(roles[0] + roles[1])) != len(roles[0] + roles[1]):
          raise ValueError('Independent actual two-second qualification roles required')
        ids = set(roles[0] + roles[1])
      parent_policy = trial.get('gross_policy') == 'fixed_input_matched_2s_v1'
      keys = {r['parent_identity'] if parent_policy else r['identity'] for r in baseline}
      if not 6 <= len(baseline) <= 24 or keys != ids or len(keys) != len(baseline):
        raise ValueError('All predeclared baseline windows')
      for row in baseline:
        metrics, demand = row['metrics'], row['demand']
        if (
          not 0 <= demand['goal_rms']
          or not 0 < demand['speed_low'] <= demand['speed_high']
          or not 1_000_000_000 <= demand['duration_ns'] <= 10_000_000_000
          or len(demand['goal_shape']) != 8
          or any(type(metrics[k]) not in (int, float) or not math.isfinite(metrics[k]) or metrics[k] < 0 for k in ('rmse', 'error_reversal_hz'))
        ):
          raise ValueError('Finite original gross baseline')
      self.baseline = baseline
      self.cohort = trial['cohort']
      self.mean_score = sum(r['metrics']['rmse'] / max(0.1, r['demand']['goal_rms']) for r in baseline) / len(baseline)
      self.mean_reversals = sum(r['metrics']['error_reversal_hz'] for r in baseline) / len(baseline)
      self.reversal_limit = max(1.25 * self.mean_reversals, self.mean_reversals + 0.3)
      self.single_reversal_limit = max(self.reversal_limit, max(r['metrics']['error_reversal_hz'] for r in baseline))
    except (KeyError, TypeError, ValueError, OverflowError):
      self.stopped = 'baseline_unavailable'

  @property
  def marker(self):
    return None if self.stopped is None else stop_marker(self.scope_key, self.values, self.stopped)

  def observe(self, event, companions, control, *, now_ns):
    if self.stopped is not None:
      return self.marker
    stamp = int(event.logMonoTime)
    if stamp == self.last_ns:
      return None
    if self.last_ns and not 0 < stamp - self.last_ns <= 50_000_000:
      self.rows.clear()
    self.last_ns = stamp
    if control is not None and control.override:
      self.override_ns = stamp
    if (
      not 0 < stamp <= now_ns <= stamp + 100_000_000
      or control is None
      or not control.valid
      or not control.active
      or not control.enabled
      or control.manual
      or control.override
      or control.fault
      or control.limited
      or control.saturated
      or abs(control.control_ns - stamp) > 30_000_000
      or control.route_effect_revision != REVISION
      or control.route_configuration_scope != self.scope_key
      or dict(control.route_applied_coordinates) != self.values
    ):
      self.rows.clear()
      return None
    if self.start_trial is not None:
      start, self.start_trial = self.start_trial, None
      try:
        claimed = start()
      except (OSError, ValueError):
        claimed = False
      if not claimed:
        self.stopped = 'baseline_unavailable'
        return self.marker
    point = read_point(event, companions, route='current-route-revocation-only', segment=0, context_key=self.scope_key)
    if point is None:
      self.rows.clear()
      return None
    if point.driver_override:
      self.override_ns = stamp
    if (
      not point.active
      or not point.healthy
      or point.saturated
      or point.driver_override
      or point.speed_mps < 2
      or self.override_ns
      and stamp <= self.override_ns + 1_000_000_000
    ):
      self.rows.clear()
      return None
    self.rows.append(point)
    if self.rows[-1].mono_ns - self.rows[0].mono_ns < 2_350_000_000:
      return None
    start = self.rows[0].mono_ns
    rows = tuple(p for p in self.rows if p.mono_ns < start + 2_000_000_000)
    while self.rows and self.rows[0].mono_ns < start + 2_000_000_000:
      self.rows.popleft()
    if len(rows) < 40 or rows[-1].mono_ns - rows[0].mono_ns < 1_950_000_000:
      return None
    window = RouteWindow(window_identity(rows), rows)
    if not window.valid() or cohort(window) != self.cohort:
      return None
    demand = window_demand(window)
    if not any(_matches(r['demand'], demand) for r in self.baseline):
      return None
    metrics = tracking_metrics(rows)
    score = metrics['rmse'] / max(0.1, demand['goal_rms'])
    if score > 1.5 * max(self.mean_score, 0.03) or metrics['error_reversal_hz'] > self.single_reversal_limit:
      self.stopped = 'gross_tracking_or_oscillation_worse'
    else:
      self.future.append(metrics['error_reversal_hz'])
      if len(self.future) == len(self.baseline):
        if sum(self.future) / len(self.future) > self.reversal_limit:
          self.stopped = 'oscillation_batch_worse'
        self.future.clear()
    return self.marker


def from_configuration(cp, params, cache_directory, *, vehicle_session):
  from openpilot.starpilot.flm.offroad_worker import _current_scope, trial_document
  from openpilot.starpilot.flm.offroad_store import startup_row
  from openpilot.starpilot.flm.offroad_cache import AssessmentCache

  try:
    scope = _current_scope(cp, params)
    row, _ = startup_row(params, scope)
  except (OSError, KeyError, TypeError, ValueError, AttributeError, OverflowError):
    return None
  if row is None:
    return None
  try:
    cache = AssessmentCache(cache_directory)
    trial = trial_document(cache, row['analysis_sha256'])
    guard = TrialGuard(scope.key(), row['values'], trial)
    if row['state'] == 'ready_for_next_drive' and guard.stopped is None:
      if os.path.lexists(cache.directory / ('trial-' + row['analysis_sha256'] + '.json')):
        guard.stopped = 'baseline_unavailable'
      else:
        guard.start_trial = lambda digest=row['analysis_sha256'], session=vehicle_session: cache.claim_trial(digest, session)
  except (OSError, KeyError, TypeError, ValueError, OverflowError):
    guard = TrialGuard(scope.key(), row['values'], {})
  return guard
