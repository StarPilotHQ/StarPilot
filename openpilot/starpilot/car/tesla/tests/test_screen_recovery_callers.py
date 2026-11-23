"""Native-heartbeat and physical screen recovery through actual Tesla callers."""
import os
from dataclasses import replace
from types import SimpleNamespace
import unittest
import pytest
from unittest.mock import patch

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.values import CAR
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car.card import Car
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.starpilot.aol.wire import SafetyState, PandaSlot, encode_safety
from openpilot.starpilot.aol.runtime import current_native
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
from openpilot.starpilot.tests.tesla_fixture import DBC, EPS, FW_VERSIONS, TeslaFixture


def screen_params(platform, marker, *, longitudinal=False, release=False):
  fingerprint = gen_empty_fingerprint()
  fingerprint[1][0x3DF] = 8
  fingerprint[2][0x293] = 8
  fingerprint[0 if marker == 'marker054' else 2][0x054 if marker == 'marker054' else 0x489] = 8
  fw = structs.CarParams.CarFw(ecu=EPS[0], address=EPS[1], brand='tesla', fwVersion=FW_VERSIONS[platform][EPS][0])
  return CarInterface.get_params(platform, fingerprint, [fw], longitudinal and not release, release, False)


def start_card(cp, *, after_snapshot=None):
  def create(*args, pre_create_hook, **kwargs):
    if after_snapshot is not None:
      after_snapshot()
    return CarInterface(pre_create_hook(cp.as_reader().as_builder(), cp.carFingerprint, {}, []))
  with patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=SimpleNamespace(can=[1])), \
       patch('openpilot.selfdrive.car.card.get_car', side_effect=create):
    return Car()


def selfdrive_step(sd, published, native, previous_command, cp, safety, now):
  peers = []
  for service in sd.sm.data:
    if service in ('alertDebug', 'lateralManeuverPlan', 'laneChangeAssistWire'):
      continue
    if service == 'pandaStates' and service in published:
      peers.append(published[service])
      continue
    if service == 'aolIntentWire':
      peers.append(published[service])
      continue
    if service == 'aolSafetyWire':
      peers.append(native.as_reader())
      continue
    size = 1 if service == 'pandaStates' else None
    event = messaging.new_message(service, size, valid=True, logMonoTime=now)
    value = getattr(event, service)
    if service == 'pandaStates':
      value[0].safetyModel = cp.safetyConfigs[0].safetyModel
      value[0].safetyParam = cp.safetyConfigs[0].safetyParam
      value[0].alternativeExperience = cp.alternativeExperience
      value[0].controlsAllowed = bool(safety.get_controls_allowed())
      value[0].safetyRxChecksInvalid = not bool(safety.safety_config_valid())
    elif service == 'extrinsicsCalibration':
      value.calStatus, value.rpyCalib = 'calibrated', [0., 0., 0.]
    elif service == 'vehicleParameters':
      value.stiffnessFactor, value.steerRatio, value.valid = 1., cp.steerRatio, True
    elif service == 'deviceMotion':
      value.inputsOK = value.posenetOK = True
      value.velocityDevice.x = published['carState'].carState.vEgo
    elif service == 'controlsState':
      value.lateralControlState.init('angleState')
    elif service == 'carControl':
      value.from_dict(previous_command.to_dict())
    elif service == 'deviceState':
      value.freeSpacePercent = 50.
    peers.append(event.as_reader())
  output = {}
  with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False), \
       patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now), \
       patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=published['carState']), \
       patch.object(sd.sm, 'update', side_effect=lambda _: sd.sm.update_msgs(now / 1e9, peers)), \
       patch.object(sd.pm, 'send', side_effect=lambda service, msg: output.update({service: msg.as_reader()})):
    sd.step()
  return output


def transport_receipt(native, tick, now):
  if tick == 48:
    return replace(native, validUntilMonoTime=now - 1)
  if tick == 49:
    return replace(native, safetyParam=native.safetyParam ^ 1024)
  if tick == 50:
    return replace(native, safetyModel=39, safetyParam=0)
  if tick == 51:
    return replace(native, axisSessionId='other-session')
  if tick == 52:
    return replace(native, safetyParam=native.safetyParam ^ 1)
  if tick == 53:
    return replace(native, safetyParam=514)
  return native



class Exercise(unittest.TestCase):
  def run_case(self, fault, selected_platform, selected_marker, selected_brake_option, selected_longitudinal):
    master_off = False
    safety = libsafety_py.libsafety
    release = safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0
    for platform in (selected_platform,):
      for marker in (selected_marker,):
        for brake_option, longitudinal in ((selected_brake_option, selected_longitudinal),):
          with self.subTest(platform=platform, marker=marker, brake_option=brake_option, longitudinal=longitudinal), OpenpilotPrefix(), \
               patch.dict(os.environ, {'SIMULATION': '1', 'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}):
            saved = Params()
            for key, value in (('OpenpilotEnabledToggle', True), ('AlwaysOnLateral', True),
                               ('TeslaAOLScreenTap', True), ('TeslaAOLDisengageOnBrake', brake_option),
                               ('IsReleaseBranch', release), ('SafeMode', False)):
              saved.put_bool(key, value, block=True)
            if master_off:
              from openpilot.selfdrive.car import card as card_module
              requested = card_module.feature_requested
              saved.put_bool('AlwaysOnLateral', False, block=True)
              def sample_request(params, feature, requested=requested, saved=saved):
                result = requested(params, feature)
                if feature == 'aol':
                  saved.put_bool('AlwaysOnLateral', True, block=True)
                return result
              with patch.object(card_module, 'feature_requested', side_effect=sample_request), \
                   patch.object(card_module, 'can_comm_callbacks', wraps=card_module.can_comm_callbacks) as callbacks:
                selected = start_card(screen_params(platform, marker, longitudinal=longitudinal, release=release),
                                      after_snapshot=lambda saved=saved: saved.put_bool('AlwaysOnLateral', False, block=True))
              self.assertIs(callbacks.call_args.args[1], selected.pm.sock['sendcan'])
              self.assertIn('aolSafetyWire', selected.sm.data)
              saved.put_bool('AlwaysOnLateral', False, block=True)
              iterations = iter((False, True))
              with patch('openpilot.selfdrive.car.card.time.sleep'):
                selected.params_thread(SimpleNamespace(is_set=lambda iterations=iterations: next(iterations)))
            else:
              selected = start_card(screen_params(platform, marker, longitudinal=longitudinal, release=release))
            cp, ci = selected.CP, selected.CI
            # Startup also admits the scroll speed sync frame (4096) on a car with the cable and StarPilot longitudinal.
            self.assertEqual(cp.safetyConfigs[0].safetyParam, (1536 if brake_option else 512) + (4097 if cp.openpilotLongitudinalControl else 0))
            self.assertEqual(cp.alternativeExperience, 32)
            self.assertEqual(cp.openpilotLongitudinalControl, longitudinal and not release)
            self.assertTrue(cp.pcmCruise)
            self.assertIsNotNone(selected.aol_card_intent)
            controls = Controls()
            with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False):
              sd = SelfdriveD(CP=cp)
            self.assertTrue(controls.aol_replay)
            self.assertTrue(sd.aol_replay)
            self.assertEqual(controls.CP.to_dict(), cp.to_dict())
            packer = CANPacker(DBC)
            safety.init_tests()
            safety.set_alternative_experience(cp.alternativeExperience)
            self.assertEqual(safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam), 0)
            observed = {}
            previous_receipt = None
            previous_command = structs.CarControl()
            selfdriven = {}
            for tick in range(210):
              now = 1_000_000_000 + tick * 10_000_000
              cruise = 0
              brake = False
              unbuckled = False
              bad = 80 <= tick < 110
              counts = (3,) if tick in (40, 160, 190) else (0,)
              incoming = []
              for bus, name, period, defaults in TeslaFixture.INPUTS:
                if tick % period:
                  continue
                values = dict(defaults)
                if name == 'DI_systemStatus':
                  values['DI_gear'] = (2 if fault == 'reverse' else 3) if bad and fault in ('reverse', 'neutral') else 4
                if name == 'DI_state':
                  values['DI_cruiseState'] = cruise
                if name == 'ESP_status':
                  values['ESP_driverBrakeApply'] = 2 if brake else 1
                if name == 'UI_warning':
                  values['buckleStatus'] = 0 if unbuckled else 1
                if name == 'EPAS3S_sysStatus':
                  values['EPAS3S_handsOnLevel'] = 0
                  values['EPAS3S_eacStatus'] = 0 if bad and fault == 'temporary' else 3 if bad and fault == 'permanent' else 1
                incoming.append(packer.make_can_msg(name, bus, values))
              incoming.extend((0x3DF, bytes((0, 0, 0, count, 0, 0, 0, 0)), 1) for count in counts)
              safety.set_timer(now // 1000)
              for address, payload, bus in incoming:
                self.assertTrue(safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, payload)))
              safety.safety_tick()
              safety.set_aol_test_heartbeat(bool(selected.aol_card_intent.allowed_latch or sd.enabled))
              peers = []
              control = messaging.new_message('carControl', valid=True, logMonoTime=now)
              control.carControl = previous_command
              peers.append(control.as_reader())
              events = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now)
              peers.append(selfdriven['onroadEvents'] if selfdriven else events.as_reader())
              panda = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now)
              panda.pandaStates[0].safetyModel = cp.safetyConfigs[0].safetyModel
              panda.pandaStates[0].safetyParam = cp.safetyConfigs[0].safetyParam
              panda.pandaStates[0].alternativeExperience = cp.alternativeExperience
              panda.pandaStates[0].controlsAllowed = bool(safety.get_controls_allowed())
              panda.pandaStates[0].safetyRxChecksInvalid = not bool(safety.safety_config_valid())
              peers.append(panda.as_reader())
              if previous_receipt is not None:
                peers.append(previous_receipt.as_reader())
              selected.sm.update_msgs(now / 1e9, peers)
              selected.CC_prev = previous_command
              published = {}
              new_message = messaging.new_message
              def stamped_message(service, size=None, *, factory=new_message, stamp=now, **kwargs):
                return factory(service, size, **({'logMonoTime': stamp} | kwargs))
              with patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw', return_value=[b'can']), \
                   patch('openpilot.selfdrive.car.card.can_capnp_to_list', return_value=[(now, incoming)]), \
                   patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now), \
                   patch('openpilot.selfdrive.car.card.REPLAY', False), patch.object(selected.sm, 'update'):
                cs, radar = selected.state_update()
              with patch('openpilot.selfdrive.car.card.messaging.new_message', side_effect=stamped_message), \
                   patch.object(selected.pm, 'send', side_effect=lambda service, msg, published=published: published.update({service: msg.as_reader()})):
                selected.state_publish(cs, radar)
              published['pandaStates'] = panda.as_reader()
              ordinary = bool(cs.cruiseState.enabled)
              mask = safety.aol_get_permission_mask()
              native = SafetyState(1, True, now, now + 30_000_000, int(cp.safetyConfigs[0].safetyModel.raw),
                                   cp.safetyConfigs[0].safetyParam, bool(mask & 1), bool(mask & 2),
                                   bool(safety.aol_get_request_mask() & 1), bool(safety.aol_get_request_mask() & 2),
                                   'tesla-panda', sd.aol_session_id, now,
                                   (PandaSlot(0, 'tesla-panda', int(cp.safetyConfigs[0].safetyModel.raw),
                                              cp.safetyConfigs[0].safetyParam, cp.alternativeExperience,
                                              bool(safety.get_controls_allowed()), not bool(safety.safety_config_valid())),))
              receipt = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
              receipt.aolSafetyWire = encode_safety(native)
              selfdriven.update(selfdrive_step(sd, published, receipt, previous_command, cp, safety, now))
              wanted = sd.aol_axis_decision
              safety.aol_set_host_request(int(wanted.desired_lateral) | (int(wanted.desired_longitudinal) << 1))
              mask = safety.aol_get_permission_mask()
              native = SafetyState(1, True, now, now + 30_000_000, int(cp.safetyConfigs[0].safetyModel.raw),
                                   cp.safetyConfigs[0].safetyParam, bool(mask & 1), bool(mask & 2),
                                   wanted.desired_lateral, wanted.desired_longitudinal, 'tesla-panda', sd.aol_session_id, now,
                                   (PandaSlot(0, 'tesla-panda', int(cp.safetyConfigs[0].safetyModel.raw),
                                              cp.safetyConfigs[0].safetyParam, cp.alternativeExperience,
                                              bool(safety.get_controls_allowed()), not bool(safety.safety_config_valid())),))
              feed(controls, now, tick, active=ordinary, enabled=ordinary)
              state = published['carState']
              receipt = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
              receipt.aolSafetyWire = encode_safety(native)
              controls.sm.update_msgs((now + 1_000) / 1e9, [state, selfdriven['aolAxisState'], selfdriven['selfdriveState'], receipt.as_reader(),
                                                         selfdriven['onroadEvents']])
              if tick == 55:
                self.assertIsNotNone(current_native(controls.sm, cp, now_ns=now, axis_session_id=sd.aol_session_id))
                for mutate in ('unmarked', 'duplicate'):
                  invalid = cp.as_reader().as_builder()
                  if mutate == 'unmarked':
                    invalid.alternativeExperience = 0
                  else:
                    invalid.safetyConfigs = [cp.safetyConfigs[0].to_dict(), cp.safetyConfigs[0].to_dict()]
                  self.assertIsNone(current_native(controls.sm, invalid, now_ns=now, axis_session_id=sd.aol_session_id))
              command, lateral_log = controls.state_control()
              controls.publish(command, lateral_log)
              previous_command, previous_receipt = command, receipt
              self.assertEqual(command.latActive, wanted.lateral_active)
              self.assertEqual(command.longActive, wanted.longitudinal_active)
              _, outgoing = ci.apply(command.as_reader(), now)
              for address, payload, bus in outgoing:
                self.assertTrue(safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, payload)), (tick, hex(address)))
              self.assertEqual(safety.safety_fwd_hook(1, 0x3DF), -1)
              self.assertEqual(safety.safety_fwd_hook(0, 0x3DF), 2)
              observed[tick] = (command.latActive, [event.pressed for event in cs.buttonEvents], cs.canValid,
                                {'permission': mask, 'desired': wanted.desired_lateral, 'events': sd.events.names,
                                 'latch': selected.aol_card_intent.allowed_latch, 'axis_ack': wanted.native_acknowledged,
                                 'native_healthy': bool(safety.safety_config_valid()), 'long_active': command.longActive,
                                 'unbuckled': cs.seatbeltUnlatched, 'cancel': command.cruiseControl.cancel,
                                 'ordinary_active': sd.active, 'ordinary_enabled': sd.enabled,
                                 'authority_lost': sd.aol_authority_lost,
                                 'fault_inhibit': selected.aol_card_intent._fault_inhibit,
                                 'clear_reason': selected.aol_card_intent.latch_clear_reason,
                                 'screen_override': selected.aol_card_intent.screen_override,
                                 'transport_failed': selected.aol_transport_feedback.failed})
            self.assertTrue(observed[65][0], ('initial', fault, observed[65]))
            self.assertFalse(observed[95][0], ('pause output', fault, observed[95]))
            if fault != 'permanent':
              self.assertTrue(observed[95][3]['latch'], ('retained intent', fault, observed[95]))
              self.assertTrue(observed[130][0], ('fresh Drive recovery', fault, observed[130]))
            else:
              self.assertFalse(observed[130][0], ('permanent fault must require new intent', fault, observed[130]))
            self.assertEqual(observed[175][0], fault == 'permanent', ('fresh screen gesture after recovery', fault, observed[175]))
            self.assertEqual(observed[200][0], fault != 'permanent', ('second explicit screen toggle', fault, observed[200]))


@pytest.mark.parametrize("platform", (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y))
@pytest.mark.parametrize("marker", ("marker054", "marker489"))
@pytest.mark.parametrize("brake_option", (False, True))
@pytest.mark.parametrize("longitudinal", (False, True))
@pytest.mark.parametrize("fault", ("reverse", "neutral", "temporary", "permanent"))
def test_actual_screen_pause_recovery(platform, marker, brake_option, longitudinal, fault):
  Exercise().run_case(fault, platform, marker, brake_option, longitudinal)
