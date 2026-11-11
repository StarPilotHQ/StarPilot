import pytest

from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import ControllerMode, default_selection, replace_mode, selection_from_bytes
from openpilot.starpilot.lateral.hkg_vehicle_policy import HKGVehicleTorquePolicy, PROFILES, policy_for


CASES = [(vehicle, profile, factor) for profile, (vehicles, factor) in PROFILES.items() for vehicle in sorted(vehicles, key=str)]


@pytest.mark.parametrize('vehicle,profile,factor', CASES)
def test_exact_profile_defaults_and_saved_controller_selection(vehicle, profile, factor):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  assert policy_for(cp) == profile
  expected = ControllerMode.STARPILOT
  assert default_selection(cp).mode == expected
  for raw in (b'{', b'{"version":true,"vehicles":{}}', b'{"version":1,"vehicles":{},"vehicles":{}}', b'x' * 4097):
    assert selection_from_bytes(cp, raw).mode == expected
  unrelated = interfaces[CAR.HYUNDAI_IONIQ_6].get_non_essential_params(CAR.HYUNDAI_IONIQ_6)
  assert selection_from_bytes(cp, replace_mode(None, unrelated, ControllerMode.STANDARD)).mode == expected
  saved = replace_mode(None, cp, ControllerMode.STARPILOT)
  choice = selection_from_bytes(cp, saved)
  assert choice.mode == ControllerMode.STARPILOT
  assert choice.policy == profile
  ci = interfaces[vehicle](cp)
  default = LatControlTorque(cp.as_reader(), ci, 0.01)
  assert bool(default.starpilot_extension) == (expected == ControllerMode.STARPILOT)
  standard_choice = selection_from_bytes(cp, replace_mode(saved, cp, ControllerMode.STANDARD))
  standard = LatControlTorque(cp.as_reader(), ci, 0.01, controller_mode=standard_choice.mode)
  assert standard.starpilot_extension is None
  assert standard.torque_params.latAccelFactor == pytest.approx(cp.lateralTuning.torque.latAccelFactor)
  controller = LatControlTorque(cp.as_reader(), ci, 0.01, controller_mode=choice.mode)
  policy = controller.starpilot_extension.policy
  assert isinstance(policy, HKGVehicleTorquePolicy)
  assert policy.profile == profile
  assert controller.torque_params.latAccelFactor == pytest.approx(cp.lateralTuning.torque.latAccelFactor * factor)
  assert controller.pid.pos_limit == pytest.approx(controller.lateral_accel_from_torque(controller.steer_max, cp.lateralTuning.torque))
  for _ in range(3):
    controller.update_torque_parameters(3.0, 0.02, 0.12)
    assert controller.torque_params.latAccelFactor == pytest.approx(3.0 * factor)
    assert controller.torque_params.latAccelOffset == pytest.approx(0.02)
    assert controller.torque_params.friction == pytest.approx(0.12)
  assert selection_from_bytes(cp, replace_mode(saved, cp, ControllerMode.STANDARD)).mode == ControllerMode.STANDARD


@pytest.mark.parametrize('vehicle,profile,factor', CASES)
def test_unadmitted_and_cross_vehicle_choices_are_not_applied(vehicle, profile, factor):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  for field in ('passive', 'dashcamOnly', 'notCar'):
    other = cp.as_reader().as_builder()
    setattr(other, field, True)
    assert policy_for(other) is None
  cp.brand = 'gm'
  assert policy_for(cp) is None
  cp.brand = 'hyundai'
  saved = replace_mode(None, cp, ControllerMode.STARPILOT)
  other = interfaces[CAR.HYUNDAI_IONIQ_5].get_non_essential_params(CAR.HYUNDAI_IONIQ_5)
  if vehicle != CAR.HYUNDAI_IONIQ_5:
    assert selection_from_bytes(other, saved).mode == ControllerMode.STARPILOT
    assert selection_from_bytes(other, saved).source == 'default'


@pytest.mark.parametrize('vehicle,profile,factor', CASES)
def test_invalid_torque_contract_cannot_select_vehicle_policy(vehicle, profile, factor):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  for field, value in (('latAccelFactor', 0.0), ('latAccelFactor', float('nan')), ('latAccelOffset', float('inf')), ('friction', -1.0)):
    other = cp.as_reader().as_builder()
    setattr(other.lateralTuning.torque, field, value)
    assert policy_for(other) is None
    assert selection_from_bytes(other, None).mode == ControllerMode.STANDARD
  cp.lateralTuning.init('pid')
  assert policy_for(cp) is None


@pytest.mark.parametrize('kph,desired,increment', (
  (60., 0., 0.), (100., .65, 0.), (110., .65, .30), (120., .65, .30),
  (120., -.65, .30), (120., 1.6, .15), (120., -1.6, .15),
  (120., 2.0, 0.), (140., 0., .30), (140., -2., 0.),
))
def test_kona_2022_high_speed_friction_boundaries(monkeypatch, kph, desired, increment):
  from openpilot.starpilot.lateral import hkg_vehicle_shaping as shaping
  threshold = shaping.get_kona_ev_2022_friction_threshold(kph / 3.6, desired)
  monkeypatch.setattr(shaping, 'KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_GAIN', 0.)
  baseline = shaping.get_kona_ev_2022_friction_threshold(kph / 3.6, desired)
  assert threshold == pytest.approx(baseline + increment)
  assert baseline <= threshold <= baseline + .30


def test_kona_2022_high_speed_friction_reaches_selected_controller(monkeypatch):
  from types import SimpleNamespace
  from openpilot.cereal import log
  from opendbc.car.vehicle_model import VehicleModel
  from opendbc.car.lateral import get_friction
  from openpilot.starpilot.lateral import hkg_vehicle_shaping as shaping

  cp = interfaces[CAR.HYUNDAI_KONA_EV_2022].get_non_essential_params(CAR.HYUNDAI_KONA_EV_2022)
  cs = SimpleNamespace(vEgo=120. / 3.6, steeringAngleDeg=0., steeringPressed=False)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0., roll=0.)

  def run():
    controller = LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), .01, controller_mode=ControllerMode.STARPILOT)
    for _ in range(60):
      output, _, state = controller.update(True, cs, VehicleModel(cp), params, False, .0001, False, .28)
    return controller, output, state

  controller, output, state = run()
  threshold = shaping.get_kona_ev_2022_friction_threshold(cs.vEgo, .65)
  standard = shaping.get_standard_friction_threshold(cs.vEgo)
  for direction in (-1., 1.):
    small = get_friction(direction * .1, 0., threshold, controller.torque_params)
    assert abs(small) < abs(get_friction(direction * .1, 0., standard, controller.torque_params))
    assert small * direction > 0.
    assert get_friction(direction, 0., threshold, controller.torque_params) == pytest.approx(
      get_friction(direction, 0., standard, controller.torque_params))
  monkeypatch.setattr(shaping, 'KONA_EV_2022_HIGH_SPEED_FRICTION_THRESHOLD_GAIN', 0.)
  baseline, base_output, base_state = run()
  assert state.active and base_state.active
  assert state.desiredLateralAccel == pytest.approx(base_state.desiredLateralAccel)
  assert state.p == pytest.approx(base_state.p)
  assert abs(state.f) < abs(base_state.f)
  assert abs(output) < abs(base_output)
  assert controller.steer_max == baseline.steer_max


@pytest.mark.parametrize('vehicle', (CAR.HYUNDAI_IONIQ_5, CAR.HYUNDAI_KONA_NON_SCC))
def test_kona_2022_friction_does_not_change_sibling_policies(monkeypatch, vehicle):
  from openpilot.starpilot.lateral import hkg_vehicle_shaping as shaping

  def unexpected(*_):
    raise AssertionError('Kona 2022 friction used by sibling policy')

  monkeypatch.setattr(shaping, 'get_kona_ev_2022_friction_threshold', unexpected)
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  controller = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), .01, controller_mode=ControllerMode.STARPILOT)
  threshold, scale = controller.starpilot_extension.policy.friction_context(.1, 0., 0., 120. / 3.6)
  assert threshold > 0. and scale > 0.


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('direction', (-1., 1.))
def test_ev6_highway_center_damping_preserves_controller_and_curve_response(monkeypatch, alpha, direction):
  from types import SimpleNamespace
  import math
  from openpilot.cereal import log
  from opendbc.car.vehicle_model import VehicleModel
  from openpilot.starpilot.lateral import hkg_vehicle_shaping as shaping

  cp = interfaces[CAR.KIA_EV6].get_non_essential_params(CAR.KIA_EV6)
  cp.openpilotLongitudinalControl, cp.pcmCruise = alpha, not alpha
  cs = SimpleNamespace(vEgo=26., steeringAngleDeg=0., steeringPressed=False)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0., roll=0.)
  selected = shaping.KIA_EV6_HIGHWAY_CENTER_DAMPING
  assert selected == .08

  def run(taper, turn, mode=ControllerMode.STARPILOT):
    monkeypatch.setattr(shaping, 'KIA_EV6_HIGHWAY_CENTER_DAMPING', taper)
    controller = LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), .01, controller_mode=mode)
    vm = VehicleModel(cp)
    outputs, stages = [], []
    for tick in range(300):
      acceleration = direction * (1.5 if turn else .12 * math.sin(2 * math.pi * .4 * tick * .01))
      value, _, state = controller.update(True, cs, vm, params, False, acceleration / cs.vEgo**2, False, .386)
      assert math.isfinite(value) and abs(value) <= controller.steer_max + 1e-6
      outputs.append(value)
      stages.append((state.p, state.i, state.d, state.f))
    off, _, state = controller.update(False, cs, vm, params, False, 0., False, .386)
    assert off == 0. and not state.active
    return outputs, stages, controller.torque_params.to_dict(), (controller.pid.pos_limit, controller.pid.neg_limit, controller.steer_max)

  actual, baseline = run(selected, False), run(0., False)
  assert actual[1:] == baseline[1:]
  assert sum(x*x for x in actual[0]) < .88 * sum(x*x for x in baseline[0])
  assert all(a*b >= -1e-12 for a, b in zip(actual[0], baseline[0], strict=True))
  actual_turn, baseline_turn = run(selected, True), run(0., True)
  assert actual_turn[1:] == baseline_turn[1:]
  assert actual_turn[0] == pytest.approx(baseline_turn[0], rel=1e-6, abs=1e-8)
  assert run(selected, False, ControllerMode.STANDARD) == run(0., False, ControllerMode.STANDARD)


def test_ev6_taper_keeps_low_speed_and_substantial_curve_effect_small():
  from openpilot.starpilot.lateral import hkg_vehicle_shaping as shaping
  for speed in (0., 3., 26., 36.):
    for acceleration in (-2., -1., 0., 1., 2.):
      actual = shaping.get_kia_ev6_center_output_scale(acceleration, speed)
      speed_weight = shaping._kia_ev6_sigmoid((speed - shaping.KIA_EV6_CENTER_OUTPUT_TAPER_SPEED) /
                                              shaping.KIA_EV6_CENTER_OUTPUT_TAPER_SPEED_WIDTH)
      center_weight = shaping._kia_ev6_sigmoid((shaping.KIA_EV6_CENTER_OUTPUT_TAPER_LAT - abs(acceleration)) /
                                               shaping.KIA_EV6_CENTER_OUTPUT_TAPER_LAT_WIDTH)
      old = 1. - .14 * speed_weight * center_weight
      assert .78 <= actual <= old <= 1.
      if speed <= 3.:
        assert old - actual < .002
      if abs(acceleration) >= 1.:
        assert old - actual < .00002


@pytest.mark.parametrize('vehicle,profile,factor', CASES)
@pytest.mark.parametrize('alpha_long', [False, True])
def test_vehicle_tune_defaults_are_independent_of_longitudinal_choice(vehicle, profile, factor, alpha_long):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  cp.openpilotLongitudinalControl = alpha_long
  assert default_selection(cp).mode == ControllerMode.STARPILOT
  raw = replace_mode(None, cp, ControllerMode.STANDARD)
  selected = selection_from_bytes(cp, raw)
  assert selected.mode == ControllerMode.STANDARD
  assert selected.source == 'saved'
  assert selected.policy == profile


@pytest.mark.parametrize('vehicle', [CAR.KIA_CARNIVAL_2025, CAR.KIA_CARNIVAL_HEV_4TH_GEN])
@pytest.mark.parametrize('alpha', [False, True])
def test_carnival_crawl_center_tune_reaches_selected_controller(monkeypatch, vehicle, alpha):
  from types import SimpleNamespace
  from openpilot.cereal import log
  from opendbc.car.vehicle_model import VehicleModel
  from opendbc.car import structs
  from openpilot.starpilot.lateral import hkg_vehicle_shaping as shaping

  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  cp.openpilotLongitudinalControl, cp.pcmCruise = alpha, not alpha
  cs = SimpleNamespace(vEgo=2., steeringAngleDeg=8., steeringPressed=False, standstill=False,
                       steerFaultTemporary=False, steerFaultPermanent=False,
                       gearShifter=structs.CarState.GearShifter.drive)
  params = log.VehicleParameters.new_message(angleOffsetDeg=0., roll=0.)

  def run(enabled):
    controller = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), .01,
                                  controller_mode=ControllerMode.STARPILOT)
    with monkeypatch.context() as patch:
      if not enabled:
        patch.setattr(shaping, 'get_kia_carnival_crawl_center_weight', lambda *args: 0., raising=False)
      output, _, state = controller.update(True, cs, VehicleModel(cp), params, False, .001, False, .3)
    return controller, output, state

  baseline, old_output, old_state = run(False)
  candidate, new_output, new_state = run(True)
  policy = candidate.starpilot_extension.policy
  assert policy.hev_center_weight == pytest.approx(1.)
  assert abs(new_state.p) < abs(old_state.p)
  assert abs(new_output) < abs(old_output)
  assert candidate.pid.pos_limit == baseline.pid.pos_limit
  assert candidate.pid.neg_limit == baseline.pid.neg_limit
  assert candidate.torque_params.to_dict() == baseline.torque_params.to_dict()
  cs.steeringPressed = True
  controller, _, _ = run(True)
  assert controller.starpilot_extension.policy.hev_center_weight == 0.
  cs.steeringPressed = False
  cs.vEgo = 6.
  candidate, output, state = run(True)
  baseline, old_output, old_state = run(False)
  assert output == old_output and state.to_dict() == old_state.to_dict()


@pytest.mark.parametrize('speed,setpoint,future,expected', [
  (0., 0., 0., 0.), (.3, 0., 0., 0.), (1., 0., 0., 1.), (2., .1, -.1, 1.),
  (3., -.1, .1, 1.), (4., 0., 0., .5), (5., 0., 0., 0.), (30., 0., 0., 0.),
  (2., .3, 0., 0.), (2., 0., -.3, 0.), (2., -.2, .1, .5),
])
def test_carnival_crawl_center_tune_boundaries(speed, setpoint, future, expected):
  from openpilot.starpilot.lateral import hkg_vehicle_shaping as shaping
  assert shaping.get_kia_carnival_crawl_center_weight(speed, setpoint, future) == pytest.approx(expected)
  assert shaping.get_kia_carnival_crawl_center_weight(speed, -setpoint, -future) == pytest.approx(expected)
