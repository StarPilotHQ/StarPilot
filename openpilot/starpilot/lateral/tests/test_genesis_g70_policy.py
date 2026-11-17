"""Compare runtime output against frozen default original StarPilot traces."""

import json
from collections import deque
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
from openpilot.starpilot.lateral.controller_selection import ControllerMode
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.lateral.torque_shaping import INTERP_SPEEDS, KP_INTERP, KP
from openpilot.starpilot.lateral.genesis_g70_policy import supported_cp
from openpilot.starpilot.lateral import genesis_g70_policy

FIXTURE = Path(__file__).with_name('fixtures') / 'genesis_g70_ba901b5f.json'


def controller_for(car=CAR.GENESIS_G70_2020):
  cp = interfaces[car].get_non_essential_params(car)
  return cp, LatControlTorque(cp.as_reader(), interfaces[car](cp), 0.01), VehicleModel(cp)


@pytest.mark.parametrize('case_index', range(5))
@patch.object(genesis_g70_policy, 'get_genesis_g70_center_measurement_damping_gain', return_value=0.0)
@patch.object(genesis_g70_policy, 'get_genesis_g70_traffic_friction_increment', return_value=0.0)
def test_frozen_base_tuning_numeric_traces(_traffic, _damping, case_index):
  # This ba901b5f fixture intentionally tests historical base math, as the
  # existing damping isolation does. The new traffic stage is exercised by
  # test_genesis_dom_leaves.py against actual friction/current driver state.
  fixture = json.loads(FIXTURE.read_text())
  cp, controller, vm = controller_for()
  assert selected_policy(controller) is not None
  assert controller.pid._k_p == [[0.0], [KP]]
  # Isolate the historical gain schedule while keeping the original numeric traces frozen.
  controller.pid._k_p = [list(INTERP_SPEEDS), list(KP_INTERP)]
  params = log.VehicleParameters.new_message(angleOffsetDeg=0.3, roll=0.006)
  for index, frame in enumerate(fixture['cases'][case_index]['frames']):
    active, speed, angle, pressed, limited, curve, curve_limited, delay = frame['input']
    cs = SimpleNamespace(vEgo=speed, steeringAngleDeg=angle, steeringPressed=pressed)
    torque, desired_angle, state = controller.update(active, cs, vm, params, limited, curve, curve_limited, delay)
    assert desired_angle == 0.0
    assert torque == pytest.approx(frame['torque'], abs=1e-12, rel=0), (case_index, index)
    assert [getattr(state, field) for field in fixture['log_fields']] == pytest.approx(frame['log'], abs=1e-12, rel=0)
    assert state.active == frame['active']
    assert state.saturated == frame['saturated']
    assert abs(torque) <= 1.0
    if not active:
      assert torque == 0.0
      assert controller.pid.i == 0.0
      assert selected_policy(controller).prev_output_torque == 0.0


def test_exact_vehicle_admission_and_upstream_gains():
  cp, controller, _ = controller_for()
  assert supported_cp(cp)
  assert controller.pid._k_i[1] == [0.35]
  for field, value in [('brand', 'toyota'), ('dashcamOnly', True), ('passive', True), ('steerControlType', structs.CarParams.SteerControlType.angle)]:
    changed = cp.as_reader().as_builder()
    setattr(changed, field, value)
    assert not supported_cp(changed)
    native = LatControlTorque(changed.as_reader(), interfaces[CAR.GENESIS_G70_2020](changed), 0.01)
    assert selected_policy(native) is None
    assert native.pid._k_i[1] == [0.15]
  other_cp, other, _ = controller_for(CAR.GENESIS_G70)
  assert not supported_cp(other_cp)
  assert selected_policy(other) is None
  assert other.pid._k_i[1] == [0.15]
  non_torque = cp.as_reader().as_builder()
  non_torque.lateralTuning.init('pid')
  assert not supported_cp(non_torque)


def test_live_source_updates_keep_raw_calibration_and_limits():
  cp, controller, _ = controller_for()
  original = cp.lateralTuning.torque.latAccelFactor
  assert controller.torque_params.latAccelFactor == original
  for _ in range(3):
    controller.update_torque_parameters(original * 1.1, 0.025, 0.08)
    assert controller.torque_params.latAccelFactor == pytest.approx(original * 1.1)
    assert controller.torque_params.latAccelOffset == pytest.approx(0.025)
    assert controller.torque_params.friction == pytest.approx(0.08)
    assert controller.pid.pos_limit == controller.lateral_accel_from_torque(1.0, controller.torque_params)
    assert controller.pid.neg_limit == controller.lateral_accel_from_torque(-1.0, controller.torque_params)


def test_driver_override_and_inactive_state_reset():
  _, controller, vm = controller_for()
  params = log.VehicleParameters.new_message(angleOffsetDeg=0.3, roll=0.006)
  cs = SimpleNamespace(vEgo=4.0, steeringAngleDeg=20.3, steeringPressed=False)
  with patch.object(genesis_g70_policy, 'get_genesis_g70_low_speed_angle_damping',
                    wraps=genesis_g70_policy.get_genesis_g70_low_speed_angle_damping) as damping, \
       patch.object(genesis_g70_policy, 'get_genesis_g70_stabilized_output',
                    wraps=genesis_g70_policy.get_genesis_g70_stabilized_output) as smoothing, \
       patch.object(controller.pid, 'update', wraps=controller.pid.update) as pid_update:
    controller.update(True, cs, vm, params, False, .002, False, .2)
    damping.assert_called_once()
    smoothing.assert_called_once()
    cs.steeringPressed = True
    controller.update(True, cs, vm, params, False, .002, False, .2)
    assert damping.call_count == 1
    assert smoothing.call_count == 1
    assert pid_update.call_args.kwargs['freeze_integrator'] is True
    cs.steeringPressed = False
    controller.update(True, cs, vm, params, True, .002, False, .2)
    assert pid_update.call_args.kwargs['freeze_integrator'] is True
  torque, _, state = controller.update(False, cs, vm, params, False, .002, False, .2)
  policy = selected_policy(controller)
  assert torque == 0.0 and not state.active and controller.pid.i == 0.0
  assert policy.prev_output_torque == 0.0
  assert policy.jerk_filter.x == 0.0
  assert policy.measurement_rate_filter.x == 0.0
  assert policy.curvature_request_buffer[-1] == .002


def test_preconfigured_manual_torque_values_preserved():
  cp = interfaces[CAR.GENESIS_G70_2020].get_non_essential_params(CAR.GENESIS_G70_2020)
  cp.lateralTuning.torque.latAccelFactor = 2.45
  cp.lateralTuning.torque.latAccelOffset = -.031
  cp.lateralTuning.torque.friction = .072
  controller = LatControlTorque(cp.as_reader(), interfaces[CAR.GENESIS_G70_2020](cp), .01)
  assert controller.torque_params.latAccelFactor == cp.lateralTuning.torque.latAccelFactor
  assert controller.torque_params.latAccelOffset == cp.lateralTuning.torque.latAccelOffset
  assert controller.torque_params.friction == cp.lateralTuning.torque.friction
  assert controller.pid.pos_limit == controller.lateral_accel_from_torque(1.0, controller.torque_params)


@pytest.mark.parametrize('angle', [-85., 0., 85.])
@pytest.mark.parametrize('pid_output', [-.125, 0., .125])
def test_angle_taper_uses_actual_sent_torque_direction(angle, pid_output):
  outputs = []
  for taper in (lambda *_: 1., genesis_g70_policy.get_genesis_g70_angle_output_scale):
    _, controller, vm = controller_for()
    cs = SimpleNamespace(vEgo=15., steeringAngleDeg=angle, steeringPressed=True)
    params = log.VehicleParameters.new_message(angleOffsetDeg=0., roll=0.)
    with patch.object(controller.pid, 'update', return_value=pid_output), \
         patch.object(controller, 'torque_from_lateral_accel', side_effect=lambda value, *_: value), \
         patch.object(genesis_g70_policy, 'get_genesis_g70_angle_output_scale', taper):
      output, _, state = controller.update(True, cs, vm, params, False, 0., False, .2)
    assert state.active
    outputs.append(output)
  baseline, tapered = outputs
  if pid_output == 0:
    assert baseline == tapered == 0
  else:
    assert baseline * pid_output < 0
    if baseline * angle > 0:
      assert 0 < abs(tapered) < abs(baseline)
    else:
      assert tapered == pytest.approx(baseline)


@pytest.mark.parametrize('mph,desired,measured,jerk,expected', [
  (65., 0., .10, 0., .09), (50., 0., .10, 0., 0.), (55., 0., .10, 0., .045),
  (65., .25, .10, 0., .045), (65., 0., .25, 0., .045),
  (65., .35, .10, 0., 0.), (65., 0., .35, 0., 0.),
  (65., 0., .10, .35, .045), (65., 0., .10, .50, 0.),
])
def test_center_measurement_damping_gates(mph, desired, measured, jerk, expected):
  for direction in (-1., 1.):
    gain = genesis_g70_policy.get_genesis_g70_center_measurement_damping_gain(
      mph * .44704, desired * direction, measured * direction, jerk * direction)
    assert gain == pytest.approx(expected)


@pytest.mark.parametrize('direction', (-1., 1.))
def test_center_measurement_damping_update_and_recovery(direction):
  _, controller, vm = controller_for()
  cs = SimpleNamespace(vEgo=65. * .44704, steeringAngleDeg=0., steeringPressed=False)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0., roll=0.)
  controller.update(True, cs, vm, params, False, 0., False, .2)
  cs.steeringAngleDeg = -direction * .5
  output, _, moving = controller.update(True, cs, vm, params, False, 0., False, .2)
  assert moving.d * direction < 0.
  assert abs(moving.d) <= .225
  assert abs(output) <= controller.steer_max
  for _ in range(150):
    _, _, steady = controller.update(True, cs, vm, params, False, 0., False, .2)
  assert steady.d == pytest.approx(0., abs=1e-6)
  cs.steeringPressed = True
  cs.steeringAngleDeg = 0.
  _, _, driver = controller.update(True, cs, vm, params, False, 0., False, .2)
  assert driver.d == 0.
  cs.steeringPressed = False
  controller.update(False, cs, vm, params, False, 0., False, .2)
  _, _, resumed = controller.update(True, cs, vm, params, False, 0., False, .2)
  assert resumed.d == 0.
  cs.vEgo = 40. * .44704
  cs.steeringAngleDeg = -direction * .5
  _, _, low_speed = controller.update(True, cs, vm, params, False, 0., False, .2)
  assert low_speed.d == 0.
  cs.vEgo = 65. * .44704
  curvature = direction * .8 / cs.vEgo**2
  policy = selected_policy(controller)
  policy.curvature_request_buffer = deque([curvature] * policy.request_buffer_len, maxlen=policy.request_buffer_len)
  _, _, curve = controller.update(True, cs, vm, params, False, curvature, False, .2)
  assert curve.d == 0.


@pytest.mark.parametrize('car', (CAR.GENESIS_G70_2020, CAR.GENESIS_G70, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN, CAR.KIA_EV6, CAR.HYUNDAI_IONIQ_6))
def test_center_damping_isolated_to_selected_g70(car):
  cp, controller, vm = controller_for(car)
  if car == CAR.GENESIS_G70_2020:
    controller = LatControlTorque(cp.as_reader(), interfaces[car](cp), .01, controller_mode=ControllerMode.STANDARD)
  cs = SimpleNamespace(vEgo=65. * .44704, steeringAngleDeg=0., steeringPressed=False)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0., roll=0.)
  with patch.object(genesis_g70_policy, 'get_genesis_g70_center_measurement_damping_gain', side_effect=AssertionError('Wrong vehicle policy')):
    controller.update(True, cs, vm, params, False, 0., False, .2)
