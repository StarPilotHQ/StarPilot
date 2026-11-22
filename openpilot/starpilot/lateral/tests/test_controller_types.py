"""Explicit controller choices reach startup without changing vehicle authority."""
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from opendbc.car import DT_CTRL
from opendbc.car.car_helpers import interfaces
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.lateral_controller_factory import create_lateral_controller
from openpilot.starpilot.lateral.controller_selection import (
  DOCUMENT_KEY, ControllerMode, parse_document, prepare_controller, replace_mode,
  selection_from_bytes, torque_choice_cp,
)
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences

PID_CARS = ('CHRYSLER_PACIFICA_2018_HYBRID', 'CHRYSLER_PACIFICA_2019_HYBRID',
            'HONDA_CIVIC', 'VOLKSWAGEN_GOLF_MK7')


def factory(identity):
  return interfaces[identity].get_non_essential_params(identity)


@pytest.mark.parametrize('identity', PID_CARS)
@pytest.mark.parametrize('mode', list(ControllerMode))
def test_explicit_choice_selects_real_controller_and_preserves_authority(identity, mode):
  cp = factory(identity)
  assert cp.lateralTuning.which() == 'pid'
  baseline = cp.to_dict()
  assert torque_choice_cp(cp) is not None
  assert cp.to_dict() == baseline
  assert prepare_controller(cp, None).to_dict() == baseline
  raw = replace_mode(None, cp, mode, force_torque=True)
  prepared = VehicleStartupPreferences(controller_selection=raw).prepare(cp)
  actual = prepared.to_dict()
  actual.pop('lateralTuning')
  expected = baseline.copy()
  expected.pop('lateralTuning')
  assert actual == expected
  assert prepared.lateralTuning.which() == 'torque'
  selection = selection_from_bytes(prepared, raw)
  ci = interfaces[identity](prepared)
  controller = create_lateral_controller(prepared.as_reader(), ci, DT_CTRL, mode=selection.mode)
  assert type(controller).__name__ == 'LatControlTorque'
  assert (controller.starpilot_extension is not None) == (mode == ControllerMode.STARPILOT)
  assert controller.torque_params.to_dict() == prepared.lateralTuning.torque.to_dict()
  restored_raw = replace_mode(raw, prepared, ControllerMode.STANDARD)
  stock = prepare_controller(factory(identity), restored_raw)
  assert stock.to_dict() == baseline
  stock_controller = create_lateral_controller(stock.as_reader(), interfaces[identity](stock), DT_CTRL, mode=ControllerMode.STANDARD)
  assert type(stock_controller).__name__ == 'LatControlPID'


@pytest.mark.parametrize('identity', PID_CARS)
def test_developer_choice_saved_and_startup_snapshot_is_frozen(tmp_path, identity):
  cp = factory(identity)
  params = Params(str(tmp_path))
  owner = FeatureSettingsOwner(params, lambda _: True, vehicle_fingerprint=lambda: identity, vehicle_params=lambda: cp)
  state = owner.snapshot('torque/developer', parked=True, system_long=False, lateral_context=True, metric=False)
  current = next(r for r in state.rows if r.key == DOCUMENT_KEY)
  assert current.available and current.value == 'Stock Controller'
  assert current.choices == ('Stock Controller', 'Torque Controller', 'StarPilot Controller')
  frozen = VehicleStartupPreferences.read(params, enabled=True)
  request = FeatureSettingsRequest(current.key, current.source, 'Torque Controller', vehicle_fingerprint=identity,
                                  capability=current.capability, dependencies=current.dependencies)
  assert owner.apply(request)
  assert not owner.apply(request)
  assert frozen.prepare(factory(identity)).lateralTuning.which() == 'pid'
  saved = VehicleStartupPreferences.read(params, enabled=True)
  params.remove(DOCUMENT_KEY)
  assert saved.prepare(factory(identity)).lateralTuning.which() == 'torque'
  assert VehicleStartupPreferences.read(params, enabled=False).prepare(factory(identity)).lateralTuning.which() == 'pid'
  assert cp.lateralTuning.which() == 'pid'
  assert not owner.apply(replace(request, value='Invented Controller'))


@pytest.mark.parametrize('field,value', [('steerControlType', 'angle'), ('steerControlType', 'curvature'),
  ('brand', 'toyota'), ('notCar', True), ('passive', True), ('dashcamOnly', True), ('carFingerprint', 'UNKNOWN')])
def test_forced_choice_refuses_unsupported_configuration(field, value):
  cp = factory(PID_CARS[0])
  raw = replace_mode(None, cp, ControllerMode.STARPILOT, force_torque=True)
  setattr(cp, field, value)
  before = cp.to_dict()
  assert torque_choice_cp(cp) is None
  assert prepare_controller(cp, raw).to_dict() == before
  with pytest.raises(ValueError):
    replace_mode(None, cp, ControllerMode.STANDARD, force_torque=True)


def test_missing_calibration_unsafe_profile_and_malformed_choice_do_not_convert():
  cp = factory(PID_CARS[0])
  original = cp.to_dict()
  raw = replace_mode(None, cp, ControllerMode.STARPILOT, force_torque=True)
  with patch('opendbc.car.interfaces.CarInterfaceBase.configure_torque_tune', side_effect=KeyError('missing')):
    assert torque_choice_cp(cp) is None
    assert prepare_controller(cp, raw).to_dict() == original
  cp.safetyConfigs[0].safetyModel = 'noOutput'
  assert torque_choice_cp(cp) is None
  cp = factory(PID_CARS[0])
  for bad in (b'{}', b'x' * 4097, raw.replace(b'true', b'1'), raw.replace(b'true', b'"true"')):
    assert selection_from_bytes(cp, bad).source == 'invalid'
    assert prepare_controller(cp, bad).to_dict() == original
  document = json.loads(raw)
  del document['vehicles'][PID_CARS[0]]['forceTorque']
  legacy = json.dumps(document).encode()
  assert prepare_controller(cp, legacy).to_dict() == original
  assert prepare_controller(factory(PID_CARS[1]), raw).lateralTuning.which() == 'pid'
  assert parse_document(raw)['vehicles'][PID_CARS[0]]['forceTorque'] is True


@pytest.mark.parametrize('identity', PID_CARS[:2])
@pytest.mark.parametrize('mode', list(ControllerMode))
def test_pacifica_choice_reaches_actual_controls(identity, mode):
  import os
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.controls.controlsd import Controls
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1'}):
    params = Params()
    cp = factory(identity)
    raw = replace_mode(None, cp, mode, force_torque=True)
    params.put(DOCUMENT_KEY, json.loads(raw), block=True)
    preferences = VehicleStartupPreferences.read(params, enabled=True)
    preferences.prepare(cp)
    ci = interfaces[identity](cp)
    preferences.finalize(cp)
    preferences.configure_controller(ci)
    params.put('CarParams', cp.to_bytes(), block=True)
    controls = Controls()
    assert type(controls.LaC).__name__ == 'LatControlTorque'
    assert (controls.LaC.starpilot_extension is not None) == (mode == ControllerMode.STARPILOT)
    assert controls.CP.to_dict()['safetyConfigs'] == cp.to_dict()['safetyConfigs']


def test_every_eligible_factory_pid_can_select_and_restore():
  supported = []
  for identity in interfaces:
    cp = factory(identity)
    if cp.lateralTuning.which() != 'pid' or torque_choice_cp(cp) is None:
      continue
    before = cp.to_dict()
    raw = replace_mode(None, cp, ControllerMode.STANDARD, force_torque=True)
    prepare_controller(cp, raw)
    assert cp.lateralTuning.which() == 'torque', identity
    original = factory(identity)
    prepare_controller(original, replace_mode(raw, cp, ControllerMode.STANDARD))
    assert original.to_dict() == before, identity
    supported.append(identity)
  assert set(PID_CARS).issubset(supported)
