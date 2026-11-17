from types import SimpleNamespace as NS
import threading

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.car.ford.bluepilot import BluePilotInputs
from openpilot.starpilot.car.ford.cluster_preferences import FordClusterPreference
from openpilot.starpilot.car.ford.controller_selection import FordController


class ModelInputs:
  def __init__(self, _):
    self.data = {"modelV2": NS(), "vehicleParameters": NS()}
    self.seen = dict.fromkeys(self.data, False)

  def __getitem__(self, name):
    return self.data[name]

  def update(self, _):
    pass


@pytest.fixture
def inputs(tmp_path, monkeypatch):
  from openpilot.starpilot.car.ford import bluepilot
  monkeypatch.setattr(bluepilot.messaging, "SubMaster", ModelInputs)
  params = Params(str(tmp_path))
  params.put("FordPrefLateralControl", 2, block=True)
  return params, BluePilotInputs(params)


def forbid_reads(params, monkeypatch):
  def forbidden(*_):
    raise AssertionError("Control loop read Ford settings")
  monkeypatch.setattr(params, "get_param_path", forbidden)


def test_controller_refresh_frame_never_reads_params(inputs, monkeypatch):
  params, owner = inputs
  forbid_reads(params, monkeypatch)
  owner.frames = 300
  owner.update()
  assert owner.controller_choice == FordController.BLUEPILOT_CURVATURE


def test_worker_publishes_mode_and_settings_together(inputs, monkeypatch):
  params, owner = inputs
  params.put("FordPrefLateralControl", 3, block=True)
  owner.refresh_settings()
  assert owner.controller_choice == FordController.BLUEPILOT_CURVATURE
  forbid_reads(params, monkeypatch)
  owner.frames = 300
  frame = owner.update()
  assert owner.controller_choice == FordController.STARPILOT
  assert {p.key: p.value for p in frame.params}["FordPrefLateralControl"] == b"1"


def test_unreadable_controller_choice_still_disables_bluepilot(inputs, monkeypatch):
  params, owner = inputs
  with open(params.get_param_path("FordPrefLateralControl"), "wb") as stream:
    stream.write(b"invalid")
  owner.refresh_settings()
  forbid_reads(params, monkeypatch)
  owner.frames = 300
  frame = owner.update()
  assert owner.controller_choice is None
  assert {p.key: p.value for p in frame.params}["disable_BP_lat_UI"] == b"1"


def test_cluster_refresh_frame_never_reads_params(tmp_path, monkeypatch):
  params = Params(str(tmp_path))
  params.put_bool("FordHandsFreeCluster", True, block=True)
  owner = FordClusterPreference(params)
  params.put_bool("FordHandsFreeCluster", False, block=True)
  owner.refresh_settings()
  assert owner.enabled
  forbid_reads(params, monkeypatch)
  owner.frames = 100
  owner.update()
  assert not owner.enabled


def test_existing_card_settings_thread_refreshes_both_ford_sources(monkeypatch):
  from openpilot.selfdrive.car import card
  stop = threading.Event()
  refreshed = []
  instance = card.Car.__new__(card.Car)
  vars(instance).update(
    params=NS(get_bool=lambda _: False, get=lambda *_, **__: None),
    CP=NS(openpilotLongitudinalControl=True), aol_card_intent=None,
    read_slc_configuration=lambda: None, v_cruise_helper=NS(),
    CI=NS(CC=NS(bluepilot_inputs=NS(refresh_settings=lambda: refreshed.append("lateral")),
                hands_free_cluster_input=NS(refresh_settings=lambda: refreshed.append("cluster")))),
  )
  monkeypatch.setattr(card, "read_cruise_intervals", lambda *_: None)
  monkeypatch.setattr(card.time, "sleep", lambda _: stop.set())
  instance.params_thread(stop)
  assert refreshed == ["lateral", "cluster"]
