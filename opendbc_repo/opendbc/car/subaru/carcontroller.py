import numpy as np
from opendbc.can import CANPacker
from opendbc.car import Bus, make_tester_present_msg, structs
from opendbc.car.lateral import (apply_driver_steer_torque_limits, apply_std_steer_angle_limits,
                                apply_steer_angle_limits_vm, common_fault_avoidance)
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.subaru import subarucan
from opendbc.car.subaru.jacobw_angle import limit_angle as jacobw_limit_angle
from opendbc.car.subaru.stock_aol import qualified as subaru_aol_qualified
from opendbc.car.subaru.values import CAR, DBC, GLOBAL_ES_ADDR, CanBus, CarControllerParams, SubaruFlags
from opendbc.car.vehicle_model import VehicleModel

# FIXME: These limits aren't exact. The real limit is more than likely over a larger time period and
# involves the total steering angle change rather than rate, but these limits work well for now
MAX_STEER_RATE = 25  # deg/s
MAX_STEER_RATE_FRAMES = 7  # tx control frames needed before torque can be cut
GEN2_ANGLE_PORTS = frozenset((CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_ASCENT_2023, CAR.SUBARU_FORESTER_2022, CAR.SUBARU_OUTBACK_2023))


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    from opendbc.car.subaru.startup import selected, StopStartRequest
    self.stop_start = StopStartRequest() if selected(CP) else None
    from opendbc.car.subaru.avh import selected as avh_selected, AvhStartup
    self.avh_startup = AvhStartup() if avh_selected(CP) else None
    self.apply_torque_last = 0
    self.apply_angle_last = 0.
    self.angle_was_active = False
    self.angle_override_frames = 0
    self.ascent_angle_initialized = False
    self.ascent_es_distance_counter_last = None
    self.ascent_lkas_alert_counter_last = None
    self.angle_driver_override = False
    self.angle_handoff_active = False
    self.VM = VehicleModel(CP) if CP.carFingerprint in GEN2_ANGLE_PORTS else None

    from opendbc.car.subaru.sng import StopAndGo
    self.sng_owner = StopAndGo()
    self.sng_command_ns = 0
    self.sng_sent_sources = {}
    self.sng_pulse_ns = 0
    self.sng_stationary_since_ns = 0
    self.software_cruise_command = 0
    self.software_cruise_command_ns = 0
    self.software_cruise_sent_source = 0
    self.software_cruise_last_frame = -10
    self.software_cruise_last_send_ns = 0
    self.cruise_button_prev = 0
    self.steer_rate_counter = 0

    self.p = CarControllerParams(CP)
    self.packer = CANPacker(DBC[CP.carFingerprint][Bus.pt])

  def angle_active(self, CS, available):
    if not available:
      self.angle_driver_override = False
      self.angle_override_frames = 0
      self.angle_handoff_active = False
      return False
    high, low = (150, 100) if self.CP.carFingerprint == CAR.SUBARU_CROSSTREK_2025 else (200, 150)
    torque = abs(CS.out.steeringTorque)
    if self.angle_driver_override:
      if torque < low:
        self.angle_driver_override = False
    elif torque > high:
      self.angle_override_frames += 1
      if self.angle_override_frames >= 2:
        self.angle_driver_override = True
        self.angle_override_frames = 0
    else:
      self.angle_override_frames = 0
    if self.angle_driver_override:
      self.angle_handoff_active = True
      return False
    if self.angle_handoff_active:
      if abs(CS.out.steeringRateDeg) <= 2.0:
        self.angle_handoff_active = False
      return False
    if not self.angle_was_active and abs(CS.out.steeringRateDeg) > 2.0:
      self.angle_handoff_active = True
      return False
    return True

  def _ascent_lkas_alert(self, CC, CS):
    if self.CP.carFingerprint != CAR.SUBARU_ASCENT_2023 or not self.CP.flags & SubaruFlags.ASCENT_LKAS_ALERT:
      return None
    dat = CS.es_lkas_alert_dat
    lateral_active = self.angle_was_active and CC.latActive and (CC.enabled or subaru_aol_qualified(self.CP, marked_only=True))
    msg = subarucan.create_ascent_lkas_alert(dat, lateral_active, CC.hudControl.visualAlert)
    if msg is None or (dat[1] & 0xf) == self.ascent_lkas_alert_counter_last:
      return None
    self.ascent_lkas_alert_counter_last = dat[1] & 0xf
    return msg

  def update(self, CC, CS, now_nanos):
    _projection = self.learning_projection_observer
    if _projection is not None:
      _projection.begin(CC.actuators.torque, self.frame)
    actuators = CC.actuators
    hud_control = CC.hudControl
    pcm_cancel_cmd = CC.cruiseControl.cancel

    can_sends = []
    if self.stop_start is not None:
      request = self.stop_start.update(CC, CS, now_nanos / 1e9, now_nanos=now_nanos)
      if request is not None:
        can_sends.append(request)

    if self.avh_startup is not None:
      now = now_nanos / 1e9
      source = CS.avh_sources
      safe = (CS.out.canValid and not CS.out.canTimeout and not source.blocked and source.fresh(now) and
              CS.out.standstill and not CS.out.gasPressed and not CS.out.steerFaultPermanent and
              not CS.out.steerFaultTemporary and not CS.out.accFaulted and
              CS.out.gearShifter == structs.CarState.GearShifter.park)
      can_sends.extend(self.avh_startup.update(now, source.frames, self.avh_startup.configured(now), safe,
                                             CC.enabled or CC.latActive or CC.longActive))

    # *** steering ***
    if (self.frame % self.p.STEER_STEP) == 0:
      if self.CP.carFingerprint in GEN2_ANGLE_PORTS:
        independent_lateral = subaru_aol_qualified(self.CP, marked_only=True)
        # Match Dom's independent-lateral envelope during large manual turns and parking speeds.
        aol_motion_ok = CC.enabled or (CS.out.vEgoRaw > 0.44704 and abs(CS.out.steeringAngleDeg) < 120.)
        angle_available = (CC.latActive and aol_motion_ok and CS.out.cruiseState.available and
                           (independent_lateral or CC.enabled and CS.out.cruiseState.enabled and not CS.out.brakePressed) and
                           CS.out.gearShifter == structs.CarState.GearShifter.drive and
                           not CS.out.standstill and not CS.out.steerFaultTemporary and not CS.out.steerFaultPermanent)
        angle_active = self.angle_active(CS, angle_available)
        if self.CP.carFingerprint == CAR.SUBARU_ASCENT_2023 and not self.ascent_angle_initialized:
          self.apply_angle_last = CS.out.steeringAngleDeg
          self.ascent_angle_initialized = True
          angle_active = False
        if self.CP.carFingerprint in (CAR.SUBARU_LEGACY_2025, CAR.SUBARU_ASCENT_2023, CAR.SUBARU_OUTBACK_2023):
          apply_angle = apply_std_steer_angle_limits(actuators.steeringAngleDeg, self.apply_angle_last,
                                                     CS.out.vEgoRaw, CS.out.steeringAngleDeg, angle_active,
                                                     self.p.FIXED_ANGLE_LIMITS)
        elif self.CP.carFingerprint == CAR.SUBARU_CROSSTREK_2025:
          assert self.VM is not None
          apply_angle, angle_active = jacobw_limit_angle(
            actuators.steeringAngleDeg, self.apply_angle_last, CS.out.vEgoRaw,
            CS.out.steeringAngleDeg, angle_active, self.p, self.VM)
        else:
          assert self.VM is not None
          apply_angle = apply_steer_angle_limits_vm(actuators.steeringAngleDeg, self.apply_angle_last,
                                                    CS.out.vEgoRaw, CS.out.steeringAngleDeg, angle_active,
                                                    self.p, self.VM)
        self.apply_angle_last = apply_angle
        self.angle_was_active = angle_active
        angle_bus = CanBus.main
        can_sends.append(subarucan.create_steering_control_angle(self.packer, apply_angle, angle_active, angle_bus))
      else:
        apply_torque = int(round(actuators.torque * self.p.STEER_MAX))

        # limits due to driver torque

        new_torque = int(round(apply_torque))
        if _projection is not None:
          _projection.input(new_torque, self.p.STEER_MAX, self.apply_torque_last, CS.out.steeringTorque, self.p, limiter='driver')
        apply_torque = apply_driver_steer_torque_limits(new_torque, self.apply_torque_last, CS.out.steeringTorque, self.p)

        if not CC.latActive:
          apply_torque = 0

        if self.CP.flags & SubaruFlags.PREGLOBAL:
          can_sends.append(subarucan.create_preglobal_steering_control(self.packer, self.frame // self.p.STEER_STEP, apply_torque, CC.latActive))
          if _projection is not None:
            _projection.emit('subaru.PREGLOBAL_ES_LKAS', apply_torque, CC.latActive, can_sends[-1])
        else:
          apply_steer_req = CC.latActive

          if self.CP.flags & SubaruFlags.STEER_RATE_LIMITED:
            # Steering rate fault prevention
            self.steer_rate_counter, apply_steer_req = \
              common_fault_avoidance(abs(CS.out.steeringRateDeg) > MAX_STEER_RATE, apply_steer_req,
                                     self.steer_rate_counter, MAX_STEER_RATE_FRAMES)

          can_sends.append(subarucan.create_steering_control(self.packer, apply_torque, apply_steer_req))
          if _projection is not None:
            _projection.emit('subaru.ES_LKAS', apply_torque, apply_steer_req, can_sends[-1])

        self.apply_torque_last = apply_torque

    # *** longitudinal ***

    if CC.longActive:
      apply_throttle = int(round(np.interp(actuators.accel, CarControllerParams.THROTTLE_LOOKUP_BP, CarControllerParams.THROTTLE_LOOKUP_V)))
      apply_rpm = int(round(np.interp(actuators.accel, CarControllerParams.RPM_LOOKUP_BP, CarControllerParams.RPM_LOOKUP_V)))
      apply_brake = int(round(np.interp(actuators.accel, CarControllerParams.BRAKE_LOOKUP_BP, CarControllerParams.BRAKE_LOOKUP_V)))

      # limit min and max values
      cruise_throttle = np.clip(apply_throttle, CarControllerParams.THROTTLE_MIN, CarControllerParams.THROTTLE_MAX)
      cruise_rpm = np.clip(apply_rpm, CarControllerParams.RPM_MIN, CarControllerParams.RPM_MAX)
      cruise_brake = np.clip(apply_brake, CarControllerParams.BRAKE_MIN, CarControllerParams.BRAKE_MAX)
    else:
      cruise_throttle = CarControllerParams.THROTTLE_INACTIVE
      cruise_rpm = CarControllerParams.RPM_MIN
      cruise_brake = CarControllerParams.BRAKE_MIN

    from opendbc.car.subaru.software_cruise_sender import send as send_software_cruise
    can_sends.extend(send_software_cruise(self, CC, CS, now_nanos))
    from opendbc.car.subaru.sng_sender import send as send_sng
    can_sends.extend(send_sng(self, CC, CS, now_nanos))

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

    else:
      if self.frame % 10 == 0:
        can_sends.append(subarucan.create_es_dashstatus(self.packer, self.frame // 10, CS.es_dashstatus_msg, CC.enabled,
                                                        self.CP.openpilotLongitudinalControl, CC.longActive, hud_control.leadVisible))

        lkas_active = self.angle_was_active if self.CP.carFingerprint in GEN2_ANGLE_PORTS else CC.enabled
        lkas_state_msg = CS.es_lkas_state_msg
        if self.CP.carFingerprint in GEN2_ANGLE_PORTS or subaru_aol_qualified(self.CP, marked_only=True):
          if self.CP.carFingerprint not in GEN2_ANGLE_PORTS:
            lkas_active = CC.latActive
          lkas_state_msg = dict(lkas_state_msg, LKAS_ACTIVE=int(lkas_active))
        can_sends.append(subarucan.create_es_lkas_state(self.packer, self.frame // 10, lkas_state_msg, lkas_active, hud_control.visualAlert,
                                                        hud_control.leftLaneVisible, hud_control.rightLaneVisible,
                                                        hud_control.leftLaneDepart, hud_control.rightLaneDepart))

        if self.CP.flags & SubaruFlags.SEND_INFOTAINMENT:
          can_sends.append(subarucan.create_es_infotainment(self.packer, self.frame // 10, CS.es_infotainment_msg, hud_control.visualAlert))

      if self.CP.openpilotLongitudinalControl:
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
            bus = CanBus.alt if self.CP.flags & SubaruFlags.GLOBAL_GEN2 else CanBus.main
            can_sends.append(subarucan.create_es_distance(self.packer, CS.es_distance_msg["COUNTER"] + 1, CS.es_distance_msg, bus, pcm_cancel_cmd))

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
    new_actuators.torque = self.apply_torque_last / self.p.STEER_MAX
    new_actuators.torqueOutputCan = self.apply_torque_last
    if self.CP.carFingerprint in GEN2_ANGLE_PORTS:
      new_actuators.steeringAngleDeg = self.apply_angle_last

    self.frame += 1
    if _projection is not None:
      _projection.finish(new_actuators.torque)
    return new_actuators, can_sends
