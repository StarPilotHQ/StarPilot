"""Saved assist reaches the model/torque path only with existing rolling authority."""

import json
import os
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed, ioniq_candidate
from openpilot.starpilot.lateral.controller_selection import ControllerMode, DOCUMENT_KEY, replace_mode


def rolling_feed(controls, timestamp, tick, *, speed=.5, standstill=False, fault=False, active=True, gear='drive',
                 signal=True, brake=False, pressed=False, blindspot=False, model_stop=False, planner_stop=False):
  captured = []
  with patch.object(controls.sm, 'update_msgs', side_effect=lambda _time, events: captured.extend(events)):
    feed(controls, timestamp, tick, fault=fault, active=active, enabled=active, signal=signal)
  events = [event.as_builder() for event in captured]
  cs = next(event.carState for event in events if event.which() == 'carState')
  cs.vEgo = cs.vEgoRaw = speed
  cs.standstill = standstill
  cs.gearShifter = gear
  cs.aEgo = -.1
  cs.leftBlinker = False
  cs.rightBlinker = signal
  cs.brakePressed, cs.steeringPressed, cs.rightBlindspot = brake, pressed, blindspot
  next(event.longitudinalPlan for event in events if event.which() == 'longitudinalPlan').shouldStop = planner_stop
  if tick % 5 == 0:
    md = next(event.modelV2 for event in events if event.which() == 'modelV2')
    md.position.x = [float(x) for x in range(51)]
    md.position.y = [.1 * x * x for x in range(51)]
    md.action.desiredCurvature = .0001
    md.action.shouldStop = model_stop
  controls.sm.update_msgs(timestamp / 1e9, [event.as_reader() for event in events])


class TestTurnModelRuntime(unittest.TestCase):
  def construct(self, enabled, mode=ControllerMode.STARPILOT, *, automatic=False):
    cp, _ = ioniq_candidate()
    params = Params()
    params.put('CarParams', cp.to_bytes(), block=True)
    params.put_bool('TurnAssist', enabled, block=True)
    params.put_bool('AutomaticTurnInitiation', automatic, block=True)
    params.put_bool('OpenpilotEnabledToggle', True, block=True)
    params.put(DOCUMENT_KEY, json.loads(replace_mode(None, cp, mode)), block=True)
    return Controls()

  def test_explicit_off_preserves_each_controller_commands(self):
    for mode in (ControllerMode.STARPILOT, ControllerMode.STANDARD):
      with self.subTest(mode=mode), OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
        selected = self.construct(False, mode)
        reference = self.construct(False, mode)
        for tick in range(20):
          now = 1_000_000_000 + tick * 10_000_000
          rolling_feed(selected, now, tick)
          rolling_feed(reference, now, tick)
          actual, _ = selected.state_control()
          expected, _ = reference.state_control()
          self.assertEqual(actual.to_bytes(), expected.to_bytes())
          self.assertEqual(selected.model_turn_assist.turn_hold_curvature, 0.)

  def test_model_bias_reaches_actual_curvature_and_torque_limits(self):
    for mode in (ControllerMode.STANDARD, ControllerMode.STARPILOT):
      with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
        controls = self.construct(True, mode, automatic=True)
        reference = self.construct(False, mode, automatic=True)
        for owner in (controls, reference):
          rolling_feed(owner, 1_000_000_000, 0, signal=False)
          owner.state_control()
        before = controls.LaC.torque_params.to_dict()
        maximum_hold = maximum_difference = 0.
        with patch.object(controls.model_turn_assist, 'update', wraps=controls.model_turn_assist.update) as update:
          for tick in range(1, 121):
            rolling_feed(controls, 1_000_000_000 + tick * 10_000_000, tick)
            rolling_feed(reference, 1_000_000_000 + tick * 10_000_000, tick)
            command, _ = controls.state_control()
            reference.state_control()
            maximum_hold = max(maximum_hold, abs(controls.model_turn_assist.turn_hold_curvature))
            maximum_difference = max(maximum_difference, abs(controls.desired_curvature - reference.desired_curvature))
            self.assertLessEqual(abs(command.actuators.torque), 1.)
          self.assertEqual(update.call_count, 120)
          self.assertGreater(maximum_hold, 0.)
          self.assertGreater(maximum_difference, 0.)
        self.assertEqual(controls.LaC.torque_params.to_dict(), before)

  def test_standstill_speed_fault_reverse_and_inactive_deny_assist(self):
    for gate in ({'speed': 0.}, {'speed': .044}, {'standstill': True}, {'fault': True}, {'gear': 'reverse'}, {'active': False}):
      with self.subTest(gate=gate), OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
        controls = self.construct(True)
        with patch.object(controls.model_turn_assist, 'update', wraps=controls.model_turn_assist.update) as update:
          for tick in range(10):
            rolling_feed(controls, 1_000_000_000 + tick * 10_000_000, tick, **gate)
            controls.state_control()
          update.assert_not_called()
          self.assertEqual(controls.model_turn_assist.turn_hold_curvature, 0.)

  def test_default_off_adds_no_assistance_without_driver_initiation(self):
    for mode in (ControllerMode.STARPILOT, ControllerMode.STANDARD):
      with self.subTest(mode=mode), OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
        selected = self.construct(True, mode)
        reference = self.construct(False, mode)
        for tick in range(30):
          now = 1_000_000_000 + tick * 10_000_000
          rolling_feed(selected, now, tick)
          rolling_feed(reference, now, tick)
          actual, _ = selected.state_control()
          expected, _ = reference.state_control()
          self.assertEqual(actual.to_bytes(), expected.to_bytes())
          self.assertEqual(selected.model_turn_assist.turn_hold_curvature, 0.)
          self.assertFalse(selected.LaC.turn_assist_maneuver)

  def test_opt_in_requires_first_low_speed_sample(self):
    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      controls = self.construct(True, automatic=True)
      rolling_feed(controls, 1_000_000_000, 0, speed=5., signal=False)
      controls.state_control()
      for tick in range(1, 21):
        rolling_feed(controls, 1_000_000_000 + tick * 10_000_000, tick, speed=5.)
        controls.state_control()
        self.assertFalse(controls.turn_unlock.unlocked)
        self.assertFalse(controls.LaC.turn_assist_maneuver)
        self.assertEqual(controls.model_turn_assist.turn_hold_curvature, 0.)
      rolling_feed(controls, 1_250_000_000, 25)
      controls.state_control()
      self.assertTrue(controls.turn_unlock.unlocked)
      self.assertGreater(controls.model_turn_assist.turn_hold_curvature, 0.)

  def test_opt_in_preserves_stop_brake_blindspot_and_override_refusals(self):
    for gate in ({'model_stop': True}, {'planner_stop': True}, {'brake': True}, {'pressed': True}, {'blindspot': True}):
      with self.subTest(gate=gate), OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
        controls = self.construct(True, automatic=True)
        rolling_feed(controls, 1_000_000_000, 0, signal=False)
        controls.state_control()
        for tick in range(5, 15):
          rolling_feed(controls, 1_000_000_000 + tick * 10_000_000, tick, **gate)
          command, _ = controls.state_control()
          self.assertEqual(controls.model_turn_assist.turn_hold_curvature, 0.)
          self.assertFalse(controls.LaC.turn_assist_maneuver)
          self.assertLessEqual(abs(command.actuators.torque), 1.)
