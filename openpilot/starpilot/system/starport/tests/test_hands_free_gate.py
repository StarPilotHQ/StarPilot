"""2026-11-02 Honda/OTTOCAST, 43 projections: the car grants its screen only once its own hands-free link to the comma is
up; the first Wi-Fi join and the adapter's handshake restart cost the first attempt; cold boots started before
NetworkManager managed the radio. Each piece here is one of those four findings."""

import socket
import threading
import time
from types import SimpleNamespace

import pytest

from openpilot.starpilot.system.starport import bluez_phone, bootstrap as bs, car_profiles, network, supervisor
from openpilot.starpilot.system.starport.tests.test_starport import FakeNetworkManager, credentials, make_lease
from openpilot.starpilot.system.starport.tests.test_stream_focus import Session, Source, Watch

CAR = "02:00:00:00:00:01"
BSSID = "AA:BB:CC:DD:EE:FF"


# ------------------------------------------------------------------ fast first join: the remembered channel

def test_first_join_pins_the_remembered_channel_and_keeps_the_profile_hidden():
  nm = FakeNetworkManager()
  lease = make_lease(nm)
  events = []
  lease.log = lambda name, **values: events.append((name, values))
  saved = {}
  lease.channel_memory = network.ChannelMemory(lambda: {BSSID: 5180}, saved.update)
  assert lease.acquire(credentials(), timeout=2.0) == "192.168.50.23"
  wireless = nm.added[0]["802-11-wireless"]
  assert (wireless["band"], wireless["channel"], wireless["hidden"]) == (("s", "a"), ("u", 36), ("b", True))
  joining = next(values for name, values in events if name == "wifi_joining")
  assert (joining["pinned_freq"], joining["pinned_source"]) == (5180, "remembered")
  assert saved == {BSSID: 5745}  # where the access point actually was is what the next session pins


def test_a_remembered_channel_that_fails_is_forgotten_before_the_retry_scans():
  nm = FakeNetworkManager(fail_with_bssid=True)
  lease = make_lease(nm)
  events = []
  lease.log = lambda name, **values: events.append((name, values))
  memory = {BSSID: 5180}
  lease.channel_memory = network.ChannelMemory(lambda: dict(memory), lambda channels: (memory.clear(), memory.update(channels)))
  assert lease.acquire(credentials(), timeout=2.0) == "192.168.50.23"
  assert "channel" in nm.added[0]["802-11-wireless"] and "bssid" not in nm.added[1]["802-11-wireless"]
  assert memory == {} and [name for name, _ in events if name == "wifi_channel_forgotten"] == ["wifi_channel_forgotten"]


def test_without_a_memory_or_a_sighting_the_join_scans_as_before():
  nm = FakeNetworkManager()
  lease = make_lease(nm)
  events = []
  lease.log = lambda name, **values: events.append((name, values))
  lease.acquire(credentials(), timeout=2.0)
  wireless = nm.added[0]["802-11-wireless"]
  assert "channel" not in wireless and wireless["hidden"] == ("b", True)
  assert next(values for name, values in events if name == "wifi_joining")["pinned_source"] == ""


def test_channel_memory_is_tolerant_and_saves_only_changes():
  saves = []
  memory = network.ChannelMemory(lambda: {BSSID: 5180, "bad": "x"}, saves.append)  # keys are stored upper-case
  assert memory.frequency(BSSID) == 5180 and memory.frequency("bad") == 0 and memory.frequency("none") == 0
  memory.remember(BSSID, 5180)  # unchanged
  memory.remember("none", 0)  # nothing to forget
  assert saves == []
  memory.remember(BSSID, 5200)
  memory.remember(BSSID, 0)
  assert saves == [{BSSID: 5200, "bad": "x"}, {"bad": "x"}]


# ------------------------------------------------------------------ fast first join: the remembered address
# 2026-11-13 OTTOCAST, sessions 37-56: the join took 6.2 s at the median on a pinned channel, and the dongle leased the
# same address every time. Assume it, check the car answers from it, and ask the access point only when it does not.

class LeasingNetworkManager(FakeNetworkManager):
  """An access point that leases 192.168.50.23/24; a profile that brings its own address keeps that one."""

  def get(self, path, interface, name):
    if name == "AddressData":
      ipv4 = self.added[-1]["ipv4"]
      if ipv4["method"] == ("s", "manual"):
        return [dict(ipv4["address-data"][1][0])]
      return [{"address": ("s", "192.168.50.23"), "prefix": ("u", 24)}]
    return super().get(path, interface, name)


def _address_lease(memory, answers=True, peer="192.168.50.1"):
  nm = LeasingNetworkManager()
  lease = make_lease(nm)
  events = []
  lease.log = lambda name, **values: events.append((name, values))
  lease.address_memory = network.AddressMemory(lambda: dict(memory), lambda addresses: (memory.clear(), memory.update(addresses)))
  lease._peer_answers = lambda cancelled: answers
  lease.peer = peer   # the supervisor sets this from the car's projection address before the join
  return nm, lease, events


def test_a_join_the_access_point_answered_remembers_its_lease():
  memory = {}
  nm, lease, events = _address_lease(memory)
  assert lease.acquire(credentials(), timeout=2.0) == "192.168.50.23"
  assert nm.added[0]["ipv4"]["method"] == ("s", "auto") and memory == {BSSID: "192.168.50.23/24"}
  assert "address_source" not in next(values for name, values in events if name == "wifi_joined")
  lease.release(restore=False)
  assert memory == {BSSID: "192.168.50.23/24"}, "a leased address needs no confirmation"


def test_the_next_join_assumes_the_remembered_address_and_skips_dhcp():
  memory = {BSSID: "192.168.50.99/24"}
  nm, lease, events = _address_lease(memory)
  assert lease.acquire(credentials(), timeout=2.0) == "192.168.50.99"
  ipv4 = nm.added[0]["ipv4"]
  assert ipv4["method"] == ("s", "manual") and len(nm.added) == 1
  assert ipv4["address-data"] == ("aa{sv}", [{"address": ("s", "192.168.50.99"), "prefix": ("u", 24)}])
  assert ipv4["never-default"] == ("b", True), "the car's network still never takes the Internet route"
  assert next(values for name, values in events if name == "wifi_joining")["address_source"] == "remembered"
  assert next(values for name, values in events if name == "wifi_joined")["address_source"] == "remembered"
  lease.confirm_address()   # the car accepted the projection connection from it
  lease.release(restore=False)
  assert memory == {BSSID: "192.168.50.99/24"}


def test_an_assumed_address_the_car_does_not_answer_from_is_forgotten_and_dhcp_takes_over():
  memory = {BSSID: "192.168.50.99/24"}
  nm, lease, events = _address_lease(memory, answers=False)
  assert lease.acquire(credentials(), timeout=2.0) == "192.168.50.23"
  assert [settings["ipv4"]["method"] for settings in nm.added] == [("s", "manual"), ("s", "auto")]
  assert "DeactivateConnection" in nm.calls, "the assumed join is taken down before the access point is asked"
  forgotten = next(values for name, values in events if name == "wifi_address_forgotten")
  assert forgotten["address"] == "192.168.50.99" and "did not answer" in forgotten["reason"]
  assert memory == {BSSID: "192.168.50.23/24"}, "what the access point leased is what the next session assumes"


def test_no_address_is_assumed_without_the_cars_own_address_or_off_its_network():
  # Unknown where the car is: just ask the access point. Known, and not on the remembered network (or at the
  # remembered address itself): that memory is of some other network, so it goes.
  for peer, forgotten in (("", False), ("not an address", False), ("10.0.0.5", True), ("192.168.50.99", True)):
    memory = {BSSID: "192.168.50.99/24"}
    nm, lease, events = _address_lease(memory, peer=peer)
    lease.acquire(credentials(), timeout=2.0)
    assert nm.added[0]["ipv4"]["method"] == ("s", "auto") and len(nm.added) == 1, peer
    reasons = [values["reason"] for name, values in events if name == "wifi_address_forgotten"]
    assert reasons == ([f"the car is at {peer} now"] if forgotten else []), peer


def test_an_assumed_address_the_car_took_no_connection_from_is_forgotten_at_release():
  memory = {BSSID: "192.168.50.99/24"}
  nm, lease, events = _address_lease(memory)
  lease.acquire(credentials(), timeout=2.0)
  lease.release(restore=False)   # the attempt ended without the car accepting the projection connection
  assert memory == {}
  assert next(values for name, values in events if name == "wifi_address_forgotten")["reason"] == "the car accepted no connection from it"
  lease.release(restore=True)
  assert [name for name, _ in events].count("wifi_address_forgotten") == 1


def test_a_lease_without_an_address_memory_joins_as_before():
  nm = LeasingNetworkManager()
  lease = make_lease(nm)
  lease.peer = "192.168.50.1"
  assert lease.acquire(credentials(), timeout=2.0) == "192.168.50.23"
  assert nm.added[0]["ipv4"]["method"] == ("s", "auto") and "address-data" not in nm.added[0]["ipv4"]
  lease.confirm_address()
  lease.release(restore=False)


def test_address_memory_is_tolerant_and_saves_only_changes():
  saves = []
  stored = {BSSID: "192.168.1.102/24", "V6": "fe80::1/64", "BAD": "x", "WIDE": "10.0.0.1/4", "NUM": 5}
  memory = network.AddressMemory(lambda: stored, saves.append)
  assert memory.address(BSSID.lower()) == ("192.168.1.102", 24)
  assert all(memory.address(key) is None for key in ("V6", "BAD", "WIDE", "NUM", "none"))
  memory.remember(BSSID, "192.168.1.102", 24)   # unchanged
  memory.remember("none")                       # nothing to forget
  assert saves == []
  memory.remember(BSSID, "192.168.1.103", 24)
  assert saves[-1][BSSID] == "192.168.1.103/24"
  memory.remember(BSSID)
  assert BSSID not in saves[-1]


def test_the_kernels_arp_table_says_whether_the_car_answered(tmp_path):
  table = tmp_path / "arp"
  table.write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                   "192.168.1.101    0x1         0x2         02:00:00:00:00:0a     *        wlan0\n"
                   "192.168.1.150    0x1         0x0         00:00:00:00:00:00     *        wlan0\n"
                   "10.0.0.1         0x1         0x2         02:00:00:00:00:0b     *        aa0\n")
  assert network.arp_answered("192.168.1.101", "wlan0", str(table))
  assert not network.arp_answered("192.168.1.150", "wlan0", str(table)), "asked, never answered"
  assert not network.arp_answered("10.0.0.1", "wlan0", str(table)), "another interface's neighbour"
  assert not network.arp_answered("192.168.1.101", "wlan0", str(tmp_path / "missing"))


def test_the_join_is_told_where_the_car_is_only_for_a_profile_that_remembers_addresses():
  class Lease:   # the join's signature is unchanged: every lease implementation still takes it
    peer = "unset"

    def acquire(self, credentials, cancelled=None, **options):
      self.joined = (credentials, cancelled, options)
      return "192.168.1.102"

  boot = SimpleNamespace(endpoint=bs.Endpoint("192.168.1.101", 5288), join_is_cancelled=lambda: False)
  join = supervisor.Supervisor._join
  lease = Lease()
  assert join(lease, boot, "credentials", {**car_profiles.DEFAULTS, "remember_wifi_address": True}) == "192.168.1.102"
  assert lease.peer == "192.168.1.101" and lease.joined == ("credentials", boot.join_is_cancelled, {})
  lease = Lease()
  join(lease, boot, "credentials", {**car_profiles.DEFAULTS, "dhcp_patience_s": 6.0})
  # A profile with DHCP patience also hands over where the adapter is, for the made-up address the join falls back to.
  options = lease.joined[2]
  assert lease.peer == "unset" and (options["dhcp_patience"], options["static_first"]) == (6.0, False)
  boot.endpoint_ip = "192.168.1.101"
  assert options["endpoint_ip"]() == "192.168.1.101"
  lease = Lease()
  join(lease, boot, "credentials", {**car_profiles.DEFAULTS, "static_ip": True})
  assert lease.joined[2]["static_first"] is True and lease.joined[2]["dhcp_patience"] == 0.0
  lease = Lease()
  join(lease, SimpleNamespace(endpoint=None, join_is_cancelled=lambda: False), "credentials",
       {**car_profiles.DEFAULTS, "remember_wifi_address": True})
  assert lease.peer == "unset"
  ottocast = next(rule for rule in car_profiles.RULES if rule["id"] == "ottocast")
  assert ottocast["tuning"]["remember_wifi_address"] is True and car_profiles.DEFAULTS["remember_wifi_address"] is False


# ------------------------------------------------------------------ fast first join: the adapter's handshake restart

def _frame(message_id: int, payload: bytes) -> bytes:
  return bs.encode_frame(message_id, payload)


def test_keepalive_hands_a_handshake_restart_to_the_supervisor_during_setup():
  phone, car = socket.socketpair()
  events = []
  boot = bs.WirelessBootstrap(phone, lambda name, **values: events.append(name), stage_timeout=1.0)
  boot.on_restart = lambda: True
  stop = threading.Event()
  thread = threading.Thread(target=boot.keepalive, args=(stop,), daemon=True)
  thread.start()
  car.sendall(_frame(bs.WIFI_VERSION_REQUEST, b"\x08\x01"))
  thread.join(3.0)
  try:
    assert not thread.is_alive() and boot.restarted.is_set() and boot.restart_payload == b"\x08\x01"
    assert not boot.link_closed.is_set() and "bootstrap_restart_during_setup" in events
  finally:
    stop.set()
    phone.close()
    car.close()


def test_keepalive_ignores_a_handshake_restart_once_the_session_is_up():
  phone, car = socket.socketpair()
  events = []
  boot = bs.WirelessBootstrap(phone, lambda name, **values: events.append(name), stage_timeout=1.0)
  stop = threading.Event()
  thread = threading.Thread(target=boot.keepalive, args=(stop,), daemon=True)
  thread.start()
  car.sendall(_frame(bs.WIFI_VERSION_REQUEST, b"\x08\x01"))
  deadline = time.monotonic() + 2.0
  while "bootstrap_ignored" not in events and time.monotonic() < deadline:
    time.sleep(0.01)
  try:
    assert thread.is_alive() and not boot.restarted.is_set() and "bootstrap_ignored" in events
  finally:
    stop.set()
    phone.close()
    car.close()
    thread.join(2.0)


class FakeBoot:
  def __init__(self):
    self.restarted = threading.Event()
    self.link_closed = threading.Event()
    self.restart_payload = b""
    self.queue: list = []
    self.runs = 0
    self.on_restart = lambda: False

  def keepalive(self, stop):
    stop.wait(5.0)

  def run(self, join_wifi, cancelled=None):
    self.runs += 1
    return SimpleNamespace(head_unit={"restarted": self.runs})

  def join_is_cancelled(self):
    return False


def bare_supervisor(events):
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  sup.log = lambda name, **values: events.append((name, values))
  sup._stop = threading.Event()
  sup._status = {"state": "", "detail": "", "last_stage": "authenticating"}
  sup._set = lambda **values: sup._status.update(values)
  sup._lock = threading.RLock()
  sup._sockets = set()
  sup._setup_sock = None
  sup._setup_phase = False
  sup._companion_keeper = None
  return sup


def test_a_handshake_restart_during_setup_is_answered_and_the_connection_made_again():
  events = []
  sup = bare_supervisor(events)
  boot = FakeBoot()
  projections = []

  def project(result, lease, ident, link_lost):
    projections.append(result.head_unit)
    if len(projections) == 1:
      assert boot.on_restart() is True  # still setting up: the keepalive may take the adapter's new request
      boot.restart_payload = b"\x08\x01"
      boot.restarted.set()
      raise EOFError("Head unit disconnected after 0/4 bytes")
    assert sup._setup_phase and not boot.restarted.is_set()

  sup._project = project
  tuning = {**car_profiles.DEFAULTS, "setup_restarts": 2, "tcp_stops_with_rfcomm": True}
  lease = SimpleNamespace(acquire=lambda c, cancelled=None: "192.168.1.102")
  sup._project_with_restarts(boot, SimpleNamespace(head_unit={}), lease, None, tuning)
  assert projections == [{}, {"restarted": 1}] and boot.runs == 1
  assert boot.queue == [(bs.WIFI_VERSION_REQUEST, b"\x08\x01")]  # the new request is answered first, as the first one was
  assert [name for name, _ in events] == ["bootstrap_restarted_during_setup"] and not sup._setup_phase


def test_setup_restarts_stay_off_unless_the_car_profile_asks():
  events = []
  sup = bare_supervisor(events)
  boot = FakeBoot()

  def project(result, lease, ident):  # as every other adapter: no restart handling, no link watch
    assert boot.on_restart() is False
    boot.restarted.set()
    raise EOFError("Head unit disconnected after 0/4 bytes")

  sup._project = project
  with pytest.raises(EOFError):
    sup._project_with_restarts(boot, SimpleNamespace(head_unit={}), None, None, dict(car_profiles.DEFAULTS))
  assert boot.runs == 0 and events == []


def test_a_restart_past_the_setup_phase_is_not_taken():
  events = []
  sup = bare_supervisor(events)
  boot = FakeBoot()

  def project(result, lease, ident, link_lost):
    sup._setup_phase = False  # session.start() finished: the car is projecting
    assert boot.on_restart() is False
    boot.restarted.set()
    raise RuntimeError("Head unit stopped answering for 15 s")

  sup._project = project
  with pytest.raises(RuntimeError):
    sup._project_with_restarts(boot, SimpleNamespace(head_unit={}), None, None, {**car_profiles.DEFAULTS, "setup_restarts": 2})
  assert boot.runs == 0


def test_the_dead_setup_socket_is_shut_down_when_a_restart_is_taken():
  sup = bare_supervisor([])
  boot = FakeBoot()
  a, b = socket.socketpair()
  sup._track(a)

  def project(result, lease, ident, link_lost):
    if result.head_unit:
      return  # the second connection, after the restart
    sup._setup_sock = a
    assert boot.on_restart() is True
    assert b.recv(1) == b""  # the other end sees the shutdown at once
    boot.restarted.set()
    raise EOFError("closed")

  sup._project = project
  lease = SimpleNamespace(acquire=lambda c, cancelled=None: "ip")
  sup._project_with_restarts(boot, SimpleNamespace(head_unit={}), lease, None, {**car_profiles.DEFAULTS, "setup_restarts": 1})
  assert sup._setup_sock is None and a not in sup._sockets and boot.runs == 1
  b.close()


# ------------------------------------------------------------------ the hands-free gate

def test_companion_link_keeper_retries_until_the_link_is_up_then_rests():
  link = {"up": False}
  events = []
  outcomes = iter(["org.bluez.Error.Failed: br-connection-refused", "connected"])

  def connect(timeout):
    outcome = next(outcomes)
    link["up"] = outcome == "connected"
    return outcome

  keeper = supervisor.CompanionLinkKeeper(connect, lambda: link["up"], lambda name, **values: events.append((name, values)),
                                          interval=0.03, timeout=1.0).start()
  deadline = time.monotonic() + 2.0
  while keeper.attempts < 2 and time.monotonic() < deadline:
    time.sleep(0.005)
  time.sleep(0.1)  # with the link up it rests instead of connecting again
  keeper.stop()
  assert keeper.attempts == 2
  assert [values["outcome"] for _, values in events] == ["org.bluez.Error.Failed: br-connection-refused", "connected"]
  assert [values["up"] for _, values in events] == [False, True]


def test_companion_link_keeper_survives_a_connect_that_raises():
  events = []

  def connect(timeout):
    raise RuntimeError("D-Bus went away")

  keeper = supervisor.CompanionLinkKeeper(connect, lambda: False, lambda name, **values: events.append(values), interval=0.02).start()
  deadline = time.monotonic() + 2.0
  while keeper.attempts < 2 and time.monotonic() < deadline:
    time.sleep(0.005)
  keeper.stop()
  assert keeper.attempts >= 2 and events[0]["outcome"] == "RuntimeError: D-Bus went away"


def test_companion_link_keeper_waits_for_the_projection_to_stream_before_paging():
  # 2026-11-08 session 38: the first page held the phone's D-Bus lock through connecting_tcp -> authenticating (8.0 s).
  ready = {"streaming": False}
  calls = []
  keeper = supervisor.CompanionLinkKeeper(lambda timeout: calls.append(timeout) or "connected", lambda: bool(calls),
                                          lambda name, **values: None, interval=0.02, ready=lambda: ready["streaming"],
                                          ready_poll=0.01).start()
  time.sleep(0.1)
  assert calls == [] and keeper.attempts == 0
  ready["streaming"] = True
  deadline = time.monotonic() + 2.0
  while keeper.attempts < 1 and time.monotonic() < deadline:
    time.sleep(0.005)
  keeper.stop()
  assert keeper.attempts == 1 and calls == [supervisor.COMPANION_CONNECT_TIMEOUT_S]


def test_companion_link_keeper_rests_after_a_page_the_car_did_not_answer():
  # 2026-11-08: TimeoutError / br-connection-busy alternated every 10 s, so the controller paged 8 s of every 10 and the
  # car's own hands-free connection (the one that grants the screen) landed only in the one idle window.
  events = []
  outcomes = iter(["TimeoutError", "org.bluez.Error.InProgress: br-connection-busy", "org.bluez.Error.Failed: br-connection-refused"])
  keeper = supervisor.CompanionLinkKeeper(lambda timeout: next(outcomes), lambda: False,
                                          lambda name, **values: events.append(values), interval=0.02, backoff=0.2).start()
  deadline = time.monotonic() + 3.0
  while keeper.attempts < 3 and time.monotonic() < deadline:
    time.sleep(0.005)
  keeper.stop()
  assert [values["next_s"] for values in events[:3]] == [0.2, 0.2, 0.0]  # paged, still paging, answered: rest, rest, retry
  assert all(supervisor.CompanionLinkKeeper.paged(marker) for marker in ("TimeoutError: x", "br-connection-busy", "br-connection-page-timeout"))
  assert not supervisor.CompanionLinkKeeper.paged("org.bluez.Error.Failed: br-connection-refused")


# ------------------------------------------------------------------ a link from the car that carries nothing
# 2026-11-13, sessions 55 and 56 (controller trace): the Honda's link came up at ignition with no authentication and no
# hands-free; the Honda dropped it after 68.2 s and its next call, 2-3 s later, brought hands-free and the screen.

def _silent_keeper(link, events, *, ready=None, limit=3, drop_outcome="dropped", **timing):
  calls = {"drop": 0, "connect": 0}

  def drop():
    calls["drop"] += 1
    if drop_outcome == "dropped":
      link["since"] = None   # hung up; the car has not called back yet
    return drop_outcome

  def connect(timeout):
    calls["connect"] += 1
    return "org.bluez.Error.Failed: br-connection-refused"

  def silent_for():
    return None if link["since"] is None else time.monotonic() - link["since"]

  options = {"interval": 0.05, "silent_after": 0.15, "redial_wait": 0.2, "silent_poll": 0.02, "ready_poll": 0.01, **timing}
  keeper = supervisor.CompanionLinkKeeper(connect, lambda: link["up"], lambda name, **values: events.append((name, values)),
                                          ready=ready, silent_for=silent_for, drop=drop, recycle_limit=limit, **options)
  return keeper, calls


def _until(condition, seconds=3.0):
  deadline = time.monotonic() + seconds
  while not condition() and time.monotonic() < deadline:
    time.sleep(0.005)
  return condition()


def test_the_keeper_hangs_up_a_link_that_stays_silent_and_never_pages_it():
  link, events = {"up": False, "since": time.monotonic()}, []
  keeper, calls = _silent_keeper(link, events, redial_wait=5.0)
  keeper.start()
  assert _until(lambda: calls["drop"] == 1)
  keeper.stop()
  assert calls["connect"] == 0, "a link that is already up is not paged: the lookup on a silent one goes unanswered"
  name, values = events[0]
  assert name == "companion_link_recycled" and values["outcome"] == "dropped" and values["recycle"] == 1
  assert 0.1 <= values["silent_s"] < 1.0 and values["next_s"] == 5.0, "then the air is left to the car"


def test_the_keeper_leaves_a_link_alone_once_its_hands_free_is_up():
  # A working link has hands-free within half a second; the 5 s threshold never meets it.
  link, events = {"up": False, "since": time.monotonic()}, []
  keeper, calls = _silent_keeper(link, events, silent_after=0.3)
  keeper.start()
  time.sleep(0.1)
  link["up"] = True
  time.sleep(0.5)
  keeper.stop()
  assert calls == {"drop": 0, "connect": 0} and events == []


def test_a_silent_link_is_hung_up_before_the_projection_streams():
  # Hanging up is not a page, so it does not wait for streaming the way the keeper's own page does (2026-11-08).
  link, events = {"up": False, "since": time.monotonic()}, []
  keeper, calls = _silent_keeper(link, events, ready=lambda: False)
  keeper.start()
  assert _until(lambda: calls["drop"] == 1)
  time.sleep(0.4)   # past the redial wait with no link at all: still no page before streaming
  keeper.stop()
  assert calls["connect"] == 0 and keeper.attempts == 0


def test_the_keeper_pages_the_car_itself_when_it_does_not_call_back():
  link, events = {"up": False, "since": time.monotonic()}, []
  keeper, calls = _silent_keeper(link, events)
  keeper.start()
  assert _until(lambda: calls["connect"] >= 1)
  keeper.stop()
  assert [name for name, _ in events[:2]] == ["companion_link_recycled", "companion_link"]
  assert calls["drop"] == 1


def test_silent_links_are_hung_up_a_limited_number_of_times_a_session():
  # Session 55 had three silent links in a row. Past the limit the keeper is the one it was before.
  link, events = {"up": False, "since": time.monotonic()}, []
  keeper, calls = _silent_keeper(link, events, limit=2, redial_wait=0.05)
  original_drop = keeper.drop

  def drop_and_car_calls_back_silent():
    outcome = original_drop()
    link["since"] = time.monotonic()   # the car called again, and that link is silent too
    return outcome
  keeper.drop = drop_and_car_calls_back_silent
  keeper.start()
  assert _until(lambda: calls["connect"] >= 1)
  keeper.stop()
  assert calls["drop"] == 2 and keeper.recycles == 2
  assert [values["recycle"] for name, values in events if name == "companion_link_recycled"] == [1, 2]


def test_a_refused_hang_up_ends_recycling_and_a_stale_one_does_not_count():
  for outcome, recycles_left in (("audio connected", False), ("Selected paired hands-free car is unavailable", False),
                                 ("not connected", True)):
    link, events = {"up": False, "since": time.monotonic()}, []
    keeper, calls = _silent_keeper(link, events, drop_outcome=outcome)
    keeper._recycle(6.0)
    assert (keeper._silent_seconds() is not None) is recycles_left, outcome
    assert events[0][1]["outcome"] == outcome and events[0][1]["next_s"] == 0.0 and keeper.recycles in (0, 3)


def test_a_hang_up_that_raises_does_not_take_the_keeper_down():
  link, events = {"up": False, "since": time.monotonic()}, []
  keeper, _ = _silent_keeper(link, events)
  keeper.drop = lambda: (_ for _ in ()).throw(RuntimeError("D-Bus went away"))
  assert keeper._recycle(6.0) == keeper.silent_poll
  assert events[0][1]["outcome"] == "RuntimeError: D-Bus went away" and keeper._silent_seconds() is None


def test_the_supervisor_times_the_cars_link_from_its_housekeeping_pass():
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  assert sup._companion_silent_for() is None, "never seen: not silent"
  now = time.monotonic()
  sup._note_companion_acl(True, now - 4.0)
  sup._note_companion_acl(True, now)   # still up: the count runs from the first sighting
  assert 4.0 <= sup._companion_silent_for() < 4.5
  sup._note_companion_acl(False, now)
  assert sup._companion_silent_for() is None
  sup._note_companion_acl(True, now - 1.5)
  assert 1.5 <= sup._companion_silent_for() < 2.0, "a new link starts its own count"


def test_the_supervisor_hangs_up_through_the_owner_and_restarts_the_count():
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  sup.config = {"companion_address": CAR}
  sup._companion_acl_since = 50.0
  dropped = []
  sup._shared_bluetooth_owner = SimpleNamespace(drop_companion=lambda address, selected: dropped.append((address, selected())) or "dropped")
  assert sup._drop_companion_link() == "dropped" and dropped == [(CAR, CAR)] and sup._companion_acl_since is None
  sup._companion_acl_since = 60.0
  sup._shared_bluetooth_owner = SimpleNamespace(drop_companion=lambda *a: (_ for _ in ()).throw(RuntimeError("Bluetooth is busy")))
  assert sup._drop_companion_link() == "Bluetooth is busy" and sup._companion_acl_since == 60.0
  sup.config = {"companion_address": ""}
  assert sup._drop_companion_link() == "no saved car"


def test_only_a_profile_that_asks_for_it_hangs_up_silent_links():
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  sup.config = {"companion_address": CAR}
  sup._bluez = SimpleNamespace(hfp_link=lambda address: True)
  sup._shared_bluetooth_owner = SimpleNamespace(prepare_companion=lambda *a, **k: None)
  sup.log = lambda *a, **k: None
  sup._companion_keeper = None
  sup._start_companion_keeper({**car_profiles.DEFAULTS, "hfp_gate": True})
  assert sup._companion_keeper.silent_for is None and sup._companion_keeper.drop is None
  sup._start_companion_keeper({**car_profiles.DEFAULTS, "hfp_gate": True, "recycle_silent_link": True})
  assert sup._companion_keeper.silent_for == sup._companion_silent_for and sup._companion_keeper.drop == sup._drop_companion_link
  sup._stop_companion_keeper()
  ottocast = next(rule for rule in car_profiles.RULES if rule["id"] == "ottocast")
  assert ottocast["tuning"]["recycle_silent_link"] is True and car_profiles.DEFAULTS["recycle_silent_link"] is False


def test_the_keeper_pages_only_once_the_supervisor_reports_streaming():
  sup = bare_supervisor([])
  assert sup._projection_streaming() is False
  sup._set(state="streaming")
  assert sup._projection_streaming() is True
  sup._set(state="suspended")
  assert sup._projection_streaming() is True
  sup._set(state="connecting_tcp")
  assert sup._projection_streaming() is False


def test_connecting_the_saved_car_follows_its_hands_free_link_not_bluez_connected():
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  sup.config = {"companion_address": CAR}
  links: set = set()
  sup._bluez = SimpleNamespace(hfp_link=lambda address: address in links)
  calls = []

  def prepare(address, selected, *, connect=False, timeout=12.0, link_up=None):
    calls.append((address, connect, timeout, link_up is not None and link_up()))
    links.add(address)

  sup._shared_bluetooth_owner = SimpleNamespace(prepare_companion=prepare)
  assert sup._connect_companion_link(8.0) == "connected" and calls == [(CAR, True, 8.0, False)]
  assert sup._connect_companion_link(8.0) == "up" and len(calls) == 1
  links.clear()
  sup._shared_bluetooth_owner = SimpleNamespace(prepare_companion=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("br-connection-refused")))
  assert sup._connect_companion_link(8.0) == "br-connection-refused"
  sup.config = {"companion_address": ""}
  assert sup._connect_companion_link(8.0) == "no saved car"


def test_the_keeper_runs_only_for_a_profile_with_the_gate_and_a_saved_car():
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  sup.config = {"companion_address": CAR}
  sup._bluez = SimpleNamespace(hfp_link=lambda address: True)
  sup._shared_bluetooth_owner = SimpleNamespace(prepare_companion=lambda *a, **k: None)
  sup.log = lambda *a, **k: None
  sup._companion_keeper = None
  sup._start_companion_keeper(dict(car_profiles.DEFAULTS))
  assert sup._companion_keeper is None
  sup._start_companion_keeper({**car_profiles.DEFAULTS, "hfp_gate": True})
  assert sup._companion_keeper is not None
  sup._stop_companion_keeper()
  assert sup._companion_keeper is None
  sup.config = {"companion_address": ""}
  sup._start_companion_keeper({**car_profiles.DEFAULTS, "hfp_gate": True})
  assert sup._companion_keeper is None


def _stream_with_keeper(monkeypatch, session, link_up, seconds=0.2):
  monkeypatch.setattr(supervisor, "FOCUS_RETRY_FIRST", 10.0)
  monkeypatch.setattr(supervisor.device_health, "sample", lambda interface: {})
  sup = bare_supervisor([])
  sup._stage = lambda *a, **k: None
  sup._native_reconnects, sup._native_reconnect_limit = 0, 0
  sup._companion_link_up = link_up
  stops = []

  def stop_keeper():
    stops.append(1)
    sup._companion_keeper = None

  sup._stop_companion_keeper = stop_keeper
  sup._companion_keeper = SimpleNamespace()
  timer = threading.Timer(seconds, sup._stop.set)
  timer.start()
  try:
    sup._stream(session, type("Encoder", (), {"last_encode_ms": 0.0})(), Source(), Watch(), 1 / 30)
  finally:
    sup._stop.set()
    timer.cancel()
  return sup, stops


def test_the_car_screen_wait_names_the_missing_hands_free_link(monkeypatch):
  sup, stops = _stream_with_keeper(monkeypatch, Session(), lambda: False)
  assert sup._status["detail"] == supervisor.COMPANION_LINK_HINT and stops == []
  sup, stops = _stream_with_keeper(monkeypatch, Session(), lambda: True)
  assert sup._status["detail"] == "" and stops == []


def test_the_keeper_stops_once_the_car_hands_over(monkeypatch):
  session = Session()
  session.focused, session.focus_epoch = True, 1
  sup, stops = _stream_with_keeper(monkeypatch, session, lambda: False)
  assert stops == [1] and sup._status["detail"] == ""


def test_bluez_phone_tracks_open_hands_free_links_per_peer(monkeypatch):
  phone = bluez_phone.BluezPhone.__new__(bluez_phone.BluezPhone)
  phone._peers_lock, phone._hfp_peers, phone._stop = threading.Lock(), {}, threading.Event()
  phone.log = lambda *a, **k: None
  gone = []
  phone.on_disconnection = gone.append
  release = threading.Event()
  monkeypatch.setattr(bluez_phone.hfp, "serve", lambda sock, stop, log, peer: release.wait(2.0))
  phone._hfp_peers["AA:BB"] = 2  # the car reconnected before its first link closed
  threads = [threading.Thread(target=phone._serve_peer, args=(None, "AA:BB", "dev_AA_BB"), daemon=True) for _ in range(2)]
  for thread in threads:
    thread.start()
  assert phone.hfp_link("aa:bb")
  release.set()
  for thread in threads:
    thread.join(2.0)
  assert not phone.hfp_link("AA:BB") and gone == ["AA:BB", "AA:BB"]


# ------------------------------------------------------------------ cold boot: the radio is not managed yet

def test_auto_connect_waits_for_networkmanager_to_manage_the_radio():
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  events = []
  sup.log = lambda name, **values: events.append((name, values))
  sup.config = {"connection": "wireless", "wifi_interface": "wlan0"}
  sup._lease_factory = None
  sup._wifi_probe_at, sup._wifi_ready_state = -10.0, True
  states = iter([10, 20, 30])
  sup._wifi_probe = SimpleNamespace(interface_state=lambda: next(states))
  assert sup._wifi_ready(0.0) is False and sup._wifi_ready(1.0) is False  # within the probe interval: no second ask
  assert sup._wifi_ready(5.0) is False  # unavailable: no supplicant yet
  assert sup._wifi_ready(10.0) is True
  assert [(name, values["state"]) for name, values in events] == [("wifi_radio_waiting", 10), ("wifi_radio_ready", 30)]


def test_the_radio_probe_fails_open():
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  sup.log = lambda name, **values: None
  sup.config = {"connection": "wireless", "wifi_interface": "wlan0"}
  sup._lease_factory = None
  sup._wifi_probe_at, sup._wifi_ready_state = -10.0, False
  sup._wifi_probe = SimpleNamespace(interface_state=lambda: (_ for _ in ()).throw(RuntimeError("no NetworkManager")))
  assert sup._wifi_ready(0.0) is True
  sup.config = {"connection": "wired", "wifi_interface": "wlan0"}
  sup._wifi_probe_at, sup._wifi_ready_state = -10.0, False
  assert sup._wifi_ready(0.0) is True  # wired sessions never wait for the radio


# ------------------------------------------------------------------ cold boot: the keeper owns the link, nothing pages before a session

def _auto_connect_unit(events, profile):
  from openpilot.starpilot.system.starport.tests.test_car_first_restart import fixture
  unit = fixture()
  unit.config["car_profile_override"] = profile
  unit.log = lambda name, **values: events.append((name, values))
  return unit


def test_with_the_gate_ignition_starts_the_adapter_without_waiting_for_the_car():
  # 2026-11-03 cold boot: the start waited 23 s on a page of the car whose service lookup hung; the keeper then
  # brought the link up 32 s into the session and the car handed over a second later.
  events = []
  unit = _auto_connect_unit(events, "ottocast")
  unit._onroad = lambda: True
  unit._shared_bluetooth_owner.prepare_companion.return_value = {"connected": False}
  unit._auto_connect(100)
  assert [call.kwargs for call in unit._shared_bluetooth_owner.prepare_companion.call_args_list] == [{}]  # admitted, never paged
  unit.start.assert_called_once_with(trigger="onroad")
  gate = next(values for name, values in events if name == "auto_connect_gate")
  assert (gate["onroad"], gate["car_link"], gate["ready"]) == (True, False, True)


def test_with_the_gate_a_parked_comma_never_pages_the_car_but_still_answers_it():
  # The 11-02 journal: 218 page timeouts an hour, all day, from the 15 s retry while the car was in the garage.
  events = []
  unit = _auto_connect_unit(events, "ottocast")
  unit._shared_bluetooth_owner.prepare_companion.return_value = {"connected": False}
  for now in (100, 101, 115, 130, 200, 400):
    unit._auto_connect(now)
  assert all(call.kwargs == {} for call in unit._shared_bluetooth_owner.prepare_companion.call_args_list)
  unit.start.assert_not_called()
  assert [name for name, _ in events].count("auto_connect_gate") == 1  # unchanged gate: logged once
  unit._car_seen_at = 405.0  # the car opened hands-free to the comma by itself
  unit._auto_connect(410)
  unit.start.assert_called_once_with(trigger="car_connected")


def test_without_the_gate_the_car_is_still_paged_before_the_adapter():
  events = []
  unit = _auto_connect_unit(events, "default")
  unit._onroad = lambda: True
  unit._shared_bluetooth_owner.prepare_companion.return_value = {"connected": False}
  unit._auto_connect(100)
  assert [call.kwargs for call in unit._shared_bluetooth_owner.prepare_companion.call_args_list] == [{}, {"connect": True}]
  unit.start.assert_not_called()


def test_the_attempt_only_admits_the_car_under_the_gate():
  events = []
  unit = supervisor.Supervisor.__new__(supervisor.Supervisor)
  unit.config = {"companion_address": CAR}
  unit.log = lambda name, **values: events.append((name, values))
  unit._check_cancel = lambda: None
  calls = []
  unit._shared_bluetooth_owner = SimpleNamespace(prepare_companion=lambda address, selected, *, connect: calls.append((address, connect)))
  unit._admit_companion({**car_profiles.DEFAULTS, "hfp_gate": True})
  unit._admit_companion({**car_profiles.DEFAULTS, "hfp_gate": False})
  assert calls == [(CAR, False), (CAR, True)]
  assert [name for name, _ in events] == ["companion_car_admitted", "companion_car_connected"]
  unit._shared_bluetooth_owner = None
  with pytest.raises(RuntimeError):
    unit._admit_companion(car_profiles.DEFAULTS)


def test_events_between_sessions_go_to_a_rolling_daemon_log(tmp_path, monkeypatch):
  import json
  log = supervisor.EventLog(tmp_path)
  log("auto_connect_gate", onroad=False)
  log.open()
  log("session_start")
  log.close()
  daemon = tmp_path / "daemon.jsonl"
  assert [json.loads(line)["event"] for line in daemon.read_text().splitlines()] == ["auto_connect_gate"]
  session = next(path for path in tmp_path.glob("session-*.jsonl"))
  assert [json.loads(line)["event"] for line in session.read_text().splitlines()] == ["session_start"]
  monkeypatch.setattr(supervisor, "DAEMON_LOG_BYTES", 1)
  log("wifi_radio_waiting")
  assert [json.loads(line)["event"] for line in (tmp_path / "daemon.1.jsonl").read_text().splitlines()] == ["auto_connect_gate"]
  assert [json.loads(line)["event"] for line in daemon.read_text().splitlines()] == ["wifi_radio_waiting"]
  assert log.recent[-1]["event"] == "wifi_radio_waiting"
