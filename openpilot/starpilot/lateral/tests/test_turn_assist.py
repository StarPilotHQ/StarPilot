import math
import struct
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

import pytest
from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.lateral.controller_selection import ControllerMode, turn_assist_supported
from openpilot.starpilot.lateral import ioniq6_policy as policy
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


def controller(enabled=False, mode=None, minimum=0.):
  cp = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
  cp.minSteerSpeed = minimum
  lac = LatControlTorque(cp.as_reader(), interfaces[CAR.HYUNDAI_IONIQ_6](cp), DT_CTRL,
                         controller_mode=mode, turn_assist=enabled)
  cs = structs.CarState.new_message()
  cs.gearShifter = structs.CarState.GearShifter.drive
  cs.vEgo = 1.
  return cp, lac, cs


def test_default_off_preserves_other_controller_math():
  cp, off, cs = controller()
  _, on, _ = controller(True)
  vm = VehicleModel(cp)
  params = SimpleNamespace(angleOffsetDeg=0., roll=0.)
  original = policy.get_ioniq_6_low_speed_angle_assist_torque
  with patch.object(policy, 'get_ioniq_6_low_speed_angle_assist_torque', wraps=original) as assist:
    off_output, _, off_state = off.update(True, cs, vm, params, False, -.001, False, .1)
    assist.assert_not_called()
    on_output, _, on_state = on.update(True, cs, vm, params, False, -.001, False, .1)
    assert assist.call_count == 1
  for field in ('p', 'i', 'd', 'f', 'error', 'desiredLateralAccel', 'desiredLateralJerk'):
    assert getattr(off_state, field) == getattr(on_state, field)
  assert off_output != on_output
  assert math.isfinite(off_output) and abs(on_output) <= 1.


@pytest.mark.parametrize('field,value', [
  ('vEgo', 0.), ('vEgo', .044703), ('vEgo', -1.), ('vEgo', float('nan')),
  ('vEgo', float('inf')), ('standstill', True), ('steeringPressed', True),
  ('steerFaultTemporary', True), ('steerFaultPermanent', True),
  ('gearShifter', structs.CarState.GearShifter.reverse),
  ('gearShifter', structs.CarState.GearShifter.unknown),
])
def test_assist_rejects_nonrolling_or_faulted_state(field, value):
  _, lac, cs = controller(True)
  setattr(cs, field, value)
  assert not selected_policy(lac).turn_assist_active(cs)


def test_exact_rolling_threshold_and_vehicle_minimum():
  _, lac, cs = controller(True)
  cs.vEgo = .044704
  assert selected_policy(lac).turn_assist_active(cs)
  _, lac, cs = controller(True, minimum=2.)
  assert not selected_policy(lac).turn_assist_active(cs)
  cs.vEgo = 2.
  assert selected_policy(lac).turn_assist_active(cs)


def test_stock_and_unsupported_interfaces_never_gain_policy():
  cp, lac, _ = controller(True, ControllerMode.STANDARD)
  assert selected_policy(lac) is None
  assert turn_assist_supported(cp)
  cp.passive = True
  assert not turn_assist_supported(cp)
  cp.passive = False
  cp.steerControlType = structs.CarParams.SteerControlType.angle
  assert not turn_assist_supported(cp)


def test_startup_saved_exact_on_defaults_and_safe_mode():
  assert not VehicleStartupPreferences().turn_assist
  snapshot = VehicleStartupPreferences(toyota_auto_hold=True)
  assert snapshot.toyota_auto_hold and not snapshot.turn_assist
  with tempfile.TemporaryDirectory() as root:
    source = SimpleNamespace(get_param_path=lambda key: str(Path(root) / key),
                             get_default_value=Params().get_default_value)
    for raw in (None, b'0', b'1', b'', b'true', b'1\n', b'1'*20):
      path = Path(root) / 'TurnAssist'
      path.unlink(missing_ok=True)
      if raw is not None:
        path.write_bytes(raw)
      snapshot = VehicleStartupPreferences.read(source, enabled=True)
      assert snapshot.turn_assist == (raw in (None, b'1'))
      path.write_bytes(b'0')
      assert snapshot.turn_assist == (raw in (None, b'1'))
    path.write_bytes(b'1')
    assert not VehicleStartupPreferences.read(source, enabled=False).turn_assist
    (Path(root) / 'SafeMode').write_bytes(b'1')
    assert not VehicleStartupPreferences.read(source, enabled=True).turn_assist


def test_enabled_helper_retains_original_large_turn_and_unwind():
  _, lac, cs = controller(True)
  assert selected_policy(lac).turn_assist_active(cs)
  assert policy.get_ioniq_6_low_speed_angle_assist_torque(10., 0., 0., cs.vEgo) == -.39409320626365096
  assert policy.get_ioniq_6_low_speed_angle_assist_torque(0., 10., 0., cs.vEgo) == .07246171113121364


def test_nonfinite_minimum_does_not_admit_assist():
  for minimum in (float('nan'), float('inf')):
    _, lac, cs = controller(True, minimum=minimum)
    assert not selected_policy(lac).turn_assist_active(cs)


def test_stock_assist_is_independent_of_tune_and_explicit_off():
  cp, off, cs = controller(False, ControllerMode.STANDARD)
  _, on, _ = controller(True, ControllerMode.STANDARD)
  vm = VehicleModel(cp)
  params = SimpleNamespace(angleOffsetDeg=0., roll=0.)
  assert selected_policy(on) is None
  assert on.pid._k_p == off.pid._k_p
  assert on.torque_params.to_dict() == off.torque_params.to_dict()
  off_output, _, _ = off.update(True, cs, vm, params, False, -.001, False, .1)
  on_output, _, _ = on.update(True, cs, vm, params, False, -.001, False, .1)
  assert off_output != on_output
  assert abs(on_output) <= on.steer_max


def test_explicit_off_survives_repeated_startup_reads():
  with tempfile.TemporaryDirectory() as root:
    source = SimpleNamespace(get_param_path=lambda key: str(Path(root) / key),
                             get_default_value=Params().get_default_value)
    path = Path(root) / 'TurnAssist'
    assert VehicleStartupPreferences.read(source, enabled=True).turn_assist
    assert not path.exists()
    path.write_bytes(b'0')
    for _ in range(3):
      assert not VehicleStartupPreferences.read(source, enabled=True).turn_assist
      assert path.read_bytes() == b'0'


@pytest.mark.parametrize('brand,candidate', [('toyota', 'TOYOTA_COROLLA_TSS2'), ('honda', 'HONDA_FIT_4G'), ('mazda', 'MAZDA_CX5_2022'),
                                               ('gm', 'CHEVROLET_BOLT_CC_2018_2021')])
def test_shared_assist_actual_torque_factories(brand, candidate):
  from importlib import import_module
  from openpilot.starpilot.lateral.controller_selection import model_turn_assist_supported
  car = getattr(import_module(f'opendbc.car.{brand}.values').CAR, candidate)
  if brand == 'gm':
    from openpilot.starpilot.longitudinal.tests.test_bolt_mode_transition import params as bolt_params
    cp = bolt_params(car, pedal=True)
  else:
    cp = interfaces[car].get_non_essential_params(car)
  assert cp.lateralTuning.which() == 'torque'
  assert turn_assist_supported(cp)
  assert not model_turn_assist_supported(cp)
  cs = structs.CarState.new_message()
  cs.gearShifter = structs.CarState.GearShifter.drive
  cs.vEgo = max(1., cp.minSteerSpeed)
  vm = VehicleModel(cp)
  lp = SimpleNamespace(angleOffsetDeg=0., roll=0.)
  on = LatControlTorque(cp.as_reader(), interfaces[car](cp), DT_CTRL, controller_mode=ControllerMode.STANDARD, turn_assist=True)
  off = LatControlTorque(cp.as_reader(), interfaces[car](cp), DT_CTRL, controller_mode=ControllerMode.STANDARD, turn_assist=False)
  with patch.object(on.turn_assist, 'apply', wraps=on.turn_assist.apply) as assist:
    output, _, log = on.update(True, cs, vm, lp, False, -.01, False, .1)
  assist.assert_called_once()
  baseline, _, _ = off.update(True, cs, vm, lp, False, -.01, False, .1)
  assert struct.unpack("f", struct.pack("f", output))[0] == log.output
  assert abs(output) <= on.steer_max
  assert output != baseline
  for denied in ('passive', 'dashcamOnly', 'notCar'):
    setattr(cp, denied, True)
    assert not turn_assist_supported(cp)
    setattr(cp, denied, False)
  cp.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.noOutput
  assert not turn_assist_supported(cp)


def test_g70_assist_preserves_vehicle_output_cap():
  from openpilot.starpilot.lateral.genesis_g70_policy import get_genesis_g70_low_speed_output_limit
  cp = interfaces[CAR.GENESIS_G70_2020].get_non_essential_params(CAR.GENESIS_G70_2020)
  lac = LatControlTorque(cp.as_reader(), interfaces[CAR.GENESIS_G70_2020](cp), DT_CTRL, turn_assist=True)
  cs = structs.CarState.new_message()
  cs.vEgo = 1.
  cs.gearShifter = structs.CarState.GearShifter.drive
  with patch.object(lac.turn_assist, 'apply', wraps=lac.turn_assist.apply) as assist:
    output, _, state = lac.update(True, cs, VehicleModel(cp), SimpleNamespace(angleOffsetDeg=0., roll=0.),
                                  False, -.01, False, .1)
  assist.assert_called_once()
  limit = get_genesis_g70_low_speed_output_limit(state.desiredLateralAccel, cs.vEgo)
  assert limit < lac.steer_max
  assert abs(output) <= limit


@pytest.mark.parametrize('gear', [structs.CarState.GearShifter.drive, structs.CarState.GearShifter.low])
def test_generic_assist_minimum_and_driver_pause(gear):
  from openpilot.starpilot.lateral.torque_turn_assist import TorqueTurnAssist
  cp, _, cs = controller(True, ControllerMode.STANDARD, minimum=2.)
  assist = TorqueTurnAssist(cp)
  cs.gearShifter = gear
  cs.vEgo = 2.
  vm = VehicleModel(cp)
  params = SimpleNamespace(angleOffsetDeg=0., roll=0.)
  assert assist.apply(cs, vm, params, -.01, 0., .2) != 0.
  cs.vEgo = 1.999
  assert assist.apply(cs, vm, params, -.01, .1, .2) == .1
  cs.vEgo = 2.
  for field in ('standstill', 'steeringPressed', 'steerFaultTemporary', 'steerFaultPermanent'):
    setattr(cs, field, True)
    assert assist.apply(cs, vm, params, -.01, .1, .2) == .1
    setattr(cs, field, False)


def test_bolt_assist_preserves_custom_output_cap_and_single_saturation():
  from opendbc.car.gm.values import CAR as GM_CAR
  from openpilot.starpilot.longitudinal.tests.test_bolt_mode_transition import params as bolt_params
  from openpilot.starpilot.lateral.bolt_shaping import get_bolt_2022_2023_low_speed_center_output_limit
  cp = bolt_params(GM_CAR.CHEVROLET_BOLT_CC_2022_2023, pedal=True)
  lac = LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), DT_CTRL, turn_assist=True)
  cs = structs.CarState.new_message()
  cs.gearShifter, cs.vEgo = 'low', 1.
  with patch.object(lac.turn_assist, 'apply', return_value=lac.steer_max) as assist:
    output, _, state = lac.update(True, cs, VehicleModel(cp), SimpleNamespace(angleOffsetDeg=0., roll=0.),
                                  False, -.01, False, .1)
  assist.assert_called_once()
  limit = get_bolt_2022_2023_low_speed_center_output_limit(state.desiredLateralAccel, cs.vEgo)
  assert limit < lac.steer_max and abs(output) <= limit
  assert lac.sat_time == 0.
