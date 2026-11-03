"""Default-off conditional join after a real native longitudinal MPC cycle."""

import json
import os
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, call, patch

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls import plannerd
from openpilot.selfdrive.controls.radard import RadarD
from openpilot.selfdrive.controls.plannerd import current_manual_event, queue_cruise_event
from openpilot.starpilot.conditional_mode.planner_host import ConditionalPlannerHost, selected_follow_time
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.preferences import CCMOptions, CEMOptions, SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.projection import ConditionalOwnerContext, ObservedBool
from openpilot.starpilot.conditional_mode.runtime_settings import DOCUMENT_KEY
from openpilot.starpilot.conditional_mode.status import settings_fingerprint
from openpilot.starpilot.conditional_mode.tests.test_projection import BOOT, MONO, FakeSubMaster, serialized_scene
from openpilot.starpilot.speed_limits.runtime import Action as SlcAction, CruiseEvent as SlcCruiseEvent


class TestConditionalRuntimeLoop(unittest.TestCase):
  def test_slc_unit_change_preserves_cap_and_queued_driver_event(self):
    from openpilot.starpilot.speed_limits import offset_document as od
    from openpilot.starpilot.speed_limits.runtime import Action, CruiseEvent
    from openpilot.starpilot.speed_limits.tests import test_runtime_replay as replay
    fixture = replay.RuntimeReplayTests()
    fixture.setUp()
    sm, cp = fixture.sm, fixture.cp
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('SpeedLimitController', True, block=True)
      params.put('SLCConfirmation', 1, block=True)
      params.put('SLCPriority1', 'Dashboard', block=True)
      params.put('SLCOffsetSchedule', od.to_value(od.default_document(False)), block=True)
      owner = plannerd.SavedSlcLifecycle(params, cp, {})
      runtime, _ = owner.refresh(2_000_000_000)
      sm['carState'].vCruise = sm['carState'].vCruiseCluster = 60.
      sm.advance(2_900_000_000)
      pending = runtime.step(sm, cp, now_ns=2_900_000_000).message.slcState
      sm.advance(2_950_000_000)
      accepted = runtime.step(sm, cp, now_ns=2_950_000_000,
        request=Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, 'accept'))
      self.assertEqual(accepted.result.ceiling.speed_mps, 25.)
      events = deque([CruiseEvent(1, 2_990_000_000, 60/3.6, 65/3.6, 'accel', 'card-producer', 2_990_000_000)])
      params.put_bool('IsMetric', True, block=True)
      metric, _ = owner.refresh(3_000_000_000, pending_cruise=events)
      self.assertIs(metric, runtime)
      self.assertEqual(len(events), 1)
      sm['carState'].vCruise = sm['carState'].vCruiseCluster = 65.
      sm.advance(3_000_000_000)
      output = metric.step(sm, cp, now_ns=3_000_000_000, cruise_event=events.popleft())
      self.assertEqual(output.result.ceiling.speed_mps, 25.)
      self.assertEqual(sm['carState'].vCruise, 65.)

  def test_slc_unit_changes_preserve_set_speed_and_driver_choices(self):
    from openpilot.common.constants import CV
    from openpilot.selfdrive.car.cruise import slc_target_kph
    from openpilot.starpilot.speed_limits import offset_document as od
    from openpilot.starpilot.speed_limits.runtime import Action, CruiseEvent
    from openpilot.starpilot.speed_limits.tests.test_runtime_replay import RuntimeReplayTests
    now = 2_000_000_000
    for confirmation, choice in ((False, 'applied'), (True, 'applied'), (False, 'ceiling'), (True, 'decline')):
      with self.subTest(confirmation=confirmation, choice=choice), tempfile.TemporaryDirectory() as directory:
        fixture = RuntimeReplayTests()
        fixture.setUp()
        sm, cp = fixture.sm, fixture.cp
        params = Params(directory)
        params.put_bool('SpeedLimitController', True, block=True)
        params.put('SLCConfirmation', int(confirmation), block=True)
        params.put('SLCPriority1', 'Dashboard', block=True)
        params.put('SLCOffsetSchedule', od.to_value(od.default_document(False)), block=True)
        owner = plannerd.SavedSlcLifecycle(params, cp, {})
        original, _ = owner.refresh(now)
        sm['slcDashboardObservation'].speedMps = 65 * CV.MPH_TO_MS
        baseline = slc_target_kph(65 * CV.MPH_TO_MS, False)
        sm['carState'].vCruise = sm['carState'].vCruiseCluster = baseline
        aligned = original.step(sm, cp, now_ns=now)
        self.assertTrue(aligned.message.slcState.hasAccepted)
        if choice == 'ceiling':
          selected = slc_target_kph(70 * CV.MPH_TO_MS, False)
          sm['carState'].vCruise = sm['carState'].vCruiseCluster = selected
          sm.advance(now + 50_000_000)
          chosen = original.step(sm, cp, now_ns=now + 50_000_000, cruise_event=CruiseEvent(
            1, now + 50_000_000, baseline / 3.6, selected / 3.6, 'accel', 'card-producer', now + 50_000_000))
          self.assertFalse(chosen.message.slcState.driverOverrideActive)
        elif choice == 'decline':
          sm['slcDashboardObservation'].speedMps = 55 * CV.MPH_TO_MS
          sm.advance(now + 50_000_000)
          pending = original.step(sm, cp, now_ns=now + 50_000_000).message.slcState
          sm.advance(now + 100_000_000)
          original.step(sm, cp, now_ns=now + 100_000_000,
            request=Action(original.session_id, 1, pending.decisionId, pending.presentationId, 'reject'))
        selected = float(sm['carState'].vCruise)
        params.put_bool('IsMetric', True, block=True)
        metric, changed = owner.refresh(now + 1_000_000_000)
        self.assertTrue(changed)
        sm.advance(now + 1_000_000_000)
        output = metric.step(sm, cp, now_ns=now + 1_000_000_000)
        self.assertFalse(output.message.slcState.hasPending)
        self.assertTrue(output.message.slcState.hasAccepted)
        self.assertEqual(sm['carState'].vCruise, selected)
        self.assertFalse(output.message.slcState.driverOverrideActive)
        if choice == 'decline':
          self.assertEqual(output.result.acceptance.basis, 'rejected')

  def test_slc_uses_current_set_units_and_retires_actions_when_units_change(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('SpeedLimitController', True, block=True)
      params.put('SLCPriority1', 'Dashboard', block=True)
      owner = plannerd.SavedSlcLifecycle(params, cp, {})
      imperial, _ = owner.refresh(MONO)
      self.assertFalse(imperial.is_metric)
      actions, cruise = deque([object()]), deque([object()])
      params.put_bool('IsMetric', True, block=True)
      metric, changed = owner.refresh(MONO + 1_000_000_000, pending_actions=actions, pending_cruise=cruise)
      self.assertTrue(changed)
      self.assertTrue(metric.is_metric)
      self.assertIs(metric, imperial)
      self.assertFalse(actions)
      self.assertEqual(len(cruise), 1)

  def test_starpilot_loop_subscriptions_follow_conditional_selection(self):
    class EndLoop(Exception):
      pass

    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put('CarParams', cp.to_bytes(), block=True)
      params.put_bool('SpeedLimitController', False, block=True)
      params.put_bool('ShowSpeedLimits', False, block=True)
      params.put_bool('CurveSpeedController', False, block=True)
      for enabled in (False, True):
        params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(
          mode=ModeChoice.CEM if enabled else ModeChoice.STOCK))), block=True)
        params.put_bool('SafeMode', not enabled, block=True)
        params.put_bool('UseStarPilotLongitudinalPlanner', enabled, block=True)
        with self.subTest(enabled=enabled):
          sm = Mock()
          sm.update.side_effect = EndLoop
          settings = {'CONDITIONAL_MODE_REPLAY_RUNTIME': '0',
                      'SLC_REPLAY_RUNTIME': '0', 'CURVE_REPLAY_RUNTIME': '0', 'LONG_PLANNER_REPLAY_RUNTIME': '0'}
          with patch.dict(os.environ, settings, clear=True), patch.object(plannerd, 'Params', return_value=params), \
               patch.object(plannerd, 'config_realtime_process'), \
               patch.object(plannerd, 'ConditionalPlannerHost', wraps=ConditionalPlannerHost) as conditional_ctor, \
               patch.object(messaging, 'PubMaster') as publisher, \
               patch.object(messaging, 'SubMaster', return_value=sm), patch.object(messaging, 'sub_sock') as subscribe:
            with self.assertRaises(EndLoop):
              plannerd.starpilot_main()
          self.assertEqual(subscribe.call_args_list, [
            call('carState', conflate=False),
            call('slcAction', conflate=False),
            call('slcCruiseEvent', conflate=False)])
          # Status and transport are reserved independently of saved SLC/CEM
          # admission, so a later saved change can attach without restarting.
          self.assertEqual(publisher.call_args.args[0], ['longitudinalPlan', 'starpilotLongitudinalPlan',
                                                       'driverAssistance', 'slcState'])
          self.assertEqual(conditional_ctor.call_count, int(enabled))
          if enabled:
            self.assertIs(conditional_ctor.call_args.args[0], params)

  def test_saved_slc_attaches_replaces_and_detaches_without_planner_restart(self):
    from openpilot.starpilot.longitudinal.tests.test_ioniq6_start import candidate
    from openpilot.starpilot.speed_limits.selection import Source
    _, cp = candidate(alternate=True)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('ShowSpeedLimits', False, block=True)
      params.put_bool('SpeedLimitController', False, block=True)
      params.put('SLCPriority1', 'Dashboard', block=True)
      owner = plannerd.SavedSlcLifecycle(params, cp, {})
      self.assertTrue(plannerd.slc_transport_capable(cp, {}))
      self.assertEqual(owner.refresh(1_000_000_000), (None, True))
      params.put_bool('ShowSpeedLimits', True, block=True)
      display, changed = owner.refresh(2_000_000_000)
      self.assertTrue(changed)
      self.assertIsNotNone(display)
      self.assertFalse(display.settings.enabled)
      self.assertFalse(display.vision_enabled)
      params.put('SLCPriority1', 'Vision', block=True)
      vision, changed = owner.refresh(3_000_000_000)
      self.assertTrue(changed)
      self.assertIsNot(vision, display)
      self.assertTrue(vision.vision_enabled)
      self.assertTrue(vision.vision_display_qualified)
      self.assertFalse(vision.vision_control_qualified)
      self.assertEqual(vision.settings.selection.slots[0], Source.VISION)
      session = vision.session_id
      pending_actions = deque([(3_000_000_000, SlcAction(session, 1, 1, 1, 'accept'))])
      pending_cruise = deque([SlcCruiseEvent(1, 3_000_000_000, 20., 21., 'set', session_id=session)])
      self.assertEqual(owner.refresh(3_050_000_000, pending_actions=pending_actions,
                                    pending_cruise=pending_cruise), (vision, False))
      self.assertEqual(len(pending_actions), 1)
      self.assertEqual(len(pending_cruise), 1)
      params.put_bool('SpeedLimitController', True, block=True)
      control, changed = owner.refresh(4_000_000_000, pending_actions=pending_actions, pending_cruise=pending_cruise)
      self.assertFalse(pending_actions)
      self.assertFalse(pending_cruise)
      self.assertTrue(changed)
      self.assertTrue(control.vision_control_qualified)
      self.assertFalse(control.vision_display_qualified)
      self.assertNotEqual(control.session_id, session)
      self.assertNotEqual(vision.session_id, session)  # Retired session was reset.
      params.put_bool('ShowSpeedLimits', False, block=True)
      params.put_bool('SpeedLimitController', False, block=True)
      pending_actions.append((4_050_000_000, SlcAction(control.session_id, 1, 1, 1, 'accept')))
      pending_cruise.append(SlcCruiseEvent(2, 4_050_000_000, 20., 21., 'set', session_id=control.session_id))
      self.assertEqual(owner.refresh(5_000_000_000, pending_actions=pending_actions,
                                    pending_cruise=pending_cruise), (None, True))
      self.assertFalse(pending_actions)
      self.assertFalse(pending_cruise)
      Path(params.get_param_path('ShowSpeedLimits')).write_bytes(b'invalid')
      self.assertEqual(owner.refresh(6_000_000_000), (None, True))
      self.assertEqual(Path(params.get_param_path('ShowSpeedLimits')).read_bytes(), b'invalid')
      passive = cp.as_builder() if hasattr(cp, 'as_builder') else cp.as_reader().as_builder()
      passive.passive = True
      params.put_bool('ShowSpeedLimits', True, block=True)
      self.assertFalse(plannerd.slc_transport_capable(passive, {}))
      self.assertIsNone(plannerd.SavedSlcLifecycle(params, passive, {}).refresh(7_000_000_000)[0])
      self.assertTrue(plannerd.slc_transport_capable(None, {'SLC_REPLAY_RUNTIME': '1'}))

  def test_optional_slc_sources_are_reserved_while_saved_runtime_is_absent(self):
    from openpilot.starpilot.longitudinal.tests.test_ioniq6_start import candidate
    _, cp = candidate(alternate=True)
    class EndLoop(Exception):
      pass
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put('CarParams', cp.to_bytes(), block=True)
      params.put_bool('ShowSpeedLimits', False, block=True)
      params.put_bool('SpeedLimitController', False, block=True)
      sm = Mock()
      sm.update.side_effect = EndLoop
      with patch.dict(os.environ, {}, clear=True), patch.object(plannerd, 'Params', return_value=params), \
           patch.object(plannerd, 'config_realtime_process'), \
           patch.object(messaging, 'PubMaster') as publisher, \
           patch.object(messaging, 'SubMaster', return_value=sm) as subscriber, patch.object(messaging, 'sub_sock'):
        with self.assertRaises(EndLoop):
          plannerd.starpilot_main()
      self.assertNotIn('slcCruiseCommand', publisher.call_args.args[0])
      for service in ('slcDashboardObservation', 'slcVisionObservation'):
        self.assertIn(service, subscriber.call_args.args[0])
        for check in ('ignore_alive', 'ignore_valid', 'ignore_avg_freq'):
          self.assertIn(service, subscriber.call_args.kwargs[check])
      for service in plannerd.NATIVE_PLAN_INPUTS:
        self.assertIn(service, subscriber.call_args.args[0])
        for check in ('ignore_alive', 'ignore_valid', 'ignore_avg_freq'):
          self.assertNotIn(service, subscriber.call_args.kwargs[check])
      self.assertIsNone(plannerd.SavedSlcLifecycle(params, cp, {}).refresh(1_000_000_000)[0])

  def test_automatic_chill_ignores_retired_slc_modes_but_keeps_native_radar_veto(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    planner = LongitudinalPlanner(cp, init_v=25.0)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put(DOCUMENT_KEY, json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CCM))), block=True)
      owner = ConditionalPlannerHost(params)
      stamp = MONO
      radar = RadarD(adjacent_enabled=True, radar_available=True,
                     clock_pair_fn=lambda: (stamp + 1_000_000, BOOT + stamp - MONO + 1_000_000, 1000))

      class Capture(messaging.PubMaster):
        def __init__(self):
          pass

        def send(self, s, dat):
          # The simulated publishers share this explicit recorded test clock.
          dat.logMonoTime = stamp + 1_000_000
          events[s] = messaging.log_from_bytes(dat.to_bytes())

      class Sources(messaging.SubMaster):
        def __init__(self, payloads, stamp):
          self.data = payloads
          self.logMonoTime = dict.fromkeys(payloads, stamp)
          self.recv_time = dict.fromkeys(payloads, stamp / 1e9)
          self.seen = dict.fromkeys(payloads, True)
          self.alive = dict.fromkeys(payloads, True)
          self.valid = dict.fromkeys(payloads, True)

        def all_checks(self, service_list=None):
          return all(self.valid[service] for service in ('modelV2', 'carState', 'radarTracks'))

      legacy = Path(params.get_param_path('SLCFallback'))
      saved_modes = (b'0', b'1', b'2', b'broken')
      for index in range(31):
        legacy.write_bytes(saved_modes[index % len(saved_modes)])
        stamp = MONO + index * 50_000_000
        payloads = serialized_scene(stamp=BOOT + stamp - MONO, speed=25.0, horizon=300.0,
                                    lead_present=False, experimental_mode=True)
        model = payloads['modelV2'].as_builder()
        model.orientationRate.z = [0.0] * 33
        model.init('leadsV3', 3)
        model.action.shouldStop = False
        payloads['modelV2'] = model.as_reader()
        control = payloads['controlsState'].as_builder()
        control.curvature = 0.0
        payloads['controlsState'] = control.as_reader()
        car = payloads['carState'].as_builder()
        car.vCruise = 130.0
        payloads['carState'] = car.as_reader()
        for service in ('vehicleParameters', 'radarTracks'):
          event = messaging.new_message(service, valid=True)
          if service == 'radarTracks' and index == 30:
            point = event.radarTracks.init('points', 1)[0]
            point.trackId, point.dRel, point.yRel, point.vRel = 17, 25.0, 3.0, -10.0
          payloads[service] = getattr(messaging.log_from_bytes(event.to_bytes()), service)
        sm = Sources(payloads, stamp)
        sm.recv_frame = {'carState': index + 1}
        for service in payloads:
          sm.seen[service] = sm.valid[service] = sm.alive[service] = True
          sm.logMonoTime[service] = stamp
          sm.recv_time[service] = stamp / 1e9
        sm.updated = dict.fromkeys(payloads, True)
        events = {}
        radar.update(sm, payloads['radarTracks'])
        radar.publish(Capture())
        for service, event in events.items():
          payloads[service] = getattr(event, service)
          sm.logMonoTime[service] = int(event.logMonoTime)
          sm.recv_time[service] = event.logMonoTime / 1e9
          sm.valid[service] = bool(event.valid)
          sm.seen[service] = sm.alive[service] = True
        planner.update(sm)
        current, _ = owner.sample(sm, cp, planner, now_mono_ns=stamp + 2_000_000,
                                   now_boot_ns=BOOT + stamp - MONO + 2_000_000, sample_skew_ns=1000,
                                   drive_id=MONO - 1_000_000_000, native_plan_valid=True)
        self.assertEqual(legacy.read_bytes(), saved_modes[index % len(saved_modes)])
        if 20 <= index < 30:
          self.assertIs(current.projected.scene.adjacent_lead_ambiguous, False)
          self.assertEqual(current.decision.reason.value, 'ccm_speed')
          self.assertIs(current.override_experimental, False)
      self.assertIs(current.projected.scene.adjacent_lead_ambiguous, True)
      self.assertEqual(current.decision.reason.value, 'ccm_veto')
      self.assertIs(current.override_experimental, True)  # Real adjacent radar veto exits Chill immediately.

  def test_current_build_disabled_scenes_make_chill_launch_known(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    planner = LongitudinalPlanner(cp, init_v=15.0)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put(DOCUMENT_KEY, json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CCM,
                 ccm=CCMOptions(launch_assist=True)))), block=True)
      payloads = serialized_scene()
      vehicle = messaging.new_message('vehicleParameters', valid=True)
      payloads['vehicleParameters'] = messaging.log_from_bytes(vehicle.to_bytes()).vehicleParameters
      sm = FakeSubMaster(payloads, MONO - 5_000_000)
      supported_disabled = ConditionalPlannerHost(params)
      enabled_unavailable = ConditionalPlannerHost(params, enabled_scene_owners=frozenset({'red_light'}))
      for owner in (supported_disabled, enabled_unavailable):
        owner.sample(sm, cp, planner, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000, drive_id=MONO)
      event = MONO + 50_000_000
      sm.stamp(event)
      sm.payloads.update(serialized_scene(stamp=BOOT + 50_000_000))
      planner.update(sm)
      known, _ = supported_disabled.sample(sm, cp, planner, now_mono_ns=event + 1_000_000,
                                           now_boot_ns=BOOT + 51_000_000, sample_skew_ns=1000,
                                           drive_id=MONO, native_plan_valid=True)
      unknown, _ = enabled_unavailable.sample(sm, cp, planner, now_mono_ns=event + 1_000_000,
                                              now_boot_ns=BOOT + 51_000_000, sample_skew_ns=1000,
                                              drive_id=MONO, native_plan_valid=True)
      assert known.projected is not None and unknown.projected is not None
      self.assertIs(known.projected.scene.launch_candidate, False)
      self.assertIsNone(unknown.projected.scene.launch_candidate)

  def test_current_configuration_and_explicitly_disabled_scene_owners(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    planner = LongitudinalPlanner(cp, init_v=15.0)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      saved = SavedPreferences(mode=ModeChoice.CEM,
                               cem=CEMOptions(stop_lights=True, model_stop_s=7.5, slower_lead=True, stopped_lead=False))
      params.put(DOCUMENT_KEY, json.loads(encode_preferences(saved)), block=True)
      owner = ConditionalPlannerHost(params)
      payloads = serialized_scene()
      vehicle = messaging.new_message('vehicleParameters', valid=True)
      payloads['vehicleParameters'] = messaging.log_from_bytes(vehicle.to_bytes()).vehicleParameters
      sm = FakeSubMaster(payloads, MONO - 5_000_000)
      owner.sample(sm, cp, planner, now_mono_ns=MONO, now_boot_ns=BOOT,
                   sample_skew_ns=1000, drive_id=MONO)
      event = MONO + 50_000_000
      sm.stamp(event)
      sm.payloads.update(serialized_scene(stamp=BOOT + 50_000_000, experimental_mode=True))
      planner.update(sm)
      proposal, _ = owner.sample(sm, cp, planner, now_mono_ns=event + 1_000_000,
                                 now_boot_ns=BOOT + 51_000_000, sample_skew_ns=1000, drive_id=MONO)
      assert proposal.projected is not None
      self.assertFalse(proposal.projected.scene.traffic_mode)
      self.assertFalse(proposal.projected.scene.stop_sign_confirmed)
      self.assertIsNotNone(proposal.projected.stop_source_observed_mono_s)
      self.assertIsNotNone(proposal.projected.slower_source_observed_mono_s)

      # A future enabled traffic owner with no fresh frame stays unknown. Its
      # absence cannot be silently converted into a clear traffic scene.
      enabled = ConditionalPlannerHost(params, enabled_scene_owners=frozenset({'traffic_mode'}))
      unknown, _ = enabled.sample(sm, cp, planner, now_mono_ns=event + 1_000_000,
                                  now_boot_ns=BOOT + 51_000_000, sample_skew_ns=1000, drive_id=MONO)
      assert unknown.projected is not None
      self.assertIsNone(unknown.projected.scene.traffic_mode)
      self.assertIsNone(unknown.projected.stop_source_observed_mono_s)

      # Current gas pedal and effective ExperimentalMode use their real
      # carState/selfdriveState source timestamps, not settings refresh time.
      next_event = event + 50_000_000
      sm.stamp(next_event)
      sm.payloads.update(serialized_scene(stamp=BOOT + 100_000_000, standstill=True, gas_pressed=True,
                                          experimental_mode=True))
      planner.update(sm)
      held, _ = owner.sample(sm, cp, planner, now_mono_ns=next_event + 1_000_000,
                             now_boot_ns=BOOT + 101_000_000, sample_skew_ns=1000, drive_id=MONO,
                             owner_context=ConditionalOwnerContext(stop_sign_confirmed=ObservedBool(True, next_event)))
      assert held.projected is not None
      self.assertFalse(held.projected.scene.standstill_stop_hold)

  def test_manual_receipt_is_distinct_and_requires_receiver_mapping(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    planner = LongitudinalPlanner(cp, init_v=15.0)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put(DOCUMENT_KEY, json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CEM,
                             cem=CEMOptions(speed_mps=20.0)))), block=True)
      params.put('LKASButtonControl', 5, block=True)
      owner = ConditionalPlannerHost(params)
      payloads = serialized_scene()
      vehicle = messaging.new_message('vehicleParameters', valid=True)
      payloads['vehicleParameters'] = messaging.log_from_bytes(vehicle.to_bytes()).vehicleParameters
      sm = FakeSubMaster(payloads, MONO - 5_000_000)
      owner.sample(sm, cp, planner, now_mono_ns=MONO, now_boot_ns=BOOT, sample_skew_ns=1000, drive_id=MONO)

      def receipt(sequence, stamp, fingerprint):
        event = messaging.new_message('slcCruiseEvent', valid=True)
        event.logMonoTime = stamp
        outer = event.slcCruiseEvent
        outer.kind = 'conditionalMode'
        outer.eventId = sequence
        outer.producerSessionId = 'b' * 32
        outer.observedMonoTime = stamp - 1000
        outer.manualMode = {'version': 1, 'sessionId': 'b' * 32, 'sequence': sequence,
                            'observedMonoTime': stamp - 1000, 'driveStartMonoTime': MONO,
                            'settingsFingerprint': fingerprint, 'choice': 'conditionalExperimental',
                            'button': 'lkas', 'press': 'short', 'sourceCarStateMonoTime': stamp,
                            'validUntilMonoTime': stamp + 100_000_000}
        return messaging.log_from_bytes(event.to_bytes())

      stamp = MONO + 50_000_000
      sm.stamp(stamp)
      sm.payloads.update(serialized_scene(stamp=BOOT + 50_000_000))
      planner.update(sm)
      fingerprint = settings_fingerprint(owner.settings.current)
      assert fingerprint is not None
      slc, curve, manual = deque(), deque(), deque()
      event = receipt(1, stamp, fingerprint)
      queue_cruise_event(event, slc, curve, manual)
      self.assertFalse(slc)
      self.assertFalse(curve)
      self.assertIsNone(current_manual_event(manual, stamp + 1_000_000, stamp - 1))
      proposal, _ = owner.sample(sm, cp, planner, now_mono_ns=stamp + 1_000_000,
                                 now_boot_ns=BOOT + 51_000_000, sample_skew_ns=1000,
                                 drive_id=MONO, manual_event=current_manual_event(manual, stamp + 1_000_000, stamp))
      self.assertEqual(owner.manual_state.intent.value, 'force_experimental')
      self.assertTrue(proposal.override_experimental)
      params.put('LKASButtonControl', 0, block=True)
      later = MONO + 100_000_000
      sm.stamp(later)
      sm.payloads.update(serialized_scene(stamp=BOOT + 100_000_000))
      planner.update(sm)
      owner.sample(sm, cp, planner, now_mono_ns=later + 1_000_000, now_boot_ns=BOOT + 101_000_000,
                   sample_skew_ns=1000, drive_id=MONO, manual_event=receipt(2, later, fingerprint))
      self.assertEqual(owner.manual_state.intent.value, 'force_experimental')
      params.put('LKASButtonControl', 5, block=True)
      owner.sample(sm, cp, planner, now_mono_ns=later + 2_000_000, now_boot_ns=BOOT + 102_000_000,
                   sample_skew_ns=1000, drive_id=MONO, manual_event=receipt(2, later, fingerprint))
      self.assertEqual(owner.manual_state.intent.value, 'force_experimental')  # Denied receipt cannot rearm.

  def test_actual_mpc_headway_settings_and_model_lane_join_without_manual_owner(self):
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    cp.pcmCruise = False
    planner = LongitudinalPlanner(cp, init_v=15.0)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      saved = SavedPreferences(mode=ModeChoice.CEM,
                               cem=CEMOptions(speed_mps=20.0, signal_speed_mps=20.0, signal_lane_width_m=3.0))
      params.put(DOCUMENT_KEY, json.loads(encode_preferences(saved)), block=True)
      owner = ConditionalPlannerHost(params)
      payloads = serialized_scene(left_blinker=True, left_edge=-2.0)
      vehicle = messaging.new_message('vehicleParameters', valid=True)
      payloads['vehicleParameters'] = messaging.log_from_bytes(vehicle.to_bytes()).vehicleParameters
      sm = FakeSubMaster(payloads, MONO - 5_000_000)
      first, _ = owner.sample(sm, cp, planner, now_mono_ns=MONO, now_boot_ns=BOOT,
                              sample_skew_ns=1000, drive_id=MONO)
      self.assertIsNone(first.override_experimental)
      for index in range(1, 5):
        event = MONO + index * 50_000_000
        sm.stamp(event)
        sm.payloads.update(serialized_scene(stamp=BOOT + index * 50_000_000, left_blinker=True, left_edge=-2.0))
        planner.update(sm)
        selected = selected_follow_time(planner)
        assert selected is not None
        self.assertAlmostEqual(selected, float(planner.mpc.params[0, 4]))
        proposal, snapshot = owner.sample(sm, cp, planner, now_mono_ns=event + 1_000_000,
                                          now_boot_ns=BOOT + index * 50_000_000 + 1_000_000,
                                          sample_skew_ns=1000, drive_id=MONO)
        self.assertEqual(proposal.settings_revision, snapshot.revision)
        self.assertEqual(proposal.projected.scene.lane_available, False if index == 4 else None)
        self.assertTrue(proposal.override_experimental)  # Explicit nonpersistent saved choice starts at NONE.
        status = owner.status.attach(None, proposal, snapshot, now_ns=event + 1_000_000,
                                     drive_id=MONO, model_ns=event, car_state_ns=event)
        observed = messaging.log_from_bytes(status.to_bytes()).slcState.conditionalMode
        self.assertEqual(observed.settingsRevision, snapshot.revision)
        self.assertTrue(observed.hasOverride)
        self.assertEqual(str(observed.status), 'proposed')

  def test_invalid_native_headway_is_unavailable(self):
    class Native:
      mpc = type('MPC', (), {'params': [[0.0, 0.0, 0.0, 0.0, float('nan')]]})()

    self.assertIsNone(selected_follow_time(Native()))
    self.assertIsNone(selected_follow_time(object()))


if __name__ == '__main__':
  unittest.main()
