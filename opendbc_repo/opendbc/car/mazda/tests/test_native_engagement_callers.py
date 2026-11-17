"""Synthetic transport, real Mazda parser/Card/SD/Controls/encoder/native callers.

Native masks originate from actual libsafety RX, never a requested expected value.
Root must run this in its matching native runtime; no standalone artifact fallback.
"""
import ctypes
import subprocess
from pathlib import Path

import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.mazda.values import CAR, DBC
from opendbc.car.mazda.tests.test_stock_startup import complete_frames, exercise_startup


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_ordinary_engagement_brake_actual_family_callers(identity, monkeypatch):
  _exercise_actual_family_callers(identity, monkeypatch)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_unassigned_cancel_retains_lateral_main_off_revokes_actual_callers(identity, monkeypatch):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='cancel', cancel_action=0)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_longitudinal_cancel_assignment_keeps_lateral_actual_callers(identity, monkeypatch):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='long_cancel', cancel_action=4)


@pytest.fixture(scope='module')
def armed_intent_decoder(tmp_path_factory):
  import capnproto
  from openpilot.common.basedir import BASEDIR

  root = Path(BASEDIR)
  tmp = tmp_path_factory.mktemp('mazda-armed-intent')
  source = tmp / 'armed_intent.cc'
  source.write_text(r'''
#include "openpilot/selfdrive/pandad/aol_wire.h"
extern "C" bool mazda_test_armed(const unsigned char *data, size_t size, uint64_t message_ns,
                                 uint64_t state_ns, uint64_t now_ns, const char *producer) {
  return aol_armed_intent(kj::ArrayPtr<const capnp::byte>(data, size), message_ns, state_ns, now_ns,
                          false, std::string(producer));
}
''')
  library = tmp / 'armed_intent.so'
  generated = [root / ('openpilot/cereal/gen/cpp/' + name + '.capnp.c++') for name in ('custom', 'car')]
  subprocess.run(['c++', '-std=c++17', '-O1', '-shared', '-fPIC', '-Wall', '-Werror', '-I', str(root),
                  '-I', str(root / 'openpilot/cereal/gen/cpp'), '-I', capnproto.INCLUDE_DIR,
                  str(source), *map(str, generated), '-L', capnproto.LIB_DIR, '-lcapnp', '-lkj',
                  '-Wl,-rpath,' + capnproto.LIB_DIR, '-o', str(library)], check=True, timeout=40)
  host = ctypes.CDLL(str(library))
  armed = host.mazda_test_armed
  armed.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_char_p)
  armed.restype = ctypes.c_bool
  assert not armed(b'', 0, 0, 0, 0, b'')
  yield armed


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_reverse_return_retains_lateral_actual_callers(identity, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='reverse', armed_intent=armed_intent_decoder)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_standstill_return_retains_lateral_actual_callers(identity, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='standstill', armed_intent=armed_intent_decoder)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_permanent_fault_while_stopped_disarms_actual_callers(identity, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='stop_fault', armed_intent=armed_intent_decoder)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
@pytest.mark.parametrize('cancel_action', (3, 9))
def test_mapped_cancel_pause_and_toggle_actual_callers(identity, cancel_action, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='mapped_cancel', cancel_action=cancel_action,
                                 armed_intent=armed_intent_decoder)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
@pytest.mark.parametrize('lateral_only', (False, True))
def test_eps_recovery_actual_callers(identity, lateral_only, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='temporary', lateral_only=lateral_only,
                                 armed_intent=armed_intent_decoder)


def _exercise_actual_family_callers(identity, monkeypatch, *, scenario='brake', cancel_action=3, armed_intent=None,
                                    lateral_only=False, warning=None, unbelted=False):
  from openpilot.cereal import messaging
  from openpilot.selfdrive.car import card as card_module
  from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety
  from openpilot.starpilot.aol.tests.test_ioniq_availability import IoniqAvailabilityTests
  from opendbc.safety.tests.common import make_msg
  from opendbc.safety.tests.libsafety import libsafety_py

  def probe(card, ci, startup_controls, saved):
    patch_scope = monkeypatch.context()
    local_patch = patch_scope.__enter__()
    local_patch.setenv('SIMULATION', '0')
    case = IoniqAvailabilityTests(methodName='runTest')
    safety = libsafety_py.libsafety
    now, tick, queued = [10_000_000_000], 0, []
    received, previous_sd = {}, {}
    first_engagement_ns = None
    belt_off = [False]
    try:
      sd, controls, drive = case._authority_callers(False, cp=card.CP.as_reader().as_builder(), genuine_events=True)
      if scenario in ('cancel', 'long_cancel', 'mapped_cancel', 'temporary', 'startup', 'startup_stopped'):
        from openpilot.starpilot.aol.intent import read_settings
        sd.aol_settings = read_settings(saved)
        assert sd.aol_settings.cancel_actions == (cancel_action, 0, 0)
        assert card.aol_card_intent.settings == sd.aol_settings
      soft_warning = [False]
      original_events = sd.update_events

      def warning_events(*args, **kwargs):
        original_events(*args, **kwargs)
        if soft_warning[0]:
          from openpilot.selfdrive.selfdrived.events import EventName
          assert isinstance(warning, str)
          sd.events.add(getattr(EventName, warning))
      local_patch.setattr(sd, 'update_events', warning_events)
      assert sd.CP.to_dict() == card.CP.to_dict() == ci.CP.to_dict() == controls.CP.to_dict()
      safety.init_tests()
      safety.set_alternative_experience(int(card.CP.alternativeExperience))
      safety.set_safety_hooks(structs.CarParams.SafetyModel.mazda, 0)
      safety.set_aol_test_heartbeat(False)
      local_patch.setattr(card_module.time, 'monotonic_ns', lambda: now[0])
      local_patch.setattr(card_module.time, 'clock_gettime_ns', lambda clock: now[0])
      local_patch.setattr(card_module.messaging, 'drain_sock_raw', lambda *args, **kwargs: list(queued))
      local_patch.setattr(card.sm, 'update', lambda *args: None)
      create = messaging.new_message

      def new_message(service=None, size=None, **kwargs):
        kwargs.setdefault('logMonoTime', now[0])
        return create(service, size, **kwargs)
      local_patch.setattr(messaging, 'new_message', new_message)
      send = card.pm.send

      def capture(service, event):
        raw = event if isinstance(event, bytes) else event.to_bytes()
        received[service] = messaging.log_from_bytes(raw).as_builder()
        return send(service, raw)
      local_patch.setattr(card.pm, 'send', capture)
      packer = CANPacker(DBC[identity][Bus.pt])

      def step(*, engaged=False, brake=False, cancel=False, main=True, reverse=False, speed_kph=80., permanent=False,
               temporary=False):
        nonlocal tick, first_engagement_ns
        tick += 1
        now[0] = 10_000_000_000 + tick * 10_000_000
        frames = complete_frames(packer, card.CP, tick, main=main, engaged=engaged, brake=brake, cancel=cancel, reverse=reverse,
                                 speed_kph=speed_kph, permanent=permanent, temporary=temporary)
        frames.append(packer.make_can_msg('STEER', 0, {'STEER_ANGLE': 1.0}))
        if belt_off[0]:
          belt = packer.make_can_msg('SEATBELT', 0, {'DRIVER_SEATBELT': 0})
          frames = [row for row in frames if not (row[0] == belt[0] and row[2] == 0)] + [belt]
        safety.set_timer(now[0] // 1000)
        prior_state = previous_sd.get('selfdriveState')
        prior_axis = previous_sd.get('aolAxisState')
        heartbeat = bool(prior_state is not None and prior_state.selfdriveState.enabled)
        if prior_axis is not None:
          heartbeat |= bool(prior_axis.aolAxisState.desiredLateral or prior_axis.aolAxisState.desiredLongitudinal)
        # Transport requests follow the preceding actual Selfdrive axis desire.
        # Before its first publication the host has requested neither axis.
        request_lat = bool(prior_axis is not None and prior_axis.aolAxisState.desiredLateral)
        request_long = bool(prior_axis is not None and prior_axis.aolAxisState.desiredLongitudinal)
        request_mask = int(request_lat) | (int(request_long) << 1)
        # Match pandad: a current, producer-bound armed intent can retain the
        # heartbeat while output is intentionally paused. Validate its actual
        # serialized payload with production aol_armed_intent, not a fake grant.
        prior_intent = received.get('aolIntentWire')
        if armed_intent is not None and prior_axis is not None and prior_axis.valid and prior_intent is not None and prior_intent.valid:
          prior = prior_axis.aolAxisState
          axis_fresh = (prior.qualified and prior.sessionId == card.aol_axis_session() and
                        prior.observedMonoTime == prior_axis.logMonoTime and
                        0 < prior.observedMonoTime <= now[0] <= prior.validUntilMonoTime)
          if axis_fresh:
            payload = bytes(prior_intent.aolIntentWire)
            heartbeat |= bool(armed_intent(payload, len(payload), int(prior_intent.logMonoTime),
                                        int(prior.sourceCarStateMonoTime), now[0],
                                        str(prior.intentProducerSessionId).encode()))
        safety.set_aol_test_heartbeat(heartbeat)
        for address, data, bus in frames:
          safety.safety_rx_hook(make_msg(bus, address, dat=data))
        safety.safety_tick()
        # Permission remains the actual native response to the actual axis request.
        safety.aol_set_host_request(request_mask)
        # Match main_comms.h status: report native-filtered request, then permission.
        reported_request_mask = safety.aol_get_request_mask()
        mask = safety.aol_get_permission_mask()
        reported_request_lat = bool(reported_request_mask & 1)
        reported_request_long = bool(reported_request_mask & 2)
        if first_engagement_ns is None and not scenario.startswith('startup'):
          assert request_mask == reported_request_mask == mask == 0, 'No axis request before actual SD desire'
          if engaged:
            assert not heartbeat and safety.get_controls_allowed(), 'Cold physical engagement must precede host acknowledgment'
            first_engagement_ns = now[0]
        if mask & 1:
          assert heartbeat and request_lat and reported_request_lat, 'Native lateral permission needs actual request and heartbeat'
          assert (scenario.startswith('startup') or first_engagement_ns is not None and now[0] > first_engagement_ns), (
            'Native acknowledgment must follow physical grant')
        source = messaging.log_from_bytes(card_module.can_list_to_can_capnp(frames)).as_builder()
        source.logMonoTime = now[0]
        queued[:] = [source.to_bytes()]
        events = []
        for name, size in (('deviceState', None), ('pandaStates', 1)):
          events.append(messaging.new_message(name, size, valid=True, logMonoTime=now[0]))
        events[0].deviceState.started = True
        events[0].deviceState.startedMonoTime = 9_000_000_000
        panda = events[-1].pandaStates[0]
        panda.safetyModel = card.CP.safetyConfigs[0].safetyModel
        panda.safetyParam = 0
        panda.alternativeExperience = int(card.CP.alternativeExperience)
        panda.controlsAllowed = safety.get_controls_allowed()
        if 'onroadEvents' not in previous_sd:
          events.append(messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=now[0]))
        for name in ('selfdriveState', 'aolAxisState', 'onroadEvents'):
          if name in previous_sd:
            events.append(previous_sd[name].as_builder())
        wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=now[0])
        wire.aolSafetyWire = encode_safety(SafetyState(1, True, now[0], now[0] + 30_000_000,
          int(card.CP.safetyConfigs[0].safetyModel.raw), 0, bool(mask & 1), bool(mask & 2),
          reported_request_lat, reported_request_long, 'native-mazda-test', card.aol_axis_session(), now[0],
          (PandaSlot(0, 'native-mazda-test', int(card.CP.safetyConfigs[0].safetyModel.raw), 0, int(card.CP.alternativeExperience),
                     safety.get_controls_allowed(), bool(not safety.safety_config_valid()),
                     False, int(bool(safety.get_relay_malfunction()))),)))
        events.append(wire)
        card.sm.update_msgs(now[0] / 1e9, [event.as_reader() for event in events])
        state, _ = card.state_update()
        card.state_publish(state, None)
        axis, host_state, command = drive(tick, observed_state=state,
          intent_message=received['aolIntentWire'], allowed=bool(mask & 1), long_allowed=bool(mask & 2),
          native_request_lat=reported_request_lat, native_request_long=reported_request_long, panda_controls_allowed=safety.get_controls_allowed())
        previous_sd.update(sd.pm.messages)
        # Feed exact serialized Controls output through Card's control/publication
        # path; direct CI.apply would bypass its startup and source gates.
        published_control = controls.pm.messages['carControl']
        can_source_ns = now[0]
        # This is a later receive/decision instant, not a rewritten source stamp.
        # Only the actual Controls publication enters carControl once per cycle.
        now[0] += 1
        card.sm.update_msgs(now[0] / 1e9, [published_control])
        card.can_log_mono_time = can_source_ns
        received.pop('sendcan', None)
        card.controls_update(state, card.sm['carControl'])
        if state.canValid:
          assert 'sendcan' in received, 'Card did not publish the actual Controls command'
          outgoing = received['sendcan']
          assert outgoing.valid
          assert any(frame.address == 0x243 for frame in outgoing.sendcan)
          for frame in outgoing.sendcan:
            assert safety.safety_tx_hook(make_msg(frame.src, frame.address, dat=frame.dat))
        if unbelted and belt_off[0]:
          assert state.seatbeltUnlatched
          assert not axis.desiredLongitudinal and not axis.longitudinalActive and not command.longActive
          assert not safety.aol_get_permission_mask() & 2
        return state, axis, host_state, command

      for _ in range(120):
        state, axis, host_state, command = step(speed_kph=0. if scenario == 'startup_stopped' else 80.)
        if scenario == 'startup_stopped':
          assert not command.latActive and not command.longActive and safety.aol_get_permission_mask() == 0
      if scenario == 'startup_stopped':
        for _ in range(30):
          state, axis, host_state, command = step()
      if scenario == 'startup_managed':
        state, axis, host_state, command = step()
        assert state.canValid and state.cruiseState.available and not state.cruiseState.enabled
        assert not card.aol_card_intent.allowed_latch and not command.latActive and not command.longActive
        assert safety.aol_get_permission_mask() == 0 and not safety.get_controls_allowed()
        return
      if scenario in ('startup', 'startup_stopped'):
        state, axis, host_state, command = step()
        assert state.canValid and state.cruiseState.available and not state.cruiseState.enabled
        assert card.aol_card_intent.allowed_latch, 'MAIN already ON at boot never armed Mazda lateral intent'
        assert axis.lateralActive and command.latActive and safety.aol_get_permission_mask() == 1
        assert not host_state.enabled and not command.longActive and not safety.get_controls_allowed()
        for _ in range(5):
          step(cancel=True)
        for off_tick in range(120):
          state, axis, host_state, command = step()
          assert not card.aol_card_intent.allowed_latch and not command.latActive
          for frame in received['sendcan'].sendcan:
            if frame.address == 0x243 and frame.src == 0:
              assert (((frame.dat[0] & 15) << 8) | frame.dat[1]) - 2048 == 0
          # The first native poll precedes this cycle's published OFF decision.
          # Its next real request must withdraw the previous grant completely.
          if off_tick:
            assert safety.aol_get_permission_mask() == 0
          assert not safety.get_controls_allowed()
        for _ in range(10):
          state, axis, host_state, command = step(main=False)
          assert not card.aol_card_intent.allowed_latch and not command.latActive
        return
      assert not card.aol_card_intent.allowed_latch
      for _ in range(30):
        state, axis, host_state, command = step(engaged=True)
      assert state.canValid and host_state.enabled and command.latActive
      assert card.aol_card_intent.allowed_latch
      if unbelted:
        for _ in range(30):
          state, axis, host_state, command = step(brake=True)
        assert card.aol_card_intent.allowed_latch and axis.lateralActive and not axis.longitudinalActive
        belt_off[0] = True
        for _ in range(5):
          state, axis, host_state, command = step()
          assert state.seatbeltUnlatched and card.aol_card_intent.allowed_latch
          assert axis.lateralActive and command.latActive and not command.longActive
          assert safety.aol_get_permission_mask() == 1
      if lateral_only:
        for _ in range(30):
          state, axis, host_state, command = step(brake=True)
        assert card.aol_card_intent.allowed_latch and axis.lateralActive and not axis.longitudinalActive
        for _ in range(30):
          state, axis, host_state, command = step()
        assert card.aol_card_intent.allowed_latch and axis.lateralActive and not axis.longitudinalActive
      if scenario == 'warning':
        from openpilot.selfdrive.selfdrived.events import EventName
        assert isinstance(warning, str)
        lateral_expected = warning == 'radarTempUnavailable'
        for _ in range(30):
          state, axis, host_state, command = step(brake=True)
        for _ in range(30):
          state, axis, host_state, command = step()
        assert card.aol_card_intent.allowed_latch and axis.lateralActive and not command.longActive
        soft_warning[0] = True
        for paused_tick in range(350):
          state, axis, host_state, command = step()
          if any(event.softDisable for event in card.sm['onroadEvents']):
            assert card.aol_transport_feedback.ordinary_warning_qualified == (not lateral_expected)
          if paused_tick > 20:
            assert safety.aol_get_request_mask() == safety.aol_get_permission_mask() == int(lateral_expected)
          assert card.aol_card_intent.allowed_latch, 'Ordinary warning erased armed Mazda lateral intent'
          assert command.latActive == axis.lateralActive == lateral_expected
          assert not command.longActive and not axis.longitudinalActive
          assert getattr(EventName, warning) in sd.events.names
        soft_warning[0] = False
        for _ in range(40):
          state, axis, host_state, command = step()
          assert card.aol_card_intent.allowed_latch
          assert not command.longActive and not axis.longitudinalActive
          if not any(event.softDisable for event in card.sm['onroadEvents']):
            assert not card.aol_transport_feedback.ordinary_warning_qualified
        assert axis.lateralActive and command.latActive, 'Healthy warning recovery failed to restore retained lateral'
        return
      if scenario == 'warning_active':
        from openpilot.selfdrive.selfdrived.events import EventName
        assert isinstance(warning, str)
        lateral_expected = warning == 'radarTempUnavailable'
        assert card.aol_card_intent.allowed_latch and host_state.enabled and command.latActive
        soft_warning[0] = True
        for paused_tick in range(350):
          state, axis, host_state, command = step(engaged=True)
          if any(event.softDisable for event in card.sm['onroadEvents']):
            assert card.aol_transport_feedback.ordinary_warning_qualified == (not lateral_expected)
          if paused_tick > 20:
            assert safety.aol_get_request_mask() == safety.aol_get_permission_mask() == int(lateral_expected)
          assert card.aol_card_intent.allowed_latch, 'Ordinary warning erased armed Mazda lateral intent'
          assert command.latActive == axis.lateralActive == lateral_expected
          assert not command.longActive and not axis.longitudinalActive
          assert getattr(EventName, warning) in sd.events.names
        soft_warning[0] = False
        for _ in range(40):
          state, axis, host_state, command = step()
          assert card.aol_card_intent.allowed_latch
          assert not command.longActive and not axis.longitudinalActive
          if not any(event.softDisable for event in card.sm['onroadEvents']):
            assert not card.aol_transport_feedback.ordinary_warning_qualified
        assert axis.lateralActive and command.latActive, 'Healthy warning recovery failed to restore retained lateral'
        return
      if scenario == 'mapped_cancel':
        for _ in range(5):
          state, axis, host_state, command = step(cancel=True)
          if cancel_action == 4:
            assert axis.lateralActive and command.latActive, 'LONG-only Cancel assignment withdrew Mazda LAT'
        for _ in range(10):
          state, axis, host_state, command = step()
        if cancel_action == 3:
          assert card.aol_card_intent.allowed_latch, f'Mapped pause Cancel erased Mazda intent: {card.aol_card_intent.latch_clear_reason}'
          assert card.aol_card_intent.pause_lateral and not axis.lateralActive and not command.latActive
          for _ in range(5):
            step(cancel=True)
          for _ in range(10):
            state, axis, host_state, command = step()
          assert card.aol_card_intent.allowed_latch and not card.aol_card_intent.pause_lateral
          assert axis.lateralActive and command.latActive, 'Second mapped Cancel did not resume existing Mazda intent'
        elif cancel_action == 4:
          assert card.aol_card_intent.allowed_latch and card.aol_card_intent.pause_longitudinal
          assert axis.lateralActive and command.latActive and not command.longActive
        else:
          assert not card.aol_card_intent.allowed_latch, 'Mapped toggle Cancel rearmed instead of switching Mazda AOL off'
          assert not axis.lateralActive and not command.latActive
          for _ in range(5):
            step(cancel=True)
          for _ in range(30):
            state, axis, host_state, command = step()
          assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive, (
            'Mapped Cancel toggle could not rearm from a fresh physical Cancel')
        return
      if scenario == 'temporary':
        kwargs = {'temporary': True}
        for _ in range(30):
          state, axis, host_state, command = step(**kwargs)
          assert card.aol_card_intent.allowed_latch, f'Temporary Mazda restriction erased existing intent: {card.aol_card_intent.latch_clear_reason}'
          assert not axis.lateralActive and not command.latActive
          assert safety.aol_get_permission_mask() == 0
          assert not axis.longitudinalActive and not command.longActive
        for _ in range(30):
          state, axis, host_state, command = step()
        assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive, 'Mazda healthy return did not resume retained lateral'
        return
      if scenario in ('cancel', 'long_cancel'):
        for _ in range(5):
          state, axis, host_state, command = step(cancel=True)
          assert state.cruiseState.available and not state.cruiseState.enabled
          assert card.aol_card_intent.allowed_latch
          assert axis.lateralActive and command.latActive, 'Unassigned stock Cancel withdrew Mazda lateral'
          assert not axis.longitudinalActive and not command.longActive
          assert safety.aol_get_permission_mask() == 1
        for _ in range(30):
          state, axis, host_state, command = step()
          assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive
        for _ in range(5):
          state, axis, host_state, command = step(main=False)
          assert not card.aol_card_intent.allowed_latch
          assert not axis.lateralActive and not command.latActive and not command.longActive
          assert safety.aol_get_permission_mask() == 0
        return
      if scenario in ('reverse', 'standstill', 'stop_fault'):
        for _ in range(30):
          step(brake=True)
        for _ in range(30):
          state, axis, host_state, command = step()
        assert card.aol_card_intent.allowed_latch and axis.lateralActive and not axis.longitudinalActive
        restricted = {'reverse': True} if scenario == 'reverse' else {'speed_kph': 0.}
        for _ in range(30):
          state, axis, host_state, command = step(**restricted)
          assert card.aol_card_intent.allowed_latch, 'Physical restriction erased established Mazda lateral intent'
          assert not axis.lateralActive and not axis.longitudinalActive
          assert not command.latActive and not command.longActive and safety.aol_get_permission_mask() == 0
        if scenario == 'stop_fault':
          for _ in range(10):
            state, axis, host_state, command = step(speed_kph=0., permanent=True)
            assert not axis.lateralActive and not command.latActive and safety.aol_get_permission_mask() == 0
          assert not card.aol_card_intent.allowed_latch
          for _ in range(30):
            state, axis, host_state, command = step()
            assert not card.aol_card_intent.allowed_latch and not command.latActive
          return
        for _ in range(30):
          state, axis, host_state, command = step()
        assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive
        assert not axis.longitudinalActive and not command.longActive and not safety.get_controls_allowed()
        for _ in range(5):
          state, axis, host_state, command = step(main=False)
          assert not card.aol_card_intent.allowed_latch and not command.latActive
        return
      nonzero = False
      for _ in range(30):
        state, axis, host_state, command = step(brake=True)
        nonzero |= abs(command.actuators.torque) > 0
        assert not safety.get_controls_allowed()
        assert safety.aol_get_permission_mask() == 1
        assert card.aol_card_intent.allowed_latch
        assert axis.lateralActive and command.latActive
        assert not axis.longitudinalActive and not command.longActive
      assert nonzero, 'Actual Controls never requested a nonzero correction for measured steering error'
      invalid = controls.pm.messages['carControl'].as_builder()
      invalid.valid = False
      now[0] += 1  # Ordered receipt of an unchanged-source invalid packet.
      card.sm.update_msgs(now[0] / 1e9, [invalid.as_reader()])
      received.pop('sendcan', None)
      card.controls_update(state, card.sm['carControl'])
      assert 'sendcan' not in received, 'Invalid carControl reached Card sendcan publication'
    finally:
      safety.init_tests()
      safety.set_alternative_experience(0)
      safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
      # Restore nested patches before the harness closes its own clock mocks.
      patch_scope.__exit__(None, None, None)
      case.doCleanups()

  exercise_startup(identity, False, False, True, monkeypatch, probe=probe, cancel_action=cancel_action,
                   mapping=(0, 0) if scenario in ('startup', 'startup_stopped') else (9, 9))


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_main_already_on_startup_actual_callers(identity, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='startup', cancel_action=9, armed_intent=armed_intent_decoder)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_main_on_startup_respects_button_managed_aol(identity, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='startup_managed', armed_intent=armed_intent_decoder)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_main_on_startup_stopped_then_drive_actual_callers(identity, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='startup_stopped', cancel_action=9,
                                 armed_intent=armed_intent_decoder)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
def test_main_on_startup_disabled_aol_actual_card_controls_native(identity, monkeypatch):
  from openpilot.cereal import messaging
  from openpilot.selfdrive.car import card as card_module
  from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
  from opendbc.safety.tests.common import make_msg
  from opendbc.safety.tests.libsafety import libsafety_py

  def probe(card, ci, controls, saved):
    now, queued = [10_000_000_000], []
    monkeypatch.setattr(card_module.time, 'monotonic_ns', lambda: now[0])
    monkeypatch.setattr(card_module.time, 'clock_gettime_ns', lambda clock: now[0])
    monkeypatch.setattr(card_module.messaging, 'drain_sock_raw', lambda *args, **kwargs: list(queued))
    monkeypatch.setattr(card.sm, 'update', lambda *args: None)
    packer = CANPacker(DBC[identity][Bus.pt])
    safety = libsafety_py.libsafety
    safety.init_tests()
    safety.set_alternative_experience(int(card.CP.alternativeExperience))
    safety.set_safety_hooks(structs.CarParams.SafetyModel.mazda, 0)
    try:
      assert card.CP.alternativeExperience == 0
      for tick in range(1, 131):
        now[0] += 10_000_000
        safety.set_timer(now[0] // 1000)
        frames = complete_frames(packer, card.CP, tick, main=True)
        for address, data, bus in frames:
          safety.safety_rx_hook(make_msg(bus, address, dat=data))
        safety.safety_tick()
        safety.aol_set_host_request(0)
        source = messaging.log_from_bytes(card_module.can_list_to_can_capnp(frames)).as_builder()
        source.logMonoTime = now[0]
        queued[:] = [source.to_bytes()]
        events = [messaging.new_message(name, size, valid=True, logMonoTime=now[0])
                  for name, size in (('deviceState', None), ('onroadEvents', 0), ('pandaStates', 1))]
        events[0].deviceState.started = True
        events[0].deviceState.startedMonoTime = 9_000_000_000
        panda = events[-1].pandaStates[0]
        panda.safetyModel = card.CP.safetyConfigs[0].safetyModel
        panda.safetyParam = 0
        panda.alternativeExperience = 0
        card.sm.update_msgs(now[0] / 1e9, [e.as_reader() for e in events])
        state, _ = card.state_update()
        card.state_publish(state, None)
        feed(controls, now[0], tick, active=False, enabled=False,
             can_valid=state.canValid, can_timeout=state.canTimeout)
        observed = messaging.new_message('carState', valid=bool(state.canValid), logMonoTime=now[0])
        observed.carState = state
        controls.sm.update_msgs(now[0] / 1e9, [observed.as_reader()])
        command, _ = controls.state_control()
        assert card.aol_card_intent is None and not command.latActive and not command.longActive
        assert safety.aol_get_permission_mask() == 0 and not safety.get_controls_allowed()
      assert state.canValid and state.cruiseState.available and not state.cruiseState.enabled
      assert card.CP.to_dict() == controls.CP.to_dict()
    finally:
      safety.init_tests()
      safety.set_alternative_experience(0)
      safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  exercise_startup(identity, False, False, False, monkeypatch, probe=probe)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
@pytest.mark.parametrize('warning', ('calibrationInvalid', 'lowMemory', 'processNotRunning', 'radarTempUnavailable'))
def test_ordinary_warning_retains_intent_actual_callers(identity, warning, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='warning', warning=warning, armed_intent=armed_intent_decoder)


@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
@pytest.mark.parametrize('scenario', ('mapped_cancel', 'temporary'))
def test_unbelted_lateral_pause_recovery_actual_callers(identity, scenario, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario=scenario, cancel_action=3,
                                 armed_intent=armed_intent_decoder, unbelted=True)

@pytest.mark.parametrize('identity', (CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021))
@pytest.mark.parametrize('warning', ('calibrationInvalid', 'lowMemory', 'processNotRunning', 'radarTempUnavailable'))
def test_active_ordinary_warning_retains_intent_actual_callers(identity, warning, monkeypatch, armed_intent_decoder):
  _exercise_actual_family_callers(identity, monkeypatch, scenario='warning_active', warning=warning, armed_intent=armed_intent_decoder)
