"""Actual automatic Subaru factory/parser/Card/SD/Controls/native/CPP negotiation."""

import json
import time
from unittest.mock import patch
import pytest
from opendbc.can import CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.subaru.values import CAR, DBC
from opendbc.car.subaru.fingerprints import FW_VERSIONS
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import log, messaging
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.pandad import can_capnp_to_list
from openpilot.starpilot.aol.wire import decode_intent
from openpilot.starpilot.car.subaru.tests.caller_helpers import actual_factory, firmware_records, CompleteCan, feed_perception_only
from openpilot.starpilot.car.subaru.tests.selfdrive_step import selfdrive_step
from openpilot.starpilot.car.subaru.tests.pandad_order import PandadOrder

from openpilot.starpilot.car.subaru.tests.native_fixture import subaru_native_pair as subaru_native_pair


@pytest.mark.parametrize('candidate', [CAR.SUBARU_ASCENT_2023, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_FORESTER, CAR.SUBARU_OUTBACK])
def test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(candidate, subaru_native_pair, tmp_path, aol=True, manual=False, stop_resume=False,
                                                                      hard_stop_native_first=False, main_only=False, preferences=None, firmware=None,
                                                                      deadline_gap=False, fixed_angle_handoff=False, host_first_stop=False,
                                                                      source_outage=False, vm_angle_handoff=0, crawl=False):
  angle_handoff = fixed_angle_handoff or vm_angle_handoff
  fw = firmware_records(candidate, FW_VERSIONS, 'subaru') if firmware is None else firmware
  rows = []
  with actual_factory(candidate, gen_empty_fingerprint(), fw, '0' * 17, manual=manual, aol=aol, preferences=preferences) as (card, params, matches):
    cp = card.CP
    ci = card.CI
    safety = libsafety_py.libsafety
    assert cp.pcmCruise and not cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable
    assert cp.alternativeExperience == (32 if aol else 0) and not cp.dashcamOnly
    if candidate == CAR.SUBARU_IMPREZA_2020:
      prefs = preferences or {}
      expected_word = (4096 if aol else 0) | (512 if prefs.get('SubaruRedneckCruise') else 0) | (8 if prefs.get('SubaruSNG') else 0)
      assert cp.safetyConfigs[0].safetyParam == expected_word
      assert cp.autoResumeSng == bool(prefs.get('SubaruSNG'))
    sd, controls = SelfdriveD(CP=cp), Controls()
    stream = CompleteCan(ci)
    safety.init_tests()
    safety.set_alternative_experience(cp.alternativeExperience)
    assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
    safety.set_aol_test_heartbeat(False)
    library, bridge = subaru_native_pair
    pandad = PandadOrder(safety, library, bridge)
    previous_controls = {}
    latest_controls = {}
    driven = {}
    driven_new = {}
    receipt = None
    steering_name = 'ES_LKAS_ANGLE' if cp.steerControlType == structs.CarParams.SteerControlType.angle else 'ES_LKAS'
    steering_address = ci.CC.packer.dbc.name_to_msg[steering_name].address
    steering_bus = 0
    decoder = CANParser(DBC[candidate][Bus.pt], [(steering_name, 0)], steering_bus)
    dash_decoder = CANParser(DBC[candidate][Bus.pt], [('ES_LKAS_State', 0)], 0)
    dash_emissions = 0
    active_emissions = []
    base = time.monotonic_ns() + 100_000_000
    try:
      published = {}
      ticks = (list(range(125)) + list(range(125, 135)) * 30 + list(range(135, 460))
               if hard_stop_native_first else range(460))
      gap_tick = 75 if main_only else 210
      last_card_published = {}
      pending_host_can = []
      for elapsed, tick in enumerate(ticks):
        now = base + elapsed * 10_000_000 + (22_504_479 if deadline_gap and elapsed >= gap_tick else 0)
        cruise = not main_only and (30 <= tick < 110 or tick >= 410)
        brake = 90 <= tick < 110
        reverse = 150 <= tick < 185
        temporary = 225 <= tick < 260
        main = not 310 <= tick < 350 and (tick >= 30 or not main_only)
        stopped = stop_resume and (125 <= tick < 135 if hard_stop_native_first or host_first_stop else 185 <= tick < 195)
        first_resume = 145 <= tick < 150 if hard_stop_native_first or host_first_stop else 125 <= tick < 145
        source_missing = bool(source_outage and 75 <= tick < 81)
        values = {
          'Throttle': {'Throttle_Pedal': 100 if source_outage and 60 <= tick < 90 else 0},
          'Steering_Torque': {'Steer_Warning': int(temporary),
                             'Steer_Torque_Sensor': (250 if 70 <= tick < 76 else
                                                     80 if vm_angle_handoff and 76 <= tick < 90 else
                                                     143 if 76 <= tick < 90 else 0) if angle_handoff else 0},
          'Steering_2': {'Steering_Angle': (vm_angle_handoff * (5.65 if tick < 78 else 5.60) if vm_angle_handoff else
                                          (5.65 if tick < 78 else 5.61) if fixed_angle_handoff else 0)},
          'Wheel_Speeds': dict.fromkeys(('FL', 'FR', 'RL', 'RR'),
                                       0 if stopped else 98.2 if vm_angle_handoff else 121.5 if fixed_angle_handoff else 7.2 if crawl else 54),
          'Brake_Status': {'Brake': int(brake)},
          'CruiseControl': {'Cruise_On': int(main), 'Cruise_Activated': int(cruise)},
          'ES_Status': {'Cruise_Activated': int(cruise)},
          'ES_DashStatus': {'Cruise_On': int(main), 'Cruise_Set_Speed': 100},
          'Transmission': {'Gear': 3 if reverse else 121},
        }
        incoming = stream.frames(values, tick=tick)
        if source_outage:
          pending_host_can.append((now, incoming))
        safety.set_timer((now // 1000) & 0xFFFFFFFF)
        for addr, data, bus in incoming:
          safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, data))
        safety.safety_tick()
        hard_stop_receipt = (hard_stop_native_first or host_first_stop) and elapsed == 125
        if hard_stop_receipt:
          # Native zero wheels and its USB poll precede the host's stopped CarState.
          receipt, hard_order = pandad.step(driven, published, now)
          assert hard_order['before'][:2] == (1, 0), hard_order
        panda = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now)
        panda.pandaStates[0].safetyModel = cp.safetyConfigs[0].safetyModel
        panda.pandaStates[0].safetyParam = cp.safetyConfigs[0].safetyParam
        panda.pandaStates[0].alternativeExperience = cp.alternativeExperience
        panda.pandaStates[0].ignitionLine = True
        panda.pandaStates[0].controlsAllowed = bool(safety.get_controls_allowed())
        panda.pandaStates[0].safetyRxChecksInvalid = not bool(safety.safety_config_valid())
        device = messaging.new_message('deviceState', valid=True, logMonoTime=now)
        device.deviceState.started = True
        device.deviceState.startedMonoTime = 500_000_000
        peers = [device.as_reader()]
        if tick % 10 == 0:
          peers.append(panda.as_reader())
        if 'carControl' in latest_controls:
          peers.append(latest_controls['carControl'])
        peers.extend(driven_new[s] for s in ('onroadEvents', 'selfdriveState', 'aolAxisState') if s in driven_new and s in card.sm.data)
        if receipt is not None and (tick % 10 == 1 or hard_stop_receipt and not host_first_stop or host_first_stop and elapsed == 127):
          peers.append(receipt.as_reader())
        card.sm.update_msgs((now + 1000) / 1e9, peers)
        published = {}
        real_new = messaging.new_message

        def stamped(service=None, size=None, *, _real_new=real_new, _now=now, **kwargs):
          return _real_new(service, size, **({'logMonoTime': _now} | kwargs))

        def capture(service, msg, _published=published):
          wire = msg if isinstance(msg, bytes) else msg.to_bytes()
          _published[service] = messaging.log_from_bytes(wire)

        if source_missing:
          published = {key: value for key, value in last_card_published.items() if key != 'sendcan'}
        else:
          with (
            patch.object(card.sm, 'update'),
            patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw', return_value=[b'can']),
            patch('openpilot.selfdrive.car.card.can_capnp_to_list', return_value=list(pending_host_can) if source_outage else [(now, incoming)]),
            patch('openpilot.selfdrive.car.card.REPLAY', False),
            patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now + 2000),
            patch('openpilot.selfdrive.car.card.time.monotonic', return_value=(now + 2000) / 1e9),
            patch('openpilot.selfdrive.car.card.time.clock_gettime_ns', return_value=now + 2000),
            patch('openpilot.selfdrive.car.card.messaging.new_message', side_effect=stamped),
            patch.object(card.pm, 'send', side_effect=capture),
          ):
            card.step()
          last_card_published = dict(published)
          pending_host_can.clear()
        if tick % 10 == 0:
          published['pandaStates'] = panda.as_reader()
        cs = published['carState'].carState
        assert cs.brakePressed == brake and cs.gearShifter == (structs.CarState.GearShifter.reverse if reverse else structs.CarState.GearShifter.drive)
        if tick >= 10:
          assert cs.canValid and safety.safety_config_valid()
        if hard_stop_receipt and not host_first_stop:
          _assert_hard_stop_fault_guards(card, cs, now + 2000)
        # Deliver each genuine10Hz native receipt once; do not refresh receive time/frequency from a cached snapshot.
        new_native = receipt if receipt is not None and (tick % 10 == 1 or hard_stop_receipt) else None
        if deadline_gap and elapsed == gap_tick:
          prior = driven['aolAxisState'].aolAxisState
          assert prior.lateralActive and now + 2000 > prior.validUntilMonoTime
          with patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now + 2000):
            expired, _ = controls.state_control()
          assert not expired.latActive and not expired.longActive
        driven_new = selfdrive_step(sd, published, new_native, previous_controls, cp, safety, now, calibration='calibrated',
                                   source_timeout=source_missing)
        if deadline_gap and elapsed == gap_tick:
          current = driven_new['aolAxisState'].aolAxisState
          assert current.lateralActive and not sd.aol_authority_lost, 'SUBARU_FRESH_SUCCESSOR_LOST_LATERAL'
          assert current.validUntilMonoTime > prior.validUntilMonoTime
          assert current.sourceCarStateMonoTime == published['carState'].logMonoTime
          assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
        driven.update(driven_new)
        order = None
        if aol and tick % 10 == 0:
          receipt, order = pandad.step(driven, published, now)
        feed_perception_only(controls, now, elapsed, enabled=sd.enabled, active=sd.active)
        plan = messaging.new_message('longitudinalPlan', valid=True, logMonoTime=now)
        plan.longitudinalPlan.aTarget = 0.5
        control_peers = [published['carState'], published['carOutput'], plan.as_reader()]
        control_peers.extend(driven_new[s] for s in ('selfdriveState', 'onroadEvents', 'aolAxisState') if s in driven_new and s in controls.sm.data)
        if receipt is not None and tick % 10 == 0:
          control_peers.append(receipt.as_reader())
        controls.sm.update_msgs((now + 1000) / 1e9, control_peers)
        previous_controls = {}

        def capture_control(service, msg, _controls=previous_controls):
          wire = msg if isinstance(msg, bytes) else msg.to_bytes()
          _controls[service] = messaging.log_from_bytes(wire)

        with (
          patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now + 2000),
          patch('openpilot.cereal.messaging.time.monotonic', return_value=(now + 2000) / 1e9),
          patch.object(controls.pm, 'send', side_effect=capture_control),
        ):
          command, lateral_log = controls.state_control()
          controls.publish(command, lateral_log)
        latest_controls.update(previous_controls)
        command = previous_controls['carControl'].carControl
        outgoing = can_capnp_to_list([published['sendcan'].as_builder().to_bytes()], msgtype='sendcan')[0][1] if 'sendcan' in published else []
        tx = [(a, d.hex(), b, bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(a, b, d)))) for a, d, b in outgoing]
        decoder.update([(now, outgoing)])
        dash_decoder.update([(now, outgoing)])
        if any(address == 0x322 for address, _, _ in outgoing):
          dash = dash_decoder.vl['ES_LKAS_State']
          expected = ci.CC.angle_was_active if steering_name == 'ES_LKAS_ANGLE' else card.sm['carControl'].latActive
          assert bool(dash['LKAS_ACTIVE']) == expected, (candidate, tick, expected, dash)
          assert dash['LKAS_Dash_State'] == (2 if expected else 0), (candidate, tick, expected, dash)
          assert all(accepted for address, _, _, accepted in tx if address == 0x322)
          dash_emissions += 1
        decoded_steering = []
        for address, _data, _bus, accepted in tx:
          if address == steering_address:
            request = bool(decoder.vl[steering_name]['LKAS_Request'])
            output = decoder.vl[steering_name]['LKAS_Output']
            decoded_steering.append({'request': request, 'output': output, 'nativeAccepted': accepted})
            if crawl and 95 <= tick < 105:
              assert aol and main_only and cs.brakePressed and not command.enabled
              assert request and accepted, ('CRAWL_AOL_BRAKE_WITHDREW', tick, decoded_steering)
            if not stopped and (65 <= tick < 90 and not (source_outage and 75 <= tick < 84) or
                                aol and (first_resume or 205 <= tick < 220 or 280 <= tick < 300)):
              if angle_handoff and 72 <= tick < 78:
                assert not request and accepted, (tick, decoded_steering)
              else:
                assert request, (tick, decoded_steering)
                assert accepted, ('SUBARU_ANGLE_HANDOFF_NATIVE_REJECTED', tick, decoded_steering)
              if output != 0:
                active_emissions.append(tick)
            if 165 <= tick < 180 or 240 <= tick < 255 or not aol and (125 <= tick < 145 or 205 <= tick < 220 or 280 <= tick < 300):
              if angle_handoff:
                assert not request and abs(output - cs.steeringAngleDeg) <= 0.011, (tick, decoded_steering)
              else:
                assert not request and output == 0, (tick, decoded_steering)
        intent = decode_intent(published['aolIntentWire'].aolIntentWire) if aol else None
        row = {
          'tick': tick,
          'elapsed': elapsed,
          'clearReason': card.aol_card_intent.latch_clear_reason if card.aol_card_intent is not None else None,
          'hardStopOrder': hard_order if hard_stop_receipt else None,
          'intent': bool(intent and intent.allowedLatch),
          'armed': bool(intent and intent.lateralArmed),
          'cardLatch': bool(card.aol_card_intent and card.aol_card_intent.allowed_latch),
          'lat': command.latActive,
          'long': command.longActive,
          'enabled': sd.enabled,
          'active': sd.active,
          'permission': int(safety.aol_get_permission_mask()),
          'controls': bool(safety.get_controls_allowed()),
          'nativeNewToSD': new_native is not None,
          'pandaNew': tick % 10 == 0,
          'nativeSource': None if receipt is None else int(receipt.logMonoTime),
          'order': order,
          'tx': tx,
          'steering': decoded_steering,
          'fault': str(driven['aolAxisState'].aolAxisState.faultReason) if aol else 'unsupported',
          'events': [str(e.name) for e in driven['onroadEvents'].onroadEvents],
        }
        rows.append(row)
        if stopped:
          assert not row['lat'] and row['cardLatch'] and row['permission'] == 0 and row['fault'] == 'none', ('SUBARU_STOP_LOST_ARMED_INTENT', row)
        if source_outage and 78 <= tick < 81:
          assert not row['lat'] and not row['long'] and row['fault'] == 'availabilityPause', ('SUBARU_SOURCE_GAP_BECAME_CRITICAL', row)
        if source_outage and 84 <= tick < 90:
          assert row['lat'] and row['cardLatch'] and row['permission'] == 1 and not row['controls'], row
        assert not command.longActive, row
        if 10 <= tick < 30:
          assert not command.latActive and not bool(intent and intent.allowedLatch) and row['permission'] == 0, row
        if 65 <= tick < 90 and not (source_outage and 75 <= tick < 84):
          assert command.latActive and bool(intent and intent.allowedLatch) == aol and row['permission'] == int(aol), row
        if not stopped and (first_resume or 205 <= tick < 220 or 280 <= tick < 300):
          assert command.latActive == aol and bool(intent and intent.allowedLatch) == aol and row['permission'] == int(aol) and not row['controls'], row
        if 165 <= tick < 180:
          assert (not command.latActive and not bool(intent and intent.allowedLatch) and
                bool(intent and intent.lateralArmed) == aol and row['cardLatch'] == aol and row['permission'] == 0), row
        if 240 <= tick < 255:
          assert not command.latActive and bool(intent and intent.lateralArmed) == aol and row['cardLatch'] == aol and row['permission'] == 0, row
        main_rearm = aol and candidate in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2020, CAR.SUBARU_OUTBACK_2023)
        if 330 <= tick < (350 if main_rearm else 400):
          assert not command.latActive and not bool(intent and intent.allowedLatch), row
        if main_rearm and 375 <= tick < 400:
          assert command.latActive and row['cardLatch'] and row['permission'] == 1 and not row['controls'], row
        if tick >= 440:
          assert command.latActive and bool(intent and intent.allowedLatch) == aol and row['permission'] == int(aol), row
      assert len(active_emissions) >= 4, rows[65:90]
      assert dash_emissions >= 40
    finally:
      pandad.close()
      (tmp_path / (str(candidate) + '-' + str(aol) + '-actual.json')).write_text(json.dumps(rows, indent=2) + '\n')


@pytest.mark.parametrize('candidate', [CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_FORESTER, CAR.SUBARU_OUTBACK])
def test_actual_stop_retains_lateral_intent_and_resumes(candidate, subaru_native_pair, tmp_path):
  test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(candidate, subaru_native_pair, tmp_path, stop_resume=True)
  rows = json.loads((tmp_path / (str(candidate) + '-True-actual.json')).read_text())
  commands = [command for row in rows if 185 <= row['tick'] < 195 for command in row['steering']]
  assert commands and all(not command['request'] and command['nativeAccepted'] for command in commands)


def _assert_hard_stop_fault_guards(card, state, now):
  feedback, sm = card.aol_transport_feedback, card.sm
  axis, stamp = sm['aolAxisState'], int(sm.logMonoTime['aolAxisState'])

  def retained(sample, at=now):
    return feedback._angle_stop_intent_current(sm, card.CP, sample, axis, stamp, at,
      latched=True, producer=card.slc_producer_session)

  assert retained(state)
  for field, value in (('canValid', False), ('canTimeout', True), ('steerFaultPermanent', True),
                       ('steerFaultTemporary', True), ('accFaulted', True)):
    invalid = state.as_builder()
    setattr(invalid, field, value)
    assert not retained(invalid.as_reader()), field
  rolling = state.as_builder()
  rolling.standstill = False
  rolling.vEgoRaw = rolling.vEgo = 1.0
  assert not retained(rolling.as_reader())
  assert not retained(state, now + 200_000_001)
  for service in ('aolSafetyWire', 'onroadEvents', 'pandaStates'):
    original = sm.valid[service]
    try:
      sm.valid[service] = False
      assert not retained(state), service
    finally:
      sm.valid[service] = original
  original = sm.data['pandaStates']
  try:
    for field, value in (('safetyRxChecksInvalid', True), ('heartbeatLost', True), ('faults', ['relayMalfunction'])):
      invalid = messaging.new_message('pandaStates', len(original))
      for index, panda in enumerate(original):
        invalid.pandaStates[index] = panda
      setattr(invalid.pandaStates[0], field, value)
      sm.data['pandaStates'] = invalid.as_reader().pandaStates
      assert not retained(state), field
  finally:
    sm.data['pandaStates'] = original
  original = sm.data['onroadEvents']
  try:
    mismatch = messaging.new_message('onroadEvents', 1)
    mismatch.onroadEvents[0].name = log.OnroadEvent.EventName.controlsMismatch
    mismatch.onroadEvents[0].immediateDisable = True
    sm.data['onroadEvents'] = mismatch.as_reader().onroadEvents
    assert not retained(state)
  finally:
    sm.data['onroadEvents'] = original
  assert retained(state)


@pytest.mark.parametrize('candidate', [CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_FORESTER, CAR.SUBARU_OUTBACK])
def test_actual_native_first_hard_stop_retains_intent_and_resumes(candidate, subaru_native_pair, tmp_path):
  test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(
    candidate, subaru_native_pair, tmp_path, stop_resume=True, hard_stop_native_first=True)
  rows = json.loads((tmp_path / (str(candidate) + '-True-actual.json')).read_text())
  stopped = [row for row in rows if 125 <= row['tick'] < 135]
  assert len(stopped) == 300
  assert all(row['cardLatch'] and not row['lat'] and row['permission'] == 0 for row in stopped)
  commands = [command for row in stopped for command in row['steering']]
  assert commands
  if candidate in (CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_LEGACY_2025):
    assert all(not command['request'] and command['nativeAccepted'] for command in commands)
  else:
    # Card can hold the preceding Controls command on the first torque frame.
    # Native must reject that stale request; subsequent commands must be inactive.
    assert all(not command['request'] or not command['nativeAccepted'] for command in stopped[0]['steering'])
    settled = [command for row in stopped if row['elapsed'] >= 127 for command in row['steering']]
    assert settled and all(not command['request'] and command['nativeAccepted'] for command in settled)


@pytest.mark.parametrize('candidate', [CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2020, CAR.SUBARU_OUTBACK_2023])
def test_actual_main_without_set_retains_native_first_stop_intent(subaru_native_pair, tmp_path, candidate):
  test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(
    candidate, subaru_native_pair, tmp_path,
    stop_resume=True, hard_stop_native_first=True, main_only=True)
  rows = json.loads((tmp_path / (str(candidate) + '-True-actual.json')).read_text())
  assert all(not row['controls'] and not row['enabled'] and not row['long'] for row in rows)
  assert all(row['lat'] and row['permission'] == 1 for row in rows if 65 <= row['tick'] < 90)
  assert all(not row['lat'] and row['cardLatch'] and row['permission'] == 0 for row in rows if 125 <= row['tick'] < 135)


@pytest.mark.parametrize('candidate', [CAR.SUBARU_IMPREZA_2020, CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_OUTBACK_2023])
def test_actual_fresh_subaru_source_after_expired_output(candidate, subaru_native_pair, tmp_path):
  test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(
    candidate, subaru_native_pair, tmp_path, main_only=True, deadline_gap=True)


@pytest.mark.parametrize('event', [log.OnroadEvent.EventName.buttonCancel, log.OnroadEvent.EventName.pedalPressed])
def test_actual_subaru_expired_output_keeps_user_disable(event, subaru_native_pair, tmp_path):
  original = SelfdriveD._expire_axis_continuity

  class ObservedVeto(Exception):
    pass

  def check(sd, state, now_ns):
    proof = getattr(sd, 'aol_step_input_proof', None)
    if proof is not None and now_ns > proof[3] and sd.aol_axis_decision.lateral_active:
      sd.events.add(event)
      assert original(sd, state, now_ns)
      assert not sd.aol_axis_decision.lateral_active and not sd.aol_axis_decision.longitudinal_active
      assert sd.aol_authority_lost and event in sd.events.names
      raise ObservedVeto
    return original(sd, state, now_ns)

  with patch.object(SelfdriveD, '_expire_axis_continuity', check), pytest.raises(ObservedVeto):
    test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(
      CAR.SUBARU_IMPREZA_2020, subaru_native_pair, tmp_path, main_only=True, deadline_gap=True)


@pytest.mark.parametrize('candidate', [CAR.SUBARU_OUTBACK_2023, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_ASCENT_2023])
def test_actual_fixed_angle_driver_handoff_keeps_native_rate_limit(candidate, subaru_native_pair, tmp_path, preferences=None):
  test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(
    candidate, subaru_native_pair, tmp_path, preferences=preferences, fixed_angle_handoff=True)


@pytest.mark.parametrize('candidate', [CAR.SUBARU_IMPREZA_2020, CAR.SUBARU_OUTBACK_2023, CAR.SUBARU_CROSSTREK_2025])
def test_actual_host_first_stop_retains_subaru_intent(candidate, subaru_native_pair, tmp_path):
  test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(
    candidate, subaru_native_pair, tmp_path, main_only=True, stop_resume=True, host_first_stop=True)


@pytest.mark.parametrize('candidate', [CAR.SUBARU_IMPREZA_2020, CAR.SUBARU_OUTBACK_2023, CAR.SUBARU_CROSSTREK_2025])
def test_actual_held_gas_source_outage_withdraws_and_recovers_subaru(candidate, subaru_native_pair, tmp_path):
  test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(
    candidate, subaru_native_pair, tmp_path, main_only=True, source_outage=True)


@pytest.mark.parametrize('candidate', [CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_FORESTER_2022])
@pytest.mark.parametrize('direction', [-1, 1])
def test_actual_vm_angle_driver_handoff_keeps_native_rate_limit(candidate, direction, subaru_native_pair, tmp_path):
  test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(
    candidate, subaru_native_pair, tmp_path, vm_angle_handoff=direction)


def test_actual_crawl_lateral_only_tracks_small_targets(subaru_native_pair, tmp_path):
  test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(
    CAR.SUBARU_CROSSTREK_2025, subaru_native_pair, tmp_path, main_only=True, crawl=True)
