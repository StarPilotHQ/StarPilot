"""Exact GV70 feedforward tuning through the production torque extension."""

from collections import deque
import json
import math
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from openpilot.cereal import log
from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import ControllerMode, policy_for, selection_from_bytes
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.lateral import genesis_gv70_policy as policy
from openpilot.starpilot.lateral import gv70_shaping
from openpilot.starpilot.lateral.torque_shaping import KP


def controller_for(vehicle=CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN, mode=None):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  controller = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), 0.01, controller_mode=mode)
  cs = SimpleNamespace(vEgo=25.0, steeringAngleDeg=0.0, steeringPressed=False)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0.0, roll=0.0)
  return cp, controller, VehicleModel(cp), cs, params


def update(controller, vm, cs, params, curvature=0.0002, active=True):
  return controller.update(active, cs, vm, params, False, curvature, False, 0.2)


def test_original_derived_default_formula_vectors():
  data = json.loads((Path(__file__).with_name('fixtures') / 'gv70_385428f6.json').read_text())
  assert data['source_commit'] == '385428f6ea1c183de4ddcd8604f294abec02a9e8'
  for row in data['vectors']:
    value = getattr(gv70_shaping, row['function'])(*row['input'])
    expected = row['expected']
    # Green Garden88c739 adds an exact curve gate. These original vectors
    # remain frozen; undertracking/opposite-side curves now have zero damping.
    if row['function'] in ('get_genesis_gv70_high_speed_error_scale', 'get_genesis_gv70_reversal_output_scale'):
      request, measured, _, _ = row['input']
      if abs(request) >= .55 and (request * measured <= 0. or abs(measured) <= abs(request)):
        expected = 1.0
    assert value == pytest.approx(expected, abs=1e-14, rel=0), row


def test_exact_admission_and_saved_standard_keeps_upstream_controller():
  cp, controller, *_ = controller_for()
  assert policy.supported_cp(cp)
  assert policy_for(cp) == 'genesis_gv70_electrified'
  assert isinstance(selected_policy(controller), policy.GenesisGV70TorquePolicy)
  assert controller.pid._k_p == [[0.0], [KP]]
  assert controller.pid._k_i == ([0], [0.35])
  choice = json.dumps({'version': 1, 'vehicles': {'GENESIS_GV70_ELECTRIFIED_1ST_GEN': {'brand': 'hyundai', 'mode': 'standard'}}}).encode()
  assert selection_from_bytes(cp, choice).mode == ControllerMode.STANDARD
  _, standard, *_ = controller_for(mode=ControllerMode.STANDARD)
  assert selected_policy(standard) is None
  assert standard.pid._k_i == ([0], [0.15])
  assert standard.pid._k_d == ([0], [0.0])
  for field, value in (
    ('brand', 'toyota'),
    ('passive', True),
    ('dashcamOnly', True),
    ('notCar', True),
    ('steerControlType', structs.CarParams.SteerControlType.angle),
  ):
    changed = cp.as_reader().as_builder()
    setattr(changed, field, value)
    assert not policy.supported_cp(changed)
    assert policy_for(changed) is None
  changed = cp.as_reader().as_builder()
  changed.lateralTuning.init('pid')
  assert not policy.supported_cp(changed)
  for vehicle, expected in ((CAR.GENESIS_GV70_1ST_GEN, 'generic'), (CAR.GENESIS_GV70_ELECTRIFIED_2ND_GEN, None)):
    sibling = interfaces[vehicle].get_non_essential_params(vehicle)
    assert not policy.supported_cp(sibling)
    assert policy_for(sibling) == expected


def test_stabilization_only_changes_feedforward_feedback_remains_immediate():
  cp, controller, vm, cs, params = controller_for()
  calls = []

  def stable(*values):
    calls.append(values)
    return 0.123

  with patch.object(policy, 'get_genesis_gv70_stabilized_output', side_effect=stable):
    update(controller, vm, cs, params)
    assert not calls  # first frame has no previous feedforward
    output, _, state = update(controller, vm, cs, params)
    assert state.f == pytest.approx(0.123)
    assert output == pytest.approx(-controller.torque_from_lateral_accel(controller.pid.control, controller.torque_params))
    prior_p = state.p
    cs.steeringAngleDeg = 0.5
    _, _, changed = update(controller, vm, cs, params)
    assert changed.f == pytest.approx(0.123)
    assert changed.p != pytest.approx(prior_p)
    assert calls[-1][1] == pytest.approx(0.123)
    previous = len(calls)
    cs.steeringPressed = True
    _, _, pressed = update(controller, vm, cs, params)
    assert pressed.d == 0
    assert len(calls) == previous
    cs.steeringPressed = False
    update(controller, vm, cs, params)
    assert len(calls) == previous  # release tick also bypasses smoothing
    update(controller, vm, cs, params, active=False)
    assert selected_policy(controller).previous_feedforward is None
    update(controller, vm, cs, params, curvature=-0.0002)
    assert len(calls) == previous


def test_measurement_damping_matches_original_schedule_bound_and_has_no_bias():
  _, controller, vm, cs, params = controller_for()
  cs.vEgo = 30.0
  update(controller, vm, cs, params, curvature=0)
  cs.steeringAngleDeg = -5.0
  output, _, moving = update(controller, vm, cs, params, curvature=0)
  # Assert double-precision PID arithmetic and the exact Float32 telemetry separately.
  assert controller.pid.d == pytest.approx(-0.3, abs=1e-12), controller.pid.d
  assert moving.d == struct.unpack("f", struct.pack("f", -0.3))[0], moving.d
  assert abs(output) <= controller.steer_max
  for _ in range(150):
    _, _, steady = update(controller, vm, cs, params, curvature=0)
  assert steady.d == pytest.approx(0, abs=1e-6)
  cs.vEgo = 3.0
  cs.steeringAngleDeg = 5.0
  _, _, low = update(controller, vm, cs, params, curvature=0)
  assert low.d == 0


@pytest.mark.parametrize('direction', (-1.0, 1.0))
def test_overshoot_feedback_bypasses_previous_turn_feedforward(direction):
  _, controller, vm, cs, params = controller_for()
  cs.vEgo = 22.0
  cs.steeringAngleDeg = math.degrees(vm.get_steer_from_curvature(-direction * 2.4 / cs.vEgo**2, cs.vEgo, 0))
  selected = selected_policy(controller)
  selected.curvature_request_buffer = deque([direction * 0.8 / cs.vEgo**2] * selected.request_buffer_len, maxlen=selected.request_buffer_len)
  selected.previous_feedforward = direction * 0.8
  with patch.object(policy, 'get_genesis_gv70_stabilized_output', return_value=direction * 0.2):
    output, _, state = update(controller, vm, cs, params, curvature=direction * 0.8 / cs.vEgo**2)
  assert state.p * direction < 0
  assert output * direction > 0
  assert abs(output) <= controller.steer_max


@pytest.mark.parametrize('vehicle', (CAR.GENESIS_G70_2020, CAR.GENESIS_GV70_1ST_GEN, CAR.KIA_EV6, CAR.HYUNDAI_IONIQ_6))
def test_measurement_damping_does_not_touch_other_vehicle_policies(vehicle):
  _, controller, *_ = controller_for(vehicle)
  assert not isinstance(selected_policy(controller), policy.GenesisGV70TorquePolicy)
  assert max(controller.pid._k_d[1]) == 0


def test_learned_calibration_updates_stay_raw_without_new_multipliers():
  cp, controller, *_ = controller_for()
  factor = cp.lateralTuning.torque.latAccelFactor
  assert controller.torque_params.latAccelFactor == factor
  controller.update_torque_parameters(factor * 1.1, 0.025, 0.08)
  assert controller.torque_params.latAccelFactor == pytest.approx(factor * 1.1, rel=1e-6)
  assert controller.torque_params.latAccelOffset == pytest.approx(0.025)
  assert controller.torque_params.friction == pytest.approx(0.08)
