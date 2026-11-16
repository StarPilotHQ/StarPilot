from opendbc.car import get_safety_config, structs
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.gwm.carcontroller import CarController
from opendbc.car.gwm.carstate import CarState
from opendbc.car.gwm.radar_interface import RadarInterface
from opendbc.car.gwm.values import GwmFlags


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "gwm"
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.gwm)]

    ret.dashcamOnly = False

    ret.steerActuatorDelay = 0.3
    ret.steerLimitTimer = 0.4

    ret.steerControlType = structs.CarParams.SteerControlType.torque
    CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    ret.radarUnavailable = True

    ret.alphaLongitudinalAvailable = True
    if alpha_long:
      ret.openpilotLongitudinalControl = True
      ret.safetyConfigs[-1].safetyParam |= GwmFlags.LONG_CONTROL.value

      # Stop & Go: enabled so openpilot holds at standstill and resumes.
      # The GWM H6 GT has no native S&G - resume is handled in carcontroller
      # via an AP_ENABLE_COMMAND pulse when the planner wants to start moving.
      ret.autoResumeSng = True

      ret.longitudinalActuatorDelay = 0.25

      # stopAccel: accel command sent while holding at standstill (m/s^2).
      # More negative = firmer hold. -0.75 is conservative; tune down to
      # -1.0 if the car rolls on slopes.
      # NOTE (rollback 2026-06-28): there is a recorded rollback event on a
      # slope when resuming from a stop. Do not treat this value as validated
      # until the uphill resume logic has been reviewed.
      ret.stopAccel = -0.75

      # Pure-integral controller, as in Sato's original port.
      # No kp on purpose: proportional gain couples every speed-measurement
      # jitter straight into the gas/brake command, which on this car felt as
      # constant pulsing (routes 0000005e/60). The integrator changes the
      # command smoothly and converges to whatever bias holds the set speed.
      # The old "stuck 20 km/h under set" failure was the gas map deadzone
      # (fixed in gwmcan with a single through-zero line), not the missing kp.
      ret.longitudinalTuning.kiBP = [0.]
      ret.longitudinalTuning.kiV = [0.4]

    return ret
