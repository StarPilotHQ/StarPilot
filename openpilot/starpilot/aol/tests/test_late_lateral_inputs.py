"""A fresh queued proof may replace an expired snapshot; no lease is renewed."""

from unittest.mock import call, patch
import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
from opendbc.car.gm.values import DBC
from opendbc.safety.tests.libsafety import libsafety_py as native
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.aol.tests.test_gm_startup_main import parsed_packets
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
from openpilot.starpilot.aol.wire import SafetyState, encode_safety

BASE = 10_000_000_000


@pytest.mark.parametrize('c9', [False, True])
@pytest.mark.parametrize(
  'case',
  [
    'healthy',
    'empty',
    'withdrawn',
    'temporary_eps',
    'permanent_eps',
    'native_denied',
    'wrong_session',
    'invalid_axis',
    'invalid_native',
    'invalid_state',
    'future_axis',
    'stale_axis',
    'stock_sibling',
    'live_snapshot',
    'disabled_long_candidate',
    'clock_crosses_deadline',
    'old_long_axis',
    'old_long_axis_empty',
    'old_long_axis_stale',
    'old_long_active_only',
    'old_long_desired_only',
    'stock_cruise_off',
    'stock_cruise_off_denied',
    'new_host_long',
  ],
)
def test_queued_fresh_sdgm_axis_is_consumed_before_steering_gate(c9, case):
  stock_transition = case in ('stock_cruise_off', 'stock_cruise_off_denied')
  cp = sdgm_params(alpha=case != 'stock_sibling' and not stock_transition, brake_c9=c9)
  cp.alternativeExperience |= 32
  with OpenpilotPrefix():
    params = Params()
    params.put_bool('AlwaysOnLateral', True, block=True)
    params.put('CarParams', cp.to_bytes(), block=True)
    controls = Controls()
    ci = CarInterface(cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    safety = native.libsafety
    safety.init_tests()
    safety.set_alternative_experience(32)
    assert safety.set_safety_hooks(4, cp.safetyConfigs[0].safetyParam) == 0
    for tick in range(101):
      ns = BASE - 1_000_000_000 + tick * 10_000_000
      packets = parsed_packets(cp, packer, ns, tick, 24.78, True)
      if stock_transition:
        packets = [packet for packet in packets if packet[0] != 0x1C4]
        packets.append(packer.make_can_msg('AcceleratorPedal2', 0, {'CruiseState': 2}))
      cs = ci.update([(ns, packets)])
      safety.set_timer(ns // 1000)
      safety.set_aol_test_heartbeat(True)
      for a, data, bus in packets:
        safety.safety_fwd_hook(bus, a)
        assert safety.safety_rx_hook(native.make_CANPacket(a, bus, data))
      safety.safety_tick()
      safety.aol_set_host_request(1)
    assert cs.canValid and not cs.canTimeout
    assert bool(safety.get_controls_allowed()) == stock_transition
    assert bool(cs.cruiseState.enabled) == stock_transition
    mask = safety.aol_get_permission_mask()
    assert mask & 1 and not mask & 2

    def batch(ns):
      captured = []
      with patch.object(controls.sm, 'update_msgs', side_effect=lambda stamp, rows: captured.extend(rows)):
        normal_enabled = stock_transition and ns == BASE or case == 'new_host_long' and ns > BASE
        feed(controls, ns, 5, active=normal_enabled, enabled=normal_enabled)
      state = messaging.new_message('carState', valid=True, logMonoTime=ns)
      state.carState = cs
      axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=ns)
      a = axis.aolAxisState
      a.qualified = a.nativeAcknowledged = a.desiredLateral = a.lateralActive = True
      a.sessionId = 'late-sdgm'
      a.observedMonoTime = ns
      a.validUntilMonoTime = ns + 30_000_000
      a.sourceCarStateMonoTime = ns
      wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=ns)
      wire.aolSafetyWire = encode_safety(
        SafetyState(
          1,
          True,
          ns,
          ns + 200_000_000,
          4,
          cp.safetyConfigs[0].safetyParam,
          bool(mask & 1),
          bool(mask & 2),
          bool(safety.aol_get_request_mask() & 1),
          False,
          'native-test-panda',
          'late-sdgm',
        )
      )
      return [r for r in captured if r.which() != 'carState'] + [state.as_reader(), axis.as_reader(), wire.as_reader()]

    old = batch(BASE)
    if case.startswith('old_long_'):
      changed = old[-2].as_builder()
      changed.aolAxisState.longitudinalActive = case != 'old_long_desired_only'
      changed.aolAxisState.desiredLongitudinal = case != 'old_long_active_only'
      old[-2] = changed.as_reader()
    controls.sm.update_msgs(BASE / 1e9, old)
    with patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=BASE):
      command, _ = controls.state_control()
    assert command.latActive and not command.longActive
    original_deadline = controls.sm['aolAxisState'].validUntilMonoTime
    if stock_transition:
      stamp = BASE + 25_000_000
      packets = parsed_packets(cp, packer, stamp, 101, 24.78, True)
      cs = ci.update([(stamp, packets)])
      safety.set_timer(stamp // 1000)
      for a, data, bus in packets:
        safety.safety_fwd_hook(bus, a)
        assert safety.safety_rx_hook(native.make_CANPacket(a, bus, data))
      safety.aol_set_host_request(0 if case == 'stock_cruise_off_denied' else 1)
      mask = safety.aol_get_permission_mask()
      assert not safety.get_controls_allowed() and not cs.cruiseState.enabled and cs.cruiseState.available
      assert bool(mask & 1) == (case == 'stock_cruise_off')
    if case == 'new_host_long':
      stamp = BASE + 25_000_000
      packets = parsed_packets(cp, packer, stamp, 101, 24.78, True)
      packets = [packet for packet in packets if packet[0] != 0x1E1]
      packets.append(packer.make_can_msg('ASCMSteeringButton', 0, {'ACCButtons': 2}))
      cs = ci.update([(stamp, packets)])
      safety.set_timer(stamp // 1000)
      for a, data, bus in packets:
        safety.safety_fwd_hook(bus, a)
        assert safety.safety_rx_hook(native.make_CANPacket(a, bus, data))
      safety.aol_set_host_request(3)
      mask = safety.aol_get_permission_mask()
      assert safety.get_controls_allowed() and mask == 3
    pending = batch(BASE + 25_000_000)
    now = BASE + 31_000_000
    values = {v.which(): v.as_builder() for v in pending}
    if case == 'withdrawn':
      values['aolAxisState'].aolAxisState.lateralActive = False
    elif case == 'temporary_eps':
      values['carState'].carState.steerFaultTemporary = True
    elif case == 'permanent_eps':
      values['carState'].carState.steerFaultPermanent = True
    elif case == 'invalid_state':
      values['carState'].carState.canValid = False
      values['carState'].carState.canTimeout = True
    elif case == 'wrong_session':
      values['aolAxisState'].aolAxisState.sessionId = 'replaced-session'
    elif case == 'invalid_axis':
      values['aolAxisState'].valid = False
    elif case == 'invalid_native':
      values['aolSafetyWire'].valid = False
    elif case == 'future_axis':
      values['aolAxisState'].aolAxisState.observedMonoTime = now + 1
    elif case in ('stale_axis', 'old_long_axis_stale'):
      values['aolAxisState'].aolAxisState.validUntilMonoTime = now - 1
    elif case == 'native_denied':
      values['aolSafetyWire'].aolSafetyWire = encode_safety(
        SafetyState(
          1, True, BASE + 25_000_000, BASE + 225_000_000, 4, cp.safetyConfigs[0].safetyParam, False, False, True, False, 'native-test-panda', 'late-sdgm'
        )
      )
    elif case == 'live_snapshot':
      now = BASE + 29_000_000
    elif case == 'new_host_long':
      values['aolAxisState'].aolAxisState.longitudinalActive = True
      values['aolAxisState'].aolAxisState.desiredLongitudinal = True
      values['aolSafetyWire'].aolSafetyWire = encode_safety(
        SafetyState(
          1,
          True,
          BASE + 25_000_000,
          BASE + 225_000_000,
          4,
          cp.safetyConfigs[0].safetyParam,
          bool(mask & 1),
          bool(mask & 2),
          True,
          True,
          'native-test-panda',
          'late-sdgm',
        )
      )
    elif case == 'disabled_long_candidate':
      values['aolAxisState'].aolAxisState.longitudinalActive = True
      values['aolAxisState'].aolAxisState.desiredLongitudinal = True
      values['aolSafetyWire'].aolSafetyWire = encode_safety(
        SafetyState(
          1, True, BASE + 25_000_000, BASE + 225_000_000, 4, cp.safetyConfigs[0].safetyParam, True, True, True, True, 'native-test-panda', 'late-sdgm'
        )
      )
    pending = [] if case in ('empty', 'old_long_axis_empty') else [v.as_reader() for v in values.values()]

    transport_events = []

    def receive(timeout):
      nonlocal now
      transport_events.append(('receive', timeout))
      assert timeout == 0
      controls.sm.update_msgs(now / 1e9, pending)
      if case == 'clock_crosses_deadline':
        now = BASE + 60_000_000
        decision_clock.return_value = now

    with (
      patch.object(controls.sm, 'update', side_effect=receive) as receive_latest,
      patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now) as decision_clock,
      patch('openpilot.cereal.messaging.Poller') as poller,
    ):
      poller.return_value.registerSocket.side_effect = lambda socket: transport_events.append(('register', socket))
      poller.return_value.poll.side_effect = lambda timeout: transport_events.append(('poll', timeout))
      command, _ = controls.state_control()
    # GM may refresh an expired combined proof, but cannot start LONG on a LAT-only receipt.
    expected = case in ('healthy', 'live_snapshot', 'disabled_long_candidate', 'stock_sibling', 'stock_cruise_off', 'new_host_long', 'old_long_axis')
    assert command.latActive == expected and not command.longActive, (case, command.to_dict())
    if case in ('live_snapshot', 'old_long_active_only', 'old_long_desired_only'):
      receive_latest.assert_not_called()
      poller.assert_not_called()
      assert not transport_events
    elif case in ('empty', 'old_long_axis_empty'):
      # Only an unchanged header permits the bounded wait and second receive.
      socket = controls.sm.sock['aolAxisState']
      poller.assert_called_once_with()
      poller.return_value.registerSocket.assert_called_once_with(socket)
      poller.return_value.poll.assert_called_once_with(10)
      assert receive_latest.call_args_list == [call(0), call(0)]
      assert transport_events == [('receive', 0), ('register', socket), ('poll', 10), ('receive', 0)]
      assert controls.sm.logMonoTime['aolAxisState'] == BASE
      assert controls.sm['aolAxisState'].validUntilMonoTime == original_deadline
    else:
      receive_latest.assert_called_once_with(0)
      poller.assert_not_called()
      assert transport_events == [('receive', 0)]
    if case == 'old_long_axis':
      refreshed = controls.sm['aolAxisState']
      assert refreshed.sourceCarStateMonoTime == BASE + 25_000_000
      assert refreshed.validUntilMonoTime == BASE + 55_000_000
      assert not refreshed.longitudinalActive and not refreshed.desiredLongitudinal
    safety.set_timer(now // 1000)
    safety.set_aol_test_heartbeat(True)
    if case != 'new_host_long':
      safety.aol_set_host_request(int(command.latActive))
    _, frames = ci.apply(command.as_reader(), now)
    assert frames
    for a, data, bus in frames:
      assert safety.safety_tx_hook(native.make_CANPacket(a, bus, data)), (case, hex(a), bus)
    if case == 'new_host_long':
      controls.sm.update_msgs((now + 1_000_000) / 1e9, pending)
      with patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now + 1_000_000):
        normal_command, _ = controls.state_control()
      assert normal_command.enabled and normal_command.latActive and normal_command.longActive
      assert safety.get_controls_allowed() and safety.aol_get_permission_mask() == 3
    assert old[-2].aolAxisState.validUntilMonoTime == original_deadline == BASE + 30_000_000


@pytest.mark.parametrize('denied', [False, True])
def test_ioniq_queued_axis_uses_same_admitted_transport_contract(denied):
  # Actual Card/SelfdriveD/Controls callers; this fixture supplies native observations,
  # model readiness and receive transport, rather than exercising an HKG CAN/Panda loop.
  from contextlib import ExitStack
  from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture

  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, controls, step, card, _ = case._ioniq_availability_callers(full_op=False)
    cleanup.callback(card.vehicle_startup.close)
    old_axis, _, old_command = step(2, native_request_long=False)
    assert old_axis.lateralActive and old_command.latActive and not old_command.longActive
    old_deadline = int(old_axis.validUntilMonoTime)
    pending = []
    with (
      patch.object(controls.sm, 'update_msgs', side_effect=lambda stamp, rows: pending.extend(rows)),
      patch.object(controls, 'state_control', return_value=(old_command, None)),
      patch.object(controls, 'publish'),
    ):
      fresh_axis, _, _ = step(5, offset_ns=-5_000_000, native_request_long=False, allowed=not denied)
    assert fresh_axis.lateralActive == (not denied)
    now = 10_051_000_000

    def receive(timeout):
      assert timeout == 0
      controls.sm.update_msgs(now / 1e9, pending)

    with (
      patch.object(controls.sm, 'update', side_effect=receive) as receive_latest,
      patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now),
    ):
      command, _ = controls.state_control()
    assert command.latActive == (not denied) and not command.longActive
    receive_latest.assert_called_once_with(0)
    assert int(old_axis.validUntilMonoTime) == old_deadline == 10_050_000_000
