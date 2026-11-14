from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.gm.tests.test_bolt_factory_acc import factory_params
from opendbc.car.gm.values import DBC
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import EventName
from openpilot.starpilot.aol.tests import test_gm as gm_fixture
from openpilot.starpilot.aol.tests import test_ioniq_availability as caller_fixture
from openpilot.starpilot.aol.tests.test_gm_startup_main import parsed_packets
from openpilot.starpilot.aol.tests.test_gm_route18e_recovery import deliver_card
from openpilot.starpilot.aol.wire import decode_safety, encode_safety
from openpilot.starpilot.car.gm.aol import cold_main_readiness

NOW = 10_000_000_000


def callers(cleanup):
  params = Params()
  params.put_bool('AlwaysOnLateral', True, block=True)
  card = gm_fixture.TestGmAol.card(factory_params(alpha=False), params)
  cleanup.callback(card.vehicle_startup.close)
  case = caller_fixture.IoniqAvailabilityTests(methodName='runTest')
  cleanup.callback(case.doCleanups)
  sd, controls, drive = case._authority_callers(False, cp=card.CP.as_reader().as_builder(), genuine_events=True)
  card.slc_producer_session = 'authority-card'
  packer = CANPacker(DBC[card.CP.carFingerprint][Bus.pt])
  packets = parsed_packets(card.CP, packer, NOW, 0, 20., True)
  packets = [row for row in packets if not (row[0] == 0x370 and row[2] == 2)]
  packets.append(packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 2}))
  card.CI.update([(NOW - 1_000_000, packets)])
  cs = card.CI.update([(NOW, packets)])
  assert cs.canValid and not cs.cruiseState.enabled and cs.cruiseState.available
  assert card.CP.pcmCruise and not card.CP.openpilotLongitudinalControl
  card.aol_card_intent.update(cs, fault_active=False, now_ns=NOW)
  update_events = sd.update_events
  def waiting(state):
    update_events(state)
    sd.events.add(EventName.commIssue)
  sd.update_events = waiting
  return card, sd, controls, drive, cs, update_events


def test_stock_acc_idle_publication_gap_withdraws_output_without_critical_and_recovers():
  with OpenpilotPrefix(), ExitStack() as cleanup:
    card, sd, controls, drive, cs, healthy_update = callers(cleanup)
    axis, state, command = drive(0, observed_state=cs, native_request_lat=False, native_request_long=False, allowed=False)
    assert EventName.pcmDisable in sd.events.names
    assert str(axis.faultReason) == 'availabilityPause'
    assert not axis.lateralActive and not axis.longitudinalActive and not command.latActive and not command.longActive
    deliver_card(card, sd, controls, cs, 0)
    assert card.aol_card_intent.allowed_latch
    original_source = sd.aol_car_state_log_ns
    original_until = sd.aol_last_intent.validUntilMonoTime
    sd._expire_axis_source(cs, NOW + 30_903_542)
    assert sd.aol_car_state_log_ns == 0 and not sd.conditional_car_state_valid
    assert str(sd.aol_fault_reason) == 'availabilityPause'
    assert EventName.controlsMismatch not in sd.events.names
    assert not sd.aol_axis_decision.lateral_active and not sd.aol_axis_decision.longitudinal_active
    assert sd.aol_publication_retained_intent.carStateLogMonoTime == original_source
    assert sd.aol_publication_retained_intent.validUntilMonoTime == original_until
    with patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=NOW + 31_000_000):
      sd.publish_axis_proof(cs)
    deliver_card(card, sd, controls, cs, 4)
    assert card.aol_card_intent.allowed_latch
    sd.update_events = healthy_update
    axis, _, command = drive(8, observed_state=cs, native_request_lat=False, native_request_long=False, allowed=False)
    assert axis.desiredLateral and not axis.lateralActive and not command.latActive and not command.longActive
    deliver_card(card, sd, controls, cs, 8)
    assert card.aol_card_intent.allowed_latch
    axis, _, command = drive(9, observed_state=cs, native_request_lat=True, native_request_long=False, allowed=True)
    assert axis.lateralActive and command.latActive and not command.longActive


@pytest.mark.parametrize('bad', ['buttonCancel', 'wrongCarMode', 'controlsMismatch', 'main_off',
                                  'cruise_enabled', 'op_long', 'native_requested', 'intent_disarmed'])
def test_idle_readiness_does_not_admit_explicit_disarm_or_other_owner(bad):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    _, sd, _, drive, cs, _ = callers(cleanup)
    drive(0, observed_state=cs, native_request_lat=False, native_request_long=False, allowed=False)
    cp = sd.CP.as_builder()
    native = decode_safety(sd.sm['aolSafetyWire'])
    if bad in ('buttonCancel', 'wrongCarMode', 'controlsMismatch'):
      sd.events.add(getattr(EventName, bad))
    elif bad == 'main_off':
      cs.cruiseState.available = False
    elif bad == 'cruise_enabled':
      cs.cruiseState.enabled = True
    elif bad == 'op_long':
      cp.openpilotLongitudinalControl = True
    elif bad == 'native_requested':
      native = replace(native, requestedLateral=True, lateralAllowed=True)
    elif bad == 'intent_disarmed':
      sd.aol_last_intent = replace(sd.aol_last_intent, allowedLatch=False, lateralArmed=False)
    if bad == 'intent_disarmed':
      assert not sd.cold_gm_main_readiness(cs, sd.aol_last_intent, NOW)
    else:
      assert not cold_main_readiness(cp, cs, native, sd.events.to_msg())


@pytest.mark.parametrize('bad', ['invalid', 'expired', 'rx_fault', 'relay', 'session'])
def test_real_native_mismatch_remains_critical(bad):
  with OpenpilotPrefix(), ExitStack() as cleanup:
    _, sd, _, drive, cs, _ = callers(cleanup)
    drive(0, observed_state=cs, native_request_lat=False, native_request_long=False, allowed=False)
    native = decode_safety(sd.sm['aolSafetyWire'])
    if bad == 'expired':
      native = replace(native, observedMonoTime=NOW - 210_000_000, validUntilMonoTime=NOW - 10_000_000)
    elif bad == 'session':
      native = replace(native, axisSessionId='foreign')
    wire = messaging.new_message('aolSafetyWire', 0, valid=bad != 'invalid', logMonoTime=native.observedMonoTime)
    wire.aolSafetyWire = encode_safety(native)
    axis, _, command = drive(1, observed_state=cs, native_request_lat=False, native_request_long=False, allowed=False,
                             safety_message=wire, panda_fault=bad == 'rx_fault', relay_fault=bad == 'relay')
    assert EventName.controlsMismatch in sd.events.names
    assert str(axis.faultReason) == 'critical'
    assert not command.latActive and not command.longActive


def test_previously_active_lateral_loss_remains_critical():
  with OpenpilotPrefix(), ExitStack() as cleanup:
    _, sd, _, drive, cs, healthy_update = callers(cleanup)
    sd.update_events = healthy_update
    axis, _, command = drive(0, observed_state=cs, native_request_lat=True, native_request_long=False, allowed=True)
    assert axis.lateralActive and command.latActive and sd.aol_ever_active
    axis, _, command = drive(1, observed_state=cs, native_request_lat=True, native_request_long=False, allowed=False)
    assert not command.latActive and not command.longActive
    sd._expire_axis_source(cs, NOW + 41_000_000)
    assert EventName.controlsMismatch in sd.events.names
    assert str(sd.aol_fault_reason) == 'critical'


@pytest.mark.parametrize('bad', ['none', 'expired_source', 'producer', 'epoch', 'session', 'fault', 'main_off', 'disarmed'])
def test_zero_output_startup_metadata_has_original_bounded_source(bad):
  from opendbc.car.structs import car
  from openpilot.selfdrive.selfdrived.events import Events
  from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
  from openpilot.starpilot.aol.tests.test_gm_cold_main_recovery import snapshot
  cp = factory_params(alpha=False)
  cp.alternativeExperience = 32
  cs = car.CarState(canValid=True, gearShifter=car.CarState.GearShifter.drive, vEgo=20.)
  cs.cruiseState.available = True
  feedback = TransportPauseFeedback(cp)
  def sample(now, source):
    sm = snapshot(cp, now, kind='soft', faults=int(bad == 'fault' and source == 0))
    axis = sm['aolAxisState'].as_builder()
    axis.sourceCarStateMonoTime = source
    if source == 0 and bad == 'producer':
      axis.intentProducerSessionId = 'foreign'
    if source == 0 and bad == 'epoch':
      axis.intentIntegrityEpoch += 1
    message = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
    message.aolAxisState = axis
    events = Events()
    events.add(EventName.commIssue)
    events.add(EventName.pcmDisable)
    event = messaging.new_message('onroadEvents', len(events), valid=True, logMonoTime=now)
    event.onroadEvents = events.to_msg()
    messages = [message.as_reader(), event.as_reader()]
    if source == 0 and bad == 'session':
      native = replace(decode_safety(sm['aolSafetyWire']), axisSessionId='foreign')
      wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
      wire.aolSafetyWire = encode_safety(native)
      messages.append(wire.as_reader())
    for message in messages:
      service = message.which()
      sm.data[service] = getattr(message, service)
      sm.logMonoTime[service] = message.logMonoTime
      sm.valid[service] = message.valid
    return sm
  sm = sample(NOW, NOW)
  assert not feedback.observe(sm, cp, cs, NOW, latched=True, intent_producer_session='producer')
  assert feedback.cold_main_source_ns == NOW
  if bad == 'main_off':
    cs.cruiseState.available = False
  stamp = NOW + (200_000_001 if bad == 'expired_source' else 79_154_636)
  sm = sample(stamp, 0)
  rejected = feedback.observe(sm, cp, cs, stamp, latched=bad != 'disarmed', intent_producer_session='producer')
  assert feedback.cold_main_source_ns == NOW
  if bad == 'none':
    assert not rejected and feedback.temporary_host_qualified
    # A second zero-source sample must not renew the original source deadline.
    stamp = NOW + 190_000_000
    assert not feedback.observe(sample(stamp, 0), cp, cs, stamp, latched=True, intent_producer_session='producer')
    assert feedback.cold_main_source_ns == NOW
    stamp = NOW + 200_000_001
    assert feedback.observe(sample(stamp, 0), cp, cs, stamp, latched=True, intent_producer_session='producer')
  elif bad == 'disarmed':
    assert not feedback.temporary_host_qualified
  else:
    assert rejected
