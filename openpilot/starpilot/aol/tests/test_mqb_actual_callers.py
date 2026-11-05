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
from opendbc.can import CANPacker
from opendbc.can.dbc import DBC as ParsedDBC
from opendbc.car import structs
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.values import CAR
from opendbc.safety.tests.common import make_msg
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import log, messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module
from openpilot.starpilot.aol.tests.stock_lateral_test_helpers import authority_case


@pytest.mark.parametrize('gearbox', ('automatic', 'manual'))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('engage_on_set', (False, True))
@pytest.mark.parametrize('offset', (0,))
@pytest.mark.parametrize('fixed', (False,))
def test_mqb_full_engagement_brake_reverse_drive_actual_callers(gearbox, alpha, offset, fixed, engage_on_set,
                                                               stop_and_resume=False, candidate=CAR.VOLKSWAGEN_GOLF_MK7,
                                                               brake_held_engagement=False):
  with ExitStack() as cleanup:
    cleanup.enter_context(OpenpilotPrefix())
    cleanup.enter_context(mock.patch.dict('os.environ', {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}))
    params = Params()
    params.put_bool('AlwaysOnLateral', True, block=True)
    params.put('MainCruiseButtonControl', 0, block=True)
    params.put('AolBrakePauseSpeedMps', 5., block=True)
    params.put_bool('AlphaLongitudinalEnabled', alpha, block=True)
    fp = {bus: {} for bus in range(8)}
    if gearbox != 'manual':
      fp[offset][0xAD if gearbox == 'automatic' else 0x187] = 8
    fp[offset + 1][0xFD] = 8
    if stop_and_resume or brake_held_engagement:
      fp[offset + 2][0x126] = 8
      fp[offset][0x6B8] = 8
    cp = CarInterface.get_params(candidate, fp, [], alpha, False, False)
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
            envelope.driverMonitoringState.noResponseForceDecel = int(envelope.logMonoTime) >= 11_100_000_000
          actual.append(envelope)
        return update(timestamp, actual)
      return receive
    cleanup.enter_context(mock.patch.object(sd.sm, 'update_msgs', side_effect=dm_samples(sd.sm.update_msgs)))
    cleanup.enter_context(mock.patch.object(controls.sm, 'update_msgs', side_effect=dm_samples(controls.sm.update_msgs)))
    # Explicit ambient producers added since the original shared caller fixture.
    # Keep production SubMaster health/frequency checks; never ignore a required service.
    from openpilot.cereal.services import SERVICE_LIST
    ambient_services = ('alertDebug', 'lateralManeuverPlan', 'laneChangeAssistWire',
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
    native.set_safety_hooks(15, (24 if gearbox == 'manual' else 40 if gearbox == 'direct' else 8) | int(alpha))
    native.set_aol_test_heartbeat(True)
    card.CI.update([])
    dbc = ParsedDBC('vw_mqb')
    packer = CANPacker('vw_mqb')
    emitted, lat_commands, long_commands = [], [], []
    request = 0
    for tick in range(120):
      now = 10_000_000_000 + tick * 10_000_000
      native.set_timer(1_000_000 + tick * 10_000)
      if quiet is not None:
        quiet.set_timer(1_000_000 + tick * 10_000)
        quiet.safety_tick()
        assert quiet.get_current_safety_mode() == int(structs.CarParams.SafetyModel.noOutput)
        assert not quiet.get_controls_allowed()
        assert native.get_current_safety_mode() != quiet.get_current_safety_mode()

      brake = 65 <= tick < 75 or (brake_held_engagement and tick < 29)
      reverse = 85 <= tick < 95
      ordinary = engage_on_set and 21 <= tick < 65
      overrides = {
        'TSK_06': {'TSK_Status': 3 if ordinary and not alpha else 2},
        'LH_EPS_03': {'EPS_HCA_Status': 3, 'EPS_Lenkmoment': 0},
        'Motor_20': {'MO_Fahrpedalrohwert_01': 0},
        'Motor_14': {'MO_Fahrer_bremst': int(brake), 'MO_Kuppl_schalter': 0},
        'ESP_05': {'ESP_Fahrer_bremst': int(brake)},
        'Gateway_73': {'GE_Fahrstufe': 6 if reverse else 8},
        'Gateway_72': {'BCM1_Rueckfahrlicht_Schalter': int(reverse)},
        'Motor_EV_01': {'MO_Waehlpos': 3 if reverse else 5},
        'ESP_19': dict.fromkeys(('ESP_VL_Radgeschw_02', 'ESP_VR_Radgeschw_02', 'ESP_HL_Radgeschw_02', 'ESP_HR_Radgeschw_02'),
                                      0 if (stop_and_resume and 45 <= tick < 56) or (brake_held_engagement and tick < 30) else 72),
        'Airbag_02': {'AB_Gurtschloss_FA': 0 if 105 <= tick < 111 else 3},
        'GRA_ACC_01': {'GRA_Hauptschalter': 1, 'GRA_Tip_Setzen': int(engage_on_set and tick == 20)},
        'ACC_02': {'ACC_Gesetzte_Zeitluecke': 1},
      }
      incoming = []
      for parser in card.CI.can_parsers.values():
        for name in tuple(parser.vl):
          if isinstance(name, str) and name in dbc.name_to_msg:
            if tick < 3 and name in ('TSK_06', 'LH_EPS_03'):
              continue  # Real startup may receive other CAN sources before cruise and EPS.
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
      if brake_held_engagement and 22 <= tick < 29:
        assert state.standstill and state.brakePressed
        assert selfdrive.state == log.SelfdriveState.OpenpilotState.preEnabled
        assert not command.longActive and not (permission & 2)
      if brake_held_engagement and 38 <= tick < 45:
        assert command.longActive and axis.longitudinalActive and permission & 2, "VW_BRAKE_RELEASE_NO_LONG"
      if tick >= 112:
        assert command.driverMonitoringEscalation
      if stop_and_resume and 45 <= tick < 56:
        assert state.standstill and not command.latActive and not axis.desiredLateral, "VW_STOP_LATERAL_REQUEST_NOT_PAUSED"
        assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, "VW_STOP_CONTROLS_MISMATCH"
        assert card.aol_card_intent.allowed_latch
        if alpha and not cp.autoResumeSng:
          assert cp.minEnableSpeed > 0 and not command.longActive
        if alpha and engage_on_set and cp.autoResumeSng:
          assert axis.desiredLongitudinal and axis.longitudinalActive and command.longActive, (
            "VW_STOP_LONG_DROPPED", tick, gearbox, alpha, permission, request, axis.to_dict(), list(sd.events.names))
      if stop_and_resume and 60 <= tick < 65:
        assert command.latActive and card.aol_card_intent.allowed_latch
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
      sent = []
      with (mock.patch.object(card_module.time, 'monotonic_ns', return_value=now),
            mock.patch.object(card_module.time, 'clock_gettime_ns', return_value=now),
            mock.patch.object(card_module, 'REPLAY', False),
            mock.patch.object(card, 'publish_sendcan', side_effect=lambda values, sent=sent, **kw: sent.extend(values))):
        card.controls_update(state, card.sm['carControl'])
      for addr, data, bus in sent:
        assert offset <= bus < offset + 4
        assert native.safety_tx_hook(make_msg(bus - offset, addr, dat=data)), (tick, hex(addr), data.hex(), bus)
      if quiet is not None:
        for addr, data, bus in sent:
          assert not quiet.safety_tx_hook(make_msg(bus - offset, addr, dat=data))
      emitted.extend(sent)
      lat_commands.append(command.latActive)
      long_commands.append(command.longActive)
    assert any(lat_commands[35:60])
    assert any(lat_commands[76:84])  # braking released long, not qualified lateral intent
    assert not any(lat_commands[86:95])
    assert any(lat_commands[100:])
    assert any(lat_commands[106:110])  # Existing qualified unbelted policy, with current native permission.
    assert not any(long_commands[66:])  # no automatic longitudinal restoration
    assert emitted
    if alpha and engage_on_set:
      assert any(long_commands[35:60])
    else:
      assert not any(long_commands)


@pytest.mark.parametrize('gearbox,alpha', [('automatic', False), ('automatic', True), ('manual', False),
                                           ('direct', False), ('direct', True)])
def test_mqb_stop_pauses_only_lateral_and_recovers_without_mismatch(gearbox, alpha):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    gearbox, alpha, 0, False, True, stop_and_resume=True, candidate=CAR.VOLKSWAGEN_GOLF_MK7 if gearbox == "direct" else CAR.SEAT_ATECA_MK1)


@pytest.mark.parametrize('gearbox', ('automatic', 'direct'))
def test_mqb_brake_held_engagement_waits_for_release_actual_callers(gearbox):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    gearbox, True, 0, False, True, brake_held_engagement=True,
    candidate=CAR.SEAT_ATECA_MK1 if gearbox == 'automatic' else CAR.VOLKSWAGEN_GOLF_MK7)
