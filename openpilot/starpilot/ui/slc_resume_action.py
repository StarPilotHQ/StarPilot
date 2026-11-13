"""Resume Speed Limit + Offset: the SLC action a car button (wheel, stalk) or a favorite can run."""
import time

from openpilot.starpilot.common.car_control_config import NO_LIMIT_REASON, SPEED_RESUME
from openpilot.starpilot.favorites.state import FavoriteAction
from openpilot.starpilot.ui.onroad_state import SlcActionKind, SlcUiRequest


def resume_action(shell, ui_state, drive, *, require_display: bool) -> FavoriteAction:
  """Adopt the current limit + offset, or accept a limit waiting for confirmation.

  A car button is handled part-way through a screen frame, so the car's messages are read with the scroll wheel's age
  limit (shell._live_slc_message(..., car_input=True)), both here and when the dispatcher checks again before sending.
  require_display (a favorite) also needs the screen's own authority."""
  def read(now_ns):
    return shell._live_slc_message(now_ns, car_input=True)

  def blocked():
    return bool(ui_state.params.get_bool('SafeMode') or getattr(ui_state, 'replay_clock', None) is not None or
                getattr(shell, 'slc_actions', None) is None or require_display and not shell._favorite_authority())

  def invoke():
    live = None if blocked() else read(time.monotonic_ns())
    if live is None:
      return False
    kind = SlcActionKind.ACCEPT if live.hasPending and not live.automaticAdoptionPending else SlcActionKind.ADOPT
    request = SlcUiRequest(kind, str(live.sessionId), int(live.decisionId), int(live.presentationId), float(live.speedLimit))
    return shell.slc_actions.dispatch(request, read_current=read)

  live = None if blocked() else read(time.monotonic_ns())
  available = bool(live is not None and live.enabled and live.observationKind == 'valid')
  reason = ('Unavailable in this context' if available or live is None and blocked() else
            'Speed Limit Controller is not running' if live is None or not live.enabled else NO_LIMIT_REASON)
  token = repr((drive, str(live.sessionId) if live is not None else '', int(live.presentationId) if live is not None else 0))
  return FavoriteAction(SPEED_RESUME, 'Resume Speed Limit + Offset', available=available, reason=reason, token=token,
                        invoke=invoke)
