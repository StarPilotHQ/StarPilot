"""When to start and stop projection without anyone pressing Start.

The car is "present" while the comma is onroad (ignition) or has a Bluetooth
link to the chosen car (the car reached the comma's hands-free gateway, as it
does with a phone when it powers on). Pure decision logic; the supervisor feeds
it observations from its housekeeping tick and carries out the result.

* Start when the car is present, a car is chosen, Bluetooth is up, and the user
  has not stopped projection during this drive.
* Stop a session once the car has been gone for ``STOP_AFTER`` seconds, so a
  switched-off car does not leave the comma retrying and parked on its Wi-Fi.
  Sessions that never saw the car (a manual Start with the car off) are left to
  the user, as before.
* A manual Stop while a session runs holds auto-connect off until the next drive
  (the next time the comma goes onroad) or a manual Start.
"""

from __future__ import annotations

STOP_AFTER = 60.0            # car gone this long -> end the session
START_REFUSED_WAIT = 30.0    # Start refused (e.g. identity missing): wait before asking again


class AutoConnectPolicy:
  def __init__(self):
    self.suppressed = False
    self._was_onroad = False
    self._seen_present = False
    self._absent_since: float | None = None
    self._not_before = 0.0

  def manual_start(self) -> None:
    self.suppressed = False

  def manual_stop(self, running: bool) -> None:
    if running:
      self.suppressed = True

  def hold(self) -> None:
    self.suppressed = True

  def start_refused(self, now: float) -> None:
    self._not_before = now + START_REFUSED_WAIT

  def decide(self, now: float, *, enabled: bool, onroad: bool, car_link: bool, ready: bool, running: bool) -> str | None:
    """Return "start", "stop" or None."""
    if onroad and not self._was_onroad:
      self.suppressed = False  # a new drive
    self._was_onroad = onroad
    present = onroad or car_link

    if running:
      if present:
        self._seen_present, self._absent_since = True, None
      elif self._seen_present:
        if self._absent_since is None:
          self._absent_since = now
        elif now - self._absent_since >= STOP_AFTER:
          return "stop"
      return None

    self._seen_present, self._absent_since = False, None
    if enabled and present and ready and not self.suppressed and now >= self._not_before:
      self._seen_present = True  # even if the car goes away before the session is seen running
      return "start"
    return None
