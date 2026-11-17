import math
import numpy as np
from opendbc.car import ACCEL_MAX_TAKEOFF
from opendbc.car import Bus, make_tester_present_msg, rate_limit, structs, ACCELERATION_DUE_TO_GRAVITY, DT_CTRL
from opendbc.car.lateral import apply_meas_steer_torque_limits, apply_std_steer_angle_limits, common_fault_avoidance
from opendbc.car.carlog import carlog
from opendbc.car.common.filter_simple import FirstOrderFilter, HighPassFilter
from opendbc.car.common.pid import PIDController
from opendbc.car.secoc import add_mac, build_sync_mac
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.toyota import toyotacan
from opendbc.car.toyota import (dom_defaults, prius_longitudinal, rav4h_filter, pedal, eps8_sdsu, legacy_sdsu,
                                advert_filter, legacy_stop_timer, legacy_advert_filter)
from opendbc.car.toyota import pedal_composite, camera_2aa_filter, prius_sdsu, prius_advert_filter
from opendbc.car.toyota.door_locks import DoorLocks
from opendbc.car.toyota.values import CAR, CarControllerParams, ToyotaFlags, TOYOTA_AUTO_HOLD_CARS, uses_toyota_auto_hold_aeb
from opendbc.can import CANPacker
from opendbc.safety import ALTERNATIVE_EXPERIENCE

Ecu = structs.CarParams.Ecu
LongCtrlState = structs.CarControl.Actuators.LongControlState
SteerControlType = structs.CarParams.SteerControlType
VisualAlert = structs.CarControl.HUDControl.VisualAlert

# The up limit allows the brakes/gas to unwind quickly leaving a stop,
# the down limit roughly matches the rate of ACCEL_NET, reducing PCM compensation windup
ACCEL_WINDUP_LIMIT = 4.0 * DT_CTRL * 3  # m/s^2 / frame
ACCEL_WINDDOWN_LIMIT = -4.0 * DT_CTRL * 3  # m/s^2 / frame
ACCEL_PID_UNWIND = 0.03 * DT_CTRL * 3  # m/s^2 / frame

MAX_PITCH_COMPENSATION = 1.5  # m/s^2

# LKA limits
# EPS faults if you apply torque while the steering rate is above 100 deg/s for too long
MAX_STEER_RATE = 100  # deg/s
COROLLA_MAX_STEER_RATE = 80
MAX_STEER_RATE_FRAMES = 17  # tx control frames needed before torque can be cut

# EPS allows user torque above threshold for 50 frames before permanently faulting
MAX_USER_TORQUE = 500


TOYOTA_AUTO_HOLD_ACCEL = -1.0
TOYOTA_AUTO_HOLD_ACTIVATION_FRAMES = 100


def get_toyota_steer_rate_limit(car_fingerprint):
  return COROLLA_MAX_STEER_RATE if car_fingerprint == CAR.TOYOTA_COROLLA_TSS2 else MAX_STEER_RATE


def supports_toyota_auto_hold(CP):
  return (CP.carFingerprint in TOYOTA_AUTO_HOLD_CARS and CP.openpilotLongitudinalControl and
          not CP.flags & ToyotaFlags.SECOC and bool(CP.flags & ToyotaFlags.AUTO_BRAKE_HOLD) and
          (CP.alternativeExperience & (ALTERNATIVE_EXPERIENCE.TOYOTA_AUTO_HOLD | ALTERNATIVE_EXPERIENCE.TOYOTA_AEB_HOLD)) ==
          (ALTERNATIVE_EXPERIENCE.TOYOTA_AEB_HOLD if uses_toyota_auto_hold_aeb(CP) else
           ALTERNATIVE_EXPERIENCE.TOYOTA_AUTO_HOLD))


def get_long_tune(CP, params):
  prius = prius_longitudinal.enabled(CP)
  if prius or CP.flags & ToyotaFlags.TSS2 or dom_defaults.camry_hybrid(CP) or dom_defaults.rav4h_sdsu(CP):
    kiBP = [2., 5.]
    kiV = [0.5, 0.25]
  else:
    kiBP = [0., 5., 35.]
    kiV = [3.6, 2.4, 1.5]

  return PIDController(0.0, (kiBP, kiV), k_f=.8 if prius else 1.0,
                       pos_limit=params.ACCEL_MAX, neg_limit=params.ACCEL_MIN,
                       rate=1 / (DT_CTRL * 3))


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.door_locks = DoorLocks()
    self.reverse_cruise_input = None
    self.prius_filter_input = None
    self.original_prius_sdsu = prius_sdsu.configured(CP) or prius_longitudinal.eps73_status_configured(CP)
    self.original_prius_filter = self.original_prius_sdsu or prius_advert_filter.configured(CP)
    self.pedal_input = None
    self.rav4h_filter_input = None
    self.advert_filter_input = None
    self.params = CarControllerParams(self.CP)
    self.last_torque = 0
    self.last_angle = 0
    self.alert_active = False
    self.standstill_req = False
    self.sng_hack = False
    self.last_standstill = False
    self.prius_longitudinal = prius_longitudinal.enabled(CP)
    self.sdsu_hybrid = (eps8_sdsu.configured(CP) or legacy_sdsu.configured(CP)) and bool(CP.flags & ToyotaFlags.HYBRID)
    self.legacy_stop_timer = legacy_stop_timer.enabled(CP)
    self.legacy_advert_filter = legacy_advert_filter.configured(CP)
    self.camera_twoaa_filter = camera_2aa_filter.configured(CP)
    self.permit_braking = True
    self.steer_rate_counter = 0
    self.distance_button = 0

    # *** start long control state ***
    self.long_pid = get_long_tune(self.CP, self.params)
    self.aego = FirstOrderFilter(0.0, 0.25, DT_CTRL * 3)
    self.pitch = FirstOrderFilter(0, 0.5, DT_CTRL)
    self.pitch_hp = HighPassFilter(0.0, 0.25, 1.5, DT_CTRL)

    self.accel = 0
    self.prev_accel = 0
    # *** end long control state ***

    self.packer = CANPacker(dbc_names[Bus.pt])

    self.secoc_lka_message_counter = 0
    self.secoc_lta_message_counter = 0
    self.secoc_acc_message_counter = 0
    self.secoc_prev_reset_counter = 0
    self.brake_hold_active = False
    self._brake_hold_counter = 0

  def update_auto_hold_state(self, CS, cancel_requested=False, activation_frames=TOYOTA_AUTO_HOLD_ACTIVATION_FRAMES):
    aeb_hold = uses_toyota_auto_hold_aeb(self.CP)
    allowed = (not cancel_requested and CS.out.standstill and CS.out.cruiseState.available and
               not CS.out.gasPressed and not CS.out.cruiseState.enabled and
               CS.out.gearShifter not in (structs.CarState.GearShifter.park, structs.CarState.GearShifter.reverse))
    if aeb_hold:
      # Manual AEB hold requires a driver brake stop.
      if allowed and not self.brake_hold_active and CS.out.brakePressed:
        self._brake_hold_counter += 1
        self.brake_hold_active = self._brake_hold_counter > activation_frames
      elif not allowed:
        self.reset_auto_hold_state()
    elif not allowed:
      self.reset_auto_hold_state()
    elif not self.brake_hold_active:
      if CS.out.brakePressed:
        self._brake_hold_counter += 1
        self.brake_hold_active = self._brake_hold_counter > activation_frames
      else:
        self._brake_hold_counter = 0
    return self.brake_hold_active

  def reset_auto_hold_state(self):
    if self.brake_hold_active and not uses_toyota_auto_hold_aeb(self.CP):
      self.standstill_req = False
    self._brake_hold_counter = 0
    self.brake_hold_active = False

  def update(self, CC, CS, now_nanos):
    from opendbc.car.toyota import dsu_bypass
    bypass_long = dsu_bypass.enabled(self.CP)
    _projection = self.learning_projection_observer
    if _projection is not None:
      _projection.begin(CC.actuators.torque, self.frame)
    filter_allowed = (not self.prius_longitudinal or self.prius_filter_input is not None and
                      self.prius_filter_input.update(now_nanos))
    original_prius_long = self.original_prius_filter and self.CP.openpilotLongitudinalControl
    if self.original_prius_filter:
      filter_allowed = filter_allowed and bool(
        CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and (not self.original_prius_sdsu or CS.sdsu_state == 0) and
        not CS.out.accFaulted and not CS.out.stockAeb and not CS.out.steerFaultTemporary and not CS.out.steerFaultPermanent and
        CS.out.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport) and not CS.out.doorOpen and
        not CS.out.seatbeltUnlatched and (not CS.out.brakePressed or CS.out.standstill))
    rav4h_long = rav4h_filter.enabled(self.CP)
    if rav4h_long:
      filter_allowed = bool(self.rav4h_filter_input is not None and self.rav4h_filter_input.update(now_nanos) and
                            CS.out.canValid and not CS.out.canTimeout and CS.sdsu_state == 0 and
                            not CS.out.accFaulted and not CS.out.stockAeb)
      if legacy_sdsu.configured(self.CP) and self.CP.carFingerprint in legacy_sdsu.ALT_MAIN_CARS:
        # The native owner requires fresh physical DSU MAIN independently of
        # status. Neutral output must also withdraw its standstill release.
        filter_allowed = filter_allowed and bool(CS.out.cruiseState.available)
    advert_owner = advert_filter.configured(self.CP)
    advert_long = advert_owner and self.CP.openpilotLongitudinalControl
    if advert_long:
      filter_allowed = bool(self.advert_filter_input is not None and self.advert_filter_input.update(now_nanos) and
                            CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and
                            not CS.out.steerFaultTemporary and not CS.out.steerFaultPermanent and not CS.out.accFaulted and not CS.out.stockAeb)
    guarded_advert = self.legacy_advert_filter or self.camera_twoaa_filter
    legacy_advert_source_ready = not guarded_advert or (
      CS.out.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport) and not CS.out.doorOpen)
    if guarded_advert:
      filter_allowed = filter_allowed and legacy_advert_source_ready
    pedal_owner = pedal.configured(self.CP)
    hold_source_ready = filter_allowed and bool(
      CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and not CS.out.accFaulted and
      not CS.out.stockAeb and not CS.out.steerFaultTemporary and not CS.out.steerFaultPermanent and not CS.out.doorOpen and
      not CS.out.seatbeltUnlatched and CS.out.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport))
    if pedal_owner:
      hold_source_ready = hold_source_ready and bool(self.pedal_input is not None and self.pedal_input.update(now_nanos) and CS.pedal_ready)
    if bypass_long:
      hold_source_ready = hold_source_ready and bool(self.bypass_input is not None and self.bypass_input.update(now_nanos))
    composite_pedal = pedal_composite.configured(self.CP)
    if composite_pedal:
      filter_allowed = filter_allowed and bool(
        CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and not CS.out.accFaulted and not CS.out.stockAeb and
        not CS.out.steerFaultTemporary and not CS.out.steerFaultPermanent and not CS.out.doorOpen and not CS.out.seatbeltUnlatched and
        (not CS.out.brakePressed or CS.out.standstill) and
        CS.out.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport))
    if pedal_owner:
      filter_allowed = filter_allowed and bool(self.pedal_input is not None and self.pedal_input.update(now_nanos) and CS.pedal_ready)
    if bypass_long:
      preference = getattr(self, 'bypass_input', None)
      filter_allowed = filter_allowed and bool(
        preference is not None and preference.update(now_nanos) and CS.out.canValid and not CS.out.canTimeout and
        CS.out.cruiseState.available and not CS.out.accFaulted and not CS.out.stockAeb and
        not CS.out.steerFaultTemporary and not CS.out.steerFaultPermanent and not CS.out.doorOpen and
        not CS.out.seatbeltUnlatched and (not CS.out.brakePressed or CS.out.standstill) and
        CS.out.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport))
    long_active = CC.longActive and filter_allowed
    if self.original_prius_filter or composite_pedal or bypass_long:
      long_active = long_active and not (CS.out.gasPressed or CS.out.brakePressed)
    if guarded_advert:
      long_active = long_active and not (CS.out.seatbeltUnlatched or CS.out.gasPressed or CS.out.brakePressed)
    actuators = CC.actuators
    stopping = actuators.longControlState == LongCtrlState.stopping
    hud_control = CC.hudControl
    pcm_cancel_cmd = CC.cruiseControl.cancel
    if (bypass_long or rav4h_long or pedal_owner or advert_long or original_prius_long) and not filter_allowed and CS.out.cruiseState.enabled:
      pcm_cancel_cmd = True
    lat_active = CC.latActive and abs(CS.out.steeringTorque) < MAX_USER_TORQUE
    if dsu_bypass.configured(self.CP):
      lat_active = lat_active and CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and not (
        CS.out.steerFaultTemporary or CS.out.steerFaultPermanent or CS.out.doorOpen)
    if self.original_prius_filter:
      lat_active = lat_active and CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and not (
        CS.out.accFaulted or CS.out.steerFaultTemporary or CS.out.steerFaultPermanent or CS.out.doorOpen)
    if advert_owner:
      lat_active = lat_active and CS.out.canValid and not CS.out.canTimeout and CS.out.cruiseState.available and not (
        CS.out.steerFaultTemporary or CS.out.steerFaultPermanent)
    if guarded_advert:
      lat_active = lat_active and legacy_advert_source_ready
    CS.zss_lateral_active = bool(CC.latActive)

    if len(CC.orientationNED) == 3:
      self.pitch.update(CC.orientationNED[1])
      self.pitch_hp.update(CC.orientationNED[1])

    # *** control msgs ***
    can_sends = []

    # *** handle secoc reset counter increase ***
    if self.CP.flags & ToyotaFlags.SECOC.value:
      if CS.secoc_synchronization['RESET_CNT'] != self.secoc_prev_reset_counter:
        self.secoc_lka_message_counter = 0
        self.secoc_lta_message_counter = 0
        self.secoc_acc_message_counter = 0
        self.secoc_prev_reset_counter = CS.secoc_synchronization['RESET_CNT']

        expected_mac = build_sync_mac(self.secoc_key, int(CS.secoc_synchronization['TRIP_CNT']), int(CS.secoc_synchronization['RESET_CNT']))
        if int(CS.secoc_synchronization['AUTHENTICATOR']) != expected_mac:
          carlog.error("SecOC synchronization MAC mismatch, wrong key?")

    # *** steer torque ***
    new_torque = int(round(actuators.torque * self.params.STEER_MAX))
    if _projection is not None:
      _projection.input(new_torque, self.params.STEER_MAX, self.last_torque, CS.out.steeringTorqueEps, self.params, limiter='measured')
    apply_torque = apply_meas_steer_torque_limits(new_torque, self.last_torque, CS.out.steeringTorqueEps, self.params)

    # Steering-rate fault prevention
    self.steer_rate_counter, apply_steer_req = common_fault_avoidance(
      abs(CS.out.steeringRateDeg) >= get_toyota_steer_rate_limit(self.CP.carFingerprint), lat_active,
      self.steer_rate_counter, MAX_STEER_RATE_FRAMES)

    if not lat_active:
      apply_torque = 0

    # *** steer angle ***
    if self.CP.steerControlType == SteerControlType.angle:
      # If using LTA control, disable LKA and set steering angle command
      apply_torque = 0
      apply_steer_req = False
      if self.frame % 2 == 0:
        # EPS uses the torque sensor angle to control with, offset to compensate
        apply_angle = actuators.steeringAngleDeg + CS.out.steeringAngleOffsetDeg

        # Angular rate limit based on speed
        self.last_angle = apply_std_steer_angle_limits(apply_angle, self.last_angle, CS.out.vEgoRaw,
                                                       CS.out.steeringAngleDeg + CS.out.steeringAngleOffsetDeg,
                                                       CC.latActive, self.params.ANGLE_LIMITS)

    self.last_torque = apply_torque

    # toyota can trace shows STEERING_LKA at 42Hz, with counter adding alternatively 1 and 2;
    # sending it at 100Hz seem to allow a higher rate limit, as the rate limit seems imposed
    # on consecutive messages
    steer_command = toyotacan.create_steer_command(self.packer, apply_torque, apply_steer_req)
    if self.CP.flags & ToyotaFlags.SECOC.value:
      # TODO: check if this slow and needs to be done by the CANPacker
      steer_command = add_mac(self.secoc_key,
                              int(CS.secoc_synchronization['TRIP_CNT']),
                              int(CS.secoc_synchronization['RESET_CNT']),
                              self.secoc_lka_message_counter,
                              steer_command)
      self.secoc_lka_message_counter += 1
    can_sends.append(steer_command)
    if _projection is not None:
      _projection.emit('toyota.STEERING_LKA', apply_torque, apply_steer_req, can_sends[-1])

    # STEERING_LTA does not seem to allow more rate by sending faster, and may wind up easier
    if self.frame % 2 == 0 and self.CP.flags & ToyotaFlags.TSS2:
      lta_active = lat_active and self.CP.steerControlType == SteerControlType.angle
      # cut steering torque with TORQUE_WIND_DOWN when either EPS torque or driver torque is above
      # the threshold, to limit max lateral acceleration and for driver torque blending respectively.
      full_torque_condition = (abs(CS.out.steeringTorqueEps) < self.params.STEER_MAX and
                               abs(CS.out.steeringTorque) < self.params.MAX_LTA_DRIVER_TORQUE_ALLOWANCE)

      # TORQUE_WIND_DOWN at 0 ramps down torque at roughly the max down rate of 1500 units/sec
      torque_wind_down = 100 if lta_active and full_torque_condition else 0
      can_sends.append(toyotacan.create_lta_steer_command(self.packer, self.CP.steerControlType, self.last_angle,
                                                          lta_active, self.frame // 2, torque_wind_down))

      if self.CP.flags & ToyotaFlags.SECOC.value:
        lta_steer_2 = toyotacan.create_lta_steer_command_2(self.packer, self.frame // 2)
        lta_steer_2 = add_mac(self.secoc_key,
                              int(CS.secoc_synchronization['TRIP_CNT']),
                              int(CS.secoc_synchronization['RESET_CNT']),
                              self.secoc_lta_message_counter,
                              lta_steer_2)
        self.secoc_lta_message_counter += 1
        can_sends.append(lta_steer_2)

    # handle UI messages
    fcw_alert = hud_control.visualAlert == VisualAlert.fcw
    steer_alert = hud_control.visualAlert in (VisualAlert.steerRequired, VisualAlert.ldw)
    lead = hud_control.leadVisible or CS.out.vEgo < 12.  # at low speed we always assume the lead is present so ACC can be engaged

    # *** gas and brake ***
    if supports_toyota_auto_hold(self.CP) and CS.out.canValid and not CS.out.canTimeout and hold_source_ready:
      self.update_auto_hold_state(CS, pcm_cancel_cmd if not uses_toyota_auto_hold_aeb(self.CP) else False)
      if (uses_toyota_auto_hold_aeb(self.CP) and self.frame % 2 == 0 and
          CS.out.standstill and CS.out.cruiseState.available and not CS.out.gasPressed):
        can_sends.append(toyotacan.create_brake_hold_command(self.packer, self.frame, CS.pre_collision_2, self.brake_hold_active))
    else:
      self.reset_auto_hold_state()
    if self.CP.openpilotLongitudinalControl:
      # if user engages at a stop with foot on brake, PCM starts in a special cruise standstill mode. on resume press,
      # brakes can take a while to ramp up causing a lurch forward. prevent resume press until planner wants to move.
      # don't use CC.cruiseControl.resume since it is gated on CS.cruiseState.standstill which goes false for 3s after resume press
      # whitelist hybrids as they do not have this issue and can stay stopped after resume press
      if self.prius_longitudinal:
        if CS.out.standstill and not self.last_standstill:
          self.standstill_req = True
        if CS.pcm_acc_status != 8 or CC.cruiseControl.resume:
          self.standstill_req = False
        self.last_standstill = CS.out.standstill
        if not long_active and CS.out.standstill:
          self.standstill_req = True
      elif self.legacy_stop_timer:
        self.standstill_req = legacy_stop_timer.request(
          self.last_standstill, self.standstill_req, CS.out.standstill,
          CS.pcm_acc_status, CC.cruiseControl.resume, long_active)
        self.last_standstill = CS.out.standstill
      elif self.sdsu_hybrid or (advert_long and bool(self.CP.flags & ToyotaFlags.HYBRID) and
                              not (legacy_advert_filter.enabled(self.CP) and self.CP.carFingerprint == CAR.TOYOTA_RAV4H)):
        # Retain the existing hybrid resume behavior while active. The shared
        # owner guard below still holds neutral when LONG or its source is lost.
        self.standstill_req = not long_active and CS.out.standstill
      elif rav4h_long or (legacy_advert_filter.enabled(self.CP) and self.CP.carFingerprint == CAR.TOYOTA_RAV4H) or not self.CP.flags & ToyotaFlags.HYBRID.value:
        should_resume = long_active and actuators.accel > 0 if rav4h_long or legacy_advert_filter.enabled(self.CP) else actuators.accel > 0
        if should_resume:
          self.standstill_req = False

        if not should_resume and CS.out.cruiseState.standstill:
          self.standstill_req = True

      if self.sng_hack and long_active:
        self.standstill_req = False

      if (bypass_long or rav4h_long or pedal_owner or advert_long or original_prius_long) and (not filter_allowed or not long_active and CS.out.standstill):
        self.standstill_req = True

      if pedal_owner and (self.CP.carFingerprint in pedal.RADAR_PEDAL_CARS or pedal_composite.configured(self.CP)) and not long_active:
        # A radar-disable safety handoff starts with fresh native RX checks.
        # Neutral output must not request release before LONG is admitted.
        self.standstill_req = True

      if self.frame % 3 == 0:
        # Press distance button until we are at the correct bar length. Only change while enabled to avoid skipping startup popup
        if self.frame % 6 == 0 and self.CP.openpilotLongitudinalControl:
          desired_distance = 4 - hud_control.leadDistanceBars
          if CS.out.cruiseState.enabled and CS.pcm_follow_distance != desired_distance:
            self.distance_button = not self.distance_button
          else:
            self.distance_button = 0

        # internal PCM gas command can get stuck unwinding from negative accel so we apply a generous rate limit
        pcm_accel_cmd = actuators.accel if filter_allowed else 0.
        if (bypass_long or self.prius_longitudinal or rav4h_long or advert_long) and not long_active:
          pcm_accel_cmd = 0.
        if long_active:
          pcm_accel_cmd = rate_limit(pcm_accel_cmd, self.prev_accel, ACCEL_WINDDOWN_LIMIT, ACCEL_WINDUP_LIMIT)
        self.prev_accel = pcm_accel_cmd

        # calculate amount of acceleration PCM should apply to reach target, given pitch.
        # clipped to only include downhill angles, avoids erroneously unsetting PERMIT_BRAKING when stopping on uphills
        accel_due_to_pitch = math.sin(min(self.pitch.x, 0.0)) * ACCELERATION_DUE_TO_GRAVITY
        # TODO: on uphills this sometimes sets PERMIT_BRAKING low not considering the creep force
        net_acceleration_request = pcm_accel_cmd + accel_due_to_pitch

        # GVC does not overshoot ego acceleration when starting from stop, but still has a similar delay
        if not self.CP.flags & ToyotaFlags.SECOC.value:
          a_ego_blended = float(np.interp(CS.out.vEgo, [1.0, 2.0], [CS.gvc, CS.out.aEgo]))
        else:
          a_ego_blended = CS.out.aEgo

        # wind down integral when approaching target for step changes and smooth ramps to reduce overshoot
        prev_aego = self.aego.x
        self.aego.update(a_ego_blended)
        j_ego = (self.aego.x - prev_aego) / (DT_CTRL * 3)

        future_t = float(np.interp(CS.out.vEgo, [2., 5.], [0.25, 0.5]))
        a_ego_future = a_ego_blended + j_ego * future_t

        accel_max = max(self.params.ACCEL_MAX, min(ACCEL_MAX_TAKEOFF, actuators.accel))
        self.long_pid.pos_limit = accel_max
        if long_active:
          if dom_defaults.bypass_pid(self.CP):
            self.long_pid.reset()
          else:
            # constantly slowly unwind integral to recover from large temporary errors
            unwind = ACCEL_PID_UNWIND * (8. if (self.prius_longitudinal or dom_defaults.camry_hybrid(self.CP)) and pcm_accel_cmd * self.long_pid.i < 0. else 1.)
            self.long_pid.i -= unwind * float(np.sign(self.long_pid.i))

            error_future = pcm_accel_cmd - a_ego_future

            if not stopping:
              # Toyota's PCM slowly responds to changes in pitch. On change, we amplify our
              # acceleration request to compensate for the undershoot and following overshoot
              pitch_compensation = float(np.clip(math.sin(self.pitch_hp.x) * ACCELERATION_DUE_TO_GRAVITY,
                                                 -MAX_PITCH_COMPENSATION, MAX_PITCH_COMPENSATION))
              pcm_accel_cmd += pitch_compensation

            pcm_accel_cmd = self.long_pid.update(error_future,
                                                 speed=CS.out.vEgo,
                                                 feedforward=(prius_longitudinal.get_prius_feedforward(pcm_accel_cmd, CS.out.vEgo)
                                                              if self.prius_longitudinal else pcm_accel_cmd * .8
                                                              if dom_defaults.camry_hybrid(self.CP) and pcm_accel_cmd > 0 else pcm_accel_cmd),
                                                 freeze_integrator=actuators.longControlState != LongCtrlState.pid)
        else:
          self.long_pid.reset()

        # Along with rate limiting positive jerk above, this greatly improves gas response time
        # Consider the net acceleration request that the PCM should be applying (pitch included)
        net_acceleration_request_min = min(actuators.accel + accel_due_to_pitch, net_acceleration_request)
        self.permit_braking = prius_longitudinal.update_permit_braking(
          self.permit_braking, net_acceleration_request_min, stopping, long_active, CS.out.vEgo, lead)
        if pedal_owner:
          pcm_accel_cmd = dom_defaults.limit_interceptor_pcm_accel(pcm_accel_cmd, actuators.accel, stopping, CS.out.vEgo)
          pcm_accel_cmd = dom_defaults.limit_interceptor_stopping_accel(
            pcm_accel_cmd, actuators.accel, stopping, CS.out.vEgo, hud_control.leadVisible)
        else:
          pcm_accel_cmd = prius_longitudinal.limit_no_lead_cruise_sign_flip(
            pcm_accel_cmd, actuators.accel, stopping, CS.out.vEgo, CS.out.cruiseState.speed, hud_control.leadVisible)
          if self.prius_longitudinal:
            pcm_accel_cmd = prius_longitudinal.limit_prius_stopping_accel(
              pcm_accel_cmd, actuators.accel, stopping, CS.out.vEgo, lead)
        pcm_accel_cmd = float(np.clip(pcm_accel_cmd, self.params.ACCEL_MIN, accel_max))

        if self.brake_hold_active and not uses_toyota_auto_hold_aeb(self.CP):
          pcm_accel_cmd = TOYOTA_AUTO_HOLD_ACCEL
          self.permit_braking = True
          self.standstill_req = True

        if ((bypass_long or rav4h_long or pedal_owner or advert_long or original_prius_long) and not long_active and
            not (self.brake_hold_active and not uses_toyota_auto_hold_aeb(self.CP))):
          pcm_accel_cmd = 0.
        main_accel_cmd = 0. if self.CP.flags & ToyotaFlags.SECOC.value else pcm_accel_cmd
        can_sends.append(toyotacan.create_accel_command(self.packer, main_accel_cmd, pcm_cancel_cmd, self.permit_braking, self.standstill_req, lead,
                                                        CS.acc_type, fcw_alert, self.distance_button,
                                                        reverse_cruise=bool(self.CP.pcmCruise and self.reverse_cruise_input is not None and
                                                                            self.reverse_cruise_input.update())))
        if self.CP.flags & ToyotaFlags.SECOC.value:
          acc_cmd_2 = toyotacan.create_accel_command_2(self.packer, pcm_accel_cmd)
          acc_cmd_2 = add_mac(self.secoc_key,
                              int(CS.secoc_synchronization['TRIP_CNT']),
                              int(CS.secoc_synchronization['RESET_CNT']),
                              self.secoc_acc_message_counter,
                              acc_cmd_2)
          self.secoc_acc_message_counter += 1
          can_sends.append(acc_cmd_2)

        self.accel = pcm_accel_cmd

    else:
      # we can spam can to cancel the system even if we are using lat only control
      if pcm_cancel_cmd:
        if self.CP.flags & ToyotaFlags.UNSUPPORTED_DSU:
          can_sends.append(toyotacan.create_acc_cancel_command(self.packer))
        else:
          can_sends.append(toyotacan.create_accel_command(self.packer, 0, pcm_cancel_cmd, True, False, lead, CS.acc_type, False, self.distance_button))

    # *** hud ui ***
    if self.CP.carFingerprint != CAR.TOYOTA_PRIUS_V:
      # ui mesg is at 1Hz but we send asap if:
      # - there is something to display
      # - there is something to stop displaying
      send_ui = False
      if ((fcw_alert or steer_alert) and not self.alert_active) or \
         (not (fcw_alert or steer_alert) and self.alert_active):
        send_ui = True
        self.alert_active = not self.alert_active
      elif pcm_cancel_cmd:
        # forcing the pcm to disengage causes a bad fault sound so play a good sound instead
        send_ui = True

      if self.frame % 20 == 0 or send_ui:
        can_sends.append(toyotacan.create_ui_command(self.packer, steer_alert, pcm_cancel_cmd, hud_control.leftLaneVisible,
                                                     hud_control.rightLaneVisible, hud_control.leftLaneDepart,
                                                     hud_control.rightLaneDepart, lat_active, CS.lkas_hud))

      if (self.frame % 100 == 0 or send_ui) and self.CP.flags & ToyotaFlags.DISABLE_RADAR.value:
        can_sends.append(toyotacan.create_fcw_command(self.packer, fcw_alert))

    # keep radar disabled
    if self.frame % 20 == 0 and self.CP.flags & ToyotaFlags.DISABLE_RADAR.value:
      can_sends.append(make_tester_present_msg(0x750, 0, 0xF))

    door_command = self.door_locks.update(CS.out)
    if door_command is not None:
      from opendbc.car import CanData
      can_sends.append(CanData(0x750, door_command, 0))

    new_actuators = actuators.as_builder()
    new_actuators.torque = apply_torque / self.params.STEER_MAX
    new_actuators.torqueOutputCan = apply_torque
    new_actuators.steeringAngleDeg = self.last_angle
    new_actuators.accel = self.accel

    if pedal_owner and self.frame % 2 == 0:
      pedal_value = pedal.amount(self.CP, CC, CS, self.accel) if filter_allowed and not self.brake_hold_active else 0.
      can_sends.append(pedal.command(self.packer, pedal_value, self.frame // 2))
    self.frame += 1
    if _projection is not None:
      _projection.finish(new_actuators.torque)
    return new_actuators, can_sends
