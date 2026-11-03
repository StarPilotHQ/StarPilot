"""A car that is the only paired Android Auto device is chosen by housekeeping, without touching anything the owner set up."""
import pytest

from openpilot.starpilot.system.starport import identity as identity_store
from openpilot.starpilot.system.starport.tests.fake_head_unit import make_identity
from openpilot.starpilot.system.starport.tests.test_starport import make_supervisor

CAR, OTHER = "AA:BB:CC:DD:EE:07", "AA:BB:CC:DD:EE:08"


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("identity"))


@pytest.fixture
def sup(identity, tmp_path, monkeypatch):
  supervisor, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: None)
  with supervisor._lock:
    supervisor.config.update(receiver_address="", receiver_name="", car_type="builtin")
  class Recorder:
    recent = ()

    def __init__(self):
      self.events = []

    def __call__(self, name, **values):
      self.events.append((name, values))

  supervisor.log = Recorder()
  supervisor.events = supervisor.log.events
  supervisor._projection_enabled = lambda: True
  supervisor.baseline = supervisor._phone().acquired   # make_supervisor chose a car once already
  return supervisor


def names(sup):
  return [name for name, _ in sup.events]


def test_the_only_candidate_is_chosen_trusted_and_the_projection_role_given_back(sup):
  bluez = sup._phone()
  bluez.paired_devices = [(CAR, "Civic", True), (OTHER, "Headphones", False)]
  sup._adopt_only_car(1000.0)
  assert sup.config["receiver_address"] == CAR and sup.config["receiver_name"] == "Civic"
  assert identity_store.load_config()["receiver_address"] == CAR
  # the phone look is given back; the standby gateway stays
  assert bluez.trusted[-1] == CAR and bluez.acquired == sup.baseline + 1 and bluez.class_restored >= 1
  assert "car_adopted" in names(sup) and sup._candidates == []


def test_two_candidates_are_never_guessed_and_nothing_is_acquired(sup):
  bluez = sup._phone()
  bluez.paired_devices = [(CAR, "Civic", True), (OTHER, "Wagon", True)]
  for tick in range(5):
    sup._adopt_only_car(1000.0 + 25 * tick)
  assert sup.config["receiver_address"] == "" and bluez.acquired == sup.baseline
  assert [car["address"] for car in sup.status()["car_candidates"]] == [CAR, OTHER]
  assert names(sup).count("car_adopt_ambiguous") == 1   # said once, not every 20 s


def test_the_car_used_as_bluetooth_audio_is_left_to_the_owner(sup):
  sup._bt_client = type("C", (), {"status": lambda s: type("St", (), {"selected_audio": CAR.lower()})()})()
  sup._phone().paired_devices = [(CAR, "Civic", True)]
  for tick in range(3):
    sup._adopt_only_car(1000.0 + 25 * tick)
  assert sup.config["receiver_address"] == "" and sup._phone().acquired == sup.baseline
  assert names(sup).count("car_adopt_skipped") == 1
  assert [car["address"] for car in sup.status()["car_candidates"]] == [CAR]   # still offered through the setup screen's Use button


def test_when_the_audio_selection_cannot_be_read_the_owners_setup_is_left_alone(sup):
  def broken():
    raise RuntimeError("bluetooth service down")
  sup._bt_client = type("C", (), {"status": lambda s: broken()})()
  sup._phone().paired_devices = [(CAR, "Civic", True)]
  sup._adopt_only_car(1000.0)
  assert sup.config["receiver_address"] == ""


@pytest.mark.parametrize("change", [
  {"receiver_address": "AA:00:00:00:00:99"}, {"connection": "wired"}, {"car_type": "adapter"},
])
def test_it_does_nothing_once_a_car_is_chosen_or_for_wired_and_adapter_setups(sup, change):
  with sup._lock:
    sup.config.update(change)
  sup._phone().paired_devices = [(CAR, "Civic", True)]
  sup._adopt_only_car(1000.0)
  assert sup.config["receiver_address"] in ("", "AA:00:00:00:00:99") and sup._phone().acquired == sup.baseline


def test_it_does_nothing_while_a_session_runs_projection_is_off_or_it_ran_a_moment_ago(sup):
  sup._phone().paired_devices = [(CAR, "Civic", True)]
  sup._projection_enabled = lambda: False
  sup._adopt_only_car(1000.0)
  assert sup.config["receiver_address"] == ""
  sup._projection_enabled = lambda: True
  sup._adopt_only_car(2000.0)
  assert sup.config["receiver_address"] == CAR
  sup.config["receiver_address"] = ""
  sup._adopt_only_car(2005.0)      # inside the 20 s interval
  assert sup.config["receiver_address"] == ""


def test_an_unreadable_bluetooth_service_is_reported_once(sup):
  def broken():
    raise RuntimeError("no shared owner")
  sup._phone = broken
  for tick in range(4):
    sup._adopt_only_car(1000.0 + 25 * tick)
  assert names(sup).count("car_adopt_unavailable") == 1
