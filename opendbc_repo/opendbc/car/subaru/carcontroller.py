import math
import numpy as np
from opendbc.can import CANPacker
from opendbc.car import Bus, DT_CTRL, make_tester_present_msg, structs
from opendbc.car.lateral import apply_driver_steer_torque_limits, apply_std_steer_angle_limits, apply_steer_angle_limits_vm, common_fault_avoidance
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.subaru import subarucan
from opendbc.car.subaru.avh import AvhStartup
from opendbc.car.subaru.values import CAR, DBC, GLOBAL_ES_ADDR, SUBARU_STOP_START_CARS, CanBus, CarControllerParams, SubaruFlags
from opendbc.car.vehicle_model import VehicleModel

# FIXME: These limits aren't exact. The real limit is more than likely over a larger time period and
# involves the total steering angle change rather than rate, but these limits work well for now
MAX_STEER_RATE = 25  # deg/s
MAX_STEER_RATE_FRAMES = 7  # tx control frames needed before torque can be cut

_SNG_ACC_MIN_DIST = 3
_SNG_ACC_MAX_DIST = 4.5
_LEGACY_2025_MADS_MIN_SPEED = 0.44704
_LEGACY_2025_MADS_MAX_STEER_ANGLE = 120.0
_ANGLE_OVERRIDE_CONFIRM_FRAMES = 2
_ANGLE_REENGAGE_MAX_STEER_RATE = 2.0
_ANGLE_MADS_MIN_SPEED = 0.44704
_ANGLE_MADS_MAX_STEER_ANGLE = 120.0
_ASCENT_AOL_ARM_FRAMES = 30
_ASCENT_ACCEL_FEEDBACK_MIN_SPEED = 70.0 / 3.6
_ASCENT_ACCEL_FEEDBACK_FULL_SPEED = 80.0 / 3.6
_ASCENT_ACCEL_FEEDBACK_TIME_CONSTANT = 0.75
_ASCENT_ACCEL_FEEDBACK_GAIN = 1.5
_ASCENT_ACCEL_FEEDBACK_MAX_CORRECTION = 0.65
_STOP_START_STARTUP_DELAY_FRAMES = 100
# StarPilot's first populated toggle message can arrive several seconds after
# the car controller starts while fingerprinting and settings settle.
_STOP_START_STARTUP_DEADLINE_FRAMES = 1000
_STOP_START_PULSE_FRAMES = 30
_STOP_START_PULSE_PERIOD_FRAMES = 5
_REDNECK_BUTTON_INTERVAL_FRAMES = 10
_REDNECK_BUTTON_COPIES = 2


def _throttle_max_for_car(car_fingerprint):
  if car_fingerprint == CAR.SUBARU_ASCENT:
    return CarControllerParams.ASCENT_THROTTLE_MAX
  return CarControllerParams.THROTTLE_MAX


def _ascent_adaptive_accel_request(requested_accel, measured_accel, v_ego, filtered_error, dt):
  """Apply a bounded, speed-gated correction for the Ascent's measured accel shortfall."""
  values = (requested_accel, measured_accel, v_ego, filtered_error, dt)
  if not all(math.isfinite(value) for value in values) or requested_accel <= 0.0 or dt <= 0.0:
    return requested_accel, 0.0

  if v_ego < _ASCENT_ACCEL_FEEDBACK_MIN_SPEED:
    return requested_accel, 0.0

  alpha = dt / (_ASCENT_ACCEL_FEEDBACK_TIME_CONSTANT + dt)
  filtered_error += alpha * (requested_accel - measured_accel - filtered_error)
  filtered_error = min(max(filtered_error, -1.0), 1.0)
  speed_weight = min(max(
    (v_ego - _ASCENT_ACCEL_FEEDBACK_MIN_SPEED) /
    (_ASCENT_ACCEL_FEEDBACK_FULL_SPEED - _ASCENT_ACCEL_FEEDBACK_MIN_SPEED),
    0.0), 1.0)
  correction = min(max(filtered_error, 0.0) * _ASCENT_ACCEL_FEEDBACK_GAIN,
                   _ASCENT_ACCEL_FEEDBACK_MAX_CORRECTION) * speed_weight
  return requested_accel + correction, filtered_error


def get_safety_CP():
  from opendbc.car.subaru.interface import CarInterface
  return CarInterface.get_non_essential_params("SUBARU_ASCENT")


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.apply_torque_last = 0
    self.apply_steer_last = 0
    self.driver_override = False
    self.angle_override_confirm_frames = 0
    self.angle_lkas_active = False
    self.angle_handoff_active = False
    self.ascent_angle_initialized = False
    self.ascent_aol_arm_frames = 0
    self.ascent_es_distance_counter_last = None
    self.ascent_lkas_alert_counter_last = None

    self.cruise_button_prev = 0
    self.steer_rate_counter = 0

    self.p = CarControllerParams(CP)
    self.packer = CANPacker(DBC[CP.carFingerprint][Bus.pt])
    self.main_bus = CanBus.main_for_cp(CP)
    self.angle_bus = CanBus.angle_for_cp(CP)
    self.status_bus = CanBus.camera if CP.flags & SubaruFlags.D_PLATFORM_CAMERA else CanBus.main

    if CP.flags & SubaruFlags.LKAS_ANGLE and CP.carFingerprint != CAR.SUBARU_OUTBACK_2023:
      self.VM = VehicleModel(get_safety_CP())

    self.prev_close_distance = 0
    self.epb_resume_frames_remaining = -1
    self.last_standstill_frame = 0
    self.stop_start_attempted = False
    self.stop_start_request_started = False
    self.stop_start_request_frame = 0
    self.stop_start_initial_state = None
    self.stop_start_counter = 0
    self.stop_start_acknowledged = False
    self.last_redneck_button_frame = 0
    self.avh_startup = AvhStartup()
    self.ascent_accel_feedback_error = 0.0

  def _stop_start_off_request(self, CC, CS, starpilot_toggles):
    """Send one bounded Subaru Stop/Start OFF request after ignition.

    This is intentionally opt-in and limited to a stationary vehicle in
    Park/Neutral. A single ignition session gets at most one attempt.
    """
    if self.CP.carFingerprint not in SUBARU_STOP_START_CARS or \
       not getattr(starpilot_toggles, "subaru_stop_start_off", False) or self.stop_start_attempted:
      return None

    if self.frame > _STOP_START_STARTUP_DEADLINE_FRAMES or getattr(CC, "enabled", False):
      self.stop_start_attempted = True
      return None

    if self.frame < _STOP_START_STARTUP_DELAY_FRAMES or not getattr(getattr(CS, "out", None), "canValid", True):
      return None

    out = CS.out
    if not getattr(out, "standstill", False) or out.gearShifter not in (
      structs.CarState.GearShifter.park,
      structs.CarState.GearShifter.neutral,
    ):
      return None

    dashlights_msg = getattr(CS, "dashlights_msg", None)
    if not dashlights_msg:
      return None

    if not self.stop_start_request_started:
      self.stop_start_request_started = True
      self.stop_start_request_frame = self.frame
      self.stop_start_initial_state = getattr(CS, "stop_start_state", None)
      self.stop_start_counter = (int(dashlights_msg.get("COUNTER", 0)) + 1) % 0x10

    current_state = getattr(CS, "stop_start_state", None)
    if self.stop_start_initial_state is not None and self.stop_start_initial_state != 3 and current_state == 3:
      self.stop_start_attempted = True
      self.stop_start_acknowledged = True
      return None

    elapsed = self.frame - self.stop_start_request_frame
    if elapsed >= _STOP_START_PULSE_FRAMES:
      self.stop_start_attempted = True
      return None

    if elapsed % _STOP_START_PULSE_PERIOD_FRAMES != 0:
      return None

    msg = subarucan.create_stop_start_control(
      self.packer, dashlights_msg, raw_dat=getattr(CS, "dashlights_dat", None),
      counter=self.stop_start_counter, bus=CanBus.alt_for_cp(self.CP),
    )
    self.stop_start_counter = (self.stop_start_counter + 1) % 0x10
    return msg

  def _reset_angle_handoff(self):
    self.driver_override = False
    self.angle_override_confirm_frames = 0
    self.angle_handoff_active = False

  def _angle_manual_handoff(self, CS, lat_active):
    if not lat_active:
      self._reset_angle_handoff()
      return False

    driver_override = self._update_angle_driver_override(CS)
    steering_rate = abs(getattr(CS.out, "steeringRateDeg", 0.0))
    if driver_override:
      self.angle_handoff_active = True
      return True

    if self.angle_handoff_active:
      if steering_rate > _ANGLE_REENGAGE_MAX_STEER_RATE:
        return True

      self.angle_handoff_active = False
      return True

    if not self.angle_lkas_active and steering_rate > _ANGLE_REENGAGE_MAX_STEER_RATE:
      self.angle_handoff_active = True
      return True

    return False

  def _update_angle_driver_override(self, CS):
    """Debounce the higher-confidence raw torque override signal for angle cars."""
    abs_torque = abs(getattr(CS.out, "steeringTorque", 0.0))
    if self.driver_override:
      if abs_torque < self.p.STEER_OVERRIDE_TORQUE_LOW:
        self.driver_override = False
    elif abs_torque > self.p.STEER_OVERRIDE_TORQUE_HIGH:
      self.angle_override_confirm_frames += 1
      if self.angle_override_confirm_frames >= _ANGLE_OVERRIDE_CONFIRM_FRAMES:
        self.driver_override = True
        self.angle_override_confirm_frames = 0
    else:
      self.angle_override_confirm_frames = 0

    return self.driver_override

  def _ascent_aol_ready(self, ready):
    if not ready:
      self.ascent_aol_arm_frames = 0
      return False

    self.ascent_aol_arm_frames = min(self.ascent_aol_arm_frames + 1, _ASCENT_AOL_ARM_FRAMES)
    return self.ascent_aol_arm_frames >= _ASCENT_AOL_ARM_FRAMES

  def lateral_angle(self, CC, CS):
    if self.CP.carFingerprint == CAR.SUBARU_LEGACY_2025:
      mads_only = CC.latActive and not CC.enabled
      mads_only_ok = CS.out.vEgoRaw > _LEGACY_2025_MADS_MIN_SPEED and \
        abs(CS.out.steeringAngleDeg) < _LEGACY_2025_MADS_MAX_STEER_ANGLE
      lkas_available = CC.latActive and (not mads_only or mads_only_ok) and \
        CS.out.gearShifter == structs.CarState.GearShifter.drive and not CS.out.standstill

      manual_handoff = self._angle_manual_handoff(CS, lkas_available)
      lkas_active = lkas_available and not manual_handoff

      apply_steer = apply_std_steer_angle_limits(
        CC.actuators.steeringAngleDeg,
        self.apply_steer_last,
        CS.out.vEgoRaw,
        CS.out.steeringAngleDeg,
        lkas_active,
        self.p.LEGACY_2025_ANGLE_LIMITS,
      )
      self.apply_steer_last = apply_steer
      self.angle_lkas_active = lkas_active
      return subarucan.create_steering_control_angle(self.packer, apply_steer, lkas_active, self.angle_bus)

    if self.CP.carFingerprint in (CAR.SUBARU_ASCENT_2023, CAR.SUBARU_OUTBACK_2023):
      if self.CP.carFingerprint == CAR.SUBARU_ASCENT_2023 and not self.ascent_angle_initialized:
        self.apply_steer_last = CS.out.steeringAngleDeg
        self.ascent_angle_initialized = True

      mads_only = CC.latActive and not CC.enabled
      mads_only_ok = CS.out.vEgoRaw > _ANGLE_MADS_MIN_SPEED and \
        abs(CS.out.steeringAngleDeg) < _ANGLE_MADS_MAX_STEER_ANGLE
      lkas_available = CC.latActive and (not mads_only or mads_only_ok) and \
        CS.out.gearShifter == structs.CarState.GearShifter.drive and not CS.out.standstill
      if self.CP.carFingerprint == CAR.SUBARU_ASCENT_2023:
        if mads_only:
          cruise_available = getattr(getattr(CS.out, "cruiseState", None), "available", True)
          lkas_available = self._ascent_aol_ready(lkas_available and cruise_available)
        else:
          self.ascent_aol_arm_frames = _ASCENT_AOL_ARM_FRAMES if lkas_available else 0

      if self.CP.carFingerprint == CAR.SUBARU_OUTBACK_2023:
        manual_handoff = not self.angle_lkas_active and \
          abs(getattr(CS.out, "steeringRateDeg", 0.0)) > _ANGLE_REENGAGE_MAX_STEER_RATE
      else:
        manual_handoff = self._angle_manual_handoff(CS, lkas_available)
      lkas_active = lkas_available and not manual_handoff

      if lkas_active and not self.angle_lkas_active and self.CP.carFingerprint != CAR.SUBARU_ASCENT_2023:
        self.apply_steer_last = CS.out.steeringAngleDeg

      apply_steer = apply_std_steer_angle_limits(
        CC.actuators.steeringAngleDeg,
        self.apply_steer_last,
        CS.out.vEgoRaw,
        CS.out.steeringAngleDeg,
        lkas_active,
        self.p.FIXED_ANGLE_LIMITS,
      )
      self.apply_steer_last = apply_steer
      self.angle_lkas_active = lkas_active
      return subarucan.create_steering_control_angle(self.packer, apply_steer, lkas_active, self.angle_bus)

    mads_only = CC.latActive and not getattr(CC, "enabled", False)
    mads_only_ok = CS.out.vEgoRaw > _ANGLE_MADS_MIN_SPEED and \
      abs(CS.out.steeringAngleDeg) < _ANGLE_MADS_MAX_STEER_ANGLE
    lkas_available = CC.latActive and (not mads_only or mads_only_ok) and \
      getattr(CS.out, "gearShifter", structs.CarState.GearShifter.drive) == structs.CarState.GearShifter.drive and \
      not getattr(CS.out, "standstill", False)
    manual_handoff = self._angle_manual_handoff(CS, lkas_available)
    lat_active = lkas_available and not self.driver_override and not manual_handoff
    if lat_active and not self.angle_lkas_active:
      self.apply_steer_last = CS.out.steeringAngleDeg
    apply_steer = apply_steer_angle_limits_vm(
      CC.actuators.steeringAngleDeg,
      self.apply_steer_last,
      CS.out.vEgoRaw,
      CS.out.steeringAngleDeg,
      lat_active,
      self.p,
      self.VM,
    )

    if not lat_active:
      apply_steer = CS.out.steeringAngleDeg

    self.apply_steer_last = apply_steer
    self.angle_lkas_active = lat_active
    return subarucan.create_steering_control_angle(self.packer, apply_steer, lat_active, self.angle_bus)

  def lateral_torque(self, CC, CS):
    apply_torque = int(round(CC.actuators.torque * self.p.STEER_MAX))
    apply_torque = apply_driver_steer_torque_limits(apply_torque, self.apply_torque_last, CS.out.steeringTorque, self.p)

    if not CC.latActive:
      apply_torque = 0

    self.apply_torque_last = apply_torque

    if self.CP.flags & SubaruFlags.PREGLOBAL:
      return subarucan.create_preglobal_steering_control(
        self.packer, self.frame // self.p.STEER_STEP, apply_torque, CC.latActive,
      )

    apply_steer_req = CC.latActive
    if self.CP.flags & SubaruFlags.STEER_RATE_LIMITED:
      self.steer_rate_counter, apply_steer_req = common_fault_avoidance(
        abs(CS.out.steeringRateDeg) > MAX_STEER_RATE,
        apply_steer_req,
        self.steer_rate_counter,
        MAX_STEER_RATE_FRAMES,
      )

    return subarucan.create_steering_control(self.packer, apply_torque, apply_steer_req)

  def _lkas_status_active(self, CC):
    if self.CP.carFingerprint in (CAR.SUBARU_ASCENT_2023, CAR.SUBARU_OUTBACK_2023):
      return self.angle_lkas_active
    return CC.latActive

  def _ascent_lkas_alert(self, CC, CS):
    if self.CP.carFingerprint != CAR.SUBARU_ASCENT_2023 or not self.CP.flags & SubaruFlags.ASCENT_LKAS_ALERT:
      return None
    dat = getattr(CS, "es_lkas_alert_dat", b"")
    msg = subarucan.create_ascent_lkas_alert(dat, self._lkas_status_active(CC), CC.hudControl.visualAlert)
    if msg is None or (dat[1] & 0xf) == self.ascent_lkas_alert_counter_last:
      return None
    self.ascent_lkas_alert_counter_last = dat[1] & 0xf
    return msg

  def update(self, CC, CS, now_nanos, starpilot_toggles):
    actuators = CC.actuators
    hud_control = CC.hudControl
    pcm_cancel_cmd = CC.cruiseControl.cancel
    subaru_redneck_cruise = bool(
      self.CP.carFingerprint == CAR.SUBARU_IMPREZA_2020 and
      getattr(starpilot_toggles, "subaru_redneck_cruise", False)
    )

    can_sends = []

    stop_start_msg = self._stop_start_off_request(CC, CS, starpilot_toggles)
    if stop_start_msg is not None:
      can_sends.append(stop_start_msg)

    if self.CP.carFingerprint == CAR.SUBARU_LEGACY_2025:
      can_sends.extend(self.avh_startup.update(
        now_nanos / 1e9, getattr(CS, "avh_frames", {}),
        getattr(starpilot_toggles, "subaru_avh_on", False), getattr(CS.out, "canValid", False),
        CC.enabled or CC.latActive or CC.longActive,
      ))

    # *** steering ***
    if (self.frame % self.p.STEER_STEP) == 0:
      if self.CP.flags & SubaruFlags.LKAS_ANGLE:
        can_sends.append(self.lateral_angle(CC, CS))
      else:
        can_sends.append(self.lateral_torque(CC, CS))

    # *** stop and go ***
    subaru_sng_manual_parking_brake = getattr(starpilot_toggles, "subaru_sng_manual_parking_brake", False)
    if starpilot_toggles.subaru_sng:
      throttle_cmd, speed_cmd = self.stop_and_go(CC, CS, subaru_sng_manual_parking_brake)

    # *** longitudinal ***

    if CC.longActive:
      # The generic Subaru alpha-long mapping is acceleration-only. OEM EyeSight
      # data from this Ascent shows a strong speed-dependent feed-forward need:
      # at highway speeds the generic 1818/600 zero-accel command is far below
      # the stock Cruise_Throttle / Cruise_RPM required to maintain speed.
      #
      # Apply the Ascent-specific feed-forward only to positive acceleration
      # commands. Braking and all other Subaru platforms stay unchanged.
      if self.CP.carFingerprint == CAR.SUBARU_ASCENT and actuators.accel >= 0.0:
        accel_ff, self.ascent_accel_feedback_error = _ascent_adaptive_accel_request(
          actuators.accel, CS.out.aEgo, CS.out.vEgo, self.ascent_accel_feedback_error, DT_CTRL,
        )
        accel_ff = np.clip(accel_ff, 0.0, 2.0)
        throttle_base = np.interp(CS.out.vEgo, CarControllerParams.ASCENT_LONG_SPEED_BP,
                                  CarControllerParams.ASCENT_THROTTLE_BASE_V)
        rpm_base = np.interp(CS.out.vEgo, CarControllerParams.ASCENT_LONG_SPEED_BP,
                             CarControllerParams.ASCENT_RPM_BASE_V)

        apply_throttle = int(round(throttle_base + accel_ff * CarControllerParams.ASCENT_THROTTLE_ACCEL_GAIN))
        apply_rpm = int(round(rpm_base + accel_ff * CarControllerParams.ASCENT_RPM_ACCEL_GAIN))
      else:
        if self.CP.carFingerprint == CAR.SUBARU_ASCENT:
          self.ascent_accel_feedback_error = 0.0
        apply_throttle = int(round(np.interp(actuators.accel, CarControllerParams.THROTTLE_LOOKUP_BP, CarControllerParams.THROTTLE_LOOKUP_V)))
        apply_rpm = int(round(np.interp(actuators.accel, CarControllerParams.RPM_LOOKUP_BP, CarControllerParams.RPM_LOOKUP_V)))

      apply_brake = int(round(np.interp(actuators.accel, CarControllerParams.BRAKE_LOOKUP_BP, CarControllerParams.BRAKE_LOOKUP_V)))

      # limit min and max values
      throttle_max = _throttle_max_for_car(self.CP.carFingerprint)
      cruise_throttle = np.clip(apply_throttle, CarControllerParams.THROTTLE_MIN, throttle_max)
      cruise_rpm = np.clip(apply_rpm, CarControllerParams.RPM_MIN, CarControllerParams.RPM_MAX)
      cruise_brake = np.clip(apply_brake, CarControllerParams.BRAKE_MIN, CarControllerParams.BRAKE_MAX)
    else:
      self.ascent_accel_feedback_error = 0.0
      cruise_throttle = CarControllerParams.THROTTLE_INACTIVE
      cruise_rpm = CarControllerParams.RPM_MIN
      cruise_brake = CarControllerParams.BRAKE_MIN

    # *** alerts and pcm cancel ***
    lkas_alert_msg = self._ascent_lkas_alert(CC, CS)
    if lkas_alert_msg is not None:
      can_sends.append(lkas_alert_msg)

    if self.CP.flags & SubaruFlags.PREGLOBAL:
      if self.frame % 5 == 0:
        # 1 = main, 2 = set shallow, 3 = set deep, 4 = resume shallow, 5 = resume deep
        # disengage ACC when OP is disengaged
        if pcm_cancel_cmd:
          cruise_button = 1
        # turn main on if off and past start-up state
        elif not CS.out.cruiseState.available and CS.ready:
          cruise_button = 1
        else:
          cruise_button = CS.cruise_button

        # unstick previous mocked button press
        if cruise_button == 1 and self.cruise_button_prev == 1:
          cruise_button = 0
        self.cruise_button_prev = cruise_button

        can_sends.append(subarucan.create_preglobal_es_distance(self.packer, cruise_button, CS.es_distance_msg))

      if starpilot_toggles.subaru_sng:
        can_sends.append(subarucan.create_preglobal_throttle(self.packer, CS.throttle_msg["COUNTER"] + 1, CS.throttle_msg,
                                                             throttle_cmd))
        if self.frame % 2 == 0:
          can_sends.append(subarucan.create_preglobal_brake_pedal(self.packer, CS.brake_pedal_msg,
                                                                  speed_cmd))
    else:
      if self.frame % 10 == 0:
        can_sends.append(subarucan.create_es_dashstatus(self.packer, self.frame // 10, CS.es_dashstatus_msg, CC.enabled,
                                                        self.CP.openpilotLongitudinalControl and not subaru_redneck_cruise,
                                                        CC.longActive, hud_control.leadVisible,
                                                        self.status_bus))

        can_sends.append(subarucan.create_es_lkas_state(
          self.packer, self.frame // 10, CS.es_lkas_state_msg, self._lkas_status_active(CC), hud_control.visualAlert,
          hud_control.leftLaneVisible, hud_control.rightLaneVisible,
          hud_control.leftLaneDepart, hud_control.rightLaneDepart, self.status_bus,
        ))

        if self.CP.flags & SubaruFlags.SEND_INFOTAINMENT:
          can_sends.append(subarucan.create_es_infotainment(self.packer, self.frame // 10, CS.es_infotainment_msg,
                                                           hud_control.visualAlert, self.status_bus))

      if starpilot_toggles.subaru_sng:
        can_sends.append(subarucan.create_throttle(self.packer, CS.throttle_msg["COUNTER"] + 1, CS.throttle_msg,
                                                   throttle_cmd))
        if self.frame % 2 == 0:
          can_sends.append(subarucan.create_brake_pedal(self.packer, self.frame // 2, CS.brake_pedal_msg,
                                                        speed_cmd, pcm_cancel_cmd))

      if self.CP.openpilotLongitudinalControl and not subaru_redneck_cruise:
        if self.frame % 5 == 0:
          can_sends.append(subarucan.create_es_status(self.packer, self.frame // 5, CS.es_status_msg,
                                                      self.CP.openpilotLongitudinalControl, CC.longActive, cruise_rpm))

          can_sends.append(subarucan.create_es_brake(self.packer, self.frame // 5, CS.es_brake_msg,
                                                     self.CP.openpilotLongitudinalControl, CC.longActive, cruise_brake))

          can_sends.append(subarucan.create_es_distance(self.packer, self.frame // 5, CS.es_distance_msg, 0, pcm_cancel_cmd,
                                                        self.CP.openpilotLongitudinalControl, cruise_brake > 0, cruise_throttle))
      else:
        cancel_frame_ready = True
        if self.CP.carFingerprint == CAR.SUBARU_ASCENT_2023:
          stock_counter = CS.es_distance_msg["COUNTER"]
          cancel_frame_ready = stock_counter != self.ascent_es_distance_counter_last
          self.ascent_es_distance_counter_last = stock_counter
        if pcm_cancel_cmd and cancel_frame_ready:
          if not (self.CP.flags & SubaruFlags.HYBRID):
            bus = CanBus.alt_for_cp(self.CP) if self.CP.flags & SubaruFlags.GLOBAL_GEN2 else self.main_bus
            can_sends.append(subarucan.create_es_distance(self.packer, CS.es_distance_msg["COUNTER"] + 1, CS.es_distance_msg, bus, pcm_cancel_cmd))

      if subaru_redneck_cruise:
        redneck_button = {
          1: subarucan.CRUISE_BUTTON_RESUME,
          2: subarucan.CRUISE_BUTTON_SET,
        }.get(getattr(CS, "redneck_send_button", 0))
        cruise_buttons_msg = getattr(CS, "cruise_buttons_msg", None)
        if redneck_button and cruise_buttons_msg and self.frame - self.last_redneck_button_frame >= _REDNECK_BUTTON_INTERVAL_FRAMES:
          counter = (int(cruise_buttons_msg["COUNTER"]) + 1) % 0x10
          for copy_idx in range(_REDNECK_BUTTON_COPIES):
            can_sends.append(subarucan.create_cruise_buttons(
              self.packer, counter + copy_idx, cruise_buttons_msg, redneck_button, self.main_bus,
            ))
          self.last_redneck_button_frame = self.frame

      if self.CP.flags & SubaruFlags.DISABLE_EYESIGHT:
        # Tester present (keeps eyesight disabled)
        if self.frame % 100 == 0:
          can_sends.append(make_tester_present_msg(GLOBAL_ES_ADDR, CanBus.camera, suppress_response=True))

        # Create all of the other eyesight messages to keep the rest of the car happy when eyesight is disabled
        if self.frame % 5 == 0:
          can_sends.append(subarucan.create_es_highbeamassist(self.packer))

        if self.frame % 10 == 0:
          can_sends.append(subarucan.create_es_static_1(self.packer))

        if self.frame % 2 == 0:
          can_sends.append(subarucan.create_es_static_2(self.packer))

    new_actuators = actuators.as_builder()
    if self.CP.flags & SubaruFlags.LKAS_ANGLE:
      new_actuators.steeringAngleDeg = self.apply_steer_last
    else:
      new_actuators.torque = self.apply_torque_last / self.p.STEER_MAX
      new_actuators.torqueOutputCan = self.apply_torque_last

    self.frame += 1
    return new_actuators, can_sends

  def stop_and_go(self, CC, CS, manual_parking_brake=False):
    throttle_cmd = False
    speed_cmd = False

    if not CC.enabled or not CC.hudControl.leadVisible:
      return throttle_cmd, speed_cmd

    close_distance = CS.close_distance
    if not CS.out.standstill:
      self.last_standstill_frame = self.frame

    standstill_timers = (0.75, 0.8) if self.CP.flags & SubaruFlags.PREGLOBAL else (0.5, 0.55)
    standstill_duration = (self.frame - self.last_standstill_frame) * DT_CTRL
    in_standstill_hold = standstill_duration > standstill_timers[0]
    if standstill_duration >= standstill_timers[1]:
      self.last_standstill_frame = self.frame

    if manual_parking_brake or not (self.CP.flags & SubaruFlags.PREGLOBAL):
      speed_cmd = in_standstill_hold

    should_resume = (
      CS.out.standstill and
      _SNG_ACC_MIN_DIST < close_distance < _SNG_ACC_MAX_DIST and
      close_distance > self.prev_close_distance
    )
    if should_resume:
      self.epb_resume_frames_remaining = 15

    throttle_cmd = self.epb_resume_frames_remaining > 0
    if self.epb_resume_frames_remaining > 0:
      self.epb_resume_frames_remaining -= 1

    self.prev_close_distance = close_distance
    return throttle_cmd, speed_cmd
