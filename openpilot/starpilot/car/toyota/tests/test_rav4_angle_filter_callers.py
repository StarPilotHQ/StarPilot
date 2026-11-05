"""Matched FW and real Card/SelfdriveD/Controls/CI/native path for RAV4 radar filter.

CAN, firmware acquisition, perception and USB are synthetic. A shared 100 Hz
clock tests source/native ordering; this is not installed-hardware acceptance.
"""
import json
import ctypes
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, car_helpers, gen_empty_fingerprint, structs
from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.can_definitions import CanData
from opendbc.car.toyota.values import CAR, DBC, ToyotaFlags
from opendbc.car.toyota.tests.test_auto_hold import decode
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.selfdrived import selfdrived as sd_module
from openpilot.starpilot.car.toyota.tests import test_pedal_caller_native as helpers

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope


@pytest.mark.parametrize('alpha,disable,radar_mode', [(False, False, 0), (True, False, 0), (True, False, 1), (True, False, 2), (True, True, 1)])
def test_rav4_filter_actual_callers(alpha, disable, radar_mode, tmp_path):
  run_case(CAR.TOYOTA_RAV4_TSS2_2023, alpha, disable, radar_mode, tmp_path)


def run_case(candidate, alpha, disable, radar_mode, tmp_path, *, aol=True, has_filter=True, bridge=None,
             filter_advertisements=None, recorded_firmware=None, has_pedal=False):
  helpers.SYNTHETIC_LAST.clear()
  helpers.NATIVE_LAST.clear()
  safety = libsafety_py.libsafety
  trace = []
  diag_queue, diag_tx = [], []
  native_globals = ctypes.CDLL(libsafety_py.libpath)

  def native_view(host):
    cfg = structs.CarParams.SafetyConfig(
      safetyModel=ctypes.c_uint16.in_dll(native_globals, 'current_safety_mode').value,
      safetyParam=ctypes.c_uint16.in_dll(native_globals, 'current_safety_param').value)
    return SimpleNamespace(CP=SimpleNamespace(safetyConfigs=[cfg],
      alternativeExperience=ctypes.c_int.in_dll(native_globals, 'alternative_experience').value))

  def diag_recv(wait_for_one=False):
    del wait_for_one
    if not diag_queue:
      return []
    batch = list(diag_queue)
    diag_queue.clear()
    return [batch]

  def diag_send(messages):
    assert ctypes.c_uint16.in_dll(native_globals, 'current_safety_mode').value == int(structs.CarParams.SafetyModel.elm327)
    for message in messages:
      assert safety.safety_tx_hook(libsafety_py.make_CANPacket(message.address, message.src, message.dat))
      assert message.address == 0x750 and message.src == 0
      diag_tx.append(bytes(message.dat).hex())
      if bytes(message.dat)[2:4] == bytes([0x10, 0x03]):
        diag_queue.append(CanData(0x758, bytes([0x0F, 2, 0x50, 3, 0, 0, 0, 0]), 0))
      elif bytes(message.dat)[2:5] == bytes([0x28, 1, 1]):
        diag_queue.append(CanData(0x758, bytes([0x0F, 2, 0x68, 1, 0, 0, 0, 0]), 0))
      else:
        raise AssertionError(('Unexpected initialization diagnostic', message))
  with OpenpilotPrefix(), ExitStack() as stack:
    params = Params()
    for key, value in [('OpenpilotEnabledToggle', True), ('AlphaLongitudinalEnabled', alpha),
                       ('DisableOpenpilotLongitudinal', disable), ('AlwaysOnLateral', aol), ('SafeMode', False)]:
      params.put_bool(key, value, block=True)
    params.put('ToyotaRadarMode', radar_mode, block=True)  # Retired values must not affect automatic selection.
    fw = helpers.firmware(candidate) if recorded_firmware is None else recorded_firmware
    exact, found = match_fw_to_car(fw, '', log=False)
    assert exact and found == {candidate}
    fp = gen_empty_fingerprint()
    advertisements = ([(0x2AA, bytes(8), 0)] if has_filter else []) if filter_advertisements is None else filter_advertisements
    for address, data, bus in advertisements:
      fp[bus][address] = len(data)
    if has_pedal:
      assert not has_filter
      fp[0][0x201] = 6
    observed = (candidate, fp, '0' * 17, fw, structs.CarParams.FingerprintSource.fw, True)
    with (patch.object(car_helpers, 'fingerprint', return_value=observed),
          patch.object(card_module, 'can_comm_callbacks', return_value=(diag_recv, diag_send)),
          patch.object(card_module.messaging, 'recv_one_retry', return_value=messaging.new_message('can', 1))):
      host = card_module.Car()
    long = alpha and not disable
    angle = candidate == CAR.TOYOTA_RAV4_TSS2_2023
    enrolled = aol
    assert bool(host.CP.flags & ToyotaFlags.DISABLE_RADAR) == (not has_filter and long)
    word = 0x49 | (0x400 if angle else 0) | (0x2000 if has_pedal and alpha else 0) | (0x1000 if has_filter else 0) | (0 if long else 0x200)
    assert host.CP.safetyConfigs[0].safetyParam == word
    assert host.CP.openpilotLongitudinalControl == long
    assert host.CP.alternativeExperience == 32
    controls, sd = Controls(), sd_module.SelfdriveD(CP=host.CP)
    assert controls.CP.to_dict() == host.CP.to_dict()
    safety.init_tests()
    safety.set_alternative_experience(host.CP.alternativeExperience)
    diagnostic = bool(host.CP.flags & ToyotaFlags.DISABLE_RADAR)
    initial_mode = int(structs.CarParams.SafetyModel.elm327) if diagnostic else host.CP.safetyConfigs[0].safetyModel.raw
    diagnostic_word = 0 if params.get_bool('ObdMultiplexingEnabled') else 1
    assert safety.set_safety_hooks(initial_mode, diagnostic_word if diagnostic else word) == 0
    if bridge is not None:
      bridge.reset_host()
    safety.set_aol_test_heartbeat(True)
    packer = CANPacker(DBC[candidate][Bus.pt])
    published, previous = {}, {}

    def capture(name, message):
      if isinstance(message, bytes):
        message = messaging.log_from_bytes(message)
      published[name] = message.as_reader() if hasattr(message, 'as_reader') else message

    stack.enter_context(patch.object(host.pm, 'send', side_effect=capture))
    stack.enter_context(patch.object(controls.pm, 'send', side_effect=capture))
    clock = [time.monotonic_ns()]
    real_clock = time.clock_gettime_ns
    stack.enter_context(patch.object(time, 'monotonic_ns', side_effect=lambda: clock[0]))
    stack.enter_context(patch.object(time, 'monotonic', side_effect=lambda: clock[0] / 1e9))
    stack.enter_context(patch.object(time, 'clock_gettime_ns',
      side_effect=lambda c: clock[0] if c in (time.CLOCK_MONOTONIC, time.CLOCK_BOOTTIME) else real_clock(c)))
    cc = structs.CarControl().as_reader()
    active = steering = acceleration = pedal_active = False
    try:
      for tick in range(1, 651):
        clock[0] += 10_000_000
        now = clock[0]
        safety.set_timer((now // 1000) & 0xFFFFFFFF)
        brake = 260 <= tick < 280
        gas = 310 <= tick < 330
        reverse = 360 <= tick < 380
        fault = 420 <= tick < 440
        main = not 500 <= tick < 520
        cruise = tick >= 150 and not any(a <= tick < b for a, b in ((260, 290), (310, 340), (360, 390), (420, 450), (500, 530)))
        frames = helpers.physical(packer, cruise=cruise, main=main, brake=brake, gas=gas, stopped=brake, gear=16 if reverse else 0, eps=9 if fault else 1)
        # LTA requires initialized measured angle and its own EPS status.
        frames = [f for f in frames if f[0] not in (0x260, 0x262)]
        frames += [packer.make_can_msg('STEER_TORQUE_SENSOR', 0, {'STEER_ANGLE': .0573}),
                   packer.make_can_msg('EPS_STATUS', 0, {'LKA_STATE': 9 if fault else 1, 'LTA_STATE': 9 if fault else 1}),
                   packer.make_can_msg('PCS_HUD', 0, {}),
                   # The physical filter advertises itself; it does not grant controls.
                   *advertisements]
        if not has_filter and not long:
          frames.append(packer.make_can_msg('ACC_CONTROL', 0, {'ACC_TYPE': 1}))
        # Stock AEB/PCS traffic must not trip the ACC relay check.
        if has_filter and 580 <= tick < 600:
          frames.append(packer.make_can_msg('ACC_CONTROL', 0, {}))
        if has_pedal:
          from opendbc.car.toyota import pedal
          address, data, bus = packer.make_can_msg('GAS_SENSOR', 0, {
            'INTERCEPTOR_GAS': 806 if gas else 0, 'INTERCEPTOR_GAS2': 806 if gas else 0,
            'COUNTER_PEDAL': tick & 15, 'STATE': 0})
          data = bytearray(data)
          data[-1] = pedal.checksum(data)
          frames.append((address, bytes(data), bus))
        for address, data, bus in frames:
          assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data)), (tick, hex(address))
        safety.safety_tick()
        native = helpers.native_events(native_view(host), safety, now, sd.aol_session_id)
        helpers.deliver(host.sm, [*native, *previous.values()])
        batch = messaging.log_from_bytes(card_module.can_list_to_can_capnp(frames)).as_builder()
        batch.logMonoTime = now
        with patch.object(card_module.messaging, 'drain_sock_raw', return_value=[batch.to_bytes()]), patch.object(host.sm, 'update'):
          cs, radar = host.state_update()
        host.state_publish(cs, radar)
        state = dict(published)
        peers = helpers.selfdrive_inputs(sd, state, native, cc, host.CP, now)
        events = {}
        with (patch.object(sd_module.messaging, 'recv_one', return_value=state['carState']),
              patch.object(sd_module.messaging, 'recv_one_or_none', return_value=None),
              patch.object(sd.sm, 'update', side_effect=lambda _, peers=peers: helpers.deliver(sd.sm, peers)),
              patch.object(sd.pm, 'send', side_effect=lambda n, m, events=events: events.update({n: m.as_reader()}))):
          sd.step()
        wanted = sd.aol_axis_decision
        if bridge is None:
          safety.aol_set_host_request(int(wanted.desired_lateral) | int(wanted.desired_longitudinal) << 1)
        else:
          axis = events.get('aolAxisState', previous.get('aolAxisState'))
          intent = state.get('aolIntentWire')
          axis_bytes = axis.as_builder().to_bytes() if axis is not None else b''
          intent_bytes = intent.as_builder().to_bytes() if intent is not None else b''
          observed = native_view(host).CP.safetyConfigs[0]
          capabilities = int(bool(ctypes.c_void_p.in_dll(native_globals, 'aol_policy').value))
          capabilities |= 2 * int(ctypes.c_bool.in_dll(native_globals, 'aol_optional_release_capable').value)
          engaged = bool(events.get('selfdriveState', previous.get('selfdriveState')).selfdriveState.enabled)
          packed = bridge.plan_host(observed.safetyModel.raw, observed.safetyParam,
            safety.aol_get_request_mask(), safety.aol_get_permission_mask(), now,
            axis_bytes, len(axis_bytes), intent_bytes, len(intent_bytes), engaged, capabilities)
          safety.aol_set_host_request(packed & 0xFF)
          safety.set_aol_test_heartbeat(bool(packed & 0x100))
          bridge.complete_host(safety.aol_get_request_mask(), safety.aol_get_permission_mask())
        native = helpers.native_events(native_view(host), safety, now, sd.aol_session_id)
        current = {**previous, **events}
        helpers.deliver(controls.sm, helpers.control_inputs(controls, published, state, current, native, now, .00005))
        command, lac_log = controls.state_control()
        controls.publish(command, lac_log)
        cc_event = published['carControl']
        cc = cc_event.carControl
        helpers.deliver(host.sm, [cc_event, *native, *events.values()])
        host.controls_update(cs, cc)
        if diagnostic and host.ci_initialized and ctypes.c_uint16.in_dll(native_globals, 'current_safety_mode').value == initial_mode:
          # The real pandad waits for Card's asynchronous Params handoff.
          # Poll its actual write; never set the readiness flag in the fixture.
          for _ in range(50):
            if params.get_bool('ControlsReady'):
              break
            time.sleep(.005)
          assert params.get_bool('ControlsReady')
          safety.set_alternative_experience(host.CP.alternativeExperience)
          assert safety.set_safety_hooks(host.CP.safetyConfigs[0].safetyModel.raw, word) == 0
          safety.set_aol_test_heartbeat(True)
        outgoing = published['sendcan']
        for frame in outgoing.sendcan:
          if has_filter:
            assert frame.address != 0x750, 'Filter path must not keep radar disabled'
          packet = libsafety_py.make_CANPacket(frame.address, frame.src, bytes(frame.dat))
          if diagnostic and ctypes.c_uint16.in_dll(native_globals, 'current_safety_mode').value == initial_mode:
            # Radar initialization still owns ELM327: actual native refusal is
            # required until Card publishes and pandad consumes ControlsReady.
            assert not cc.latActive and not cc.longActive
            if frame.address == 0x750:
              assert safety.safety_tx_hook(packet)
            else:
              assert not safety.safety_tx_hook(packet)
            continue
          assert safety.safety_tx_hook(packet), (tick, hex(frame.address), bytes(frame.dat).hex())
          if frame.address == 0x200:
            from opendbc.car.toyota import pedal
            data = bytes(frame.dat)
            assert len(data) == 6 and data[5] == pedal.checksum(data)
            if data[4] & 0x80:
              assert long and cc.longActive and not cs.gasPressed and not cs.brakePressed
              pedal_active = True
            else:
              assert data[:4] == bytes(4)
          if frame.address == 0x191:
            value = decode(host.CP, (frame.address, bytes(frame.dat), frame.src), 'STEERING_LTA')
            steering |= bool(value['STEER_REQUEST'] and abs(value['STEER_ANGLE_CMD']) > 0)
          elif frame.address == 0x2E4 and not angle:
            value = decode(host.CP, (frame.address, bytes(frame.dat), frame.src), 'STEERING_LKA')
            steering |= bool(value['STEER_REQUEST'] and value['STEER_TORQUE_CMD'])
          elif frame.address == 0x343:
            value = decode(host.CP, (frame.address, bytes(frame.dat), frame.src), 'ACC_CONTROL')
            acceleration |= value['ACCEL_CMD'] > 0
        trace.append({'tick': tick, 'canValid': cs.canValid, 'lat': cc.latActive, 'long': cc.longActive,
                      'events': [str(e) for e in sd.events.names], 'state': str(sd.state_machine.state)})
        if any(a <= tick < b for a, b in ((220, 240), (550, 570), (620, 640))):
          assert cs.canValid and not cs.canTimeout and host.ci_initialized and sd.initialized, trace[-1]
          assert cc.latActive and cc.longActive == long, trace[-1]
          active = True
        if any(a <= tick < b for a, b in ((370, 379), (430, 439), (510, 519))):
          assert not cc.latActive and not cc.longActive, trace[-1]
        if any(a <= tick < b for a, b in ((270, 279), (320, 329))):
          assert cc.latActive == enrolled and not cc.longActive, trace[-1]
        if enrolled and 442 <= tick < 449:
          assert cc.latActive and not cc.longActive, trace[-1]
        previous = current
      assert bool(diag_tx) == diagnostic
      assert active and steering
      assert acceleration == long
      if has_pedal and alpha:
        assert pedal_active == long
      assert not safety.get_relay_malfunction()
    finally:
      (tmp_path / 'diagnostics.json').write_text(json.dumps(diag_tx))
      (tmp_path / 'trace.json').write_text(json.dumps(trace, indent=2) + '\n')
      safety.set_alternative_experience(0)
      safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
      host.vehicle_startup.close()
