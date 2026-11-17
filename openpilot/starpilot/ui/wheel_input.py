"""Physical wheel input remains live when display rendering is asleep."""

from typing import Any


def onroad_action_context(session: Any, device: Any, ui: Any) -> bool:
  from openpilot.starpilot.ui.shell import ShellMode
  # A sleeping display can retain HOME/SETTINGS from its last rendered frame.
  # Only a currently consumed physical wheel command may use live onroad state
  # in that case; touch, controller and projection authority are unchanged.
  return bool(session._mode == ShellMode.ONROAD or
              getattr(session, "_screen_off_wheel", False) and not device.awake and ui.started)


def poll_screen_off_wheel(session: Any, awake: bool, now_ns: int) -> None:
  if awake:
    return  # The rendered onroad page keeps its existing admission and timing.
  session._screen_off_wheel = True
  try:
    session._poll_wheel(now_ns)
  finally:
    session._screen_off_wheel = False
