"""BlueZ integration for the phone role: HFP gateway profile, phone Class of Device, device queries.

Pairing, its agent and radio admission belong to ``SharedBluetoothOwner``.
This module adds and releases the phone-specific profiles and identity:

* an HFP Audio Gateway profile (skipped when another service, such as
  bluez-alsa, already provides one). It never auto-connects to other devices and
  answers only the devices ``accepts`` allows (the chosen car, or anything while
  pairing). While auto-connect is on it stays registered so the car can reach
  the comma the way it reaches a phone when it powers on.
* a temporary smartphone Class of Device (0x00020c major/minor), set only while
  pairing or connecting to the car and restored as soon as the car has handed
  over its Wi-Fi. aa-proxy-rs uses the same temporary identity for head units
  that classify paired devices by class.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from jeepney import DBusAddress, MatchRule, new_error, new_method_call, new_method_return
from jeepney.io.threading import DBusRouter, open_dbus_connection
from jeepney.low_level import HeaderFields, MessageType
from jeepney.wrappers import Properties

from openpilot.starpilot.system.starport import hfp
from openpilot.starpilot.system.starport.sdp import AA_WIRELESS_UUID

BLUEZ = "org.bluez"
PROFILE_MANAGER_IFACE = "org.bluez.ProfileManager1"
PROFILE_IFACE = "org.bluez.Profile1"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
HFP_AG_UUID = "0000111f-0000-1000-8000-00805f9b34fb"
HFP_HF_UUID = "0000111e-0000-1000-8000-00805f9b34fb"  # the car's side; BlueZ's ConnectProfile names the remote role
HFP_PROFILE_PATH = "/link/firestar/starpilot/starport/hfp_ag"
HFP_RETRY_SECONDS = 30.0
PHONE_MAJOR_CLASS = 2   # Phone
PHONE_MINOR_CLASS = 3   # Smartphone (minor field value; 0x00020c as a full CoD)
STRAY_HANDLE_RE = re.compile(r"ACL packet for unknown connection handle (\d+)")
KNOWN_HANDLE_RE = re.compile(r"\bhandle (\d+)\b")
QCA_DEBUG_HANDLE = 3804  # Qualcomm firmware debug packets, never a real link
MAX_ACL_HANDLE = 0x0EFF


def stray_acl_handle(kernel_log: str) -> int | None:
  """The most recent ACL handle the kernel reported data for without knowing the link, or None."""
  handles = [int(value) for value in STRAY_HANDLE_RE.findall(kernel_log)]
  handles = [handle for handle in handles if handle != QCA_DEBUG_HANDLE and handle <= MAX_ACL_HANDLE]
  return handles[-1] if handles else None


def known_acl_handles(hcitool_con: str) -> set[int]:
  return {int(value) for value in KNOWN_HANDLE_RE.findall(hcitool_con)}


def address_from_path(path: str) -> str:
  """``/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF`` -> ``AA:BB:CC:DD:EE:FF``."""
  return path.rsplit("/", 1)[-1].removeprefix("dev_").replace("_", ":").upper()


def unwrap(value: Any) -> Any:
  if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
    return unwrap(value[1])
  if isinstance(value, dict):
    return {key: unwrap(item) for key, item in value.items()}
  if isinstance(value, list):
    return [unwrap(item) for item in value]
  return value


class BluezPhone:
  def __init__(self, log):
    self.log = log
    self.router = DBusRouter(open_dbus_connection(bus="SYSTEM", enable_fds=True))
    self._lock = threading.RLock()
    self._stop = threading.Event()
    self._profile_registered = False
    self._original_class: int | None = None
    self._class_changed = False
    self._bluez_owner = ""
    self._hfp_existing = False
    self._hfp_retry_at = 0.0
    self.accepts: Callable[[str], bool] = lambda address: True  # which devices may open the HFP link
    self.on_connection: Callable[[str], None] = lambda address: None
    self._filter = self.router.filter(MatchRule(type="method_call", path=HFP_PROFILE_PATH), bufsize=16)
    self._queue = self._filter.__enter__()
    self._thread = threading.Thread(target=self._profile_loop, name="aa_hfp_profile", daemon=True)
    self._thread.start()

  # -------------------------------------------------------------------- D-Bus

  def _call(self, path: str, interface: str, member: str, signature: str | None = None, body: tuple = (), timeout: float = 15.0):
    address = DBusAddress(path, bus_name=BLUEZ, interface=interface)
    message = new_method_call(address, member, signature, body) if signature else new_method_call(address, member)
    with self._lock:
      reply = self.router.send_and_get_reply(message, timeout=timeout)
    if reply.header.message_type == MessageType.error:
      name = reply.header.fields.get(HeaderFields.error_name, "org.bluez.Error.Failed")
      raise RuntimeError(f"{name}: {reply.body[0] if reply.body else ''}")
    return reply.body

  def managed_objects(self) -> dict:
    return unwrap(self._call("/", OBJECT_MANAGER, "GetManagedObjects")[0])

  def adapter(self) -> tuple[str, dict]:
    for path, interfaces in self.managed_objects().items():
      if ADAPTER_IFACE in interfaces:
        return path, interfaces[ADAPTER_IFACE]
    raise RuntimeError("Bluetooth adapter is not available")

  @staticmethod
  def _device_info(path: str, props: dict) -> dict:
    address = str(props.get("Address", "")).upper()
    uuids = [str(value).lower() for value in props.get("UUIDs", [])]
    return {"path": path, "address": address, "name": str(props.get("Alias") or props.get("Name") or address),
            "paired": bool(props.get("Paired")), "trusted": bool(props.get("Trusted")),
            "connected": bool(props.get("Connected")), "uuids": uuids, "starport": str(AA_WIRELESS_UUID) in uuids}

  def devices(self) -> list[dict]:
    return [self._device_info(path, interfaces[DEVICE_IFACE])
            for path, interfaces in self.managed_objects().items() if DEVICE_IFACE in interfaces]

  def device(self, address: str) -> dict | None:
    address = address.upper()
    return next((info for info in self.devices() if info["address"] == address), None)

  def snapshot(self, address: str) -> tuple[bool, dict | None]:
    """One BlueZ query: (adapter present and powered, the device at ``address`` or None)."""
    self._check_bluez_restart()
    ready, device = False, None
    for path, interfaces in self.managed_objects().items():
      if ADAPTER_IFACE in interfaces:
        ready = ready or bool(interfaces[ADAPTER_IFACE].get("Powered"))
      if DEVICE_IFACE in interfaces:
        info = self._device_info(path, interfaces[DEVICE_IFACE])
        if info["address"] == address.upper():
          device = info
    return ready, device

  def _check_bluez_restart(self) -> None:
    """A restarted bluetoothd forgets registered profiles; register again afterwards."""
    address = DBusAddress("/org/freedesktop/DBus", bus_name="org.freedesktop.DBus", interface="org.freedesktop.DBus")
    with self._lock:
      reply = self.router.send_and_get_reply(new_method_call(address, "GetNameOwner", "s", (BLUEZ,)), timeout=5.0)
    if reply.header.message_type == MessageType.error:
      raise RuntimeError("BlueZ is not running")
    owner = str(reply.body[0])
    if self._bluez_owner and owner != self._bluez_owner:
      self._profile_registered = self._hfp_existing = False
      self._hfp_retry_at = 0.0
      self.log("bluez_restarted")
    self._bluez_owner = owner

  def connect_device(self, address: str, timeout: float = 20.0) -> None:
    info = self.device(address)
    if info is None:
      raise RuntimeError(f"{address} is not known to Bluetooth; pair the car first")
    if info["connected"]:
      return
    if info['paired'] and str(AA_WIRELESS_UUID) in info['uuids'] and HFP_HF_UUID not in info['uuids']:
      # Wireless adapters can expose only their custom AA service, with no
      # auto-connectable BlueZ profile. Raw SDP/RFCOMM establishes the ACL;
      # generic Device.Connect can stall instead of reaching that service.
      self.log('device_connect_raw_starport', address=info['address'])
      return
    if self._profile_registered and HFP_HF_UUID in info["uuids"]:
      # Only the hands-free link, like a phone: Device.Connect would also bring
      # up every other auto-connect profile BlueZ has for the car (e.g. audio).
      try:
        self._call(info["path"], DEVICE_IFACE, "ConnectProfile", "s", (HFP_HF_UUID,), timeout=timeout)
        return
      except RuntimeError as error:
        self.log("device_connect_warning", error=str(error), profile="hfp")
    try:
      self._call(info["path"], DEVICE_IFACE, "Connect", timeout=timeout)
    except RuntimeError as error:
      # Connect fails when no auto-connect profile matches, but the ACL link a
      # raw SDP/RFCOMM socket needs is created on demand anyway.
      self.log("device_connect_warning", error=str(error))

  # ---------------------------------------------------------- phone identity

  def acquire(self, phone_class: bool = True) -> None:
    self.register_hfp()
    if phone_class:
      self.set_phone_class()

  def release(self) -> None:
    self.unregister_hfp()
    self.restore_class()

  def register_hfp(self) -> None:
    self._stop.clear()
    self._register_hfp()

  def unregister_hfp(self) -> None:
    self._stop.set()  # also closes open HFP links
    if self._profile_registered:
      try:
        self._call("/org/bluez", PROFILE_MANAGER_IFACE, "UnregisterProfile", "o", (HFP_PROFILE_PATH,))
      except RuntimeError as error:
        self.log("hfp_unregister_failed", error=str(error))
      self._profile_registered = False

  @property
  def hfp_registered(self) -> bool:
    return self._profile_registered

  def close(self) -> None:
    self.release()
    try:
      self._queue.put_nowait(None)
    except Exception:
      pass
    self._filter.__exit__(None, None, None)
    self.router.close()
    self._thread.join(timeout=1.0)

  def _register_hfp(self) -> None:
    if self._profile_registered or self._hfp_existing or time.monotonic() < self._hfp_retry_at:
      return
    # AutoConnect stays off: BlueZ would otherwise open this link to every
    # device with a hands-free role (headsets, other gadgets) when it connects.
    options = {"Name": ("s", "StarPilot Hands-Free Gateway"), "RequireAuthentication": ("b", True),
               "RequireAuthorization": ("b", False), "AutoConnect": ("b", False),
               "Version": ("q", hfp.HFP_VERSION), "Features": ("q", hfp.SDP_FEATURES)}
    try:
      self._call("/org/bluez", PROFILE_MANAGER_IFACE, "RegisterProfile", "osa{sv}", (HFP_PROFILE_PATH, HFP_AG_UUID, options))
      self._profile_registered = True
      self.log("hfp_registered")
    except RuntimeError as error:
      if "AlreadyExists" in str(error) or "already" in str(error).lower():
        self._hfp_existing = True
        self.log("hfp_existing_provider", detail=str(error))
      else:
        self._hfp_retry_at = time.monotonic() + HFP_RETRY_SECONDS
        self.log("hfp_register_failed", error=str(error))

  def _profile_loop(self) -> None:
    while True:
      message = self._queue.get()
      if message is None:
        return
      member = message.header.fields.get(HeaderFields.member, "")
      try:
        if member == "NewConnection":
          device_path, descriptor = str(message.body[0]), message.body[1]
          sock = descriptor.to_socket()
          address = address_from_path(device_path)
          if not self.accepts(address):
            sock.close()
            self.log("hfp_refused", device=device_path.rsplit("/", 1)[-1])
            raise RuntimeError("StarPilot answers hands-free only for the chosen car")
          threading.Thread(target=hfp.serve, args=(sock, self._stop, self.log, device_path.rsplit("/", 1)[-1]),
                           name="aa_hfp_conn", daemon=True).start()
          self.log("hfp_connected", device=device_path.rsplit("/", 1)[-1])
          self.on_connection(address)
        self.router.send(new_method_return(message))
      except Exception as error:
        try:
          self.router.send(new_error(message, "org.bluez.Error.Rejected", "s", (str(error),)))
        except Exception:
          pass

  def set_phone_class(self) -> None:
    if self._class_changed:
      return
    try:
      _, props = self.adapter()
      original = int(props.get("Class", 0))
    except RuntimeError:
      return
    if (original >> 8) & 0x1F == PHONE_MAJOR_CLASS:
      return  # already presents as a phone; nothing to change or restore
    if self._run_btmgmt(PHONE_MAJOR_CLASS, PHONE_MINOR_CLASS):
      self._original_class, self._class_changed = original, True
      self.log("phone_class_set", original=f"{original:#08x}")

  def restore_class(self) -> None:
    if not self._class_changed:
      return
    original = self._original_class or 0
    if self._run_btmgmt((original >> 8) & 0x1F, (original >> 2) & 0x3F):
      self._class_changed = False
      self._original_class = None
      self.log("phone_class_restored", value=f"{original:#08x}")

  def _run_btmgmt(self, major: int, minor: int) -> bool:
    commands = []
    if shutil.which("btmgmt"):
      commands.append(["sudo", "-n", "btmgmt", "--index", "0", "class", str(major), str(minor)])
    if shutil.which("hciconfig"):
      commands.append(["sudo", "-n", "hciconfig", "hci0", "class", f"{(major << 8) | (minor << 2):#08x}"])
    for command in commands:
      try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
          return True
        self.log("class_command_failed", command=command[2], stderr=result.stderr.strip()[-200:])
      except (OSError, subprocess.TimeoutExpired) as error:
        self.log("class_command_failed", command=command[2], stderr=str(error))
    if not commands:
      self.log("class_command_unavailable")
    return False

  def disconnect_stray_link(self) -> int | None:
    """Drop a link the controller still holds but the kernel forgot ("ACL packet for unknown connection handle N").

    BlueZ cannot see that link, so only a raw HCI Disconnect reaches it. Returns the handle, or None when there
    was nothing it was safe to drop.
    """
    try:
      kernel = subprocess.run(["journalctl", "-k", "--since=-60s", "-o", "cat", "--no-pager"],
                              capture_output=True, text=True, timeout=5).stdout
      handle = stray_acl_handle(kernel)
      if handle is None:
        self.log("stray_link_not_found")
        return None
      connections = subprocess.run(["hcitool", "con"], capture_output=True, text=True, timeout=5)
      if connections.returncode != 0 or handle in known_acl_handles(connections.stdout):
        self.log("stray_link_skipped", handle=handle, reason="the kernel knows this handle or could not list links")
        return None
      # HCI Disconnect (OGF 0x01, OCF 0x0006): handle little-endian, reason 0x13 (remote user terminated)
      result = subprocess.run(["sudo", "-n", "hcitool", "-i", "hci0", "cmd", "0x01", "0x0006",
                               f"{handle & 0xFF:#04x}", f"{handle >> 8:#04x}", "0x13"],
                              capture_output=True, text=True, timeout=5)
      if result.returncode != 0:
        self.log("stray_link_failed", handle=handle, error=result.stderr.strip()[-200:])
        return None
    except (OSError, subprocess.TimeoutExpired) as error:
      self.log("stray_link_failed", error=str(error))
      return None
    self.log("stray_link_disconnected", handle=handle)
    return handle

  def set_trusted(self, address: str) -> None:
    info = self.device(address)
    if info is None:
      return
    address_obj = DBusAddress(info["path"], bus_name=BLUEZ, interface=DEVICE_IFACE)
    with self._lock:
      self.router.send_and_get_reply(Properties(address_obj).set("Trusted", "b", True), timeout=10.0)
