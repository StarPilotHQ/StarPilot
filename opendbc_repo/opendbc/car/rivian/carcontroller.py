import numpy as np
from opendbc.car import ACCEL_MAX_TAKEOFF
from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.lateral import apply_driver_steer_torque_limits
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.rivian.riviancan import create_lka_steering, create_longitudinal, create_wheel_touch, create_adas_status
from opendbc.car.rivian.values import CarControllerParams, RivianFlags
from opendbc.car.rivian.toi_controller import TOI_MAX_ANGLE_DEG, ToiController


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.apply_torque_last = 0
    self.packer = CANPacker(dbc_names[Bus.pt])

    self.cancel_frames = 0
    self.base_gen1 = CP.flags == 0
    self.toi_controller = ToiController() if self.base_gen1 else None

  def update(self, CC, CS, now_nanos):
    _projection = self.learning_projection_observer
    if _projection is not None:
      _projection.begin(CC.actuators.torque, self.frame)
    actuators = CC.actuators
    can_sends = []

    if self.base_gen1:
      lat_active = CC.latActive and CS.out.gearShifter == structs.CarState.GearShifter.drive
      torque_request, torque_allowed = self.toi_controller.update(
        lat_active, abs(CS.out.steeringAngleDeg) >= TOI_MAX_ANGLE_DEG,
        CS.toi_fault, CS.toi_active, CS.toi_unavailable,
      )
    else:
      # Preserve current upstream GEN2/unknown-profile behavior verbatim.
      lat_active = torque_request = torque_allowed = CC.latActive
    apply_torque = 0
    steer_max = round(float(np.interp(CS.out.vEgoRaw, CarControllerParams.STEER_MAX_LOOKUP[0],
                                      CarControllerParams.STEER_MAX_LOOKUP[1])))
    if lat_active and torque_allowed:
      new_torque = int(round(CC.actuators.torque * steer_max))
      if _projection is not None:
        _projection.input(new_torque, steer_max, self.apply_torque_last, CS.out.steeringTorque, CarControllerParams, limiter='driver')
      apply_torque = apply_driver_steer_torque_limits(new_torque, self.apply_torque_last,
                                                      CS.out.steeringTorque, CarControllerParams, steer_max)

    # send steering command
    self.apply_torque_last = apply_torque
    can_sends.append(create_lka_steering(self.packer, self.frame, CS.acm_lka_hba_cmd, apply_torque, CC.enabled, torque_request))
    if _projection is not None:
      _projection.emit('rivian.ACM_lkaHbaCmd', apply_torque, torque_request, can_sends[-1])

    if self.frame % 5 == 0 and not (self.CP.flags & RivianFlags.GEN2):
      can_sends.append(create_wheel_touch(self.packer, CS.sccm_wheel_touch, CC.enabled))

    # Longitudinal control
    if self.CP.openpilotLongitudinalControl:
      if self.base_gen1:
        long_active = CC.enabled and CC.longActive and CS.out.gearShifter == structs.CarState.GearShifter.drive
        accel = float(np.clip(actuators.accel, CarControllerParams.ACCEL_MIN, ACCEL_MAX_TAKEOFF)) if long_active and not CS.out.gasPressed else 0.0
      else:
        long_active = CC.enabled
        accel = float(np.clip(actuators.accel, CarControllerParams.ACCEL_MIN, ACCEL_MAX_TAKEOFF))
      can_sends.append(create_longitudinal(self.packer, self.frame, accel, long_active))
    else:
      interface_status = None
      if CC.cruiseControl.cancel:
        # if there is a noEntry, we need to send a status of "available" before the ACM will accept "unavailable"
        # send "available" right away as the VDM itself takes a few frames to acknowledge
        interface_status = 1 if self.cancel_frames < 5 else 0
        self.cancel_frames += 1
      else:
        self.cancel_frames = 0

      for msg in CS.vdm_adas_status:
        can_sends.append(create_adas_status(self.packer, msg, interface_status, cancel=self.base_gen1 and CC.cruiseControl.cancel))

    new_actuators = actuators.as_builder()
    new_actuators.torque = apply_torque / steer_max
    new_actuators.torqueOutputCan = apply_torque

    self.frame += 1
    if _projection is not None:
      _projection.finish(new_actuators.torque)
    return new_actuators, can_sends
