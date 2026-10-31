#!/usr/bin/env python3
import os
import math
import time
import threading
import uuid
from copy import copy
from dataclasses import replace

import openpilot.cereal.messaging as messaging

from openpilot.cereal import log
from opendbc.car.structs import car
from opendbc.car.hyundai.ev6_aol import qualified as ev6_aol_qualified
from opendbc.car.honda.stock_aol import qualified as honda_aol_qualified, temporary_restriction as honda_temporary_restriction
from openpilot.cereal.visionipc import VisionStreamType
from msgq.visionipc import VisionIpcClient


from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper, DT_CTRL
from openpilot.common.swaglog import cloudlog
from openpilot.common.gps import get_gps_location_service
from openpilot.starpilot.gps.source import observation as gps_observation

from openpilot.selfdrive.car.car_events import CarEvents
from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose
from openpilot.selfdrive.selfdrived.events import Events, ET, Alert, Priority as AlertPriority
from openpilot.starpilot.longitudinal.force_stop_alert import HoldAlertState, EVENT_TYPE as FORCE_STOP_HOLD
from openpilot.selfdrive.selfdrived.helpers import (ExcessiveActuationCheck, jetlink_in_control, jetlink_lost_alert, throttled_param,
                                                    update_big_model_status, update_jetlink_swaps)
from openpilot.starpilot.models import jetlink_adapter
from openpilot.system.manager.process_health import driving_process_failures
from openpilot.selfdrive.selfdrived.state import StateMachine
from opendbc.car.gm.aol import qualified_gm
from openpilot.starpilot.aol.panda_faults import blocking_fault_list
from openpilot.starpilot.aol.vehicle import native_bootstrap_supported
from opendbc.car.gm.steering_authority import monitored_profile
from openpilot.starpilot.car.gm.steering_companion import SteeringCompanion
from openpilot.starpilot.car.hyundai.aol import qualified_ioniq6
from opendbc.car.gm.values import GMFlags, is_volt_ascm_longitudinal
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.intent_companion import IntentCompanion
from openpilot.starpilot.aol.transport_pause import AvailabilityPause, TransportPause, availability_only, cp_identity, healthy_transport
from openpilot.starpilot.aol.source_availability import (
  ordinary_source_availability, fresh_ordinary_lag_continuation, stock_lateral_role, stock_lateral_fault_proof,
)
from openpilot.starpilot.aol.runtime import (INTENT_MAX_AGE_NS, SAFETY_MAX_AGE_NS, AxisDecision, current_intent, current_native, decide_axes,
                                            ordinary_lateral_requested, decide_ordinary_axis, known_reverse_pause)
from openpilot.starpilot.aol.vehicle import policy_for as axis_policy_for, ordinary_axis_request_allowed
from openpilot.starpilot.conditional_mode.consumer import ConsumerResult, ModeConsumer
from openpilot.starpilot.conditional_mode.effective_status import publish_ack
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.feature_capabilities import lateral_supported
from openpilot.starpilot.controllers.mode_actions import SwitchbackStatusOwner, SwitchbackCooldown, ModeActionOwner
from openpilot.starpilot.conditional_mode.projection import paired_clocks_ns
from openpilot.starpilot.conditional_mode.runtime_settings import (ConditionalSettingsOwner, experimental_requested, read_mode_sources)
from openpilot.starpilot.conditional_mode.status import settings_fingerprint
from openpilot.starpilot.feature_runtime import enabled as feature_enabled
from openpilot.starpilot.nostalgia import aol_no_entry, paddle_cancel, physical_cancel, saved_enabled as nostalgia_saved_enabled
from openpilot.starpilot.lateral.low_speed_advisory import LowSpeedAdvisory, advisory_eligible
from openpilot.starpilot.lateral.lane_change_status_wire import alert_wording, decode as decode_lane_status, fresh_for_model
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager, set_offroad_alert

from openpilot.common.version import get_build_metadata
from openpilot.common.hardware import HARDWARE

REPLAY = "REPLAY" in os.environ
SIMULATION = "SIMULATION" in os.environ
TESTING_CLOSET = "TESTING_CLOSET" in os.environ

LONGITUDINAL_PERSONALITY_MAP = {v: k for k, v in log.LongitudinalPersonality.schema.enumerants.items()}

ThermalStatus = log.DeviceState.ThermalStatus
State = log.SelfdriveState.OpenpilotState
PandaType = log.PandaState.PandaType
LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection
EventName = log.OnroadEvent.EventName
ButtonType = car.CarState.ButtonEvent.Type
SafetyModel = car.CarParams.SafetyModel
AlertLevel = log.DriverMonitoringState.AlertLevel
MonitoringPolicy = log.DriverMonitoringState.MonitoringPolicy

IGNORED_SAFETY_MODES = (SafetyModel.silent, SafetyModel.noOutput)


class SelfdriveD:
  def __init__(self, CP=None):
    self.params = Params()

    # Ensure the current branch is cached, otherwise the first cycle lags
    build_metadata = get_build_metadata()

    if CP is None:
      cloudlog.info("selfdrived is waiting for CarParams")
      self.CP = messaging.log_from_bytes(self.params.get("CarParams", block=True), car.CarParams)
      cloudlog.info("selfdrived got CarParams")
    else:
      self.CP = CP

    self.aol_replay = feature_enabled(self.params, self.CP, 'aol', os.environ)
    self.ordinary_axis_ack_required = axis_policy_for(self.CP).ordinary_axis_ack_required
    self.aol_cruise_main_required = axis_policy_for(self.CP).cruise_main_required
    self.axis_transport_required = self.aol_replay or self.ordinary_axis_ack_required
    self.nostalgia_enabled = nostalgia_saved_enabled(self.params)
    self.nostalgia_paddle_cancel = False
    self.aol_session_id = uuid.uuid4().hex
    self.aol_sequence = 0
    self.aol_axis_decision = AxisDecision()
    self.aol_dm_lateral_inhibit = False
    self.aol_authority_lost = False
    self.aol_transport_pause = TransportPause(self.CP, self.aol_session_id)
    self.aol_fault_reason = 'none'
    self.steering_authority_unavailable = False
    self.steering_companion = (SteeringCompanion(messaging.sub_sock('starpilotCarState', conflate=False))
                               if monitored_profile(self.CP) else None)
    self.force_stop_hold_alert = HoldAlertState()
    self.aol_car_state_log_ns = 0
    self.aol_last_intent = None
    self.aol_intent_companion = (IntentCompanion(messaging.sub_sock('aolIntentWire', conflate=False))
                                 if self.aol_replay else None)
    self.lane_status_session = ""
    self.lane_status_sequence = -1
    self.conditional_replay = feature_enabled(self.params, self.CP, 'conditional', os.environ)
    self.conditional_settings = ConditionalSettingsOwner(self.params) if self.conditional_replay else None
    self.conditional_consumer = ModeConsumer() if self.conditional_replay else None
    self.conditional_status = 'disabled'
    self.controller_action_sock = messaging.sub_sock('slcAction', conflate=False)
    self.controller_action_owner = ModeActionOwner()
    self.switchback_capable = lateral_supported(self.CP)
    self.switchback_status = SwitchbackStatusOwner()
    self.switchback_cooldown = SwitchbackCooldown()
    self.low_speed_advisory = LowSpeedAdvisory()
    self.switchback_setting_ns = 0
    self.switchback_cooldown_ns = 300_000_000_000
    self.conditional_car_state_valid = False
    self.conditional_ack_session = uuid.uuid4().hex
    self.conditional_ack_sequence = 0
    self.conditional_result = ConsumerResult(False, False, 'disabled')

    self.car_events = CarEvents(self.CP)
    from openpilot.starpilot.car.tesla.stock_events import StockCruiseConsumer
    self.tesla_stock_consumer = StockCruiseConsumer(self.CP)
    self.tesla_stock_alerts = []

    self.pose_calibrator = PoseCalibrator()
    self.calibrated_pose: Pose | None = None
    self.excessive_actuation_check = ExcessiveActuationCheck()
    self.excessive_actuation = self.params.get("Offroad_ExcessiveActuation") is not None
    self.big_model_loading = False
    self.big_model_active = False
    self.big_model_chestnut = False
    self.big_model_failed = False
    self.big_model_ready_t = 0.
    # Jetlink's swap events: bookkeeping alone, so built link on or off; None without a usable jetlink
    self.jetlink_hooks = jetlink_adapter.hooks()
    self.jetlink_swaps = self.jetlink_hooks.swap_events(DT_CTRL)
    self.jetlink_settling = self.jetlink_link_lost = False

    # Setup sockets
    self.pm = messaging.PubMaster(['selfdriveState', 'onroadEvents'] +
                                  (['aolAxisState'] if self.axis_transport_required else []) +
                                  (['starpilotSelfdriveState'] if self.conditional_replay else []))

    self.gps_location_service = get_gps_location_service(self.params)
    self.gps_packets = [self.gps_location_service, 'starpilotCarState']
    self.sensor_packets = ["accelerometer", "gyroscope"]
    self.camera_packets = ["narrowRoadCameraState", "cabinCameraState", "wideRoadCameraState"]

    # TODO: de-couple selfdrived with card/conflate on carState without introducing controls mismatches
    self.car_state_poller = messaging.Poller() if self.aol_replay and self.axis_transport_required else None
    self.car_state_sock = messaging.sub_sock('carState', poller=self.car_state_poller, timeout=20)

    ignore = self.sensor_packets + self.gps_packets + ['alertDebug', 'lateralManeuverPlan', 'laneChangeAssistWire', 'starpilotLongitudinalPlan']
    if self.aol_replay:
      ignore += ['aolIntentWire']
    if self.axis_transport_required:
      ignore += ['aolSafetyWire']
    if self.conditional_replay or self.switchback_capable:
      # This optional proposal never controls the native process-health gate.
      ignore += ['slcState']
    if SIMULATION:
      ignore += ['cabinCameraState', 'managerState']
    if REPLAY:
      # no vipc in replay will make them ignored anyways
      ignore += ['narrowRoadCameraState', 'wideRoadCameraState']
    from openpilot.starpilot.car.gm.radar_recovery import capability as radar_recovery_capability
    radar_notifications = ['radarTracks'] if radar_recovery_capability(self.CP) is not None else []
    ignore += radar_notifications
    ignore = set(ignore)
    self.sm = messaging.SubMaster(['deviceState', 'pandaStates', 'peripheralState', 'modelV2', 'extrinsicsCalibration',
                                   'carOutput', 'driverMonitoringState', 'longitudinalPlan', 'deviceMotion', 'lateralDelay',
                                   'managerState', 'vehicleParameters', 'radarState', 'lateralTorqueParameters',
                                   'controlsState', 'carControl', 'driverAssistance', 'alertDebug', 'userBookmark',
                                   'lateralManeuverPlan', 'laneChangeAssistWire', 'starpilotLongitudinalPlan'] +
                                  (['aolIntentWire'] if self.aol_replay else []) +
                                   (['aolSafetyWire'] if self.axis_transport_required else []) +
                                   (['slcState'] if self.conditional_replay or self.switchback_capable else []) + \
                                   self.camera_packets + self.sensor_packets + self.gps_packets + radar_notifications,
                                  ignore_alive=ignore, ignore_avg_freq=ignore,
                                  ignore_valid=ignore, frequency=int(1/DT_CTRL))

    # read params
    self.is_metric = self.params.get_bool("IsMetric")
    self.aol_settings = read_settings(self.params) if self.aol_replay else None
    self.is_ldw_enabled = self.params.get_bool("IsLdwEnabled")
    self.disengage_on_accelerator = self.params.get_bool("DisengageOnAccelerator")
    self.radar_recovery_enabled = self.params.get_bool("RadarRecoveryAlert")
    self.blindspot_alert_settings = (self.params.get_bool("LoudBlindspotAlert"),
                                    self.params.get_bool("LoudBlindspotAlertWhenDisengaged"))

    car_recognized = self.CP.brand != 'mock'

    # Capability changes affect runtime authority, never the saved Alpha Long choice.
    if not self.CP.openpilotLongitudinalControl:
      self.params.remove("ExperimentalMode")

    self.CS_prev = car.CarState.new_message()
    self.AM = AlertManager()
    from openpilot.starpilot.car.events import event_rules
    self.events = Events(event_rules(self.CP))

    self.initialized = False
    self.enabled = False
    self.active = False
    self.mismatch_counter = 0
    self.cruise_mismatch_counter = 0
    self.last_steering_pressed_frame = 0
    self.distance_traveled = 0
    self.last_functional_fan_frame = 0
    self.events_prev = []
    self.logged_comm_issue = None
    self.not_running_prev = None
    self.experimental_mode = False
    self.requested_experimental_mode = False
    self.personality = self.params.get("LongitudinalPersonality", return_default=True)
    self.recalibrating_seen = False
    self.dm_lockout_set = False
    self.dm_uncertain_alerted = False
    self.state_machine = StateMachine()
    self.rk = Ratekeeper(100, print_delay_threshold=None)
    # Observational only. Do not cache this qualification for authority decisions.

    # Determine startup event
    self.startup_event = EventName.startup if build_metadata.openpilot.comma_remote and build_metadata.tested_channel else EventName.startupMaster
    if HARDWARE.get_device_type() == 'mici':
      self.startup_event = None
    if not car_recognized:
      self.startup_event = EventName.startupNoCar
    elif car_recognized and self.CP.passive:
      self.startup_event = EventName.startupNoControl
    elif self.CP.secOcRequired and not self.CP.secOcKeyAvailable:
      self.startup_event = EventName.startupNoSecOcKey

    if not car_recognized:
      self.events.add(EventName.carUnrecognized, static=True)
      set_offroad_alert("Offroad_CarUnrecognized", True)
    elif self.CP.passive:
      self.events.add(EventName.dashcamMode, static=True)

  def update_process_health(self) -> bool:
    not_running = driving_process_failures(self.sm['managerState'].processes)
    if self.sm.recv_frame['managerState'] and not_running:
      if not_running != self.not_running_prev:
        cloudlog.event("process_not_running", not_running=not_running, error=True)
      self.not_running_prev = not_running
      self.events.add(EventName.processNotRunning)
      return True
    return False

  def update_big_model_status(self) -> bool:
    return update_big_model_status(self)

  def _update_steering_authority(self, CS):
    if self.CP.brand != 'gm' or not monitored_profile(self.CP):
      self.steering_authority_unavailable = False
      return
    companion = getattr(self, 'steering_companion', None)
    if companion is None:
      companion = self.steering_companion = SteeringCompanion()
    # Receiving may cross a publication boundary. Validate every original source
    # against the clock after the bounded drain, without renewing any receipt.
    drained = companion.drain()
    now_ns = self.aol_car_state_log_ns if REPLAY and self.aol_car_state_log_ns else time.monotonic_ns()
    source_ns = self.aol_car_state_log_ns
    source_current = bool(getattr(self, 'conditional_car_state_valid', False) and CS.canValid and not CS.canTimeout and
                          0 < source_ns <= now_ns and now_ns - source_ns <= INTENT_MAX_AGE_NS)
    from openpilot.starpilot.car.gm.aol import steering_raw_projection_owner, steering_source_gap_intent_only
    pair = companion.current(self.sm, source_ns=source_ns, now_ns=now_ns, drain=False,
                             allow_raw_projection=steering_raw_projection_owner(self.CP)) if drained else None
    if not source_current:
      pair = None
    previous_axes = self.aol_axis_decision
    expected_active = self.active or previous_axes.lateral_active or previous_axes.longitudinal_active
    if pair is not None:
      self.steering_authority_unavailable = pair
    elif expected_active and not (drained and steering_source_gap_intent_only(self, CS, companion, now_ns)):
      self.steering_authority_unavailable = True
    if getattr(self, 'steering_authority_unavailable', False):
      self.events.add(EventName.controlsMismatch)

  def _update_car_event_sample(self, CS, previous):
    # Block resume if cruise never previously enabled
    resume_pressed = any(be.type in (ButtonType.accelCruise, ButtonType.resumeCruise) for be in CS.buttonEvents)
    if not self.CP.pcmCruise and CS.vCruise > 250 and resume_pressed:
      self.events.add(EventName.resumeBlocked)

    # Add car events, ignore if CAN isn't valid
    if CS.canValid:
      car_events = self.car_events.update(CS, previous, self.sm['carControl']).to_msg()
      device = self.sm['deviceState']
      drive_id = (int(device.startedMonoTime) if self.sm.seen['deviceState'] and self.sm.alive['deviceState'] and
                  self.sm.valid['deviceState'] and device.started else 0)
      eligible = advisory_eligible(self.CP, CS, active=self.active,
        intent=self.aol_last_intent if self.aol_replay else None)
      show_low_speed = self.low_speed_advisory.update(float(CS.vEgo), float(self.CP.minSteerSpeed),
        drive_id=drive_id, eligible=eligible)
      self.events.add_from_msg([event for event in car_events if event.name != EventName.belowSteerSpeed])
      if show_low_speed:
        self.events.add(EventName.belowSteerSpeed)
      paddle_pressed = paddle_cancel(self.CP, CS, enabled=self.enabled, saved=self.nostalgia_enabled)
      self.nostalgia_paddle_cancel |= bool(paddle_pressed and EventName.buttonCancel not in self.events.names and
                                         not physical_cancel(CS, self.CP))
      if paddle_pressed:
        self.events.add(EventName.buttonCancel)

      if self.CP.notCar:
        # wait for everything to init first
        if self.sm.frame > int(2. / DT_CTRL) and self.initialized:
          # body always wants to enable
          self.events.add(EventName.pcmEnable)

      # Disable on rising edge of accelerator or brake. Also disable on brake when speed > 0
      if (CS.gasPressed and not previous.gasPressed and self.disengage_on_accelerator) or \
        (CS.brakePressed and (not previous.brakePressed or not CS.standstill)) or \
        (CS.regenBraking and (not previous.regenBraking or not CS.standstill)):
        self.events.add(EventName.pedalPressed)


  def update_events(self, CS):
    """Compute onroadEvents from carState"""

    self.events.clear()
    self.car_event_sample_count = 0
    self.aol_reconciled_sources = ()
    self.tesla_stock_alerts = []
    self.nostalgia_paddle_cancel = False
    action_owner = getattr(self, 'controller_action_owner', None)
    action_sock = getattr(self, 'controller_action_sock', None)
    if action_owner is not None and action_sock is not None:
      for _ in range(8):
        request = messaging.recv_one_or_none(action_sock)
        if request is None:
          break
        if (str(request.slcAction.kind) == 'disengageRequest' and self.enabled and
            action_owner.update(request, self.sm, self.CP, now_ns=time.monotonic_ns())):
          self.events.add(EventName.buttonCancel)

    if self.sm['controlsState'].lateralControlState.which() == 'debugState':
      self.events.add(EventName.joystickDebug)
      self.startup_event = None

    loading = throttled_param(self, "ChestnutLoading", self.params.get_bool)
    if self.big_model_loading and not loading:
      self.big_model_ready_t = time.monotonic()
    self.big_model_loading = loading
    if self.big_model_loading:
      self.events.add(EventName.bigModelLoading)

    if self.jetlink_swaps is not None:
      update_jetlink_swaps(self, jetlink_in_control(self))
    big_failed = self.update_big_model_status()

    if self.sm.recv_frame['lateralManeuverPlan'] > 0:
      self.events.add(EventName.lateralManeuver)
      self.startup_event = None
    elif self.sm.recv_frame['alertDebug'] > 0:
      self.events.add(EventName.longitudinalManeuver)
      self.startup_event = None

    # Add startup event
    if self.startup_event is not None:
      self.events.add(self.startup_event)
      self.startup_event = None

    # Don't add any more events if not initialized
    if not self.initialized:
      self._update_steering_authority(CS)
      self.events.add(EventName.selfdriveInitializing)
      return

    # Check for user bookmark press
    if self.sm.updated['userBookmark']:
      prime_type = self.params.get("PrimeType")
      paired = prime_type is not None and int(prime_type) >= 0
      self.events.add(EventName.userBookmark if paired else EventName.userBookmarkNotPaired)

    # Don't add any more events while in dashcam mode
    if self.CP.passive:
      self._update_steering_authority(CS)
      return

    # Handle DM
    if not self.CP.notCar:
      # Block engaging until lockout times out or ignition reset
      if self.sm['driverMonitoringState'].lockout and not self.dm_lockout_set:
        self.params.put_bool("DriverTooDistracted", True)
        self.dm_lockout_set = True
      elif not self.sm['driverMonitoringState'].lockout and self.dm_lockout_set:
        self.params.remove("DriverTooDistracted")
        self.dm_lockout_set = False
      # No entry conditions
      if self.sm['driverMonitoringState'].lockout or self.sm['driverMonitoringState'].alwaysOnLockout:
        self.events.add(EventName.tooDistracted)
      # Alerts
      vision_dm = self.sm['driverMonitoringState'].activePolicy == MonitoringPolicy.vision
      if self.sm['driverMonitoringState'].alertLevel == AlertLevel.one:
        self.events.add(EventName.driverDistracted1 if vision_dm else EventName.driverUnresponsive1)
      elif self.sm['driverMonitoringState'].alertLevel == AlertLevel.two:
        self.events.add(EventName.driverDistracted2 if vision_dm else EventName.driverUnresponsive2)
      elif self.sm['driverMonitoringState'].alertLevel == AlertLevel.three:
        self.events.add(EventName.driverDistracted3 if vision_dm else EventName.driverUnresponsive3)
      # Warn consistent DM uncertainty
      if self.sm['driverMonitoringState'].visionPolicyState.uncertainOffroadAlertPercent >= 100 and not self.dm_uncertain_alerted:
        set_offroad_alert("Offroad_DriverMonitoringUncertain", True)
        self.dm_uncertain_alerted = True

    # Create events for temperature, disk space, and memory
    if self.sm['deviceState'].thermalStatus >= ThermalStatus.overheated:
      self.events.add(EventName.overheat)
    if self.sm['deviceState'].freeSpacePercent < 7 and not SIMULATION:
      self.events.add(EventName.outOfSpace)
    if self.sm['deviceState'].memoryUsagePercent > 90 and not SIMULATION:
      self.events.add(EventName.lowMemory)

    # Alert if fan isn't spinning for 5 seconds
    if self.sm['peripheralState'].pandaType != log.PandaState.PandaType.unknown:
      if self.sm['peripheralState'].fanSpeedRpm < 500 and self.sm['deviceState'].fanSpeedPercentDesired > 50:
        # allow enough time for the fan controller in the panda to recover from stalls
        if (self.sm.frame - self.last_functional_fan_frame) * DT_CTRL > 15.0:
          self.events.add(EventName.fanMalfunction)
      else:
        self.last_functional_fan_frame = self.sm.frame

    # Handle calibration status
    cal_status = self.sm['extrinsicsCalibration'].calStatus
    if cal_status != log.ExtrinsicsCalibration.Status.calibrated:
      if cal_status == log.ExtrinsicsCalibration.Status.uncalibrated:
        self.events.add(EventName.calibrationIncomplete)
      elif cal_status == log.ExtrinsicsCalibration.Status.recalibrating:
        if not self.recalibrating_seen:
          set_offroad_alert("Offroad_Recalibration", True)
        self.recalibrating_seen = True
        self.events.add(EventName.calibrationRecalibrating)
      else:
        self.events.add(EventName.calibrationInvalid)

    # Lane departure warning
    if self.is_ldw_enabled and self.sm.valid['driverAssistance']:
      if self.sm['driverAssistance'].leftLaneDeparture or self.sm['driverAssistance'].rightLaneDeparture:
        self.events.add(EventName.ldw)

    # ******************************************************************************************
    #  NOTE: To fork maintainers.
    #  Disabling or nerfing safety features will get you and your users banned from our servers.
    #  We recommend that you do not change these numbers from the defaults.
    if self.sm.updated['extrinsicsCalibration']:
      self.pose_calibrator.feed_extrinsics_calibration(self.sm['extrinsicsCalibration'])
    if self.sm.updated['deviceMotion']:
      device_motion = Pose.from_device_motion(self.sm['deviceMotion'])
      self.calibrated_pose = self.pose_calibrator.build_calibrated_pose(device_motion)

    for i, pandaState in enumerate(self.sm['pandaStates']):
      # All pandas must match the list of safetyConfigs, and if outside this list, must be silent or noOutput
      if i < len(self.CP.safetyConfigs):
        safety_mismatch = pandaState.safetyModel != self.CP.safetyConfigs[i].safetyModel or \
                          pandaState.safetyParam != self.CP.safetyConfigs[i].safetyParam or \
                          pandaState.alternativeExperience != self.CP.alternativeExperience
      else:
        safety_mismatch = pandaState.safetyModel not in IGNORED_SAFETY_MODES

      # safety mismatch allows some time for pandad to set the safety mode and publish it back from panda
      if ((safety_mismatch and self.sm.frame*DT_CTRL > 10.) or pandaState.safetyRxChecksInvalid or self.mismatch_counter >= 200 or
          (pandaState.faults and blocking_fault_list(pandaState.faults, self.CP))):
        self.events.add(EventName.controlsMismatch)

      if log.PandaState.FaultType.relayMalfunction in pandaState.faults:
        self.events.add(EventName.relayMalfunction)

    # Handle HW and system malfunctions
    # Order is very intentional here. Be careful when modifying this.
    # All events here should at least have NO_ENTRY and SOFT_DISABLE.
    num_events = len(self.events)

    if self.big_model_active and big_failed:
      self.events.add(EventName.bigModelFailed)

    if not self.update_process_health():
      if not SIMULATION and not self.rk.lagging:
        if not self.sm.all_alive(self.camera_packets):
          self.events.add(EventName.cameraMalfunction)
        elif not self.sm.all_freq_ok(self.camera_packets):
          self.events.add(EventName.cameraFrameRate)
    if not REPLAY and self.rk.lagging:
      self.events.add(EventName.selfdrivedLagging)
    if self.CP.openpilotLongitudinalControl:
      if self.sm['radarState'].radarErrors.canError:
        self.events.add(EventName.canError)
      elif self.sm['radarState'].radarErrors.radarUnavailableTemporary:
        self.events.add(EventName.radarTempUnavailable)
      elif any(self.sm['radarState'].radarErrors.to_dict().values()):
        self.events.add(EventName.radarFault)
    hardware_events_added = len(self.events) - num_events

    from openpilot.starpilot.car.hyundai.aol import source_deadline_supported
    input_proof = getattr(self, 'aol_step_input_proof', None)
    reconcile_input = bool(input_proof is not None and not REPLAY and self.aol_replay and self.axis_transport_required and
                           source_deadline_supported(self.CP) and
                           input_proof[0] == self.aol_session_id and
                           (input_proof[8] or input_proof[9]))
    self.aol_event_input_reconcile = reconcile_input
    CS = self.refresh_event_car_state(CS)
    if not reconcile_input:
      # Other vehicle owners retain their original ordering. In particular,
      # GM's exact steering companion must not lose an earlier source's fault
      # when a newer carState arrives during car-event processing.
      self._update_steering_authority(CS)
    previous = self.CS_prev
    retained_source = (getattr(self, 'car_state_retained_source_ns', 0)
                       if not getattr(self, 'car_state_samples', ()) else 0)
    consumed = 0
    envelope_errors = []
    while True:
      received = getattr(self, 'car_state_samples', ())
      samples = received[consumed:] if received else ((CS, getattr(self, 'conditional_car_state_valid', False)),)
      for sample, envelope_valid in samples:
        self._update_car_event_sample(sample, previous)
        self.car_event_sample_count += 1
        if sample.canTimeout:
          envelope_errors.append(EventName.canBusMissing)
        elif (received and not envelope_valid) or not sample.canValid:
          envelope_errors.append(EventName.canError)
        previous = sample
      consumed = len(received)
      if not reconcile_input:
        break
      # Input can arrive while car-event owners run. Reconcile it before the
      # selected-source owners and authority/state-machine decisions. The drain
      # retains one total 32-envelope bound; only a blocking wait depends on
      # the original output deadline. A fresh pair may renew accepted engagement
      # after that deadline. The cursor processes each physical sample once.
      # Interleave the two raw inputs before selected-source owners. Intent
      # can arrive just before its carState; never select that state without
      # processing every newly consumed physical sample through this cursor.
      # The cursor only repeats for new carStates (32 total); all of these
      # intent drains share one 32-message step budget, including earlier waits.
      companion = getattr(self, 'aol_intent_companion', None)
      remaining = max(0, 32 - self.aol_step_intent_drain_count)
      drained = companion is not None and self.drain_axis_intent(companion, max_messages=remaining)
      self.aol_event_intent_drained = bool(drained and self.aol_step_intent_drain_ok and self.aol_event_intent_drained is not False)
      CS = self.refresh_event_car_state(CS, allow_wait=not self.aol_event_input_wait_attempted)
      if len(getattr(self, 'car_state_samples', ())) <= consumed:
        break
      self.aol_reconciled_sources = ((retained_source,) if retained_source else ()) + tuple(
        int(sample.logMonoTime) for sample in self.car_state_envelopes)
    samples = getattr(self, 'car_state_samples', ()) or ((CS, getattr(self, 'conditional_car_state_valid', False)),)
    if self.car_event_sample_count > 1:
      for event in envelope_errors:
        self.events.add(event)
    self.event_car_state = CS
    if reconcile_input:
      self._update_steering_authority(CS)
    if any(physical_cancel(sample, self.CP) for sample, _ in samples):
      self.nostalgia_paddle_cancel = False
    if CS.canValid:
      if self.tesla_stock_consumer.binding is not None:
        intent_companion = getattr(self, 'aol_intent_companion', None)
        drained = intent_companion is None or self.drain_axis_intent(intent_companion)
        now_ns = self.aol_car_state_log_ns if REPLAY and self.aol_car_state_log_ns else time.monotonic_ns()
        companion = current_intent(self.sm, car_state_ns=self.aol_car_state_log_ns, now_ns=now_ns,
                                   previous=self.aol_last_intent, companion=intent_companion, drain=False) if drained else None
        session = companion.producerSessionId if companion is not None else None
        self.tesla_stock_alerts = self.tesla_stock_consumer.poll(
          self.sm, now_ns=now_ns, session=session, car_state=CS,
          source_ns=self.aol_car_state_log_ns, source_valid=self.conditional_car_state_valid)

    if self.calibrated_pose is not None and not self.CP.notCar:
      excessive_actuation = self.excessive_actuation_check.update(self.sm, CS, self.calibrated_pose)
      if not self.excessive_actuation and excessive_actuation is not None:
        set_offroad_alert("Offroad_ExcessiveActuation", True, extra_text=str(excessive_actuation))
        self.excessive_actuation = True

    if self.excessive_actuation:
      self.events.add(EventName.excessiveActuation)
    # ******************************************************************************************

    # Handle lane change
    if self.sm['modelV2'].meta.laneChangeState == LaneChangeState.preLaneChange:
      direction = self.sm['modelV2'].meta.laneChangeDirection
      if (CS.leftBlindspot and direction == LaneChangeDirection.left) or \
         (CS.rightBlindspot and direction == LaneChangeDirection.right):
        self.events.add(EventName.laneChangeBlocked)
      else:
        if direction == LaneChangeDirection.left:
          self.events.add(EventName.preLaneChangeLeft)
        else:
          self.events.add(EventName.preLaneChangeRight)
    elif self.sm['modelV2'].meta.laneChangeState in (LaneChangeState.laneChangeStarting,
                                                    LaneChangeState.laneChangeFinishing):
      self.events.add(EventName.laneChange)

    num_can_events = len(self.events)
    if CS.canTimeout:
      self.events.add(EventName.canBusMissing)
    elif not CS.canValid:
      self.events.add(EventName.canError)

    # generic catch-all. ideally, a more specific event should be added above instead
    has_disable_events = self.events.contains(ET.NO_ENTRY) and (self.events.contains(ET.SOFT_DISABLE) or self.events.contains(ET.IMMEDIATE_DISABLE))
    no_system_errors = (not has_disable_events) or (hardware_events_added == 0 and len(self.events) == num_can_events)
    warmup_sec = 5.
    big_model_settling = self.big_model_loading or time.monotonic() < self.big_model_ready_t + warmup_sec
    if not self.sm.all_checks() and no_system_errors and not big_model_settling:  # the load holds modelV2 and friends back on purpose
      if not self.sm.all_alive():
        self.events.add(EventName.commIssue)
      elif not self.sm.all_freq_ok():
        self.events.add(EventName.commIssueAvgFreq)
      else:
        self.events.add(EventName.commIssue)

      logs = {
        'invalid': [s for s, valid in self.sm.valid.items() if not valid],
        'not_alive': [s for s, alive in self.sm.alive.items() if not alive],
        'not_freq_ok': [s for s, freq_ok in self.sm.freq_ok.items() if not freq_ok],
      }
      if logs != self.logged_comm_issue:
        cloudlog.event("commIssue", error=True, **logs)
        self.logged_comm_issue = logs
    else:
      self.logged_comm_issue = None

    if not self.CP.notCar and not big_model_settling:  # localization has nothing to work with during the load
      # the defaults of a message that was never received are not a localizer failure
      if self.sm.seen['deviceMotion'] and not self.sm['deviceMotion'].posenetOK:
        self.events.add(EventName.posenetInvalid)
      if self.sm.seen['deviceMotion'] and not self.sm['deviceMotion'].inputsOK:
        self.events.add(EventName.locationdTemporaryError)
      if (self.sm.seen['vehicleParameters'] and not self.sm['vehicleParameters'].valid and cal_status == log.ExtrinsicsCalibration.Status.calibrated and
          not TESTING_CLOSET and (not SIMULATION or REPLAY)):
        self.events.add(EventName.paramsdTemporaryError)

    # conservative HW alert. if the data or frequency are off, locationd will throw an error
    if any((self.sm.frame - self.sm.recv_frame[s])*DT_CTRL > 10. for s in self.sensor_packets):
      self.events.add(EventName.sensorDataInvalid)

    if not REPLAY:
      # Check for mismatch between openpilot and car's PCM
      cruise_mismatch = CS.cruiseState.enabled and (not self.enabled or not self.CP.pcmCruise)
      self.cruise_mismatch_counter = self.cruise_mismatch_counter + 1 if cruise_mismatch else 0
      if self.cruise_mismatch_counter > int(6. / DT_CTRL):
        self.events.add(EventName.cruiseMismatch)

    # Send a "steering required alert" if saturation count has reached the limit
    if CS.steeringPressed:
      self.last_steering_pressed_frame = self.sm.frame
    recent_steer_pressed = (self.sm.frame - self.last_steering_pressed_frame)*DT_CTRL < 2.0
    controlstate = self.sm['controlsState']
    lac = getattr(controlstate.lateralControlState, controlstate.lateralControlState.which())
    if lac.active and not recent_steer_pressed and not self.CP.notCar:
      clipped_speed = max(CS.vEgo, 0.3)
      actual_lateral_accel = controlstate.curvature * (clipped_speed**2)
      desired_lateral_accel = self.sm['modelV2'].action.desiredCurvature * (clipped_speed**2)
      undershooting = abs(desired_lateral_accel) / abs(1e-3 + actual_lateral_accel) > 1.2
      turning = abs(desired_lateral_accel) > 1.0
      # TODO: lac.saturated includes speed and other checks, should be pulled out
      if undershooting and turning and lac.saturated:
        self.events.add(EventName.steerSaturated)

    # Check for FCW
    stock_long_is_braking = self.enabled and not self.CP.openpilotLongitudinalControl and CS.aEgo < -1.25
    model_fcw = self.sm['modelV2'].meta.hardBrakePredicted and not CS.brakePressed and not stock_long_is_braking
    planner_fcw = self.sm['longitudinalPlan'].fcw and self.enabled
    if (planner_fcw or model_fcw) and not self.CP.notCar:
      self.events.add(EventName.fcw)

    # GPS checks
    gps_ok = self.sm.recv_frame[self.gps_location_service] > 0 and (self.sm.frame - self.sm.recv_frame[self.gps_location_service]) * DT_CTRL < 2.0
    gps_ok = gps_ok or gps_observation(self.sm, 'starpilotCarState', time.monotonic_ns()) is not None
    if not gps_ok and self.sm['deviceMotion'].inputsOK and (self.distance_traveled > 1500):
      self.events.add(EventName.noGps)
    if gps_ok:
      self.distance_traveled = 0
    self.distance_traveled += abs(CS.vEgo) * DT_CTRL

    # TODO: fix simulator
    if not SIMULATION or REPLAY:
      if self.sm['modelV2'].frameDropPerc > 1 and not big_model_settling:
        self.events.add(EventName.modeldLagging)

    # Decrement personality on distance button press
    if self.CP.openpilotLongitudinalControl:
      for sample, _ in samples:
        if any(not be.pressed and be.type == ButtonType.gapAdjustCruise for be in sample.buttonEvents):
          self.personality = (self.personality - 1) % 3
          self.params.put('LongitudinalPersonality', self.personality)
          self.events.add(EventName.personalityChanged)


  def data_sample(self):
    self.car_state_retained_source_ns = self.aol_car_state_log_ns
    self.car_state_retained_valid = getattr(self, 'conditional_car_state_valid', False)
    _car_state = messaging.recv_one(self.car_state_sock)
    samples = []
    queue_invalid = False
    if _car_state is not None:
      samples.append(_car_state)
      if (getattr(self, 'aol_replay', False) and getattr(self, 'axis_transport_required', False) and
          not REPLAY):
        # Preserve every consumed transition; only the authority source advances.
        for _ in range(31):
          following = messaging.recv_one_or_none(self.car_state_sock)
          if following is None:
            break
          queue_invalid |= int(following.logMonoTime) <= int(samples[-1].logMonoTime)
          samples.append(following)
        else:
          # A full bounded batch cannot establish that the receiver caught up.
          queue_invalid = True
        _car_state = samples[-1]
    self.sm.update(0)

    if (getattr(self, 'aol_replay', False) and getattr(self, 'axis_transport_required', False) and
        not REPLAY):
      # A carState may arrive during SubMaster work after the blocking wait/drain.
      # Catch up without a second wait, preserving every transition and the total bound.
      for _ in range(32 - len(samples)):
        following = messaging.recv_one_or_none(self.car_state_sock)
        if following is None:
          break
        previous_stamp = int(samples[-1].logMonoTime) if samples else self.aol_car_state_log_ns
        queue_invalid |= int(following.logMonoTime) <= previous_stamp or not following.valid
        samples.append(following)
      else:
        queue_invalid = True
      if samples:
        _car_state = samples[-1]
      queue_invalid |= any(not sample.valid for sample in samples)

    self.car_state_envelopes = tuple(samples)
    self.car_state_queue_invalid = queue_invalid
    if samples:
      self.car_state_last_received_ns = max(int(sample.logMonoTime) for sample in samples)
    self.car_state_samples = tuple((sample.carState, bool(sample.valid)) for sample in samples)
    CS = _car_state.carState if _car_state else self.CS_prev
    self.aol_expired_healthy_source = False
    if _car_state is not None:
      self.aol_car_state_log_ns = int(_car_state.logMonoTime)
      self.conditional_car_state_valid = bool(_car_state.valid) and not queue_invalid
      if queue_invalid:
        self.aol_car_state_log_ns = 0
    else:
      # The 20 ms socket wait can expire between healthy CAN frames. Reusing
      # CS_prev must also keep its original timestamp, never create a new one.
      age_ns = time.monotonic_ns() - self.aol_car_state_log_ns
      retained = (self.conditional_car_state_valid and self.aol_car_state_log_ns > 0 and
                  0 <= age_ns <= INTENT_MAX_AGE_NS and CS.canValid and not CS.canTimeout)
      self.aol_expired_healthy_source = bool(self.conditional_car_state_valid and
        self.aol_car_state_log_ns > 0 and age_ns > INTENT_MAX_AGE_NS and CS.canValid and not CS.canTimeout)
      if not retained:
        self.aol_car_state_log_ns = 0
        self.conditional_car_state_valid = False

    if not self.initialized:
      all_valid = CS.canValid and self.sm.all_checks()
      if self.axis_transport_required and native_bootstrap_supported(self.CP):
        now_ns = self.aol_car_state_log_ns if REPLAY and self.aol_car_state_log_ns else time.monotonic_ns()
        all_valid = all_valid and current_native(self.sm, self.CP, now_ns=now_ns,
                                                axis_session_id=self.aol_session_id) is not None
      timed_out = self.sm.frame * DT_CTRL > 6.
      if all_valid or timed_out or (SIMULATION and not REPLAY):
        available_streams = VisionIpcClient.available_streams("camerad", block=False)
        if VisionStreamType.VISION_STREAM_NARROW_ROAD not in available_streams:
          self.sm.ignore_alive.add('narrowRoadCameraState')
          self.sm.ignore_valid.add('narrowRoadCameraState')
        if VisionStreamType.VISION_STREAM_WIDE_ROAD not in available_streams:
          self.sm.ignore_alive.add('wideRoadCameraState')
          self.sm.ignore_valid.add('wideRoadCameraState')

        if REPLAY and any(ps.controlsAllowed for ps in self.sm['pandaStates']):
          self.state_machine.state = State.enabled

        self.initialized = True
        cloudlog.event(
          "selfdrived.initialized",
          dt=self.sm.frame*DT_CTRL,
          timeout=timed_out,
          canValid=CS.canValid,
          invalid=[s for s, valid in self.sm.valid.items() if not valid],
          not_alive=[s for s, alive in self.sm.alive.items() if not alive],
          not_freq_ok=[s for s, freq_ok in self.sm.freq_ok.items() if not freq_ok],
          error=True,
        )

    # When the panda and selfdrived do not agree on controls_allowed
    # we want to disengage openpilot. However the status from the panda goes through
    # another socket other than the CAN messages and one can arrive earlier than the other.
    # Therefore we allow a mismatch for two samples, then we trigger the disengagement.
    if not self.enabled:
      self.mismatch_counter = 0

    # All pandas not in silent mode must have controlsAllowed when openpilot is enabled
    if self.enabled and any(not ps.controlsAllowed for ps in self.sm['pandaStates']
           if ps.safetyModel not in IGNORED_SAFETY_MODES):
      self.mismatch_counter += 1

    return CS

  def ford_source_deadline_supported(self):
    from opendbc.car.ford.aol import profile_word, qualified
    from opendbc.car.ford.values import CAR
    return (qualified(self.CP, marked_only=True) and
            self.CP.carFingerprint == CAR.FORD_MUSTANG_MACH_E_MK1 and
            len(self.CP.safetyConfigs) == 1 and profile_word(self.CP.safetyConfigs[0].safetyParam) == 3)

  def source_deadline_deferral_supported(self):
    from openpilot.starpilot.car.hyundai.aol import source_deadline_supported
    from openpilot.starpilot.car.toyota.aol import qualified as qualified_toyota
    return (qualified_ioniq6(self.CP) or qualified_gm(self.CP) or source_deadline_supported(self.CP) or
            qualified_toyota(self.CP, marked_only=True) or self.ford_source_deadline_supported())

  def held_axis_override_wait(self, CS, proof, now_ns, *, fresh_successor=False):
    """Recognize an already applied override; never admit a new physical edge."""
    if (proof is None or not (qualified_ioniq6(self.CP) or qualified_gm(self.CP) or
        (self.CP.brand == 'hyundai' and self.source_deadline_deferral_supported()) or
        self.ford_source_deadline_supported()) or not self.sm.seen['carControl'] or
        not self.sm.valid['carControl'] or self.sm['carControl'].longActive or
        not 0 < int(self.sm.logMonoTime['carControl']) <= now_ns or
        not fresh_successor and now_ns - int(self.sm.logMonoTime['carControl']) > INTENT_MAX_AGE_NS):
      return False, False
    previous = self.CS_prev
    # A missed socket read may retain only the original healthy receipt. This
    # recognizes an already-held override; the caller still checks its expiry.
    retained_valid = bool((self.CP.brand == 'hyundai' or self.ford_source_deadline_supported()) and
      getattr(self, 'aol_expired_healthy_source', False) and not getattr(self, 'car_state_queue_invalid', False) and
      getattr(self, 'car_state_retained_valid', False) and getattr(self, 'car_state_retained_source_ns', 0) == proof[1])
    samples = getattr(self, 'car_state_samples', ()) or ((CS, self.conditional_car_state_valid or retained_valid),)
    held_brake = bool(proof[8] and not proof[9] and not self.enabled and not self.active and
      (previous.brakePressed or previous.regenBraking) and
      all(valid and sample.brakePressed == previous.brakePressed and sample.regenBraking == previous.regenBraking
          and not sample.gasPressed and not sample.buttonEvents for sample, valid in samples))
    ford_lateral_gas = bool(self.ford_source_deadline_supported() and proof[8] and not proof[9] and
      (not self.enabled and not self.active and not CS.cruiseState.enabled or
       self.enabled and self.active and CS.cruiseState.enabled and proof[7]))
    held_gas = bool((proof[9] and self.enabled and self.active or ford_lateral_gas) and previous.gasPressed and
      self.events.contains(ET.OVERRIDE_LONGITUDINAL) and
      all(valid and sample.gasPressed and not sample.brakePressed and not sample.regenBraking
          and not sample.buttonEvents for sample, valid in samples))
    return held_brake, held_gas

  def drain_axis_intent(self, companion, *, max_messages=None):
    if getattr(self, 'aol_event_input_reconcile', False):
      remaining = max(0, 32 - getattr(self, 'aol_step_intent_drain_count', 0))
      max_messages = remaining if max_messages is None else min(max_messages, remaining)
    drained = companion.drain() if max_messages is None else companion.drain(max_messages=max_messages)
    # Empty later drains must not erase earlier restrictive burst evidence.
    self.aol_step_intent_drain_count = (getattr(self, 'aol_step_intent_drain_count', 0) +
                                       getattr(companion, 'last_drain_count', 0))
    # A later empty/fresh drain cannot erase a failed earlier burst. Only the
    # qualified interleaved reconciliation consumes this cumulative verdict;
    # legacy callers still receive this call's original result.
    self.aol_step_intent_drain_ok = bool(getattr(self, 'aol_step_intent_drain_ok', True) and drained)
    return drained

  def event_input_wait_ns(self, CS, now_ns, *, final_held_override=False):
    """Bound an input wait by an original published lease; grant no new output."""
    proof = getattr(self, 'aol_published_input_proof', None)
    companion = getattr(self, 'aol_intent_companion', None)
    intent = getattr(self, 'aol_last_intent', None)
    previous_native = getattr(self, 'aol_published_input_native', None)
    held_brake, held_gas = self.held_axis_override_wait(CS, proof, now_ns) if final_held_override else (False, False)
    from opendbc.car.hyundai.canfd_stock_aol import qualified as qualified_stock_hkg
    from opendbc.car.hyundai.canfd_angle_aol import qualified as qualified_angle_aol
    stock_cruise_off = bool(proof is not None and proof[8] and not proof[9] and
      not self.enabled and not self.active and not CS.cruiseState.enabled and
      (qualified_stock_hkg(self.CP, marked_only=True) or
       (self.ford_source_deadline_supported() and (held_brake or held_gas)) or
       qualified_angle_aol(self.CP, marked_only=True) and self.CP.pcmCruise and not self.CP.openpilotLongitudinalControl) and
      EventName.pcmDisable in self.events_prev and self.events.names == self.events_prev)
    user_disable = any(event.userDisable and
      not (held_brake and event.name == EventName.pedalPressed) and
      not (stock_cruise_off and event.name == EventName.pcmDisable) for event in self.events.to_msg())
    if (proof is None or companion is None or intent is None or previous_native is None or
        self.aol_authority_lost or
        user_disable or any(self.events.contains(kind) for kind in (ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE)) or
        not self.sm.all_checks() or self.rk.lagging or
        self.sm['extrinsicsCalibration'].calStatus != log.ExtrinsicsCalibration.Status.calibrated or
        self.sm['deviceState'].thermalStatus >= ThermalStatus.overheated or
        self.sm['deviceState'].freeSpacePercent < 7 or self.sm['deviceState'].memoryUsagePercent > 90 or
        self.sm['deviceMotion'].inputsOK is False or self.sm['deviceMotion'].posenetOK is False or
        self.sm.seen['vehicleParameters'] and not self.sm['vehicleParameters'].valid or
        any(self.sm['radarState'].radarErrors.to_dict().values()) or
        driving_process_failures(self.sm['managerState'].processes)):
      return 0
    session, source, published, deadline, producer, epoch, lateral, longitudinal, active_lateral, active_longitudinal = proof
    ordinary = bool(active_longitudinal and self.enabled and self.active)
    lateral_only = bool(active_lateral and not active_longitudinal and intent.allowedLatch and intent.lateralArmed)
    if not (ordinary or lateral_only):
      return 0
    if (session != self.aol_session_id or not 0 < source <= published <= now_ns < deadline or
        published - source > INTENT_MAX_AGE_NS or deadline != published + INTENT_MAX_AGE_NS or
        intent.producerSessionId != producer or intent.carStateLogMonoTime != source or companion.integrity_epoch != epoch):
      return 0
    wait_origin_ns = now_ns
    wait_deadline_ns = min(deadline, wait_origin_ns + int(DT_CTRL * 1e9))
    if not self.drain_axis_intent(companion) or companion.integrity_epoch != epoch:
      return 0
    # Transport may deliver intent published after the caller's clock sample.
    # Validate it against a post-drain clock without renewing the original wait.
    now_ns = time.monotonic_ns()
    if not wait_origin_ns <= now_ns < wait_deadline_ns:
      return 0
    latest = companion.latest
    if final_held_override and getattr(self, 'aol_step_intent_drain_count', 0) > 1:
      # A burst could contain an intermediate restriction. Do not infer an
      # unchanged permission from just its final sample during this hold.
      return 0
    # An unchanged pause on an unrequested, inactive axis does not revoke the
    # other axis's existing lease. New pauses and requested-axis pauses still veto.
    if (latest is None or not latest[1] or latest[2] is None or
        latest[2].producerSessionId != producer or not latest[2].settingsQualified or
        latest[2].pauseLateral and (lateral or active_lateral or not intent.pauseLateral) or
        latest[2].pauseLongitudinal and (longitudinal or active_longitudinal or not intent.pauseLongitudinal) or
        latest[2].allowedLatch != intent.allowedLatch or latest[2].lateralArmed != intent.lateralArmed or
        latest[0] != latest[2].carStateLogMonoTime or not 0 < latest[0] <= now_ns or
        not 0 < latest[2].observedMonoTime <= now_ns <= latest[2].validUntilMonoTime or
        not 0 < latest[2].validUntilMonoTime - latest[2].observedMonoTime <= SAFETY_MAX_AGE_NS):
      return 0
    samples = getattr(self, 'car_state_samples', ()) or ((CS, getattr(self, 'car_state_retained_valid', False)),)
    if any(not valid or not sample.canValid or sample.canTimeout or sample.steerFaultTemporary or
           sample.steerFaultPermanent or (sample.brakePressed or sample.regenBraking) and not held_brake or
           active_longitudinal and sample.gasPressed and not held_gas or
           sample.seatbeltUnlatched or sample.accFaulted or sample.doorOpen or physical_cancel(sample, self.CP) or
           qualified_gm(self.CP) and not sample.cruiseState.available or
           sample.gearShifter not in (car.CarState.GearShifter.drive, car.CarState.GearShifter.sport,
                                     car.CarState.GearShifter.low, car.CarState.GearShifter.brake)
           for sample, valid in samples):
      return 0
    native = current_native(self.sm, self.CP, now_ns=now_ns, axis_session_id=session)
    transport_current = healthy_transport(self.sm, self.CP, CS, session, now_ns, native)
    if not transport_current and held_gas and self.ford_source_deadline_supported() and not active_longitudinal:
      from openpilot.starpilot.aol.transport_pause import independent_lateral_health
      transport_current = native is not None and independent_lateral_health(self.sm, self.CP, CS, session, now_ns) == native
    if (native is None or not transport_current or
        native.pandaSerial != previous_native.pandaSerial or native.pandaInventory != previous_native.pandaInventory or
        native.observedMonoTime < previous_native.observedMonoTime or
        bool(native.requestedLateral) != lateral or bool(native.requestedLongitudinal) != longitudinal):
      return 0
    return wait_deadline_ns - wait_origin_ns

  def refresh_event_car_state(self, CS, *, allow_wait=True):
    if not getattr(self, 'event_input_refresh_pending', False) or REPLAY or not self.aol_replay or not self.axis_transport_required:
      return CS
    samples = list(getattr(self, 'car_state_envelopes', ()))
    invalid = getattr(self, 'car_state_queue_invalid', False)
    previous_stamp = int(samples[-1].logMonoTime) if samples else getattr(self, 'car_state_last_received_ns', 0)
    initial_count = len(samples)
    for _ in range(32 - len(samples)):
      following = messaging.recv_one_or_none(self.car_state_sock)
      if following is None:
        break
      invalid |= int(following.logMonoTime) <= previous_stamp or not following.valid
      previous_stamp = max(previous_stamp, int(following.logMonoTime))
      samples.append(following)
    else:
      invalid = True
    # If a real producer gap straddles the source cutoff, only wait while the
    # original published proof can independently remain valid at consumers.
    now_ns = time.monotonic_ns()
    selected_stamp = int(samples[-1].logMonoTime) if samples else getattr(self, 'car_state_retained_source_ns', 0)
    if (allow_wait and not invalid and len(samples) == initial_count and selected_stamp > 0 and
        now_ns - selected_stamp > INTENT_MAX_AGE_NS and
        not (getattr(self, 'aol_event_input_reconcile', False) and getattr(self, 'aol_event_input_wait_attempted', False))):
      self.aol_event_input_wait_attempted = True
      wait_until_ns = now_ns + self.event_input_wait_ns(CS, now_ns)
      poller = getattr(self, 'car_state_poller', None)
      # Validation itself can consume the remaining lease. Never poll from its
      # earlier clock; the original absolute deadline still bounds this wait.
      wait_ns = max(0, wait_until_ns - time.monotonic_ns())
      polled = False
      if poller is not None and wait_ns >= 1_000_000:
        polled = poller.poll(wait_ns // 1_000_000)
      if polled:
        for _ in range(32 - len(samples)):
          following = messaging.recv_one_or_none(self.car_state_sock)
          if following is None:
            break
          invalid |= int(following.logMonoTime) <= previous_stamp or not following.valid
          previous_stamp = max(previous_stamp, int(following.logMonoTime))
          samples.append(following)
        else:
          invalid = True
    self.car_state_envelopes = tuple(samples)
    self.car_state_queue_invalid = invalid
    self.car_state_last_received_ns = previous_stamp
    self.car_state_samples = tuple((sample.carState, bool(sample.valid)) for sample in samples)
    if samples:
      latest = samples[-1]
      self.aol_car_state_log_ns = 0 if invalid else int(latest.logMonoTime)
      self.conditional_car_state_valid = bool(latest.valid) and not invalid
      self.aol_expired_healthy_source = False
      CS = latest.carState
    return CS

  def defer_expired_axis_publication(self, CS, initial_state, *, now_ns=None):
    """Keep an existing unexpired proof for one normal tick; issue no new lease."""
    if REPLAY or not self.aol_replay or not self.axis_transport_required or not self.source_deadline_deferral_supported():
      return False
    proof = getattr(self, 'aol_step_input_proof', None)
    if now_ns is None:
      now_ns = time.monotonic_ns()
    source = self.aol_car_state_log_ns
    source_valid = self.conditional_car_state_valid
    from openpilot.starpilot.car.hyundai.aol import source_deadline_supported
    from openpilot.starpilot.car.toyota.aol import qualified as qualified_toyota
    if ((source_deadline_supported(self.CP) or qualified_toyota(self.CP, marked_only=True) or
         self.ford_source_deadline_supported()) and
        source == 0 and not source_valid and
        getattr(self, 'aol_expired_healthy_source', False) and
        not getattr(self, 'car_state_samples', ()) and not getattr(self, 'car_state_queue_invalid', False) and
        getattr(self, 'car_state_retained_valid', False)):
      # A socket timeout cleared the selected source. Validate only the old
      # publication's receipt; keep the source invalid and its clocks unchanged.
      source = getattr(self, 'car_state_retained_source_ns', 0)
      source_valid = True
    if (getattr(self, 'aol_step_deferral_checked', False) or proof is None or proof != getattr(self, 'aol_published_input_proof', None) or
        not source_valid or not 0 < source <= now_ns or
        now_ns - source <= INTENT_MAX_AGE_NS or source != proof[1] or
        now_ns >= proof[3] or self.aol_authority_lost or
        getattr(self, 'aol_fault_reason', 'none') != 'none' or
        getattr(self, 'aol_published_fault_reason', 'none') != 'none' or
        initial_state != (self.state_machine.state, self.state_machine.soft_disable_timer, self.enabled, self.active) or
        (self.state_machine.soft_disable_timer != 0 and not (
          self.state_machine.state == log.SelfdriveState.OpenpilotState.disabled and
          not self.enabled and not self.active and getattr(self, 'aol_availability_long_inhibit', False) and
          proof[6] and not proof[7] and proof[8] and not proof[9])) or self.events.names != self.events_prev or
        CS.buttonEvents or any(sample.buttonEvents for sample, _ in getattr(self, 'car_state_samples', ())) or
        any(self.events.contains(kind) for kind in (ET.ENABLE, ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE)) or
        (self.aol_axis_decision.lateral_active, self.aol_axis_decision.longitudinal_active) != (proof[8], proof[9])):
      return False
    # This eligibility check uses the same native/intent/health checks as an
    # existing bounded input wait. It never changes the source or publishes an
    # active decision. The next ordinary loop iteration consumes the next input.
    self.aol_step_deferral_checked = True
    allowed = self.event_input_wait_ns(CS, now_ns, final_held_override=True) > 0
    return bool(allowed and now_ns <= time.monotonic_ns() < proof[3])

  def reconciled_intent(self, intent, previous, pairs, epoch, now_ns):
    sources = getattr(self, 'aol_reconciled_sources', ())
    if not sources or intent is None:
      return intent
    companion = self.aol_intent_companion
    proof = getattr(self, 'aol_step_input_proof', None)
    if (proof is None or proof[0] != self.aol_session_id or companion is None or
        epoch != proof[5] or companion.integrity_epoch != epoch or
        intent.producerSessionId != proof[4]):
      return None
    for source_ns in sources:
      pair = pairs.get(source_ns)
      if (pair is None and previous is not None and source_ns == proof[1] and
          previous.carStateLogMonoTime == source_ns and previous.producerSessionId == proof[4]):
        pair = (source_ns, True, previous)
      if pair is None or not pair[1] or pair[2] is None:
        return None
      restriction = pair[2]
      if (pair[0] != source_ns or restriction.carStateLogMonoTime != source_ns or
          not restriction.settingsQualified or restriction.producerSessionId != intent.producerSessionId or
          not 0 < source_ns <= now_ns or not 0 < restriction.observedMonoTime <= now_ns <= restriction.validUntilMonoTime or
          not 0 < restriction.validUntilMonoTime - restriction.observedMonoTime <= SAFETY_MAX_AGE_NS):
        return None
      # Earlier exact companions only veto the normally validated fresh pair.
      # Their clocks never renew a source or supply an output lease.
      intent = replace(intent, allowedLatch=intent.allowedLatch and restriction.allowedLatch,
        lateralArmed=intent.lateralArmed and restriction.lateralArmed,
        pauseLateral=intent.pauseLateral or restriction.pauseLateral,
        pauseLongitudinal=intent.pauseLongitudinal or restriction.pauseLongitudinal,
        optionalSetRelease=intent.optionalSetRelease and restriction.optionalSetRelease)
    return intent

  def update_alerts(self, CS):
    # Engagement may change after car events; retire only the inactive advisory.
    if (EventName.belowSteerSpeed in self.events.names or 'belowSteerSpeed/warning' in self.AM.alerts) and not advisory_eligible(
        self.CP, CS, active=self.active, intent=self.aol_last_intent if self.aol_replay else None):
      self.events.events = [event for event in self.events.names if event != EventName.belowSteerSpeed]
      self.AM.alerts.pop('belowSteerSpeed/warning', None)
    clear_event_types = set()
    if ET.WARNING not in self.state_machine.current_alert_types:
      clear_event_types.add(ET.WARNING)
    if self.enabled:
      clear_event_types.add(ET.NO_ENTRY)

    pers = LONGITUDINAL_PERSONALITY_MAP[self.personality]
    alerts = self.events.create_alerts(self.state_machine.current_alert_types, [self.CP, CS, self.sm, self.is_metric,
                                                                                self.state_machine.soft_disable_timer, pers])
    if getattr(self, 'aol_lag_takeover_alert', False) and not self.events.contains(ET.IMMEDIATE_DISABLE):
      lag = Events(self.events.event_rules)
      lag.add(EventName.selfdrivedLagging)
      # Steering is already withdrawn, irrespective of any old ordinary timer.
      alerts.extend(lag.create_alerts([ET.SOFT_DISABLE], [self.CP, CS, self.sm, self.is_metric, 0, pers]))
    if (getattr(self, 'aol_soft_takeover_alert', False) and
        not getattr(self, 'aol_lag_takeover_alert', False) and not self.events.contains(ET.IMMEDIATE_DISABLE)):
      # AOL already withdrew steering while ordinary cruise was disabled.
      # Select the existing fault's takeover warning without starting LONG or
      # changing its event classification, threshold, or output decision.
      alerts.extend(alert for alert in self.events.create_alerts(
        [ET.SOFT_DISABLE], [self.CP, CS, self.sm, self.is_metric, 0, pers]) if alert.alert_type != 'doorOpen/softDisable')
    alerts.extend(alert for alert in getattr(self, 'tesla_stock_alerts', [])
                  if alert.event_type in self.state_machine.current_alert_types)
    now_ns = time.monotonic_ns()
    drive = int(self.sm['deviceState'].startedMonoTime) if self.sm['deviceState'].started else 0
    from openpilot.starpilot.audio.blindspot_alert import ALERT_TYPE as BSM_ALERT, blindspot_alert
    loud, without_lateral = getattr(self, 'blindspot_alert_settings', (False, False))
    blindspot = blindspot_alert(CS, self.sm, loud=loud, without_lateral=without_lateral,
                                lane_change_blocked=EventName.laneChangeBlocked in self.events.names, now_ns=now_ns)
    if blindspot is not None:
      alerts = [alert for alert in alerts if alert.alert_type != 'laneChangeBlocked/warning']
      self.AM.alerts.pop('laneChangeBlocked/warning', None)
      alerts.append(blindspot)
    else:
      self.AM.alerts.pop(BSM_ALERT, None)
    switchback = bool(self.switchback_capable and self.sm.seen['slcState'] and self.sm.alive['slcState'] and
                      self.sm.valid['slcState'] and self.sm['carControl'].latActive and
                      self.switchback_status.sample_state(self.sm['slcState'], drive_id=drive, now_ns=now_ns,
                        event_ns=int(self.sm.logMonoTime['slcState'])))
    if now_ns - self.switchback_setting_ns >= 1_000_000_000:
      self.switchback_setting_ns = now_ns
      try:
        cooldown = float(self.params.get('SwitchbackModeCooldown') or 5)
        self.switchback_cooldown_ns = int(cooldown * 60e9) if math.isfinite(cooldown) and 0 <= cooldown <= 30 else 0
      except (TypeError, ValueError, OverflowError):
        self.switchback_cooldown_ns = 0
    # Reset the advisory clock even on frames without either advisory.
    self.switchback_cooldown.allow('', active=switchback, drive_id=drive, now_ns=now_ns,
                                  cooldown_ns=self.switchback_cooldown_ns)
    alerts = [alert for alert in alerts if alert.alert_type not in ('belowSteerSpeed/warning', 'steerSaturated/warning') or
              self.switchback_cooldown.allow(alert.alert_type.split('/')[0], active=switchback,
                drive_id=drive, now_ns=now_ns, cooldown_ns=self.switchback_cooldown_ns)]
    for index, alert in enumerate(alerts):
      if alert.alert_type not in ("preLaneChangeLeft/warning", "preLaneChangeRight/warning"):
        continue
      source_ok = (self.sm.seen['laneChangeAssistWire'] and self.sm.alive['laneChangeAssistWire'] and
                   self.sm.valid['laneChangeAssistWire'] and self.sm.valid['modelV2'])
      status = decode_lane_status(self.sm['laneChangeAssistWire']) if source_ok else None
      now_mono_ns = time.monotonic_ns()
      recv_ns = int(self.sm.recv_time['laneChangeAssistWire'] * 1e9)
      message_ns = int(self.sm.logMonoTime['laneChangeAssistWire'])
      valid_status = fresh_for_model(status, self.sm['modelV2'], now_mono_ns, recv_ns,
                                    self.lane_status_sequence, self.lane_status_session, message_ns)
      if valid_status:
        assert status is not None
        self.lane_status_session, self.lane_status_sequence = status.producer_session_id, status.sequence
      wording = alert_wording(status if valid_status else None, self.sm['modelV2'], now_mono_ns, recv_ns)
      if wording is None:
        continue
      replacement = copy(alert)
      replacement.alert_text_1, replacement.alert_text_2 = wording
      alerts[index] = replacement
    if not hasattr(self, 'force_stop_hold_alert'):
      self.force_stop_hold_alert = HoldAlertState()
    holding = self.force_stop_hold_alert.active(
      self.sm, self.CP, CS, enabled=self.enabled, car_ns=getattr(self, 'aol_car_state_log_ns', 0),
      car_valid=getattr(self, 'conditional_car_state_valid', False), now_ns=time.monotonic_ns()) and ET.WARNING in self.state_machine.current_alert_types
    if holding:
      alert = Alert("Force Stop Holding", "Press RES or accelerator to proceed", log.SelfdriveState.AlertStatus.normal,
                    log.SelfdriveState.AlertSize.mid, AlertPriority.LOW, car.CarControl.HUDControl.VisualAlert.none,
                    log.SelfdriveState.AudibleAlert.none, 0.)
      alert.alert_type, alert.event_type = "forceStopHold/warning", FORCE_STOP_HOLD
      alerts.append(alert)
    else:
      clear_event_types.add(FORCE_STOP_HOLD)
    from openpilot.starpilot.car.gm.radar_recovery import EVENT_TYPE as RADAR_RECOVERY_EVENT, RecoveryNotification
    if not hasattr(self, 'radar_recovery_notification'):
      self.radar_recovery_notification = RecoveryNotification()
    recovery = self.radar_recovery_notification
    notification = recovery.update(self.CP, self.sm, enabled=getattr(self, 'radar_recovery_enabled', False), now_ns=now_ns)
    if notification is not None:
      alerts.append(notification)
    if not recovery.monitor.visible(now_ns):
      clear_event_types.add(RADAR_RECOVERY_EVENT)
    # A cold/unarmed fault classification is not a pause of expected steering.
    # Qualified availability retention already carries both fields even while
    # ordinary cruise is disabled; this only selects its presentation.
    availability_warning = bool(getattr(self, 'aol_fault_reason', 'none') == 'availabilityPause' and
      self.aol_axis_decision.desired_lateral and self.aol_axis_decision.native_acknowledged)
    if availability_warning:
      warning = Alert("Steering Paused", "Communication Issue", log.SelfdriveState.AlertStatus.normal,
        log.SelfdriveState.AlertSize.small, AlertPriority.LOW, car.CarControl.HUDControl.VisualAlert.none,
        (log.SelfdriveState.AudibleAlert.prompt if
         now_ns <= getattr(self, 'aol_availability_chime_until_ns', 0) else log.SelfdriveState.AudibleAlert.none), 0.)
      warning.alert_type, warning.event_type = 'aolAvailability/warning', ET.WARNING
      alerts.append(warning)
    else:
      # Retire only this presentation; retain every original safety alert.
      self.AM.alerts.pop('aolAvailability/warning', None)
    if getattr(self, 'jetlink_link_lost', False):
      alerts.append(jetlink_lost_alert())
    self.AM.add_many(self.sm.frame, alerts)
    self.AM.process_alerts(self.sm.frame, clear_event_types)
    from openpilot.starpilot.audio.availability_warning import AvailabilityWarningDelivery
    if not hasattr(self, 'availability_warning_delivery'):
      self.availability_warning_delivery = AvailabilityWarningDelivery()
    selected = self.AM.current_alert
    self.availability_warning_delivery.observe(session=getattr(self, 'aol_session_id', None), drive=drive,
      now_ns=now_ns, selected=bool(selected.alert_type == 'aolAvailability/warning' and
        selected.audible_alert == log.SelfdriveState.AudibleAlert.prompt),
      requested_until_ns=getattr(self, 'aol_availability_chime_until_ns', 0))
    recovery.selected(self.AM.current_alert)

  def ordinary_source_availability(self, CS, now_ns, native=None):
    """Classify lost host source; never renew a command or change engagement."""
    return ordinary_source_availability(self, CS, now_ns, native, replay=REPLAY, native_provider=current_native)

  def observe_ordinary_source_metadata(self, CS, intent, healthy_native, now_ns):
    """Reuse this call's exact transport validation; retain the original proof."""
    native = healthy_native
    anchor = getattr(self, 'aol_ordinary_source_anchor', None)
    companion = getattr(self, 'aol_intent_companion', None)
    if (anchor is None or not (anchor[0][8] or anchor[0][9]) or
        anchor[0][9] and (not self.enabled or not self.active) or intent is None or
        not 0 < intent.carStateLogMonoTime <= now_ns or now_ns - intent.carStateLogMonoTime > INTENT_MAX_AGE_NS or
        intent.carStateLogMonoTime <= anchor[2].carStateLogMonoTime or
        intent.producerSessionId != anchor[0][4] or companion is None or companion.integrity_epoch != anchor[0][5] or
        (self.events.contains(ET.USER_DISABLE) and not stock_lateral_role(self, CS, native, intent)) or
        self.events.contains(ET.IMMEDIATE_DISABLE) or
        native is None or native is not getattr(self.aol_transport_pause, 'observed_native', None) or
        native.pandaSerial != anchor[1].pandaSerial or native.observedMonoTime < anchor[1].observedMonoTime or
        tuple(p.hardwareSerial for p in native.pandaInventory) != tuple(p.hardwareSerial for p in anchor[1].pandaInventory)):
      return
    # The originally published command proof, deadline and CP identity remain
    # unchanged. Only an input already admitted by normal runtime is remembered.
    self.aol_ordinary_source_anchor = (anchor[0], native, intent, anchor[3], CS)

  def ordinary_source_withdrawal(self):
    accepted = self.aol_ordinary_source_anchor[2]
    native = self.aol_ordinary_source_native
    lateral_grant = bool(accepted.allowedLatch and accepted.lateralArmed and not accepted.pauseLateral and
                         not getattr(self, 'aol_dm_lateral_inhibit', False) and
                         native.requestedLateral and native.lateralAllowed)
    # Both commands remain inactive. This ACK describes only an actual native
    # lateral grant supporting separately retained armed intent; none is invented.
    return AxisDecision(desired_lateral=lateral_grant, native_acknowledged=lateral_grant)

  def retain_ordinary_source_state(self, CS, now_ns):
    """Publication-time expiry withdraws commands; StateMachine owns countdown."""
    if not self.ordinary_source_availability(CS, now_ns):
      return False
    self.aol_ordinary_source_pause = True
    self.aol_publication_retained_intent = self.aol_ordinary_source_anchor[2]
    self.events.add(EventName.commIssue)
    self.aol_axis_decision = self.ordinary_source_withdrawal()
    self.aol_fault_reason = 'availabilityPause'
    self.aol_car_state_log_ns = 0
    self.conditional_car_state_valid = False
    # If publication crosses the cutoff after StateMachine ran, emit the fault
    # now; its existing next-cycle transition owns the timer and driver state.
    if self.enabled and ET.SOFT_DISABLE not in self.state_machine.current_alert_types:
      self.state_machine.current_alert_types.append(ET.SOFT_DISABLE)
    return True

  def _expire_axis_source(self, CS, now_ns):
    if REPLAY or not self.aol_replay or not self.axis_transport_required:
      return
    source_ns = self.aol_car_state_log_ns
    if 0 < source_ns <= now_ns and now_ns - source_ns <= INTENT_MAX_AGE_NS:
      self.aol_publication_retained_intent = None
      return
    if self.retain_ordinary_source_state(CS, now_ns):
      return
    pause = getattr(self, 'aol_transport_pause', None)
    if (getattr(self, 'aol_fault_reason', 'none') == 'transportPause' and pause is not None and
        pause.active and pause.observed_native is not None and not self.aol_authority_lost and
        not getattr(self, 'aol_selected_pair_future', False) and
        not self.aol_axis_decision.lateral_active and not self.aol_axis_decision.longitudinal_active):
      # This step already validated the typed pause; its expired source grants no output.
      self.aol_car_state_log_ns = 0
      self.conditional_car_state_valid = False
      self.aol_publication_retained_intent = None
      self.aol_axis_decision = AxisDecision()
      self.state_machine.state = log.SelfdriveState.OpenpilotState.disabled
      self.enabled = self.active = False
      if ET.ENABLE in self.state_machine.current_alert_types:
        self.state_machine.current_alert_types.remove(ET.ENABLE)
      return
    intent = getattr(self, 'aol_last_intent', None) or getattr(self, 'aol_publication_retained_intent', None)
    if not source_ns and intent is not None:
      source_ns = int(intent.carStateLogMonoTime)
    healthy_expiry = bool(not getattr(self, 'aol_selected_pair_future', False) and source_ns > 0 and source_ns <= now_ns and
      now_ns - source_ns <= SAFETY_MAX_AGE_NS and CS.canValid and not CS.canTimeout and
      (getattr(self, 'conditional_car_state_valid', False) or
       getattr(self, 'aol_publication_retained_intent', None) is intent))
    self.aol_car_state_log_ns = 0
    self.conditional_car_state_valid = False
    self.aol_expired_healthy_source = healthy_expiry
    previous = self.aol_axis_decision
    was_enabled = self.enabled
    self.aol_axis_decision = AxisDecision()
    if healthy_expiry and not was_enabled and self.cold_gm_main_readiness(CS, intent, now_ns):
      self.aol_fault_reason = 'availabilityPause'
      self.aol_publication_retained_intent = intent
      return
    pause = getattr(self, 'aol_availability_pause', None)
    if (healthy_expiry and pause is not None and intent is not None and
        getattr(self, 'aol_fault_reason', 'none') in ('none', 'availabilityPause') and
        not self.aol_authority_lost and not self.events.contains(ET.IMMEDIATE_DISABLE) and
        (not self.events.contains(ET.SOFT_DISABLE) or availability_only(self.events.to_msg())) and
        not self.events.contains(ET.USER_DISABLE)):
      self.events.add(EventName.commIssue)
      native = current_native(self.sm, self.CP, now_ns=now_ns, axis_session_id=self.aol_session_id)
      from openpilot.starpilot.aol.transport_pause import _healthy_transport_native
      healthy = _healthy_transport_native(self.sm, self.CP, CS, self.aol_session_id, now_ns, native)
      retained = pause.observe(cp=self.CP, session=self.aol_session_id, sm=self.sm, cs=CS,
        native=native, now_ns=now_ns, companion=getattr(self, 'aol_intent_companion', None),
        intent=intent, events=self.events.to_msg(),
        model_ready=bool(self.sm.all_checks(['modelV2', 'extrinsicsCalibration']) and
          self.sm['extrinsicsCalibration'].calStatus == log.ExtrinsicsCalibration.Status.calibrated),
        source_current=False, source_expired=True, prior_lateral_active=previous.lateral_active,
        hard_fault=False, healthy_native=healthy)
      if retained:
        self.aol_publication_retained_intent = intent
        self.aol_fault_reason = 'availabilityPause'
        self.aol_availability_long_inhibit = True
        self.aol_availability_ordinary_warning = bool(was_enabled or (
          getattr(self, 'aol_published_fault_reason', 'none') == 'availabilityPause' and
          getattr(self, 'aol_availability_ordinary_warning', False)))
        if getattr(self, 'aol_published_fault_reason', 'none') != 'availabilityPause':
          self.aol_availability_chime_until_ns = now_ns + 400_000_000
        self.state_machine.state = log.SelfdriveState.OpenpilotState.disabled
        self.enabled = self.active = False
        if ET.ENABLE in self.state_machine.current_alert_types:
          self.state_machine.current_alert_types.remove(ET.ENABLE)
        if self.aol_availability_ordinary_warning and ET.SOFT_DISABLE not in self.state_machine.current_alert_types:
          self.state_machine.current_alert_types.append(ET.SOFT_DISABLE)
        self.aol_axis_decision = AxisDecision(desired_lateral=True, native_acknowledged=True)
        if ET.WARNING not in self.state_machine.current_alert_types:
          self.state_machine.current_alert_types.append(ET.WARNING)
        return
    self.aol_publication_retained_intent = None
    self.state_machine.state = log.SelfdriveState.OpenpilotState.disabled
    self.enabled = self.active = False
    if ET.ENABLE in self.state_machine.current_alert_types:
      self.state_machine.current_alert_types.remove(ET.ENABLE)
    cold_gm_startup = bool(not self.initialized and qualified_gm(self.CP) and
      not getattr(self, 'aol_ever_active', False) and not self.aol_authority_lost and
      not self.events.contains(ET.IMMEDIATE_DISABLE))
    # Armed MAIN while initializing is intent, not previously granted actuation.
    # Expiry still removes the source and all output; actual active/fault paths
    # retain their existing immediate withdrawal and warning ownership.
    if (was_enabled or previous.lateral_active or previous.longitudinal_active or
        intent is not None and intent.allowedLatch and not cold_gm_startup):
      self.aol_fault_reason = 'critical'
      self.aol_authority_lost = True
      self.events.add(EventName.controlsMismatch)
      if ET.IMMEDIATE_DISABLE not in self.state_machine.current_alert_types:
        self.state_machine.current_alert_types.append(ET.IMMEDIATE_DISABLE)

  def _expire_axis_continuity(self, CS, now_ns):
    proof = getattr(self, 'aol_step_input_proof', getattr(self, 'aol_published_input_proof', None))
    decision = self.aol_axis_decision
    if (proof is None or proof[0] != self.aol_session_id or now_ns <= proof[3] or
        not (decision.lateral_active or decision.longitudinal_active)):
      return False
    source_ns = self.aol_car_state_log_ns
    intent = getattr(self, 'aol_last_intent', None)
    companion = getattr(self, 'aol_intent_companion', None)
    fresh_successor = bool(proof[1] < source_ns <= now_ns and now_ns - source_ns <= INTENT_MAX_AGE_NS and
        self.conditional_car_state_valid and CS.canValid and not CS.canTimeout and
        intent is not None and intent.carStateLogMonoTime == source_ns and
        intent.producerSessionId == proof[4] and intent.settingsQualified and
        0 < intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime and
        now_ns - intent.observedMonoTime <= INTENT_MAX_AGE_NS and
        companion is not None and companion.integrity_epoch == proof[5] and
        decision.native_acknowledged and not self.aol_authority_lost and
        getattr(self, 'aol_fault_reason', 'none') == 'none' and
        not self.events.contains(ET.IMMEDIATE_DISABLE) and
        (not decision.longitudinal_active or self.enabled and self.active))
    user_disable = self.events.contains(ET.USER_DISABLE)
    if user_disable:
      from opendbc.car.hyundai.canfd_angle_aol import qualified as angle_qualified
      from opendbc.car.hyundai.canfd_stock_aol import qualified as stock_canfd_qualified
      from openpilot.starpilot.car.toyota.aol import qualified as toyota_qualified

      if (toyota_qualified(self.CP, marked_only=True) and CS.standstill or
          angle_qualified(self.CP, marked_only=True) or
          self.CP.brand == 'hyundai' and len(self.CP.safetyConfigs) == 1 and
          stock_canfd_qualified(self.CP, marked_only=True)):
        native = current_native(self.sm, self.CP, now_ns=now_ns, axis_session_id=self.aol_session_id)
        if stock_lateral_role(self, CS, native, intent, allow_independent_cancel=True,
                              allow_independent_brake=True, allow_independent_gas=True, now_ns=now_ns):
          user_disable = False
    if (user_disable and fresh_successor and proof[6] and not proof[7] and proof[8] and not proof[9] and
        decision.desired_lateral and not decision.desired_longitudinal and not decision.longitudinal_active and
        not self.enabled and not self.active and self.CP.brand == 'toyota' and toyota_qualified(self.CP, marked_only=True) and
        CS.cruiseState.available and not CS.cruiseState.enabled and
        self.CS_prev.cruiseState.available and not self.CS_prev.cruiseState.enabled and
        all(valid and sample.cruiseState.available and not sample.cruiseState.enabled
            for sample, valid in getattr(self, 'car_state_samples', ()))):
      # An already-off PCM is not a new cruise cancellation. Only a fresh,
      # independently checked LAT source may replace the expired LAT-only proof.
      user_disable = any(event.userDisable and event.name != EventName.pcmDisable for event in self.events.to_msg())
    held_brake, held_gas = self.held_axis_override_wait(CS, proof, now_ns,
      fresh_successor=fresh_successor and (qualified_gm(self.CP) or self.ford_source_deadline_supported()))
    if user_disable and (held_brake or self.ford_source_deadline_supported() and held_gas):
      # A fresh same-owner source can establish its own LAT permission after
      # an old output expires. Held-brake continuity does not renew that output;
      # the current native proof below still has to grant the new decision.
      stock_already_off = bool(fresh_successor and
        ((qualified_gm(self.CP) and self.CP.pcmCruise and not self.CP.openpilotLongitudinalControl) or
         self.ford_source_deadline_supported()) and not CS.cruiseState.enabled and
        not self.CS_prev.cruiseState.enabled and all(valid and not sample.cruiseState.enabled
          for sample, valid in getattr(self, 'car_state_samples', ())))
      user_disable = any(event.userDisable and event.name != EventName.pedalPressed and not
        (stock_already_off and event.name == EventName.pcmDisable) for event in self.events.to_msg())
    if fresh_successor and not user_disable:
      native = current_native(self.sm, self.CP, now_ns=now_ns, axis_session_id=self.aol_session_id)
      prior_native = getattr(self, 'aol_published_input_native', None)
      if (native is not None and prior_native is not None and native.pandaSerial == prior_native.pandaSerial and
          native.observedMonoTime >= prior_native.observedMonoTime and
          tuple(slot.hardwareSerial for slot in native.pandaInventory) ==
          tuple(slot.hardwareSerial for slot in prior_native.pandaInventory) and
          (not decision.lateral_active or native.requestedLateral and native.lateralAllowed) and
          (not decision.longitudinal_active or native.requestedLongitudinal and native.longitudinalAllowed) and
          (not self.events.contains(ET.SOFT_DISABLE) or
           fresh_ordinary_lag_continuation(self, CS, intent, native, now_ns))):
        # The old lease still expires at consumers. A newly validated source
        # supplies its own permission without restoring a disengaged state.
        return False
    # The expired command still withdraws; host loss need not revoke a healthy
    # driver's ordinary engagement before the existing soft-disable countdown.
    if self.retain_ordinary_source_state(CS, now_ns):
      return True
    # A retained source cannot renew an expired output lease.
    self.aol_axis_decision = AxisDecision()
    was_enabled = self.enabled
    self.state_machine.state = log.SelfdriveState.OpenpilotState.disabled
    self.enabled = self.active = False
    self.aol_availability_long_inhibit = True
    if ET.ENABLE in self.state_machine.current_alert_types:
      self.state_machine.current_alert_types.remove(ET.ENABLE)
    intent = getattr(self, 'aol_last_intent', None)
    pause = getattr(self, 'aol_availability_pause', None)
    retained = False
    if (intent is not None and pause is not None and not self.aol_authority_lost and
        getattr(self, 'aol_fault_reason', 'none') == 'none' and
        not any(self.events.contains(kind) for kind in (ET.USER_DISABLE, ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE))):
      self.events.add(EventName.commIssue)
      native = current_native(self.sm, self.CP, now_ns=now_ns, axis_session_id=self.aol_session_id)
      from openpilot.starpilot.aol.transport_pause import _healthy_transport_native
      healthy = _healthy_transport_native(self.sm, self.CP, CS, self.aol_session_id, now_ns, native)
      retained = pause.observe(cp=self.CP, session=self.aol_session_id, sm=self.sm, cs=CS,
        native=native, now_ns=now_ns, companion=getattr(self, 'aol_intent_companion', None),
        intent=intent, events=self.events.to_msg(),
        model_ready=bool(self.sm.all_checks(['modelV2', 'extrinsicsCalibration']) and
          self.sm['extrinsicsCalibration'].calStatus == log.ExtrinsicsCalibration.Status.calibrated),
        source_current=bool(self.conditional_car_state_valid and 0 < self.aol_car_state_log_ns <= now_ns and
          now_ns - self.aol_car_state_log_ns <= INTENT_MAX_AGE_NS and CS.canValid and not CS.canTimeout),
        source_expired=False, prior_lateral_active=bool(proof[8]), hard_fault=False, healthy_native=healthy)
    if retained:
      self.aol_fault_reason = 'availabilityPause'
      self.aol_axis_decision = AxisDecision(desired_lateral=True, native_acknowledged=True)
      self.aol_availability_ordinary_warning = was_enabled
      if getattr(self, 'aol_published_fault_reason', 'none') != 'availabilityPause':
        self.aol_availability_chime_until_ns = now_ns + 400_000_000
      if ET.WARNING not in self.state_machine.current_alert_types:
        self.state_machine.current_alert_types.append(ET.WARNING)
      if was_enabled and ET.SOFT_DISABLE not in self.state_machine.current_alert_types:
        self.state_machine.current_alert_types.append(ET.SOFT_DISABLE)
    else:
      self.aol_fault_reason = 'critical'
      self.aol_authority_lost = True
      self.events.add(EventName.controlsMismatch)
      if ET.IMMEDIATE_DISABLE not in self.state_machine.current_alert_types:
        self.state_machine.current_alert_types.append(ET.IMMEDIATE_DISABLE)
    return True

  def publish_axis_proof(self, CS, *, defer_events=False, source_context=None):
    now_ns = self.aol_car_state_log_ns if REPLAY and self.aol_car_state_log_ns else time.monotonic_ns()
    source_clock_scoped = bool(not REPLAY and self.aol_replay and self.axis_transport_required)
    if source_clock_scoped:
      self._expire_axis_source(CS, now_ns)
      self._expire_axis_continuity(CS, now_ns)
    if self.aol_axis_decision.lateral_active or self.aol_axis_decision.longitudinal_active:
      self.aol_ever_active = True
    reason = getattr(self, 'aol_fault_reason', 'none')
    event_now_ns = now_ns
    ce_send = None
    if ((self.sm.frame % int(1. / DT_CTRL) == 0) or self.events.names != self.events_prev or
        reason != getattr(self, 'aol_published_fault_reason', 'unknown')):
      ce_send = messaging.new_message('onroadEvents', len(self.events))
      ce_send.logMonoTime = event_now_ns
      ce_send.valid = True
      ce_send.onroadEvents = self.events.to_msg()
    fault_event_ns = int(ce_send.logMonoTime) if ce_send is not None else getattr(self, 'aol_fault_event_ns', 0)
    if self.aol_replay or getattr(self, 'ordinary_axis_ack_required', False):
      if not source_clock_scoped:
        now_ns = self.aol_car_state_log_ns if REPLAY and self.aol_car_state_log_ns else time.monotonic_ns()
      next_sequence = self.aol_sequence + 1
      axis_msg = messaging.new_message('aolAxisState')
      axis_msg.logMonoTime = now_ns
      axis_msg.valid = bool(self.aol_car_state_log_ns > 0 or reason == 'transportPause' or
                            source_clock_scoped and reason == 'availabilityPause')
      axis = axis_msg.aolAxisState
      axis.sessionId = self.aol_session_id
      axis.faultReason = reason
      axis.faultSessionId = self.aol_session_id
      accepted_intent = getattr(self, 'aol_last_intent', None)
      if accepted_intent is None and reason == 'availabilityPause' and not self.aol_car_state_log_ns:
        accepted_intent = getattr(self, 'aol_publication_retained_intent', None)
      accepted_companion = getattr(self, 'aol_intent_companion', None)
      axis.intentProducerSessionId = accepted_intent.producerSessionId if accepted_intent is not None else ''
      axis.intentIntegrityEpoch = accepted_companion.integrity_epoch if accepted_companion is not None else 0
      axis.faultEventMonoTime = fault_event_ns
      axis.sequence = next_sequence
      axis.sourceCarStateMonoTime = self.aol_car_state_log_ns
      axis.observedMonoTime = now_ns
      axis.validUntilMonoTime = now_ns + 30_000_000
      axis.mode = self.aol_axis_decision.mode
      axis.lateralActive = self.aol_axis_decision.lateral_active
      axis.longitudinalActive = self.aol_axis_decision.longitudinal_active
      axis.desiredLateral = self.aol_axis_decision.desired_lateral
      axis.desiredLongitudinal = self.aol_axis_decision.desired_longitudinal
      axis.optionalSetRelease = bool(self.aol_replay and self.aol_last_intent is not None
                                     and self.aol_last_intent.optionalSetRelease)
      axis.nativeAcknowledged = self.aol_axis_decision.native_acknowledged
      axis.qualified = bool((self.aol_replay or getattr(self, 'ordinary_axis_ack_required', False)) and
        (not source_clock_scoped or self.aol_car_state_log_ns > 0 or
         reason in ('transportPause', 'availabilityPause', 'critical')))
      axis_deferred = False
      if source_clock_scoped:
        final_ns = time.monotonic_ns()
        if ((axis.lateralActive or axis.longitudinalActive) and
            (not 0 < self.aol_car_state_log_ns <= final_ns or final_ns - self.aol_car_state_log_ns > INTENT_MAX_AGE_NS) and
            self.source_deadline_deferral_supported()):
          if (source_context is not None and source_context[1] and
              self.defer_expired_axis_publication(CS, source_context[0])):
            # Serialization crossed the same source boundary. Retain only the
            # original proof; staged message metadata grants no new authority.
            self.aol_axis_publication_deferred = axis_deferred = True
          else:
            self._expire_axis_source(CS, time.monotonic_ns())
            return self.publish_axis_proof(CS, defer_events=defer_events, source_context=source_context)
        if not axis_deferred and self._expire_axis_continuity(CS, final_ns):
          return self.publish_axis_proof(CS, defer_events=defer_events, source_context=source_context)
      if not axis_deferred:
        self.aol_sequence = next_sequence
        self.pm.send('aolAxisState', axis_msg)
        # Bind critical history to the actual completed axis publication. The
        # working decision can change again during later source-expiry checks.
        self.aol_published_fault_proof = ((str(axis.sessionId), str(axis.faultReason),
          int(axis.faultEventMonoTime), tuple(self.events.names), bool(axis.desiredLateral),
          bool(axis.desiredLongitudinal), bool(axis.lateralActive), bool(axis.longitudinalActive),
          bool(axis.nativeAcknowledged)) if reason == 'critical' else None)
        self.aol_published_stock_lateral_proof = (
          stock_lateral_fault_proof(self, CS, axis_msg, time.monotonic_ns()) if source_clock_scoped and reason == 'critical' else None)
        self.aol_published_input_proof = None
        self.aol_published_input_native = None
        if (source_clock_scoped and reason == 'none' and axis_msg.valid and axis.qualified and
            axis.nativeAcknowledged and (axis.lateralActive or axis.longitudinalActive) and
            0 < axis.sourceCarStateMonoTime <= now_ns and
            now_ns - axis.sourceCarStateMonoTime <= INTENT_MAX_AGE_NS):
          self.aol_published_input_proof = (str(axis.sessionId), int(axis.sourceCarStateMonoTime),
            now_ns, int(axis.validUntilMonoTime), str(axis.intentProducerSessionId),
            int(axis.intentIntegrityEpoch), bool(axis.desiredLateral), bool(axis.desiredLongitudinal),
            bool(axis.lateralActive), bool(axis.longitudinalActive))
          self.aol_published_input_native = getattr(self, 'aol_decision_native', None)
          if axis.lateralActive or axis.longitudinalActive and self.enabled and self.active:
            previous_anchor = getattr(self, 'aol_ordinary_source_anchor', None)
            identity = previous_anchor[3] if previous_anchor is not None else cp_identity(self.CP)
            self.aol_ordinary_source_anchor = (self.aol_published_input_proof,
              self.aol_published_input_native, accepted_intent, identity, CS)
    if not defer_events:
      if ce_send is not None:
        self.aol_fault_event_ns = int(ce_send.logMonoTime)
        self.pm.send('onroadEvents', ce_send)
      self.aol_published_fault_reason = reason
      self.events_prev = self.events.names.copy()
    return ce_send, reason

  def publish_selfdriveState(self, CS, *, axis_published=False):
    # selfdriveState
    ss_msg = messaging.new_message('selfdriveState')
    ss_msg.valid = True
    ss = ss_msg.selfdriveState
    ss.enabled = self.enabled
    ss.active = self.active
    ss.state = self.state_machine.state
    ss.engageable = not self.events.contains(ET.NO_ENTRY)
    ss.experimentalMode = self.experimental_mode
    ss.personality = self.personality

    ss.alertText1 = self.AM.current_alert.alert_text_1
    ss.alertText2 = self.AM.current_alert.alert_text_2
    ss.alertSize = self.AM.current_alert.alert_size
    ss.alertStatus = self.AM.current_alert.alert_status
    ss.alertType = self.AM.current_alert.alert_type
    ss.alertSound = self.AM.current_alert.audible_alert
    delivery = getattr(self, 'availability_warning_delivery', None)
    if delivery is not None and delivery.pending(session=getattr(self, 'aol_session_id', None),
        drive=int(self.sm['deviceState'].startedMonoTime) if self.sm['deviceState'].started else 0,
        now_ns=time.monotonic_ns(),
        audible_none=ss.alertSound == log.SelfdriveState.AudibleAlert.none,
        priority=self.AM.current_alert.priority, low_priority=AlertPriority.LOW):
      ss.alertSound = log.SelfdriveState.AudibleAlert.prompt
    ss.alertHudVisual = self.AM.current_alert.visual_alert

    ce_send, reason = (None, None) if axis_published else self.publish_axis_proof(CS, defer_events=True)
    self.pm.send('selfdriveState', ss_msg)
    if self.conditional_replay:
      self.conditional_ack_sequence += 1
      result = self.conditional_result
      proposal = self.conditional_consumer.last if result.accepted and self.conditional_consumer is not None else None
      ack = publish_ack(session=self.conditional_ack_session, sequence=self.conditional_ack_sequence,
                        observed_ns=time.monotonic_ns(), selfdrive_state_ns=int(ss_msg.logMonoTime),
                        drive_id=int(self.sm['deviceState'].startedMonoTime), effective_experimental=bool(ss.experimentalMode),
                        result=result, accepted_proposal=proposal)
      historic = ack.starpilotSelfdriveState
      historic.alertText1 = ss.alertText1
      historic.alertText2 = ss.alertText2
      historic.alertStatus = str(ss.alertStatus)
      historic.alertSize = str(ss.alertSize)
      historic.alertType = ss.alertType
      sound = str(ss.alertSound)
      historic.alertSound = sound if sound in ('none', 'engage', 'disengage', 'refuse', 'warningSoft',
                                                'warningImmediate', 'prompt', 'promptRepeat', 'promptDistracted') else 'none'
      historic.vEgo = CS.vEgo
      self.pm.send('starpilotSelfdriveState', ack)


    if not axis_published:
      if ce_send is not None:
        self.aol_fault_event_ns = int(ce_send.logMonoTime)
        self.pm.send('onroadEvents', ce_send)
      self.aol_published_fault_reason = reason
      self.events_prev = self.events.names.copy()

  def update_conditional_mode(self, CS):
    # Conditional document and baseline come from one accepted owner snapshot;
    # only this thread publishes their effective choice.
    self.experimental_mode = self.requested_experimental_mode if not self.conditional_replay else False
    self.conditional_result = ConsumerResult(self.experimental_mode, False, 'unavailable')
    if not self.conditional_replay:
      return
    assert self.conditional_settings is not None and self.conditional_consumer is not None
    snapshot = self.conditional_settings.current
    clocks = paired_clocks_ns() if not REPLAY else None
    if clocks is None:
      self.conditional_consumer.invalidate(time.monotonic_ns())
      self.conditional_status = 'clock_unavailable'
      return
    now_ns, boot_ns, skew_ns = clocks
    drive_id = int(self.sm['deviceState'].startedMonoTime)
    verdict = self.conditional_settings.verdict(snapshot, now_mono_ns=now_ns, drive_id=drive_id)
    configured = (verdict.status == 'ready' and verdict.selection is not None and
                  snapshot is not None and verdict.revision == snapshot.revision)
    baseline = bool(configured and experimental_requested(snapshot.experimental_raw, snapshot.experimental_readable,
                                                         snapshot.safe_mode_raw, True, self.CP))
    self.requested_experimental_mode = baseline
    self.experimental_mode = baseline
    required = ('deviceState', 'modelV2', 'carControl')
    def service_fresh(service):
      return bool(self.sm.seen[service] and self.sm.valid[service] and self.sm.alive[service] and
                  0 < self.sm.logMonoTime[service] <= now_ns and
                  now_ns - self.sm.logMonoTime[service] <= (2_000_000_000 if service == 'deviceState' else 150_000_000))
    fresh = all(service_fresh(service) for service in required)
    axis_active = self.aol_axis_decision.longitudinal_active if self.aol_replay else self.enabled
    authority = bool(configured and fresh and self.initialized and axis_active and
                     not getattr(self, 'aol_axis_publication_deferred', False) and
                     self.CP.openpilotLongitudinalControl and not self.CP.passive and
                     self.conditional_car_state_valid and CS.canValid and not CS.canTimeout and self.sm['deviceState'].started and
                     self.sm['carControl'].longActive and self.sm.seen['slcState'] and self.sm.alive['slcState'])
    message_ns = int(self.sm.logMonoTime['slcState'])
    receipt_ns = int(self.sm.recv_time['slcState'] * 1e9)
    model_ns = int(self.sm.logMonoTime['modelV2'])
    car_state_ns = self.aol_car_state_log_ns
    result = self.conditional_consumer.sample(
      self.sm['slcState'], now_ns=now_ns, now_boot_ns=boot_ns, sample_skew_ns=skew_ns,
      message_ns=message_ns, receipt_ns=receipt_ns,
      drive_id=drive_id, model_ns=model_ns, car_state_ns=car_state_ns,
      authority=authority, stock_experimental=baseline,
      choice=verdict.selection.choice if configured else ModeChoice.STOCK,
      settings_fingerprint=settings_fingerprint(snapshot) if configured else None)
    self.experimental_mode = result.experimental
    self.conditional_status = result.status
    self.conditional_result = result


  def _decide_aol_axes(self, CS, intent, native, *, now_ns=0, ordinary_lag=False):
    from openpilot.starpilot.car.gm.aol import physical_cancel_lateral_allowed, stock_ascm_independent_lateral, cc_independent_lateral_below_engage
    from openpilot.starpilot.car.hyundai.aol import physical_cancel_lateral_allowed as hkg_cancel_lateral_allowed
    from openpilot.starpilot.car.mazda.aol import physical_cancel_lateral_allowed as mazda_cancel_lateral_allowed
    from openpilot.starpilot.car.ford.aol import physical_cancel_lateral_allowed as ford_cancel_lateral_allowed
    from openpilot.starpilot.car.subaru.aol import sng_standstill_lateral_inhibit
    from openpilot.starpilot.aol.dm import lateral_event_context
    from openpilot.starpilot.car.toyota.legacy_lateral import below_engage_lateral_allowed as toyota_below_engage_lateral_allowed
    retained_dm_lateral, lateral_events = lateral_event_context(self, CS, intent, native, now_ns, ordinary_lag)
    reverse_pause = known_reverse_pause(CS, getattr(self, 'car_state_samples', ()))
    if reverse_pause:
      lateral_events = [event for event in lateral_events if event not in (EventName.wrongGear, EventName.reverseGear)]
    return decide_axes(
      standard_lateral=self.active, standard_longitudinal=bool(self.enabled and self.CP.openpilotLongitudinalControl and
        not any(sample.seatbeltUnlatched or sample.doorOpen for sample, _ in getattr(self, 'car_state_samples', ()))),
      intent=intent, native=native, car_state=CS, initialized=self.initialized,
      model_ready=bool(self.sm.all_checks(['modelV2', 'extrinsicsCalibration']) and
                       self.sm['extrinsicsCalibration'].calStatus == log.ExtrinsicsCalibration.Status.calibrated),
      no_entry=aol_no_entry(lateral_events, CS, paddle_only_cancel=self.nostalgia_paddle_cancel,
                           allow_lateral_cancel=bool(intent is not None and intent.allowedLatch and intent.optionalSetRelease
                                                     and ev6_aol_qualified(self.CP, marked_only=True)) or
                             physical_cancel_lateral_allowed(self.CP, CS, intent, native,
                               source_ns=getattr(self, 'aol_car_state_log_ns', 0), now_ns=now_ns) or
                             hkg_cancel_lateral_allowed(self.CP, CS, intent, native,
                               settings=self.aol_settings, previous=getattr(self, 'aol_axis_decision', None),
                               source_ns=getattr(self, 'aol_car_state_log_ns', 0), now_ns=now_ns,
                               axis_session_id=getattr(self, 'aol_session_id', '')) or
                             ford_cancel_lateral_allowed(self.CP, CS, intent, native,
                               settings=self.aol_settings, previous=getattr(self, 'aol_axis_decision', None),
                               source_ns=getattr(self, 'aol_car_state_log_ns', 0), now_ns=now_ns,
                               axis_session_id=getattr(self, 'aol_session_id', '')) or
                             mazda_cancel_lateral_allowed(self.CP, CS, intent, native,
                               settings=self.aol_settings, previous=getattr(self, 'aol_axis_decision', None),
                               source_ns=getattr(self, 'aol_car_state_log_ns', 0), now_ns=now_ns,
                               axis_session_id=getattr(self, 'aol_session_id', '')),
                           cruise_main_required=getattr(self, 'aol_cruise_main_required', True),
                           allow_below_engage_speed=bool((is_volt_ascm_longitudinal(self.CP) or
                                                        stock_ascm_independent_lateral(self.CP) or
                                                        cc_independent_lateral_below_engage(self.CP, intent,
                                                          enabled=self.enabled, active=self.active)) and
                                                       not self.CP.flags & GMFlags.PEDAL_LONG) or
                             toyota_below_engage_lateral_allowed(self.CP, CS, intent, native,
                               source_ns=getattr(self, 'aol_car_state_log_ns', 0), now_ns=now_ns,
                               axis_session_id=getattr(self, 'aol_session_id', ''))),
      immediate_disable=self.events.contains(ET.IMMEDIATE_DISABLE),
      dm_lockout=bool(not self.sm.all_checks(['driverMonitoringState']) or
                      not retained_dm_lateral and (self.sm['driverMonitoringState'].lockout or
                        self.sm['driverMonitoringState'].alwaysOnLockout or
                        self.sm['driverMonitoringState'].alertLevel == AlertLevel.three)),
      pause_brake_mps=self.aol_settings.pause_brake_mps if self.aol_settings is not None else 0.0,
      gear_pause=reverse_pause,
      lateral_inhibit=self.aol_dm_lateral_inhibit or sng_standstill_lateral_inhibit(self.CP, CS) or any(
        sample.steerFaultTemporary or sample.brakePressed and not sample.standstill and
        sample.vEgo < (self.aol_settings.pause_brake_mps if self.aol_settings is not None else 0.0)
        for sample, _ in getattr(self, 'car_state_samples', ())))

  def cold_gm_park_readiness(self, CS, intent, now_ns):
    from openpilot.starpilot.car.gm.aol import cold_park_readiness
    from openpilot.starpilot.aol.transport_pause import _healthy_transport_native
    from openpilot.starpilot.aol.runtime import INTENT_MAX_AGE_NS
    if (self.CP.brand != 'gm' or not self.initialized or self.enabled or self.active or getattr(self, 'aol_ever_active', False) or
        self.aol_authority_lost or not getattr(self, 'conditional_car_state_valid', False) or
        intent is None or not intent.settingsQualified or not intent.lateralArmed or intent.allowedLatch or
        intent.pauseLateral or intent.pauseLongitudinal or
        intent.carStateLogMonoTime != self.aol_car_state_log_ns or
        not 0 < intent.carStateLogMonoTime <= intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime or
        now_ns - intent.carStateLogMonoTime > INTENT_MAX_AGE_NS):
      return False
    native = _healthy_transport_native(self.sm, self.CP, CS, self.aol_session_id, now_ns)
    return cold_park_readiness(self.CP, CS, native, self.events.to_msg())

  def cold_gm_main_readiness(self, CS, intent, now_ns):
    from openpilot.starpilot.car.gm.aol import cold_main_readiness
    from openpilot.starpilot.aol.transport_pause import _healthy_transport_native
    if (self.CP.brand != 'gm' or self.enabled or self.active or getattr(self, 'aol_ever_active', False) or self.aol_authority_lost or
        not self.events.contains(ET.SOFT_DISABLE) or self.events.contains(ET.IMMEDIATE_DISABLE) or
        intent is None or not intent.settingsQualified or not intent.allowedLatch or not intent.lateralArmed or
        not 0 < intent.carStateLogMonoTime <= intent.observedMonoTime <= now_ns <= intent.validUntilMonoTime or
        now_ns - intent.carStateLogMonoTime > SAFETY_MAX_AGE_NS):
      return False
    native = _healthy_transport_native(self.sm, self.CP, CS, self.aol_session_id, now_ns)
    return cold_main_readiness(self.CP, CS, native, self.events.to_msg())

  def ordinary_native_long_withdrawal(self, CS, native, previous, requested, intent, *, source_current, now_ns):
    """Keep the existing ordinary Panda mismatch owner for a LONG-only drop.

    This classifies no new native permission. The unchanged per-axis decision
    withdraws LONG immediately; genuine hardware faults and the ordinary
    mismatch counter retain their original fault ownership.
    """
    from openpilot.starpilot.aol.transport_pause import independent_lateral_health
    from opendbc.car.ford.values import CAR as FORD_CAR
    from opendbc.car.ford.aol import profile_word
    from openpilot.starpilot.car.ford.aol import native_accepts_cp as ford_native_accepts_cp
    # Established ordinary Mach-E cruise does not require an independent AOL latch.
    # Its native LONG pedal withdrawal may precede Card; the original combined
    # publication, live LAT grant and every health/deadline check below still apply.
    ford_word = self.CP.safetyConfigs[-1].safetyParam
    ordinary_ford = bool(self.CP.carFingerprint == FORD_CAR.FORD_MUSTANG_MACH_E_MK1 and profile_word(ford_word) == 3 and
                         ford_native_accepts_cp(self.CP, int(car.CarParams.SafetyModel.ford), ford_word))
    proof = getattr(self, 'aol_published_input_proof', None)
    prior = getattr(self, 'aol_published_input_native', None)
    anchor = getattr(self, 'aol_ordinary_source_anchor', None)
    companion = getattr(self, 'aol_intent_companion', None)
    if (not self.enabled or not self.active or not self.CP.openpilotLongitudinalControl or
        not previous.lateral_active or not previous.longitudinal_active or
        not requested.desired_lateral or not requested.desired_longitudinal or not source_current or
        not self.conditional_car_state_valid or not 0 < self.aol_car_state_log_ns <= now_ns or
        now_ns - self.aol_car_state_log_ns > INTENT_MAX_AGE_NS or
        native is None or prior is None or proof is None or anchor is None or
        anchor[3] != cp_identity(self.CP) or proof[0] != self.aol_session_id or
        not proof[8] or not proof[9] or not 0 < proof[2] <= now_ns <= proof[3] or
        intent is None or companion is None or proof[4] != intent.producerSessionId or
        proof[5] != companion.integrity_epoch or not ((intent.allowedLatch and intent.lateralArmed) or ordinary_ford) or
        not native.requestedLateral or not native.lateralAllowed or
        not native.requestedLongitudinal or native.longitudinalAllowed or not native.pandaInventory or
        CS.brakePressed or CS.regenBraking or CS.gasPressed or CS.steeringPressed and not ordinary_ford or
        any(self.events.contains(kind) for kind in (ET.IMMEDIATE_DISABLE, ET.SOFT_DISABLE, ET.USER_DISABLE,
                                                  ET.OVERRIDE_LONGITUDINAL)) or
        not ordinary_ford and self.events.contains(ET.OVERRIDE_LATERAL)):
      return False
    def identity(state):
      return (state.axisSessionId, state.pandaSerial, state.safetyModel, state.safetyParam,
              tuple((slot.slotIndex, slot.hardwareSerial, slot.safetyModel, slot.safetyParam,
                     slot.alternativeExperience) for slot in state.pandaInventory))
    if (identity(prior) != identity(native) or
        not 0 < prior.observedMonoTime <= native.observedMonoTime <= now_ns <= prior.validUntilMonoTime or
        independent_lateral_health(self.sm, self.CP, CS, self.aol_session_id, now_ns) != native):
      return False
    owners = [index for index, config in enumerate(self.CP.safetyConfigs)
              if str(config.safetyModel) not in ('silent', 'noOutput')]
    if len(owners) != 1:
      return False
    from openpilot.starpilot.car.gm.aol import native_accepts_cp as gm_native_accepts_cp
    from openpilot.starpilot.car.toyota.aol import qualified as toyota_aol_qualified
    from openpilot.starpilot.car.hyundai.aol import early_native_long_withdrawal_supported
    # Pandad reads health before its final native permission read. A GM driver
    # withdrawal between those reads legitimately leaves the earlier health
    # controlsAllowed true. For an admitted OP-long GM profile, the current
    # LAT-only native mask itself proves controls_allowed has gone false.
    # Keep LONG withdrawn and leave disagreement to the ordinary mismatch owner.
    # Toyota's native LONG mask also checks gas, brake and belt independently
    # of controls_allowed. Health and that mask may precede the parsed pedal
    # edge: honor LONG withdrawal while the existing native LAT grant stands.
    # Ioniq6 also checks the physical brake before its separately published host sample.
    # Retain only its already granted LAT; all proof, health and LONG withdrawal gates remain.
    # The exact vehicle policies and all proof/health checks above remain required.
    return bool(ordinary_ford or gm_native_accepts_cp(self.CP, native.safetyModel, native.safetyParam) or
                early_native_long_withdrawal_supported(self.CP) or
                toyota_aol_qualified(self.CP, marked_only=True) or
                not native.pandaInventory[owners[0]].controlsAllowed and
                not self.sm['pandaStates'][owners[0]].controlsAllowed)

  def honda_native_lateral_grant_lost(self, CS, native, previous, requested, *, source_current, previous_intent, intent, now_ns):
    """An established host LAT request lost its native grant, not a cold wait."""
    pause = getattr(self, 'aol_transport_pause', None)
    prior = getattr(pause, 'observed_native', None)
    if (not honda_aol_qualified(self.CP, marked_only=True) or not source_current or
        not self.initialized or not previous.lateral_active or not requested.desired_lateral or
        native is None or previous_intent is None or intent is None or not previous_intent.producerSessionId or
        previous_intent.producerSessionId != intent.producerSessionId or
        not 0 < previous_intent.carStateLogMonoTime <= native.observedMonoTime <= now_ns or
        now_ns - previous_intent.carStateLogMonoTime > INTENT_MAX_AGE_NS or
        native.requestedLateral or native.lateralAllowed or
        prior is None or not prior.requestedLateral or not prior.lateralAllowed or
        getattr(self, 'aol_fault_reason', 'none') != 'none' or
        pause.active or getattr(getattr(self, 'aol_availability_pause', None), 'active', False) or
        CS.brakePressed or CS.regenBraking or CS.gasPressed or CS.steeringPressed or
        honda_temporary_restriction(self.CP, CS) or
        any(self.events.contains(event) for event in
            (ET.USER_DISABLE, ET.OVERRIDE_LATERAL, ET.OVERRIDE_LONGITUDINAL, ET.IMMEDIATE_DISABLE, ET.SOFT_DISABLE)) or
        not self.sm.all_checks(['modelV2', 'extrinsicsCalibration']) or
        self.sm['extrinsicsCalibration'].calStatus != log.ExtrinsicsCalibration.Status.calibrated):
      return False
    def identity(state):
      return (state.axisSessionId, state.pandaSerial, state.safetyModel, state.safetyParam,
              tuple((p.slotIndex, p.hardwareSerial, p.safetyModel, p.safetyParam,
                     p.alternativeExperience) for p in state.pandaInventory))
    return bool(pause.cp == cp_identity(self.CP) and pause.session == self.aol_session_id and
                identity(prior) == identity(native) and
                0 < prior.observedMonoTime <= native.observedMonoTime <= now_ns <= prior.validUntilMonoTime and
                now_ns - prior.observedMonoTime <= SAFETY_MAX_AGE_NS and
                current_native(self.sm, self.CP, now_ns=now_ns, axis_session_id=self.aol_session_id) == native and
                healthy_transport(self.sm, self.CP, CS, self.aol_session_id, now_ns, native))


  def step(self):
    self.aol_lag_takeover_alert = False
    self.aol_soft_takeover_alert = False
    initial_state = (self.state_machine.state, self.state_machine.soft_disable_timer, self.enabled, self.active)
    previous_deferred = getattr(self, 'aol_axis_publication_deferred', False)
    self.aol_axis_publication_deferred = False
    self.aol_step_deferral_checked = False
    self.aol_step_intent_drain_count = 0
    self.aol_step_intent_drain_ok = True
    self.aol_event_intent_drained = None
    self.aol_event_input_reconcile = False
    self.aol_event_input_wait_attempted = False
    previous_fault_reason = getattr(self, 'aol_fault_reason', 'none')
    self.aol_ordinary_source_pause = False
    self.aol_step_input_proof = getattr(self, 'aol_published_input_proof', None)
    CS = self.data_sample()
    self.car_event_sample_count = 0
    self.aol_reconciled_sources = ()
    self.event_car_state = CS
    self.event_input_refresh_pending = True
    try:
      self.update_events(CS)
    finally:
      self.event_input_refresh_pending = False
    CS = self.event_car_state
    if max(self.car_event_sample_count, len(getattr(self, 'car_state_samples', ()))) > 1 and any(self.events.contains(kind) for kind in (
        ET.USER_DISABLE, ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE)):
      # A queued enable cannot override a cancellation or pedal edge in this batch.
      self.events.events = [event for event in self.events.names if ET.ENABLE not in self.events.event_rules.get(event, {})]
    native = None
    lost_active_aol = bool(self.aol_replay and getattr(self, 'steering_authority_unavailable', False) and
                           (self.aol_axis_decision.lateral_active or self.aol_axis_decision.longitudinal_active))
    intent_companion = getattr(self, 'aol_intent_companion', None)
    intent_drained = (self.aol_event_intent_drained and self.aol_step_intent_drain_ok if self.aol_event_intent_drained is not None else
                      not self.aol_replay or intent_companion is None or self.drain_axis_intent(intent_companion))
    if self.aol_replay or getattr(self, 'ordinary_axis_ack_required', False):
      now_ns = self.aol_car_state_log_ns if REPLAY and self.aol_car_state_log_ns else time.monotonic_ns()
      # Intent draining can cross the source cutoff. Check the original proof
      # against the same post-drain clock used by admission, before authority
      # state changes. This retains no new input, proof, deadline, or enable.
      if not previous_deferred and self.defer_expired_axis_publication(CS, initial_state, now_ns=now_ns):
        self.aol_axis_publication_deferred = True
        self.update_alerts(CS)
        self.update_conditional_mode(CS)
        self.publish_selfdriveState(CS, axis_published=True)
        self.CS_prev = CS
        del self.aol_step_input_proof
        return
      native = current_native(self.sm, self.CP, now_ns=now_ns, axis_session_id=self.aol_session_id)
      if native is None and self.initialized:
        lost_active_aol = self.aol_axis_decision.lateral_active or self.aol_axis_decision.longitudinal_active
        self.events.add(EventName.controlsMismatch)
    self.aol_decision_native = native
    if self.aol_replay:
      previous_intent = getattr(self, 'aol_last_intent', None)
      reconciled_pairs = dict(intent_companion.samples) if intent_companion is not None and self.aol_reconciled_sources else {}
      reconciled_epoch = intent_companion.integrity_epoch if intent_companion is not None else -1
      source_pair = intent_companion.samples.get(self.aol_car_state_log_ns) if intent_companion is not None else None
      verified = intent_companion.verified_sm_projection if intent_companion is not None else None
      if (intent_companion is not None and source_pair is None and verified is not None and
          verified[0] == self.aol_car_state_log_ns and
          verified[2] == intent_companion.integrity_epoch and verified[3] == intent_companion.session):
        source_pair = (verified[0], True, verified[1])
      self.aol_selected_pair_future = bool(source_pair is not None and source_pair[2] is not None and
        source_pair[2].observedMonoTime > now_ns)
      known_intent = previous_intent or getattr(self, 'aol_publication_retained_intent', None)
      if self.aol_car_state_log_ns > 0:
        self.aol_expired_healthy_source = bool(not REPLAY and self.conditional_car_state_valid and
          CS.canValid and not CS.canTimeout and now_ns - self.aol_car_state_log_ns > INTENT_MAX_AGE_NS and
          source_pair is not None and source_pair[1] and source_pair[2] is not None and
          source_pair[2].carStateLogMonoTime == self.aol_car_state_log_ns and source_pair[2].observedMonoTime <= now_ns and
          source_pair[2].settingsQualified and source_pair[2].allowedLatch and source_pair[2].lateralArmed and
          known_intent is not None and source_pair[2].producerSessionId == known_intent.producerSessionId)
      intent = current_intent(self.sm, car_state_ns=self.aol_car_state_log_ns, now_ns=now_ns,
                              previous=getattr(self, 'aol_last_intent', None),
                              companion=intent_companion, drain=False) if intent_drained else None
      intent = self.reconciled_intent(intent, previous_intent, reconciled_pairs, reconciled_epoch, now_ns)
      self.aol_last_intent = intent
      from openpilot.starpilot.aol.dm import dm_clear, observe_owner
      observe_owner(self, intent, previous_intent)
      previous = self.aol_axis_decision
      requested = self._decide_aol_axes(CS, intent, native, now_ns=now_ns)
      source_current = bool(intent is not None and getattr(self, 'conditional_car_state_valid', False) and
                            CS.canValid and not CS.canTimeout)
      driver_override = bool(CS.brakePressed or CS.regenBraking or CS.gasPressed or
                             self.events.contains(ET.USER_DISABLE))
      lost_companion = bool((previous.lateral_active or previous.longitudinal_active) and
                            not source_current and not self.events.contains(ET.USER_DISABLE))
      lost_honda_lateral_grant = self.honda_native_lateral_grant_lost(
        CS, native, previous, requested, source_current=source_current,
        previous_intent=previous_intent, intent=intent, now_ns=now_ns)
      ordinary_long_withdrawal = bool(native is not None and not native.longitudinalAllowed and
        self.ordinary_native_long_withdrawal(
          CS, native, previous, requested, intent, source_current=source_current, now_ns=now_ns))
      from openpilot.starpilot.car.hyundai.aol import expected_stopped_lateral_denial
      stopped_lateral_withdrawal = bool(source_current and requested.desired_lateral and
        expected_stopped_lateral_denial(self.CP, CS, native, sm=self.sm,
          axis_session_id=self.aol_session_id, now_ns=now_ns))
      lost_permission = bool(lost_honda_lateral_grant or native is not None and not driver_override and (
        previous.lateral_active and requested.desired_lateral and native.requestedLateral and
        not native.lateralAllowed and not stopped_lateral_withdrawal and
        not CS.steeringPressed and not self.events.contains(ET.OVERRIDE_LATERAL) or
        previous.longitudinal_active and requested.desired_longitudinal and native.requestedLongitudinal and
        not native.longitudinalAllowed and not ordinary_long_withdrawal and
        not self.events.contains(ET.OVERRIDE_LONGITUDINAL)))
      if not hasattr(self, 'aol_transport_pause'):
        self.aol_transport_pause = TransportPause(self.CP, self.aol_session_id)
      pause, upgraded = self.aol_transport_pause.observe(
        cp=self.CP, session=self.aol_session_id, sm=self.sm, cs=CS, native=native, now_ns=now_ns,
        companion=getattr(self, 'aol_intent_companion', None), previous_intent=previous_intent, intent=intent,
        expired_healthy_source=getattr(self, 'aol_expired_healthy_source', False),
        previously_active=previous.lateral_active, hard_fault=bool(
          self.aol_selected_pair_future or self.aol_authority_lost or lost_active_aol or lost_permission or self.events.contains(ET.IMMEDIATE_DISABLE) or
          self.events.contains(ET.SOFT_DISABLE)),
        model_ready=bool(self.sm.all_checks(['modelV2', 'extrinsicsCalibration']) and
          self.sm['extrinsicsCalibration'].calStatus == log.ExtrinsicsCalibration.Status.calibrated),
        user_disable=self.events.contains(ET.USER_DISABLE))
      if source_current:
        self.observe_ordinary_source_metadata(CS, intent, self.aol_transport_pause.observed_native, now_ns)
      if not hasattr(self, 'aol_availability_pause'):
        self.aol_availability_pause = AvailabilityPause(self.CP, self.aol_session_id)
      retained_intent = getattr(self, 'aol_publication_retained_intent', None)
      if retained_intent is None and getattr(self, 'aol_expired_healthy_source', False):
        retained_intent = previous_intent
      healthy_native = self.aol_transport_pause.observed_native
      publication_expiry = bool(not self.aol_selected_pair_future and healthy_native is not None and
        healthy_native.pandaInventory and retained_intent is not None and not source_current and
        0 < retained_intent.carStateLogMonoTime <= now_ns and
        now_ns - retained_intent.carStateLogMonoTime <= SAFETY_MAX_AGE_NS)
      availability_events = self.events.to_msg()
      if publication_expiry and EventName.commIssue not in self.events.names:
        source_expiry_events = Events()
        source_expiry_events.add(EventName.commIssue)
        availability_events += source_expiry_events.to_msg()
      availability = self.aol_availability_pause.observe(
        cp=self.CP, session=self.aol_session_id, sm=self.sm, cs=CS, native=native, now_ns=now_ns,
        companion=intent_companion, intent=intent if intent is not None else retained_intent, events=availability_events,
        model_ready=bool(self.sm.all_checks(['modelV2', 'extrinsicsCalibration']) and
          self.sm['extrinsicsCalibration'].calStatus == log.ExtrinsicsCalibration.Status.calibrated),
        source_current=source_current, source_expired=publication_expiry, prior_lateral_active=previous.lateral_active,
        hard_fault=bool(self.aol_selected_pair_future or self.aol_authority_lost or lost_active_aol or
          lost_companion and not publication_expiry or lost_permission or upgraded),
        healthy_native=self.aol_transport_pause.observed_native)
      ordinary_availability = bool(not source_current and not lost_active_aol and not lost_permission and not upgraded and
        self.ordinary_source_availability(CS, now_ns, native))
      if ordinary_availability:
        self.aol_ordinary_source_pause = True
        self.aol_publication_retained_intent = self.aol_ordinary_source_anchor[2]
        self.events.add(EventName.commIssue)
        lost_companion = False
        self.aol_transport_pause.active = pause = False
        # Preserve the normal state machine and SOFT_DISABLE countdown. The
        # independent armed-LAT retention must not disable ordinary engagement.
        availability = False
      if publication_expiry and (availability or not pause) and EventName.commIssue not in self.events.names:
        self.events.add(EventName.commIssue)
      if availability:
        if publication_expiry:
          self.aol_publication_retained_intent = retained_intent
        lost_companion = False
        self.aol_transport_pause.active = pause = False
        if previous_fault_reason != 'availabilityPause':
          self.aol_availability_ordinary_warning = self.enabled
          self.aol_availability_chime_until_ns = time.monotonic_ns() + 400_000_000
        self.aol_availability_long_inhibit = True
      if pause and source_current and requested.lateral_active:
        # Ordinary controls were disabled at the first pause. Fresh normal AOL
        # gates and exact native acknowledgment recover only its lateral axis.
        self.aol_transport_pause.active = pause = False
      if pause:
        lost_companion = False
      if self.initialized and (upgraded or lost_active_aol or lost_companion or lost_permission):
        lost_active_aol = True
        self.aol_authority_lost = True
      elif source_current and not intent.allowedLatch:
        self.aol_authority_lost = False
      # Temporary EPS withdraws steering commands without revoking the armed
      # latch. Keep the event and ordinary soft-disable behavior unchanged.
      from openpilot.starpilot.aol.seatbelt import seatbelt_lateral_soft_event_allowed
      reverse_pause = known_reverse_pause(CS, getattr(self, 'car_state_samples', ()))
      aol_hard_soft_fault = any(event.softDisable and not (
        event.name == EventName.doorOpen or
        event.name == EventName.wrongGear and reverse_pause or
        event.name == EventName.steerTempUnavailable and CS.steerFaultTemporary and not CS.steerFaultPermanent or
        seatbelt_lateral_soft_event_allowed(self.CP, CS, intent, native, event,
          source_ns=self.aol_car_state_log_ns, now_ns=now_ns, axis_session_id=self.aol_session_id,
          pending_lateral_request=not previous.lateral_active and not self.aol_authority_lost,
          gear_pause=reverse_pause))
        for event in self.events.to_msg())
      cold_main_wait = self.cold_gm_main_readiness(CS, intent, now_ns)
      cold_park_wait = self.cold_gm_park_readiness(CS, intent, now_ns)
      self.aol_fault_reason = ('none' if cold_park_wait else
                               'availabilityPause' if ordinary_availability or cold_main_wait else
                               'critical' if self.aol_authority_lost or self.events.contains(ET.IMMEDIATE_DISABLE) else
                               'availabilityPause' if availability or aol_hard_soft_fault else 'transportPause' if pause else 'none')
      if self.aol_authority_lost or pause:
        self.events.add(EventName.controlsMismatch)
        lost_active_aol |= pause
    was_enabled = self.enabled
    if self.aol_replay and source_current and CS.doorOpen and was_enabled:
      # Ordinary cruise is withdrawn; its legacy permission is no longer expected.
      self.aol_availability_long_inhibit = True
    if not self.CP.passive and self.initialized:
      self.enabled, self.active = self.state_machine.update(self.events)
    if not was_enabled and self.enabled and self.events.contains(ET.ENABLE):
      self.aol_step_input_proof = None
    if lost_active_aol and ET.IMMEDIATE_DISABLE not in self.state_machine.current_alert_types:
      self.state_machine.current_alert_types.append(ET.IMMEDIATE_DISABLE)
    if self.aol_replay:
      if (not availability and source_current and self.enabled and self.events.contains(ET.ENABLE) and
          dm_clear(self) and
          not self.events.contains(ET.NO_ENTRY) and not self.events.contains(ET.IMMEDIATE_DISABLE) and
          not self.events.contains(ET.SOFT_DISABLE)):
        self.aol_availability_long_inhibit = False
      if getattr(self, 'aol_availability_long_inhibit', False):
        # Keep ordinary state truthful until a fresh accepted ENABLE. AOL's
        # separately acknowledged lateral recovery does not reengage LONG.
        self.state_machine.state = log.SelfdriveState.OpenpilotState.disabled
        self.enabled = self.active = False
        if ET.ENABLE in self.state_machine.current_alert_types:
          self.state_machine.current_alert_types.remove(ET.ENABLE)
        if (availability and getattr(self, 'aol_availability_ordinary_warning', False) and
            self.state_machine.soft_disable_timer > 0 and
            ET.SOFT_DISABLE not in self.state_machine.current_alert_types):
          self.state_machine.current_alert_types.append(ET.SOFT_DISABLE)
      ordinary_lag = bool(not availability and not pause and not self.aol_ordinary_source_pause and
        fresh_ordinary_lag_continuation(self, CS, intent, native, now_ns))
      self.aol_axis_decision = (self._decide_aol_axes(CS, intent, native, now_ns=now_ns, ordinary_lag=True)
        if ordinary_lag else self._decide_aol_axes(CS, intent, native, now_ns=now_ns))
      if ordinary_lag:
        # Preserve the exact current SOFT event and normal countdown. A live
        # standard-axis proof describes healthy current authority, not critical.
        self.aol_fault_reason = 'none'
      if cold_main_wait:
        self.aol_axis_decision = AxisDecision()
      if cold_park_wait:
        self.aol_axis_decision = AxisDecision(native_acknowledged=True)
      if self.aol_ordinary_source_pause:
        self.aol_axis_decision = self.ordinary_source_withdrawal()
      elif availability:
        self.aol_axis_decision = AxisDecision(desired_lateral=True, native_acknowledged=True)
      elif getattr(self, 'aol_availability_long_inhibit', False):
        decision = self.aol_axis_decision
        self.aol_axis_decision = AxisDecision(desired_lateral=decision.desired_lateral,
          lateral_active=decision.lateral_active, native_acknowledged=decision.native_acknowledged)
      if pause:
        # Negotiate the fresh lateral request while output and ordinary LONG
        # remain off; requiring an active ack before requesting would deadlock.
        self.aol_axis_decision = AxisDecision(desired_lateral=bool(source_current and requested.desired_lateral))
    elif getattr(self, 'ordinary_axis_ack_required', False):
      self.aol_axis_decision = decide_ordinary_axis(
        requested=bool(self.initialized and self.conditional_car_state_valid and
                       CS.canValid and not CS.canTimeout and
                       ordinary_lateral_requested(self.active, CS, self.CP) and
                       ordinary_axis_request_allowed(self.CP, CS)), native=native)
    if (self.aol_replay and source_current and not was_enabled and not self.enabled and
        previous.lateral_active and previous.native_acknowledged and not self.aol_axis_decision.lateral_active and
        self.aol_fault_reason == 'critical' and not self.aol_authority_lost and
        intent is not None and intent.allowedLatch and EventName.selfdrivedLagging in self.events.names and
        not self.events.contains(ET.IMMEDIATE_DISABLE) and
        not any(event.softDisable and event.name != EventName.selfdrivedLagging for event in self.events.to_msg()) and
        self._decide_aol_axes(CS, intent, native, now_ns=now_ns, ordinary_lag=True).lateral_active):
      # Independent AOL has no ordinary enabled state to select a SOFT alert.
      # Removing only lag proves the cause of this acknowledged steering loss;
      # this notification-only result never replaces the withdrawn axis decision.
      self.aol_lag_takeover_alert = True
    if (self.aol_replay and source_current and not was_enabled and not self.enabled and
        previous.lateral_active and previous.native_acknowledged and not self.aol_axis_decision.lateral_active and
        self.aol_fault_reason in ('critical', 'availabilityPause') and not self.aol_authority_lost and
        intent is not None and intent.allowedLatch and
        any(event.softDisable and event.name != EventName.doorOpen for event in self.events.to_msg()) and
        not self.events.contains(ET.IMMEDIATE_DISABLE)):
      # Door-open does not inhibit independent steering; ordinary LONG keeps its alert.
      self.aol_soft_takeover_alert = True
    if (self.aol_replay and intent is not None and intent.lateralArmed and
        CS.steerFaultTemporary and not CS.steerFaultPermanent and not CS.steeringPressed):
      # An armed AOL session still needs the ordinary temporary-steering warning
      # while actual steering is suspended and the stock state machine is off.
      # Like CarSpecificEvents, skip it while the driver is steering: a hard
      # override alone puts Honda EPS in NO_TORQUE_ALERT_1.
      if EventName.steerTempUnavailableSilent not in self.events.names:
        self.events.add(EventName.steerTempUnavailableSilent)
      if ET.WARNING not in self.state_machine.current_alert_types:
        self.state_machine.current_alert_types.append(ET.WARNING)
    if (getattr(self, 'aol_fault_reason', 'none') == 'availabilityPause' and
        (self.enabled or intent is not None and intent.allowedLatch) and
        ET.WARNING not in self.state_machine.current_alert_types):
      self.state_machine.current_alert_types.append(ET.WARNING)
    axis_published = bool(not REPLAY and self.aol_replay and self.axis_transport_required)
    if axis_published:
      self.aol_axis_publication_deferred = bool(not previous_deferred and self.defer_expired_axis_publication(CS, initial_state))
      if not self.aol_axis_publication_deferred:
        self.publish_axis_proof(CS, source_context=(initial_state, not previous_deferred))
    self.update_alerts(CS)
    self.update_conditional_mode(CS)

    self.publish_selfdriveState(CS, axis_published=axis_published)

    self.CS_prev = CS
    # Preserve an explicit None during a new engagement; retire it after publication.
    del self.aol_step_input_proof

  def refresh_saved_driving_mode(self, now_ns: int):
    if self.conditional_replay:
      assert self.conditional_settings is not None
      self.conditional_settings.refresh(now_ns, force=True, wait=True)
    else:
      _, (baseline, readable), (safe, safe_readable) = read_mode_sources(self.params, wait=True)
      self.requested_experimental_mode = experimental_requested(baseline, readable, safe, safe_readable, self.CP)

  def params_thread(self, evt):
    while not evt.is_set():
      self.is_metric = self.params.get_bool("IsMetric")
      if self.aol_replay:
        self.aol_settings = read_settings(self.params)
      self.is_ldw_enabled = self.params.get_bool("IsLdwEnabled")
      self.disengage_on_accelerator = self.params.get_bool("DisengageOnAccelerator")
      self.radar_recovery_enabled = self.params.get_bool("RadarRecoveryAlert")
      self.blindspot_alert_settings = (self.params.get_bool("LoudBlindspotAlert"),
                                      self.params.get_bool("LoudBlindspotAlertWhenDisengaged"))
      self.nostalgia_enabled = nostalgia_saved_enabled(self.params)
      self.refresh_saved_driving_mode(time.monotonic_ns())
      self.personality = self.params.get("LongitudinalPersonality", return_default=True)
      time.sleep(0.1)

  def run(self):
    e = threading.Event()
    t = threading.Thread(target=self.params_thread, args=(e, ))
    try:
      t.start()
      while True:
        self.step()
        self.rk.monitor_time()
    finally:
      e.set()
      t.join()


def main():
  config_realtime_process(5, Priority.CTRL_HIGH)
  s = SelfdriveD()
  s.run()

if __name__ == "__main__":
  main()
