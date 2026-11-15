import time
import numpy as np
from opendbc.car import ACCEL_MAX_TAKEOFF
from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.lateral import apply_steer_angle_limits_vm
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.tesla.teslacan import TeslaCAN
from opendbc.car.tesla.pedal_handover import HandoverSample
from opendbc.car.tesla.preap.stock import PreAPStockCarController
from opendbc.car.tesla.hw1 import HW1CarController
from opendbc.car.tesla.values import CAR, CarControllerParams, TeslaSafetyFlags
from opendbc.car.tesla.coop_steering import CooperativeSteeringController
from opendbc.car.vehicle_model import VehicleModel
from opendbc.car.tesla.speed_sync import SpeedSynchronizer, EXPERIMENTAL_TEST_BUILD, SYNC_FLAG, sync_controller


def get_safety_CP():
  # We use the TESLA_MODEL_Y platform for lateral limiting to match safety
  # A Model 3 at 40 m/s using the Model Y limits sees a <0.3% difference in max angle (from curvature factor)
  from opendbc.car.tesla.interface import CarInterface
  return CarInterface.get_non_essential_params("TESLA_MODEL_Y")


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.speed_sync = (SpeedSynchronizer(experimental_test=EXPERIMENTAL_TEST_BUILD) if CP.carFingerprint in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y) and
                       CP.openpilotLongitudinalControl and any(s.safetyParam & SYNC_FLAG for s in CP.safetyConfigs) else None)
    if CP.carFingerprint == CAR.TESLA_MODEL_S_PREAP:
      self.preap = PreAPStockCarController(dbc_names, CP)
      return
    if CP.carFingerprint == CAR.TESLA_MODEL_S_HW1:
      self.hw1 = HW1CarController(dbc_names, CP)
      return
    self.apply_angle_last = 0
    self.coop_steer = CooperativeSteeringController()
    self.coop_enabled = CP.carFingerprint in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y) and bool(CP.safetyConfigs[0].safetyParam & TeslaSafetyFlags.COOP_STEERING)
    self.steering_limit_info = {}
    self.packer = CANPacker(dbc_names[Bus.party])
    self.tesla_can = TeslaCAN(CP, self.packer)

    # Vehicle model used for lateral limiting
    self.VM = VehicleModel(get_safety_CP())

  def update(self, CC, CS, now_nanos):
    if self.CP.carFingerprint == CAR.TESLA_MODEL_S_PREAP:
      CS.preap.out = CS.out
      CS.preap.preap_lateral_authorized = CS.preap_lateral_authorized
      return self.preap.update(CC, CS.preap, now_nanos)
    if self.CP.carFingerprint == CAR.TESLA_MODEL_S_HW1:
      return self.hw1.update(CC, CS, now_nanos)
    actuators = CC.actuators
    can_sends = []

    # Tesla EPS enforces disabling steering on heavy lateral override force.
    # When enabling in a tight curve, we wait until user reduces steering force to start steering.
    # Canceling is done on rising edge and is handled generically with CC.cruiseControl.cancel
    lat_active = CC.latActive and (not CS.out.steeringDisengage if self.coop_enabled else CS.hands_on_level < 3)
    if not lat_active:
      self.steering_limit_info = {}

    if self.frame % 2 == 0:
      # Angular rate limit based on speed
      self.apply_angle_last = apply_steer_angle_limits_vm(actuators.steeringAngleDeg, self.apply_angle_last, CS.out.vEgoRaw, CS.out.steeringAngleDeg,
                                                          lat_active, CarControllerParams, self.VM)

      command_angle, lat_active = self.coop_steer.update(self.apply_angle_last, lat_active, self.coop_enabled, CS, self.VM)
      self.command_angle = command_angle
      if self.coop_enabled and lat_active:
        self.steering_limit_info = {
          "valid": True, "monoTime": now_nanos,
          "modelLimitErrorDeg": abs(actuators.steeringAngleDeg - self.apply_angle_last),
          "resumeLimitErrorDeg": self.coop_steer.resume_limit_error_deg,
          "cooperativeLimitErrorDeg": self.coop_steer.cooperative_limit_error_deg,
          "cooperativeOffsetDeg": self.coop_steer.cooperative_offset_deg,
          "combinedLimitErrorDeg": abs(actuators.steeringAngleDeg + self.coop_steer.cooperative_offset_deg - command_angle),
        }
      can_sends.append(self.tesla_can.create_steering_control(command_angle, lat_active))

    if self.frame % 10 == 0:
      can_sends.append(self.tesla_can.create_steering_allowed())

    # Longitudinal control
    if self.CP.openpilotLongitudinalControl:
      if self.frame % 4 == 0:
        state = 13 if CC.cruiseControl.cancel else 4  # 4=ACC_ON, 13=ACC_CANCEL_GENERIC_SILENT
        accel = float(np.clip(actuators.accel, CarControllerParams.ACCEL_MIN, ACCEL_MAX_TAKEOFF))
        cntr = (self.frame // 4) % 8
        handover = None
        observed_ns = getattr(CS, "pedal_handover_observed_ns", 0)
        if self.CP.carFingerprint in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y) and observed_ns > 0:
          handover = HandoverSample(
            time=now_nanos * 1e-9, gas=CS.out.gasPressed, enabled=CC.enabled, long_active=CC.longActive,
            override=CS.pedal_handover_override, di_age=(now_nanos - observed_ns) * 1e-9,
            valid=CS.out.canValid and CS.pedal_handover_native_ok and not CS.out.canTimeout,
            brake=CS.out.brakePressed, aeb=CS.out.stockAeb, cancel=CC.cruiseControl.cancel,
            fault=CS.out.accFaulted or CS.out.stockFcw or CS.out.espActive or CS.out.vehicleSensorsInvalid or CS.out.carNotReady,
            speed=CS.out.vEgo, native_speed=CS.out.cruiseState.speed,
            selected_speed=CS.out.vCruise / 3.6, accel=accel,
          )
        can_sends.append(self.tesla_can.create_longitudinal_command(state, accel, cntr, CS.out.vEgo, CC.longActive,
          frame=self.frame if self.CP.carFingerprint in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y) else None, gas_pressed=CS.out.gasPressed, handover=handover))

    else:
      # Increment counter so cancel is prioritized even without openpilot longitudinal
      if CC.cruiseControl.cancel:
        cntr = (CS.das_control["DAS_controlCounter"] + 1) % 8
        can_sends.append(self.tesla_can.create_longitudinal_command(13, 0, cntr, CS.out.vEgo, False,
          frame=self.frame if self.CP.carFingerprint in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y) else None))

    # Speed sync: append-only mirror; never changes the selected target or actuators.
    if self.speed_sync is not None:
      can_sends.extend(sync_controller(self, CC, CS, time.clock_gettime_ns(time.CLOCK_BOOTTIME)))

    # TODO: HUD control
    new_actuators = actuators.as_builder()
    new_actuators.steeringAngleDeg = getattr(self, "command_angle", self.apply_angle_last)

    self.frame += 1
    return new_actuators, can_sends
