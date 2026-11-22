import unittest

from opendbc.car.subaru.tests.test_ascent_angle import setup_controller, step
from opendbc.car.subaru.values import CAR
from opendbc.car import structs


class TestAngleHandoff(unittest.TestCase):
  def test_override_release_waits_for_low_torque_and_settled_wheel(self):
    for car, high, low in ((CAR.SUBARU_CROSSTREK_2025, 150, 100), (CAR.SUBARU_IMPREZA_2025, 150, 100), (CAR.SUBARU_LEGACY_2025, 200, 150)):
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
    for car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025, CAR.SUBARU_LEGACY_2025):
      with self.subTest(car=car):
        _, controller, command, state = setup_controller(car)
        state.out.steeringTorque = 0
        for rate, expected in ((3, 0), (2.1, 0), (2, 0), (2, 1)):
          state.out.steeringRateDeg = rate
          _, decoded = step(controller, command, state)
          self.assertEqual(decoded['LKAS_Request'], expected)

  def test_unavailable_control_clears_override_without_sending_active_commands(self):
    for car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025, CAR.SUBARU_LEGACY_2025):
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
    cars = (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_ASCENT_2023,
            CAR.SUBARU_FORESTER_2022, CAR.SUBARU_OUTBACK_2023)
    for car in cars:
      cases = ((5., 199.5, True), (5., -199.5, True), (5., 200., True), (5., -200., True),
               (5., 201., False), (5., -201., False), (.4, 10., False), (.5, 10., True)) \
        if car == CAR.SUBARU_LEGACY_2025 else \
        ((5., 119.5, True), (5., -119.5, True), (5., 120., False), (5., -120., False),
         (5., 200., False), (5., -200., False), (.4, 10., False), (.5, 10., True))
      for speed, angle, expected in cases:
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
            expected_angle = max(-190., min(190., angle)) if car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025) else angle
            self.assertAlmostEqual(decoded['LKAS_Output'], expected_angle, places=2)

  def test_legacy_2025_mads_hysteresis_and_eps_stall_guard(self):
    cp, controller, command, state = setup_controller(CAR.SUBARU_LEGACY_2025)
    cp.alternativeExperience = 32
    command.enabled = False
    state.out.cruiseState.enabled = False
    state.out.vEgoRaw = state.out.vEgo = 5.
    state.out.standstill = False
    state.out.steeringTorque = state.out.steeringRateDeg = 0
    state.out.steeringAngleDeg = 199.
    command.actuators.steeringAngleDeg = 199.
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 1)
    state.out.steeringAngleDeg = 205.
    command.actuators.steeringAngleDeg = 205.
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 1)
    state.out.steeringAngleDeg = 211.
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 0)

    # 8-frame EPS stall guard releases for 1 frame when wheel angle stops tracking target.
    _, controller, command, state = setup_controller(CAR.SUBARU_LEGACY_2025)
    state.out.vEgoRaw = state.out.vEgo = 5.
    state.out.standstill = False
    state.out.steeringAngleDeg = 0.
    command.actuators.steeringAngleDeg = 0.
    _, decoded = step(controller, command, state)
    self.assertEqual(decoded['LKAS_Request'], 1)
    command.actuators.steeringAngleDeg = 40.
    stalled = 0
    for _ in range(20):
      prev_output = decoded['LKAS_Output']
      _, decoded = step(controller, command, state)
      if abs(prev_output - state.out.steeringAngleDeg) > 5.0:
        stalled += 1
        if stalled == 8:
          self.assertEqual(decoded['LKAS_Request'], 0)
          self.assertEqual(decoded['LKAS_Output'], 0.)
          break
      self.assertEqual(decoded['LKAS_Request'], 1)
    else:
      self.fail("EPS stall guard did not trigger after 8 stalled frames")

  def test_legacy_2025_aol_clamps_target_to_hold_max_turn(self):
    for sign in (-1, 1):
      with self.subTest(sign=sign):
        cp, controller, command, state = setup_controller(CAR.SUBARU_LEGACY_2025)
        cp.alternativeExperience = 32
        command.enabled = False
        state.out.cruiseState.enabled = False
        state.out.vEgoRaw = state.out.vEgo = 25.
        state.out.standstill = False
        state.out.steeringTorque = state.out.steeringRateDeg = 0
        state.out.steeringAngleDeg = 199. * sign
        command.latActive = False
        step(controller, command, state)
        command.latActive = True
        command.actuators.steeringAngleDeg = 240. * sign
        for _ in range(10):
          _, decoded = step(controller, command, state)
          state.out.steeringAngleDeg = decoded['LKAS_Output']
        self.assertEqual(decoded['LKAS_Request'], 1)
        self.assertAlmostEqual(decoded['LKAS_Output'], 200. * sign, places=2)

    # Active cruise (ACC engaged) is not capped to the AOL 200 deg ceiling.
    _, controller, command, state = setup_controller(CAR.SUBARU_LEGACY_2025)
    command.enabled = True
    state.out.cruiseState.enabled = True
    state.out.vEgoRaw = state.out.vEgo = 25.
    state.out.standstill = False
    state.out.steeringTorque = state.out.steeringRateDeg = 0
    state.out.steeringAngleDeg = 199.
    command.latActive = False
    step(controller, command, state)
    command.latActive = True
    command.actuators.steeringAngleDeg = 220.
    for _ in range(10):
      _, decoded = step(controller, command, state)
      state.out.steeringAngleDeg = decoded['LKAS_Output']
    self.assertEqual(decoded['LKAS_Request'], 1)
    self.assertGreater(decoded['LKAS_Output'], 200.)

  def test_ordinary_engagement_preserves_transmitted_history_and_other_profiles(self):
    for car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_ASCENT_2023,
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
          if car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025, CAR.SUBARU_LEGACY_2025):
            # The EPS bound applies to transmitted commands, not measured-angle admission.
            # Follow the small target without jumping to the measured angle.
            expected = -1.45 * (1. - (30. / 31.) ** 5)
            self.assertAlmostEqual(decoded['LKAS_Output'], round(expected, 2), places=2)


class TestJacobwAngleContinuation(unittest.TestCase):
  def test_active_aol_crosses_admission_angle_without_losing_request(self):
    for car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025):
      for sign in (-1, 1):
        with self.subTest(car=car, sign=sign):
          cp, controller, command, state = setup_controller(car)
          cp.alternativeExperience = 32
          command.enabled = state.out.cruiseState.enabled = False
          command.actuators.steeringAngleDeg = 170 * sign
          state.out.vEgoRaw = state.out.vEgo = 4.8
          state.out.standstill = False
          state.out.steeringTorque = state.out.steeringRateDeg = 0
          for angle in (115, 119, 120, 121, 140, 170, 189):
            state.out.steeringAngleDeg = angle * sign
            _, decoded = step(controller, command, state)
            self.assertTrue(decoded['LKAS_Request'])
            self.assertLessEqual(abs(decoded['LKAS_Output']), 190)
          state.out.steeringAngleDeg = 190 * sign
          _, decoded = step(controller, command, state)
          self.assertFalse(decoded['LKAS_Request'])
          state.out.steeringAngleDeg = 140 * sign
          for _ in range(3):
            _, decoded = step(controller, command, state)
            self.assertFalse(decoded['LKAS_Request'])

  def test_continuation_preserves_driver_override_and_all_independent_vetoes(self):
    for car in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025):
      for veto in ('driver', 'temporary', 'permanent', 'main', 'reverse', 'standstill', 'slow', 'lateral'):
        with self.subTest(car=car, veto=veto):
          cp, controller, command, state = setup_controller(car)
          cp.alternativeExperience = 32
          command.enabled = state.out.cruiseState.enabled = False
          command.actuators.steeringAngleDeg = 160
          state.out.vEgoRaw = state.out.vEgo = 4.8
          state.out.standstill = False
          state.out.steeringAngleDeg = 110
          state.out.steeringTorque = state.out.steeringRateDeg = 0
          _, decoded = step(controller, command, state)
          self.assertTrue(decoded['LKAS_Request'])
          state.out.steeringAngleDeg = 140
          if veto == 'driver':
            state.out.steeringTorque = 151
          elif veto == 'temporary':
            state.out.steerFaultTemporary = True
          elif veto == 'permanent':
            state.out.steerFaultPermanent = True
          elif veto == 'main':
            state.out.cruiseState.available = False
          elif veto == 'reverse':
            state.out.gearShifter = structs.CarState.GearShifter.reverse
          elif veto == 'standstill':
            state.out.standstill = True
          elif veto == 'slow':
            state.out.vEgoRaw = .4
          else:
            command.latActive = False
          for _ in range(2):
            _, decoded = step(controller, command, state)
          self.assertFalse(decoded['LKAS_Request'])
