"""Deleting a Bluetooth pairing also clears it as Android Auto's car, companion and cached RFCOMM channel."""

import json

import pytest

from openpilot.starpilot.system.starport import daemon, identity as identity_store, protocol
from openpilot.starpilot.system.starport.tests.fake_head_unit import make_identity
from openpilot.starpilot.system.starport.tests.test_starport import make_supervisor

ADAPTER = "AA:BB:CC:DD:EE:01"  # make_supervisor selects this one as the receiver
CAR = "AA:BB:CC:DD:EE:02"
OTHER = "AA:BB:CC:DD:EE:03"


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("identity"))


@pytest.fixture
def sup(identity, tmp_path, monkeypatch):
  sup, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: None)
  with sup._lock:
    sup.config.update(companion_address=CAR, companion_name="Civic", rfcomm_cache={ADAPTER: 8, OTHER: 5})
    identity_store.save_config(sup.config)
  return sup


def saved():
  return json.loads(identity_store.CONFIG_PATH.read_text())


def test_forgetting_the_chosen_receiver_clears_it_its_companion_and_its_channel(sup):
  assert daemon.handle(sup, {"command": "forget_receiver", "address": ADAPTER.lower()}) == {"cleared": True}
  for config in (sup.config, saved()):
    assert config["receiver_address"] == config["receiver_name"] == ""
    assert config["companion_address"] == config["companion_name"] == ""
    assert config["rfcomm_cache"] == {OTHER: 5}


def test_forgetting_the_companion_keeps_the_receiver(sup):
  assert sup.forget_receiver(CAR) is False
  assert saved()["receiver_address"] == ADAPTER
  assert saved()["companion_address"] == ""


def test_forgetting_a_device_with_only_a_cached_channel(sup):
  assert sup.forget_receiver(OTHER) is False
  assert saved()["rfcomm_cache"] == {ADAPTER: 8}
  assert saved()["receiver_address"] == ADAPTER and saved()["companion_address"] == CAR


def test_forgetting_an_unrelated_device_writes_nothing(sup):
  before = identity_store.CONFIG_PATH.stat().st_mtime_ns
  assert sup.forget_receiver("AA:BB:CC:DD:EE:99") is False
  assert identity_store.CONFIG_PATH.stat().st_mtime_ns == before


def test_forget_car_edits_the_saved_config_when_the_daemon_is_not_running(tmp_path, monkeypatch):
  monkeypatch.setattr(identity_store, "CONFIG_PATH", tmp_path / "config.json")
  identity_store.save_config({**identity_store.DEFAULT_CONFIG, "receiver_address": ADAPTER, "receiver_name": "AAWireless",
                              "companion_address": CAR, "companion_name": "Civic", "rfcomm_cache": {ADAPTER: 8}})
  client = protocol.StarPortClient(socket_path=str(tmp_path / "missing.sock"))
  assert protocol.forget_car(OTHER, client) is False
  assert protocol.forget_car(CAR, client) is False
  assert saved()["companion_address"] == "" and saved()["receiver_address"] == ADAPTER
  assert protocol.forget_car(ADAPTER.lower(), client) is True
  assert saved()["receiver_address"] == "" and saved()["rfcomm_cache"] == {}


def test_the_version_reply_a_car_started_with_is_kept_and_forgotten_with_it(sup):
  sup._remember_reply(ADAPTER, True)
  sup._remember_reply(OTHER, True)
  assert saved()["modern_reply_cache"] == {ADAPTER: True, OTHER: True}
  sup._remember_reply(OTHER, False)
  assert saved()["modern_reply_cache"] == {ADAPTER: True}
  daemon.handle(sup, {"command": "forget_receiver", "address": ADAPTER})
  assert saved()["modern_reply_cache"] == {} and ADAPTER not in sup._modern_reply
