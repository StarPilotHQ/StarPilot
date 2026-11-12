"""Fixed shared ceiling and the actual final longitudinal controller boundary."""

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.longitudinal.output_max import KEY, final_output
from openpilot.starpilot.longitudinal.tests.test_gm_volt_long_policy import controls_fixture


class OutputMaximumTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.path = Path(self.params.get_param_path(KEY))

  def test_fixed_four_ignores_saved_values_without_rewriting_them(self):
    for raw in (b'0.1', b'1.5', b'4.0', b'broken', b'nan', b'1' * 129):
      with self.subTest(raw=raw):
        self.path.write_bytes(raw)
        self.assertEqual(final_output(5.0), 4.0)
        self.assertEqual(final_output(2.0), 2.0)
        self.assertEqual(final_output(-2.0), -2.0)
        self.assertEqual(self.path.read_bytes(), raw)
    self.path.unlink()
    self.assertEqual(final_output(5.0), 4.0)

  def test_actual_controls_clamps_after_pid_preserving_history_braking_and_inactive(self):
    controls, now, offset = controls_fixture()
    reference, _, _ = controls_fixture()
    self.path.write_bytes(b"0.25")
    for owner in (controls, reference):
      owner.sm['longitudinalPlan'].aTarget = .5
      owner.LoC.pid.i = .8
    with patch.dict(os.environ, {'REPLAY': '0'}), \
         patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)), \
         patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
      actual, _ = controls.state_control()
      original, _ = reference.state_control()
      self.assertGreater(original.actuators.accel, .25)
      self.assertEqual(actual.actuators.accel, original.actuators.accel)
      self.assertEqual(controls.CI.get_pid_accel_limits(controls.CP, 12.0, 0.0), (-4.0, 2.0))
      self.assertLessEqual(actual.actuators.accel, 2.0)
      self.assertEqual(controls.LoC.last_output_accel, reference.LoC.last_output_accel)
      self.assertEqual((controls.LoC.pid.p, controls.LoC.pid.i, controls.LoC.pid.f),
                       (reference.LoC.pid.p, reference.LoC.pid.i, reference.LoC.pid.f))
      for owner in (controls, reference):
        owner.sm['longitudinalPlan'].aTarget = -1.0
        owner.LoC.pid.i = 0.0
      actual, _ = controls.state_control()
      original, _ = reference.state_control()
      self.assertLess(actual.actuators.accel, 0)
      self.assertEqual(actual.actuators.accel, original.actuators.accel)
      self.assertEqual(controls.LoC.last_output_accel, reference.LoC.last_output_accel)
      controls.sm['selfdriveState'].enabled = False
      actual, _ = controls.state_control()
      self.assertEqual((actual.actuators.accel, controls.LoC.last_output_accel), (0., 0.))


  def test_actual_controls_reads_saved_takeoff_preference_and_keeps_normal_ceiling(self):
    from openpilot.starpilot.longitudinal.inputs import LongitudinalInputs

    for enabled in (False, True):
      controls, now, offset = controls_fixture()
      controls.params.put_bool('FasterLeadTakeoff', enabled, block=True)
      controls.longitudinal_inputs = LongitudinalInputs(controls.CP, controls.params, lambda controls=controls: controls.sm)
      controls.sm['longitudinalPlan'].aTarget = 3.5
      with patch.dict(os.environ, {'REPLAY': '0'}), \
           patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)), \
           patch('openpilot.starpilot.longitudinal.inputs.time.monotonic_ns', return_value=now), \
           patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
        actual, _ = controls.state_control()
      self.assertEqual(controls.longitudinal_inputs.faster_takeoffs, enabled)
      self.assertEqual(actual.actuators.accel, 3.5 if enabled else 2.0)
