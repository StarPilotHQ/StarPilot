"""Exact Dom leaves must reach actual selected controllers, preserving native ownership."""

from types import SimpleNamespace
import math
import pytest
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import ControllerMode, default_selection
from openpilot.starpilot.lateral import hkg_vehicle_shaping as shaping


@pytest.mark.parametrize(
  'vehicle,helper,speed,curvature',
  [
    (CAR.KIA_STINGER_2022, 'get_kia_stinger_2022_friction_threshold', 28.0, 0.00005),
    (CAR.HYUNDAI_IONIQ_5, 'get_ioniq_5_low_speed_output_limit', 1.0, 0.02),
    (CAR.KIA_CARNIVAL_HEV_4TH_GEN, 'get_kia_carnival_unwind_output_scale', 15.0, 0.0002),
    (CAR.HYUNDAI_KONA_EV_2022, 'get_kona_ev_2022_friction_threshold', 110.0 / 3.6, 0.00065),
  ],
)
@pytest.mark.parametrize('direction', [-1.0, 1.0])
@pytest.mark.parametrize('pressed', [False, True])
def test_actual_controller_context_defaults_live_tune_limits_and_off(monkeypatch, vehicle, helper, speed, curvature, direction, pressed):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  ci = interfaces[vehicle](cp)
  assert default_selection(cp).mode == (ControllerMode.STARPILOT if vehicle == CAR.KIA_CARNIVAL_HEV_4TH_GEN else ControllerMode.STANDARD)
  native = LatControlTorque(cp.as_reader(), ci, 0.01, controller_mode=ControllerMode.STANDARD)
  assert native.starpilot_extension is None
  actual = LatControlTorque(cp.as_reader(), ci, 0.01, controller_mode=ControllerMode.STARPILOT)
  actual.update_torque_parameters(3.0, 0.02, 0.12)
  tune = actual.torque_params.to_dict()
  limits = (actual.pid.pos_limit, actual.pid.neg_limit, actual.steer_max)
  calls = []
  original = getattr(shaping, helper)

  def capture(*args, **kwargs):
    calls.append((args, kwargs))
    return original(*args, **kwargs)

  monkeypatch.setattr(shaping, helper, capture)
  cs = SimpleNamespace(vEgo=speed, steeringAngleDeg=0.0, steeringPressed=pressed)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0.0, roll=0.0)
  for _ in range(80):
    output, _, state = actual.update(True, cs, VehicleModel(cp), params, False, direction * curvature, False, 0.28)
    assert math.isfinite(output) and abs(output) <= actual.steer_max + 1e-6
  assert state.active and calls
  args, kwargs = calls[-1]
  if vehicle == CAR.KIA_STINGER_2022:
    assert args[3] == pytest.approx(direction * curvature * speed**2)
    assert args[4] is pressed
  elif vehicle == CAR.HYUNDAI_IONIQ_5:
    assert args[3] == pytest.approx(0.0 if pressed else direction * curvature)
    assert math.isfinite(args[4])
  elif vehicle == CAR.KIA_CARNIVAL_HEV_4TH_GEN:
    assert (kwargs['output_torque'] is None) is pressed
  assert actual.torque_params.to_dict() == tune
  assert (actual.pid.pos_limit, actual.pid.neg_limit, actual.steer_max) == limits
  calls.clear()
  output, _, state = actual.update(False, cs, VehicleModel(cp), params, False, direction * curvature, False, 0.28)
  assert output == 0.0 and not state.active and calls == []


@pytest.mark.parametrize('direction', [-1.0, 1.0])
def test_stinger_threshold_increment_bypasses_override_and_future_turn(direction):
  speed = 60.0 * 0.44704
  old = shaping.get_kia_stinger_2022_friction_threshold(speed, direction * 0.1, 0.0, 0.0, True)
  assert shaping.get_kia_stinger_2022_friction_threshold(speed, direction * 0.1, 0.0) == pytest.approx(old + 0.2)
  assert shaping.get_kia_stinger_2022_friction_threshold(speed, direction * 0.1, 0.0, direction * 0.4) == pytest.approx(old)


@pytest.mark.parametrize('direction', [-1.0, 1.0])
def test_ioniq5_creep_floor_only_for_requested_turn_direction(direction):
  old = shaping.get_ioniq_5_low_speed_output_limit(0.01, 0.0, 1.0)
  actual = shaping.get_ioniq_5_low_speed_output_limit(0.01, 0.0, 1.0, direction * 0.02, direction * 0.1)
  assert actual >= 0.3 and actual > old
  assert shaping.get_ioniq_5_low_speed_output_limit(0.01, 0.0, 1.0, direction * 0.02, -direction * 0.1) == old


@pytest.mark.parametrize('direction', [-1.0, 1.0])
def test_carnival_countersteer_is_retained(direction):
  assert shaping.get_kia_carnival_unwind_output_scale(direction * 0.2, direction * 0.5, -direction, 25.0, output_torque=-direction * 0.1) == 1.0
  assert shaping.get_kia_carnival_unwind_output_scale(direction * 0.2, direction * 0.5, -direction, 25.0, output_torque=direction * 0.1) < 1.0


@pytest.mark.parametrize(
  'vehicle,helper', [(CAR.KIA_STINGER_2022, 'get_kia_stinger_2022_friction_threshold'), (CAR.HYUNDAI_IONIQ_5, 'get_ioniq_5_low_speed_output_limit')]
)
def test_actual_first_driver_release_tick_preserves_dom_override_bypass(monkeypatch, vehicle, helper):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  controller = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), 0.01, controller_mode=ControllerMode.STARPILOT)
  cs = SimpleNamespace(vEgo=28.0, steeringAngleDeg=0.0, steeringPressed=True)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0.0, roll=0.0)
  vm = VehicleModel(cp)
  controller.update(True, cs, vm, params, False, 0.00005, False, 0.2)
  calls = []
  original = getattr(shaping, helper)

  def capture(*args, **kwargs):
    calls.append((args, kwargs))
    return original(*args, **kwargs)

  monkeypatch.setattr(shaping, helper, capture)
  cs.steeringPressed = False
  controller.update(True, cs, vm, params, False, 0.00005, False, 0.2)
  args, _ = calls[-1]
  if vehicle == CAR.KIA_STINGER_2022:
    assert args[4] is True
  else:
    assert args[3] == 0.0
  calls.clear()
  controller.update(True, cs, vm, params, False, 0.00005, False, 0.2)
  args, _ = calls[-1]
  if vehicle == CAR.KIA_STINGER_2022:
    assert args[4] is False
  else:
    assert args[3] == pytest.approx(0.00005)
