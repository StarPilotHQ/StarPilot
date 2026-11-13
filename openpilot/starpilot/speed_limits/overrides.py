"""Pure SLC override contribution from qualified driver intent and current pedal evidence."""

from typing import cast


import math
from dataclasses import dataclass, replace
from enum import Enum

from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import action_arbitration as arb
from openpilot.starpilot.speed_limits import speed_domain as sd

SUSPENSION_NS = 750_000_000


class EvidenceKind(Enum):
  VALID = "valid"
  ABSENT = "absent"
  UNKNOWN = "unknown"
  STALE = "stale"


@dataclass(frozen=True)
class SelectedEvidence:
  kind: EvidenceKind
  speed_mps: float | None = None


@dataclass(frozen=True)
class PedalEvidence:
  kind: EvidenceKind
  pressed: bool | None = None
  ego_mps: float | None = None


@dataclass(frozen=True)
class Context:
  context_id: str
  issued_at_ns: int
  accepted_target_mps: float


@dataclass(frozen=True)
class State:
  session_id: str
  persistent_selected_mps: float | None = None
  accepted_mps: float | None = None
  context: Context | None = None
  next_context_id: int = 1
  active: bool = False
  mode: acc.Mode | None = None
  owner: acc.LongitudinalOwner | None = None
  suspension_started_ns: int | None = None
  last_effect_id: int = -1
  last_adoption_sequence: int = -1
  last_timestamp_ns: int | None = None
  reset_required: bool = False
  effective_target_mps: float | None = None
  applied_selected_mps: float | None = None


@dataclass(frozen=True)
class EventReceipt:
  effect_id: int
  acknowledged: bool
  consumed: bool
  status: str


@dataclass(frozen=True)
class Decision:
  state: State
  contribution_mps: float | None = None
  basis: str = "none"
  context: Context | None = None
  event_receipt: EventReceipt | None = None
  errors: tuple[str, ...] = ()


def _token(value: object) -> bool:
  return type(value) is str and 0 < len(value) <= 256 and bool(value.strip()) and not any(ord(c) < 32 for c in value)


def _integer(value: object, minimum: int = 0) -> bool:
  return type(value) is int and value >= minimum


def _speed(value: object) -> bool:
  if type(value) not in (int, float):
    return False
  assert isinstance(value, (int, float))
  try:
    return math.isfinite(value) and value > 0
  except OverflowError:
    return False


def _ego_speed(value: object) -> bool:
  if type(value) not in (int, float):
    return False
  assert isinstance(value, (int, float))
  try:
    return math.isfinite(value) and value >= 0
  except OverflowError:
    return False


def _state_valid(state: object) -> bool:
  if not isinstance(state, State) or not _token(state.session_id):
    return False
  if any(v is not None and not _speed(v) for v in (state.persistent_selected_mps, state.accepted_mps, state.effective_target_mps, state.applied_selected_mps)):
    return False
  if (
    not _integer(state.next_context_id, 1)
    or type(state.active) is not bool
    or not _integer(state.last_effect_id, -1)
    or not _integer(state.last_adoption_sequence, -1)
    or type(state.reset_required) is not bool
    or any(v is not None and not _integer(v) for v in (state.suspension_started_ns, state.last_timestamp_ns))
  ):
    return False
  if (state.mode is not None and not isinstance(state.mode, acc.Mode)) or (state.owner is not None and not isinstance(state.owner, acc.LongitudinalOwner)):
    return False
  if (state.mode is None) != (state.owner is None):
    return False
  if state.mode is not None and state.last_timestamp_ns is None:
    return False
  if state.reset_required and (
    state.persistent_selected_mps is not None or state.active or state.context is not None or state.suspension_started_ns is not None
  ):
    return False
  if state.suspension_started_ns is not None and (
    state.active
    or state.last_timestamp_ns is None
    or state.suspension_started_ns > state.last_timestamp_ns
    or state.mode not in (acc.Mode.LONGITUDINAL_ONLY, acc.Mode.COMBINED)
    or state.owner is not acc.LongitudinalOwner.SYSTEM
  ):
    return False
  # Retained intent is raw selected speed; a negative offset can make it lower
  # than the raw accepted limit while still exceeding the effective threshold.
  if state.context is not None:
    c = state.context
    if (
      not isinstance(c, Context)
      or not _token(c.context_id)
      or not _integer(c.issued_at_ns)
      or not _speed(c.accepted_target_mps)
      or state.last_timestamp_ns is None
      or c.issued_at_ns > state.last_timestamp_ns
      or not state.active
      or state.accepted_mps != c.accepted_target_mps
      or state.next_context_id <= 1
      or c.context_id != f"override:{state.next_context_id - 1}"
      or state.mode not in (acc.Mode.LONGITUDINAL_ONLY, acc.Mode.COMBINED)
      or state.owner is not acc.LongitudinalOwner.SYSTEM
    ):
      return False
  if state.active and (state.context is None or state.suspension_started_ns is not None):
    return False
  if state.active and state.effective_target_mps is None:
    return False
  return True


def new_session(session_id: str) -> State:
  state = State(session_id)
  if not _state_valid(state):
    raise ValueError("invalid session ID")
  return state


def _selected_valid(selected: object) -> bool:
  return (
    isinstance(selected, SelectedEvidence)
    and isinstance(selected.kind, EvidenceKind)
    and (_speed(selected.speed_mps) if selected.kind is EvidenceKind.VALID else selected.speed_mps is None)
  )


def _pedal_valid(pedal: object) -> bool:
  return (
    isinstance(pedal, PedalEvidence)
    and isinstance(pedal.kind, EvidenceKind)
    and (
      (type(pedal.pressed) is bool and ((pedal.ego_mps is None or _ego_speed(pedal.ego_mps)) if not pedal.pressed else _ego_speed(pedal.ego_mps)))
      if pedal.kind is EvidenceKind.VALID
      else pedal.pressed is None and pedal.ego_mps is None
    )
  )


def _change_valid(change: object) -> bool:
  return (
    isinstance(change, arb.ClassifiedChange)
    and _token(change.session_id)
    and _integer(change.effect_id)
    and (change.action_id is None or _integer(change.action_id))
    and (change.context_id is None or _token(change.context_id))
    and (change.started_at_ns is None or _integer(change.started_at_ns))
    and (change.previous_mps is None or _speed(change.previous_mps))
    and _speed(change.selected_mps)
    and isinstance(change.disposition, arb.Disposition)
  )


def _acceptance_valid(decision: object, authority: object, policy: object, session_id: str, now_ns: int) -> bool:
  if (
    not isinstance(decision, acc.Decision)
    or not isinstance(decision.state, acc.State)
    or decision.errors
    or decision.state.session_id != session_id
    or decision.state.last_timestamp_ns != now_ns
    or not isinstance(authority, acc.Authority)
    or not isinstance(policy, acc.Policy)
    or decision.authority != authority
    or decision.policy != policy
    or not acc.observation_is_valid(decision.state.observation, session_id=session_id)
  ):
    return False
  if policy.display_only or not authority.can_control:
    return decision.control_target_mps is None
  if decision.control_target_mps is None:
    return True
  accepted = decision.state.accepted
  if (
    accepted is None
    or not _token(accepted.session_id)
    or not _speed(accepted.candidate.speed_mps)
    or decision.control_target_mps != accepted.candidate.speed_mps
  ):
    return False
  return decision.state.observation.kind is acc.ObservationKind.VALID


def step(
  state: State,
  acceptance: acc.Decision,
  authority: acc.Authority,
  policy: acc.Policy,
  selected: SelectedEvidence,
  pedal: PedalEvidence,
  *,
  now_ns: int,
  change: arb.ClassifiedChange | None = None,
  domain: sd.DomainResolution | None = None,
) -> Decision:
  """Propose an SLC contribution, never an actuator or stock-button command."""
  if not _state_valid(state):
    return Decision(state, errors=("invalid override state",))
  if state.reset_required:
    return Decision(state, basis="session_reset_required", errors=("new session required",))
  if (
    not _integer(now_ns)
    or (state.last_timestamp_ns is not None and now_ns < state.last_timestamp_ns)
    or not _selected_valid(selected)
    or not _pedal_valid(pedal)
    or (change is not None and not _change_valid(change))
    or (domain is not None and (not isinstance(domain, sd.DomainResolution) or not isinstance(domain.status, sd.DomainStatus)))
  ):
    failed = replace(state, persistent_selected_mps=None, context=None, active=False, suspension_started_ns=None, reset_required=True)
    return Decision(failed, basis="session_reset_required", errors=("invalid evidence or monotonic clock",))
  old = state
  state = replace(state, last_timestamp_ns=now_ns)
  receipt = None
  if change is not None:
    if change.session_id != state.session_id:
      receipt = EventReceipt(change.effect_id, True, False, "foreign_session")
    elif change.effect_id <= state.last_effect_id:
      receipt = EventReceipt(change.effect_id, True, False, "already_seen")
    else:
      state = replace(state, last_effect_id=change.effect_id)
      receipt = EventReceipt(change.effect_id, True, False, "ineligible")

  if not _acceptance_valid(acceptance, authority, policy, state.session_id, now_ns):
    failed = replace(state, persistent_selected_mps=None, active=False, context=None, suspension_started_ns=None, reset_required=True)
    return Decision(failed, basis="session_reset_required", event_receipt=receipt, errors=("acceptance authority, policy, target or clock mismatch",))

  assert isinstance(acceptance, acc.Decision) and isinstance(acceptance.state, acc.State)
  assert isinstance(authority, acc.Authority) and isinstance(policy, acc.Policy)
  accepted = acceptance.state.accepted
  accepted_speed = accepted.candidate.speed_mps if accepted is not None else None
  domain_unavailable = False
  selected_delta = 0.0
  effective_target = old.effective_target_mps if acceptance.control_target_mps is None and accepted_speed == old.accepted_mps else acceptance.control_target_mps
  if domain is not None:
    if domain.status is sd.DomainStatus.INVALID:
      failed = replace(state, persistent_selected_mps=None, active=False, context=None, suspension_started_ns=None, reset_required=True)
      return Decision(failed, basis="session_reset_required", event_receipt=receipt, errors=("invalid speed domain",))
    if domain.status is not sd.DomainStatus.VALID:
      if domain.context is not None:
        failed = replace(state, persistent_selected_mps=None, active=False, context=None, suspension_started_ns=None, reset_required=True)
        return Decision(failed, basis="session_reset_required", event_receipt=receipt, errors=("invalid speed domain",))
      domain_unavailable = True
    else:
      c = domain.context
      if (
        not isinstance(c, sd.DomainContext)
        or not sd.context_is_valid(c)
        or c.session_id != state.session_id
        or c.timestamp_ns != now_ns
        or c.accepted_raw_mps != acceptance.control_target_mps
        or selected.speed_mps != c.selected_raw_mps
        or (pedal.pressed and pedal.ego_mps != c.ego_raw_mps)
        or not _speed(c.effective_cluster_mps)
      ):
        failed = replace(state, persistent_selected_mps=None, active=False, context=None, suspension_started_ns=None, reset_required=True)
        return Decision(failed, basis="session_reset_required", event_receipt=receipt, errors=("speed domain mismatch",))
      selected_delta = c.selected_delta_mps
      effective_target = c.effective_cluster_mps
  mode_changed = old.mode is not None and (old.mode != authority.mode or old.owner != authority.owner)
  if acceptance.history_write is not None:
    changed_limit = old.accepted_mps is None or accepted_speed is None or abs(accepted_speed - old.accepted_mps) >= 0.1
    if changed_limit:
      # Old SLC keeps a higher manual target through an increasing limit until
      # the adjusted limit catches it. A real lower limit always resets it.
      keep_manual = (
        old.accepted_mps is not None
        and accepted_speed is not None
        and accepted_speed > old.accepted_mps
        and state.persistent_selected_mps is not None
        and effective_target is not None
        and state.persistent_selected_mps + selected_delta > effective_target + 0.1
      )
      state = replace(state, persistent_selected_mps=state.persistent_selected_mps if keep_manual else None, applied_selected_mps=None)
  comparison_target = state.applied_selected_mps + selected_delta if state.applied_selected_mps is not None else effective_target
  if (
    not domain_unavailable
    and state.persistent_selected_mps is not None
    and effective_target is not None
    and selected.speed_mps is not None
    and math.isclose(cast(float, comparison_target), selected.speed_mps + selected_delta, abs_tol=0.001, rel_tol=0)
  ):
    state = replace(state, persistent_selected_mps=None)
  state = replace(state, accepted_mps=accepted_speed, effective_target_mps=effective_target, mode=authority.mode, owner=authority.owner)

  adoption = acceptance.adoption
  adopted_now = False
  if adoption is not None and adoption.session_id == state.session_id and adoption.clear_override and acceptance.adoption_receipt is not None:
    if (
      acceptance.adoption_receipt.consumed
      and acceptance.adoption_receipt.sequence_id == adoption.action_sequence_id
      and adoption.action_sequence_id > state.last_adoption_sequence
    ):
      state = replace(state, persistent_selected_mps=None, last_adoption_sequence=adoption.action_sequence_id)
      adopted_now = True

  eligible = (
    authority.can_control
    and not policy.display_only
    and accepted_speed is not None
    and effective_target is not None
    and selected.kind is EvidenceKind.VALID
    and pedal.kind is EvidenceKind.VALID
    and (not domain_unavailable or acceptance.control_target_mps is None)
  )
  if not eligible:
    state = replace(state, active=False, context=None, suspension_started_ns=None)
    return Decision(state, basis="display_only" if policy.display_only else "inactive_or_missing_evidence", event_receipt=receipt)

  target = accepted_speed
  assert target is not None and _speed(target) and selected.speed_mps is not None and pedal.pressed is not None
  state = replace(state, suspension_started_ns=None)
  rotate = (
    not old.active
    or old.context is None
    or old.context.accepted_target_mps != target
    or old.effective_target_mps != effective_target
    or mode_changed
    or adopted_now
  )
  if rotate:
    context = Context(f"override:{state.next_context_id}", now_ns, target)
    state = replace(state, context=context, next_context_id=state.next_context_id + 1, active=True)
  else:
    state = replace(state, active=True)

  if receipt is not None and receipt.status == "ineligible" and change is not None:
    if policy.display_only:
      receipt = replace(receipt, status="display_only")
    elif (
      change.disposition is arb.Disposition.DRIVER_INTENT
      and change.action_id is not None
      and change.previous_mps is not None
      and change.previous_mps != change.selected_mps
      and old.active
      and old.context is not None
      and not rotate
      and change.context_id == old.context.context_id
      and change.started_at_ns is not None
      and old.context.issued_at_ns <= change.started_at_ns <= now_ns
      and change.selected_mps == selected.speed_mps
    ):
      state = replace(
        state,
        persistent_selected_mps=(
          change.selected_mps
          if comparison_target is not None and not math.isclose(change.selected_mps + selected_delta, comparison_target, abs_tol=0.001, rel_tol=0)
          else None
        ),
      )
      receipt = replace(receipt, consumed=True, status="driver_intent")
    else:
      receipt = replace(receipt, status="unqualified_effect")

  if state.persistent_selected_mps is not None:
    return Decision(state, state.persistent_selected_mps, "persistent", state.context, receipt)
  return Decision(state, basis="adoption_clear" if adopted_now else "none", context=state.context, event_receipt=receipt)
