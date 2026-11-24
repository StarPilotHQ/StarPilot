"""Exact Dom leaves must reach actual selected controllers, preserving native ownership."""

from types import SimpleNamespace
import math
import pytest
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR, HyundaiFlags
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
  assert default_selection(cp).mode == ControllerMode.STARPILOT
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


@pytest.mark.parametrize('direction', [-1.0, 1.0])
@pytest.mark.parametrize('op_long', [False, True])
@pytest.mark.parametrize('hda2,speed,lateral_accel,pressed,prior_pressed,expected', [
  (True, 30.0, 0.9, False, False, 0.18),
  (True, 28.0, 0.9, False, False, 0.09),
  (True, 26.0, 0.9, False, False, 0.0),
  (True, 30.0, 0.3, False, False, 0.0),
  (True, 30.0, 1.8, False, False, 0.0),
  (True, 30.0, 0.9, True, False, 0.0),
  (True, 30.0, 0.9, False, True, 0.0),
  (False, 30.0, 0.9, False, False, 0.0),
])
def test_ioniq5_hda2_highway_arc_friction(direction, op_long, hda2, speed, lateral_accel, pressed, prior_pressed, expected):
  vehicle = CAR.HYUNDAI_IONIQ_5
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  cp.openpilotLongitudinalControl = op_long
  cp.pcmCruise = not op_long
  if hda2:
    cp.flags |= int(HyundaiFlags.CANFD_LKA_STEER_MSG)
  else:
    cp.flags &= ~int(HyundaiFlags.CANFD_LKA_STEER_MSG)
  controller = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), 0.01, controller_mode=ControllerMode.STARPILOT)
  policy = controller.starpilot_extension.policy
  policy.steering_pressed, policy.previous_pressed = pressed, prior_pressed
  setpoint, jerk = direction * lateral_accel, direction * 0.15
  threshold, scale = policy.friction_context(setpoint, jerk, 0.0, speed)
  assert threshold == pytest.approx(shaping.get_ioniq_5_friction_threshold(speed, setpoint, jerk) + expected)
  assert scale == pytest.approx(1 + (shaping.get_ioniq_5_friction_scale(speed, setpoint, jerk) - 1) *
                               shaping.get_ioniq_5_center_taper_scale(setpoint, speed))


@pytest.mark.parametrize('op_long', [False, True])
@pytest.mark.parametrize('direction', [-1.0, 1.0])
@pytest.mark.parametrize('hda2,speed,accel,jerk,pressed,prior_pressed,blend', [
  (True, 19.5, 1.95, 0.5, False, False, 0.25),
  (True, 22.0, 1.95, 0.5, False, False, 0.125),
  (True, 19.5, 1.95, 0.0, False, False, 0.0),
  (True, 19.5, 0.9, 0.5, False, False, 0.0),
  (True, 19.5, 2.65, 0.5, False, False, 0.0),
  (True, 16.0, 1.95, 0.5, False, False, 0.0),
  (True, 29.0, 1.95, 0.5, False, False, 0.0),
  (True, 19.5, 1.95, 0.5, True, False, 0.0),
  (True, 19.5, 1.95, 0.5, False, True, 0.0),
  (False, 19.5, 1.95, 0.5, False, False, 0.0),
])
def test_ioniq5_hda2_tight_curve_feedforward(op_long, direction, hda2, speed, accel, jerk, pressed, prior_pressed, blend):
  vehicle = CAR.HYUNDAI_IONIQ_5
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  cp.openpilotLongitudinalControl = op_long
  cp.pcmCruise = not op_long
  if hda2:
    cp.flags |= int(HyundaiFlags.CANFD_LKA_STEER_MSG)
  else:
    cp.flags &= ~int(HyundaiFlags.CANFD_LKA_STEER_MSG)
  controller = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), 0.01, controller_mode=ControllerMode.STARPILOT)
  policy = controller.starpilot_extension.policy
  policy.steering_pressed, policy.previous_pressed = pressed, prior_pressed
  setpoint = accel * direction
  jerk *= -direction
  original = shaping.get_ioniq_5_ff_scale(setpoint, jerk, speed)
  steady = shaping.get_ioniq_5_ff_scale(setpoint, 0.0, speed)
  expected = (original + blend * (steady - original)) * shaping.get_ioniq_5_center_taper_scale(setpoint, speed)
  assert policy.feedforward_context(1.0, setpoint, jerk, setpoint, speed) == pytest.approx(expected)


@pytest.mark.parametrize('op_long', [False, True])
@pytest.mark.parametrize('hda2', [False, True])
def test_ioniq5_hda1_feedforward_damps_transients_and_reseeds_on_override(op_long, hda2):
  vehicle = CAR.HYUNDAI_IONIQ_5
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  cp.openpilotLongitudinalControl = op_long
  cp.pcmCruise = not op_long
  if hda2:
    cp.flags |= int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    cp.flags &= ~int(HyundaiFlags.CANFD_CAMERA_SCC)
  else:
    cp.flags &= ~int(HyundaiFlags.CANFD_LKA_STEER_MSG)
    cp.flags |= int(HyundaiFlags.CANFD_CAMERA_SCC)
  controller = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), 0.01, controller_mode=ControllerMode.STARPILOT)
  policy = controller.starpilot_extension.policy
  vm = VehicleModel(cp)
  params = SimpleNamespace(angleOffsetDeg=0., roll=0.)
  state = SimpleNamespace(vEgo=30., steeringAngleDeg=0., steeringPressed=False)
  original = shaping.get_ioniq_5_ff_scale(.5, 0., 30.) * shaping.get_ioniq_5_center_taper_scale(.5, 30.)

  def tick(target, active=True):
    controller.update(active, state, vm, params, False, target / state.vEgo**2, False, .37)
    return policy.feedforward_context(1.0, .5, 0., .5, state.vEgo) / original

  assert tick(0.) == pytest.approx(1.)
  assert tick(.1) == pytest.approx(1. if hda2 else 1. - .1 * .20 / .21)
  assert tick(.5) == pytest.approx(1. if hda2 else .76)
  for _ in range(180):
    steady = tick(.5)
  assert steady == pytest.approx(1.0, abs=.0001)
  state.steeringPressed = True
  assert tick(-.5) == pytest.approx(1.)
  state.steeringPressed = False
  assert tick(.5) == pytest.approx(1.)
  tick(.5, active=False)
  assert tick(-.5) == pytest.approx(1.)
