"""Replay-only Ioniq 6 FLM controller seam; no production profile selection.

Historical vectors retain their original calibration; the highway retune has
separate current-controller coverage in test_ioniq6_policy.
"""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from opendbc.car import structs, scale_rot_inertia, scale_tire_stiffness
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR as HYUNDAI
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.flm.torque_surface import Ioniq6Surface
from openpilot.starpilot.lateral.ioniq6_policy import Ioniq6TorquePolicy


# Independently generated from frozen 678af783 LatControlTorque and Ioniq helpers.
# Full controller/PID/filter rows include the recorded symmetric-unwind correction.
_GOLDEN = json.loads((Path(__file__).parent / "testdata" / "ioniq6_controller_replay.json").read_text())
_EXTENDED = json.loads((Path(__file__).parent / "testdata" / "ioniq6_controller_extended.json").read_text())
_LIVE = json.loads((Path(__file__).parent / "testdata" / "ioniq6_controller_live_update.json").read_text())
_TUNED_KNOBS = {"ff_gain_left": 0.30, "ff_gain_right": 0.12,
                "turn_in_boost_left": 2.20, "unwind_taper_right": 10.0,
                "center_taper_max": 0.16, "center_deadband_low_deg": 0.1,
                "low_speed_angle_assist_max_torque": 0.7}


def _params(*, firmware_2025=False):
  car = HYUNDAI.HYUNDAI_IONIQ_6
  cp = interfaces[car].get_non_essential_params(car)
  for name, value in (("mass", 2084.0), ("wheelbase", 2.97), ("centerToFront", 1.188),
                      ("steerRatio", 14.26), ("tireStiffnessFactor", 0.65)):
    setattr(cp, name, value)
  cp.rotationalInertia = scale_rot_inertia(cp.mass, cp.wheelbase)
  cp.tireStiffnessFront, cp.tireStiffnessRear = scale_tire_stiffness(
    cp.mass, cp.wheelbase, cp.centerToFront, cp.tireStiffnessFactor)
  cp.lateralTuning.torque.latAccelFactor = 3.0
  cp.lateralTuning.torque.latAccelOffset = 0.0
  cp.lateralTuning.torque.friction = 0.09
  cp.lateralTuning.torque.steeringAngleDeadzoneDeg = 0.0
  if firmware_2025:
    versions = cp.init("carFw", 2)
    versions[0].fwVersion = b"IONIQ6-230915"
    versions[1].fwVersion = b"IONIQ6-240206"
  return cp


def _controller(cp, surface=None, *, original_constructor_context=False):
  controller = LatControlTorque(cp.as_reader(), interfaces[HYUNDAI.HYUNDAI_IONIQ_6](cp), DT_CTRL, turn_assist=True)
  if surface is not None:
    # Replace before the first update. The production constructor always uses None.
    controller.starpilot_extension.policy = Ioniq6TorquePolicy(controller, cp.as_reader(), surface=surface, turn_assist=True)
  if original_constructor_context:
    controller.hkg_canfd_torque = False
    # These frozen vectors invoke the original constructor directly. Dom
    # controlsd replaces this curve with the saved/default flat gain each tick;
    # this is not a drive-loop replay. Preserve its gain curve and raw-CP startup limits.
    controller.pid._k_p = [[1, 1.5, 2., 3., 5, 7.5, 10, 15, 30],
                           [250, 120, 65, 30, 11.5, 5.5, 3.5, 2., .6]]
    controller.pid.set_limits(controller.lateral_accel_from_torque(controller.steer_max, cp.lateralTuning.torque),
                              controller.lateral_accel_from_torque(-controller.steer_max, cp.lateralTuning.torque))
  return controller


def _assert_rows(test, actual, expected):
  for row, reference in zip(actual, expected, strict=True):
    for value, frozen in zip(row, reference, strict=True):
      test.assertAlmostEqual(value, frozen, places=8)


def _trace(controller, cp, sequence, *, historical_friction=False):
  vm = VehicleModel(cp)
  cs = structs.CarState.new_message()
  cs.gearShifter = structs.CarState.GearShifter.drive
  params = SimpleNamespace(angleOffsetDeg=0.0, roll=0.03)
  rows = []
  for active, speed, angle, curvature, pressed, limited in sequence:
    cs.vEgo = speed
    cs.steeringAngleDeg = angle
    cs.steeringPressed = pressed
    # Historical vectors qualify the unchanged calibration/PID equations before
    # the separately tested friction growth guard, not its new transients.
    if historical_friction:
      with mock.patch.object(selected_policy(controller), 'center_friction',
                             side_effect=lambda friction, *args, **kwargs: friction):
        torque, _, state = controller.update(active, cs, vm, params, limited, curvature, limited, 0.1)
    else:
      torque, _, state = controller.update(active, cs, vm, params, limited, curvature, limited, 0.1)
    rows.append((torque, state.desiredLateralAccel, state.desiredLateralJerk,
                 state.p, state.i, state.f, selected_policy(controller).directional_taper_filter.x))
  return rows


SEQUENCE = (
  [(False, 0.3, 10.0, -0.001, False, False)] * 2 +
  [(True, 0.3, 10.0, -0.001, False, False)] * 4 +
  [(True, 3.0, 8.0, -0.001, False, False)] * 4 +
  [(True, 14.0, -3.0, 0.0004, False, False)] * 5 +
  [(True, 27.0, -2.0, -0.0003, False, True)] * 4 +
  [(True, 14.0, -3.0, 0.0004, True, False)] * 2 +
  [(True, 14.0, -3.0, 0.0004, False, False)] * 3 +
  [(False, 0.0, 8.0, -0.001, False, False)] * 2 +
  [(True, 3.0, 8.0, -0.001, False, False)] * 4
)


class TestIoniq6ControllerReplay(unittest.TestCase):
  @mock.patch("openpilot.starpilot.lateral.ioniq6_policy.IONIQ_6_STANDARD_FRICTION_THRESHOLD_INCREASE", 0.0)
  def test_frozen_stateful_controller_trace(self):
    self.assertEqual(_GOLDEN["sourceCommit"], "678af783")
    self.assertEqual(_GOLDEN["controllerSha256"], "3561d5ac43a72f9b68bc9e44042b8f5cbdca406dec940e7648c64607862a60f7")
    self.assertEqual(_GOLDEN["fields"], ["output", "desiredLateralAccel", "desiredLateralJerk", "p", "i", "f", "directionalFilter"])
    for firmware, fw in (("2023", False), ("2025", True)):
      for mode in ("default", "tuned"):
        with self.subTest(firmware=firmware, mode=mode):
          cp = _params(firmware_2025=fw)
          surface = Ioniq6Surface.validated("firmware_2025" if fw else "standard", _TUNED_KNOBS) if mode == "tuned" else None
          actual = _trace(_controller(cp, surface, original_constructor_context=True), cp, SEQUENCE)
          expected = _GOLDEN["cases"][f"{firmware}_{mode}"]
          _assert_rows(self, actual, expected)

  @mock.patch("openpilot.starpilot.lateral.ioniq6_policy.IONIQ_6_2025_HIGHWAY_FRICTION_THRESHOLD_INCREASE", 0.0)
  @mock.patch("openpilot.starpilot.lateral.ioniq6_policy.IONIQ_6_STANDARD_FRICTION_THRESHOLD_INCREASE", 0.0)
  def test_extended_frozen_filter_limit_override_and_reset_trace(self):
    self.assertEqual(_EXTENDED["sourceCommit"], "678af783")
    self.assertEqual(_EXTENDED["controllerSha256"], _GOLDEN["controllerSha256"])
    self.assertEqual(_EXTENDED["inputFields"], ["active", "speed", "angle", "curvature", "pressed", "limited"])
    self.assertEqual(_EXTENDED["outputFields"], _GOLDEN["fields"])
    sequence = _EXTENDED["sequence"]
    self.assertEqual(len(sequence), 154)
    self.assertTrue(any(not row[0] for row in sequence))
    self.assertTrue(any(row[4] for row in sequence))
    self.assertTrue(any(row[5] for row in sequence))
    for firmware, fw in (("2023", False), ("2025", True)):
      for mode in ("default", "tuned"):
        with self.subTest(firmware=firmware, mode=mode):
          cp = _params(firmware_2025=fw)
          surface = Ioniq6Surface.validated("firmware_2025" if fw else "standard", _TUNED_KNOBS) if mode == "tuned" else None
          actual = _trace(_controller(cp, surface, original_constructor_context=True), cp, sequence, historical_friction=True)
          expected = _EXTENDED["cases"][f"{firmware}_{mode}"]
          _assert_rows(self, actual, expected)

  @mock.patch("openpilot.starpilot.lateral.ioniq6_policy.IONIQ_6_2025_HIGHWAY_FRICTION_THRESHOLD_INCREASE", 0.0)
  @mock.patch("openpilot.starpilot.lateral.ioniq6_policy.IONIQ_6_STANDARD_FRICTION_THRESHOLD_INCREASE", 0.0)
  def test_actual_torque_parameter_update_recalculates_frozen_pid_limits(self):
    self.assertEqual(_LIVE["sourceControllerSha256"], _GOLDEN["controllerSha256"])
    self.assertEqual(_LIVE["fields"], _GOLDEN["fields"])
    for firmware, fw in (("2023", False), ("2025", True)):
      for mode in ("default", "tuned"):
        with self.subTest(firmware=firmware, mode=mode):
          cp = _params(firmware_2025=fw)
          surface = Ioniq6Surface.validated("firmware_2025" if fw else "standard", _TUNED_KNOBS) if mode == "tuned" else None
          controller = _controller(cp, surface, original_constructor_context=True)
          expected = _LIVE["cases"][f"{firmware}_{mode}"]
          self.assertAlmostEqual(controller.pid.pos_limit, expected["before"]["posLimit"], places=8)
          self.assertAlmostEqual(controller.pid.neg_limit, expected["before"]["negLimit"], places=8)
          controller.update_torque_parameters(3.0, 0.0, 0.09)
          self.assertAlmostEqual(controller.pid.pos_limit, expected["after"]["posLimit"], places=8)
          self.assertAlmostEqual(controller.pid.neg_limit, expected["after"]["negLimit"], places=8)
          actual = _trace(controller, cp, _EXTENDED["sequence"], historical_friction=True)
          _assert_rows(self, actual, expected["rows"])

  @mock.patch("openpilot.starpilot.lateral.ioniq6_policy.IONIQ_6_2025_HIGHWAY_FRICTION_THRESHOLD_INCREASE", 0.0)
  @mock.patch("openpilot.starpilot.lateral.ioniq6_policy.IONIQ_6_STANDARD_FRICTION_THRESHOLD_INCREASE", 0.0)
  def test_guarded_extended_trace_preserves_model_pid_inputs_and_manual_surface(self):
    for firmware, fw in (("2023", False), ("2025", True)):
      for mode in ("default", "tuned"):
        with self.subTest(firmware=firmware, mode=mode):
          cp = _params(firmware_2025=fw)
          surface = Ioniq6Surface.validated("firmware_2025" if fw else "standard", _TUNED_KNOBS) if mode == "tuned" else None
          controller = _controller(cp, surface, original_constructor_context=True)
          tune = controller.torque_params.to_dict()
          actual = _trace(controller, cp, _EXTENDED["sequence"])
          expected = _EXTENDED["cases"][f"{firmware}_{mode}"]
          for row, reference in zip(actual, expected, strict=True):
            for index in (1, 2, 3, 6):
              self.assertAlmostEqual(row[index], reference[index], places=8)
            self.assertLessEqual(abs(row[0]), controller.steer_max)
          if mode == "tuned":
            _assert_rows(self, actual, expected)
          else:
            self.assertTrue(any(abs(row[5] - ref[5]) > 1e-8 for row, ref in zip(actual, expected, strict=True)))
          self.assertEqual(controller.torque_params.to_dict(), tune)

  def test_current_startup_and_equal_live_update_use_compensated_limits(self):
    for firmware, fw in (("2023", False), ("2025", True)):
      for mode in ("default", "tuned"):
        with self.subTest(firmware=firmware, mode=mode):
          cp = _params(firmware_2025=fw)
          surface = Ioniq6Surface.validated("firmware_2025" if fw else "standard", _TUNED_KNOBS) if mode == "tuned" else None
          controller = _controller(cp, surface)
          self.assertAlmostEqual(controller.torque_params.latAccelFactor, 3.0 * 1.22, places=6)
          limits = (controller.pid.pos_limit, controller.pid.neg_limit)
          self.assertEqual(limits, (controller.torque_params.latAccelFactor, -controller.torque_params.latAccelFactor))
          for sign in (-1, 1):
            controller.pid.reset()
            accel = controller.pid.update(0., feedforward=sign * 100., freeze_integrator=True)
            self.assertEqual(accel, sign * controller.torque_params.latAccelFactor)
          controller.update_torque_parameters(3.0, 0.0, 0.09)
          self.assertEqual((controller.pid.pos_limit, controller.pid.neg_limit), limits)

  def test_curvature_delay_scales_at_current_speed_without_spurious_unwind(self):
    cp = _params()
    controller = _controller(cp)
    sequence = [(True, 10., 0., .001, False, False)] * 30 + [(True, 20., 0., .001, False, False)] * 20
    actual = _trace(controller, cp, sequence)
    alpha = selected_policy(controller).jerk_filter.alpha
    self.assertAlmostEqual(actual[30][2], actual[29][2] * (1.0 - alpha), places=8)

  def test_replay_surface_is_immutable_session_choice_and_neutral_matches_default(self):
    for variant, fw in (("standard", False), ("firmware_2025", True)):
      with self.subTest(variant=variant):
        cp = _params(firmware_2025=fw)
        original = _controller(cp)
        neutral = _controller(cp, Ioniq6Surface.validated(variant, {}))
        default_rows = _trace(original, cp, SEQUENCE)
        neutral_rows = _trace(neutral, cp, SEQUENCE)
        for row, expected in zip(neutral_rows, default_rows, strict=True):
          for value, reference in zip(row, expected, strict=True):
            self.assertAlmostEqual(value, reference, places=10)
        self.assertEqual(selected_policy(original).surface, None)
        self.assertEqual(selected_policy(neutral).surface.variant, variant)

  def test_tuned_surface_changes_shaping_but_reset_is_deterministic(self):
    for variant, fw in (("standard", False), ("firmware_2025", True)):
      with self.subTest(variant=variant):
        cp = _params(firmware_2025=fw)
        surface = Ioniq6Surface.validated(variant, _TUNED_KNOBS)
        baseline = _trace(_controller(cp), cp, SEQUENCE)
        tuned = _trace(_controller(cp, surface), cp, SEQUENCE)
        repeated = _trace(_controller(cp, surface), cp, SEQUENCE)
        self.assertEqual(tuned, repeated)
        self.assertTrue(any(abs(row[0] - base[0]) > 1e-7 for row, base in zip(tuned, baseline, strict=True)))
        self.assertEqual(tuned[0][0], 0.0)
        self.assertEqual(tuned[-5][0], 0.0)  # inactive reset frame

  def test_invalid_surface_rejected_before_parent_mutation(self):
    cp = _params()
    parent = _controller(cp)
    original = (parent.pid, parent.torque_params.latAccelFactor, selected_policy(parent))
    with self.assertRaises(ValueError):
      Ioniq6TorquePolicy(parent, cp.as_reader(), surface=Ioniq6Surface.validated("firmware_2025", {}))
    self.assertIs(parent.pid, original[0])
    self.assertEqual(parent.torque_params.latAccelFactor, original[1])
    self.assertIs(selected_policy(parent), original[2])
    with self.assertRaises(ValueError):
      Ioniq6TorquePolicy(parent, cp.as_reader(), surface=object())
    other = interfaces[TOYOTA.TOYOTA_COROLLA_TSS2].get_non_essential_params(TOYOTA.TOYOTA_COROLLA_TSS2)
    with self.assertRaises(ValueError):
      Ioniq6TorquePolicy(parent, other.as_reader(), surface=Ioniq6Surface.validated("standard", {}))
    self.assertIs(parent.pid, original[0])


if __name__ == "__main__":
  unittest.main()
