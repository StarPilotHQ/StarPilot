from openpilot.starpilot.system.starport import home_wifi, network, supervisor
from openpilot.starpilot.system.starport.home_wifi import HomeWifiKeeper, candidate, choose

DEVICE = "/dev/wlan0"


def profile(ssid, timestamp=0, **connection):
  settings = {"connection": {"type": ("s", "802-11-wireless"), "id": ("s", f"openpilot connection {ssid}"), "timestamp": ("t", timestamp)},
              "802-11-wireless": {"ssid": ("ay", ssid.encode()), "mode": ("s", "infrastructure")}}
  for key, value in connection.items():
    settings["connection"][key.replace("_", "-")] = value
  return settings


class FakeLease:
  """NetworkManager as the keeper sees it: one Wi-Fi device, saved profiles, and access points with ages in seconds."""

  def __init__(self, world):
    self.world, self.router = world, None

  def _wifi_devices(self):
    if self.world.failing:
      raise network.NetworkError("NetworkManager did not answer")
    return [(DEVICE, "wlan0")]

  def _get(self, path, interface, name):
    world = self.world
    if path == DEVICE:
      return {"State": world.state, "Autoconnect": world.autoconnect,
              "LastScan": -1 if world.scanned_at is None else int(world.scanned_at * 1000)}[name]
    ssid, seen = world.access_points[path]
    return {"Ssid": ssid.encode(), "LastSeen": int(seen)}[name]

  def _call(self, path, interface, member, signature=None, body=(), timeout=10.0):
    world = self.world
    world.calls.append(member)
    if member == "RequestScan":
      world.scanned_at = world.clock + 3  # results land before the keeper looks again
      world.access_points = {f"/ap/{index}": (ssid, world.scanned_at) for index, ssid in enumerate(world.in_range)}
      return ()
    if member == "ListConnections":
      return (list(world.profiles),)
    if member == "GetSettings":
      return (world.profiles[path],)
    if member == "GetAllAccessPoints":
      return (list(world.access_points),)
    if member == "ActivateConnection":
      world.activated.append(body[0])
      return ("/active/1",)
    raise AssertionError(member)


class World:
  def __init__(self, in_range=("RayNetOutside",)):
    self.clock = 1000.0
    self.state, self.autoconnect = network.DEVICE_STATE_DISCONNECTED, True
    self.scanned_at = None
    self.failing = False
    self.in_range = list(in_range)
    self.access_points = {}
    self.profiles = {"/settings/1": profile("RayNetOutside", 50), "/settings/2": profile("Fantasia-5G", 40)}
    self.calls, self.activated, self.events = [], [], []
    self.keeper = HomeWifiKeeper(lambda event, **fields: self.events.append(event), lease=lambda log, interface: FakeLease(self),
                                 boottime=lambda: self.clock)

  def run(self, seconds):
    for _ in range(int(seconds)):
      self.clock += 1
      self.keeper.step(self.clock)


def test_candidate_takes_only_saved_networks_networkmanager_would_join():
  assert candidate(profile("Home", 7), "wlan0") == (7, b"Home")
  assert candidate(profile("Home", autoconnect=("b", False)), "wlan0") is None
  assert candidate(profile("Home", id=("s", network.CONNECTION_ID)), "wlan0") is None  # the car's projection network
  assert candidate(profile("Home", interface_name=("s", "aa0")), "wlan0") is None
  assert candidate(profile("Home", interface_name=("s", "wlan0")), "wlan0") == (0, b"Home")
  hotspot = profile("weedle")
  hotspot["802-11-wireless"]["mode"] = ("s", "ap")
  assert candidate(hotspot, "wlan0") is None
  assert candidate({"connection": {"type": ("s", "gsm")}}, "wlan0") is None


def test_choose_prefers_the_most_recently_used_network_in_range():
  saved = [(40, b"A", "/a"), (50, b"B", "/b"), (60, b"C", "/c")]
  assert choose(saved, {b"A", b"B"}) == "/b"
  assert choose(saved, {b"Other"}) == ""


def test_rejoins_a_saved_network_networkmanager_left_alone():
  world = World()
  world.run(home_wifi.GRACE_S - 1)
  assert world.calls == []  # NetworkManager's own autoconnect goes first
  world.run(60)
  assert world.activated == ["/settings/1"] and world.events == ["home_wifi_rejoin"]
  assert world.calls.count("RequestScan") == 1


def test_a_join_that_does_not_hold_is_retried_with_a_growing_gap():
  world = World()
  world.run(3600)
  assert 6 <= len(world.activated) <= 10  # 60, 120, 240, 480 s, then every 600 s: not one per check
  world.state = network.DEVICE_STATE_ACTIVATED
  world.run(60)
  world.state, before = network.DEVICE_STATE_DISCONNECTED, len(world.activated)
  world.run(110)
  assert len(world.activated) == before + 1  # connected once, so the gap starts over


def test_keeps_out_while_autoconnect_is_paused_or_a_join_is_under_way():
  for change in ({"autoconnect": False}, {"state": network.DEVICE_STATE_PREPARE}, {"state": network.DEVICE_STATE_ACTIVATED}):
    world = World()
    for name, value in change.items():
      setattr(world, name, value)
    world.run(600)
    assert world.calls == [] and world.activated == []


def test_scans_less_often_away_from_every_saved_network():
  world = World(in_range=("CoffeeShop",))
  world.run(3600)
  assert world.activated == []
  assert 10 <= world.calls.count("RequestScan") <= 16  # 60 s apart at first, 300 s once nothing turns up
  world.in_range = ["Fantasia-5G"]
  world.run(400)
  assert world.activated == ["/settings/2"]


def test_a_dbus_failure_is_logged_once_and_checked_again():
  world = World()

  def broken(log, interface):
    raise FileNotFoundError("no system bus")
  world.keeper._lease = broken
  world.run(300)
  assert world.events == ["home_wifi_check_failed"]
  world.keeper._lease = lambda log, interface: FakeLease(world)
  world.run(120)
  assert world.activated == ["/settings/1"]


def test_holds_one_connection_and_replaces_it_only_after_an_error():
  world, opened = World(), []

  def lease(log, interface):
    opened.append(1)
    return FakeLease(world)
  world.keeper._lease = lease
  world.run(3600)
  assert len(opened) == 1  # 2026-11-20: one per check ran the daemon out of file descriptors in 4.2 h
  world.failing = True
  world.run(60)
  world.failing = False
  world.run(60)
  assert len(opened) >= 2 and world.events.count("home_wifi_check_failed") == 1


def test_closing_a_router_leaves_no_descriptor_behind():
  import os
  import socket
  from jeepney.io.threading import DBusConnection, DBusRouter

  def cycle():
    ours, theirs = socket.socketpair()
    router = DBusRouter(DBusConnection(ours))
    network.close_router(router)
    theirs.close()

  cycle()  # anything allocated once is counted in the baseline
  before = len(os.listdir("/proc/self/fd"))
  for _ in range(50):
    cycle()
  assert len(os.listdir("/proc/self/fd")) == before


def test_supervisor_keeps_home_wifi_only_between_sessions():
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  steps = []
  sup.log = lambda *args, **kwargs: None  # ty: ignore[invalid-assignment]  # test double
  sup._home_wifi = type("Keeper", (), {"step": lambda self, now: steps.append(now)})()  # ty: ignore[invalid-assignment]  # test double
  sup._session_alive = lambda: True  # ty: ignore[invalid-assignment]  # test double
  sup.keep_home_wifi(1.0)
  sup._session_alive = lambda: False  # ty: ignore[invalid-assignment]  # test double
  sup.keep_home_wifi(2.0)
  assert steps == [2.0]
