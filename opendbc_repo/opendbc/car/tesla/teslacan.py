from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.tesla.values import CANBUS, CarControllerParams
from opendbc.car.tesla.pedal_handover import PedalHandover


class TeslaCAN:
  def __init__(self, CP, packer):
    self.CP = CP
    self.packer = packer
    self.gas_release_frame = 0
    self.pedal_handover = PedalHandover()

  def create_steering_control(self, angle, enabled):
    values = {
      "DAS_steeringAngleRequest": -angle,
      "DAS_steeringHapticRequest": 0,
      "DAS_steeringControlType": 1 if enabled else 0,  # ANGLE_CONTROL
    }

    return self.packer.make_can_msg("DAS_steeringControl", CANBUS.party, values)

  def create_longitudinal_command(self, acc_state, accel, counter, v_ego, active, *, frame=None, gas_pressed=False, handover=None):
    set_speed = min(max(v_ego + accel, 0) * CV.MS_TO_KPH, 400)

    # Restore the previous PR3249 one-second, symmetric pedal ramp.
    # Frame is the 100 Hz controller clock, not the 25 Hz CAN message counter.
    # Calls outside the Model3/Y owner retain the existing fixed-bound contract.
    jerk = CarControllerParams.JERK_LIMIT_MAX
    if frame is not None:
      if gas_pressed:
        self.gas_release_frame = frame
      jerk *= min(1.0, max(0.0, (frame - self.gas_release_frame) / 100.0))

    values = {
      "DAS_setSpeed": set_speed,
      "DAS_accState": acc_state,
      "DAS_aebEvent": 0,
      "DAS_jerkMin": -jerk,
      "DAS_jerkMax": jerk,
      "DAS_accelMin": accel,
      "DAS_accelMax": max(accel, 0),
      "DAS_controlCounter": counter,
    }
    if handover is None:
      self.pedal_handover.reset()
    else:
      values = self.pedal_handover.update(handover, values)
    return self.packer.make_can_msg("DAS_control", CANBUS.party, values)

  def create_steering_allowed(self):
    values = {
      "APS_eacAllow": 1,
    }

    return self.packer.make_can_msg("APS_eacMonitor", CANBUS.party, values)


def tesla_checksum(address: int, sig, d: bytearray) -> int:
  checksum = (address & 0xFF) + ((address >> 8) & 0xFF)
  checksum_byte = sig.start_bit // 8
  for i in range(len(d)):
    if i != checksum_byte:
      checksum += d[i]
  return checksum & 0xFF
