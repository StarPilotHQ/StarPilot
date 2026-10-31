"""Shared actual Ford factory/Card/SD/Controls/native regression fixture.

Transport, clocks and initialized onroad service sources are synthetic. This is
not startup/USB/road qualification. No desired/allowed axis value is invented.
"""

from types import SimpleNamespace

from opendbc.can import CANParser
from opendbc.car import Bus
from opendbc.car.can_definitions import CanData
from unittest.mock import patch

import pytest
from openpilot.starpilot.aol.wire import decode_intent

"""Test transport subset of pandad's current, producer-bound retained-arm heartbeat."""


def retained_arm(intent_event, axis_event, now_ns):
  if intent_event is None or axis_event is None or not intent_event.valid or not axis_event.valid:
    return False
  intent = decode_intent(bytes(intent_event.aolIntentWire))
  axis = axis_event.aolAxisState
  if intent is None:
    return False
  stamp = int(intent_event.logMonoTime)
  # The coupled probe has exact same-source Card/SD publications. This stricter
  # subset needs none of pandad's optional newer-source/expired-axis joins.
  return bool(
    axis.qualified
    and axis.sessionId
    and axis.intentProducerSessionId
    and int(axis_event.logMonoTime) == axis.observedMonoTime == axis.sourceCarStateMonoTime == stamp
    and 0 < stamp <= now_ns <= axis.validUntilMonoTime
    and now_ns - stamp <= 30_000_000
    and intent.producerSessionId == str(axis.intentProducerSessionId)
    and intent.sequence > 0
    and intent.carStateLogMonoTime == intent.observedMonoTime == stamp
    and intent.settingsQualified
    and intent.lateralArmed
    and now_ns <= intent.validUntilMonoTime
  )


from opendbc.car.ford.values import CAR, FordFlags, DBC
from openpilot.cereal import messaging
from openpilot.selfdrive.car import card as card_module
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety


FORD_FACTORIES = (
  (CAR.FORD_BRONCO_SPORT_MK1, 0),
  (CAR.FORD_EDGE_MK2, 8),
  (CAR.FORD_ESCAPE_MK4, 0),
  (CAR.FORD_ESCAPE_MK4_5, 2),
  (CAR.FORD_EXPLORER_MK6, 0),
  (CAR.FORD_EXPEDITION_MK4, 2),
  (CAR.FORD_F_150_MK14, 2),
  (CAR.FORD_F_150_LIGHTNING_MK1, 2),
  (CAR.FORD_FOCUS_MK4, 0),
  (CAR.FORD_MONDEO_MK5, 10),
  (CAR.FORD_MAVERICK_MK1, 0),
  (CAR.FORD_MUSTANG_MACH_E_MK1, 2),
  (CAR.FORD_RANGER_MK2, 2),
  (CAR.FORD_TRANSIT_MK5, 12),
)


@pytest.fixture
def ford_joined(identity, alpha, hands_free, monkeypatch, record_property):
  from opendbc.car import car_helpers, gen_empty_fingerprint, structs
  from opendbc.safety.tests import test_ford_aol as ford_native_module
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix

  monkeypatch.setenv('SIMULATION', '1')
  monkeypatch.setenv('REPLAY', '1')
  monkeypatch.setenv('AOL_REPLAY_RUNTIME', '0')
  native = ford_native_module.TestFordAolDriverIntent(methodName='runTest')
  native.setUp()
  release = native.safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0
  host = None
  with OpenpilotPrefix():
    saved = Params()
    for key, value in (
      ('OpenpilotEnabledToggle', True),
      ('AlwaysOnLateral', True),
      ('SafeMode', False),
      ('AlphaLongitudinalEnabled', alpha),
      ('IsReleaseBranch', release),
    ):
      saved.put_bool(key, value, block=True)
    from pathlib import Path

    for key, value in (('PauseAOLOnBrake', b'0.0'), ('CancelButtonControl', b'0'), ('MainCruiseButtonControl', b'0')):
      Path(saved.get_param_path(key)).write_bytes(value)
    Path(saved.get_param_path("FordHandsFreeCluster")).write_bytes(b"1" if hands_free else b"0")
    fp = gen_empty_fingerprint()
    topology = getattr(monkeypatch, 'ford_topology', 'automatic')
    offset = 4 if topology == 'multi-panda' else 0
    manual = topology == 'manual' or topology == 'manual-multi'
    if topology == 'manual-multi':
      offset = 4
    if not manual:
      fp[offset][0x5A] = 8
    fp[offset].update({0x3A6: 8, 0x3A7: 8})
    fp[offset + 2].update({0x3D6: 8, 0x186: 8})
    name, base = identity
    observed = (name, fp, '0' * 17, [], structs.CarParams.FingerprintSource.can, True)
    try:
      with monkeypatch.context() as discovery:
        discovery.setattr(car_helpers, 'fingerprint', lambda *args: observed)
        discovery.setattr(card_module, 'can_comm_callbacks', lambda *args: (lambda *args: [], lambda frames: None))
        discovery.setattr(card_module.messaging, 'recv_one_retry', lambda socket: messaging.new_message('can', 1))
        host = card_module.Car()
      assert host.CP.alternativeExperience == 32 and host.aol_card_intent is not None
      assert str(host.CP.carFingerprint) == str(name)
      expected_long = bool(alpha)
      assert host.CP.openpilotLongitudinalControl == expected_long
      expected_word = base | int(expected_long) | (256 if manual else 0)
      assert host.CP.safetyConfigs[-1].safetyParam == expected_word
      record_property('ford_factory_identity', str(name))
      record_property('ford_saved_alpha', alpha)
      record_property('ford_release', release)
      record_property('ford_longitudinal', expected_long)
      record_property('ford_safety_word', int(host.CP.safetyConfigs[-1].safetyParam))
      native.reset(host.CP.safetyConfigs[-1].safetyParam)
      native.word &= ~(1 | 128)
      # Real empty update registers lazily accessed parser messages before RX.
      # Card.controls_update owns CI initialization; no flag is assigned here.
      host.CI.update([])
      for _warm_tick in range(120):
        preframes = []

        def pre_rx(message, fields, preframes=preframes):
          frame = native.packer.make_can_msg(message, 0, fields)
          from opendbc.safety.tests.libsafety import libsafety_py

          assert native.safety.safety_rx_hook(libsafety_py.make_CANPacket(frame[0], frame[2], frame[1]))
          preframes.append(frame)

        with patch.object(native, 'rx', side_effect=pre_rx):
          native.pump(1, main=0 if getattr(monkeypatch, 'ford_first_main', False) else 3, mask=0, heartbeat=False, speed=15.0)
        extras: list[tuple[str, int, dict[str, float]]] = [
          ('SteeringPinion_Data', 0, {'StePinComp_An_Est': 0., 'StePinCompAnEst_D_Qf': 3}),
          ('Cluster_Info1_FD1', 0, {'AccEnbl_B_RqDrv': 1}),
          ('BodyInfo_3_FD1', 0, {}),
          ('RCMStatusMessage2_FD1', 0, {'FirstRowBuckleDriver': 1}),
          ('INSTRUMENT_PANEL', 0, {}),
          ('IPMA_Data', 2, {}),
          ('ACCDATA', 2, {}),
          ('ACCDATA_2', 2, {}),
          ('ACCDATA_3', 2, {}),
        ]
        if host.CP.flags & FordFlags.ALT_STEER_ANGLE:
          extras += [('SteeringPinion_Data_Alt', 0, {'StePinRelInit_An_Sns': 0}), ('ParkAid_Data', 0, {'ExtSteeringAngleReq2': 0})]
        if host.CP.flags & FordFlags.LKA_STEERING:
          extras += [('LateralMotionControl', 2, {})]
        extras += [('Side_Detect_L_Stat', 2 if host.CP.flags & FordFlags.CANFD else 0, {}),
                   ('Side_Detect_R_Stat', 2 if host.CP.flags & FordFlags.CANFD else 0, {})]
        preframes += [native.packer.make_can_msg(message, bus, fields) for message, bus, fields in extras]
        state = host.CI.update([(10_000_000_000 + native.tick * 10_000_000, [CanData(addr, data, bus + offset) for addr, data, bus in preframes])])
        assert not native.safety.get_controls_allowed() and native.safety.aol_get_permission_mask() == 0
      assert state.canValid and not state.canTimeout
      yield SimpleNamespace(host=host, native=native, safety=native.safety, initial_cp=host.CP.to_dict(),
                            expected_long=expected_long, hands_free=hands_free, offset=offset)
    finally:
      native.tearDown()
      if host is not None:
        host.vehicle_startup.close()


def run_actual_producer(ford_joined, monkeypatch, *, scenario='reverse', reengage=False):
  # Reuse the factory/parser/native configuration; no old synthetic SD peer runs.
  from openpilot.starpilot.aol.tests import test_ioniq_availability as availability

  joined = ford_joined
  host, native, safety = joined.host, joined.native, joined.safety
  case = availability.IoniqAvailabilityTests(methodName='runTest')
  now = [10_000_000_000]
  queued, published, previous_sd = [], {}, {}
  delayed_native = {}
  steps = start_tick = native.tick
  warmup_waits = qualified_samples = 0
  active_lat_frames = active_long_frames = 0
  nonzero_after_brake = 0
  brake_seen = False
  transition_trace = []
  last_neutral_ui = {}
  joined.rejected_ui_packets = []
  long_parser = CANParser(DBC[host.CP.carFingerprint][Bus.pt], [('ACCDATA', 50)], 0)

  class ClockMappedSafety:
    """Translate only the native fixture's clock epoch to the existing SD harness."""

    def set_timer(self, timestamp):
      safety.set_timer(timestamp + 9_000_000)

    def __getattr__(self, name):
      return getattr(safety, name)

  try:
    with monkeypatch.context() as local:
      local.setenv('SIMULATION', '0')
      sd, controls, drive = case._authority_callers(False, cp=host.CP.as_reader().as_builder(), genuine_events=True)
      assert sd.CP.to_dict() == controls.CP.to_dict() == host.CP.to_dict()
      from openpilot.starpilot.aol.intent import read_settings
      sd.aol_settings = read_settings(host.params)
      local.setattr(card_module, 'REPLAY', False)
      local.setattr(card_module.time, 'monotonic_ns', lambda: now[0])
      local.setattr(card_module.time, 'clock_gettime_ns', lambda clock: now[0])
      local.setattr(card_module.time, 'monotonic', lambda: now[0] / 1e9)
      local.setattr(card_module.messaging, 'drain_sock_raw', lambda *args, **kwargs: list(queued))
      local.setattr(host.sm, 'update', lambda *args: None)
      create = messaging.new_message

      def clocked_message(service=None, size=None, **kwargs):
        kwargs.setdefault('logMonoTime', now[0])
        return create(service, size, **kwargs)

      local.setattr(messaging, 'new_message', clocked_message)
      send = host.pm.send

      def capture(service, event):
        raw = event if isinstance(event, bytes) else event.to_bytes()
        published[service] = messaging.log_from_bytes(raw).as_builder()
        return send(service, raw)

      local.setattr(host.pm, 'send', capture)
      if getattr(monkeypatch, 'ford_nonzero', False):
        control_update = controls.sm.update_msgs

        def curved_model_update(timestamp, messages):
          snapshots = []
          for original in messages:
            if original.which() == 'modelV2':
              original = original.as_builder()
              original.modelV2.action.desiredCurvature = getattr(monkeypatch, "ford_test_curvature", 0.0001)
              original = original.as_reader()
            snapshots.append(original)
          return control_update(timestamp, snapshots)

        local.setattr(controls.sm, 'update_msgs', curved_model_update)
      native.safety = ClockMappedSafety()

      location_unavailable = [False]
      original_update = sd.sm.update_msgs

      def location_update(timestamp, messages):
        selected = []
        for message in messages:
          if message.which() == 'deviceMotion':
            message = message.as_builder()
            message.deviceMotion.inputsOK = not location_unavailable[0]
            message = message.as_reader()
          selected.append(message)
        return original_update(timestamp, selected)

      local.setattr(sd.sm, 'update_msgs', location_update)

      delayed_pedal_frames = []
      delayed_pedals_seen = set()
      route45_old = {}
      route45_mixed = [False]
      route45_phase = ['warmup']
      route45_trace = []

      def step(*, engaged=False, brake=False, reverse=False, warmup=False, fault=None, main_off=False,
               availability_transition=False, steering=False, cancel=False, park=False):
        nonlocal steps, warmup_waits, qualified_samples, active_lat_frames, active_long_frames, nonzero_after_brake, brake_seen
        brake_seen |= brake
        steps += 1
        now[0] = 10_000_000_000 + steps * 10_000_000
        prior_axis = previous_sd.get('aolAxisState')
        joined.last_input_axis = prior_axis
        prior_state = previous_sd.get('selfdriveState')
        request = 0 if prior_axis is None else (int(prior_axis.aolAxisState.desiredLateral) | (int(prior_axis.aolAxisState.desiredLongitudinal) << 1))
        arm_heartbeat = retained_arm(published.get('aolIntentWire'), prior_axis, now[0])
        heartbeat = bool(request or prior_state is not None and prior_state.selfdriveState.enabled or arm_heartbeat)
        frames = delayed_pedal_frames[:]
        delayed_pedal_frames.clear()

        def receive(name, values):
          fields = dict(values)
          refusal = getattr(monkeypatch, 'ford_route45_refusal', None) if route45_mixed[0] else None
          if scenario == 'route45' and refusal == 'permanent-eps' and name == 'EPAS_INFO':
            fields['EPAS_Failure'] = 2
          if scenario == 'route45' and refusal == 'main-off' and name == 'EngBrakeData':
            fields['CcStat_D_Actl'] = 0
          if scenario == 'route45' and refusal == 'cancel' and name == 'Steering_Data_FD1':
            fields['CcAslButtnCnclPress'] = 1
          if scenario in ('route49', 'cancel-native-cadence', 'neutral-main-cadence') and name == 'Steering_Data_FD1':
            fields['CcAslButtnCnclResPress'] = int(cancel)
          if scenario in ('route45', 'route49', 'route22') and name == 'EPAS_INFO':
            fields['SteeringColumnTorque'] = 2 if steering else 0
          if scenario == 'route45' and name == 'Lane_Assist_Data3_FD1':
            fields['LatCtlSte_D_Stat'] = 0 if fault in ('temporary-lkas', 'temporary-lkas-gas') else 1
          if scenario == 'route45' and fault == 'permanent-eps' and name == 'EPAS_INFO':
            fields['EPAS_Failure'] = 2
          if getattr(joined, 'stationary', False):
            if name == 'DesiredTorqBrk':
              fields['VehStop_D_Stat'] = 1
            if name == 'EngBrakeData':
              fields['AccStopMde_D_Rq'] = 3 if engaged and not brake else 0
          if name == 'EngBrakeData' and brake:
            fields['BpedDrvAppl_D_Actl'] = 2
          if name == 'EngVehicleSpThrottle' and fault in ('gas', 'temporary-eps-gas', 'temporary-lkas-gas'):
            fields['ApedPos_Pc_ActlArb'] = 25
          if name == 'BrakeSysFeatures' and fault == 'rx-quality':
            fields['VehVActlBrk_D_Qf'] = 0
          frame = native.packer.make_can_msg(name, 0, fields)
          if not (fault == 'source-loss' and name == 'Yaw_Data_FD1'):
            accepted = safety.safety_rx_hook(joined_packet(frame))
            assert bool(accepted) == (not (fault == 'rx-quality' and name == 'BrakeSysFeatures'))
          pedal = name == 'EngVehicleSpThrottle' and fault in ('gas', 'temporary-eps-gas', 'temporary-lkas-gas') or name == 'EngBrakeData' and brake
          if getattr(monkeypatch, 'ford_native_first_pedal', False) and pedal and name not in delayed_pedals_seen:
            # The real native RX already consumed this frame above. Deliver it
            # to the host on the next update, retaining native authority now.
            delayed_pedals_seen.add(name)
            delayed_pedal_frames.append(frame)
          else:
            frames.append(frame)

        with patch.object(native, 'rx', side_effect=receive):
          native.pump(
            1,
            main=0 if main_off else 5 if engaged else 3,
            gear=0 if park else 1 if reverse else 3,
            mask=request,
            heartbeat=heartbeat and fault != 'heartbeat',
            speed=(3.1 + .12 * ((steps // 2) % 12) / 11 if getattr(monkeypatch, "ford_speed_window", False) else getattr(joined, 'speed', 15.0)),
            eps=1 if fault in ('temporary-eps', 'temporary-eps-gas') else 0,
          )
        assert native.tick == steps
        safety.safety_tick()
        native_request = safety.aol_get_request_mask()
        permission = safety.aol_get_permission_mask()
        joined.last_permission = permission
        joined.last_native_request = native_request
        if prior_axis is None:
          assert request == native_request == permission == 0
        assert permission & 1 == 0 or heartbeat and request & 1 and native_request & 1
        extras = [
          ('SteeringPinion_Data', 0, {'StePinComp_An_Est': 0., 'StePinCompAnEst_D_Qf': 3}),
          ('Cluster_Info1_FD1', 0, {'AccEnbl_B_RqDrv': 1}),
          ('BodyInfo_3_FD1', 0, {}),
          ('RCMStatusMessage2_FD1', 0, {'FirstRowBuckleDriver': 1}),
          ('INSTRUMENT_PANEL', 0, {}),
          ('IPMA_Data', 2, {}),
          ('ACCDATA', 2, {}),
          ('ACCDATA_2', 2, {}),
          ('ACCDATA_3', 2, {}),
        ]
        if host.CP.flags & FordFlags.ALT_STEER_ANGLE:
          extras.append(('SteeringPinion_Data_Alt', 0, {'StePinRelInit_An_Sns': 0}))
          if steps % 2 == 0:
            extras.append(('ParkAid_Data', 0, {'ExtSteeringAngleReq2': 0}))
        if host.CP.flags & FordFlags.LKA_STEERING and steps % 5 == 0:
          extras.append(('LateralMotionControl', 2, {}))
        extras += [('Side_Detect_L_Stat', 2 if host.CP.flags & FordFlags.CANFD else 0, {}),
                   ('Side_Detect_R_Stat', 2 if host.CP.flags & FordFlags.CANFD else 0, {})]
        frames += [native.packer.make_can_msg(name, bus, fields) for name, bus, fields in extras]
        frames = [(addr, data, bus + joined.offset) for addr, data, bus in frames]
        batch = messaging.log_from_bytes(card_module.can_list_to_can_capnp(frames)).as_builder()
        batch.logMonoTime = now[0]
        queued[:] = [batch.to_bytes()]
        config = host.CP.safetyConfigs[-1]
        slot = PandaSlot(
          len(host.CP.safetyConfigs) - 1, 'authority-panda', config.safetyModel.raw, config.safetyParam, 32,
          bool(safety.get_controls_allowed()), not bool(safety.safety_config_valid())
        )
        native_state = SafetyState(
          1,
          True,
          now[0],
          now[0] + 200_000_000,
          config.safetyModel.raw,
          config.safetyParam,
          bool(permission & 1),
          bool(permission & 2),
          bool(native_request & 1),
          bool(native_request & 2),
          'authority-panda',
          str(prior_axis.aolAxisState.sessionId) if prior_axis is not None else host.aol_axis_session(),
          sourcePandaStatesMonoTime=now[0],
          pandaInventory=(slot,) if len(host.CP.safetyConfigs) == 1 else (
            PandaSlot(0, 'silent-panda-0', host.CP.safetyConfigs[0].safetyModel.raw, 0, 32, False, False), slot),
        )
        wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now[0])
        wire.aolSafetyWire = encode_safety(native_state)
        panda = messaging.new_message('pandaStates', len(host.CP.safetyConfigs), valid=True, logMonoTime=now[0])
        panda.pandaStates[-1].safetyModel = config.safetyModel
        panda.pandaStates[-1].safetyParam = config.safetyParam
        panda.pandaStates[-1].alternativeExperience = 32
        panda.pandaStates[-1].controlsAllowed = slot.controlsAllowed
        panda.pandaStates[-1].safetyRxChecksInvalid = slot.safetyRxChecksInvalid
        if len(host.CP.safetyConfigs) == 2:
          panda.pandaStates[0].safetyModel = host.CP.safetyConfigs[0].safetyModel
          panda.pandaStates[0].alternativeExperience = 32
        device = messaging.new_message('deviceState', valid=True, logMonoTime=now[0])
        device.deviceState.started, device.deviceState.startedMonoTime = True, 9_000_000_000
        # Match pandad's 10Hz native publication, including a pedal-first USB read.
        # Values are actual native results; retained messages keep their stamps.
        if getattr(monkeypatch, 'ford_native_10hz', False):
          if not delayed_native or steps % 10 == 0 or (brake or cancel) and not permission & 2 and delayed_native.get('permission', 3) & 2:
            delayed_native.update(wire=wire.as_reader(), panda=panda.as_reader(), permission=permission)
          wire_message, panda_message = delayed_native['wire'], delayed_native['panda']
        else:
          wire_message, panda_message = wire.as_reader(), panda.as_reader()
        events = [wire_message, panda_message, device.as_reader()]
        for name in ('selfdriveState', 'aolAxisState', 'onroadEvents'):
          if name in previous_sd:
            events.append(previous_sd[name])  # Original publication/source stamps.
        if 'onroadEvents' not in previous_sd:
          events.append(messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now[0]).as_reader())
        if scenario == 'route45' and route45_phase[0] == 'released' and route45_old:
          current_events = previous_sd.get('onroadEvents')
          has_released_events = bool(current_events is not None and
            not any(str(e.name) == 'gasPressedOverride' for e in current_events.onroadEvents))
          mode = getattr(monkeypatch, 'ford_route45_delivery', 'paired')
          if has_released_events and (not route45_mixed[0] or mode == 'never-paired') and mode != 'paired':
            service = 'onroadEvents' if mode in ('axis-first', 'never-paired') else 'aolAxisState'
            events = [e for e in events if e.which() != service]
            events.append(route45_old[service])
            route45_mixed[0] = True
        host.sm.update_msgs(now[0] / 1e9, events)
        state, _ = host.state_update()
        host.state_publish(state, None)
        assert 'aolIntentWire' in published
        parser_ready = bool(
          state.canValid and not state.canTimeout and (state.cruiseState.available or scenario == 'route49' or warmup and main_off) and
          str(state.gearShifter) == 'drive' and safety.safety_config_valid()
        )
        if warmup and not parser_ready:
          assert steps <= start_tick + 120 and not engaged and not brake and not reverse
          assert request == native_request == permission == 0
          assert not safety.get_controls_allowed() and not host.aol_card_intent.allowed_latch
          warmup_waits += 1
          return state, None, None, None
        if fault is None:
          assert state.canValid and not state.canTimeout
        qualified_samples += 1
        parsed_before_producer = state.to_dict()
        assert (main_off or scenario == 'route49' or state.cruiseState.available or
                getattr(monkeypatch, 'ford_main_off_warmup', False) and steps <= start_tick + 130 or
                availability_transition and not state.cruiseState.enabled
                or scenario == 'route45' and getattr(monkeypatch, 'ford_route45_refusal', None) == 'main-off')
        assert scenario in ('route45', 'route49', 'route22') or not state.steeringPressed
        assert bool(state.gasPressed) == (fault in ('gas', 'temporary-eps-gas', 'temporary-lkas-gas')
                                          and not any(frame[0] == 0x204 for frame in delayed_pedal_frames))
        axis, sd_state, command = drive(
          steps,
          car_state_message=published['carState'],
          gas=bool(state.gasPressed),
          steering=bool(state.steeringPressed),
          intent_message=published['aolIntentWire'],
          allowed=bool(permission & 1),
          long_allowed=bool(permission & 2),
          native_request_lat=bool(native_request & 1),
          native_request_long=bool(native_request & 2),
          panda_controls_allowed=bool(safety.get_controls_allowed()),
          panda_fault=slot.safetyRxChecksInvalid,
          panda_state_message=panda_message.as_builder(),
          safety_message=wire_message.as_builder(),
        )
        if delayed_pedal_frames:
          assert command.latActive and host.aol_card_intent.allowed_latch, 'Native-first pedal erased granted lateral intent'
          assert not command.longActive, 'Native LONG withdrawal was ignored'
        assert state.to_dict() == parsed_before_producer, 'Authority harness changed actual parsed CarState'
        previous_sd.update(sd.pm.messages)
        control = controls.pm.messages['carControl']
        source_ns = now[0]
        now[0] += 1
        host.sm.update_msgs(now[0] / 1e9, [control])
        host.can_log_mono_time = source_ns
        published.pop('sendcan', None)
        joined.last_lateral_packets = []
        host.controls_update(state, host.sm['carControl'])
        if state.canValid:
          accepted_ui = []
          assert 'sendcan' in published
          assert published['sendcan'].valid
          for frame in published['sendcan'].sendcan:
            from opendbc.safety.tests.libsafety import libsafety_py

            accepted = safety.safety_tx_hook(libsafety_py.make_CANPacket(frame.address, frame.src - joined.offset, frame.dat))
            # A real native fault can precede the next 10Hz host receipt. Active
            # requests in that gap must be rejected, never treated as accepted.
            fault_before_receipt = (getattr(monkeypatch, 'ford_native_10hz', False) and
              scenario == 'route22' and fault in ('heartbeat', 'source-loss') and permission == 0)
            if fault_before_receipt and frame.address in (0x3D3, 0x3D6):
              mode = ((frame.dat[4] >> 2) & 7) if frame.address == 0x3D3 else ((frame.dat[0] >> 4) & 7)
              if mode:
                assert not accepted, 'Native fault admitted active steering'
            if not accepted:
              display_only = frame.address in (0x18A, 0x3D8)
              display_veto = (display_only and scenario == 'route22' and
                fault in ('heartbeat', 'source-loss', 'rx-quality') and permission == 0)
              assert display_veto or (fault_before_receipt and not safety.get_controls_allowed()), (
                steps, fault, hex(frame.address), bytes(frame.dat).hex(), request, native_request, permission)
              if display_only:
                joined.rejected_ui_packets.append((steps, frame.address))
              continue
            if frame.address in (0x18A, 0x3D8):
              accepted_ui.append(frame)
            data, address = frame.dat, frame.address
            if address in (0x3CA, 0x3D3, 0x3D6):
              joined.last_lateral_packets.append((address, bytes(data)))
              active = ((data[4] >> 2) & 7) != 0 if address == 0x3D3 else ((data[0] >> 4) & 7) != 0 if address == 0x3D6 else (data[0] >> 5) in (2, 4)
              if active:
                assert command.latActive and permission & 1
                active_lat_frames += 1
              if address in (0x3D3, 0x3D6):
                path_raw = (((data[3] & 31) << 6) | (data[4] >> 2)) if address == 0x3D6 else ((data[3] << 3) | (data[4] >> 5))
                if brake_seen and not brake and not reverse and path_raw != 1000:
                  nonzero_after_brake += 1
            elif address == 0x186:
              long_parser.update([(source_ns, [(address, bytes(data), frame.src - joined.offset)])])
              active = bool(long_parser.vl['ACCDATA']['Cmbb_B_Enbl'])
              assert active == bool(command.longActive)
              active_long_frames += int(active)
            elif address in (0x18A, 0x3D8) and not command.latActive and host.CP.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1:
              assert host.CP.openpilotLongitudinalControl
              if address == 0x18A:
                assert ((data[5] >> 2) & 7) <= 1, 'Inactive UI claimed active or hands-free steering'
              else:
                assert data[6] >> 3 in (0, 30) and ((data[6] >> 1) & 3) == 0
              last_neutral_ui[address] = now[0]
        if scenario in (
            'display', 'route24', 'route24-mixed-eps', 'route2a', 'route2a-main', 'route2a-main-fault'):
          ui = accepted_ui if state.canValid else []
          owns = all(safety.safety_fwd_hook(2, frame.address) == -1 for frame in ui)
          assert host.CP.carFingerprint not in (CAR.FORD_MUSTANG_MACH_E_MK1, CAR.FORD_F_150_LIGHTNING_MK1,
                                                  CAR.FORD_ESCAPE_MK4_5, CAR.FORD_EXPEDITION_MK4,
                                                  CAR.FORD_F_150_MK14, CAR.FORD_RANGER_MK2) or not ui or owns, (
            'Host injected cluster UI while native still forwarded stock UI',
            steps,
            bool(command.latActive),
            int(permission),
            int(safety.aol_get_permission_mask()),
            [(hex(f.address), bytes(f.dat).hex()) for f in published['sendcan'].sendcan],
            dict(vars(host.CI.CC)).get('aol_mache_ui'),
          )
          for frame in ui:
            if frame.address == 0x18A and command.latActive:
              display_parser = CANParser('ford_lincoln_base_pt', [('ACCDATA_3', 5)], 0)
              display_parser.update([(now[0], [(frame.address, bytes(frame.dat), frame.src - joined.offset)])])
              assert display_parser.vl['ACCDATA_3']['Tja_D_Stat'] == (7 if joined.hands_free and host.CP.flags & FordFlags.CANFD else 2)
          if not command.latActive and host.CP.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1:
            if not host.CP.openpilotLongitudinalControl:
              assert not ui
              assert all(safety.safety_fwd_hook(2, address) == 0 for address in (0x3D8, 0x18A))
            else:
              for address in (0x18A, 0x3D8):
                if safety.safety_fwd_hook(2, address) == -1:
                  assert address in last_neutral_ui
                  # IPMA is 1Hz; fresh accepted ACC Off/Standby refreshes the shared display heartbeat.
                  assert 0 <= now[0] - max(last_neutral_ui.values()) <= 200_000_000
        assert bool(axis.longitudinalActive) == bool(command.longActive)
        if not joined.expected_long:
          assert not axis.longitudinalActive and not command.longActive
        elif command.longActive:
          assert permission & 2 and safety.get_controls_allowed()
        transition_trace.append((steps, str(state.gearShifter), bool(state.brakePressed), bool(host.aol_card_intent.allowed_latch),
                                 bool(command.latActive), bool(command.longActive), permission, native_request,
                                 str(axis.faultReason), bool(host.aol_transport_feedback.failed),
                                 str(host.aol_transport_feedback.pending_event),
                                 tuple(str(event.name) for event in host.sm['onroadEvents'])))
        return state, axis, sd_state, command

      def joined_packet(frame):
        from opendbc.safety.tests.libsafety import libsafety_py

        return libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])

      for _ in range(120):
        step(warmup=True, main_off=scenario == 'route49' or getattr(monkeypatch, 'ford_main_off_warmup', False))
      assert 0 <= warmup_waits < 120 and qualified_samples >= 100
      assert host.ci_initialized  # Read-only witness: real controls_update completed init.
      assert not host.aol_card_intent.allowed_latch
      if scenario == 'startup-warning':
        if getattr(monkeypatch, 'ford_startup_armed', False):
          for _ in range(40):
            state, axis, sd_state, command = step(engaged=True)
          assert host.aol_card_intent.allowed_latch and command.latActive
          warning_seen = False
          warning_types = set()
          for _ in range(60):
            state, axis, sd_state, command = step(fault='temporary-eps')
            warning_types.add(sd_state.alertType)
            warning_seen |= sd_state.alertType in ('steerTempUnavailableSilent/warning', 'steerTempUnavailable/softDisable')
          assert state.steerFaultTemporary and host.aol_card_intent.allowed_latch
          assert 'warning' in sd.state_machine.current_alert_types
          assert warning_seen and not command.latActive and not command.longActive and joined.last_permission == 0, (
            warning_types, command.latActive, command.longActive, joined.last_permission)
        else:
          for _ in range(30):
            state, axis, sd_state, command = step(main_off=True, park=True, fault='temporary-eps')
          assert str(state.gearShifter) == 'park' and not state.cruiseState.available
          assert state.steerFaultTemporary and not host.aol_card_intent.allowed_latch
          assert not command.enabled and not command.latActive and not command.longActive
          assert sd_state.alertType != 'steerTempUnavailableSilent/warning', 'Unarmed parked startup shows steering warning'
        return
      if scenario == 'route22':
        assert host.CP.openpilotLongitudinalControl
        latch_trace = []
        for _ in range(40):
          state, axis, sd_state, command = step(engaged=True, fault='gas')
          latch_trace.append((steps, host.aol_card_intent.allowed_latch,
            host.aol_card_intent.latch_clear_reason, str(axis.mode), str(axis.faultReason),
            joined.last_native_request, joined.last_permission))
        assert state.gasPressed and state.cruiseState.enabled
        assert host.aol_card_intent.allowed_latch, ('Gas-held first engagement erased AOL', latch_trace)
        assert command.latActive and not command.longActive
        for _ in range(40):
          state, axis, sd_state, command = step(engaged=True)
        assert command.latActive and command.longActive and host.aol_card_intent.allowed_latch
        # Driver releases a small steering override while AOL remains armed.
        # Every real packed angle command must stay active and pass native TX.
        for pressed in (True, False):
          lateral = []
          for _ in range(70):
            state, axis, sd_state, command = step(engaged=True, steering=pressed)
            assert command.latActive and host.aol_card_intent.allowed_latch
            lateral.extend((address, data) for address, data in joined.last_lateral_packets if address in (0x3D3, 0x3D6))
          assert lateral
          for address, data in lateral:
            mode = ((data[4] >> 2) & 7) if address == 0x3D3 else ((data[0] >> 4) & 7)
            assert mode == 1, ('Unprompted angle reset after steering override', pressed, address, data.hex())
        brake_seen = False
        for _ in range(30):
          state, axis, sd_state, command = step(brake=True)
          assert host.aol_card_intent.allowed_latch, ('Brake erased first-engagement AOL', transition_trace[-12:])
          assert command.latActive, transition_trace[-12:]
          # The simulated 50 Hz brake source need not transmit on the first 100 Hz tick.
          # LONG must be off once either native or host has actually observed that pedal.
          brake_seen |= state.brakePressed
          if state.brakePressed or not joined.last_permission & 2:
            assert not command.longActive, transition_trace[-12:]
          assert str(axis.faultReason) == 'none'
          assert sd_state.alertType != 'controlsMismatch/immediateDisable'
        assert brake_seen and not command.longActive
        for _ in range(30):
          state, axis, sd_state, command = step()
        assert command.latActive and not command.longActive and host.aol_card_intent.allowed_latch
        for _ in range(30):
          state, axis, sd_state, command = step(fault=getattr(monkeypatch, 'ford_route22_final_fault', 'heartbeat'))
        assert not command.latActive and not command.longActive
        if getattr(monkeypatch, 'ford_route22_final_fault', 'heartbeat') == 'source-loss':
          # This injection withholds native yaw RX while the host parser still receives it.
          # Host CAN validity must never substitute for missing native permission.
          assert joined.last_permission == 0 and state.canValid
        else:
          assert not host.aol_card_intent.allowed_latch
        assert active_lat_frames > 0 and active_long_frames > 0
        return
      if scenario == 'route49':
        assert not host.CP.openpilotLongitudinalControl
        trace = []
        original_intent_update = host.aol_card_intent.update

        def trace_first_main(cs, **kwargs):
          before = host.aol_card_intent
          snapshot = {'tick': steps, 'main': cs.cruiseState.available,
                      'ordinary': cs.cruiseState.enabled, 'fault': kwargs.get('fault_active'),
                      'native_rejection': kwargs.get('native_rejection_ns'),
                      'idle_ready': before.main_idle_transition_qualified,
                      'pending_before': before.main_pending_ns,
                      'axis_fault': str(host.sm['aolAxisState'].faultReason)}
          result = original_intent_update(cs, **kwargs)
          snapshot.update(armed=before.allowed_latch, reason=before.latch_clear_reason,
                          pending_after=before.main_pending_ns, inhibit=before._fault_inhibit)
          trace.append(snapshot)
          return result

        local.setattr(host.aol_card_intent, 'update', trace_first_main)
        # Recorded first MAIN edge is raw0->5, not an already-idle MAIN3 fixture.
        # Existing real Card/SD/native receipts, CAN and clocks remain in charge.
        for _ in range(40):
          state, axis, sd_state, command = step(engaged=True, fault='gas', steering=True)
        assert state.cruiseState.available and state.cruiseState.enabled
        assert command.enabled
        assert host.aol_card_intent.allowed_latch, ('First MAIN0->5 did not arm AOL', trace[:12])
        cancel_seen = False
        for _ in range(10):
          state, axis, sd_state, command = step(engaged=True, fault='gas', cancel=True)
          cancel_seen |= host.CI.CS.cancel_button
          assert host.aol_card_intent.allowed_latch
        assert cancel_seen
        for _ in range(30):
          state, axis, sd_state, command = step(fault='gas')
          assert host.aol_card_intent.allowed_latch
        assert not state.cruiseState.enabled and not command.enabled
        assert command.latActive and joined.last_permission & 1
        for _ in range(20):
          state, axis, sd_state, command = step(brake=True)
          assert host.aol_card_intent.allowed_latch
        for _ in range(30):
          state, axis, sd_state, command = step()
          assert host.aol_card_intent.allowed_latch
        assert not command.enabled and command.latActive and joined.last_permission & 1
        joined.route49_trace = trace
        return
      if scenario == 'route45':
        assert host.CP.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1
        assert not host.CP.openpilotLongitudinalControl
        for _ in range(40):
          state, axis, sd_state, command = step(engaged=True)
        assert command.latActive and host.aol_card_intent.allowed_latch
        for _ in range(100):
          state, axis, sd_state, command = step(fault='gas', steering=True)
        assert state.steeringPressed and state.gasPressed
        # Release pressure without an optional reset pulse. The real EPS
        # restriction below must still withdraw output and retain only intent.
        for _ in range(20):
          state, axis, sd_state, command = step(fault='gas')
          assert host.aol_card_intent.allowed_latch and command.latActive
          for address, data in joined.last_lateral_packets:
            if address == 0x3D6:
              assert (data[0] >> 4) & 7 == 1
        for _ in range(8):
          state, axis, sd_state, command = step(fault='gas')
        for _ in range(77):
          state, axis, sd_state, command = step(fault='temporary-lkas-gas')
          if state.steerFaultTemporary:
            assert not command.latActive and not joined.last_permission & 1
          assert host.aol_card_intent.allowed_latch
        route45_old.update(previous_sd)
        route45_phase[0] = 'released'
        delivery = getattr(monkeypatch, 'ford_route45_delivery', 'paired')
        lost = False
        refusal = getattr(monkeypatch, 'ford_route45_refusal', None)
        for _ in range(34):
          state, axis, sd_state, command = step(fault='temporary-lkas')
          lost |= not host.aol_card_intent.allowed_latch
          assert not command.latActive and not command.longActive and not joined.last_permission & 1
          feedback = host.aol_transport_feedback
          route45_trace.append({'tick': steps, 'latch': host.aol_card_intent.allowed_latch,
            'reason': host.aol_card_intent.latch_clear_reason, 'failed': feedback.failed,
            'event': int(host.sm.logMonoTime['onroadEvents']),
            'axisEvent': int(host.sm['aolAxisState'].faultEventMonoTime),
            'signature': feedback.signature, 'gas': state.gasPressed,
            'permission': joined.last_permission, 'request': joined.last_native_request})
          if delivery != 'never-paired' and refusal is None:
            assert not lost, ('Route45 mixed EPS receipt erased intent', route45_trace[-4:])
        if refusal == 'cancel':
          # Stock-long Ford leaves this button with cruise; its physical native
          # cancellation must still prevent output after the EPS signal recovers.
          assert route45_mixed[0]
          for _ in range(60):
            state, axis, sd_state, command = step()
            assert not command.latActive and not command.longActive and not joined.last_permission & 1
          return
        if refusal is not None:
          assert route45_mixed[0] and lost, 'Physical refusal must disarm despite a retained EPS join'
          assert not command.latActive and not command.longActive
          return
        if delivery == 'never-paired':
          assert lost, 'Unmatched EPS proof must not renew its previous source lease'
        else:
          recovered = False
          for _ in range(60):
            state, axis, sd_state, command = step()
            recovered |= bool(command.latActive and joined.last_permission & 1)
            assert host.aol_card_intent.allowed_latch
          assert recovered, 'Retained selection needs independently earned fresh native recovery'
        for _ in range(30):
          state, axis, sd_state, command = step(main_off=True)
        assert not host.aol_card_intent.allowed_latch and not command.latActive and not command.longActive
        if delivery != 'paired':
          assert route45_mixed[0]
        joined.route45_trace = route45_trace
        return
      if scenario in ('cancel-native-cadence', 'neutral-main-cadence'):
        cancel_gas = 'gas' if getattr(monkeypatch, 'ford_cancel_gas', False) else None
        for _ in range(41):
          state, axis, sd_state, command = step(engaged=True)
        assert command.latActive and host.aol_card_intent.allowed_latch
        if cancel_gas:
          # Settle gas on a real 10Hz receipt before testing the later Cancel.
          while steps % 10 != 9:
            state, axis, sd_state, command = step(engaged=True)
          for gas_tick in range(25):
            state, axis, sd_state, command = step(engaged=True, fault=cancel_gas)
            assert host.aol_card_intent.allowed_latch and command.latActive
            if gas_tick >= 2:
              assert not command.longActive
        for _ in range(12):
          state, axis, sd_state, command = step(engaged=True, cancel=True, fault=cancel_gas)
          assert host.aol_card_intent.allowed_latch, (host.aol_card_intent.latch_clear_reason, transition_trace[-12:])
          assert command.latActive
          if not joined.last_permission & 2:
            assert not command.longActive
        for _ in range(35):
          state, axis, sd_state, command = step(fault=cancel_gas)
          assert host.aol_card_intent.allowed_latch and command.latActive and not command.longActive, transition_trace[-12:]
        if cancel_gas:
          for _ in range(15):
            state, axis, sd_state, command = step()
            assert host.aol_card_intent.allowed_latch and command.latActive and not command.longActive
        for _ in range(30):
          state, axis, sd_state, command = step(main_off=True)
        assert not host.aol_card_intent.allowed_latch and not command.latActive and not command.longActive
        if scenario == 'neutral-main-cadence':
          assert not native.safety.get_controls_allowed() and native.safety.aol_get_permission_mask() == 0
          assert native.safety.safety_fwd_hook(2, 0x18A) == -1
          assert native.safety.safety_fwd_hook(2, 0x3D8) == -1
          assert native.safety.safety_fwd_hook(2, 0x3D6) == 0
          for transition_tick in range(40):
            state, axis, sd_state, command = step(engaged=True, availability_transition=transition_tick < 10)
          assert command.latActive and command.longActive and host.aol_card_intent.allowed_latch
          assert native.safety.aol_get_permission_mask() == 3
        return
      if scenario == 'brake-native-cadence':
        for _ in range(41):
          state, axis, sd_state, command = step(engaged=True)
        assert command.latActive and command.longActive and host.aol_card_intent.allowed_latch
        if getattr(monkeypatch, 'ford_standstill_brake', False):
          for slowing_tick in range(300):
            joined.speed = max(0.0, 15.0 - (slowing_tick + 1) * .05)
            joined.stationary = joined.speed == 0.0
            state, axis, sd_state, command = step(engaged=True)
          for _ in range(120):
            state, axis, sd_state, command = step(engaged=True)
          assert state.standstill and command.latActive and command.longActive
        for _ in range(35):
          state, axis, sd_state, command = step(brake=True)
          assert host.aol_card_intent.allowed_latch, (host.aol_card_intent.latch_clear_reason, transition_trace[-12:])
          assert command.latActive, transition_trace[-12:]
          if state.brakePressed or not joined.last_permission & 2:
            assert not command.longActive
        for _ in range(35):
          state, axis, sd_state, command = step()
          assert host.aol_card_intent.allowed_latch and command.latActive and not command.longActive
        return
      if scenario == 'configuration-parity':
        for _ in range(40):
          state, axis, sd_state, command = step(engaged=True)
        assert command.latActive and host.aol_card_intent.allowed_latch
        assert bool(command.longActive) == joined.expected_long
        for _ in range(30):
          state, axis, sd_state, command = step(engaged=True, fault='gas')
          if getattr(monkeypatch, 'ford_native_first_pedal', False):
            assert command.latActive and host.aol_card_intent.allowed_latch, 'Native-first gas erased lateral intent'
            assert not command.longActive or joined.last_permission & 2
        assert command.latActive and not command.longActive and host.aol_card_intent.allowed_latch
        for _ in range(30):
          state, axis, sd_state, command = step(engaged=True)
        assert command.latActive and bool(command.longActive) == joined.expected_long
        for _ in range(30):
          state, axis, sd_state, command = step(brake=True)
          if getattr(monkeypatch, 'ford_native_first_pedal', False):
            assert command.latActive and host.aol_card_intent.allowed_latch, 'Native-first brake erased lateral intent'
            assert not command.longActive or joined.last_permission & 2
        assert host.aol_card_intent.allowed_latch and command.latActive and not command.longActive
        for _ in range(30):
          state, axis, sd_state, command = step(reverse=True)
        assert host.aol_card_intent.allowed_latch and not command.latActive and not command.longActive, transition_trace[-40:]
        for _ in range(40):
          state, axis, sd_state, command = step()
        assert command.latActive and not command.longActive
        eps_gas = getattr(monkeypatch, 'ford_eps_gas', False)
        if eps_gas:
          # Route 3b: gas starts 40 ms before the 150 ms EPS restriction.
          for _ in range(4):
            state, axis, sd_state, command = step(fault='gas')
            assert host.aol_card_intent.allowed_latch and command.latActive and not command.longActive
        for _ in range(15 if eps_gas else 40):
          state, axis, sd_state, command = step(fault='temporary-eps-gas' if eps_gas else 'temporary-eps')
          if eps_gas:
            assert host.aol_card_intent.allowed_latch, transition_trace[-40:]
            assert not command.longActive
            if state.steerFaultTemporary:
              assert not command.latActive, transition_trace[-40:]
        assert host.aol_card_intent.allowed_latch and not command.latActive, transition_trace[-40:]
        for _ in range(40):
          state, axis, sd_state, command = step(fault='gas' if eps_gas else None)
          if eps_gas:
            assert host.aol_card_intent.allowed_latch, 'Route3b EPS recovery with gas erased AOL intent'
            assert not command.longActive
        assert command.latActive and not command.longActive
        for _ in range(30):
          state, axis, sd_state, command = step(main_off=True)
        assert not host.aol_card_intent.allowed_latch and not command.latActive and not command.longActive
        assert active_lat_frames > 0
        assert bool(active_long_frames) == joined.expected_long
        return
      assert scenario in ('route24', 'route24-mixed-eps', 'route2a', 'route2a-main', 'route2a-main-fault')
      if scenario in ('route24', 'route24-mixed-eps', 'route2a', 'route2a-main', 'route2a-main-fault'):
        mixed_eps = scenario in ('route24-mixed-eps', 'route2a', 'route2a-main', 'route2a-main-fault')
        assert host.CP.flags == 3 and not host.CP.openpilotLongitudinalControl
        for _ in range(30):
          step(fault='gas')
        for _ in range(40):
          state, axis, sd_state, command = step(engaged=True, fault='gas')
        assert state.cruiseState.enabled and state.gasPressed and host.aol_card_intent.allowed_latch
        for _ in range(40):
          state, axis, sd_state, command = step()
          assert host.aol_card_intent.allowed_latch, 'Route24 normal stock disengage after gas engagement erased AOL'
        assert axis.lateralActive and command.latActive and not command.longActive and not sd_state.enabled
        for _ in range(40):
          step(engaged=True)
        for _ in range(30):
          state, axis, sd_state, command = step(brake=True)
        assert host.aol_card_intent.allowed_latch and command.latActive and not command.longActive
        for _ in range(20):
          state, axis, sd_state, command = step(brake=mixed_eps)
        assert state.brakePressed == mixed_eps and command.latActive and host.aol_card_intent.allowed_latch
        for _ in range(60):
          state, axis, sd_state, command = step(brake=mixed_eps, fault='temporary-eps')
          assert host.aol_card_intent.allowed_latch
          if state.steerFaultTemporary:
            assert not command.latActive and not command.longActive
        for _ in range(40):
          state, axis, sd_state, command = step(brake=mixed_eps)
        assert state.brakePressed == mixed_eps and command.latActive and host.aol_card_intent.allowed_latch and not command.longActive
        for _ in range(20):
          state, axis, sd_state, command = step()
        assert not state.brakePressed and command.latActive and host.aol_card_intent.allowed_latch
        for _ in range(30):
          state, axis, sd_state, command = step(reverse=True)
        assert host.aol_card_intent.allowed_latch and not command.latActive and not command.longActive
        for _ in range(40):
          state, axis, sd_state, command = step()
        assert command.latActive and not command.longActive and host.aol_card_intent.allowed_latch
        if scenario == 'route2a':
          # The previous native session has been withdrawn with MAIN still on,
          # matching the route's new engagement after its earlier host failure.
          for _ in range(30):
            state, axis, sd_state, command = step(fault='heartbeat')
          assert not host.aol_card_intent.allowed_latch and not command.latActive and not command.longActive
          for _ in range(30):
            step()
          for _ in range(40):
            state, axis, sd_state, command = step(engaged=True, fault='gas')
          assert state.cruiseState.enabled and command.latActive and host.aol_card_intent.allowed_latch
          for _ in range(40):
            state, axis, sd_state, command = step()
            assert host.aol_card_intent.allowed_latch, 'Route2a new ordinary engagement lost retained native token'
          assert command.latActive and axis.lateralActive and not command.longActive and not sd_state.enabled
        for _ in range(30):
          state, axis, sd_state, command = step(main_off=True)
        assert not state.cruiseState.available
        assert not host.aol_card_intent.allowed_latch and not command.latActive and not command.longActive
        if scenario in ('route2a-main', 'route2a-main-fault'):
          failed_edge = scenario == 'route2a-main-fault'
          for transition_tick in range(30):
            state, axis, sd_state, command = step(fault='rx-quality' if failed_edge else None, availability_transition=transition_tick < 10)
          if failed_edge:
            for _ in range(40):
              state, axis, sd_state, command = step()
          assert state.cruiseState.available and not state.cruiseState.enabled
          assert host.aol_card_intent.allowed_latch == (not failed_edge)
          assert command.latActive == (not failed_edge) and not command.longActive
          for _ in range(30):
            state, axis, sd_state, command = step(main_off=True)
          assert not host.aol_card_intent.allowed_latch and not command.latActive and not command.longActive
        return
  finally:
    native.safety = safety
    case.doCleanups()
