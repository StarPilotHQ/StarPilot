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
                                                               brake_held_engagement=False, creep_start=False, early_brake=False,
                                                               parked_boot=False, cruise_cancel=False, main_cycle=False,
                                                               pq_eps_pause=False, pq_real_fault=False, native_phase=0, delayed_heartbeat=False,
                                                               manual_speed_withdrawal=False, cold_creep=False, cancel_override=False, reverse_boot=False,
                                                               reverse_unbraked=False, cancel_axis_delay=0,
                                                               cancel_steering=True, cancel_axis_expiry=False, startup_event_delay=0,
                                                               startup_delivery_delay=0, startup_delivery_services=(), receipt_sink=None,
                                                               route2c_start=False, startup_block_until=0, startup_expect_expiry=False,
                                                               startup_probe_denials=False, startup_stall=None, startup_stall_mode='frozen',
                                                               startup_floor_dip=False, cancel_delivery_delay=0, brake_delivery_delay=0, rushed_start=False):
  pq = candidate == CAR.VOLKSWAGEN_SHARAN_MK2
  with ExitStack() as cleanup:
    cleanup.enter_context(OpenpilotPrefix())
    cleanup.enter_context(mock.patch.dict('os.environ', {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}))
    params = Params()
    params.put_bool('AlwaysOnLateral', True, block=True)
    params.put('MainCruiseButtonControl', 0, block=True)
    params.put('AolBrakePauseSpeedMps', 0. if route2c_start else 5., block=True)
    params.put_bool('AlphaLongitudinalEnabled', alpha, block=True)
    fp = {bus: {} for bus in range(8)}
    if gearbox != 'manual':
      fp[offset][0x440 if pq else 0xAD if gearbox == 'automatic' else 0x187] = 8
    fp[offset + 1][0x1A0 if pq else 0xFD] = 8
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
    sd.aol_settings = card.aol_settings
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
            if stamp < next_due.get(service, 10_000_000_000 + native_phase * 10_000_000 if service == 'aolSafetyWire' else 0):
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
    native.set_safety_hooks(21 if pq else 15, (24 if gearbox == 'manual' else 40 if gearbox == 'direct' else 8) | int(alpha))
    native.set_aol_test_heartbeat(not parked_boot)
    card.CI.update([])
    dbc = ParsedDBC('vw_pq' if pq else 'vw_mqb')
    packer = CANPacker('vw_pq' if pq else 'vw_mqb')
    emitted, lat_commands, long_commands = [], [], []
    request = 0
    startup_vetoes_checked = False
    route2c_denials_checked = False
    floor_vetoes_checked = False
    reverse_event_vetoes_checked = False
    cancel_join_vetoes_checked = False
    for tick in range(120):
      now = 10_000_000_000 + tick * 10_000_000
      native.set_timer(1_000_000 + tick * 10_000)
      if (parked_boot or main_cycle or creep_start and tick >= 12) and (not delayed_heartbeat or tick % 10 == 1):
        native.set_aol_test_heartbeat(bool((card.aol_card_intent.allowed_latch or request) and tick >= startup_block_until))
      if quiet is not None:
        quiet.set_timer(1_000_000 + tick * 10_000)
        quiet.safety_tick()
        assert quiet.get_current_safety_mode() == int(structs.CarParams.SafetyModel.noOutput)
        assert not quiet.get_controls_allowed()
        assert native.get_current_safety_mode() != quiet.get_current_safety_mode()

      brake = (((60 if early_brake else 65) <= tick < 75 and not startup_floor_dip) or
               (brake_held_engagement and tick < 29) or (reverse_boot and not reverse_unbraked and tick < 40))
      reverse = 85 <= tick < 95 or (reverse_boot and tick < 35)
      if route2c_start:
        reverse = 20 <= tick < 35 or 85 <= tick < 95
      ordinary = engage_on_set and 21 <= tick < (55 if cruise_cancel else 65)
      overrides = {
        'TSK_06': {'TSK_Status': 0 if main_cycle and 45 <= tick < 50 else 3 if ordinary and not alpha else 2},
        'LH_EPS_03': {'EPS_HCA_Status': 3, 'EPS_Lenkmoment':
          100 if route2c_start and 30 <= tick < 40 or (early_brake or cancel_override and cancel_steering) and 52 <= tick <= 61 else 0},
        'Motor_20': {'MO_Fahrpedalrohwert_01': 20 if cancel_override and 48 <= tick < 65 else 0},
        'Motor_14': {'MO_Fahrer_bremst': int(brake), 'MO_Kuppl_schalter': 0},
        'ESP_05': {'ESP_Fahrer_bremst': int(brake)},
        'Gateway_73': {'GE_Fahrstufe': 6 if reverse else 8},
        'Gateway_72': {'BCM1_Rueckfahrlicht_Schalter': int(reverse)},
        'Motor_EV_01': {'MO_Waehlpos': 3 if reverse else 5},
        'ESP_19': dict.fromkeys(('ESP_VL_Radgeschw_02', 'ESP_VR_Radgeschw_02', 'ESP_HL_Radgeschw_02', 'ESP_HR_Radgeschw_02'),
                                      (72 if tick < 12 else 0 if tick < 20 else 1 if tick < 24 else 2 if tick < 46 else 72) if creep_start else
                                      0 if (stop_and_resume and 45 <= tick < 56) or (brake_held_engagement and tick < 30) else
                                      0 if parked_boot and tick < 30 else 2 if cold_creep else 16 if manual_speed_withdrawal and 45 <= tick < 65 else 72),
        'Airbag_02': {'AB_Gurtschloss_FA': 0 if 105 <= tick < 111 else 3},
        'GRA_ACC_01': {'GRA_Abbrechen': int(cruise_cancel and tick == 55), 'GRA_Hauptschalter': int(not main_cycle or not 45 <= tick < 50),
                       'GRA_Tip_Setzen': int(engage_on_set and (tick in (20, 50) if creep_start else tick == 20))},
        'ACC_02': {'ACC_Gesetzte_Zeitluecke': 1},
      }
      if startup_floor_dip and 39 <= tick < 48:
        overrides['ESP_19'] = dict.fromkeys(('ESP_VL_Radgeschw_02', 'ESP_VR_Radgeschw_02',
                                           'ESP_HL_Radgeschw_02', 'ESP_HR_Radgeschw_02'), 0)
      if pq:
        overrides = {
          'Motor_2': {'MO2_Sta_GRA': int(ordinary and not alpha), 'MO2_BLS': int(brake)},
          'Motor_3': {'MO3_Pedalwert': 0},
          'Motor_5': {'MO5_GRA_Hauptsch': int(not main_cycle or not 45 <= tick < 50)},
          'Lenkhilfe_2': {'LH2_Sta_HCA': 2 if pq_real_fault and tick == 60 else 4 if pq_eps_pause and 45 <= tick < 50 else 3},
          'Lenkhilfe_3': {'LH3_LM': 100 if (early_brake or cancel_override and cancel_steering) and 52 <= tick <= 61 else 0},
          'Getriebe_1': {'GE1_Wahl_Pos': 7 if reverse else 5},
          'Bremse_1': {'BR1_Rad_kmh': 0 if (parked_boot or brake_held_engagement) and tick < 30 else 72},
          'Airbag_1': {'Gurtschalter_Fahrer': int(not 105 <= tick < 111)},
          'GRA_Neu': {'GRA_Abbrechen': int(cruise_cancel and tick == 55),
                      'GRA_Hauptschalt': int(not main_cycle or not 45 <= tick < 50),
                      'GRA_Neu_Setzen': int(engage_on_set and tick == 20)},
        }
      incoming = []
      for parser in card.CI.can_parsers.values():
        for name in tuple(parser.vl):
          if isinstance(name, str) and name in dbc.name_to_msg:
            if tick < 3 and name in (('Motor_5', 'Lenkhilfe_2') if pq else ('TSK_06', 'LH_EPS_03')):
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
      if early_brake and tick == 60:
        incoming = [(address, data, bus) for address, data, bus in incoming if address not in ((0x288,) if pq else (0x106, 0x3BE))]
        if pq_real_fault:
          incoming = [(address, data, bus) for address, data, bus in incoming if address != 0x3D2]
        assert not native.get_controls_allowed() and not permission & 2
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
        stalled = startup_stall is not None and startup_stall[0] <= tick < startup_stall[1]
        if not stalled:
          state, radar = card.state_update()
          card.state_publish(state, radar)
        elif startup_stall_mode == 'publish-only':
          # Freeze owner updates but serialize using the actual late publication clock.
          card.state_publish(state, radar)
      if tick >= 15 and startup_stall is None:
        assert state.canValid and not state.canTimeout
      if (parked_boot and 15 <= tick < 30) or (reverse_boot and 15 <= tick < (35 if reverse_unbraked else 40)):
        assert not card.aol_card_intent.allowed_latch, 'VW_PARKED_START_CONSUMED_MAIN_ARM'
      if delayed_heartbeat and not startup_vetoes_checked:
        from copy import copy
        from dataclasses import replace
        from openpilot.starpilot.car.volkswagen.aol import awaiting_initial_request
        from openpilot.starpilot.aol.runtime import current_native
        measured = current_native(card.sm, card.CP, now_ns=now, axis_session_id=card.aol_axis_session())
        feedback, owner = card.aol_transport_feedback, card.aol_card_intent
        def waiting(cs=state, proof=measured, tracker=feedback, request_owner=owner, stamp=now, sources=card.sm):
          return awaiting_initial_request(card.CP, cs, sources, proof, tracker, request_owner,
            now_ns=stamp, producer=card.slc_producer_session)
        if waiting():
          assert feedback is not None and owner is not None
          anchor = owner.startup_arm_ns
          for field, value in (('canValid', False), ('canTimeout', True), ('steerFaultPermanent', True),
                               ('steerFaultTemporary', True), ('accFaulted', True), ('vehicleSensorsInvalid', True),
                               ('steeringDisengage', True), ('brakePressed', True), ('doorOpen', True)):
            bad = structs.CarState(**state.to_dict())
            setattr(bad, field, value)
            assert not waiting(cs=bad), field
          bad = structs.CarState(**state.to_dict())
          bad.cruiseState.available = False
          assert not waiting(cs=bad)
          for field, value in (('failed', True), ('paused', True), ('pending_event', now),
                               ('sequence', card.sm['aolAxisState'].sequence + 1), ('cp', ('foreign',))):
            assert feedback is not None
            bad_feedback = copy(feedback)
            setattr(bad_feedback, field, value)
            assert not waiting(tracker=bad_feedback), field
          bad_feedback = copy(feedback)
          signature = feedback.signature
          assert signature is not None
          bad_feedback.signature = (*signature[:15], signature[15] + 1)
          assert not waiting(tracker=bad_feedback)
          for field, value in (('allowed_latch', False), ('pause_lateral', True), ('startup_arm_ns', now - 200_000_001)):
            bad_owner = copy(owner)
            setattr(bad_owner, field, value)
            assert not waiting(request_owner=bad_owner), field
          for changes in ({'enabled': False}, {'main_action': 9}, {'lkas_action': 9}):
            bad_owner = copy(owner)
            bad_owner.settings = replace(owner.settings, **changes)
            assert not waiting(request_owner=bad_owner)
          assert not waiting(proof=None)
          assert not waiting(proof=replace(measured, validUntilMonoTime=now - 1))
          if any(event.name == log.OnroadEvent.EventName.reverseGear for event in card.sm['onroadEvents']):
            from openpilot.selfdrive.selfdrived.events import Events
            for extra in (log.OnroadEvent.EventName.controlsMismatch, log.OnroadEvent.EventName.commIssue,
                          log.OnroadEvent.EventName.buttonCancel):
              bad_sources = copy(card.sm)
              bad_sources.data = dict(card.sm.data)
              events = Events()
              events.add(extra)
              bad_sources.data['onroadEvents'] = [*card.sm['onroadEvents'], *events.to_msg()]
              assert not waiting(sources=bad_sources), extra
            bad_sources = copy(card.sm)
            bad_sources.logMonoTime = dict(card.sm.logMonoTime)
            bad_sources.logMonoTime['onroadEvents'] = anchor
            assert not waiting(sources=bad_sources)
            bad_sources = copy(card.sm)
            bad_sources.data = dict(card.sm.data)
            bad_axis = card.sm['aolAxisState'].as_builder()
            bad_axis.faultEventMonoTime = card.sm.logMonoTime['onroadEvents']
            bad_sources.data['aolAxisState'] = bad_axis.as_reader()
            assert not waiting(sources=bad_sources)
            reverse_event_vetoes_checked = True
          assert owner.startup_arm_ns == anchor
          startup_vetoes_checked = True
      if startup_floor_dip and getattr(card.aol_card_intent, 'startup_floor_pending', False) and not floor_vetoes_checked:
        assert_floor_recovery_vetoes(card, state, now)
        floor_vetoes_checked = True
      if startup_probe_denials and not route2c_denials_checked and card.aol_card_intent.startup_ready:
        assert_readiness_denials(card, state, now)
        route2c_denials_checked = True
      intent = card.pm.messages['aolIntentWire'].as_builder()
      axis, selfdrive, command = step(tick, observed_state=state, intent_message=intent,
        gas=state.gasPressed, steering=state.steeringPressed, brake=state.brakePressed,
        allowed=bool(permission & 1), long_allowed=bool(permission & 2),
        native_request_lat=bool(request & 1), native_request_long=bool(request & 2),
        panda_controls_allowed=native.get_controls_allowed(),
        car_output_valid=not rushed_start or not 28 <= tick < 42,
        **({'car_state_message': card.pm.messages['carState'].as_builder(),
            'authority_message': card.pm.messages['starpilotCarState'].as_builder() if 'starpilotCarState' in card.pm.messages else None,
            'companion': 'starpilotCarState' in card.pm.messages,
            'timeout': stalled and startup_stall_mode == 'frozen-timeout'} if startup_stall is not None else {}))
      if rushed_start and 30 <= tick < 42:
        assert not card.aol_card_intent.allowed_latch, 'VW_STARTUP_ARMED_BEFORE_HOST_READY'
        assert not command.latActive and not command.longActive
      if startup_floor_dip and state.vEgo <= cp.minSteerSpeed:
        assert not command.latActive and not command.longActive
      if startup_floor_dip and command.latActive and not card.aol_card_intent.startup_granted:
        assert now <= card.aol_card_intent.startup_arm_ns + 200_000_000
      if brake_delivery_delay and 66 <= tick < 75:
        assert card.aol_card_intent.allowed_latch
        assert command.latActive and not command.longActive
      if early_brake and tick == 60:
        assert not state.brakePressed and state.steeringPressed
        if pq_real_fault:
          assert permission == 0 and log.OnroadEvent.EventName.controlsMismatch in sd.events.names
          assert not command.latActive and not command.longActive
          return
        assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names, 'VW_EARLY_BRAKE_STEERING_MISMATCH'
        assert not command.longActive
        assert card.aol_card_intent.allowed_latch
      if brake_held_engagement and 22 <= tick < 29:
        assert state.standstill and state.brakePressed
        assert selfdrive.state == log.SelfdriveState.OpenpilotState.preEnabled
        assert not command.longActive and not (permission & 2)
      if brake_held_engagement and 38 <= tick < 45:
        assert command.longActive and axis.longitudinalActive and permission & 2, "VW_BRAKE_RELEASE_NO_LONG"
      if parked_boot and tick < 30:
        assert not command.latActive and not command.longActive
        assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
      if cancel_axis_delay and not cancel_axis_expiry and not cancel_join_vetoes_checked and tick == 57:
        from copy import copy
        from dataclasses import replace
        from openpilot.starpilot.aol.wire import encode_safety
        feedback = card.aol_transport_feedback
        def retained(sources=card.sm, cs=state, latched=True, feedback=feedback, now=now):
          return feedback._native_long_withdrawal_lateral_current(sources, card.CP, cs, sources['aolAxisState'], now,
            latched=latched, producer=card.slc_producer_session)
        proof = retained()
        if proof is not None:
          assert not retained(latched=False)
          for field, value in (('canValid', False), ('canTimeout', True), ('steerFaultTemporary', True),
                               ('steerFaultPermanent', True), ('accFaulted', True), ('steeringDisengage', True)):
            bad_state = structs.CarState(**state.to_dict())
            setattr(bad_state, field, value)
            assert retained(cs=bad_state) is None, field
          bad_sources = copy(card.sm)
          bad_sources.data = dict(card.sm.data)
          bad_sources.data['aolSafetyWire'] = encode_safety(replace(proof, lateralAllowed=False))
          assert retained(sources=bad_sources) is None
          if card.sm['aolAxisState'].desiredLongitudinal and not state.buttonEvents:
            bad_sources = copy(card.sm)
            bad_sources.logMonoTime = dict(card.sm.logMonoTime)
            bad_sources.logMonoTime['onroadEvents'] = now - 30_000_001
            assert feedback is not None
            bad_feedback = copy(feedback)
            bad_feedback.longitudinal_cancel_ns = 0
            assert retained(sources=bad_sources, feedback=bad_feedback) is None
            if feedback.longitudinal_cancel_ns:
              assert retained(sources=bad_sources) is not None
          cancel_join_vetoes_checked = True
      if cancel_axis_expiry and tick == 58:
        assert not card.aol_card_intent.allowed_latch
        assert not command.latActive and not command.longActive
        return
      if cruise_cancel and 58 <= tick < 65:
        assert card.aol_card_intent.allowed_latch, ('VW_CANCEL_CLEARED_LATERAL_INTENT', tick, native_phase,
          card.aol_card_intent.latch_clear_reason, card.aol_transport_feedback.failed,
          card.aol_transport_feedback.signature, axis.to_dict(), list(sd.events.names))
        assert command.latActive and permission & 1, 'VW_CANCEL_WITHDREW_LATERAL_PERMISSION'
        assert not command.longActive and not permission & 2, 'VW_CANCEL_RESTORED_LONG'
      if main_cycle and 62 <= tick < 65:
        assert card.aol_card_intent.allowed_latch
        assert command.latActive, 'VW_MAIN_ON_REQUIRES_SET_AFTER_HEARTBEAT_WITHDRAWAL'
        assert not command.longActive
      if pq_eps_pause and 46 <= tick < 50:
        assert state.steerFaultTemporary and not command.latActive
        assert card.aol_card_intent.allowed_latch
      if pq_eps_pause and 51 <= tick < 60:
        assert not command.latActive  # Await the next genuine 10 Hz native acknowledgment.
      if pq_eps_pause and 62 <= tick < 65:
        assert command.latActive and permission & 1, (tick, permission, request, state.to_dict(), axis.to_dict(),
          list(sd.events.names), card.aol_card_intent.allowed_latch, card.aol_card_intent._fault_inhibit)
        assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
      if manual_speed_withdrawal and 50 <= tick < 65:
        assert card.aol_card_intent.allowed_latch, 'VW_MANUAL_CRUISE_MINIMUM_CLEARED_AOL'
        assert command.latActive and not command.longActive
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
      if startup_delivery_delay and 35 <= tick < 35 + startup_delivery_delay:
        frames = [message for message in frames if message.which() not in startup_delivery_services]
      if startup_event_delay and 35 <= tick < 35 + startup_event_delay:
        frames = [message for message in frames if message.which() != 'onroadEvents']
      if brake_delivery_delay and 65 <= tick < 65 + brake_delivery_delay - 1:
        frames = [message for message in frames if message.which() != 'aolAxisState']
      if cancel_delivery_delay and 55 <= tick < 55 + cancel_delivery_delay:
        frames = [message for message in frames if message.which() not in ('aolAxisState', 'onroadEvents')]
      if cancel_axis_delay and 55 <= tick < 55 + cancel_axis_delay:
        frames = [message for message in frames if message.which() != 'aolAxisState']
      card.sm.update_msgs(now / 1e9, frames)
      sent = []
      with (mock.patch.object(card_module.time, 'monotonic_ns', return_value=now),
            mock.patch.object(card_module.time, 'clock_gettime_ns', return_value=now),
            mock.patch.object(card_module, 'REPLAY', False),
            mock.patch.object(card, 'publish_sendcan', side_effect=lambda values, sent=sent, **kw: sent.extend(values))):
        if not stalled:
          card.controls_update(state, card.sm['carControl'])
      for addr, data, bus in sent:
        assert offset <= bus < offset + 4
        if addr == 0x397 and not pq:
          # The HUD runs after the steering frame on this tick.
          hca = next(data for address, data, _ in sent if address == 0x126)
          assert command.latActive and (int.from_bytes(hca, 'little') >> 16) & 511
        accepted = native.safety_tx_hook(make_msg(bus - offset, addr, dat=data))
        if cancel_override and alpha and state.gasPressed and addr in (0x122, 0x12E):
          raw_accel = ((data[4] & 7) << 8 | data[3]) if addr == 0x122 else ((data[7] << 3) | (data[6] >> 5))
          accel = raw_accel * 5 - 7220
          assert accepted == (accel == 3010)
        elif pq and main_cycle and alpha and 45 <= tick < 50 and addr == 0x368:
          # Physical MAIN withdrawal precedes the next 10 Hz host acknowledgment.
          assert not accepted and not native.get_controls_allowed() and not permission & 2
        else:
          assert accepted, (tick, hex(addr), data.hex(), bus)
      if quiet is not None:
        for addr, data, bus in sent:
          assert not quiet.safety_tx_hook(make_msg(bus - offset, addr, dat=data))
      if receipt_sink is not None:
        native_wire = card.sm['aolSafetyWire']
        receipt_sink.append({
          'tick': tick, 'now_ns': now, 'permission_mask': int(permission), 'host_request': request,
          'card_stalled': stalled, 'published_car_state_ns': int(card.pm.messages['carState'].logMonoTime),
          'published_authority_ns': int(card.pm.messages['starpilotCarState'].logMonoTime) if 'starpilotCarState' in card.pm.messages else None,
          'native_controls_allowed': bool(native.get_controls_allowed()),
          'card_intent': {'allowed': card.aol_card_intent.allowed_latch,
                          'startup_arm_ns': card.aol_card_intent.startup_arm_ns,
                          'clear_reason': card.aol_card_intent.latch_clear_reason,
                          'fault_inhibit': card.aol_card_intent._fault_inhibit,
                          'deferred': getattr(card.aol_card_intent, 'startup_deferred', False),
                          'release_ns': getattr(card.aol_card_intent, 'startup_release_ns', 0),
                          'granted': getattr(card.aol_card_intent, 'startup_granted', False),
                          'aborted': getattr(card.aol_card_intent, 'startup_aborted', False)},
          'card_received_axis': card.sm['aolAxisState'].to_dict(),
          'card_received_events': [event.to_dict() for event in card.sm['onroadEvents']],
          'card_source_stamps': {name: int(card.sm.logMonoTime[name]) for name in
                                ('aolAxisState', 'onroadEvents', 'aolSafetyWire', 'pandaStates')},
          'native_wire_hex': bytes(native_wire).hex(),
          'intent_wire_hex': bytes(intent.aolIntentWire).hex(),
          'feedback_session': card.aol_transport_feedback.session,
          'feedback_failed': card.aol_transport_feedback.failed,
          'feedback_signature': card.aol_transport_feedback.signature,
          'pending_native_since_ns': getattr(card, '_angle_aol_pending_since_ns', 0),
          'physical_state': state.to_dict(), 'sd_axis': axis.to_dict(),
          'control': command.to_dict(),
          'sendcan': [{'address': addr, 'data': data.hex(), 'bus': bus} for addr, data, bus in sent],
        })
        # This asserts command authority against the genuine current native result.
        assert not command.latActive or permission & 1
        for addr, data, _bus in sent:
          if addr == 0x126:
            hca = int.from_bytes(data, 'little')
            if hca >> 30 & 1 or hca >> 16 & 511:
              assert permission & 1, ('VW_STARTUP_OUTPUT_BEFORE_NATIVE_GRANT', tick)
      emitted.extend(sent)
      lat_commands.append(command.latActive)
      long_commands.append(command.longActive)
    if startup_stall is not None:
      assert card.aol_card_intent.startup_arm_ns == 10_350_000_000
      assert card.aol_card_intent.startup_aborted
      assert not card.aol_card_intent.allowed_latch
      return
    if delayed_heartbeat and not reverse_boot and alpha and gearbox == 'manual' and native_phase == 0:
      assert startup_vetoes_checked
    if startup_event_delay and native_phase == 6:
      assert reverse_event_vetoes_checked
    if cancel_axis_delay == 2 and native_phase == 5 and alpha:
      assert cancel_join_vetoes_checked
    if startup_probe_denials:
      assert route2c_denials_checked
    if creep_start:
      assert any(lat_commands[35:45]), 'AOL must request and obtain native steering below the manual long-enable speed'
      assert not any(long_commands[:46]), 'Creep AOL must not enable longitudinal below its independent minimum'
    if startup_expect_expiry:
      assert not any(lat_commands[35:]) and not any(long_commands)
      assert card.aol_card_intent.startup_aborted and not card.aol_card_intent.allowed_latch
      return
    if rushed_start:
      assert any(lat_commands[43:75])
    elif reverse_boot and not startup_floor_dip and card.aol_card_intent.startup_drive_identity is not None:
      arm_tick = (card.aol_card_intent.startup_arm_ns - 10_000_000_000) // 10_000_000
      first_active = next((tick for tick, active in enumerate(lat_commands) if active), None)
      assert first_active is not None and max(45, arm_tick) <= first_active <= arm_tick + 20
    else:
      assert any(lat_commands[60:74] if startup_floor_dip else lat_commands[45:60] if reverse_boot else lat_commands[35:60])
    assert any(lat_commands[76:84])  # braking released long, not qualified lateral intent
    assert not any(lat_commands[86:95])
    assert any(lat_commands[100:])
    assert any(lat_commands[106:110])  # Existing qualified unbelted policy, with current native permission.
    assert not any(long_commands[66:])  # no automatic longitudinal restoration
    assert emitted
    if alpha and engage_on_set:
      assert any(long_commands[50:65] if creep_start else long_commands[35:60])
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


@pytest.mark.parametrize('offset,fixed', [(0, False), (0, True), (4, False), (4, True)])
def test_mqb_manual_alpha_stopped_creep_preserves_native_arm_and_fresh_speed_engagement(offset, fixed):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual', True, offset, fixed, True, creep_start=True)


@pytest.mark.parametrize('veto', ('none', 'can', 'timeout', 'temporary', 'permanent', 'main', 'reverse', 'latch', 'pause', 'automatic', 'stock'))
def test_manual_cruise_minimum_exception_retains_physical_and_intent_vetoes(veto):
  from types import SimpleNamespace
  from openpilot.starpilot.car.volkswagen.aol import below_engage_lateral_allowed
  from openpilot.starpilot.aol.vehicle import policy_for
  fp = {bus: {} for bus in range(8)}
  fp[1][0xFD] = 8
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, fp, [], True, False, False)
  policy = policy_for(cp)
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  state = structs.CarState(canValid=True, gearShifter=structs.CarState.GearShifter.drive)
  state.cruiseState.available = True
  intent = SimpleNamespace(allowedLatch=True, pauseLateral=False)
  if veto in ('can', 'timeout', 'temporary', 'permanent'):
    setattr(state, {'can': 'canValid', 'timeout': 'canTimeout', 'temporary': 'steerFaultTemporary',
                    'permanent': 'steerFaultPermanent'}[veto], veto != 'can')
  elif veto == 'main':
    state.cruiseState.available = False
  elif veto == 'reverse':
    state.gearShifter = structs.CarState.GearShifter.reverse
  elif veto == 'latch':
    intent.allowedLatch = False
  elif veto == 'pause':
    intent.pauseLateral = True
  elif veto == 'automatic':
    cp.transmissionType = structs.CarParams.TransmissionType.automatic
    cp.safetyConfigs[-1].safetyParam = 9
  elif veto == 'stock':
    cp.openpilotLongitudinalControl, cp.pcmCruise = False, True
    cp.safetyConfigs[-1].safetyParam = 24
  assert below_engage_lateral_allowed(cp, state, intent) is (veto == 'none')


@pytest.mark.parametrize('gearbox', ('automatic', 'manual', 'direct'))
def test_native_brake_precedes_parsed_pedal_during_driver_steering(gearbox):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    gearbox, True, 0, False, True, early_brake=True,
    candidate=CAR.SEAT_ATECA_MK1 if gearbox == 'automatic' else CAR.VOLKSWAGEN_GOLF_MK7)


@pytest.mark.parametrize('gearbox', ('automatic', 'manual', 'direct'))
@pytest.mark.parametrize('alpha', (False, True))
def test_mqb_parked_main_start_waits_for_steering_speed_without_set(gearbox, alpha):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(gearbox, alpha, 0, False, False, parked_boot=True)


@pytest.mark.parametrize('gearbox', ('automatic', 'manual', 'direct'))
@pytest.mark.parametrize('alpha', (False, True))
def test_mqb_default_cancel_withdraws_only_cruise_actual_callers(gearbox, alpha):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(gearbox, alpha, 0, False, True, cruise_cancel=True)


@pytest.mark.parametrize('gearbox', ('automatic', 'manual', 'direct'))
@pytest.mark.parametrize('alpha', (False, True))
def test_mqb_main_cycle_without_set_recovers_only_lateral_actual_callers(gearbox, alpha):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(gearbox, alpha, 0, False, False, main_cycle=True)


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('offset,fixed', ((0,False), (4,True)))
def test_pq_factory_parser_card_control_native_actual_callers(alpha, offset, fixed):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'automatic', alpha, offset, fixed, True, candidate=CAR.VOLKSWAGEN_SHARAN_MK2)


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('scenario', ('parked_boot', 'cruise_cancel', 'main_cycle'))
def test_pq_main_and_cancel_actual_callers(alpha, scenario):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'automatic', alpha, 0, False, scenario != 'parked_boot', candidate=CAR.VOLKSWAGEN_SHARAN_MK2, **{scenario: True})


@pytest.mark.parametrize('alpha', (False, True))
def test_pq_aol_eps_pause_and_recovery_actual_callers(alpha):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'automatic', alpha, 0, False, False, candidate=CAR.VOLKSWAGEN_SHARAN_MK2, pq_eps_pause=True)


def test_pq_early_native_brake_with_driver_steering_actual_callers():
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'automatic', True, 0, False, True, candidate=CAR.VOLKSWAGEN_SHARAN_MK2, early_brake=True)


def test_pq_real_native_eps_fault_is_not_ordinary_long_withdrawal():
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'automatic', True, 0, False, True, candidate=CAR.VOLKSWAGEN_SHARAN_MK2, early_brake=True, pq_real_fault=True)


def test_pq_brake_held_standstill_engagement_actual_callers():
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'automatic', True, 0, False, True, candidate=CAR.VOLKSWAGEN_SHARAN_MK2, brake_held_engagement=True)


@pytest.mark.parametrize('gearbox', ('automatic', 'manual', 'direct'))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('phase', range(10))
def test_mqb_cancel_native_and_panda_publication_phases(phase, alpha, gearbox):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    gearbox, alpha, 0, False, True, cruise_cancel=True, native_phase=phase)


@pytest.mark.parametrize('gearbox', ('automatic', 'manual', 'direct'))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('phase', (0, 1, 5, 9))
def test_mqb_startup_main_precedes_heartbeat_and_native_ack(alpha, phase, gearbox):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    gearbox, alpha, 0, False, False, parked_boot=True, native_phase=phase, delayed_heartbeat=True)


def test_mqb_manual_long_minimum_withdraws_speed_control_only():
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual', True, 0, False, True, manual_speed_withdrawal=True)


def test_mqb_cold_main_creep_never_needs_cruise_engagement():
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual', True, 0, False, False, parked_boot=True, delayed_heartbeat=True, cold_creep=True)


@pytest.mark.parametrize('veto', ('none', 'can', 'timeout', 'permanent', 'temporary', 'reverse', 'main',
                                   'automatic', 'stock', 'above_minimum', 'below_steering', 'paused', 'disarmed'))
def test_manual_speed_event_never_masks_other_disarming_faults(veto):
  from types import SimpleNamespace
  from openpilot.selfdrive.selfdrived.events import Events
  from openpilot.starpilot.car.volkswagen.aol import policy_for, lateral_immediate_disable, intent_disarming_fault
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, {bus: ({0xFD: 8} if bus == 1 else {}) for bus in range(8)}, [], True, False, False)
  policy = policy_for(cp)
  cp.alternativeExperience |= policy.alternative_experience_addition
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  state = structs.CarState(canValid=True, vEgo=4.0, gearShifter=structs.CarState.GearShifter.drive)
  state.cruiseState.available = True
  intent = SimpleNamespace(allowedLatch=veto != 'disarmed', lateralArmed=True, pauseLateral=veto == 'paused')
  fields = {'can': ('canValid', False), 'timeout': ('canTimeout', True), 'permanent': ('steerFaultPermanent', True),
            'temporary': ('steerFaultTemporary', True), 'reverse': ('gearShifter', structs.CarState.GearShifter.reverse),
            'above_minimum': ('vEgo', 20.0), 'below_steering': ('vEgo', 0.1)}
  if veto in fields:
    setattr(state, *fields[veto])
  elif veto == 'main':
    state.cruiseState.available = False
  elif veto == 'automatic':
    cp.transmissionType = structs.CarParams.TransmissionType.automatic
    cp.safetyConfigs[-1].safetyParam = 9
  elif veto == 'stock':
    cp.openpilotLongitudinalControl, cp.pcmCruise = False, True
    cp.safetyConfigs[-1].safetyParam = 24
  events = Events()
  events.add(log.OnroadEvent.EventName.speedTooLow)
  assert lateral_immediate_disable(cp, state, intent, events.to_msg()) is (veto != 'none')
  events.add(log.OnroadEvent.EventName.controlsMismatch)
  assert lateral_immediate_disable(cp, state, intent, events.to_msg())
  assert intent_disarming_fault(cp, events.to_msg(), state)


@pytest.mark.parametrize('gearbox', ('automatic', 'manual', 'direct'))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('phase', range(10))
def test_mqb_cancel_with_accelerator_and_driver_steering(gearbox, alpha, phase):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    gearbox, alpha, 0, False, True, cruise_cancel=True, cancel_override=True, native_phase=phase)


@pytest.mark.parametrize('gearbox', ('automatic', 'manual', 'direct'))
@pytest.mark.parametrize('alpha', (False, True))
def test_mqb_reverse_braked_start_waits_before_consuming_default_main(gearbox, alpha):
  receipt = []
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    gearbox, alpha, 0, False, False, parked_boot=True, reverse_boot=True, delayed_heartbeat=True, receipt_sink=receipt)
  first_active = next(row for row in receipt if row['control'].get('latActive'))
  assert first_active['tick'] < 65
  for row in receipt[first_active['tick']:85]:
    assert row['card_intent']['allowed'] and row['control'].get('latActive') and row['permission_mask'] & 1


@pytest.mark.parametrize('gearbox', ('automatic', 'manual', 'direct'))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('cancel', (False, True))
def test_mqb_external_panda_fixed_selection_start_and_pedal_cancel(gearbox, alpha, cancel):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    gearbox, alpha, 4, True, cancel, parked_boot=not cancel, reverse_boot=not cancel,
    delayed_heartbeat=not cancel, cruise_cancel=cancel, cancel_override=cancel, native_phase=1)


@pytest.mark.parametrize('phase', range(10))
def test_mqb_route23_stopped_start(phase):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual', True, 0, False, False, parked_boot=True, reverse_boot=True,
    reverse_unbraked=True, delayed_heartbeat=True, native_phase=phase, startup_event_delay=2)


@pytest.mark.parametrize('phase', range(10))
@pytest.mark.parametrize('delay', (1, 2))
def test_mqb_route23_cancel_axis_arrives_after_button(phase, delay):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual', True, 0, False, True, cruise_cancel=True, cancel_override=True,
    native_phase=phase, cancel_axis_delay=delay, cancel_steering=False)


def test_mqb_cancel_does_not_extend_axis_deadline():
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual', True, 0, False, True, cruise_cancel=True, cancel_override=True,
    native_phase=5, cancel_axis_delay=3, cancel_steering=False, cancel_axis_expiry=True)


@pytest.mark.parametrize('gearbox', ('automatic', 'manual', 'direct'))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('steering', (False, True))
def test_mqb_cancel_ordering_preserves_other_configurations(gearbox, alpha, steering):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    gearbox, alpha, 4, True, True, cruise_cancel=True, cancel_override=True,
    native_phase=5, cancel_axis_delay=2, cancel_steering=steering)


@pytest.mark.parametrize('phase', range(10))
@pytest.mark.parametrize('delay', (1, 2))
@pytest.mark.parametrize('services', ((), ('aolAxisState',), ('onroadEvents',), ('aolAxisState', 'onroadEvents')))
def test_route2c_startup_delivery_order(phase, delay, services):
  receipts = []
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual',
    True,
    0,
    False,
    False,
    parked_boot=True,
    reverse_boot=True,
    reverse_unbraked=True,
    delayed_heartbeat=True,
    native_phase=phase,
    cold_creep=True,
    route2c_start=True,
    startup_delivery_delay=delay,
    startup_delivery_services=services,
    receipt_sink=receipts,
  )
  assert_startup_provenance(receipts)


def assert_startup_provenance(receipts):
  from openpilot.starpilot.aol.wire import decode_intent, decode_safety

  anchors = {row['card_intent']['startup_arm_ns'] for row in receipts if row['card_intent']['deferred']}
  assert len(anchors) == 1
  deadline = anchors.pop() + 200_000_000
  first_active = False
  for row in receipts:
    owner = row['card_intent']
    intent = decode_intent(bytes.fromhex(row['intent_wire_hex']))
    assert intent is not None
    assert not row['control']['longActive']
    if owner['deferred'] and not owner['granted']:
      if not owner['aborted'] and intent.observedMonoTime <= deadline:
        assert intent.validUntilMonoTime == deadline, 'VW_STARTUP_WIRE_RENEWED_ORIGINAL_ATTEMPT'
      if owner['aborted'] or intent.observedMonoTime > deadline:
        assert not intent.allowedLatch and not intent.lateralArmed
    if row['control']['latActive'] and not first_active:
      native = decode_safety(bytes.fromhex(row['native_wire_hex']))
      assert native is not None and native.requestedLateral and native.lateralAllowed
      assert row['permission_mask'] & 1
      assert native.observedMonoTime <= deadline
      assert row['sd_axis']['observedMonoTime'] <= deadline
      first_active = True


@pytest.mark.parametrize('phase,expired', ((5, False), (6, True), (0, True)))
def test_route2c_original_deadline_grant_boundary(phase, expired):
  receipts = []
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual',
    True,
    0,
    False,
    False,
    parked_boot=True,
    reverse_boot=True,
    reverse_unbraked=True,
    delayed_heartbeat=True,
    native_phase=phase,
    cold_creep=True,
    route2c_start=True,
    startup_delivery_delay=1,
    startup_delivery_services=('aolAxisState', 'onroadEvents'),
    startup_block_until=50,
    startup_expect_expiry=expired,
    receipt_sink=receipts,
  )
  assert_startup_provenance(receipts)


def assert_readiness_denials(card, state, now):
  from copy import copy
  from dataclasses import replace
  from openpilot.starpilot.aol.runtime import current_native
  from openpilot.selfdrive.selfdrived.events import Events

  proof = current_native(card.sm, card.CP, now_ns=now, axis_session_id=card.aol_axis_session())
  assert proof is not None and not proof.requestedLateral and not proof.lateralAllowed
  owner = copy(card.aol_card_intent)
  owner.startup_release_ns = 0
  owner.allowed_latch = False
  cases = []

  def ready(cs=state, request=owner, sources=card.sm, native=proof, panda_ready=True, stamp=now, producer=card.slc_producer_session):
    candidate = copy(request)
    candidate.observe_startup_readiness(sources, cs, native=native, panda_ready=panda_ready, now_ns=stamp, producer=producer)
    return candidate.startup_ready

  def deny(name, **kwargs):
    assert not ready(**kwargs), name
    cases.append(name)

  assert ready(), 'actual fresh OFF/Drive baseline must qualify'
  for field, value in (
    ('canValid', False),
    ('canTimeout', True),
    ('steerFaultTemporary', True),
    ('steerFaultPermanent', True),
    ('accFaulted', True),
    ('vehicleSensorsInvalid', True),
    ('steeringDisengage', True),
    ('doorOpen', True),
    ('seatbeltUnlatched', True),
    ('brakePressed', True),
    ('regenBraking', True),
    ('vEgo', 0.1),
    ('standstill', True),
    ('gearShifter', structs.CarState.GearShifter.reverse),
  ):
    cs = structs.CarState(**state.to_dict())
    setattr(cs, field, value)
    deny(field, cs=cs)
  cs = structs.CarState(**state.to_dict())
  cs.cruiseState.available = False
  deny('MAIN off', cs=cs)
  cs = structs.CarState(**state.to_dict())
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=True)]
  deny('physical Cancel', cs=cs)
  for key, value in (('startup_aborted', True), ('startup_granted', True), ('pause_lateral', True), ('startup_arm_ns', now - 200_000_001)):
    bad = copy(owner)
    setattr(bad, key, value)
    deny(key, request=bad)
  for changes in ({'enabled': False}, {'main_action': 9}, {'lkas_action': 9}):
    bad = copy(owner)
    bad.settings = replace(owner.settings, **changes)
    deny(str(changes), request=bad)
  deny('native inventory missing', native=replace(proof, pandaInventory=(), sourcePandaStatesMonoTime=0))
  deny('native missing', native=None)
  deny('Panda not configured', panda_ready=False)
  deny('expired native', native=replace(proof, validUntilMonoTime=now - 1))
  deny('native requested LAT refused', native=replace(proof, requestedLateral=True))
  deny('foreign native session', native=replace(proof, axisSessionId='foreign'))
  deny('foreign native serial', native=replace(proof, pandaSerial='foreign'))
  for field in ('heartbeatLost', 'safetyRxChecksInvalid'):
    slot = replace(proof.pandaInventory[0], **{field: True})
    deny(field, native=replace(proof, pandaInventory=(slot,)))
  for field, value in (
    ('intentProducerSessionId', 'foreign'),
    ('intentIntegrityEpoch', card.sm['aolAxisState'].intentIntegrityEpoch + 1),
    ('faultReason', 'critical'),
    ('faultSessionId', 'foreign'),
    ('sequence', 0),
    ('sourceCarStateMonoTime', owner.startup_arm_ns - 1),
    ('faultEventMonoTime', owner.startup_arm_ns - 1),
    ('validUntilMonoTime', now - 1),
    ('desiredLateral', True),
    ('desiredLongitudinal', True),
  ):
    sm = copy(card.sm)
    sm.data = dict(card.sm.data)
    axis = card.sm['aolAxisState'].as_builder()
    setattr(axis, field, value)
    sm.data['aolAxisState'] = axis.as_reader()
    deny(field, sources=sm)
  for service in ('aolAxisState', 'onroadEvents', 'pandaStates'):
    for flag in ('seen', 'valid', 'alive'):
      sm = copy(card.sm)
      setattr(sm, flag, dict(getattr(card.sm, flag)))
      getattr(sm, flag)[service] = False
      deny(service + ' ' + flag, sources=sm)
  for name in (
    log.OnroadEvent.EventName.controlsMismatch,
    log.OnroadEvent.EventName.commIssue,
    log.OnroadEvent.EventName.buttonCancel,
    log.OnroadEvent.EventName.reverseGear,
  ):
    sm = copy(card.sm)
    sm.data = dict(card.sm.data)
    events = Events()
    events.add(name)
    sm.data['onroadEvents'] = [*card.sm['onroadEvents'], *events.to_msg()]
    deny(str(name), sources=sm)
  deny('foreign producer argument', producer='foreign')
  assert len(cases) == 56


def test_route2c_readiness_denial_boundaries():
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual',
    True,
    0,
    False,
    False,
    parked_boot=True,
    reverse_boot=True,
    reverse_unbraked=True,
    delayed_heartbeat=True,
    native_phase=6,
    cold_creep=True,
    route2c_start=True,
    startup_delivery_delay=1,
    startup_delivery_services=('aolAxisState', 'onroadEvents'),
    startup_probe_denials=True,
  )


@pytest.mark.parametrize('phase', (0, 6))
@pytest.mark.parametrize('stall', ((36, 58), (38, 58), (54, 58)))
@pytest.mark.parametrize('mode', ('frozen', 'frozen-timeout', 'publish-only'))
def test_route2c_card_stall_preserves_original_deadline(phase, stall, mode):
  from openpilot.starpilot.aol.wire import decode_intent

  receipts = []
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual',
    True,
    0,
    False,
    False,
    parked_boot=True,
    reverse_boot=True,
    reverse_unbraked=True,
    delayed_heartbeat=True,
    native_phase=phase,
    cold_creep=True,
    route2c_start=True,
    startup_delivery_delay=1,
    startup_delivery_services=('aolAxisState', 'onroadEvents'),
    startup_block_until=50,
    receipt_sink=receipts,
    startup_stall=stall,
    startup_stall_mode=mode,
  )
  assert_startup_provenance(receipts)
  anchor = 10_350_000_000
  deadline = anchor + 200_000_000
  for row in receipts:
    intent = decode_intent(bytes.fromhex(row['intent_wire_hex']))
    assert intent is not None
    assert row['card_intent']['startup_arm_ns'] in (0, anchor)
    if mode.startswith('frozen') and row['card_stalled']:
      assert row['published_car_state_ns'] == 10_000_000_000 + (stall[0] - 1) * 10_000_000
    if mode == 'publish-only' and row['card_stalled']:
      assert row['published_car_state_ns'] == row['now_ns']
    if row['now_ns'] > deadline:
      assert not row['control']['latActive'] and not row['control']['longActive']
      assert not row['sd_axis']['desiredLateral'] and not row['sd_axis']['lateralActive']
      assert not row['sd_axis']['desiredLongitudinal'] and not row['sd_axis']['longitudinalActive']
      # Native may still report its prior grant until the next host request; no host/axis/output may use it.
      assert not row['host_request'] & 1
      assert not row['sendcan'] or all(
        address['address'] != 0x126
        or not ((int.from_bytes(bytes.fromhex(address['data']), 'little') >> 30 & 1) or (int.from_bytes(bytes.fromhex(address['data']), 'little') >> 16 & 511))
        for address in row['sendcan']
      )
      if intent.observedMonoTime > deadline:
        assert not intent.allowedLatch and not intent.lateralArmed


@pytest.mark.parametrize('phase', range(10))
def test_mqb_cold_start_floor_dip_recovers_on_fresh_drive_feedback(phase):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual', True, 0, False, False, parked_boot=True, reverse_boot=True,
    reverse_unbraked=True, delayed_heartbeat=True, native_phase=phase, cold_creep=True, route2c_start=True,
    startup_delivery_delay=1, startup_delivery_services=('aolAxisState', 'onroadEvents'), startup_floor_dip=True)


@pytest.mark.parametrize('phase', range(10))
@pytest.mark.parametrize('delay', (1, 2))
def test_mqb_cancel_receipt_precedes_both_status_publications(phase, delay):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual', True, 0, False, True, cruise_cancel=True, native_phase=phase, cancel_delivery_delay=delay)


def assert_floor_recovery_vetoes(card, state, now):
  from copy import copy
  from dataclasses import replace
  from openpilot.starpilot.aol.runtime import current_native
  proof = current_native(card.sm, card.CP, now_ns=now, axis_session_id=card.aol_axis_session())
  assert proof is not None
  def observe(cs=state, native=proof, stamp=now, producer=card.slc_producer_session, owner=card.aol_card_intent):
    candidate = copy(owner)
    candidate.observe_startup_readiness(card.sm, cs, native=native, panda_ready=True, now_ns=stamp, producer=producer)
    return candidate
  assert observe().startup_floor_pending
  for field, value in (('canValid', False), ('canTimeout', True), ('steerFaultPermanent', True),
                       ('steerFaultTemporary', True), ('accFaulted', True), ('steeringDisengage', True)):
    cs = structs.CarState(**state.to_dict())
    setattr(cs, field, value)
    result = observe(cs=cs)
    assert not result.startup_floor_pending and not result.allowed_latch
  for native in (None, replace(proof, axisSessionId='foreign'), replace(proof, pandaInventory=())):
    result = observe(native=native)
    assert not result.startup_floor_pending and not result.allowed_latch
  assert not observe(stamp=now + 30_000_001).startup_floor_pending
  assert not observe(producer='foreign').startup_floor_pending
  off = copy(card.aol_card_intent)
  off.settings = replace(off.settings, enabled=False)
  assert not observe(owner=off).startup_floor_pending


@pytest.mark.parametrize('veto', ('none', 'held', 'stale_receipt', 'future_receipt', 'stale_signal', 'future_signal', 'no_raw_press', 'foreign_state'))
def test_mqb_cancel_receipt_binds_exact_current_raw_press(veto):
  from types import SimpleNamespace
  from openpilot.starpilot.car.volkswagen.aol import cancel_source, policy_for
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, {bus: ({0xFD: 8} if bus == 1 else {}) for bus in range(8)}, [], True, False, False)
  cp.fingerprintSource = structs.CarParams.FingerprintSource.fw
  addition = policy_for(cp).safety_param_addition
  assert addition
  cp.safetyConfigs[-1].safetyParam |= addition
  cp.alternativeExperience = 32
  state = structs.CarState(canValid=True, gearShifter=structs.CarState.GearShifter.drive)
  state.cruiseState.available = True
  state.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=veto != 'held')]
  now, stamp, received = 10_000_000_000, 20_000_000_000, 10_000_000_000
  if veto == 'stale_receipt':
    received -= 30_000_001
  if veto == 'future_receipt':
    received += 1
  signal = stamp - 30_000_001 if veto == 'stale_signal' else stamp + 1 if veto == 'future_signal' else stamp
  parser = SimpleNamespace(vl={'GRA_ACC_01': {'GRA_Abbrechen': int(veto != 'no_raw_press')}},
                           ts_nanos={'GRA_ACC_01': {'GRA_Abbrechen': signal}})
  observation = (object() if veto == 'foreign_state' else state, received, stamp, stamp)
  assert cancel_source(cp, state, parser, observation, now) == (now if veto == 'none' else 0)


@pytest.mark.parametrize('phase', range(10))
@pytest.mark.parametrize('delay', (1, 2))
def test_mqb_brake_withdraws_long_and_preserves_lateral(phase, delay):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual', True, 0, False, True, native_phase=phase, brake_delivery_delay=delay)


@pytest.mark.parametrize('phase', range(10))
def test_mqb_cold_drive_waits_for_healthy_startup(phase):
  test_mqb_full_engagement_brake_reverse_drive_actual_callers(
    'manual', True, 0, False, False, parked_boot=True, delayed_heartbeat=True,
    native_phase=phase, rushed_start=True)
