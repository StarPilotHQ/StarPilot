"""Shared-radio change monitoring for Android Auto projection."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

RADIO_CHECK_INTERVAL = 0.5   # how often the comma's own Wi-Fi is checked for a join or roam (a sysfs read)
RADIO_CHANGE_WINDOW = 15.0   # a join or roam this recent can still stall the car (11-02: stall came 7.5 s after a roam)


class SharedRadioWatch:
  """Notices the comma's own Wi-Fi joining or roaming while the car is on aa0; both share one radio.

  The link going down or up bumps carrier_changes, a plain sysfs read. The network's name is never read or logged.
  """

  def __init__(self, log: Callable[..., None], interface: str = "wlan0", interval: float = RADIO_CHECK_INTERVAL,
               counter: Path | None = None, carrier: Path | None = None, on_down: Callable[[], None] | None = None,
               on_stall: Callable[[], None] | None = None):
    self.log, self.interface, self.interval = log, interface, interval
    self.counter = counter or Path(f"/sys/class/net/{interface}/carrier_changes")
    self.carrier = carrier or Path(f"/sys/class/net/{interface}/carrier")
    self.on_down = on_down  # the other network was lost, not just roamed
    self.on_stall = on_stall  # the car's acks stopped after a change that has not connected yet
    self.changed_at: float | None = None
    self._stop = threading.Event()
    self._thread = threading.Thread(target=self._run, name="aa_radio_watch", daemon=True)

  def start(self) -> SharedRadioWatch:
    self._thread.start()
    return self

  def stop(self) -> None:
    self._stop.set()

  def recent(self, now: float | None = None) -> bool:
    """Whether the other network joined or roamed within RADIO_CHANGE_WINDOW."""
    changed_at = self.changed_at
    now = time.monotonic() if now is None else now
    return changed_at is not None and now - changed_at <= RADIO_CHANGE_WINDOW

  def _read(self) -> str | None:
    try:
      return self.counter.read_text().strip()
    except OSError:
      return None

  def _run(self) -> None:
    last = self._read()
    while not self._stop.wait(self.interval):
      current = self._read()
      if current is None or current == last:
        continue
      last = current
      self.changed_at = time.monotonic()
      self.log("other_wifi_changed", interface=self.interface)
      if self.on_down is not None and not self._carrier():
        try:
          self.on_down()
        except Exception as error:
          self.log("other_wifi_hold_failed", error=str(error) or type(error).__name__)

  def stalled(self) -> None:
    # A join that already connected has only its address left. Stopping it there lost the hotspot and still
    # dropped the car on 2026-11-16, while a joined hotspot then streamed alongside the car for minutes.
    if self._carrier():
      self.log("other_wifi_join_kept", interface=self.interface)
      return
    if self.on_stall is not None:
      self.on_stall()

  def _carrier(self) -> bool:
    try:
      return self.carrier.read_text().strip() == "1"
    except OSError:
      return False  # reading carrier of a down interface fails with EINVAL
