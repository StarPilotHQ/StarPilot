from typing import Any
import os
import subprocess
import sys
import threading
import time

import pytest

from openpilot.starpilot.system.starport import car_interface, network
from openpilot.starpilot.system.starport.bootstrap import WifiCredentials
from openpilot.starpilot.system.starport.car_interface import DedicatedInterface, DedicatedLease
from openpilot.starpilot.system.starport.network import DedicatedUnavailable, JoinTimeout, NetworkError, NetworkLease


class Log:
  def __init__(self):
    self.events = []

  def __call__(self, event, **fields):
    self.events.append((event, fields))

  def names(self):
    return [event for event, _ in self.events]


class FakeInterface:
  instances: list = []

  def __init__(self, log, fail=False):
    self.opened = self.closed = False
    self.fail = fail
    FakeInterface.instances.append(self)

  def open(self):
    if self.fail:
      raise DedicatedUnavailable("no aa0")
    self.opened = True
    return "aa0"

  def close(self):
    self.closed = True


class FakeLease:
  def __init__(self, log, interface, dedicated=False):
    self.interface, self.dedicated = interface, dedicated
    self.local_ip = ""
    self.errors: list = []
    self.releases: list = []
    self.held = False

  def prepare_device(self, cancelled):
    self.prepared = True

  def acquire(self, credentials, cancelled=lambda: False, timeout=40.0):
    if self.errors:
      raise self.errors.pop(0)
    self.local_ip = "192.168.180.183"
    return self.local_ip

  def still_connected(self):
    return True

  def release(self, restore=True):
    self.releases.append(restore)

  def hold_autoconnect(self):
    self.held = True
    return True


def credentials():
  return WifiCredentials(ssid="Car-4E91", key="secret-key", bssid="C6:B7:57:F7:4E:91", security=8, ap_type=0)


def make(fail=False, errors=(), fail_reopen=False):
  leases = []

  def factory(log, interface, dedicated=False):
    lease = FakeLease(log, interface, dedicated)
    if dedicated:
      lease.errors = list(errors) if not any(item.dedicated for item in leases) else []
    leases.append(lease)
    return lease
  FakeInterface.instances = []
  log = Log()
  def interface(log):
    return FakeInterface(log, fail or (fail_reopen and len(FakeInterface.instances) > 0))

  return DedicatedLease(log, "wlan0", factory, interface_factory=interface), leases, log


def test_dedicated_session_keeps_aa0_between_attempts():
  lease, leases, log = make()
  lease.prepare()  # at session start, while Bluetooth connects
  assert lease._opening is not None
  assert lease.acquire(credentials()) == "192.168.180.183"
  assert leases[0].prepared
  assert (leases[0].interface, leases[0].dedicated) == ("aa0", True) and lease.interface == "aa0"
  lease.release(restore=False)
  assert not FakeInterface.instances[0].closed
  lease.acquire(credentials())
  assert len(leases) == 1 and len(FakeInterface.instances) == 1
  lease.release(restore=True)
  assert FakeInterface.instances[0].closed and leases[0].releases == [False, True]
  assert "wifi_dedicated" in log.names()


def test_falls_back_to_wlan0_when_aa0_cannot_be_created():
  lease, leases, log = make(fail=True)
  assert lease.acquire(credentials()) == "192.168.180.183"
  assert [(item.interface, item.dedicated) for item in leases] == [("wlan0", False)]
  assert "wifi_dedicated_unavailable" in log.names()
  lease.release(restore=True)
  assert not lease.legacy  # the next session tries aa0 again


def test_overlapping_car_subnet_finishes_join_on_wlan0():
  lease, leases, log = make(errors=[DedicatedUnavailable("overlaps")])
  assert lease.acquire(credentials()) == "192.168.180.183"
  assert [(item.interface, item.dedicated) for item in leases] == [("aa0", True), ("wlan0", False)]
  assert leases[0].releases == [True] and FakeInterface.instances[0].closed


def test_failed_aa0_join_finishes_the_same_join_on_wlan0():
  error = NetworkError("NetworkManager could not join the projection network")
  lease, leases, log = make(errors=[error])
  leases_errors = [error]

  def factory(log, interface, dedicated=False):
    item = FakeLease(log, interface, dedicated)
    if dedicated:
      item.errors = list(leases_errors)  # the fresh aa0 fails the same way
    leases.append(item)
    return item
  lease.lease_factory = factory
  assert lease.acquire(credentials()) == "192.168.180.183"
  assert [(item.interface, item.dedicated) for item in leases] == [("aa0", True), ("aa0", True), ("wlan0", False)]
  assert lease.aa0_failed and not lease.aa0_joined and all(item.closed for item in FakeInterface.instances)
  assert log.names().count("wifi_dedicated_reopen") == 1


def test_networkmanager_restart_rebuilds_aa0_instead_of_falling_back():
  stale = NetworkError("Cannot read ActiveConnection: Object does not exist at path /org/freedesktop/NetworkManager/Devices/116")
  lease, leases, log = make(errors=[stale, stale])
  assert lease.acquire(credentials()) == "192.168.180.183"
  assert [(item.interface, item.dedicated) for item in leases] == [("aa0", True), ("aa0", True)]
  assert FakeInterface.instances[0].closed and not FakeInterface.instances[1].closed
  assert lease.interface == "aa0" and lease.dedicated and lease.aa0_joined and not lease.legacy
  assert "wifi_dedicated_reopen" in log.names() and "wifi_dedicated_unavailable" not in log.names()


def test_aa0_that_cannot_be_rebuilt_finishes_on_wlan0():
  lease, leases, log = make(errors=[NetworkError("stale")], fail_reopen=True)
  assert lease.acquire(credentials()) == "192.168.180.183"
  assert [(item.interface, item.dedicated) for item in leases] == [("aa0", True), ("wlan0", False)]
  assert lease.legacy and not lease.dedicated and not lease.aa0_failed


def test_lost_home_wifi_is_held_across_attempts_until_the_session_ends():
  lease, leases, log = make()
  lease.acquire(credentials())
  lease.hold_home_wifi()
  lease.hold_home_wifi()  # a second carrier change keeps the one hold
  home = [item for item in leases if item.interface == "wlan0"]
  assert len(home) == 1 and home[0].held and not home[0].dedicated
  lease.release(restore=False)
  assert home[0].releases == []  # between attempts the hold stays
  lease.release(restore=True)
  assert home[0].releases == [True] and lease.home is None
  assert log.names().count("wifi_home_held") == 1 and "wifi_home_released" in log.names()


def wait_released(lease):
  deadline = time.monotonic() + 2
  while lease.home is not None:
    assert time.monotonic() < deadline
    time.sleep(0.01)


def test_every_hold_ends_on_its_own_and_backs_off_to_a_cap(monkeypatch):
  monkeypatch.setattr(car_interface, "HOME_RETRY_S", 0.02)
  monkeypatch.setattr(car_interface, "HOME_RETRY_MAX_S", 0.05)
  lease, leases, log = make()
  lease.acquire(credentials())
  waits = []
  for hold in (lease.hold_home_wifi, lease.stop_home_join, lease.stop_home_join, lease.hold_home_wifi):
    hold()
    assert lease.home is not None and leases[-1].held
    waits.append([fields for event, fields in log.events if event == "wifi_home_held"][-1]["retry_after_s"])
    wait_released(lease)
    assert leases[-1].releases == [True]
  assert waits == [0.02, 0.04, 0.05, 0.05]
  lease.release(restore=True)


def test_a_held_join_is_not_held_twice_and_the_session_end_resets_the_backoff(monkeypatch):
  monkeypatch.setattr(car_interface, "HOME_RETRY_S", 30.0)
  lease, leases, log = make()
  lease.acquire(credentials())
  lease.hold_home_wifi()
  lease.stop_home_join()
  assert log.names().count("wifi_home_held") == 1
  lease.release(restore=True)
  assert lease.home is None and lease._home_retry is None and lease._home_holds == 0


def test_home_hold_backoff_is_capped_after_many_holds():
  lease, leases, log = make()
  lease.acquire(credentials())
  lease._home_holds = 10000
  lease.hold_home_wifi()
  assert lease._home_retry.interval == car_interface.HOME_RETRY_MAX_S
  lease.release(restore=True)


def test_home_wifi_is_never_held_while_the_car_uses_it():
  lease, leases, log = make(fail=True)
  lease.acquire(credentials())
  lease.hold_home_wifi()
  assert lease.home is None and not any(item.held for item in leases)


def test_late_radio_callback_cannot_hold_home_wifi_after_release():
  lease, leases, log = make()
  lease.acquire(credentials())
  lease.release(restore=True)
  lease.hold_home_wifi()
  assert lease.home is None and not any(item.held for item in leases)


def test_home_wifi_that_came_back_is_not_held(monkeypatch):
  lease, leases, log = make()
  lease.acquire(credentials())
  monkeypatch.setattr(FakeLease, "hold_autoconnect", lambda self: False)
  lease.hold_home_wifi()
  assert lease.home is None and leases[-1].releases == [True]


def test_repeated_aa0_timeouts_fall_back_for_the_session():
  lease, leases, log = make(errors=[JoinTimeout("timed out"), JoinTimeout("timed out")])
  for _ in range(2):
    with pytest.raises(JoinTimeout):
      lease.acquire(credentials())
    lease.release(restore=False)
  assert lease.legacy and FakeInterface.instances[0].closed and not lease.aa0_failed
  lease.acquire(credentials())
  assert leases[-1].interface == "wlan0"


def test_skipped_car_uses_wlan0_without_creating_aa0():
  log = Log()
  leases = []
  def no_aa0(log):
    raise AssertionError("aa0 created")
  lease = DedicatedLease(log, "wlan0", lambda log, interface, dedicated=False: leases.append(FakeLease(log, interface)) or leases[-1],
                         interface_factory=no_aa0, enabled=False)
  lease.prepare()
  lease.acquire(credentials())
  lease.release(restore=True)
  assert [item.interface for item in leases] == ["wlan0"] and lease.legacy


def test_cancelled_join_is_not_counted():
  lease, leases, log = make(errors=[NetworkError("cancelled"), NetworkError("cancelled")])
  for _ in range(2):
    with pytest.raises(NetworkError):
      lease.acquire(credentials(), cancelled=lambda: True)
  assert not lease.legacy and lease.timeouts == 0 and not lease.aa0_failed


def test_session_stop_cancels_a_slow_aa0_setup():
  started, finish = threading.Event(), threading.Event()

  class SlowLease(FakeLease):
    def prepare_device(self, cancelled):
      started.set()
      while not cancelled():
        time.sleep(0.01)
      finish.set()
      raise NetworkError("cancelled")

  log = Log()
  FakeInterface.instances = []
  lease = DedicatedLease(log, "wlan0", SlowLease, interface_factory=lambda log: FakeInterface(log))
  lease.prepare()
  assert started.wait(2)
  lease.release(restore=True)
  assert finish.is_set() and FakeInterface.instances[0].closed and lease.lease is None
  assert "wifi_dedicated_unavailable" not in log.names()


class FakeNM:
  def __init__(self, devices=None, autoconnect=True, overlap=False, appears_after=0, state=30):
    self.devices = devices if devices is not None else {"/dev/wlan0": "wlan0", "/dev/aa0": "aa0"}
    self.state = state
    self.autoconnect = autoconnect
    self.overlap = overlap
    self.appears_after = appears_after  # GetDevices calls before NetworkManager lists aa0
    self.calls = []
    self.set = []

  def call(self, path, interface, member, signature=None, body=(), timeout=10.0):
    self.calls.append(member)
    if member == "GetDevices":
      if self.appears_after:
        self.appears_after -= 1
        return [[path for path, name in self.devices.items() if name != "aa0"]]
      return [list(self.devices)]
    if member == "AddAndActivateConnection2":
      return ["/settings/aa", "/active/aa"]
    if member == "GetSettings":
      return [{"connection": {"id": ("s", "home")}}]
    return []

  def get(self, path, interface, name):
    if name == "Interface":
      return self.devices[path]
    if name == "Ip4Config":
      return {"/active/aa": "/ip4/aa", "/dev/aa0": "/ip4/aa", "/dev/wlan0": "/ip4/home"}.get(path, "/")
    if name == "AddressData":
      if path == "/ip4/home":
        return [{"address": ("s", "192.168.180.40" if self.overlap else "192.168.35.249"), "prefix": ("u", 24)}]
      return [{"address": ("s", "192.168.180.183"), "prefix": ("u", 24)}]
    if name == "Frequency":
      return 5745 if path == "/ap/aa0" else 2437
    return {"WirelessEnabled": True, "DeviceType": 2, "ActiveConnection": "/active/home", "State": self.state if path.startswith("/dev") else 2,
            "Autoconnect": self.autoconnect, "Connection": "/settings/home", "ActiveAccessPoint": "/ap/" + path.rsplit("/", 1)[-1]}[name]

  def set_property(self, path, interface, name, signature, value):
    self.set.append((path, name, value))
    self.autoconnect = value


def nm_lease(nm, dedicated=True, monkeypatch=None):
  lease: Any = NetworkLease(Log(), "aa0", dedicated=dedicated)
  lease._call, lease._get, lease._set = nm.call, nm.get, nm.set_property
  lease._delete_stale_profiles = lambda: None  # test double
  lease.router = type("R", (), {"close": lambda self: None})()
  return lease


def test_dedicated_network_lease_leaves_home_wifi_alone(monkeypatch):
  nm = FakeNM(autoconnect=False)
  lease = nm_lease(nm)
  assert lease.acquire(credentials(), timeout=2.0) == "192.168.180.183"
  assert lease.previous_connection == ""
  lease.release(restore=True)
  assert "ActivateConnection" not in nm.calls


def test_dedicated_network_lease_turns_off_autoconnect_before_joining():
  nm = FakeNM(autoconnect=True)
  lease = nm_lease(nm)
  lease.acquire(credentials(), timeout=2.0)
  assert nm.set == [("/dev/aa0", "Autoconnect", False)]
  assert nm.calls.index("AddAndActivateConnection2") > 0
  joined = dict(lease.log.events)["wifi_joined"]
  assert joined["freq"] == 5745 and joined["wlan0_freq"] == 2437 and joined["wlan0_state"] == 30


def test_dedicated_network_lease_waits_for_networkmanager_to_list_aa0(monkeypatch):
  monkeypatch.setattr(time, "sleep", lambda _: None)
  nm = FakeNM(autoconnect=False, appears_after=3)
  lease = nm_lease(nm)
  lease.prepare_device()
  assert lease.device_path == "/dev/aa0" and lease.device_ready
  assert "wifi_scan_requested" in lease.log.names()  # aa0 learns the car's access point before the join
  lease.acquire(credentials(), timeout=2.0)
  assert nm.calls.count("AddAndActivateConnection2") == nm.calls.count("RequestScan") == 1
  assert nm.calls.index("RequestScan") < nm.calls.index("AddAndActivateConnection2")


def test_dedicated_network_lease_never_falls_back_to_another_device(monkeypatch):
  monkeypatch.setattr(network, "DEVICE_READY_TIMEOUT_S", 0.3)
  nm = FakeNM(devices={"/dev/wlan0": "wlan0"}, autoconnect=False)
  lease = nm_lease(nm)
  with pytest.raises(DedicatedUnavailable):
    lease.acquire(credentials(), timeout=2.0)
  assert lease.interface == "aa0" and "wifi_retry_without_bssid" not in lease.log.names()


def test_dedicated_network_lease_rejects_overlapping_subnet_without_a_second_join():
  nm = FakeNM(autoconnect=False, overlap=True)
  lease = nm_lease(nm)
  with pytest.raises(DedicatedUnavailable, match="overlaps"):
    lease.acquire(credentials(), timeout=2.0)
  assert "wifi_subnet_overlap" in lease.log.names()
  assert nm.calls.count("AddAndActivateConnection2") == 1


@pytest.mark.parametrize("state, held, disconnected", [(30, True, False), (120, True, False), (50, True, True), (100, False, False)])
def test_hold_autoconnect_on_lost_home_wifi(tmp_path, state, held, disconnected):
  nm = FakeNM(state=state)
  lease: Any = NetworkLease(Log(), "wlan0")
  lease._call, lease._get, lease._set = nm.call, nm.get, nm.set_property
  lease.router = type("R", (), {"close": lambda self: None})()
  lease.marker = tmp_path / "paused.json"
  assert lease.hold_autoconnect() is held
  assert ("Disconnect" in nm.calls) is disconnected
  assert nm.set == ([("/dev/wlan0", "Autoconnect", False)] if held else [])
  lease.release(restore=True)
  assert nm.autoconnect and not lease.marker.exists()


def test_legacy_lease_still_falls_back():
  lease = nm_lease(FakeNM(devices={"/dev/wlan0": "wlan0"}), dedicated=False)
  lease.acquire(credentials(), timeout=2.0)
  assert lease.interface == "wlan0" and lease.previous_connection == "/settings/home"


def worker(script):
  def popen(args, **kwargs):
    assert args[:2] == ["sudo", "-n"] and args[-1] == "openpilot.starpilot.system.starport.car_interface"
    return subprocess.Popen([sys.executable, "-c", script], **kwargs)
  return popen


def test_interface_lease_ready_heartbeat_and_close(monkeypatch):
  monkeypatch.setattr(car_interface, "HEARTBEAT_S", 0.05)
  script = "\n".join(["import sys", "print('{\"ready\": true, \"interface\": \"aa0\"}', flush=True)", "beats = 0",
                      "while sys.stdin.buffer.read(1): beats += 1", "sys.exit(0 if beats else 3)"])
  iface = DedicatedInterface(Log(), popen=worker(script))
  assert iface.open() == "aa0"
  child = iface.child
  time.sleep(0.3)
  iface.close()
  assert child.returncode == 0


@pytest.mark.parametrize("script,match", [("print('{\"ready\": false, \"error\": \"busy\"}', flush=True)", "busy"),
                                          ("import time; time.sleep(5)", "not ready"), ("pass", "Could not create")])
def test_interface_lease_failures(script, match):
  iface = DedicatedInterface(Log(), popen=worker(script), ready_timeout=0.5)
  with pytest.raises(DedicatedUnavailable, match=match):
    iface.open()
  assert iface.child is None


def test_supervisor_uses_dedicated_lease_only_when_enabled(monkeypatch, tmp_path):
  from openpilot.starpilot.system.starport import identity, supervisor
  monkeypatch.setattr(car_interface, "DedicatedInterface", FakeInterface)
  config = identity.load_config(tmp_path / "missing.json")
  assert config["dedicated_wifi_interface"] is True
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  sup.log, sup._lease_factory, sup.config, sup._dedicated_strikes = Log(), FakeLease, config, {}  # ty: ignore[invalid-assignment]  # test double
  assert isinstance(sup._lease(), DedicatedLease)
  sup.config = {**config, "dedicated_wifi_interface": False}
  assert isinstance(sup._lease(), FakeLease)


def test_supervisor_keeps_a_car_on_wlan0_after_repeated_aa0_failures(monkeypatch):
  from openpilot.starpilot.system.starport import identity, supervisor
  monkeypatch.setattr(car_interface, "DedicatedInterface", FakeInterface)
  config = {**identity.load_config(None), "dedicated_wifi_interface": True, "receiver_address": "AA:BB:CC:DD:EE:FF"}
  sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
  sup.log, sup._lease_factory, sup.config, sup._dedicated_strikes = Log(), FakeLease, config, {}  # ty: ignore[invalid-assignment]  # test double
  for _ in range(supervisor.DEDICATED_STRIKES):
    lease = sup._lease()
    assert lease.enabled
    lease.release(restore=True)
    lease.aa0_failed = True
    sup._count_dedicated(lease)
  lease = sup._lease()
  assert not lease.enabled and "wifi_dedicated_skipped" in sup.log.names()
  lease.aa0_joined = True
  sup._count_dedicated(lease)
  assert sup._dedicated_strikes == {}


class FakeWireless:
  def __init__(self, existing=()):
    self.existing = set(existing)
    self.calls = []

  def __call__(self, method, *args, **kwargs):
    self.calls.append((method, args, kwargs))
    if method == "interfaces":
      return {name: {} for name in self.existing}
    if method == "create":
      self.existing.add(args[0])
    if method == "delete":
      self.existing.discard(args[0])
    return None


def serve(wireless, stdin, stop=None, is_owned=lambda interface, alias: False, timeout=10.0):
  run = []
  thread = threading.Thread(target=car_interface.serve, args=(wireless, stdin, stop or threading.Event()),
                            kwargs={"run": lambda args, **kwargs: run.append(args), "is_owned": is_owned, "lease_timeout": timeout})
  thread.start()
  return thread, run


@pytest.mark.parametrize("ending", ["eof", "heartbeat", "stop"])
def test_worker_deletes_aa0_when_the_lease_ends(ending, capsys):
  read, write = os.pipe()
  wireless, stop = FakeWireless(existing=["wlan0", "p2p0", "galaxy0"]), threading.Event()
  thread, run = serve(wireless, read, stop, timeout=0.3 if ending == "heartbeat" else 10.0)
  time.sleep(0.1)
  if ending == "eof":
    os.close(write)
  elif ending == "stop":
    stop.set()
  thread.join(3)
  assert not thread.is_alive()
  assert '"ready": true' in capsys.readouterr().out
  assert ("create", ("aa0",), {"iftype": 2}) in wireless.calls and wireless.calls[-1] == ("delete", ("aa0",), {})
  assert run == [["ip", "link", "set", "dev", "aa0", "alias", "starpilot-aa-wifi"]]
  touched = [args[0] for method, args, _ in wireless.calls if args] + [word for command in run for word in command]
  assert not {"wlan0", "p2p0", "galaxy0"} & set(touched)
  os.close(read)
  if ending != "eof":
    os.close(write)


def test_worker_leaves_an_aa0_it_does_not_own(capsys):
  read, write = os.pipe()
  wireless = FakeWireless(existing=["wlan0", "aa0"])
  thread, run = serve(wireless, read)
  thread.join(3)
  assert '"ready": false' in capsys.readouterr().out
  assert [method for method, _, _ in wireless.calls] == ["interfaces"]
  os.close(read)
  os.close(write)


def test_worker_replaces_its_own_stale_aa0(capsys):
  read, write = os.pipe()
  wireless = FakeWireless(existing=["wlan0", "aa0"])
  thread, run = serve(wireless, read, is_owned=lambda interface, alias: interface == "aa0" and alias == "starpilot-aa-wifi")
  time.sleep(0.1)
  os.close(write)
  thread.join(3)
  assert [method for method, _, _ in wireless.calls][:3] == ["delete", "interfaces", "create"]
  os.close(read)


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  from openpilot.starpilot.system.starport.tests.fake_head_unit import make_identity
  return make_identity(tmp_path_factory.mktemp("identity"))


def test_daemon_saves_and_reports_the_dedicated_wifi_choice(identity, tmp_path, monkeypatch):
  import json
  from openpilot.starpilot.system.starport import daemon, identity as identity_store
  from openpilot.starpilot.system.starport.tests.test_starport import make_supervisor
  import openpilot.common.params as params
  store = params.Params(str(tmp_path / "params"))
  store.put_bool("StarPortEnabled", True, block=True)
  monkeypatch.setattr(params, "Params", lambda: store)
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: None)
  assert daemon.handle(sup, {"command": "status"})["status"]["dedicated_wifi_interface"] is False
  daemon.handle(sup, {"command": "set_dedicated_wifi", "enabled": True})
  assert sup.status()["dedicated_wifi_interface"] is True
  assert json.loads(identity_store.CONFIG_PATH.read_text())["dedicated_wifi_interface"] is True
  daemon.handle(sup, {"command": "set_dedicated_wifi", "enabled": "yes"})  # only a real true turns it on
  assert sup.status()["dedicated_wifi_interface"] is False
