"""Ordinary acknowledgment adds no independent gesture or engagement authority."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpilot.cereal import log
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.selfdrive.selfdrived.events import Events, ET
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.starpilot.aol.runtime import AxisDecision, decide_ordinary_axis, ordinary_lateral_requested


class TestOrdinaryAxis(unittest.TestCase):
  def test_shared_request_predicate_preserves_baseline_output_boundaries(self):
    cp = SimpleNamespace(minSteerSpeed=0., steerAtStandstill=False)
    cs = SimpleNamespace(vEgo=20., standstill=False, steerFaultTemporary=False, steerFaultPermanent=False)
    for active in (False, True):
      for speed in (0., 0.3, 0.300001, 20.):
        for temporary, permanent, stopped in ((False, False, False), (True, False, False),
                                             (False, True, False), (False, False, True)):
          cs.vEgo, cs.steerFaultTemporary, cs.steerFaultPermanent, cs.standstill = speed, temporary, permanent, stopped
          baseline = active and not temporary and not permanent and not (abs(speed) <= 0.3 or stopped)
          self.assertEqual(ordinary_lateral_requested(active, cs, cp), baseline)
    cp.minSteerSpeed = 10.
    cs.vEgo, cs.standstill = 9., False
    cs.steerFaultTemporary = cs.steerFaultPermanent = False
    self.assertFalse(ordinary_lateral_requested(True, cs, cp))
    cp.steerAtStandstill = True
    self.assertTrue(ordinary_lateral_requested(True, cs, cp))

  def test_native_ack_cannot_create_request_or_longitudinal_axis(self):
    for requested in (False, True):
      for observed in (False, True):
        for permitted in (False, True):
          native = SimpleNamespace(requestedLateral=observed, requestedLongitudinal=False, lateralAllowed=permitted)
          result = decide_ordinary_axis(requested=requested, native=native)
          self.assertEqual(result.desired_lateral, requested)
          self.assertEqual(result.lateral_active, requested and observed == requested and permitted)
          self.assertFalse(result.desired_longitudinal or result.longitudinal_active)
    self.assertFalse(decide_ordinary_axis(requested=True, native=None).lateral_active)
    native = SimpleNamespace(requestedLateral=True, requestedLongitudinal=True, lateralAllowed=True)
    self.assertFalse(decide_ordinary_axis(requested=True, native=native).native_acknowledged)

  def test_actual_state_machine_keeps_normal_engagement_and_native_loss_disable(self):
    sd = SelfdriveD.__new__(SelfdriveD)
    sd.CP = SimpleNamespace(passive=False, openpilotLongitudinalControl=False,
                            minSteerSpeed=0., steerAtStandstill=False)
    sd.aol_replay = False
    sd.ordinary_axis_ack_required = True
    sd.aol_car_state_log_ns = 1_000_000_000
    sd.aol_session_id = 'ordinary-session'
    sd.aol_axis_decision = AxisDecision()
    sd.enabled = sd.active = False
    sd.initialized = True
    sd.conditional_car_state_valid = True
    sd.events, sd.state_machine = Events(), StateMachine()
    cs = SimpleNamespace(vEgo=20., standstill=False, steerFaultTemporary=False, steerFaultPermanent=False,
                         canValid=True, canTimeout=False)
    vars(sd)['data_sample'] = lambda: cs
    vars(sd)['update_events'] = lambda _cs: (sd.events.clear(), sd.events.add(log.OnroadEvent.EventName.buttonEnable))
    sd.update_alerts = Mock()
    sd.update_conditional_mode = Mock()
    from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
    sd.AM = AlertManager()
    sd.pm = Mock()
    vars(sd)['sm'] = SimpleNamespace(frame=1, seen={'aolSafetyWire': True})
    sd.experimental_mode = False
    sd.personality = 1
    sd.conditional_replay = False
    sd.events_prev = []
    sd.aol_sequence = 0
    off = SimpleNamespace(requestedLateral=False, requestedLongitudinal=False, lateralAllowed=False)
    on = SimpleNamespace(requestedLateral=True, requestedLongitudinal=False, lateralAllowed=True)
    with patch('openpilot.selfdrive.selfdrived.selfdrived.ordinary_axis_request_allowed', return_value=True), \
         patch('openpilot.selfdrive.selfdrived.selfdrived.current_native', side_effect=(off, on, None)), \
         patch('openpilot.selfdrive.selfdrived.selfdrived.current_intent') as intent_reader:
      sd.step()
      self.assertTrue(sd.enabled and sd.active)
      self.assertTrue(sd.aol_axis_decision.desired_lateral)
      self.assertFalse(sd.aol_axis_decision.lateral_active)
      sd.step()
      self.assertTrue(sd.aol_axis_decision.lateral_active)
      sd.step()
      self.assertFalse(sd.enabled or sd.active)
      self.assertTrue(sd.events.contains(ET.IMMEDIATE_DISABLE))
      intent_reader.assert_not_called()
    axes = [call.args[1].aolAxisState for call in sd.pm.send.call_args_list if call.args[0] == 'aolAxisState']
    self.assertEqual(len(axes), 3)
    self.assertEqual([axis.desiredLateral for axis in axes], [True, True, False])
    self.assertEqual([axis.lateralActive for axis in axes], [False, True, False])
    self.assertTrue(all(not axis.desiredLongitudinal for axis in axes))
    self.assertFalse(any(call.args[0] == 'aolIntentWire' for call in sd.pm.send.call_args_list))

  def test_actual_controls_intersects_baseline_without_changing_enabled_or_long(self):
    import os
    from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params
    from openpilot.cereal import messaging
    from openpilot.common.params import Params
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.selfdrive.controls.controlsd import Controls
    from openpilot.starpilot.aol.wire import SAFETY_SERVICE, SafetyState, encode_safety
    from openpilot.starpilot.lateral.tests.test_lane_runtime import feed

    cp = params()
    with OpenpilotPrefix(), patch.dict(os.environ, {'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}), \
         patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      Params().put_bool('AlwaysOnLateral', False)
      Params().put('CarParams', cp.to_bytes(), block=True)
      controls = Controls()
      self.assertFalse(controls.aol_replay)
      self.assertTrue(controls.ordinary_axis_ack_required)
      self.assertNotIn('aolIntentWire', controls.sm.services)
      for tick, (active, fault, acknowledged, session, expected) in enumerate(
          ((True, False, True, 'ordinary', True), (True, False, False, 'ordinary', False),
           (True, True, True, 'ordinary', False), (False, False, True, 'ordinary', False),
           (True, False, True, 'old', False), (True, False, True, 'profile', False),
           (True, False, True, 'stale', False))):
        now = 1_000_000_000 + tick * 10_000_000
        feed(controls, now, tick, active=active, enabled=active, fault=fault)
        cs = messaging.new_message('carState', valid=True, logMonoTime=now)
        cs.carState = controls.sm['carState']
        cs.carState.gearShifter = 'drive'
        cs.carState.cruiseState.enabled = True
        controls.sm.data['carState'] = cs.carState.as_reader()
        axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
        axis.aolAxisState.qualified = True
        axis.aolAxisState.sessionId = 'ordinary'
        axis.aolAxisState.observedMonoTime = now
        axis.aolAxisState.validUntilMonoTime = now + 30_000_000
        axis.aolAxisState.desiredLateral = True
        axis.aolAxisState.lateralActive = acknowledged
        axis.aolAxisState.nativeAcknowledged = acknowledged
        native = messaging.new_message(SAFETY_SERVICE, 0, valid=True, logMonoTime=now)
        native.aolSafetyWire = encode_safety(SafetyState(
          1, True, now, now + 200_000_000, int(cp.safetyConfigs[0].safetyModel.raw),
          cp.safetyConfigs[0].safetyParam ^ (1 if session == 'profile' else 0),
          acknowledged, False, True, False, 'synthetic-panda', 'ordinary' if session in ('profile', 'stale') else session))
        if session == 'stale':
          axis.aolAxisState.validUntilMonoTime = now - 1
        controls.sm.update_msgs(now / 1e9, [axis.as_reader(), native.as_reader()])
        command, _ = controls.state_control()
        self.assertEqual(command.latActive, expected)
        self.assertEqual(command.enabled, active)
        self.assertFalse(command.longActive)

  def test_encoded_session_ack_matches_only_current_ordinary_request(self):
    from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params
    from openpilot.cereal import messaging
    from openpilot.starpilot.aol.runtime import current_native, ordinary_axis_acknowledged
    from openpilot.starpilot.aol.wire import SAFETY_SERVICE, SafetyState, encode_safety

    cp = params()
    now = 1_000_000_000
    from openpilot.common.prefix import OpenpilotPrefix
    with OpenpilotPrefix():
      sm = messaging.SubMaster(['aolAxisState', SAFETY_SERVICE])
      axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
      axis.aolAxisState.qualified = True
      axis.aolAxisState.sessionId = 'ordinary-current'
      axis.aolAxisState.observedMonoTime = now
      axis.aolAxisState.validUntilMonoTime = now + 30_000_000
      axis.aolAxisState.desiredLateral = True
      axis.aolAxisState.lateralActive = True
      axis.aolAxisState.nativeAcknowledged = True
      for tick, (session, requested, allowed, expected) in enumerate((
          ('ordinary-current', True, True, True), ('old-session', True, True, False),
          ('ordinary-current', False, True, False), ('ordinary-current', True, False, False))):
        now = 1_000_000_000 + tick * 10_000_000
        axis.logMonoTime = now
        axis.aolAxisState.observedMonoTime = now
        axis.aolAxisState.validUntilMonoTime = now + 30_000_000
        native = messaging.new_message(SAFETY_SERVICE, 0, valid=True, logMonoTime=now)
        native.aolSafetyWire = encode_safety(SafetyState(
          1, True, now, now + 200_000_000, int(cp.safetyConfigs[0].safetyModel.raw),
          cp.safetyConfigs[0].safetyParam, requested, False, allowed, False,
          'synthetic-transport-only', session))
        sm.update_msgs(now / 1e9, [axis.as_reader(), native.as_reader()])
        observed = current_native(sm, cp, now_ns=now, axis_session_id='ordinary-current')
        decision = decide_ordinary_axis(requested=True, native=observed)
        self.assertEqual(decision.lateral_active, expected)
        self.assertEqual(ordinary_axis_acknowledged(sm, cp, now_ns=now), expected)
        self.assertFalse(decision.desired_longitudinal or decision.longitudinal_active)
      self.assertFalse(ordinary_axis_acknowledged(sm, cp, now_ns=now + 30_000_001))
