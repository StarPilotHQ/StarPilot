"""Finite Classic Bosch stock and Nidec PCM actual parser/Card/SD/Controls/native ordering test.
Compiles current native safety and the production C++ negotiation bridge in a temporary directory.
Controlled source clocks/perception; this does not prove physical ECU acceptance.
"""

import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from contextlib import ExitStack
from types import SimpleNamespace

from opendbc.can.parser import CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car import car_helpers
from opendbc.car.honda.fingerprints import FW_VERSIONS
from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.honda.values import CAR, HondaFlags, DBC
from opendbc.can.dbc import DBC as CANDBC
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car.card import Car
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.selfdrive.pandad import can_capnp_to_list
from openpilot.starpilot.aol.wire import decode_intent
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
from openpilot.starpilot.aol.tests.honda_can import HondaCan
from openpilot.starpilot.aol.tests.honda_pandad_order import PandadOrder
from openpilot.starpilot.aol.tests.honda_selfdrive_step import selfdrive_step

from openpilot.starpilot.aol.tests.honda_native_fixture import honda_native_pair as honda_native_pair

TRACE = []


class TestHondaCurrentCaller(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    super().setUpClass()
    cls.honda_native_origins = cls.enterClassContext(honda_native_pair())

  def test_classic_civic_temporary_eps_reverse_return(self):
    self.run_current_caller(candidate=CAR.HONDA_CIVIC_BOSCH, word=0)

  def test_radarless_civic_temporary_eps_reverse_return(self):
    self.run_current_caller(candidate=CAR.HONDA_CIVIC_2022, word=8)

  def test_nidec_civic_temporary_eps_reverse_return(self):
    self.run_current_caller(candidate=CAR.HONDA_CIVIC, word=0)

  def test_radarless_alpha_civic_eps_2_with_brake_override(self):
    self.run_current_caller(candidate=CAR.HONDA_CIVIC_2022, word=10, alpha=True, hybrid=True, eps_pedal='brake')

  def test_radarless_alpha_civic_eps_2_with_gas_override(self):
    self.run_current_caller(candidate=CAR.HONDA_CIVIC_2022, word=10, alpha=True, hybrid=True, eps_pedal='gas')

  def test_diagnostic_during_recovered_reverse(self):
    self.run_current_caller(candidate=CAR.HONDA_CIVIC_BOSCH, word=0, fault='diagnostic')

  def test_relay_fault_during_recovered_reverse(self):
    self.run_current_caller(candidate=CAR.HONDA_CIVIC_BOSCH, word=0, fault='relay')

  def run_current_caller(self, *, candidate, word, fault=None, alternate_brake=False, alpha=False, speed_mps=12.5,
                         pedal=False, host_scenario=None, serialized=False, eps_pedal=None, hybrid=False):
    safety = libsafety_py.libsafety
    with (
      OpenpilotPrefix(),
      patch('opendbc.car.honda.interface.disable_ecu', return_value=True),
      patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0', 'FINGERPRINT': '', 'SKIP_FW_QUERY': '', 'DISABLE_FW_CACHE': '1'}),
    ):
      saved = Params()
      for name, value in [
        ('OpenpilotEnabledToggle', True),
        ('AlphaLongitudinalEnabled', alpha),
        ('AlwaysOnLateral', True),
        ('SafeMode', False),
        ('IsOffroad', False),
        ('ControlsReady', False),
        ('IsReleaseBranch', False),
      ]:
        saved.put_bool(name, value, block=True)
      for name, value in [('LKASButtonControl', 9), ('MainCruiseButtonControl', 0), ('PauseAOLOnBrake', 0.0)]:
        saved.put(name, value, block=True)
      fp = gen_empty_fingerprint()
      nidec = bool(candidate.config.flags & HondaFlags.NIDEC)
      pt_bus = 0 if nidec or candidate.config.flags & (HondaFlags.BOSCH_RADARLESS | HondaFlags.BOSCH_CANFD) else 1
      gear_msg = CANDBC(DBC[candidate][Bus.pt]).name_to_msg['GEARBOX_AUTO']
      fp[pt_bus][gear_msg.address] = gear_msg.size
      if hybrid:
        fp[pt_bus][0x184] = 8
      if pedal:
        fp[pt_bus][0x201] = 6
      if alternate_brake:
        fp[pt_bus][0x1BE] = 3
      fw = [
        structs.CarParams.CarFw(ecu=ecu, address=address, subAddress=0 if sub is None else sub, fwVersion=versions[0], brand='honda')
        for (ecu, address, sub), versions in FW_VERSIONS[candidate].items()
      ]
      vin = '0' * 17  # VIN wildcard matching; complete real recorded firmware owns identity.
      exact, matches = match_fw_to_car(fw, vin, log=False)
      self.assertTrue(exact)
      self.assertEqual(matches, {candidate})
      # Patch acquisition only; production fingerprint/FW matcher/get_car/Card own selection.
      with ExitStack() as io:
        io.enter_context(patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=SimpleNamespace(can=[1])))
        io.enter_context(patch('openpilot.selfdrive.car.card.can_comm_callbacks', return_value=(lambda *a, **kw: [], lambda *a, **kw: None)))
        io.enter_context(patch('openpilot.selfdrive.car.card.obd_callback', return_value=lambda _: None))
        io.enter_context(patch.object(car_helpers, 'get_vin', return_value=(-1, -1, vin)))
        io.enter_context(patch.object(car_helpers, 'get_present_ecus', return_value=set()))
        io.enter_context(patch.object(car_helpers, 'get_fw_versions_ordered', return_value=fw))
        io.enter_context(patch.object(car_helpers, 'can_fingerprint', return_value=(None, fp)))
        card = Car()
      cp, ci = card.CP, card.CI
      assert card.aol_card_intent is not None
      self.assertEqual(cp.fingerprintSource, structs.CarParams.FingerprintSource.fw)
      self.assertFalse(cp.fuzzyFingerprint)
      self.assertEqual(cp.carVin, vin)
      self.assertEqual([record.to_dict() for record in cp.carFw], [record.to_dict() for record in fw])
      self.assertEqual(cp.carFingerprint, candidate)
      if hybrid:
        self.assertTrue(cp.flags & HondaFlags.HYBRID)
      self.assertEqual((cp.safetyConfigs[0].safetyParam, cp.alternativeExperience), (word, 32))
      self.assertEqual(cp.pcmCruise, not (alpha or pedal))
      self.assertEqual(cp.openpilotLongitudinalControl, nidec or alpha)
      self.assertEqual((ci.CC.CAN.pt, ci.CC.CAN.lkas), (pt_bus, pt_bus if alpha else 0))
      alt_brake = bool(cp.flags & HondaFlags.BOSCH_ALT_BRAKE)
      self.assertEqual(alt_brake, alternate_brake or bool(candidate.config.flags & HondaFlags.BOSCH_ALT_BRAKE))
      with structs.CarParams.from_bytes(saved.get('CarParams')) as published:
        self.assertEqual(published.to_dict(), cp.to_dict())
      sd, controls = SelfdriveD() if serialized else SelfdriveD(CP=cp), Controls()
      self.assertEqual(sd.CP.to_dict(), cp.to_dict())
      self.assertEqual(controls.CP.to_dict(), cp.to_dict())
      stream = HondaCan(ci)
      safety.init_tests()
      safety.set_alternative_experience(32)
      self.assertEqual(safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, word), 0)
      safety.set_aol_test_heartbeat(False)
      pandad = PandadOrder(safety, Path(os.environ['HONDA_NATIVE_LIBRARY']), Path(os.environ['HONDA_PANDAD_BRIDGE']))
      # Decode the actual controller's on-wire fields; never substitute command flags.
      tx_dbc = ci.CC.packer.dbc
      steer_msg = tx_dbc.name_to_msg['STEERING_CONTROL']
      self.assertIn((steer_msg.address, steer_msg.size), ((0xE4, 5), (0x194, 4)))
      self.assertTrue({'STEER_TORQUE', 'STEER_TORQUE_REQUEST'} <= steer_msg.sigs.keys())
      tx_messages = [('STEERING_CONTROL', 100)]
      if nidec:
        self.assertEqual(tx_dbc.name_to_msg['BRAKE_COMMAND'].address, 0x1FA)
        self.assertEqual(tx_dbc.name_to_msg['ACC_HUD'].address, 0x30C)
        self.assertTrue({'COMPUTER_BRAKE', 'COMPUTER_BRAKE_REQUEST', 'AEB_REQ_1', 'AEB_REQ_2', 'AEB_STATUS'} <= tx_dbc.name_to_msg['BRAKE_COMMAND'].sigs.keys())
        self.assertTrue({'PCM_SPEED', 'PCM_GAS'} <= tx_dbc.name_to_msg['ACC_HUD'].sigs.keys())
        tx_messages += [('BRAKE_COMMAND', 50), ('ACC_HUD', 10)]
      tx_parser = CANParser(tx_dbc.name, tx_messages, ci.CC.CAN.lkas)
      previous, driven, receipt = structs.CarControl(), {}, None
      controls_published, controls_latest, driven_new = {}, {}, {}
      base = time.monotonic_ns() + 100_000_000
      try:
        for tick in range(145):
          now = base + tick * 10_000_000
          reengage = host_scenario == 'long_reengage' and tick >= 131
          if host_scenario == 'settings_off' and tick == 135:
            from openpilot.starpilot.aol.intent import read_settings
            saved.put_bool('AlwaysOnLateral', False, block=True)
            card.aol_card_intent.settings = read_settings(saved)
            sd.aol_settings = read_settings(saved)
          # SET press/release is the real first ordinary native grant, preceding engaged HB.
          incoming = stream.frames(
            cruise_button=3 if tick == 20 or host_scenario == 'long_reengage' and tick == 130 else 0,
            cruise=int(21 <= tick < 50 or reengage),
            brake=50 <= tick < 55 or eps_pedal == 'brake' and 80 <= tick < 95,
            eps=(2 if eps_pedal else 6) if 80 <= tick < 95 else 0,
            gas=20 if eps_pedal == 'gas' and 80 <= tick < 95 else 0,
            steering_torque=1301 if eps_pedal and 80 <= tick < 95 else 0,
            gear=2 if 110 <= tick < 125 else None,
            lkas=0, speed_kph=speed_mps * 3.6,
          )
          safety.set_timer((now // 1000) & 0xFFFF_FFFF)
          if tick == 110 and fault == 'relay':
            safety.set_relay_malfunction(True)
          if tick >= 110 and fault == 'diagnostic':
            pandad.reported_diagnostic_mask = 1 << 3
          for address, data, bus in incoming:
            self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data)), (tick, address, bus))
          safety.safety_tick()
          # Native generic lagging is >=1s; this400ms loss instead exercises
          # the stricter300ms AOL source lifetime with still-valid generic RX.
          self.assertTrue(safety.safety_config_valid())
          self.assertEqual(bool(safety.get_relay_malfunction()), fault == "relay" and tick >= 110)
          pre_host_status = None
          if 21 <= tick <= 29:
            # SET release at21 is the physical grant. Read native status before
            # the next actual100ms pandad request/heartbeat at30, including80ms at29.
            pre_host_status = (int(safety.aol_get_request_mask()), int(safety.aol_get_permission_mask()))
            self.assertEqual(pre_host_status, (0, 0), (tick, pre_host_status))
            self.assertFalse(pandad.heartbeat)
          panda = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now)
          panda.pandaStates[0].safetyModel = cp.safetyConfigs[0].safetyModel
          panda.pandaStates[0].safetyParam, panda.pandaStates[0].alternativeExperience = word, 32
          panda.pandaStates[0].ignitionLine = True
          if tick >= 110 and fault in ('relay', 'diagnostic'):
            panda.pandaStates[0].faults = ['relayMalfunction' if fault == 'relay' else 'interruptRateCan2']
          panda.pandaStates[0].controlsAllowed = bool(safety.get_controls_allowed())
          panda.pandaStates[0].safetyRxChecksInvalid = not bool(safety.safety_config_valid())
          previous_event = controls_latest.get('carControl')
          if previous_event is not None:
            self.assertEqual(previous_event.carControl.to_dict(), previous.to_dict())
            self.assertEqual(int(previous_event.logMonoTime), int(((base + (tick - 1) * 10_000_000 + 2000) / 1e9) * 1e9))
            self.assertGreater(now - int(previous_event.logMonoTime), 0)
            self.assertLessEqual(now - int(previous_event.logMonoTime), 150_000_000)
          # No fabricated validity or renewed source stamp at the consumer boundary.
          device = messaging.new_message('deviceState', valid=True, logMonoTime=now)
          device.deviceState.started, device.deviceState.startedMonoTime = True, 500_000_000
          peers = [panda.as_reader(), device.as_reader()]
          if previous_event is not None:
            peers.append(previous_event)
          peers += [driven_new[service] for service in ('onroadEvents', 'aolAxisState', 'selfdriveState') if service in driven_new]
          if receipt is not None:
            peers.append(receipt.as_reader())
          card.sm.update_msgs((now + 1000) / 1e9, peers)
          if previous_event is not None:
            self.assertEqual(card.sm.logMonoTime['carControl'], previous_event.logMonoTime)
            self.assertEqual(card.sm.valid['carControl'], previous_event.valid)
          published = {}
          real_new = messaging.new_message

          def stamped(service, size=None, _new=real_new, _now=now, **kwargs):
            return _new(service, size, **({'logMonoTime': _now} | kwargs))

          controller_frame_before = ci.CC.frame
          with (
            patch.object(card.sm, 'update'),
            patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw', return_value=[b'can']),
            patch('openpilot.selfdrive.car.card.can_capnp_to_list', return_value=[(now, incoming)]),
            patch('openpilot.selfdrive.car.card.REPLAY', False),
            patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now + 2000),
            patch('openpilot.selfdrive.car.card.time.monotonic', return_value=(now + 2000) / 1e9),
            patch('openpilot.selfdrive.car.card.time.clock_gettime_ns', return_value=now + 2000),
            patch('openpilot.selfdrive.car.card.messaging.new_message', side_effect=stamped),
            patch.object(
              card.pm,
              'send',
              side_effect=lambda service, msg, _published=published: _published.update(
                {service: messaging.log_from_bytes(msg) if isinstance(msg, bytes) else msg.as_reader()}
              ),
            ),
          ):
            card.step()
          controller_frame_after = ci.CC.frame
          self.assertIn(controller_frame_after - controller_frame_before, (0, 1))
          cs = published['carState'].carState
          pt_parser = ci.can_parsers[Bus.pt]
          eps_state = next(state for state in pt_parser.message_states.values() if state.name == 'STEER_STATUS')
          parser_observation = {
            'frequency': eps_state.frequency,
            'timeout_threshold': eps_state.timeout_threshold,
            'last_seen': eps_state.timestamps[-1],
            'now': now,
            'elapsed': now - eps_state.timestamps[-1],
            'invalid_count': pt_parser.can_invalid_cnt,
          }
          self.assertTrue(cs.canValid, (tick, parser_observation))
          self.assertFalse(cs.canTimeout)
          self.assertAlmostEqual(cs.vEgoRaw, speed_mps, places=5)
          self.assertFalse(cs.standstill or cs.seatbeltUnlatched)
          self.assertFalse(cs.steerFaultPermanent)
          self.assertEqual(cs.steerFaultTemporary, 80 <= tick < 95)
          self.assertEqual(cs.gearShifter, structs.CarState.GearShifter.reverse if 110 <= tick < 125 else structs.CarState.GearShifter.drive)
          self.assertEqual(cs.brakePressed, 50 <= tick < 55 or eps_pedal == 'brake' and 80 <= tick < 95)
          self.assertTrue(cs.cruiseState.available)
          self.assertEqual(cs.cruiseState.enabled, 21 <= tick < 50 or reengage)
          intent = decode_intent(published['aolIntentWire'].aolIntentWire)
          self.assertIsNotNone(intent)
          # Initial receipt is unavailable until actual negotiator completes its zero acknowledgment.
          absent = messaging.new_message('aolSafetyWire', 0, valid=False, logMonoTime=now)
          current_receipt = receipt if receipt is not None else absent
          driven_new = selfdrive_step(
            sd, published, current_receipt, controls_published, cp, safety, now, calibration='calibrated',
            driver_lockout=host_scenario == 'driver_lockout' and tick >= 135,
            driver_monitoring_valid=not (host_scenario == 'driver_lockout' and tick >= 140))
          retained = dict(driven)
          driven.update(driven_new)
          for service, event in retained.items():
            if service not in driven_new:
              self.assertIs(driven[service], event)
              self.assertLessEqual(int(event.logMonoTime), now)
          # Held producer snapshots are only for native observation. Simulated
          # receivers get each actual new publication once, never a restamp.
          order = None
          if tick % 10 == 0:
            receipt, order = pandad.step(driven, published, now)
          feed(controls, now, tick, enabled=sd.enabled, active=sd.active)
          plan = messaging.new_message('longitudinalPlan', valid=True, logMonoTime=now)
          plan.longitudinalPlan.aTarget = 0.5
          controls_peers = [published['carState'], published['carOutput'], plan.as_reader()]
          controls_peers += [driven_new[service] for service in ('selfdriveState', 'onroadEvents', 'aolAxisState') if service in driven_new]
          if tick % 5 == 0:
            # Same perception scaffold, modest 0.001/m curvature (0.156m/s² at12.5m/s).
            # Production Controls derives torque; no actuator or native state is injected.
            perception = messaging.new_message('modelV2', valid=True, logMonoTime=now)
            perception.modelV2 = controls.sm['modelV2'].as_builder()
            perception.modelV2.action.desiredCurvature = 0.001
            controls_peers.append(perception.as_reader())
          if receipt is not None:
            controls_peers.append(receipt.as_reader())
          controls.sm.update_msgs((now + 1000) / 1e9, controls_peers)
          controls_published = {}

          def capture_control(service, message, _output=controls_published):
            wire = message if isinstance(message, bytes) else message.to_bytes()
            _output[service] = messaging.log_from_bytes(wire)

          with (
            patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now + 2000),
            patch('openpilot.cereal.messaging.time.monotonic', return_value=(now + 2000) / 1e9),
            patch.object(controls.pm, 'send', side_effect=capture_control),
          ):
            computed, lateral_log = controls.state_control()
            controls.publish(computed, lateral_log)
          retained_controls = dict(controls_latest)
          controls_latest.update(controls_published)
          for service, event in retained_controls.items():
            if service not in controls_published:
              self.assertIs(controls_latest[service], event)
              self.assertLessEqual(int(event.logMonoTime), now)
          control_event = controls_published['carControl']
          command = control_event.carControl
          self.assertEqual(control_event.valid, cs.canValid)
          self.assertEqual(int(control_event.logMonoTime), int(((now + 2000) / 1e9) * 1e9))
          self.assertEqual(command.to_dict(), computed.to_dict())
          outgoing = can_capnp_to_list([published['sendcan'].as_builder().to_bytes()], msgtype='sendcan')[0][1] if 'sendcan' in published else []
          cached_invalid = previous_event is not None and not previous_event.valid
          if cached_invalid:
            self.assertFalse(card.sm.valid['carControl'])
            self.assertTrue(card.sm.all_alive(['carControl']))
            self.assertEqual(card.sm.logMonoTime['carControl'], previous_event.logMonoTime)
            self.assertFalse(outgoing)
            self.assertNotIn('sendcan', published)
          row = {
            'tick': tick,
            'heartbeat': pandad.heartbeat,
            'native_order': order,
            'controls': bool(safety.get_controls_allowed()),
            'request': int(safety.aol_get_request_mask()),
            'permission': int(safety.aol_get_permission_mask()),
            'intent': intent.allowedLatch,
            'stored_latch': card.aol_card_intent.allowed_latch,
            'armed': intent.lateralArmed,
            'standard_enabled': sd.enabled,
            'lat': command.latActive,
            'long': command.longActive,
            'previous': previous.to_dict(),
            'parsed_state': cs.to_dict(),
            'frames': [(a, d.hex(), b) for a, d, b in outgoing],
            'TX': [],
          }
          row['pre_host_status'] = pre_host_status
          row['parser_observation'] = parser_observation
          TRACE.append(row)
          tx_parser.update([(now, outgoing)])
          for address, data, bus in outgoing:
            accepted = bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)))
            tx = {'address': address, 'bus': bus, 'accepted': accepted, 'bytes': data.hex()}
            if address == steer_msg.address and bus == ci.CC.CAN.lkas:
              tx.update(torque=int(tx_parser.vl['STEERING_CONTROL']['STEER_TORQUE']), request=bool(tx_parser.vl['STEERING_CONTROL']['STEER_TORQUE_REQUEST']))
              if not accepted:
                self.assertTrue(tick in (80, 110) or fault == "relay" and tick >= 110, row)
                self.assertFalse(command.latActive, row)
                if tick in (80, 110):
                  self.assertTrue(previous.latActive, row)
                self.assertFalse(row['permission'] & 1, row)
              if not previous.latActive:
                self.assertEqual((tx['torque'], tx['request']), (0, False), row)
            else:
              if (alpha and address == 0x1DF or pedal and address == 0x200) and tick == 50:
                # Card applies the prior Controls envelope. Physical brake has
                # already revoked LONG; the native hook must reject this one
                # in-flight positive command before the new envelope arrives.
                self.assertFalse(accepted, row)
                self.assertTrue(previous.longActive, row)
                self.assertFalse(command.longActive or row['controls'] or row['permission'] & 2, row)
              else:
                self.assertTrue(accepted or fault == "relay" and tick >= 110, row)
              if alpha and address == 0x1DF and tick >= 52 and not reengage:
                gas = int.from_bytes(data[:2], 'big', signed=True)
                accel = (data[3] << 3) | (data[4] >> 5)
                accel = accel - 0x800 if accel & 0x400 else accel
                self.assertEqual((gas, accel), (-30000, 0), row)
            row['TX'].append(tx)
          row['events'] = [str(e.name) for e in driven['onroadEvents'].onroadEvents]
          row['fault_reason'] = str(driven['aolAxisState'].aolAxisState.faultReason)
          if tick in (45, 79, 105) or tick == 135 and fault != "relay" and host_scenario not in ("driver_lockout", "settings_off"):
            self.assertTrue(command.latActive, row)
            self.assertTrue(any(t.get('accepted') and t.get('request') and t.get('torque') for t in row['TX']), row)
            self.assertTrue(intent.allowedLatch, row)
          if eps_pedal and 82 <= tick < 95:
            self.assertTrue(cs.steerFaultTemporary and cs.steeringPressed, row)
            self.assertTrue(cs.brakePressed if eps_pedal == 'brake' else cs.gasPressed, row)
            self.assertTrue(card.aol_card_intent.allowed_latch, row)
            self.assertFalse(command.latActive or command.longActive or row['permission'] & 1, row)
          if tick == 90 or tick == 120 and fault != "relay":
            self.assertTrue(card.aol_card_intent.allowed_latch, row)
            self.assertTrue(intent.lateralArmed, row)
            self.assertFalse(command.latActive or command.longActive, row)
            self.assertFalse(row['permission'] & 1, row)
          if tick < 20:
            self.assertFalse(intent.allowedLatch or command.latActive or command.longActive, row)
          if host_scenario == "driver_lockout" and 135 <= tick < 140:
            # Current shared policy retains an already active, source-bound LAT
            # owner during fresh DM lockout, with LONG inhibited.
            self.assertTrue(sd.sm['driverMonitoringState'].lockout, row)
            self.assertTrue(sd.aol_availability_long_inhibit, row)
            self.assertTrue(command.latActive and row['permission'] & 1, row)
            self.assertFalse(command.longActive or row['permission'] & 2, row)
          withdraw = host_scenario == "settings_off" and tick >= 137 or host_scenario == "driver_lockout" and tick >= 142
          if withdraw:
            self.assertFalse(command.latActive or command.longActive, row)
            if tick == 144:
              if host_scenario == "settings_off":
                self.assertFalse(row["permission"] & 3, row)
              else:
                # A short availability pause retains the native LAT owner.
                # Host commands still withdraw, including the actual CAN TX.
                self.assertEqual(row["fault_reason"], "availabilityPause", row)
                self.assertEqual(row["permission"], 1, row)
                steer = [tx for tx in row['TX'] if 'torque' in tx]
                self.assertTrue(steer, row)
                self.assertTrue(all(tx['accepted'] and tx['torque'] == 0 and not tx['request'] for tx in steer), row)
          if host_scenario == "long_reengage" and tick == 135:
            self.assertTrue(command.latActive, row)
            self.assertFalse(command.longActive or row["permission"] & 2, row)
          if host_scenario == "long_reengage" and tick == 144:
            self.assertTrue(command.latActive and command.longActive, row)
            self.assertEqual(row["permission"], 3, row)
          if tick >= 55 and not reengage:
            self.assertFalse(command.longActive, row)
          if fault == 'relay' and tick >= 120:
            self.assertFalse(card.aol_card_intent.allowed_latch or intent.lateralArmed, row)
            self.assertFalse(command.latActive or command.longActive or row['permission'] & 1, row)
          previous = command
        TRACE.append(
          {
            'completed': True,
            'candidate': str(candidate),
            'word': word,
            'pcmCruise': cp.pcmCruise,
            'opLong': cp.openpilotLongitudinalControl,
            'scenario': 'temporary_eps_and_reverse_return',
            'cycles': 145,
          }
        )
      finally:
        pandad.close()
        card.vehicle_startup.close()
