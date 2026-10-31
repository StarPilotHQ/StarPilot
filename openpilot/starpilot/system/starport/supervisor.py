"""Android Auto session supervisor: owns every stage, deadline, retry and cleanup.

One session, started by the user or by auto-connect (see auto_connect.py), runs
in one worker thread:

  idle -> connecting_bluetooth -> discovering -> rfcomm -> wifi_start -> wifi_info
       -> joining_wifi -> connecting_tcp -> authenticating -> negotiating -> streaming
  streaming <-> suspended (head unit showing its own screen)
  failure -> cleanup -> backoff -> retry          stop -> cleanup -> idle

Each attempt has a generation number; Stop cancels immediately (sockets are
closed from the caller's thread to unblock I/O). Between retries the projection
network is released without restoring the previous Wi-Fi, which happens once,
on Stop. Nothing here runs as root or touches vehicle control.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import math
import os
import queue
import socket
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openpilot.common.hardware import COMMA_HARDWARE
from openpilot.starpilot.system.starport import car_profiles, device_health, identity as identity_store, system_slice
from openpilot.starpilot.system.starport.auto_connect import AutoConnectPolicy
from openpilot.starpilot.system.starport.bootstrap import MODERN_MAJOR, NAMES, BootstrapError, WirelessBootstrap
from openpilot.starpilot.system.starport.bugsink_report import PairingAttempt
from openpilot.starpilot.system.starport.frame_source import (DEFAULT_PATH as DEFAULT_FRAME_PATH, FLAG_ASYNC_READBACK, FLAG_NV12,
                                                                  FORMAT_NV12, FrameRequest)
from openpilot.starpilot.system.starport.projection_control import DEFAULT_CONTROL_SOCKET, NATIVE_FOCUS
from openpilot.starpilot.system.starport.touch import DEFAULT_TOUCH_SOCKET
from openpilot.starpilot.system.starport.view import CAR_FRAME_PATH, ViewSource, renderer_available
from openpilot.starpilot.system.starport.session import AuthenticationRejected, PeerRequestedStop, ProjectionSession

BACKOFF_SECONDS = (2.0, 4.0, 8.0, 15.0, 30.0)
STABLE_SESSION_SECONDS = 30.0
WIFI_RECOVER_RETRY_S = 30.0
DEDICATED_STRIKES = 2         # sessions where aa0 failed but wlan0 joined, before a car stays on wlan0
PEER_STOP_RETRY_SECONDS = 10.0
FRAME_MAX_AGE = 0.5          # never send a UI frame older than this
SOFTWARE_FPS = 15            # libx264 cadence; the hardware encoder runs at 30
UNAVAILABLE_AFTER = 1.0      # focused but no fresh UI frame for this long -> "unavailable" card; the Honda gives the screen back after 3 s without video
SDP_SETTLE = (1.5, 2.2, 3.0)
TCP_ATTEMPTS = 6
MAX_LOG_FILES = 20
LOG_QUEUE_MAX = 1000          # session log records waiting for the writer; past this they are dropped and counted
ENCODER_RECOVERIES = 3       # hardware encoder reopens allowed per window before the session is torn down
ENCODER_RECOVERY_WINDOW = 10.0
CAR_LINK_HOLD = 10.0         # a hands-free connection from the car counts as "car present" this long
HFP_FRESH = 3.0              # a hands-free link from the car this recent still means "the car is reaching out now"
CAR_FIRST_WAIT = 8.0         # at ignition, give the car this long to open hands-free before the comma pages it
NO_STARPORT_WAIT = 8.0    # a paired device must offer wireless projection within this long
USB_HANDSHAKE_WAIT = 10.0    # the car connected but sent no AOA START this long -> present as an accessory directly
USB_CONFIGURE_WAIT = 15.0    # slower receivers need time to enumerate after the AOA switch
LINK_CHECK_INTERVAL = 1.0    # how often the link watcher asks whether the car's Wi-Fi is still up
LINK_CHECK_SLOW = 1.0        # a link check slower than this is logged (NetworkManager answers over D-Bus)
STREAM_STALL_LOG = 1.0       # a streaming loop pass slower than this is logged
REPORTED_SETTINGS = ("connection", "usb_mode", "view", "encoder", "fps", "bitrate_kbps", "rate_control", "gpu_nv12",
                     "async_readback", "wifi_interface", "dedicated_wifi_interface", "verify_head_unit", "version_status",
                     "rfcomm_channel", "phone_class", "auto_connect")


def encoder_preference(configured: str) -> str:
  return 'hardware' if COMMA_HARDWARE else configured

STATE_LABELS = {
  "idle": "off", "connecting_bluetooth": "connecting to car", "discovering": "finding android auto",
  "rfcomm": "starting wireless setup", "wifi_start": "waiting for car", "wifi_info": "getting car wi-fi",
  "joining_wifi": "joining car wi-fi", "connecting_tcp": "connecting", "authenticating": "authenticating",
  "negotiating": "negotiating video", "streaming": "projecting", "suspended": "car showing its own screen",
  "backoff": "retrying", "stopping": "stopping", "error": "error",
  "waiting_for_usb": "plug into the car's usb", "usb_accessory": "starting usb",
  "waiting_for_ipad": "open headunitpad on the ipad", "waiting_for_tablet": "open open headunit on the tablet",
}


def _is_onroad() -> bool:
  try:
    from openpilot.common.params import Params
    from openpilot.starpilot.saved_source import read_saved
    raw, readable = read_saved(Params(), "IsOffroad", 1)
    return readable and raw == b"0"
  except Exception:
    return False


def jetlink_blocker() -> str:
  """Why wired USB cannot connect right now: Jetlink's computer link is using the USB-C port. Wired can still be chosen."""
  try:
    from openpilot.common.params import Params
    mode = Params().get("JetlinkMode")
    if type(mode) is int and mode in (1, 2, 3):
      return "Turn off Jetlink to use wired Android Auto"
  except Exception:
    pass
  return ""


def chestnut_blocker() -> str:
  """Why wired USB cannot be chosen at all: a Chestnut is plugged into the comma's USB-C port."""
  try:
    from openpilot.starpilot.models.jetlink_adapter import adapter
    if adapter().chestnut_present():
      return "Wired Android Auto is unavailable while Chestnut is connected"
  except Exception:
    pass
  return ""


def wired_blocker() -> str:
  """Why wired USB must not take the comma's USB-C port, or "" when it is free."""
  return jetlink_blocker() or chestnut_blocker()


class Cancelled(Exception):
  pass


class StrayLinkError(RuntimeError):
  """The controller holds a link to the car that the kernel lost; every new channel to the car fails with EALREADY."""


def _stray_link(error: BaseException) -> bool:
  return isinstance(error, OSError) and error.errno == errno.EALREADY


class NoLease:
  """Wired sessions use no car Wi-Fi network."""
  local_ip = None

  def release(self, restore: bool = False) -> None:
    pass


class UsbLease:
  """The link of a wired session: the USB cable, via the accessory bridge."""
  local_ip = None
  lost = "The car's USB connection ended"

  def __init__(self, bridge):
    self.bridge = bridge

  def still_connected(self) -> bool:
    return not self.bridge.closed.is_set()


def _wifi_dbm(interface: str = "wlan0") -> int | None:
  """Signal level of the joined network from /proc/net/wireless (a plain file read, never D-Bus)."""
  try:
    with open("/proc/net/wireless") as handle:
      for line in handle:
        name, _, rest = line.partition(":")
        if name.strip() == interface:
          return int(float(rest.split()[2].rstrip(".")))
  except (OSError, ValueError, IndexError):
    pass
  return None


LOCAL_SOCKET_ERRORS = (errno.ENOSYS, errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT)


def car_never_answered(error: BaseException) -> bool:
  """False when this device's own Bluetooth stack failed, so the car's cached channel says nothing about the car."""
  return not (isinstance(error, OSError) and error.errno in LOCAL_SOCKET_ERRORS)


def _where(error: BaseException, depth: int = 4) -> list[str]:
  """The innermost frames of a failure, so a bare exception still says which call raised it."""
  frames = traceback.extract_tb(error.__traceback__)[-depth:]
  return [f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}" for frame in frames]


class LinkWatch:
  """Checks the lease off the streaming thread.

  NetworkManager can take seconds to answer over D-Bus (a 5 s timeout ended three Toyota sessions on
  2026-10-08 before the car granted focus). A slow or failed check must neither stall video nor end the
  session; only a definite "not connected" does. The socket itself still fails fast on a real loss.
  """

  def __init__(self, lease, log: Callable[..., None], interval: float = LINK_CHECK_INTERVAL):
    self.lease, self.log, self.interval = lease, log, interval
    self.lost = ""
    self.wifi_dbm: int | None = None
    self._stop = threading.Event()
    self._thread = threading.Thread(target=self._run, name="aa_link_watch", daemon=True)

  def start(self) -> LinkWatch:
    self._thread.start()
    return self

  def stop(self) -> None:
    self._stop.set()  # a check stuck in D-Bus finishes on its own timeout; nothing waits for it

  def _run(self) -> None:
    check = getattr(self.lease, "still_connected", None)
    wireless = not isinstance(self.lease, (NoLease, UsbLease))
    while not self._stop.is_set():
      if wireless:
        self.wifi_dbm = _wifi_dbm(getattr(self.lease, "interface", "wlan0"))
      if check is not None:
        started = time.monotonic()
        try:
          connected = check()
        except Exception as error:
          connected = True  # unknown is not lost
          self.log("link_check_failed", error=str(error) or type(error).__name__, kind=type(error).__name__,
                   ms=round((time.monotonic() - started) * 1000), where=_where(error))
        else:
          spent = time.monotonic() - started
          if spent > LINK_CHECK_SLOW:
            self.log("link_check_slow", ms=round(spent * 1000), connected=connected)
        if not connected and not self._stop.is_set():
          self.lost = getattr(self.lease, "lost", "Lost the car's Wi-Fi network")
          self.log("link_lost", reason=self.lost, wifi_dbm=self.wifi_dbm)
          return
      self._stop.wait(self.interval)


class EventLog:
  """Sanitized JSONL session log under /data/starport/logs, plus the recent tail in memory.

  The streaming loop logs too, so records are queued and a background thread writes them: a slow
  or full disk never stalls projection. If the writer falls behind, records are dropped and the
  next one that fits is preceded by a ``log_dropped`` count.
  """

  def __init__(self, directory: Path | None = None):
    self.directory = directory or identity_store.LOG_DIR
    self.recent: deque[dict] = deque(maxlen=40)
    self.lock = threading.Lock()
    self._queue: queue.Queue = queue.Queue(maxsize=LOG_QUEUE_MAX)
    self._writer: threading.Thread | None = None
    self._writer_stop: threading.Event | None = None
    self._dropped = 0
    self.path: Path | None = None
    self.started = 0.0

  def open(self) -> None:
    try:
      self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
      logs = sorted(self.directory.glob("session-*.jsonl"), key=identity_store.session_log_order)
      for old in logs[:max(0, len(logs) - MAX_LOG_FILES + 1)]:
        old.unlink(missing_ok=True)
      system_slice.prune_sidecars(self.directory)
      number = max([0, *(identity_store.session_log_order(log)[0] for log in logs)]) + 1
      path = self.directory / f"session-{number:06d}-{identity_store.timestamp()}.jsonl"
      fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
      handle = os.fdopen(fd, "a")
    except OSError:
      return
    with self.lock:
      self.path, self.started = path, datetime.now(UTC).timestamp()
      self._queue = queue.Queue(maxsize=LOG_QUEUE_MAX)
      self._dropped = 0
      self._writer_stop = threading.Event()
      self._writer = threading.Thread(target=self._write, args=(handle, self._queue, self._writer_stop),
                                      name="aa_session_log", daemon=True)
      self._writer.start()

  @staticmethod
  def _write(handle, records: queue.Queue, stopped: threading.Event) -> None:
    with handle:
      while not stopped.is_set() or not records.empty():
        try:
          line = records.get(timeout=0.1)
        except queue.Empty:
          continue
        try:
          handle.write(line)
          if records.empty():
            handle.flush()
        except OSError:
          pass

  def close(self) -> None:
    """Write what is queued, then stop the writer (bounded: a stuck disk is left to the daemon thread)."""
    with self.lock:
      writer, stopped = self._writer, self._writer_stop
      self._writer = self._writer_stop = None
    if writer is None:
      return
    assert stopped is not None
    stopped.set()
    writer.join(timeout=2.0)

  def capture_system(self) -> None:
    """Save what the device was doing during this session beside its log, off the session's thread."""
    if self.path is not None:
      system_slice.write_sidecar_async(self.path, self.started, datetime.now(UTC).timestamp())

  def _line(self, record: dict) -> str:
    return json.dumps(record, default=str) + "\n"

  def __call__(self, name: str, **values) -> None:
    record = {"t": datetime.now(UTC).isoformat(timespec="milliseconds"), "event": name, "mono_s": time.monotonic(), **values}
    with self.lock:
      self.recent.append(record)
      if self._writer is None:
        return
      try:
        if self._dropped:
          self._queue.put_nowait(self._line({"t": record["t"], "event": "log_dropped", "count": self._dropped}))
          self._dropped = 0
        self._queue.put_nowait(self._line(record))
      except queue.Full:
        self._dropped += 1


class Supervisor:
  _pairing_note = ""      # why a paired car was not chosen, kept after the pairing window closes
  _no_starport_since = 0.0
  def __init__(self, bluez_factory=None, lease_factory=None, bluetooth_client=None, frame_path: str | None = None,
               synthetic: bool = False, car_frame_path: str = CAR_FRAME_PATH, touch_path: str = DEFAULT_TOUCH_SOCKET,
               control_path: str = DEFAULT_CONTROL_SOCKET, renderer_command: list[str] | None = None, onroad=_is_onroad,
               shared_bluetooth_owner=None, source_session: tuple | None = None,
               projection_enabled: Callable[[], bool] = lambda: True):
    self._synthetic = synthetic
    self._onroad = onroad
    self._car_frame_path, self._touch_path, self._control_path = car_frame_path, touch_path, control_path
    self._renderer_command = renderer_command
    self._bluez_factory = bluez_factory
    self._shared_bluetooth_owner = shared_bluetooth_owner
    self._source_session = source_session
    self._projection_enabled = projection_enabled
    self._lease_factory = lease_factory
    self._dedicated_strikes: dict[str, int] = {}
    self._wifi_recover_at = 0.0
    self._bt_client = bluetooth_client
    self._frame_path = frame_path
    self._lock = threading.RLock()
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None
    self._generation = 0
    self._sockets: set[socket.socket] = set()
    self._bluez: Any = None
    self._pairing_until = 0.0
    self._pairing_known: set[str] | None = None  # Android Auto cars already paired when the pairing window opened
    self._pairing_attempt: PairingAttempt | None = None
    self._pairing_seen: tuple = ()
    self._car_seen_at = -CAR_LINK_HOLD
    self._hfp_link = threading.Event()  # set when the chosen car opens the hands-free link
    self._modern_reply: dict[str, bool] = {}  # car address -> answer its v4+ version request like a current phone
    self.auto = AutoConnectPolicy()
    self._companion_retry_at = 0.0
    self.log = EventLog()
    self.config = identity_store.load_config()
    self._status: dict = {"state": "idle", "detail": "", "error": "", "last_stage": "", "attempt": 0,
                          "retry_in": 0.0, "mode": None, "stats": {}, "head_unit": {}, "identity": "", "running": False,
                          "view": "", "encoder": "", "target_fps": 0}

  # ------------------------------------------------------------------ status

  def _set(self, **values) -> None:
    with self._lock:
      self._status.update(values)

  def _stage(self, state: str, detail: str = "") -> None:
    self._check_cancel()
    self._set(state=state, detail=detail, last_stage=state)
    self.log("stage", state=state, detail=detail)

  def _check_cancel(self) -> None:
    if self._stop.is_set():
      raise Cancelled()

  def status(self) -> dict:
    with self._lock:
      status = dict(self._status)
    status["label"] = STATE_LABELS.get(status["state"], status["state"])
    status["receiver_address"] = self.config["receiver_address"]
    status["receiver_name"] = self.config["receiver_name"]
    status["companion_address"] = self.config.get("companion_address", "")
    status["companion_name"] = self.config.get("companion_name", "")
    status["pairing_note"] = self._pairing_note
    status["car_type"] = self.car_type()
    status.update(car_profiles.summary(self.config))
    status["adapter_choice_needed"] = self._adapter_choice_needed()
    status["configured_view"] = self.config["view"]
    status["connection"] = self.config["connection"]
    status["wired_blocked"] = chestnut_blocker()
    status["wired_busy"] = jetlink_blocker()
    status["pairing_ready"] = self._pairing_active()
    status["auto_connect"] = bool(self.config["auto_connect"])
    status["dedicated_wifi_interface"] = bool(self.config["dedicated_wifi_interface"])
    status["auto_paused"] = self.auto.suppressed
    status["recent"] = list(self.log.recent)[-8:]
    return status

  # ---------------------------------------------------------------- commands

  def user_start(self) -> None:
    self.auto.manual_start()
    self.start()

  def user_stop(self) -> None:
    """Stop pressed: also holds auto-connect off until the next drive."""
    self.auto.manual_stop(self._session_alive())
    self.stop()

  def set_auto_connect(self, enabled: bool) -> None:
    with self._lock:
      self.config["auto_connect"] = bool(enabled)
      identity_store.save_config(self.config)
    self.log("auto_connect_selected", enabled=bool(enabled))

  def set_dedicated_wifi(self, enabled: bool) -> None:
    """Join the car on aa0 so wlan0 keeps its network; used from the next session."""
    with self._lock:
      self.config["dedicated_wifi_interface"] = bool(enabled)
      identity_store.save_config(self.config)
    self.log("dedicated_wifi_selected", enabled=bool(enabled))

  def _adapter_choice_needed(self) -> bool:
    return self.car_type() == "adapter" and not self.config.get("car_profile_override")

  def start(self, trigger: str = "manual") -> None:
    with self._lock:
      if not self._projection_enabled():
        raise RuntimeError("Android Auto is disabled")
      if self._adapter_choice_needed():
        raise RuntimeError("Choose your adapter model (AAWireless or Other) in Galaxy first")
      if self.config["connection"] == "wired" and (reason := wired_blocker()):
        raise RuntimeError(reason)
      if self._thread is not None and self._thread.is_alive():
        if self._stop.is_set():
          raise RuntimeError("Android Auto is still stopping; try again in a moment")
        return
      if self.config["connection"] == "wireless" and not self.config["receiver_address"]:
        raise RuntimeError("Choose your car first")
      identity = identity_store.load_identity()  # fail fast with a clear message
      self._stop.clear()
      self._generation += 1
      self._set(state="connecting_bluetooth", detail="", error="", attempt=0, retry_in=0.0, running=True,
                identity=identity_store.expiry_warning(identity))
      self._thread = threading.Thread(target=self._run, args=(self._generation, trigger), name="starport_session", daemon=True)
      self._thread.start()

  def stop(self, timeout: float = 8.0, graceful: float = 3.0) -> None:
    """Cancel the session: let a streaming session say goodbye to the car, then force it."""
    thread = self._thread
    self._stop.set()
    if thread is not None:
      thread.join(timeout=graceful)
    self._close_sockets()  # unblocks any stage still waiting on Bluetooth or the network
    if thread is not None:
      thread.join(timeout=max(0.0, timeout - graceful))
    with self._lock:
      if thread is not None and thread.is_alive():
        return  # still cleaning up; start() refuses until it finishes
      self._thread = None
      if self._status["state"] != "error":
        self._set(state="idle", detail="")
      self._set(running=False, retry_in=0.0)

  def select_receiver(self, address: str, name: str = "") -> None:
    from openpilot.starpilot.system.starport.bt_sockets import normalize_address
    address = normalize_address(address)
    device = self._phone().device(address)
    if device is None or not device['paired'] or not device.get('starport'):
      raise RuntimeError('Choose a paired Android Auto receiver')
    with self._lock:
      if self._thread is not None and self._thread.is_alive():
        raise RuntimeError("Stop Android Auto before changing the car")
      if self._bluez is not None and self.config['receiver_address'] and self.config['receiver_address'] != address:
        self._bluez.release()
      if self.config["receiver_address"] != address:
        self.config.update(companion_address="", companion_name="", car_fingerprint={})
      self.config.update(receiver_address=address, receiver_name=name or address)
      identity_store.save_config(self.config)
    self._unselect_car_audio(address)
    try:
      self._phone().acquire()
      self._phone().set_trusted(address)  # the car's own connections are accepted onroad, like a phone's
    except Exception as error:
      self.log("trust_failed", error=str(error))

  def forget_receiver(self, address: str) -> bool:
    """A Bluetooth pairing was deleted: drop it as the chosen car or companion, and its RFCOMM channel,
    so it leaves the car pickers and auto-connect stops paging it. True if it was the chosen car."""
    from openpilot.starpilot.system.starport.bt_sockets import normalize_address
    address = normalize_address(address)
    chosen = self.config["receiver_address"].upper() == address
    if chosen and self._session_alive():
      self.stop()
    with self._lock:
      companion = self.config.get("companion_address", "").upper() == address
      cache, replies = dict(self.config["rfcomm_cache"]), dict(self.config.get("modern_reply_cache", {}))
      cached = cache.pop(address, None) is not None
      cached = replies.pop(address, None) is not None or cached
      if not (chosen or companion or cached):
        return False  # nothing of this device was kept
      self.config.update(rfcomm_cache=cache, modern_reply_cache=replies)
      self._modern_reply.pop(address, None)
      if chosen:
        if self._bluez is not None:
          self._bluez.release()
        self.config.update(receiver_address="", receiver_name="", companion_address="", companion_name="", car_fingerprint={})
      elif companion:
        self.config.update(companion_address="", companion_name="")
      identity_store.save_config(self.config)
    self.log("car_forgotten", address=address, receiver=chosen, companion=companion)
    return chosen

  def set_view(self, view: str) -> None:
    """Choose what the car shows; applies from the next projection session."""
    if view not in ("car", "mirror"):
      raise RuntimeError(f"Unknown view {view!r}")
    with self._lock:
      self.config["view"] = view
      identity_store.save_config(self.config)
    self.log("view_selected", view=view)

  def set_connection(self, connection: str) -> None:
    """Choose wireless (Bluetooth + car Wi-Fi), wired (USB), or HeadunitPad or Open Headunit (on the Galaxy hotspot) projection."""
    if connection not in identity_store.CONNECTIONS:
      raise RuntimeError(f"Unknown connection {connection!r}")
    if connection == "wired" and (reason := chestnut_blocker()):
      raise RuntimeError(reason)
    with self._lock:
      if self._thread is not None and self._thread.is_alive():
        raise RuntimeError("Stop Android Auto before changing the connection")
      self.config["connection"] = connection
      identity_store.save_config(self.config)
    self.log("connection_selected", connection=connection)

  def car_type(self) -> str:
    """What is in the car, for the setup page: wired and tablet follow the connection, wireless is built-in or an adapter."""
    connection = self.config["connection"]
    if connection == "wired":
      return "usb"
    if connection in ("headunitpad", "openheadunit"):
      return "app"
    return self.config["car_type"] or ("adapter" if self.config.get("companion_address") else "builtin")

  def set_car_type(self, car_type: str) -> None:
    if car_type not in ("builtin", "adapter", "usb", "app"):
      raise RuntimeError(f"Unknown car type {car_type!r}")
    previous_wireless_type = self.config["car_type"] or ("adapter" if self.config.get("companion_address") else "builtin")
    connection = {"usb": "wired", "app": "headunitpad" if self.config["connection"] not in ("headunitpad", "openheadunit")
                  else self.config["connection"]}.get(car_type, "wireless")
    if connection != self.config["connection"]:
      self.set_connection(connection)
    if car_type in ("builtin", "adapter"):
      with self._lock:
        self.config["car_type"] = car_type
        if previous_wireless_type != car_type:
          if self._bluez is not None and self.config["receiver_address"]:
            self._bluez.release()
          self.config.update(receiver_address="", receiver_name="", companion_address="", companion_name="", car_fingerprint={})
        if car_type != "adapter":
          self.config["car_profile_override"] = ""  # the adapter model only applies to a wireless adapter
        identity_store.save_config(self.config)
    self.log("car_type_selected", car_type=car_type)

  def set_car_profile(self, profile: str) -> None:
    """Pin a car profile ("default" for standard behavior) or go back to picking it by fingerprint ("")."""
    if not car_profiles.valid_override(profile):
      raise RuntimeError(f"Unknown car profile {profile!r}")
    with self._lock:
      self.config["car_profile_override"] = profile
      identity_store.save_config(self.config)
    self.log("car_profile_selected", profile=profile or "auto")

  def _remember_fingerprint(self, head_unit: dict) -> None:
    fingerprint = car_profiles.fingerprint_from(head_unit, self.config["receiver_address"])
    if fingerprint and fingerprint != self.config.get("car_fingerprint"):
      with self._lock:
        self.config["car_fingerprint"] = fingerprint
        identity_store.save_config(self.config)

  def prepare_pairing(self, seconds: float = 180.0) -> None:
    """Present as a phone (HFP gateway, smartphone class) while the car pairs."""
    if self._adapter_choice_needed():
      raise RuntimeError("Choose your adapter model (AAWireless or Other) in Galaxy first")
    if self._bluez_factory is None and (self._source_session is None or self._shared_bluetooth_owner is None or
                                        not self._shared_bluetooth_owner.session_valid(self._source_session)):
      raise RuntimeError('Current Galaxy pairing authorization required')
    if self._session_alive():
      raise RuntimeError('Stop projection before pairing another receiver')
    bluez = self._phone()
    known = {device["address"] for device in bluez.devices() if device["paired"] and device.get("starport")}
    if self._bluez_factory is None:
      bluez.release()
      bluez.acquire_pairing()
    elif self.config.get('phone_class', True):
      bluez.acquire()
    self._end_pairing("reopened")
    with self._lock:
      self._pairing_known = known
      self._pairing_until = time.monotonic() + seconds
      self._pairing_attempt, self._pairing_seen = PairingAttempt(seconds), ()
      self._pairing_note, self._no_starport_since = "", 0.0
    self.log("pairing_window", seconds=seconds)

  def pair_device(self, address: str) -> None:
    if self._source_session is None or self._shared_bluetooth_owner is None or not self._pairing_active():
      raise RuntimeError('Current Galaxy pairing authorization required')
    self._pairing_step("pair_requested")
    self._shared_bluetooth_owner.pair_device(self._source_session, address)

  def pairing_status(self) -> dict:
    if self._source_session is None or self._shared_bluetooth_owner is None:
      return {'active': False, 'receiver': None, 'prompt': None, 'approved': False}
    status = self._shared_bluetooth_owner.incoming_status(self._source_session)
    seen = (status.get('state'), bool(status.get('prompt')), bool(status.get('approved')), status.get('error') or '')
    if seen != getattr(self, '_pairing_seen', ()):
      self._pairing_seen = seen
      self._pairing_step("status", state=seen[0], prompt=seen[1], approved=seen[2], **({'error': seen[3]} if seen[3] else {}))
    return status

  def _pairing_step(self, name: str, **values) -> None:
    attempt = getattr(self, '_pairing_attempt', None)
    if attempt is not None:
      attempt.step(name, **values)

  def _end_pairing(self, result: str, **values) -> None:
    with self._lock:
      attempt, self._pairing_attempt = getattr(self, '_pairing_attempt', None), None
    if attempt is not None:
      if result in ("interrupted", "cancelled", "expired") and attempt.reached_connection():
        values["ended"], result = result, "paired_not_selected"
      attempt.finish(result, **values)

  def pairing_response(self, prompt_id: str, accepted: bool, value: str = '') -> None:
    if not isinstance(prompt_id, str) or len(prompt_id) != 32 or type(accepted) is not bool or not isinstance(value, str):
      raise ValueError('Invalid pairing response')
    if self._source_session is None or self._shared_bluetooth_owner is None or not self._pairing_active() or \
       not self._shared_bluetooth_owner.incoming_response(self._source_session, prompt_id, accepted, value):
      raise RuntimeError('Pairing prompt expired or changed')
    self._pairing_step("prompt_answered", accepted=accepted)

  def cancel_pairing(self) -> None:
    self._end_pairing("cancelled")
    with self._lock:
      self._pairing_until, self._pairing_known = 0.0, None
      phone = self._bluez if not self._session_alive() else None
      if phone is not None:
        self._bluez = None
    if phone is not None:
      phone.close()

  def bind_source_session(self, session: tuple) -> None:
    """Bind a Galaxy-minted, externally revalidated pairing source."""
    if self._shared_bluetooth_owner is None or not self._shared_bluetooth_owner.session_valid(session):
      raise RuntimeError('Galaxy Android Auto pairing source is unavailable')
    with self._lock:
      if self._source_session == session:
        return
      old_phone = self._bluez if not self._session_alive() else None
      if old_phone is not None:
        self._bluez = None
      elif self._bluez is not None and hasattr(self._bluez, 'session'):
        self._bluez.session = session
      self._source_session = session
      if old_phone is not None:
        self._pairing_until, self._pairing_known = 0.0, None
    if old_phone is not None:
      old_phone.close()

  def devices(self) -> list[dict]:
    try:
      devices = self._phone().devices()
    except Exception as error:
      raise RuntimeError(f"Bluetooth unavailable: {error}") from error
    return [{key: device[key] for key in ("address", "name", "paired", "connected", "starport")}
            for device in devices if device["paired"]]

  def maintain(self, now: float | None = None) -> None:
    """Periodic housekeeping from the daemon thread: pairing window, auto-connect."""
    now = time.monotonic() if now is None else now
    self.recover_wifi(now)
    owner = self._shared_bluetooth_owner
    if not self._projection_enabled():
      self._set(error='Android Auto was disabled')
      self.stop(timeout=0.0, graceful=0.0)
      if self._bluez is not None:
        self._bluez.close()
        self._bluez = None
      return
    if owner is not None:
      try:
        owner.maintain_phone_role()
      except Exception:
        self._set(error='Bluetooth receiver identity is unavailable')
        self.stop(timeout=0.0, graceful=0.0)
        if self._bluez is not None:
          self._bluez.close()
          self._bluez = None
        return
      lease = getattr(self._bluez, 'lease', None)
      if lease is not None and lease.released:
        if lease.session is None:
          self._set(error='Selected Bluetooth receiver changed or was unpaired')
          self.stop(timeout=0.0, graceful=0.0)
        else:
          self._pairing_until, self._pairing_known = 0.0, None
          self._end_pairing("interrupted", reason="Bluetooth lease released")
        self._bluez.close()
        self._bluez = None
        return
      if self._source_session is not None and not owner.session_valid(self._source_session):
        with self._lock:
          old_phone = self._bluez if self._bluez is not None and \
                      getattr(getattr(self._bluez, 'lease', None), 'session', None) is not None else None
          if old_phone is not None:
            self._bluez = None
          self._source_session = None
          self._pairing_until, self._pairing_known = 0.0, None
        self._end_pairing("interrupted", reason="Galaxy session ended")
        if old_phone is not None:
          old_phone.close()
    if self._pairing_until:
      if self._pairing_active():
        self._select_new_car()
      else:
        with self._lock:
          self._pairing_until, self._pairing_known = 0.0, None
        self._end_pairing("expired")
        if not self._session_alive():
          self._release_phone()
    self._auto_connect(now)

  def _auto_connect(self, now: float) -> None:
    config = self.config
    if self._pairing_active():
      return
    address = config["receiver_address"]
    if config["connection"] == "wired" and self._projection_enabled() and config["auto_connect"]:
      if wired_blocker():
        return
      if self._bluez is not None and not self._session_alive():
        self._release_phone()  # wired needs no Bluetooth
      # The drive is the only sign of the car: the session then waits for its USB handshake,
      # whether the car was on before the comma booted or is switched on after.
      onroad = self._onroad()
      action = self.auto.decide(now, enabled=True, onroad=onroad, car_link=False, ready=True, running=self._session_alive())
      self._apply_auto_action(now, action, onroad)
      return
    if not (self._projection_enabled() and config["auto_connect"] and config["connection"] == "wireless" and address):
      if self._bluez is not None and not self._session_alive() and not self._pairing_active():
        self._release_phone()  # drop a standby gateway left from before auto-connect was turned off
      return
    running = self._session_alive()
    try:
      bluez = self._phone()
      adapter_ready, device = bluez.snapshot(address)
      if adapter_ready and device and device['paired']:
        if self._bluez_factory is None:
          bluez.acquire()
        elif config.get('phone_class', True):
          bluez.register_hfp()
    except Exception:
      adapter_ready, device = False, None  # Bluetooth still starting (or restarting)
    car_device = device
    companion = config.get('companion_address', '')
    if companion and adapter_ready and device and device['paired'] and self._shared_bluetooth_owner is not None:
      try:
        car_device = self._shared_bluetooth_owner.prepare_companion(companion, lambda: self.config.get('companion_address', ''))
        if not running and not car_device['connected'] and now >= self._companion_retry_at:
          self._companion_retry_at = now + 15.0
          car_device = self._shared_bluetooth_owner.prepare_companion(
            companion, lambda: self.config.get('companion_address', ''), connect=True)
      except Exception as error:
        car_device = None
        self._set(error=f'Waiting for selected car: {error}'[:300])
    car_link = bool(car_device and car_device["connected"]) or now - self._car_seen_at < CAR_LINK_HOLD
    onroad = self._onroad()
    action = self.auto.decide(now, enabled=True, onroad=onroad, car_link=car_link,
                              ready=adapter_ready and bool(device and device["paired"]) and (not companion or car_link), running=running)
    self._apply_auto_action(now, action, onroad)

  def _apply_auto_action(self, now: float, action: str | None, onroad: bool) -> None:
    if action == "start":
      trigger = "onroad" if onroad else "car_connected"
      try:
        self.start(trigger=trigger)
      except Exception as error:
        self.auto.start_refused(now)
        self._set(error=f"auto-connect: {error}"[:300])
        self.log("auto_connect_refused", error=str(error))
    elif action == "stop":
      self.log("auto_connect_stop", reason="car gone")
      self.stop()

  def _select_new_car(self) -> None:
    """A car that paired during the pairing window and offers Android Auto becomes the chosen car."""
    if self._session_alive():
      return
    try:
      devices = self._phone().devices()
    except Exception:
      return
    approved = self.pairing_status()
    receiver = approved['receiver'] if approved['active'] and approved['approved'] else None
    with self._lock:
      known = self._pairing_known
      if known is None:
        return
      new = [device for device in devices if receiver is not None and device['address'] == receiver['address'] and
             device["paired"] and device.get("starport") and (device["address"] not in known or self._shared_bluetooth_owner is not None and
              self._shared_bluetooth_owner.phone_lease is not None and
              self._shared_bluetooth_owner.phone_lease.receiver == device["address"])]
      if not new:
        self._note_missing_starport(devices, receiver)
        return
    with self._lock:
      attempt, self._pairing_attempt = getattr(self, '_pairing_attempt', None), None
    try:
      self.cancel_pairing()
      self.select_receiver(new[0]["address"], new[0]["name"])
      self.log("car_selected_after_pairing", car=new[0]["name"])
      if attempt is not None:
        attempt.finish("paired")
    except (RuntimeError, ValueError) as error:
      self.log("car_select_failed", error=str(error))
      if attempt is not None:
        attempt.finish("select_failed", error=str(error))

  def _note_missing_starport(self, devices: list, receiver: dict | None) -> None:
    """An approved, paired device that still does not offer wireless Android Auto after a short wait is not a car we can use."""
    device = next((item for item in devices if receiver is not None and item["address"] == receiver["address"] and
                   item["paired"] and not item.get("starport")), None)
    if device is None:
      self._no_starport_since = 0.0
      return
    now = time.monotonic()
    if not self._no_starport_since:
      self._no_starport_since = now
    elif now - self._no_starport_since >= NO_STARPORT_WAIT and not self._pairing_note:
      name = device["name"] or "This device"
      hint = "Using a wireless adapter? Choose Wireless adapter, pair the adapter here, then connect your car on the Bluetooth page."
      self._pairing_note = f"{name} paired, but it doesn't offer wireless Android Auto. {hint}"
      self._pairing_step("no_starport")
      self.log("pairing_no_starport", address=device["address"])

  def close(self) -> None:
    self.stop()
    if self._bluez is not None:
      try:
        self._bluez.close()
      except Exception:
        pass
      self._bluez = None

  # ----------------------------------------------------------------- helpers

  def _phone(self):
    with self._lock:
      if self._bluez is None:
        if self._bluez_factory is not None:
          bluez = self._bluez_factory(self.log)  # test injection only
        elif self._shared_bluetooth_owner is not None:
          from openpilot.starpilot.system.starport.bluetooth_bridge import LeaseAwarePhone
          bluez = LeaseAwarePhone(self._shared_bluetooth_owner, self._source_session, self.log,
                                 enabled=self._projection_enabled,
                                 selected_receiver=lambda: self.config['receiver_address'])
        else:
          raise RuntimeError('Shared StarPilot Bluetooth owner is required for wireless Android Auto')
        bluez.accepts = self._hfp_accepts
        bluez.on_connection = self._hfp_connected
        self._bluez = bluez
      return self._bluez

  def _pairing_active(self) -> bool:
    return time.monotonic() < self._pairing_until

  def _session_alive(self) -> bool:
    thread = self._thread
    return thread is not None and thread.is_alive()

  def _hfp_accepts(self, address: str) -> bool:
    receiver = self.config["receiver_address"]
    if self._pairing_active() and self._source_session is not None and self._shared_bluetooth_owner is not None:
      lease = self._shared_bluetooth_owner.phone_lease
      return bool(lease is not None and not lease.released and
                  (lease.receiver == address.upper() or lease.incoming is not None and
                   lease.incoming.approved_address == address.upper()))
    return bool(receiver) and (address.upper() == receiver.upper() or
      self._shared_bluetooth_owner is not None and self._shared_bluetooth_owner.companion_accepts(address))

  def phone_window(self, operation: str, session: tuple, **arguments):
    owner = self._shared_bluetooth_owner
    return None if owner is None else owner.phone_window(operation, session, **arguments)

  def companion_power(self, enabled: bool, session: tuple):
    owner = self._shared_bluetooth_owner
    if owner is None:
      return None
    if not enabled and self._session_alive():
      from openpilot.starpilot.bluetooth.owner import BluetoothRejected
      raise BluetoothRejected('Stop Android Auto before turning Bluetooth off', code='projection_active')
    # Same lock order as select_receiver: supervisor first, then the owner releases its lease.
    with self._lock:
      return owner.companion_power(enabled, session)

  def companion_bluetooth_action(self, operation: str, address: str, session: tuple):
    owner = self._shared_bluetooth_owner
    if owner is None:
      return None
    if operation == 'forget' and self.config['receiver_address'].upper() == address.upper():
      return self._forget_selected_car(owner, address, session)
    # select_receiver takes the supervisor lock before releasing an owner lease.
    # Keep that same order through connection, save and admission pruning so an
    # old adapter's successful action cannot save its car under a new adapter.
    with self._lock, owner.lock:
      lease = owner.phone_lease
      receiver = self.config['receiver_address']
      result = owner.companion_action(operation, address, session)
      if result is None:
        return None
      if (lease is None or owner.phone_lease is not lease or lease.released or lease.session is not None or
          lease.receiver != receiver.upper() or self.config['receiver_address'] != receiver or
          not owner.session_valid(session) or lease.enabled is None or not lease.enabled() or
          lease.selected_receiver is None or lease.selected_receiver().upper() != lease.receiver):
        raise RuntimeError('Android Auto receiver or pairing authorization changed')
      if operation == 'connect':
        device = self._phone().device(address)
        if (device is None or not device['paired'] or not device['connected'] or
            owner.phone_lease is not lease or lease.released or
            not owner.session_valid(session) or not lease.enabled()):
          raise RuntimeError('Selected car connection was not confirmed')
        if '0000111e-0000-1000-8000-00805f9b34fb' not in device.get('uuids', []):
          return result  # An accessory must never replace the dongle's selected car.
        self.config.update(companion_address=device['address'], companion_name=device['name'])
        identity_store.save_config(self.config)
        for previous in list(owner.companion_devices):
          if previous != device['address']:
            owner.companion_devices.pop(previous, None)
      elif self.config.get('companion_address', '').upper() == address.upper():
        self.config.update(companion_address='', companion_name='')
        identity_store.save_config(self.config)
      return result

  def _forget_selected_car(self, owner, address: str, session: tuple):
    """Forget the chosen car even while auto-connect is retrying it: stop the attempt, then delete the pairing."""
    from openpilot.starpilot.bluetooth.owner import BluetoothRejected
    lease = owner.phone_lease
    if not owner.session_valid(session):
      raise BluetoothRejected('Galaxy session expired', code='session_expired')
    if lease is not None and not lease.released and lease.session is not None:
      raise BluetoothRejected('Finish Android Auto pairing first', code='busy')
    self.auto.manual_stop(self._session_alive())
    self.stop()
    if self._session_alive():
      raise BluetoothRejected('Android Auto is still stopping', code='busy')
    result = owner.request('forget', address=address)
    self.forget_receiver(address)
    return result

  def _hfp_connected(self, address: str) -> None:
    if address.upper() in (self.config["receiver_address"].upper(), self.config.get("companion_address", "").upper()):
      self._car_seen_at = time.monotonic()
      self._hfp_link.set()

  def _release_phone(self) -> None:
    """Stop looking like a phone; keep only the standby gateway auto-connect needs."""
    bluez = self._bluez
    if bluez is None:
      return
    config = self.config
    standby = config["auto_connect"] and config["connection"] == "wireless" and bool(config["receiver_address"]) \
              and self._projection_enabled()
    if self._pairing_active():
      return
    if self._bluez_factory is not None:
      if standby:
        bluez.restore_class()
      else:
        bluez.release()
      return
    if not standby:
      bluez.release()
    elif getattr(bluez, 'lease', None) is None or bluez.lease.released:
      bluez.acquire()
    else:
      bluez.restore_class()

  def _remember_reply(self, address: str, modern: bool) -> None:
    """Keep the version reply a v4+ head unit started with, so the next session leads with it."""
    with self._lock:
      self._modern_reply[address] = modern
      replies = dict(self.config.get("modern_reply_cache", {}))
      if modern == replies.get(address, False):
        return
      if modern:
        replies[address] = True
      else:
        replies.pop(address, None)
      self.config["modern_reply_cache"] = replies
      identity_store.save_config(self.config)
    self.log("bootstrap_reply_remembered", modern=modern)

  def _remember_channel(self, address: str, channel: int | None) -> None:
    with self._lock:
      cache = dict(self.config["rfcomm_cache"])
      if channel is None:
        if cache.pop(address, None) is None:
          return
      elif cache.get(address) == channel:
        return
      else:
        cache[address] = channel
      self.config["rfcomm_cache"] = cache
      identity_store.save_config(self.config)

  def _lease(self):
    factory = self._lease_factory
    if factory is None:
      from openpilot.starpilot.system.starport.network import NetworkLease
      factory = NetworkLease
    if self.config["dedicated_wifi_interface"]:
      from openpilot.starpilot.system.starport.car_interface import DedicatedLease
      strikes = self._dedicated_strikes.get(self.config["receiver_address"], 0)
      if strikes >= DEDICATED_STRIKES:
        self.log("wifi_dedicated_skipped", strikes=strikes)
      lease = DedicatedLease(self.log, self.config["wifi_interface"], factory, enabled=strikes < DEDICATED_STRIKES)
      lease.prepare()
      return lease
    return factory(self.log, self.config["wifi_interface"])

  def _count_dedicated(self, lease) -> None:
    """A car whose network keeps failing on aa0 but joining on wlan0 stays on wlan0 until the daemon restarts."""
    address = self.config["receiver_address"]
    if getattr(lease, "aa0_joined", False):
      self._dedicated_strikes.pop(address, None)
    elif getattr(lease, "aa0_failed", False):
      self._dedicated_strikes[address] = self._dedicated_strikes.get(address, 0) + 1

  def _unselect_car_audio(self, address: str) -> None:
    if self._bt_client is None:
      return
    try:
      if self._bt_client.status().selected_audio.upper() == address.upper():
        self._bt_client.select_audio("")
        self.log("car_audio_unselected")
    except Exception:
      pass

  def _track(self, sock: socket.socket) -> socket.socket:
    with self._lock:
      self._sockets.add(sock)
    if self._stop.is_set():
      self._close_sockets()
      raise Cancelled()
    return sock

  def _close_sockets(self) -> None:
    with self._lock:
      sockets, self._sockets = self._sockets, set()
    for sock in sockets:
      try:
        sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      try:
        sock.close()
      except OSError:
        pass

  def _wait(self, seconds: float) -> None:
    if self._stop.wait(seconds):
      raise Cancelled()

  def _car_link_fresh(self) -> bool:
    return time.monotonic() - self._car_seen_at < HFP_FRESH

  def _wait_backoff(self, delay: float, car_can_wake: bool) -> None:
    """Sleep out a retry delay, but retry at once when the car opens hands-free to us.

    That is the car reaching out (at startup, or when the driver taps Android Auto); it hangs
    up again within a second, so waiting out a 30 s backoff would miss it.
    """
    if not car_can_wake:
      self._wait(delay)
      return
    if not self._car_link_fresh():
      self._hfp_link.clear()
    deadline = time.monotonic() + delay
    while (remaining := deadline - time.monotonic()) > 0:
      if self._hfp_link.is_set():
        # One early retry per connection from the car: if that attempt also fails fast,
        # the next backoff must not end at once just because the link is still fresh.
        self._hfp_link.clear()
        self.log("retry_early", reason="car opened hands-free", skipped_s=round(remaining, 1))
        return
      self._wait(min(0.1, remaining))

  def _await_car_first(self) -> None:
    """At ignition the car pages the comma for hands-free on its own. Paging it at the same moment crosses the two
    connections, and the kernel can drop the one that won while the controller keeps it (see StrayLinkError)."""
    if self._car_link_fresh():
      return
    self._hfp_link.clear()
    deadline = time.monotonic() + CAR_FIRST_WAIT
    while (remaining := deadline - time.monotonic()) > 0:
      if self._hfp_link.is_set():
        self.log("car_first_wait", outcome="hfp", waited_s=round(CAR_FIRST_WAIT - remaining, 1))
        return
      self._wait(min(0.1, remaining))
    self.log("car_first_wait", outcome="timeout", waited_s=CAR_FIRST_WAIT)

  def _clear_stray_link(self) -> None:
    address = self.config["receiver_address"]
    try:
      bluez = self._phone()
      device = bluez.device(address)
      if device is not None and device["connected"]:
        self.log("stray_link_skipped", reason="BlueZ reports the car connected")
        return
      bluez.disconnect_stray_link()
    except Exception as error:
      self.log("stray_link_failed", error=str(error))

  # ---------------------------------------------------------------- session

  def _run(self, generation: int, trigger: str = "manual") -> None:
    self.log.open()
    connection = self.config["connection"]
    wireless = connection == "wireless"
    receiver = {"wired": "usb", "headunitpad": "headunitpad", "openheadunit": "openheadunit"}.get(connection, self.config["receiver_name"])
    from openpilot.starpilot.system.starport.bugsink_transport import device_metadata
    software = device_metadata()
    self.log("session_start", receiver=receiver, generation=generation, trigger=trigger,
             software={key: software.get(key, "unknown") for key in ("commit", "branch")},
             settings={key: self.config.get(key) for key in REPORTED_SETTINGS})
    lease = self._lease() if wireless else NoLease()
    attempt = 0
    car_first = trigger == "onroad" and wireless
    try:
      while not self._stop.is_set():
        started = time.monotonic()
        peer_stopped = False
        self.log("attempt_start")
        try:
          if car_first:
            car_first = False
            self._await_car_first()
          self._attempt(lease)
          self.log("session_ended")
        except Cancelled:
          break
        except PeerRequestedStop as error:
          self._set(error="", detail=str(error))
          self.log("session_ended", reason=str(error))
          peer_stopped = True
        except Exception as error:
          if self._stop.is_set():
            break  # I/O torn down by Stop; not a failure to report
          stage = error.stage if isinstance(error, BootstrapError) else self._status["last_stage"]
          message = self._describe_error(stage, error)
          self._set(error=message)
          self.log("attempt_failed", stage=stage, error=message, kind=type(error).__name__, where=_where(error))
          if isinstance(error, StrayLinkError):
            self._clear_stray_link()
            car_first = True  # the car pages right back once its link drops; let it, rather than crossing it again
        finally:
          self._close_sockets()
          try:
            lease.release(restore=False)
          except Exception as error:
            self.log("wifi_release_failed", error=str(error))
        if self._stop.is_set():
          break
        attempt = 0 if time.monotonic() - started > STABLE_SESSION_SECONDS else attempt + 1
        delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
        if peer_stopped:
          delay = max(delay, PEER_STOP_RETRY_SECONDS)  # the car ended projection itself; do not bounce straight back
        self._set(state="backoff", attempt=attempt, retry_in=delay, mode=None)
        try:
          self._wait_backoff(delay, car_can_wake=wireless and not peer_stopped)
        except Cancelled:
          break
    finally:
      self._set(state="stopping", detail="")
      try:
        lease.release(restore=True)
      except Exception as error:
        self.log("wifi_release_failed", error=str(error))
      self._count_dedicated(lease)
      self._wifi_recover_at = 0.0  # if the restore above failed, housekeeping retries it
      try:
        self._release_phone()
      except Exception as error:
        self.log("bluetooth_release_failed", error=str(error))
      self.log("session_stop")
      self.log.close()
      self.log.capture_system()
      self._set(state="idle", detail="", running=False, retry_in=0.0, mode=None)

  @staticmethod
  def _describe_error(stage: str, error: Exception) -> str:
    if isinstance(error, AuthenticationRejected):
      return "The car rejected the Android Auto identity; it may have expired"
    text = str(error) or type(error).__name__
    if stage == "authenticating" and "certificate" in text.lower():
      text += " (set verify_head_unit false in config.json to test without verifying the car)"
    return f"{STATE_LABELS.get(stage, stage)}: {text}"[:300]

  def _attempt(self, lease) -> None:
    if self.config["connection"] == "wired":
      self._attempt_usb()
      return
    if self.config["connection"] in ("headunitpad", "openheadunit"):
      self._attempt_headunitpad()
      return
    config = self.config
    address = config["receiver_address"]
    ident = identity_store.load_identity()

    self._stage("connecting_bluetooth", config["receiver_name"])
    bluez = self._phone()
    bluez.acquire()
    device = bluez.device(address)
    if device is None or not device["paired"]:
      raise RuntimeError("The car is not paired with this comma; pair it in Bluetooth settings")
    companion = config.get('companion_address', '')
    if companion:
      if self._shared_bluetooth_owner is None:
        raise RuntimeError('Shared Bluetooth owner required for the selected car')
      self._shared_bluetooth_owner.prepare_companion(companion, lambda: self.config.get('companion_address', ''), connect=True)
      self._check_cancel()
      self.log('companion_car_connected', address=companion)
    elif device.get('starport') and '0000111e-0000-1000-8000-00805f9b34fb' not in device.get('uuids', []):
      raise RuntimeError('Connect your paired car in Galaxy Bluetooth first to select it for this Android Auto adapter')
    if delay := car_profiles.tuning(config)["connect_delay_s"]:
      self.log("car_profile_connect_delay", seconds=delay)
      self._wait(delay)
    bluez.connect_device(address)
    self._wait(1.0)

    from openpilot.starpilot.system.starport import bt_sockets
    channel = int(config.get("rfcomm_channel") or 0)
    source = "config"
    if not channel:
      channel, source = config["rfcomm_cache"].get(address, 0), "cache"
    self._stage("discovering")
    if not channel:
      channel, source = self._discover_channel(address), "sdp"
    self.log("rfcomm_channel", channel=channel, source=source)

    boot = None
    try:
      self._stage("rfcomm", f"channel {channel}")
      rfcomm = self._track(bt_sockets.connect_rfcomm(address, channel))
      boot = WirelessBootstrap(rfcomm, self._bootstrap_log, device_serial=config["device_name"],
                               version_status=int(config.get("version_status", 0)),
                               experiment="all" if car_profiles.tuning(config)["bootstrap_experiments"] else "",
                               modern_reply=self._modern_reply.get(address, config.get("modern_reply_cache", {}).get(address, False)))
      self._set(state="wifi_start")
      result = boot.run(lambda credentials: lease.acquire(credentials, cancelled=boot.join_is_cancelled), cancelled=self._stop.is_set)
    except Exception as error:
      if _stray_link(error):
        raise StrayLinkError(f"Bluetooth link to the car is stuck: {error}") from error  # says nothing about the cached channel
      if boot is not None and boot.silent_after_modern_version(error):
        self._modern_reply[address] = not boot.modern_reply  # try the other version reply next
        self.log("bootstrap_reply_switched", modern=self._modern_reply[address])
      if source == "cache" and not self._stop.is_set() and self._status["last_stage"] in ("rfcomm", "wifi_start") and car_never_answered(error):
        self._remember_channel(address, None)  # the car never answered there; ask it over SDP next time
        self.log("rfcomm_cache_dropped", channel=channel)
      raise
    if source == "sdp":
      self._remember_channel(address, channel)
    if boot.version_major >= MODERN_MAJOR:
      self._remember_reply(address, boot.modern_reply)
    self._set(head_unit=result.head_unit)
    self._remember_fingerprint(result.head_unit)
    bluez.restore_class()  # the car has accepted the comma; stop looking like a phone to everything else
    keepalive_stop = threading.Event()
    threading.Thread(target=boot.keepalive, args=(keepalive_stop,), name="aa_rfcomm_keepalive", daemon=True).start()
    try:
      self._project(result, lease, ident)
    finally:
      keepalive_stop.set()

  def _discover_channel(self, address: str) -> int:
    from openpilot.starpilot.system.starport import bt_sockets, sdp
    last_error: Exception | None = None
    for settle in SDP_SETTLE:
      self._check_cancel()
      try:
        with self._track(bt_sockets.connect_l2cap(address, sdp.SDP_PSM)) as sdp_sock:
          self._wait(settle)
          return sdp.query_channel(sdp_sock)
      except (OSError, sdp.SdpError) as error:
        if _stray_link(error):
          raise StrayLinkError(f"Bluetooth link to the car is stuck: {error}") from error  # retrying cannot clear it
        last_error = error
        self.log("sdp_retry", error=str(error), settle=settle)
        self._wait(0.35)
    raise RuntimeError(f"Could not find the car's Android Auto service: {last_error}")

  def _attempt_usb(self) -> None:
    """Wired: wait for the car's accessory handshake on USB (or skip it), then project over the cable."""
    if reason := wired_blocker():
      raise RuntimeError(reason)
    from openpilot.starpilot.system.starport import usb_accessory as usb
    ident = identity_store.load_identity()
    mode = self.config["usb_mode"]
    gadget = usb.AccessoryGadget(self.log)
    listener = usb.UeventListener()
    bridge = None
    failed = False
    try:
      direct = mode == "direct"
      gadget.prepare(direct=direct)
      self._stage("waiting_for_usb")
      if direct:
        self._await_usb_configured(listener, None, gadget.connection_state)
      else:
        direct = self._await_accessory_start(listener, fallback=mode == "auto", state_reader=gadget.connection_state)
        gadget.switch_to_accessory()
        if not self._await_usb_configured(listener, USB_CONFIGURE_WAIT, gadget.connection_state):
          raise RuntimeError("The car did not finish USB setup. Use its Android Auto data port and a data-capable cable")
      method = "direct" if direct else "handshake"
      self._stage("usb_accessory", method)
      bridge = usb.AccessoryBridge(log=self.log)
      self.log("usb_accessory_ready", method=method, strings=getattr(bridge, "strings", {}))
      self._project(None, UsbLease(bridge), ident, connect=lambda: self._track(bridge.socket))
    except BaseException:
      failed = True
      raise
    finally:
      self._release_usb(gadget, bridge, listener, failed)

  def _attempt_headunitpad(self) -> None:
    """HeadunitPad or Open Headunit: the tablet, on the Galaxy hotspot or the same LAN, finds this port and connects like a head unit."""
    ident = identity_store.load_identity()
    headunitpad = self.config["connection"] == "headunitpad"
    port = int(self.config["headunitpad_port"])
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self._track(listener)
    try:
      listener.bind(("", port))
    except OSError as error:
      raise RuntimeError(f"Cannot listen on port {port} ({error})") from error
    listener.listen(1)
    listener.settimeout(1.0)
    self._stage("waiting_for_ipad" if headunitpad else "waiting_for_tablet", f"port {port}")
    sock = self._accept_headunitpad(listener)
    listener.close()
    self._project(None, NoLease(), ident, connect=lambda: sock, fragment_acks=headunitpad)

  def _accept_headunitpad(self, listener: socket.socket) -> socket.socket:
    while True:
      self._check_cancel()
      try:
        sock, (address, _) = listener.accept()
      except TimeoutError:
        continue
      if not ipaddress.ip_address(address).is_private:
        self.log("headunitpad_rejected", address=address)
        sock.close()
        continue
      sock.settimeout(None)
      sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
      self.log("headunitpad_connected", address=address)
      return self._track(sock)

  def _release_usb(self, gadget, bridge, listener, failed: bool) -> None:
    """Undo a wired attempt, running every step even when one fails.

    Closing an fd in another thread does not cancel a Linux blocking read, so the gadget
    detaches first and the bridge workers are joined before the fd can be reused. A cleanup
    error is logged, and raised only when the attempt itself ended cleanly: it must never
    replace the real reason (a car-requested stop keeps its longer retry delay).
    """
    errors = []
    steps = [("detach", gadget.detach), ("bridge", bridge.close if bridge is not None else None),
             ("listener", listener.close), ("restore", gadget.restore)]
    for name, step in steps:
      if step is None:
        continue
      try:
        step()
      except Exception as error:
        errors.append(error)
        self.log("usb_cleanup_failed", step=name, error=str(error))
    if errors and not failed:
      raise errors[0]

  def recover_wifi(self, now: float | None = None) -> None:
    """Turn wlan0 autoconnect back on if a session paused it and crashed. Retried while idle until it works."""
    now = time.monotonic() if now is None else now
    if now < self._wifi_recover_at or self._session_alive():
      return
    from openpilot.starpilot.system.starport.network import recover_autoconnect
    try:
      done = recover_autoconnect(self.log)
    except Exception as error:
      self.log("wifi_autoconnect_recover_failed", error=str(error))
      done = False
    self._wifi_recover_at = float("inf") if done else now + WIFI_RECOVER_RETRY_S

  def recover_usb(self) -> None:
    """At startup: undo a wired session a crash interrupted, so the car stops seeing a dead accessory
    and ADB comes back if it is on. Nothing happens unless our gadget was left holding the USB port."""
    try:
      from openpilot.starpilot.system.starport import usb_accessory as usb
      usb.AccessoryGadget(self.log).recover()
    except Exception as error:
      self.log("usb_recover_failed", error=str(error))

  def _usb_event(self, listener) -> dict | None:
    self._check_cancel()
    event = listener.next(0.5)
    if event is not None and "USB_STATE" in event:
      self.log("usb_state", state=event["USB_STATE"])
    return event

  def _await_accessory_start(self, listener, fallback: bool, state_reader=lambda: "") -> bool:
    """Wait for the car's AOA START; True when it never came and the comma should present as an accessory itself.

    The wait starts when the car first connects, not when it configures the comma: a head unit
    that reads the descriptors of a device it does not recognize may never configure it at all.
    """
    connected_at = None
    while True:
      event = self._usb_event(listener)
      if event is not None:
        if event.get("ACCESSORY") == "START":
          return False
        state = event.get("USB_STATE")
        if state in ("CONNECTED", "CONFIGURED"):
          connected_at = connected_at or time.monotonic()
        elif state == "DISCONNECTED":
          connected_at = None
      state = state_reader()
      if state in ("CONNECTED", "CONFIGURED") and connected_at is None:
        connected_at = time.monotonic()
      elif state == "DISCONNECTED":
        connected_at = None
      if fallback and connected_at is not None and time.monotonic() - connected_at >= USB_HANDSHAKE_WAIT:
        # Some head units only send the handshake to devices they recognize as phones.
        self.log("usb_no_accessory_start", waited=USB_HANDSHAKE_WAIT)
        return True

  def _await_usb_configured(self, listener, timeout: float | None, state_reader=lambda: "") -> bool:
    """Wait until the car has configured the accessory, so reads do not fail on a link that is not up yet."""
    started = time.monotonic()
    while timeout is None or time.monotonic() - started < timeout:
      self._check_cancel()
      if state_reader() == "CONFIGURED":
        return True  # kernels may coalesce or omit the android_usb state uevent
      event = self._usb_event(listener)
      if event is not None and event.get("USB_STATE") == "CONFIGURED" and state_reader() in ("", "CONFIGURED"):
        return True
    self.log("usb_configure_timeout", waited=timeout)
    return False

  def _bootstrap_log(self, name: str, **values) -> None:
    self.log(name, **values)
    if name == "bootstrap_tx" and values.get("message") == NAMES[2]:
      self._set(state="wifi_info", last_stage="wifi_info")
    elif name == "wifi_joining":
      self._set(state="joining_wifi", last_stage="joining_wifi", detail=str(values.get("ssid", "")))

  def _connect_tcp(self, result, lease) -> socket.socket:
    self._stage("connecting_tcp", f"{result.endpoint.ip}:{result.endpoint.port}")
    last_error: Exception | None = None
    for attempt in range(TCP_ATTEMPTS):
      self._check_cancel()
      try:
        sock = socket.create_connection((result.endpoint.ip, result.endpoint.port), timeout=5.0,
                                        source_address=(lease.local_ip, 0) if lease.local_ip else None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return self._track(sock)
      except OSError as error:
        last_error = error
        self.log("tcp_retry", attempt=attempt + 1, error=str(error))
        self._wait(1.0 + attempt * 0.5)
    raise RuntimeError(f"Car did not accept the projection connection: {last_error}")

  def _adapter_address(self) -> str:
    """This comma's Bluetooth address for the head unit's pairing request; empty when BlueZ is not up."""
    try:
      return str(self._bluez.adapter()[1].get("Address", "")).upper() if self._bluez is not None else ""
    except Exception:  # DBus errors vary; the pairing request is optional
      return ""

  def _project(self, result, lease, ident, connect=None, fragment_acks: bool = False) -> None:
    config = self.config
    sock = connect() if connect is not None else self._connect_tcp(result, lease)
    ca = ident.root if config.get("verify_head_unit", False) else None
    session = ProjectionSession(sock, ident.cert, ident.key, self.log, ca)
    session.fragment_acks = fragment_acks
    tuning = car_profiles.tuning(config)
    session.max_window = tuning["max_window"]
    session.phone_frames = tuning["phone_frames"]
    if tuning["phone_services"]:
      session.phone_services = True
      session.phone_address = self._adapter_address()
    self._stage("authenticating")
    session.authenticate()
    self._stage("negotiating")
    mode = session.start(config["device_name"], "comma.ai")
    self._set(mode=mode.as_dict(), error="")
    self.log("projection_ready", mode=mode.as_dict(), head_unit_subject=session.head_unit_subject)
    try:
      from openpilot.starpilot.system.starport.display_profile import record_screen
      record_screen(mode.as_dict())
    except (OSError, ValueError, TypeError, KeyError) as error:
      self.log("screen_profile_error", error=str(error))

    from openpilot.starpilot.system.starport.hw_encoder import create_encoder
    # ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: Encoder cadence and pipeline flags are tuned together for Android Auto video.
    # Altering this can degrade video smoothness or introduce lag on Android Auto.
    # Do not change it autonomously; require an explicit user request specifically for this video optimization.
    software_fps = min(SOFTWARE_FPS, config["fps"]) if config["fps"] else SOFTWARE_FPS
    encoder, fps = create_encoder(mode.width, mode.height, preference=encoder_preference(config["encoder"]),
                                  bitrate_kbps=config["bitrate_kbps"],
                                  margin_height=mode.margin_height if mode.content_top in (None, mode.margin_height // 2) else 0,
                                  software_fps=software_fps, log=self.log,
                                  rate_control=config["rate_control"])
    fps = min(fps, mode.fps, config["fps"] or fps)
    interval = 1.0 / fps
    flags = (FLAG_NV12 if config["gpu_nv12"] and getattr(encoder, "supports_nv12", False) else 0) | \
            (FLAG_ASYNC_READBACK if config["async_readback"] else 0)
    request = FrameRequest(mode.width, mode.height, mode.margin_width, mode.margin_height, int(interval * 1e6), flags,
                           mode.content_left, mode.content_top)
    self.log("frame_pipeline", nv12=bool(flags & FLAG_NV12), async_readback=bool(flags & FLAG_ASYNC_READBACK),
             target_fps=fps, encoder=getattr(encoder, "backend", "libx264"))
    view = config["view"]
    if view == "car" and not self._synthetic and self._renderer_command is None and not renderer_available():
      view = "unavailable"
    source = ViewSource(view, request, self.log, synthetic=self._synthetic,
                        mirror_path=self._frame_path or DEFAULT_FRAME_PATH, car_path=self._car_frame_path,
                        touch_path=self._touch_path, control_path=self._control_path, renderer_command=self._renderer_command,
                        renderer_log=identity_store.LOG_DIR / "car_ui.log")
    self._set(view=source.label, encoder=getattr(encoder, "backend", "libx264"), target_fps=fps)
    watch = LinkWatch(lease, self.log).start()
    try:
      from openpilot.starpilot.system.starport.media_audio import MediaAudio, music_control
      from openpilot.starpilot.system.starport.media_status import MediaStatus
      session.media_control = music_control
      session.audio = MediaAudio.create(session, self.log)
      if session.audio is not None:
        session.audio.track_info = MediaStatus.create(session, self.log)
    except Exception as error:  # music is optional; projection must never depend on it
      self.log("media_audio_unavailable", error=str(error))
    try:
      from openpilot.starpilot.system.starport.nav_status import NavStatus
      session.nav = NavStatus.create(session, self.log)
    except Exception as error:  # turn-by-turn is optional; projection must never depend on it
      self.log("nav_status_unavailable", error=str(error))
    try:
      self._stream(session, encoder, source, watch, interval)
    finally:
      if session.audio is not None:
        session.audio.close()
      if getattr(session, "nav", None) is not None:
        session.nav.close()
      watch.stop()
      source.close()
      encoder.close()
      if self._stop.is_set():
        try:
          session.shutdown()
        except Exception:
          pass

  # ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: The bounded ACK/input drain and conditional waits keep video and touches responsive.
  # Altering this can degrade video smoothness or introduce lag on Android Auto.
  # Do not change it autonomously; require an explicit user request specifically for this video optimization.
  def _stream(self, session: ProjectionSession, encoder, source: ViewSource, watch: LinkWatch, interval: float) -> None:
    started = time.monotonic()
    last_fresh = time.monotonic()
    last_unavailable = 0.0
    next_check = 0.0
    ages: deque[float] = deque(maxlen=120)
    sent_times: deque[float] = deque(maxlen=200)
    unavailable: dict[str, bytes] = {}
    recoveries: list[float] = []  # when the hardware encoder was reopened, this session
    encode_peak = 0.0
    wait_for_frame = False
    audio = getattr(session, "audio", None)
    nav = getattr(session, "nav", None)
    self._stage("streaming")
    while not self._stop.is_set():
      pass_started = time.monotonic()
      # Wait only when there was no frame or the receiver's window is full.
      # select wakes immediately for touches/ACKs; sleeping after every encode
      # used to add dead time even when the next frame was already available.
      timeout = min(interval / 4, 0.01) if wait_for_frame else 0.0
      handled = session.pump(timeout if session.can_send() else 0.05)
      # Bound the drain so a burst of input cannot starve video, but do not
      # make each queued touch or ACK wait for another encode/poll cycle.
      for _ in range(15):
        if not handled:
          break
        handled = session.pump(0.0)
      session.check_progress()
      if audio is not None:
        try:
          audio.service()  # non-blocking: drains the Spotify pipe and sends within the car's audio credit
        except (OSError, TimeoutError):
          raise  # the link itself failed; video would fail the same way
        except Exception as error:
          self.log("media_audio_failed", error=f"{type(error).__name__}: {error}")
          audio.close()
          audio = session.audio = None
      if nav is not None:
        try:
          nav.service()  # non-blocking: compares the route's next turn and sends at most a few small messages
        except (OSError, TimeoutError):
          raise
        except Exception as error:
          self.log("nav_status_failed", error=f"{type(error).__name__}: {error}")
          nav.close()
          nav = session.nav = None
      now = time.monotonic()
      wait_for_frame = True
      if session.focused:
        source.demand(1.0)
      else:
        source.release_demand()
      if session.touch_events:
        source.send_touches(list(session.touch_events))
        session.touch_events.clear()
      for action in source.drain_controls():
        if action == NATIVE_FOCUS:
          session.request_native()
      state = "streaming" if session.focused else "suspended"
      if self._status["state"] != state:
        self._set(state=state)
      if session.can_send():
        frame = source.latest()
        if frame is not None:
          age = now - frame.captured_ns / 1e9
          encode = encoder.encode_nv12 if frame.pixel_format == FORMAT_NV12 else encoder.encode_rgba
          encoded = self._encode(encoder, encode, frame.data, session.needs_keyframe, recoveries) if age <= FRAME_MAX_AGE else None
          if encoded is not None:
            data, keyframe = encoded
            encode_peak = max(encode_peak, encoder.last_encode_ms)
            session.send_frame(data, frame.captured_ns // 1000, keyframe=keyframe)
            if source.view == "car":
              source.source.mark_sent(frame.captured_ns)
            sent_at = time.monotonic()
            ages.append(sent_at - frame.captured_ns / 1e9)
            sent_times.append(sent_at)
            last_fresh = sent_at
            wait_for_frame = False
        elif now - last_fresh > UNAVAILABLE_AFTER and now - last_unavailable > 1.0:
          # The UI stopped producing frames (e.g. the offroad render budget ran
          # out). Say so on the car instead of freezing on an old image.
          text = "Starting StarPilot" if source.waiting_for_first_frame else "StarPilot display unavailable"
          if text not in unavailable:
            unavailable[text] = self._unavailable_frame(source.request, text)
          encoded = self._encode(encoder, encoder.encode_rgba, unavailable[text], True, recoveries)
          if encoded is not None:
            session.send_frame(encoded[0], time.monotonic_ns() // 1000, keyframe=encoded[1])
          last_unavailable = now
      now = time.monotonic()
      if now >= next_check:
        next_check = now + 1.0
        if watch.lost:
          raise RuntimeError(watch.lost)
        label = source.label
        source.check(now, focused=session.focused)
        if source.label != label:
          self._set(view=source.label)
        window = [t for t in sent_times if now - t <= 5.0]
        ordered = sorted(ages)
        audio_stats = {"media_audio": audio.status()} if audio is not None else {}
        stats = {**session.stats(), **audio_stats, "fps": round(len(window) / 5.0, 1), "encode_ms": round(encoder.last_encode_ms, 1),
                 "encode_peak_ms": round(encode_peak, 1), "encoder_recoveries": len(recoveries),
                 "frame_age_p95_ms": round(ordered[int(len(ordered) * 0.95) - 1] * 1000) if ordered else None,
                 "uptime_s": round(now - started), "frames_from_view": source.frames, "wifi_dbm": watch.wifi_dbm}
        self._set(stats=stats)
        if int(now - started) % 30 == 0:
          self.log("stats", **{**device_health.sample(getattr(watch.lease, "interface", "wlan0")), **stats})
          encode_peak = 0.0  # the peak covers each logged 30 s window
      spent = time.monotonic() - pass_started
      if spent > STREAM_STALL_LOG:
        # Nothing was read from the car meanwhile: pings, focus and ACKs all waited on this pass.
        self.log("stream_stall", ms=round(spent * 1000), focused=session.focused, pending=session.unacked)

  def _encode(self, encoder, encode, data: bytes, keyframe: bool, recoveries: list[float]) -> tuple[bytes, bool] | None:
    """Encode one frame. When the hardware encoder fails (the shared VPU stalled past the
    deadline), reopen it and drop this frame instead of tearing down the whole session:
    the next frame is an IDR, and the car waits 3 s for video before taking its screen back.
    Repeated failures still end the attempt, which reconnects from scratch."""
    started = time.monotonic()
    try:
      return encode(data, keyframe=keyframe)
    except Exception as error:
      failed = time.monotonic()
      recent = sum(1 for at in recoveries if failed - at <= ENCODER_RECOVERY_WINDOW)
      reopen = getattr(encoder, "reopen", None)  # libx264 has nothing to reopen
      if reopen is None or recent >= ENCODER_RECOVERIES:
        self.log("encoder_failed", error=str(error), encode_ms=round((failed - started) * 1000), recent_recoveries=recent)
        raise
      try:
        reopen()
      except Exception as reopen_error:
        self.log("encoder_reopen_failed", error=str(error), reopen_error=str(reopen_error))
        raise error from reopen_error
      recoveries.append(failed)
      self.log("encoder_recovered", error=str(error), encode_ms=round((failed - started) * 1000),
               reopen_ms=round((time.monotonic() - failed) * 1000), recent_recoveries=recent + 1, total=len(recoveries))
      return None

  @staticmethod
  def _unavailable_frame(request: FrameRequest | None, text: str) -> bytes:
    assert request is not None
    try:
      from PIL import Image, ImageDraw, ImageFont
      from openpilot.common.basedir import BASEDIR

      base = Path(BASEDIR) / "openpilot"
      logo_path = base / "selfdrive" / "assets" / "images" / "starpilot_logo.png"
      brand_font_path = base / "starpilot" / "ui" / "assets" / "fonts" / "Sora[wght].ttf"  # the logo wordmark's font
      fallback_font_path = base / "selfdrive" / "assets" / "fonts" / "Inter-Black.ttf"  # if Sora cannot be set to weight 800

      bg_color = (10, 10, 22, 255)  # Cosmic void #0a0a16
      canvas = Image.new("RGBA", (request.width, request.height), bg_color)

      # Text is drawn at 4x and shrunk: at car-screen sizes Sora's round letters (S, a, o) dip about half a
      # pixel below the baseline, which renders as a 1 px step and makes the bottoms look crooked.
      supersample = 4
      font_size = max(18, min(64, int(request.height * 0.06)))
      try:
        font = ImageFont.truetype(str(brand_font_path), font_size * supersample)
        font.set_variation_by_axes([800])
      except Exception:
        font = None
      try:
        font = font or ImageFont.truetype(str(fallback_font_path), font_size * supersample)
      except Exception:
        font, supersample = ImageFont.load_default(), 1

      bbox = font.getbbox(text)
      text_w = math.ceil((bbox[2] - bbox[0]) / supersample)
      text_h = math.ceil((bbox[3] - bbox[1]) / supersample)

      gap = max(12, int(request.height * 0.04))
      target_logo_h = int(min(request.height * 0.42, request.width * 0.35))

      if logo_path.exists() and target_logo_h > 20:
        logo = Image.open(logo_path).convert("RGBA")
        logo_w = int(logo.width * (target_logo_h / logo.height))
        logo_resized = logo.resize((logo_w, target_logo_h), Image.Resampling.LANCZOS)
        total_h = target_logo_h + gap + text_h
        start_y = max(8, (request.height - total_h) // 2)
        logo_x = (request.width - logo_w) // 2
        canvas.paste(logo_resized, (logo_x, int(start_y)), logo_resized)
        text_y = start_y + target_logo_h + gap
      else:
        text_y = (request.height - text_h) // 2

      text_x = (request.width - text_w) // 2
      layer = Image.new("RGBA", ((text_w + 1) * supersample, (text_h + 1) * supersample), (0, 0, 0, 0))
      draw = ImageDraw.Draw(layer)
      origin = (-bbox[0], -bbox[1])
      draw.text((origin[0] + supersample, origin[1] + supersample), text, font=font, fill=(30, 20, 50, 180))  # 1 px shadow
      draw.text(origin, text, font=font, fill=(250, 248, 255, 255))
      layer = layer.resize((text_w + 1, text_h + 1), Image.Resampling.LANCZOS)
      canvas.alpha_composite(layer, (int(text_x), int(text_y)))
      return canvas.tobytes()
    except Exception:
      try:
        import cv2
        import numpy as np
        image = np.zeros((request.height, request.width, 4), np.uint8)
        image[..., 3] = 255
        scale = request.height / 480
        size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, int(2 * scale)))[0]
        cv2.putText(image, text, ((request.width - size[0]) // 2, (request.height + size[1]) // 2), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, (255, 255, 255, 255), max(1, int(2 * scale)), cv2.LINE_AA)
        return image.tobytes()
      except Exception:
        return bytes(request.width * request.height * 4)
