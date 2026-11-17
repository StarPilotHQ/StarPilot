import os
import tempfile
import unittest
from unittest.mock import patch

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.selfdrive.selfdrived.events import EventName
from opendbc.car import structs
from opendbc.car.gm.bolt_cc import button_bytes
from opendbc.car.gm.gmcan import pedal_crc
from opendbc.car.gm.tests.test_bolt_cc import feed, fixture, native, setup
from opendbc.car.gm.values import AccState, CAR
from opendbc.safety.tests.libsafety import libsafety_py


class TestBoltCcControlsLow(unittest.TestCase):
  def test_final_params_real_controls_low_drive_override_and_cancel(self):
    cases = ((CAR.CHEVROLET_BOLT_CC_2018_2021, False, False),
             (CAR.CHEVROLET_BOLT_CC_2018_2021, True, False),
             (CAR.CHEVROLET_BOLT_CC_2018_2021, False, True),
             (CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, False, True))
    for identity, removed, pedal in cases:
      stock_acc = identity == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL
      with self.subTest(removed=removed, pedal=pedal), tempfile.TemporaryDirectory() as params_root, \
           patch.dict(os.environ, {'PARAMS_ROOT': params_root, 'SIMULATION': '1', 'REPLAY': '1'}), OpenpilotPrefix():
        params = Params()
        for key in ('OpenpilotEnabledToggle',):
          params.put_bool(key, True, block=True)
        for key in ('AlwaysOnLateral', 'SafeMode', 'AdvancedLateralTune'):
          params.put_bool(key, False, block=True)
        cp, ci, packer = fixture(identity, removed=removed, present=pedal)
        if stock_acc:
          from openpilot.starpilot.aol.tests.test_gm import TestGmAol
          selected = TestGmAol.card(cp, params)
          cp, ci = selected.CP, selected.CI
          self.assertIsNone(selected.aol_card_intent)
          self.assertEqual(cp.alternativeExperience, 0)
        cp.fingerprintSource = structs.CarParams.FingerprintSource.can
        cp.carFw = []
        params.put('CarParams', cp.as_reader().as_builder().to_bytes(), block=True)
        controls = Controls()
        self.assertEqual(controls.CP.to_dict(), cp.to_dict())
        self.assertEqual(controls.CP.safetyConfigs[0].safetyParam, 0x1CD if stock_acc else 0x9D if pedal else 0xC121 if removed else 0xC120)
        _, template = feed(ci, packer, 900_000_000, active=False, camera=not removed)
        setup(cp)
        with patch("openpilot.selfdrive.selfdrived.selfdrived.REPLAY", False):
          sd = SelfdriveD(CP=cp)
        previous_control = structs.CarControl()
        active_frames = gas_frames = cancelled_frames = 0
        drive_pedal_frames = low_pedal_frames = stock_withdrawals = 0
        last_feeds = {}
        steering_receipts = []
        internal_frames = 0
        internal_status_frames = 0
        from opendbc.safety.tests.test_gm_bolt_pedal import TestGmBoltPedalSafety
        recorder = TestGmBoltPedalSafety()
        recorder.safety = libsafety_py.libsafety
        if pedal and not stock_acc:
          # An arbitrary host status cannot acquire the physical RX-owned feed.
          libsafety_py.libsafety.reset_recorded_can()
          self.assertFalse(native('tx', (0x3D1, bytes(8), 0), 900_000))
          self.assertEqual(recorder.recorded(), [])
        for tick in range(360):
          now = 1_000_000_000 + tick * 10_000_000
          gas = 120 <= tick < 150
          button = 3 if 40 <= tick < 42 else 6 if 320 <= tick < 322 else 1
          replacement = [
            packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 4 if (stock_acc and tick < 180) or 180 <= tick < 220 else 6}),
            packer.make_can_msg('ECMEngineStatus', 0, {'CruiseMainOn': int(not stock_acc or tick < 330)}),
            packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': int(tick >= 42), 'CruiseSetSpeed': 54}),
            packer.make_can_msg('AcceleratorPedal2', 0, {'AcceleratorPedal2': 30 if gas else 0,
              'CruiseState': AccState.FAULTED if stock_acc and 310 <= tick < 315 else int(stock_acc and 240 <= tick < 260)}),
            (0x1E1, button_bytes(button, tick % 4), 0),
          ]
          if stock_acc:
            if tick % 4 == 0:
              replacement.append(packer.make_can_msg('EBCMFrictionBrakeCmd', 2, {'RollingCounter': (tick // 4) % 4}))
            replacement.append(packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 2}))
            replacement.append(packer.make_can_msg('EBCMFrictionBrakeStatus', 0,
                                                    {'FrictionBrakeUnavailable': int(310 <= tick < 315)}))
          if pedal and tick % 2 == 0:
            raw = bytearray.fromhex(('040201fe0000' if gas else '0264011d0000') if stock_acc else
                                     ('053502ba0000' if gas else '0279012a0000'))
            raw[4] = (tick // 2) % 16
            raw[5] = pedal_crc(raw)
            replacement.append((0x201, bytes(raw), 0))
          replaced = {frame[0] for frame in replacement}
          frames = [frame for frame in template if frame[0] not in replaced] + replacement
          libsafety_py.libsafety.reset_recorded_can()
          for frame in sorted(frames, key=lambda frame: frame[0] in (0x3D1, 0xBD, 0x1F5)):
            if frame[0] in (0x184, 0x3D1, 0x1E1, 0xC9, 0x1C4, 0x1F5, 0x34A, 0x201, 0xBD):
              self.assertTrue(native('rx', frame, now // 1000))
          if pedal:
            for address, bus, payload in recorder.recorded():
              self.assertEqual(bus, 0)
              self.assertEqual(payload, last_feeds[address])
              internal_frames += 1
              internal_status_frames += int(address == 0x3D1)
          libsafety_py.libsafety.safety_tick()
          cs = ci.update([(now - 5_000_000, steering_receipts), (now, frames)])
          steering_receipts = []
          self.assertTrue(cs.canValid)
          expected_gear = (structs.CarState.GearShifter.drive if (stock_acc and tick < 180) or 180 <= tick < 220
                           else structs.CarState.GearShifter.low)
          self.assertEqual(cs.gearShifter, expected_gear)
          cs.vCruise = cs.vCruiseCluster = 100.
          messages = []

          def message(service, size=None, now=now, messages=messages):
            msg = messaging.new_message(service, size, valid=True, logMonoTime=now)
            messages.append(msg)
            return getattr(msg, service)

          state = message('carState')
          state.from_dict(cs.to_dict())
          parameters = message('vehicleParameters')
          parameters.stiffnessFactor, parameters.steerRatio = 1., cp.steerRatio
          message('longitudinalPlan').aTarget = 1.
          message('modelV2').action.desiredCurvature = .00001
          message('lateralDelay').lateralDelay = .1
          message('carOutput')
          peers = []
          for service in sd.sm.data:
            if service in ('alertDebug', 'lateralManeuverPlan', 'laneChangeAssistWire'):
              continue
            size = 1 if service == 'pandaStates' else 0 if service == 'laneChangeAssistWire' else None
            peer = messaging.new_message(service, size,
                                         valid=True, logMonoTime=now)
            payload = getattr(peer, service)
            if service == 'pandaStates':
              payload[0].safetyModel = cp.safetyConfigs[0].safetyModel
              payload[0].safetyParam = cp.safetyConfigs[0].safetyParam
              payload[0].alternativeExperience = cp.alternativeExperience
              payload[0].controlsAllowed = bool(libsafety_py.libsafety.get_controls_allowed())
              payload[0].safetyRxChecksInvalid = not bool(libsafety_py.libsafety.safety_config_valid())
            elif service == 'extrinsicsCalibration':
              payload.calStatus = 'calibrated'
              payload.rpyCalib = [0., 0., 0.]
            elif service == 'vehicleParameters':
              payload.stiffnessFactor, payload.steerRatio, payload.valid = 1., cp.steerRatio, True
            elif service == 'deviceMotion':
              payload.inputsOK = payload.posenetOK = True
              payload.velocityDevice.x = cs.vEgo
            elif service == 'controlsState':
              payload.lateralControlState.init('torqueState')
            elif service == 'carControl':
              payload.from_dict(previous_control.to_dict())
            elif service == 'deviceState':
              payload.freeSpacePercent = 50.
            peers.append(peer.as_reader())
          car_state_event = messages[0].as_reader()
          with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False), \
               patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=car_state_event), \
               patch.object(sd.sm, 'update', side_effect=lambda timeout, now=now, peers=peers, sd=sd:
                            sd.sm.update_msgs(now / 1e9, peers)):
            sd.step()
          self.assertEqual(sd.mismatch_counter, int(not stock_acc and tick == 320), tick)
          self.assertNotIn(EventName.controlsMismatch, sd.events.names)
          host = message('selfdriveState')
          host.enabled, host.active, host.state = sd.enabled, sd.active, sd.state_machine.state
          event_message = messaging.new_message('onroadEvents', len(sd.events.to_msg()), valid=True, logMonoTime=now)
          event_message.onroadEvents = sd.events.to_msg()
          messages.append(event_message)
          controls.sm.update_msgs(now / 1e9, [msg.as_reader() for msg in messages])
          cc, lateral_log = controls.state_control()
          self.assertEqual(cc.enabled, sd.enabled)
          controls.publish(cc, lateral_log)
          self.assertEqual(cc.cruiseControl.cancel, cs.cruiseState.enabled and (not cc.enabled or not cp.pcmCruise))
          _, commands = ci.apply(cc.as_reader(), now + 1_000_000)
          for frame in commands:
            allowed = native('tx', frame, (now + 1_000_000) // 1000)
            if pedal and frame[0] in (0xBD, 0x1F5, 0x3D1):
              # The host schedules these exact feeds; matching physical RX emits
              # them internally. They never grant ordinary host TX permission.
              self.assertFalse(allowed)
              last_feeds[frame[0]] = frame[1]
            else:
              self.assertTrue(allowed, (tick, hex(frame[0])))
              if frame[0] == 0x180:
                steering_receipts.append((frame[0], bytes(frame[1]), 128))
          if stock_acc and (60 <= tick < 100 or 220 <= tick < 240):
            pedals = [frame for frame in commands if frame[0] == 0x200]
            for frame in pedals:
              self.assertTrue(frame[1][4] & 0x80, (tick, cs.gearShifter, frame))
              if cs.gearShifter == structs.CarState.GearShifter.drive:
                drive_pedal_frames += 1
              else:
                low_pedal_frames += 1
          if stock_acc and 240 <= tick < 260:
            for frame in commands:
              if frame[0] == 0x200:
                self.assertFalse(frame[1][4] & 0x80)
                stock_withdrawals += 1
            self.assertFalse(any(frame[0] == 0x315 for frame in commands))
            self.assertEqual(libsafety_py.libsafety.safety_fwd_hook(2, 0x315), 0)
          if stock_acc and tick >= 330:
            self.assertFalse(cs.cruiseState.available)
            self.assertFalse(any(frame[0] == 0x315 for frame in commands))
            self.assertEqual(libsafety_py.libsafety.safety_fwd_hook(2, 0x315), 0)
          if stock_acc and 310 <= tick < 315:
            self.assertTrue(cs.accFaulted)
            self.assertIn(EventName.accFaulted, sd.events.names)
            self.assertFalse(cc.enabled or cc.latActive or cc.longActive)
          if 45 <= tick < (310 if stock_acc else 320):
            self.assertTrue(cc.enabled)
            self.assertTrue(cc.latActive)
            self.assertTrue(libsafety_py.libsafety.get_controls_allowed())
            active_frames += 1
          if gas:
            self.assertFalse(cc.longActive)
            self.assertTrue(cc.cruiseControl.override)
            gas_frames += 1
          if tick >= 322:
            self.assertFalse(cc.enabled or cc.latActive or cc.longActive)
            cancelled_frames += 1
          previous_control = cc
        self.assertGreater(active_frames, 200)
        self.assertEqual(gas_frames, 30)
        self.assertEqual(cancelled_frames, 38)

        if stock_acc:
          self.assertGreater(drive_pedal_frames, 0)
          self.assertGreater(low_pedal_frames, 0)
          self.assertGreater(stock_withdrawals, 0)
        if pedal:
          if not stock_acc:
            self.assertGreater(internal_frames, 0)
            self.assertGreater(internal_status_frames, 0)
          for index, button in enumerate((3, 1)):
            rearm = [frame for frame in frames if frame[0] not in (0x1E1, 0x201, 0xC9)]
            rearm.append(packer.make_can_msg("ECMEngineStatus", 0, {"CruiseMainOn": 1}))
            raw = bytearray.fromhex('0279012a0000')
            raw[4] = (4 + index) % 16
            raw[5] = pedal_crc(raw)
            rearm += [(0x1E1, button_bytes(button, index), 0), (0x201, bytes(raw), 0)]
            for frame in rearm:
              if frame[0] in (0x184, 0x1E1, 0xC9, 0x1C4, 0x1F5, 0x34A, 0x201, 0xBD):
                self.assertTrue(native('rx', frame, 4_650_000 + index * 20_000))
          self.assertTrue(libsafety_py.libsafety.get_controls_allowed())
          raw = bytearray.fromhex('0279012a0600')
          raw[5] = pedal_crc(raw) ^ 1
          self.assertTrue(native('rx', (0x201, bytes(raw), 0), 4_700_000))
          self.assertFalse(libsafety_py.libsafety.get_controls_allowed())
          ci.update([(4_700_000_000, [(0x201, bytes(raw), 0)])])
          self.assertFalse(ci.CS.pedal_sensor_healthy)
          continue

        maximum_mismatch = 0
        mismatch_observed = False
        for tick in range(225):
          now = 5_000_000_000 + tick * 10_000_000
          replacement = [
            packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 6}),
            packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': int(tick < 8), 'CruiseSetSpeed': 54}),
            packer.make_can_msg('AcceleratorPedal2', 0, {'AcceleratorPedal2': 0}),
            (0x1E1, button_bytes(3 if 2 <= tick < 4 else 1, tick % 4), 0),
          ]
          replaced = {frame[0] for frame in replacement}
          frames = [frame for frame in template if frame[0] not in replaced] + replacement
          for frame in sorted(frames, key=lambda frame: frame[0] == 0x3D1):
            if frame[0] in (0x184, 0x3D1, 0x1E1, 0xC9, 0x1C4, 0x1F5, 0x34A, 0x201, 0xBD):
              self.assertTrue(native('rx', frame, now // 1000))
          libsafety_py.libsafety.safety_tick()
          cs = ci.update([(now, frames)])
          self.assertTrue(cs.canValid)
          cs.vCruise = cs.vCruiseCluster = 100.
          current_peers = []
          for peer in peers:
            fresh = peer.as_builder()
            fresh.logMonoTime = now
            if fresh.which() == 'pandaStates':
              fresh.pandaStates[0].controlsAllowed = bool(libsafety_py.libsafety.get_controls_allowed())
              fresh.pandaStates[0].safetyRxChecksInvalid = not bool(libsafety_py.libsafety.safety_config_valid())
            elif fresh.which() == 'carControl':
              fresh.carControl.from_dict(previous_control.to_dict())
            current_peers.append(fresh.as_reader())
          current_state = messaging.new_message('carState', valid=True, logMonoTime=now)
          current_state.carState = cs
          with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False), \
               patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=current_state.as_reader()), \
               patch.object(sd.sm, 'update', side_effect=lambda timeout, now=now, current_peers=current_peers, sd=sd:
                            sd.sm.update_msgs(now / 1e9, current_peers)):
            sd.step()
          maximum_mismatch = max(maximum_mismatch, sd.mismatch_counter)
          mismatch_observed |= EventName.controlsMismatch in sd.events.names
          if 5 <= tick < 8:
            self.assertTrue(sd.enabled)
          if tick >= 8:
            self.assertFalse(libsafety_py.libsafety.get_controls_allowed())
        self.assertEqual(maximum_mismatch, 200)
        self.assertTrue(mismatch_observed)
        self.assertFalse(sd.enabled)

