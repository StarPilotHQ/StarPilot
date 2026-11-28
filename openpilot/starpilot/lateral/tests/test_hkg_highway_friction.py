import math
from types import SimpleNamespace
import unittest
from unittest import mock

from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.lateral import get_friction
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.constants import CV
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib import latcontrol_torque
from openpilot.starpilot.lateral.controller_selection import ControllerMode


def controller_for(vehicle=CAR.KIA_EV6, mode=None, **changes):
  cp = interfaces[vehicle].get_non_essential_params(vehicle)
  ci = interfaces[vehicle](cp)
  for key, value in changes.items():
    setattr(cp, key, value)
  return cp, LatControlTorque(cp.as_reader(), ci, .01, controller_mode=mode)


class HKGHighwayFrictionTests(unittest.TestCase):
  def test_registry_and_wrong_brand_flag_collision(self):
    admitted = 0
    for vehicle in CAR:
      cp = interfaces[vehicle].get_non_essential_params(vehicle)
      if cp.lateralTuning.which() != 'torque':
        continue
      _, controller = controller_for(vehicle)
      expected = bool(cp.flags & HyundaiFlags.CANFD and not cp.flags & HyundaiFlags.CANFD_ANGLE_STEERING and
                      vehicle != CAR.HYUNDAI_IONIQ_6 and
                      cp.steerControlType == structs.CarParams.SteerControlType.torque and
                      not (cp.passive or cp.dashcamOnly or cp.notCar))
      self.assertEqual(controller.hkg_canfd_torque, expected, str(vehicle))
      admitted += expected
    self.assertGreater(admitted, 20)
    for changes in (
      {'brand': 'toyota'}, {'flags': 0}, {'passive': True}, {'dashcamOnly': True}, {'notCar': True},
      {'steerControlType': structs.CarParams.SteerControlType.angle},
      {'flags': int(HyundaiFlags.CANFD | HyundaiFlags.CANFD_ANGLE_STEERING)},
    ):
      with self.subTest(changes=changes):
        _, controller = controller_for(**changes)
        self.assertFalse(controller.hkg_canfd_torque)
        cs = SimpleNamespace(vEgo=75 * CV.MPH_TO_MS, steeringPressed=False)
        self.assertEqual(controller.friction(.1, 0., .2, cs, .1), get_friction(.1, 0., .2, controller.torque_params))

  def test_threshold_floor_soft_deadband_and_existing_larger_threshold(self):
    _, controller = controller_for()
    for mph, threshold in ((0, .39), (50, .39), (60, .39 + (.65 - .39) * 2 / 3),
                           (65, .65), (75, .78), (100, .78)):
      with self.subTest(mph=mph):
        cs = SimpleNamespace(vEgo=mph * CV.MPH_TO_MS, steeringPressed=False)
        with mock.patch.object(latcontrol_torque, 'get_friction', wraps=get_friction) as friction:
          controller.friction(.1, .01, .39, cs, 0.)
        error, deadzone, actual_threshold, params = friction.call_args.args
        weight = min(max((mph - 50) / 15, 0.), 1.)
        self.assertAlmostEqual(error, .1 - .04 * weight)
        self.assertEqual(deadzone, .01)
        self.assertAlmostEqual(actual_threshold, threshold)
        self.assertIs(params, controller.torque_params)
    cs = SimpleNamespace(vEgo=75 * CV.MPH_TO_MS, steeringPressed=False)
    with mock.patch.object(latcontrol_torque, 'get_friction', wraps=get_friction) as friction:
      controller.friction(.1, 0., .9, cs, 0.)
    self.assertEqual(friction.call_args.args[2], .9)

  def test_curve_relief_driver_handoff_zero_and_peak_compensation(self):
    _, controller = controller_for()
    for mph, setpoint, pressed in ((50, 0., False), (75, .65, False), (75, -.65, False),
                                   (75, 2., False), (75, 0., True)):
      with self.subTest(mph=mph, setpoint=setpoint, pressed=pressed):
        cs = SimpleNamespace(vEgo=mph * CV.MPH_TO_MS, steeringPressed=pressed)
        for error in (-1., -.1, 0., .1, 1.):
          self.assertEqual(controller.friction(error, 0., .39, cs, setpoint),
                           get_friction(error, 0., .39, controller.torque_params))
    cs = SimpleNamespace(vEgo=75 * CV.MPH_TO_MS, steeringPressed=False)
    self.assertAlmostEqual(controller.friction(0., 0., .39, cs, 0.), 0.)
    self.assertAlmostEqual(controller.friction(.02, 0., .39, cs, 0.), 0.)
    for sign in (-1, 1):
      self.assertEqual(controller.friction(sign * 2., 0., .39, cs, 0.),
                       get_friction(sign * 2., 0., .39, controller.torque_params))

  def test_kona_se_uses_highway_friction_with_ordinary_torque_controller(self):
    cp, controller = controller_for(CAR.HYUNDAI_KONA_EV_2023_NON_SCC, dashcamOnly=False)
    self.assertEqual(controller.controller_mode, ControllerMode.STANDARD)
    self.assertFalse(cp.flags & HyundaiFlags.CANFD)
    cs = SimpleNamespace(vEgo=65 * CV.MPH_TO_MS, steeringPressed=False)
    self.assertLess(abs(controller.friction(.1, 0., .2, cs, .1)),
                    abs(get_friction(.1, 0., .2, controller.torque_params)))
    cs.vEgo = 40 * CV.MPH_TO_MS
    self.assertEqual(controller.friction(.1, 0., .2, cs, .1), get_friction(.1, 0., .2, controller.torque_params))

  def test_symmetry_continuity_and_friction_bound(self):
    _, controller = controller_for()
    cap = controller.torque_params.latAccelFactor * controller.torque_params.friction
    epsilon = 1e-7
    for mph in (30., 50., 60., 65., 75., 100.):
      cs = SimpleNamespace(vEgo=mph * CV.MPH_TO_MS, steeringPressed=False)
      for accel in (0., .25, .45, .65, 1.):
        for error in (-2., -.1, -.04, 0., .04, .1, 2.):
          value = controller.friction(error, 0., .39, cs, accel)
          negative = controller.friction(-error, 0., .39, cs, -accel)
          self.assertTrue(math.isfinite(value))
          self.assertAlmostEqual(value, -negative)
          self.assertLessEqual(abs(value), cap)
          self.assertLessEqual(abs(value), abs(get_friction(error, 0., .39, controller.torque_params)) + 1e-15)
          left = controller.friction(error - epsilon, 0., .39, cs, accel - epsilon)
          right = controller.friction(error + epsilon, 0., .39, cs, accel + epsilon)
          self.assertLess(abs(left - right), 1e-5)
        left = controller.friction(.1, 0., .39, SimpleNamespace(vEgo=cs.vEgo - epsilon, steeringPressed=False), accel)
        right = controller.friction(.1, 0., .39, SimpleNamespace(vEgo=cs.vEgo + epsilon, steeringPressed=False), accel)
        self.assertLess(abs(left - right), 1e-5)

  def test_standard_and_custom_paths_only_change_friction(self):
    for vehicle in (CAR.KIA_EV6, CAR.HYUNDAI_IONIQ_5, CAR.HYUNDAI_IONIQ_6, CAR.KIA_CARNIVAL_HEV_4TH_GEN,
                    CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN, CAR.HYUNDAI_TUCSON_4TH_GEN):
      for mode in ControllerMode:
        for speed, accel, quiet in ((20., .1, False), (32., .1, True), (32., .8, False)):
          with self.subTest(vehicle=str(vehicle), mode=mode, speed=speed, accel=accel):
            cp, controller = controller_for(vehicle, mode)
            _, baseline = controller_for(vehicle, mode)
            baseline.hkg_canfd_torque = False
            vm = VehicleModel(cp)
            cs = structs.CarState.new_message(vEgo=speed, steeringAngleDeg=0., steeringPressed=False)
            cs.gearShifter = structs.CarState.GearShifter.drive
            params = SimpleNamespace(angleOffsetDeg=0., roll=0.)
            traces = []
            for candidate in (controller, baseline):
              values = []
              for _ in range(100):
                candidate.update(False, cs, vm, params, False, accel / speed ** 2, False, .3)
              for _ in range(100):
                output, _, state = candidate.update(True, cs, vm, params, False, accel / speed ** 2, False, .3)
                values.append((output, state.desiredLateralAccel, state.desiredLateralJerk, state.p, state.i, state.f))
              traces.append(values)
            self.assertEqual(controller.torque_params.to_dict(), baseline.torque_params.to_dict())
            self.assertEqual((controller.pid.pos_limit, controller.pid.neg_limit, controller.steer_max),
                             (baseline.pid.pos_limit, baseline.pid.neg_limit, baseline.steer_max))
            for changed, original in zip(*traces, strict=True):
              self.assertEqual(changed[1:5], original[1:5])
            if quiet and vehicle != CAR.HYUNDAI_IONIQ_6:
              self.assertLess(abs(traces[0][-1][5]), abs(traces[1][-1][5]))
              self.assertLess(abs(traces[0][-1][0]), abs(traces[1][-1][0]))
            else:
              self.assertEqual(traces[0], traces[1])


  def test_ioniq6_preserves_original_threshold_and_error(self):
    for mode in ControllerMode:
      _, controller = controller_for(CAR.HYUNDAI_IONIQ_6, mode)
      self.assertFalse(controller.hkg_canfd_torque)
      for mph in (0., 30., 50., 60., 65., 75., 100.):
        for setpoint in (-1., -.65, -.25, 0., .25, .65, 1.):
          for pressed in (False, True):
            cs = SimpleNamespace(vEgo=mph * CV.MPH_TO_MS, steeringPressed=pressed)
            for error in (-1., -.1, -.04, -.02, 0., .02, .04, .1, 1.):
              for threshold in (.2, .39, .9):
                with self.subTest(mode=mode, mph=mph, setpoint=setpoint, pressed=pressed,
                                  error=error, threshold=threshold):
                  with mock.patch.object(latcontrol_torque, 'get_friction', wraps=get_friction) as friction:
                    value = controller.friction(error, .01, threshold, cs, setpoint)
                  self.assertEqual(friction.call_args.args, (error, .01, threshold, controller.torque_params))
                  self.assertEqual(value, get_friction(error, .01, threshold, controller.torque_params))


def test_tucson_softens_shallow_curve_feedback_in_both_cruise_modes():
  for alpha_long in (False, True):
    _, controller = controller_for(CAR.HYUNDAI_TUCSON_4TH_GEN, ControllerMode.STARPILOT,
                                   openpilotLongitudinalControl=alpha_long, pcmCruise=not alpha_long)
    policy = controller.starpilot_extension.policy
    for direction in (-1., 1.):
      assert math.isclose(policy.pid_error_context(direction, direction * .5, 13.), direction * .55)
      for speed, demand in ((4., .5), (31., .5), (13., 1.15)):
        assert policy.pid_error_context(direction, direction * demand, speed) == direction
    for demand, speed, base_scale in ((.5, 19., .8875), (.9, 13., .925)):
      assert math.isclose(policy.pid_error_context(1., demand, speed), base_scale)
    policy.jerk_filter.x = -1.0
    assert math.isclose(policy.pid_error_context(1., .5, 13.), .85)
    policy.jerk_filter.x = 0.0
    for field in ('steering_pressed', 'previous_pressed'):
      setattr(policy, field, True)
      assert policy.pid_error_context(1., .5, 13.) == 1.
      setattr(policy, field, False)

  _, other = controller_for(CAR.KIA_EV6, ControllerMode.STARPILOT)
  assert other.starpilot_extension.policy.pid_error_context(1., .5, 13.) == 1.
  _, standard = controller_for(CAR.HYUNDAI_TUCSON_4TH_GEN, ControllerMode.STANDARD)
  assert standard.starpilot_extension is None


def test_tucson_highway_feedback_damps_large_errors_without_touching_steady_tracking():
  _, controller = controller_for(CAR.HYUNDAI_TUCSON_4TH_GEN, ControllerMode.STARPILOT)
  policy = controller.starpilot_extension.policy
  for side in (-1., 1.):
    assert math.isclose(policy.pid_error_context(side * .5, side * .8, 25.), side * .325)
    assert policy.pid_error_context(side * .1, side * .8, 25.) == side * .1
    assert policy.pid_error_context(side * .5, side * 1.7, 25.) == side * .5
    assert policy.pid_error_context(side * .5, side * .8, 31.) == side * .5
    policy.jerk_filter.x = 1.5
    assert policy.pid_error_context(side * .5, side * .8, 25.) == side * .5
    policy.jerk_filter.x = 0.
  for field in ('steering_pressed', 'previous_pressed'):
    setattr(policy, field, True)
    assert policy.pid_error_context(.5, .8, 25.) == .5
    setattr(policy, field, False)


def test_tucson_damps_large_shallow_turn_corrections_at_city_speeds():
  for alpha_long in (False, True):
    _, controller = controller_for(CAR.HYUNDAI_TUCSON_4TH_GEN, ControllerMode.STARPILOT,
                                   openpilotLongitudinalControl=alpha_long, pcmCruise=not alpha_long)
    policy = controller.starpilot_extension.policy
    for side in (-1., 1.):
      request = side * .55
      speed = 12.
      nominal = policy.output_context(side, request, 0., request, speed)
      near = policy.output_context(side, request, 0., request - side * .1, speed)
      moderate = policy.output_context(side, request, 0., request - side * .38, speed)
      far = policy.output_context(side, request, 0., request - side * .6, speed)
      assert near == nominal
      assert math.isclose(moderate, nominal * .825)
      assert math.isclose(far, nominal * .65)
      for unmodified_request, unmodified_speed in ((request, 5.), (request, 21.), (side * 1.25, speed)):
        assert policy.output_context(side, unmodified_request, 0., unmodified_request - side, unmodified_speed) == \
          policy.output_context(side, unmodified_request, 0., unmodified_request, unmodified_speed)
