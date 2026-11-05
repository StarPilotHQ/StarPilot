#!/usr/bin/env python3
import math
import importlib
import os
import time
from numbers import Number

from openpilot.cereal import log
from opendbc.car.structs import car
import openpilot.cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.hardware import HARDWARE
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, DT_CTRL, Priority, Ratekeeper, set_core_affinity
from openpilot.selfdrive.controls.lib.cycle_timing import ControlCycleTiming, PUBLISH_PHASES
from openpilot.common.swaglog import cloudlog

from opendbc.car.car_helpers import interfaces
from opendbc.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib.lateral_controller_factory import create_lateral_controller
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.selfdrive.modeld.constants import LAT_SMOOTH_SECONDS
from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose
from openpilot.starpilot.car.hyundai.lateral_fault import LateralFaultLatch
from openpilot.starpilot.feature_runtime import enabled as feature_enabled
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
from openpilot.starpilot.lateral.pause import LateralPause, read_settings as read_lateral_pause
from openpilot.starpilot.lateral.model_turn_assist import ModelTurnAssist, update_twitch_guard
from openpilot.starpilot.aol.runtime import (
  current_axis,
  current_native,
  ordinary_lateral_requested,
  ordinary_axis_acknowledged,
  refresh_expired_lateral_snapshot,
)
from openpilot.starpilot.aol.transition_diagnostic import TransitionDiagnostic
from openpilot.starpilot.aol.vehicle import policy_for as axis_policy_for, ordinary_axis_request_allowed, allow_lateral_onset
from openpilot.starpilot.lateral.lane_centering import (
  LaneCenteringController,
  LaneCenteringInput,
  LaneCenteringRequest,
  LaneCenteringResult,
)
from openpilot.starpilot.lateral.lane_feedback import feedback_message
from openpilot.starpilot.lateral.lane_clearance import current_delay, interface_dimensions
from openpilot.starpilot.lateral.lane_change_preferences import LiveLaneChangePolicy
from openpilot.starpilot.lateral.lane_change_smoothing import LaneChangeSmoother, limit_rate
from openpilot.starpilot.lateral.lane_runtime import LaneCenteringHost, runtime_supported as lane_runtime_supported
from openpilot.starpilot.lateral.torque_runtime import TorqueHost, runtime_enabled as torque_runtime_enabled
from openpilot.starpilot.lateral.controller_selection import learning_allowed, read_selection
from openpilot.starpilot.lateral.gain_runtime import create_gain_owner
from openpilot.starpilot.flm.global_runtime import GlobalRuntime
from openpilot.starpilot.flm.evidence_hooks import control_message
from openpilot.starpilot.flm.application_wire import write_application
from openpilot.starpilot.longitudinal.inputs import LongitudinalInputs
from openpilot.starpilot.longitudinal.output_max import final_output

State = log.SelfdriveState.OpenpilotState
LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

ACTUATOR_FIELDS = tuple(car.CarControl.Actuators.schema.fields.keys())


class Controls:
  def __init__(self, *, predict_lane_clearance: bool = False) -> None:
    if type(predict_lane_clearance) is not bool:
      raise TypeError("Invalid lane-clearance selection")
    self.params = Params()
    cloudlog.info("controlsd is waiting for CarParams")
    self.CP = messaging.log_from_bytes(self.params.get("CarParams", block=True), car.CarParams)
    cloudlog.info("controlsd got CarParams")
    from openpilot.starpilot.car.subaru.smoothing import read_frozen

    self.subaru_smoothing = read_frozen(self.CP, self.params)

    self.CI = interfaces[self.CP.carFingerprint](self.CP)
    self.hyundai_lateral_fault = LateralFaultLatch(self.CP.carFingerprint)
    self.lateral_controller_selection = read_selection(self.params, self.CP)
    self.vehicle_startup_preferences = VehicleStartupPreferences.read(self.params, enabled=self.params.get_bool("OpenpilotEnabledToggle"))
    manual_effects, manual_status = self._configure_manual_tune()
    self.aol_replay = feature_enabled(self.params, self.CP, 'aol', os.environ)
    self.aol_transition_diagnostic = TransitionDiagnostic() if self.aol_replay and self.CP.brand == 'gm' else None
    self.ordinary_axis_ack_required = axis_policy_for(self.CP).ordinary_axis_ack_required
    self.longitudinal_inputs = LongitudinalInputs(self.CP, self.params, lambda: self.sm)

    services = [
      'lateralDelay',
      'vehicleParameters',
      'lateralTorqueParameters',
      'modelV2',
      'selfdriveState',
      'extrinsicsCalibration',
      'deviceMotion',
      'longitudinalPlan',
      'lateralManeuverPlan',
      'carState',
      'carOutput',
      'driverMonitoringState',
      'onroadEvents',
      'driverAssistance',
    ] + (['aolAxisState', 'aolSafetyWire'] if self.aol_replay or self.ordinary_axis_ack_required else [])
    optional = list(
      dict.fromkeys(
        self.longitudinal_inputs.optional_services + ['starpilotCarState', 'deviceState', 'autoSteeringLearning', 'starpilotSteeringTracking', 'pandaStates']
      )
    )
    if optional:
      ignore = set(optional)
      self.sm = messaging.SubMaster(services + optional, poll='selfdriveState', ignore_alive=ignore, ignore_valid=ignore, ignore_avg_freq=ignore)
    else:
      self.sm = messaging.SubMaster(services, poll='selfdriveState')
    self.pm = messaging.PubMaster(['carControl', 'controlsState', 'starpilotLateralState'])

    self.steer_limited_by_safety = False
    self.curvature = 0.0
    self.desired_curvature = 0.0
    self.model_turn_assist = ModelTurnAssist()
    self.lane_centering_controller = (
      LaneCenteringController(body=interface_dimensions(self.CI, self.CP), body_required=True) if predict_lane_clearance else LaneCenteringController()
    )
    self.lateral_pause = LateralPause(read_lateral_pause(self.params), self.params)
    self.lane_change_preferences = LiveLaneChangePolicy(self.params, now_ns=0 if os.getenv('REPLAY') == '1' else time.monotonic_ns())
    self.lane_change_policy = self.lane_change_preferences.applied
    self.lane_change_smoother = LaneChangeSmoother()
    self.lane_centering_host = LaneCenteringHost(self.params) if lane_runtime_supported(self.CP) else None
    self.torque_host = (
      TorqueHost(self.params, self.CP, allow_learning=(self.torque_learning_allowed if self.lateral_controller_selection.policy is not None else None))
      if torque_runtime_enabled(self.CP, self.params)
      else None
    )
    self.last_lane_centering_result: LaneCenteringResult | None = None
    self.lane_centering_applied = 0.0
    self.lane_centering_inactive_reason = 'inactive'

    self.pose_calibrator = PoseCalibrator()
    self.calibrated_pose: Pose | None = None

    self.lateral_gain_owner = None
    self.LoC = LongControl(self.CP, startup_preferences=self.vehicle_startup_preferences)
    self.VM = VehicleModel(self.CP)
    self.LaC: LatControl
    self.LaC = create_lateral_controller(
      self.CP, self.CI, DT_CTRL, mode=self.lateral_controller_selection.mode, turn_assist=self.vehicle_startup_preferences.turn_assist
    )
    if isinstance(self.LaC, LatControlTorque):
      # Only the actual startup owner reads configuration; replay clones stay pure.
      if manual_effects is not None and manual_effects.profile.context[1] != str(self.LaC.controller_mode):
        manual_effects, manual_status = None, 'context_changed'
      self.LaC.manual_flm_effects, self.LaC.manual_flm_status = manual_effects, manual_status
      self.lateral_gain_owner = create_gain_owner(self.params, self.CP, self.LaC)
      cloudlog.info(
        {
          "event": "torque controller selected",
          "controller": self.LaC.controller_mode.value,
          "source": self.lateral_controller_selection.source,
          "vehicle": self.CP.carFingerprint,
          "policy": self.LaC.controller_policy,
          "automaticLearning": self.torque_learning_allowed,
          "startupParameterSource": "vehicle",
          "manualCoordinateStatus": self.LaC.manual_flm_status,
          "startupTorque": {
            "factor": self.LaC.torque_params.latAccelFactor,
            "offset": self.LaC.torque_params.latAccelOffset,
            "friction": self.LaC.torque_params.friction,
          },
          "startupPidGain": [list(row) for row in self.LaC.pid._k_p],
          "startupPidLimits": [self.LaC.pid.neg_limit, self.LaC.pid.pos_limit],
        }
      )

    from openpilot.starpilot.flm.live_trial_store import load_confirmed_history
    from openpilot.starpilot.lateral.learning_preferences import read_preference

    preference = read_preference(self.params, self.CP)
    route_effects, self.route_flm_status = self._configure_route_tune(manual_status)
    self.global_learning = (
      GlobalRuntime(self.CP, self.CI, self.LaC, confirmed_live_history=load_confirmed_history(self.params)) if preference.valid and preference.enabled else None
    )
    # Load optional capture and live-trial code before the first control command.
    # Engagement must not compile/import replay and feedback modules in the loop.
    if self.global_learning is not None and self.global_learning.descriptor is not None:
      try:
        for module in ('controller_replay', 'physical_input_observation', 'feedback_wire', 'live_trial_wire', 'live_tracking_response', 'route_recording'):
          importlib.import_module('openpilot.starpilot.flm.' + module)
      except Exception:
        self._discard_global_learning()

    from openpilot.starpilot.lateral.controller_selection import model_turn_assist_supported

    assist_enabled = self.vehicle_startup_preferences.turn_assist and model_turn_assist_supported(self.CP)
    self.turn_assist_enabled = lambda: assist_enabled

  def _configure_route_tune(self, manual_status):
    from openpilot.starpilot.flm.offroad_runtime import configure

    owner, status = configure(self.CP, self.CI, self.LaC, self.params, manual_status=manual_status)
    self.LaC.route_flm_effects = owner
    if owner is not None and owner.scope.controller_domain == 'torque_pid':
      from openpilot.starpilot.flm.pid_stages import PidObservation

      self.LaC.route_flm_observation = PidObservation()
    # Route configuration is separate from live admission/ACK/lease history.
    # A configured route map cannot be overlaid with a concurrent live trial.
    return owner, status

  def _configure_manual_tune(self):
    manual_effects, manual_status = None, 'not_configured'
    if self.CP.lateralTuning.which() == 'torque' and str(self.CP.steerControlType) == 'torque':
      from openpilot.starpilot.flm.manual_coordinates import load as load_manual_coordinates

      manual_effects, manual_status = load_manual_coordinates(self.CP, self.lateral_controller_selection.mode, self.params)
    self.torque_learning_allowed = bool(
      manual_status == 'not_configured' and learning_allowed(self.params, self.CP, selection=self.lateral_controller_selection)
    )
    return manual_effects, manual_status

  def update(self, timeout: int = 15):
    self.sm.update(timeout)
    if self.sm.updated["extrinsicsCalibration"]:
      self.pose_calibrator.feed_extrinsics_calibration(self.sm['extrinsicsCalibration'])
    if self.sm.updated["deviceMotion"]:
      device_motion = Pose.from_device_motion(self.sm['deviceMotion'])
      self.calibrated_pose = self.pose_calibrator.build_calibrated_pose(device_motion)

  def refresh_lane_change_policy(self, carstate, model_v2, now_ns):
    self.lane_change_policy = self.lane_change_preferences.sample(
      now_ns, signaling=bool(carstate.leftBlinker or carstate.rightBlinker), maneuver_active=model_v2.meta.laneChangeState != LaneChangeState.off
    )
    return self.lane_change_policy

  def _discard_global_learning(self):
    route = getattr(self.LaC, 'route_flm_effects', None)
    if route is not None:
      route.admitted_ns = 0
      route.frame_allowed = False
    automatic = getattr(self, 'global_learning', None)
    self.global_learning = None
    if automatic is not None:
      try:
        automatic._neutral('optional_metadata_failed')
      except Exception:
        pass  # Hooks are detached below even if optional cleanup failed.
      # These are optional metadata fields on the real GlobalRuntime object.
      automatic.outcome = automatic.frame = automatic.profile = automatic.validation = None
      automatic.eligible = False
    # Detach only optional hooks. Native gains/PID/filter state and every core
    # controller/authority gate remain owned by their existing callers.
    for name in ('automatic_flm_stages', 'automatic_flm_centre', 'automatic_flm_observation', 'automatic_flm_adapter'):
      self.LaC.__dict__.pop(name, None)

  def state_control(self, *, lane_centering: LaneCenteringRequest | None = None):
    cycle_timing = getattr(self, 'control_cycle_timing', None)
    if cycle_timing is not None:
      cycle_timing.start()
    diagnostic_observer = getattr(self, 'aol_transition_diagnostic', None)
    diagnostic_entry = diagnostic_gate = diagnostic_native = None
    diagnostic_reason = ''
    diagnostic_initial_lateral = diagnostic_onset = True
    lateral_inputs_refreshed = False
    late_gm_refresh = False
    refresh_combined_long = False
    if self.aol_replay and os.getenv('REPLAY') != '1' and getattr(self, 'aol_previous_lateral_active', False):
      refresh_now_ns = time.monotonic_ns()
      if diagnostic_observer is not None:
        diagnostic_entry = diagnostic_observer.capture(self.sm, refresh_now_ns)
      late_gm_refresh = self.CP.brand == 'gm'
      if not late_gm_refresh:
        lateral_inputs_refreshed = refresh_expired_lateral_snapshot(self.sm, now_ns=refresh_now_ns)
    if (
      self.lane_centering_host is not None and os.getenv('REPLAY') != '1' and self.lane_centering_host.inputs_need_refresh(self.sm, now_ns=time.monotonic_ns())
    ):
      self.update(0)
    CS = self.sm['carState']
    self.flm_native_witness = None
    if self.lateral_gain_owner is not None:
      self.lateral_gain_owner.refresh()

    # Update VehicleModel
    lp = self.sm['vehicleParameters']
    x = max(lp.stiffnessFactor, 0.1)
    sr = max(lp.steerRatio, 0.1)
    torque_extension = getattr(self.LaC, 'starpilot_extension', None)
    if torque_extension is not None:
      sr = torque_extension.vehicle_model_ratio(sr, CS.vEgo)
    self.VM.update_params(x, sr)

    steer_angle_without_offset = math.radians(CS.steeringAngleDeg - lp.angleOffsetDeg)
    self.curvature = -self.VM.calc_curvature(steer_angle_without_offset, CS.vEgo, lp.roll)

    # Update Torque Params
    if isinstance(self.LaC, LatControlTorque) and self.torque_host is None and self.torque_learning_allowed:
      torque_params = self.sm['lateralTorqueParameters']
      if self.sm.all_checks(['lateralTorqueParameters']) and torque_params.useParams:
        from openpilot.starpilot.flm.native_baseline import apply_native_refinement

        apply_native_refinement(
          self.LaC,
          self.CP,
          self.sm,
          now_ns=int(self.sm.logMonoTime['carState']) if os.getenv('REPLAY') == '1' else time.monotonic_ns(),
          apply=lambda: self.LaC.update_torque_parameters(
            torque_params.latAccelFactorFiltered, torque_params.latAccelOffsetFiltered, torque_params.frictionCoefficientFiltered
          ),
        )

    long_plan = self.sm['longitudinalPlan']
    model_v2 = self.sm['modelV2']

    CC = car.CarControl.new_message()
    CC.enabled = self.sm['selfdriveState'].enabled

    # Check which actuators can be enabled
    standstill = abs(CS.vEgo) <= max(self.CP.minSteerSpeed, 0.3) or CS.standstill
    lateral_requested = self.sm['selfdriveState'].enabled and self.sm['selfdriveState'].active
    CC.latActive = ordinary_lateral_requested(self.sm['selfdriveState'].active, CS, self.CP)
    CC.longActive = CC.enabled and not any(e.overrideLongitudinal for e in self.sm['onroadEvents']) and self.CP.openpilotLongitudinalControl
    CC.longActive = self.longitudinal_inputs.qualify_active(CC.longActive)
    if self.aol_replay:
      now_ns = int(self.sm.logMonoTime['carState']) if os.getenv('REPLAY') == '1' else time.monotonic_ns()
      if late_gm_refresh:
        # Use the authority check's clock sample: a proof can expire after the
        # earlier preparation/qualification work, even when it was fresh on entry.
        previous_axis = self.sm['aolAxisState']
        refresh_combined_long = bool(CC.longActive and previous_axis.longitudinalActive and previous_axis.desiredLongitudinal)
        lateral_inputs_refreshed = refresh_expired_lateral_snapshot(self.sm, now_ns=now_ns, allow_combined=True)
        if lateral_inputs_refreshed:
          CS = self.sm['carState']
          CC.enabled = self.sm['selfdriveState'].enabled
          standstill = abs(CS.vEgo) <= max(self.CP.minSteerSpeed, 0.3) or CS.standstill
          steer_angle_without_offset = math.radians(CS.steeringAngleDeg - lp.angleOffsetDeg)
          self.curvature = -self.VM.calc_curvature(steer_angle_without_offset, CS.vEgo, lp.roll)
          now_ns = time.monotonic_ns()
      axis = current_axis(self.sm, now_ns=now_ns)
      native = current_native(self.sm, self.CP, now_ns=now_ns, axis_session_id=str(axis.sessionId) if axis is not None else None)
      self.flm_native_witness = native
      acknowledged = axis is not None and native is not None and axis.nativeAcknowledged
      lateral_requested = bool(axis.desiredLateral) if axis is not None else None
      CC.latActive = bool(
        acknowledged
        and axis.lateralActive
        and bool(axis.desiredLateral) == bool(native.requestedLateral)
        and native.lateralAllowed
        and not CS.steerFaultTemporary
        and not CS.steerFaultPermanent
        and (not standstill or self.CP.steerAtStandstill)
      )
      CC.longActive = bool(
        acknowledged
        and axis.longitudinalActive
        and bool(axis.desiredLongitudinal) == bool(native.requestedLongitudinal)
        and native.longitudinalAllowed
        and self.CP.openpilotLongitudinalControl
        and not any(e.overrideLongitudinal for e in self.sm['onroadEvents'])
      )

      if diagnostic_entry is not None:
        diagnostic_gate = diagnostic_observer.capture(self.sm, now_ns)
        diagnostic_native = native
        diagnostic_initial_lateral = bool(CC.latActive)
        if not diagnostic_initial_lateral:
          diagnostic_reason = 'lateral_gate'

    elif getattr(self, 'ordinary_axis_ack_required', False):
      now_ns = int(self.sm.logMonoTime['carState']) if os.getenv('REPLAY') == '1' else time.monotonic_ns()
      CC.latActive = bool(
        CC.latActive
        and CS.canValid
        and not CS.canTimeout
        and ordinary_axis_request_allowed(self.CP, CS)
        and ordinary_axis_acknowledged(self.sm, self.CP, now_ns=now_ns, on_validated_native=lambda native: setattr(self, 'flm_native_witness', native))
      )

    if lateral_inputs_refreshed:
      # Fresh receipts can continue a requested GM LONG axis. A LAT-only refresh
      # cannot start LONG, and a newly received physical veto still wins.
      CC.latActive = bool(CC.latActive and CS.canValid and not CS.canTimeout)
      CC.longActive = bool(refresh_combined_long and CC.enabled and CC.longActive and CS.canValid and not CS.canTimeout)

    fault_latch = getattr(self, 'hyundai_lateral_fault', None)
    if fault_latch is not None:
      latched = fault_latch.update(requested=lateral_requested, temporary_fault=bool(CS.steerFaultTemporary), cruise_enabled=bool(CS.cruiseState.enabled))
      CC.latActive = bool(CC.latActive and not latched)

    CC.latActive = allow_lateral_onset(
      self.CP,
      requested=bool(CC.latActive),
      normal_enabled=bool(CC.enabled),
      steering_pressed=bool(CS.steeringPressed),
      previous_active=bool(getattr(self, 'aol_previous_lateral_active', False)),
    )

    if diagnostic_entry is not None:
      diagnostic_onset = bool(CC.latActive)

    pause_now_ns = int(self.sm.logMonoTime['carState']) if os.getenv('REPLAY') == '1' else time.monotonic_ns()
    self.lateral_pause.refresh(pause_now_ns)
    pause_allowed = True
    if self.lateral_pause.settings.speed_mps > 0:
      device = self.sm['deviceState']
      device_ns = int(self.sm.logMonoTime['deviceState'])
      pause_drive = (
        int(device.startedMonoTime)
        if (
          self.sm.seen['deviceState']
          and self.sm.alive['deviceState']
          and self.sm.valid['deviceState']
          and device.started
          and 0 < int(device.startedMonoTime) < device_ns <= pause_now_ns
          and pause_now_ns - device_ns <= 2_000_000_000
        )
        else 0
      )
      pause_source = int(self.sm.logMonoTime['carState']) if (self.sm.seen['carState'] and self.sm.alive['carState'] and self.sm.valid['carState']) else 0
      pause_allowed = self.lateral_pause.allowed(CS, now_ns=pause_now_ns, source_ns=pause_source, drive_id=pause_drive)

    CC.latActive = bool(CC.latActive and pause_allowed)

    if self.torque_host is not None:
      # The optional source must not keep an active torque request through lost car state.
      CC.latActive = bool(CC.latActive and CS.canValid and not CS.canTimeout)
      now_ns = int(self.sm.logMonoTime['carState']) if os.getenv('REPLAY') == '1' else time.monotonic_ns()
      tune = self.torque_host.sample(self.sm, now_ns=now_ns, lat_active=bool(CC.latActive))
      from openpilot.starpilot.flm.native_baseline import apply_native_refinement
      from openpilot.starpilot.lateral.torque_tuning import TorqueSource

      native_applied = bool(
        self.torque_learning_allowed
        and tune.source == TorqueSource.LEARNED
        and self.torque_host.settings.user_factor is None
        and self.torque_host.settings.user_friction is None
        and apply_native_refinement(self.LaC, self.CP, self.sm, now_ns=now_ns, host=True, apply=lambda: self.torque_host.apply(self.LaC, tune))
      )
      if not native_applied:
        self.torque_host.apply(self.LaC, tune)
      if not CC.latActive:
        self.LaC.pid.reset()

    if cycle_timing is not None:
      cycle_timing.mark()
    actuators = CC.actuators
    actuators.longControlState = self.LoC.long_control_state

    # Enable blinkers while lane changing
    if model_v2.meta.laneChangeState != LaneChangeState.off:
      CC.leftBlinker = model_v2.meta.laneChangeDirection == LaneChangeDirection.left
      CC.rightBlinker = model_v2.meta.laneChangeDirection == LaneChangeDirection.right

    if not CC.latActive:
      self.LaC.reset()
    if not CC.longActive:
      self.LoC.reset()

    # accel PID loop
    publish_takeoff_state = getattr(self.LoC, 'takeoff_starting', False)
    pid_accel_limits = self.CI.get_pid_accel_limits(self.CP, CS.vEgo, CS.vCruise * CV.KPH_TO_MS)
    from opendbc.car.tesla.preap.pedal_profile import accel_limits as preap_pedal_limits

    pid_accel_limits = preap_pedal_limits(self.CP, CS.vEgo, getattr(self.sm['selfdriveState'].personality, 'raw', 1), pid_accel_limits)
    longitudinal_output = self.LoC.update(
      CC.longActive, CS, long_plan.aTarget, long_plan.shouldStop, pid_accel_limits, context=self.longitudinal_inputs.context(CC.longActive)
    )
    actuators.accel = final_output(longitudinal_output)
    if self.longitudinal_inputs.publish_state or publish_takeoff_state:
      # Publish the selected launch state to the vehicle controller in this same frame.
      actuators.longControlState = self.LoC.long_control_state

    if cycle_timing is not None:
      cycle_timing.mark()
    # Steering PID loop and lateral MPC
    # Reset desired curvature to current to avoid violating the limits on engage
    if self.sm.valid['lateralManeuverPlan']:
      new_desired_curvature = self.sm['lateralManeuverPlan'].desiredCurvature if CC.latActive else self.curvature
    else:
      new_desired_curvature = model_v2.action.desiredCurvature if CC.latActive else self.curvature
    assist = self.turn_assist_enabled()
    rolling = math.isfinite(CS.vEgo) and CS.vEgo > max(0.1 * CV.MPH_TO_MS, self.CP.minSteerSpeed)
    if (
      assist
      and rolling
      and self.sm.all_checks(['modelV2'])
      and CC.latActive
      and not CS.standstill
      and not CS.steerFaultTemporary
      and not CS.steerFaultPermanent
      and CS.gearShifter in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low)
    ):
      self.model_turn_assist.curvature = self.curvature
      self.model_turn_assist.twitch_guard_remaining = update_twitch_guard(self.model_turn_assist.twitch_guard_remaining, CS.vEgo, CS.standstill)
      new_desired_curvature = self.model_turn_assist.update(CS, model_v2, CC, new_desired_curvature)
    elif getattr(self, 'model_turn_assist', None) is not None:
      self.model_turn_assist.reset()
      self.model_turn_assist.twitch_guard_remaining = update_twitch_guard(0.0, CS.vEgo, CS.standstill)
    lane_base_curvature = new_desired_curvature
    self.lane_centering_applied = 0.0
    self.lane_centering_inactive_reason = 'inactive'
    self.last_lane_centering_result = None
    lane_delay = None
    if lane_centering is None and self.lane_centering_host is not None:
      now_ns = int(self.sm.logMonoTime['carState']) if os.getenv('REPLAY') == '1' else time.monotonic_ns()
      lane_centering = self.lane_centering_host.sample(self.sm, now_ns=now_ns, lateral_active=CC.latActive, longitudinal_active=CC.longActive)
      if lane_centering is None:
        self.lane_centering_inactive_reason = self.lane_centering_host.last_refusal
    if lane_centering is None:
      self.lane_centering_controller.reset()
    elif not isinstance(lane_centering, LaneCenteringRequest):
      self.lane_centering_controller.reset()
      self.last_lane_centering_result = LaneCenteringResult(None, 0.0, 0, "invalid_request")
    else:
      lane_now_ns = int(self.sm.logMonoTime['carState']) if os.getenv('REPLAY') == '1' else time.monotonic_ns()
      lane_delay = current_delay(self.sm, lane_now_ns)
      result = self.lane_centering_controller.update(
        LaneCenteringInput(
          model=model_v2,
          model_sample_id=self.sm.logMonoTime['modelV2'],
          model_valid=bool(self.sm.all_checks(['modelV2'])),
          base_curvature=new_desired_curvature,
          speed_mps=CS.vEgo,
          mode=lane_centering.mode,
          lateral_active=CC.latActive,
          settings=lane_centering.settings,
          elapsed_seconds=lane_centering.elapsed_seconds,
          turn_signal_active=bool(CS.leftBlinker or CS.rightBlinker),
          driver_override=bool(CS.steeringPressed),
          time_discontinuity=lane_centering.time_discontinuity,
          measured_curvature=self.curvature,
          lateral_delay_s=lane_delay,
        )
      )
      self.last_lane_centering_result = result
      if result.candidate_curvature is not None:
        new_desired_curvature = result.candidate_curvature
    previous_curvature = self.desired_curvature
    lane_policy_now_ns = int(self.sm.logMonoTime['carState']) if os.getenv('REPLAY') == '1' else time.monotonic_ns()
    self.refresh_lane_change_policy(CS, model_v2, lane_policy_now_ns)
    factor = self.lane_change_smoother.factor(
      active=bool(CC.latActive and CS.canValid and not CS.canTimeout and self.sm.all_checks(['modelV2']) and self.lane_change_policy.enabled),
      lane_change_state=model_v2.meta.laneChangeState,
      speed=CS.vEgo,
      minimum_speed=self.lane_change_policy.minimum_speed_mps,
      duration=self.lane_change_policy.duration_s,
      previous=previous_curvature,
      desired=new_desired_curvature,
      turn_assist=assist,
    )
    new_desired_curvature = limit_rate(CS.vEgo, previous_curvature, new_desired_curvature, factor)
    self.desired_curvature, curvature_limited = clip_curvature(CS.vEgo, previous_curvature, new_desired_curvature, lp.roll)
    if self.last_lane_centering_result is not None and self.last_lane_centering_result.correction != 0.0:
      baseline_curvature, _ = clip_curvature(CS.vEgo, previous_curvature, limit_rate(CS.vEgo, previous_curvature, lane_base_curvature, factor), lp.roll)
      guarded = self.lane_centering_controller.guard_applied(model_v2, baseline_curvature, self.desired_curvature, self.curvature, CS.vEgo, lane_delay)
      if guarded != self.desired_curvature:
        self.desired_curvature = guarded
        curvature_limited = True
      self.lane_centering_applied = self.desired_curvature - baseline_curvature
    from openpilot.starpilot.car.subaru.smoothing import seconds as subaru_smooth_seconds, control_maximum

    lat_delay = self.sm["lateralDelay"].lateralDelay + LAT_SMOOTH_SECONDS + subaru_smooth_seconds(CS.vEgo, control_maximum(self))

    actuators.curvature = self.desired_curvature
    if cycle_timing is not None:
      cycle_timing.mark()
    automatic = getattr(self, 'global_learning', None)
    if automatic is not None:
      try:
        automatic.prepare(
          self.sm,
          now_ns=time.monotonic_ns(),
          active=bool(CC.latActive),
          healthy=bool(
            CS.canValid
            and not CS.canTimeout
            and not CS.steerFaultTemporary
            and not CS.steerFaultPermanent
            and not CS.accFaulted
            and not CS.steeringPressed
            and not CS.brakePressed
            and not CS.regenBraking
            and not any(str(button.type) == 'cancel' for button in CS.buttonEvents)
            and not any(event.softDisable or event.immediateDisable for event in self.sm['onroadEvents'])
            and not self.sm['driverMonitoringState'].noResponseForceDecel
          ),
          controller=self.LaC,
          native_required=bool(self.aol_replay or getattr(self, 'ordinary_axis_ack_required', False)),
          native_witness=getattr(self, 'flm_native_witness', None),
        )
        automatic.begin_frame(
          CS,
          self.VM,
          lp,
          active=bool(CC.latActive),
          safety_limited=self.steer_limited_by_safety,
          desired_curvature=self.desired_curvature,
          curvature_limited=curvature_limited,
          lat_delay=lat_delay,
          raw_model_curvature=model_v2.action.desiredCurvature,
          model_source_ns=int(self.sm.logMonoTime['modelV2']),
        )
      except Exception:
        self._discard_global_learning()
        automatic = None
    if cycle_timing is not None:
      cycle_timing.mark()
    steer, lateral_output, lac_log = self.LaC.update(
      CC.latActive, CS, self.VM, lp, self.steer_limited_by_safety, self.desired_curvature, curvature_limited, lat_delay
    )
    actuators.torque = float(steer)
    if self.CP.steerControlType == car.CarParams.SteerControlType.curvature:
      actuators.curvature = float(lateral_output)
    else:
      actuators.steeringAngleDeg = float(lateral_output)
    # Ensure no NaNs/Infs
    for p in ACTUATOR_FIELDS:
      attr = getattr(actuators, p)
      if not isinstance(attr, Number):
        continue

      if not math.isfinite(attr):
        cloudlog.error(f"actuators.{p} not finite {actuators.to_dict()}")
        setattr(actuators, p, 0.0)

    if automatic is not None:
      try:
        requested = (
          actuators.curvature
          if self.CP.steerControlType == car.CarParams.SteerControlType.curvature
          else actuators.steeringAngleDeg
          if self.CP.steerControlType == car.CarParams.SteerControlType.angle
          else actuators.torque
        )
        automatic.finish_frame(
          CS,
          self.VM,
          lp,
          lac_log,
          desired_curvature=self.desired_curvature,
          curvature_limited=curvature_limited,
          lat_delay=lat_delay,
          requested=requested,
          selected_command=(
            lateral_output if self.CP.steerControlType in (car.CarParams.SteerControlType.angle, car.CarParams.SteerControlType.curvature) else steer
          ),
        )
      except Exception:
        self._discard_global_learning()
    if diagnostic_entry is not None and diagnostic_observer is not None and not CC.latActive:
      try:
        if not diagnostic_reason:
          diagnostic_reason = (
            'refreshed_source_invalid'
            if lateral_inputs_refreshed and (not CS.canValid or CS.canTimeout)
            else 'lateral_fault_latch'
            if fault_latch is not None and latched
            else 'vehicle_onset_denied'
            if not diagnostic_onset
            else 'lateral_pause'
            if not pause_allowed
            else 'car_state_invalid'
          )
        diagnostic_observer.finish(
          diagnostic_entry,
          diagnostic_gate,
          now_ns=time.monotonic_ns(),
          refreshed=lateral_inputs_refreshed,
          native=diagnostic_native,
          native_header_ns=self.sm.logMonoTime['aolSafetyWire'],
          native_flags=(self.sm.seen['aolSafetyWire'], self.sm.valid['aolSafetyWire'], self.sm.alive['aolSafetyWire']),
          native_raw=self.sm['aolSafetyWire'] if diagnostic_native is None else b'',
          reason=diagnostic_reason,
          cs=CS,
          enabled=CC.enabled,
          long_active=CC.longActive,
          axis=axis,
          standstill=standstill,
          steer_at_standstill=self.CP.steerAtStandstill,
        )
      except Exception:
        diagnostic_observer.disabled = True
        diagnostic_observer.pending = None
    self.aol_previous_lateral_active = bool(CC.latActive)
    if cycle_timing is not None:
      cycle_timing.mark()
    return CC, lac_log

  def publish(self, CC, lac_log):
    publish_timing = getattr(self, 'publish_cycle_timing', None)
    if publish_timing is not None:
      publish_timing.start()
    CS = self.sm['carState']

    # Orientation and angle rates can be useful for carcontroller
    # Only calibrated (car) frame is relevant for the carcontroller
    CC.currentCurvature = self.curvature
    if self.calibrated_pose is not None:
      CC.orientationNED = self.calibrated_pose.orientation.xyz.tolist()
      CC.angularVelocity = self.calibrated_pose.angular_velocity.xyz.tolist()

    CC.cruiseControl.override = CC.enabled and not CC.longActive and self.CP.openpilotLongitudinalControl
    CC.cruiseControl.cancel = CS.cruiseState.enabled and (not CC.enabled or not self.CP.pcmCruise)
    CC.cruiseControl.resume = CC.enabled and CS.cruiseState.standstill and not self.sm['longitudinalPlan'].shouldStop
    if self.LoC.extension is not None:
      CC.cruiseControl.resume = self.LoC.extension.qualify_resume(
        CC.cruiseControl.resume, preferences=self.vehicle_startup_preferences, inputs=self.longitudinal_inputs
      )

    hudControl = CC.hudControl
    hudControl.setSpeed = float(CS.vCruiseCluster * CV.KPH_TO_MS)
    hudControl.speedVisible = CC.enabled
    hudControl.lanesVisible = CC.enabled
    hudControl.leadVisible = self.sm['longitudinalPlan'].hasLead
    hudControl.leadVisible = self.longitudinal_inputs.lead_visible(hudControl.leadVisible)
    hudControl.leadDistanceBars = self.sm['selfdriveState'].personality.raw + 1
    hudControl.visualAlert = self.sm['selfdriveState'].alertHudVisual

    hudControl.rightLaneVisible = True
    hudControl.leftLaneVisible = True
    if self.sm.valid['driverAssistance']:
      hudControl.leftLaneDepart = self.sm['driverAssistance'].leftLaneDeparture
      hudControl.rightLaneDepart = self.sm['driverAssistance'].rightLaneDeparture

    feedback_input_limited = self.steer_limited_by_safety
    if CC.latActive:
      CO = self.sm['carOutput']
      if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
        from openpilot.starpilot.car.tesla.coop_saturation import is_angle_steering_limited

        self.steer_limited_by_safety = is_angle_steering_limited(
          self.CP,
          CC.actuators.steeringAngleDeg,
          CO,
          self.sm['starpilotCarState'],
          bool(self.sm.valid['starpilotCarState'] and self.sm.valid['carOutput'] and self.sm.alive['carOutput']),
          int(self.sm.logMonoTime['carState']),
        )
      else:
        self.steer_limited_by_safety = abs(CC.actuators.torque - CO.actuatorsOutput.torque) > 1e-2

    else:
      self.steer_limited_by_safety = False

    # TODO: both controlsState and carControl valids should be set by
    #       sm.all_checks(), but this creates a circular dependency

    # controlsState
    dat = messaging.new_message('controlsState')
    dat.valid = CS.canValid
    cs = dat.controlsState

    cs.curvature = self.curvature
    cs.longitudinalPlanMonoTime = self.sm.logMonoTime['longitudinalPlan']
    cs.lateralPlanMonoTime = self.sm.logMonoTime['modelV2']
    cs.desiredCurvature = self.desired_curvature
    cs.longControlState = self.LoC.long_control_state
    cs.upAccelCmd = float(self.LoC.pid.p)
    cs.uiAccelCmd = float(self.LoC.pid.i)
    cs.ufAccelCmd = float(self.LoC.pid.f)
    cs.forceDecel = bool(self.sm['driverMonitoringState'].noResponseForceDecel or (self.sm['selfdriveState'].state == State.softDisabling))

    # trigger the car's stock driver monitoring escalation
    CC.driverMonitoringEscalation = cs.forceDecel

    lat_tuning = self.CP.lateralTuning.which()
    if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      cs.lateralControlState.angleState = lac_log
    elif self.CP.steerControlType == car.CarParams.SteerControlType.curvature:
      cs.lateralControlState.curvatureState = lac_log
    elif lat_tuning == 'pid':
      cs.lateralControlState.pidState = lac_log
    elif lat_tuning == 'torque':
      cs.lateralControlState.torqueState = lac_log

    if publish_timing is not None:
      publish_timing.mark()
    self.pm.send('controlsState', dat)
    if publish_timing is not None:
      publish_timing.mark()

    # carControl
    cc_send = messaging.new_message('carControl')
    cc_send.valid = CS.canValid
    cc_send.carControl = CC
    self.pm.send('carControl', cc_send)
    if publish_timing is not None:
      publish_timing.mark()
    feedback = feedback_message(
      self.last_lane_centering_result,
      self.lane_centering_applied,
      CC.latActive,
      model_mono_time=self.sm.logMonoTime['modelV2'],
      car_control_mono_time=cc_send.logMonoTime,
      valid=bool(CS.canValid and self.sm.all_checks(['modelV2'])),
      inactive_reason=getattr(self, 'lane_centering_inactive_reason', 'inactive'),
    )
    automatic = getattr(self, 'global_learning', None)
    if automatic is not None:
      try:
        learning_feedback = feedback.as_reader().as_builder()
        written = control_message(
          learning_feedback, self.LaC, CC, control_ns=cc_send.logMonoTime, state_ns=self.sm.logMonoTime['carState'], limited=self.steer_limited_by_safety
        )
        if written is False:
          raise ValueError('optional control evidence write failed')
        if automatic.outcome is not None:
          write_application(learning_feedback.starpilotLateralState, automatic.outcome)
        feedback = learning_feedback
      except Exception:
        self._discard_global_learning()
    if (
      automatic is not None
      and automatic.eligible
      and automatic.source is not None
      and automatic.source.enabled
      and not automatic.source.manual
      and self.CP.steerControlType == car.CarParams.SteerControlType.torque
    ):
      try:
        from openpilot.starpilot.flm.feedback_observation import capture_controls
        from openpilot.starpilot.flm.feedback_wire import write_consumption

        observation = capture_controls(self, CC, control_ns=int(cc_send.logMonoTime), input_limited=feedback_input_limited)
        if observation is not None:
          feedback_copy = feedback.as_reader().as_builder()
          write_consumption(feedback_copy.starpilotLateralState, observation)
          feedback = feedback_copy
      except Exception:
        self.learning_feedback_prior_publish_ns = 0
    else:
      self.learning_feedback_prior_publish_ns = 0
    if publish_timing is not None:
      publish_timing.mark()
    self.pm.send('starpilotLateralState', feedback)
    diagnostic_observer = getattr(self, 'aol_transition_diagnostic', None)
    if diagnostic_observer is not None:
      diagnostic_observer.emit(cloudlog.event, cc_send)
    if publish_timing is not None:
      publish_timing.mark()

  def run(self):
    rk = Ratekeeper(100, print_delay_threshold=None)
    next_overrun_log_ns = 0
    self.control_cycle_timing = ControlCycleTiming()
    self.publish_cycle_timing = ControlCycleTiming(phases=PUBLISH_PHASES)
    while True:
      started, cpu_started = time.monotonic_ns(), time.thread_time_ns()
      self.update()
      updated = time.monotonic_ns()
      CC, lac_log = self.state_control()
      controlled = time.monotonic_ns()
      self.publish(CC, lac_log)
      published = time.monotonic_ns()
      # Report real blocking/scheduling costs after output; never alter authority.
      if published - started > 50_000_000 and published >= next_overrun_log_ns:
        next_overrun_log_ns = published + 5_000_000_000
        cloudlog.event(
          'controls_cycle_overrun',
          update_ms=(updated - started) / 1e6,
          control_ms=(controlled - updated) / 1e6,
          publish_ms=(published - controlled) / 1e6,
          wall_ms=(published - started) / 1e6,
          cpu_ms=(time.thread_time_ns() - cpu_started) / 1e6,
          control_phases=self.control_cycle_timing.snapshot(),
          control_phases_complete=self.control_cycle_timing.complete,
          publish_phases=self.publish_cycle_timing.snapshot(),
          publish_phases_complete=self.publish_cycle_timing.complete,
          error=True,
        )
      rk.monitor_time()


def main():
  config_realtime_process(4, Priority.CTRL_HIGH)
  controls = Controls()
  if HARDWARE.get_device_type() in ("tici", "tizi", "mici"):
    set_core_affinity([6])
    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
    try:
      os.setpriority(os.PRIO_PROCESS, 0, -5)
    except OSError:
      cloudlog.warning("Controls normal scheduling preference unavailable")
  else:
    set_core_affinity([4, 5])
  controls.run()


if __name__ == "__main__":
  main()
