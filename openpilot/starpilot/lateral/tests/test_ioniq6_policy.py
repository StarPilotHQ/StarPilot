"""Frozen 678af783 Ioniq 6 traces and committed-parent non-Ioniq traces."""

import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR as HYUNDAI
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.vehicle_model import VehicleModel
from opendbc.car.lateral import get_friction
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib import latcontrol_torque
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.lateral import ioniq6_policy as tune
from openpilot.starpilot.lateral.torque_shaping import INTERP_SPEEDS

TESTDATA = Path(__file__).parent / 'testdata'


def _legacy_friction(controller, error, deadzone, threshold, _cs, _setpoint):
  return latcontrol_torque.get_friction(error, deadzone, threshold, controller.torque_params)


def _rows(car, sequence, *, profile='2023', matched_frozen_cp=False, passive=False, direct_original_controller=False, turn_assist=True,
          roll=None, controller_mode=None):
  cp = interfaces[car].get_non_essential_params(car)
  cp.passive = passive
  if matched_frozen_cp:
    for name, value in (('mass', 2084.0), ('wheelbase', 2.97), ('centerToFront', 1.188),
                        ('steerRatio', 14.26), ('tireStiffnessFactor', 0.65)):
      setattr(cp, name, value)
    cp.lateralTuning.torque.latAccelFactor = 3.0
    cp.lateralTuning.torque.latAccelOffset = 0.0
    cp.lateralTuning.torque.friction = 0.09
    cp.lateralTuning.torque.steeringAngleDeadzoneDeg = 0.0
    if profile == '2025':
      versions = cp.init('carFw', 2)
      versions[0].fwVersion = b'IONIQ6-230915'
      versions[1].fwVersion = b'IONIQ6-240206'
  ci = interfaces[car](cp)
  controller = LatControlTorque(cp.as_reader(), ci, DT_CTRL, turn_assist=turn_assist, controller_mode=controller_mode)
  if direct_original_controller:
    # The older frozen vectors called the controller directly, before the
    # original controlsd drive loop replaced the PID gain curve each tick.
    controller.pid._k_p = [INTERP_SPEEDS, tune.KP_INTERP]
  vm = VehicleModel(cp)
  cs = structs.CarState.new_message()
  cs.gearShifter = structs.CarState.GearShifter.drive
  params = SimpleNamespace(angleOffsetDeg=0.0, roll=(0.03 if matched_frozen_cp else 0.02) if roll is None else roll)
  rows = []
  for active, speed, angle, curvature, pressed in sequence:
    cs.vEgo = speed
    cs.steeringAngleDeg = angle
    cs.steeringPressed = pressed
    output, _, state = controller.update(active, cs, vm, params, False, curvature, False, 0.1)
    rows.append([output, state.desiredLateralAccel, state.desiredLateralJerk, state.p, state.i, state.f])
  return controller, rows


class Ioniq6PolicyTests(unittest.TestCase):
  def test_ioniq_drive_loop_uses_original_flat_proportional_gain(self):
    cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    cp.lateralTuning.torque.latAccelFactor = 3.0
    cp.lateralTuning.torque.friction = 0.09
    controller = LatControlTorque(cp.as_reader(), interfaces[HYUNDAI.HYUNDAI_IONIQ_6](cp), DT_CTRL)
    self.assertEqual(controller.pid._k_p, [[0.0], [0.6]])
    vm = VehicleModel(cp)
    cs = structs.CarState.new_message()
    params = SimpleNamespace(angleOffsetDeg=0.0, roll=0.0)
    sequence = ((False, 2.0, 0.0002, False),
                (True, 2.0, 0.0002, False),
                (True, 8.0, -0.0003, False),
                (True, 8.0, -0.0003, True),
                (True, 20.0, 0.0004, True),
                (True, 20.0, 0.0004, False),
                (False, 0.0, 0.0, False),
                (True, 2.0, -0.0002, False))
    for active, speed, curvature, pressed in sequence:
      cs.vEgo = speed
      cs.steeringAngleDeg = 0.0
      cs.steeringPressed = pressed
      output, _, state = controller.update(active, cs, vm, params, False, curvature, False, 0.1)
      self.assertTrue(math.isfinite(output))
      self.assertEqual(state.active, active)
      if active:
        self.assertAlmostEqual(state.p, 0.6 * state.error, places=6)
        self.assertAlmostEqual(output, state.output, places=6)
      else:
        self.assertEqual(output, 0.0)
      self.assertEqual(controller.pid._k_p, [[0.0], [0.6]])
    controller.update_torque_parameters(3.2, 0.01, 0.10)
    self.assertEqual(controller.pid._k_p, [[0.0], [0.6]])

  @mock.patch.object(tune, "IONIQ_6_STANDARD_FRICTION_THRESHOLD_INCREASE", 0.0)
  def test_actual_frozen_controller_sequences_and_pure_calibration(self):
    sequence = ([(False, 0.3, 10.0, -0.001, False)] * 2 +
                [(True, 0.3, 10.0, -0.001, False)] * 4 +
                [(True, 2.0, 5.0, -0.001, False)] * 4 +
                [(True, 20.0, 2.0, 0.0003, False)] * 4 +
                [(True, 20.0, 2.0, -0.0003, True)] * 2 +
                [(False, 0.0, 8.0, -0.001, False)] * 2 +
                [(True, 3.0, 8.0, -0.001, False)] * 4)
    samples = ((0.3, .1, .2, 8., 3., -.2),
               (12., -.5, -1.3, -6., -10., .2),
               (27., .02, .8, 1., 0., -.1))
    helpers = []
    for speed, accel, jerk, angle, actual, torque in samples:
      helpers.append([
        tune.get_ioniq_6_ff_scale(accel, jerk, speed),
        tune.get_ioniq_6_directional_taper_scale(accel, jerk, speed),
        tune.get_ioniq_6_friction_threshold(speed, accel, jerk),
        tune.get_ioniq_6_center_taper_scale(accel, speed),
        tune.get_ioniq_6_low_speed_angle_assist_torque(angle, actual, torque, speed),
        tune.get_ioniq_6_2025_center_output_scale(accel, speed),
      ])
    for profile in ('2023', '2025'):
      with self.subTest(profile=profile):
        frozen = json.loads((TESTDATA / f'ioniq6_frozen_{profile}.json').read_text())
        controller, actual = _rows(HYUNDAI.HYUNDAI_IONIQ_6, sequence, profile=profile,
                                   matched_frozen_cp=True, direct_original_controller=True)
        self.assertIsNotNone(selected_policy(controller))
        self.assertEqual(selected_policy(controller).is_2025, profile == '2025')
        self.assertAlmostEqual(controller.torque_params.latAccelFactor, frozen['factor'], places=6)
        for row, expected in zip(actual, frozen['rows'], strict=True):
          for value, reference in zip(row, expected, strict=True):
            self.assertAlmostEqual(value, reference, places=6)
        for row, expected in zip(helpers, frozen['helpers'], strict=True):
          for value, reference in zip(row, expected, strict=True):
            self.assertAlmostEqual(value, reference, places=6)

  def test_threshold_reaches_controller_without_changing_requests_or_pid(self):
    for speed, curvature in ((20., .0002), (32., .001), (32., .0001)):
      with self.subTest(speed=speed, curvature=curvature):
        sequence = [(False, speed, 0., curvature, False)] * 100 + [(True, speed, 0., curvature, False)] * 100
        with mock.patch.object(latcontrol_torque, 'get_friction', wraps=get_friction) as friction:
          controller, actual = _rows(HYUNDAI.HYUNDAI_IONIQ_6, sequence, roll=0.)
        with mock.patch.object(latcontrol_torque, 'get_friction', wraps=get_friction) as old_friction:
          with mock.patch.object(LatControlTorque, 'friction', _legacy_friction):
            baseline, original = _rows(HYUNDAI.HYUNDAI_IONIQ_6, sequence, roll=0.)
        self.assertEqual(friction.call_args.args[:3], old_friction.call_args.args[:3])
        self.assertEqual(controller.torque_params.to_dict(), baseline.torque_params.to_dict())
        self.assertEqual((controller.pid.pos_limit, controller.pid.neg_limit),
                         (baseline.pid.pos_limit, baseline.pid.neg_limit))
        for row, old in zip(actual, original, strict=True):
          self.assertEqual(row[1:5], old[1:5])
        self.assertEqual(actual, original)

  def test_firmware_gate_and_live_factor_do_not_stack(self):
    cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    self.assertFalse(tune.is_ioniq_6_2025_model(cp))
    single = cp.init('carFw', 1)
    single[0].fwVersion = b'IONIQ6-230915'
    self.assertFalse(tune.is_ioniq_6_2025_model(cp))
    both = cp.init('carFw', 2)
    both[0].fwVersion = b'IONIQ6-230915'
    both[1].fwVersion = b'IONIQ6-240206'
    self.assertTrue(tune.is_ioniq_6_2025_model(cp))
    controller = LatControlTorque(cp.as_reader(), interfaces[HYUNDAI.HYUNDAI_IONIQ_6](cp), DT_CTRL)
    raw = cp.lateralTuning.torque.latAccelFactor
    for _ in range(3):
      controller.update_torque_parameters(raw, 0.02, 0.10)
      self.assertAlmostEqual(controller.torque_params.latAccelFactor, raw * 1.22, places=6)
      self.assertAlmostEqual(controller.torque_params.latAccelOffset, 0.02, places=6)
      self.assertAlmostEqual(controller.torque_params.friction, 0.10, places=6)

  @mock.patch.object(LatControlTorque, 'friction', _legacy_friction)
  def test_other_cars_without_assist_match_committed_parent_trace(self):
    sequence = ([(False, 2., 5., -.0005, False)] * 2 +
                [(True, 2., 5., -.0005, False)] * 3 +
                [(True, 15., -1., .0004, False)] * 3 +
                [(True, 25., -2., .0001, True)] * 2 +
                [(False, 0., 5., -.001, False)] * 2)
    parent = json.loads((TESTDATA / 'non_ioniq_parent.json').read_text())
    for car in (TOYOTA.TOYOTA_COROLLA_TSS2, HYUNDAI.HYUNDAI_IONIQ_5):
      with self.subTest(car=str(car)):
        controller, actual = _rows(car, sequence, passive=car == TOYOTA.TOYOTA_COROLLA_TSS2, turn_assist=False,
                                   controller_mode='standard')
        self.assertIsNone(selected_policy(controller))
        for row, expected in zip(actual, parent[str(car)], strict=True):
          for value, reference in zip(row, expected, strict=True):
            self.assertAlmostEqual(value, reference, places=7)


class Ioniq6HighwayFrictionTests(unittest.TestCase):
  def controller(self, *, firmware_2025=True, long=False):
    cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    cp.openpilotLongitudinalControl = long
    cp.pcmCruise = not long
    if firmware_2025:
      fw = cp.init('carFw', 2)
      fw[0].fwVersion = b'IONIQ6-230915'
      fw[1].fwVersion = b'IONIQ6-240206'
    controller = LatControlTorque(cp.as_reader(), interfaces[HYUNDAI.HYUNDAI_IONIQ_6](cp), DT_CTRL)
    return controller, VehicleModel(cp)

  def trace(self, *, speed=29.0, firmware_2025=True, long=False, tuning=True, pressed=False, center=-3.0):
    controller, vm = self.controller(firmware_2025=firmware_2025, long=long)
    params = SimpleNamespace(angleOffsetDeg=0.0, roll=0.0)
    cs = structs.CarState.new_message(vEgo=speed, steeringPressed=pressed,
                                      gearShifter=structs.CarState.GearShifter.drive)
    rows = []
    with mock.patch.object(tune, 'IONIQ_6_2025_HIGHWAY_FRICTION_THRESHOLD_INCREASE', .50 if tuning else 0.0):
      for index in range(400):
        cs.steeringAngleDeg = center + .5 * math.sin(index * DT_CTRL * 2 * math.pi)
        output, _, state = controller.update(index != 0, cs, vm, params, True, .001, False, .322)
        rows.append((output, state.f, state.desiredLateralAccel, state.desiredLateralJerk, state.p, state.i))
        self.assertLessEqual(abs(output), 1.0)
        self.assertTrue(all(math.isfinite(x) for x in rows[-1]))
    return controller, rows

  def test_highway_feedback_is_softer_without_changing_path_or_pid(self):
    _, tuned = self.trace()
    _, baseline = self.trace(tuning=False)
    tuned_f = [row[1] for row in tuned[100:]]
    original_f = [row[1] for row in baseline[100:]]
    self.assertLess(max(tuned_f) - min(tuned_f), .9 * (max(original_f) - min(original_f)))
    for row, old in zip(tuned, baseline, strict=True):
      self.assertEqual(row[2:], old[2:])

  def test_low_speed_and_older_firmware_are_unchanged(self):
    for kwargs in ({'speed': 20.0}, {'speed': 5.0}, {'firmware_2025': False}):
      with self.subTest(**kwargs):
        self.assertEqual(self.trace(**kwargs)[1], self.trace(tuning=False, **kwargs)[1])

  def test_stock_and_alpha_long_use_identical_steering(self):
    self.assertEqual(self.trace(long=False)[1], self.trace(long=True)[1])

  def test_driver_override_is_unchanged_and_inactive_resets(self):
    controller, rows = self.trace(pressed=True)
    self.assertEqual(rows, self.trace(pressed=True, tuning=False)[1])
    vm = VehicleModel(interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6))
    cs = structs.CarState.new_message(vEgo=29.0, steeringAngleDeg=5.0)
    output, _, state = controller.update(False, cs, vm, SimpleNamespace(angleOffsetDeg=0.0, roll=0.0),
                                         False, .001, False, .322)
    self.assertEqual(output, 0.0)
    self.assertFalse(state.active)
    self.assertEqual(selected_policy(controller).measurement_rate_filter.x, 0.0)

  def test_constant_turn_strength_live_parameters_and_limits_are_preserved(self):
    results = []
    for tuning in (False, True):
      controller, vm = self.controller()
      controller.update_torque_parameters(3.2, .01, .08)
      self.assertAlmostEqual(controller.torque_params.latAccelFactor, 3.2 * 1.22, places=6)
      self.assertAlmostEqual(controller.torque_params.friction, .08, places=6)
      self.assertEqual(controller.pid.pos_limit, controller.lateral_accel_from_torque(1.0, controller.torque_params))
      cs = structs.CarState.new_message(vEgo=29.0, steeringAngleDeg=-3.0,
                                        gearShifter=structs.CarState.GearShifter.drive)
      curve = -vm.calc_curvature(math.radians(cs.steeringAngleDeg), cs.vEgo, 0.0)
      with mock.patch.object(tune, 'IONIQ_6_2025_HIGHWAY_FRICTION_THRESHOLD_INCREASE', .50 if tuning else 0.0):
        for index in range(400):
          output, _, _ = controller.update(index != 0, cs, vm, SimpleNamespace(angleOffsetDeg=0.0, roll=0.0),
                                           True, curve, False, .322)
        results.append(output)
    self.assertAlmostEqual(*results, places=12)

  def test_large_tracking_error_keeps_full_correction(self):
    _, tuned = self.trace(center=20.0)
    _, original = self.trace(center=20.0, tuning=False)
    self.assertEqual(tuned[100:], original[100:])


class Ioniq6StandardFrictionTests(Ioniq6HighwayFrictionTests):
  def controller(self, *, firmware_2025=False, long=False):
    return super().controller(firmware_2025=firmware_2025, long=long)

  def trace(self, *, speed=32.0, firmware_2025=False, long=False, tuning=True, pressed=False, center=None):
    if center is None:
      _, vm = self.controller(firmware_2025=firmware_2025, long=long)
      center = math.degrees(vm.get_steer_from_curvature(-.001, speed, 0.0))
    with mock.patch.object(tune, 'IONIQ_6_STANDARD_FRICTION_THRESHOLD_INCREASE', .50 if tuning else 0.0):
      return super().trace(speed=speed, firmware_2025=firmware_2025, long=long, tuning=False, pressed=pressed, center=center)

  def test_low_speed_and_older_firmware_are_unchanged(self):
    for kwargs in ({'speed': 3.0}, {'speed': .3}, {'firmware_2025': True}):
      with self.subTest(**kwargs):
        self.assertEqual(self.trace(**kwargs)[1], self.trace(tuning=False, **kwargs)[1])

  def test_city_feedback_is_softer_without_changing_path_or_pid(self):
    _, tuned = self.trace(speed=10.0)
    _, baseline = self.trace(speed=10.0, tuning=False)
    self.assertLess(max(row[1] for row in tuned[100:]) - min(row[1] for row in tuned[100:]),
                    max(row[1] for row in baseline[100:]) - min(row[1] for row in baseline[100:]))
    for row, old in zip(tuned, baseline, strict=True):
      self.assertEqual(row[2:], old[2:])

  def test_constant_turn_strength_live_parameters_and_limits_are_preserved(self):
    results = []
    for tuning in (False, True):
      controller, vm = self.controller()
      controller.update_torque_parameters(3.2, .01, .08)
      self.assertAlmostEqual(controller.torque_params.latAccelFactor, 3.2 * 1.22, places=6)
      self.assertAlmostEqual(controller.torque_params.friction, .08, places=6)
      self.assertEqual(controller.pid.pos_limit, controller.lateral_accel_from_torque(1.0, controller.torque_params))
      cs = structs.CarState.new_message(vEgo=32.0, steeringAngleDeg=-3.0,
                                        gearShifter=structs.CarState.GearShifter.drive)
      curve = -vm.calc_curvature(math.radians(cs.steeringAngleDeg), cs.vEgo, 0.0)
      with mock.patch.object(tune, 'IONIQ_6_STANDARD_FRICTION_THRESHOLD_INCREASE', .50 if tuning else 0.0):
        for index in range(400):
          output, _, _ = controller.update(index != 0, cs, vm, SimpleNamespace(angleOffsetDeg=0.0, roll=0.0),
                                           True, curve, False, .322)
        results.append(output)
    self.assertAlmostEqual(*results, places=12)
