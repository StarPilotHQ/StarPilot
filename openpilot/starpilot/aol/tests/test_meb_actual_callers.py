"""Actual SD.step/Controls.publish/Card.controls_update + native TX.

Synthetic physical CAN/transport; no native/axis/CarParams permission is fabricated.
The shared actual-caller harness serializes measured native booleans/inventory and
retains the real family events/state machine. This is not physical road proof.
"""
from contextlib import ExitStack
import shutil
import tempfile
from pathlib import Path
from unittest import mock
import pytest
from opendbc.can import CANPacker, CANParser
from opendbc.can.dbc import DBC as ParsedDBC
from opendbc.car import Bus, structs
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.meb_modes import profile_word
from opendbc.car.volkswagen.values import DBC
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
from opendbc.safety.tests.common import make_msg
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module
from openpilot.starpilot.aol.tests.stock_lateral_test_helpers import authority_case


@pytest.mark.parametrize('identity', ('VOLKSWAGEN_ID4_MK1', 'VOLKSWAGEN_ID4_MK2'))
@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('alt_gear', (False, True))
@pytest.mark.parametrize('offset,fixed', ((0, False), (4, True)))
def test_meb_full_engagement_brake_reverse_drive_actual_callers(identity, stock, alt_gear, offset, fixed, *, fault_phase=False):
  with ExitStack() as cleanup:
    cleanup.enter_context(OpenpilotPrefix())
    cleanup.enter_context(mock.patch.dict('os.environ', {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}))
    params = Params()
    params.put_bool('AlwaysOnLateral', True, block=True)
    params.put('MainCruiseButtonControl', 0, block=True)
    params.put('AolBrakePauseSpeedMps', 5., block=True)
    params.put_bool('DisableOpenpilotLongitudinal', stock, block=True)
    fp = {bus: {} for bus in range(8)}
    fp[offset + 1][0x13D] = 32
    fp[offset][0x25D] = 8  # Actual KLR source selects the existing stock Emergency Assist contract.
    if alt_gear:
      fp[offset][0x3DC] = 8
    cp = CarInterface.get_params(identity, fp, [], False, False, False)
    VehicleStartupPreferences.read(params, enabled=True).prepare(cp, fingerprints=fp)
    cp.fingerprintSource = structs.CarParams.FingerprintSource.fixed if fixed else structs.CarParams.FingerprintSource.fw
    # Use the standard source-built loader in fresh pytest processes; private runners may preload it.
    native = libsafety_py.libsafety
    quiet = None
    if offset:
      temporary = tempfile.TemporaryDirectory(prefix='vw-native-pair-')
      cleanup.callback(temporary.cleanup)
      copy = Path(temporary.name) / 'quiet.so'
      shutil.copy2(libsafety_py.libpath, copy)
      quiet = libsafety_py.ffi.dlopen(str(copy))
      cleanup.callback(libsafety_py.ffi.dlclose, quiet)
      quiet.init_tests()
      quiet.set_alternative_experience(32)
      quiet.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
      assert not quiet.get_controls_allowed()
      # The caller harness receives genuine current noOutput state for the leading slot.
      from openpilot.starpilot.aol.wire import PandaSlot
      def measured_slot(index, serial, mode, word, experience, controls_allowed, invalid, lost, faults):
        if index == 0:
          assert mode == quiet.get_current_safety_mode() and word == quiet.get_current_safety_param()
          controls_allowed = bool(quiet.get_controls_allowed())
        return PandaSlot(index, serial, mode, word, experience, controls_allowed, invalid, lost, faults)
      cleanup.enter_context(mock.patch('openpilot.starpilot.aol.tests.test_ioniq_availability.PandaSlot', side_effect=measured_slot))
    case = authority_case()
    cleanup.callback(case.doCleanups)
    # The harness marks its SD CP. Card must independently finalize the original normal factory CP.
    card_cp = structs.CarParams(**cp.to_dict())
    sd, controls, step = case._authority_callers(False, cp=cp, genuine_events=True)
    card = card_module.Car(CI=CarInterface(card_cp), RI=CarInterface.RadarInterface(card_cp))
    assert card.CP.to_dict() == sd.CP.to_dict()
    if offset:
      original_update = sd.sm.update_msgs
      def measured_pandas(timestamp, messages):
        actual = []
        for envelope in messages:
          if envelope.which() == 'pandaStates':
            envelope = envelope.as_builder()
            envelope.pandaStates[0].controlsAllowed = bool(quiet.get_controls_allowed())
          actual.append(envelope)
        return original_update(timestamp, actual)
      cleanup.enter_context(mock.patch.object(sd.sm, 'update_msgs', side_effect=measured_pandas))

    # Real DM messages, not a direct edit of CarControl escalation or native permissions.
    def dm_samples(update):
      next_due = {}
      def receive(timestamp, messages):
        messages = list(messages)
        if timestamp >= 10. and not any(envelope.which() == 'driverMonitoringState' for envelope in messages):
          messages.append(messaging.new_message('driverMonitoringState', valid=True, logMonoTime=int(timestamp * 1e9)).as_reader())
        actual = []
        for envelope in messages:
          service = envelope.which()
          if service in ('driverMonitoringState', 'aolSafetyWire'):
            stamp = int(timestamp * 1e9)
            if stamp < next_due.get(service, 0):
              continue
            next_due[service] = stamp + (50_000_000 if service == 'driverMonitoringState' else 100_000_000)
          if envelope.which() == 'driverMonitoringState':
            envelope = envelope.as_builder()
            envelope.driverMonitoringState.noResponseForceDecel = int(envelope.logMonoTime) >= (12_200_000_000 if fault_phase else 11_100_000_000)
          actual.append(envelope)
        return update(timestamp, actual)
      return receive
    cleanup.enter_context(mock.patch.object(sd.sm, 'update_msgs', side_effect=dm_samples(sd.sm.update_msgs)))
    cleanup.enter_context(mock.patch.object(controls.sm, 'update_msgs', side_effect=dm_samples(controls.sm.update_msgs)))
    # Explicit ambient producers added since the original shared caller fixture.
    # Keep production SubMaster health/frequency checks; never ignore a required service.
    from openpilot.cereal.services import SERVICE_LIST
    ambient_services = ('laneChangeAssistWire',
                        'starpilotLongitudinalPlan', 'slcState', 'gpsLocation')
    original_sd_update = sd.sm.update_msgs
    ambient_due = {}
    def ambient_update(timestamp, messages):
      stamp = int(timestamp * 1e9)
      actual = list(messages)
      present = {message.which() for message in actual}
      for service in ambient_services:
        if service in sd.sm.services and service not in present and stamp >= ambient_due.get(service, 0):
          frequency = SERVICE_LIST[service].frequency
          ambient_due[service] = stamp + (round(1e9 / min(frequency, 100)) if frequency > 0 else 0)
          actual.append(messaging.new_message(service, 0 if service == 'laneChangeAssistWire' else None, valid=True, logMonoTime=stamp).as_reader())
      return original_sd_update(timestamp, actual)
    cleanup.enter_context(mock.patch.object(sd.sm, 'update_msgs', side_effect=ambient_update))
    for prime in range(-110, 0):
      ambient_update((10_000_000_000 + prime * 10_000_000) / 1e9, [])
    card.slc_producer_session = 'authority-card'
    class Publisher(messaging.PubMaster):
      def __init__(self):
        self.messages = {}
      def send(self, s, dat):
        if not isinstance(dat, bytes):
          dat = dat.to_bytes()
        self.messages[s] = messaging.log_from_bytes(dat)
    card.pm = Publisher()
    native = libsafety_py.libsafety
    cleanup.callback(native.init_tests)
    native.init_tests()
    native.set_alternative_experience(32)
    native.set_safety_hooks(34, profile_word(cp))
    native.set_aol_test_heartbeat(True)
    card.CI.update([])
    dbc_name = DBC[identity][Bus.pt]
    dbc = ParsedDBC(dbc_name)
    packer = CANPacker(dbc_name)
    emitted, lat_commands, long_commands = [], [], []
    klr_escalated = []
    fault_trace = []
    request = 0
    dm_tick = 222 if fault_phase else 112
    for tick in range(240 if fault_phase else 120):
      now = 10_000_000_000 + tick * 10_000_000
      native.set_timer(1_000_000 + tick * 10_000)
      if quiet is not None:
        quiet.set_timer(1_000_000 + tick * 10_000)
        quiet.safety_tick()
        assert quiet.get_current_safety_mode() == int(structs.CarParams.SafetyModel.noOutput)
        assert not quiet.get_controls_allowed()
        assert native.get_current_safety_mode() != quiet.get_current_safety_mode()

      brake = 65 <= tick < 75
      gas = 45 <= tick < 50
      reverse = 85 <= tick < 95
      ordinary = 21 <= tick < 65 or (fault_phase and tick >= 200)
      overrides = {
        'Motor_51': {'TSK_Status': 3 if ordinary and stock else 2, 'Accel_Pedal_Pressure': 10 if gas else 0},
        'Motor_54': {'Engine_On': 1},
        'LH_EPS_03': {'EPS_Lenkmoment': 0},
        'QFK_01': {'LatCon_HCA_Status': (7 if fault_phase and 120 <= tick < 125 else 3 if fault_phase and 160 <= tick < 162 else 2), 'Curvature': 0},
        'Motor_14': {'MO_Fahrer_bremst': int(brake)},
        'Getriebe_11': {'GE_Fahrstufe': 6 if reverse else 8},
        'Gateway_73': {'GE_Fahrstufe': 6 if reverse else 8},
        'ESC_51': {name + '_Radgeschw': 72 for name in ('VL', 'VR', 'HL', 'HR')},
        'ESP_21': {'ESP_v_Signal': 72},
        'Airbag_02': {'AB_Gurtschloss_FA': 0 if 105 <= tick < 111 else 3},
        'GRA_ACC_01': {'GRA_Hauptschalter': 1, 'GRA_Tip_Setzen': int(tick == 20 or (fault_phase and tick == 200))},
        'ACC_19': {'ACC_Gesetzte_Zeitluecke': 1, 'ACC_Wunschgeschw_02': 72},
      }
      incoming = []
      for parser in card.CI.can_parsers.values():
        for name in tuple(parser.vl):
          if isinstance(name, str) and name in dbc.name_to_msg:
            fields: dict[str, float] = dict(overrides.get(name, {}))
            if 'COUNTER' in dbc.name_to_msg[name].sigs:
              fields['COUNTER'] = tick % 16
            incoming.append(packer.make_can_msg(name, parser.bus, fields))
      for addr, data, bus in incoming:
        native.safety_rx_hook(make_msg(bus - offset, addr, dat=data))
      native.safety_tick()
      native.aol_set_host_request(request)
      permission = native.aol_get_permission_mask()
      original_message = messaging.new_message
      def clocked_card_message(*args, original_message=original_message, now=now, **kwargs):
        message = original_message(*args, **kwargs)
        message.logMonoTime = now
        return message
      with (mock.patch.object(card_module.messaging, 'new_message', side_effect=clocked_card_message),
            mock.patch.object(card_module.messaging, 'drain_sock_raw', return_value=[b'fixture']),
            mock.patch.object(card_module, 'can_capnp_to_list', return_value=[(now, incoming)]),
            mock.patch.object(card_module.time, 'monotonic_ns', return_value=now),
            mock.patch.object(card_module.time, 'clock_gettime_ns', return_value=now),
            mock.patch.object(card_module, 'REPLAY', False), mock.patch.object(card.sm, 'update'),
            mock.patch.object(card.RI, 'update', return_value=None)):
        state, radar = card.state_update()
        card.state_publish(state, radar)
      if tick >= 15:
        assert state.canValid and not state.canTimeout
      intent = card.pm.messages['aolIntentWire'].as_builder()
      axis, selfdrive, command = step(tick, observed_state=state, intent_message=intent,
        gas=state.gasPressed, steering=state.steeringPressed, brake=state.brakePressed,
        allowed=bool(permission & 1), long_allowed=bool(permission & 2),
        native_request_lat=bool(request & 1), native_request_long=bool(request & 2),
        panda_controls_allowed=native.get_controls_allowed())
      if tick == 60 and not stock:
        assert native.get_controls_allowed() and axis.desiredLongitudinal and command.longActive
      if tick >= dm_tick:
        assert command.driverMonitoringEscalation
      request = int(axis.desiredLateral) | (int(axis.desiredLongitudinal) << 1)
      # Feed the exact serialized Controls publication into the actual Card caller.
      frames = [controls.pm.messages['carControl'], sd.pm.messages['aolAxisState'],
                sd.pm.messages['onroadEvents'], sd.pm.messages['selfdriveState']]
      for name in ('aolSafetyWire', 'pandaStates', 'deviceState'):
        envelope = messaging.new_message(name, len(sd.sm[name]) if name in ('pandaStates', 'aolSafetyWire') else None,
          valid=sd.sm.valid[name], logMonoTime=sd.sm.logMonoTime[name])
        setattr(envelope, name, sd.sm[name])
        frames.append(envelope.as_reader())
      card.sm.update_msgs(now / 1e9, frames)
      assert card.sm['carControl'].driverMonitoringEscalation == command.driverMonitoringEscalation
      sent = []
      with (mock.patch.object(card_module.time, 'monotonic_ns', return_value=now),
            mock.patch.object(card_module.time, 'clock_gettime_ns', return_value=now),
            mock.patch.object(card_module, 'REPLAY', False),
            mock.patch.object(card, 'publish_sendcan', side_effect=lambda values, sent=sent, **kw: sent.extend(values))):
        card.controls_update(state, card.sm['carControl'])
      for addr, data, bus in sent:
        assert offset <= bus < offset + 4
        assert native.safety_tx_hook(make_msg(bus - offset, addr, dat=data)), (tick, hex(addr), data.hex(), bus)
      if tick >= dm_tick and any(addr == 0x25D and bus == offset + 2 for addr, _, bus in sent):
        # Decode the current output payload independently of this synthetic RX stream's counters.
        klr_observer = CANParser(dbc_name, [('KLR_01', 0)], offset + 2)
        klr_observer.update([(now, sent)])
        klr_escalated.append(klr_observer.vl['KLR_01']['KLR_Touchauswertung'])
      if quiet is not None:
        for addr, data, bus in sent:
          assert not quiet.safety_tx_hook(make_msg(bus - offset, addr, dat=data))
      emitted.extend(sent)
      if fault_phase and tick in (119, 120, 124, 125, 129, 130, 132, 139, 140, 145, 150, 159, 160, 169, 170, 175, 180):
        fault_trace.append((tick, permission, card.aol_card_intent.allowed_latch, state.steerFaultTemporary,
                            state.steerFaultPermanent, axis.desiredLateral, axis.lateralActive,
                            axis.nativeAcknowledged, command.latActive, str(axis.faultReason),
                            card.aol_card_intent._fault_inhibit, card.aol_card_intent._native_rejection_ns))
      lat_commands.append(command.latActive)
      long_commands.append(command.longActive)
    assert any(lat_commands[35:60])
    assert any(lat_commands[76:84])  # braking released long, not qualified lateral intent
    assert not any(lat_commands[86:95])
    assert any(lat_commands[100:])
    assert any(lat_commands[106:110])  # Existing qualified unbelted policy, with current native permission.
    assert not any(long_commands[76:200 if fault_phase else None])  # Brake disables long; it never restores automatically.
    if fault_phase:
      assert not any(lat_commands[121:125])
      if not any(lat_commands[145:155]):
        print('MEB_FAULT_TRACE', fault_trace)
      assert any(lat_commands[145:155]), fault_trace  # Temporary EPS rejection retains intent.
      assert not any(lat_commands[161:200])  # A recovered permanent fault cannot restore armed intent.
      assert any(lat_commands[212:220])  # Fresh ordinary driver engagement rearms it.
      if not stock:
        assert any(long_commands[212:220])
    assert emitted
    assert klr_escalated and all(value == 0 for value in klr_escalated)
    if not stock:
      assert any(long_commands[35:60])
    else:
      assert not any(long_commands)


@pytest.mark.parametrize('identity', ('VOLKSWAGEN_ID4_MK1', 'VOLKSWAGEN_ID4_MK2'))
@pytest.mark.parametrize('stock', (False, True))
def test_meb_actual_eps_rejection_permanent_fault_and_fresh_ordinary_rearm(identity, stock):
  test_meb_full_engagement_brake_reverse_drive_actual_callers(identity, stock, False, 0, False, fault_phase=True)
