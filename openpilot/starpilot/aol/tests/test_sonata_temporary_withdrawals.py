from typing import Any
import dataclasses
import json
import pytest
import importlib
import os
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.radar_interface import RadarInterface
from opendbc.car.hyundai.values import CAR
from opendbc.car.hyundai.classic_scc_aol import qualified as qualified_classic
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.test_hyundai import checksum
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module
from openpilot.starpilot.aol.intent import AOL_TOGGLE, read_settings
from openpilot.starpilot.aol.runtime import current_native

from openpilot.starpilot.aol.tests import test_ioniq_availability as caller_fixture
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety
from openpilot.starpilot.car.hyundai.classic_scc_intent import ClassicSccCardIntent
from openpilot.cereal import log
from openpilot.starpilot.aol.tests.hyundai_cpp_order import hkg_pandad_order as hkg_pandad_order

disable_ecu_module = importlib.import_module('opendbc.car.disable_ecu')


def classic_frames(ci, tick, *, main=0, main_pressed=0, lkas=0, button=0, engaged=False, brake=False, steer_fault=False, gas=False):

  assert ci.CP.carFingerprint == CAR.HYUNDAI_SONATA_HYBRID
  packer = CANPacker('hyundai_can_generated')


  hz = {'E_EMS11':100, 'ELECT_GEAR':20, 'SCC11':50, 'SCC12':50, 'WHL_SPD11':100, 'TCS13':100, 'MDPS12':50, 'CLU11':50,
        'BCM_PO_11':50, 'LKAS11':100}
  scheduled = {(packer.dbc.name_to_msg[name].address, 0): name for name in hz if name != 'LKAS11'}
  for parser in ci.can_parsers.values():
    for address in parser.addresses:
      scheduled[(address, parser.bus)] = parser.dbc.addr_to_msg[address].name
  frames = []
  for (address,bus),name in sorted(scheduled.items()):
    parser = next((p for p in ci.can_parsers.values() if p.bus == bus), None)
    declared = parser.message_states[address].frequency if parser is not None and address in parser.addresses else 0
    frequency = hz.get(name, min(declared,100) if 0 < declared <= 100 else 100)
    period = max(1,round(100/frequency))
    if tick % period:
      continue
    counter = tick // period
    values = {}
    if name == 'CLU11':
      values.update(CF_Clu_AliveCnt1=counter%16, CF_Clu_CruiseSwState=button, CF_Clu_CruiseSwMain=main_pressed)
    elif name == 'E_EMS11':
      values.update(CR_Vcu_AccPedDep_Pos=20 if gas else 0)
    elif name == 'WHL_SPD11':
      values.update(WHL_SPD_AliveCounter_LSB=counter%4, WHL_SPD_AliveCounter_MSB=(counter//4)%4,
                    WHL_SPD_FL=40,WHL_SPD_FR=40,WHL_SPD_RL=40,WHL_SPD_RR=40)
    elif name == 'TCS13':
      values.update(AliveCounterTCS=counter%8, ACCEnable=0, ACC_REQ=int(engaged), DriverOverride=2 if brake else 0)
    elif name == 'MDPS12':
      values.update(CR_Mdps_StrColTq=0, CR_Mdps_OutTq=0, CF_Mdps_ToiFlt=int(steer_fault))
    elif name == 'BCM_PO_11':
      values['LDA_BTN']=lkas
    elif name == 'CGW1':
      values['CF_Gway_DrvSeatBeltSw']=1
    elif name == 'ELECT_GEAR':
      values['Elect_Gear_Shifter']=5
    elif name == 'SCC11':
      values['MainMode_ACC']=main
    elif name == 'SCC12':
      values.update(ACCMode=int(engaged), CR_VSM_Alive=counter%16)
    elif name == 'SAS11':
      values['SAS_Angle']=1.
    elif name == 'CLU15':
      values['CF_Clu_VehicleSpeed']=40
    frame = packer.make_can_msg(name,bus,values)
    if name in ('SCC12','WHL_SPD11','TCS13'):
      address, data, bus = checksum(frame)
      frame = (address, bytes(data), bus)
    frames.append(frame)
  assert not ci.CP.openpilotLongitudinalControl and ci.CP.pcmCruise
  assert all(address!=0x340 or bus==2 for address,_,bus in frames), 'Never echo outgoing LKAS on protected RX bus'
  return frames


def _invalid_controls_emits_no_commands(received):
  if 'sendcan' not in received:
    return True
  envelope = received['sendcan']
  return envelope.which() == 'sendcan' and not envelope.valid and len(envelope.sendcan) == 0


@pytest.mark.parametrize('raw_fault', [0, 8])
@pytest.mark.parametrize('withdrawal', ['brake', 'cruise-cancel'])
@pytest.mark.parametrize('temporary', ['eps', 'source', 'axis'])
def test_classic_stock_temporary_withdrawal_recovers_with_real_cpp(
    raw_fault, withdrawal, temporary, monkeypatch, hkg_pandad_order, *, main_off=False, gas_override=False):
  shared_long = False
  native = libsafety_py.libsafety
  case = caller_fixture.IoniqAvailabilityTests(methodName='runTest')
  card = None
  protocol = None
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put_bool('AlwaysOnLateral', True, block=True)
    saved.put('LKASButtonControl', AOL_TOGGLE, block=True)
    saved.put('MainCruiseButtonControl', AOL_TOGGLE, block=True)
    now = [10_000_000_000]
    for key in ('CancelButtonControl', 'LongCancelButtonControl', 'VeryLongCancelButtonControl'):
      saved.put(key, 0, block=True)
    try:
      saved.put_bool('AlphaLongitudinalEnabled', False, block=True)
      saved.put_bool('IsReleaseBranch', False, block=True)
      fp = gen_empty_fingerprint()
      fp[0].update({0x391: 8, 0x38d: 8})
      fp[2][0x485] = 4
      fp[1][0x500] = 8
      cp = CarInterface.get_params(CAR.HYUNDAI_SONATA_HYBRID, fp, [], False, False, False)
      assert int(cp.flags) == 0x1011580
      assert qualified_classic(cp) and cp.pcmCruise and not cp.openpilotLongitudinalControl
      assert cp.lateralTuning.which() == 'torque' and not CarInterface.startup_required(cp)
      card = card_module.Car(CI=CarInterface(cp), RI=RadarInterface(cp))
      assert qualified_classic(card.CP, marked_only=True)
      assert card.CP.safetyConfigs[0].safetyParam == 0xC02 and card.CP.alternativeExperience == 32
      assert isinstance(card.aol_card_intent, ClassicSccCardIntent)
      assert not card.ci_initialized and not saved.get_bool('ControlsReady')
      card.slc_producer_session = 'authority-card'
      card.CI.update([])
      sd, controls, drive = case._authority_callers(False, cp=card.CP.as_reader().as_builder(), genuine_events=True)

      sd.aol_settings = read_settings(saved)
      assert sd.aol_settings.lkas_action == AOL_TOGGLE and sd.aol_settings.main_action == AOL_TOGGLE
      assert sd.CP.to_dict() == card.CP.to_dict() == controls.CP.to_dict()
      assert len(card.CP.safetyConfigs) == 1 and bool(card.CP.openpilotLongitudinalControl) == shared_long
      config = card.CP.safetyConfigs[0]
      model, word = int(config.safetyModel.raw), int(config.safetyParam)
      native.set_alternative_experience(card.CP.alternativeExperience)
      assert native.set_safety_hooks(model, word) == 0
      protocol = hkg_pandad_order(native, libsafety_py.libpath)
      native.init_tests()
      native.set_alternative_experience(card.CP.alternativeExperience)
      queued, received, previous_sd = [], {}, {}
      tick = 0
      native_counts = {'accepted': 0, 'blocked': 0, 'nonzero': 0, 'lateral_only_nonzero': 0}
      freeze_native = [None]
      last_native = [None]
      telemetry_rate_bad = [False]
      patch_scope = monkeypatch.context()
      local = patch_scope.__enter__()
      try:
        diagnostic_requests = []
        def diagnostic_query(sender, receiver, bus, addresses, requests, responses):
          assert bus == 0 and addresses == [(0x7d0, None)]
          assert len(requests) == 1
          request = requests[0]
          assert request in (b'\x10\x03', b'\x28\x83\x01')
          diagnostic_requests.append(request)
          def receive(timeout):
            sender([(0x7d0, bytes([len(request)]) + request + bytes(7-len(request)), bus)])
            return {(0x7d0, None): b'\x50\x03'} if request == b'\x10\x03' else {}
          return SimpleNamespace(get_data=receive)
        local.setattr(disable_ecu_module, 'IsoTpParallelQuery', diagnostic_query)
        local.setattr(card_module, 'REPLAY', False)
        local.setattr(card_module.time, 'monotonic_ns', lambda: now[0])
        local.setattr(card_module.time, 'clock_gettime_ns', lambda clock: now[0])
        local.setattr(card_module.messaging, 'drain_sock_raw', lambda *args, **kwargs: list(queued))
        local.setattr(card.sm, 'update', lambda *args: None)
        def panda_cadence(original):
          due = [0.0]
          def update(stamp, messages):
            delivered = []
            for message in messages:
              if message.which() == 'pandaStates':
                if stamp + 1e-9 < due[0]:
                  continue
                due[0] = stamp + (0.0 if telemetry_rate_bad[0] else 0.1)
              delivered.append(message)
            return original(stamp, delivered)
          return update
        local.setattr(sd.sm, 'update_msgs', panda_cadence(sd.sm.update_msgs))
        local.setattr(card.sm, 'update_msgs', panda_cadence(card.sm.update_msgs))
        create_message = messaging.new_message
        def clocked_message(service=None, size=None, **kwargs):
          kwargs.setdefault('logMonoTime', now[0])
          event = create_message(service, size, **kwargs)
          if service == 'aolSafetyWire' and freeze_native[0] is not None:
            event.logMonoTime = freeze_native[0].observedMonoTime
          if service == 'pandaStates' and raw_fault:
            for panda in event.pandaStates:
              panda.faults = ['interruptRateCan2']
          return event
        local.setattr(messaging, 'new_message', clocked_message)
        original_encode = caller_fixture.encode_safety
        def recorded_fault_wire(state):
          state = dataclasses.replace(state, pandaInventory=tuple(
            dataclasses.replace(slot, faults=slot.faults | raw_fault) for slot in state.pandaInventory))
          last_native[0] = state
          return original_encode(freeze_native[0] or state)
        local.setattr(caller_fixture, 'encode_safety', recorded_fault_wire)
        send = card.pm.send
        def capture(service, event):
          raw = event if isinstance(event, bytes) else event.to_bytes()
          received[service] = messaging.log_from_bytes(raw).as_builder()
          return send(service, raw)
        local.setattr(card.pm, 'send', capture)

        def step(*, main=1, main_pressed=0, lkas=0, button=0, engaged=False, withdraw_request=False, bad_checksum=False,
                 heartbeat_override=None, inject_relay=False, drop_wheel=False, brake=False, steer_fault=False, drop_state=False, hold_axis=False):
          nonlocal tick
          tick += 1
          now[0] = source_ns = 10_000_000_000 + tick * 10_000_000
          prior_axis = previous_sd.get('aolAxisState')
          prior_state = previous_sd.get('selfdriveState')
          request = 0 if prior_axis is None else (
            int(prior_axis.aolAxisState.desiredLateral) | int(prior_axis.aolAxisState.desiredLongitudinal) << 1)
          if withdraw_request:
            request = 0
          heartbeat = bool(request or (prior_state is not None and prior_state.selfdriveState.enabled))
          native.set_timer(source_ns // 1000)
          if 'aolAxisState' in previous_sd and 'aolIntentWire' in received:
            _, handshake = protocol.step(previous_sd, received, source_ns)
            assert handshake['plan'][0], handshake
            if withdraw_request:
              native.aol_set_host_request(0)
            if heartbeat_override is not None:
              native.set_aol_test_heartbeat(heartbeat_override)
          else:
            native.set_aol_test_heartbeat(heartbeat if heartbeat_override is None else heartbeat_override)
            native.aol_set_host_request(request)
          main = 0 if main_off else main
          frames = classic_frames(card.CI, tick, main=main, main_pressed=main_pressed, lkas=lkas, button=button,
                                  engaged=engaged, brake=brake, steer_fault=steer_fault, gas=gas_override)
          if drop_wheel:
            frames = [frame for frame in frames if frame[0] != 0x386]
          if bad_checksum:
            frames = [(address, bytes([data[0] ^ 1]) + data[1:] if address == 0x386 else data, bus)
                      for address, data, bus in frames]
          if inject_relay:
            camera = next(frame for frame in frames if frame[0] == 0x340 and frame[2] == 2)
            frames.append((camera[0], camera[1], 0))
          for address, data, bus in frames:
            accepted = native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
            assert bool(accepted) == (not bad_checksum or address != 0x386), (hex(address), bus)
          if tick % 100 == 0:
            native.safety_tick()
          actual_request = native.aol_get_request_mask()
          mask = native.aol_get_permission_mask()



          rx_invalid = bool(native.safety_test_toyota_rx_summary_invalid())
          relay_fault = bool(native.get_relay_malfunction())
          assert not relay_fault or inject_relay
          batch = messaging.log_from_bytes(card_module.can_list_to_can_capnp(frames)).as_builder()
          batch.logMonoTime = source_ns
          queued[:] = [batch.to_bytes()]
          device = messaging.new_message('deviceState', valid=True, logMonoTime=source_ns)
          device.deviceState.started, device.deviceState.startedMonoTime = True, 9_000_000_000
          pandas = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=source_ns)
          panda = pandas.pandaStates[0]
          panda.safetyModel, panda.safetyParam = config.safetyModel, word
          panda.alternativeExperience = card.CP.alternativeExperience
          panda.controlsAllowed = native.get_controls_allowed()
          panda.safetyRxChecksInvalid = rx_invalid
          panda.ignitionLine = True
          panda.faults = (['interruptRateCan2'] if raw_fault else []) + (['relayMalfunction'] if relay_fault else [])
          slot = PandaSlot(0, 'authority-panda', model, word, card.CP.alternativeExperience,
                           native.get_controls_allowed(), safetyRxChecksInvalid=rx_invalid, faults=raw_fault | int(relay_fault))
          wire = messaging.new_message('aolSafetyWire', 0, valid=protocol.compatible, logMonoTime=source_ns)
          wire.aolSafetyWire = encode_safety(SafetyState(1, protocol.compatible, source_ns, source_ns + 30_000_000,
            model, word, bool(mask & 1), bool(mask & 2), bool(actual_request & 1), bool(actual_request & 2),
            'authority-panda', protocol.axis_session, source_ns, (slot,)))
          messages = [device.as_reader(), pandas.as_reader(), wire.as_reader()]
          messages += [previous_sd[name] for name in ('selfdriveState', 'aolAxisState', 'onroadEvents') if name in previous_sd]
          if 'onroadEvents' not in previous_sd:
            messages.append(messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=source_ns).as_reader())
          card.sm.update_msgs(source_ns / 1e9, messages)
          state, _ = card.state_update()
          if state.canValid and tick % 2 == 0:
            assert state.cruiseState.available == bool(main)
          for service in ('carState', 'carOutput', 'aolIntentWire', 'starpilotCarState'):
            received.pop(service, None)
          card.state_publish(state, None)
          state_envelope = received['carState']
          output_envelope = received['carOutput']
          assert state_envelope.logMonoTime == source_ns and state_envelope.valid == state.canValid
          assert output_envelope.logMonoTime == source_ns

          axis, host, command = drive(tick, timeout=drop_state, intent_message=received['aolIntentWire'],
            car_state_message=state_envelope, car_output_message=output_envelope,
            companion=False, authority_message=received.get('starpilotCarState'),
            allowed=bool(mask & 1), long_allowed=bool(mask & 2), native_request_lat=bool(actual_request & 1),
            native_request_long=bool(actual_request & 2), panda_controls_allowed=native.get_controls_allowed(),
            panda_fault=rx_invalid, relay_fault=relay_fault)
          if sd.conditional_car_state_valid and not drop_state:
            assert sd.aol_car_state_log_ns == state_envelope.logMonoTime
          if drop_state:
            assert sd.aol_car_state_log_ns < state_envelope.logMonoTime
          assert controls.sm.logMonoTime['carState'] == state_envelope.logMonoTime
          assert controls.sm.valid['carState'] == state_envelope.valid
          assert controls.sm['carState'].to_dict() == state_envelope.carState.to_dict()
          assert controls.sm.logMonoTime['carOutput'] == output_envelope.logMonoTime
          assert controls.sm.valid['carOutput'] == output_envelope.valid
          assert controls.sm['carOutput'].to_dict() == output_envelope.carOutput.to_dict()
          # Delay only delivery to Card/pandad; Controls still consumes its own real current inputs.
          if not hold_axis:
            previous_sd.update(sd.pm.messages)
          validated = current_native(sd.sm, sd.CP, now_ns=source_ns, axis_session_id=sd.aol_session_id)
          if not protocol.compatible or not protocol.axis_session:
            assert not axis.lateralActive and not axis.longitudinalActive
            assert not command.latActive and not command.longActive
            assert native.aol_get_permission_mask() == 0
          if (not rx_invalid and not relay_fault and freeze_native[0] is None and state.canValid and native.safety_config_valid() and
              protocol.compatible and protocol.axis_session):
            assert validated is not None, 'RECORDED_IRQ_ONLY_CURRENT_NATIVE_REJECTED'
            assert validated is not None and validated.lateralAllowed == bool(mask & 1)
            assert validated.requestedLateral == bool(actual_request & 1)
            assert validated.pandaInventory[0].faults == raw_fault
            assert list(sd.sm['pandaStates'][0].faults) == ([log.PandaState.FaultType.interruptRateCan2] if raw_fault else [])
          else:
            assert not axis.lateralActive and not axis.longitudinalActive
            assert not command.latActive and not command.longActive

          now[0] += 1
          card.sm.update_msgs(now[0] / 1e9, [controls.pm.messages['carControl']])
          received.pop('sendcan', None)
          card.controls_update(state, card.sm['carControl'])
          steering = []
          if state.canValid:
            assert 'sendcan' in received and received['sendcan'].valid
            steering = [frame for frame in received['sendcan'].sendcan
                        if frame.address == 0x340 and frame.src == 0]
            assert steering
            for frame in received['sendcan'].sendcan:
              accepted = bool(native.safety_tx_hook(libsafety_py.make_CANPacket(frame.address, frame.src, frame.dat)))
              assert accepted == (not relay_fault)
              native_counts['accepted' if accepted else 'blocked'] += 1
              if accepted and frame.address == 0x340 and (int.from_bytes(bytes(frame.dat)[2:4], 'little') & 0x7ff) != 1024:
                native_counts['nonzero'] += 1
                native_counts['lateral_only_nonzero'] += int(not native.get_controls_allowed())
          return state, axis, host, command, steering

        for _ in range(120):
          step(main=0)
        assert native.safety_config_valid()
        # Real Card constructed CI before applying the native AOL marker.
        # The mapped physical button arms without SET or stock MAIN availability.
        for _ in range(14):
          step(main=1, main_pressed=int(not main_off), lkas=int(main_off))
        nonzero = False
        for _ in range(35):
          state, axis, host, command, steering = step(main=1, engaged=False)
          nonzero |= abs(command.actuators.torque) > 0
        assert axis.lateralActive and command.latActive, axis.to_dict()
        assert not host.enabled and not native.get_controls_allowed()
        assert not state.cruiseState.enabled and nonzero
        assert not axis.longitudinalActive and not command.longActive
        # First LKAS press turns the same mapped session off; its next fresh
        # press restores it. Held repeats and stock MAIN do not overwrite it.
        for _ in range(10):
          state, axis, host, command, steering = step(main=1, lkas=1)
        for _ in range(10):
          state, axis, host, command, steering = step(main=1)
        assert not axis.desiredLateral and not axis.lateralActive and not command.latActive
        for _ in range(10):
          state, axis, host, command, steering = step(main=1, lkas=1)
        for _ in range(10):
          state, axis, host, command, steering = step(main=1)
        assert axis.lateralActive and command.latActive
        assert not host.enabled and not native.get_controls_allowed()
        assert card.aol_card_intent.allowed_latch
        if temporary == 'eps':
          for _ in range(16):
            state, axis, host, command, steering = step(main=1, steer_fault=True)
            if state.steerFaultTemporary:
              assert not command.latActive and not axis.lateralActive
              assert card.aol_card_intent.allowed_latch
        elif temporary == 'source':
          for index in range(4):
            state, axis, host, command, steering = step(main=1, drop_state=True)
            if index >= 3:
              assert not command.latActive and not axis.lateralActive
        else:
          # Recorded Ioniq6 A5 had59.971ms without an axis publication while
          # Card kept fresh armed intent. The native output request must drop
          # after30ms; its retained physical arm must not become a fresh grant.
          for index in range(6):
            state, axis, host, command, steering = step(main=1, hold_axis=True)
            if index >= 3:
              assert not command.latActive and native.aol_get_permission_mask() == 0
        for _ in range(30):
          state, axis, host, command, steering = step(main=1)
        assert not state.steerFaultTemporary and card.aol_card_intent.allowed_latch
        assert axis.lateralActive and command.latActive
        gas_override = False
        observations: list[dict[str, Any]] = []
        for index in range(20):
          state, axis, host, command, steering = step(main=1, engaged=False,
            brake=withdrawal == 'brake', button=4 if withdrawal == 'cruise-cancel' and index < 2 else 0)
          observations.append({'cruise_enabled': state.cruiseState.enabled, 'main_available': state.cruiseState.available,
            'brake': state.brakePressed, 'buttons': [event.to_dict() for event in state.buttonEvents],
            'native_before_allowed': last_native[0].lateralAllowed, 'native_before_requested': last_native[0].requestedLateral,
            'native_controls': native.get_controls_allowed(), 'native_after_mask': native.aol_get_permission_mask(),
            'axis': axis.to_dict(), 'command_lat': command.latActive, 'command_long': command.longActive,
            'intent_allowed': card.aol_card_intent.allowed_latch, 'fault_rearm': card.aol_card_intent.fault_rearm,
            'pause_lateral': card.aol_card_intent.pause_lateral, 'events': [str(e) for e in sd.events.names]})
        proof = {'raw_fault': raw_fault, 'withdrawal': withdrawal, 'observations': observations}
        print(json.dumps(proof))
        stopped = [row for row in observations if not row['cruise_enabled']]
        assert stopped and all(row['main_available'] == (not main_off) for row in stopped)
        assert all(row['axis']['lateralActive'] and row['command_lat'] for row in stopped), (
          'CLASSIC_CRUISE_ONLY_WITHDRAWAL_LOST_LAT ' + json.dumps(proof))
        assert all(not row['command_long'] and not row['axis']['longitudinalActive'] for row in observations)
        # Explicit physical OFF must survive a later delivery gap and recovery.
        for _ in range(10):
          state, axis, host, command, steering = step(main=1, lkas=1)
        for _ in range(10):
          state, axis, host, command, steering = step(main=1)
        assert not card.aol_card_intent.allowed_latch and not axis.desiredLateral and not command.latActive
        for _ in range(6):
          step(main=1, hold_axis=True)
        for _ in range(15):
          state, axis, host, command, steering = step(main=1)
          assert not card.aol_card_intent.allowed_latch and not axis.lateralActive and not command.latActive
          assert native.aol_get_permission_mask() == 0 and not command.longActive

      finally:
        patch_scope.__exit__(None, None, None)
    finally:
      if protocol is not None:
        protocol.close()
      native.init_tests()
      native.set_alternative_experience(0)
      native.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
      case.doCleanups()
      if card is not None:
        card.vehicle_startup.close()


@pytest.mark.parametrize('gas_override', [False, True])
def test_classic_eps_recovery_with_cruise_main_off(gas_override, monkeypatch, hkg_pandad_order):
  test_classic_stock_temporary_withdrawal_recovers_with_real_cpp(
    0, 'brake', 'eps', monkeypatch, hkg_pandad_order, main_off=True, gas_override=gas_override)
