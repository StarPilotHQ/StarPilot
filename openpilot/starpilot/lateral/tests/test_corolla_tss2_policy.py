"""Compare runtime output against frozen default original StarPilot traces."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.cereal import log
from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.toyota.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.lateral.torque_shaping import INTERP_SPEEDS, KP_INTERP, KP
from openpilot.starpilot.lateral.corolla_tss2_policy import supported_cp

FIXTURE = Path(__file__).with_name('fixtures') / 'corolla_tss2_ba901b5f.json'


def controller_for(car=CAR.TOYOTA_COROLLA_TSS2):
  cp = interfaces[car].get_non_essential_params(car)
  return cp, LatControlTorque(cp.as_reader(), interfaces[car](cp), 0.01), VehicleModel(cp)


@pytest.mark.parametrize('case_index', range(5))
def test_frozen_original_numeric_traces(case_index):
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
    assert torque == pytest.approx(frame['torque'], abs=1e-12), (case_index, index)
    assert [getattr(state, field) for field in fixture['log_fields']] == pytest.approx(frame['log'], abs=1e-12)
    assert state.active == frame['active']
    assert state.saturated == frame['saturated']
    assert abs(torque) <= 1.0
    if not active:
      assert torque == 0.0
      assert controller.pid.i == 0.0


def test_exact_vehicle_admission_and_upstream_gains():
  cp, controller, _ = controller_for()
  assert supported_cp(cp)
  assert controller.pid._k_i[1] == [0.35]
  for field, value in [('brand', 'hyundai'), ('dashcamOnly', True), ('passive', True), ('steerControlType', structs.CarParams.SteerControlType.angle)]:
    changed = cp.as_reader().as_builder()
    setattr(changed, field, value)
    assert not supported_cp(changed)
    native = LatControlTorque(changed.as_reader(), interfaces[CAR.TOYOTA_COROLLA_TSS2](changed), 0.01)
    assert selected_policy(native) is None
    assert native.pid._k_i[1] == [0.15]
  other_cp, other, _ = controller_for(CAR.TOYOTA_RAV4_TSS2)
  assert not supported_cp(other_cp)
  assert type(selected_policy(other)).__name__ == "Rav4TSS2TorquePolicy"
  assert other.pid._k_i[1] == [0.15]


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
