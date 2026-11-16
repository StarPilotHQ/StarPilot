"""Track causal cruise effects without inferring driver intent from speed edges.

This ledger does not authorize control or associate physical buttons with CAN.
The input adapter must supply real action identities and a qualified context.
"""

from dataclasses import dataclass, replace
from enum import Enum

from openpilot.starpilot.speed_limits.acceptance import ActionReceipt, integer, number, token
from openpilot.starpilot.speed_limits.acceptance import Decision as AcceptanceDecision
from openpilot.starpilot.speed_limits.acceptance import State as AcceptanceState

MAX_TRANSACTIONS = 128


class Origin(Enum):
  DRIVER_CRUISE = "driver_cruise"
  DRIVER_OTHER = "driver_other"
  AUTOMATIC = "automatic"


class Disposition(Enum):
  UNRESOLVED = "unresolved"
  DRIVER_INTENT = "driver_intent"
  SLC_CONSUMED = "slc_consumed"
  AUTOMATIC = "automatic"
  UNRELATED = "unrelated"


@dataclass(frozen=True)
class Transaction:
  action_id: int
  origin: Origin
  context_id: str
  started_at_ns: int
  disposition: Disposition = Disposition.UNRESOLVED


@dataclass(frozen=True)
class Ledger:
  session_id: str
  transactions: tuple[Transaction, ...] = ()
  last_action_id: int = -1
  last_effect_id: int = -1
  last_timestamp_ns: int | None = None
  reset_required: bool = False


@dataclass(frozen=True)
class BeginAction:
  session_id: str
  action_id: int
  origin: Origin
  context_id: str


@dataclass(frozen=True)
class ResolveAction:
  session_id: str
  action_id: int
  disposition: Disposition


@dataclass(frozen=True)
class CompleteAction:
  session_id: str
  action_id: int


@dataclass(frozen=True)
class CruiseChange:
  session_id: str
  effect_id: int
  action_id: int | None
  previous_mps: float | None
  selected_mps: float


@dataclass(frozen=True)
class ClassifiedChange:
  session_id: str
  effect_id: int
  action_id: int | None
  context_id: str | None
  started_at_ns: int | None
  previous_mps: float | None
  selected_mps: float
  disposition: Disposition


@dataclass(frozen=True)
class Result:
  state: Ledger
  status: str
  change: ClassifiedChange | None = None
  errors: tuple[str, ...] = ()


def _coherent(origin: Origin, disposition: Disposition) -> bool:
  if disposition is Disposition.DRIVER_INTENT:
    return origin is Origin.DRIVER_CRUISE
  if disposition is Disposition.AUTOMATIC:
    return origin is Origin.AUTOMATIC
  if disposition is Disposition.SLC_CONSUMED:
    return origin in (Origin.DRIVER_CRUISE, Origin.DRIVER_OTHER)
  return disposition in (Disposition.UNRESOLVED, Disposition.UNRELATED)


def _state_valid(state: object) -> bool:
  if (not isinstance(state, Ledger) or not token(state.session_id) or type(state.transactions) is not tuple or
      len(state.transactions) > MAX_TRANSACTIONS or not integer(state.last_action_id, -1) or
      not integer(state.last_effect_id, -1) or type(state.reset_required) is not bool or
      (state.last_timestamp_ns is not None and not integer(state.last_timestamp_ns))):
    return False
  last_id = -1
  for tx in state.transactions:
    if (not isinstance(tx, Transaction) or not integer(tx.action_id) or not last_id < tx.action_id <= state.last_action_id or
        not isinstance(tx.origin, Origin) or not token(tx.context_id) or not isinstance(tx.disposition, Disposition) or
        not integer(tx.started_at_ns) or state.last_timestamp_ns is None or tx.started_at_ns > state.last_timestamp_ns or
        not _coherent(tx.origin, tx.disposition)):
      return False
    last_id = tx.action_id
  return True


def new_ledger(session_id: str) -> Ledger:
  state = Ledger(session_id)
  if not _state_valid(state):
    raise ValueError("invalid session ID")
  return state


def _event_valid(event: object) -> bool:
  if not isinstance(event, (BeginAction, ResolveAction, CompleteAction, CruiseChange)) or not token(event.session_id):
    return False
  if isinstance(event, CruiseChange):
    return (integer(event.effect_id) and (event.action_id is None or integer(event.action_id)) and
            (event.previous_mps is None or number(event.previous_mps, positive=True)) and number(event.selected_mps, positive=True))
  if not integer(event.action_id):
    return False
  if isinstance(event, BeginAction):
    return isinstance(event.origin, Origin) and token(event.context_id)
  if isinstance(event, ResolveAction):
    return isinstance(event.disposition, Disposition) and event.disposition is not Disposition.UNRESOLVED
  return True


def step(state: Ledger, event: BeginAction | ResolveAction | CompleteAction | CruiseChange, *, now_ns: int) -> Result:
  """Classify supplied causal evidence. Missing evidence cannot establish intent."""
  if not _state_valid(state):
    return Result(state, "invalid_state", errors=("invalid ledger",))
  if not _event_valid(event):
    return Result(replace(state, reset_required=True), "invalid_input", errors=("invalid action event",))
  if event.session_id != state.session_id:
    return Result(state, "foreign_session")
  if not integer(now_ns) or (state.last_timestamp_ns is not None and now_ns < state.last_timestamp_ns):
    return Result(replace(state, reset_required=True), "invalid_input", errors=("invalid or reversed monotonic clock",))
  state = replace(state, last_timestamp_ns=now_ns)
  if state.reset_required:
    return Result(state, "session_reset_required")
  transaction = next((tx for tx in state.transactions if tx.action_id == event.action_id), None)

  if isinstance(event, BeginAction):
    if event.action_id <= state.last_action_id:
      return Result(state, "already_seen_action")
    state = replace(state, last_action_id=event.action_id)
    if len(state.transactions) == MAX_TRANSACTIONS:
      return Result(replace(state, reset_required=True), "session_reset_required")
    tx = Transaction(event.action_id, event.origin, event.context_id, now_ns)
    return Result(replace(state, transactions=(*state.transactions, tx)), "begun")

  if isinstance(event, CruiseChange):
    if event.effect_id <= state.last_effect_id:
      return Result(state, "already_seen_effect")
    state = replace(state, last_effect_id=event.effect_id)
    disposition = transaction.disposition if transaction is not None else Disposition.UNRESOLVED
    # A first scalar sample or unchanged value is not a fresh cruise selection.
    if disposition is Disposition.DRIVER_INTENT and (event.previous_mps is None or event.previous_mps == event.selected_mps):
      disposition = Disposition.UNRESOLVED
    change = ClassifiedChange(state.session_id, event.effect_id, event.action_id,
                              transaction.context_id if transaction is not None else None,
                              transaction.started_at_ns if transaction is not None else None,
                              event.previous_mps, event.selected_mps, disposition)
    return Result(state, disposition.value, change)

  if transaction is None:
    return Result(state, "unknown_or_completed_action")
  if isinstance(event, CompleteAction):
    return Result(replace(state, transactions=tuple(tx for tx in state.transactions if tx.action_id != event.action_id)), "completed")
  if not _coherent(transaction.origin, event.disposition):
    return Result(replace(state, reset_required=True), "invalid_resolution", errors=("disposition contradicts action origin",))
  if transaction.disposition is event.disposition:
    return Result(state, "already_resolved")
  if transaction.disposition is not Disposition.UNRESOLVED:
    return Result(replace(state, reset_required=True), "invalid_resolution", errors=("final action disposition cannot change",))
  return Result(replace(state, transactions=tuple(replace(tx, disposition=event.disposition) if tx.action_id == event.action_id else tx
                                                for tx in state.transactions)), "resolved")


def resolve_acceptance(state: Ledger, action_id: int, decision: AcceptanceDecision, *, now_ns: int) -> Result:
  """Bind a matching final acceptance receipt; acknowledgment alone is insufficient.

This helper consumes an actual acceptance result, not its optional speed target.
A caller handles actions not submitted to acceptance with an explicit resolution.
"""
  if not _state_valid(state):
    return Result(state, "invalid_state", errors=("invalid ledger",))
  if (not isinstance(decision, AcceptanceDecision) or not isinstance(decision.state, AcceptanceState) or not integer(action_id) or
      any(r is not None and (not isinstance(r, ActionReceipt) or not integer(r.sequence_id) or
                            type(r.acknowledged) is not bool or type(r.consumed) is not bool or not token(r.status))
          for r in (decision.action_receipt, decision.adoption_receipt))):
    return Result(replace(state, reset_required=True), "invalid_input", errors=("invalid acceptance resolution",))
  if decision.state.session_id != state.session_id:
    return Result(state, "foreign_session")
  transaction = next((tx for tx in state.transactions if tx.action_id == action_id), None)
  if transaction is None:
    return Result(state, "unknown_or_completed_action")
  if (not integer(now_ns) or decision.state.last_timestamp_ns is None or not integer(decision.state.last_timestamp_ns) or
      not transaction.started_at_ns <= decision.state.last_timestamp_ns <= now_ns):
    return Result(replace(state, reset_required=True), "invalid_input", errors=("acceptance clock does not match action lifecycle",))
  receipts = [r for r in (decision.action_receipt, decision.adoption_receipt) if r is not None and r.sequence_id == action_id]
  if decision.errors or len(receipts) != 1 or not receipts[0].acknowledged or receipts[0].status in ("already_seen", "foreign_session", "seen"):
    return Result(state, "unresolved_acceptance")
  if receipts[0].consumed:
    disposition = Disposition.SLC_CONSUMED
  elif transaction.origin is Origin.DRIVER_CRUISE:
    disposition = Disposition.DRIVER_INTENT
  elif transaction.origin is Origin.AUTOMATIC:
    disposition = Disposition.AUTOMATIC
  else:
    disposition = Disposition.UNRELATED
  return step(state, ResolveAction(state.session_id, action_id, disposition), now_ns=now_ns)
