"""An accepted idle baseline retires a prior GM cold MAIN wait, not safety gates."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.values import CAR
from opendbc.car.structs import car
from openpilot.cereal import messaging
from openpilot.starpilot.aol.runtime import current_native, decide_axes
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety

NOW = 10_000_000_000


def owner():
  cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
  cp.alternativeExperience = 96
  assert cp.safetyConfigs[0].safetyParam == 157
  cs = car.CarState(canValid=True, gearShifter=car.CarState.GearShifter.drive, vEgo=20.)
  cs.cruiseState.available = True
  return cp, cs


def snapshot(cp, now, *, kind='idle', faults=0, expired=False, session='session'):
  sm = messaging.SubMaster(['aolAxisState', 'aolSafetyWire', 'pandaStates', 'onroadEvents'])
  config = cp.safetyConfigs[0]
  native = SafetyState(1, True, now, now + 200_000_000, config.safetyModel.raw, config.safetyParam,
                      False, False, False, False, 'panda', session, now,
                      (PandaSlot(0, 'panda', config.safetyModel.raw, config.safetyParam, cp.alternativeExperience, False, faults=faults),))
  if expired:
    native = replace(native, observedMonoTime=now-201_000_000, validUntilMonoTime=now-1)
  wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=native.observedMonoTime)
  wire.aolSafetyWire = encode_safety(native)
  pandas = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now)
  pandas.pandaStates[0].safetyModel = config.safetyModel
  pandas.pandaStates[0].safetyParam = config.safetyParam
  pandas.pandaStates[0].alternativeExperience = cp.alternativeExperience
  if faults:
    pandas.pandaStates[0].faults = ['relayMalfunction']
  events = messaging.new_message('onroadEvents', int(kind == 'soft'), valid=True, logMonoTime=now)
  if kind == 'soft':
    events.onroadEvents[0].name = 'commIssueAvgFreq'
    events.onroadEvents[0].softDisable = True
  axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
  a = axis.aolAxisState
  a.sessionId = a.faultSessionId = session
  a.sequence = (now-NOW)//10_000_000 + 1
  a.sourceCarStateMonoTime = a.observedMonoTime = a.faultEventMonoTime = now
  a.validUntilMonoTime = now + 30_000_000
  a.intentProducerSessionId = 'producer'
  a.intentIntegrityEpoch = 1
  a.qualified = True
  a.nativeAcknowledged = kind != 'soft'
  a.desiredLateral = kind == 'request'
  a.faultReason = 'availabilityPause' if kind == 'soft' else 'none'
  sm.update_msgs(now/1e9, [wire.as_reader(), pandas.as_reader(), events.as_reader(), axis.as_reader()])
  return sm


@pytest.mark.parametrize('bad', ('none', 'fault', 'expired_native'))
def test_idle_recovery_does_not_reject_next_request_using_old_wait(bad):
  cp, cs = owner()
  feedback = TransportPauseFeedback(cp)
  def observe(now, kind, latched, **kwargs):
    sm = snapshot(cp, now, kind=kind, **kwargs)
    rejected = feedback.observe(sm, cp, cs, now, latched=latched,
                                intent_producer_session='producer')
    return sm, rejected
  _, rejected = observe(NOW, 'idle', False)
  assert not rejected
  _, rejected = observe(NOW+10_000_000, 'soft', True)
  assert not rejected and feedback.cold_main_wait_ns
  # The previous request is no longer latched; admit a new healthy idle baseline.
  sm, rejected = observe(NOW+500_000_000, 'idle', False,
                         faults=1 if bad == 'fault' else 0, expired=bad == 'expired_native')
  if bad != 'none':
    assert current_native(sm, cp, now_ns=NOW+500_000_000, axis_session_id='session') is None
    assert feedback.cold_main_wait_ns  # No invalid proof may retire the wait.
    return
  assert not rejected and feedback.cold_main_wait_ns == 0
  sm, rejected = observe(NOW+510_000_000, 'request', True)
  assert not rejected
  # Waiting for the native request echo never manufactures output authority.
  native = current_native(sm, cp, now_ns=NOW+510_000_000, axis_session_id='session')
  assert native is not None and not native.lateralAllowed
  intent = SimpleNamespace(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False)
  decision = decide_axes(standard_lateral=False, standard_longitudinal=False,
                        intent=intent, native=native, car_state=cs, initialized=True,
                        model_ready=True, no_entry=False, immediate_disable=False,
                        dm_lockout=False, pause_brake_mps=0.)
  assert not decision.lateral_active and not decision.longitudinal_active


def test_recovered_intent_waits_for_native_ack_without_chime_loop():
  from opendbc.can import CANPacker
  from opendbc.car import Bus
  from opendbc.car.gm.gmcan import pedal_crc
  from opendbc.car.gm.tests.test_bolt_cc import feed
  from opendbc.car.gm.values import DBC
  from opendbc.safety.tests.libsafety import libsafety_py
  from openpilot.starpilot.aol.intent import AolSettings
  from openpilot.starpilot.aol.wire import IntentState, encode_intent
  from openpilot.starpilot.audio.axis_alerts import AudibleAlert, AxisAlerts
  from openpilot.starpilot.car.gm.aol import GmAolCardIntent

  cp, cs = owner()
  feedback = TransportPauseFeedback(cp)
  assert not feedback.observe(snapshot(cp, NOW), cp, cs, NOW, latched=False, intent_producer_session='producer')
  assert not feedback.observe(snapshot(cp, NOW+10_000_000, kind='soft'), cp, cs,
                              NOW+10_000_000, latched=True, intent_producer_session='producer')
  assert feedback.cold_main_wait_ns
  intent = GmAolCardIntent(AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)), cp)
  audio = AxisAlerts()
  audio_sm = messaging.SubMaster(['aolIntentWire', 'selfdriveState'])
  sounds = []
  for tick in range(7):
    now = NOW + 500_000_000 + tick * 10_000_000
    sm = snapshot(cp, now, kind='idle' if tick == 0 else 'request')
    rejected = feedback.observe(sm, cp, cs, now, latched=intent.allowed_latch, intent_producer_session='producer')
    intent.update(cs, fault_active=rejected, now_ns=now)
    assert intent.allowed_latch and not rejected
    native = current_native(sm, cp, now_ns=now, axis_session_id='session')
    decision = decide_axes(standard_lateral=False, standard_longitudinal=False,
                          intent=SimpleNamespace(allowedLatch=intent.allowed_latch, pauseLateral=False, pauseLongitudinal=False),
                          native=native, car_state=cs, initialized=True, model_ready=True,
                          no_entry=False, immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
    assert not decision.lateral_active
    wire = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=now)
    wire.aolIntentWire = encode_intent(IntentState('producer', tick+1, now, now, now+200_000_000,
                                                  True, False, False, True, True))
    state = messaging.new_message('selfdriveState', valid=True, logMonoTime=now)
    audio_sm.update_msgs(now/1e9, [wire.as_reader(), state.as_reader()])
    sounds.append(audio.update(audio_sm, AudibleAlert.none, now))
  assert AudibleAlert.disengage not in sounds
  assert sounds.count(AudibleAlert.engage) <= 1

  # Earn LAT through real current MAIN/gear/pedal RX and host request, not a grant setter.
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(cp.alternativeExperience)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, 157) == 0
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for tick in range(32):
    now = NOW + 600_000_000 + tick * 10_000_000
    _, rows = feed(SimpleNamespace(update=lambda _: None), packer, now,
                   counter=tick % 4, active=False, speed=20., camera=True)
    rows = [row for row in rows if row[0] != 0x1F5]
    rows.append(packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 6}))
    sensor = bytearray(packer.make_can_msg('GAS_SENSOR', 0, {
      'INTERCEPTOR_GAS': 0., 'INTERCEPTOR_GAS2': 0., 'STATE': 0, 'COUNTER_PEDAL': tick % 16})[1])
    sensor[5] = pedal_crc(sensor)
    rows.append((0x201, bytes(sensor), 0))
    safety.set_timer(now//1000)
    for address, payload, bus in rows:
      assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, payload))
    safety.safety_tick()
    safety.set_aol_test_heartbeat(True)
    safety.aol_set_host_request(1)
  mask = safety.aol_get_permission_mask()
  assert mask == 1 and not safety.get_controls_allowed()
  sm = snapshot(cp, now, kind='request')
  receipt = current_native(sm, cp, now_ns=now, axis_session_id='session')
  receipt = replace(receipt, requestedLateral=True, lateralAllowed=bool(mask & 1))
  decision = decide_axes(standard_lateral=False, standard_longitudinal=False,
                        intent=SimpleNamespace(allowedLatch=intent.allowed_latch, pauseLateral=False, pauseLongitudinal=False),
                        native=receipt, car_state=cs, initialized=True, model_ready=True,
                        no_entry=False, immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
  assert decision.lateral_active and not decision.longitudinal_active
  safety.set_alternative_experience(0)


@pytest.mark.parametrize('variation', ('still_latched', 'invalid_axis', 'foreign_producer'))
def test_wait_is_not_retired_without_accepted_unlatched_baseline(variation):
  cp, cs = owner()
  feedback = TransportPauseFeedback(cp)
  assert not feedback.observe(snapshot(cp, NOW), cp, cs, NOW, latched=False, intent_producer_session='producer')
  assert not feedback.observe(snapshot(cp, NOW+10_000_000, kind='soft'), cp, cs,
                              NOW+10_000_000, latched=True, intent_producer_session='producer')
  prior = feedback.cold_main_wait_ns
  assert prior
  now = NOW+500_000_000
  sm = snapshot(cp, now)
  if variation == 'invalid_axis':
    sm.valid['aolAxisState'] = False
  if variation == 'foreign_producer':
    message = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
    message.aolAxisState = sm['aolAxisState']
    message.aolAxisState.intentProducerSessionId = 'foreign'
    sm.update_msgs((now+1)/1e9, [message.as_reader()])
  feedback.observe(sm, cp, cs, now, latched=variation == 'still_latched', intent_producer_session='producer')
  assert feedback.cold_main_wait_ns == prior


def test_actual_card_retires_rejected_cold_wait_before_next_request():
  """Real parser/Card publication stays armed while the first native echo is idle."""
  import os
  from contextlib import ExitStack
  from unittest.mock import patch

  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car import card as module
  from openpilot.starpilot.aol.tests.test_gm import TestGmAol
  from openpilot.starpilot.aol.tests.test_gm_held_native import parse
  from openpilot.starpilot.aol.wire import decode_intent

  with OpenpilotPrefix(), ExitStack() as cleanup, patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
    saved = Params()
    saved.put_bool('AlwaysOnLateral', True, block=True)
    saved.put_bool('AlphaLongitudinalEnabled', True, block=True)
    cp, _ = owner()
    card = TestGmAol.card(cp, saved)
    cleanup.callback(card.vehicle_startup.close)
    card.ci_initialized = True
    steering_receipts = []

    def publish_sendcan(frames, valid=True):
      # This idle transport fixture confirms only the commands actually sent.
      if valid:
        steering_receipts.extend((address, bytes(data), 128)
                                 for address, data, bus in frames if address == 0x180 and bus == 0)

    card.publish_sendcan = publish_sendcan
    published = {}
    card.pm.send = lambda name, message: published.__setitem__(name, message.as_reader())
    for tick in range(20):
      stamp = NOW-(20-tick)*10_000_000
      rows = parse(card, stamp, counter=tick % 4, packets_only=True)
      card.CI.update([(stamp, rows)])

    def cycle(tick, kind):
      now = NOW+tick*10_000_000
      peers = snapshot(card.CP, now, kind=kind)
      messages = []
      for name in ('aolSafetyWire', 'pandaStates', 'aolAxisState', 'onroadEvents'):
        size = len(peers[name]) if name in ('pandaStates', 'onroadEvents') else 0 if name == 'aolSafetyWire' else None
        message = messaging.new_message(name, size, valid=True, logMonoTime=now)
        setattr(message, name, peers[name])
        if name == 'aolAxisState':
          message.aolAxisState.intentProducerSessionId = card.slc_producer_session
        messages.append(message)
      if kind == 'soft':
        messages[-1].logMonoTime = NOW+10_000_000
        messages[-2].aolAxisState.faultEventMonoTime = NOW+10_000_000
      if kind == 'critical':
        events = messaging.new_message('onroadEvents', 1, valid=True, logMonoTime=now)
        events.onroadEvents[0].name = 'controlsMismatch'
        events.onroadEvents[0].immediateDisable = True
        messages[-1] = events
        messages[-2].aolAxisState.faultReason = 'critical'
      control = messaging.new_message('carControl', valid=True, logMonoTime=now)
      card.sm.update_msgs(now/1e9, [m.as_reader() for m in messages]+[control.as_reader()])
      rows = parse(card, now, counter=tick % 4, packets_only=True)
      rows = steering_receipts + rows
      steering_receipts.clear()
      batch = messaging.log_from_bytes(module.can_list_to_can_capnp(rows)).as_builder()
      batch.logMonoTime = now
      with patch.object(module, 'REPLAY', False), \
           patch.object(module.messaging, 'drain_sock_raw', return_value=[batch.to_bytes()]), \
           patch.object(module.time, 'monotonic_ns', return_value=now), \
           patch.object(module.time, 'monotonic', return_value=now/1e9), \
           patch.object(module.time, 'clock_gettime_ns', return_value=now), patch.object(card.sm, 'update'):
        card.step()
      assert card.CS_prev.canValid and not card.CS_prev.canTimeout
      assert not card.last_actuators_output.torqueOutputCan
      result = decode_intent(published['aolIntentWire'].aolIntentWire)
      assert result is not None
      return result

    assert cycle(0, 'idle').lateralArmed
    assert cycle(1, 'soft').lateralArmed
    assert card.aol_transport_feedback.cold_main_wait_ns
    for tick in range(2, 40):
      assert cycle(tick, 'soft').lateralArmed
    assert not cycle(40, 'critical').lateralArmed
    assert cycle(41, 'idle').lateralArmed
    assert cycle(42, 'request').lateralArmed
    assert card.aol_transport_feedback.cold_main_wait_ns == 0
