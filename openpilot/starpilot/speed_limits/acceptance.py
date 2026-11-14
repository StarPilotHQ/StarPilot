"""Immutable acceptance decisions with explicit authority and no runtime I/O."""
from typing import cast


import math
from dataclasses import dataclass, replace
from enum import Enum

CONFIRMATION_NS = 30_000_000_000
# Where SLC sets the set speed (cruise.slc_sets_set_speed): automatic adoption waits this long, and a different
# source's limit must stay this long before it counts. The ceiling uses 0 for both (Policy defaults).
AUTOMATIC_ADOPTION_NS = 3_000_000_000
SOURCE_SWITCH_NS = 2_000_000_000


class ObservationKind(Enum):
  VALID = "valid"
  ABSENT = "absent"
  UNKNOWN = "unknown"
  STALE = "stale"


class IdentityKind(Enum):
  GEOGRAPHIC = "geographic"
  PRODUCER_EPISODE = "producer_episode"
  SESSION_VALUE = "session_value"


@dataclass(frozen=True)
class ObservationIdentity:
  kind: IdentityKind
  value: str | None = None
  session_id: str | None = None


@dataclass(frozen=True)
class Candidate:
  source: str
  observation_identity: ObservationIdentity
  speed_mps: float


@dataclass(frozen=True)
class Observation:
  kind: ObservationKind
  candidate: Candidate | None = None


class Mode(Enum):
  OFF = "off"
  LATERAL_ONLY = "lateral_only"
  LONGITUDINAL_ONLY = "longitudinal_only"
  COMBINED = "combined"


class LongitudinalOwner(Enum):
  NONE = "none"
  SYSTEM = "system"
  STOCK = "stock"


@dataclass(frozen=True)
class Authority:
  mode: Mode
  owner: LongitudinalOwner
  lateral_active: bool
  longitudinal_active: bool
  stock_acc_active: bool
  fully_disengaged: bool
  accelerator_override: bool = False

  @property
  def can_control(self) -> bool:
    """Permission to adopt an SLC limit during a system-longitudinal engagement."""
    return (self.mode in (Mode.LONGITUDINAL_ONLY, Mode.COMBINED) and
            self.owner is LongitudinalOwner.SYSTEM and (self.longitudinal_active or self.accelerator_override))


@dataclass(frozen=True)
class Policy:
  confirm_lower: bool = True
  confirm_higher: bool = True
  display_only: bool = False
  # The ceiling adopts at once and switches source at once (0); where SLC sets the set speed these are 3 s / 2 s.
  automatic_adoption_ns: int = 0
  # How long a different source's limit must stay before it counts; 0 when the caller's adoption delay covers it.
  source_switch_ns: int = 0


class ActionKind(Enum):
  ACCEPT = "accept"
  REJECT = "reject"


@dataclass(frozen=True)
class DriverAction:
  session_id: str
  sequence_id: int
  decision_id: int
  kind: ActionKind
  observed_ns: int | None = None


@dataclass(frozen=True)
class AdoptRequest:
  session_id: str
  sequence_id: int
  presentation_id: int
  candidate: Candidate


@dataclass(frozen=True)
class ActionReceipt:
  sequence_id: int
  acknowledged: bool
  consumed: bool
  status: str


@dataclass(frozen=True)
class AcceptedLimit:
  candidate: Candidate
  session_id: str
  accepted_at_ns: int


@dataclass(frozen=True)
class PendingDecision:
  candidate: Candidate
  decision_id: int
  active_elapsed_ns: int = 0
  automatic: bool = False


@dataclass(frozen=True)
class Presentation:
  presentation_id: int
  candidate: Candidate


@dataclass(frozen=True)
class State:
  session_id: str
  observation: Observation = Observation(ObservationKind.ABSENT)
  # A historical adoption may match a returned proposal the driver now declines.
  accepted: AcceptedLimit | None = None
  pending: PendingDecision | None = None
  rejected: Candidate | None = None
  last_timestamp_ns: int | None = None
  timer_running: bool = False
  last_action_sequence: int = -1
  next_decision_id: int = 1
  presentation: Presentation | None = None
  next_presentation_id: int = 1
  received_speed_mps: float | None = None
  # Where SLC sets the set speed: the source of the last received limit and a pending switch to another source.
  received_source: str | None = None
  switch_candidate: Candidate | None = None
  switch_started_ns: int | None = None


@dataclass(frozen=True)
class AdoptionProposal:
  """Where SLC sets the set speed: an explicit adoption the override module and card's command consume."""
  session_id: str
  action_sequence_id: int
  presentation_id: int
  accepted: AcceptedLimit
  reconciliation_speed_mps: float
  clear_override: bool = True


@dataclass(frozen=True)
class Decision:
  state: State
  display_candidate: Candidate | None = None
  control_target_mps: float | None = None
  basis: str = "none"
  history_write: AcceptedLimit | None = None
  action_receipt: ActionReceipt | None = None
  errors: tuple[str, ...] = ()
  adoption_receipt: ActionReceipt | None = None
  adoption: AdoptionProposal | None = None
  authority: Authority | None = None
  policy: Policy | None = None


def _token(value: object) -> bool:
  return type(value) is str and 0 < len(value) <= 256 and bool(value.strip()) and not any(ord(c) < 32 for c in value)


def _integer(value: object, minimum: int = 0) -> bool:
  return type(value) is int and value >= minimum


def _identity_valid(identity: object) -> bool:
  if not isinstance(identity, ObservationIdentity) or not isinstance(identity.kind, IdentityKind):
    return False
  if identity.kind is IdentityKind.SESSION_VALUE:
    return identity.value is None and _token(identity.session_id)
  return _token(identity.value) and identity.session_id is None


def _speed_valid(value: object) -> bool:
  try:
    return type(value) in (int, float) and math.isfinite(cast(float, value)) and cast(float, value) > 0
  except OverflowError:
    return False


def _candidate_valid(candidate: object, session_id: str | None = None) -> bool:
  if not isinstance(candidate, Candidate) or not _token(candidate.source) or not _identity_valid(candidate.observation_identity):
    return False
  identity = candidate.observation_identity
  if session_id is not None and identity.kind is IdentityKind.SESSION_VALUE and identity.session_id != session_id:
    return False
  return _speed_valid(candidate.speed_mps)


def observation_is_valid(observation: object, *, session_id: str | None = None) -> bool:
  """Check the shared candidate contract without choosing a source or freshness policy."""
  return isinstance(observation, Observation) and isinstance(observation.kind, ObservationKind) and (
    _candidate_valid(observation.candidate, session_id) if observation.kind is ObservationKind.VALID else observation.candidate is None)


def _accepted_valid(accepted: object) -> bool:
  return (isinstance(accepted, AcceptedLimit) and _candidate_valid(accepted.candidate, accepted.session_id) and
          _token(accepted.session_id) and _integer(accepted.accepted_at_ns))


def _authority_errors(authority: object) -> list[str]:
  if not isinstance(authority, Authority) or not isinstance(authority.mode, Mode) or not isinstance(authority.owner, LongitudinalOwner):
    return ["invalid authority type"]
  if any(type(v) is not bool for v in (authority.lateral_active, authority.longitudinal_active,
                                      authority.stock_acc_active, authority.fully_disengaged, authority.accelerator_override)):
    return ["authority flags must be booleans"]
  errors = []
  if authority.lateral_active and authority.mode not in (Mode.LATERAL_ONLY, Mode.COMBINED):
    errors.append("active lateral axis contradicts mode")
  if authority.longitudinal_active and (authority.mode not in (Mode.LONGITUDINAL_ONLY, Mode.COMBINED) or
                                        authority.owner is not LongitudinalOwner.SYSTEM):
    errors.append("active longitudinal axis contradicts mode or owner")
  if authority.stock_acc_active and authority.owner is not LongitudinalOwner.STOCK:
    errors.append("active stock ACC contradicts owner")
  if authority.accelerator_override and (authority.mode not in (Mode.LONGITUDINAL_ONLY, Mode.COMBINED) or
                                        authority.owner is not LongitudinalOwner.SYSTEM):
    errors.append("accelerator override contradicts mode or owner")
  if authority.fully_disengaged and (authority.lateral_active or authority.longitudinal_active or
                                   authority.stock_acc_active or authority.accelerator_override):
    errors.append("fully disengaged contradicts active authority")
  return errors


def authority_is_valid(authority: object) -> bool:
  """Validate the shared mode, owner and activity contract."""
  return not _authority_errors(authority)


def _state_errors(state: State) -> list[str]:
  errors = []
  if not isinstance(state, State):
    return ["invalid state type"]
  if not _token(state.session_id) or not observation_is_valid(state.observation, session_id=state.session_id):
    errors.append("invalid state identity or observation")
  if state.accepted is not None and (not _accepted_valid(state.accepted) or not _candidate_valid(state.accepted.candidate, state.session_id)):
    errors.append("invalid accepted history")
  if state.last_timestamp_ns is not None and not _integer(state.last_timestamp_ns):
    errors.append("invalid state clock")
  if state.received_speed_mps is not None and not _speed_valid(state.received_speed_mps):
    errors.append("invalid received speed")
  if state.received_source is not None and not _token(state.received_source):
    errors.append("invalid received source")
  if state.switch_candidate is not None:
    if (not _candidate_valid(state.switch_candidate, state.session_id) or state.switch_started_ns is None or not _integer(state.switch_started_ns) or
        state.last_timestamp_ns is None or state.switch_started_ns > state.last_timestamp_ns):
      errors.append("invalid source switch")
  elif state.switch_started_ns is not None:
    errors.append("source switch lacks candidate")
  if not _integer(state.last_action_sequence, -1) or not _integer(state.next_decision_id, 1) or not _integer(state.next_presentation_id, 1):
    errors.append("invalid state sequence")
  if type(state.timer_running) is not bool:
    errors.append("invalid state flags")
  rejection_valid = state.rejected is None or _candidate_valid(state.rejected, state.session_id)
  if not rejection_valid:
    errors.append("invalid rejection history")
  if state.pending is not None:
    pending = state.pending
    if (not isinstance(pending, PendingDecision) or not _candidate_valid(pending.candidate, state.session_id) or
        not _integer(pending.decision_id, 1) or not _integer(pending.active_elapsed_ns) or type(pending.automatic) is not bool or
        not _integer(state.next_decision_id, 1) or pending.decision_id >= state.next_decision_id or
        pending.candidate.speed_mps != state.received_speed_mps or
        pending.automatic and state.observation != Observation(ObservationKind.VALID, pending.candidate)):
      errors.append("invalid pending decision")
    elif rejection_valid and state.rejected is not None and pending.candidate.speed_mps == state.rejected.speed_mps:
      errors.append("pending candidate is already rejected")
  if state.timer_running and (state.pending is None or state.last_timestamp_ns is None):
    errors.append("timer lacks pending decision or clock")
  if state.presentation is not None:
    shown = state.presentation
    if (not isinstance(shown, Presentation) or not _integer(shown.presentation_id, 1) or
        not _integer(state.next_presentation_id, 1) or shown.presentation_id >= state.next_presentation_id or
        not _candidate_valid(shown.candidate, state.session_id) or state.last_timestamp_ns is None or
        state.observation != Observation(ObservationKind.VALID, shown.candidate) and not (
          isinstance(state.pending, PendingDecision) and not state.pending.automatic and
          state.pending.candidate == shown.candidate and isinstance(state.observation, Observation) and
          state.observation.kind is not ObservationKind.VALID)):
      errors.append("invalid presentation context")
  elif isinstance(state.observation, Observation) and state.observation.kind is ObservationKind.VALID:
    errors.append("valid observation lacks presentation context")
  return errors


def new_session(session_id: str, accepted: AcceptedLimit | None = None) -> State:
  """Explicitly reset transient state; a caller may supply independently qualified history."""
  state = State(session_id=session_id, accepted=accepted)
  errors = _state_errors(state)
  if errors:
    raise ValueError("; ".join(errors))
  return state


def _action_valid(action: object) -> bool:
  return (isinstance(action, DriverAction) and _token(action.session_id) and _integer(action.sequence_id) and
          _integer(action.decision_id, 1) and isinstance(action.kind, ActionKind) and
          (action.observed_ns is None or _integer(action.observed_ns, 1)))


def _timely(action: DriverAction, pending: PendingDecision, now_ns: int) -> bool:
  observed_ns = now_ns if action.observed_ns is None else action.observed_ns
  return 0 <= pending.active_elapsed_ns - (now_ns - observed_ns) <= CONFIRMATION_NS


def _adopt_valid(adopt: object) -> bool:
  return (isinstance(adopt, AdoptRequest) and _token(adopt.session_id) and _integer(adopt.sequence_id) and
          _integer(adopt.presentation_id, 1) and _candidate_valid(adopt.candidate, adopt.session_id))


def _acknowledge(state: State, action: DriverAction | AdoptRequest | None) -> tuple[State, ActionReceipt | None]:
  if action is None:
    return state, None
  if action.session_id != state.session_id:
    return state, ActionReceipt(action.sequence_id, True, False, "foreign_session")
  if action.sequence_id <= state.last_action_sequence:
    return state, ActionReceipt(action.sequence_id, True, False, "already_seen")
  return replace(state, last_action_sequence=action.sequence_id), ActionReceipt(action.sequence_id, True, False, "seen")


def _accept(state: State, candidate: Candidate, now_ns: int) -> tuple[State, AcceptedLimit | None]:
  if state.rejected is not None and (state.accepted is None or state.accepted.candidate.speed_mps != candidate.speed_mps or
                                     state.rejected.speed_mps == candidate.speed_mps):
    state = replace(state, rejected=None)
  if state.accepted is not None and state.accepted.candidate == candidate and state.pending is None:
    return replace(state, pending=None, timer_running=False), None
  accepted = AcceptedLimit(candidate, state.session_id, now_ns)
  return replace(state, accepted=accepted, pending=None, timer_running=False), accepted


def _reject(state: State, candidate: Candidate) -> State:
  return replace(state, rejected=candidate, pending=None, timer_running=False)


def step(state: State, observation: Observation, authority: Authority, policy: Policy, *, now_ns: int,
         action: DriverAction | None = None, adopt: AdoptRequest | None = None, set_speed_matches: bool = False,
         confirmation_started_ns: int | None = None, confirmation_grace_ns: int = 0) -> Decision:
  """Return a decision, never a cruise command. Invalid input produces no control target."""
  state_errors = _state_errors(state)
  if state_errors:
    return Decision(state, errors=tuple(state_errors))
  errors = _authority_errors(authority)
  if not observation_is_valid(observation, session_id=state.session_id):
    errors.append("invalid observation")
  if not isinstance(policy, Policy) or any(type(v) is not bool for v in (
      policy.confirm_lower, policy.confirm_higher, policy.display_only)) or (
      type(policy.automatic_adoption_ns) is not int or not 0 <= policy.automatic_adoption_ns <= CONFIRMATION_NS or
      type(policy.source_switch_ns) is not int or not 0 <= policy.source_switch_ns <= CONFIRMATION_NS):
    errors.append("invalid policy")
  if not _integer(now_ns) or (state.last_timestamp_ns is not None and now_ns < state.last_timestamp_ns):
    errors.append("invalid or reversed monotonic clock")
  if action is not None and (not _action_valid(action) or action.observed_ns is not None and action.observed_ns > now_ns):
    errors.append("invalid driver action")
  if adopt is not None and not _adopt_valid(adopt):
    errors.append("invalid adopt request")
  if action is not None and adopt is not None:
    errors.append("ambiguous action and adopt request")
  if type(set_speed_matches) is not bool:
    errors.append("invalid SET alignment")
  if not _integer(confirmation_grace_ns):
    errors.append("invalid confirmation grace")
  if confirmation_started_ns is not None and (not _integer(confirmation_started_ns) or not _integer(now_ns) or confirmation_started_ns > now_ns):
    errors.append('invalid confirmation start')
  if errors:
    # Do not defer a stale prompt or credit the invalid interval on recovery.
    invalid = replace(state, observation=Observation(ObservationKind.UNKNOWN), pending=None, timer_running=False, presentation=None)
    # A valid same-session event is seen even when another input makes it ineligible.
    invalid, receipt = _acknowledge(invalid, action if _action_valid(action) else None)
    invalid, adopt_receipt = _acknowledge(invalid, adopt if _adopt_valid(adopt) else None)
    if receipt is not None and receipt.status == "seen":
      receipt = replace(receipt, status="invalid_input")
    if adopt_receipt is not None and adopt_receipt.status == "seen":
      adopt_receipt = replace(adopt_receipt, status="invalid_input")
    return Decision(invalid, action_receipt=receipt, adoption_receipt=adopt_receipt, errors=tuple(errors))

  old = state
  state, receipt = _acknowledge(replace(state, observation=observation, last_timestamp_ns=now_ns), action)
  state, adopt_receipt = _acknowledge(state, adopt)
  if old.pending is not None and old.timer_running:
    assert old.last_timestamp_ns is not None
    state = replace(state, pending=replace(old.pending,
      active_elapsed_ns=old.pending.active_elapsed_ns + now_ns - old.last_timestamp_ns))
  candidate = observation.candidate
  if candidate is None and (state.pending is None or state.pending.automatic):
    state = replace(state, presentation=None)
  elif candidate is not None and (old.presentation is None or old.presentation.candidate != candidate):
    state = replace(state, presentation=Presentation(state.next_presentation_id, candidate), next_presentation_id=state.next_presentation_id + 1)

  def finish(basis: str, *, target: float | None = None, history: AcceptedLimit | None = None,
             adoption: AdoptionProposal | None = None) -> Decision:
    nonlocal state
    if observation.kind is not ObservationKind.VALID and state.pending is None:
      state = replace(state, presentation=None)
    final_receipt = replace(receipt, status=basis) if receipt is not None and receipt.status == "seen" else receipt
    final_adopt_receipt = replace(adopt_receipt, status=basis) if adopt_receipt is not None and adopt_receipt.status == "seen" else adopt_receipt
    return Decision(state, candidate, target, basis, history, final_receipt, adoption_receipt=final_adopt_receipt,
                    adoption=adoption, authority=authority, policy=policy)

  def previous_target() -> float | None:
    return state.accepted.candidate.speed_mps if state.accepted is not None and authority.can_control else None

  def unavailable(basis: str) -> Decision:
    nonlocal state, receipt, candidate
    pending = state.pending
    if (pending is None or pending.automatic or not authority.can_control or
        not (policy.confirm_lower or policy.confirm_higher)):
      state = replace(state, pending=None, timer_running=False)
      return finish(basis, target=previous_target())
    candidate = pending.candidate
    # A presented manual choice stays actionable until its original deadline.
    if (receipt is not None and receipt.status == 'seen' and action is not None and
        action.decision_id == pending.decision_id and old.presentation is not None and
        old.presentation.candidate == pending.candidate and
        _timely(action, pending, now_ns)):
      receipt = replace(receipt, consumed=True, status=action.kind.value)
      if action.kind is ActionKind.ACCEPT:
        state, history = _accept(state, pending.candidate, now_ns)
        return finish('driver_accept', target=pending.candidate.speed_mps, history=history)
      state = _reject(state, pending.candidate)
      return finish('driver_reject', target=previous_target())
    if pending.active_elapsed_ns >= CONFIRMATION_NS + confirmation_grace_ns:
      state = _reject(state, pending.candidate)
      return finish('timeout_reject', target=previous_target())
    return finish(basis, target=previous_target())

  if policy.display_only:
    state = replace(state, pending=None, timer_running=False)
    return finish("display_only")
  if observation.kind is not ObservationKind.VALID:
    return unavailable(observation.kind.value)

  assert candidate is not None
  new_limit = state.received_speed_mps != candidate.speed_mps
  explicit_adopt = (adopt is not None and adopt_receipt is not None and adopt_receipt.status == 'seen' and
                    old.presentation is not None and old.presentation.presentation_id == adopt.presentation_id and
                    old.presentation.candidate == candidate == adopt.candidate)
  if (policy.source_switch_ns and new_limit and state.received_source is not None and candidate.source != state.received_source and
      authority.can_control and not explicit_adopt and confirmation_started_ns is None):
    switching = state.switch_candidate
    if switching is None or (switching.source, switching.speed_mps) != (candidate.source, candidate.speed_mps):
      state = replace(state, switch_candidate=candidate, switch_started_ns=now_ns)
    assert state.switch_started_ns is not None
    if now_ns - state.switch_started_ns < policy.source_switch_ns:
      return unavailable('source_switch_pending')
  if policy.source_switch_ns:
    state = replace(state, received_source=candidate.source, switch_candidate=None, switch_started_ns=None)
  if new_limit:
    state = replace(state, received_speed_mps=candidate.speed_mps, pending=None, timer_running=False)
  if adopt_receipt is not None and adopt_receipt.status == "seen":
    assert adopt is not None
    if not explicit_adopt:
      adopt_receipt = replace(adopt_receipt, status="different_presentation")
    else:
      # Remember explicit adoption even when longitudinal control is inactive.
      state, history = _accept(state, candidate, now_ns)
      assert state.accepted is not None
      adopt_receipt = replace(adopt_receipt, consumed=True, status="adopt")
      if not authority.can_control:
        return finish("driver_adopt", history=history)
      proposal = AdoptionProposal(state.session_id, adopt.sequence_id, adopt.presentation_id, state.accepted, candidate.speed_mps)
      return finish("driver_adopt", target=candidate.speed_mps, history=history, adoption=proposal)

  if state.rejected is not None and state.rejected.speed_mps == candidate.speed_mps:
    state = replace(state, pending=None, timer_running=False)
    return finish("rejected", target=previous_target())

  previous_speed = state.accepted.candidate.speed_mps if state.accepted is not None else None
  if candidate.speed_mps == previous_speed:
    state = replace(state, pending=None, timer_running=False)
    return finish("accepted", target=previous_target())
  confirmation = policy.confirm_lower or policy.confirm_higher
  if previous_speed is not None:
    confirmation = policy.confirm_higher if candidate.speed_mps > previous_speed else policy.confirm_lower
  if confirmation and not authority.can_control:
    state = replace(state, pending=None, timer_running=False)
    return finish("inactive")
  if not new_limit and old.pending is not None and old.pending.automatic == (not confirmation):
    assert state.pending is not None
    pending = replace(state.pending, candidate=candidate)
  else:
    pending = PendingDecision(candidate, state.next_decision_id, automatic=not confirmation)
    state = replace(state, next_decision_id=state.next_decision_id + 1)
  if confirmation_started_ns is not None:
    pending = replace(pending, active_elapsed_ns=max(pending.active_elapsed_ns, now_ns - confirmation_started_ns))
  state = replace(state, pending=pending, timer_running=True)

  if pending.automatic:
    if pending.active_elapsed_ns >= policy.automatic_adoption_ns and (authority.can_control or authority.fully_disengaged):
      state, history = _accept(state, candidate, now_ns)
      return finish("accepted" if authority.can_control else "disengaged_accept",
                    target=candidate.speed_mps if authority.can_control else None, history=history)
    return finish("automatic_pending", target=previous_target())

  if receipt is not None and receipt.status == "seen" and action is not None and _timely(action, pending, now_ns):
    if action.decision_id != pending.decision_id:
      receipt = replace(receipt, status="different_decision")
    elif old.pending is None or old.pending.decision_id != pending.decision_id:
      receipt = replace(receipt, status="not_presented")
    elif ((old.presentation is None or old.presentation.candidate != candidate) and
          not (action.kind is ActionKind.REJECT and old.presentation is None and
               old.pending.candidate.speed_mps == candidate.speed_mps)):
      receipt = replace(receipt, status="different_presentation")
    else:
      receipt = replace(receipt, consumed=True, status=action.kind.value)
      if action.kind is ActionKind.ACCEPT:
        state, history = _accept(state, candidate, now_ns)
        return finish("driver_accept", target=candidate.speed_mps if authority.can_control else None, history=history)
      state = _reject(state, candidate)
      return finish("driver_reject", target=previous_target())

  if pending.active_elapsed_ns >= CONFIRMATION_NS + confirmation_grace_ns:
    state = _reject(state, candidate)
    return finish("timeout_reject", target=previous_target())

  # An explicit choice above wins; alignment is approval only while we own speed control.
  if set_speed_matches and authority.can_control and pending.active_elapsed_ns < CONFIRMATION_NS:
    state, history = _accept(state, candidate, now_ns)
    return finish("accepted", target=candidate.speed_mps, history=history)

  return finish("pending", target=previous_target())
