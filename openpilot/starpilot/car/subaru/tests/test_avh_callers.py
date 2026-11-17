"""Actual automatic Subaru factory/parser/Card/SD/Controls/native/CPP negotiation."""

import json
import time
from unittest.mock import patch
import pytest
from opendbc.can import CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.subaru.values import CAR, DBC
from opendbc.car.subaru.fingerprints import FW_VERSIONS
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.pandad import can_capnp_to_list
from openpilot.starpilot.aol.wire import decode_intent
from openpilot.starpilot.car.subaru.tests.caller_helpers import actual_factory, firmware_records, CompleteCan, feed_perception_only
from openpilot.starpilot.car.subaru.tests.selfdrive_step import selfdrive_step
from openpilot.starpilot.car.subaru.tests.pandad_order import PandadOrder

from openpilot.starpilot.car.subaru.tests.native_fixture import subaru_native_pair as subaru_native_pair


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('combined', [False, True])
@pytest.mark.parametrize('aol', [False, True])
def test_actual_startup_factory_card_controls_native(enabled, combined, aol, subaru_native_pair, tmp_path, configure_delay=0):
  candidate = CAR.SUBARU_LEGACY_2025
  fw = firmware_records(candidate, FW_VERSIONS, 'subaru')
  rows = []
  with actual_factory(candidate, gen_empty_fingerprint(), fw, '0' * 17, manual=False, aol=aol,
                      preferences={"SubaruAvhStartup": enabled, "SubaruStopStartOff": combined}) as (card, params, matches):
    cp = card.CP
    ci = card.CI
    safety = libsafety_py.libsafety
    assert cp.pcmCruise and not cp.openpilotLongitudinalControl and not cp.alphaLongitudinalAvailable
    assert cp.alternativeExperience == (32 if aol else 0) and not cp.dashcamOnly
    assert cp.safetyConfigs[0].safetyParam == (145 + (1024 if enabled else 0) + (256 if combined else 0))
    sd, controls = SelfdriveD(CP=cp), Controls()
    stream = CompleteCan(ci)
    base = time.monotonic_ns() + 100_000_000
    safety.init_tests()
    safety.set_timer((base // 1000) & 0xFFFFFFFF)
    safety.set_alternative_experience(cp.alternativeExperience)
    assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw if configure_delay == 0 else structs.CarParams.SafetyModel.noOutput,
                                   cp.safetyConfigs[0].safetyParam if configure_delay == 0 else 0) == 0
    safety.set_aol_test_heartbeat(False)
    library, bridge = subaru_native_pair
    pandad = PandadOrder(safety, library, bridge)
    previous_controls = {}
    latest_controls = {}
    driven = {}
    driven_new = {}
    receipt = None
    steering_name = 'ES_LKAS_ANGLE' if cp.steerControlType == structs.CarParams.SteerControlType.angle else 'ES_LKAS'
    steering_bus = 0
    decoder = CANParser(DBC[candidate][Bus.pt], [(steering_name, 0)], steering_bus)
    dash_decoder = CANParser(DBC[candidate][Bus.pt], [('ES_LKAS_State', 0)], 0)
    dash_emissions = 0
    stop_start_emissions = []
    startup_emissions = []
    from opendbc.car.subaru.avh import INPUTS, avh_request, checksum
    from opendbc.car.subaru.tests.test_avh import sample
    template = None
    receiver = CANParser(DBC[candidate][Bus.pt], [("Dashlights", 10)], 1)
    try:
      for tick in range(1200 if configure_delay == 0 else 2300):
        now = base + tick * 10_000_000
        cruise = False
        brake = False
        temporary = False
        main = True
        values = {
          'Steering_Torque': {'Steer_Warning': int(temporary)},
          'Steering_2': {'Steering_Angle': 0},
          'Wheel_Speeds': dict.fromkeys(('FL', 'FR', 'RL', 'RR'), 0),
          'Brake_Status': {'Brake': int(brake)},
          'CruiseControl': {'Cruise_On': int(main), 'Cruise_Activated': int(cruise)},
          'ES_Status': {'Cruise_Activated': int(cruise)},
          'ES_DashStatus': {'Cruise_On': int(main), 'Cruise_Set_Speed': 100},
          'Transmission': {'Gear': 4},
        }
        incoming = stream.frames(values, tick=tick)
        if tick % 10 == 0:
          for name, fields in (("Dashlights", {"COUNTER": (tick // 10) % 16}),
                               ("Engine_Stop_Start", {"Counter": (tick // 10) % 16, "STOP_START_STATE": 1})):
            incoming.append(ci.CC.packer.make_can_msg(name, 1, fields))
        # Dom's AVH sources are on the alternate bus. The required physical
        # main-bus throttle/gear and bus1 wheel stream remain independently live.
        incoming = [(a, d, b) for a, d, b in incoming if not (a == 0x174 and b == 1)]
        for address in INPUTS:
          if address == 0x13A or (address == 0x6BB and tick % 100):
            continue
          data = bytearray(sample(address, tick // 100 if address == 0x6BB else tick))
          if address == 0x174:
            data[4] |= 0x40  # Stop/Start state1 plus engine-running bit.
            data[0] = checksum(address, data)
          incoming.append((address, bytes(data), 1))
          if address == 0x6BB:
            template = bytes(data)
        receiver.update([(now, incoming)])
        safety.set_timer((now // 1000) & 0xFFFFFFFF)
        if configure_delay and tick == configure_delay:
          safety.set_alternative_experience(cp.alternativeExperience)
          assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
        for addr, data, bus in incoming:
          safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, data))
        safety.safety_tick()
        panda = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=now)
        panda.pandaStates[0].safetyModel = cp.safetyConfigs[0].safetyModel if tick >= configure_delay else structs.CarParams.SafetyModel.noOutput
        panda.pandaStates[0].safetyParam = cp.safetyConfigs[0].safetyParam if tick >= configure_delay else 0
        panda.pandaStates[0].alternativeExperience = cp.alternativeExperience
        panda.pandaStates[0].ignitionLine = True
        panda.pandaStates[0].controlsAllowed = bool(safety.get_controls_allowed())
        panda.pandaStates[0].safetyRxChecksInvalid = not bool(safety.safety_config_valid())
        device = messaging.new_message('deviceState', valid=True, logMonoTime=now)
        device.deviceState.started = True
        device.deviceState.startedMonoTime = 500_000_000
        peers = [device.as_reader()]
        if tick % 10 == 0:
          peers.append(panda.as_reader())
        if 'carControl' in latest_controls:
          peers.append(latest_controls['carControl'])
        peers.extend(driven_new[s] for s in ('onroadEvents', 'selfdriveState', 'aolAxisState') if s in driven_new and s in card.sm.data)
        if receipt is not None and tick % 10 == 1:
          peers.append(receipt.as_reader())
        card.sm.update_msgs((now + 1000) / 1e9, peers)
        published = {}
        real_new = messaging.new_message

        def stamped(service=None, size=None, *, _real_new=real_new, _now=now, **kwargs):
          return _real_new(service, size, **({'logMonoTime': _now} | kwargs))

        def capture(service, msg, _published=published):
          wire = msg if isinstance(msg, bytes) else msg.to_bytes()
          _published[service] = messaging.log_from_bytes(wire)

        with (
          patch.object(card.sm, 'update'),
          patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw', return_value=[b'can']),
          patch('openpilot.selfdrive.car.card.can_capnp_to_list', return_value=[(now, incoming)]),
          patch('openpilot.selfdrive.car.card.REPLAY', False),
          patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now + 2000),
          patch('openpilot.selfdrive.car.card.time.monotonic', return_value=(now + 2000) / 1e9),
          patch('openpilot.selfdrive.car.card.time.clock_gettime_ns', return_value=now + 2000),
          patch('openpilot.selfdrive.car.card.messaging.new_message', side_effect=stamped),
          patch.object(card.pm, 'send', side_effect=capture),
        ):
          card.step()
        if tick % 10 == 0:
          published['pandaStates'] = panda.as_reader()
        cs = published['carState'].carState
        assert cs.standstill and cs.gearShifter == structs.CarState.GearShifter.park
        if tick >= configure_delay + 10:
          assert cs.canValid and safety.safety_config_valid()
        # Deliver each genuine10Hz native receipt once; do not refresh receive time/frequency from a cached snapshot.
        new_native = receipt if receipt is not None and tick % 10 == 1 else None
        driven_new = selfdrive_step(sd, published, new_native, previous_controls, cp, safety, now, calibration='calibrated')
        driven.update(driven_new)
        order = None
        if aol and tick % 10 == 0:
          receipt, order = pandad.step(driven, published, now)
        feed_perception_only(controls, now, tick, enabled=sd.enabled, active=sd.active)
        plan = messaging.new_message('longitudinalPlan', valid=True, logMonoTime=now)
        plan.longitudinalPlan.aTarget = 0.5
        control_peers = [published['carState'], published['carOutput'], plan.as_reader()]
        control_peers.extend(driven_new[s] for s in ('selfdriveState', 'onroadEvents', 'aolAxisState') if s in driven_new and s in controls.sm.data)
        if receipt is not None and tick % 10 == 0:
          control_peers.append(receipt.as_reader())
        controls.sm.update_msgs((now + 1000) / 1e9, control_peers)
        previous_controls = {}

        def capture_control(service, msg, _controls=previous_controls):
          wire = msg if isinstance(msg, bytes) else msg.to_bytes()
          _controls[service] = messaging.log_from_bytes(wire)

        with (
          patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now + 2000),
          patch('openpilot.cereal.messaging.time.monotonic', return_value=(now + 2000) / 1e9),
          patch.object(controls.pm, 'send', side_effect=capture_control),
        ):
          command, lateral_log = controls.state_control()
          controls.publish(command, lateral_log)
        latest_controls.update(previous_controls)
        command = previous_controls['carControl'].carControl
        outgoing = can_capnp_to_list([published['sendcan'].as_builder().to_bytes()], msgtype='sendcan')[0][1] if 'sendcan' in published else []
        tx = [(a, d.hex(), b, bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(a, b, d)))) for a, d, b in outgoing]
        decoder.update([(now, outgoing)])
        dash_decoder.update([(now, outgoing)])
        if any(address == 0x322 for address, _, _ in outgoing):
          dash = dash_decoder.vl['ES_LKAS_State']
          expected = ci.CC.angle_was_active if steering_name == 'ES_LKAS_ANGLE' else card.sm['carControl'].latActive
          assert bool(dash['LKAS_ACTIVE']) == expected, (candidate, tick, expected, dash)
          assert dash['LKAS_Dash_State'] == (2 if expected else 0), (candidate, tick, expected, dash)
          assert all(accepted == (tick >= configure_delay) for address, _, _, accepted in tx if address == 0x322)
          dash_emissions += 1
        for address, data_hex, bus, accepted in tx:
          if address == 0x390:
            assert combined and accepted and bus == 1, (tick, tx)
            stop_start_emissions.append((now, data_hex))
          if address == 0x6BB:
            assert enabled and accepted and bus == 1, (tick, tx)
            data = bytes.fromhex(data_hex)
            assert data == avh_request(template, len(startup_emissions) + 1)[1]
            startup_emissions.append((now, data_hex))

        if tick >= 20:
          assert receiver.can_valid
          assert receiver.message_states[0x390].counter_fail <= 1
        decoded_steering = []
        intent = decode_intent(published['aolIntentWire'].aolIntentWire) if aol else None
        row = {
          'tick': tick,
          'intent': False if intent is None else intent.allowedLatch,
          'armed': False if intent is None else intent.lateralArmed,
          'cardLatch': False if card.aol_card_intent is None else card.aol_card_intent.allowed_latch,
          'lat': command.latActive,
          'long': command.longActive,
          'enabled': sd.enabled,
          'active': sd.active,
          'permission': int(safety.aol_get_permission_mask()),
          'controls': bool(safety.get_controls_allowed()),
          'nativeNewToSD': new_native is not None,
          'pandaNew': tick % 10 == 0,
          'nativeSource': None if receipt is None else int(receipt.logMonoTime),
          'order': order,
          'tx': tx,
          'steering': decoded_steering,
          'fault': str(driven['aolAxisState'].aolAxisState.faultReason) if aol else 'unsupported',
          'events': [str(e.name) for e in driven['onroadEvents'].onroadEvents],
        }
        rows.append(row)
        assert not command.enabled and not command.latActive and not command.longActive, row
        assert not safety.get_controls_allowed() and safety.aol_get_permission_mask() == 0, row
      if configure_delay == 0:
        assert bool(stop_start_emissions) == combined
      assert dash_emissions >= 20
      assert bool(startup_emissions) == enabled
      assert len(startup_emissions) == (2 if enabled else 0)
      if startup_emissions:
        assert 45_000_000 <= startup_emissions[-1][0] - startup_emissions[0][0] <= 80_000_000
        earliest = 10_000_000_000 + configure_delay * 10_000_000
        assert all(earliest <= stamp - base < earliest + 1_500_000_000 for stamp, _ in startup_emissions)
    finally:
      pandad.close()
      (tmp_path / (str(candidate) + '-' + str(aol) + '-actual.json')).write_text(json.dumps(rows, indent=2) + '\n')


def test_startup_option_preserves_actual_stock_engage_aol_brake_reverse_recovery(subaru_native_pair, tmp_path):
  from openpilot.starpilot.car.subaru.tests import test_stock_aol_callers as stock
  original = stock.actual_factory

  def selected(*args, **kwargs):
    preferences = {**(kwargs.pop('preferences', None) or {}), 'SubaruAvhStartup': True, 'SubaruStopStartOff': True}
    return original(*args, **kwargs, preferences=preferences)

  with patch.object(stock, 'actual_factory', side_effect=selected):
    stock.test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(CAR.SUBARU_LEGACY_2025, subaru_native_pair, tmp_path)


@pytest.mark.parametrize('aol', [False, True])
def test_late_native_configuration_still_gets_one_qualified_request(aol, subaru_native_pair, tmp_path):
  test_actual_startup_factory_card_controls_native(True, False, aol, subaru_native_pair, tmp_path, configure_delay=950)
