"""Qualified SLC decisions supply a planner cap beneath the driver's SET."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace

from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling
from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import action_arbitration as arb
from openpilot.starpilot.speed_limits import overrides as ov
from openpilot.starpilot.speed_limits import selection as sel
from openpilot.starpilot.speed_limits import speed_domain as sd
from openpilot.starpilot.speed_limits.runtime_settings import PedalOverride


@dataclass(frozen=True)
class HostState:
  system_long_available: bool
  long_control_active: bool
  selfdrive_enabled: bool
  force_decel: bool
  driver_v_cruise_kph: float
  selected_cluster_kph: float | None


@dataclass(frozen=True)
class Frame:
  now_ns: int
  observations: Mapping[sel.Source, acc.Observation]
  selection_policy: sel.SelectionPolicy
  authority: acc.Authority
  acceptance_policy: acc.Policy
  offset_schedule: sd.OffsetSchedule
  selected_kind: sd.DomainStatus
  ego_pair: sd.SpeedPair
  pedal_pressed: bool | None
  host: HostState
  action: acc.DriverAction | None = None
  adopt: acc.AdoptRequest | None = None
  classified_cruise: arb.Result | None = None
  is_metric: bool | None = None
  adoption_observations: Mapping[sel.Source, acc.Observation] | None = None
  confirmation_started_ns: int | None = None
  pedal_override: PedalOverride = PedalOverride.OFF
  preview_reverted: bool = False
  confirmation_grace_ns: int = 0
  wheel_override_enabled: bool = False
  wheel_override_blocked: bool = False
  wheel_override_since_ns: int = 0
  filtered_ego_delta_mps: float | None = None
  # SLC sets the set speed (cruise.slc_sets_set_speed): SLC writes SET, driver SET changes persist through the override module,
  # and no planner ceiling is supplied. per_limit_memory: Remember manual speed (this drive).
  set_speed_rule: bool = False
  per_limit_memory: bool = False
  set_speed_ready: bool = True  # card's last SLC command receipt has been seen in carState


@dataclass(frozen=True)
class State:
  session_id: str
  acceptance: acc.State
  last_now_ns: int | None = None
  reset_required: bool = False
  pedal_target_mps: float = 0.
  pedal_blocked_until_release: bool = False
  wheel_override_metric: bool | None = None
  wheel_override_since_ns: int = 0
  override: ov.State | None = None  # only where SLC sets the set speed


@dataclass(frozen=True)
class Result:
  state: State
  selection: sel.Selection | None = None
  acceptance: acc.Decision | None = None
  domain: sd.DomainResolution | None = None
  coordinate: sd.PlannerCoordinate | None = None
  ceiling: CruiseCeiling | None = None
  status: str = "none"
  errors: tuple[str, ...] = ()
  override: ov.Decision | None = None  # only where SLC sets the set speed


def _integer(value: object) -> bool:
  return type(value) is int and value >= 0


def _number(value: object, *, positive: bool = False) -> bool:
  if type(value) not in (int, float):
    return False
  assert isinstance(value, (int, float))
  try:
    return math.isfinite(value) and (value > 0 if positive else value >= 0)
  except OverflowError:
    return False


def _state_valid(state: object) -> bool:
  if not isinstance(state, State) or not isinstance(state.acceptance, acc.State):
    return False
  return (type(state.session_id) is str and bool(state.session_id) and state.acceptance.session_id == state.session_id and
          _number(state.pedal_target_mps) and (state.last_now_ns is None or _integer(state.last_now_ns)) and
          type(state.reset_required) is bool and type(state.pedal_blocked_until_release) is bool and
          (state.wheel_override_metric is None or type(state.wheel_override_metric) is bool) and
          _integer(state.wheel_override_since_ns) and
          (state.override is None or isinstance(state.override, ov.State) and state.override.session_id == state.session_id))


def _host_valid(host: object) -> bool:
  return (isinstance(host, HostState) and
          all(type(value) is bool for value in (host.system_long_available, host.long_control_active,
                                                host.selfdrive_enabled, host.force_decel)) and
          _number(host.driver_v_cruise_kph) and
          (host.selected_cluster_kph is None or _number(host.selected_cluster_kph)))


def new_session(session_id: str, previous_limit: acc.AcceptedLimit | None = None) -> State:
  if previous_limit is not None:
    if not isinstance(previous_limit, acc.AcceptedLimit):
      raise ValueError('invalid previous speed limit')
    acc.new_session(previous_limit.session_id, previous_limit)
    # Remember the value without carrying an old source episode into this session.
    candidate = replace(previous_limit.candidate,
      observation_identity=acc.ObservationIdentity(acc.IdentityKind.SESSION_VALUE, session_id=session_id))
    previous_limit = acc.AcceptedLimit(candidate, session_id, 0)
  return State(session_id, acc.new_session(session_id, previous_limit), override=ov.new_session(session_id))


def step(state: State, frame: Frame) -> Result:
  if not _state_valid(state):
    return Result(state, status="invalid_state", errors=("invalid composition state",))
  if state.reset_required:
    return Result(state, status="session_reset_required", errors=("new session required",))
  if (not isinstance(frame, Frame) or not _integer(frame.now_ns) or
      (state.last_now_ns is not None and frame.now_ns < state.last_now_ns) or
      not _host_valid(frame.host) or not acc.authority_is_valid(frame.authority) or
      not isinstance(frame.acceptance_policy, acc.Policy) or
      not isinstance(frame.pedal_override, PedalOverride) or
      not _integer(frame.wheel_override_since_ns) or
      any(type(value) is not bool for value in (frame.acceptance_policy.confirm_lower,
                                                frame.acceptance_policy.confirm_higher,
                                                frame.acceptance_policy.display_only,
                                                frame.wheel_override_enabled, frame.wheel_override_blocked)) or
      not isinstance(frame.selected_kind, sd.DomainStatus) or not isinstance(frame.ego_pair, sd.SpeedPair) or
      (frame.filtered_ego_delta_mps is not None and not _number(frame.filtered_ego_delta_mps)) or
      (frame.is_metric is not None and type(frame.is_metric) is not bool)):
    return Result(replace(state, reset_required=True), status="invalid_frame", errors=("invalid frame or clock",))
  host = frame.host
  selected_initialized = host.driver_v_cruise_kph > 0 and host.driver_v_cruise_kph != 255.0
  host_long = host.system_long_available and host.long_control_active and host.selfdrive_enabled
  system_mode = frame.authority.mode in (acc.Mode.LONGITUDINAL_ONLY, acc.Mode.COMBINED) and frame.authority.owner is acc.LongitudinalOwner.SYSTEM
  if (system_mode and frame.authority.longitudinal_active != host_long or
      frame.authority.accelerator_override and not (host.system_long_available and host.selfdrive_enabled and frame.pedal_pressed is True)):
    return Result(replace(state, last_now_ns=frame.now_ns, reset_required=True),
                  status="host_authority_disagreement", errors=("host and system-long authority disagree",))
  engaged_path = (frame.authority.can_control and (host_long or frame.authority.accelerator_override) and
                  selected_initialized and not frame.acceptance_policy.display_only)
  active_path = engaged_path and not host.force_decel
  base = replace(state, last_now_ns=frame.now_ns, pedal_target_mps=0., wheel_override_metric=None, wheel_override_since_ns=0)

  if frame.classified_cruise is not None:
    classified = frame.classified_cruise
    if (not isinstance(classified, arb.Result) or classified.errors or not isinstance(classified.state, arb.Ledger) or
        not isinstance(classified.change, arb.ClassifiedChange) or classified.state.session_id != state.session_id or
        classified.change.session_id != state.session_id or classified.state.reset_required or
        classified.state.last_timestamp_ns != frame.now_ns or
        classified.state.last_effect_id != classified.change.effect_id or
        not isinstance(classified.change.disposition, arb.Disposition) or
        classified.status != classified.change.disposition.value or frame.action is not None or frame.adopt is not None):
      return Result(replace(base, reset_required=True), status="invalid_ledger_result",
                    errors=("invalid, stale or ambiguous causal ledger result",))
  selected = sel.select_limit(frame.adoption_observations if frame.adoption_observations is not None else frame.observations,
                              frame.selection_policy)
  if selected.errors:
    return Result(replace(base, reset_required=True), selection=selected, status="invalid_selection", errors=selected.errors)
  ego = frame.ego_pair
  candidate = selected.observation.candidate
  matches_set = False
  if (active_path and frame.set_speed_ready and candidate is not None and frame.selected_kind is sd.DomainStatus.VALID and
      ego.status is sd.DomainStatus.VALID and ego.session_id == state.session_id and ego.timestamp_ns == frame.now_ns and
      _number(ego.raw_mps) and _number(ego.cluster_mps)):
    assert ego.raw_mps is not None and ego.cluster_mps is not None
    target = sd.adjusted_limit(candidate.speed_mps, frame.offset_schedule,
                               minimum_target_mps=max(0.0, ego.cluster_mps - ego.raw_mps))
    matches_set = target is not None and sd.set_speed_matches(
      target, host.driver_v_cruise_kph, host.selected_cluster_kph, frame.is_metric)
  acceptance = acc.step(base.acceptance, selected.observation, frame.authority, frame.acceptance_policy,
                        now_ns=frame.now_ns, action=frame.action, adopt=frame.adopt, set_speed_matches=matches_set,
                        confirmation_started_ns=frame.confirmation_started_ns,
                        confirmation_grace_ns=frame.confirmation_grace_ns)
  base = replace(base, acceptance=acceptance.state)
  if acceptance.errors:
    return Result(replace(base, reset_required=True), selected, acceptance,
                  status="invalid_acceptance", errors=acceptance.errors)

  if frame.selected_kind is sd.DomainStatus.VALID and selected_initialized and host.selected_cluster_kph is not None:
    pair = sd.selected_pair_from_kph(host.driver_v_cruise_kph / 3.6, host.selected_cluster_kph,
                                     session_id=state.session_id, timestamp_ns=frame.now_ns)
  elif selected_initialized:
    pair = sd.SpeedPair(frame.selected_kind)
  else:
    pair = sd.SpeedPair(sd.DomainStatus.ABSENT)
  if (frame.set_speed_rule and acceptance.control_target_mps is not None and
      acceptance.state.observation.kind is not acc.ObservationKind.VALID):
    # Where SLC sets the set speed, a source gap carries no control target (SET simply stays where it is); the
    # ceiling keeps capping to the previous accepted limit instead.
    acceptance = replace(acceptance, control_target_mps=None)
  domain = sd.resolve(acceptance, frame.offset_schedule, pair, frame.ego_pair)
  if frame.set_speed_rule:
    return _set_speed_rule(state, base, frame, selected, acceptance, domain, pair, active_path)
  if domain.status is sd.DomainStatus.INVALID:
    return Result(replace(base, reset_required=True), selected, acceptance, domain,
                  status="invalid_domain", errors=(domain.reason,))
  clear_override = frame.preview_reverted or acceptance.basis in ('driver_accept', 'driver_adopt')
  clear_wheel = clear_override or state.acceptance.accepted != acceptance.state.accepted
  wheel_metric = state.wheel_override_metric if frame.wheel_override_enabled and not clear_wheel else None
  previous = 0. if clear_override else state.pedal_target_mps
  pedal_blocked = (state.pedal_blocked_until_release or clear_override) and (
    frame.pedal_pressed is not False or ego.status is not sd.DomainStatus.VALID)
  base = replace(base, pedal_blocked_until_release=pedal_blocked)
  if not active_path or domain.status is not sd.DomainStatus.VALID:
    # Longitudinal output can resume one frame after the driver releases the pedal.
    if (system_mode and host.selfdrive_enabled and not engaged_path and selected_initialized and
        not (host.force_decel or frame.acceptance_policy.display_only) and
        ego.status is sd.DomainStatus.VALID and pair.status is sd.DomainStatus.VALID):
      base = replace(base, pedal_target_mps=previous if frame.pedal_override is not PedalOverride.OFF else 0.,
                     wheel_override_metric=wheel_metric)
    return Result(base, selected, acceptance, domain, status='inactive_or_unavailable')
  context = domain.context
  assert context is not None
  selected_cluster = context.selected_raw_mps + context.selected_delta_mps
  if frame.wheel_override_enabled and frame.is_metric is not None:
    since = 0 if clear_wheel else state.wheel_override_since_ns
    pending = acceptance.state.pending
    ready = (host_long and not frame.pedal_pressed and not frame.wheel_override_blocked and
             (pending is None or pending.automatic))
    adjusted_limit = context.effective_cluster_mps
    change = frame.classified_cruise.change if frame.classified_cruise is not None else None
    # A tap belongs to the eligibility interval at button-down, even if SET changes later.
    if (wheel_metric is None and ready and since > 0 and frame.wheel_override_since_ns == since and change is not None and
        change.disposition is arb.Disposition.DRIVER_INTENT and change.previous_mps is not None and
        not sd.set_above_limit(change.previous_mps, adjusted_limit, frame.is_metric)):
      wheel_metric = frame.is_metric
    # A display-unit edit can round two different speeds to the same number.
    if wheel_metric is not None and not sd.set_above_limit(selected_cluster, adjusted_limit, wheel_metric):
      wheel_metric = None
    base = replace(base, wheel_override_metric=wheel_metric,
                   wheel_override_since_ns=(since or frame.now_ns) if ready else 0)
    if wheel_metric is not None:
      coordinate = sd.PlannerCoordinate(None, 'wheel', selected_cluster, context.ego_delta_mps)
      return Result(base, selected, acceptance, domain, coordinate, status=acceptance.basis)

  pedal_target = 0.
  if frame.pedal_override is not PedalOverride.OFF:
    ego_cluster = context.ego_raw_mps + context.ego_delta_mps
    gas_above_limit = not pedal_blocked and frame.authority.accelerator_override and ego_cluster > context.effective_cluster_mps
    if gas_above_limit or previous > context.effective_cluster_mps:
      pedal_target = (selected_cluster if frame.pedal_override is PedalOverride.SET_SPEED else
                      min(selected_cluster, max(previous, ego_cluster if gas_above_limit else 0.)))
      if pedal_target <= context.effective_cluster_mps:
        pedal_target = 0.
  base = replace(base, pedal_target_mps=pedal_target)
  coordinate = sd.to_planner_coordinate(context, override_mps=pedal_target)
  if coordinate.cap_mps is None:
    return Result(replace(base, reset_required=True), selected, acceptance, domain, coordinate,
                  status="invalid_coordinate", errors=("planner coordinate unavailable",))
  if frame.filtered_ego_delta_mps is not None and pedal_target == 0.:
    coordinate = sd.to_planner_coordinate(replace(context, ego_delta_mps=frame.filtered_ego_delta_mps),
                                           override_mps=pedal_target)
    if coordinate.cap_mps is None:
      return Result(replace(base, reset_required=True), selected, acceptance, domain, coordinate,
                    status="invalid_coordinate", errors=("planner coordinate unavailable",))
  return Result(base, selected, acceptance, domain, coordinate,
                ceiling=CruiseCeiling(coordinate.cap_mps, frame.authority),
                status=acceptance.basis)


def _override_evidence(kind: sd.DomainStatus) -> ov.EvidenceKind:
  return {sd.DomainStatus.VALID: ov.EvidenceKind.VALID,
          sd.DomainStatus.ABSENT: ov.EvidenceKind.ABSENT,
          sd.DomainStatus.STALE: ov.EvidenceKind.STALE}.get(kind, ov.EvidenceKind.UNKNOWN)


def _set_speed_rule(state: State, base: State, frame: Frame, selected, acceptance, domain, pair, active_path: bool) -> Result:
  """SLC sets the set speed: the accepted limit becomes SET through card; a driver SET change persists through the
  override module; no planner ceiling (a ceiling would hold the car below a + press)."""
  override_state = base.override if base.override is not None else ov.new_session(state.session_id)
  selected_evidence = ov.SelectedEvidence(_override_evidence(pair.status), pair.raw_mps if pair.status is sd.DomainStatus.VALID else None)
  pedal_evidence = ov.PedalEvidence(_override_evidence(frame.ego_pair.status),
                                    frame.pedal_pressed if frame.ego_pair.status is sd.DomainStatus.VALID else None,
                                    frame.ego_pair.raw_mps if frame.ego_pair.status is sd.DomainStatus.VALID else None)
  change = frame.classified_cruise.change if frame.classified_cruise is not None else None
  if (frame.per_limit_memory is True and acceptance.state.accepted is not None and
      override_state.accepted_mps is not None and
      abs(acceptance.state.accepted.candidate.speed_mps-override_state.accepted_mps) >= .1):
    override_state = replace(override_state, persistent_selected_mps=None)
  override = ov.step(override_state, acceptance, frame.authority, frame.acceptance_policy,
                     selected_evidence, pedal_evidence, now_ns=frame.now_ns, change=change, domain=domain)
  base = replace(base, override=override.state)
  if override.errors or domain.status is sd.DomainStatus.INVALID:
    return Result(replace(base, reset_required=True), selected, acceptance, domain, override=override,
                  status="invalid_domain_or_override", errors=override.errors or (domain.reason,))
  if not active_path or domain.status is not sd.DomainStatus.VALID:
    return Result(base, selected, acceptance, domain, override=override,
                  status=acceptance.basis if acceptance.basis == 'source_switch_pending' else 'inactive_or_unavailable')
  context = domain.context
  if (not isinstance(context, sd.DomainContext) or override.state.session_id != state.session_id or
      override.state.last_timestamp_ns != frame.now_ns or override.state.accepted_mps != context.accepted_raw_mps or
      override.state.effective_target_mps != context.effective_cluster_mps):
    return Result(replace(base, reset_required=True), selected, acceptance, domain, override=override,
                  status="invalid_join", errors=("override and speed domain mismatch",))
  coordinate = sd.override_planner_coordinate(context, override.contribution_mps, override.basis)
  if coordinate.cap_mps is None:
    return Result(replace(base, reset_required=True), selected, acceptance, domain, coordinate, override=override,
                  status="invalid_coordinate", errors=("planner coordinate unavailable",))
  # Adoption changes card's set speed; SLC never supplies a second planner ceiling.
  return Result(base, selected, acceptance, domain, coordinate, override=override, status=acceptance.basis)
