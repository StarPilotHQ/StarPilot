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
import os
import socket
import stat
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
DEVICE_STATE_IP_CONFIG = 70
DEVICE_STATE_ACTIVATED = 100
DHCP_RESTARTS = 3
DEVICE_STATES_NOT_MANAGED = (10, 20)  # NM_DEVICE_STATE_UNMANAGED, NM_DEVICE_STATE_UNAVAILABLE
DEVICE_READY_TIMEOUT_S = 5.0
ACTIVE_STATE_ACTIVATING = 1
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
# A wireless adapter gives up ~12 s after sending its network (2026-10-29 OTTOCAST), and NetworkManager spent 6-15 s of
# that scanning every channel for a hidden SSID. Where this interface already sees the car's BSSID broadcasting, pin it
# to that channel; a sighting older than this may be from before the access point restarted on another channel.
AP_FRESH_S = 30.0
SCAN_REUSE_S = 5.0  # a scan requested this recently (device preparation just before the join) is not asked for again
# 2026-11-13 OTTOCAST, sessions 37-56: the join took 6.2 s at the median (5.4-13.9) and the adapter leased the same
# address every time (192.168.1.102, the car on .101); 2026-11-03's journal put association at 0.1 s and the rest in
# DHCP. A join that assumes the remembered address has no exchange to wait for, and nothing has proved the address
# either: the car must answer ARP from it within this long, or the address is forgotten and the join starts over with DHCP.
ADDRESS_CHECK_S = 1.5
ARP_TABLE = "/proc/net/arp"
REJOIN_MARKER = DATA_DIR / "wifi_rejoin.json"
REJOIN_ATTEMPTS = 2
DRIVE_JOIN_TIMEOUT_S = 20.0
DRIVE_SCAN_WAIT_S = 5.0
DRIVE_SCAN_EVERY_S = 45.0   # a forced scan takes the shared radio off the car's channel, so not on every check
HEARD_FRESH_S = 20.0        # a network not heard in the latest scan has been driven away from
FAILED_SKIP_S = 120.0       # a network that just refused a join is passed over for this long
DEVICE_STATE_FAILED = 120


class NetworkError(RuntimeError):
  pass


class DedicatedUnavailable(NetworkError):
  """The dedicated car interface cannot be used this session; projection falls back to wlan0."""


class JoinTimeout(NetworkError):
  """The car's network never came up in time."""


class NetworkTimeout(NetworkError):
  """NetworkManager did not answer a D-Bus request in time; the request may still have gone through."""


def close_router(router) -> None:
  """Close a D-Bus router together with its connection and the connection's stop pipe.

  jeepney 0.9.0: DBusRouter.close() leaves the connection open, and DBusConnection.close() never closes the two
  descriptors of its stop pipe. 2026-11-20: a check that opened a connection every 30 s left 1000 pipe descriptors
  behind in 4.2 h, the daemon reached its limit of 1024 open files, and every Bluetooth call failed from then on.
  """
  conn = getattr(router, "conn", None)
  try:
    router.close()
  finally:
    try:
      if conn is not None:
        conn.close()
    finally:
      for name in ("_stop_r", "_stop_w") if conn is not None else ():
        fd = getattr(conn, name, -1)
        setattr(conn, name, -1)
        try:
          if fd >= 0 and stat.S_ISFIFO(os.fstat(fd).st_mode):
            os.close(fd)
        except OSError:
          pass  # a later jeepney closed it already


def _boottime() -> float:
  """NetworkManager's AccessPoint.LastSeen clock."""
  return time.clock_gettime(time.CLOCK_BOOTTIME) if hasattr(time, "CLOCK_BOOTTIME") else time.monotonic()


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
        close_router(lease.router)
  marker.unlink(missing_ok=True)
  return True


def band_channel(frequency: int) -> tuple[str, int] | None:
  """NetworkManager's band and channel for a frequency in MHz, or None outside 2.4/5 GHz."""
  if frequency == 2484:
    return "bg", 14
  if 2412 <= frequency <= 2472 and (frequency - 2407) % 5 == 0:
    return "bg", (frequency - 2407) // 5
  if 5000 < frequency <= 5895 and frequency % 5 == 0:
    return "a", (frequency - 5000) // 5
  return None


def arp_answered(address: str, interface: str, table: str = ARP_TABLE) -> bool:
  """The kernel holds a hardware address for ``address`` on ``interface``: something there answered ARP. Entries go
  when the interface loses its last address, so one found after a fresh join was learned on that join."""
  try:
    with open(table) as handle:
      rows = [line.split() for line in handle.read().splitlines()[1:]]
  except OSError:
    return False
  for row in rows:
    if len(row) >= 6 and row[0] == address and row[5] == interface:
      try:
        complete = int(row[2], 16) & 0x2
      except ValueError:
        continue
      if complete and row[3] != "00:00:00:00:00:00":
        return True
  return False


def _rejoin_state(marker: Path) -> dict:
  try:
    state = json.loads(marker.read_text())
  except (OSError, ValueError):
    return {}
  return state if isinstance(state, dict) and state.get("boot") == _boot_id() else {}


def _remember_interrupted(marker: Path, interface: str, connection: str) -> None:
  state = _rejoin_state(marker)
  connections = [path for path in state.get("connections", []) if path != connection] + [connection]
  marker.parent.mkdir(parents=True, exist_ok=True)
  marker.write_text(json.dumps({"interface": interface, "boot": _boot_id(), "connections": connections}))


def rejoin_interrupted(log: Callable[..., None], marker: Path = REJOIN_MARKER) -> bool:
  """Rejoin a saved network a hold stopped mid-join; NetworkManager blocks its autoconnect until then. True when done."""
  if not marker.exists():
    return True
  state = _rejoin_state(marker)
  connections = [path for path in state.get("connections", []) if isinstance(path, str)]
  if not connections:
    marker.unlink(missing_ok=True)  # a reboot clears NetworkManager's block
    return True
  saved = state.get("failures")
  failures: dict[str, int] = saved if isinstance(saved, dict) else {}
  lease = NetworkLease(log, state.get("interface", "wlan0"))
  try:
    path = next((path for path, name in lease._wifi_devices() if name == lease.interface), "")
    if not path or not lease._get(path, NM_DEVICE_IFACE, "Autoconnect"):
      return False
    if int(lease._get(path, NM_DEVICE_IFACE, "State")) != DEVICE_STATE_DISCONNECTED:
      return False  # busy or on another network; wait until it is idle
    available = set(lease._get(path, NM_DEVICE_IFACE, "AvailableConnections"))
    connection = next((connection for connection in reversed(connections) if connection in available), "")  # newest first
    if not connection:
      return False
    try:
      lease._call(NM_PATH, NM_IFACE, "ActivateConnection", "ooo", (connection, path, "/"))
      log("wifi_rejoined", interface=lease.interface)
    except NetworkError as error:
      failures[connection] = failures.get(connection, 0) + 1
      log("wifi_rejoin_failed", error=str(error), attempt=failures[connection])
      if failures[connection] < REJOIN_ATTEMPTS:
        marker.write_text(json.dumps({**state, "failures": failures}))
        return False
  except NetworkError as error:
    log("wifi_rejoin_failed", error=str(error))
    return False
  finally:
    if lease.router is not None:
      close_router(lease.router)
  connections.remove(connection)
  failures.pop(connection, None)
  if connections:
    marker.write_text(json.dumps({**state, "connections": connections, "failures": failures}))
    return False
  marker.unlink(missing_ok=True)
  return True


def static_address(endpoint_ip: str) -> str:
  """An unused address beside the dongle's own, for dongles whose DHCP server never answers."""
  try:
    address = ipaddress.ip_address(endpoint_ip)
  except ValueError:
    return ""
  if address.version != 4 or not address.is_private:
    return ""
  octets = str(address).split(".")
  octets[3] = "77" if octets[3] != "77" else "78"
  return ".".join(octets)


def connection_settings(credentials: WifiCredentials, interface: str, frequency: int = 0, hidden: bool = True,
                        address: tuple[str, int] | None = None, static_ip: str = "") -> dict:
  """``frequency``/``hidden=False`` only for a BSSID this interface saw broadcasting: a profile that is not hidden never
  matches a hidden access point, and a pinned channel never finds one that moved. ``address`` (address, prefix) joins
  with that address instead of asking the access point for one; ``static_ip`` is the made-up address for a dongle
  whose DHCP server never answers, used only when no ``address`` is given."""
  wireless = {
    "ssid": ("ay", credentials.ssid.encode("utf-8")),
    "mode": ("s", "infrastructure"),
    "hidden": ("b", hidden or not credentials.bssid),  # projection networks are often hidden; harmless when broadcast
  }
  if credentials.bssid:
    wireless["bssid"] = ("ay", bytes.fromhex(credentials.bssid.replace(":", "")))
    if (pinned := band_channel(frequency)) is not None:
      wireless["band"], wireless["channel"] = ("s", pinned[0]), ("u", pinned[1])
  settings: dict[str, dict] = {
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
  if address is not None:
    settings["ipv4"]["method"] = ("s", "manual")
    settings["ipv4"]["address-data"] = ("aa{sv}", [{"address": ("s", address[0]), "prefix": ("u", int(address[1]))}])
  elif static_ip:
    settings["ipv4"]["method"] = ("s", "manual")
    settings["ipv4"]["address-data"] = ("aa{sv}", [{"address": ("s", static_ip), "prefix": ("u", 24)}])
  if not credentials.open:
    settings["802-11-wireless-security"] = {
      "key-mgmt": ("s", "sae" if credentials.security in SECURITY_WPA3 else "wpa-psk"),
      "psk": ("s", credentials.key),
    }
  return settings


NM_DEVICE_STATE_USABLE = 30  # NetworkManager device states below this: 10 unmanaged, 20 unavailable (no supplicant yet)


class ChannelMemory:
  """Where a car's access point was last found, per BSSID. 2026-11-02 OTTOCAST: without a sighting in the last 30 s the
  first join scans every channel for the hidden SSID (median 6.5 s, up to 11.4 s) and the adapter restarts its handshake
  at ~12 s; pinned joins took 3.4 s. A pin that fails is forgotten and the retry scans as before."""

  def __init__(self, load: Callable[[], dict], save: Callable[[dict], None]):
    self.load, self.save = load, save

  def frequency(self, bssid: str) -> int:
    try:
      return int(self.load().get(bssid.upper(), 0) or 0)
    except (TypeError, ValueError, AttributeError):
      return 0

  def remember(self, bssid: str, frequency: int) -> None:
    """Store where the access point was found, or forget it (``frequency`` 0)."""
    channels = dict(self.load())
    key = bssid.upper()
    if frequency:
      if channels.get(key) == frequency:
        return
      channels[key] = frequency
    elif channels.pop(key, None) is None:
      return
    self.save(channels)


class AddressMemory:
  """The IPv4 address a car's access point leased this comma last time, per BSSID, as "address/prefix"."""

  def __init__(self, load: Callable[[], dict], save: Callable[[dict], None]):
    self.load, self.save = load, save

  def address(self, bssid: str) -> tuple[str, int] | None:
    try:
      interface = ipaddress.ip_interface(str(self.load().get(bssid.upper(), "")))
    except (ValueError, AttributeError):
      return None
    if interface.version != 4 or not 8 <= interface.network.prefixlen <= 30:
      return None
    return str(interface.ip), interface.network.prefixlen

  def remember(self, bssid: str, address: str = "", prefix: int = 0) -> None:
    """Store the lease, or forget it (no ``address``)."""
    addresses = dict(self.load())
    key = bssid.upper()
    if address:
      value = f"{address}/{int(prefix)}"
      if addresses.get(key) == value:
        return
      addresses[key] = value
    elif addresses.pop(key, None) is None:
      return
    self.save(addresses)


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
    self.joined: WifiCredentials | None = None  # what active_path was activated for
    self.reuse_join = False  # car profile: keep a join in progress when the adapter restarts the handshake
    self.channel_memory: ChannelMemory | None = None  # car profile: pin the first join to the remembered channel
    self.pinned_from_memory = False
    self.address_memory: AddressMemory | None = None  # car profile: join with the address the access point leased last time
    self.peer = ""                # the car's address on its network; the supervisor sets it before a join when it knows
    self.static_address = ""      # the remembered address the current join assumed; "" when the access point leased one
    self.address_confirmed = False  # the car accepted a connection from the assumed address
    self._assumed: tuple[str, int] | None = None  # (address, prefix) the join being started will assume
    self.dhcp_patience = 0.0
    self.static_ip = ""
    self.endpoint_ip: Callable[[], str] = lambda: ""
    self.marker = AUTOCONNECT_MARKER
    self.scan_requested_at = -SCAN_REUSE_S
    self.rejoin_marker = REJOIN_MARKER
    self._failed_at: dict[str, float] = {}
    self._scanned_at = -DRIVE_SCAN_EVERY_S
    self._lock = threading.Lock()

  def _open(self) -> DBusRouter:
    if self.router is None:
      self.router = DBusRouter(open_dbus_connection(bus="SYSTEM"))
    return self.router

  def _call(self, path: str, interface: str, member: str, signature: str | None = None, body: tuple = (), timeout: float = 10.0):
    address = DBusAddress(path, bus_name=NM, interface=interface)
    message = new_method_call(address, member, signature, body) if signature else new_method_call(address, member)
    reply = self._reply(message, timeout, member)
    if reply.header.message_type == MessageType.error:
      raise NetworkError(f"{member} failed: {reply.body[0] if reply.body else reply.header}")
    return reply.body

  def _reply(self, message, timeout: float, what: str):
    # jeepney raises a bare TimeoutError (empty message) when NetworkManager does not answer. 2026-11-02 OTTOCAST: three
    # joins failed with that blank error 5 s after NetworkManager had activated the profile, so the join below retries.
    try:
      return self._open().send_and_get_reply(message, timeout=timeout)
    except TimeoutError as error:
      raise NetworkTimeout(f"NetworkManager did not answer {what} within {timeout:.0f} s") from error

  def _get(self, path: str, interface: str, name: str):
    address = DBusAddress(path, bus_name=NM, interface=interface)
    reply = self._reply(Properties(address).get(name), 5.0, f"reading {name}")
    if reply.header.message_type == MessageType.error:
      raise NetworkError(f"Cannot read {name}: {reply.body[0] if reply.body else ''}")
    return reply.body[0][1]

  def _set(self, path: str, interface: str, name: str, signature: str, value) -> None:
    address = DBusAddress(path, bus_name=NM, interface=interface)
    reply = self._reply(Properties(address).set(name, signature, value), 5.0, f"setting {name}")
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

  def acquire(self, credentials: WifiCredentials, timeout: float = JOIN_TIMEOUT_S, cancelled: Callable[[], bool] = lambda: False,
              dhcp_patience: float = 0.0, endpoint_ip: Callable[[], str] = lambda: "", static_first: bool = False) -> str:
    """Join the projection network and return the comma's IPv4 address on it.

    The BSSID the car reports is tried first (fast, unambiguous); if that
    attempt fails, one retry matches the SSID alone in case the reported BSSID
    belongs to a different radio of the head unit.

    With ``peer`` (the car's own address there) and an address memory set on the lease, the join assumes the address
    the access point leased last time and checks that the car answers from it, instead of waiting for DHCP.
    """
    self.dhcp_patience = dhcp_patience
    self.endpoint_ip = endpoint_ip
    self.static_ip = static_address(endpoint_ip()) if static_first else ""
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
        if self.pinned_from_memory and self.channel_memory is not None and not cancelled():
          self.pinned_from_memory = False  # the access point is not where it was last time; scan for it as before
          self.channel_memory.remember(attempt.bssid, 0)
          self.log("wifi_channel_forgotten", bssid=attempt.bssid, error=str(error))
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
    self.joined = None

  def _acquire_once(self, credentials: WifiCredentials, timeout: float, cancelled: Callable[[], bool]) -> str:
    deadline = time.monotonic() + timeout
    restarts = 0
    while True:
      self._assumed = self._remembered_address(credentials)
      self._activate(credentials, cancelled)
      if not self._wait_activated(deadline, timeout, cancelled, restarts < DHCP_RESTARTS):
        restarts += 1
        self.static_ip = static_address(self.endpoint_ip())
        self.log("wifi_dhcp_restart", interface=self.interface, patience_s=self.dhcp_patience, restarts=restarts, static_ip=self.static_ip)
        self._drop_attempt()
        continue
      while True:
        if cancelled():
          raise NetworkError("cancelled")
        try:
          self.local_ip = self._ipv4()
          break
        except NetworkTimeout as error:
          self._slow_answer(error, deadline)  # the network is up; a slow answer must not tear it down
      if self.static_address and not self._peer_answers(cancelled):
        if cancelled():
          raise NetworkError("cancelled")
        # Nothing answered from the assumed address: the access point leases something else now. Ask it.
        self._forget_address(credentials.bssid, f"the car did not answer from it within {ADDRESS_CHECK_S:.1f} s")
        self._drop_attempt()
        continue
      break
    if self.dedicated:
      self._check_overlap()
    radio = self._radio()
    self.log("wifi_joined", interface=self.interface, local_ip=self.local_ip, **radio,
             **({"address_source": "remembered"} if self.static_address else {}))
    self.pinned_from_memory = False
    if self.channel_memory is not None and credentials.bssid and radio.get("freq"):
      self.channel_memory.remember(credentials.bssid, int(radio["freq"]))
    if self.address_memory is not None and credentials.bssid and not self.static_address and not self.static_ip:
      self._remember_address(credentials.bssid)
    return self.local_ip

  def _remembered_address(self, credentials: WifiCredentials) -> tuple[str, int] | None:
    """The address to assume for this join: the one remembered for the access point, if the car is on that network."""
    if self.address_memory is None or not credentials.bssid or not self.peer:
      return None
    address = self.address_memory.address(credentials.bssid)
    if address is None:
      return None
    try:
      network = ipaddress.ip_interface(f"{address[0]}/{address[1]}").network
      peer = ipaddress.ip_address(self.peer)
    except ValueError:
      return None
    if peer not in network or str(peer) == address[0]:
      self._forget_address(credentials.bssid, f"the car is at {self.peer} now", address[0])
      return None
    return address

  def _remember_address(self, bssid: str) -> None:
    """After a join the access point answered: keep the address and prefix it leased."""
    try:
      config = self._get(self.active_path, NM_ACTIVE_IFACE, "Ip4Config")
      entries = self._get(config, NM_IP4_CONFIG_IFACE, "AddressData") if config and config != "/" else []
      for entry in entries:
        address, prefix = entry.get("address", ("s", ""))[1], entry.get("prefix", ("u", 0))[1]
        if address == self.local_ip and prefix:
          self.address_memory.remember(bssid, address, int(prefix))
          return
    except (NetworkError, TypeError, ValueError, AttributeError):
      pass

  def _forget_address(self, bssid: str, reason: str, address: str = "") -> None:
    if self.address_memory is not None and bssid:
      self.address_memory.remember(bssid)
      self.log("wifi_address_forgotten", address=address or self.static_address, reason=reason)
    self.static_address = ""

  def _peer_answers(self, cancelled: Callable[[], bool]) -> bool:
    """The car answers ARP for its own address from the address this join assumed. A datagram to the car's discard port
    is sent only so the kernel resolves that address; whether anything listens there does not matter."""
    deadline = time.monotonic() + ADDRESS_CHECK_S
    try:
      probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
      return False
    try:
      probe.bind((self.local_ip, 0))
      tries = 0
      while True:
        if tries % 5 == 0:
          try:
            probe.sendto(b"", (self.peer, 9))
          except OSError:
            pass
        tries += 1
        if arp_answered(self.peer, self.interface):
          return True
        if cancelled() or time.monotonic() >= deadline:
          return False
        time.sleep(0.05)
    except OSError:
      return False
    finally:
      probe.close()

  def confirm_address(self) -> None:
    """The car accepted a connection from the address this join assumed; without that, release() forgets it."""
    self.address_confirmed = True

  def _activate(self, credentials: WifiCredentials, cancelled: Callable[[], bool]) -> None:
    with self._lock:
      if not self._get(NM_PATH, NM_IFACE, "WirelessEnabled"):
        raise NetworkError("Wi-Fi is turned off on the comma")
      if self.dedicated:
        self._prepare_device(cancelled)
      else:
        self.device_path = self._wifi_device()
        self._wait_managed(cancelled)
        self._pause_autoconnect()
      if not (self.reuse_join and self._reusable(credentials)):
        active = self._get(self.device_path, NM_DEVICE_IFACE, "ActiveConnection")
        if active and active != "/" and not self.previous_connection and not self.dedicated:
          try:
            previous = self._get(active, NM_ACTIVE_IFACE, "Connection")
            if self._connection_id(previous) != CONNECTION_ID:
              self.previous_connection = previous
          except NetworkError:
            pass
        self._delete_stale_profiles()
        frequency = self._seen_frequency(credentials) if credentials.bssid else 0
        pinned_source = "seen" if frequency else ""
        if not frequency and credentials.bssid and self.channel_memory is not None:
          frequency = self.channel_memory.frequency(credentials.bssid)
          pinned_source = "remembered" if frequency else ""
        self.pinned_from_memory = pinned_source == "remembered"
        assumed = self._assumed
        self.static_address, self.address_confirmed = (assumed[0] if assumed else ""), False
        self._request_scan()  # every attempt: a retry's access point may have restarted since the last scan
        self.log("wifi_joining", interface=self.interface, **{k: v for k, v in credentials.describe().items() if k != "key_length"},
                 had_previous=bool(self.previous_connection), pinned_freq=frequency, pinned_source=pinned_source,
                 static_ip="" if assumed else self.static_ip, **({"address_source": "remembered"} if assumed else {}))
        # A remembered channel keeps the profile hidden: the access point may not broadcast, and the pin alone narrows the scan.
        body = self._call(NM_PATH, NM_IFACE, "AddAndActivateConnection2", "a{sa{sv}}ooa{sv}",
                          (connection_settings(credentials, self.interface, frequency, hidden=pinned_source != "seen", address=assumed,
                                               static_ip=self.static_ip),
                           self.device_path, "/", {"persist": ("s", "volatile")}), timeout=15.0)
        self.connection_path, self.active_path = body[0], body[1]
        self.joined = credentials

  def _wait_activated(self, deadline: float, timeout: float, cancelled: Callable[[], bool], may_restart: bool) -> bool:
    """True once active; False when DHCP sat unanswered past dhcp_patience and the join should start over."""
    ip_config_since = 0.0
    while True:
      if cancelled():
        raise NetworkError("cancelled")
      try:
        state = self._get(self.active_path, NM_ACTIVE_IFACE, "State")
      except NetworkTimeout as error:
        self._slow_answer(error, deadline)
        continue
      except NetworkError as error:
        raise NetworkError("The projection network connection was removed (wrong key or out of range?)") from error
      if state == ACTIVE_STATE_ACTIVATED:
        return True
      if state == ACTIVE_STATE_DEACTIVATED:
        raise NetworkError("NetworkManager could not join the projection network")
      now = time.monotonic()
      if now >= deadline:
        raise JoinTimeout(f"Timed out joining the projection network after {timeout:.0f} s")
      if may_restart and self.dhcp_patience > 0:
        if self._get(self.device_path, NM_DEVICE_IFACE, "State") == DEVICE_STATE_IP_CONFIG:
          ip_config_since = ip_config_since or now
          if now - ip_config_since >= self.dhcp_patience:
            return False
        else:
          ip_config_since = 0.0
      time.sleep(0.25)

  def interface_state(self) -> int:
    """NetworkManager's state for this interface: 10 unmanaged, 20 unavailable, 30 and up usable; 0 without such a device."""
    with self._lock:
      for path, name in self._wifi_devices():
        if name == self.interface:
          return int(self._get(path, NM_DEVICE_IFACE, "State"))
    return 0

  def _slow_answer(self, error: NetworkTimeout, deadline: float) -> None:
    """A NetworkManager request timed out mid-join: log it and keep waiting until the join's own deadline."""
    self.log("wifi_slow_answer", error=str(error))
    if time.monotonic() >= deadline:
      raise JoinTimeout("NetworkManager stopped answering while joining the projection network") from error
    time.sleep(0.5)

  def _seen_frequency(self, credentials: WifiCredentials) -> int:
    """The channel this interface recently saw the car's BSSID broadcast its SSID on, or 0."""
    try:
      body = self._call(self.device_path, NM_WIRELESS_IFACE, "GetAllAccessPoints")
      for access_point in body[0] if body else []:
        if self._get(access_point, NM_AP_IFACE, "HwAddress").upper() != credentials.bssid:
          continue
        last_seen = int(self._get(access_point, NM_AP_IFACE, "LastSeen"))
        if (bytes(self._get(access_point, NM_AP_IFACE, "Ssid")) != credentials.ssid.encode("utf-8") or
            last_seen < 0 or _boottime() - last_seen > AP_FRESH_S):
          return 0
        return int(self._get(access_point, NM_AP_IFACE, "Frequency"))
    except (NetworkError, TypeError, ValueError):
      pass
    return 0

  def _request_scan(self) -> None:
    if time.monotonic() - self.scan_requested_at < SCAN_REUSE_S:
      return
    try:
      self._call(self.device_path, NM_WIRELESS_IFACE, "RequestScan", "a{sv}", ({},))
      self.scan_requested_at = time.monotonic()
      self.log("wifi_scan_requested", interface=self.interface)
    except NetworkError as error:
      self.log("wifi_scan_request_failed", error=str(error))

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
    self._request_scan()

  def hold_autoconnect(self) -> bool:
    """Stop wlan0 hunting for a network until release(restore=True). True when held."""
    with self._lock:
      self.device_path = next((path for path, name in self._wifi_devices() if name == self.interface), "")
      if not self.device_path:
        return False
      state = int(self._get(self.device_path, NM_DEVICE_IFACE, "State"))
      if state == DEVICE_STATE_ACTIVATED:
        if self._carrier():
          return False  # connected again; nothing to hold
        # NetworkManager reports a lost network as connected for 15 s, then joins the next one by itself.
        self._pause_autoconnect()
        return self.autoconnect_paused
      self._pause_autoconnect()
      if DEVICE_STATE_PREPARE <= state < DEVICE_STATE_ACTIVATED:
        self._remember_join()
        try:
          self._call(self.device_path, NM_DEVICE_IFACE, "Disconnect")  # a join already under way
        except NetworkError as error:
          self.log("wifi_hold_disconnect_failed", error=str(error))
      return self.autoconnect_paused

  def _carrier(self) -> bool:
    try:
      return Path(f"/sys/class/net/{self.interface}/carrier").read_text().strip() == "1"
    except OSError:
      return True

  def drive_join(self, allowed: Callable[[str], bool], before: Callable[[], bool] = lambda: True,
                 after: Callable[[], None] = lambda: None, timeout: float = DRIVE_JOIN_TIMEOUT_S,
                 cancelled: Callable[[], bool] = lambda: False) -> str:
    """Join the best allowed saved network in range by hand while auto-join stays paused.

    Returns "connected" (already on a network), "none" (nothing to join), "joined" or "failed".
    """
    with self._lock:
      if cancelled():
        return "none"
      path = self.device_path or next((path for path, name in self._wifi_devices() if name == self.interface), "")
      if not path:
        return "none"
      state = int(self._get(path, NM_DEVICE_IFACE, "State"))
      if state == DEVICE_STATE_ACTIVATED:
        return "connected" if self._carrier() else "none"
      if state != DEVICE_STATE_DISCONNECTED:
        return "none"
    self._scan(path, cancelled)
    with self._lock:
      if cancelled() or int(self._get(path, NM_DEVICE_IFACE, "State")) != DEVICE_STATE_DISCONNECTED:
        return "none"
      heard = self._heard(path)
      now = time.monotonic()
      self._failed_at = {key: at for key, at in self._failed_at.items() if now - at < FAILED_SKIP_S}
      candidates = []
      for connection in self._get(path, NM_DEVICE_IFACE, "AvailableConnections"):
        settings = self._call(connection, NM_SETTINGS_CONNECTION_IFACE, "GetSettings")[0]
        values = {key: entry[1] for key, entry in settings.get("connection", {}).items()}
        if values.get("id") == CONNECTION_ID or not values.get("autoconnect", True) or not allowed(values.get("uuid", "")):
          continue
        wireless = settings.get("802-11-wireless", {})
        strength = heard.get(bytes(wireless["ssid"][1]), -1) if "ssid" in wireless else -1
        if strength < 0 or connection in self._failed_at:
          continue
        candidates.append((int(values.get("autoconnect-priority", 0)), strength, connection))
      if not candidates:
        return "none"
      target = max(candidates)[2]
    if not before():
      after()
      return "none"
    try:
      with self._lock:
        if cancelled() or int(self._get(path, NM_DEVICE_IFACE, "State")) != DEVICE_STATE_DISCONNECTED:
          return "none"
        self._call(NM_PATH, NM_IFACE, "ActivateConnection", "ooo", (target, path, "/"))
      deadline, started = time.monotonic() + timeout, False
      while time.monotonic() < deadline:
        time.sleep(0.25)
        with self._lock:
          state = int(self._get(path, NM_DEVICE_IFACE, "State"))
        if state == DEVICE_STATE_ACTIVATED:
          return "joined"
        if state == DEVICE_STATE_FAILED or (started and state == DEVICE_STATE_DISCONNECTED):
          self._failed_at[target] = time.monotonic()
          return "failed"  # refused, or stopped because the car link stalled
        if state >= DEVICE_STATE_PREPARE:
          started = True
      self._failed_at[target] = time.monotonic()
      return "failed"
    except NetworkError as error:
      self.log("wifi_drive_join_failed", error=str(error))
      self._failed_at[target] = time.monotonic()
      return "failed"
    finally:
      after()
      if self.autoconnect_paused:
        try:
          with self._lock:
            self._set(path, NM_DEVICE_IFACE, "Autoconnect", "b", False)  # joining by hand turns auto-join back on
        except NetworkError as error:
          self.log("wifi_autoconnect_pause_failed", error=str(error))

  def _scan(self, path: str, cancelled: Callable[[], bool] = lambda: False) -> None:
    """Request a scan while auto-join is paused and wait briefly for completion."""
    if time.monotonic() - self._scanned_at < DRIVE_SCAN_EVERY_S:
      return
    self._scanned_at = time.monotonic()
    try:
      with self._lock:
        if cancelled():
          return
        before = int(self._get(path, NM_WIRELESS_IFACE, "LastScan"))
        self._call(path, NM_WIRELESS_IFACE, "RequestScan", "a{sv}", ({},))
    except NetworkError:
      return  # already scanning, or scans refused; the access points heard so far still count
    deadline = time.monotonic() + DRIVE_SCAN_WAIT_S
    while time.monotonic() < deadline:
      time.sleep(0.25)
      try:
        with self._lock:
          if cancelled():
            return
          if int(self._get(path, NM_WIRELESS_IFACE, "LastScan")) != before:
            return
      except NetworkError:
        return

  def _heard(self, path: str) -> dict[bytes, int]:
    """Strongest signal per network name among access points heard in the latest scan."""
    try:
      last_scan = int(self._get(path, NM_WIRELESS_IFACE, "LastScan")) / 1000
    except NetworkError:
      last_scan = -1
    now = time.clock_gettime(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))
    since = now - HEARD_FRESH_S
    if 0 < now - last_scan <= DRIVE_SCAN_EVERY_S + DRIVE_SCAN_WAIT_S + 5:
      since = min(since, last_scan - 5)  # between forced scans, what the last one heard still counts
    heard: dict[bytes, int] = {}
    for access_point in self._get(path, NM_WIRELESS_IFACE, "AccessPoints"):
      try:
        seen = int(self._get(access_point, NM_AP_IFACE, "LastSeen"))
        if seen < 0 or seen < since:
          continue
        ssid = bytes(self._get(access_point, NM_AP_IFACE, "Ssid"))
        heard[ssid] = max(heard.get(ssid, 0), int(self._get(access_point, NM_AP_IFACE, "Strength")))
      except NetworkError:
        continue
    return heard

  def _remember_join(self) -> None:
    """Disconnect blocks the network being joined from autoconnecting; note it so it can be rejoined after the session."""
    try:
      active = self._get(self.device_path, NM_DEVICE_IFACE, "ActiveConnection")
      connection = self._get(active, NM_ACTIVE_IFACE, "Connection") if active and active != "/" else ""
      if connection and connection != "/":
        _remember_interrupted(self.rejoin_marker, self.interface, connection)
    except (NetworkError, OSError) as error:
      self.log("wifi_rejoin_note_failed", error=str(error))

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

  def _reusable(self, credentials: WifiCredentials) -> bool:
    """The adapter restarted the handshake with the same network while our join of it is still up or coming up.

    2026-10-29 OTTOCAST: DHCP finished 11.4 s into the join, the adapter restarted at 12 s, and deleting the profile then
    threw away a working connection and cost two more joins. Keep it; a dead one still fails the wait below.
    """
    if not self.active_path or credentials != self.joined:
      return False
    try:
      state = self._get(self.active_path, NM_ACTIVE_IFACE, "State")
    except NetworkError:
      return False
    if state not in (ACTIVE_STATE_ACTIVATING, ACTIVE_STATE_ACTIVATED):
      return False
    self.log("wifi_join_reused", interface=self.interface, state=int(state))
    return True

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
    # An assumed address the car never accepted a connection from is not kept: the next join asks the access point.
    if self.static_address and not self.address_confirmed and self.joined is not None:
      self._forget_address(self.joined.bssid, "the car accepted no connection from it")
    self.static_address, self.address_confirmed = "", False
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
      self.joined = None
      if restore:
        self.previous_connection = ""
      try:
        if self.router is not None:
          close_router(self.router)
      finally:
        self.router = None
