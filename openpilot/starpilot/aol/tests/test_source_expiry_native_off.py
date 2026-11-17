from contextlib import ExitStack
from unittest import mock

from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR

import pytest

from openpilot.cereal import log, messaging
from openpilot.starpilot.aol.wire import encode_intent
from openpilot.starpilot.aol.tests.test_stock_location_family_intent import stock_family_callers


def callers(cleanup, family):
  if family != 'ev6_camera':
    return stock_family_callers(cleanup, family)
  factory = CarInterface.get_params

  def ev6_params(identity, *args, **kwargs):
    return factory(CAR.KIA_EV6 if identity == CAR.KIA_NIRO_EV_2ND_GEN else identity, *args, **kwargs)

  with mock.patch.object(CarInterface, 'get_params', side_effect=ev6_params):
    result = stock_family_callers(cleanup, 'canfd_camera')
  assert result[0].CP.carFingerprint == CAR.KIA_EV6
  assert result[0].CP.safetyConfigs[-1].safetyParam == 0x809
  return result


@pytest.mark.parametrize('family', ['ev6_camera', 'canfd_radar', 'canfd_lka', 'canfd_lka_alt', 'canfd_camera', 'canfd_ccnc_camera'])
@pytest.mark.parametrize('gas', [False, True])
@pytest.mark.parametrize('delivery', ['timeout', 'publication_expiry'])
def test_expired_stock_source_with_native_request_off_retains_intent_only(family, gas, delivery):
  with ExitStack() as cleanup:
    sd, drive, card, receive, state = callers(cleanup, family)
    sd.aol_settings = card.aol_card_intent.settings
    for tick in range(2, 5):
      axis, _, command = drive(tick, observed_state=state, gas=gas,
        native_request_long=False, panda_controls_allowed=False)
      assert axis.lateralActive and command.latActive and not command.longActive
      assert receive(axis.observedMonoTime, state=state,
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    deadline = axis.validUntilMonoTime
    accepted = sd.aol_last_intent
    held_intent = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=accepted.carStateLogMonoTime)
    held_intent.aolIntentWire = encode_intent(accepted)
    held_authority = messaging.new_message('starpilotCarState', valid=True, logMonoTime=sd.sm.logMonoTime['starpilotCarState'])
    held_authority.starpilotCarState = sd.sm['starpilotCarState']
    if delivery == 'timeout':
      paused, _, command = drive(10, timeout=True, observed_state=state, gas=gas,
        intent_message=held_intent, authority_message=held_authority, native_request_lat=False, allowed=False,
        native_request_long=False, panda_controls_allowed=False)
      assert not command.latActive and not command.longActive
    else:
      from dataclasses import replace
      from openpilot.starpilot.aol.runtime import current_native
      from openpilot.starpilot.aol.wire import encode_safety

      now = axis.observedMonoTime + 64_400_207
      original_native = current_native(sd.sm, sd.CP, now_ns=axis.observedMonoTime, axis_session_id=sd.aol_session_id)
      assert original_native is not None
      native = replace(original_native, observedMonoTime=now, validUntilMonoTime=now + 200_000_000,
        sourcePandaStatesMonoTime=now, requestedLateral=False, lateralAllowed=False,
        requestedLongitudinal=False, longitudinalAllowed=False)
      wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
      wire.aolSafetyWire = encode_safety(native)
      pandas = messaging.new_message('pandaStates', len(sd.CP.safetyConfigs), valid=True, logMonoTime=now)
      pandas.pandaStates = [panda.to_dict() for panda in sd.sm['pandaStates']]
      sd.sm.update_msgs(now / 1e9, [pandas.as_reader(), wire.as_reader()])
      with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now):
        sd.publish_axis_proof(sd.CS_prev)
      paused = sd.pm.messages['aolAxisState'].aolAxisState
    assert deadline < paused.observedMonoTime
    assert not paused.lateralActive and not paused.longitudinalActive
    assert paused.sourceCarStateMonoTime == 0
    assert str(paused.faultReason) == 'availabilityPause'
    assert not sd.aol_authority_lost
    assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
    assert receive(paused.observedMonoTime, state=state,
      axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert card.aol_card_intent.allowed_latch
    fresh, _, command = drive(11, observed_state=state, gas=gas,
      native_request_lat=False, allowed=False, native_request_long=False, panda_controls_allowed=False)
    assert not fresh.lateralActive and not command.latActive and not command.longActive
    assert not sd.aol_authority_lost
    assert receive(fresh.observedMonoTime, state=state,
      axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert card.aol_card_intent.allowed_latch
    for tick in (12, 13):
      recovered, _, command = drive(tick, observed_state=state, gas=gas,
        native_request_long=False, panda_controls_allowed=False)
      assert recovered.lateralActive and command.latActive and not command.longActive
      assert receive(recovered.observedMonoTime, state=state,
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      assert card.aol_card_intent.allowed_latch
    assert axis.validUntilMonoTime == deadline


@pytest.mark.parametrize('fault', ['requested_denial', 'receive_checks', 'relay', 'interrupt_rate',
                                  'native_session', 'native_invalid', 'producer', 'stale_baseline',
                                  'permanent_eps', 'invalid_can'])
def test_source_expiry_does_not_hide_faults_or_changed_provenance(fault):
  from dataclasses import replace
  from openpilot.starpilot.aol.runtime import current_native
  from openpilot.starpilot.aol.wire import encode_safety

  with ExitStack() as cleanup:
    sd, drive, card, receive, state = callers(cleanup, 'ev6_camera')
    sd.aol_settings = card.aol_card_intent.settings
    for tick in range(2, 5):
      axis, _, command = drive(tick, observed_state=state, gas=True,
        native_request_long=False, panda_controls_allowed=False)
      assert axis.lateralActive and command.latActive
      assert receive(axis.observedMonoTime, state=state,
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    old_native = current_native(sd.sm, sd.CP, now_ns=axis.observedMonoTime, axis_session_id=sd.aol_session_id)
    assert old_native is not None
    intent = sd.aol_last_intent
    if fault == 'producer':
      intent = replace(intent, producerSessionId='changed-producer')
    held = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=intent.carStateLogMonoTime)
    held.aolIntentWire = encode_intent(intent)
    authority = messaging.new_message('starpilotCarState', valid=True, logMonoTime=sd.sm.logMonoTime['starpilotCarState'])
    authority.starpilotCarState = sd.sm['starpilotCarState']
    tick = 30 if fault == 'stale_baseline' else 10
    now = axis.observedMonoTime + (tick - 4) * 10_000_000
    faults = ['interruptRateCan1'] if fault == 'interrupt_rate' else ['relayMalfunction'] if fault == 'relay' else []
    mask = sum(1 << log.PandaState.FaultType.schema.enumerants[name] for name in faults)
    native = replace(old_native, observedMonoTime=now, validUntilMonoTime=now + 200_000_000,
      sourcePandaStatesMonoTime=now, requestedLateral=fault == 'requested_denial', lateralAllowed=False,
      requestedLongitudinal=False, longitudinalAllowed=False,
      axisSessionId='changed-native-session' if fault == 'native_session' else old_native.axisSessionId,
      pandaInventory=tuple(replace(slot, faults=mask, safetyRxChecksInvalid=fault == 'receive_checks')
                           for slot in old_native.pandaInventory))
    wire = messaging.new_message('aolSafetyWire', 0, valid=fault != 'native_invalid', logMonoTime=now)
    wire.aolSafetyWire = encode_safety(native)
    pandas = messaging.new_message('pandaStates', len(sd.CP.safetyConfigs), valid=True, logMonoTime=now)
    pandas.pandaStates = [panda.to_dict() for panda in sd.sm['pandaStates']]
    for panda in pandas.pandaStates:
      panda.faults = faults
      panda.safetyRxChecksInvalid = fault == 'receive_checks'
    state.steerFaultPermanent = fault == 'permanent_eps'
    state.canValid = fault != 'invalid_can'
    paused, _, command = drive(tick, timeout=True, observed_state=state, gas=True, intent_message=held,
      authority_message=authority, safety_message=wire, panda_state_message=pandas,
      native_request_lat=False, allowed=False, native_request_long=False, panda_controls_allowed=False)
    assert not paused.lateralActive and not paused.longitudinalActive
    assert not command.latActive and not command.longActive
    assert not receive(now, state=state, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert not card.aol_card_intent.allowed_latch
