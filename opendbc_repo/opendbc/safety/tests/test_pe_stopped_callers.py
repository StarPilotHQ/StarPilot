"""Same real pending owner, actual callers, C++ requests and native emitted frames."""
import os
import unittest

import pytest

from threading import Event
from time import perf_counter
from unittest.mock import patch

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.hyundai.radar_interface import RadarInterface
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_ev9_pending_native as native_fixture
from opendbc.car.hyundai.tests.test_ioniq5pe_pending_startup import PEStartupFixture
from opendbc.safety.tests.test_ioniq5pe_paired_native import PEFixture
from openpilot.cereal import messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module
from openpilot.starpilot.aol.intent import AOL_TOGGLE, read_settings
from openpilot.starpilot.aol.tests import test_ioniq_availability as caller_fixture
from openpilot.starpilot.aol.tests.test_canfd_location_native_recovery import physical_frames
from openpilot.starpilot.aol.tests.test_classic_location_stock_intent import host_heartbeat_bridge as host_heartbeat_bridge
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety


class PEJoinedCase(PEStartupFixture, PEFixture, unittest.TestCase):
  physical_tick = native_fixture.TestEV9PendingControllerNativeDebug.physical_tick

  def setUp(self):
    PEStartupFixture.setUp(self)
    PEFixture.setUp(self)
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.elm327, 1), 0)


def test_real_pe_stop_preserves_intent_without_rearming(monkeypatch, host_heartbeat_bridge):
  gas_disengage = False
  case = PEJoinedCase(methodName='runTest')
  callers = caller_fixture.IoniqAvailabilityTests(methodName='runTest')
  owner = card = None
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    saved = Params()
    for key, value in (('OpenpilotEnabledToggle', True), ('AlwaysOnLateral', True),
                       ('AlphaLongitudinalEnabled', True), ('IsReleaseBranch', False), ('IsOffroad', False)):
      saved.put_bool(key, value, block=True)
    saved.put_bool('DisengageOnAccelerator', gas_disengage, block=True)
    for key, value in (('LKASButtonControl', AOL_TOGGLE), ('MainCruiseButtonControl', 0),
                       ('CancelButtonControl', 0), ('LongCancelButtonControl', 0), ('VeryLongCancelButtonControl', 0),
                       ('PauseAOLOnBrake', 0.)):
      saved.put(key, value, block=True)
    keys = ('AlwaysOnLateral', 'AlphaLongitudinalEnabled', 'LKASButtonControl', 'MainCruiseButtonControl', 'PauseAOLOnBrake', 'DisengageOnAccelerator')
    original_settings = {key: saved.get(key) for key in keys}
    case.setUp()
    case.boot, case.seconds, case.now = 9_000_000_000, 9., 9_000_000
    native = case.safety
    native.set_aol_test_heartbeat(False)
    received, queued = {}, []
    local = monkeypatch.context()
    patcher = local.__enter__()
    try:
      patcher.setattr(card_module.time, 'monotonic_ns', lambda: case.boot)
      patcher.setattr(card_module.time, 'clock_gettime_ns', lambda clock: case.boot)
      patcher.setattr(card_module.time, 'monotonic', lambda: case.boot / 1e9)
      owner, ci, packer = case.instance(seal=False)
      card = card_module.Car(CI=ci, RI=RadarInterface(ci.CP), startup_owner=owner)
      assert card.CP.alternativeExperience == 32 and owner.prepared_for(card.CP)
      assert owner.owner.published and not owner.takeover_attempted
      assert case.queries == []
      patcher.setattr(card.sm, 'update', lambda *args: None)
      patcher.setattr(card_module, 'REPLAY', False)
      patcher.setattr(card_module.messaging, 'drain_sock_raw', lambda *args, **kwargs: list(queued))
      original_send = card.pm.send

      def capture(service, event):
        raw = event if isinstance(event, bytes) else event.to_bytes()
        received[service] = messaging.log_from_bytes(raw).as_builder()
        return original_send(service, raw)

      patcher.setattr(card.pm, 'send', capture)

      def pandas_message(diagnostic=False):
        message = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=case.boot)
        panda = message.pandaStates[0]
        panda.safetyModel = 'elm327' if diagnostic else card.CP.safetyConfigs[0].safetyModel
        panda.safetyParam = 1 if diagnostic else 0x5495
        panda.alternativeExperience = 0 if diagnostic else 32
        panda.controlsAllowed = native.get_controls_allowed()
        panda.safetyRxChecksInvalid = False if diagnostic else bool(native.safety_test_toyota_rx_summary_invalid())
        panda.ignitionLine = True
        return message

      def inactive_control():
        control = messaging.new_message('carControl', valid=True, logMonoTime=case.boot)
        return control

      for _ in range(12):
        case.physical_tick(ci, packer, native_hyundai=False)
      initialized = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=case.boot)
      card.sm.update_msgs(case.boot / 1e9, [pandas_message(True).as_reader(), inactive_control().as_reader(), initialized.as_reader()])
      owner.diagnostic_admission = card.startup_diagnostic_admission
      card.step()
      assert card.ci_initialized and not owner.pending_target and owner.takeover_attempted
      deadline = perf_counter() + 1.
      while not saved.get_bool('ControlsReady') and perf_counter() < deadline:
        Event().wait(.001)
      assert saved.get_bool('ControlsReady') and not owner.handed_off
      native.set_alternative_experience(32)
      assert native.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, 0x5495) == 0
      for _ in range(12):
        case.physical_tick(ci, packer)
      panda = pandas_message()
      card.sm.update_msgs(case.boot / 1e9, [panda.as_reader(), inactive_control().as_reader()])
      card.step()
      assert owner.handed_off, 'Only the actual serialized send acknowledgment may transfer this owner'
      first = received['sendcan']
      assert first.valid
      first_cb = next(frame.dat for frame in first.sendcan if frame.address == 0xcb)
      first_scc = next(frame.dat for frame in first.sendcan if frame.address == 0x1a0)
      assert (first_cb[3] >> 4) & 15 == 1 and (first_scc[8] >> 4) & 7 == 0
      for frame in first.sendcan:
        assert native.safety_tx_hook(libsafety_py.make_CANPacket(frame.address, frame.src, frame.dat))

      card.slc_producer_session = 'authority-card'
      sd, controls, drive = callers._authority_callers(False, cp=card.CP.as_reader().as_builder(), genuine_events=True)
      sd.aol_settings = read_settings(saved)
      sd.disengage_on_accelerator = saved.get_bool('DisengageOnAccelerator')
      assert sd.disengage_on_accelerator is gas_disengage
      # The common caller fixture controls its own clock; restore this test's shared time source afterward.
      patcher.setattr(card_module.time, 'monotonic_ns', lambda: case.boot)
      patcher.setattr(card_module.time, 'clock_gettime_ns', lambda clock: case.boot)
      patcher.setattr(card_module.time, 'monotonic', lambda: case.boot / 1e9)
      original_update = card.sm.update_msgs
      panda_due = [0.]

      def panda_cadence(timestamp, messages):
        delivered = []
        for message in messages:
          if message.which() == 'pandaStates':
            if timestamp + 1e-9 < panda_due[0]:
              continue
            panda_due[0] = timestamp + .1
          delivered.append(message)
        return original_update(timestamp, delivered)

      patcher.setattr(card.sm, 'update_msgs', panda_cadence)
      previous = {}
      auxiliary_due = {}
      host_heartbeat_bridge.reset_host()
      tick = case.tick
      emitted_active = 0
      emitted_longitudinal = 0
      native_rejections = 0
      last = None
      probe = False
      trace = []

      def step(*, main=0, lkas=0, button=0, gas=False, brake=False, gear=5, speed=40.):
        nonlocal tick, emitted_active, emitted_longitudinal, native_rejections, last
        tick += 1
        source_ns = 10_000_000_000 + tick * 10_000_000
        case.boot, case.seconds = source_ns, source_ns / 1e9
        native.set_timer(source_ns // 1000)
        if tick % 10 == 0:
          axis = previous.get('aolAxisState')
          intent = received.get('aolIntentWire')
          axis_bytes = axis.as_builder().to_bytes() if axis is not None else b''
          intent_bytes = intent.to_bytes() if intent is not None else b''
          state = previous.get('selfdriveState')
          plan = host_heartbeat_bridge.plan_host(28, 0x5495, native.aol_get_request_mask(), native.aol_get_permission_mask(),
            source_ns, axis_bytes, len(axis_bytes), intent_bytes, len(intent_bytes),
            state is not None and state.selfdriveState.enabled)
          native.aol_set_host_request(plan & 0xff)
          native.set_aol_test_heartbeat(bool(plan & 0x100))
          host_heartbeat_bridge.complete_host(native.aol_get_request_mask(), native.aol_get_permission_mask())
        frames = physical_frames(ci, tick, lkas=lkas)
        pack = CANPacker('hyundai_canfd_generated')
        replacement: dict[int, tuple[str, dict[str, float]]] = {
          0x35: ('ACCELERATOR', {'GEAR': gear, 'ACCELERATOR_PEDAL': 4 if gas else 0, 'COUNTER': tick % 256}),
          0xa0: ('WHEEL_SPEEDS', {**dict.fromkeys(('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal'), speed),
                                  'COUNTER': tick % 256}),
          0x175: ('TCS', {'ACCEnable': 0, 'ACC_REQ': 1, 'DriverBraking': int(brake), 'COUNTER': tick // 2 % 256}),
          0x1cf: ('CRUISE_BUTTONS', {'LDA_BTN': lkas, 'ADAPTIVE_CRUISE_MAIN_BTN': main,
                                   'CRUISE_BUTTONS': button, 'COUNTER': tick // 2 % 16}),
        }
        frames = [pack.make_can_msg(replacement[address][0], bus, replacement[address][1])
                  if address in replacement and bus == 1 else (address, data, bus) for address, data, bus in frames]
        assert not any(address == 0x1a0 and bus == 1 for address, _, bus in frames)
        for address, data, bus in frames:
          assert native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data)), (hex(address), bus)
        if tick % 100 == 0:
          native.safety_tick()
        mask, requested = native.aol_get_permission_mask(), native.aol_get_request_mask()
        rx_invalid = bool(native.safety_test_toyota_rx_summary_invalid())
        assert not native.get_relay_malfunction()
        batch = messaging.log_from_bytes(card_module.can_list_to_can_capnp(frames)).as_builder()
        batch.logMonoTime = source_ns
        queued[:] = [batch.to_bytes()]
        device = messaging.new_message('deviceState', valid=True, logMonoTime=source_ns)
        device.deviceState.started, device.deviceState.startedMonoTime = True, 9_000_000_000
        pandas = pandas_message()
        slot = PandaSlot(0, 'authority-panda', 28, 0x5495, 32, native.get_controls_allowed(), rx_invalid)
        wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=source_ns)
        wire.aolSafetyWire = encode_safety(SafetyState(1, True, source_ns, source_ns + 30_000_000,
          28, 0x5495, bool(mask & 1), bool(mask & 2), bool(requested & 1), bool(requested & 2),
          'authority-panda', card.aol_axis_session(), source_ns, (slot,)))
        messages = [device.as_reader(), pandas.as_reader(), wire.as_reader()]
        messages += [previous[name] for name in ('selfdriveState', 'aolAxisState', 'onroadEvents') if name in previous]
        if 'onroadEvents' not in previous:
          messages.append(messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=source_ns).as_reader())
        card.sm.update_msgs(source_ns / 1e9, messages)
        state, _ = card.state_update()
        card.state_publish(state, None)
        auxiliary = []
        for name in ('alertDebug', 'lateralManeuverPlan', 'laneChangeAssistWire', 'starpilotLongitudinalPlan', 'slcState', 'gpsLocation'):
          if source_ns >= auxiliary_due.get(name, 0):
            frequency = SERVICE_LIST[name].frequency
            auxiliary_due[name] = source_ns + (round(1e9 / min(frequency, 100)) if frequency > 0 else 0)
            auxiliary.append(messaging.new_message(name, 0 if name == 'laneChangeAssistWire' else None, valid=True, logMonoTime=source_ns).as_reader())
        # Auxiliary and authority envelopes belong to the same 100 Hz receive.
        # Preserve one SubMaster frame per tick, including real 1 Hz events.
        frame_before_auxiliary = sd.sm.frame
        sd.sm.update_msgs(source_ns / 1e9, auxiliary)
        sd.sm.frame = frame_before_auxiliary
        axis, host, command = drive(tick, intent_message=received['aolIntentWire'],
          car_state_message=received['carState'], car_output_message=received['carOutput'], companion=False,
          authority_message=received.get('starpilotCarState'), allowed=bool(mask & 1), long_allowed=bool(mask & 2),
          native_request_lat=bool(requested & 1), native_request_long=bool(requested & 2),
          panda_controls_allowed=native.get_controls_allowed(), panda_fault=rx_invalid,
          panda_state_message=pandas, safety_message=wire)
        previous.update(sd.pm.messages)
        case.boot += 1
        card.sm.update_msgs(case.boot / 1e9, [controls.pm.messages['carControl']])
        received.pop('sendcan', None)
        card.controls_update(state, card.sm['carControl'])
        if 'sendcan' in received:
          outgoing = received['sendcan']
          for frame in outgoing.sendcan:
            accepted = bool(native.safety_tx_hook(libsafety_py.make_CANPacket(frame.address, frame.src, frame.dat)))
            native_rejections += not accepted
            assert accepted, (tick, hex(frame.address), frame.src)
            if frame.address == 0x1a0:
              active_long = ((frame.dat[8] >> 4) & 7) in (1, 2)
              raw_accel = (((frame.dat[17] & 7) << 8) | frame.dat[16]) - 1023
              value_accel = ((frame.dat[18] << 4) | (frame.dat[17] >> 4)) - 1023
              assert command.longActive or (raw_accel == 0 and value_accel == 0)
              emitted_longitudinal += active_long
            if frame.address == 0xcb:
              active = ((frame.dat[3] >> 4) & 15) == 2
              assert not active or command.latActive
              emitted_active += active
        if probe:
          row = (native.aol_get_request_mask(), native.aol_get_permission_mask(), native.get_controls_allowed(),
            card.aol_card_intent.allowed_latch, axis.lateralActive, axis.longitudinalActive, str(axis.mode),
            str(axis.faultReason), host.enabled, str(host.state), command.latActive, command.longActive,
            tuple(str(event) for event in sd.events.names), bool(state.cruiseState.enabled),
            bool(state.cruiseState.available), card.aol_card_intent.latch_clear_reason,
            card.aol_transport_feedback.longitudinal_cancel_ns)
          if not trace or trace[-1][1] != row:
            trace.append((tick, row))
            print('PE_FULL_TRANSITION', tick, row, flush=True)
        last = (state, axis, host, command)
        return last

      for _ in range(120):
        step()
      for _ in range(2):
        step(main=1)
      for _ in range(8):
        step()
      for _ in range(2):
        step(lkas=1)
      for _ in range(40):
        state, axis, host, command = step()
      assert state.canValid and owner.handed_off and card.aol_card_intent.allowed_latch
      assert native.aol_get_permission_mask() == 1 and not native.get_controls_allowed()
      assert axis.lateralActive and command.latActive and not host.enabled and not command.longActive
      assert emitted_active > 0 and native_rejections == 0
      probe = True
      for _stop in range(3):
        for speed in (.5, .375, .3125, .25, .125, 0.):
          for _ in range(12):
            state, axis, host, command = step(speed=speed)
            assert str(state.gearShifter) == 'drive'
            assert card.aol_card_intent.allowed_latch
            assert str(axis.faultReason) == 'none'
            assert not command.longActive
            if speed == 0.:
              assert not axis.lateralActive and not command.latActive
              assert native.aol_get_permission_mask() == 0
        for _ in range(100):
          state, axis, host, command = step(speed=0.)
          assert card.aol_card_intent.allowed_latch
          assert not axis.lateralActive and not command.latActive and not command.longActive
          assert str(axis.faultReason) == 'none'
          assert native.aol_get_permission_mask() == 0
        for _ in range(40):
          state, axis, host, command = step(speed=5.)
        assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive
        assert native.aol_get_permission_mask() == 1 and not command.longActive
      for pedal in ('gas', 'brake'):
        before_lat = emitted_active
        for _ in range(24):
          state, axis, host, command = step(**{pedal: True})
          assert axis.lateralActive and command.latActive
          assert native.aol_get_permission_mask() == 1 and not native.get_controls_allowed()
          assert not axis.longitudinalActive and not command.longActive
          assert card.aol_card_intent.allowed_latch, pedal
        assert emitted_active > before_lat, 'Armed AOL must keep emitting accepted steering through pedals'
        for _ in range(30):
          state, axis, host, command = step()
        assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive, pedal
      probe = True
      for disengage in ('cancel', 'main'):
        before_long = emitted_longitudinal
        for _ in range(2):
          step(button=2)
        for _ in range(40):
          state, axis, host, command = step()
        assert native.get_controls_allowed() and native.aol_get_permission_mask() == 3, disengage
        assert host.enabled and axis.longitudinalActive and command.longActive, disengage
        assert emitted_longitudinal > before_long, 'Actual SCC output must demonstrate normal FULL engagement'
        for _ in range(2):
          step(button=4 if disengage == 'cancel' else 0, main=int(disengage == 'main'))
        before_lat = emitted_active
        for _ in range(40):
          state, axis, host, command = step()
        assert not native.get_controls_allowed() and native.aol_get_permission_mask() == 1, disengage
        assert not host.enabled and not axis.longitudinalActive and not command.longActive, disengage
        assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive, disengage
        assert emitted_active > before_lat, 'Actual steering must resume independently after longitudinal disengagement'
      assert native_rejections == 0
      for _ in range(2):
        step(lkas=1)
      for _ in range(40):
        state, axis, host, command = step()
      assert not card.aol_card_intent.allowed_latch
      assert not axis.lateralActive and not command.latActive and not command.longActive
      assert native.aol_get_permission_mask() == 0
      assert {key: saved.get(key) for key in keys} == original_settings
    finally:
      try:
        if owner is not None and card is not None:
          native.set_alternative_experience(0)
          native.set_safety_hooks(structs.CarParams.SafetyModel.elm327, 1)
          # Restoration is a new native telemetry sample, independent of the operating 10Hz decimator.
          type(card.sm).update_msgs(card.sm, case.boot / 1e9, [pandas_message(True).as_reader()])
          # The production restoration timeout must advance even if the diagnostic setup fails.
          patcher.setattr(card_module.time, 'monotonic', perf_counter)
          assert card.prepare_pe_restoration()
          owner.close()
      finally:
        callers.doCleanups()
        case.doCleanups()
        local.__exit__(None, None, None)


def test_recorded_pe_stock_wheel_cutoff_withdraws_native_output_without_new_button():
  from opendbc.safety.tests.test_hyundai_stock_angle_aol import TestHyundaiStockAngleAol

  case = TestHyundaiStockAngleAol(methodName='runTest')
  case.setUp()
  word = 0x5491
  case.mode(word)
  case.sources(word)
  case.gesture(word)
  assert case.safety.aol_get_permission_mask() == 1
  # Last moving and first stopped 0xa0 samples from each recorded warning.
  samples = ((11, 10, 12, 13), (11, 10, 12, 12), (13, 13, 13, 13),
             (12, 10, 12, 13), (12, 10, 12, 12), (13, 13, 13, 13),
             (10, 9, 10, 13), (10, 9, 10, 12), (13, 13, 13, 13))
  names = ('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal')
  for counter, wheels in enumerate(samples, start=7):
    case.safety.set_timer(1_000_000 + (counter - 6) * 5_000)
    case.rx('WHEEL_SPEEDS', 1, dict(zip(names, (raw * .03125 for raw in wheels), strict=True), COUNTER=counter))
    moving = any(raw > 12 for raw in wheels)
    assert case.safety.aol_get_request_mask() & 1
    assert case.safety.aol_get_permission_mask() == int(moving)
    assert bool(case.safety.safety_tx_hook(case.command(word, counter))) is moving
    if not moving:
      inactive = case.command(word, counter, active=False)
      assert not case.safety.safety_tx_hook(inactive)
      assert case.safety.safety_fwd_hook(2, inactive[0].addr) == 0


@pytest.mark.parametrize('identity', ['HYUNDAI_IONIQ_5_PE', 'KIA_EV9'])
def test_stock_cruise_enabled_angle_stop_wait_and_recovery(identity):
  callers = caller_fixture.IoniqAvailabilityTests(methodName='runTest')
  try:
    with OpenpilotPrefix():
      saved = Params()
      for key, value in (('OpenpilotEnabledToggle', True), ('AlwaysOnLateral', True),
                         ('AlphaLongitudinalEnabled', False), ('DisengageOnAccelerator', False), ('PauseLateralOnSignal', False)):
        saved.put_bool(key, value, block=True)
      for key, value in (('LKASButtonControl', 9), ('MainCruiseButtonControl', 0),
                         ('CancelButtonControl', 0), ('LongCancelButtonControl', 0), ('VeryLongCancelButtonControl', 0),
                         ('PauseAOLOnBrake', 0.), ('PauseLateralSpeed', 0.)):
        saved.put(key, value, block=True)
      original = {key: saved.get(key) for key in ('AlwaysOnLateral', 'AlphaLongitudinalEnabled',
        'DisengageOnAccelerator', 'LKASButtonControl', 'MainCruiseButtonControl', 'PauseAOLOnBrake')}
      sd, _, drive, card, receive = callers._ioniq_availability_callers(full_op=False, stock_angle=identity)
      sd.aol_settings = read_settings(saved)
      rolling = caller_fixture.car_state()
      rolling.vEgo = rolling.vEgoRaw = 1.
      rolling.cruiseState.enabled = True
      axis, host, command = drive(2, observed_state=rolling, panda_controls_allowed=True)
      assert host.enabled and axis.lateralActive and command.latActive
      assert not command.longActive and receive(10_020_000_000, state=rolling,
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      stopped = caller_fixture.car_state()
      stopped.vEgo = stopped.vEgoRaw = .09765625
      stopped.standstill = True
      stopped.cruiseState.enabled = True
      for tick in range(3, 205):
        axis, host, command = drive(tick, observed_state=stopped, allowed=False,
          native_request_lat=True, native_request_long=False, panda_controls_allowed=True)
        assert host.enabled and not axis.lateralActive and not command.latActive and not command.longActive
        assert str(axis.faultReason) == 'none' and not sd.aol_authority_lost
        assert receive(10_000_000_000 + tick * 10_000_000, state=stopped,
          axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      axis, host, command = drive(205, observed_state=rolling, allowed=True,
        native_request_lat=True, native_request_long=False, panda_controls_allowed=True)
      assert host.enabled and axis.lateralActive and command.latActive and not command.longActive
      assert receive(12_050_000_000, state=rolling,
        axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      assert original == {key: saved.get(key) for key in original}
  finally:
    callers.doCleanups()
