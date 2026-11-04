"""A second Wi-Fi station, aa0, for the car's projection network so wlan0 keeps home Wi-Fi.

The unprivileged daemon leases aa0 from a root worker over a pipe, like the Galaxy hotspot:
EOF, ten seconds without a heartbeat, or SIGTERM deletes the interface. The worker only ever
creates or deletes aa0; it never touches wlan0, p2p0 or galaxy0.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openpilot.starpilot.system.starport.network import DedicatedUnavailable, JoinTimeout, NetworkError
from openpilot.starpilot.system.pipe_worker import claim, owned, spawn, stop_event

INTERFACE = "aa0"
ALIAS = "starpilot-aa-wifi"
RUNTIME = Path("/run/starpilot-aa-wifi")
HEARTBEAT_S = 2.0
LEASE_TIMEOUT_S = 10.0
READY_TIMEOUT_S = 5.0
# The car's network never appeared this many times on aa0; finish the session on wlan0 in case aa0 is the reason.
DEDICATED_TIMEOUTS = 2


class DedicatedInterface:
  """Holds aa0 while open."""

  def __init__(self, log: Callable[..., None], popen=subprocess.Popen, ready_timeout: float = READY_TIMEOUT_S):
    self.log, self.popen, self.ready_timeout = log, popen, ready_timeout
    self.child = None
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None

  def open(self) -> str:
    self.child = spawn("starpilot-aa-wifi", "openpilot.starpilot.system.starport.car_interface", popen=self.popen)
    try:
      if not select.select([self.child.stdout], [], [], self.ready_timeout)[0]:
        raise DedicatedUnavailable(f"{INTERFACE} was not ready in time")
      line = self.child.stdout.readline()
      reply = json.loads(line) if line else {}
      if not reply.get("ready"):
        raise DedicatedUnavailable(reply.get("error") or f"Could not create {INTERFACE}")
    except (OSError, ValueError) as error:
      self.close()
      raise DedicatedUnavailable(f"Could not create {INTERFACE}: {error}") from error
    except BaseException:
      self.close()
      raise
    self._thread = threading.Thread(target=self._heartbeat, args=(self.child,), name="aa_wifi_lease", daemon=True)
    self._thread.start()
    return INTERFACE

  def _heartbeat(self, child) -> None:
    while not self._stop.wait(HEARTBEAT_S):
      try:
        child.stdin.write(b"\n")
        child.stdin.flush()
      except (OSError, ValueError):
        return

  def close(self) -> None:
    self._stop.set()
    child, self.child = self.child, None
    if child is None:
      return
    try:
      child.stdin.close()
    except OSError:
      pass
    try:
      child.wait(timeout=12)
    except subprocess.TimeoutExpired:
      child.kill()  # the worker still deletes aa0 once its lease runs out
    if child.stdout is not None:
      child.stdout.close()


class DedicatedLease:
  """The car's Wi-Fi on aa0, falling back to the legacy lease on wlan0 for a session that cannot use it."""

  def __init__(self, log: Callable[..., None], fallback_interface: str, lease_factory, interface_factory=None,
               enabled: bool = True):
    self.log, self.fallback_interface = log, fallback_interface
    self.lease_factory, self.interface_factory = lease_factory, interface_factory or DedicatedInterface
    self.enabled = enabled
    self.lease = None
    self.iface = None
    self.legacy = not enabled
    self.timeouts = 0
    self.aa0_joined = False
    self.aa0_failed = False  # aa0 could not join but wlan0 then did; the supervisor counts these per car
    self._opening: threading.Thread | None = None
    self._open_error: Exception | None = None
    self._cancel = threading.Event()
    self.home = None  # a hold on wlan0's autoconnect, from hold_home_wifi until the session ends
    self._home_lock = threading.Lock()

  @property
  def local_ip(self):
    return self.lease.local_ip if self.lease is not None else ""

  @property
  def dedicated(self) -> bool:
    """The car is on aa0, sharing the radio with wlan0; the supervisor's radio watch keys off this."""
    return not self.legacy

  @property
  def interface(self) -> str:
    return getattr(self.lease, "interface", self.fallback_interface if self.legacy else INTERFACE)

  def prepare(self) -> None:
    """Create aa0 while Bluetooth connects, so the car's credentials do not wait on it."""
    if self.legacy or self.lease is not None or self._opening is not None:
      return
    self._cancel.clear()
    self._opening = threading.Thread(target=self._open, name="aa_wifi_open", daemon=True)
    self._opening.start()

  def _open(self) -> None:
    try:
      self.iface = self.interface_factory(self.log)
      self.iface.open()
      lease = self.lease_factory(self.log, INTERFACE, dedicated=True)
      try:
        lease.prepare_device(self._cancel.is_set)
      except BaseException:
        lease.release(restore=True)
        raise
      self.lease = lease
      self.log("wifi_dedicated", interface=INTERFACE)
    except Exception as error:
      self._open_error = error
      if self._cancel.is_set() and self.iface is not None:
        self.iface.close()  # the session already ended; nothing else will close it

  def _finish_opening(self) -> None:
    if self._opening is not None:
      self._opening.join()
      self._opening = None
    error, self._open_error = self._open_error, None
    if error is not None and not self._cancel.is_set():
      self._fall_back(error)

  def _ensure(self) -> None:
    self.prepare()
    self._finish_opening()
    if self.lease is None:
      self.legacy = True
      self.lease = self.lease_factory(self.log, self.fallback_interface)

  def hold_home_wifi(self) -> None:
    """wlan0 lost its network while the car streams on aa0: keep it from searching on the shared radio until the
    session ends. Called from the radio watch thread."""
    with self._home_lock:
      if self.legacy or self.home is not None:
        return
      home = self.lease_factory(self.log, self.fallback_interface)
      try:
        held = home.hold_autoconnect()
      except NetworkError as error:
        self.log("wifi_home_hold_failed", error=str(error))
        held = False
      if not held:
        home.release(restore=True)
        return
      self.home = home
      self.log("wifi_home_held", interface=self.fallback_interface)

  def _release_home(self) -> None:
    with self._home_lock:
      home, self.home = self.home, None
    if home is not None:
      try:
        home.release(restore=True)
        self.log("wifi_home_released", interface=self.fallback_interface)
      except Exception as error:
        self.log("wifi_home_release_failed", error=str(error))

  def _fall_back(self, error: Exception) -> None:
    self._release_home()  # the car's join moves onto wlan0, which keeps its own autoconnect pause
    self.log("wifi_dedicated_unavailable", error=str(error) or type(error).__name__)
    if self.lease is not None:
      try:
        self.lease.release(restore=True)
      except Exception:
        pass
    if self.iface is not None:
      self.iface.close()
    self.lease = self.iface = None
    self.legacy = True

  def _reopen(self) -> None:
    """Drop aa0 and its lease, then create both again; a failure to recreate falls back to wlan0."""
    if self.lease is not None:
      try:
        self.lease.release(restore=True)
      except Exception:
        pass
    if self.iface is not None:
      self.iface.close()
    self.lease = self.iface = None
    self._ensure()

  def acquire(self, credentials, cancelled: Callable[[], bool] = lambda: False, **kwargs):
    self._ensure()
    try:
      local_ip = self.lease.acquire(credentials, cancelled=cancelled, **kwargs)
    except NetworkError as error:
      if self.legacy or cancelled():
        raise
      if isinstance(error, JoinTimeout):
        # The car's network never showed up, which wlan0 would not change; give aa0 another attempt first.
        self.timeouts += 1
        if self.timeouts >= DEDICATED_TIMEOUTS:
          self._fall_back(error)
        raise
      if not isinstance(error, DedicatedUnavailable):
        # A NetworkManager restart leaves the lease holding device paths that no longer exist (2026-11-04: the rest of
        # the drive then ran on wlan0). A fresh aa0 and lease resolve them again; only a second failure falls back.
        self.log("wifi_dedicated_reopen", error=str(error) or type(error).__name__)
        self._reopen()
        try:
          local_ip = self.lease.acquire(credentials, cancelled=cancelled, **kwargs)
        except NetworkError as retry_error:
          if self.legacy or cancelled() or isinstance(retry_error, JoinTimeout):
            raise
          error = retry_error
        else:
          if not self.legacy:
            self.aa0_joined = True
            self.timeouts = 0
          return local_ip
      # aa0 itself failed; finish this same join on wlan0 rather than report a failure to the car.
      self._fall_back(error)
      self._ensure()
      local_ip = self.lease.acquire(credentials, cancelled=cancelled, **kwargs)
      self.aa0_failed = not isinstance(error, DedicatedUnavailable)
      return local_ip
    if not self.legacy:
      self.aa0_joined = True
      self.timeouts = 0
    return local_ip

  def still_connected(self) -> bool:
    return self.lease is not None and self.lease.still_connected()

  def release(self, restore: bool = True) -> None:
    if restore:
      self._cancel.set()
      self._finish_opening()
    if self.lease is not None:
      self.lease.release(restore=restore)
    if restore:
      self._release_home()
      if self.iface is not None:
        self.iface.close()
      self.lease = self.iface = None
      self.legacy = not self.enabled
      self.timeouts = 0


def serve(wireless: Callable[..., Any], stdin: int, stop: threading.Event, run=subprocess.run, is_owned=owned,
          lease_timeout: float = LEASE_TIMEOUT_S) -> None:
  """Create aa0, report on stdout, and hold it until stdin closes, heartbeats stop or ``stop`` is set."""
  created = False
  try:
    if is_owned(INTERFACE, ALIAS):
      wireless("delete", INTERFACE)  # left behind by a crashed worker
    try:
      if INTERFACE in wireless("interfaces"):
        raise RuntimeError(f"{INTERFACE} exists and is not ours")
      wireless("create", INTERFACE, iftype=2)
      created = True
      run(["ip", "link", "set", "dev", INTERFACE, "alias", ALIAS], check=True, capture_output=True, timeout=5)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
      print(json.dumps({"ready": False, "error": str(error) or type(error).__name__}), flush=True)
      return
    print(json.dumps({"ready": True, "interface": INTERFACE}), flush=True)
    last = time.monotonic()
    while not stop.is_set() and time.monotonic() - last < lease_timeout:
      if select.select([stdin], [], [], 0.5)[0]:
        if not os.read(stdin, 64):
          break
        last = time.monotonic()
  finally:
    if created or is_owned(INTERFACE, ALIAS):
      wireless("delete", INTERFACE)


def main() -> None:
  from openpilot.starpilot.galaxy.hotspot_netlink import WirelessInterfaces
  fd = claim(RUNTIME, "The car interface worker")
  stop = stop_event()

  def wireless(method: str, *args, **kwargs):
    client = WirelessInterfaces()
    try:
      return getattr(client, method)(*args, **kwargs)
    finally:
      client.close()

  try:
    serve(wireless, sys.stdin.fileno(), stop)
  finally:
    os.close(fd)


if __name__ == "__main__":
  main()
