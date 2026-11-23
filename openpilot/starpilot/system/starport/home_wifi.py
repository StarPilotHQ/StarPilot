"""Rejoin a saved Wi-Fi network that NetworkManager has stopped joining on its own.

2026-11-16 01:36, parked at home: the access point stopped answering association for a minute. NetworkManager
treats three association timeouts as a wrong password, fails the join with 'no-secrets', and from then on leaves
that profile out of autoconnect until someone activates it by hand. The comma then sat in range for 3.9 days
(to 2026-11-19 22:48) without joining and without scanning, while a join from the settings screen took 5 s.

The keeper does what that tap does. While wlan0 sits disconnected with autoconnect on, it asks for a scan now and
then, and when a saved network is in the results it activates the most recently used one. A session that pauses
autoconnect, or any connection attempt under way, keeps it out.

It holds one D-Bus connection for the life of the daemon and replaces it only after an error (see close_router).
"""

from __future__ import annotations

from collections.abc import Callable

from openpilot.starpilot.system.starport.network import (
  CONNECTION_ID, DEVICE_STATE_ACTIVATED, DEVICE_STATE_DISCONNECTED, NM_AP_IFACE, NM_DEVICE_IFACE, NM_IFACE, NM_PATH,
  NM_SETTINGS_CONNECTION_IFACE, NM_WIRELESS_IFACE, NetworkError, NetworkLease, _boottime, close_router,
)

NM_SETTINGS_PATH = "/org/freedesktop/NetworkManager/Settings"
NM_SETTINGS_IFACE = "org.freedesktop.NetworkManager.Settings"
CHECK_S = 30.0        # how often the device state is read
GRACE_S = 45.0        # NetworkManager's own autoconnect gets this long after a disconnect before the keeper steps in
SCAN_WAIT_S = 10.0    # a requested scan is read this much later (2026-11-19: scans took 2.5-5 s)
SCAN_FRESH_S = 60.0   # results older than this are not trusted; the keeper scans again
SCAN_GAP_S = 60.0     # between scans that found no saved network, doubling up to SCAN_GAP_MAX_S
SCAN_GAP_MAX_S = 300.0
RETRY_S = 60.0        # between joins that did not hold, doubling up to RETRY_MAX_S
RETRY_MAX_S = 600.0


def _value(settings: dict, section: str, key: str, default=None):
  entry = settings.get(section, {}).get(key)
  return entry[1] if entry is not None else default


def candidate(settings: dict, interface: str) -> tuple[int, bytes] | None:
  """(last used, SSID) for a saved profile NetworkManager would autoconnect on ``interface``, else None."""
  if _value(settings, "connection", "type") != "802-11-wireless" or _value(settings, "connection", "id") == CONNECTION_ID:
    return None
  if not _value(settings, "connection", "autoconnect", True):
    return None
  if _value(settings, "connection", "interface-name", interface) not in ("", interface):
    return None
  if _value(settings, "802-11-wireless", "mode", "infrastructure") != "infrastructure":
    return None  # the comma's own hotspot
  ssid = bytes(_value(settings, "802-11-wireless", "ssid", b""))
  return (int(_value(settings, "connection", "timestamp", 0)), ssid) if ssid else None


def choose(saved: list[tuple[int, bytes, str]], visible: set[bytes]) -> str:
  """The profile path of the most recently used saved network in range, or ""."""
  in_range = [entry for entry in saved if entry[1] in visible]
  return max(in_range, key=lambda entry: entry[0])[2] if in_range else ""


class HomeWifiKeeper:
  def __init__(self, log: Callable[..., None], interface: str = "wlan0",
               lease: Callable[..., NetworkLease] = NetworkLease, boottime: Callable[[], float] = _boottime):
    self.log = log
    self.interface = interface
    self._lease = lease
    self._boottime = boottime
    self._held: NetworkLease | None = None  # one connection, kept between checks
    self._check_at = 0.0
    self._idle_since: float | None = None
    self._attempt_at = 0.0
    self._retry = RETRY_S
    self._scan_at = 0.0
    self._scan_gap = SCAN_GAP_S
    self._judged_scan = -1
    self._last_error = ""

  def step(self, now: float) -> None:
    if now < self._check_at:
      return
    self._check_at = now + CHECK_S
    try:
      if self._held is None:
        self._held = self._lease(self.log, self.interface)
      self._step(self._held, now)
      self._last_error = ""
    except (NetworkError, OSError) as error:
      self.close()  # the next check starts on a new connection
      if str(error) != self._last_error:  # a comma without NetworkManager would otherwise log this every check
        self._last_error = str(error)
        self.log("home_wifi_check_failed", error=str(error))

  def close(self) -> None:
    held, self._held = self._held, None
    if held is not None and held.router is not None:
      try:
        close_router(held.router)
      except OSError:
        pass

  def _step(self, lease: NetworkLease, now: float) -> None:
    device = next((path for path, name in lease._wifi_devices() if name == self.interface), "")
    if not device:
      return
    state = int(lease._get(device, NM_DEVICE_IFACE, "State"))
    if state != DEVICE_STATE_DISCONNECTED or not lease._get(device, NM_DEVICE_IFACE, "Autoconnect"):
      self._idle_since = None
      if state == DEVICE_STATE_ACTIVATED:
        self._retry, self._attempt_at, self._scan_gap, self._scan_at = RETRY_S, 0.0, SCAN_GAP_S, 0.0
      return
    if self._idle_since is None:
      self._idle_since = now
    if now - self._idle_since < GRACE_S or now < self._attempt_at:
      return
    last_scan = int(lease._get(device, NM_WIRELESS_IFACE, "LastScan"))  # ms on the boot clock, -1 before the first scan
    if last_scan < 0 or self._boottime() - last_scan / 1000.0 > SCAN_FRESH_S:
      if now >= self._scan_at:
        lease._call(device, NM_WIRELESS_IFACE, "RequestScan", "a{sv}", ({},))
        self._scan_at = now + self._scan_gap
        self._check_at = now + SCAN_WAIT_S
      return
    if last_scan == self._judged_scan:
      return  # these results already had no saved network in them, or the join they led to is still being given time
    self._judged_scan = last_scan
    profile = choose(self._saved(lease), self._visible(lease, device))
    if not profile:
      self._scan_gap = min(self._scan_gap * 2, SCAN_GAP_MAX_S)
      self._scan_at = now + self._scan_gap
      return
    lease._call(NM_PATH, NM_IFACE, "ActivateConnection", "ooo", (profile, device, "/"))
    self.log("home_wifi_rejoin", interface=self.interface, idle_s=round(now - self._idle_since, 1), retry_s=self._retry)
    self._attempt_at = now + self._retry
    self._retry = min(self._retry * 2, RETRY_MAX_S)

  def _saved(self, lease: NetworkLease) -> list[tuple[int, bytes, str]]:
    saved = []
    for path in lease._call(NM_SETTINGS_PATH, NM_SETTINGS_IFACE, "ListConnections")[0]:
      try:
        found = candidate(lease._call(path, NM_SETTINGS_CONNECTION_IFACE, "GetSettings")[0], self.interface)
      except NetworkError:
        continue  # deleted meanwhile
      if found is not None:
        saved.append((found[0], found[1], path))
    return saved

  def _visible(self, lease: NetworkLease, device: str) -> set[bytes]:
    visible = set()
    for path in lease._call(device, NM_WIRELESS_IFACE, "GetAllAccessPoints")[0]:
      try:
        seen = int(lease._get(path, NM_AP_IFACE, "LastSeen"))  # s on the boot clock, -1 when never
        if seen >= 0 and self._boottime() - seen <= SCAN_FRESH_S + SCAN_WAIT_S:
          visible.add(bytes(lease._get(path, NM_AP_IFACE, "Ssid")))
      except NetworkError:
        continue  # aged out meanwhile
    return visible
