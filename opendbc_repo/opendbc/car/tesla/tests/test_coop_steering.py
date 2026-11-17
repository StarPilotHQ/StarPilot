import math
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.tesla.carcontroller import CarController, get_safety_CP
from opendbc.car.tesla.coop_steering import CooperativeSteeringController
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.values import CAR, DBC, CarControllerParams, TeslaSafetyFlags
from opendbc.car.vehicle_model import VehicleModel


def make_car_state(torque=0.0, speed=15.0, angle=0.0):
  return SimpleNamespace(out=SimpleNamespace(
    steeringTorque=torque,
    steeringAngleDeg=angle,
    vEgo=speed,
    vEgoRaw=speed,
  ))


@pytest.fixture
def vehicle_model():
  return VehicleModel(get_safety_CP())


def test_disabled_preserves_angle_command(vehicle_model):
  controller = CooperativeSteeringController()
  angle, lat_active = controller.update(12.5, True, False, make_car_state(torque=2.0), vehicle_model)

  assert angle == 12.5
  assert lat_active


def test_light_driver_torque_adjusts_angle(vehicle_model):
  controller = CooperativeSteeringController()
  angle = 0.0

  for _ in range(10):
    angle, lat_active = controller.update(0.0, True, True, make_car_state(torque=1.5), vehicle_model)

  assert lat_active
  assert angle > 0.0
  assert angle <= CarControllerParams.ANGLE_LIMITS.STEER_ANGLE_MAX


def test_inactive_lateral_resets_override(vehicle_model):
  controller = CooperativeSteeringController()
  for _ in range(10):
    controller.update(0.0, True, True, make_car_state(torque=1.5), vehicle_model)

  angle, lat_active = controller.update(8.0, False, True, make_car_state(torque=1.5, angle=8.0), vehicle_model)
  assert angle == 8.0
  assert not lat_active

  angle, lat_active = controller.update(8.0, True, True, make_car_state(angle=8.0), vehicle_model)
  assert angle == 8.0
  assert lat_active


@pytest.mark.parametrize(("candidate", "enabled", "expected"), (
  (CAR.TESLA_MODEL_3, False, False),
  (CAR.TESLA_MODEL_3, True, True),
  (CAR.TESLA_MODEL_Y, True, False),
  (CAR.TESLA_MODEL_S_PREAP, True, False),
))
def test_safety_flag_is_model_3_only(candidate, enabled, expected):
  params = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], False, False, True)
  from openpilot.starpilot.car.tesla.coop_preferences import prepare
  prepare(params, enabled)

  has_flag = any(config.safetyParam & TeslaSafetyFlags.COOP_STEERING.value for config in params.safetyConfigs)
  assert has_flag is expected

  if candidate != CAR.TESLA_MODEL_S_PREAP:
    assert CarController(DBC[candidate], params).coop_enabled is expected


def assert_finite_nonnegative_limit_errors(controller):
  errors = (controller.resume_limit_error_deg, controller.cooperative_limit_error_deg)
  assert all(math.isfinite(error) and error >= 0.0 for error in errors)
  assert math.isfinite(controller.cooperative_offset_deg)


def test_zero_torque_has_zero_cooperative_diagnostics(vehicle_model):
  controller = CooperativeSteeringController()

  angle, lat_active = controller.update(0.0, True, True, make_car_state(), vehicle_model)

  assert angle == 0.0
  assert lat_active
  assert controller.resume_limit_error_deg == 0.0
  assert controller.cooperative_limit_error_deg == 0.0
  assert controller.cooperative_offset_deg == 0.0
  assert_finite_nonnegative_limit_errors(controller)


def test_steady_light_torque_reports_offset_without_real_limiting(vehicle_model):
  controller = CooperativeSteeringController()

  for _ in range(100):
    controller.update(0.0, True, True, make_car_state(torque=0.9), vehicle_model)

  assert controller.cooperative_offset_deg > 2.5
  assert controller.resume_limit_error_deg < 2.5
  assert controller.cooperative_limit_error_deg < 2.5
  assert_finite_nonnegative_limit_errors(controller)


def test_torque_reversal_updates_signed_offset_without_negative_errors(vehicle_model):
  controller = CooperativeSteeringController()
  for _ in range(100):
    controller.update(0.0, True, True, make_car_state(torque=0.9), vehicle_model)

  for _ in range(200):
    controller.update(0.0, True, True, make_car_state(torque=-0.9), vehicle_model)

  assert controller.cooperative_offset_deg < -2.5
  assert_finite_nonnegative_limit_errors(controller)


def test_release_reports_gradual_offset_unwind(vehicle_model):
  controller = CooperativeSteeringController()
  for _ in range(100):
    controller.update(0.0, True, True, make_car_state(torque=1.5), vehicle_model)

  offsets = []
  for _ in range(100):
    controller.update(0.0, True, True, make_car_state(), vehicle_model)
    offsets.append(controller.cooperative_offset_deg)

  assert offsets[0] > offsets[-1] >= 0.0
  assert all(next_offset <= offset for offset, next_offset in zip(offsets, offsets[1:], strict=False))
  assert offsets[-1] == pytest.approx(0.0, abs=1e-6)
  assert_finite_nonnegative_limit_errors(controller)


def test_resume_ramp_reports_resume_limiting(vehicle_model):
  controller = CooperativeSteeringController()
  controller.reset_resume_state(0.0)
  controller.reset_override_state(0.0)

  controller.update(20.0, True, True, make_car_state(), vehicle_model)

  assert controller.resume_limit_error_deg > 2.5
  assert controller.cooperative_limit_error_deg < 2.5
  assert controller.cooperative_offset_deg == 0.0


def test_final_limiter_reports_cooperative_target_clipping(vehicle_model):
  controller = CooperativeSteeringController()
  controller.reset_resume_state(20.0)
  controller.reset_override_state(0.0)

  controller.update(20.0, True, True, make_car_state(), vehicle_model)

  assert controller.resume_limit_error_deg == 0.0
  assert controller.cooperative_limit_error_deg > 2.5
  assert controller.cooperative_offset_deg == 0.0


def test_final_limiter_remains_visible_with_light_torque(vehicle_model):
  controller = CooperativeSteeringController()
  controller.reset_resume_state(20.0)
  controller.reset_override_state(0.0)

  controller.update(20.0, True, True, make_car_state(torque=-0.9), vehicle_model)

  assert abs(controller.cooperative_offset_deg) > 0.0
  assert controller.cooperative_limit_error_deg > 2.5


def test_diagnostics_reset_on_disabled_update(vehicle_model):
  controller = CooperativeSteeringController()
  controller.reset_resume_state(20.0)
  controller.update(20.0, True, True, make_car_state(), vehicle_model)
  assert controller.cooperative_limit_error_deg > 2.5

  controller.update(4.0, True, False, make_car_state(torque=2.0), vehicle_model)

  assert controller.resume_limit_error_deg == 0.0
  assert controller.cooperative_limit_error_deg == 0.0
  assert controller.cooperative_offset_deg == 0.0
