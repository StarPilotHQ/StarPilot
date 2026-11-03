#!/usr/bin/env python3
import os
from collections import deque

from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process, set_core_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.ldw import LaneDepartureWarning
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner, NATIVE_PLAN_INPUTS
import openpilot.cereal.messaging as messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.starpilot.speed_limits.planner_host import (
  SLC_ACTIONS, SavedSlcLifecycle, action_from_message, cruise_event_from_message, current_cruise_event, receive_slc_events,
)
from openpilot.starpilot.longitudinal.profile_runtime import ProfileHost
from openpilot.starpilot.longitudinal.lane_change_gap import LaneGapPreferences
from openpilot.starpilot.longitudinal.experimental_release import ConditionalHandoff
from openpilot.starpilot.longitudinal.lead_approach import LeadApproachKey
from openpilot.starpilot.longitudinal.lead_takeoff_preferences import TakeoffPreferences
from openpilot.starpilot.longitudinal.encouragement import NudgePreferences
from openpilot.starpilot.longitudinal.force_stop_runtime import ForceStopRuntime
from openpilot.starpilot.longitudinal.stop_resume import collect_resume
from openpilot.starpilot.longitudinal.lead_approach_runtime import LeadApproachPreferences
from openpilot.starpilot.curve_speed.host import CurveHost
from openpilot.starpilot.curve_speed.runtime import DriverEvent as CurveDriverEvent
from openpilot.starpilot.curve_speed.preferences import PreferenceHost as CurvePreferenceHost
from openpilot.starpilot.curve_speed.status import StatusPublisher as CurveStatusPublisher
from openpilot.starpilot.model_geometry import road_curvature
from openpilot.starpilot.conditional_mode.planner_host import ConditionalPlannerHost
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.projection import ConditionalOwnerContext, ObservedBool
from openpilot.starpilot.conditional_mode.traffic import TrafficOwner, TrafficVerdict
from openpilot.starpilot.controllers.coast import CoastRuntime
from openpilot.starpilot.controllers.mode_actions import ModeActionOwner, KINDS as CONTROLLER_MODE_KINDS, publish_switchback, apply_switchback_gesture
import secrets
from openpilot.starpilot.feature_capabilities import longitudinal_supported
from openpilot.starpilot.conditional_mode.projection import paired_clocks_ns
from openpilot.starpilot.conditional_mode.ui_action import observation as ui_manual_observation
from openpilot.starpilot.longitudinal.cruise_ceiling import CurveCeiling
from openpilot.starpilot.feature_runtime import enabled as feature_enabled, requested as feature_requested, slc_transport_capable
import time


def profile_for_frame(host: ProfileHost | None, sm, CP, now_ns: int, *, traffic_mode: bool | None = False):
  if host is None or not sm['carState'].canValid or sm['carState'].canTimeout:
    return None
  required = ('carState', 'carControl', 'selfdriveState', 'controlsState')
  if not all(sm.valid[s] and sm.alive[s] and 0 <= now_ns - int(sm.logMonoTime[s]) <=
             int(2e9 / SERVICE_LIST[s].frequency) for s in required):
    return None
  return host.sample(now_ns, sm['selfdriveState'].personality, float(sm['carState'].vEgo), CP,
                     traffic_mode=traffic_mode)


def global_braking_for_frame(host: ProfileHost | None, sm, now_ns: int) -> str | None:
  if host is None or not sm['carState'].canValid or sm['carState'].canTimeout:
    return None
  required = ('carState', 'carControl', 'selfdriveState', 'controlsState')
  if not all(sm.valid[s] and sm.alive[s] and 0 <= now_ns - int(sm.logMonoTime[s]) <=
             int(2e9 / SERVICE_LIST[s].frequency) for s in required):
    return None
  return host.sample_global_braking(now_ns)


def _profile_frame_valid(sm, now_ns: int) -> bool:
  if not sm['carState'].canValid or sm['carState'].canTimeout:
    return False
  required = ('carState', 'carControl', 'selfdriveState', 'controlsState')
  if not all(sm.valid[s] and sm.alive[s] and 0 <= now_ns - int(sm.logMonoTime[s]) <=
             int(2e9 / SERVICE_LIST[s].frequency) for s in required):
    return False
  return True


def profile_host_needed(CP, *, profile_enabled: bool, conditional_enabled: bool) -> bool:
  """Conditional Traffic profiles follow acceleration ownership, not media buttons."""
  return profile_enabled or (conditional_enabled and longitudinal_supported(CP))


def traffic_observation(verdict, *, model_ns: int, owner_present: bool = True):
  if type(model_ns) is not int or model_ns <= 0:
    return None
  if verdict is None:
    return None if owner_present else ObservedBool(False, model_ns)
  value = verdict.effective
  if (value is None and verdict.requested is False and verdict.profile_mode is False and
      verdict.reason in ('media_unavailable', 'unassigned', 'off', 'unsupported_car')):
    value = False
  return ObservedBool(value, model_ns) if type(value) is bool else None


def traffic_profile_status(mode: bool | None, target, applied, settings) -> tuple[bool, str]:
  """Report actual post-MPC application, not merely an available target."""
  if mode is not True:
    return False, 'inactive'
  if target is not None and applied is not None:
    return True, 'qualified'
  if target is not None:
    return False, 'target_unapplied'
  reason = getattr(settings, 'reason', 'unavailable')
  return False, 'source_unavailable' if reason == 'valid' else reason


def curve_for_frame(host: CurveHost | None, sm, CP, now_ns: int, *, drive_id: int = 0,
                    event: CurveDriverEvent | None = None, follow_time_s: float | None = None,
                    cruise_ceiling_mps: float | None = None):
  """A diagnostic runtime result is not planner authority by itself."""
  if host is None:
    return None, None
  result = host.sample(sm, CP, now_ns, drive_id=drive_id, event=event, follow_time_s=follow_time_s,
                       cruise_ceiling_mps=cruise_ceiling_mps)
  if result.ceiling_mps is None:
    return None, result
  model_ns = sm.logMonoTime.get('modelV2', 0)
  if type(model_ns) is not int or model_ns <= 0:
    return None, result
  return CurveCeiling(result.ceiling_mps, model_ns), result


def confirm_curve_frame(host: CurveHost | None, planner: LongitudinalPlanner, now_ns: int) -> None:
  if host is not None:
    host.runtime.confirm_applied(now_ns, applied=bool(planner.last_curve_ceiling_applied))


def wheel_override_blocked(force_stop: ForceStopRuntime | None, curve_host: CurveHost | None, *, curve_backlog: bool = False) -> bool:
  # Force Stop can consume RES on the tick it commits, before a forcing plan is published.
  return ((force_stop is not None and (force_stop.policy.can_commit_next_frame or force_stop.plan.speed_ceiling_mps is not None)) or
          (curve_host is not None and (curve_backlog or curve_host.runtime.was_controlling)))


def conditional_handoff_for_frame(host: ConditionalPlannerHost | None, sm, CP, now_ns: int,
                                  drive_id: int) -> ConditionalHandoff | None:
  if host is None or not CP.openpilotLongitudinalControl or CP.passive or CP.dashcamOnly:
    return None
  try:
    stamp = sm.logMonoTime['deviceState']
    device = sm['deviceState']
    if (type(drive_id) is not int or not 0 < drive_id <= stamp <= now_ns or
        not sm.valid['deviceState'] or not sm.alive['deviceState'] or
        now_ns - stamp > int(2e9 / SERVICE_LIST['deviceState'].frequency) or
        not device.started or device.startedMonoTime != drive_id):
      return None
  except (AttributeError, KeyError, TypeError):
    return None
  snapshot = host.settings.refresh(now_ns)
  verdict = host.settings.verdict(snapshot, now_mono_ns=now_ns, drive_id=drive_id)
  if (verdict.status != 'ready' or verdict.safe_mode is not False or verdict.selection is None or
      verdict.selection.choice not in (ModeChoice.CEM, ModeChoice.CCM)):
    return None
  return snapshot.owner_token, snapshot.revision, drive_id, verdict.selection.choice.value


def apply_conditional_stop_hold(host, planner, sm, CP, now_ns: int, drive_id: int, *, now_boot_ns: int | None = None, sample_skew_ns: int = 0) -> bool:
  def unavailable():
    owner = getattr(host, 'stopped_lead_release', None)
    if owner is not None:
      owner.reset()
    return False

  if (host is None or not CP.openpilotLongitudinalControl or CP.passive or CP.dashcamOnly or
      not 0 < drive_id <= now_ns or host.mode.drive_id != drive_id or
      not host.mode.projector.stop_detector.standstill_committed):
    return unavailable()
  settings = host.settings.verdict(host.settings.current, now_mono_ns=now_ns, drive_id=drive_id)
  if settings.safe_mode is True:
    host.mode.projector.stop_detector.reset()
  if settings.status != 'ready' or settings.safe_mode is not False:
    return unavailable()
  if not all(host._fresh_service(sm, name, drive_id, now_ns)
             for name in ('carState', 'carControl', 'selfdriveState')):
    return unavailable()
  state, control, vehicle = sm['selfdriveState'], sm['carControl'], sm['carState']
  if not vehicle.canValid or vehicle.canTimeout:
    return unavailable()
  if not state.enabled or not control.enabled or not control.longActive or vehicle.brakePressed or vehicle.gasPressed:
    host.mode.projector.stop_detector.reset()
    return unavailable()
  if not host._fresh_service(sm, 'modelV2', drive_id, now_ns):
    return unavailable()
  if (type(now_boot_ns) is not int or type(sample_skew_ns) is not int or
      now_boot_ns <= 0 or not 0 <= sample_skew_ns <= 1_000_000 or
      now_boot_ns < now_ns - sample_skew_ns // 2 or not sm.all_checks(NATIVE_PLAN_INPUTS) or
      not 0 < sm['modelV2'].timestampEof <= now_boot_ns or
      now_boot_ns - sm['modelV2'].timestampEof > 150_000_000):
    return unavailable()
  if (now_ns - sm.logMonoTime['modelV2'] > 150_000_000 or
      now_ns - int(sm.recv_time['modelV2'] * 1e9) > 150_000_000):
    return unavailable()
  stop_plan = getattr(planner, 'force_stop_plan', None)
  if (stop_plan is not None and stop_plan.resume_override and stop_plan.model_ns == sm.logMonoTime['modelV2'] and
      not stop_plan.forcing and not stop_plan.should_stop and abs(vehicle.vEgo) < 2.0 and planner.allow_throttle and
      not any(lead.present for lead in (sm['radarState'].leadOne, sm['radarState'].leadTwo))):
    host.mode.projector.stop_detector.reset()
    return False
  from openpilot.starpilot.conditional_mode.stop_release import owner_for
  owner = getattr(host, 'stopped_lead_release', None)
  if owner is None:
    owner = owner_for(CP)
    if owner is not None:
      host.stopped_lead_release = owner
  if owner is not None and owner.sample(sm, CP, planner, host.mode.projector.stop_detector, now_ns=now_ns, drive_id=drive_id):
    detector = host.mode.projector.stop_detector
    detector.committed = detector.standstill_committed = False
    detector.commit_clear_since_s = None
    detector._reset_light()
    return False
  planner.output_should_stop = True
  planner.output_a_target = min(planner.output_a_target, 0.)
  return True


def lead_approach_for_frame(preferences: LeadApproachPreferences | None, sm, CP, now_ns: int) -> LeadApproachKey | None:
  if preferences is None:
    return None
  try:
    device = sm['deviceState']
    stamp = sm.logMonoTime['deviceState']
    if (sm.valid['deviceState'] is not True or sm.alive['deviceState'] is not True or device.started is not True or
        not 0 < device.startedMonoTime <= stamp <= now_ns or
        now_ns - stamp > int(2e9 / SERVICE_LIST['deviceState'].frequency)):
      return None
    return preferences.sample(CP, now_ns, int(device.startedMonoTime))
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return None


def update_curve_frame(planner: LongitudinalPlanner, sm, CP, now_ns: int, *, host: CurveHost | None = None,
                       cruise_ceiling=None, profile_tuning=None, traffic_mode: bool | None = False, drive_id: int = 0,
                       event: CurveDriverEvent | None = None, lane_change_policy=None,
                       global_braking_response: str | None = None, selected_profiles=None, conditional_handoff: ConditionalHandoff | None = None,
                       lead_approach_key: LeadApproachKey | None = None, force_stop_provider=None,
                       faster_lead_takeoff: bool = False, wheel_coast=None, experimental_speed_nudge: bool = True):
  """One model cycle: MPC chooses follow time, then Curve samples and receives winner feedback."""
  if host is None:
    planner.update(sm, cruise_ceiling=cruise_ceiling, profile_tuning=profile_tuning, traffic_mode=traffic_mode,
                   lane_change_policy=lane_change_policy, global_braking_response=global_braking_response, selected_profiles=selected_profiles,
                   conditional_handoff=conditional_handoff, lead_approach_key=lead_approach_key,
                   force_stop_provider=force_stop_provider, faster_lead_takeoff=faster_lead_takeoff, wheel_coast=wheel_coast,
                   takeoff_drive_id=drive_id, now_ns=now_ns, drive_id=drive_id, experimental_speed_nudge=experimental_speed_nudge)
    return None
  results = []
  def provider(follow_time_s: float | None):
    ceiling, result = curve_for_frame(host, sm, CP, now_ns, drive_id=drive_id,
                                      event=event, follow_time_s=follow_time_s,
                                      cruise_ceiling_mps=cruise_ceiling.speed_mps if cruise_ceiling is not None else None)
    results.append(result)
    return ceiling
  planner.update(sm, cruise_ceiling=cruise_ceiling, profile_tuning=profile_tuning,
                 curve_provider=provider, traffic_mode=traffic_mode, lane_change_policy=lane_change_policy,
                 global_braking_response=global_braking_response, selected_profiles=selected_profiles, conditional_handoff=conditional_handoff,
                 lead_approach_key=lead_approach_key, force_stop_provider=force_stop_provider, faster_lead_takeoff=faster_lead_takeoff, wheel_coast=wheel_coast,
                   takeoff_drive_id=drive_id, now_ns=now_ns, drive_id=drive_id, experimental_speed_nudge=experimental_speed_nudge)
  confirm_curve_frame(host, planner, now_ns)
  return results[0] if results else None


def queue_cruise_event(event, slc_events, curve_events, conditional_events=None, traffic_events=None) -> None:
  """One nonconflated Card stream preserves both physical and cruise changes."""
  if not event.valid:
    return
  cruise = event.slcCruiseEvent
  kind = str(cruise.kind)
  if kind == 'conditionalMode':
    if conditional_events is not None:
      conditional_events.append(event)
  elif kind == 'trafficMode':
    if traffic_events is not None:
      traffic_events.append(event)
  elif kind == 'curveAccelPress':
    if (not cruise.longPress and str(cruise.button) == 'accel' and
        not (cruise.sessionId or cruise.decisionId or cruise.presentationId or cruise.commandId)):
      curve_events.append(CurveDriverEvent(str(cruise.producerSessionId), int(cruise.eventId),
                                          int(cruise.observedMonoTime), kind, str(cruise.button), int(event.logMonoTime)))
  else:
    slc_events.append(cruise_event_from_message(event))


def queue_ui_action(event, slc_actions, conditional_actions, mode_actions=None) -> None:
  if not event.valid:
    return
  request = event.slcAction
  kind = str(request.kind)
  if kind in CONTROLLER_MODE_KINDS and mode_actions is not None:
    mode_actions.append(event)
  elif kind == 'conditionalModeCycle':
    conditional_actions.append(event)
  elif kind in SLC_ACTIONS:
    slc_actions.append(action_from_message(event))


def current_ui_manual_event(events, now_ns: int):
  while events:
    event = events.popleft()
    if ui_manual_observation(event, now_ns) is not None:
      return event
  return None


def current_manual_event(events, now_ns: int, car_state_ns: int):
  while events and (not events[0].valid or now_ns > int(events[0].slcCruiseEvent.manualMode.validUntilMonoTime)):
    events.popleft()
  return (events.popleft() if events and
          int(events[0].slcCruiseEvent.manualMode.observedMonoTime) <= now_ns and
          int(events[0].slcCruiseEvent.manualMode.sourceCarStateMonoTime) <= car_state_ns else None)


def current_traffic_event(events, now_ns: int, car_state_ns: int):
  while events and (not events[0].valid or now_ns > int(events[0].slcCruiseEvent.trafficMode.validUntilMonoTime)):
    events.popleft()
  return (events.popleft() if events and
          int(events[0].slcCruiseEvent.trafficMode.observedMonoTime) <= now_ns and
          int(events[0].slcCruiseEvent.trafficMode.sourceCarStateMonoTime) <= car_state_ns else None)


def starpilot_main():
  config_realtime_process(5, Priority.CTRL_LOW)

  cloudlog.info("plannerd is waiting for CarParams")
  params = Params()
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  cloudlog.info("plannerd got CarParams: %s", CP.brand)

  ldw = LaneDepartureWarning()
  longitudinal_planner = LongitudinalPlanner(CP)
  nudge_preferences = NudgePreferences(params)
  # Saved-on features start only for a finalized, supported driving owner. Their
  # current-source and longitudinal authority checks still run every frame.
  slc_available = slc_transport_capable(CP, os.environ)
  profile_replay = feature_enabled(params, CP, 'profile', os.environ)
  curve_replay = feature_enabled(params, CP, 'curve', os.environ)
  conditional_replay = feature_enabled(params, CP, 'conditional', os.environ)
  # A validated Traffic gesture uses its frozen default profile even when
  # CustomPersonalities is off; saved custom category values still require
  # that master switch in ProfileHost.
  traffic_capable = longitudinal_supported(CP) and feature_requested(params, 'conditional')
  traffic_settings = ConditionalSettingsOwner(params) if traffic_capable else None
  profile_request_ns = -1_000_000_000
  profile_host = ProfileHost(params) if profile_host_needed(CP, profile_enabled=profile_replay,
                                                            conditional_enabled=conditional_replay) or traffic_capable else None
  lane_gap_preferences = LaneGapPreferences(params) if CP.openpilotLongitudinalControl else None
  approach_preferences = LeadApproachPreferences(params) if CP.openpilotLongitudinalControl else None
  curve_preferences = CurvePreferenceHost(params) if curve_replay else None
  curve_host = curve_preferences.make_host(replay='REPLAY' in os.environ) if curve_preferences is not None else None
  curve_status = CurveStatusPublisher() if curve_host is not None else None
  force_stop_owner = ForceStopRuntime(params) if CP.openpilotLongitudinalControl else None
  takeoff_preferences = TakeoffPreferences(params) if CP.openpilotLongitudinalControl else None
  stop_resume_sock = messaging.sub_sock("carState", conflate=False) if force_stop_owner is not None else None
  conditional_host = (ConditionalPlannerHost(params, enabled_scene_owners=(frozenset({'traffic_mode'}) if traffic_capable else frozenset()) |
                                                                        frozenset({'forcing_stop', 'plan_forcing_stop'}))
                      if conditional_replay else None)
  traffic_owner = TrafficOwner() if traffic_capable else None
  mode_owner = ModeActionOwner() if not CP.passive and not CP.dashcamOnly and not CP.notCar else None
  mode_session, mode_sequence = secrets.token_hex(16), 0
  wheel_coast = CoastRuntime()
  pending_wheel = deque(maxlen=32)
  pending_modes = deque(maxlen=8)
  pending_switchback = deque(maxlen=8)
  mode_settings = ConditionalSettingsOwner(params) if mode_owner is not None else None
  slc_lifecycle = SavedSlcLifecycle(params, CP, os.environ)
  slc_lifecycle.refresh(0)
  slc_action_sock = (messaging.sub_sock("slcAction", conflate=False)
                     if slc_available or conditional_host is not None or mode_owner is not None else None)
  slc_cruise_sock = messaging.sub_sock("slcCruiseEvent", conflate=False)
  pending_slc_actions = deque(maxlen=64)
  pending_slc_cruise = deque(maxlen=64)
  pending_curve_events = deque(maxlen=64)
  pending_conditional_events = deque(maxlen=64)
  pending_conditional_ui = deque(maxlen=64)
  pending_traffic_events = deque(maxlen=64)
  drive_start_ns = 0
  publish_services = ['longitudinalPlan', 'starpilotLongitudinalPlan', 'driverAssistance']
  if slc_available or curve_host is not None or conditional_host is not None or traffic_owner is not None or mode_owner is not None:
    publish_services.append('slcState')
  pm = messaging.PubMaster(publish_services)
  native_plan_checks = list(NATIVE_PLAN_INPUTS)
  subscribed_services = list(NATIVE_PLAN_INPUTS)
  subscribed_services.append('deviceState')
  if conditional_host is not None:
    subscribed_services.append('starpilotRadarState')
  optional_slc = slc_lifecycle.source_services() if slc_available else []
  if force_stop_owner is not None:
    optional_slc.append('starpilotCarState')
  if force_stop_owner is not None and 'slcVisionObservation' not in optional_slc:
    optional_slc.append('slcVisionObservation')
  subscribed_services.extend(optional_slc)
  subscribed_services.append('starpilotNavigation')
  optional_conditional = ['starpilotNavigation'] + (['starpilotRadarState'] if conditional_host is not None else []) + optional_slc
  sm = messaging.SubMaster(subscribed_services, poll='modelV2',
                           ignore_alive=optional_conditional, ignore_valid=optional_conditional,
                           ignore_avg_freq=optional_conditional)

  set_core_affinity([3, 5])

  try:
    while True:
      if 'REPLAY' not in os.environ:
        # Due saved-setting verification can block on Params. Complete it
        # before acquiring the model and fast-source batch, not after that
        # batch has already begun aging toward the CEM freshness deadline.
        slc_lifecycle.refresh(time.monotonic_ns(), pending_actions=pending_slc_actions, pending_cruise=pending_slc_cruise)
      sm.update()
      if sm.updated['modelV2']:
        # Only explicit replay uses the recorded model timeline. In a live host,
        # modelV2 may lag newer inputs in the same SubMaster update.
        now_ns = int(sm.logMonoTime['modelV2']) if 'REPLAY' in os.environ else time.monotonic_ns()
        if 'REPLAY' in os.environ:
          slc_lifecycle.refresh(now_ns, pending_actions=pending_slc_actions, pending_cruise=pending_slc_cruise)
        current_start_ns = (int(sm['deviceState'].startedMonoTime)
                            if 'deviceState' in subscribed_services and sm.valid['deviceState'] and
                            sm.alive['deviceState'] and 0 < sm.logMonoTime['deviceState'] <= now_ns else 0)
        if stop_resume_sock is not None:
          collect_resume(force_stop_owner.resume, stop_resume_sock, messaging.recv_one_or_none,
                         now_ns=now_ns, drive_id=current_start_ns,
                         receive_clock=time.monotonic_ns if 'REPLAY' not in os.environ else None)
        planner_status = None
        drive_changed = current_start_ns > 0 and drive_start_ns > 0 and current_start_ns != drive_start_ns
        if slc_cruise_sock is not None:
          for event in receive_slc_events(slc_cruise_sock):
            if str(event.slcCruiseEvent.kind) == 'wheelAction' and event.valid:
              pending_wheel.append(event)
              continue
            if str(event.slcCruiseEvent.kind) == 'switchbackMode' and event.valid:
              pending_switchback.append(event)
              continue
            queue_cruise_event(event, pending_slc_cruise, pending_curve_events,
                               pending_conditional_events, pending_traffic_events)
        if drive_changed:
          pending_slc_cruise.clear()
          pending_curve_events.clear()
          pending_conditional_events.clear()
          pending_conditional_ui.clear()
          pending_traffic_events.clear()
          pending_modes.clear()
          pending_wheel.clear()
          pending_switchback.clear()
          if mode_owner is not None:
            mode_owner.reset(current_start_ns)
          if traffic_owner is not None:
            traffic_owner.reset()
        if slc_action_sock is not None:
          for event in receive_slc_events(slc_action_sock):
            queue_ui_action(event, pending_slc_actions, pending_conditional_ui, pending_modes)
        waiting_wheel = deque(maxlen=32)
        while pending_wheel:
          event = pending_wheel.popleft()
          wire = event.slcCruiseEvent.wheelAction
          if (int(wire.sourceCarStateMonoTime) > int(sm.logMonoTime['carState']) or
              int(wire.sourceCarControlMonoTime) > int(sm.logMonoTime['carControl'])) and now_ns <= int(wire.validUntilMonoTime):
            waiting_wheel.append(event)
          else:
            wheel_coast.receive(event, params, CP, sm, now_ns=now_ns)
        pending_wheel = waiting_wheel
        # Curve observes the press before SLC sees the release. A queued Curve press can still own the tap.
        curve_event = current_cruise_event(pending_curve_events, now_ns)
        slc_output = slc_lifecycle.step(sm, now_ns, pending_slc_actions, pending_slc_cruise, drive_changed=drive_changed,
          wheel_override_blocked=wheel_override_blocked(force_stop_owner, curve_host, curve_backlog=bool(pending_curve_events)))
        cruise_ceiling = slc_output.result.ceiling if slc_output is not None else None
        if slc_output is not None:
          planner_status = slc_output.message
        needs_clock = conditional_host is not None or traffic_owner is not None or force_stop_owner is not None or mode_owner is not None
        clock_pair = paired_clocks_ns() if needs_clock and 'REPLAY' not in os.environ else None
        controller_traffic_toggle = bool(wheel_coast.traffic_toggles % 2)
        wheel_coast.traffic_toggles = 0
        if controller_traffic_toggle and mode_owner is not None:
          mode_owner.requested["traffic"] = not mode_owner.requested["traffic"]
        if mode_owner is not None and clock_pair is not None:
          while pending_switchback:
            apply_switchback_gesture(mode_owner, pending_switchback.popleft(), params=params,
              settings=mode_settings, sm=sm, cp=CP, now_ns=clock_pair[0], now_boot_ns=clock_pair[1])
        if mode_owner is not None:
          while pending_modes:
            command = pending_modes.popleft()
            if mode_owner.update(command, sm, CP, now_ns=now_ns):
              if str(command.slcAction.kind) == 'trafficModeToggle':
                controller_traffic_toggle = not controller_traffic_toggle
              elif str(command.slcAction.kind) == 'forceCoastToggle':
                wheel_coast.owner.toggle(2, long_active=bool(sm['carControl'].longActive))
              elif str(command.slcAction.kind) == 'pulseGlideToggle':
                wheel_coast.owner.toggle(14, long_active=bool(sm['carControl'].longActive))
        traffic_verdict = None
        traffic_mode: bool | None = False
        if traffic_owner is not None and clock_pair is not None:
          traffic_now_ns, traffic_boot_ns, _ = clock_pair
          traffic_event = current_traffic_event(pending_traffic_events, traffic_now_ns, int(sm.logMonoTime['carState']))
          if traffic_owner.controller_source and mode_owner is not None and traffic_event is not None:
            if apply_switchback_gesture(mode_owner, traffic_event, params=params, settings=mode_settings,
                sm=sm, cp=CP, now_ns=traffic_now_ns, now_boot_ns=traffic_boot_ns, mode='traffic'):
              controller_traffic_toggle = not controller_traffic_toggle
          traffic_verdict = traffic_owner.sample(
            traffic_event,
            params=params, settings=traffic_settings, sm=sm, cp=CP,
            drive_id=current_start_ns, now_mono_ns=traffic_now_ns, now_boot_ns=traffic_boot_ns,
            controller_toggle=controller_traffic_toggle,
            controller_requested=mode_owner.sample('traffic', sm, CP, now_ns=traffic_now_ns).requested
            if mode_owner is not None and controller_traffic_toggle else None)
          traffic_mode = traffic_verdict.profile_mode
        elif traffic_owner is not None:
          traffic_owner.reset()
          traffic_mode = None
        if now_ns - profile_request_ns >= 1_000_000_000 or now_ns < profile_request_ns:
          profile_request_ns = now_ns
          profile_replay = feature_enabled(params, CP, 'profile', os.environ)
          if profile_replay and profile_host is None:
            profile_host = ProfileHost(params)
        # A Traffic-only host may resolve the accepted Traffic defaults, but
        # cannot turn on ordinary saved profiles without their existing gate.
        profile_tuning = (profile_for_frame(profile_host, sm, CP, now_ns, traffic_mode=traffic_mode)
                          if profile_replay or traffic_mode is not False else None)
        # selected response supersedes the old global-only channel.
        global_braking_response = None
        selected_profiles = (profile_host.sample_selected(now_ns, sm['selfdriveState'].personality,
                              sm['carState'].vEgo, CP, traffic_mode=traffic_mode, legacy=profile_tuning)
                             if profile_replay and profile_host is not None and
                             _profile_frame_valid(sm, now_ns) else None)
        lane_change_policy = lane_gap_preferences.sample(now_ns) if lane_gap_preferences is not None else None
        lead_approach_key = lead_approach_for_frame(approach_preferences, sm, CP, now_ns)
        if curve_preferences is not None and curve_host is not None:
          curve_preferences.refresh(curve_host, now_ns)
        conditional_handoff = (conditional_handoff_for_frame(conditional_host, sm, CP, clock_pair[0], current_start_ns)
                               if clock_pair is not None else None)
        wheel_coast.conditional_stop = bool(conditional_handoff is not None and conditional_host is not None and
                                            conditional_host.mode.projector.stop_detector.committed)
        faster_lead_takeoff = takeoff_preferences.sample(now_ns) if takeoff_preferences is not None else False
        force_stop_provider = None
        if force_stop_owner is not None:
          if clock_pair is None:
            force_stop_owner.reset()
          else:
            def force_stop_provider(follow_seconds, clock_pair=clock_pair, current_start_ns=current_start_ns,
                                    traffic_mode=traffic_mode, faster_lead_takeoff=faster_lead_takeoff):
              return force_stop_owner.sample(sm, CP, now_ns=clock_pair[0], now_boot_ns=clock_pair[1],
                                              drive_id=current_start_ns, follow_seconds=follow_seconds,
                                              traffic_mode=traffic_mode, takeoff_enabled=faster_lead_takeoff)
        curve_result = update_curve_frame(longitudinal_planner, sm, CP, now_ns, host=curve_host,
                                          cruise_ceiling=cruise_ceiling,
                                          profile_tuning=profile_tuning,
                                          traffic_mode=traffic_mode, lane_change_policy=lane_change_policy,
                                          global_braking_response=global_braking_response, selected_profiles=selected_profiles,
                                          conditional_handoff=conditional_handoff,
                                          lead_approach_key=lead_approach_key, force_stop_provider=force_stop_provider,
                                          drive_id=current_start_ns, event=curve_event,
                                          faster_lead_takeoff=faster_lead_takeoff, wheel_coast=(wheel_coast, params),
                                          experimental_speed_nudge=nudge_preferences.sample(now_ns))
        if curve_preferences is not None and curve_host is not None and curve_result is not None:
          curve_preferences.persist(curve_host, curve_result, now_ns)
          assert curve_status is not None
          curve_geometry = road_curvature(sm['modelV2'], sm['carState'].vEgo) if curve_result.candidate_mps is not None else None
          planner_status = curve_status.attach(planner_status, curve_host, curve_result, longitudinal_planner,
                                               now_ns=now_ns, model_ns=int(sm.logMonoTime['modelV2']),
                                               persistence_status=curve_preferences.status,
                                               road_curvature=curve_geometry[0] if curve_geometry is not None else None)
        if conditional_host is not None and 'REPLAY' not in os.environ:
          if drive_changed:
            conditional_host.reset()
          # A recorded BOOTTIME pair is not part of the current replay input.
          # Camera EOF cannot be used to invent one; replay remains unavailable.
          if clock_pair is not None:
            conditional_now_ns, conditional_boot_ns, conditional_skew_ns = clock_pair
            proposal, snapshot = conditional_host.sample(
              sm, CP, longitudinal_planner, now_mono_ns=conditional_now_ns, now_boot_ns=conditional_boot_ns,
              sample_skew_ns=conditional_skew_ns, drive_id=current_start_ns,
              cruise_ceiling_mps=cruise_ceiling.speed_mps if cruise_ceiling is not None else None,
              owner_context=ConditionalOwnerContext(
                traffic_mode=traffic_observation(traffic_verdict, model_ns=int(sm.logMonoTime['modelV2']),
                                                 owner_present=traffic_owner is not None),
                forcing_stop=ObservedBool(longitudinal_planner.force_stop_plan.forcing, int(sm.logMonoTime['modelV2'])),
                plan_forcing_stop=ObservedBool(longitudinal_planner.force_stop_plan.forcing, int(sm.logMonoTime['modelV2']))),
              native_plan_valid=bool(sm.all_checks(native_plan_checks)),
              manual_event=current_manual_event(pending_conditional_events, conditional_now_ns,
                                                int(sm.logMonoTime['carState'])),
              ui_event=current_ui_manual_event(pending_conditional_ui, conditional_now_ns),
            )
            planner_status = conditional_host.status.attach(
              planner_status, proposal, snapshot, now_ns=conditional_now_ns,
              drive_id=current_start_ns, model_ns=int(sm.logMonoTime['modelV2']),
              car_state_ns=int(sm.logMonoTime['carState']),
            )
        apply_conditional_stop_hold(conditional_host, longitudinal_planner, sm, CP,
                                    clock_pair[0] if clock_pair is not None else now_ns, current_start_ns,
                                    now_boot_ns=clock_pair[1] if clock_pair is not None else None,
                                    sample_skew_ns=clock_pair[2] if clock_pair is not None else 0)
        if traffic_owner is not None:
          traffic_profile_ready, traffic_profile_reason = traffic_profile_status(
            traffic_mode, profile_tuning, longitudinal_planner.last_profile,
            getattr(profile_host, 'settings', None))
          planner_status = traffic_owner.attach(
            planner_status, traffic_verdict or TrafficVerdict(False, None, 'clock_unavailable', -1, 0, 0, '', ''),
            now_ns=now_ns, drive_id=current_start_ns,
            profile_target_ready=traffic_profile_ready,
            profile_reason=traffic_profile_reason)
        if mode_owner is not None:
          mode_sequence += 1
          planner_status = publish_switchback(planner_status,
            mode_owner.sample('switchback', sm, CP, now_ns=now_ns), session=mode_session,
            sequence=mode_sequence, now_ns=now_ns, source_car_control_ns=int(sm.logMonoTime['carControl']))
        if planner_status is not None:
          if planner_status.slcState.wheelOverrideSince and wheel_override_blocked(force_stop_owner, curve_host):
            planner_status.slcState.wheelOverrideSince = 0
          pm.send('slcState', planner_status)
        if current_start_ns > 0:
          drive_start_ns = current_start_ns
        longitudinal_planner.publish(sm, pm)

        ldw.update(sm.frame, sm['modelV2'], sm['carState'], sm['carControl'])
        msg = messaging.new_message('driverAssistance')
        msg.valid = sm.all_checks(native_plan_checks)
        msg.driverAssistance.leftLaneDeparture = ldw.left
        msg.driverAssistance.rightLaneDeparture = ldw.right
        pm.send('driverAssistance', msg)

  finally:
    if conditional_host is not None:
      conditional_host.close()
    if curve_preferences is not None:
      curve_preferences.close()

def main():
  # Manager stops plannerd offroad. Select once before constructing any planner
  # or StarPilot feature host; a saved change takes effect on the next drive.
  from openpilot.starpilot.longitudinal.planner_selection import run_selected
  return run_selected(Params(), starpilot_main)


if __name__ == "__main__":
  main()
