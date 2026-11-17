import json
import math
from types import SimpleNamespace

import pytest

from opendbc.car import DT_CTRL, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import (
  DOCUMENT_KEY, ControllerMode, default_selection, learning_allowed,
  parse_document, policy_for, read_selection, replace_mode,
)
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change


def cp_for(identity):
  return interfaces[identity].get_non_essential_params(identity)


def owner_for(params, cp):
  return FeatureSettingsOwner(params, lambda group: True, vehicle_fingerprint=lambda: str(cp.carFingerprint),
                              vehicle_params=lambda: cp)


def test_niro_2023_choice_is_visible_and_saves(tmp_path):
  params = Params(str(tmp_path))
  cp = cp_for(CAR.KIA_NIRO_EV_2ND_GEN)
  assert cp.steerControlType == structs.CarParams.SteerControlType.torque
  assert cp.lateralTuning.which() == 'torque'
  owner = owner_for(params, cp)
  snapshot = owner.snapshot('torque', parked=True, system_long=False, lateral_context=False, metric=False)
  row = next((row for row in snapshot.rows if row.key == DOCUMENT_KEY), None)
  assert row is not None, '2023 Niro EV must expose the controller selector'
  assert row.choices == ('Stock Controller', 'StarPilot Controller')
  assert row.value == 'Stock Controller'
  request = row_change(row)
  assert request is not None and request.value == 'StarPilot Controller'
  assert owner.apply(request)
  assert read_selection(params, cp).mode == ControllerMode.STARPILOT
  assert not owner.apply(request), 'stale expected bytes must remain rejected'
  ci = interfaces[CAR.KIA_NIRO_EV_2ND_GEN](cp)
  controller = LatControlTorque(cp.as_reader(), ci, DT_CTRL, controller_mode=read_selection(params, cp).mode)
  assert selected_policy(controller) is not None
  assert controller.controller_policy == 'generic'
  assert controller.torque_params.to_dict() == cp.lateralTuning.torque.to_dict()


def test_every_registered_torque_tune_can_save_both_choices():
  from openpilot.starpilot.lateral.controller_selection import generic_torque_supported
  checked = []
  for identity in interfaces:
    cp = cp_for(identity)
    if not generic_torque_supported(cp):
      continue
    assert policy_for(cp) is not None, identity
    for mode in ControllerMode:
      raw = replace_mode(None, cp, mode)
      assert parse_document(raw)['vehicles'][str(identity)]['brand'] == str(cp.brand)
      from openpilot.starpilot.lateral.controller_selection import selection_from_bytes
      selected = selection_from_bytes(cp, raw)
      assert selected.mode == mode and selected.source == 'saved', identity
    checked.append(str(identity))
  assert str(CAR.KIA_NIRO_EV_2ND_GEN) in checked
  assert len(checked) > 100
  print('Registered torque controller selection coverage:', len(checked))


def test_saved_vehicle_choices_remain_independent():
  from opendbc.car.honda.values import CAR as HONDA
  niro = cp_for(CAR.KIA_NIRO_EV_2ND_GEN)
  honda = cp_for(HONDA.HONDA_FIT_4G)
  raw = replace_mode(None, niro, ControllerMode.STARPILOT)
  raw = replace_mode(raw, honda, ControllerMode.STANDARD)
  from openpilot.starpilot.lateral.controller_selection import selection_from_bytes
  assert selection_from_bytes(niro, raw).mode == ControllerMode.STARPILOT
  assert selection_from_bytes(honda, raw).mode == ControllerMode.STANDARD


@pytest.mark.parametrize('failure', ['brand', 'unknown', 'angle', 'pid', 'passive', 'dashcam', 'notCar', 'nan', 'factor', 'friction'])
def test_generic_refuses_invalid_controller_configuration(failure):
  from openpilot.starpilot.lateral.controller_selection import generic_torque_supported
  cp = cp_for(CAR.KIA_NIRO_EV_2ND_GEN)
  if failure == 'brand':
    cp.brand = 'toyota'
  elif failure == 'unknown':
    cp.carFingerprint = 'UNREGISTERED'
  elif failure == 'angle':
    cp.steerControlType = structs.CarParams.SteerControlType.angle
  elif failure == 'pid':
    cp.lateralTuning.init('pid')
  elif failure in ('passive', 'dashcam', 'notCar'):
    setattr(cp, 'dashcamOnly' if failure == 'dashcam' else failure, True)
  elif failure == 'nan':
    cp.lateralTuning.torque.latAccelFactor = float('nan')
  elif failure == 'factor':
    cp.lateralTuning.torque.latAccelFactor = 0
  elif failure == 'friction':
    cp.lateralTuning.torque.friction = -1
  assert not generic_torque_supported(cp)
  assert policy_for(cp) is None


def test_generic_defaults_and_existing_learning_are_preserved(tmp_path):
  params = Params(str(tmp_path))
  cp = cp_for(CAR.KIA_NIRO_EV_2ND_GEN)
  assert default_selection(cp).mode == ControllerMode.STANDARD
  assert learning_allowed(params, cp)
  params.put_bool('ForceAutoTuneOff', True, block=True)
  assert not learning_allowed(params, cp)
  params.put(DOCUMENT_KEY, json.loads(replace_mode(None, cp, ControllerMode.STARPILOT)), block=True)
  assert not learning_allowed(params, cp)


def test_shared_controller_uses_real_interfaces_and_keeps_output_limits():
  checked = set()
  for identity in interfaces:
    cp = cp_for(identity)
    if policy_for(cp) != 'generic' or str(cp.brand) in checked:
      continue
    checked.add(str(cp.brand))
    ci = interfaces[identity](cp)
    standard = LatControlTorque(cp.as_reader(), ci, DT_CTRL, controller_mode=ControllerMode.STANDARD)
    custom = LatControlTorque(cp.as_reader(), ci, DT_CTRL, controller_mode=ControllerMode.STARPILOT)
    assert standard.starpilot_extension is None
    assert custom.starpilot_extension is not None
    assert custom.torque_params.to_dict() == standard.torque_params.to_dict()
    for acceleration in (-1., -.2, 0., .2, 1.):
      assert (custom.torque_from_lateral_accel(acceleration, custom.torque_params) ==
              standard.torque_from_lateral_accel(acceleration, standard.torque_params))
    assert custom.pid.pos_limit == standard.pid.pos_limit
    assert custom.pid.neg_limit == standard.pid.neg_limit
    state = structs.CarState.new_message()
    vm = VehicleModel(cp)
    params = SimpleNamespace(angleOffsetDeg=0., roll=0.01)
    for active, speed, curvature, pressed, limited in ((False, 0., 0., False, False), (True, 12., .001, False, False),
        (True, 25., -.001, False, True), (True, 15., .002, True, False), (False, 15., .002, False, False)):
      state.vEgo, state.steeringPressed = speed, pressed
      output, _, message = custom.update(active, state, vm, params, limited, curvature, False, .2)
      assert math.isfinite(output) and abs(output) <= custom.steer_max
      assert message.active == active
      if not active:
        assert output == 0
  assert 'hyundai' in checked and len(checked) >= 3
