from dataclasses import replace
from types import SimpleNamespace

import pytest

from openpilot.cereal import messaging
from openpilot.starpilot.aol.runtime import current_native
from openpilot.starpilot.aol.transport_pause import _newer_combined_intent_native, healthy_transport
from openpilot.starpilot.aol.tests.test_panda_inventory import NOW, bound_state, configured_cp, sm_for
from openpilot.starpilot.aol.tests.test_runtime import car_state


@pytest.mark.parametrize('case', ['older', 'equal', 'newer', 'legacy', 'slot_controls', 'native_long', 'native_lat',
  'request_long', 'request_lat', 'session', 'expired', 'future', 'lease', 'native_rx', 'native_heartbeat', 'native_fault',
  'native_identity', 'native_experience', 'native_invalid', 'native_lost', 'panda_rx', 'panda_heartbeat', 'panda_fault',
  'panda_identity', 'panda_experience', 'panda_invalid', 'panda_lost', 'panda_unseen', 'axis_lat',
  'axis_request_lat', 'axis_request_long', 'axis_ack', 'brake', 'gas', 'acc', 'can', 'timeout', 'steer', 'cruise'])
@pytest.mark.parametrize('long_active', [False, True])
def test_newer_native_retains_only_complete_current_combined_intent(case, long_active):
  cp = configured_cp()
  native = bound_state(cp)
  source = native.sourcePandaStatesMonoTime
  timestamp = source - 50_000_000 if case not in ('equal', 'newer') else source + int(case == 'newer')
  cs = car_state()
  cs.cruiseState.enabled = True
  axis = SimpleNamespace(sessionId=native.axisSessionId, nativeAcknowledged=True,
    desiredLateral=True, lateralActive=True, desiredLongitudinal=True, longitudinalActive=long_active)
  native_changes = {'native_long': {'longitudinalAllowed': False}, 'native_lat': {'lateralAllowed': False},
    'request_long': {'requestedLongitudinal': False}, 'request_lat': {'requestedLateral': False},
    'future': {'observedMonoTime': NOW + 1}, 'lease': {'validUntilMonoTime': NOW + 200_000_001}}
  if case in native_changes:
    native = replace(native, **native_changes[case])
  slot_changes = {'slot_controls': {'controlsAllowed': False}, 'native_rx': {'safetyRxChecksInvalid': True},
    'native_heartbeat': {'heartbeatLost': True}, 'native_fault': {'faults': 1},
    'native_identity': {'safetyParam': native.safetyParam ^ 1}, 'native_experience': {'alternativeExperience': cp.alternativeExperience ^ 1}}
  if case in slot_changes:
    native = replace(native, pandaInventory=(replace(native.pandaInventory[0], **slot_changes[case]),))
  if case == 'legacy':
    native = replace(native, pandaInventory=(), sourcePandaStatesMonoTime=0)
    timestamp = NOW - 50_000_000
  sm = sm_for(cp, native, panda_stamp=timestamp)
  update = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=timestamp)
  update.pandaStates[0] = sm['pandaStates'][0]
  p = update.pandaStates[0]
  p.controlsAllowed = False
  panda_changes = {'panda_rx': ('safetyRxChecksInvalid', True), 'panda_heartbeat': ('heartbeatLost', True),
    'panda_fault': ('faults', ['relayMalfunction']), 'panda_identity': ('safetyParam', 0),
    'panda_experience': ('alternativeExperience', cp.alternativeExperience ^ 1)}
  if case in panda_changes:
    setattr(p, *panda_changes[case])
  sm.update_msgs(NOW / 1e9, [update.as_reader()])
  if case in ('panda_invalid', 'panda_lost', 'panda_unseen'):
    getattr(sm, {'panda_invalid': 'valid', 'panda_lost': 'alive', 'panda_unseen': 'seen'}[case])['pandaStates'] = False
  if case in ('native_invalid', 'native_lost'):
    getattr(sm, 'valid' if case == 'native_invalid' else 'alive')['aolSafetyWire'] = False
  axis_changes = {'session': ('sessionId', 'foreign'), 'axis_lat': ('lateralActive', False),
    'axis_request_lat': ('desiredLateral', False),
    'axis_request_long': ('desiredLongitudinal', False), 'axis_ack': ('nativeAcknowledged', False)}
  if case in axis_changes:
    setattr(axis, *axis_changes[case])
  cs_changes = {'brake': ('brakePressed', True), 'gas': ('gasPressed', True), 'acc': ('accFaulted', True),
    'can': ('canValid', False), 'timeout': ('canTimeout', True), 'steer': ('steerFaultPermanent', True)}
  if case in cs_changes:
    setattr(cs, *cs_changes[case])
  if case == 'cruise':
    cs.cruiseState.enabled = False
  now = NOW + 200_000_001 if case == 'expired' else NOW
  assert healthy_transport(sm, cp, cs, axis.sessionId, now) == (case == 'request_long')
  before = (native, tuple(sm.logMonoTime.items()), tuple(sm.recv_time.items()))
  result = _newer_combined_intent_native(sm, cp, cs, axis, now)
  assert (result is not None) == (case == 'older')
  assert (native, tuple(sm.logMonoTime.items()), tuple(sm.recv_time.items())) == before
  if result is not None:
    assert result == current_native(sm, cp, now_ns=now, axis_session_id=axis.sessionId)
    assert result.observedMonoTime == NOW and result.validUntilMonoTime == NOW + 200_000_000


@pytest.mark.parametrize('case', ['older', 'equal', 'newer', 'fault', 'native_identity', 'expired', 'source_lost'])
def test_actual_gm_sd_controls_and_card_keep_current_denials(case):
  from unittest.mock import patch
  from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
  from opendbc.car.gm.values import CAR
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car
  from openpilot.starpilot.aol.intent import AolProcessFaultContext, AolSettings
  from openpilot.starpilot.aol.tests.test_ioniq_availability import IoniqAvailabilityTests
  from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
  from openpilot.starpilot.aol.vehicle import create_intent, policy_for
  from openpilot.starpilot.aol.wire import IntentState, encode_intent, encode_safety

  fixture = IoniqAvailabilityTests(methodName='runTest')
  try:
    with OpenpilotPrefix():
      cp = ordinary_params(CAR.CHEVROLET_VOLT_ASCM, alpha=True, sascm=True, radar=True)
      sd, controls, drive = fixture._authority_callers(True, cp=cp)
      assert sd.CP.brand == 'gm' and sd.CP.openpilotLongitudinalControl
      card = Car.__new__(Car)
      card.CP, card.slc_producer_session = sd.CP, 'authority-card'
      services = ['aolAxisState', 'aolSafetyWire', 'pandaStates', 'onroadEvents']
      card.sm = messaging.SubMaster(services, ignore_avg_freq=services)
      card.aol_process_fault_context = AolProcessFaultContext()
      card.aol_transport_feedback = TransportPauseFeedback(card.CP)
      settings = AolSettings(True, 0., 9, 0, (0, 0, 0), (0, 0, 0))
      card.aol_settings = settings
      card.aol_card_intent = create_intent(card.CP, settings, policy_for(card.CP))
      assert card.aol_card_intent is not None
      cs = car_state()
      cs.cruiseState.available = cs.cruiseState.enabled = True
      card.aol_card_intent.update(cs, fault_active=False, now_ns=NOW - 1, standard_enabled=True)
      assert card.aol_card_intent.allowed_latch
      for tick in range(3):
        now = NOW + tick * 10_000_000
        state = bound_state(sd.CP, now=now)
        pandas = messaging.new_message('pandaStates', len(cp.safetyConfigs), valid=True,
                                      logMonoTime=state.sourcePandaStatesMonoTime)
        for panda, config in zip(pandas.pandaStates, cp.safetyConfigs, strict=True):
          panda.safetyModel, panda.safetyParam = config.safetyModel, config.safetyParam
          panda.alternativeExperience, panda.controlsAllowed = cp.alternativeExperience, True
        if tick == 2:
          pandas.logMonoTime = state.sourcePandaStatesMonoTime + (1 if case == 'newer' else 0 if case == 'equal' else -50_000_000)
          pandas.pandaStates[-1].controlsAllowed = False
          if case == 'fault':
            pandas.pandaStates[-1].faults = ['relayMalfunction']
          if case == 'source_lost':
            pandas.valid = False
          if case == 'native_identity':
            state = replace(state, pandaInventory=(replace(state.pandaInventory[0], safetyParam=0),))
          if case == 'expired':
            state = replace(state, observedMonoTime=now - 200_000_001, validUntilMonoTime=now - 1)
        wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=state.observedMonoTime)
        wire.aolSafetyWire = encode_safety(state)
        vehicle = messaging.new_message('carState', valid=True, logMonoTime=now)
        vehicle.carState = cs
        intent = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=now)
        intent.aolIntentWire = encode_intent(IntentState('authority-card', tick + 1, now, now,
          now + 200_000_000, True, False, False, True, True))
        raw_companions = [intent.as_reader()]
        authority = messaging.new_message('starpilotCarState', valid=True, logMonoTime=now)
        authority.starpilotCarState.sourceCarStateMonoTime = now
        authority.starpilotCarState.lateralAuthorityUnavailable = False
        steering_companions = [authority.as_reader()]
        optional_receive = messaging.recv_one_or_none
        def receive(sock, raw_companions=raw_companions, steering_companions=steering_companions, optional_receive=optional_receive):
          if sock is sd.aol_intent_companion.sock:
            return raw_companions.pop(0) if raw_companions else None
          if sock is sd.steering_companion.sock:
            return steering_companions.pop(0) if steering_companions else None
          return optional_receive(sock)
        with patch.object(messaging, 'recv_one_or_none', side_effect=receive):
          axis, _, command = drive(tick, panda_state_message=pandas, safety_message=wire,
            car_state_message=vehicle, intent_message=intent, authority_message=authority)
        assert not raw_companions and not steering_companions
        if case in ('native_identity', 'expired') and tick == 2:
          assert not axis.lateralActive and not axis.longitudinalActive and not command.latActive and not command.longActive
        else:
          assert axis.lateralActive and axis.longitudinalActive and command.latActive and command.longActive
        frames = [pandas.as_reader(), wire.as_reader(), sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']]
        card.sm.update_msgs(now / 1e9, frames)
        fault = card.aol_disarming_fault(cs, int(card.sm.logMonoTime['onroadEvents']), now)
        card.aol_card_intent.update(cs, fault_active=fault, now_ns=now, standard_enabled=True)
        assert fault == (tick == 2 and case != 'older')
        assert card.aol_card_intent.allowed_latch == (tick != 2 or case == 'older')
        if tick == 2 and case == 'older':
          assert not healthy_transport(card.sm, cp, cs, state.axisSessionId, now)
  finally:
    fixture.doCleanups()
