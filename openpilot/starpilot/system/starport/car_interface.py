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
HOME_RETRY_S = 15.0
HOME_RETRY_MAX_S = 300.0
HOME_CHECK_S = 15.0        # how often a held wlan0 looks for an allowed network to join
HOTSPOT_PAUSE_WAIT_S = 10.0


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


def _join_policy() -> dict:
  from openpilot.starpilot.galaxy.network_prefs import read_prefs
  return read_prefs()


def _hotspot():
  try:
    from openpilot.starpilot.galaxy import hotspot
    return hotspot
  except Exception:
    return None


class DedicatedLease:
  """The car's Wi-Fi on aa0, falling back to the legacy lease on wlan0 for a session that cannot use it."""

  def __init__(self, log: Callable[..., None], fallback_interface: str, lease_factory, interface_factory=None,
               enabled: bool = True, join_policy: Callable[[], dict] | None = None, hotspot=None):
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
    self.home = None
    self._home_lock = threading.Lock()
    self._home_retry: threading.Timer | None = None
    self._home_holds = 0
    self._join_stalled = False
    self._last_join = ""
    self.join_policy, self.hotspot = join_policy or _join_policy, hotspot if hotspot is not None else _hotspot()

  @property
  def local_ip(self):
    return self.lease.local_ip if self.lease is not None else ""

  @property
  def dedicated(self) -> bool:
    """Whether the car uses aa0 on the shared radio."""
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
    """Hold wlan0 off the shared radio; each hold lasts longer, up to HOME_RETRY_MAX_S."""
    with self._home_lock:
      if self.legacy or self._cancel.is_set():
        return
      if self.home is not None:
        if self._home_retry is None:
          self._arm(HOME_CHECK_S)  # the network joined during this drive was lost again
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
      after_s = min(HOME_RETRY_S * 2 ** min(self._home_holds, 5), HOME_RETRY_MAX_S)
      self._home_holds += 1
      self._arm(after_s)
      self.log("wifi_home_held", interface=self.fallback_interface, retry_after_s=after_s)

  def _arm(self, after_s: float) -> None:
    self._home_retry = threading.Timer(after_s, self._retry_home)
    self._home_retry.daemon = True
    self._home_retry.start()

  def stop_home_join(self) -> None:
    """Stop a wlan0 join that stalls the car."""
    with self._home_lock:
      home = self.home
    if home is None:
      self.hold_home_wifi()
      return
    self._join_stalled = True
    try:
      home.hold_autoconnect()  # stops the join this drive started by hand
    except NetworkError as error:
      self.log("wifi_home_hold_failed", error=str(error))

  def _retry_home(self) -> None:
    """When a hold runs out, join an allowed network by hand; wlan0 never hunts by itself during a drive."""
    with self._home_lock:
      home, self._home_retry = self.home, None
    join = getattr(home, "drive_join", None)
    if home is None or join is None:
      self._release_home()
      return
    self._join_stalled = False
    try:
      prefs = self.join_policy()
      parked_only = set(prefs.get("parkedOnly", ()))
      pause = bool(prefs.get("pauseHotspot")) and self.hotspot is not None
      outcome = join(lambda uuid: uuid not in parked_only,
                     before=self._pause_hotspot if pause else lambda: True,
                     after=self.hotspot.clear_pause if pause else lambda: None,
                     cancelled=lambda: self._cancel.is_set() or self._join_stalled or self.home is not home)
    except Exception as error:  # a join is optional; the car link must never depend on it
      self.log("wifi_drive_join_failed", error=str(error) or type(error).__name__)
      outcome = "failed"
    shown = "stalled" if self._join_stalled else outcome
    if shown != self._last_join:
      self.log("wifi_drive_join", interface=self.fallback_interface, outcome=shown)
    self._last_join = shown
    with self._home_lock:
      if self.home is not home or self._cancel.is_set():
        return
      if outcome == "joined" and not self._join_stalled:
        self._home_holds = 0
        return  # the next loss of this network arms the check again
      if self._join_stalled:
        after_s = min(HOME_RETRY_S * 2 ** min(self._home_holds, 5), HOME_RETRY_MAX_S)
        self._home_holds += 1
      else:
        after_s = HOME_CHECK_S  # a refused network is skipped for a while, so the next one is tried soon
      self._arm(after_s)

  def _pause_hotspot(self) -> bool:
    """Stand the Galaxy hotspot down and wait until its interface is gone; a join with it up can crash the radio."""
    self.hotspot.request_pause(HOTSPOT_PAUSE_WAIT_S + 30.0)
    deadline = time.monotonic() + HOTSPOT_PAUSE_WAIT_S
    while Path(f"/sys/class/net/{self.hotspot.INTERFACE}").exists():
      if time.monotonic() >= deadline or self._cancel.is_set():
        self.log("wifi_drive_join_skipped", reason="hotspot_still_up")
        return False
      time.sleep(0.2)
    return True

  def _release_home(self) -> None:
    with self._home_lock:
      home, self.home = self.home, None
      self._home_retry = None
    if home is not None:
      try:
        home.release(restore=True)
        self.log("wifi_home_released", interface=self.fallback_interface)
      except NetworkError as error:
        self.log("wifi_home_release_failed", error=str(error))

  def _fall_back(self, error: Exception) -> None:
    self.legacy = True
    self._release_home()  # the car's join moves onto wlan0, which keeps its own autoconnect pause
    self.log("wifi_dedicated_unavailable", error=str(error) or type(error).__name__)
    if self.lease is not None:
      try:
        self.lease.release(restore=True)
      except Exception:  # the failed aa0 lease may be broken in any way; falling back must still finish
        pass
    if self.iface is not None:
      self.iface.close()
    self.lease = self.iface = None

  def _reopen(self) -> None:
    """Drop aa0 and its lease, then create both again; a failure to recreate falls back to wlan0."""
    if self.lease is not None:
      try:
        self.lease.release(restore=True)
      except NetworkError as error:
        self.log("wifi_dedicated_release_failed", error=str(error))
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
        # A NetworkManager restart leaves stale device paths; retry once on a fresh aa0.
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
      with self._home_lock:
        retry, self._home_retry, self._home_holds = self._home_retry, None, 0
      if retry is not None:
        retry.cancel()
      if self.hotspot is not None:
        self.hotspot.clear_pause()
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
