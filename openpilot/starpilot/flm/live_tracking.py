"""Optional learner's observation-triggered trials, never Controls permission.

Inputs are completed whole episodes from the genuine fullrate/physical join.
This module neither replays a route nor changes controller/native limits.
Application is an explicit separate lease, not a ReplayValidation token.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict, cast
import hashlib
import math
import statistics
from openpilot.starpilot.flm.automatic import T95

from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, GROUPS, coordinate_bounds, full_profile_sha256
from openpilot.starpilot.flm.live_trial_contract import LiveTrialBinding, LiveTrialOffer, VERSION, MAX_STEP, MAX_LEASE_NS, SOURCE_EXPERIMENT_STEP

if TYPE_CHECKING:
  from openpilot.starpilot.flm.live_tracking_response import ResponseCertificate, ValidatedResponseWitness

RETRY_NS = 20_000_000_000
TRIAL_TIMEOUT_NS = 120_000_000_000
MAX_EPISODES = 24
MAX_DISCOVERY_EPISODES = 128


def _beta_fraction(a: float, b: float, x: float) -> float:
  """Bounded continued fraction for the Student survival integral."""
  tiny = 1e-300
  c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
  d = 1.0 / (d if abs(d) > tiny else tiny)
  value = d
  for m in range(1, 129):
    for numerator in (m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)), -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))):
      d = 1.0 + numerator * d
      c = 1.0 + numerator / c
      d = 1.0 / (d if abs(d) > tiny else tiny)
      c = c if abs(c) > tiny else tiny
      delta = c * d
      value *= delta
    if abs(delta - 1.0) < 1e-13:
      return value
  raise ValueError('Student confidence integration did not converge')


def _student_survival(t: float, df: int) -> float:
  x, a, b = df / (df + t * t), df / 2.0, 0.5
  if x == 1.0:
    return 0.5
  scale = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
  beta = scale * _beta_fraction(a, b, x) / a if x < (a + 1.0) / (a + b + 2.0) else 1.0 - scale * _beta_fraction(b, a, 1.0 - x) / b
  return 0.5 * beta


@lru_cache(maxsize=256, typed=True)
def _critical(df: int, alpha: float) -> float:
  if type(df) is not int or df < 1 or type(alpha) is not float or not 0 < alpha < 0.5:
    raise ValueError('bounded Student confidence arguments')
  low, high = 0.0, 1.0
  while _student_survival(high, df) > alpha and high < 1e8:
    high *= 2.0
  if _student_survival(high, df) > alpha:
    raise ValueError('Student confidence outside numerical range')
  log_scale = math.lgamma((df + 1) / 2.0) - math.lgamma(df / 2.0) - 0.5 * math.log(df * math.pi)
  value = (low + high) / 2.0
  for _ in range(32):
    tail = _student_survival(value, df)
    if tail > alpha:
      low = value
    else:
      high = value
    # Verify a conservative upper root with the SAME survival function. No
    # approximate normal tail, rounded df or lower confidence is substituted.
    if abs(tail - alpha) <= alpha * 1e-13:
      upper = math.nextafter(value + 2e-12 * max(1.0, abs(value)), math.inf)
      if _student_survival(upper, df) <= alpha:
        return upper
    pdf = math.exp(log_scale - (df + 1) / 2.0 * math.log1p(value * value / df))
    candidate = value + (tail - alpha) / pdf if pdf > 0 else math.nan
    value = candidate if low < candidate < high else (low + high) / 2.0
  for _ in range(64):
    middle = (low + high) / 2.0
    if _student_survival(middle, df) > alpha:
      low = middle
    else:
      high = middle
  return high


@dataclass(frozen=True)
class HookSensitivity:
  name: str
  error_feature_sum: float
  feature_energy: float
  native_separated: int
  native_endpoints: int
  max_step_physical_rms: float  # RMS physical acceleration DELTA at .005, after native encoding and retained slope.
  native_gradient_complete: bool = False
  minus_native_separated: int | None = None
  plus_native_separated: int | None = None
  minus_max_step_physical_rms: float | None = None
  plus_max_step_physical_rms: float | None = None

  def native_effect(self, step: float) -> tuple[int, float]:
    if step == 0:
      return 0, 0.0
    if self.minus_native_separated is None:
      # Older numerical episodes retain their original secant contract.
      # Production probe episodes record both actual directions below.
      return self.native_separated, self.max_step_physical_rms
    if step < 0:
      return self.minus_native_separated, cast(float, self.minus_max_step_physical_rms)
    return cast(int, self.plus_native_separated), cast(float, self.plus_max_step_physical_rms)

  def validate(self):
    if (
      type(self.name) is not str
      or self.name not in NAMES
      or type(self.native_gradient_complete) is not bool
      or any(type(value) not in (int, float) or not math.isfinite(value) for value in (self.error_feature_sum, self.feature_energy, self.max_step_physical_rms))
      or self.feature_energy < 0
      or self.max_step_physical_rms < 0
      or type(self.native_separated) is not int
      or type(self.native_endpoints) is not int
      or not 0 <= self.native_separated <= self.native_endpoints <= 240
      or self.native_gradient_complete
      and self.native_endpoints < 4
    ):
      raise ValueError('actual finite hook/native probe support required')
    directional = (self.minus_native_separated, self.plus_native_separated,
                   self.minus_max_step_physical_rms, self.plus_max_step_physical_rms)
    if any(value is not None for value in directional) and (
      any(type(value) is not int or not 0 <= value <= self.native_endpoints for value in directional[:2])
      or any(value is None or type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in directional[2:])
      or any(count == 0 and effect != 0 for count, effect in zip(directional[:2], directional[2:], strict=True))
    ):
      raise ValueError('complete finite directional native probe support required')
    return self


@dataclass(frozen=True)
class TrackingEpisode:
  """Numerical evidence only; input adapters must verify actual source joins.

  sensitivity is an algorithmic actual-controller probe, NOT a plant fit.
  The response model/lag and future trial score are independently measured.
  """

  binding: LiveTrialBinding
  profile_sha256: str
  episode_id: str
  start_ns: int
  end_ns: int
  kind: str
  direction: int
  speed_low: float
  speed_high: float
  goal_rms: float
  error_rms: float
  reversal_hz: float
  endpoints: int
  control_max_gap_ns: int
  sensitivity: tuple[HookSensitivity, ...]
  goal_shape: tuple[float, ...] = ()
  native_gradient_complete: bool = False
  normalization_accel: float | None = None

  def validate(self):
    if self.normalization_accel is not None and (
      type(self.normalization_accel) not in (int, float) or not math.isfinite(self.normalization_accel) or self.normalization_accel != max(0.1, self.goal_rms)
    ):
      raise ValueError('explicit kinematic tracking normalization required')
    if (
      type(self.binding) is not LiveTrialBinding
      or type(self.kind) is not str
      or self.kind not in ('large', 'subtle')
      or type(self.direction) is not int
      or self.direction not in ((-1, 0, 1) if self.normalization_accel is not None else (-1, 1))
      or type(self.native_gradient_complete) is not bool
      or any(type(v) is not int for v in (self.start_ns, self.end_ns, self.endpoints, self.control_max_gap_ns))
      or not 0 < self.start_ns < self.end_ns
      or not 200_000_000 <= self.end_ns - self.start_ns <= 10_000_000_000
      or not 4 <= self.endpoints <= 240
      or not 0 < self.control_max_gap_ns <= 50_000_000
      or any(type(v) not in (int, float) or not math.isfinite(v) for v in (self.speed_low, self.speed_high, self.goal_rms, self.error_rms, self.reversal_hz))
      or not 0 < self.speed_low <= self.speed_high <= 1.15 * self.speed_low
      or min(self.goal_rms, self.error_rms, self.reversal_hz) < 0
      or type(self.sensitivity) is not tuple
      or len(self.sensitivity) > 30
      or type(self.goal_shape) is not tuple
      or len(self.goal_shape) != 8
      or any(type(value) not in (int, float) or not math.isfinite(value) for value in self.goal_shape)
    ):
      raise ValueError('complete real tracking episode required')
    self.binding.validate()
    if any(type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value) for value in (self.profile_sha256, self.episode_id)):
      raise ValueError('exact actual episode/profile identities required')
    names = set()
    for row in self.sensitivity:
      if type(row) is not HookSensitivity:
        raise ValueError('actual independent hook probe required')
      row.validate()
      if row.name in names:
        raise ValueError('distinct actual hook probes required')
      names.add(row.name)
    return self


def _map(values: tuple[tuple[str, float], ...]) -> dict[str, float]:
  if type(values) is not tuple or tuple(name for name, _ in values) != NAMES:
    raise ValueError('canonical complete thirty-coordinate target required')
  result = {}
  for name, value in values:
    low, high = coordinate_bounds(name)
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
      raise ValueError('original own-car coordinate domain')
    result[name] = float(value)
  return result


def _required(kind: str) -> int:
  return 3 if kind == 'large' else 9


def _look_quotas(required: int, feasible: int) -> tuple[int, ...]:
  later = min(2 * required, feasible, MAX_EPISODES)
  return (required, later) if required >= 9 and later >= required + 3 else (required,)


def _discovery_support(rows, kind, model_rmse):
  # Maneuver geometry and miss severity are separate. A modest tracking miss
  # in a substantial curve needs the longer small-error evidence path, rather
  # than being excluded by the large-error floor forever.
  if model_rmse is None:
    # Native visibility selects a bounded experiment, not permission to keep it.
    # One complete original window is enough to try a slow single-coordinate
    # step; three NEW uncensored baseline windows and later feedback still
    # decide its outcome. Quantized/missing probes never become sensitivity.
    return (tuple(rows[-1:]), 1) if rows else ((), None)
  large = tuple(rows[-3:])
  if kind == 'large' and len(large) == 3 and all(row.error_rms >= (0.10 if model_rmse is None else max(0.10, 6 * model_rmse)) for row in large):
    return large, 3
  small = tuple(rows[-9:])
  if len(small) == 9 and all(row.error_rms >= (0.015 if model_rmse is None else max(0.015, 3 * model_rmse)) for row in small):
    return small, 9
  return (), None


def _kind_for_group(group: str, kind: str) -> bool:
  # Actual native probes determine which hook affects this maneuver. A small
  # tracking oscillation can need feedforward/friction changes too; the label
  # alone must not exclude a measured controller effect. Confidence, native
  # visibility and independent future comparisons remain mandatory.
  return group in GROUPS and kind in ('large', 'subtle')


def _group_direction_matches(group: str, kind: str, direction: int) -> bool:
  return kind != 'large' or not (group.endswith('.left') and direction < 0 or group.endswith('.right') and direction > 0)


@dataclass(frozen=True)
class DemandSignature:
  kind: str
  direction: int
  duration_ns: int
  speed_low: float
  speed_high: float
  goal_rms: float
  goal_shape: tuple[float, ...]
  normalization_accel: float | None = None


def _demand(episode: TrackingEpisode) -> DemandSignature:
  return DemandSignature(
    episode.kind,
    episode.direction,
    episode.end_ns - episode.start_ns,
    episode.speed_low,
    episode.speed_high,
    episode.goal_rms,
    episode.goal_shape,
    episode.normalization_accel,
  )


@lru_cache(maxsize=256)
def _input_shape_descriptor(shape):
  return sum(shape) / 8, math.sqrt(sum((a - b) ** 2 for a, b in zip(shape, shape[1:], strict=False)) / 7)


def _matches_demand(left: DemandSignature, right: TrackingEpisode) -> bool:
  if left.normalization_accel is not None and right.normalization_accel is not None:
    # Source tracking compares normalized error across the same road regime.
    # Requiring an almost identical tiny steering waveform prevented three
    # fresh highway windows from ever completing a bounded experiment.
    # Direction, speed, window duration and all source/native/profile guards
    # still apply; model-based trials retain the stricter matching below.
    return (
      left.kind == right.kind
      and (left.kind != 'large' or left.direction == right.direction)
      and max(left.duration_ns, right.end_ns - right.start_ns) <= 1.15 * min(left.duration_ns, right.end_ns - right.start_ns)
      and max(left.speed_high, right.speed_high) <= 1.15 * min(left.speed_low, right.speed_low)
      and min(_score_scale(left), _score_scale(right)) > 0
    )
  return (
    left.kind == right.kind
    and left.direction == right.direction
    and max(left.duration_ns, right.end_ns - right.start_ns) <= 1.15 * min(left.duration_ns, right.end_ns - right.start_ns)
    and max(abs(a - b) for a, b in zip(left.goal_shape, right.goal_shape, strict=True)) <= 0.15
    and max(left.speed_high, right.speed_high) <= 1.15 * min(left.speed_low, right.speed_low)
    and min(_score_scale(left), _score_scale(right)) > 0
    and max(_score_scale(left), _score_scale(right)) <= 1.25 * min(_score_scale(left), _score_scale(right))
  )


def _matches(left: TrackingEpisode, right: TrackingEpisode) -> bool:
  return _matches_demand(_demand(left), right)


def _score_scale(episode) -> float:
  return episode.goal_rms if episode.normalization_accel is None else episode.normalization_accel


def _score(episode: TrackingEpisode) -> float:
  # Raw error falls when the road demands less acceleration even with an
  # unchanged proportional tracking error. Compare dimensionless tracking.
  return episode.error_rms / _score_scale(episode) if _score_scale(episode) > 0 else math.inf


def _noise(rows: Sequence[TrackingEpisode], model_rmse: float | None) -> tuple[float, float, int]:
  """Whole-episode uncertainty, with a measured-model noise floor.

  Positive short-range serial covariance inflates the mean variance. This
  bounded HAC estimate is an empirical road-noise defense, not a guarantee
  under arbitrary nonstationarity; source/demand matching remains mandatory.
  """
  values = [_score(row) for row in rows]
  center = statistics.mean(values)
  variance = statistics.variance(values)
  if model_rmse is not None:
    variance = max(variance, (model_rmse / min(row.goal_rms for row in rows)) ** 2)
  inflation = 1.0
  lags = min(3, len(rows) - 1)
  if variance > 0:
    for lag in range(1, lags + 1):
      covariance = sum((values[i] - center) * (values[i - lag] - center) for i in range(lag, len(values))) / len(values)
      inflation += 2 * (1.0 - lag / (lags + 1)) * max(0.0, covariance) / variance
  return variance, inflation, max(1, math.floor(len(rows) / inflation) - 1)


def _feedback_result(before, future):
  """Small-step empirical feedback, NOT an inferential confidence test.

  Both fixed batches are unselected actual tracking windows. Require a mean
  AND median improvement of at least0.5%, with at least80% of later windows
  staying within the observed baseline error envelope. Existing reversal, gross-regression and
  source/native guards are independently mandatory. Road drift can confound
  this rule; it does not establish physical causation or a false-positive rate.
  """
  if (
    len(before) not in (3, 9)
    or len(future) != len(before)
    or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in (*before, *future))
  ):
    raise ValueError('fixed finite empirical tracking batches required')
  old_mean, new_mean = statistics.mean(before), statistics.mean(future)
  old_median, new_median = statistics.median(before), statistics.median(future)
  margin = max(0.0001, 0.005 * old_mean)
  required = math.ceil(0.8 * len(future))
  if old_mean - new_mean > margin and old_median - new_median > margin and sum(value <= max(before) + margin for value in future) >= required:
    return 1
  if new_mean - old_mean > margin and new_median - old_median > margin and sum(value >= min(before) - margin for value in future) >= required:
    return -1
  return 0


def _welch_df(left_variance: float, left_df: int, right_variance: float, right_df: int) -> int:
  denominator = left_variance**2 / left_df + right_variance**2 / right_df
  return max(1, math.floor((left_variance + right_variance) ** 2 / denominator)) if denominator else left_df + right_df


class TrialState(TypedDict):
  trial_id: str
  phase: Literal['trial', 'confirmed', 'rollback']
  group: str
  names: tuple[str, ...]
  baseline: dict[str, float]
  target: dict[str, float]
  started_ns: int
  deadline_ns: int
  required_episodes: int
  critical: float
  alpha: float
  accepted: bool
  look_quotas: NotRequired[tuple[int, ...]]
  target_sequence: NotRequired[int]
  demand: NotRequired[DemandSignature]
  published: NotRequired[bool]
  prior_accepted: NotRequired[TrialState | None]
  source_rechecked_before: NotRequired[tuple[TrackingEpisode, ...]]


@dataclass
class SourceRecheck:
  """Inert A/B numbers; never a retained application or permission."""
  intent: TrialState
  before: tuple[TrackingEpisode, ...]
  future: tuple[TrackingEpisode, ...]
  context: tuple[LiveTrialBinding, ResponseCertificate, str]
  baseline: list[TrackingEpisode]
  first_ack_ns: int = 0


class DiscoveryState(TypedDict):
  group: str
  name: str
  target: dict[str, float]
  expected: float
  anchor: TrackingEpisode
  selected_end_ns: int
  quota: int
  expires_ns: int
  recovery: bool
  minimum_episodes: NotRequired[int]


class SelectorHint(TypedDict):
  group: str
  name: str
  delta: float
  demand: DemandSignature
  binding: LiveTrialBinding
  profile_sha256: str
  model_revision: str
  controller_domain: str
  selected_ns: int
  expires_ns: int
  source_episode_ids: tuple[str, ...]
  quota: int
  minimum_episodes: NotRequired[int]


TrialPlan = tuple[int, float, float]
TrialChoice = tuple[float, str, str, dict[str, float], tuple[TrackingEpisode, ...], float, float, tuple[int, int], TrackingEpisode]


def discovery_needs_probes(discovery) -> bool:
  # A recovery selector must re-establish its effect with fresh actual native
  # endpoints. Its new comparison episodes cannot be measurement-only until
  # that existing final revalidation has completed.
  return discovery is not None and discovery['recovery'] is True


@lru_cache(maxsize=32, typed=True)
def _comparison_thresholds(variance, inflation, feasible, minimum, alpha):
  """Numerical work only; never cache evidence, current health or permission.

  Same original operation order and Student/alpha/df calculations. Hooks with
  the same measured planning context share this immutable arithmetic grid;
  each hook's expected effect still selects its own original allocation.
  """
  rows = []
  for before_count in range(minimum, MAX_EPISODES + 1):
    left = variance * inflation / before_count
    before_df = max(1, math.floor(before_count / inflation) - 1)
    futures = []
    for future_count in range(minimum, feasible + 1):
      right = variance * inflation / future_count
      future_df = max(1, math.floor(future_count / inflation) - 1)
      look_alpha = alpha / len(_look_quotas(future_count, feasible))
      critical = _critical(_welch_df(left, before_df, right, future_df), look_alpha)
      futures.append((future_count, (critical + 0.8416212335729143) * math.sqrt(left + right)))
    rows.append((before_count, tuple(futures)))
  return tuple(rows)


class LiveTrackingOptimizer:
  def __init__(
    self, binding: LiveTrialBinding, accepted_full30: tuple[tuple[str, float], ...], *, controller_domain: str, response_certificate: ResponseCertificate
  ):
    from openpilot.starpilot.flm.live_tracking_response import validate_certificate

    certificate = validate_certificate(response_certificate)
    if type(binding) is not LiveTrialBinding or certificate.binding != binding or controller_domain not in ('torque', 'torque_pid'):
      raise ValueError('genuinely identified current response binding required')
    binding.validate()
    self.binding = binding
    self.accepted = _map(accepted_full30)
    from openpilot.starpilot.flm.live_tracking_response import TrackingSourceContext

    self.domain, self.lag_ns = controller_domain, certificate.lag_ns
    self.rmse = None if type(certificate) is TrackingSourceContext else certificate.heldout_rmse
    self.certificate = certificate
    self.response_witness: ValidatedResponseWitness | None = None
    self.ready_since: dict[str, int] = {}
    # Completed negative trials guide selection only. They never contribute
    # scores, confidence or authority to a subsequent independent experiment.
    self.rejected_directions: deque[tuple[str, int, str, DemandSignature]] = deque(maxlen=128)
    self.closed_loop_worse: deque[tuple[str, int, str, DemandSignature, int]] = deque(maxlen=64)
    self.inconclusive_trials: deque[tuple[str, int, str, DemandSignature, int]] = deque(maxlen=64)
    self.baseline: deque[TrackingEpisode] = deque(maxlen=MAX_DISCOVERY_EPISODES)
    self.future: list[TrackingEpisode] = []
    self.seen: deque[str] = deque(maxlen=256)
    self.planning_history: deque[TrackingEpisode] = deque(maxlen=MAX_DISCOVERY_EPISODES if self.rmse is None else MAX_EPISODES)
    self.sequence = self.publication_sequence = self.last_attempt = self.last_correction = self.last_ack_ns = 0
    self.offer_state: TrialState | None = None
    self.last_ack: tuple[str, int, str, str, int] | None = None
    self.before: tuple[TrackingEpisode, ...] = ()
    self.discovery: DiscoveryState | None = None
    self.selector_hint: SelectorHint | None = None
    self.selector_pending = False
    self.selector_after_ns = self.last_episode_end_ns = 0
    self.comparison: list[TrackingEpisode] = []
    self.settled_ns: int | None = None
    self.paused_intent: tuple[TrialState, tuple[TrackingEpisode, ...], tuple[TrackingEpisode, ...]] | None = None
    self.paused_demand_context: tuple[LiveTrialBinding, ResponseCertificate, str] | None = None
    self.paused_baseline_ns = 0
    self.source_recheck: SourceRecheck | None = None
    self.status, self.reason = 'collecting', 'qualified_tracking_episodes_required'
    self.model_status = 'unavailable'

  def _hash(self, values: dict[str, float] | tuple[tuple[str, float], ...]) -> str:
    return full_profile_sha256(self.binding.baseline_revision, values)

  def _drop_paused(self) -> None:
    self.paused_intent = self.paused_demand_context = None
    self.paused_baseline_ns = 0

  def _check_source_recheck(self, now_ns: int) -> None:
    check = self.source_recheck
    if check is not None and (
      type(now_ns) is not int or now_ns > check.intent['deadline_ns']
      or check.context != (self.binding, self.certificate, self.domain)
      or check.intent['baseline'] != self.accepted
    ):
      self.source_recheck = None
      self.status, self.reason = 'collecting', 'return_baseline_recheck_expired_or_context_changed'

  @staticmethod
  def _source_batches_improved(before, future) -> bool:
    old = [_score(row) for row in before]
    new = [_score(row) for row in future]
    mean_reversals = statistics.mean(row.reversal_hz for row in before)
    reversal_limit = max(1.25 * mean_reversals, mean_reversals + 0.3)
    return (
      _feedback_result(old, new) > 0
      and statistics.mean(row.reversal_hz for row in future) <= reversal_limit
      and all(_score(row) <= 1.5 * max(statistics.mean(old), 0.03)
        and row.reversal_hz <= max(reversal_limit, max(old_row.reversal_hz for old_row in before)) for row in future)
    )

  def _begin_source_recheck(self) -> None:
    assert self.offer_state is not None
    state = self.offer_state
    self.source_recheck = SourceRecheck(state.copy(), self.before, tuple(self.future),
      (self.binding, self.certificate, self.domain), [])
    # This is an explicit return-to-baseline measurement, not a rejected
    # experiment's rollback/retry. Provisional B is neither accepted nor saved.
    baseline_offer = state.copy()
    baseline_offer['phase'], baseline_offer['target'] = 'rollback', dict(state['baseline'])
    self.offer_state = baseline_offer
    self._drop_paused()
    self._discard_selector()
    self.before, self.future, self.settled_ns = (), [], None
    self.status, self.reason = 'collecting', 'actual_accepted_baseline_ack_before_recheck_required'

  def _observe_source_recheck(self, episode: TrackingEpisode, now_ns: int) -> bool:
    check, state = self.source_recheck, self.offer_state
    assert check is not None
    if (state is None or state['phase'] != 'rollback' or not state['accepted']
      or not check.first_ack_ns or episode.start_ns <= check.first_ack_ns + self.lag_ns
      or self.last_ack is None
      or self.last_ack[:4] != (state['trial_id'], state.get('target_sequence', self.sequence), 'rollback', self._hash(self.accepted))
      or not 0 <= now_ns - self.last_ack[-1] <= 100_000_000
      or not _matches_demand(check.intent['demand'], episode)
      or check.baseline and episode.start_ns <= check.baseline[-1].end_ns):
      return False
    check.baseline.append(episode)
    self.status, self.reason = 'collecting', 'measuring_accepted_baseline_after_trial'
    if len(check.baseline) < check.intent['required_episodes']:
      return True
    self.source_recheck = None
    if not self._source_batches_improved(check.baseline, check.future):
      # No second chance on these outcomes. A remains the accepted map.
      for name in check.intent['names']:
        direction = 1 if check.intent['target'][name] > self.accepted[name] else -1
        self.inconclusive_trials.append((name, direction, self._hash(self.accepted), check.intent['demand'], episode.end_ns))
      self.status, self.reason = 'collecting', 'return_baseline_did_not_confirm_improvement'
      return True
    self.sequence += 1
    trial = check.intent.copy()
    trial['trial_id'] = hashlib.sha256(repr((trial['trial_id'], 'baseline-rechecked', self.sequence, now_ns)).encode()).hexdigest()
    trial['target_sequence'], trial['published'], trial['accepted'] = self.sequence, False, False
    trial['prior_accepted'] = state
    trial['source_rechecked_before'] = check.before
    # ORIGINAL start, deadline, fixed quotas and confidence allocation survive.
    # B2 must earn a fresh full-map ACK, settling and a whole new future batch.
    self.offer_state, self.before, self.future, self.settled_ns = trial, tuple(check.baseline), [], None
    self.status, self.reason = 'trial_pending', 'rechecked_target_requires_new_application_and_performance'
    return True

  def _check_paused_demand(self, now_ns: int) -> None:
    if self.paused_demand_context is not None and (
      self.paused_intent is None
      or self.paused_demand_context != (self.binding, self.certificate, self.domain)
      or self.paused_intent[0]['baseline'] != self.accepted
      or now_ns >= self.paused_intent[0]['deadline_ns']
    ):
      self._drop_paused()

  def _resume_paused(self, control_ns: int) -> None:
    assert self.paused_intent is not None
    intent, before, future = self.paused_intent
    self._drop_paused()
    self.sequence += 1
    resumed = intent.copy()
    resumed['trial_id'] = hashlib.sha256(repr((intent['trial_id'], self.sequence, control_ns)).encode()).hexdigest()
    resumed['target_sequence'] = self.sequence
    resumed['published'] = False
    resumed['prior_accepted'] = self.offer_state
    self.offer_state = resumed
    self.before, self.future = before, list(future)
    self.settled_ns = None
    self.status, self.reason = 'trial_pending', 'fresh_target_application_after_evidence_gap_required'

  def interrupt(self, reason: str, *, context_changed: bool = False) -> None:
    self.source_recheck = None
    # Retain only inert numerical study intent on missing/stale evidence. The
    # command still rolls back; a fresh baseline ACK must precede a NEW wire
    # trial. Completed scores, alpha and original deadline are never reset.
    # Normalized source timing gaps reject the pair without changing identity.
    # Wrong domains still arrive with context_changed=True and discard intent.
    transient = not context_changed and reason in (
      'acknowledged_trial_profile_withdrawn', 'expired_response_witness', 'stale_control', 'stale_applied_output',
      'current_evidence_delayed', 'learning_evidence_delayed', 'no_current_learning_evidence', 'consumed_source_mismatch',
      'consumer_pending_trial_interrupted',
      'fresh_physical_residual_window_required',
    )
    if not transient:
      self._drop_paused()
    elif ((reason in ('acknowledged_trial_profile_withdrawn', 'expired_response_witness') or self.rmse is None and transient) and self.offer_state is not None
        and self.offer_state['phase'] == 'trial' and not self.offer_state.get('accepted', False) and self.before):
      # Source-only transient gaps retain completed numerical study rows,
      # never a command lease. Resume still requires a fresh baseline ACK.
      # Profile withdrawal requires a qualified newer source control. Expiry
      # requires an otherwise valid original owned physical witness. Neither
      # restores its command lease; both retain only the numerical study.
      self.paused_intent = (self.offer_state.copy(), self.before, tuple(self.future))
    # Already completed clean geometry survives an ordinary brake/override.
    # The input builder discards interrupted partial episodes independently.
    retain_comparison = not context_changed and self.discovery is not None and (self.offer_state is None or self.offer_state.get('accepted') is True)
    if not retain_comparison:
      self.discovery = None
      self.comparison.clear()
      self.selector_pending = self.selector_hint is not None
      self.selector_after_ns = max(self.selector_after_ns, self.last_episode_end_ns, self.last_ack_ns)
    # Only completed baseline observations survive. No old witness, command,
    # trial outcome or deadline becomes current. A pending experiment still
    # follows the unchanged rollback path below.
    if context_changed:
      self.baseline.clear()
      self.planning_history.clear()
      self.inconclusive_trials.clear()
      self._discard_selector()
    self.future.clear()
    self.before = ()
    if self.offer_state is not None and not self.offer_state.get('accepted', False):
      state = self.offer_state.copy()
      if state['phase'] == 'trial' and state.get('published', True) is False:
        # No transmission was attempted, so the consumer has no such pending
        # trial to roll back. Keep any earlier accepted owner with its OWN
        # sequence; the abandoned attempt still spends its confidence budget.
        self.offer_state = state.get('prior_accepted')
      else:
        state['phase'], state['target'] = 'rollback', dict(state['baseline'])
        state.pop('source_rechecked_before', None)
        self.offer_state = state
    self.status, self.reason = 'paused', reason
    self.settled_ns = None

  def _discard_selector(self) -> None:
    self.selector_hint = None
    self.selector_pending = False
    self.discovery = None
    self.comparison.clear()

  def _current_selector(self, now_ns: int) -> SelectorHint | None:
    hint = self.selector_hint
    changed = hint is not None and (
      hint['binding'] != self.binding
      or hint['profile_sha256'] != self._hash(self.accepted)
      or hint['model_revision'] != self.certificate.model_revision
      or hint['controller_domain'] != self.domain
      or len(hint['source_episode_ids']) < hint.get('minimum_episodes', _required(hint['demand'].kind))
    )
    if hint is not None and (changed or not hint['selected_ns'] <= now_ns <= hint['expires_ns']):
      self._discard_selector()
      if changed:
        self.baseline.clear()
        self.planning_history.clear()
      return None
    return hint

  def preferred_probe_group(self, now_ns: int) -> str | None:
    hint = self._current_selector(now_ns)
    state = self.offer_state
    if hint is None or not (self.selector_pending or self.discovery is not None) or state is not None and not state['accepted']:
      return None
    return hint['group']

  def _fresh_selected_effect(self, rows: Sequence[TrackingEpisode], name: str, target: dict[str, float]) -> float | None:
    effects = []
    for row in rows:
      feature = next((item for item in row.sensitivity if item.name == name), None)
      if (
        not row.native_gradient_complete
        or feature is None
        or not feature.native_gradient_complete
        or feature.feature_energy <= 1e-10
        or feature.native_endpoints < 4
        or feature.native_separated / feature.native_endpoints < 0.09
        or feature.max_step_physical_rms <= 0
        or _score_scale(row) <= 0
      ):
        return None
      # No error/error_feature_sum/sign is inspected: the earlier coordinate
      # and bounded direction were fixed before these observations existed.
      delta = target[name] - self.accepted[name]
      separated, effect = feature.native_effect(delta)
      if separated / feature.native_endpoints < 0.09 or effect <= 0:
        return None
      effects.append(effect * abs(delta) / MAX_STEP / _score_scale(row))
    return min(effects) if effects else None

  def _comparison_allocation(
    self, episode: TrackingEpisode, expected: float, target: dict[str, float], *, minimum_episodes: int | None = None
  ) -> tuple[int, int] | None:
    if minimum_episodes is not None and minimum_episodes not in ((1, 3, 9) if self.rmse is None else (3, 9)):
      return None
    minimum = max(3 if self.rmse is None else _required(episode.kind), minimum_episodes or 0)
    if type(expected) not in (int, float) or not math.isfinite(expected) or expected <= 0.0001:
      return None
    # Freeze the NEW baseline size before observing any of its scores. More
    # clean baseline observations can reduce time under an experimental target.
    # This is an empirical exposure forecast, never an acceptance calculation.
    reference = tuple(
      row
      for row in self.planning_history
      if row.binding == self.binding
      and row.profile_sha256 == episode.profile_sha256
      and _matches(episode, row)
      and 0 <= episode.end_ns - row.end_ns <= 600_000_000_000
    )
    if len(reference) < 2:
      return None
    if self.rmse is None:
      cadence = statistics.median(b.end_ns - a.end_ns for a, b in zip(reference, reference[1:], strict=False))
      slew_ns = math.ceil(max(abs(target[n] - self.accepted[n]) for n in NAMES) / 0.005 * 1e9)
      if math.floor((TRIAL_TIMEOUT_NS - slew_ns - self.lag_ns) / cadence) < minimum:
        return None
      return minimum, minimum
    variance, inflation, _ = _noise(reference, self.rmse)
    intervals = [b.end_ns - a.end_ns for a, b in zip(reference, reference[1:], strict=False)]
    cadence = statistics.median(intervals)
    slew_ns = math.ceil(max(abs(target[n] - self.accepted[n]) for n in NAMES) / 0.005 * 1e9)
    feasible = min(MAX_EPISODES, math.floor((TRIAL_TIMEOUT_NS - slew_ns - self.lag_ns) / cadence))
    alpha = 0.05 / ((self.sequence + 1) * (self.sequence + 2))
    choices = []
    for before_count, futures in _comparison_thresholds(variance, inflation, feasible, minimum, alpha):
      for future_count, threshold in futures:
        if expected > threshold:
          choices.append((future_count, before_count + future_count, before_count))
          break
    if not choices:
      # Predicted local native effect is not a reliable forecast of closed-loop
      # tracking improvement. A bounded pilot may measure that improvement;
      # the unchanged independent final test still decides whether to keep it.
      if feasible < minimum:
        return None
      return minimum, minimum
    future_count, _, before_count = min(choices)
    return before_count, future_count

  def _revalidate_selector(self, episode: TrackingEpisode, now_ns: int) -> bool:
    hint = self._current_selector(now_ns)
    if hint is None:
      return False
    if episode.start_ns <= self.selector_after_ns:
      return self.rmse is not None
    if not _matches_demand(hint['demand'], episode):
      # A source recovery hint is a preference, not a collection lock. A
      # different original maneuver can seed a new independent experiment.
      return self.rmse is not None
    from openpilot.starpilot.flm.live_tracking_response import validate_response_witness

    try:
      validate_response_witness(self.response_witness, self.binding, now_ns)
    except (TypeError, ValueError):
      self.reason = 'fresh_selector_response_required'
      return True
    low, high = coordinate_bounds(hint['name'])
    target = dict(self.accepted)
    target[hint['name']] = min(high, max(low, target[hint['name']] + hint['delta']))
    expected = self._fresh_selected_effect((episode,), hint['name'], target)
    if expected is None or expected <= 0.0001:
      self.status, self.reason = 'collecting', 'fresh_selected_native_probe_required'
      return self.rmse is not None
    allocation = self._comparison_allocation(episode, expected, target, minimum_episodes=hint.get('minimum_episodes'))
    if allocation is None:
      self.status, self.reason = 'collecting', 'fresh_recovery_allocation_power_insufficient'
      return self.rmse is not None
    # This fresh source/probe observation is REVALIDATION, never a score row.
    # Old selection provenance/expiry is preserved, not retagged as new proof.
    # The actual baseline later gets ONE unchanged power check; its honest
    # required future quota may exceed this empirical forecast.
    self.discovery = {
      'group': hint['group'],
      'name': hint['name'],
      'target': target,
      'expected': expected,
      'anchor': episode,
      'selected_end_ns': episode.end_ns,
      'quota': allocation[0],
      'expires_ns': hint['expires_ns'],
      'recovery': True,
      'minimum_episodes': hint.get('minimum_episodes', _required(episode.kind)),
    }
    self.selector_pending = False
    self.status, self.reason = 'collecting', 'uncensored_independent_comparison_baseline_required'
    return True

  def response_current(self, witness, *, now_ns):
    """Fresh pose/model residual result; retained model is never permission."""
    from openpilot.starpilot.flm.live_tracking_response import validate_response_witness, prepare_response_witness

    try:
      raw = validate_response_witness(witness, self.binding, now_ns)
      if raw.certificate != self.certificate:
        raise ValueError('response certificate changed')
    except (AttributeError, TypeError, ValueError):
      self.response_witness = None
      self.model_status = 'stale'
      self.interrupt('physical_response_context_or_residual_unqualified')
      return False
    self.response_witness = prepare_response_witness(raw, self.binding, now_ns)
    self.model_status = 'valid'
    return True

  def observe_episode(self, episode: TrackingEpisode, *, now_ns: int) -> bool:
    episode.validate()
    self._check_source_recheck(now_ns)
    if (self.rmse is None) != (episode.normalization_accel is not None):
      return False  # Never mix legacy force episodes with direct kinematic windows.
    if (
      episode.binding != self.binding
      or self.model_status != 'valid'
      or episode.episode_id in self.seen
      or not episode.end_ns <= now_ns
      or self.baseline
      and episode.start_ns <= self.baseline[-1].end_ns
    ):
      return False
    self.seen.append(episode.episode_id)
    self.last_episode_end_ns = max(self.last_episode_end_ns, episode.end_ns)
    state = self.offer_state
    if state is None or state.get('accepted', False):
      self._check_paused_demand(now_ns)
      if episode.profile_sha256 != self._hash(self.accepted):
        return False
      if self.source_recheck is not None:
        return self._observe_source_recheck(episode, now_ns)
      # Planning-only evidence retains completed accepted-profile geometry,
      # including discovery and zero-error comparison episodes. It never
      # supplies the scored baseline or any future trial outcome.
      if not self.planning_history or episode.start_ns > self.planning_history[-1].end_ns:
        self.planning_history.append(episode)
      if self.paused_demand_context is not None:
        # A whole accepted-baseline window is an input-only resume trigger,
        # never another scored row. Match the ORIGINAL demand, not a newer
        # anchor. Fresh ACKs cannot move the first restored-baseline boundary.
        assert self.paused_intent is not None
        intent, before, _future = self.paused_intent
        if (
          state is not None
          and state.get('accepted', False)
          and self.paused_baseline_ns > 0
          and episode.start_ns > self.paused_baseline_ns + self.lag_ns
          and self.last_ack is not None
          and self.last_ack[:4] == (state['trial_id'], state.get('target_sequence', self.sequence), state['phase'], self._hash(self.accepted))
          and 0 <= now_ns - self.last_ack[-1] <= 100_000_000
          and _matches_demand(intent.get('demand', _demand(before[0])), episode)
        ):
          self._resume_paused(self.last_ack[-1])
        return False
      self._current_selector(now_ns)
      if self.selector_pending and self._revalidate_selector(episode, now_ns):
        return False
      if self.discovery is not None and self.rmse is None:
        # A short input gap keeps only completed baseline arithmetic. Release
        # an ended regime after three window durations of original source
        # time, so one unfinished comparison cannot monopolize discovery.
        last_match = self.comparison[-1].end_ns if self.comparison else self.discovery['selected_end_ns']
        demand = (self.selector_hint['demand'] if self.selector_hint is not None
                  else _demand(self.discovery['anchor']))
        if (episode.end_ns > self.discovery['selected_end_ns']
            and not _matches_demand(demand, episode)
            and episode.end_ns - last_match >= 3 * (episode.end_ns - episode.start_ns)):
          self._discard_selector()
      if self.discovery is not None:
        discovery = self.discovery
        if now_ns > discovery['expires_ns']:
          self._discard_selector()
          self.status, self.reason = 'collecting', 'fresh_comparison_baseline_timeout'
          return False
        demand = (self.selector_hint['demand'] if self.rmse is None and self.selector_hint is not None
                  else _demand(discovery['anchor']))
        if (
          not _matches_demand(demand, episode)
          or episode.start_ns <= discovery['selected_end_ns']
          or self.comparison
          and episode.start_ns <= self.comparison[-1].end_ns
        ):
          return False
        # These observations were requested BEFORE their error was measured.
        # Low/zero tracking errors remain in the comparison baseline.
        self.comparison.append(episode)
        if len(self.comparison) == discovery['quota']:
          rows = tuple(self.comparison)
          self.discovery = None
          self.comparison.clear()
          expected = discovery['expected']
          if discovery['recovery']:
            fresh_effect = self._fresh_selected_effect(rows, discovery['name'], discovery['target'])
            if fresh_effect is None:
              self.selector_pending = self.selector_hint is not None
              self.selector_after_ns = rows[-1].end_ns
              self.status, self.reason = 'collecting', 'fresh_selected_native_probe_required'
              return False
            expected = min(expected, fresh_effect)
          plan = self._trial_plan(rows, expected, discovery['target'], minimum_episodes=discovery.get('minimum_episodes'))
          if plan is None:
            self._discard_selector()
            self.status, self.reason = 'not_identifiable', 'independent_baseline_trial_power_insufficient'
            return False
          return self._launch(discovery['group'], discovery['name'], discovery['target'], rows, plan, now_ns)
        self.status, self.reason = 'collecting', 'uncensored_independent_comparison_baseline_required'
        return False
      self.baseline.append(episode)
      return self._propose(now_ns)
    if state['phase'] == 'trial' and now_ns > state['deadline_ns']:
      self._rollback('trial_future_support_timeout')
      return False
    if state['phase'] != 'trial' or self.settled_ns is None:
      return False
    if (
      episode.start_ns <= self.settled_ns + self.lag_ns
      or episode.profile_sha256 != self._hash(state['target'])
      or self.future
      and episode.start_ns <= self.future[-1].end_ns
    ):
      return False
    if not _matches_demand(state.get('demand', _demand(self.before[0])), episode):
      last_match = self.future[-1].end_ns if self.future else self.settled_ns + self.lag_ns
      if self.rmse is None and episode.end_ns - last_match >= 3 * (episode.end_ns - episode.start_ns):
        self._rollback('trial_input_regime_ended')
      return False
    baseline_error = statistics.mean(_score(row) for row in self.before)
    baseline_reversals = statistics.mean(row.reversal_hz for row in self.before)
    reversal_limit = max(1.25 * baseline_reversals, baseline_reversals + 0.3)
    # A single turn is compared with the observed baseline envelope. Comparing
    # one noisy crossing count with a mean incorrectly labels baseline variation
    # as deterioration. The complete future batch still has to meet the original
    # mean-rate limit below, independently of its tracking-error improvement.
    if _score(episode) > 1.5 * max(baseline_error, 0.03) or episode.reversal_hz > max(reversal_limit, max(row.reversal_hz for row in self.before)):
      self._rollback('trial_tracking_or_oscillation_worse')
      return False
    self.future.append(episode)
    if len(self.future) == state['required_episodes']:
      if statistics.mean(row.reversal_hz for row in self.future) > reversal_limit:
        self._rollback('trial_oscillation_batch_worse')
        return False
      # Predeclared looks only; no outcome-selected quota or scale search.
      old = [_score(row) for row in self.before]
      new = [_score(row) for row in self.future]
      if self.rmse is None:
        result = _feedback_result(old, new)
        if result > 0:
          original_before = state.get('source_rechecked_before')
          if original_before is None:
            self._begin_source_recheck()
          elif not self._source_batches_improved(original_before, self.future):
            self._rollback('original_baseline_improvement_not_repeated')
          else:
            confirmed = state.copy()
            confirmed['phase'] = 'confirmed'
            self.offer_state = confirmed
            self.status, self.reason = 'confirmed', 'improved_against_original_and_returned_baselines'
        else:
          demand = state.get('demand', _demand(self.before[0]))
          for name in state['names']:
            direction = 1 if state['target'][name] > state['baseline'][name] else -1
            if result < 0:
              self.closed_loop_worse.append((name, direction, self._hash(state['baseline']), demand, episode.end_ns))
            else:
              self.inconclusive_trials.append((name, direction, self._hash(state['baseline']), demand, episode.end_ns))
          self._rollback('matched_tracking_not_consistently_improved')
        return True
      improvement = statistics.mean(old) - statistics.mean(new)
      before_var, before_inflation, before_df = _noise(self.before, self.rmse)
      future_var, future_inflation, future_df = _noise(self.future, self.rmse)
      left_variance = before_var * before_inflation / len(old)
      right_variance = future_var * future_inflation / len(new)
      noise = math.sqrt(left_variance + right_variance)
      looks = state.get('look_quotas', (state['required_episodes'],))
      critical = _critical(_welch_df(left_variance, before_df, right_variance, future_df), state['alpha'] / len(looks))
      if improvement > max(0.0001, critical * noise):
        confirmed = state.copy()
        confirmed['phase'] = 'confirmed'
        self.offer_state = confirmed
        self.status, self.reason = 'confirmed', 'independent_future_tracking_improved'
      else:
        later = next((quota for quota in looks if quota > len(self.future)), None)
        if later is not None and improvement >= -max(0.0001, critical * noise):
          pending = state.copy()
          pending['required_episodes'] = later
          self.offer_state = pending
          self.status, self.reason = 'trial_active', 'predeclared_independent_future_look_required'
          return True
        if improvement < -max(0.0001, critical * noise) and len(state['names']) == 1:
          name = state['names'][0]
          direction = 1 if state['target'][name] > state['baseline'][name] else -1
          self.closed_loop_worse.append((name, direction, self._hash(state['baseline']), state.get('demand', _demand(self.before[0])), episode.end_ns))
        self._rollback('independent_future_tracking_not_improved')
    return True

  def _comparison_demand(self, rows):
    # Keep the input class fixed from selection through noise estimation,
    # baseline and future scoring. Approximate demand matching is symmetric,
    # not transitive; choosing the first baseline as a new anchor changes it.
    hint = self.selector_hint
    if (
      self.rmse is None
      and hint is not None
      and hint['binding'] == self.binding
      and hint['model_revision'] == self.certificate.model_revision
      and hint['profile_sha256'] == rows[0].profile_sha256
      and hint['selected_ns'] <= rows[-1].end_ns <= hint['expires_ns']
    ):
      return hint['demand']
    return _demand(rows[0])

  def _trial_plan(
    self, rows: tuple[TrackingEpisode, ...], expected: float, target: dict[str, float], *, minimum_episodes: int | None = None
  ) -> TrialPlan | None:
    if minimum_episodes is not None and minimum_episodes not in ((1, 3, 9) if self.rmse is None else (3, 9)):
      return None
    minimum = max(3 if self.rmse is None else _required(rows[0].kind), minimum_episodes or 0)
    if type(expected) not in (int, float) or not math.isfinite(expected) or expected <= 0.0001:
      return None
    if self.rmse is None:
      if len(rows) != minimum:
        return None
      cadence = statistics.median(b.end_ns - a.end_ns for a, b in zip(rows, rows[1:], strict=False))
      slew_ns = math.ceil(max(abs(target[n] - self.accepted[n]) for n in NAMES) / 0.005 * 1e9)
      if math.floor((TRIAL_TIMEOUT_NS - slew_ns - self.lag_ns) / cadence) < minimum:
        return None
      # Source-only empirical feedback makes NO alpha/confidence claim.
      return minimum, 0.0, 0.0
    # Predeclared family-wise budget over arbitrarily many attempts. Selecting
    # a coordinate on baseline evidence does not select any future outcome.
    alpha = 0.05 / ((self.sequence + 1) * (self.sequence + 2))
    variance, inflation, before_df = _noise(rows, self.rmse)
    # A short, unusually quiet comparison batch must not be the only forecast
    # of future road noise/correlation. Retain the larger empirical estimate
    # from earlier comparable geometry under this EXACT accepted profile.
    # Selection can affect that history: it is used only conservatively for
    # quota planning; the independent baseline and final test are unchanged.
    reference = tuple(
      row
      for row in self.planning_history
      if row.binding == self.binding
      and row.profile_sha256 == rows[0].profile_sha256
      and _matches(rows[0], row)
      and 0 <= rows[-1].end_ns - row.end_ns <= 600_000_000_000
    )
    future_variance, future_inflation = variance, inflation
    if len(reference) >= 2:
      reference_variance, reference_inflation, _ = _noise(reference, self.rmse)
      future_variance = max(variance, reference_variance)
      future_inflation = max(inflation, reference_inflation)
    intervals = [b.end_ns - a.end_ns for a, b in zip(rows, rows[1:], strict=False)]
    cadence = statistics.median(intervals)
    slew_ns = math.ceil(max(abs(target[n] - self.accepted[n]) for n in NAMES) / 0.005 * 1e9)
    feasible = min(MAX_EPISODES, math.floor((TRIAL_TIMEOUT_NS - slew_ns - self.lag_ns) / cadence))
    required = None
    # Nominal80% planning estimate under the empirical future-noise forecast.
    # The physical effect RMS is not guaranteed error reduction; actual
    # future tracking decides acceptance. Baseline variance/df remain FIXED.
    for quota in range(minimum, feasible + 1):
      left_variance, right_variance = variance * inflation / len(rows), future_variance * future_inflation / quota
      look_alpha = alpha / len(_look_quotas(quota, feasible))
      critical = _critical(_welch_df(left_variance, before_df, right_variance, max(1, math.floor(quota / future_inflation) - 1)), look_alpha)
      # Welch df need not fall when one variance grows: better balance can
      # increase df and lower its critical value. Preserve the original
      # per-quota critical as a lower bound so a noisier forecast can NEVER
      # make the planning threshold easier than the equal-noise estimate.
      original_critical = _critical(_welch_df(left_variance, before_df, variance * inflation / quota, max(1, math.floor(quota / inflation) - 1)), look_alpha)
      critical = max(critical, original_critical)
      planned_se = math.sqrt(left_variance + right_variance)
      if expected > (critical + 0.8416212335729143) * planned_se:
        required = quota
        break
    if required is None:
      if feasible < minimum:
        return None
      # Fixed minimum whole-event pilot, chosen BEFORE any pilot outcome.
      # No early positive peeking; actual final Welch/HAC/alpha test below is
      # unmodified and uses measured future variance, never this forecast.
      required = minimum
      left_variance = variance * inflation / len(rows)
      right_variance = future_variance * future_inflation / required
      critical = _critical(
        _welch_df(left_variance, before_df, right_variance, max(1, math.floor(required / future_inflation) - 1)), alpha / len(_look_quotas(required, feasible))
      )
    return required, critical, alpha

  def _propose(self, now_ns: int) -> bool:
    if self.last_attempt and now_ns - self.last_attempt < RETRY_NS:
      return False
    unsupported: frozenset[str] = frozenset()
    if self.domain == 'torque_pid':
      from openpilot.starpilot.flm.pid_stages import UNSUPPORTED

      unsupported = UNSUPPORTED
    choices: list[TrialChoice] = []
    alternatives: list[TrialChoice] = []
    # Geometry is input-only and independent of the coordinate group. Compute
    # each cohort once per proposal call, not once for every group. This local
    # memo never survives a new observation, binding, source or profile.
    matched_cohorts = {}
    # Completed episodes are immutable and validated with distinct hook names.
    # Index only for this selection call; no source/permission cache survives it.
    features = {id(row): {item.name: item for item in row.sensitivity} for row in self.baseline}
    for group, names in GROUPS.items():
      examined = set()
      for episode in reversed(self.baseline):
        if not _kind_for_group(group, episode.kind) or not _group_direction_matches(group, episode.kind, episode.direction):
          continue
        matched = matched_cohorts.get(episode.episode_id)
        if matched is None:
          demand = _demand(episode)
          matched = tuple(row for row in self.baseline if _matches_demand(demand, row) and 0 <= now_ns - row.end_ns <= 600_000_000_000)
          matched_cohorts[episode.episode_id] = matched
        geometry = tuple(row.episode_id for row in matched)
        if geometry in examined:
          continue
        examined.add(geometry)
        for name in names:
          if name in unsupported:
            continue
          # Discovery can use only episodes with an ACTUAL probe for this
          # coordinate. Missing probes from another scheduled group are not
          # zero sensitivities. The later comparison baseline stays uncensored.
          rows = tuple(
            row
            for row in matched
            if row.native_gradient_complete
            and (feature := features[id(row)].get(name)) is not None
            and feature.native_gradient_complete
            and feature.feature_energy > 1e-10
            and feature.native_endpoints >= 4
            and feature.native_separated / feature.native_endpoints >= 0.09
          )
          rows, minimum = _discovery_support(rows, episode.kind, self.rmse)
          if not rows:
            continue
          estimates: list[float] = []
          for row in rows:
            feature = features[id(row)].get(name)
            if feature is None or feature.feature_energy <= 1e-10 or feature.native_endpoints < 4 or feature.native_separated / feature.native_endpoints < 0.09:
              break
            estimates.append(feature.error_feature_sum / feature.feature_energy)
          if len(estimates) != len(rows):
            continue
          sign = 1 if statistics.median(estimates) > 0 else -1
          if self.rmse is None:
            # Native secants establish that this coordinate can change actual
            # encoded commands. They do not identify a closed-loop plant or
            # prove a consistent improvement direction. Use them to select a
            # bounded experiment only; a separate uncensored baseline/future
            # comparison must still prove improvement before any persistence.
            confidence = abs(statistics.median(estimates))
            if confidence <= 0:
              continue
          else:
            if sum(value * sign > 0 for value in estimates) < math.ceil(0.8 * len(estimates)):
              continue
            mean = statistics.mean(estimates)
            confidence = abs(mean) - T95[len(estimates) - 2] * statistics.stdev(estimates) / math.sqrt(len(estimates))
            if confidence <= 0:
              continue

          # A one-tick native derivative does not include the PID's later
          # closed-loop response. A completed independently measured WORSE
          # one-coordinate trial can select the opposite bounded experiment.
          # Its old outcomes NEVER become the new baseline or acceptance score.
          def observed_worse(direction, name=name, rows=rows):
            return any(
              old_name == name
              and old_direction == direction
              and old_profile == self._hash(self.accepted)
              and 0 <= now_ns - old_end <= 600_000_000_000
              and _matches_demand(demand, rows[-1])
              for old_name, old_direction, old_profile, demand, old_end in self.closed_loop_worse
            )

          # Without a fitted plant, the secant sign is only an experiment
          # preference. At a hard bound it must not veto the feasible direction.
          # Actual worse/inconclusive directions below remain disqualified;
          # independent baseline, later feedback and slew are unchanged.
          low, high = coordinate_bounds(name)
          step_limit = SOURCE_EXPERIMENT_STEP if self.rmse is None else min(MAX_STEP, 0.1 * confidence)
          if self.rmse is None and min(high, max(low, self.accepted[name] + sign * step_limit)) == self.accepted[name]:
            sign = -sign

          if observed_worse(sign):
            if observed_worse(-sign):
              continue  # Both directions failed; do not oscillate guesses.
            sign = -sign
          if self.rmse is None and self._same_inconclusive_trial(name, sign, rows[-1], now_ns):
            continue
          delta = sign * step_limit
          directed = [features[id(row)][name].native_effect(delta) for row in rows]
          alternate = False
          if any(separated / features[id(row)][name].native_endpoints < 0.09
                 for row, (separated, _) in zip(rows, directed, strict=True)):
            # An unfitted secant sign is only a preference. Try an actually
            # visible opposite experiment only when no preferred choice fits.
            if self.rmse is not None or observed_worse(-sign) or self._same_inconclusive_trial(name, -sign, rows[-1], now_ns):
              continue
            delta = -delta
            directed = [features[id(row)][name].native_effect(delta) for row in rows]
            if any(separated / features[id(row)][name].native_endpoints < 0.09
                   for row, (separated, _) in zip(rows, directed, strict=True)):
              continue
            alternate = True
          predicted = min(effect for _, effect in directed)
          expected = predicted * abs(delta) / MAX_STEP / max(_score_scale(row) for row in rows)
          if expected <= 0.0001:
            continue  # The original native effect must be visible.
          # Fresh comparison allocation and _trial_plan independently enforce
          # the full noise/power bound before any target can be offered. Do not
          # also reject on outcome-selected discovery-score variability.
          low, high = coordinate_bounds(name)
          target = dict(self.accepted)
          target[name] = min(high, max(low, target[name] + delta))
          if target[name] == self.accepted[name]:
            continue
          expected *= abs(target[name] - self.accepted[name]) / abs(delta)
          self.ready_since.setdefault(group, now_ns)
          benefit = sum(abs(features[id(row)][name].error_feature_sum) for row in rows)
          allocation = self._comparison_allocation(episode, expected, target, minimum_episodes=minimum)
          if allocation is not None:
            (alternatives if alternate else choices).append((confidence, group, name, target, rows, benefit, expected, allocation, episode))
    if not choices:
      choices = alternatives
    if not choices:
      self.status, self.reason = 'not_identifiable', 'qualified_consistent_hook_sensitivity_required'
      return False

    # Try another qualified coordinate before repeating a completed failure
    # on the same accepted profile and comparable maneuver. Fault-interrupted
    # trials supply no negative outcome and cannot affect this preference.
    def rejected_count(choice):
      name, target, rows = choice[2], choice[3], choice[4]
      direction = 1 if target[name] > self.accepted[name] else -1
      profile = self._hash(self.accepted)
      return sum(
        old_name == name and old_direction == direction and old_profile == profile and _matches_demand(demand, rows[-1])
        for old_name, old_direction, old_profile, demand in self.rejected_directions
      )

    least_rejected = min(map(rejected_count, choices))
    choices = [choice for choice in choices if rejected_count(choice) == least_rejected]
    _, group, name, target, rows, _, expected, allocation, input_anchor = max(
      choices, key=lambda value: (now_ns - self.ready_since[value[1]] >= TRIAL_TIMEOUT_NS, value[5], -self.ready_since[value[1]], value[1], value[2])
    )
    # Discovery may select an error/direction, but those outcome-selected
    # episodes are NEVER the score baseline for a later keep/revert decision.
    self.discovery = {
      'group': group,
      'name': name,
      'target': target,
      'expected': expected,
      'anchor': input_anchor if self.rmse is None else rows[-1],
      'selected_end_ns': max(now_ns, self.last_episode_end_ns),
      'quota': allocation[0],
      'expires_ns': now_ns + 600_000_000_000,
      'recovery': False,
      'minimum_episodes': len(rows),
    }
    self.selector_hint = {
      'group': group,
      'name': name,
      'delta': target[name] - self.accepted[name],
      'demand': _demand(input_anchor if self.rmse is None else rows[-1]),
      'binding': self.binding,
      'profile_sha256': self._hash(self.accepted),
      'model_revision': self.certificate.model_revision,
      'controller_domain': self.domain,
      'selected_ns': now_ns,
      'expires_ns': self.discovery['expires_ns'],
      'source_episode_ids': tuple(row.episode_id for row in rows),
      'quota': len(rows),
      'minimum_episodes': len(rows),
    }
    self.selector_pending = False
    self.comparison.clear()
    self.baseline.clear()
    self.status, self.reason = 'collecting', 'uncensored_independent_comparison_baseline_required'
    return False

  def _launch(self, group: str, name: str, target: dict[str, float], rows: tuple[TrackingEpisode, ...], plan: TrialPlan, now_ns: int) -> bool:
    required, critical, alpha = plan
    self.ready_since[group] = now_ns
    self.sequence += 1
    trial_id = hashlib.sha256(repr((self.binding, self.sequence, now_ns, self._hash(self.accepted), name)).encode()).hexdigest()
    # Fix every look BEFORE any trial outcome exists. Small-error pilots can
    # use a second whole-episode look inside the ORIGINAL deadline; each look
    # spends an equal share of this attempt's unchanged family-wise budget.
    looks = (required,)
    if self.rmse is not None and required >= 9 and len(rows) >= 2:
      cadence = statistics.median(b.end_ns - a.end_ns for a, b in zip(rows, rows[1:], strict=False))
      slew_ns = math.ceil(max(abs(target[n] - self.accepted[n]) for n in NAMES) / 0.005 * 1e9)
      feasible = min(MAX_EPISODES, math.floor((TRIAL_TIMEOUT_NS - slew_ns - self.lag_ns) / cadence))
      looks = _look_quotas(required, feasible)
    prior = self.offer_state if self.offer_state is not None and self.offer_state.get('accepted', False) else None
    self.offer_state = {
      'trial_id': trial_id,
      'phase': 'trial',
      'group': group,
      'names': (name,),
      'baseline': dict(self.accepted),
      'target': target,
      'started_ns': now_ns,
      'deadline_ns': now_ns + TRIAL_TIMEOUT_NS,
      'required_episodes': required,
      'critical': critical,
      'alpha': alpha,
      'accepted': False,
      'target_sequence': self.sequence,
      'published': False,
      'prior_accepted': prior,
      'look_quotas': looks,
    }
    if self.rmse is None:
      self.offer_state['demand'] = self._comparison_demand(rows)
    self.last_attempt, self.before, self.future = now_ns, rows, []
    # Every attempted baseline is spent once, including failed/interrupted
    # trials. Subsequent directions need new independent baseline geometry.
    self.baseline.clear()
    self.settled_ns = None
    self.status, self.reason = 'trial_pending', 'actual_target_application_acknowledgement_required'
    return True

  def _same_inconclusive_trial(self, name, direction, episode, now_ns):
    # A completed noisy trial supplies no sign inversion, score, acceptance or
    # current permission. Avoid repeatedly spending the same driving exposure
    # when the other supported coordinates may be informative instead.
    return any(
      old_name == name
      and old_direction == direction
      and old_profile == self._hash(self.accepted)
      and 0 <= now_ns - finished <= 600_000_000_000
      and _matches_demand(demand, episode)
      for old_name, old_direction, old_profile, demand, finished in self.inconclusive_trials
    )

  def _rollback(self, reason: str) -> None:
    self.source_recheck = None
    self._drop_paused()  # An actual scored rejection is terminal.
    if self.offer_state is None:
      raise ValueError('rollback requires a pending live trial')
    state = self.offer_state.copy()
    if state['phase'] == 'trial' and state.get('published', True) is False:
      self.interrupt(reason)
      return
    if (reason == 'trial_input_regime_ended' and self.rmse is None and state['phase'] == 'trial'
        and self.settled_ns is not None and self.before and len(self.future) < state['required_episodes']):
      # Completed immutable episodes may wait for this same input regime.
      # Roll back now; this saved arithmetic carries no application lease.
      self.paused_intent = (state.copy(), self.before, tuple(self.future))
      self.paused_demand_context = (self.binding, self.certificate, self.domain)
      self.before, self.future = (), []
      self.settled_ns = None
    if (
      state['phase'] == 'trial'
      and self.before
      and reason in (
        'independent_future_tracking_not_improved', 'matched_tracking_not_consistently_improved',
        'trial_tracking_or_oscillation_worse', 'trial_oscillation_batch_worse')
    ):
      for name in state['names']:
        direction = 1 if state['target'][name] > state['baseline'][name] else -1
        self.rejected_directions.append((name, direction, self._hash(state['baseline']), state.get('demand', _demand(self.before[0]))))
    state['phase'], state['target'] = 'rollback', dict(state['baseline'])
    state.pop('source_rechecked_before', None)
    self.offer_state = state
    self._discard_selector()
    self.status, self.reason = 'rollback', reason

  def acknowledge_application(
    self,
    *,
    trial_id: str,
    target_sequence: int,
    phase: str,
    target_sha256: str,
    applied_full30: tuple[tuple[str, float], ...],
    control_ns: int,
    binding: LiveTrialBinding,
    now_ns: int,
    status: str,
    confirmed_target_sha256: str = '',
  ) -> bool:
    from openpilot.starpilot.flm.live_tracking_response import validate_response_witness

    self._check_source_recheck(now_ns)
    try:
      validate_response_witness(self.response_witness, self.binding, now_ns)
    except (TypeError, ValueError):
      return False
    state = self.offer_state
    if (
      state is None
      or binding != self.binding
      or trial_id != state['trial_id']
      or target_sequence != state.get('target_sequence', self.sequence)
      or phase != state['phase']
      or target_sha256 != self._hash(state['target'])
      or type(control_ns) is not int
      or type(now_ns) is not int
      or not state['started_ns'] < control_ns <= now_ns <= control_ns + 100_000_000
      or control_ns < self.last_ack_ns
      or status != 'applied'
      or (phase in ('trial', 'confirmed') and not state.get('accepted', False) and now_ns > state['deadline_ns'])
      or (phase == 'confirmed' and confirmed_target_sha256 != target_sha256)
      or self._hash(_map(applied_full30)) != target_sha256
      or self.model_status != 'valid'
    ):
      return False
    state['published'] = True
    self.last_ack_ns = control_ns
    self.last_ack = (trial_id, target_sequence, phase, target_sha256, control_ns)
    if state.get('accepted', False):
      return True
    if phase == 'trial':
      self.settled_ns = self.settled_ns or control_ns
      self.status, self.reason = 'trial_active', 'scoring_later_matched_whole_episodes'
    else:
      if phase == 'confirmed':
        self.accepted = dict(state['target'])
        self._discard_selector()
        self.last_correction = control_ns
        accepted = state.copy()
        accepted['accepted'] = True
        accepted.pop('prior_accepted', None)
        self.offer_state = accepted
      else:
        # Keep leasing the actually restored accepted baseline. Dropping the
        # offer here would withdraw earlier accepted nonzero corrections.
        accepted = state.copy()
        accepted['accepted'] = True
        accepted.pop('prior_accepted', None)
        self.offer_state = accepted
        if self.selector_pending:
          self.selector_after_ns = max(self.selector_after_ns, control_ns)
      if phase == 'rollback' and self.source_recheck is not None:
        self.source_recheck.first_ack_ns = self.source_recheck.first_ack_ns or control_ns
        self.status, self.reason = 'collecting', 'measuring_accepted_baseline_after_trial'
        return True
      self._check_paused_demand(now_ns)
      if phase == 'rollback' and self.paused_demand_context is not None:
        self.paused_baseline_ns = control_ns
        self.baseline.clear()
        self.future.clear()
        self.settled_ns = None
        self.status, self.reason = 'collecting', 'original_input_regime_after_baseline_ack_required'
        return True
      paused = self.paused_intent
      if phase == 'rollback' and paused is not None:
        intent, before, future = paused
        if now_ns < intent['deadline_ns'] and intent['baseline'] == self.accepted:
          self._resume_paused(control_ns)
          return True
      self._drop_paused()
      self.baseline.clear()
      self.future.clear()
      self.settled_ns = None
      self.status, self.reason = 'collecting', 'next_independent_tracking_episodes_required'
    return True

  def mark_published(self, offer) -> None:
    state = self.offer_state
    if state is not None and offer.trial_id == state['trial_id'] and offer.target_sequence == state.get('target_sequence', self.sequence):
      state['published'] = True  # Conservative before-send marker, not an ACK.

  def _witness_expired(self, now_ns: int) -> bool:
    from openpilot.starpilot.flm.live_tracking_response import response_witness_value, validate_response_witness

    try:
      value = response_witness_value(self.response_witness)
      # Validate every original field at its original deadline. This tests
      # expiry only; it does not make the old witness current or authorize TX.
      validate_response_witness(self.response_witness, self.binding, value.valid_until_ns)
      return type(now_ns) is int and now_ns > value.valid_until_ns
    except (AttributeError, TypeError, ValueError):
      return False

  def offer(self, now_ns: int, *, publication=True) -> LiveTrialOffer | None:
    from openpilot.starpilot.flm.live_tracking_response import validate_response_witness

    self._check_paused_demand(now_ns)
    self._check_source_recheck(now_ns)
    try:
      validate_response_witness(self.response_witness, self.binding, now_ns)
    except (TypeError, ValueError):
      if self.offer_state is not None:
        self.interrupt('expired_response_witness' if self._witness_expired(now_ns) else 'fresh_physical_response_witness_unavailable')
      return None
    state = self.offer_state
    if state is None:
      return None
    if state['phase'] in ('trial', 'confirmed') and not state.get('accepted', False) and now_ns > state['deadline_ns']:
      published = state.get('published', True)
      self._rollback('trial_future_support_timeout')
      state = self.offer_state
      if state is None:
        if published:
          raise ValueError('rollback lost immutable trial state')
        return None
    baseline = tuple((name, state['baseline'][name]) for name in NAMES)
    target = tuple((name, state['target'][name]) for name in NAMES)
    self.publication_sequence += 1
    offer = LiveTrialOffer(
      VERSION,
      self.binding,
      state['trial_id'],
      self.publication_sequence,
      state['phase'],
      state['group'],
      state['names'],
      baseline,
      target,
      self._hash(state['baseline']),
      self._hash(state['target']),
      state['started_ns'],
      now_ns,
      min(now_ns + MAX_LEASE_NS, validate_response_witness(self.response_witness, self.binding, now_ns).valid_until_ns),
      self.response_witness,
      target_sequence=state.get('target_sequence', self.sequence),
      deadline_ns=state['deadline_ns'],
    ).validate()
    if publication:
      self.mark_published(offer)
    return offer

  def report(self, now_ns):
    from openpilot.starpilot.flm.live_tracking_response import validate_response_witness

    try:
      validate_response_witness(self.response_witness, self.binding, now_ns)
      current = True
    except (TypeError, ValueError):
      current = False
    state = self.offer_state
    acknowledged = bool(
      current
      and state is not None
      and self.last_ack is not None
      and self.last_ack[:4] == (state['trial_id'], state.get('target_sequence', self.sequence), state['phase'], self._hash(state['target']))
      and 0 <= now_ns - self.last_ack[-1] <= 100_000_000
    )
    status, reason = self.status, self.reason
    paused = self.paused_intent
    if (self.paused_demand_context != (self.binding, self.certificate, self.domain)
        or paused is None or paused[0]['baseline'] != self.accepted or type(now_ns) is not int or now_ns >= paused[0]['deadline_ns']):
      paused = None
    before, future = (self.before, self.future) if paused is None else paused[1:]
    if state is not None and state.get('accepted', False):
      if acknowledged:
        status = state['phase']
        reason = 'accepted_target_currently_acknowledged' if status == 'confirmed' else 'prior_accepted_baseline_restored'
        if status == 'rollback' and paused is not None and self.paused_baseline_ns > 0:
          reason = 'original_input_regime_after_baseline_ack_required'
      else:
        state = None

    def mean(rows, field):
      return statistics.mean(getattr(row, field) for row in rows) if rows else None

    return {
      'version': 1,
      'status': status,
      'reason': reason,
      'group': None if state is None else state['group'],
      'names': [] if state is None else list(state['names']),
      'trialId': None if state is None else state['trial_id'],
      'phase': None if state is None else state['phase'],
      'requiredEpisodes': None if state is None else state['required_episodes'],
      'beforeEpisodes': len(before),
      'futureEpisodes': len(future),
      'modelStatus': self.model_status if current else 'stale',
      'lagMs': self.lag_ns / 1e6,
      'baselineErrorRms': mean(before, 'error_rms'),
      'futureErrorRms': mean(future, 'error_rms'),
      'baselineReversalHz': mean(before, 'reversal_hz'),
      'futureReversalHz': mean(future, 'reversal_hz'),
      'applicationAcknowledged': acknowledged,
      'applicationSettledMonoTime': self.last_ack[-1] if acknowledged and self.last_ack is not None else self.settled_ns,
      'lastCorrectionMonoTime': self.last_correction or None,
      'bindingCurrent': current,
    }

  def tracking_values(self):
    state = self.offer_state
    pending = None if state is None or state.get('accepted', False) else state['target']
    return {
      'version': 1,
      'accepted': [{'name': name, 'value': self.accepted[name]} for name in NAMES],
      'pending': None if pending is None else [{'name': name, 'value': pending[name]} for name in NAMES],
      'acceptedSha256': self._hash(self.accepted),
      'pendingSha256': None if pending is None else self._hash(pending),
    }
