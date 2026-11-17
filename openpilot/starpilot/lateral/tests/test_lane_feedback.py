from unittest.mock import patch
from types import SimpleNamespace
import unittest

import numpy as np
from openpilot.starpilot.ui.raylib import rl

from openpilot.cereal import messaging
from openpilot.starpilot.lateral.lane_centering import LaneCenteringResult
from openpilot.starpilot.lateral.lane_feedback import BLUE, MAX_AGE_NS, SERVICE, direction, feedback_message


class TestLaneFeedback(unittest.TestCase):
  def reader(self, *, applied=0.0003, requested=0.0005):
    now = 1_000_000_000
    event = feedback_message(LaneCenteringResult(0.001, requested, 1, 'qualified'), applied, True, model_mono_time=now, car_control_mono_time=now, valid=True)
    event.logMonoTime = now
    control = messaging.new_message('carControl', valid=True, logMonoTime=now)
    control.carControl.latActive = True
    model = messaging.new_message('modelV2', valid=True, logMonoTime=now)
    sm = messaging.SubMaster([SERVICE, 'carControl', 'modelV2'], frequency=20)
    sm.update_msgs(now / 1e9 - 0.01, [])
    sm.update_msgs(now / 1e9, [event.as_reader(), control.as_reader(), model.as_reader()])
    return sm, now

  def test_inactive_reason_is_diagnostic_only_and_result_takes_precedence(self):
    for result, reason in ((None, 'host_selfdriveState_frequency'),
                           (LaneCenteringResult(0.001, 0.0003, 1, 'qualified'), 'qualified')):
      event = feedback_message(result, 0., True, model_mono_time=1_000_000_000,
                               car_control_mono_time=1_000_000_000, valid=True,
                               inactive_reason='host_selfdriveState_frequency')
      state = event.starpilotLateralState.laneCentering
      self.assertEqual(state.reason, reason)
      self.assertAlmostEqual(state.requestedCorrection, result.correction if result is not None else 0., places=9)
      self.assertEqual(state.appliedCorrection, 0.)
      self.assertFalse(event.starpilotLateralState.active)
    event = feedback_message(None, 0., False, model_mono_time=0, car_control_mono_time=0, valid=False)
    self.assertEqual(event.starpilotLateralState.laneCentering.reason, 'inactive')

  def test_real_messages_show_applied_sign_and_keep_legacy_fields_inactive(self):
    for correction, expected in ((0.0003, 1), (-0.0003, -1), (0.0, 0), (0.0000005, 0)):
      sm, now = self.reader(applied=correction, requested=0.0005 if correction >= 0 else -0.0005)
      self.assertEqual(direction(sm, now, 0), expected)
      self.assertFalse(sm[SERVICE].active)
      self.assertEqual(sm[SERVICE].frictionScale, 0.0)

  def test_stale_invalid_old_drive_disabled_and_inconsistent_inputs_clear_blue(self):
    for service in (SERVICE, 'carControl', 'modelV2'):
      for flag in ('alive', 'valid'):
        sm, now = self.reader()
        getattr(sm, flag)[service] = False
        self.assertEqual(direction(sm, now, 0), 0)
    sm, now = self.reader()
    self.assertEqual(direction(sm, now + MAX_AGE_NS + 1, 0), 0)
    self.assertEqual(direction(sm, now - 1, 0), 0)
    self.assertEqual(direction(sm, now, sm.frame), 0)
    for applied, requested in ((0.0013, 0.0013), (0.0006, 0.0005), (-0.0003, 0.0005), (float('nan'), 0.0005)):
      sm, now = self.reader(applied=applied, requested=requested)
      self.assertEqual(direction(sm, now, 0), 0)
    for field, value in (('version', 0), ('lateralActive', False), ('modelMonoTime', 1), ('carControlMonoTime', 1)):
      sm, now = self.reader()
      event = messaging.new_message(SERVICE, valid=True, logMonoTime=now)
      event.starpilotLateralState = sm[SERVICE]
      setattr(event.starpilotLateralState.laneCentering, field, value)
      sm.update_msgs(now / 1e9 + 0.01, [event.as_reader()])
      self.assertEqual(direction(sm, now, 0), 0)
    sm, now = self.reader()
    control = messaging.new_message('carControl', valid=True, logMonoTime=now)
    control.carControl.latActive = False
    sm.update_msgs(now / 1e9 + 0.01, [control.as_reader()])
    self.assertEqual(direction(sm, now, 0), 0)

  def test_model_renderer_blue_retains_alpha_and_overrides_torque_color(self):
    from openpilot.selfdrive.ui.onroad import model_renderer as large
    from openpilot.selfdrive.ui.mici.onroad import model_renderer as compact
    from openpilot.selfdrive.ui.ui_state import UIStatus

    for module in (large, compact):
      for sign in (-1, 0, 1):
        renderer = module.ModelRenderer.__new__(module.ModelRenderer)
        renderer._rect = rl.Rectangle(0, 0, 536, 240)
        self.enterContext(
          patch.object(
            renderer, '_lane_lines', [SimpleNamespace(projected_points=np.array([[0.0, 0.0], [5.0, 0.0], [0.0, 5.0]])) for _ in range(4)], create=True
          )
        )
        renderer._road_edges = []
        self.enterContext(patch.object(renderer, '_lane_line_probs', [0.3, 0.6, 0.7, 0.4], create=True))
        if module is compact:
          self.enterContext(patch.object(renderer, '_visual_status', lambda: UIStatus.ENGAGED, create=True))
          self.enterContext(patch.object(renderer, '_torque_filter', SimpleNamespace(x=0.9), create=True))
        with patch.object(module, 'lane_centering_direction', return_value=sign), patch.object(module, 'draw_polygon') as draw:
          renderer._draw_lane_lines()
        colors = [(c.r, c.g, c.b, c.a) for call in draw.call_args_list for c in [call.args[2]]]
        self.assertEqual([i for i, color in enumerate(colors) if color[:3] == BLUE], [1 if sign < 0 else 2] if sign else [])
        if sign:
          index = 1 if sign < 0 else 2
          self.assertEqual(colors[index][3], int(renderer._lane_line_probs[index] * 255))
        for index in (0, 3):
          self.assertEqual(colors[index][3], int(renderer._lane_line_probs[index] * 255))


class TestSteeringLimitFeedback(unittest.TestCase):
  def test_actual_publish_tracks_aol_lateral_and_clears_inactive_feedback(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.hyundai.values import CAR
    from openpilot.cereal import log
    from openpilot.common.params import Params
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.selfdrive.controls.controlsd import Controls

    for mode in ('angle', 'torque'):
      with self.subTest(mode=mode), OpenpilotPrefix():
        cp = interfaces[CAR.GENESIS_G70_2020].get_non_essential_params(CAR.GENESIS_G70_2020)
        cp.steerControlType = mode
        Params().put('CarParams', cp.to_bytes(), block=True)
        controls = Controls()
        command = messaging.new_message('carControl').carControl
        command.actuators.steeringAngleDeg = 10.
        command.actuators.torque = .5
        lateral_log = (log.ControlsState.LateralAngleState if mode == 'angle' else log.ControlsState.LateralTorqueState).new_message()
        for frame, (standard, lateral, limited) in enumerate(((False, True, True), (False, True, False), (True, False, True),
                                                             (False, True, True), (False, False, True))):
          messages = []
          for service in ('carState', 'carOutput', 'selfdriveState'):
            message = messaging.new_message(service, valid=True, logMonoTime=1_000_000_000 + frame * 10_000_000)
            messages.append(message)
          messages[0].carState.canValid = True
          messages[1].carOutput.actuatorsOutput.steeringAngleDeg = 0. if limited else 10.
          messages[1].carOutput.actuatorsOutput.torque = 0. if limited else .5
          messages[2].selfdriveState.active = standard
          controls.sm.update_msgs(1. + frame * .01, [message.as_reader() for message in messages])
          command.latActive = lateral
          controls.publish(command, lateral_log)
          self.assertEqual(controls.steer_limited_by_safety, lateral and limited)
