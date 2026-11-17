"""Cold GM MAIN through actual parser/Card/SelfdriveD/Controls/native RX and TX.

Model/readiness and receive transport are bench fixtures; ECU I/O is skipped.
Neither ordinary controls_allowed nor an active steering command is seeded.
"""

import pytest
from contextlib import ExitStack
from unittest.mock import patch
from opendbc.car.gm.values import DBC
from opendbc.car import Bus, structs
from opendbc.can import CANPacker
from openpilot.starpilot.aol.tests.test_gm_startup_main import parsed_packets, cases
from opendbc.safety.tests.libsafety import libsafety_py as native
from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_gm as gm, test_ioniq_availability as fixture
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.wire import SafetyState, PandaSlot, encode_safety
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed


@pytest.mark.parametrize('index', range(8))
@pytest.mark.parametrize('main_action', [0, 9])
def test_cold_park_main_waits_without_losing_intent(index, main_action):
  _exercise_parked_main(index, main_action)


def _exercise_parked_main(index, main_action, *, below_engage=False, cp=None):
  cp = list(cases())[index] if cp is None else cp
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, controls, step = case._authority_callers(False, cp=cp, genuine_events=True)
    sd.params.put('MainCruiseButtonControl', main_action, block=True)
    sd.aol_settings = read_settings(sd.params)
    # The initial real Controls result is disabled because no authority wire exists.
    # This boot-neutral fixture establishes no Panda grant or active output.
    feed(controls, 10_000_000_000, 0, active=False, enabled=False)
    boot, boot_log = controls.state_control()
    assert not boot.latActive and not boot.longActive
    controls.publish(boot, boot_log)
    card = gm.TestGmAol.card(cp, sd.params)
    cleanup.callback(card.vehicle_startup.close)
    card.ci_initialized = True
    card.car_params_published = True
    if getattr(card, 'volt_cc_selected', False):
      card.volt_cc_boot_offset_ns = 0
      card.volt_cc_source_floor_ns = 9_000_000_000
    card.sm.frame = 1
    assert card.CP.to_dict() == sd.CP.to_dict() == controls.CP.to_dict()
    original_pm = card.pm
    captured = {}
    intent_queue = []
    companion_queue = []
    original_optional = messaging.recv_one_or_none

    def deliver(sock):
      if sd.steering_companion is not None and sock is sd.steering_companion.sock:
        return companion_queue.pop(0) if companion_queue else None
      if sd.aol_intent_companion is not None and sock is sd.aol_intent_companion.sock:
        return intent_queue.pop(0) if intent_queue else None
      return original_optional(sock)

    cleanup.enter_context(patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one_or_none', side_effect=deliver))

    class Tee:
      def send(self, service, message):
        original_pm.send(service, message)
        event = messaging.log_from_bytes(message) if isinstance(message, bytes) else message.as_reader()
        captured[service] = event.as_builder()
        if service == 'aolIntentWire':
          intent_queue.append(event)
        if service == 'starpilotCarState':
          companion_queue.append(event)

    card.pm = Tee()
    sd_receive = sd.sm.update_msgs
    last_sd_control_stamp = 0

    def receive_sd(stamp, rows):
      nonlocal last_sd_control_stamp
      # Deliver the original last published Controls envelope without renewing its source.
      published = controls.pm.messages.get('carControl')
      rows = [r for r in rows if r.which() != 'carControl']
      if published is not None and published.logMonoTime != last_sd_control_stamp:
        rows.append(published)
        last_sd_control_stamp = int(published.logMonoTime)
      return sd_receive(stamp, rows)

    cleanup.enter_context(patch.object(sd.sm, 'update_msgs', side_effect=receive_sd))
    safety = native.libsafety
    safety.init_tests()
    safety.set_alternative_experience(cp.alternativeExperience)
    assert safety.set_safety_hooks(4, cp.safetyConfigs[0].safetyParam) == 0
    card_receive = card.sm.update_msgs
    # Establish actual neutral producer cadence before Card publishes carOutput.
    card_receive = card.sm.update_msgs
    card_receive(10.0, [controls.pm.messages['carControl']])
    boot_rows = []
    with patch.object(controls.sm, 'update_msgs', side_effect=lambda stamp, rows: boot_rows.extend(rows)):
      feed(controls, 10_010_000_000, 1, active=False, enabled=False)
    controls.sm.update_msgs(10.01, boot_rows)
    clocked = messaging.new_message
    with patch(
      'openpilot.selfdrive.controls.controlsd.messaging.new_message',
      side_effect=lambda *args, **kwargs: clocked(*args, **dict(kwargs, logMonoTime=10_010_000_000)),
    ):
      neutral, neutral_log = controls.state_control()
      assert not neutral.latActive and not neutral.longActive
      controls.publish(neutral, neutral_log)
    card_receive(10.01, [controls.pm.messages['carControl']])
    last_control_stamp = int(controls.pm.messages['carControl'].logMonoTime)
    delivered = {}

    def wire_batch(now):
      mask = safety.aol_get_permission_mask()
      request = safety.aol_get_request_mask()
      invalid = not safety.safety_config_valid()
      relay = safety.get_relay_malfunction()
      p = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now)
      slot = p.pandaStates[0]
      slot.safetyModel = 4
      slot.safetyParam = cp.safetyConfigs[0].safetyParam
      slot.alternativeExperience = cp.alternativeExperience
      slot.controlsAllowed = safety.get_controls_allowed()
      slot.safetyRxChecksInvalid = invalid
      slot.faults = ['relayMalfunction'] if relay else []
      w = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now)
      w.aolSafetyWire = encode_safety(
        SafetyState(
          1,
          True,
          now,
          now + 200_000_000,
          4,
          cp.safetyConfigs[0].safetyParam,
          bool(mask & 1),
          bool(mask & 2),
          bool(request & 1),
          bool(request & 2),
          'authority-panda',
          sd.aol_session_id,
          now,
          (
            PandaSlot(
              0,
              'authority-panda',
              4,
              cp.safetyConfigs[0].safetyParam,
              cp.alternativeExperience,
              safety.get_controls_allowed(),
              invalid,
              False,
              1 if relay else 0,
            ),
          ),
        )
      )
      return p.as_reader(), w.as_reader()

    def receive_card(now):
      nonlocal last_control_stamp
      rows = list(wire_batch(now))
      if getattr(card, 'volt_cc_selected', False):
        device = messaging.new_message('deviceState', valid=True, logMonoTime=now)
        device.deviceState.started = True
        device.deviceState.startedMonoTime = 9_000_000_000
        rows.append(device.as_reader())
      rows += [sd.pm.messages[k] for k in ('aolAxisState', 'selfdriveState', 'onroadEvents') if k in sd.pm.messages]
      published = controls.pm.messages['carControl']
      if published.logMonoTime != last_control_stamp:
        rows.append(published)
        last_control_stamp = int(published.logMonoTime)
      unique = []
      for event in rows:
        name = event.which()
        key = (int(event.logMonoTime), event.as_builder().to_bytes())
        if delivered.get(name) != key:
          unique.append(event)
          delivered[name] = key
      card_receive(now / 1e9, unique)

    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])

    def physical(tick, stamp, driving):
      speed = max(4.0, cp.minSteerSpeed + 2.0)
      if below_engage:
        speed = cp.minSteerSpeed + 0.05
        assert speed < cp.minEnableSpeed
      else:
        speed = max(speed, cp.minEnableSpeed + 2.0)
      packets = parsed_packets(cp, packer, stamp, tick, speed if driving else 0.0, True)
      from opendbc.car.gm.values import CAR, is_volt_cc_f1_profile
      if is_volt_cc_f1_profile(cp):
        packets = [f for f in packets if f[0] != 0xBE]
        packets.append(packer.make_can_msg('EBCMBrakePedalPosition', 0, {}))
      if cp.carFingerprint == CAR.CHEVROLET_VOLT_CC and tick in (320, 321):
        button = packer.make_can_msg('ASCMSteeringButton', 0,
          {'ACCButtons': 3 if tick == 320 else 1, 'RollingCounter': tick % 4})
        packets = [f for f in packets if f[0] != button[0]] + [button]
      packets = [f for f in packets if f[0] not in (0x1F5, 0x12A, 0x184, 0x17D)]
      if cp.pcmCruise and cp.networkLocation == structs.CarParams.NetworkLocation.fwdCamera:
        address = packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {})[0]
        packets = [f for f in packets if not (f[0] == address and f[2] == 2)]
        # Factory camera reports adaptive standby, not non-adaptive cruise mode.
        packets.append(packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 2}))
      packets += [
        packer.make_can_msg('ESPStatus', 0, {'TractionControlOn': 1}),
        packer.make_can_msg('ECMPRDNL2', 0, {'PRNDL2': 4 if driving else 1}),
        packer.make_can_msg('BCMDoorBeltStatus', 0, {'LeftSeatBelt': int(driving)}),
        packer.make_can_msg('PSCMStatus', 0, {'LKATorqueDeliveredStatus': int(driving)}),
      ]
      return packets

    for warm in range(100):
      stamp = 9_000_000_000 + warm * 10_000_000
      safety.set_timer(stamp // 1000)
      safety.set_aol_test_heartbeat(True)
      safety.aol_set_host_request(0)
      packets = physical(warm, stamp, False)
      for a, data, bus in packets:
        safety.safety_fwd_hook(bus, a)
        assert safety.safety_rx_hook(native.make_CANPacket(a, bus, data))
      card.CI.update([(stamp, packets)])
      safety.safety_tick()
    assert safety.safety_config_valid() and safety.aol_get_permission_mask() == 0
    tx = 0
    active = 0
    for tick in range(1, 581 if below_engage and cp.carFingerprint == "CHEVROLET_VOLT_CC" else 381):
      now = 10_020_000_000 + (tick - 1) * 10_000_000
      driving = tick > 300
      safety.set_timer(now // 1000)
      safety.set_aol_test_heartbeat(True)
      packets = physical(tick + 100, now, driving)
      for a, data, bus in packets:
        safety.safety_fwd_hook(bus, a)
        assert safety.safety_rx_hook(native.make_CANPacket(a, bus, data))
      cs = card.CI.update([(now, packets)])
      safety.safety_tick()
      assert cs.canValid and not cs.canTimeout
      card_receive(now / 1e9, [])
      fault = card.aol_disarming_fault(cs, int(card.sm.logMonoTime['onroadEvents']), now)
      card.aol_card_intent.update(cs, fault_active=fault, now_ns=now)
      card.observe_aol_calibration(cs, now, False)
      allowed = card.aol_card_intent.output(cs)[0]
      safety.aol_set_host_request(int(allowed))
      mask = safety.aol_get_permission_mask()
      message = messaging.new_message
      with patch(
        'openpilot.selfdrive.car.card.messaging.new_message',
        side_effect=lambda *args, _clock=message, _now=now, **kwargs: _clock(*args, **dict(kwargs, logMonoTime=_now)),
      ):
        card.state_publish(cs, None)
      axis, _, cc = step(
        tick,
        state_stamp=now,
        offset_ns=now - 10_000_000_000 - tick * 10_000_000,
        car_state_message=captured['carState'],
        intent_message=captured['aolIntentWire'],
        car_output_message=captured['carOutput'],
        car_output_valid=captured['carOutput'].valid,
        allowed=bool(mask & 1),
        long_allowed=bool(mask & 2),
        native_request_lat=allowed,
        native_request_long=False,
        panda_controls_allowed=safety.get_controls_allowed(),
        authority_message=captured.get('starpilotCarState'),
      )
      receive_card(now)
      assert not cc.enabled and not cc.longActive and not safety.get_controls_allowed()
      if not driving:
        assert str(cs.gearShifter) == 'park' and cs.seatbeltUnlatched and cs.standstill
        assert not axis.lateralActive and not cc.latActive and safety.aol_get_request_mask() == 0 and mask == 0
        assert str(axis.faultReason) != 'critical', (tick, axis.to_dict(), sd.events.names)
      assert not card.aol_disarming_fault(cs, int(card.sm.logMonoTime['onroadEvents']), now)
      assert card.aol_card_intent.allowed_latch and not card.aol_card_intent._main_cycle_required
      command = controls.pm.messages['carControl']
      captured.pop('sendcan', None)
      with (
        patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now + 2),
        patch('openpilot.selfdrive.car.card.time.monotonic', return_value=(now + 2) / 1e9),
        patch('openpilot.selfdrive.car.card.time.clock_gettime_ns', return_value=now + 2),
      ):
        card.controls_update(cs, command.carControl)
      emitted = captured['sendcan']
      assert emitted.valid
      for frame in emitted.sendcan:
        assert safety.safety_tx_hook(native.make_CANPacket(frame.address, frame.src, frame.dat))
        tx += 1
        if frame.address == 0x180:
          if not driving:
            assert not frame.dat[0] & 8
          else:
            active += bool(frame.dat[0] & 8)
    assert active > 0 and tx > 0 and card.aol_card_intent.allowed_latch
