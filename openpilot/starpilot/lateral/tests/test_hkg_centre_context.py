"""Dom center leaves qualify only through actual per-vehicle selected controllers."""

from types import SimpleNamespace
import math
import pytest
from openpilot.cereal import log
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import ControllerMode, default_selection
from openpilot.starpilot.lateral import hkg_vehicle_shaping as hkg
from openpilot.starpilot.lateral import ioniq6_policy as iq6


@pytest.mark.parametrize('vehicle,speed', [(CAR.KIA_NIRO_EV, 65.0 * 0.44704), (CAR.KIA_CARNIVAL_HEV_4TH_GEN, 10.0), (CAR.HYUNDAI_IONIQ_6, 10.0)])
@pytest.mark.parametrize('direction', [-1.0, 1.0])
@pytest.mark.parametrize('pressed', [False, True])
def test_actual_same_frame_threshold_error_output_and_override(monkeypatch, vehicle, speed, direction, pressed):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)

  def run(neutral=False):
    controller = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), 0.01, controller_mode=ControllerMode.STARPILOT)
    controller.update_torque_parameters(3.0, 0.02, 0.12)
    _policy = controller.starpilot_extension.policy
    cs = SimpleNamespace(vEgo=speed, steeringAngleDeg=0.0, steeringPressed=pressed)
    params = log.VehicleParameters.new_message(angleOffsetDeg=0.0, roll=0.0)
    calls = []
    bound = controller.friction

    def capture(error, deadzone, threshold, car, request):
      calls.append((error, deadzone, threshold))
      return bound(error, deadzone, threshold, car, request)

    monkeypatch.setattr(controller, 'friction', capture)
    if neutral:
      if vehicle == CAR.KIA_NIRO_EV:
        monkeypatch.setattr(hkg, 'KIA_NIRO_EV_CENTER_FRICTION_THRESHOLD_GAIN', 0.0)
      elif vehicle == CAR.KIA_CARNIVAL_HEV_4TH_GEN:
        monkeypatch.setattr(hkg, 'KIA_CARNIVAL_HEV_CENTER_FRICTION_INCREMENT', 0.0)
        monkeypatch.setattr(hkg, 'KIA_CARNIVAL_HEV_CENTER_ERROR_REDUCTION', 0.0)
      else:
        monkeypatch.setattr(iq6, 'get_ioniq_6_2023_center_friction_increment', lambda *_: 0.0)
    for _ in range(150):
      out, _, state = controller.update(True, cs, VehicleModel(cp), params, False, direction * 0.02 / speed**2, False, 0.2)
      assert math.isfinite(out) and abs(out) <= controller.steer_max + 1e-6
    live = (out, state.to_dict(), calls[-1], controller.torque_params.to_dict(), (controller.pid.pos_limit, controller.pid.neg_limit, controller.steer_max))
    calls.clear()
    off, _, offstate = controller.update(False, cs, VehicleModel(cp), params, False, 0.0, False, 0.2)
    assert off == 0.0 and not offstate.active and not calls
    return live

  actual = run()
  baseline = run(True)
  assert actual[3:] == baseline[3:]
  if pressed:
    assert actual == baseline
  else:
    assert actual[2][0:2] == baseline[2][0:2]  # Friction error/deadzone retain original stage.
    assert actual[2][2] > baseline[2][2]
    assert abs(actual[1]['f']) < abs(baseline[1]['f'])
    if vehicle == CAR.KIA_CARNIVAL_HEV_4TH_GEN:
      assert abs(actual[1]['error']) < abs(baseline[1]['error'])
      assert actual[1]['error'] == pytest.approx(0.75 * baseline[1]['error'])
    else:
      assert actual[1]['error'] == baseline[1]['error']


def test_niro_saved_standard_and_other_factory_have_no_new_profile():
  cp = interfaces[CAR.KIA_NIRO_EV].get_non_essential_params(CAR.KIA_NIRO_EV)
  assert default_selection(cp).mode == ControllerMode.STARPILOT
  standard = LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), 0.01, controller_mode=ControllerMode.STANDARD)
  assert standard.starpilot_extension is None
  for field in ('passive', 'dashcamOnly', 'notCar'):
    other = cp.as_reader().as_builder()
    setattr(other, field, True)
    from openpilot.starpilot.lateral.hkg_vehicle_policy import policy_for

    assert policy_for(other) is None


@pytest.mark.parametrize('future,error,jerk,speed', [(0.5, 0.02, 0.0, 10.0), (0.02, 0.4, 0.0, 10.0), (0.02, 0.02, 1.0, 10.0), (0.02, 0.02, 0.0, 25.0)])
def test_hev_center_leaf_releases_for_turn_error_jerk_and_high_speed(future, error, jerk, speed):
  assert hkg.get_kia_carnival_hev_center_weight(speed, 0.02, 0.02 - error, jerk, future) == 0.0


@pytest.mark.parametrize('vehicle', [CAR.KIA_CARNIVAL_HEV_4TH_GEN, CAR.HYUNDAI_IONIQ_6])
def test_actual_previous_driver_release_does_not_immediately_apply_center_delta(monkeypatch, vehicle):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  controller = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), 0.01, controller_mode=ControllerMode.STARPILOT)
  cs = SimpleNamespace(vEgo=10.0, steeringAngleDeg=0.0, steeringPressed=True)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0.0, roll=0.0)
  vm = VehicleModel(cp)
  for _ in range(150):
    controller.update(True, cs, vm, params, False, 0.0002, False, 0.2)
  calls = []
  original = controller.friction

  def capture(error, deadzone, threshold, car, request):
    calls.append(threshold)
    return original(error, deadzone, threshold, car, request)

  monkeypatch.setattr(controller, 'friction', capture)
  cs.steeringPressed = False
  _, _, state = controller.update(True, cs, vm, params, False, 0.0002, False, 0.2)
  if vehicle == CAR.KIA_CARNIVAL_HEV_4TH_GEN:
    assert controller.starpilot_extension.policy.hev_center_weight == 0.0
    assert calls[-1] == pytest.approx(hkg.get_kia_carnival_friction_threshold(10.0, state.desiredLateralAccel, state.desiredLateralJerk))
  else:
    taper = iq6.get_ioniq_6_center_taper_scale(state.desiredLateralAccel, 10.0)
    assert calls[-1] == pytest.approx(iq6.get_ioniq_6_friction_threshold(10.0, state.desiredLateralAccel, state.desiredLateralJerk) / max(taper, 1e-3))
