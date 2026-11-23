"""Car profiles are picked by fingerprint; cars without a rule keep the standard behavior."""

import json
import threading
from unittest.mock import Mock

import pytest

from openpilot.starpilot.system.starport import bugsink_report as br, car_profiles, daemon, identity as identity_store
from openpilot.starpilot.system.starport.supervisor import Supervisor
from openpilot.starpilot.system.starport.tests.fake_head_unit import make_identity
from openpilot.starpilot.system.starport.tests.test_starport import make_supervisor

ADAPTER = "AA:BB:CC:DD:EE:01"
PALISADE = {"id": "palisade", "label": "Hyundai Palisade", "match": {"car_make": "^hyundai$", "car_model": "palisade"},
            "tuning": {"connect_delay_s": 10.0, "unknown_key": 1}}


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("identity"))


@pytest.fixture
def sup(identity, tmp_path, monkeypatch):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: None)
  return sup


@pytest.fixture
def palisade(monkeypatch):
  monkeypatch.setattr(car_profiles, "RULES", [PALISADE])


def config(**values):
  return {"receiver_address": ADAPTER, "car_profile_override": "", "car_fingerprint": {}, **values}


def test_aawireless_tuning_applies_only_when_chosen():
  chosen = config(car_profile_override="aawireless", receiver_name="AAWireless-4F2A")
  assert car_profiles.resolve(chosen) == ("aawireless", "override")
  assert car_profiles.tuning(chosen) == {**car_profiles.DEFAULTS, "max_window": 30, "phone_services": True, "phone_frames": True,
                                          "stay_while_projecting": True, "dhcp_patience_s": 6.0,
                                          "own_handsfree": True, "static_ip": True}
  assert car_profiles.summary(chosen)["car_profile_label"] == "AAWireless"
  for other in (config(receiver_name="AAWireless-4F2A"), config(car_profile_override="default")):
    assert car_profiles.tuning(other) == car_profiles.DEFAULTS


def test_ottocast_tuning_applies_only_when_chosen():
  # The operator's set for that dongle: every connection fix is off for everyone else. 2026-11-02: the screen is gated
  # by the car's own hands-free link, so the negotiate delay and the native-screen reconnects are gone.
  chosen = config(car_profile_override="ottocast")
  assert car_profiles.resolve(chosen) == ("ottocast", "override")
  # 2026-11-13: plus recycle_silent_link, for the Honda link that comes up at ignition and carries nothing for 68 s,
  # and remember_wifi_address, for the 6 s the join spent on DHCP.
  assert car_profiles.tuning(chosen) == {**car_profiles.DEFAULTS, "phone_services": True, "reuse_wifi_join": True,
                                         "tcp_stops_with_rfcomm": True, "hfp_gate": True, "recycle_silent_link": True,
                                         "remember_wifi_channel": True, "remember_wifi_address": True,
                                         "setup_restarts": 2, "max_window": 30}  # the car's full video window (it offers 4)
  assert car_profiles.tuning(chosen)["negotiate_delay_s"] == 0.0 and car_profiles.tuning(chosen)["native_reconnects"] == 0
  assert car_profiles.summary(chosen)["car_profile_label"] == "OTTOCAST"
  for other in (config(), config(car_profile_override="default"), config(car_profile_override="aawireless")):
    tuning = car_profiles.tuning(other)
    assert (tuning["negotiate_delay_s"], tuning["native_reconnects"], tuning["reuse_wifi_join"], tuning["tcp_stops_with_rfcomm"],
            tuning["hfp_gate"], tuning["remember_wifi_channel"], tuning["setup_restarts"]) == (0.0, 0, False, False, False, False, 0)


def test_leaving_the_adapter_setup_clears_the_adapter_model(sup):
  sup.config["car_type"] = "adapter"
  sup.config.update(receiver_address=ADAPTER, receiver_name="AAWireless", companion_address="car",
                    companion_name="Car", car_fingerprint={"address": ADAPTER})
  sup.set_car_profile("aawireless")
  sup.set_car_type("builtin")
  assert sup.config["car_profile_override"] == ""
  assert not sup.config["receiver_address"] and not sup.config["companion_address"] and not sup.config["car_fingerprint"]


def test_an_adapter_setup_must_choose_its_model_before_pairing_or_starting(sup):
  sup.config["car_type"] = "adapter"
  assert sup.status()["adapter_choice_needed"] is True
  with pytest.raises(RuntimeError, match="adapter model"):
    sup.prepare_pairing()
  with pytest.raises(RuntimeError, match="adapter model"):
    sup.start()
  sup.set_car_profile("default")
  assert sup.status()["adapter_choice_needed"] is False
  sup.config["car_type"] = "builtin"
  sup.config["car_profile_override"] = ""
  assert sup.status()["adapter_choice_needed"] is False


def test_every_car_is_standard_without_rules():
  assert car_profiles.resolve(config(car_fingerprint={"address": ADAPTER, "car_make": "Hyundai"})) == ("default", "standard")
  assert car_profiles.tuning(config()) == car_profiles.DEFAULTS
  assert car_profiles.summary(config())["car_profile_label"] == ""


def test_fingerprint_picks_a_profile_only_for_the_chosen_receiver(palisade):
  fingerprint = {"address": ADAPTER, "car_make": "Hyundai", "car_model": "Palisade"}
  assert car_profiles.resolve(config(car_fingerprint=fingerprint)) == ("palisade", "detected")
  assert car_profiles.resolve(config(car_fingerprint={**fingerprint, "address": "AA:BB:CC:DD:EE:09"})) == ("default", "standard")
  assert car_profiles.resolve(config(car_fingerprint={"address": ADAPTER, "car_make": "Hyundai", "car_model": "Sonata"})) == ("default", "standard")


def test_tuning_keeps_only_known_keys(palisade):
  tuned = car_profiles.tuning(config(car_profile_override="palisade"))
  assert tuned == {**car_profiles.DEFAULTS, "connect_delay_s": 10.0}


def test_override_pins_a_profile_or_forces_standard(palisade):
  fingerprint = {"address": ADAPTER, "car_make": "Hyundai", "car_model": "Palisade"}
  assert car_profiles.resolve(config(car_profile_override="default", car_fingerprint=fingerprint)) == ("default", "override")
  assert car_profiles.resolve(config(car_profile_override="palisade")) == ("palisade", "override")
  assert car_profiles.resolve(config(car_profile_override="gone")) == ("default", "standard")
  assert car_profiles.valid_override("") and car_profiles.valid_override("default") and car_profiles.valid_override("palisade")
  assert not car_profiles.valid_override("gone")


def test_old_config_files_load_with_standard_defaults(tmp_path):
  path = tmp_path / "config.json"
  path.write_text(json.dumps({"config_version": 6, "connection": "wired", "car_type": "bogus"}))
  loaded = identity_store.load_config(path)
  assert loaded["car_type"] == "" and loaded["car_profile_override"] == "" and loaded["car_fingerprint"] == {}
  assert loaded["connection"] == "wired"


def test_car_type_follows_the_connection_and_companion(sup):
  assert sup.status()["car_type"] == "builtin"
  sup.config.update(companion_address="car")
  assert sup.car_type() == "adapter"
  sup.config.update(car_type="builtin")
  assert sup.car_type() == "builtin"
  sup.config.update(connection="headunitpad")
  assert sup.car_type() == "app"


def test_choosing_a_car_type_sets_the_connection(sup):
  sup.set_car_type("adapter")
  assert sup.car_type() == "adapter" and sup.config["connection"] == "wireless"
  sup.set_car_type("app")
  assert sup.config["connection"] == "headunitpad" and sup.car_type() == "app"
  sup.set_car_type("builtin")
  assert sup.config["connection"] == "wireless" and sup.car_type() == "builtin"
  assert json.loads(identity_store.CONFIG_PATH.read_text())["car_type"] == "builtin"
  with pytest.raises(RuntimeError):
    sup.set_car_type("hovercraft")


def test_profile_changes_are_pinned_and_validated(sup, palisade):
  sup.set_car_profile("palisade")
  status = sup.status()
  assert status["car_profile"] == "palisade" and status["car_profile_source"] == "override" and status["car_profile_label"] == "Hyundai Palisade"
  sup.set_car_profile("")
  assert sup.status()["car_profile_source"] == "standard"
  with pytest.raises(RuntimeError):
    sup.set_car_profile("nope")


def test_setup_commands_need_a_signed_in_galaxy_source():
  assert {"set_car_type", "set_car_profile"} <= daemon.AUTHENTICATED_COMMANDS


def test_head_unit_fingerprint_is_remembered_and_dropped_with_the_car(sup):
  sup.config["receiver_address"] = ADAPTER
  sup._remember_fingerprint({"car_make": "Hyundai", "car_model": "Palisade", "head_unit_make": "MOBIS", "ignored": "x"})
  stored = json.loads(identity_store.CONFIG_PATH.read_text())["car_fingerprint"]
  assert stored == {"address": ADAPTER, "head_unit_make": "MOBIS", "car_make": "Hyundai", "car_model": "Palisade"}
  sup._remember_fingerprint({})
  assert sup.config["car_fingerprint"] == stored


def make_notifier():
  sup = Supervisor.__new__(Supervisor)
  sup._lock = threading.RLock()
  sup._pairing_step = Mock()
  sup.log = Mock()
  return sup


def test_a_paired_device_without_starport_is_explained_after_a_wait(monkeypatch):
  sup = make_notifier()
  receiver = {"address": ADAPTER, "name": "Civic"}
  devices = [{"address": ADAPTER, "name": "Civic", "paired": True, "starport": False}]
  clock = iter([100.0, 103.0, 109.0])
  monkeypatch.setattr("time.monotonic", lambda: next(clock))
  for _ in range(2):
    sup._note_missing_starport(devices, receiver)
  assert sup._pairing_note == ""
  sup._note_missing_starport(devices, receiver)
  assert "doesn't offer wireless Android Auto" in sup._pairing_note and "Wireless adapter" in sup._pairing_note
  sup._pairing_step.assert_called_once_with("no_starport")


def test_a_device_that_offers_starport_never_gets_a_note():
  sup = make_notifier()
  sup._no_starport_since = 5.0
  sup._note_missing_starport([{"address": ADAPTER, "name": "Car", "paired": True, "starport": True}], {"address": ADAPTER})
  assert sup._pairing_note == "" and sup._no_starport_since == 0.0


@pytest.mark.parametrize("end", ["interrupted", "cancelled", "expired"])
def test_a_pairing_that_connected_is_not_reported_as_interrupted(end):
  records = []
  attempt = br.PairingAttempt(180, sink=records.append)
  attempt.step("status", state="connected", prompt=False, approved=True)
  sup = make_notifier()
  sup._pairing_attempt = attempt
  sup._end_pairing(end, reason="Bluetooth lease released")
  assert records[0]["result"] == "paired_not_selected" and records[0]["steps"][-1]["ended"] == end


def test_a_pairing_that_never_connected_keeps_its_result():
  records = []
  attempt = br.PairingAttempt(180, sink=records.append)
  attempt.step("status", state="idle", prompt=False, approved=False)
  sup = make_notifier()
  sup._pairing_attempt = attempt
  sup._end_pairing("interrupted", reason="Galaxy session ended")
  assert records[0]["result"] == "interrupted"


def test_the_new_result_has_a_bugsink_report():
  attempt = br.PairingAttempt(180, sink=lambda record: None)
  attempt.step("status", state="connected", prompt=False, approved=True)
  event = br.pairing_event({**attempt.record, "result": "paired_not_selected", "duration_s": 5.0})
  assert event["fingerprint"] == ["starport-pairing", "paired_not_selected"] and event["level"] == "warning"


@pytest.mark.parametrize("stored, kept", [({"car_type": "adapter"}, True), ({"car_type": "", "companion_address": "AA:BB:CC:DD:EE:02"}, True),
                                          ({"car_type": "builtin", "companion_address": "AA:BB:CC:DD:EE:02"}, False), ({}, False),
                                          ({"car_type": "adapter", "car_profile_override": "aawireless"}, True)])
def test_adapter_owners_from_before_the_model_choice_keep_connecting(tmp_path, stored, kept):
  path = tmp_path / "config.json"
  path.write_text(json.dumps({"config_version": 6, **stored}))
  config = identity_store.load_config(path)
  assert (config["car_profile_override"] != "") is kept
  assert config["car_profile_override"] == stored.get("car_profile_override", "default" if kept else "")


def test_a_new_adapter_setup_is_not_migrated(tmp_path):
  path = tmp_path / "config.json"
  path.write_text(json.dumps({"config_version": identity_store.CONFIG_VERSION, "car_type": "adapter"}))
  assert identity_store.load_config(path)["car_profile_override"] == ""


def test_negotiate_delay_holds_discovery_until_the_car_side_is_up(identity, tmp_path, monkeypatch):
  # 2026-10-31 OTTOCAST: the dongle answers the version request ~2 s in, and a video setup right after is refused.
  import socket
  import time
  from openpilot.starpilot.system.starport import hw_encoder
  from openpilot.starpilot.system.starport.tests.fake_head_unit import FakeHeadUnit, rfcomm_head_unit

  class NoEncoder:
    backend = 'fixture'
    supports_nv12 = False
    last_encode_ms = 0.0

    def encode_rgba(self, pixels, *, keyframe=False):
      raise AssertionError("no frame may be encoded without video focus")

    def close(self): pass

  monkeypatch.setattr(hw_encoder, 'create_encoder', lambda width, height, **_kwargs: (NoEncoder(), 15))
  monkeypatch.setattr(car_profiles, "RULES", [{"id": "ottocast", "label": "OTTOCAST", "match": {}, "tuning": {"negotiate_delay_s": 0.4}}])
  cars = []
  stopping = threading.Event()

  def rfcomm(address, channel, timeout=15.0):
    hu = FakeHeadUnit(identity, native_focus=True)
    cars.append(hu)
    phone, car = socket.socketpair()

    def car_side():
      try:
        with car:
          rfcomm_head_unit(car, ("127.0.0.1", hu.port), pings=False)
          stopping.wait(1.0)
      except Exception:
        pass
    threading.Thread(target=car_side, daemon=True).start()
    return phone

  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, rfcomm)
  sup.config["car_profile_override"] = "ottocast"
  sup.start()
  try:
    deadline = time.monotonic() + 15
    while not cars or not cars[0].focus_requests:
      assert time.monotonic() < deadline, sup.status()
      time.sleep(0.05)
  finally:
    stopping.set()
    sup.stop()
    for hu in cars:
      hu.close()
  events = [json.loads(line) for path in (tmp_path / "aa" / "logs").glob("session-*.jsonl") for line in path.read_text().splitlines()]
  by_name = {e["event"]: e for e in events}
  assert by_name["car_profile_negotiate_delay"]["seconds"] == 0.4
  assert by_name["discovered"]["mono_s"] - by_name["authenticated"]["mono_s"] >= 0.4


def test_pairing_request_address_comes_from_the_wrapped_bluez_phone():
  # 2026-10-31 Honda: bluetooth_opened logged phone_address "" because LeaseAwarePhone has no adapter(); the car was
  # never asked to pair with the comma. The wrapped BlueZ phone owns that call.
  class Phone:
    def adapter(self):
      return "/org/bluez/hci0", {"Address": "aa:bb:cc:dd:ee:ff"}

  class Wrapped:
    phone = Phone()

  sup = Supervisor.__new__(Supervisor)
  sup.log = lambda *args, **kwargs: None  # a failed lookup is logged
  sup._bluez = Wrapped()
  assert sup._adapter_address() == "AA:BB:CC:DD:EE:FF"
  sup._bluez = Phone()
  assert sup._adapter_address() == "AA:BB:CC:DD:EE:FF"
  sup._bluez = object()
  assert sup._adapter_address() == ""
  sup._bluez = None
  assert sup._adapter_address() == ""


def test_unofficial_dongle_turns_on_the_handshake_experiments_only_when_chosen():
  assert car_profiles.valid_override("unofficial")
  assert car_profiles.tuning(config(car_profile_override="unofficial"))["bootstrap_experiments"] is True
  for other in (config(), config(car_profile_override="default"), config(car_profile_override="aawireless")):
    assert car_profiles.tuning(other)["bootstrap_experiments"] is False


@pytest.mark.parametrize("profile, expected", [("aawireless", "Waiting for the adapter to finish starting up"),
                                               ("default", "Connect your paired car in Galaxy Bluetooth first")])
def test_an_adapter_without_hands_free_yet_waits_or_asks_for_the_car(sup, profile, expected):
  bluez = Mock()
  bluez.device.return_value = {"address": ADAPTER, "paired": True, "starport": True, "uuids": []}
  sup._phone = lambda: bluez
  sup.config.update(connection="wireless", receiver_address=ADAPTER, receiver_name="AAWireless-4F2A", companion_address="",
                    car_type="adapter", car_profile_override=profile)
  with pytest.raises(RuntimeError, match=expected):
    sup._attempt(None)
  bluez.connect_device.assert_not_called()
