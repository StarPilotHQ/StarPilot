import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.tests.test_palisade_2023 import params as topology_params
from opendbc.car.hyundai.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import ControllerMode, default_selection, replace_mode, selection_from_bytes
from openpilot.starpilot.lateral.palisade_policy import PalisadeTorquePolicy, supported_cp
from openpilot.starpilot.lateral import palisade_shaping as shaping
from openpilot.starpilot.lateral.torque_shaping import KP, KI


@pytest.mark.parametrize('speed,accel,jerk,ff,threshold,friction,center,output', [
  (2., .18, .3, 1.1469630171215324, .16001729374366647, 1.034684825233675, .9981430724236608, .9979939519844233),
  (8.9408, -.25, -.45, 1.131157979745277, .18376493402775368, 1.0340640585840144, .982732898487096, .9866506253915932),
  (22., .8, -.6, 1.1001436880961013, .2366944877586695, 1.0045028671743204, .9999907698683685, .9999871475466129),
  (33.528, -1.4, .8, 1.042046705933432, .2738465058489194, 1.0070488018554904, .9999999998280955, .9999999997426313),
])
def test_frozen_phase_and_side_formula_vectors(speed, accel, jerk, ff, threshold, friction, center, output):
  actual = (shaping.get_palisade_ff_scale(accel, jerk, speed), shaping.get_palisade_friction_threshold(speed, accel, jerk),
            shaping.get_palisade_friction_scale(speed, accel, jerk), shaping.get_palisade_center_taper_scale(accel, speed),
            shaping.get_palisade_center_output_scale(accel, speed))
  assert actual == pytest.approx((ff, threshold, friction, center, output), abs=1e-14, rel=0)


@pytest.mark.parametrize('vehicle', [CAR.HYUNDAI_PALISADE, CAR.HYUNDAI_PALISADE_2023])
def test_default_choice_startup_and_repeated_live_factor(vehicle):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  ci = interfaces[vehicle](cp)
  controller = LatControlTorque(cp.as_reader(), ci, .01)
  assert default_selection(cp).policy == 'palisade'
  assert isinstance(controller.starpilot_extension.policy, PalisadeTorquePolicy)
  assert controller.torque_params.latAccelFactor == pytest.approx(cp.lateralTuning.torque.latAccelFactor * .98)
  assert controller.pid._k_p == [[0.], [KP]]
  assert controller.pid._k_i == ([0.], [KI])
  raw_limit = controller.lateral_accel_from_torque(controller.steer_max, cp.lateralTuning.torque)
  assert controller.pid.pos_limit == pytest.approx(raw_limit)
  for _ in range(3):
    controller.update_torque_parameters(3., .02, .12)
    assert controller.torque_params.latAccelFactor == pytest.approx(2.94)
    assert controller.torque_params.latAccelOffset == pytest.approx(.02)
    assert controller.torque_params.friction == pytest.approx(.12)
    assert controller.pid.pos_limit == pytest.approx(controller.lateral_accel_from_torque(controller.steer_max, controller.torque_params))
  saved = replace_mode(None, cp, ControllerMode.STANDARD)
  assert selection_from_bytes(cp, saved).mode == ControllerMode.STANDARD
  assert LatControlTorque(cp.as_reader(), ci, .01, controller_mode=ControllerMode.STANDARD).starpilot_extension is None


@pytest.mark.parametrize('topology', ['hdai', 'hdaii', '110'])
def test_actual_selected_controller_output_and_inactive_recovery(topology):
  cp = topology_params(topology)
  controller = LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), .01)
  vm = VehicleModel(cp)
  state = SimpleNamespace(vEgo=12., steeringAngleDeg=0., steeringPressed=False)
  parameters = log.VehicleParameters.new_message(angleOffsetDeg=0., roll=.01)
  for curvature in (.0002, -.0002, 0.):
    for _ in range(25):
      torque, _, trace = controller.update(True, state, vm, parameters, False, curvature, False, .2)
      assert math.isfinite(torque) and math.isfinite(trace.f)
      limit = max(abs(controller.torque_from_lateral_accel(bound, controller.torque_params))
                  for bound in (controller.pid.pos_limit, controller.pid.neg_limit))
      assert abs(torque) <= limit + 1e-6
      assert trace.active
  with patch.object(controller.pid, 'update', wraps=controller.pid.update) as update:
    state.steeringPressed = True
    controller.update(True, state, vm, parameters, False, .0002, False, .2)
    assert update.call_args.kwargs['freeze_integrator']
  torque, _, trace = controller.update(False, state, vm, parameters, False, .0002, False, .2)
  assert torque == 0 and not trace.active and controller.pid.i == 0
  state.steeringPressed = False
  assert math.isfinite(controller.update(True, state, vm, parameters, False, .0002, False, .2)[0])


def test_unrelated_or_unadmitted_profiles_cannot_select_palisade():
  cp = interfaces[CAR.HYUNDAI_PALISADE].get_non_essential_params(CAR.HYUNDAI_PALISADE)
  for field in ('passive', 'dashcamOnly', 'notCar'):
    candidate = cp.as_reader().as_builder()
    setattr(candidate, field, True)
    assert not supported_cp(candidate)
  cp.carFingerprint = CAR.HYUNDAI_IONIQ_6
  assert not supported_cp(cp)


def test_only_palisade_hda2_softens_highway_center_corrections():
  def policy(topology):
    cp = topology_params(topology)
    controller = LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), .01)
    return controller.starpilot_extension.policy

  hda1, hda2 = policy('hdai'), policy('hdaii')
  assert hda2.output(.5, .1, 30.) < .91 * hda1.output(.5, .1, 30.)
  assert hda2.output(.5, .8, 30.) == pytest.approx(hda1.output(.5, .8, 30.), abs=4e-6)
  assert hda2.output(.5, .1, 4.) == pytest.approx(hda1.output(.5, .1, 4.), abs=.002)


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('direction', (-1., 1.))
def test_hda2_softens_rapid_highway_corrections_across_longitudinal_modes(alpha, direction):
  cp = topology_params('hdaii')
  cp.openpilotLongitudinalControl, cp.pcmCruise = alpha, not alpha
  policy = LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), .01).starpilot_extension.policy
  value, demand, jerk = direction * .4, direction * .45, direction * 1.1
  baseline = policy.output(value, demand, 31.)
  assert policy.output_context(value, demand, jerk, 0., 31.) == pytest.approx(.88 * baseline)
  assert policy.output_context(value, demand, .2, 0., 31.) == baseline
  assert policy.output_context(value, direction, jerk, 0., 31.) == policy.output(value, direction, 31.)
  assert policy.output_context(value, demand, jerk, 0., 20.) == policy.output(value, demand, 20.)


@pytest.mark.parametrize('alpha', (False, True))
def test_hda2_softens_shallow_highway_friction_in_both_longitudinal_modes(alpha):
  cp = topology_params('hdaii')
  cp.openpilotLongitudinalControl, cp.pcmCruise = alpha, not alpha
  policy = LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), .01).starpilot_extension.policy
  baseline = shaping.get_palisade_friction_threshold(30., .4, 1.1)
  assert policy.friction(.4, 1.1, 30.)[0] == pytest.approx(baseline + .16)
  for speed, setpoint in ((4., .4), (30., 1.0), (24., -.4)):
    assert policy.friction(setpoint, 1.1, speed)[0] == shaping.get_palisade_friction_threshold(speed, setpoint, 1.1)

  policy.previous_pressed = True
  assert policy.friction(.4, 1.1, 30.)[0] == baseline
  hda1_cp = topology_params('hdai')
  hda1 = LatControlTorque(hda1_cp.as_reader(), interfaces[hda1_cp.carFingerprint](hda1_cp), .01).starpilot_extension.policy
  assert hda1.friction(.4, 1.1, 30.)[0] == baseline


@pytest.mark.parametrize('topology,alpha,expected', [('hdai', False, 1.0), ('hdaii', False, .72), ('hdaii', True, .72)])
def test_exit_feedforward_damps_hda2_and_reseeds_after_driver_override(topology, alpha, expected):
  cp = topology_params(topology)
  cp.openpilotLongitudinalControl, cp.pcmCruise = alpha, not alpha
  controller = LatControlTorque(cp.as_reader(), interfaces[cp.carFingerprint](cp), .01)
  policy = controller.starpilot_extension.policy
  vm = VehicleModel(cp)
  parameters = SimpleNamespace(angleOffsetDeg=0., roll=0.)
  state = SimpleNamespace(vEgo=29., steeringAngleDeg=0., steeringPressed=False)

  def tick(target, active=True):
    controller.update(active, state, vm, parameters, False, target / state.vEgo**2, False, .22)
    original = shaping.get_palisade_ff_scale(.5, 0., state.vEgo) * shaping.get_palisade_center_taper_scale(.5, state.vEgo)
    return policy.feedforward(1.0, .5, 0., state.vEgo) / original

  assert tick(0.) == pytest.approx(1.)
  assert tick(.8) == pytest.approx(expected)
  for _ in range(200):
    steady = tick(.8)
  assert steady == pytest.approx(1., abs=.0001)
  state.steeringPressed = True
  assert tick(-.8) == pytest.approx(1.)
  state.steeringPressed = False
  assert tick(.8) == pytest.approx(1.)
  tick(.8, active=False)
  assert tick(-.8) == pytest.approx(1.)
  state.vEgo = 14.0
  assert tick(.8) == pytest.approx(1.)
