"""Bolt routes 25f676999e/f3031d6bc7: host gaps cannot become native faults.

Production SelfdriveD/Controls callers and serialized controlled native peers;
this does not replay the device scheduler or drive a vehicle.
"""
from contextlib import ExitStack
from unittest import mock

import pytest
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.values import CAR
from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import EventName
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_input_deadline_continuity import intent

BASE = 10_000_000_000


def callers(cleanup, full=False, candidate=CAR.CHEVROLET_BOLT_CC_2018_2021):
  cleanup.enter_context(OpenpilotPrefix())
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  cleanup.callback(case.doCleanups)
  sd, controls, step = case._authority_callers(full, cp=params(candidate, pedal=True, camera=True), genuine_events=True)
  sd.aol_settings = read_settings(sd.params)
  return sd, controls, step


def test_route29_missing_card_wire_keeps_only_original_intent_lease():
  with ExitStack() as cleanup:
    sd, controls, step = callers(cleanup)
    for tick in (0, 1):
      _, _, command = step(tick, gas=True, native_request_long=False)
      assert command.latActive and not command.longActive
    original = sd.sm.update_msgs
    def without_card(stamp, messages):
      return original(stamp, [m for m in messages if m.which() not in ('aolIntentWire', 'starpilotCarState')])
    with mock.patch.object(sd.sm, 'update_msgs', side_effect=without_card):
      for tick in (4, 7, 10, 13):
        axis, _, command = step(tick, timeout=True, gas=True, native_request_long=False,
                               intent_message=intent(BASE + 10_000_000, 2), offset_ns=1_500_000)
        assert not axis.lateralActive and not command.latActive and not command.longActive
        assert not sd.aol_authority_lost and EventName.controlsMismatch not in sd.events.names
        assert str(axis.faultReason) == 'availabilityPause' and axis.sourceCarStateMonoTime == 0
      assert not sd.sm.alive['aolIntentWire']
    axis, _, command = step(14, gas=True, native_request_long=False)
    assert axis.lateralActive and command.latActive and not command.longActive
    assert not sd.aol_authority_lost
    with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=axis.validUntilMonoTime + 1):
      expired, _ = controls.state_control()
    assert not expired.latActive and not expired.longActive


@pytest.mark.parametrize('candidate', (CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021,
                                      CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL))
@pytest.mark.parametrize('pedal', ('brake', 'regen'))
def test_route28_accepted_lateral_pedal_cutoff_retains_intent_without_output(candidate, pedal):
  with ExitStack() as cleanup:
    sd, _, step = callers(cleanup, full=True, candidate=candidate)
    from opendbc.car.car_helpers import interfaces
    from openpilot.selfdrive.car.card import Car
    cp = params(candidate, pedal=True, camera=True)
    card = Car(CI=interfaces[candidate](cp), RI=interfaces[candidate].RadarInterface(cp))
    cleanup.callback(card.vehicle_startup.close)
    assert card.CP.to_dict() == sd.CP.to_dict()
    card.slc_producer_session = 'authority-card'
    assert card.aol_card_intent is not None
    card.aol_card_intent.allowed_latch = True
    def receive(now):
      frames = [sd.pm.messages[name] for name in ('aolAxisState', 'onroadEvents', 'selfdriveState')]
      for service in ('pandaStates', 'aolSafetyWire', 'deviceState'):
        msg = messaging.new_message(service, len(sd.sm[service]) if service in ('pandaStates', 'aolSafetyWire') else None,
                                    valid=sd.sm.valid[service], logMonoTime=sd.sm.logMonoTime[service])
        setattr(msg, service, sd.sm[service])
        frames.append(msg.as_reader())
      card.sm.update_msgs(now / 1e9, frames)
      return card.aol_disarming_fault(sd.CS_prev, int(card.sm.logMonoTime['onroadEvents']), now)
    for tick in (0, 1):
      _, host, command = step(tick)
      assert host.enabled and command.longActive
      assert not receive(BASE + tick * 10_000_000)
    state = fixture.car_state(brake=pedal == 'brake')
    state.regenBraking = pedal == 'regen'
    axis, host, command = step(2, state_stamp=BASE + 20_000_000, offset_ns=5_042_623, observed_state=state,
                               intent_message=intent(BASE + 20_000_000, 3))
    assert command.latActive and not command.longActive and not host.enabled
    assert axis.sourceCarStateMonoTime == BASE + 20_000_000
    assert not receive(BASE + 25_042_623)
    # Exact source/publication gap shape from route 28. The last native FULL
    # receipt may still be in flight after the host has already withdrawn LONG.
    sd.car_state_samples = ()
    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=BASE + 52_866_472):
      sd.publish_axis_proof(sd.CS_prev)
    paused = sd.pm.messages['aolAxisState'].aolAxisState
    assert str(paused.faultReason) == 'availabilityPause'
    assert not paused.lateralActive and not paused.longitudinalActive and paused.sourceCarStateMonoTime == 0
    assert not sd.aol_authority_lost and EventName.controlsMismatch not in sd.events.names
    from openpilot.starpilot.car.gm.aol import gm_lateral_pedal_source_zero
    from openpilot.starpilot.aol.runtime import current_native
    native = current_native(sd.sm, sd.CP, now_ns=BASE + 52_866_472, axis_session_id=sd.aol_session_id)
    for field, value in (('accFaulted', True), ('steerFaultTemporary', True), ('steerFaultPermanent', True),
                         ('gearShifter', 'unknown'), ('canValid', False)):
      invalid = sd.CS_prev.as_builder()
      setattr(invalid, field, value)
      assert not gm_lateral_pedal_source_zero(card.aol_transport_feedback, card.CP, invalid, paused,
        native, BASE + 52_866_472, producer='authority-card')
    assert not receive(BASE + 52_866_472)
    axis, host, command = step(6, observed_state=state, native_request_long=False, panda_controls_allowed=False)
    assert command.latActive and not command.longActive and not host.enabled and axis.lateralActive


@pytest.mark.parametrize('fault', ('native', 'can', 'cancel', 'new_pedal', 'intent_expired', 'model'))
def test_held_pedal_never_hides_real_faults_or_admits_a_new_edge(fault):
  with ExitStack() as cleanup:
    sd, _, step = callers(cleanup, full=True)
    step(0)
    state = fixture.car_state(brake=True)
    step(1, observed_state=state)
    sd.car_state_samples = ()
    cs = sd.CS_prev
    if fault == 'native':
      sd.sm.valid['aolSafetyWire'] = False
    elif fault == 'can':
      cs = cs.as_builder()
      cs.canValid = False
    elif fault == 'cancel':
      sd.events.add(EventName.buttonCancel)
    elif fault == 'new_pedal':
      cs = cs.as_builder()
    elif fault == 'model':
      sd.sm.valid['modelV2'] = False
    now = BASE + (220_000_001 if fault == 'intent_expired' else 42_866_472)
    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now):
      sd.publish_axis_proof(cs)
    axis = sd.pm.messages['aolAxisState'].aolAxisState
    assert not axis.lateralActive and not axis.longitudinalActive
    assert sd.aol_authority_lost and EventName.controlsMismatch in sd.events.names
