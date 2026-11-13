"""Card-side qualification for SLC choices, software SET engagement and active caps."""

import math

from openpilot.cereal.services import SERVICE_LIST
from openpilot.selfdrive.car.cruise import SlcPendingConfirmation, SlcWheelContext, V_CRUISE_MIN, V_CRUISE_MAX
from openpilot.starpilot.speed_limits.acceptance import CONFIRMATION_NS
from openpilot.starpilot.speed_limits.selection import Source
from openpilot.starpilot.speed_limits.speed_domain import set_above_limit


STATE_MAX_AGE_NS = int(2e9 / SERVICE_LIST['slcState'].frequency)
COMMAND_MAX_AGE_NS = int(2e9 / SERVICE_LIST['slcState'].frequency)  # SLC set-speed commands


def finite_speed(value: object) -> bool:
  if type(value) not in (int, float):
    return False
  assert isinstance(value, (int, float))
  return math.isfinite(value) and value > 0


def adoption_speed(state):
  boundary = str(getattr(state, 'upcomingBoundaryId', ''))
  if not boundary:
    return float(state.speedLimit) if finite_speed(state.speedLimit) else None
  if len(boundary) != 64 or any(c not in '0123456789abcdef' for c in boundary):
    return None
  speed = getattr(state, 'upcomingSpeedLimit', 0.0)
  return float(speed) if finite_speed(speed) else None


def source_matches(state, observation, producer_session: str, now_ns: int) -> bool:
  observed_ns = int(state.sourceObservedMonoTime)
  expiry_ns = int(state.sourceValidUntilMonoTime)
  # Card can have a newer packet for the same episode before the planner publishes again.
  return (str(state.source) == 'dashboard' and str(state.observationKind) == 'valid' and
          str(state.sourceProducerSessionId) == producer_session and producer_session != '' and
          getattr(observation.status, 'value', observation.status) == 'valid' and int(state.sourceEpisode) > 0 and
          int(state.sourceEpisode) == int(observation.episode) and
          0 < observed_ns <= state.frameMonoTime and observed_ns <= observation.observed_ns <= now_ns and
          observed_ns < expiry_ns and observation.observed_ns < observation.valid_until_ns and
          now_ns <= min(expiry_ns, observation.valid_until_ns) and
          (observed_ns != observation.observed_ns or expiry_ns == observation.valid_until_ns) and
          finite_speed(state.speedLimit) and finite_speed(observation.speed_mps) and
          abs(float(state.speedLimit) - float(observation.speed_mps)) <= 0.02)


def _state(sm, now_ns: int):
  if not (sm.valid['slcState'] and sm.alive['slcState'] and
          0 < int(sm.logMonoTime['slcState']) <= now_ns and
          now_ns - int(sm.logMonoTime['slcState']) <= STATE_MAX_AGE_NS):
    return None
  state = sm['slcState']
  if not (state.enabled and not state.displayOnly and str(state.sessionId) and
          0 < int(state.frameMonoTime) <= int(sm.logMonoTime['slcState']) and
          now_ns - int(state.frameMonoTime) <= STATE_MAX_AGE_NS):
    return None
  return state


def _current_state(sm, now_ns: int):
  state = _state(sm, now_ns)
  return state if (state is not None and str(state.observationKind) == 'valid' and
                   str(state.source) in tuple(Source) and finite_speed(state.speedLimit) and adoption_speed(state) is not None) else None


def state_current(sm, observation, producer_session: str, now_ns: int):
  """The planner's current limit, bound to this car's own dashboard evidence (card applies SLC set-speed commands)."""
  state = _current_state(sm, now_ns)
  if state is None:
    return None
  if (not getattr(state, 'upcomingBoundaryId', '') and str(state.source) == 'dashboard' and
      (observation is None or not source_matches(state, observation, producer_session, now_ns))):
    return None
  return state


def active_ceiling(sm, now_ns: int, *, after_ns: int = 0) -> float | None:
  state = _state(sm, now_ns)
  if state is None or int(state.frameMonoTime) <= after_ns or not state.hasCeiling:
    return None
  cap = float(state.effectiveCap)
  return cap if finite_speed(cap) and cap <= V_CRUISE_MAX / 3.6 + 1e-5 else None


def wheel_override_context(sm, now_ns: int, *, selected_kph: float, is_metric: bool,
                           after_ns: int = 0) -> SlcWheelContext | None:
  state = _state(sm, now_ns)
  if (state is None or int(state.frameMonoTime) <= after_ns or not state.hasAcceptedAdjustedLimit or
      not finite_speed(state.acceptedAdjustedLimit) or not V_CRUISE_MIN <= selected_kph <= V_CRUISE_MAX or
      set_above_limit(selected_kph / 3.6, float(state.acceptedAdjustedLimit), is_metric)):
    return None
  since = int(state.wheelOverrideSince)
  return (str(state.sessionId), since) if 0 < since <= int(state.frameMonoTime) else None


def engagement_state(sm, observation, producer_session: str, now_ns: int, *, after_ns: int = 0):
  state = _current_state(sm, now_ns)
  if (state is None or int(state.frameMonoTime) <= after_ns or int(state.presentationId) <= 0 or
      str(state.source) == 'online' or not state.hasPresentationAdjustedLimit):
    return None
  if str(state.source) == 'dashboard' and (
      observation is None or int(state.sourceObservedMonoTime) <= after_ns or not source_matches(state, observation, producer_session, now_ns)):
    return None
  if str(state.source) == 'vision' and not (
      state.sourceProducerSessionId and state.sourceEpisode > 0 and
      0 < state.sourceObservedMonoTime <= now_ns <= state.sourceValidUntilMonoTime):
    return None
  target = float(state.presentationAdjustedLimit)
  return state if finite_speed(target) and V_CRUISE_MIN / 3.6 - 1e-5 <= target <= V_CRUISE_MAX / 3.6 + 1e-5 else None


def engagement_target(sm, observation, producer_session: str, now_ns: int, *, after_ns: int = 0):
  current = engagement_state(sm, observation, producer_session, now_ns, after_ns=after_ns)
  state = current if current is not None else _state(sm, now_ns)
  if state is None or int(state.frameMonoTime) <= after_ns:
    return None, None
  # Offset bands can make a lower posted limit raise SET.
  lower_match = (current is not None and current.speedLimit < state.acceptedSpeedLimit and
                 current.presentationAdjustedLimit < state.acceptedAdjustedLimit)
  # Restored history has no adoption time and must not initialize SET.
  if (state.hasAccepted and state.acceptedMonoTime > 0 and not state.acceptedIsPreview and state.hasAcceptedAdjustedLimit and
      (current is None or state.acceptedSpeedLimit != current.speedLimit and not lower_match)):
    return float(state.acceptedAdjustedLimit), None
  return (float(current.presentationAdjustedLimit), current) if current is not None else (None, None)


def adopted_target(sm, now_ns: int, *, wheel_ns: int) -> float | None:
  """Read the one-frame SET approval for this Card's still-pending wheel event."""
  state = _state(sm, now_ns)
  if (state is None or int(state.wheelAdoptMonoTime) != wheel_ns or
      not 0 < wheel_ns <= int(state.frameMonoTime)):
    return None
  target = float(state.wheelAdoptTargetMps)
  return target if finite_speed(target) and V_CRUISE_MIN / 3.6 - 1e-5 <= target <= V_CRUISE_MAX / 3.6 + 1e-5 else None


def pending_confirmation(sm, now_ns: int, *, long_active: bool,
                         after_ns: int = 0) -> SlcPendingConfirmation | None:
  if not long_active:
    return None
  state = _state(sm, now_ns)
  if (state is None or int(state.frameMonoTime) <= after_ns or not state.hasPending or state.automaticAdoptionPending or
      int(state.decisionId) <= 0 or int(state.presentationId) <= 0):
    return None
  started = int(state.pendingSinceMonoTime)
  # Use the published prompt and its frame time so a late press cannot change SET.
  if (str(state.pendingSource) not in tuple(Source) or not finite_speed(state.pendingSpeedLimit) or
      not 0 < started <= int(state.frameMonoTime) <= started + CONFIRMATION_NS):
    return None
  return SlcPendingConfirmation(str(state.sessionId), int(state.decisionId), int(state.presentationId))


# SLC sets the set speed (cruise.slc_sets_set_speed): card applies a qualified SLC command to the software set speed.
def command_applicable(command, state, observation, producer_session: str, now_ns: int,
                       selected_mps: float, *, long_active: bool, button_event: bool, ceiling_mps: float | None = None) -> bool:
  if state is None or button_event or not long_active or not state.hasAccepted:
    return False
  if (str(command.kind) not in ('adoptAcceptedLimit', 'reengageZone', 'previewRollback') or not str(command.sessionId) or
      str(command.sessionId) != str(state.sessionId) or int(command.commandId) <= 0 or
      int(command.actionId) < 0 or int(command.presentationId) <= 0 or
      int(command.commandId) != int(state.commandId) or
      int(command.presentationId) != int(state.presentationId) or
      int(command.sourceEpisode) != int(state.sourceEpisode) or
      str(command.source) != str(state.source) or
      str(command.sourceProducerSessionId) != str(state.sourceProducerSessionId) or
      int(command.sourceObservedMonoTime) != int(state.sourceObservedMonoTime) or
      int(command.sourceValidUntilMonoTime) != int(state.sourceValidUntilMonoTime) or
      (str(state.source) == 'dashboard' and (observation is None or not source_matches(state, observation, producer_session, now_ns)))):
    return False
  issued, expires = int(command.issuedMonoTime), int(command.expiresMonoTime)
  if not (0 < issued <= now_ns <= expires and expires - issued <= COMMAND_MAX_AGE_NS):
    return False
  expected, target = float(command.expectedSelectedMps), float(command.targetMps)
  resumed = str(command.kind) == 'reengageZone'
  if resumed and (not state.hasReengageTarget or state.hasPending):
    return False
  rollback = str(command.kind) == 'previewRollback'
  if rollback and (not state.hasRollbackTarget or state.hasPending or str(state.source) != 'map'):
    return False
  bound_target = (float(state.rollbackTarget) if rollback else float(state.reengageTarget) if resumed
                  else float(state.acceptedSpeedLimit) + float(state.offset))
  if ceiling_mps is not None and finite_speed(ceiling_mps):
    # The car's own set speed is the ceiling (Tesla Independent set speed off): SLC aims for the lower of the two.
    bound_target = min(bound_target, ceiling_mps)
  # Float32 transport can round a legal boundary just outside its double-precision value.
  return (finite_speed(expected) and finite_speed(target) and finite_speed(selected_mps) and
          abs(expected - selected_mps) <= 0.1 and
          abs(float(state.acceptedSpeedLimit) - float(state.speedLimit)) <= 0.02 and
          abs(target - bound_target) <= 0.1 and
          V_CRUISE_MIN / 3.6 - 1e-5 <= target <= V_CRUISE_MAX / 3.6 + 1e-5)
