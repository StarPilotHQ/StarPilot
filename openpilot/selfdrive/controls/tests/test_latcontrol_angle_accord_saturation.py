"""Accord on the angle-loop EPS: steerSaturated follows the carcontroller's limiter, not the raw angle error."""
import pytest

from openpilot.cereal import log
from opendbc.car import gen_empty_fingerprint
from opendbc.car.structs import car
from opendbc.car.honda import carcontroller as honda_carcontroller
from opendbc.car.honda import interface as honda_interface
from opendbc.car.honda.interface import CarInterface as HondaInterface
from opendbc.car.honda.values import CAR as HONDA, HondaFlags
from opendbc.car.nissan.interface import CarInterface as NissanInterface
from opendbc.car.nissan.values import CAR as NISSAN
from opendbc.car.structs import CarParams
from opendbc.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle

DT = 0.01
ERROR_DEG = 6.0  # an ordinary turn-entry error on the angle-loop EPS, above the 2.5 deg raw-error threshold


class FakeParams:
  def get_bool(self, key, block=False, default=False):
    return default

  def get_float(self, key, block=False, default=0.0):
    return default

  def put_float(self, key, value):
    pass


@pytest.fixture(autouse=True)
def fake_params(monkeypatch):
  # the Honda port may read Params in the interface/carcontroller; keep the test off the device store
  monkeypatch.setattr(honda_interface, "Params", FakeParams, raising=False)
  monkeypatch.setattr(honda_carcontroller, "Params", FakeParams, raising=False)


def accord_cp(eps_fw: bytes):
  car_fw = [CarParams.CarFw(ecu=CarParams.Ecu.eps, fwVersion=eps_fw, address=0x18DA30F1, subAddress=0)]
  return HondaInterface.get_params(HONDA.HONDA_ACCORD, gen_empty_fingerprint(), car_fw, False, False, False)


def angle_cp():
  return accord_cp(b"39990-TVA,A16C")


def torque_honda_cp():
  return accord_cp(b"39990-TVA-A160\x00\x00")


def nissan_cp():
  return NissanInterface.get_non_essential_params(NISSAN.NISSAN_LEAF)


def run(cp, steer_limited_by_safety, seconds=2.0, v_ego=20.0):
  cp_reader = cp.as_reader()
  controller = LatControlAngle(cp_reader, None, DT)
  VM = VehicleModel(cp_reader)
  params = log.VehicleParameters.new_message()
  CS = car.CarState.new_message()
  CS.vEgo = v_ego
  CS.steeringPressed = False
  CS.steeringAngleDeg = 0.0
  # find the desired angle for a small curvature, then hold the wheel ERROR_DEG short of it
  _, angle_des, _ = controller.update(True, CS, VM, params, False, 0.002, False, 0.2)
  controller.reset()
  CS.steeringAngleDeg = angle_des - ERROR_DEG
  saturated = []
  for _ in range(round(seconds / DT)):
    _, des, lac_log = controller.update(True, CS, VM, params, steer_limited_by_safety, 0.002, False, 0.2)
    assert abs((des - CS.steeringAngleDeg) - ERROR_DEG) < 1e-6
    saturated.append(lac_log.saturated)
  return controller, saturated


def test_accord_angle_cp_takes_the_carcontroller_path():
  cp = angle_cp()
  assert cp.steerControlType == CarParams.SteerControlType.angle
  assert cp.flags & HondaFlags.EPS_ANGLE_LOOP_FW
  assert cp.steerLimitTimer == pytest.approx(0.8)


def test_accord_angle_error_alone_does_not_saturate():
  controller, saturated = run(angle_cp(), steer_limited_by_safety=False)
  assert controller.use_steer_limited_by_safety
  assert not any(saturated)


def test_accord_angle_limited_by_carcontroller_saturates():
  _, saturated = run(angle_cp(), steer_limited_by_safety=True)
  assert saturated[-1]
  # debounced by steerLimitTimer (0.8 s), not instantly
  assert not saturated[round(0.7 / DT)]


def test_accord_angle_curvature_limited_still_saturates():
  cp = angle_cp().as_reader()
  controller = LatControlAngle(cp, None, DT)
  VM = VehicleModel(cp)
  CS = car.CarState.new_message()
  CS.vEgo = 20.0
  params = log.VehicleParameters.new_message()
  for _ in range(200):
    _, _, lac_log = controller.update(True, CS, VM, params, False, 0.0, True, 0.2)
  assert lac_log.saturated


@pytest.mark.parametrize("cp_fn", [torque_honda_cp, nissan_cp], ids=["honda_torque_fw", "nissan"])
def test_other_cars_keep_the_angle_error_rule(cp_fn):
  controller, saturated = run(cp_fn(), steer_limited_by_safety=False)
  assert not controller.use_steer_limited_by_safety
  assert saturated[-1]


def test_tesla_still_uses_carcontroller_path():
  from opendbc.car.tesla.interface import CarInterface as TeslaInterface
  from opendbc.car.tesla.values import CAR as TESLA
  controller, saturated = run(TeslaInterface.get_non_essential_params(TESLA.TESLA_MODEL_3), steer_limited_by_safety=False)
  assert controller.use_steer_limited_by_safety
  assert not any(saturated)
