"""Real GM parser/Card sources with controlled native peers; no transport claim."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.gm.bolt_cc import button_bytes
from opendbc.car.gm.gmcan import pedal_crc
from opendbc.car.gm.tests.test_bolt_cc import feed
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.values import CAR, DBC, CruiseButtons
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.runtime import decide_axes
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety

NOW = 10_000_000_000


class Peers:
  def __init__(self, messages):
    self.messages = {m.which(): m for m in messages}
    self.logMonoTime = {n: int(m.logMonoTime) for n, m in self.messages.items()}
    self.seen = dict.fromkeys(self.messages, True)
    self.valid = dict(self.seen)
    self.alive = dict(self.seen)

  def __getitem__(self, name):
    return getattr(self.messages[name], name)


def parse(card, now, gesture=None, counter=0, *, belt_unlatched=False, packets_only=False):
  packer = CANPacker(DBC[card.CP.carFingerprint][Bus.pt])
  _, packets = feed(SimpleNamespace(update=lambda _: None), packer, now, counter=counter,
                    active=False, speed=20., brake=gesture == 'brake', regen=gesture == 'paddle')
  packets = [p for p in packets if p[0] not in (0x1E1, 0x1F5)]
  packets += [packer.make_can_msg('BCMBlindSpotMonitor', 0, {}),
              packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 6}),
              (0x1E1, button_bytes(CruiseButtons.CANCEL if gesture == 'cancel' else CruiseButtons.UNPRESS, counter), 0)]
  if belt_unlatched:
    # Real physical BCMDoorBeltStatus (0x12A), not a CarState assignment.
    packets = [p for p in packets if p[0] != 0x12A]
    packets.append(packer.make_can_msg('BCMDoorBeltStatus', 0, {'LeftSeatBelt': 0}))
  sensor = bytearray(packer.make_can_msg('GAS_SENSOR', 0,
                     {'INTERCEPTOR_GAS': 0., 'INTERCEPTOR_GAS2': 0., 'STATE': 0, 'COUNTER_PEDAL': counter})[1])
  sensor[5] = pedal_crc(sensor)
  packets.append((0x201, bytes(sensor), 0))
  if card.CP.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and int(card.CP.safetyConfigs[0].safetyParam) == 0x1CD:
    if now % 40_000_000 < 10_000_000:
      packets.append(packer.make_can_msg('EBCMFrictionBrakeCmd', 2, {'RollingCounter': (now // 40_000_000) % 4}))
  if packets_only:
    return packets
  if now == NOW:
    card.CI.update([(now - 1_000_000, packets)])
  cs = card.CI.update([(now, packets)])
  assert cs.canValid and not cs.canTimeout
  assert cs.brakePressed == (gesture == 'brake')
  assert cs.regenBraking == (gesture == 'paddle')
  assert cs.seatbeltUnlatched == belt_unlatched
  card._aol_can_observation = (cs, now, now, now)
  return cs


def peers(cp, producer, now, *, controls=True, native=None, sequence=1, long_active=True):
  config = cp.safetyConfigs[0]
  if native is None:
    slot = PandaSlot(0, 'gm-unit', config.safetyModel.raw, config.safetyParam, 32, True)
    native = SafetyState(1, True, now, now + 200_000_000, config.safetyModel.raw, config.safetyParam,
                         True, True, True, True, 'gm-unit', 'gm-session',
                         sourcePandaStatesMonoTime=now, pandaInventory=(slot,))
  wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=native.observedMonoTime)
  wire.aolSafetyWire = encode_safety(native)
  pandas = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now)
  p = pandas.pandaStates[0]
  p.safetyModel, p.safetyParam, p.alternativeExperience = config.safetyModel, config.safetyParam, 32
  p.controlsAllowed = controls
  axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
  a = axis.aolAxisState
  a.sessionId = a.faultSessionId = 'gm-session'
  a.sequence, a.qualified, a.faultReason = sequence, True, 'none'
  a.observedMonoTime, a.validUntilMonoTime, a.sourceCarStateMonoTime = now, now + 30_000_000, now
  a.desiredLateral = a.lateralActive = a.nativeAcknowledged = True
  a.desiredLongitudinal = a.longitudinalActive = long_active
  a.intentProducerSessionId, a.intentIntegrityEpoch = producer, 1
  events = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now)
  return Peers([wire.as_reader(), pandas.as_reader(), axis.as_reader(), events.as_reader()]), native


def baseline():
  from openpilot.starpilot.aol.tests.test_gm import TestGmAol
  settings = Params()
  settings.put_bool('AlwaysOnLateral', True, block=True)
  settings.put_bool('AlphaLongitudinalEnabled', True, block=True)
  cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
  card = TestGmAol.card(cp, settings)
  cs = parse(card, NOW)
  card.aol_card_intent.update(cs, fault_active=False, now_ns=NOW)
  assert card.aol_card_intent.allowed_latch
  sm, native = peers(card.CP, card.slc_producer_session, NOW)
  feedback = TransportPauseFeedback(card.CP)
  assert not feedback.observe(sm, card.CP, cs, NOW, latched=True,
                              intent_producer_session=card.slc_producer_session)
  assert feedback.session == 'gm-session' and feedback.healthy_inventory
  return card, feedback, native


@pytest.mark.parametrize('health_allowed', [False, True])
def test_native_long_withdrawal_before_parser_keeps_existing_card_intent(health_allowed):
  with OpenpilotPrefix():
    card, feedback, prior = baseline()
    now = NOW + 10_000_000
    cs = parse(card, now, counter=1)
    native = replace(prior, observedMonoTime=now, validUntilMonoTime=now + 200_000_000,
      longitudinalAllowed=False, pandaInventory=(replace(prior.pandaInventory[0], controlsAllowed=health_allowed),))
    sm, _ = peers(card.CP, card.slc_producer_session, now, controls=health_allowed, native=native, sequence=2)
    # The host's last full-axis result precedes the native paddle/brake edge.
    reset = feedback.observe(sm, card.CP, cs, now, latched=True,
                             intent_producer_session=card.slc_producer_session)
    card.aol_card_intent.update(cs, fault_active=reset, now_ns=now)
    assert not reset and not feedback.failed and card.aol_card_intent.allowed_latch
    assert not native.longitudinalAllowed


@pytest.mark.parametrize('gesture', ['brake', 'paddle', 'cancel'])
def test_real_physical_withdrawal_retains_latch_with_held_long_wire(gesture):
  with OpenpilotPrefix():
    card, feedback, native = baseline()
    now = NOW + 10_000_000
    cs = parse(card, now, gesture, counter=1)
    source = card.aol_gm_withdrawal_source(cs, now)
    assert source == now
    sm, _ = peers(card.CP, card.slc_producer_session, now, controls=False, native=native, sequence=2)
    reset = feedback.observe(sm, card.CP, cs, now, latched=True, gm_withdrawal_source_ns=source,
                             intent_producer_session=card.slc_producer_session)
    card.aol_card_intent.update(cs, fault_active=reset, now_ns=now)
    assert not reset and not feedback.failed and card.aol_card_intent.allowed_latch
    assert feedback.gm_withdrawal_ns == source
    # The prior LONG wire and latest raw withdrawal are untouched. Ordinary
    # runtime keeps LONG off even though accepted driver LAT intent survives.
    intent = SimpleNamespace(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False)
    decision = decide_axes(standard_lateral=False, standard_longitudinal=False, intent=intent,
      native=native, car_state=cs, initialized=True, model_ready=True, no_entry=False,
      immediate_disable=False, dm_lockout=False, pause_brake_mps=5.)
    assert decision.lateral_active and not decision.longitudinal_active
    assert native.requestedLongitudinal and native.longitudinalAllowed
    assert native.validUntilMonoTime == NOW + 200_000_000
    # Held and release samples retain one original occurrence; the actual
    # native LAT-only acknowledgement ends the exception on the normal path.
    for offset, physical in ((20_000_000, gesture), (30_000_000, None)):
      later = NOW + offset
      cs = parse(card, later, physical, counter=2)
      sm, _ = peers(card.CP, card.slc_producer_session, later, controls=False, native=native,
                    sequence=offset // 10_000_000 + 1, long_active=False)
      assert not feedback.observe(sm, card.CP, cs, later, latched=True,
        gm_withdrawal_source_ns=card.aol_gm_withdrawal_source(cs, later), intent_producer_session=card.slc_producer_session)
      assert feedback.gm_withdrawal_ns == source
    lat_only = replace(native, observedMonoTime=NOW + 40_000_000, validUntilMonoTime=NOW + 240_000_000,
                       requestedLongitudinal=False, longitudinalAllowed=False,
                       pandaInventory=(replace(native.pandaInventory[0], controlsAllowed=False),))
    sm, _ = peers(card.CP, card.slc_producer_session, NOW + 40_000_000, controls=False,
                  native=lat_only, sequence=5, long_active=False)
    assert not feedback.observe(sm, card.CP, cs, NOW + 40_000_000, latched=True,
                                intent_producer_session=card.slc_producer_session)
    assert feedback.gm_withdrawal_ns == 0


@pytest.mark.parametrize('fault', ['no_source', 'stale_source', 'future_source', 'producer', 'epoch',
  'session', 'serial', 'inventory', 'owner', 'config', 'rx_fault', 'heartbeat', 'lat_denied', 'no_lat',
  'native_expired', 'can_invalid', 'eps', 'event', 'axis_expired', 'sequence', 'main',
  'seatbelt', 'seatbelt_event', 'door', 'gear'])
def test_physical_withdrawal_keeps_identity_freshness_and_health_vetoes(fault):
  with OpenpilotPrefix():
    card, feedback, native = baseline()
    now = NOW + 10_000_000
    cs = parse(card, now, 'brake', counter=1)
    source = card.aol_gm_withdrawal_source(cs, now)
    if fault == 'no_source':
      source = 0
    elif fault == 'stale_source':
      source = now - 30_000_001
    elif fault == 'future_source':
      source = now + 1
    elif fault == 'session':
      native = replace(native, axisSessionId='other')
    elif fault == 'serial':
      native = replace(native, pandaSerial='other')
    elif fault == 'inventory':
      native = replace(native, pandaInventory=(replace(native.pandaInventory[0], hardwareSerial='other'),))
    elif fault == 'owner':
      card.CP.alternativeExperience = 0
    elif fault == 'no_lat':
      native = replace(native, requestedLateral=False)
    elif fault == 'native_expired':
      native = replace(native, validUntilMonoTime=now - 1)
    elif fault == 'main':
      cs.cruiseState.available = False
    elif fault == 'lat_denied':
      native = replace(native, lateralAllowed=False)
    elif fault == 'can_invalid':
      cs.canValid = False
    elif fault == 'eps':
      cs.steerFaultPermanent = True
    elif fault == 'seatbelt':
      cs.seatbeltUnlatched = True
    elif fault == 'door':
      cs.doorOpen = True
    elif fault == 'gear':
      from opendbc.car.structs import car
      cs.gearShifter = car.CarState.GearShifter.park
    sm, _ = peers(card.CP, card.slc_producer_session, now, controls=False, native=native, sequence=2)
    axis = sm.messages['aolAxisState'].as_builder()
    pandas = sm.messages['pandaStates'].as_builder()
    events = sm.messages['onroadEvents'].as_builder()
    if fault == 'producer':
      axis.aolAxisState.intentProducerSessionId = 'other'
    elif fault == 'epoch':
      axis.aolAxisState.intentIntegrityEpoch = 2
    elif fault == 'sequence':
      axis.aolAxisState.sequence = 1
    elif fault == 'axis_expired':
      axis.aolAxisState.validUntilMonoTime = now - 1
    elif fault == 'config':
      pandas.pandaStates[0].safetyParam ^= 1
    elif fault == 'rx_fault':
      pandas.pandaStates[0].safetyRxChecksInvalid = True
    elif fault == 'heartbeat':
      pandas.pandaStates[0].heartbeatLost = True
    elif fault == 'seatbelt_event':
      events.init('onroadEvents', 1)
      events.onroadEvents[0].name, events.onroadEvents[0].softDisable = 'seatbeltNotLatched', True
    elif fault == 'event':
      events.init('onroadEvents', 1)
      events.onroadEvents[0].name, events.onroadEvents[0].immediateDisable = 'controlsMismatch', True
    sm = Peers([sm.messages['aolSafetyWire'], pandas.as_reader(), axis.as_reader(), events.as_reader()])
    reset = feedback.observe(sm, card.CP, cs, now, latched=True, gm_withdrawal_source_ns=source,
                             intent_producer_session=card.slc_producer_session)
    if fault == 'seatbelt':
      # Keep the original input, but correct its demonstrated contract: an
      # unlatched belt locks LONG and does not erase accepted LAT intent.
      card.aol_card_intent.update(cs, fault_active=reset, now_ns=now)
      assert not reset and not feedback.failed and card.aol_card_intent.allowed_latch
      intent = SimpleNamespace(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False)
      decision = decide_axes(standard_lateral=False, standard_longitudinal=True, intent=intent,
        native=native, car_state=cs, initialized=True, model_ready=True, no_entry=False,
        immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
      assert decision.lateral_active
      assert not decision.desired_longitudinal and not decision.longitudinal_active
    else:
      assert reset
      assert feedback.failed


def test_held_and_duplicate_samples_cannot_extend_withdrawal_deadline():
  with OpenpilotPrefix():
    card, feedback, native = baseline()
    occurrence = NOW + 10_000_000
    for tick in range(1, 23):
      now = NOW + tick * 10_000_000
      cs = parse(card, now, 'brake', counter=tick % 4)
      if tick > 1:
        native = replace(native, observedMonoTime=now, validUntilMonoTime=now + 200_000_000)
      sm, _ = peers(card.CP, card.slc_producer_session, now, controls=False, native=native,
                    sequence=tick + 1, long_active=tick == 1)
      source = card.aol_gm_withdrawal_source(cs, now)
      for _ in range(2):
        failed = feedback.observe(sm, card.CP, cs, now, latched=True, gm_withdrawal_source_ns=source,
                                  intent_producer_session=card.slc_producer_session)
        assert failed == (tick == 22)
        assert feedback.gm_withdrawal_ns == occurrence
    assert feedback.failed


@pytest.mark.parametrize('fault', ['different_cs', 'old_receipt', 'future_receipt', 'stale_parser', 'future_parser'])
def test_physical_receipt_requires_same_parsed_batch_and_separate_clock_freshness(fault):
  with OpenpilotPrefix():
    card, _, _ = baseline()
    now = NOW + 10_000_000
    cs = parse(card, now, 'brake', counter=1)
    assert card.aol_gm_withdrawal_source(cs, now) == now
    if fault == 'different_cs':
      card._aol_can_observation = (cs.as_reader().as_builder(), now, now, now)
    elif fault == 'old_receipt':
      card._aol_can_observation = (cs, now - 30_000_001, now, now)
    elif fault == 'future_receipt':
      card._aol_can_observation = (cs, now + 1, now, now)
    elif fault == 'stale_parser':
      card._aol_can_observation = (cs, now, now + 30_000_001, now + 30_000_001)
    elif fault == 'future_parser':
      card._aol_can_observation = (cs, now, now - 1, now)
    assert card.aol_gm_withdrawal_source(cs, now) == 0


@pytest.mark.parametrize('owner', ['qualified', 'unmarked', 'wrong_profile'])
def test_actual_card_state_update_captures_boot_clock_only_for_physical_gm_owner(owner):
  # Stop immediately after the production clock gate. The real joined Card.step
  # transport cases separately exercise installation/consumption of the receipt.
  class ClockBoundaryReached(Exception):
    pass

  def stop_after_clock(*_):
    raise ClockBoundaryReached

  with OpenpilotPrefix():
    card, _, _ = baseline()
    cs = parse(card, NOW + 10_000_000, 'brake', counter=1)
    if owner == 'unmarked':
      card.CP.alternativeExperience = 0
    elif owner == 'wrong_profile':
      card.CP.flags = 0
    with patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw', return_value=[]), \
         patch('openpilot.selfdrive.car.card.can_capnp_to_list', return_value=[(NOW + 10_000_000, [])]), \
         patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=NOW + 10_000_000), \
         patch('openpilot.selfdrive.car.card.time.clock_gettime_ns', return_value=NOW + 10_000_000) as clock, \
         patch.object(card.CI, 'update', return_value=cs), \
         patch.object(card, 'observe_ioniq6_long_authority', side_effect=stop_after_clock):
      with pytest.raises(ClockBoundaryReached):
        card.state_update()
      if owner == 'qualified':
        from openpilot.selfdrive.car.card import time
        clock.assert_called_once_with(time.CLOCK_BOOTTIME)
      else:
        clock.assert_not_called()


@pytest.mark.parametrize('fault', [None, 'unmarked', 'wrong_owner', 'unlatched', 'unarmed', 'source_mismatch',
  'source_expired', 'future_source', 'no_cancel', 'can_invalid', 'eps', 'no_native', 'lat_denied', 'native_inventory'])
def test_gm_cancel_exempts_only_current_armed_lateral_no_entry(fault):
  from openpilot.starpilot.aol.wire import IntentState
  from openpilot.starpilot.car.gm.aol import physical_cancel_lateral_allowed
  from openpilot.selfdrive.selfdrived.events import ET, EVENTS, EventName
  from openpilot.starpilot.nostalgia import aol_no_entry
  with OpenpilotPrefix():
    card, _, native = baseline()
    now = NOW + 10_000_000
    cs = parse(card, now, 'cancel', counter=1)
    intent = IntentState(card.slc_producer_session, 2, now, now, now + 30_000_000, True, False, False, True, True)
    source = now
    if fault == 'unmarked':
      card.CP.alternativeExperience = 0
    elif fault == 'wrong_owner':
      card.CP.flags = 0
    elif fault == 'unlatched':
      intent = replace(intent, allowedLatch=False)
    elif fault == 'unarmed':
      intent = replace(intent, lateralArmed=False)
    elif fault == 'source_mismatch':
      source -= 1
    elif fault == 'source_expired':
      now += 30_000_001
    elif fault == 'future_source':
      source += 1
    elif fault == 'no_cancel':
      cs.buttonEvents = []
    elif fault == 'can_invalid':
      cs.canValid = False
    elif fault == 'eps':
      cs.steerFaultPermanent = True
    elif fault == 'no_native':
      native = None
    elif fault == 'lat_denied':
      native = replace(native, lateralAllowed=False)
    elif fault == 'native_inventory':
      native = replace(native, pandaInventory=(replace(native.pandaInventory[0], hardwareSerial='other'),))
    allowed = physical_cancel_lateral_allowed(card.CP, cs, intent, native, source_ns=source, now_ns=now)
    assert allowed == (fault is None)
    assert aol_no_entry([EventName.buttonCancel], cs, paddle_only_cancel=False)
    assert aol_no_entry([EventName.buttonCancel], cs, paddle_only_cancel=False, allow_lateral_cancel=allowed) == (fault is not None)
    assert ET.USER_DISABLE in EVENTS[EventName.buttonCancel]
    assert aol_no_entry([EventName.buttonCancel, EventName.doorOpen], cs,
                        paddle_only_cancel=False, allow_lateral_cancel=allowed) == (not allowed)


def test_native_lat_only_after_long_belt_loss_keeps_saved_lateral_intent():
  from openpilot.starpilot.aol.intent import disarming_fault
  from openpilot.starpilot.nostalgia import aol_no_entry
  from openpilot.selfdrive.selfdrived.events import Events, EventName
  with OpenpilotPrefix():
    card, feedback, native = baseline()
    for tick, unlatched in ((1, True), (2, True), (3, False)):
      now = NOW + tick * 10_000_000
      cs = parse(card, now, counter=tick)
      cs.seatbeltUnlatched = unlatched
      native = replace(native, observedMonoTime=now, validUntilMonoTime=now + 200_000_000,
        requestedLongitudinal=False, longitudinalAllowed=False,
        pandaInventory=(replace(native.pandaInventory[0], controlsAllowed=False),))
      sm, _ = peers(card.CP, card.slc_producer_session, now, controls=False, native=native,
                    sequence=tick + 1, long_active=False)
      events = Events()
      if unlatched:
        events.add(EventName.seatbeltNotLatched)
        wire_events = sm.messages['onroadEvents'].as_builder()
        wire_events.onroadEvents = events.to_msg()
        sm = Peers([sm.messages['aolSafetyWire'], sm.messages['pandaStates'],
                    sm.messages['aolAxisState'], wire_events.as_reader()])
      reset = feedback.observe(sm, card.CP, cs, now, latched=True,
                               intent_producer_session=card.slc_producer_session)
      card.aol_card_intent.update(cs, fault_active=reset or disarming_fault(events.to_msg(), cs), now_ns=now)
      assert not reset and not feedback.failed and card.aol_card_intent.allowed_latch
      allowed, pause_lat, pause_long = card.aol_card_intent.output(cs)
      decision = decide_axes(standard_lateral=True, standard_longitudinal=unlatched,
        intent=SimpleNamespace(allowedLatch=allowed, pauseLateral=pause_lat, pauseLongitudinal=pause_long),
        native=native, car_state=cs, initialized=True, model_ready=True,
        no_entry=aol_no_entry(events.names, cs, paddle_only_cancel=False),
        immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
      assert decision.lateral_active
      assert not decision.desired_longitudinal and not decision.longitudinal_active
      assert feedback.gm_withdrawal_ns == 0


@pytest.mark.parametrize('fault', [None, 'stale_source', 'future_source', 'future_observation',
  'excessive_lease', 'expired', 'no_expiry'])
def test_real_card_published_cancel_intent_preserves_producer_lease_and_freshness(fault):
  from openpilot.starpilot.aol.runtime import current_intent
  from openpilot.starpilot.aol.wire import decode_intent
  from openpilot.starpilot.car.gm.aol import physical_cancel_lateral_allowed

  class PublishedCarState(Exception):
    pass

  with OpenpilotPrefix():
    card, _, native = baseline()
    now = NOW + 10_000_000
    cs = parse(card, now, 'cancel', counter=1)
    published = {}
    real_new_message = messaging.new_message

    def stamped_message(service=None, size=None, **kwargs):
      return real_new_message(service, size, **({'logMonoTime': now} | kwargs))

    def capture(service, message):
      wire = message if isinstance(message, bytes) else message.to_bytes()
      published[service] = messaging.log_from_bytes(wire)
      if service == 'carState':
        raise PublishedCarState

    with patch('openpilot.selfdrive.car.card.messaging.new_message', side_effect=stamped_message), \
         patch.object(card.pm, 'send', side_effect=capture):
      with pytest.raises(PublishedCarState):
        card.state_publish(cs, None)
    source = int(published['carState'].logMonoTime)
    cs = published['carState'].carState
    intent = decode_intent(published['aolIntentWire'].aolIntentWire)
    assert intent is not None
    assert intent.carStateLogMonoTime == intent.observedMonoTime == source == now
    assert intent.validUntilMonoTime - intent.observedMonoTime == 200_000_000
    admitted = current_intent(Peers([published['aolIntentWire']]), car_state_ns=source, now_ns=now)
    assert admitted == intent
    # Faults modify only the negative cases after the actual producer boundary.
    if fault == 'stale_source':
      now += 30_000_001
    elif fault == 'future_source':
      source += 1
    elif fault == 'future_observation':
      intent = replace(intent, observedMonoTime=now + 1)
    elif fault == 'excessive_lease':
      intent = replace(intent, validUntilMonoTime=intent.observedMonoTime + 200_000_001)
    elif fault == 'expired':
      intent = replace(intent, validUntilMonoTime=now - 1)
    elif fault == 'no_expiry':
      intent = replace(intent, validUntilMonoTime=0)
    assert physical_cancel_lateral_allowed(card.CP, cs, intent, native,
      source_ns=source, now_ns=now) == (fault is None)


@pytest.mark.parametrize('gesture', ['brake', 'paddle', 'cancel'])
def test_real_belt_loss_with_physical_withdrawal_retains_only_existing_lateral_intent(gesture):
  from openpilot.starpilot.aol.intent import disarming_fault
  from openpilot.starpilot.nostalgia import aol_no_entry
  from openpilot.selfdrive.selfdrived.events import Events, EventName
  with OpenpilotPrefix():
    card, feedback, native = baseline()
    occurrence = NOW + 10_000_000
    for tick, unlatched, physical in ((1, True, gesture), (2, True, gesture),
                                     (3, True, None), (4, False, None)):
      now = NOW + tick * 10_000_000
      cs = parse(card, now, physical, counter=tick % 4, belt_unlatched=unlatched)
      sm, _ = peers(card.CP, card.slc_producer_session, now, controls=False, native=native,
                    sequence=tick + 1, long_active=tick == 1)
      events = Events()
      # At the physical boundary Card consumes the prior event publication;
      # the following call consumes the real seatbelt soft-disable classification.
      if unlatched and tick > 1:
        events.add(EventName.seatbeltNotLatched)
      event_wire = sm.messages['onroadEvents'].as_builder()
      event_wire.onroadEvents = events.to_msg()
      sm = Peers([sm.messages['aolSafetyWire'], sm.messages['pandaStates'],
                  sm.messages['aolAxisState'], event_wire.as_reader()])
      source = card.aol_gm_withdrawal_source(cs, now)
      assert source == (now if physical is not None and (gesture != 'cancel' or tick == 1) else 0)
      reset = feedback.observe(sm, card.CP, cs, now, latched=True,
        gm_withdrawal_source_ns=source, intent_producer_session=card.slc_producer_session)
      card.aol_card_intent.update(cs, fault_active=reset or disarming_fault(events.to_msg(), cs), now_ns=now)
      assert not reset and not feedback.failed and card.aol_card_intent.allowed_latch
      assert feedback.gm_withdrawal_ns == occurrence
      assert native.requestedLongitudinal and native.longitudinalAllowed
      assert native.observedMonoTime == NOW and native.validUntilMonoTime == NOW + 200_000_000
      allowed, pause_lat, pause_long = card.aol_card_intent.output(cs)
      intent = SimpleNamespace(allowedLatch=allowed, pauseLateral=pause_lat, pauseLongitudinal=pause_long)
      no_entry = aol_no_entry(events.names, cs, paddle_only_cancel=False)
      decision = decide_axes(standard_lateral=True, standard_longitudinal=unlatched,
        intent=intent, native=native, car_state=cs, initialized=True, model_ready=True,
        no_entry=no_entry, immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
      assert decision.desired_lateral and decision.lateral_active
      # Belt loss locks LONG even with a standard LONG request. Rebuckling
      # supplies no new standard engagement and cannot resume LONG automatically.
      assert not decision.desired_longitudinal and not decision.longitudinal_active
      locked = decide_axes(standard_lateral=True, standard_longitudinal=unlatched,
        intent=intent, native=native, car_state=cs, initialized=True, model_ready=True,
        no_entry=no_entry, immediate_disable=False, dm_lockout=True, pause_brake_mps=0.)
      assert not locked.lateral_active and not locked.longitudinal_active
    ack_ns = NOW + 50_000_000
    cs = parse(card, ack_ns, counter=1)
    lat_only = replace(native, observedMonoTime=ack_ns, validUntilMonoTime=ack_ns + 200_000_000,
      requestedLongitudinal=False, longitudinalAllowed=False,
      pandaInventory=(replace(native.pandaInventory[0], controlsAllowed=False),))
    sm, _ = peers(card.CP, card.slc_producer_session, ack_ns, controls=False,
                  native=lat_only, sequence=6, long_active=False)
    assert not feedback.observe(sm, card.CP, cs, ack_ns, latched=True,
                               intent_producer_session=card.slc_producer_session)
    assert not feedback.failed and feedback.gm_withdrawal_ns == 0


@pytest.mark.parametrize('fault', ['belt_immediate', 'belt_user_disable', 'belt_missing_noentry',
  'wrong_belt_name', 'other_soft', 'mixed_soft', 'stale_events', 'future_events', 'door', 'eps'])
def test_combined_belt_withdrawal_preserves_exact_event_and_vehicle_fault_vetoes(fault):
  from openpilot.selfdrive.selfdrived.events import Events, EventName
  with OpenpilotPrefix():
    card, feedback, native = baseline()
    now = NOW + 10_000_000
    cs = parse(card, now, 'brake', counter=1, belt_unlatched=True)
    source = card.aol_gm_withdrawal_source(cs, now)
    assert source == now
    sm, _ = peers(card.CP, card.slc_producer_session, now, controls=False, native=native, sequence=2)
    events = Events()
    events.add(EventName.seatbeltNotLatched)
    if fault in ('other_soft', 'mixed_soft'):
      if fault == 'other_soft':
        events.clear()
      events.add(EventName.processNotRunning)
    event_wire = sm.messages['onroadEvents'].as_builder()
    event_wire.onroadEvents = events.to_msg()
    if fault == 'belt_immediate':
      event_wire.onroadEvents[0].immediateDisable = True
    elif fault == 'belt_user_disable':
      event_wire.onroadEvents[0].userDisable = True
    elif fault == 'belt_missing_noentry':
      event_wire.onroadEvents[0].noEntry = False
    elif fault == 'wrong_belt_name':
      event_wire.onroadEvents[0].name = EventName.steerTempUnavailable
    elif fault == 'stale_events':
      event_wire.logMonoTime = now - 1_500_000_001
    elif fault == 'future_events':
      event_wire.logMonoTime = now + 1
    elif fault == 'door':
      cs.doorOpen = True
    elif fault == 'eps':
      cs.steerFaultPermanent = True
    sm = Peers([sm.messages['aolSafetyWire'], sm.messages['pandaStates'],
                sm.messages['aolAxisState'], event_wire.as_reader()])
    assert feedback.observe(sm, card.CP, cs, now, latched=True,
      gm_withdrawal_source_ns=source, intent_producer_session=card.slc_producer_session)
    assert feedback.failed


@pytest.mark.parametrize('gesture', ['brake', 'paddle', 'cancel'])
@pytest.mark.parametrize('fault', [None, 'other_soft', 'immediate'])
def test_actual_sd_belt_classification_wire_reaches_next_card_cycle(gesture, fault):
  from contextlib import ExitStack

  from openpilot.selfdrive.selfdrived.events import Events, EventName
  from openpilot.starpilot.aol.intent_companion import IntentCompanion
  from openpilot.starpilot.aol.tests.test_ioniq_availability import IoniqAvailabilityTests
  from openpilot.starpilot.aol.transport_pause import AvailabilityPause, TransportPause
  from openpilot.starpilot.aol.wire import decode_intent

  class PublishedCarState(Exception):
    pass

  case = IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup, OpenpilotPrefix():
    cleanup.callback(case.doCleanups)
    card, _, native = baseline()
    sock, queued = object(), []
    companion = IntentCompanion(sock)
    sd, _, step = case._authority_callers(True, cp=card.CP.as_reader().as_builder(), intent_companion=companion)
    sd.aol_session_id = 'gm-session'
    sd.aol_transport_pause = TransportPause(sd.CP, sd.aol_session_id)
    sd.aol_availability_pause = AvailabilityPause(sd.CP, sd.aol_session_id)
    original_receive = messaging.recv_one_or_none
    cleanup.enter_context(patch('openpilot.starpilot.aol.intent_companion.messaging.recv_one_or_none',
      side_effect=lambda target: queued.pop(0) if target is sock and queued else None if target is sock else original_receive(target)))
    current = {'now': NOW, 'controls': True, 'events': Events()}
    original_update = sd.sm.update_msgs

    def receive_native(timestamp, messages):
      controlled, _ = peers(card.CP, card.slc_producer_session, current['now'],
                             controls=current['controls'], native=native)
      originals = [m for m in messages if m.which() not in ('aolSafetyWire', 'pandaStates')]
      original_update(timestamp, originals + [controlled.messages[n] for n in ('aolSafetyWire', 'pandaStates')])

    cleanup.enter_context(patch.object(sd.sm, 'update_msgs', side_effect=receive_native))
    def receive_events(cs):
      sd.events = current['events']
      sd._update_steering_authority(cs)

    cleanup.enter_context(patch.object(sd, 'update_events', side_effect=receive_events))
    real_new_message = messaging.new_message

    def publish_card(cs, now):
      published = {}

      def capture(service, message):
        published[service] = messaging.log_from_bytes(message if isinstance(message, bytes) else message.to_bytes())
        if service == 'carState':
          raise PublishedCarState

      with patch('openpilot.selfdrive.car.card.messaging.new_message',
                 side_effect=lambda service=None, size=None, **kwargs: real_new_message(service, size, **({'logMonoTime': now} | kwargs))), \
           patch.object(card.pm, 'send', side_effect=capture):
        with pytest.raises(PublishedCarState):
          card.state_publish(cs, None)
      assert decode_intent(published['aolIntentWire'].aolIntentWire).validUntilMonoTime == now + 200_000_000
      queued.append(published['aolIntentWire'])
      return published['aolIntentWire'].as_builder()

    # First establish the baseline from actual SD.step and its real publisher.
    healthy = parse(card, NOW)
    initial = publish_card(healthy, NOW)
    axis, _, _ = step(0, observed_state=healthy, intent_message=initial)
    assert str(axis.faultReason) == 'none' and axis.lateralActive and axis.longitudinalActive
    card.aol_transport_feedback = TransportPauseFeedback(card.CP)

    def receive_card(now, physical, unlatched, counter):
      controlled, _ = peers(card.CP, card.slc_producer_session, now, controls=current['controls'], native=native)
      frames = [controlled.messages[n] for n in ('aolSafetyWire', 'pandaStates')]
      frames += [sd.pm.messages[n] for n in ('aolAxisState', 'onroadEvents')]
      card.sm.update_msgs(now / 1e9, frames)
      packets = parse(card, now, physical, counter=counter, belt_unlatched=unlatched, packets_only=True)
      with patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw', return_value=[b'controlled-can-transport']), \
           patch('openpilot.selfdrive.car.card.can_capnp_to_list', return_value=[(now, packets)]), \
           patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now), \
           patch('openpilot.selfdrive.car.card.time.clock_gettime_ns', return_value=now), \
           patch('openpilot.selfdrive.car.card.REPLAY', False), patch.object(card.sm, 'update'):
        cs, _ = card.state_update()
      assert cs.canValid and cs.seatbeltUnlatched == unlatched
      return cs

    receive_card(NOW + 1_000, None, False, 1)
    assert card.aol_transport_feedback.session == 'gm-session' and card.aol_transport_feedback.healthy_inventory
    assert card.aol_transport_feedback.availability_epoch == companion.integrity_epoch
    for tick in (1, 2, 3):
      now = NOW + tick * 10_000_000
      current['now'], current['controls'] = now, False
      cs = receive_card(now, gesture if tick < 3 else None, tick < 3, (tick + 1) % 4)
      if tick == 2 and fault == 'immediate':
        assert card.aol_transport_feedback.failed and not card.aol_card_intent.allowed_latch
        return
      assert not card.aol_transport_feedback.failed and card.aol_card_intent.allowed_latch
      assert card.aol_transport_feedback.gm_withdrawal_ns == NOW + 10_000_000
      events = Events()
      if cs.seatbeltUnlatched:
        events.add(EventName.seatbeltNotLatched)
      if cs.brakePressed or cs.regenBraking:
        events.add(EventName.pedalPressed)
      elif tick == 1 and gesture == 'cancel':
        events.add(EventName.buttonCancel)
      if tick == 1 and fault is not None:
        events.add(EventName.overheat if fault == 'other_soft' else EventName.controlsMismatch)
      current['events'] = events
      produced = publish_card(cs, now)
      axis, _, command = step(tick, observed_state=cs, intent_message=produced)
      assert queued == [] and sd.aol_last_intent.producerSessionId == card.slc_producer_session
      assert axis.intentIntegrityEpoch == companion.integrity_epoch
      assert axis.intentProducerSessionId == card.slc_producer_session
      assert axis.sourceCarStateMonoTime == axis.observedMonoTime == now
      assert axis.validUntilMonoTime == now + 30_000_000
      assert native.observedMonoTime == NOW and native.validUntilMonoTime == NOW + 200_000_000
      assert native.requestedLateral and native.lateralAllowed and native.requestedLongitudinal and native.longitudinalAllowed
      assert not axis.longitudinalActive and not command.longActive
      if fault == 'immediate':
        assert str(axis.faultReason) == 'critical'
      elif fault == 'other_soft' and tick == 1:
        assert str(axis.faultReason) == 'availabilityPause'
        assert not axis.lateralActive and not command.latActive
      else:
        assert str(axis.faultReason) == 'none' and axis.lateralActive
        assert not sd.enabled  # Pedal/Cancel still disables ordinary LONG; rebuckling cannot resume it.


@pytest.mark.parametrize('fault', [None, 'owner', 'profile', 'stale_source', 'future_source', 'future_observation',
  'intent_expired', 'excessive_lease', 'unarmed', 'unqualified', 'native_missing', 'native_lateral',
  'native_fault', 'native_session', 'native_expired', 'belt_mismatch', 'event_user', 'event_no_entry'])
def test_belt_soft_classification_keeps_current_owner_intent_native_and_source_guards(fault):
  from openpilot.selfdrive.selfdrived.events import Events, EventName
  from openpilot.starpilot.aol.wire import IntentState
  from openpilot.starpilot.car.gm.aol import seatbelt_lateral_soft_event_allowed

  with OpenpilotPrefix():
    card, _, native = baseline()
    now = NOW + 10_000_000
    cs = parse(card, now, 'brake', counter=1, belt_unlatched=True)
    intent = IntentState(card.slc_producer_session, 1, now, now, now + 200_000_000,
                         True, False, False, True, True)
    source = now
    events = Events()
    events.add(EventName.seatbeltNotLatched)
    event = events.to_msg()[0]
    if fault == 'owner':
      card.CP.alternativeExperience = 0
    elif fault == 'profile':
      card.CP.carFingerprint = CAR.CHEVROLET_VOLT
    elif fault == 'stale_source':
      now += 30_000_001
    elif fault == 'future_source':
      source += 1
    elif fault == 'future_observation':
      intent = replace(intent, observedMonoTime=now + 1)
    elif fault == 'intent_expired':
      intent = replace(intent, validUntilMonoTime=now - 1)
    elif fault == 'excessive_lease':
      intent = replace(intent, validUntilMonoTime=intent.observedMonoTime + 200_000_001)
    elif fault == 'unarmed':
      intent = replace(intent, allowedLatch=False)
    elif fault == 'unqualified':
      intent = replace(intent, settingsQualified=False)
    elif fault == 'native_missing':
      native = None
    elif fault == 'native_lateral':
      native = replace(native, lateralAllowed=False)
    elif fault == 'native_fault':
      native = replace(native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
    elif fault == 'native_session':
      native = replace(native, axisSessionId='other')
    elif fault == 'native_expired':
      native = replace(native, validUntilMonoTime=now - 1)
    elif fault == 'belt_mismatch':
      cs.seatbeltUnlatched = False
    elif fault == 'event_user':
      event.userDisable = True
    elif fault == 'event_no_entry':
      event.noEntry = False
    assert seatbelt_lateral_soft_event_allowed(card.CP, cs, intent, native, event,
      source_ns=source, now_ns=now, axis_session_id='gm-session') == (fault is None)


@pytest.mark.parametrize('fault', [None, 'sparse_event', 'no_occurrence', 'same_cycle', 'event_mismatch',
  'publisher_delay', 'future_event', 'axis_stamp', 'fault_session', 'stale_source', 'future_source', 'axis_expired',
  'axis_excessive_lease', 'long_desired', 'long_active', 'event_user', 'event_immediate',
  'event_no_entry', 'mixed_soft', 'producer', 'epoch', 'native_expired', 'occurrence_expired'])
def test_rebuckled_belt_retention_requires_prior_paired_lat_only_proof(fault):
  from openpilot.selfdrive.selfdrived.events import Events, EventName

  with OpenpilotPrefix():
    card, feedback, native = baseline()
    occurrence = NOW + 10_000_000
    cs = parse(card, occurrence, 'brake', counter=1, belt_unlatched=True)
    sm, _ = peers(card.CP, card.slc_producer_session, occurrence, controls=False, native=native, sequence=2)
    assert not feedback.observe(sm, card.CP, cs, occurrence, latched=True,
      gm_withdrawal_source_ns=card.aol_gm_withdrawal_source(cs, occurrence),
      intent_producer_session=card.slc_producer_session)
    assert feedback.gm_withdrawal_ns == occurrence

    now, prior = NOW + 30_000_000, NOW + 20_000_000
    cs = parse(card, now, counter=2)
    assert not cs.seatbeltUnlatched and not cs.brakePressed and not cs.regenBraking
    assert card.aol_gm_withdrawal_source(cs, now) == 0
    sm, _ = peers(card.CP, card.slc_producer_session, prior, controls=False, native=native,
                  sequence=3, long_active=False)
    axis = sm.messages['aolAxisState'].as_builder()
    events = sm.messages['onroadEvents'].as_builder()
    ordinary = Events()
    ordinary.add(EventName.seatbeltNotLatched)
    events.onroadEvents = ordinary.to_msg()
    axis.aolAxisState.faultEventMonoTime = events.logMonoTime
    if fault == 'sparse_event':
      events.logMonoTime = axis.aolAxisState.faultEventMonoTime = occurrence
    elif fault == 'no_occurrence':
      feedback.gm_withdrawal_ns = 0
    elif fault == 'same_cycle':
      events.logMonoTime = now
      axis.logMonoTime = now
      axis.aolAxisState.sourceCarStateMonoTime = axis.aolAxisState.observedMonoTime = now
      axis.aolAxisState.validUntilMonoTime = now + 30_000_000
      axis.aolAxisState.faultEventMonoTime = now
    elif fault == 'event_mismatch':
      axis.aolAxisState.faultEventMonoTime -= 1
    elif fault == 'publisher_delay':
      axis.aolAxisState.sourceCarStateMonoTime = events.logMonoTime - 1_000
    elif fault == 'future_event':
      events.logMonoTime = axis.aolAxisState.faultEventMonoTime = prior + 1
    elif fault == 'axis_stamp':
      axis.logMonoTime -= 1
    elif fault == 'fault_session':
      axis.aolAxisState.faultSessionId = 'other-session'
    elif fault == 'stale_source':
      axis.aolAxisState.sourceCarStateMonoTime = now - 30_000_001
    elif fault == 'future_source':
      axis.aolAxisState.sourceCarStateMonoTime = now + 1
    elif fault == 'axis_expired':
      axis.aolAxisState.validUntilMonoTime = now - 1
    elif fault == 'axis_excessive_lease':
      axis.aolAxisState.validUntilMonoTime = prior + 30_000_001
    elif fault == 'long_desired':
      axis.aolAxisState.desiredLongitudinal = True
    elif fault == 'long_active':
      axis.aolAxisState.longitudinalActive = True
    elif fault == 'event_user':
      events.onroadEvents[0].userDisable = True
    elif fault == 'event_immediate':
      events.onroadEvents[0].immediateDisable = True
    elif fault == 'event_no_entry':
      events.onroadEvents[0].noEntry = False
    elif fault == 'mixed_soft':
      ordinary.add(EventName.overheat)
      events.onroadEvents = ordinary.to_msg()
    elif fault == 'producer':
      axis.aolAxisState.intentProducerSessionId = 'other-producer'
    elif fault == 'epoch':
      axis.aolAxisState.intentIntegrityEpoch += 1
    elif fault == 'native_expired':
      native = replace(native, validUntilMonoTime=now - 1)
      sm, _ = peers(card.CP, card.slc_producer_session, prior, controls=False, native=native,
                    sequence=3, long_active=False)
    elif fault == 'occurrence_expired':
      now = occurrence + 200_000_001
      prior = now - 10_000_000
      cs = parse(card, now, counter=3)
      axis.logMonoTime = prior
      axis.aolAxisState.sourceCarStateMonoTime = axis.aolAxisState.observedMonoTime = prior
      axis.aolAxisState.validUntilMonoTime = prior + 30_000_000
      events.logMonoTime = axis.aolAxisState.faultEventMonoTime = prior
      native = replace(native, observedMonoTime=prior, validUntilMonoTime=prior + 200_000_000)
      sm, _ = peers(card.CP, card.slc_producer_session, prior, controls=False, native=native,
                    sequence=3, long_active=False)
    sm = Peers([sm.messages['aolSafetyWire'], sm.messages['pandaStates'], axis.as_reader(), events.as_reader()])
    reset = feedback.observe(sm, card.CP, cs, now, latched=True,
      gm_withdrawal_source_ns=0, intent_producer_session=card.slc_producer_session)
    card.aol_card_intent.update(cs, fault_active=reset, now_ns=now)
    if fault in (None, 'sparse_event', 'publisher_delay'):
      assert not reset and not feedback.failed and card.aol_card_intent.allowed_latch
      assert feedback.gm_withdrawal_ns == occurrence
      assert native.observedMonoTime == NOW and native.validUntilMonoTime == NOW + 200_000_000
      assert native.requestedLongitudinal and native.longitudinalAllowed
      intent = SimpleNamespace(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False)
      decision = decide_axes(standard_lateral=False, standard_longitudinal=False, intent=intent,
        native=native, car_state=cs, initialized=True, model_ready=True, no_entry=False,
        immediate_disable=False, dm_lockout=False, pause_brake_mps=5.)
      assert decision.lateral_active and not decision.desired_longitudinal and not decision.longitudinal_active
    else:
      assert reset and feedback.failed and not card.aol_card_intent.allowed_latch


def _sdgm_pedal_state(card, stamp, tick, gesture=None, manual=False):
  from opendbc.car.gm.tests.test_volt_camera_control import feed_camera

  packer = CANPacker(DBC[card.CP.carFingerprint][Bus.pt])
  empty = SimpleNamespace(update=lambda _: None)
  _, frames = feed_camera(empty, packer, stamp, counter=tick % 4, speed=16.0, brake=gesture == 'brake', regen=gesture == 'paddle', active=True, eps_status=1)
  # Same recorded forward manual-mode report during Mary paddle withdrawal.
  frames = [f for f in frames if f[0] not in (0x1F5, 0xBE, 0xC9)]
  frames += [
    packer.make_can_msg('ECMAcceleratorPos', 0, {'BrakePedalPos': 8 if gesture == 'brake' else 0}),
    packer.make_can_msg('ECMEngineStatus', 0, {'CruiseMainOn': 1, 'BrakePressed': int(gesture == 'brake')}),
  ]
  frames.append(packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 4, 'ManualMode': int(manual)}))
  if tick == 0:
    card.CI.update([(stamp - 1_000_000, frames)])
  cs = card.CI.update([(stamp, frames)])
  assert cs.canValid and not cs.canTimeout
  assert cs.regenBraking == (gesture == 'paddle')
  assert cs.brakePressed == (gesture == 'brake')
  assert str(cs.gearShifter) == ('manumatic' if manual else 'drive')
  return cs


@pytest.mark.parametrize('c9', (False, True))
@pytest.mark.parametrize('gesture', ('brake', 'paddle'))
@pytest.mark.parametrize('event_first', (False, True))
@pytest.mark.parametrize('health_allowed', (False, True))
def test_sdgm_alpha_pedal_does_not_erase_armed_lateral(c9, gesture, event_first, health_allowed):
  from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
  from openpilot.starpilot.aol.tests.test_gm import TestGmAol as GmFixture
  from openpilot.starpilot.aol.wire import IntentState

  with OpenpilotPrefix():
    saved = Params()
    saved.put_bool('AlwaysOnLateral', True, block=True)
    card = GmFixture.card(sdgm_params(alpha=True, brake_c9=c9), saved)
    try:
      cp = card.CP
      feedback = TransportPauseFeedback(cp)
      cs = _sdgm_pedal_state(card, NOW, 0)
      card.aol_card_intent.update(cs, fault_active=False, now_ns=NOW)
      assert card.aol_card_intent.allowed_latch
      sm, prior = peers(cp, card.slc_producer_session, NOW)
      assert not feedback.observe(sm, cp, cs, NOW, latched=True, intent_producer_session=card.slc_producer_session)
      now = NOW + 10_000_000
      cs = _sdgm_pedal_state(card, now, 1, gesture, True)
      native = replace(
        prior,
        observedMonoTime=now,
        validUntilMonoTime=now + 200_000_000,
        longitudinalAllowed=False,
        pandaInventory=(replace(prior.pandaInventory[0], controlsAllowed=health_allowed),),
      )
      sm, _ = peers(cp, card.slc_producer_session, now, controls=health_allowed, native=native, sequence=2, long_active=False)
      a = sm.messages['aolAxisState'].as_builder()
      a.aolAxisState.faultEventMonoTime = NOW if event_first else now
      # Event first: the last full desired request may precede the ordinary disengagement result.
      a.aolAxisState.desiredLongitudinal = event_first
      e = sm.messages['onroadEvents'].as_builder()
      e.onroadEvents = [{'name': 'pedalPressed', 'userDisable': True, 'noEntry': True}]
      sm = Peers([sm.messages['aolSafetyWire'], sm.messages['pandaStates'], a.as_reader(), e.as_reader()])
      fault = feedback.observe(sm, cp, cs, now, latched=True, intent_producer_session=card.slc_producer_session)
      card.aol_card_intent.update(cs, fault_active=fault, now_ns=now)
      assert not fault and card.aol_card_intent.allowed_latch, (gesture, event_first, health_allowed, feedback.__dict__)
      intent = IntentState(card.slc_producer_session, 2, now, now, now + 200_000_000, True, False, False, True, True)
      d = decide_axes(
        standard_lateral=False,
        standard_longitudinal=False,
        intent=intent,
        native=native,
        car_state=cs,
        initialized=True,
        model_ready=True,
        no_entry=False,
        immediate_disable=False,
        dm_lockout=False,
        pause_brake_mps=0.0,
      )
      assert d.lateral_active and not d.longitudinal_active
      # Fresh independent LAT native acknowledgment after release; LONG stays disabled.
      later = now + 10_000_000
      cs = _sdgm_pedal_state(card, later, 2)
      native = replace(native, observedMonoTime=later, validUntilMonoTime=later + 200_000_000, requestedLongitudinal=False)
      sm, _ = peers(cp, card.slc_producer_session, later, controls=False, native=native, sequence=3, long_active=False)
      assert not feedback.observe(sm, cp, cs, later, latched=True, intent_producer_session=card.slc_producer_session)
      assert card.aol_card_intent.allowed_latch and not native.longitudinalAllowed
    finally:
      card.vehicle_startup.close()


def test_factory_brake_clock_packets_are_scoped_and_follow_25hz_cycles():
  for candidate in (CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021,
                    CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL):
    for removed in (False, True):
      cp = params(candidate, pedal=True, camera=not removed, removed=removed)
      card = SimpleNamespace(CP=cp)
      for tick in range(9):
        now = NOW + tick * 10_000_000
        packets = parse(card, now, counter=tick % 4, packets_only=True)
        clock = [row for row in packets if row[0] == 0x315]
        expected = candidate == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and not removed and tick % 4 == 0
        assert len(clock) == int(expected)
        if expected:
          assert clock[0][2] == 2 and len(clock[0][1]) == 5
          assert clock[0][1][4] & 3 == (now // 40_000_000) % 4
