import math
from types import SimpleNamespace
import unittest

import numpy as np

from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_bolt_cc import params
from opendbc.car.gm.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.lateral.bolt_policy import BOLT_GENERATIONS
from openpilot.starpilot.lateral.controller_selection import ControllerMode, default_selection, replace_mode, selection_from_bytes


class TestBoltTorquePolicy(unittest.TestCase):
  def controller(self, identity, mode=None):
    cp = params(identity, alpha=True).as_reader()
    return cp, LatControlTorque(cp, CarInterface(cp), 0.01, controller_mode=mode)

  def state(self):
    return SimpleNamespace(vEgo=20.0, steeringAngleDeg=0.0, steeringPressed=False, steeringRateDeg=0.0, standstill=False)

  def test_exact_default_selection_and_saved_standard(self):
    for identity in BOLT_GENERATIONS:
      cp, controller = self.controller(identity)
      self.assertEqual(default_selection(cp).mode, ControllerMode.STARPILOT)
      self.assertIsNotNone(selected_policy(controller))
      choice = selection_from_bytes(cp, replace_mode(None, cp, ControllerMode.STANDARD))
      self.assertEqual(choice.mode, ControllerMode.STANDARD)
      standard = LatControlTorque(cp, CarInterface(cp), 0.01, controller_mode=choice.mode)
      self.assertIsNone(selected_policy(standard))
      self.assertEqual(standard.pid._k_i[1], [0.15])
      self.assertAlmostEqual(standard.torque_from_lateral_accel(1.0, standard.torque_params), 1.0 / cp.lateralTuning.torque.latAccelFactor)
    cp, controller = self.controller(CAR.CHEVROLET_BOLT_EUV)
    self.assertEqual(default_selection(cp).mode, ControllerMode.STANDARD)
    self.assertIsNone(selected_policy(controller))
    valid, _ = self.controller(CAR.CHEVROLET_BOLT_CC_2017)
    for field, value in (('brand', 'toyota'), ('notCar', True), ('dashcamOnly', True), ('passive', True)):
      bad = valid.as_builder()
      setattr(bad, field, value)
      self.assertIsNone(default_selection(bad).policy)

  def test_generation_gains_and_nonlinear_limits_survive_parameter_updates(self):
    coefficients = {
      2017: ((2.15, 1.0, 0.129), (2.15, 1.0, 0.145)),
      2018: ((1.8, 1.1, 0.27), (2.0, 1.0, 0.205)),
      2022: ((2.6531724862969748, 1.1, 0.1919764879840985), (2.7031724862969748, 1.0, 0.1469764879840985)),
    }
    for identity, generation in BOLT_GENERATIONS.items():
      cp, controller = self.controller(identity)
      policy = selected_policy(controller)
      self.assertEqual(policy.ff_positive, float(np.float32(1.03)))
      self.assertEqual(policy.ff_negative, float(np.float32(float(np.float32(1.07)) * (0.9 if generation == 2017 else 1.07))))
      multiplier = float(np.float32(float(np.float32(0.93)) * (0.9 if generation == 2017 else 0.93)))
      self.assertEqual(controller.pid._k_i[1], [0.35 * multiplier])
      values = np.arange(-5.0, 5.0, 0.01)
      torques = []
      for value in values:
        a, b, c = coefficients[generation][0 if value >= 0 else 1]
        x = a * value
        torques.append(float(np.sign(x) * (1 / (1 + math.exp(-abs(x))) - 0.5) * b + value * c))
      expected_limits = (np.interp(1.0, torques, values), np.interp(-1.0, torques, values))
      for value in (-2.0, -0.05, 0.0, 0.05, 2.0):
        self.assertAlmostEqual(controller.torque_from_lateral_accel(value, controller.torque_params), np.interp(value, values, torques))
      self.assertEqual((controller.pid.pos_limit, controller.pid.neg_limit), expected_limits)
      controller.update_torque_parameters(1.1 * cp.lateralTuning.torque.latAccelFactor, 0.01, 0.05)
      self.assertEqual((controller.pid.pos_limit, controller.pid.neg_limit), expected_limits)
      self.assertAlmostEqual(controller.torque_params.friction, 0.05)

  def test_reset_inactive_priming_driver_release_and_saturation(self):
    for identity in BOLT_GENERATIONS:
      cp, controller = self.controller(identity)
      policy = selected_policy(controller)
      vm, cs = VehicleModel(cp), self.state()
      live = SimpleNamespace(angleOffsetDeg=0.0, roll=0.0)
      controller.pid.i = 0.2
      controller.sat_time = 0.3
      policy.previous_output = 0.1
      policy.curvature_buffer[-1] = 0.001
      controller.reset()
      self.assertEqual(controller.sat_time, 0.0)
      self.assertEqual(controller.pid.i, 0.2)
      self.assertEqual(policy.previous_output, 0.1)
      self.assertEqual(policy.curvature_buffer[-1], 0.001)
      output = controller.update(False, cs, vm, live, False, 0.002, False, 0.2)
      self.assertEqual(output[0], 0.0)
      self.assertEqual(controller.pid.i, 0.0)
      self.assertEqual(policy.previous_output, 0.0)
      self.assertEqual(policy.curvature_buffer[-1], 0.002)
      self.assertEqual(policy.jerk_filter.x, 0.0)
      controller.pid.i = 0.2
      policy.previous_pressed = True
      controller.update(True, cs, vm, live, True, 0.002, False, 0.2)
      self.assertAlmostEqual(controller.pid.i, 0.16)
      cs.vEgo = 35.0
      for _ in range(80):
        _, _, logged = controller.update(True, cs, vm, live, False, 1.0, False, 0.2)
      self.assertTrue(logged.saturated)
      _, _, logged = controller.update(True, cs, vm, live, True, 1.0, False, 0.2)
      self.assertFalse(logged.saturated)

  def test_default_kp_survives_parameter_updates_reset_and_inactive_dispatch(self):
    for identity in BOLT_GENERATIONS:
      cp, controller = self.controller(identity)
      self.assertEqual(controller.pid._k_p, [[0.0], [0.6]])
      self.assertEqual(controller.pid._k_p[1][0], 0.6)
      controller.update_torque_parameters(3.1, 0.01, 0.09)
      controller.reset()
      state = self.state()
      controller.update(False, state, VehicleModel(cp), SimpleNamespace(angleOffsetDeg=0.0, roll=0.0), False, 0.001, False, 0.2)
      self.assertEqual(controller.pid._k_p, [[0.0], [0.6]])
      standard = LatControlTorque(cp, CarInterface(cp), 0.01, controller_mode=ControllerMode.STANDARD)
      self.assertEqual(standard.pid._k_p[0], [1, 1.5, 2.0, 3.0, 5, 7.5, 10, 15, 30])
      self.assertEqual(standard.pid._k_p[1][-1], 0.8)
