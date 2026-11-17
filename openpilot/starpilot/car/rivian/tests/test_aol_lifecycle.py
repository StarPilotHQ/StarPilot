"""Synthetic acquisition only; actual registered factory/Card/SD/Controls/native CAN chain."""

import json
import os
import threading
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.rivian.values import CAR
from opendbc.safety.tests.libsafety import libsafety_py


class Chassis:
  """Actual DBC packing. Native checked rows use declared 50/100 Hz cadence.

  Unchecked ancillary snapshots are synthesized at 100 Hz, as in the frozen
  parser fixture; this does not claim a recorded physical bus schedule.
  """

  def __init__(self, radar_available=False):
    self.radar_available = radar_available
    self.packer = CANPacker('rivian_primary_actuator')
    self.counters = defaultdict(int)

  def frames(self, tick, *, ack=False, fault=False, unavailable=False, gear=4, gas=False, feature=0, cancel=False, speed=18.0):
    rows: list[tuple[str, int, int, str | None, dict[str, float]]] = [
      ('ESP_Status', 0, 2, 'ESP_Status_Counter', {'ESP_Vehicle_Speed': speed * 3.6, 'ESP_Vehicle_Speed_Q': 1}),
      (
        'VDM_PropStatus',
        0,
        2,
        'VDM_PropStatus_Counter',
        {'VDM_VehicleSpeed': speed * 3.6, 'VDM_VehicleSpeedQ': 1, 'VDM_Prndl_Status': gear, 'VDM_AcceleratorPedalPosition': 1 if gas else 0},
      ),
      ('iBESP2', 0, 2, 'iBESP2_AliveCounter', {'iBESP2_BrakePedalApplied': False, 'iBESP2_BrakePedalApplied_Q': 1}),
      (
        'EPAS_SystemStatus',
        0,
        1,
        'EPAS_SystemStatus_Counter',
        {'EPAS_TorsionBarTorque': 0.0, 'H_CAN_EPSS_ToiActive': ack, 'H_CAN_EPSS_ToiFlt': fault, 'H_CAN_EPS_ToiUnavailable': unavailable},
      ),
      ('EPAS_AdasStatus', 0, 1, 'EPAS_AdasStatus_Counter', {'EPAS_InternalSas': 1.0, 'EPAS_EacErrorCode': 0, 'EPAS_EacStatus': 1}),
      ('VDM_AdasSts', 0, 1, 'VDM_AdasStatus_Counter', {'VDM_UserAdasRequest': int(cancel)}),
      ('RCM_Status', 0, 1, None, {'RCM_Status_IND_WARN_BELT_DRIVER': 0}),
      ('ACM_Status', 2, 1, 'ACM_Status_Counter', {'ACM_FeatureStatus': feature}),
      ('ACM_lkaHbaCmd', 2, 1, 'ACM_lkaHbaCmd_Counter', {}),
      ('ACM_AebRequest', 2, 1, None, {}),
      ('Cluster', 1, 1, None, {'Cluster_Unit': 0, 'Cluster_VehicleSpeed': speed * 3.6}),
      ('IndicatorLights', 1, 1, None, dict.fromkeys(('RearDriverDoor', 'FrontPassengerDoor', 'DriverDoor', 'RearPassengerDoor'), 2)),
      ('SCCM_WheelTouch', 0, 1, 'SCCM_WheelTouch_Counter', {}),
    ]
    result = []
    for name, bus, period, counter, values in rows:
      if (tick - 1) % period:
        continue
      if counter:
        values[counter] = self.counters[name] % 15
        self.counters[name] += 1
      result.append(self.packer.make_can_msg(name, bus, values))
    if self.radar_available and (tick - 1) % 5 == 0:
      from opendbc.car.rivian.tests.test_radar_reachability import bank
      result.extend(bank())
    return result


@pytest.mark.parametrize('alpha', [False, True], ids=['stock', 'debug-alpha'])
def test_actual_rivian_aol_engage_brake_reverse_fault_recovery(alpha, monkeypatch, tmp_path, radar_available=False):
  from opendbc.safety.tests.common import make_msg
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car import card as card_module
  from openpilot.selfdrive.car.card import Car
  from openpilot.starpilot.aol.tests.test_ioniq_availability import (
    IoniqAvailabilityTests,
  )
  from openpilot.starpilot.aol.wire import (
    PandaSlot,
    SafetyState,
    decode_intent,
    encode_safety,
  )
  from openpilot.starpilot.lateral.learning_preferences import KEY, replace_preference

  original_environment = {key: os.environ.get(key) for key in ('REPLAY', 'SIMULATION')}
  monkeypatch.setenv('REPLAY', '1')
  monkeypatch.setenv('SIMULATION', '0')
  monkeypatch.setattr(card_module, 'REPLAY', False)
  trace, previous_sd, received, queued = [], {}, {}, []
  now, tick = [10_000_000_000], 0
  with OpenpilotPrefix(shared_download_cache=True):
    saved = Params()
    for key, value in [('OpenpilotEnabledToggle', True), ('AlwaysOnLateral', True), ('AlphaLongitudinalEnabled', alpha), ('IsReleaseBranch', False)]:
      saved.put_bool(key, value, block=True)
    fp = gen_empty_fingerprint()
    fp[0][0x321] = 7
    if radar_available:
      from opendbc.car.rivian.tests.test_radar_reachability import fingerprint
      fp = fingerprint()
    factory = interfaces[CAR.RIVIAN_R1]
    cp = factory.get_params(CAR.RIVIAN_R1, fp, [], alpha, False, False)
    saved.put(KEY, replace_preference(None, cp, False), block=True)
    card = Car(CI=factory(cp), RI=factory.RadarInterface(cp))
    assert card.CP.flags == 0 and card.CP.alternativeExperience == 32
    assert card.aol_qualified and card.aol_card_intent is not None
    assert not card.ci_initialized and not Path(saved.get_param_path('ControlsReady')).exists()
    card.CI.update([])
    case = IoniqAvailabilityTests(methodName='runTest')
    io_contexts = ExitStack()
    safety = libsafety_py.libsafety
    try:
      sd, controls, drive = case._authority_callers(False, cp=card.CP.as_reader().as_builder(), genuine_events=True)
      assert sd.CP.to_dict() == card.CP.to_dict() == controls.CP.to_dict()
      # Exercise live Controls decision time; replay source-time selection can
      # round Card's float-produced source stamp one ns before exact axis time.
      monkeypatch.setenv('REPLAY', '0')
      safety.init_tests()
      safety.set_alternative_experience(32)
      assert safety.set_safety_hooks(structs.CarParams.SafetyModel.rivian, int(alpha)) == 0
      monkeypatch.setattr(card_module.time, 'monotonic_ns', lambda: now[0])
      monkeypatch.setattr(card_module.time, 'monotonic', lambda: now[0] / 1e9)
      monkeypatch.setattr(card_module.time, 'clock_gettime_ns', lambda clock: now[0])
      monkeypatch.setattr(card_module.messaging, 'drain_sock_raw', lambda *a, **kw: list(queued))
      io_contexts.enter_context(patch.object(card.sm, 'update', lambda *a: None))
      # The reused SD helper owns a clock mock too. Bind each NEW producer event
      # to this acquisition clock, as the qualified Mazda full-caller harness
      # does, while held messages keep their original serialized source stamp.
      create_event = messaging.new_message
      def new_event(service=None, size=None, **kwargs):
        kwargs.setdefault('logMonoTime', now[0])
        return create_event(service, size, **kwargs)
      monkeypatch.setattr(messaging, 'new_message', new_event)

      send = card.pm.send

      def capture(service, event):
        raw = event if isinstance(event, bytes) else event.to_bytes()
        received[service] = messaging.log_from_bytes(raw).as_builder()
        send(service, raw)

      io_contexts.enter_context(patch.object(card.pm, 'send', capture))
      chassis = Chassis(radar_available)
      ready_observed = False
      metadata = {}

      def step(*, feature=0, brake=False, gas=False, gear=4, fault=False, cancel=False, speed=18.0):
        nonlocal tick, ready_observed
        tick += 1
        now[0] = 10_000_000_000 + tick * 10_000_000
        frames = chassis.frames(tick, feature=feature, ack=True, gas=gas, gear=gear, fault=fault, cancel=cancel, speed=speed)
        # Brake is a physical independently checksummed chassis source.
        if (tick - 1) % 2 == 0 and brake:
          frames = [(a, d, b) for a, d, b in frames if a != 0x38F]
          frames.append(
            chassis.packer.make_can_msg(
              'iBESP2', 0, {'iBESP2_AliveCounter': (chassis.counters['iBESP2'] - 1) % 15, 'iBESP2_BrakePedalApplied': True, 'iBESP2_BrakePedalApplied_Q': 1}
            )
          )
        safety.set_timer(now[0] // 1000)
        prior_axis = previous_sd.get('aolAxisState')
        prior_state = previous_sd.get('selfdriveState')
        lat = bool(prior_axis is not None and prior_axis.aolAxisState.desiredLateral)
        long = bool(prior_axis is not None and prior_axis.aolAxisState.desiredLongitudinal)
        heartbeat = lat or long or bool(prior_state is not None and prior_state.selfdriveState.enabled)
        prior_intent = received.get('aolIntentWire')
        armed = decode_intent(prior_intent.aolIntentWire) if prior_intent is not None else None
        # Match pandad's retained-arm heartbeat input from the original Card wire.
        # This conveys no actuator request and cannot arm a native token.
        if prior_axis is not None and armed is not None:
          axis = prior_axis.aolAxisState
          stamp = int(prior_intent.logMonoTime)
          retained = bool(armed.settingsQualified and armed.lateralArmed and
            armed.producerSessionId == axis.intentProducerSessionId and
            armed.carStateLogMonoTime == stamp == armed.observedMonoTime and
            0 < stamp <= now[0] <= armed.validUntilMonoTime and now[0] - stamp <= 30_000_000 and
            abs(stamp - axis.sourceCarStateMonoTime) <= 30_000_000)
          heartbeat |= retained

        safety.set_aol_test_heartbeat(heartbeat)
        for a, d, b in frames:
          assert safety.safety_rx_hook(make_msg(b, a, dat=d))
        safety.safety_tick()
        safety.aol_set_host_request(int(lat) | (int(long) << 1))
        request = safety.aol_get_request_mask()
        permission = safety.aol_get_permission_mask()
        events = []
        if (tick - 1) % 50 == 0:
          device = messaging.new_message('deviceState', valid=True, logMonoTime=now[0])
          device.deviceState.started = True
          device.deviceState.startedMonoTime = 9_000_000_000
          events.append(device)
        if (tick - 1) % 10 == 0:
          metadata['panda'] = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now[0])
          panda = metadata['panda'].pandaStates[0]
          panda.safetyModel = card.CP.safetyConfigs[0].safetyModel
          panda.safetyParam = int(alpha)
          panda.alternativeExperience = 32
          panda.controlsAllowed = safety.get_controls_allowed()
          panda.safetyRxChecksInvalid = not safety.safety_config_valid()
          events.append(metadata['panda'])
        if 'onroadEvents' not in previous_sd:
          events.append(messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now[0]))
        for service in ('selfdriveState', 'aolAxisState', 'onroadEvents'):
          if service in previous_sd:
            events.append(previous_sd[service].as_builder())
        wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now[0])
        wire.aolSafetyWire = encode_safety(
          SafetyState(
            1,
            True,
            now[0],
            now[0] + 30_000_000,
            int(card.CP.safetyConfigs[0].safetyModel.raw),
            int(alpha),
            bool(permission & 1),
            bool(permission & 2),
            bool(request & 1),
            bool(request & 2),
            'rivian-native-test',
            card.aol_axis_session(),
            now[0],
            (PandaSlot(0, 'rivian-native-test', 33, int(alpha), 32, safety.get_controls_allowed(), not safety.safety_config_valid(), False, 0),),
          )
        )
        events.append(wire)
        card.sm.update_msgs(now[0] / 1e9, [e.as_reader() for e in events])
        can_source = messaging.log_from_bytes(card_module.can_list_to_can_capnp(frames)).as_builder()
        can_source.logMonoTime = now[0]  # synthetic CAN producer's original acquisition time
        queued[:] = [can_source.to_bytes()]
        state, radar = card.state_update(defer_radar=not radar_available)
        card.state_publish(state, radar if radar_available else None)
        axis, host, command = drive(
          tick,
          car_state_message=received['carState'],
          car_output_message=received['carOutput'],
          authority_message=received.get('starpilotCarState'),
          intent_message=received['aolIntentWire'],
          allowed=bool(permission & 1),
          long_allowed=bool(permission & 2),
          native_request_lat=bool(request & 1),
          native_request_long=bool(request & 2),
          panda_controls_allowed=safety.get_controls_allowed(),
          panda_state_message=metadata['panda'],
        )
        previous_sd.update(sd.pm.messages)
        # Later receiver instant; original Controls source stamp is preserved.
        now[0] += 1
        card.sm.update_msgs(now[0] / 1e9, [controls.pm.messages['carControl']])
        received.pop('sendcan', None)
        card.controls_update(state, card.sm['carControl'])
        if not ready_observed and card.ci_initialized:
          done = threading.Event()
          values = []

          def observe():
            values.append(saved.get_bool('ControlsReady', block=True))
            done.set()

          reader = threading.Thread(target=observe, daemon=True)
          reader.start()
          assert done.wait(2)
          reader.join(2)
          assert values == [True] and not reader.is_alive()
          ready_observed = True
        tx = []
        if 'sendcan' in received:
          for frame in received['sendcan'].sendcan:
            tx.append(bool(safety.safety_tx_hook(make_msg(frame.src, frame.address, dat=frame.dat))))
          assert all(tx)
        row = {
          'tick': tick,
          'feature': feature,
          'gear': str(state.gearShifter),
          'brake': state.brakePressed,
          'temporary': state.steerFaultTemporary,
          'native_allowed': safety.get_controls_allowed(),
          'request': request,
          'permission': permission,
          'latch': card.aol_card_intent.allowed_latch,
          'latch_reason': card.aol_card_intent.latch_clear_reason,
          'sd_axis': axis.to_dict(),
          'events': [e.to_dict() for e in card.sm['onroadEvents']],
          'host_enabled': host.enabled,
          'axis_lat': axis.lateralActive,
          'cc_lat': command.latActive,
          'cc_long': command.longActive,
          'torque': float(card.last_actuators_output.torqueOutputCan),
          'tx': tx,
          'cs_ns': int(controls.sm.logMonoTime['carState']),
          'axis_ns': int(controls.sm.logMonoTime['aolAxisState']),
          'decision_ns': now[0] - 1,
          'axis_ack': bool(axis.nativeAcknowledged),
          'native_witness': controls.flm_native_witness is not None,
        }
        trace.append(row)
        return row

      for _ in range(120):
        step()
      assert not card.aol_card_intent.allowed_latch
      for _ in range(35):
        row = step(feature=1)
      assert row['latch'] and row['host_enabled'] and row['cc_lat'] and abs(row['torque']) > 0
      # Brake is independent 50 Hz CAN. Observe its actual parsed press before
      # dropping the 100 Hz ACM feature, avoiding a synthetic cancel-before-brake.
      for _ in range(2):
        row = step(feature=1, brake=True)
      for _ in range(18):
        row = step(brake=True)
      assert not row['native_allowed'] and row['permission'] == 1
      assert row['latch'] and row['axis_lat'] and row['cc_lat'] and not row['cc_long']
      # Exercise the genuine release/Drive producer interval before a separate
      # Reverse maneuver; no stale pedal USER event is reclassified as gear.
      for _ in range(110):
        row = step()
        assert row['latch'] and row['cc_lat'] and not row['cc_long']
      # An accelerator press cannot reactivate the withdrawn longitudinal axis.
      for _ in range(10):
        row = step(gas=True)
      assert row['latch'] and row['permission'] == 1 and row['cc_lat'] and not row['cc_long']
      for _ in range(12):
        row = step()
      assert row['latch'] and row['cc_lat'] and not row['cc_long']
      for _ in range(25):
        row = step(speed=0.0)
      assert row['latch'] and row['permission'] == 1 and not row['cc_lat'] and not row['cc_long'] and row['torque'] == 0
      for _ in range(12):
        row = step()
      assert row['latch'] and row['permission'] == 1 and row['cc_lat'] and not row['cc_long']
      for _ in range(4):
        row = step(gear=2)
      assert row['latch'] and not row['cc_lat'] and row['permission'] == 0 and row['torque'] == 0
      for _ in range(12):
        row = step()
      assert row['latch'] and row['permission'] == 1 and row['cc_lat'] and not row['cc_long']
      for _ in range(4):
        row = step(fault=True)
      assert row['temporary'] and row['latch'] and row['permission'] == 0 and row['torque'] == 0
      for _ in range(15):
        row = step()
      assert row['latch'] and row['permission'] == 1 and row['cc_lat'] and abs(row['torque']) > 0
      assert not row['cc_long']
      # Default physical UP_1 cancels the retained session. Availability alone
      # cannot manufacture a new engagement after that explicit action.
      for _ in range(3):
        row = step(cancel=True)
      assert not row['latch'] and not row['cc_lat'] and not row['cc_long'] and row['torque'] == 0
      for _ in range(8):
        row = step()
      assert not row['latch'] and not row['cc_lat'] and row['permission'] == 0
      assert ready_observed
    finally:
      tmp_path.joinpath('aol-' + str(alpha) + '.json').write_text(json.dumps(trace, indent=2) + '\n')
      safety.init_tests()
      safety.set_alternative_experience(0)
      safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
      io_contexts.close()
      monkeypatch.undo()
      case.doCleanups()
      card.vehicle_startup.close()
      # The helper restores its own environment snapshot after clock cleanup.
      # Preserve the surrounding suite's original replay/simulation settings.
      for key, value in original_environment.items():
        if value is None:
          os.environ.pop(key, None)
        else:
          os.environ[key] = value
