"""Leapmotor: a cancel the car makes by itself must not look like the driver's.

The B10's camera drops its ACC on its own, for example when the front passenger
unbuckles (route 00000018, 2026-11-18): ACC_STATE goes to 1 (not ready), so
cruise is off and unavailable in the same frame. openpilot then only saw
pcmDisable and wrongCarMode, user disables with a soft chime and no message.
The driver's own cancels (brake, the stalk pushed up) also make ACC_STATE 1,
so they are excluded; anything else is announced as a takeover.
"""
from openpilot.cereal import log
from openpilot.selfdrive.selfdrived.events import ET, ImmediateDisableAlert
from opendbc.car import structs

EventName = log.OnroadEvent.EventName
ButtonType = structs.CarState.ButtonEvent.Type

CANCEL_PUSH_WINDOW_FRAMES = 50  # 0.5 s at 100 Hz: logged stalk cancels ended assist within about 0.25 s
QUIET_DISABLES = (EventName.pcmDisable, EventName.wrongCarMode)


def event_rules(cp) -> dict:
  return {EventName.cruiseDisabled: {ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Car Cancelled Cruise")}}


class CarCancel:
  def __init__(self):
    self.frames_since_cancel_push = CANCEL_PUSH_WINDOW_FRAMES + 1

  def update(self, cs, cs_prev) -> bool:
    """True on the frame the car turns its ACC off without the driver braking or pushing cancel."""
    if any(b.type == ButtonType.cancel for b in cs.buttonEvents):
      self.frames_since_cancel_push = 0
    else:
      self.frames_since_cancel_push += 1
    return bool(cs_prev.cruiseState.enabled and not cs.cruiseState.enabled and not cs.cruiseState.available and
                not cs.brakePressed and not cs_prev.brakePressed and
                self.frames_since_cancel_push > CANCEL_PUSH_WINDOW_FRAMES)


def announce(events) -> None:
  """Replace the quiet user disables with the takeover alert."""
  events.events = [e for e in events.events if e not in QUIET_DISABLES]
  events.add(EventName.cruiseDisabled)
