"""Shared GM Bolt parser, caller and native recovery trajectory fixture."""
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.gm.tests.test_bolt_cc import feed as feed_car, native
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.car.gm.values import CAR, DBC
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.aol.runtime import current_native, decide_axes
from openpilot.starpilot.aol.transport_pause import TransportPause
from openpilot.starpilot.aol.wire import IntentState, PandaSlot, SafetyState, decode_safety, encode_safety
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed


def exercise_bolt_cancel_recovery(self, identity, removed, *, critical=None, communications: str | None = None,
                                  pedal_scene=False, inventory_publish=False):
  from openpilot.cereal import log
  from openpilot.selfdrive.car.car_events import CarEvents
  from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
  from openpilot.selfdrive.selfdrived.state import StateMachine
  from openpilot.selfdrive.selfdrived.events import Events, EventName, ET
  from openpilot.starpilot.aol.runtime import AxisDecision
  from openpilot.starpilot.aol.wire import encode_intent
  from opendbc.can import CANParser
  from opendbc.car.gm.bolt_cc import button_bytes
  from opendbc.car.gm.values import AccState, CruiseButtons, GMFlags
  from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
  from opendbc.car.gm.gmcan import pedal_crc
  from openpilot.starpilot.nostalgia import aol_no_entry

  ctx = {'identity': str(identity), 'removed': removed, 'critical': critical,
         'communications': communications, 'pedal_scene': pedal_scene, 'phase': 'factory'}
  cancel_edge_ticks = []
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
    settings = Params()
    for key in ('AlwaysOnLateral', 'AlphaLongitudinalEnabled'):
      settings.put_bool(key, True, block=True)
    if communications is not None:
      settings.put('AolBrakePauseSpeedMps', 25., block=True)
    if inventory_publish:
      from opendbc.car.gm.interface import CarInterface
      self.assertEqual(identity, CAR.CHEVROLET_BOLT_CC_2018_2021, msg=ctx)
      self.assertTrue(pedal_scene and not removed and critical is None and communications is None, msg=ctx)
      fingerprint = gen_empty_fingerprint()
      fingerprint[0].update({0x201: 6, 0x142: 7})
      fingerprint[2].update({0x320: 8, 0x180: 4})
      factory = CarInterface.get_params(identity, fingerprint, [], False, False, False)
    else:
      factory = pedal_params(identity, pedal=True, camera=not removed, removed=removed)
    selected = self.card(factory, settings)
    cp, ci = selected.CP, selected.CI
    word = cp.safetyConfigs[0].safetyParam
    ctx['word'] = hex(word)
    self.assertEqual(word, factory.safetyConfigs[0].safetyParam, msg=ctx)
    self.assertEqual(cp.alternativeExperience, 32, msg=ctx)
    self.assertTrue(cp.openpilotLongitudinalControl, msg=ctx)
    self.assertFalse(cp.pcmCruise, msg=ctx)
    self.assertTrue(cp.flags & GMFlags.PEDAL_LONG, msg=ctx)
    if inventory_publish:
      self.assertEqual(len(cp.safetyConfigs), 1, msg=ctx)
      self.assertEqual(cp.safetyConfigs[0].safetyModel, structs.CarParams.SafetyModel.gm, msg=ctx)
      self.assertEqual(word, 157, msg=ctx)
      self.assertEqual(cp.flags, 17, msg=ctx)
    controls = Controls()
    self.assertEqual(controls.CP.to_dict(), cp.to_dict(), msg=ctx)
    sd = SelfdriveD(CP=cp.as_reader()) if communications is not None or pedal_scene else SelfdriveD.__new__(SelfdriveD)
    if inventory_publish:
      self.assertEqual(sd.CP.to_dict(), cp.to_dict(), msg=ctx)
    sd.CP, sd.initialized, sd.aol_replay = cp, True, True
    sd.enabled = sd.active = False
    sd.aol_session_id, sd.aol_axis_decision = 'bolt-cancel', AxisDecision()
    sd.aol_authority_lost = False
    sd.aol_transport_pause = TransportPause(sd.CP, sd.aol_session_id)
    sd.aol_dm_lateral_inhibit, sd.aol_settings = False, selected.aol_settings
    sd.nostalgia_paddle_cancel = False
    sd.state_machine, sd.events = StateMachine(), Events()
    sd.state_machine.state = log.SelfdriveState.OpenpilotState.enabled
    if not pedal_scene:
      sd.sm = messaging.SubMaster(['aolIntentWire', 'aolSafetyWire', 'modelV2',
                                   'extrinsicsCalibration', 'driverMonitoringState'])
    car_events = CarEvents(cp)
    previous = structs.CarState()
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    output = CANParser(DBC[cp.carFingerprint][Bus.pt], [('GAS_COMMAND', float('nan'))], 0)
    neutral_pedal_seen = False
    safety = libsafety_py.libsafety
    safety.set_alternative_experience(32)
    self.assertEqual(safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, word), 0, msg=ctx)
    safety.init_tests()
    if inventory_publish:
      # init_tests resets this configuration scalar; restore the actual selected CP value.
      safety.set_alternative_experience(int(cp.alternativeExperience))
      self.assertEqual(safety.get_current_safety_mode(), int(cp.safetyConfigs[0].safetyModel.raw), msg=ctx)
      self.assertEqual(safety.get_current_safety_param(), word, msg=ctx)
    status_owner = word in (0xBD, 0x9D, 0x19D, 0xE700, 0xE701, 0xE702)
    queued_status = None
    last_status_emission_us = None
    last_paddle_feeds = {}
    gen2_drive_scene = identity == CAR.CHEVROLET_BOLT_CC_2022_2023 and word == 0x19D and not pedal_scene
    trajectory = set()
    sent = []
    steering_loopback = []
    cancel_start, pause_start = (40, 32) if pedal_scene else (10, 10)
    main_start, ticks = (646, 652) if pedal_scene else (26, 36)
    temporary_eps_tick, stale_axis_tick = (-1, -1) if pedal_scene else (20, 23)
    previous_sd_onroad = None
    previous_command = structs.CarControl().as_reader()
    if pedal_scene:
      def feed_event_sources(stamp, companions=()):
        # Real SD subscription shapes; optional action/maneuver services stay silent.
        names = ['deviceState', 'pandaStates', 'peripheralState', 'modelV2', 'extrinsicsCalibration',
                 'carOutput', 'driverMonitoringState', 'longitudinalPlan', 'deviceMotion', 'lateralDelay',
                 'managerState', 'vehicleParameters', 'radarState', 'lateralTorqueParameters',
                 'controlsState', 'carControl', 'driverAssistance', *sd.camera_packets, *sd.sensor_packets]
        supplied = {sample.which() for sample in companions}
        samples = list(companions)
        for name in names:
          if name in supplied:
            continue
          sample = messaging.new_message(name, 1 if name == 'pandaStates' else None, valid=True, logMonoTime=stamp)
          value = getattr(sample, name)
          if name == 'deviceState':
            value.started, value.startedMonoTime = True, 102_000_000_000
            # Healthy storage telemetry lets the genuine NO_ENTRY check evaluate normally.
            value.freeSpacePercent = 50.
          elif name == 'pandaStates':
            value[0].safetyModel = cp.safetyConfigs[0].safetyModel
            value[0].safetyParam = word
            value[0].alternativeExperience = cp.alternativeExperience
            value[0].controlsAllowed = bool(safety.get_controls_allowed())
            value[0].safetyRxChecksInvalid = not bool(safety.safety_config_valid())
          elif name == 'extrinsicsCalibration':
            value.calStatus, value.rpyCalib = 'calibrated', [0., 0., 0.]
          elif name == 'deviceMotion':
            value.inputsOK = value.posenetOK = True
          elif name == 'vehicleParameters':
            value.valid = True
          elif name == 'controlsState':
            value.lateralControlState.init('torqueState')
          elif name == 'carControl':
            sample.carControl = previous_command
          samples.append(sample.as_reader())
        sd.sm.update_msgs(stamp / 1e9, samples)
      # Startup is qualified by the actual parser/native SET/release below.
      selected.ci_initialized = True
      selected.publish_sendcan = lambda messages, **kwargs: sent.append(list(messages))
    def recorded_status():
      result = []
      for index in range(safety.get_recorded_can_count()):
        packet = libsafety_py.make_CANPacket(0, 0, bytes(8))
        self.assertTrue(safety.get_recorded_can(index, packet), msg=ctx)
        if packet.addr == 0x3D1:
          result.append((packet.addr, bytes(packet.data[0:8]), packet.bus))
      return result
    try:
      # Engage only through the physical SET/release sequence.
      for offset, counter, pedal_counter, button in ((-20_000_000, 2, 14, CruiseButtons.DECEL_SET),
                                                     (-10_000_000, 3, 15, CruiseButtons.UNPRESS)):
        ctx.update(phase='physical_prime', offset_ns=offset, raw_button=int(button), button_counter=counter, sensor_counter=pedal_counter)
        prime_now = 102_289_000_000 + offset
        _, prime_packets = feed_car(SimpleNamespace(update=lambda _: None), packer, prime_now,
                                    counter=counter, active=True, speed=20., camera=not removed)
        prime_packets = [packet for packet in prime_packets if packet[0] not in (0x1C4, 0x1E1)]
        if inventory_publish:
          prime_packets.append(packer.make_can_msg('BCMBlindSpotMonitor', 0, {}))
        prime_packets += [packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': AccState.ACTIVE})]
        sensor = bytearray(packer.make_can_msg('GAS_SENSOR', 0,
                           {'INTERCEPTOR_GAS': 0., 'INTERCEPTOR_GAS2': 0., 'STATE': 0, 'COUNTER_PEDAL': pedal_counter})[1])
        sensor[5] = pedal_crc(sensor)
        if cp.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and not removed:
          prime_packets.append(packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 0}))
          if offset == -20_000_000:
            prime_packets.append(packer.make_can_msg('EBCMFrictionBrakeCmd', 2, {'RollingCounter': 0}))
        # Required sensor readiness precedes each physical SET/release edge.
        prime_packets += [(0x201, bytes(sensor), 0), (0x1E1, button_bytes(button, counter), 0)]
        previous = ci.update([(prime_now, prime_packets)])
        for packet in prime_packets:
          self.assertTrue(native('rx', packet, prime_now // 1000), (packet, ctx))
        safety.safety_tick()
        self.assertTrue(safety.safety_config_valid(), msg=ctx)
      self.assertTrue(previous.canValid, msg=ctx)
      self.assertTrue(safety.get_controls_allowed(), msg=ctx)
      if pedal_scene:
        sd.CS_prev = previous.as_reader()
      # CANCEL edges withdraw ordinary cruise; MAIN remains available until its later cycle.
      for tick in range(ticks):
        now = 102_289_000_000 + tick * 10_000_000
        fault = tick in (10, 11)
        cancel = cancel_start <= tick < cancel_start + 2
        main = tick not in (main_start, main_start + 1)
        ordinary = tick < pause_start
        communication_loss = communications is not None and (14 <= tick < 18 or critical is not None and fault)
        regen = communications is not None and tick == 22
        brake = communications is not None and tick == 23
        physical_regen = pedal_scene and 32 <= tick < 36
        # Stock cruise is physically active until the outgoing Cancel takeover.
        stock_active = ordinary if not pedal_scene else tick < 8
        ctx.update(phase='scenario', tick=tick, main=main, ordinary=ordinary, fault=fault,
                   raw_button=int(CruiseButtons.CANCEL if cancel else CruiseButtons.UNPRESS),
                   button_counter=tick % 4, sensor_counter=tick % 16)
        _, packets = feed_car(SimpleNamespace(update=lambda _: None), packer, now,
                              counter=tick % 4, active=stock_active, speed=20., regen=physical_regen, camera=not removed)
        packets = [packet for packet in packets if packet[0] not in (0x1C4, 0x1E1, 0x184)]
        if inventory_publish:
          packets.append(packer.make_can_msg('BCMBlindSpotMonitor', 0, {}))
        if pedal_scene:
          # The reported neutral mode0 wheel bytes; preserve emitted ACC-form Cancel.
          counter = tick % 4
          checksum = 0xF0 + counter * 0x3F0
          raw_neutral = bytes((0, 0, 0, 0, counter, 0x10 | (checksum >> 8), checksum & 0xFF))
          packets = [packet for packet in packets if packet[0] != 0x1F5]
          packets.append(packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 6}))
        packets += [packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': 0}),
                    packer.make_can_msg('ECMEngineStatus', 0, {'CruiseMainOn': int(main), 'BrakePressed': int(brake)}),
                    packer.make_can_msg('PSCMStatus', 0,
                                        {'LKATorqueDeliveredStatus':
                                         3 if critical == 'permanentEPS' and fault else 2 if tick == temporary_eps_tick else 0}),
                    (0x1E1, button_bytes(CruiseButtons.CANCEL if cancel else CruiseButtons.UNPRESS, tick % 4), 0)]
        if pedal_scene and not cancel:
          packets = [packet for packet in packets if packet[0] != 0x1E1] + [(0x1E1, raw_neutral, 0)]
        sensor = bytearray(packer.make_can_msg('GAS_SENSOR', 0,
                           {'INTERCEPTOR_GAS': 0., 'INTERCEPTOR_GAS2': 0., 'STATE': 0, 'COUNTER_PEDAL': tick % 16})[1])
        sensor[5] = pedal_crc(sensor)
        packets.append((0x201, bytes(sensor), 0))
        if not removed:
          packets.append(packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 0}))
          if cp.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL and (tick + 2) % 4 == 0:
            packets.append(packer.make_can_msg('EBCMFrictionBrakeCmd', 2, {'RollingCounter': ((tick + 2) // 4) % 4}))
        self.assertTrue(any(address == 0x201 for address, _, _ in packets), msg=ctx)
        if communications is not None:
          packets = [packet for packet in packets if packet[0] != 0xBD]
          packets.append(packer.make_can_msg('EBCMRegenPaddle', 0, {'RegenPaddle': int(regen)}))
        cs = ci.update([(now - 5_000_000, steering_loopback), (now, packets)])
        steering_loopback = []
        if communications is not None:
          self.assertEqual(cs.regenBraking, regen, msg=ctx)
          self.assertEqual(cs.brakePressed, brake, msg=ctx)
        ctx.update(buttonEvents=[(button.type.raw, bool(button.pressed)) for button in cs.buttonEvents],
                   raw_wheel_button=[bytes(data).hex() for address, data, bus in packets if address == 0x1E1 and bus == 0],
                   canValid=cs.canValid, canTimeout=cs.canTimeout,
                   cruise_available=cs.cruiseState.available, cruise_enabled=cs.cruiseState.enabled,
                   eps_temporary=cs.steerFaultTemporary, eps_permanent=cs.steerFaultPermanent)
        cancel_edges = [bool(button.pressed) for button in cs.buttonEvents if button.type == structs.CarState.ButtonEvent.Type.cancel]
        self.assertEqual(cancel_edges, [True] if tick == cancel_start else [False] if tick == cancel_start + 2 else [], msg=ctx)
        self.assertTrue(cs.canValid, (tick, ctx))
        self.assertFalse(cs.accFaulted, msg=ctx)
        if pedal_scene:
          self.assertEqual(cs.regenBraking, physical_regen, msg=ctx)
          self.assertFalse(cs.cruiseState.enabled, msg=ctx)
          self.assertEqual(ci.CS.bolt_pedal_stock_active, stock_active, msg=ctx)
          if tick == 0:
            self.assertEqual(raw_neutral.hex(), '000000000010f0', msg=ctx)
        self.assertEqual(cs.cruiseState.available, main, msg=ctx)
        safety.reset_recorded_can()
        regen_boundary = None
        # Physical FWD precedes RX; update independent physical sources before stock status.
        for packet in sorted(packets, key=lambda msg: msg[0] == 0x3D1):
          if status_owner and packet[0] == 0x3D1:
            ready = main and tick != temporary_eps_tick and not (critical == 'permanentEPS' and fault)
            if pedal_scene:
              # Native status ownership yields to physical pedals while AOL lateral persists.
              ready = ready and not (cs.brakePressed or cs.gasPressed or cs.regenBraking)
            replace_status = queued_status is not None and ready
            safety.set_timer(now // 1000)
            self.assertEqual(safety.safety_fwd_hook(0, 0x3D1), -1 if replace_status else 2, ((tick, critical), ctx))
            self.assertTrue(native('rx', packet, now // 1000), ((tick, packet), ctx))
            self.assertEqual(recorded_status(), [queued_status] if replace_status else [], ((tick, critical), ctx))
            if replace_status:
              if last_status_emission_us is not None:
                self.assertGreaterEqual(now // 1000 - last_status_emission_us, 40_000, msg=ctx)
              last_status_emission_us = now // 1000
            queued_status = None
            self.assertEqual(safety.safety_fwd_hook(0, 0x3D1), 2, (tick, ctx))
          else:
            if pedal_scene and physical_regen and packet[0] == 0xBD:
              regen_boundary = safety.get_recorded_can_count()
            self.assertTrue(native('rx', packet, now // 1000), ((tick, packet), ctx))
        if pedal_scene or gen2_drive_scene:
          if gen2_drive_scene:
            self.assertEqual(cs.gearShifter, structs.CarState.GearShifter.drive, msg=ctx)
          if physical_regen:
            self.assertIsNotNone(regen_boundary, msg=ctx)
            # Observe driver regen before probing another stock gear opportunity.
            gear = next(packet for packet in packets if packet[0] == 0x1F5)
            self.assertTrue(native('rx', gear, now // 1000), msg=ctx)
          for index in range(safety.get_recorded_can_count()):
            packet = libsafety_py.make_CANPacket(0, 0, bytes(8))
            self.assertTrue(safety.get_recorded_can(index, packet), msg=ctx)
            if packet.addr in (0xBD, 0x1F5):
              size = 7 if packet.addr == 0xBD else 8
              raw = bytes(packet.data[0:size])
              self.assertEqual(raw, last_paddle_feeds[packet.addr], msg=ctx)
              self.assertEqual(packet.bus, 0, msg=ctx)
              if gen2_drive_scene:
                self.assertEqual(raw, bytes(7) if packet.addr == 0xBD else bytes.fromhex('0c0c000400000100'), msg=ctx)
                trajectory.add('gen2_drive_release_' + hex(packet.addr))
              applied = raw[0] == 0x20 if packet.addr == 0xBD else raw[5] == 2
              if physical_regen and regen_boundary is not None and index >= regen_boundary:
                self.assertFalse(applied, msg=ctx)
              if applied or 'native_apply_' + hex(packet.addr) in trajectory:
                trajectory.add(('native_apply_' if applied else 'native_release_') + hex(packet.addr))
        safety.safety_tick()
        safety.set_aol_test_heartbeat(True)
        events = car_events.update(cs, previous, structs.CarControl())
        if communication_loss and communications is not None:
          events.add(getattr(EventName, communications))
        if regen or brake:
          events.add(EventName.pedalPressed)
        if fault and critical in ('controlsMismatch', 'steerUnavailable'):
          events.add(getattr(EventName, critical))
        ctx['events'] = [int(event) for event in events.names]
        if communication_loss:
          self.assertTrue(events.contains(ET.NO_ENTRY), msg=ctx)
          self.assertTrue(events.contains(ET.SOFT_DISABLE), msg=ctx)
          self.assertEqual(events.contains(ET.IMMEDIATE_DISABLE),
                           bool(fault and critical in ('controlsMismatch', 'steerUnavailable', 'permanentEPS')), msg=ctx)
        if regen or brake:
          self.assertIn(EventName.pedalPressed, events.names, msg=ctx)
          self.assertTrue(events.contains(ET.USER_DISABLE), msg=ctx)
        self.assertEqual(EventName.buttonCancel in events.names, tick in (cancel_start, cancel_start + 2), msg=ctx)
        if tick in (cancel_start, cancel_start + 2):
          cancel_edge_ticks.append(tick)
        if tick in (cancel_start, cancel_start + 2):
          self.assertTrue(events.contains(ET.USER_DISABLE), msg=ctx)
        if critical and fault:
          self.assertTrue(events.contains(ET.NO_ENTRY), msg=ctx)
          if critical == 'calibrationInvalid':
            self.assertTrue(events.contains(ET.SOFT_DISABLE), msg=ctx)
            self.assertFalse(events.contains(ET.IMMEDIATE_DISABLE), msg=ctx)
          else:
            self.assertTrue(events.contains(ET.IMMEDIATE_DISABLE), msg=ctx)
        onroad = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now)
        onroad.onroadEvents = events.to_msg()
        if pedal_scene and previous_sd_onroad is not None:
          # Card consumes the preceding real SD event publication as onroad does.
          selected.sm.update_msgs(now / 1e9, [previous_sd_onroad])
          disarm = selected.aol_disarming_fault(cs, int(previous_sd_onroad.logMonoTime), now)
        else:
          selected.sm.update_msgs(now / 1e9, [onroad.as_reader()])
          disarm = selected.aol_disarming_fault(cs, now, now)
        self.assertEqual(disarm, bool(fault and critical), ((tick, critical), ctx))
        selected.aol_card_intent.update(cs, fault_active=disarm, now_ns=now, standard_enabled=ordinary)
        expected_latch = main and not disarm
        self.assertEqual(selected.aol_card_intent.allowed_latch, expected_latch, ((tick, critical), ctx))
        allowed, pause_lat, pause_long = selected.aol_card_intent.output(cs)
        intent_state = IntentState('card', tick + 1, now, now, now + 30_000_000,
                                   allowed, pause_lat, pause_long, True, True)
        no_entry = (aol_no_entry(events.names, cs, paddle_only_cancel=False) if communications is not None else
                    aol_no_entry(events.names, cs, paddle_only_cancel=False, allow_below_engage_speed=True)
                    if pedal_scene else events.contains(ET.NO_ENTRY))
        desired = decide_axes(standard_lateral=ordinary, standard_longitudinal=ordinary,
                              intent=intent_state, native=None, car_state=cs, initialized=True,
                              model_ready=True, no_entry=no_entry,
                              immediate_disable=events.contains(ET.IMMEDIATE_DISABLE),
                              dm_lockout=False, pause_brake_mps=25. if communications is not None else 5.)
        safety.aol_set_host_request(int(desired.desired_lateral) | (int(desired.desired_longitudinal) << 1))
        mask = safety.aol_get_permission_mask()
        if communication_loss:
          self.assertEqual(mask, 0, msg=ctx)
        if communications is not None and critical is None and tick in (18, 22, 24):
          self.assertEqual(mask, 1, msg=ctx)
        ctx.update(native_permission_mask=int(mask), native_controls_allowed=bool(safety.get_controls_allowed()),
                   host_request=(int(desired.desired_lateral) | (int(desired.desired_longitudinal) << 1)),
                   allowed_latch=selected.aol_card_intent.allowed_latch)
        receipt_state = SafetyState(1, True, now, now + 200_000_000,
                                   int(structs.CarParams.SafetyModel.gm), word,
                                   bool(mask & 1), bool(mask & 2), desired.desired_lateral,
                                   desired.desired_longitudinal, 'panda', 'bolt-cancel')
        if inventory_publish:
          slot = PandaSlot(0, receipt_state.pandaSerial, safety.get_current_safety_mode(), safety.get_current_safety_param(),
                           int(cp.alternativeExperience), bool(safety.get_controls_allowed()),
                           not bool(safety.safety_config_valid()))
          # Source PandaStates/CAN use the synthetic BOOTTIME tick; the lease is MONOTONIC.
          receipt_state = replace(receipt_state, sourcePandaStatesMonoTime=now, pandaInventory=(slot,))
        intent = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=now)
        intent.aolIntentWire = encode_intent(intent_state)
        receipt = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
        receipt.aolSafetyWire = encode_safety(receipt_state)
        if inventory_publish:
          self.assertEqual(decode_safety(receipt.aolSafetyWire), receipt_state, msg=ctx)
          self.assertEqual(receipt_state.pandaInventory[0].controlsAllowed,
                           bool(safety.get_controls_allowed()), msg=ctx)
        model = messaging.new_message('modelV2', valid=True, logMonoTime=now)
        calibration = messaging.new_message('extrinsicsCalibration', valid=True, logMonoTime=now)
        calibration.extrinsicsCalibration.calStatus = 'calibrated'
        if pedal_scene:
          calibration.extrinsicsCalibration.rpyCalib = [0., 0., 0.]
        monitoring = messaging.new_message('driverMonitoringState', valid=True, logMonoTime=now)
        if pedal_scene:
          # One receive interval per service; retain exact current companion bytes.
          feed_event_sources(now, [intent.as_reader(), receipt.as_reader(), model.as_reader(),
                                   calibration.as_reader(), monitoring.as_reader()])
        else:
          sd.sm.update_msgs(now / 1e9, [intent.as_reader(), receipt.as_reader(), model.as_reader(),
                                       calibration.as_reader(), monitoring.as_reader()])
        sd.aol_car_state_log_ns = now
        sd.conditional_car_state_valid = cs.canValid and not cs.canTimeout
        sd.events = events
        if pedal_scene:
          self.assertTrue(sd.sm.all_checks(), msg=ctx)
          state_sample = messaging.new_message('carState', valid=True, logMonoTime=now)
          state_sample.carState = cs
          with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False), \
               patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now), \
               patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic', return_value=now / 1e9), \
               patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=state_sample.as_reader()), \
               patch.object(sd.sm, 'update'), patch.object(sd, 'update_alerts'), \
               patch.object(sd, 'update_conditional_mode'), patch.object(sd, 'publish_selfdriveState'):
            sd.step()  # Genuine data_sample, update_events, state machine and axis decisions.
          actual_onroad = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now)
          actual_onroad.onroadEvents = sd.events.to_msg()
          previous_sd_onroad = actual_onroad.as_reader()
          self.assertEqual(sd.aol_car_state_log_ns, now, msg=ctx)
          self.assertEqual(sd.cruise_mismatch_counter, 0, msg=ctx)
          self.assertLess(sd.mismatch_counter, 200, msg=ctx)
          if tick > pause_start:
            self.assertEqual(sd.mismatch_counter, 0, msg=ctx)
          self.assertEqual(sd.events.names.count(EventName.pedalPressed), int(physical_regen), msg=ctx)
          if physical_regen:
            self.assertTrue(sd.events.contains(ET.USER_DISABLE), msg=ctx)
        else:
          with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', True), \
               patch.object(sd, 'data_sample', return_value=cs), patch.object(sd, 'update_events'), \
               patch.object(sd, 'update_alerts'), patch.object(sd, 'update_conditional_mode'), \
               patch.object(sd, 'publish_selfdriveState'):
            sd.step()
        ctx.update(sd_enabled=sd.enabled, sd_active=sd.active, actual_lat=sd.aol_axis_decision.lateral_active,
                   actual_long=sd.aol_axis_decision.longitudinal_active)
        if pedal_scene:
          # Record the genuine writer's gate, not the pre-step projected events.
          ctx.update(post_events=[str(event) for event in sd.events.names],
                     post_no_entry=aol_no_entry(sd.events.names, sd.CS_prev, paddle_only_cancel=sd.nostalgia_paddle_cancel,
                                               cruise_main_required=sd.aol_cruise_main_required),
                     post_immediate_disable=sd.events.contains(ET.IMMEDIATE_DISABLE),
                     post_axis=vars(sd.aol_axis_decision),
                     post_intent=vars(sd.aol_last_intent) if sd.aol_last_intent is not None else None,
                     post_initialized=sd.initialized, post_can_valid=sd.CS_prev.canValid,
                     post_gear=str(sd.CS_prev.gearShifter), post_speed=sd.CS_prev.vEgo,
                     post_model_checks=sd.sm.all_checks(['modelV2', 'extrinsicsCalibration']),
                     post_calibration=str(sd.sm['extrinsicsCalibration'].calStatus),
                     post_dm_checks=sd.sm.all_checks(['driverMonitoringState']),
                     post_dm_alert=str(sd.sm['driverMonitoringState'].alertLevel),
                     post_dm_lockout=sd.sm['driverMonitoringState'].lockout,
                     post_dm_always_on_lockout=sd.sm['driverMonitoringState'].alwaysOnLockout)
          companion = sd.aol_intent_companion
          if companion is not None:
            latest = companion.latest
            ctx.update(post_companion_sequence=companion.sequence, post_companion_stamp=companion.sequence_stamp,
                       post_companion_samples=list(companion.samples),
                       post_companion_latest=(latest[0], latest[1], vars(latest[2]) if latest[2] is not None else None)
                       if latest is not None else None)
        expected_lat = (expected_latch and bool(mask & 1) and not no_entry and
                        not events.contains(ET.IMMEDIATE_DISABLE) and tick != temporary_eps_tick and not brake)
        self.assertEqual(sd.aol_axis_decision.lateral_active, expected_lat, ((tick, critical), ctx))
        self.assertEqual(sd.aol_axis_decision.longitudinal_active, ordinary, ((tick, critical), ctx))
        if tick >= pause_start:
          self.assertFalse(sd.enabled, msg=ctx)  # Clearing CANCEL or a fault never resumes longitudinal.
        if pedal_scene:
          self.assertNotIn(EventName.controlsMismatch, sd.events.names, msg=ctx)
          self.assertNotIn(EventName.cruiseMismatch, sd.events.names, msg=ctx)
          if physical_regen or tick == 36:
            self.assertTrue(selected.aol_card_intent.allowed_latch, msg=ctx)
            self.assertTrue(sd.aol_axis_decision.lateral_active, msg=ctx)
            self.assertFalse(sd.aol_axis_decision.longitudinal_active, msg=ctx)
            trajectory.add('physical_regen' if physical_regen else 'physical_release_lateral_recovered')
          if 43 <= tick < main_start:
            self.assertTrue(selected.aol_card_intent.allowed_latch, msg=ctx)
            self.assertTrue(sd.aol_axis_decision.lateral_active, msg=ctx)
            self.assertFalse(sd.aol_axis_decision.longitudinal_active, msg=ctx)
            if tick == main_start - 1:
              self.assertGreater(now - (102_289_000_000 + 42 * 10_000_000), 6_000_000_000, msg=ctx)
              trajectory.add('six_second_event_recovery')
        feed(controls, now, tick, active=sd.active, enabled=sd.enabled)
        if pedal_scene:
          # Normal planner demand goes through Controls' longitudinal controller.
          plan = messaging.new_message('longitudinalPlan', valid=True, logMonoTime=now)
          plan.longitudinalPlan.aTarget = -4. if ordinary else 0.
          # Publish a builder; the existing SM sample is an immutable capnp reader.
          controls.sm.update_msgs((now + 1) / 1e9, [plan.as_reader()])
        axis = messaging.new_message('aolAxisState', valid=tick != stale_axis_tick, logMonoTime=now)
        axis.aolAxisState.qualified = axis.aolAxisState.nativeAcknowledged = True
        axis.aolAxisState.desiredLateral = sd.aol_axis_decision.desired_lateral
        axis.aolAxisState.desiredLongitudinal = sd.aol_axis_decision.desired_longitudinal
        axis.aolAxisState.lateralActive = sd.aol_axis_decision.lateral_active
        axis.aolAxisState.longitudinalActive = sd.aol_axis_decision.longitudinal_active
        axis.aolAxisState.sessionId = 'bolt-cancel'
        axis.aolAxisState.observedMonoTime = now
        axis.aolAxisState.validUntilMonoTime = now + 30_000_000
        state = messaging.new_message('carState', valid=True, logMonoTime=now)
        state.carState = cs
        controls.sm.update_msgs((now + 1_000) / 1e9, [axis.as_reader(), receipt.as_reader(), state.as_reader()])
        command, controller_log = controls.state_control()
        ctx.update(command_lat=command.latActive, command_long=command.longActive)
        self.assertEqual(command.latActive, expected_lat and tick != stale_axis_tick, ((tick, critical), ctx))
        self.assertEqual(command.longActive, ordinary, ((tick, critical), ctx))
        command.actuators.torque = .02 if command.latActive else 0.
        if pedal_scene:
          if inventory_publish:
            published = []
            def capture_control(service, event, collector=published):
              if service == 'carControl':
                collector.append(messaging.log_from_bytes(event.to_bytes()))
            with patch.object(controls.pm, 'send', side_effect=capture_control), \
                 patch('openpilot.cereal.messaging.time.monotonic', return_value=now / 1e9):
              controls.publish(command, controller_log)
            self.assertEqual(len(published), 1, msg=ctx)
            self.assertEqual(published[0].carControl.to_dict(), command.to_dict(), msg=ctx)
            self.assertEqual(published[0].valid, cs.canValid, msg=ctx)
            self.assertEqual(published[0].logMonoTime, now, msg=ctx)
            selected.sm.update_msgs(now / 1e9, published)
            previous_command = selected.sm['carControl']
          else:
            control_message = messaging.new_message('carControl', valid=True, logMonoTime=now)
            control_message.carControl = command
            previous_command = command.as_reader()
            selected.sm.update_msgs(now / 1e9, [control_message.as_reader()])
          selected.can_log_mono_time = now + 2
          with patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now + 2), \
               patch('openpilot.selfdrive.car.card.time.monotonic', return_value=(now + 2) / 1e9):
            selected.controls_update(cs, selected.sm['carControl'] if inventory_publish else command.as_reader())
          self.assertTrue(sent, msg=ctx)
          messages = sent.pop()
          ctx['command_accel'] = command.actuators.accel
          if ci.CC.regen_paddle_pressed:
            self.assertLess(command.actuators.accel, -.65, msg=ctx)
            trajectory.add('normal_deceleration_apply')
        else:
          _, messages = ci.apply(command.as_reader(), now + 2)
        output.update([(now, messages)])
        pedal_messages = [message for message in messages if message[0] == 0x200]
        self.assertEqual(len(pedal_messages), int(tick % 4 == 0), msg=ctx)
        for _, data, bus in pedal_messages:
          ctx['pedal_bytes'] = bytes(data).hex()
          self.assertEqual(bus, 0, msg=ctx)
          self.assertEqual(len(data), 6, msg=ctx)
          self.assertEqual(data[4] & 0xF, (tick // 4) % 16, msg=ctx)
          self.assertEqual(data[4] & 0x70, 0, msg=ctx)
          self.assertEqual(data[5], pedal_crc(data), msg=ctx)
          if tick >= pause_start:
            self.assertFalse(output.vl['GAS_COMMAND']['ENABLE'], ((identity, removed, tick), ctx))
            # Disabled pedal is raw-zero; DBC physical offsets are negative sentinels.
            self.assertEqual(bytes(data[:4]), bytes(4), msg=ctx)
            self.assertEqual(data[4], (tick // 4) % 16, msg=ctx)
            neutral_pedal_seen = True
        host_status = [message for message in messages if message[0] == 0x3D1]
        self.assertEqual(len(host_status), int(status_owner and tick % 4 == 0), ((identity, removed, tick), ctx))
        for message in messages:
          if message[0] == 0x3D1:
            # Bolt host status is queued, never directly admitted; real stock RX emits it.
            self.assertTrue(status_owner, msg=ctx)
            self.assertEqual(message[2], 0, msg=ctx)
            self.assertEqual(len(message[1]), 8, msg=ctx)
            self.assertFalse(native('tx', message, now // 1000 + 1), ((tick, message), ctx))
            queued_status = tuple(message) if ready else None
          elif (pedal_scene or gen2_drive_scene) and message[0] in (0xBD, 0x1F5):
            raw = bytes(message[1])
            pressed = ci.CC.regen_paddle_pressed
            gen2 = identity == CAR.CHEVROLET_BOLT_CC_2022_2023
            if gen2_drive_scene:
              self.assertFalse(pressed, msg=ctx)
            release_gear = 4 if gen2_drive_scene else 6
            expected = (bytes((0x20 if pressed else 0,)) + bytes(6) if message[0] == 0xBD else
                        bytes((12, 12, 0, (5 if gen2 else 7) if pressed else release_gear, 0, 2 if pressed else 0, 1, 0)))
            self.assertEqual(raw, expected, msg=ctx)
            # This False consumes the host cache feed; stock RX records actual admission.
            recorded_before = safety.get_recorded_can_count()
            self.assertFalse(native('tx', message, now // 1000 + 1), ((tick, message), ctx))
            self.assertEqual(safety.get_recorded_can_count(), recorded_before, msg=ctx)
            last_paddle_feeds[message[0]] = raw
          else:
            self.assertTrue(native('tx', message, now // 1000 + 1), ((tick, message), ctx))
            if message[0] == 0x180:
              steering_loopback.append((message[0], bytes(message[1]), 128))
            if pedal_scene and message[0] == 0x1E1:
              self.assertEqual(message[2], 0, msg=ctx)
              self.assertEqual(bytes(message[1]), button_bytes(CruiseButtons.CANCEL, (tick + 1) % 4), msg=ctx)
              self.assertFalse(native('tx', message, now // 1000 + 1), msg=ctx)
              trajectory.add('mode_zero_cancel_native_admitted')
        if communications is not None:
          self.assertEqual(selected.aol_card_intent._main_cycle_required,
                           disarm, msg=ctx)
          if communication_loss:
            self.assertFalse(command.latActive or command.longActive, msg=ctx)
            self.assertEqual(selected.aol_card_intent.allowed_latch, main and not disarm, msg=ctx)
          if critical is None and tick in (18, 22, 24):
            self.assertTrue(command.latActive, msg=ctx)
            self.assertFalse(command.longActive, msg=ctx)
        if tick == (36 if pedal_scene else 18) and critical is None:
          self.assertTrue(selected.aol_card_intent.allowed_latch, msg=ctx)
          self.assertTrue(command.latActive, msg=ctx)
          self.assertFalse(command.longActive, msg=ctx)
          self.assertTrue(main, msg=ctx)
        previous = cs
      self.assertEqual(cancel_edge_ticks, [cancel_start, cancel_start + 2], msg=ctx)
      if gen2_drive_scene:
        self.assertTrue({'gen2_drive_release_0xbd', 'gen2_drive_release_0x1f5'} <= trajectory, (trajectory, ctx))
      if pedal_scene:
        self.assertTrue({'mode_zero_cancel_native_admitted', 'normal_deceleration_apply',
                         'native_apply_0xbd', 'native_apply_0x1f5', 'native_release_0xbd', 'native_release_0x1f5',
                         'physical_regen', 'physical_release_lateral_recovered',
                         'six_second_event_recovery'} <= trajectory, (trajectory, ctx))
        if inventory_publish:
          healthy = current_native(sd.sm, cp, now_ns=now, axis_session_id='bolt-cancel')
          self.assertEqual(healthy, receipt_state, msg=ctx)
          self.assertTrue(sd._decide_aol_axes(cs, intent_state, healthy).lateral_active, msg=ctx)
          native_mask = safety.aol_get_permission_mask()
          mutations = {
            'borrowed_serial': replace(receipt_state, pandaSerial='borrowed-panda'),
            'wrong_slot_word': replace(receipt_state, pandaInventory=(replace(slot, safetyParam=word ^ 1),)),
            'expired_lease': replace(receipt_state, observedMonoTime=now - 200_000_001, validUntilMonoTime=now - 1),
          }
          for failure, bad in mutations.items():
            with self.subTest(inventory_failure=failure):
              rejected = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=bad.observedMonoTime)
              rejected.aolSafetyWire = encode_safety(bad)
              for caller in (sd, controls):
                caller.sm.update_msgs((now + 1_000) / 1e9, [rejected.as_reader()])
                self.assertIsNone(current_native(caller.sm, cp, now_ns=now, axis_session_id='bolt-cancel'), msg=ctx)
              denied_axes = sd._decide_aol_axes(cs, intent_state, current_native(sd.sm, cp, now_ns=now,
                                                                               axis_session_id='bolt-cancel'))
              self.assertFalse(denied_axes.lateral_active or denied_axes.longitudinal_active, msg=ctx)
              denied_command, _ = controls.state_control()
              self.assertFalse(denied_command.latActive or denied_command.longActive, msg=ctx)
              self.assertEqual(safety.aol_get_permission_mask(), native_mask, msg=ctx)
          for caller in (sd, controls):
            caller.sm.update_msgs((now + 1_000) / 1e9, [receipt.as_reader()])
          self.assertEqual(current_native(sd.sm, cp, now_ns=now, axis_session_id='bolt-cancel'), receipt_state, msg=ctx)
          recovered, _ = controls.state_control()
          self.assertTrue(recovered.latActive, msg=ctx)
          self.assertFalse(recovered.longActive, msg=ctx)
        # Explicit synthetic mismatch input proves the real watchdog runs and keeps its alert.
        # This control is never sent to Card/native and is not a route observation.
        mismatch = cs.as_reader().as_builder()
        mismatch.cruiseState.enabled = True
        for sample in range(602):
          probe_now = now + (sample + 1) * 10_000_000
          ctx.update(phase='watchdog_positive_control', sample=sample)
          feed_event_sources(probe_now)
          self.assertTrue(sd.sm.all_checks(), msg=ctx)
          with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False), \
               patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=probe_now), \
               patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic', return_value=probe_now / 1e9):
            sd.update_events(mismatch if sample < 601 else cs)
          self.assertEqual(sd.cruise_mismatch_counter, sample + 1 if sample < 601 else 0, msg=ctx)
          self.assertEqual(EventName.cruiseMismatch in sd.events.names, sample == 600, msg=ctx)
      self.assertTrue(neutral_pedal_seen, msg=ctx)
      ctx.update(phase='stale_withdrawal')
      stale_now = now + 2_000_000_000
      for _ in range(10):
        stale_now += 10_000_000
        stale_cs = ci.update([(stale_now, [])])
      self.assertFalse(stale_cs.canValid, msg=ctx)
      safety.set_timer(stale_now // 1000)
      safety.safety_tick()
      stale_intent = IntentState('card', 100, stale_now, stale_now, stale_now + 30_000_000,
                                 selected.aol_card_intent.allowed_latch, False, False, True, True)
      stale_decision = decide_axes(standard_lateral=False, standard_longitudinal=False,
                                   intent=stale_intent, native=receipt_state, car_state=stale_cs,
                                   initialized=True, model_ready=True, no_entry=False,
                                   immediate_disable=False, dm_lockout=False, pause_brake_mps=5.)
      self.assertFalse(stale_decision.lateral_active, msg=ctx)
      self.assertFalse(stale_decision.longitudinal_active, msg=ctx)
      safety.aol_set_host_request(0)
      feed(controls, stale_now, 100, active=False, enabled=False, can_valid=False, can_timeout=True)
      # Last axis/receipt are deliberately not restamped: their exact bytes have expired.
      stale_state = messaging.new_message('carState', valid=False, logMonoTime=stale_now)
      stale_state.carState = stale_cs
      controls.sm.update_msgs(stale_now / 1e9, [stale_state.as_reader()])
      withdrawn, _ = controls.state_control()
      self.assertFalse(withdrawn.latActive, msg=ctx)
      self.assertFalse(withdrawn.longActive, msg=ctx)
      _, neutral_messages = ci.apply(withdrawn.as_reader(), stale_now + 2)
      safety.reset_recorded_can()
      stale_pedal = [message for message in neutral_messages if message[0] == 0x200]
      self.assertEqual(len(stale_pedal), 1, msg=ctx)  # The next CI.apply frame stays on the unchanged25Hz schedule.
      for _, data, bus in stale_pedal:
        ctx['pedal_bytes'] = bytes(data).hex()
        self.assertEqual(bus, 0, msg=ctx)
        self.assertEqual(len(data), 6, msg=ctx)
        self.assertEqual(bytes(data[:4]), bytes(4), msg=ctx)
        self.assertEqual(data[4], (ticks // 4) % 16, msg=ctx)
        self.assertEqual(data[5], pedal_crc(data), msg=ctx)
      for message in neutral_messages:
        if message[0] == 0x3D1:
          self.assertTrue(status_owner, msg=ctx)
          self.assertFalse(native('tx', message, stale_now // 1000 + 1), (message, ctx))
        elif (pedal_scene or gen2_drive_scene) and message[0] in (0xBD, 0x1F5):
          self.assertFalse(native('tx', message, stale_now // 1000 + 1), (message, ctx))
        else:
          self.assertTrue(native('tx', message, stale_now // 1000 + 1), (message, ctx))
      if status_owner:
        self.assertEqual(safety.safety_fwd_hook(0, 0x3D1), 2, msg=ctx)
        raw_status = next(packet for packet in packets if packet[0] == 0x3D1)
        self.assertTrue(native('rx', raw_status, stale_now // 1000 + 2), msg=ctx)
        self.assertEqual(recorded_status(), [], msg=ctx)  # One fresh source cannot revive expired authority.
    finally:
      safety.set_alternative_experience(0)
      selected.vehicle_startup.close()

