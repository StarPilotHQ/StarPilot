import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import ControllerMode, default_selection, policy_for, replace_mode, selection_from_bytes


CASES = [(car, length) for car in (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT) for length in (None, 4, 8)]


def factory(car, length=None):
  from openpilot.starpilot.car.toyota.tests.test_prius_sdsu_filter import original_firmware

  fw = original_firmware(b'8965B47050' + bytes(6))
  if car == CAR.TOYOTA_PRIUS_RETROFIT:
    for item in fw:
      if item.ecu == structs.CarParams.Ecu.eps:
        item.fwVersion = b'8965B47070' + bytes(6)
  fp = gen_empty_fingerprint()
  if length:
    fp[0][0x2FF] = length
  return CarInterface.get_params(car, fp, fw, False, False, False)


@pytest.mark.parametrize('car,length', CASES)
def test_restored_prius_profile_and_firmware_defaults(car, length):
  cp = factory(car, length)
  assert not cp.dashcamOnly
  assert policy_for(cp) == 'prius', 'PRIUS_SHAPING_MISSING'
  assert default_selection(cp).mode == ControllerMode.STARPILOT
  assert cp.steerActuatorDelay == pytest.approx(0.14)
  assert cp.lateralTuning.torque.steeringAngleDeadzoneDeg == pytest.approx(0.3)
  controller = LatControlTorque(cp.as_reader(), interfaces[car](cp), 0.01)
  assert type(controller.starpilot_extension.policy).__name__ == 'PriusTorquePolicy'
  assert controller.steering_angle_deadzone_deg == pytest.approx(0.3)
  saved = replace_mode(None, cp, ControllerMode.STANDARD)
  chosen = selection_from_bytes(cp, saved)
  standard = LatControlTorque(cp.as_reader(), interfaces[car](cp), 0.01, controller_mode=chosen.mode)
  assert standard.starpilot_extension is None
  assert standard.torque_params.to_dict() == cp.lateralTuning.torque.to_dict()


@pytest.mark.parametrize('car', [CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT])
def test_original_source_knots_reach_every_selected_policy_stage(car):
  from openpilot.starpilot.lateral import prius_shaping as s

  cp = factory(car)
  controller = LatControlTorque(cp.as_reader(), interfaces[car](cp), 0.01)
  policy = controller.starpilot_extension.policy
  index = int(car == CAR.TOYOTA_PRIUS_RETROFIT)
  vectors = json.loads((Path(__file__).parent / 'data/prius_source_oracle.json').read_text())['rows']
  for row in vectors:
    v, sp, jerk = row['input']
    assert policy.feedforward(0.37, sp, jerk, v) == pytest.approx(0.37 * row['ff'] * row['center'], abs=1e-12)
    threshold, scale = row['friction']
    assert policy.friction(sp, jerk, v) == pytest.approx((threshold, 1.0 + (scale - 1.0) * row['center']), abs=1e-12)
    assert s.get_prius_friction_scale(v, sp, jerk) == pytest.approx(scale, abs=1e-12)
    assert policy.jerk_deadzone(sp, jerk, 0.0, v) == pytest.approx(row['deadzone'][index], abs=1e-12)
    assert policy.output(0.71, sp, v) == pytest.approx(0.71 * row['center'] * row['highspeed'][index], abs=1e-12)
    assert s.get_prius_ff_scale(sp, jerk, v) == pytest.approx(row['ff'], abs=1e-12)


@pytest.mark.parametrize('car,length', CASES)
@pytest.mark.parametrize('pressed', [False, True])
def test_real_controller_preserves_live_parameters_limits_and_withdrawal(car, length, pressed):
  cp = factory(car, length)
  controller = LatControlTorque(cp.as_reader(), interfaces[car](cp), 0.01)
  controller.update_torque_parameters(1.48, -0.21, 0.18)
  expected = controller.torque_params.to_dict()
  limits = controller.pid.neg_limit, controller.pid.pos_limit, controller.steer_max
  cs = SimpleNamespace(vEgo=32.0, steeringAngleDeg=0.0, steeringPressed=pressed)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0.0, roll=0.02)
  vm = VehicleModel(cp)
  for tick in range(160):
    output, _, state = controller.update(True, cs, vm, params, tick % 7 == 0, 0.0003 * math.sin(tick / 17), False, 0.39)
    assert math.isfinite(output) and abs(output) <= controller.steer_max
  assert state.active
  assert controller.torque_params.to_dict() == expected
  assert (controller.pid.neg_limit, controller.pid.pos_limit, controller.steer_max) == limits
  output, _, state = controller.update(False, cs, vm, params, False, 0.0003, False, 0.39)
  assert output == 0.0 and not state.active


def test_other_racks_faulted_and_invalid_tunes_cannot_select_prius_policy():
  from openpilot.starpilot.lateral.prius_policy import supported_cp

  for car in (CAR.TOYOTA_PRIUS_TSS2, CAR.TOYOTA_COROLLA_TSS2, CAR.TOYOTA_RAV4_TSS2, CAR.TOYOTA_MATRIX_RETROFIT):
    assert not supported_cp(interfaces[car].get_non_essential_params(car))
  for field in ('passive', 'dashcamOnly', 'notCar'):
    cp = factory(CAR.TOYOTA_PRIUS)
    setattr(cp, field, True)
    assert not supported_cp(cp)
  cp = factory(CAR.TOYOTA_PRIUS)
  cp.lateralTuning.torque.latAccelFactor = float('nan')
  assert not supported_cp(cp)


def test_original_rack_firmware_exception_is_preserved():
  fw = structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.eps, fwVersion=b'8965B47060' + bytes(6))
  cp = CarInterface.get_params(CAR.TOYOTA_PRIUS, gen_empty_fingerprint(), [fw], False, False, False)
  assert cp.steerActuatorDelay == pytest.approx(0.12)
  assert cp.lateralTuning.torque.steeringAngleDeadzoneDeg == 0.0


@pytest.mark.parametrize('car,hardware,firmware', [
  (CAR.TOYOTA_PRIUS, '2FF4', 'catalog'),
  (CAR.TOYOTA_PRIUS, '2FF8', 'catalog'),
  (CAR.TOYOTA_PRIUS, '2FF4', 'EPS73_8965B47070'),
  (CAR.TOYOTA_PRIUS, '2FF8', 'EPS73_8965B47070'),
  (CAR.TOYOTA_PRIUS_RETROFIT, '2FF4', 'EPS73_8965B47070'),
  (CAR.TOYOTA_PRIUS_RETROFIT, '2FF8', 'EPS73_8965B47070'),
])
@pytest.mark.parametrize('stock', [False, True])
def test_pedal_parent_profiles_keep_exact_native_and_longitudinal_ownership(car, hardware, firmware, stock):
  from opendbc.car.toyota import pedal_composite
  from openpilot.starpilot.car.toyota.tests.test_pedal_parent_configurations import sources
  fp, fw = sources(car, hardware, firmware)
  fp[0][0x201] = 6
  cp = CarInterface.get_params(car, fp, fw, True, False, False)
  assert pedal_composite.configured(cp)
  if stock:
    assert pedal_composite.prepare_stock(cp)
  before = cp.to_dict()
  selected = LatControlTorque(cp.as_reader(), interfaces[car](cp), .01)
  assert selected.controller_policy == 'prius'
  assert cp.to_dict() == before
  assert cp.openpilotLongitudinalControl == (not stock)


@pytest.mark.parametrize('stock', [False, True])
def test_dsu_bypass_keeps_exact_native_and_longitudinal_ownership(stock):
  from opendbc.car.toyota import dsu_bypass
  from openpilot.starpilot.car.toyota.tests.test_dsu_bypass import factory as bypass_factory
  cp = bypass_factory(CAR.TOYOTA_PRIUS)
  assert dsu_bypass.configured(cp)
  if stock:
    assert dsu_bypass.prepare_stock(cp)
  before = cp.to_dict()
  selected = LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), .01)
  assert selected.controller_policy == 'prius'
  assert cp.to_dict() == before
  assert cp.openpilotLongitudinalControl == (not stock)
