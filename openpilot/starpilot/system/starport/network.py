"""NetworkManager lease for the head unit's projection Wi-Fi.

The lease creates one volatile, never-default connection profile named
``starpilot-starport``, activates it on the Wi-Fi device, and on release
deactivates and deletes only that profile and reactivates whatever Wi-Fi
connection was active before -- unless the user switched networks meanwhile.
Credentials travel over D-Bus only; they never appear in argv or logs. The
cellular default route and DNS are left alone (``never-default``,
``ignore-auto-dns``) so the car's local network cannot take over Internet traffic.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from jeepney import DBusAddress, new_method_call
from jeepney.io.threading import DBusRouter, open_dbus_connection
from jeepney.low_level import MessageType
from jeepney.wrappers import Properties

from openpilot.starpilot.system.starport.bootstrap import WifiCredentials
from openpilot.starpilot.system.starport.identity import DATA_DIR, boot_id as _boot_id

NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_IFACE = "org.freedesktop.NetworkManager"
NM_DEVICE_IFACE = "org.freedesktop.NetworkManager.Device"
NM_ACTIVE_IFACE = "org.freedesktop.NetworkManager.Connection.Active"
NM_SETTINGS_CONNECTION_IFACE = "org.freedesktop.NetworkManager.Settings.Connection"
NM_IP4_CONFIG_IFACE = "org.freedesktop.NetworkManager.IP4Config"
NM_WIRELESS_IFACE = "org.freedesktop.NetworkManager.Device.Wireless"
NM_AP_IFACE = "org.freedesktop.NetworkManager.AccessPoint"
DEVICE_TYPE_WIFI = 2
DEVICE_STATE_DISCONNECTED = 30
DEVICE_STATE_PREPARE = 40
DEVICE_STATE_ACTIVATED = 100
DEVICE_STATES_NOT_MANAGED = (10, 20)  # NM_DEVICE_STATE_UNMANAGED, NM_DEVICE_STATE_UNAVAILABLE
DEVICE_READY_TIMEOUT_S = 5.0
# A shared interface can be briefly unmanaged (for example while the Wi-Fi mode is being switched). Bugsink showed 19 joins
# refused with "device is strictly unmanaged"; waiting a few seconds turns a transient state into a normal join, and a
# device that stays unmanaged is reported as such instead of as an opaque D-Bus error.
UNMANAGED_WAIT_S = 8.0
ACTIVE_STATE_ACTIVATED = 2
ACTIVE_STATE_DEACTIVATED = 4
# The car sends its Wi-Fi details ~4 s after ignition, but a Honda's access point took ~55 s from ignition to appear
# (2026-09-30 drives). After a reported failed join that car stopped auto-launching projection until the driver tapped
# Android Auto, so wait for a slow access point rather than give up. The car's reported BSSID gets most of the time;
# the SSID-only retry covers head units that report the wrong radio.
JOIN_TIMEOUT_S = 120.0
BSSID_SHARE = 0.75
CONNECTION_ID = "starpilot-starport"
SECURITY_WPA3 = (32,)
AUTOCONNECT_MARKER = DATA_DIR / "wifi_autoconnect_paused.json"


class NetworkError(RuntimeError):
  pass


class DedicatedUnavailable(NetworkError):
  """The dedicated car interface cannot be used this session; projection falls back to wlan0."""


class JoinTimeout(NetworkError):
  """The car's network never came up in time."""


def _paused_interface(marker: Path) -> str:
  """The interface a session paused this boot and never restored, or ""; NetworkManager resets autoconnect on reboot."""
  try:
    state = json.loads(marker.read_text())
  except (OSError, ValueError):
    return ""
  return state.get("interface", "") if isinstance(state, dict) and state.get("boot") == _boot_id() else ""


def recover_autoconnect(log: Callable[..., None], marker: Path = AUTOCONNECT_MARKER) -> bool:
  """Turn wlan0 autoconnect back on after a session that paused it ended without restoring it. True when done."""
  if not marker.exists():
    return True
  interface = _paused_interface(marker)
  if interface:
    lease = NetworkLease(log, interface)
    try:
      path = next((path for path, name in lease._wifi_devices() if name == interface), "")
      if not path:
        return False
      lease._set(path, NM_DEVICE_IFACE, "Autoconnect", "b", True)
      log("wifi_autoconnect_recovered", interface=interface)
    except NetworkError as error:
      log("wifi_autoconnect_recover_failed", error=str(error))
      return False
    finally:
      if lease.router is not None:
        lease.router.close()
  marker.unlink(missing_ok=True)
  return True


def connection_settings(credentials: WifiCredentials, interface: str) -> dict:
  wireless = {
    "ssid": ("ay", credentials.ssid.encode("utf-8")),
    "mode": ("s", "infrastructure"),
    "hidden": ("b", True),  # projection networks are often hidden; harmless when broadcast
  }
  if credentials.bssid:
    wireless["bssid"] = ("ay", bytes.fromhex(credentials.bssid.replace(":", "")))
  settings = {
    "connection": {
      "type": ("s", "802-11-wireless"),
      "uuid": ("s", str(uuid.uuid4())),
      "id": ("s", CONNECTION_ID),
      "interface-name": ("s", interface),
      "autoconnect": ("b", False),
      "autoconnect-retries": ("i", 1),
    },
    "802-11-wireless": wireless,
    "ipv4": {
      "method": ("s", "auto"),
      "never-default": ("b", True),
      "ignore-auto-dns": ("b", True),
      "route-metric": ("x", 3000),
    },
    "ipv6": {"method": ("s", "ignore")},
  }
  if not credentials.open:
    settings["802-11-wireless-security"] = {
      "key-mgmt": ("s", "sae" if credentials.security in SECURITY_WPA3 else "wpa-psk"),
      "psk": ("s", credentials.key),
    }
  return settings


class NetworkLease:
  def __init__(self, log: Callable[..., None], interface: str = "wlan0", dedicated: bool = False):
    """``dedicated``: the interface exists only for the car, so never fall back to another one or touch its previous network."""
    self.log = log
    self.interface = interface
    self.dedicated = dedicated
    self.router: DBusRouter | None = None
    self.device_path = ""
    self.connection_path = ""
    self.active_path = ""
    self.previous_connection = ""
    self.local_ip = ""
    self.device_ready = False
    self.autoconnect_paused = False
    self.marker = AUTOCONNECT_MARKER
    self._lock = threading.Lock()

  def _open(self) -> DBusRouter:
    if self.router is None:
      self.router = DBusRouter(open_dbus_connection(bus="SYSTEM"))
    return self.router

  def _call(self, path: str, interface: str, member: str, signature: str | None = None, body: tuple = (), timeout: float = 10.0):
    address = DBusAddress(path, bus_name=NM, interface=interface)
    message = new_method_call(address, member, signature, body) if signature else new_method_call(address, member)
    reply = self._open().send_and_get_reply(message, timeout=timeout)
    if reply.header.message_type == MessageType.error:
      raise NetworkError(f"{member} failed: {reply.body[0] if reply.body else reply.header}")
    return reply.body

  def _get(self, path: str, interface: str, name: str):
    address = DBusAddress(path, bus_name=NM, interface=interface)
    reply = self._open().send_and_get_reply(Properties(address).get(name), timeout=5.0)
    if reply.header.message_type == MessageType.error:
      raise NetworkError(f"Cannot read {name}: {reply.body[0] if reply.body else ''}")
    return reply.body[0][1]

  def _set(self, path: str, interface: str, name: str, signature: str, value) -> None:
    address = DBusAddress(path, bus_name=NM, interface=interface)
    reply = self._open().send_and_get_reply(Properties(address).set(name, signature, value), timeout=5.0)
    if reply.header.message_type == MessageType.error:
      raise NetworkError(f"Cannot set {name}: {reply.body[0] if reply.body else ''}")

  def _wifi_devices(self) -> list[tuple[str, str]]:
    devices = []
    for path in self._call(NM_PATH, NM_IFACE, "GetDevices")[0]:
      try:
        if self._get(path, NM_DEVICE_IFACE, "DeviceType") == DEVICE_TYPE_WIFI:
          devices.append((path, self._get(path, NM_DEVICE_IFACE, "Interface")))
      except NetworkError:
        continue
    return devices

  def _wifi_device(self) -> str:
    devices = self._wifi_devices()
    for path, name in devices:
      if name == self.interface:
        return path
    if not devices:
      raise NetworkError("No Wi-Fi device found")
    self.interface = devices[0][1]
    return devices[0][0]

  def prepare_device(self, cancelled: Callable[[], bool] = lambda: False) -> None:
    """Dedicated mode, ahead of the join: wait for NetworkManager to take the new interface and keep saved networks off it."""
    with self._lock:
      self._prepare_device(cancelled)

  def acquire(self, credentials: WifiCredentials, timeout: float = JOIN_TIMEOUT_S, cancelled: Callable[[], bool] = lambda: False) -> str:
    """Join the projection network and return the comma's IPv4 address on it.

    The BSSID the car reports is tried first (fast, unambiguous); if that
    attempt fails, one retry matches the SSID alone in case the reported BSSID
    belongs to a different radio of the head unit.
    """
    attempts = [credentials]
    if credentials.bssid:
      attempts.append(replace(credentials, bssid=""))
    last_error: NetworkError | None = None
    for index, attempt in enumerate(attempts):
      try:
        share = 1.0 if len(attempts) == 1 else (BSSID_SHARE if index == 0 else 1.0 - BSSID_SHARE)
        return self._acquire_once(attempt, timeout * share, cancelled)
      except DedicatedUnavailable:
        raise
      except NetworkError as error:
        last_error = error
        if cancelled() or index == len(attempts) - 1:
          raise
        self.log("wifi_retry_without_bssid", error=str(error))
        self._drop_attempt()
    raise last_error or NetworkError("Could not join the projection network")

  def _drop_attempt(self) -> None:
    for path, interface, member in ((self.active_path, NM_IFACE, "DeactivateConnection"),
                                    (self.connection_path, NM_SETTINGS_CONNECTION_IFACE, "Delete")):
      if not path:
        continue
      try:
        if member == "DeactivateConnection":
          self._call(NM_PATH, NM_IFACE, member, "o", (path,))
        else:
          self._call(path, interface, member)
      except NetworkError:
        pass
    self.active_path = self.connection_path = ""

  def _acquire_once(self, credentials: WifiCredentials, timeout: float, cancelled: Callable[[], bool]) -> str:
    with self._lock:
      if not self._get(NM_PATH, NM_IFACE, "WirelessEnabled"):
        raise NetworkError("Wi-Fi is turned off on the comma")
      if self.dedicated:
        self._prepare_device(cancelled)
      else:
        self.device_path = self._wifi_device()
        self._wait_managed(cancelled)
        self._pause_autoconnect()
      active = self._get(self.device_path, NM_DEVICE_IFACE, "ActiveConnection")
      if active and active != "/" and not self.previous_connection and not self.dedicated:
        try:
          previous = self._get(active, NM_ACTIVE_IFACE, "Connection")
          if self._connection_id(previous) != CONNECTION_ID:
            self.previous_connection = previous
        except NetworkError:
          pass
      self._delete_stale_profiles()
      self.log("wifi_joining", interface=self.interface, **{k: v for k, v in credentials.describe().items() if k != "key_length"},
               had_previous=bool(self.previous_connection))
      body = self._call(NM_PATH, NM_IFACE, "AddAndActivateConnection2", "a{sa{sv}}ooa{sv}",
                        (connection_settings(credentials, self.interface), self.device_path, "/",
                         {"persist": ("s", "volatile")}), timeout=15.0)
      self.connection_path, self.active_path = body[0], body[1]

    deadline = time.monotonic() + timeout
    while True:
      if cancelled():
        raise NetworkError("cancelled")
      try:
        state = self._get(self.active_path, NM_ACTIVE_IFACE, "State")
      except NetworkError as error:
        raise NetworkError("The projection network connection was removed (wrong key or out of range?)") from error
      if state == ACTIVE_STATE_ACTIVATED:
        break
      if state == ACTIVE_STATE_DEACTIVATED:
        raise NetworkError("NetworkManager could not join the projection network")
      if time.monotonic() >= deadline:
        raise JoinTimeout(f"Timed out joining the projection network after {timeout:.0f} s")
      time.sleep(0.25)
    self.local_ip = self._ipv4()
    if self.dedicated:
      self._check_overlap()
    self.log("wifi_joined", interface=self.interface, local_ip=self.local_ip, **self._radio())
    return self.local_ip

  def _frequency(self, path: str) -> int:
    access_point = self._get(path, NM_WIRELESS_IFACE, "ActiveAccessPoint")
    return int(self._get(access_point, NM_AP_IFACE, "Frequency")) if access_point and access_point != "/" else 0

  def _radio(self) -> dict:
    """Channels at join time; a car and home Wi-Fi on different channels share the radio's airtime."""
    radio = {}
    try:
      radio["freq"] = self._frequency(self.device_path)
      if self.dedicated:
        for path, name in self._wifi_devices():
          if name == "wlan0":
            radio["wlan0_state"] = int(self._get(path, NM_DEVICE_IFACE, "State"))
            radio["wlan0_freq"] = self._frequency(path)
    except NetworkError:
      pass
    return radio

  def _wait_step(self, deadline: float, cancelled: Callable[[], bool], failure: str) -> None:
    if cancelled():
      raise NetworkError("cancelled")
    if time.monotonic() >= deadline:
      raise DedicatedUnavailable(failure)
    time.sleep(0.25)

  def _wait_managed(self, cancelled: Callable[[], bool], timeout: float = UNMANAGED_WAIT_S) -> None:
    """Give NetworkManager a few seconds to manage the shared interface before joining on it."""
    deadline = time.monotonic() + timeout
    waited = False
    while (state := self._get(self.device_path, NM_DEVICE_IFACE, "State")) in DEVICE_STATES_NOT_MANAGED:
      if cancelled():
        raise NetworkError("cancelled")
      if time.monotonic() >= deadline:
        raise NetworkError(f"{self.interface} is unmanaged by NetworkManager (state {state}) after waiting {timeout:.0f} s")
      if not waited:
        waited = True
        self.log("wifi_waiting_managed", interface=self.interface, state=state)
      time.sleep(0.25)
    if waited:
      self.log("wifi_became_managed", interface=self.interface)

  def _prepare_device(self, cancelled: Callable[[], bool]) -> None:
    if self.device_ready:
      return
    deadline = time.monotonic() + DEVICE_READY_TIMEOUT_S
    while not (path := next((path for path, name in self._wifi_devices() if name == self.interface), "")):
      self._wait_step(deadline, cancelled, f"{self.interface} is not a NetworkManager Wi-Fi device")
    self.device_path = path
    # Before the device is ready, so a saved network can never autoconnect on it.
    if self._get(path, NM_DEVICE_IFACE, "Autoconnect"):
      try:
        self._set(path, NM_DEVICE_IFACE, "Autoconnect", "b", False)
      except NetworkError as error:
        raise DedicatedUnavailable(f"Cannot stop saved networks joining {self.interface}") from error
    while self._get(path, NM_DEVICE_IFACE, "State") < DEVICE_STATE_DISCONNECTED:
      self._wait_step(deadline, cancelled, f"NetworkManager did not take {self.interface}")
    self.device_ready = True
    # A new interface has never scanned, so the car's BSSID would be rejected until a scan finds it.
    try:
      self._call(path, NM_WIRELESS_IFACE, "RequestScan", "a{sv}", ({},))
      self.log("wifi_scan_requested", interface=self.interface)
    except NetworkError as error:
      self.log("wifi_scan_request_failed", error=str(error))

  def hold_autoconnect(self) -> bool:
    """wlan0 lost its network while the car streams on aa0: stop it hunting for one until release(restore=True).

    Each join attempt takes the shared radio off the car's channel for a second or more; on 2026-11-04 retries of a
    home network fading out of range stalled the car's video past its ACK timeout. True when held."""
    with self._lock:
      self.device_path = next((path for path, name in self._wifi_devices() if name == self.interface), "")
      if not self.device_path:
        return False
      state = int(self._get(self.device_path, NM_DEVICE_IFACE, "State"))
      if state == DEVICE_STATE_ACTIVATED:
        return False  # connected again; nothing to hold
      self._pause_autoconnect()
      if DEVICE_STATE_PREPARE <= state < DEVICE_STATE_ACTIVATED:
        try:
          self._call(self.device_path, NM_DEVICE_IFACE, "Disconnect")  # a join already under way
        except NetworkError as error:
          self.log("wifi_hold_disconnect_failed", error=str(error))
      return self.autoconnect_paused

  def _pause_autoconnect(self) -> None:
    """Keep NetworkManager from rejoining home Wi-Fi between attempts; release(restore=True) turns it back on."""
    if self.autoconnect_paused:
      return
    try:
      if self._get(self.device_path, NM_DEVICE_IFACE, "Autoconnect"):
        if not self._write_marker():
          return
        self._set(self.device_path, NM_DEVICE_IFACE, "Autoconnect", "b", False)
        self.autoconnect_paused = True
      elif _paused_interface(self.marker) == self.interface:
        self.autoconnect_paused = True
    except NetworkError as error:
      self.log("wifi_autoconnect_pause_failed", error=str(error))

  def _write_marker(self) -> bool:
    try:
      self.marker.parent.mkdir(parents=True, exist_ok=True)
      self.marker.write_text(json.dumps({"interface": self.interface, "boot": _boot_id()}))
      return True
    except OSError as error:
      self.log("wifi_autoconnect_marker_failed", error=str(error))
      return False

  def _networks(self, path: str) -> list:
    config = self._get(path, NM_DEVICE_IFACE, "Ip4Config")
    if not config or config == "/":
      return []
    return [ipaddress.ip_network(f"{entry['address'][1]}/{entry['prefix'][1]}", strict=False)
            for entry in self._get(config, NM_IP4_CONFIG_IFACE, "AddressData") if "address" in entry and "prefix" in entry]

  def _check_overlap(self) -> None:
    """The car's subnet must not also be reachable through home Wi-Fi, or replies to the head unit leave on wlan0."""
    ours = self._networks(self.device_path)
    for path in self._call(NM_PATH, NM_IFACE, "GetDevices")[0]:
      if path == self.device_path:
        continue
      try:
        others = self._networks(path)
      except NetworkError:
        continue
      for other in others:
        if any(network.overlaps(other) for network in ours):
          self.log("wifi_subnet_overlap", car=str(ours[0]), other=str(other))
          raise DedicatedUnavailable(f"The car's network {ours[0]} overlaps {other}")

  def _connection_id(self, path: str) -> str:
    try:
      return self._call(path, NM_SETTINGS_CONNECTION_IFACE, "GetSettings")[0].get("connection", {}).get("id", ("s", ""))[1]
    except NetworkError:
      return ""

  def _ipv4(self) -> str:
    config = self._get(self.active_path, NM_ACTIVE_IFACE, "Ip4Config")
    if config and config != "/":
      for entry in self._get(config, NM_IP4_CONFIG_IFACE, "AddressData"):
        address = entry.get("address", ("s", ""))[1]
        if address:
          return address
    raise NetworkError("Joined the projection network but received no IPv4 address")

  def _delete_stale_profiles(self) -> None:
    """Remove profiles left by a crashed earlier session (volatile ones vanish on their own)."""
    settings = DBusAddress("/org/freedesktop/NetworkManager/Settings", bus_name=NM, interface="org.freedesktop.NetworkManager.Settings")
    reply = self._open().send_and_get_reply(new_method_call(settings, "ListConnections"), timeout=5.0)
    if reply.header.message_type == MessageType.error:
      return
    for path in reply.body[0]:
      try:
        body = self._call(path, NM_SETTINGS_CONNECTION_IFACE, "GetSettings")
        if body[0].get("connection", {}).get("id", ("s", ""))[1] == CONNECTION_ID:
          self._call(path, NM_SETTINGS_CONNECTION_IFACE, "Delete")
          self.log("wifi_stale_profile_removed")
      except NetworkError:
        continue

  def still_connected(self) -> bool:
    with self._lock:
      if not self.active_path:
        return False
      return self._get(self.active_path, NM_ACTIVE_IFACE, "State") == ACTIVE_STATE_ACTIVATED

  def release(self, restore: bool = True) -> None:
    """Undo only what this lease owns. Safe to call repeatedly.

    ``restore=False`` is for a retry: the earlier network is remembered and
    reactivated only when the session finally stops, avoiding churn between attempts.
    """
    with self._lock:
      if self.router is None and not (restore and (self.previous_connection or self.autoconnect_paused)):
        return
      ours_active = False
      try:
        if self.device_path:
          current = self._get(self.device_path, NM_DEVICE_IFACE, "ActiveConnection")
          ours_active = current == self.active_path or current in ("", "/")
      except NetworkError:
        ours_active = True
      if self.active_path:
        try:
          self._call(NM_PATH, NM_IFACE, "DeactivateConnection", "o", (self.active_path,))
        except NetworkError:
          pass
      if self.connection_path:
        try:
          self._call(self.connection_path, NM_SETTINGS_CONNECTION_IFACE, "Delete")
        except NetworkError:
          pass  # volatile profiles delete themselves on deactivation
      if restore and self.autoconnect_paused:
        try:
          self._set(self.device_path, NM_DEVICE_IFACE, "Autoconnect", "b", True)
          self.marker.unlink(missing_ok=True)
        except (NetworkError, OSError) as error:
          self.log("wifi_autoconnect_restore_failed", error=str(error))
        self.autoconnect_paused = False
      if restore and ours_active and self.previous_connection and self.device_path:
        try:
          self._call(NM_PATH, NM_IFACE, "ActivateConnection", "ooo", (self.previous_connection, self.device_path, "/"))
          self.log("wifi_restored_previous")
        except NetworkError as error:
          self.log("wifi_restore_failed", error=str(error))
      self.connection_path = self.active_path = self.local_ip = ""
      if restore:
        self.previous_connection = ""
      try:
        if self.router is not None:
          self.router.close()
      finally:
        self.router = None
