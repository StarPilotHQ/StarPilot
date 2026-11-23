"""Card-side qualification for SLC choices, software SET engagement and active caps."""

from opendbc.car.structs import car

from openpilot.cereal.services import SERVICE_LIST
from openpilot.selfdrive.car.cruise import SlcPendingConfirmation, SlcWheelContext, VCruiseHelper, V_CRUISE_MIN, V_CRUISE_MAX
from openpilot.starpilot.speed_limits.acceptance import CONFIRMATION_NS, number
from openpilot.starpilot.speed_limits.selection import Source
from openpilot.starpilot.speed_limits.speed_domain import set_above_limit


STATE_MAX_AGE_NS = int(2e9 / SERVICE_LIST['slcState'].frequency)


def adoption_speed(state):
  boundary = str(state.upcomingBoundaryId)
  if not boundary:
    return float(state.speedLimit) if number(state.speedLimit, positive=True) else None
  if len(boundary) != 64 or any(c not in '0123456789abcdef' for c in boundary):
    return None
  speed = state.upcomingSpeedLimit
  return float(speed) if number(speed, positive=True) else None


def source_matches(state, observation, producer_session: str, now_ns: int) -> bool:
  observed_ns = int(state.sourceObservedMonoTime)
  expiry_ns = int(state.sourceValidUntilMonoTime)
  # Card can have a newer packet for the same episode before the planner publishes again.
  return (str(state.source) == 'dashboard' and str(state.observationKind) == 'valid' and
          str(state.sourceProducerSessionId) == producer_session and producer_session != '' and
          observation.status == 'valid' and int(state.sourceEpisode) > 0 and
          int(state.sourceEpisode) == int(observation.episode) and
          0 < observed_ns <= state.frameMonoTime and observed_ns <= observation.observed_ns <= now_ns and
          observed_ns < expiry_ns and observation.observed_ns < observation.valid_until_ns and
          now_ns <= min(expiry_ns, observation.valid_until_ns) and
          (observed_ns != observation.observed_ns or expiry_ns == observation.valid_until_ns) and
          number(state.speedLimit, positive=True) and number(observation.speed_mps, positive=True) and
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
                   str(state.source) in tuple(Source) and number(state.speedLimit, positive=True) and adoption_speed(state) is not None) else None


def state_current(sm, observation, producer_session: str, now_ns: int):
  """Tesla set-speed rule: the planner's current limit; a dashboard reading must still be this card's own."""
  state = _current_state(sm, now_ns)
  if (state is not None and not state.upcomingBoundaryId and str(state.source) == 'dashboard' and
      (observation is None or not source_matches(state, observation, producer_session, now_ns))):
    return None
  return state


def command_applicable(command, state, observation, producer_session: str, now_ns: int, selected_mps: float, *,
                       long_active: bool, button_event: bool) -> bool:
  """Tesla set-speed rule: whether card may write this SLC command's target into software SET now."""
  if state is None or button_event or not long_active or not state.hasAccepted:
    return False
  # Adopt limits early: the accepted limit is the upcoming Maps boundary's, not the road's current one, so the command
  # is bound to Maps (which has no producer episode) and to that boundary instead of the current source.
  preview = bool(state.acceptedIsPreview)
  if preview and (str(state.acceptedSource) != 'map' or not str(state.upcomingBoundaryId) or adoption_speed(state) is None or
                  abs(float(state.acceptedSpeedLimit) - float(state.upcomingSpeedLimit)) > 0.02):
    return False
  bound = ('map', 0, '', 0, 0) if preview else (str(state.source), int(state.sourceEpisode), str(state.sourceProducerSessionId),
                                                 int(state.sourceObservedMonoTime), int(state.sourceValidUntilMonoTime))
  if (str(command.kind) != 'adoptAcceptedLimit' or not str(command.sessionId) or
      str(command.sessionId) != str(state.sessionId) or int(command.commandId) <= 0 or int(command.presentationId) <= 0 or
      int(command.commandId) != int(state.commandId) or
      int(command.presentationId) != int(state.presentationId) or
      (str(command.source), int(command.sourceEpisode), str(command.sourceProducerSessionId),
       int(command.sourceObservedMonoTime), int(command.sourceValidUntilMonoTime)) != bound or
      (not preview and str(state.source) == 'dashboard' and
       (observation is None or not source_matches(state, observation, producer_session, now_ns)))):
    return False
  issued, expires = int(command.issuedMonoTime), int(command.expiresMonoTime)
  if not 0 < issued <= now_ns <= expires <= issued + STATE_MAX_AGE_NS:
    return False
  expected, target = float(command.expectedSelectedMps), float(command.targetMps)
  # SET must still be where the planner saw it, and the target must be the accepted limit plus its offset: the road's
  # current limit, or the early preview checked above.
  # Float32 transport can round a legal boundary just outside its double-precision value.
  return (number(expected, positive=True) and number(target, positive=True) and number(selected_mps, positive=True) and
          abs(expected - selected_mps) <= 0.1 and
          (preview or abs(float(state.acceptedSpeedLimit) - float(state.speedLimit)) <= 0.02) and
          abs(target - (float(state.acceptedSpeedLimit) + float(state.offset))) <= 0.1 and
          V_CRUISE_MIN / 3.6 - 1e-5 <= target <= V_CRUISE_MAX / 3.6 + 1e-5)


def active_ceiling(sm, now_ns: int, *, after_ns: int = 0) -> float | None:
  state = _state(sm, now_ns)
  if state is None or int(state.frameMonoTime) <= after_ns or not state.hasCeiling:
    return None
  cap = float(state.effectiveCap)
  return cap if number(cap, positive=True) and cap <= V_CRUISE_MAX / 3.6 + 1e-5 else None


def accepted_target(sm, now_ns: int, *, after_ns: int = 0) -> float | None:
  """The accepted limit plus offset, so wheel steps can stop on it."""
  state = _state(sm, now_ns)
  if state is None or int(state.frameMonoTime) <= after_ns or not state.hasAcceptedAdjustedLimit:
    return None
  return float(state.acceptedAdjustedLimit) if number(state.acceptedAdjustedLimit, positive=True) else None


def wheel_override_context(sm, now_ns: int, *, selected_kph: float, is_metric: bool,
                           after_ns: int = 0) -> SlcWheelContext | None:
  state = _state(sm, now_ns)
  if (state is None or int(state.frameMonoTime) <= after_ns or not state.hasAcceptedAdjustedLimit or
      not number(state.acceptedAdjustedLimit, positive=True) or not V_CRUISE_MIN <= selected_kph <= V_CRUISE_MAX or
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
  return state if number(target, positive=True) and V_CRUISE_MIN / 3.6 - 1e-5 <= target <= V_CRUISE_MAX / 3.6 + 1e-5 else None


def engagement_target(sm, observation, producer_session: str, now_ns: int, *, after_ns: int = 0, current_only: bool = False):
  current = engagement_state(sm, observation, producer_session, now_ns, after_ns=after_ns)
  if current_only:
    # Tesla set-speed rule: engaging starts SET at the road's current limit, never at an earlier acceptance.
    return (float(current.presentationAdjustedLimit), current) if current is not None else (None, None)
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
  return target if number(target, positive=True) and V_CRUISE_MIN / 3.6 - 1e-5 <= target <= V_CRUISE_MAX / 3.6 + 1e-5 else None


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
  if (str(state.pendingSource) not in tuple(Source) or not number(state.pendingSpeedLimit, positive=True) or
      not 0 < started <= int(state.frameMonoTime) <= started + CONFIRMATION_NS):
    return None
  return SlcPendingConfirmation(str(state.sessionId), int(state.decisionId), int(state.presentationId))


def curve_press_receipt(CS: car.CarState, helper: VCruiseHelper, sm, *, curve_replay: bool,
                        slc_replay: bool, host_long_active: bool, observed_ns: int):
  """A confirmation or native standstill RES must not also override Curve control."""
  accelerate = frozenset((car.CarState.ButtonEvent.Type.accelCruise,
                          car.CarState.ButtonEvent.Type.resumeCruise))
  pressed = {b.type.raw for b in CS.buttonEvents if b.pressed and b.type.raw in accelerate}
  if (not curve_replay or not host_long_active or not CS.canValid or CS.canTimeout or not CS.cruiseState.available or
      CS.standstill or CS.cruiseState.standstill or not pressed):
    return None
  if (helper.slc_consumed_button is not None or bool(pressed & helper.slc_suppressed_buttons) or
      slc_replay and bool(sm['slcState'].hasPending)):
    return None
  return ('curveAccelPress', None, float(helper.v_cruise_kph_last / 3.6),
          float(helper.v_cruise_kph / 3.6), observed_ns)
