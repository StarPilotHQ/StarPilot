"""Rear fog is a fresh receive-only telltale, never a vehicle command."""
from dataclasses import replace
from pathlib import Path
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.car.ford import rear_fog
from openpilot.starpilot.ui.ford_rear_fog_feature import FordRearFogFeature, KEY
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.onroad_customization import PARAM_KEY, default_document, validate_document, DEFAULT_WIDGET_ORDER


def cp(brand="ford", multi=False):
  return NS(brand=brand, carFingerprint="FORD_F_150_LIGHTNING_MK1", safetyConfigs=(
    ([NS(safetyModel="noOutput", safetyParam=0)] if multi else []) + [NS(safetyModel="ford", safetyParam=2)]))


def event(data=b"\x02" + b"\x00" * 7, *, bus=0, address=0x3B3, stamp=10_000_000_000, valid=True):
  return NS(valid=valid, logMonoTime=stamp, can=[NS(address=address, src=bus, dat=data)])


@pytest.mark.parametrize("rear,front", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_actual_dbc_bit_is_rear_not_front(rear, front):
  from opendbc.can.packer import CANPacker
  packet = CANPacker("ford_lincoln_base_pt").make_can_msg("BodyInfo_3_FD1", 0, {
    "FogLghtRearOn_B_Stat": rear, "FogLghtFrontOn_B_Stat": front})
  assert packet[0] == 0x3B3 and len(packet[1]) == 8
  assert bool(packet[1][0] & 2) == bool(rear)
  receiver = rear_fog.RearFogReceiver()
  receiver.observe(event(packet[1]), 0, 10_000_000_000)
  assert receiver.on == bool(rear)


@pytest.mark.parametrize("changes", [{"bus": 2}, {"bus": 128}, {"bus": 192}, {"address": 0x3B2},
                                    {"data": b"\x02"}, {"valid": False}, {"stamp": 0},
                                    {"stamp": 8_499_999_999}, {"stamp": 10_000_000_001}])
def test_untrusted_or_stale_frames_cannot_turn_telltale_on(changes):
  receiver = rear_fog.RearFogReceiver()
  receiver.observe(event(**changes), 0, 10_000_000_000)
  assert not receiver.on and receiver.source_ns == 0


def test_clear_and_out_of_order_frames():
  receiver = rear_fog.RearFogReceiver()
  receiver.observe(event(), 0, 10_000_000_000)
  receiver.observe(event(b"\x00" * 8, stamp=10_100_000_000), 0, 10_100_000_000)
  receiver.observe(event(), 0, 10_100_000_000)
  assert not receiver.on and receiver.source_ns == 10_100_000_000


@pytest.mark.parametrize("multi,bus", [(False, 0), (True, 4)])
def test_bounded_receive_only_lifecycle(monkeypatch, multi, bus):
  from openpilot.cereal import messaging
  monkeypatch.setattr(rear_fog, "boot_time_ns", lambda: 10_000_000_000)
  socket = Mock()
  subscribe = Mock(return_value=socket)
  receive = Mock(return_value=event(bus=bus))
  monkeypatch.setattr(messaging, "sub_sock", subscribe)
  monkeypatch.setattr(messaging, "recv_one_or_none", receive)
  receiver = rear_fog.RearFogReceiver()
  assert receiver.sample(cp(multi=multi), active=True) == (True, 10_000_000_000)
  subscribe.assert_called_once_with("can", conflate=False)
  assert receive.call_count == 32
  receive.return_value = None
  monkeypatch.setattr(rear_fog, "boot_time_ns", lambda: 11_500_000_001)
  assert receiver.sample(cp(multi=multi), active=True)[0] is False
  assert receiver.sample(cp(multi=multi), active=False) == (False, 0)
  assert receiver.socket is None
  subscribe.reset_mock()
  assert receiver.sample(cp("toyota"), active=True) == (False, 0)
  subscribe.assert_not_called()


def test_topology_fails_closed():
  assert rear_fog.main_bus(None) is None
  vehicle = cp(multi=True)
  vehicle.safetyConfigs[0].safetyParam = 1
  assert rear_fog.main_bus(vehicle) is None


def owner(tmp_path):
  params = Params(str(tmp_path))
  vehicle = cp()
  return NS(params=params, vehicle_params=lambda: vehicle, vehicle_fingerprint=lambda: vehicle.carFingerprint,
            authority=lambda _: True)


def request(feature, value="Off"):
  row = feature.rows(True)[0]
  return FeatureSettingsRequest(KEY, row.source, value, capability=row.capability,
                                vehicle_fingerprint=feature.owner.vehicle_fingerprint())


def test_vehicle_toggle_and_galaxy_share_visibility_and_preserve_positions(tmp_path):
  feature = FordRearFogFeature(owner(tmp_path))
  assert feature.rows(True)[0].value == "On"
  assert feature.apply(request(feature))
  saved = feature.owner.params.get(PARAM_KEY)
  assert all(not p["rear_fog"]["enabled"] for p in saved["layouts"].values())
  saved["layouts"]["compact"]["rear_fog"].update(enabled=True, x=230)
  feature.owner.params.put(PARAM_KEY, saved, block=True)
  assert feature.rows(True)[0].value == "On"
  assert feature.apply(request(feature, "On"))
  saved = feature.owner.params.get(PARAM_KEY)
  assert saved["layouts"]["compact"]["rear_fog"]["x"] == 230
  assert all(p["rear_fog"]["enabled"] for p in saved["layouts"].values())


@pytest.mark.parametrize("guard", ["moving", "fingerprint", "capability", "source", "malformed"])
def test_toggle_preserves_guards_and_saved_document(tmp_path, guard):
  feature = FordRearFogFeature(owner(tmp_path))
  action = request(feature)
  if guard == "moving":
    feature.owner.authority = lambda _: False
  elif guard == "fingerprint":
    action = replace(action, vehicle_fingerprint="other")
  elif guard == "capability":
    action = replace(action, capability=("other", 0))
  else:
    Path(feature.owner.params.get_param_path(PARAM_KEY)).write_bytes(b"{" if guard == "malformed" else json.dumps(default_document()).encode())
  before = Path(feature.owner.params.get_param_path(PARAM_KEY))
  before = before.read_bytes() if before.exists() else None
  assert not feature.apply(action)
  after = Path(feature.owner.params.get_param_path(PARAM_KEY))
  assert (after.read_bytes() if after.exists() else None) == before


def test_layout_migrates_without_touching_existing_widgets():
  document = default_document()
  document["layouts"]["large"]["current_speed"]["x"] = 100
  for profile in document["layouts"].values():
    del profile["rear_fog"]
  document["widgetOrder"] = {p: [k for k in order if k != "rear_fog"] for p, order in DEFAULT_WIDGET_ORDER.items()}
  result = validate_document(document)
  assert result["layouts"]["large"]["current_speed"]["x"] == 100
  for profile in result["layouts"]:
    assert result["layouts"][profile]["rear_fog"]["enabled"]
    assert "rear_fog" in result["widgetOrder"][profile]


def test_catalog_is_ford_only(tmp_path):
  from openpilot.starpilot.galaxy.onroad_layout import OnroadLayoutOwner
  feature_owner = owner(tmp_path)
  layout = OnroadLayoutOwner(feature_owner.params, lambda: True, feature_owner.vehicle_params)
  assert not layout.snapshot()["metadata"]["profiles"]["compact"]["widgets"]["rear_fog"]["hidden"]
  layout.vehicle_params = lambda: cp("toyota")
  assert layout.snapshot()["metadata"]["profiles"]["compact"]["widgets"]["rear_fog"]["hidden"]
  feature_owner.vehicle_params = layout.vehicle_params
  assert not FordRearFogFeature(feature_owner).rows(True)


def test_native_widget_visibility_and_movement(monkeypatch):
  from openpilot.starpilot.ui.onroad_rear_fog import render_rear_fog
  from openpilot.starpilot.ui import onroad_rear_fog
  from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
  from openpilot.starpilot.ui.raylib import rl
  monkeypatch.setattr(onroad_rear_fog, "boot_time_ns", lambda: 10_000_000_000)
  draw = Mock()
  monkeypatch.setattr(rl, "draw_line_ex", draw)
  monkeypatch.setattr(rl, "draw_circle_v", Mock())
  state = OnroadState(False, True, 0, None, SpeedLimitObservation(), customization=default_document(),
                      ford_rear_fog_on=True, ford_rear_fog_source_ns=10_000_000_000)
  for profile in ("large", "compact"):
    draw.reset_mock()
    render_rear_fog(state, profile)
    assert draw.call_count > 0
    first = draw.call_args_list[0].args[0]
    state.customization["layouts"][profile]["rear_fog"]["x"] += 10
    draw.reset_mock()
    render_rear_fog(state, profile)
    assert draw.call_args_list[0].args[0].x == pytest.approx(first.x + 10)
    draw.reset_mock()
    render_rear_fog(replace(state, ford_rear_fog_source_ns=1), profile)
    draw.assert_not_called()
    state.customization["layouts"][profile]["rear_fog"]["enabled"] = False
    render_rear_fog(state, profile)
    draw.assert_not_called()


def test_actual_vehicle_page_dispatches_shared_toggle(tmp_path):
  from opendbc.car.structs import CarParams
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
  from openpilot.starpilot.ui.feature_settings_state import FeaturePage, row_change
  vehicle = CarParams.new_message(brand="ford", carFingerprint="FORD_F_150_LIGHTNING_MK1",
                                  safetyConfigs=[{"safetyModel": "ford", "safetyParam": 2}])
  params = Params(str(tmp_path))
  settings = FeatureSettingsOwner(params, lambda _: True, vehicle_fingerprint=lambda: vehicle.carFingerprint,
                                  vehicle_params=lambda: vehicle)
  state = settings.snapshot(FeaturePage.VEHICLE, parked=True, system_long=False, lateral_context=False, metric=False)
  row = next(row for row in state.rows if row.key == KEY)
  assert row.available and row.value == "On"
  action = row_change(row, -1)
  assert action is not None and settings.apply(action)
  assert all(not p["rear_fog"]["enabled"] for p in params.get(PARAM_KEY)["layouts"].values())


def test_runtime_snapshot_wires_receive_only_status():
  from openpilot.starpilot.ui.runtime_app import StarShellSession
  from openpilot.starpilot.ui.shell import ShellSnapshot, ShellMode
  from openpilot.starpilot.ui.presentation import Profile
  from openpilot.starpilot.ui.settings_state import Destination
  from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
  onroad = OnroadState(False, True, 0, None, SpeedLimitObservation(), customization=default_document())
  session = StarShellSession.__new__(StarShellSession)
  session.adapter = Mock()
  session.adapter.ui_state = NS(sm=NS(frame=1), CP=cp(), started=True)
  session.adapter.build.return_value = ShellSnapshot(ShellMode.ONROAD, Mock(), Mock(), onroad)
  session.selected = Destination.STAR
  session.compact_y = session.compact_scroll_x = 0
  session.sidebar_expanded = True
  session.profile = Profile.COMPACT
  session._snapshot_cache = None
  session.rear_fog = Mock()
  session.rear_fog.sample.return_value = (True, 10_000_000_000)
  snapshot = session.snapshot(ShellMode.ONROAD)
  assert snapshot.onroad.ford_rear_fog_on and snapshot.onroad.ford_rear_fog_source_ns == 10_000_000_000
  session.rear_fog.sample.assert_called_once_with(session.adapter.ui_state.CP, active=True)
  session.adapter.ui_state.sm.frame += 1
  session.adapter.ui_state.started = False
  session.snapshot(ShellMode.ONROAD)
  assert session.rear_fog.sample.call_args.kwargs == {"active": False}
