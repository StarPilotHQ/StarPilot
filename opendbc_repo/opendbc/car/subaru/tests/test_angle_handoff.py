import unittest

from opendbc.car.subaru.tests.test_ascent_angle import setup_controller, step
from opendbc.car.subaru.values import CAR


class TestAngleHandoff(unittest.TestCase):
  def test_override_release_waits_for_low_torque_and_settled_wheel(self):
    for car, high, low in ((CAR.SUBARU_CROSSTREK_2025, 150, 100), (CAR.SUBARU_LEGACY_2025, 200, 150)):
      for sign in (-1, 1):
        with self.subTest(car=car, sign=sign):
          _, controller, command, state = setup_controller(car)
          state.out.steeringRateDeg = 0
          state.out.steeringAngleDeg = 10
          # One sample above the threshold must not hand control back and forth.
          for torque, rate, expected in ((high, 0, 1), (high + 1, 0, 1), (high + 1, 0, 0),
                                         (high, 0, 0), (low, 0, 0), (low - 1, 3, 0),
                                         (0, 2, 0), (0, 2, 1)):
            state.out.steeringTorque = torque * sign
            state.out.steeringRateDeg = rate * sign
            steer, decoded = step(controller, command, state)
            self.assertEqual(steer[2], 0)
            self.assertEqual(decoded['LKAS_Request'], expected)
            if not expected:
              self.assertEqual(decoded['LKAS_Output'], 10)

  def test_engagement_waits_for_manual_turn_to_settle(self):
    for car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_LEGACY_2025):
      with self.subTest(car=car):
        _, controller, command, state = setup_controller(car)
        state.out.steeringTorque = 0
        for rate, expected in ((3, 0), (2.1, 0), (2, 0), (2, 1)):
          state.out.steeringRateDeg = rate
          _, decoded = step(controller, command, state)
          self.assertEqual(decoded['LKAS_Request'], expected)

  def test_unavailable_control_clears_override_without_sending_active_commands(self):
    for car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_LEGACY_2025):
      with self.subTest(car=car):
        _, controller, command, state = setup_controller(car)
        state.out.steeringRateDeg = 0
        state.out.steeringTorque = 300
        step(controller, command, state)
        step(controller, command, state)
        command.latActive = False
        _, decoded = step(controller, command, state)
        self.assertEqual(decoded['LKAS_Request'], 0)
        self.assertFalse(controller.angle_driver_override)
        self.assertFalse(controller.angle_handoff_active)
        state.out.steeringTorque = 0
        command.latActive = True
        _, decoded = step(controller, command, state)
        self.assertEqual(decoded['LKAS_Request'], 1)


class TestIndependentAngleEnvelope(unittest.TestCase):
  def test_dom_aol_envelope_uses_actual_steering_request(self):
    cars = (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_ASCENT_2023,
            CAR.SUBARU_FORESTER_2022, CAR.SUBARU_OUTBACK_2023)
    for car in cars:
      for speed, angle, expected in ((5., 119.5, True), (5., -119.5, True),
                                     (5., 120., False), (5., -120., False),
                                     (5., 200., False), (5., -200., False),
                                     (.4, 10., False), (.5, 10., True)):
        with self.subTest(car=car, speed=speed, angle=angle):
          cp, controller, command, state = setup_controller(car)
          cp.alternativeExperience = 32
          command.enabled = False
          command.actuators.steeringAngleDeg = angle + 5.
          state.out.cruiseState.enabled = False
          state.out.steeringTorque = state.out.steeringRateDeg = 0
          state.out.steeringAngleDeg = angle
          state.out.vEgoRaw = state.out.vEgo = speed
          state.out.standstill = False
          for _ in range(3):
            _, decoded = step(controller, command, state)
          self.assertEqual(bool(decoded['LKAS_Request']), expected)
          if not expected:
            expected_angle = max(-190., min(190., angle)) if car == CAR.SUBARU_CROSSTREK_2025 else angle
            self.assertAlmostEqual(decoded['LKAS_Output'], expected_angle, places=2)

  def test_ordinary_engagement_preserves_transmitted_history_and_other_profiles(self):
    for car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_ASCENT_2023,
                CAR.SUBARU_FORESTER_2022, CAR.SUBARU_OUTBACK_2023):
      for speed, angle in ((5., 200.), (.4, 10.)):
        with self.subTest(car=car, speed=speed, angle=angle):
          _, controller, command, state = setup_controller(car)
          command.actuators.steeringAngleDeg = -1.45
          state.out.vEgoRaw = state.out.vEgo = speed
          state.out.steeringAngleDeg = angle
          state.out.steeringRateDeg = state.out.steeringTorque = 0
          state.out.standstill = False
          for _ in range(3):
            _, decoded = step(controller, command, state)
          self.assertTrue(decoded['LKAS_Request'])
          if car == CAR.SUBARU_CROSSTREK_2025:
            # The EPS bound applies to transmitted commands, not measured-angle admission.
            # Follow the small target without jumping to the measured angle.
            self.assertAlmostEqual(decoded['LKAS_Output'], -1.45)
