"""Read-only BlueZ state for the compact Home indicator: off, on, or a device connected.

This does not enable Bluetooth or manage devices. The system-bus query runs off
the render thread and expires after a short gap.
"""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import time

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

STATES = ("off", "on", "connected")
BLUEZ_ROOT = DBusAddress("/", bus_name="org.bluez", interface="org.freedesktop.DBus.ObjectManager")


def _clock_ns() -> int:
  boot = getattr(time, "CLOCK_BOOTTIME", None)
  return time.clock_gettime_ns(boot) if boot is not None else time.monotonic_ns()


def _managed_objects() -> dict:
  with open_dbus_connection(bus="SYSTEM") as connection:
    reply = connection.send_and_get_reply(new_method_call(BLUEZ_ROOT, "GetManagedObjects"), timeout=1)
  return reply.body[0]


def read_bluetooth_details() -> tuple[str, tuple[str, ...]]:
  """One BlueZ object listing: adapter state ('off', 'on', 'connected') and connected device names."""
  try:
    objects = _managed_objects()

    def prop(path, interface, name):
      value = objects.get(path, {}).get(interface, {}).get(name)
      return value[1] if isinstance(value, tuple) and len(value) == 2 else None
    if prop("/org/bluez/hci0", "org.bluez.Adapter1", "Powered") is not True:
      return "off", ()
    devices = []
    for path in objects:
      if path.startswith("/org/bluez/hci0/dev_") and prop(path, "org.bluez.Device1", "Connected") is True:
        name = prop(path, "org.bluez.Device1", "Name")
        if not isinstance(name, str) or not name.strip():
          name = prop(path, "org.bluez.Device1", "Alias")
        label = name.strip() if isinstance(name, str) and name.strip() else "Bluetooth"
        devices.append(label)
  except Exception:
    return "off", ()
  return ("connected", tuple(devices)) if devices else ("on", ())


def read_bluetooth_state() -> str:
  """One BlueZ object listing: the adapter's power and whether any of its devices is connected."""
  return read_bluetooth_details()[0]


class BluetoothStatusSource:
  """One bounded background read; stale, failed, or absent status is false."""

  REFRESH_NS = 2_000_000_000
  MAX_AGE_NS = 4_000_000_000

  def __init__(self, request: Callable[[], str] = read_bluetooth_state,
               clock: Callable[[], int] = _clock_ns):
    self.request = request
    self.clock = clock
    self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bluetooth-status")
    self.pending: Future[tuple[str, int]] | None = None
    self.next_read_ns = 0
    self.state = "off"
    self.observed_ns: int | None = None
    self.closed = False

  def _poll(self) -> tuple[str, int]:
    try:
      state = self.request()
    except (OSError, RuntimeError, ValueError):
      state = "off"
    return state if state in STATES else "off", self.clock()

  def snapshot(self) -> str:
    if self.closed:
      return "off"
    now = self.clock()
    if self.pending is not None and self.pending.done():
      try:
        self.state, self.observed_ns = self.pending.result()
      except (OSError, RuntimeError, ValueError):
        self.state, self.observed_ns = "off", None
      self.pending = None
    if now < self.next_read_ns - self.REFRESH_NS:
      self.next_read_ns = 0
      self.state, self.observed_ns = "off", None
    if self.pending is None and now >= self.next_read_ns:
      self.pending = self.executor.submit(self._poll)
      self.next_read_ns = now + self.REFRESH_NS
    fresh = self.observed_ns is not None and 0 <= now - self.observed_ns <= self.MAX_AGE_NS
    return self.state if fresh else "off"

  def close(self) -> None:
    self.closed = True
    self.executor.shutdown(wait=False, cancel_futures=True)
