"""One UI publisher for state-bound SLC requests.

The planner alone decides whether an action is accepted. This module never
changes a displayed limit, cruise target, Params value, or camera state.
"""

from collections.abc import Callable
import math
import time
from typing import Any

import openpilot.cereal.messaging as messaging

from openpilot.starpilot.ui.onroad_state import SlcActionKind, SlcUiRequest


_MAX_STATE_AGE_NS = 100_000_000  # two periods of the 20 Hz slcState service
_DUPLICATE_WINDOW_NS = 2_000_000_000


def _positive_number(value: Any) -> bool:
  try:
    number = float(value)
  except (TypeError, ValueError, OverflowError):
    return False
  return math.isfinite(number) and number > 0


class SlcActionDispatcher:
  """Validate a fresh state again at release, then publish one typed request."""

  def __init__(self, publisher: Any, read_current: Callable[[int], Any | None],
               clock: Callable[[], int] = time.monotonic_ns):
    self.publisher = publisher
    self.read_current = read_current
    self.clock = clock
    self._session = ""
    self._last_sequence = 0
    self._last_context: tuple[str, int, int, SlcActionKind] | None = None
    self._last_context_ns = 0
    self._feedback_sequence = 0
    self._feedback_started_ns = 0

  def dispatch(self, request: SlcUiRequest, *, wheel_observed_ns: int = 0) -> bool:
    if not _positive_number(request.candidate_speed_mps):
      return False
    now_ns = self.clock()
    state = self.read_current(now_ns)
    if state is None:
      return False
    try:
      session = str(state.sessionId)
      frame_ns = int(state.frameMonoTime)
      decision_id = int(state.decisionId)
      presentation_id = int(state.presentationId)
      if (not session or session != request.session_id or not bool(state.enabled) or
          presentation_id <= 0 or
          str(state.observationKind) == 'valid' and str(state.source) == 'none' or
          not 0 < frame_ns <= now_ns or
          now_ns - frame_ns > _MAX_STATE_AGE_NS or
          decision_id != request.decision_id or presentation_id != request.presentation_id):
        return False
      if request.kind in (SlcActionKind.ACCEPT, SlcActionKind.REJECT):
        if (not bool(state.hasPending) or bool(getattr(state, 'automaticAdoptionPending', False)) or
            decision_id <= 0 or not _positive_number(state.pendingSpeedLimit) or
            not math.isclose(float(state.pendingSpeedLimit), request.candidate_speed_mps, abs_tol=0.001)):
          return False
      elif request.kind == SlcActionKind.ADOPT:
        if (str(state.observationKind) != 'valid' or str(state.source) == 'none' or
            bool(state.hasPending) or not _positive_number(state.speedLimit) or
            not math.isclose(float(state.speedLimit), request.candidate_speed_mps, abs_tol=0.001)):
          return False
      else:
        return False
      acknowledged_sequence = int(state.actionSequenceId)
      if acknowledged_sequence < 0:
        return False
    except (AttributeError, TypeError, ValueError, OverflowError):
      return False

    context = (session, decision_id, presentation_id, request.kind)
    if (self._last_context is not None and context[:3] == self._last_context[:3] and
        now_ns - self._last_context_ns < _DUPLICATE_WINDOW_NS and
        (context == self._last_context or acknowledged_sequence < self._last_sequence)):
      return False
    if session != self._session:
      self._session = session
      self._last_sequence = 0
      self._last_context = None
    sequence = max(now_ns, acknowledged_sequence + 1, self._last_sequence + 1, 1)
    try:
      message = messaging.new_message("slcAction", valid=True)
      message.logMonoTime = now_ns
      action = message.slcAction
      action.sessionId = session
      action.sequenceId = sequence
      action.decisionId = decision_id
      action.presentationId = presentation_id
      action.kind = request.kind.value
      if request.kind == SlcActionKind.ADOPT:
        action.wheelAdoptMonoTime = wheel_observed_ns
      self.publisher.send("slcAction", message)
    except (OSError, RuntimeError, ValueError):
      return False
    self._last_sequence = sequence
    self._last_context = context
    self._last_context_ns = now_ns
    self._feedback_sequence = sequence
    self._feedback_started_ns = now_ns
    return True

  def clear_feedback(self):
    self._feedback_sequence = 0

  def feedback(self, observation) -> str:
    if observation.session_id != self._session:
      self._feedback_sequence = 0
    if not self._feedback_sequence:
      return ''
    if (observation.pending_speed_limit_mps is not None and self._last_context is not None and
        observation.decision_id != self._last_context[1]):
      self.clear_feedback()
      return ''
    age = self.clock() - self._feedback_started_ns
    if age > 4_000_000_000:
      self._feedback_sequence = 0
      return ''
    if observation.action_sequence_id != self._feedback_sequence:
      return 'Waiting…' if age < 2_000_000_000 else 'Not confirmed'
    return {'accept': 'Limit accepted', 'reject': 'Set speed kept', 'adopt': 'Limit adopted'}.get(
      observation.action_status, 'Not applied')
