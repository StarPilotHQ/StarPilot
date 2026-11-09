"""PE independent LAT intent survives only qualified native withdrawals."""
from contextlib import ExitStack

from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture


def state(*, gas=False, eps=False):
  cs = fixture.car_state()
  cs.vEgoRaw = cs.vEgo
  cs.gasPressed = gas
  cs.steerFaultTemporary = eps
  cs.cruiseState.available = True
  return cs


def setup_case(cleanup):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  cleanup.callback(case.doCleanups)
  cleanup.enter_context(OpenpilotPrefix())
  from opendbc.car.hyundai.tests.test_ioniq5pe_pending_startup import TestPEPendingStartup
  startup = TestPEPendingStartup(methodName='runTest')
  cleanup.callback(startup.doCleanups)
  startup.setUp()
  owner, ci, _ = startup.instance(seal=False)
  cleanup.callback(owner.close)
  sd, controls, drive, card, receive = case._ioniq_availability_callers(full_op=False, cp=ci.CP, startup_owner=owner)
  assert sd.CP.safetyConfigs[0].safetyParam == 0x5495
  for tick in range(2, 152):
    axis, _, command = drive(tick, native_request_long=False, gas=tick >= 150, observed_state=state(gas=tick >= 150))
    assert axis.lateralActive and command.latActive and not command.longActive
    assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                   event=sd.pm.messages['onroadEvents'], state=state(gas=tick >= 150))
  return sd, controls, drive, card, receive


def test_pe_expired_output_then_native_zero_then_pedal_denial_keeps_only_intent():
  with ExitStack() as cleanup:
    sd, _, drive, card, receive = setup_case(cleanup)
    old = sd.pm.messages['aolAxisState']
    before = card.aol_transport_feedback.lateral_source_ns
    axis, _, command = drive(159, gas=True, observed_state=state(gas=True), allowed=False,
                            native_request_lat=False, native_request_long=False)
    assert not axis.lateralActive and not command.latActive and not command.longActive
    assert receive(11_590_000_000, axis=old, event=sd.pm.messages['onroadEvents'], state=state(gas=True))
    assert receive(11_591_000_000, axis=sd.pm.messages['aolAxisState'], state=state(gas=True))
    for tick in (160, 161):
      axis, _, command = drive(tick, gas=True, observed_state=state(gas=True), allowed=False,
                              native_request_lat=True, native_request_long=False)
      assert axis.desiredLateral and not axis.lateralActive
      assert not command.latActive and not command.longActive
      assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'], state=state(gas=True))
      assert card.aol_transport_feedback.lateral_source_ns == before
    axis, _, command = drive(162, observed_state=state(), native_request_lat=True, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive
    assert receive(11_620_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=state())


def test_pe_native_denial_precedes_first_card_eps_sample_and_host_event():
  with ExitStack() as cleanup:
    sd, _, drive, card, receive = setup_case(cleanup)
    axis, _, command = drive(152, gas=True, observed_state=state(gas=True), allowed=False,
                            native_request_lat=True, native_request_long=False)
    assert axis.desiredLateral and not axis.lateralActive and not command.latActive
    eps = state(gas=True, eps=True)
    eps.steeringPressed = True
    eps.steeringTorque = 549.
    eps.steeringAngleDeg = 166.2
    assert receive(11_520_000_000, axis=sd.pm.messages['aolAxisState'],
                   event=sd.pm.messages['onroadEvents'], state=eps)
    for tick in (153, 154):
      axis, _, command = drive(tick, gas=True, observed_state=eps, allowed=False,
                              native_request_lat=True, native_request_long=False)
      assert not axis.lateralActive and not command.latActive and not command.longActive
      assert receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'], state=eps)
    axis, _, command = drive(155, observed_state=state(), native_request_lat=True, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive
    assert receive(11_550_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'], state=state())


def test_pe_duplicate_native_first_eps_cannot_extend_original_output_deadline():
  with ExitStack() as cleanup:
    sd, _, drive, card, receive = setup_case(cleanup)
    drive(152, gas=True, observed_state=state(gas=True), allowed=False,
          native_request_lat=True, native_request_long=False)
    eps = state(gas=True, eps=True)
    proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
    assert receive(11_520_000_000, axis=proof, event=event, state=eps)
    deadline = card.aol_transport_feedback.hkg_eps_join_until_ns
    assert deadline == 11_540_000_000
    assert receive(deadline, axis=proof, event=event, state=eps)
    assert card.aol_transport_feedback.hkg_eps_join_until_ns == deadline
    assert not receive(deadline + 1, axis=proof, event=event, state=eps)


def test_pe_experimental_preference_survives_exact_long_stock_long_restart():
  import json
  from pathlib import Path
  from unittest.mock import patch
  from openpilot.common.params import Params
  from openpilot.selfdrive.selfdrived import selfdrived
  from openpilot.starpilot.conditional_mode.actions import stock_document
  from opendbc.car.hyundai.ioniq5pe_long_profile import candidate
  from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params
  with OpenpilotPrefix():
    saved = Params()
    saved.put('ConditionalModeConfig', json.loads(stock_document()), block=True)
    saved.put_bool('ExperimentalMode', True, block=True)
    saved.put_bool('AlphaLongitudinalEnabled', True, block=True)
    stock = params()
    long = candidate(stock, enabled=True, is_release=False)
    for cp, expected in ((long, True), (stock, False), (long, True)):
      before = cp.to_dict()
      with patch.object(selfdrived, 'Params', return_value=saved), \
           patch.object(selfdrived.messaging, 'PubMaster'), \
           patch.object(selfdrived.messaging, 'SubMaster'), \
           patch.object(selfdrived.messaging, 'sub_sock'), \
           patch.object(selfdrived, 'feature_enabled', return_value=False):
        drive = selfdrived.SelfdriveD(CP=cp.as_reader())
      drive.refresh_saved_driving_mode(1_000_000_000)
      drive.update_conditional_mode(None)
      assert drive.experimental_mode == expected
      assert Path(saved.get_param_path('ExperimentalMode')).read_bytes() == b'1'
      assert saved.get_bool('AlphaLongitudinalEnabled')
      assert cp.to_dict() == before


def test_pe_native_first_eps_preserves_fault_identity_and_source_vetoes():
  from dataclasses import replace
  from openpilot.cereal import log
  for fault in ('native_invalid', 'rx_invalid', 'native_fault', 'permanent_eps', 'can_invalid', 'can_timeout',
                'producer', 'epoch', 'session', 'fault_session', 'source_reordered', 'hard_event', 'settings_disabled'):
    with ExitStack() as cleanup:
      sd, _, drive, card, receive = setup_case(cleanup)
      from openpilot.cereal import messaging
      pandas = None
      if fault == 'native_fault':
        pandas = messaging.new_message('pandaStates', len(sd.sm['pandaStates']), valid=True, logMonoTime=11_520_000_000)
        pandas.pandaStates = sd.sm['pandaStates']
        pandas.pandaStates[0].faults = ['interruptRateCan1']
      drive(152, gas=True, observed_state=state(gas=True), allowed=False, native_request_lat=True,
            panda_state_message=pandas,
            native_request_long=False, native_valid=fault != 'native_invalid',
            panda_fault=fault == 'rx_invalid',
            critical_event=log.OnroadEvent.EventName.canError if fault == 'hard_event' else None)
      proof = sd.pm.messages['aolAxisState'].as_builder()
      eps = state(gas=True, eps=True)
      if fault == 'permanent_eps':
        eps.steerFaultPermanent = True
      elif fault == 'can_invalid':
        eps.canValid = False
      elif fault == 'can_timeout':
        eps.canTimeout = True
      elif fault == 'producer':
        proof.aolAxisState.intentProducerSessionId = 'other-card'
      elif fault == 'epoch':
        proof.aolAxisState.intentIntegrityEpoch += 1
      elif fault == 'session':
        proof.aolAxisState.sessionId = 'other-session'
      elif fault == 'fault_session':
        proof.aolAxisState.faultSessionId = 'other-session'
      elif fault == 'source_reordered':
        proof.aolAxisState.sourceCarStateMonoTime = card.aol_transport_feedback.lateral_source_ns - 1
      elif fault == 'settings_disabled':
        card.aol_card_intent.settings = replace(card.aol_card_intent.settings, enabled=False)
      assert not receive(11_520_000_000, axis=proof.as_reader(),
                         event=sd.pm.messages['onroadEvents'], state=eps), fault
      assert not card.aol_card_intent.allowed_latch, fault
