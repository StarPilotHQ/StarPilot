"""Mach-E display preference, warning priority, and ownership regressions."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from opendbc.can import CANParser
from opendbc.car import structs
from opendbc.car.ford import fordcan
from opendbc.car.ford.tests.test_mache_ui_owner import setup, UI
from opendbc.car.ford.values import CAR
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.car.ford.cluster_preferences import KEY, FordClusterPreference, read_choice
from openpilot.starpilot.controller_extensions import configure_controller
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change


def status(owner, state, command, parser):
  for _ in range(30):
    _, frames = owner.update(command.as_reader(), state, owner.frame * 10_000_000)
    for address, data, bus in frames:
      if address == 0x18A:
        parser.update([(owner.frame * 10_000_000, [(address, data, bus)])])
        return parser.vl['ACCDATA_3']
  raise AssertionError('No active cluster message')


def test_saved_opt_in_through_actual_controller_configuration_and_warning_priority():
  with OpenpilotPrefix():
    saved = Params()
    owner, state = setup()
    configure_controller(SimpleNamespace(CP=owner.CP, CC=owner), saved)
    assert isinstance(owner.hands_free_cluster_input, FordClusterPreference)
    assert not owner.hands_free_cluster_input.enabled
    parser = CANParser('ford_lincoln_base_pt', [('ACCDATA_3', 5)], 0)
    command = structs.CarControl(latActive=True)
    state.acc_tja_status_stock_values['FcwAudioWarn_B_Rq'] = 1
    assert status(owner, state, command, parser)['Tja_D_Stat'] == 2
    Path(saved.get_param_path(KEY)).write_bytes(b'1')
    owner.hands_free_cluster_input.refresh_settings()
    for _ in range(101):
      owner.update(command.as_reader(), state, owner.frame * 10_000_000)
    assert status(owner, state, command, parser)['Tja_D_Stat'] == 7
    command.hudControl.leftLaneDepart = True
    assert status(owner, state, command, parser)['Tja_D_Stat'] == 3
    command.hudControl.leftLaneDepart = False
    command.hudControl.rightLaneDepart = True
    assert status(owner, state, command, parser)['Tja_D_Stat'] == 4
    command.hudControl.rightLaneDepart = False
    fields = status(owner, state, command, parser)
    assert fields['FcwAudioWarn_B_Rq'] == 1
    command.hudControl.visualAlert = structs.CarControl.HUDControl.VisualAlert.fcw
    assert status(owner, state, command, parser)['FcwVisblWarn_B_Rq'] == 1
    command.latActive = False
    for _ in range(110):
      _, frames = owner.update(command.as_reader(), state, owner.frame * 10_000_000)
      assert not UI.intersection(address for address, _, _ in frames)
    Path(saved.get_param_path(KEY)).write_bytes(b'0')
    owner.hands_free_cluster_input.refresh_settings()
    command.latActive = True
    for _ in range(101):
      owner.update(command.as_reader(), state, owner.frame * 10_000_000)
    assert status(owner, state, command, parser)['Tja_D_Stat'] == 2


@pytest.mark.parametrize('raw', [None, b'0', b'1', b'yes', b'1\n', b'', b'1' * 20])
def test_preference_only_accepts_exact_dom_opt_in(raw):
  with OpenpilotPrefix():
    saved = Params()
    if raw is not None:
      Path(saved.get_param_path(KEY)).write_bytes(raw)
    enabled, observed, _, _ = read_choice(saved)
    assert enabled == (raw == b'1')
    assert observed == (b'' if raw is not None and len(raw) > 8 else raw)


@pytest.mark.parametrize('enabled,main,left,right,expected', [
  (True, True, False, False, 7), (True, True, True, False, 3), (True, True, False, True, 4),
  (True, True, True, True, 3), (False, True, False, False, 1),
  (False, True, True, False, 5), (False, True, False, True, 6), (False, False, False, False, 0),
])
def test_hands_free_flag_does_not_override_departure_standby_or_off(enabled, main, left, right, expected):
  owner, state = setup()
  hud = structs.CarControl.HUDControl(leftLaneDepart=left, rightLaneDepart=right)
  frame = fordcan.create_acc_ui_msg(owner.packer, owner.CAN, owner.CP, main, enabled, False, False, False,
                                    hud, state.acc_tja_status_stock_values, True)
  parser = CANParser('ford_lincoln_base_pt', [('ACCDATA_3', 5)], 0)
  parser.update([(1, [frame])])
  assert parser.vl['ACCDATA_3']['Tja_D_Stat'] == expected


def test_shared_feature_ui_saves_opt_in_and_rejects_onroad_stale_or_wrong_vehicle():
  with OpenpilotPrefix():
    saved = Params()
    controller, _ = setup()
    parked = [True]
    owner = FeatureSettingsOwner(saved, lambda group: parked[0],
                                 vehicle_params=lambda: controller.CP,
                                 vehicle_fingerprint=lambda: str(controller.CP.carFingerprint))
    row = next(r for r in owner.snapshot('torque/bluepilot', parked=True, system_long=True, lateral_context=True, metric=False).rows if r.key == KEY)
    assert row.value == 'Off' and row.available
    request = row_change(row, 1)
    assert request is not None
    parked[0] = False
    assert not owner.apply(request)
    parked[0] = True
    assert not owner.apply(replace(request, vehicle_fingerprint=str(CAR.FORD_F_150_MK14)))
    assert owner.apply(request)
    assert read_choice(saved)[0] == 1
    assert not owner.apply(request)
    row = next(r for r in owner.snapshot('torque/bluepilot', parked=True, system_long=True, lateral_context=True, metric=False).rows if r.key == KEY)
    assert row.value == 'On'
