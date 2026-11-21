#!/usr/bin/env python3
"""
Safety tests for Volvo C1 (V40), safetyParam 2. The mode lives at
``opendbc/safety/modes/volvo_c1.h`` and is selected by ``volvo_dispatch.h``.

C1 uses the V40's legacy CAN layout and its own safety allowlist, exercised
through the generic ``CarSafetyTest`` harness.
"""

import unittest

from opendbc.car.volvo.interface import SAFETY_VOLVO
from opendbc.car.volvo.values import VolvoSafetyFlags
from opendbc.car.volvo.volvocan import create_c1_checksum, create_c1_steering_control
from opendbc.safety.tests.libsafety import libsafety_py
import opendbc.safety.tests.common as common
from opendbc.safety.tests.common import CANPackerSafety

# Must match VOLVO_FLAG_C1 in opendbc/safety/modes/volvo_dispatch.h
VOLVO_FLAG_C1 = VolvoSafetyFlags.C1.value

# Bus layout (must match VOLVO_C1_PT_BUS / VOLVO_C1_CAM_BUS in volvo_c1.h)
VOLVO_MAIN_BUS = 0
VOLVO_PARTY_BUS = 2


class TestVolvoC1(common.CarSafetyTest, common.AngleSteeringSafetyTest):
  TX_MSGS = [[0xD0, VOLVO_MAIN_BUS], [0x125, VOLVO_PARTY_BUS], [0x10, VOLVO_MAIN_BUS]]
  RELAY_MALFUNCTION_ADDRS = {
    VOLVO_MAIN_BUS: (0xD0,),
    VOLVO_PARTY_BUS: (0x125,),
  }
  FWD_BLACKLISTED_ADDRS = {
    VOLVO_MAIN_BUS: [0x125],
    VOLVO_PARTY_BUS: [0xD0],
  }
  STANDSTILL_THRESHOLD = 0.1
  GAS_PRESSED_THRESHOLD = 5.0

  STEER_ANGLE_MAX = 359.9
  STEER_ANGLE_TEST_MAX = 350.0
  DEG_TO_CAN = 1 / 0.04395
  ANGLE_RATE_BP = [7.0, 17.0, 36.0]
  ANGLE_RATE_UP = [2.0, 0.25, 0.1]
  ANGLE_RATE_DOWN = [2.0, 0.25, 0.1]

  def setUp(self):
    self.packer = CANPackerSafety("volvo_v40_2017_pt")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(SAFETY_VOLVO, VOLVO_FLAG_C1)
    self.safety.init_tests()

  def _angle_cmd_msg(self, angle: float, enabled: bool, increment_timer: bool = True):
    values = {
      "SET_X_E3": 0xE3,
      "SET_X_B4": 0xB4,
      "SET_X_08": 0x08,
      "LKAAngleReq": angle,
      "LKASteerDirection": 3 if enabled else 0,
      "TrqLim": 0,
      "SET_X_25": 0x25,
      "SET_X_02": 0x02,
    }

    def fix_checksum(msg):
      address, data, bus = msg
      data = bytearray(data)
      data[6] = create_c1_checksum(data)
      return address, data, bus

    return self.packer.make_can_msg_safety("FSM1", VOLVO_MAIN_BUS, values, fix_checksum)

  def _angle_meas_msg(self, angle: float):
    return self.packer.make_can_msg_safety(
      "PSCM1", VOLVO_MAIN_BUS, {"SteeringAngleServo": angle})

  def _speed_msg(self, speed):
    return self.packer.make_can_msg_safety(
      "VehicleSpeed1", VOLVO_MAIN_BUS, {"VehicleSpeed": speed * 3.6})

  def _speed_msg_2(self, speed):
    return None

  def _user_brake_msg(self, brake):
    return self.packer.make_can_msg_safety(
      "PedalandBrake", VOLVO_MAIN_BUS, {"BrakePedalActive2": bool(brake)})

  def _user_gas_msg(self, gas):
    return self.packer.make_can_msg_safety(
      "PedalandBrake", VOLVO_MAIN_BUS, {"AccPedal": gas})

  def _pcm_status_msg(self, enable):
    return self.packer.make_can_msg_safety(
      "FSM0", VOLVO_PARTY_BUS, {"ACCStatusActive": bool(enable)})

  def test_cancel_button_only(self):
    allowed = self.packer.make_can_msg_safety(
      "CCButtons", VOLVO_MAIN_BUS, {"ACCStopBtn": 1})
    self.assertTrue(self._tx(allowed))

    for signal in ("ACCOnOffBtn", "ACCSetBtn", "ACCResumeBtn", "ACCMinusBtn",
                   "TimeGapIncreaseBtn", "TimeGapDecreaseBtn"):
      msg = self.packer.make_can_msg_safety("CCButtons", VOLVO_MAIN_BUS, {signal: 1})
      self.assertFalse(self._tx(msg), signal)

  def test_pscm_relay_cannot_invent_angle(self):
    for _ in range(common.MAX_SAMPLE_VALS):
      self._rx(self._angle_meas_msg(10))
    valid = self.packer.make_can_msg_safety(
      "PSCM1", VOLVO_PARTY_BUS, {"SteeringAngleServo": 10, "LKATorque": 0})
    invalid = self.packer.make_can_msg_safety(
      "PSCM1", VOLVO_PARTY_BUS, {"SteeringAngleServo": 20, "LKATorque": 0})
    self.assertTrue(self._tx(valid))
    self.assertFalse(self._tx(invalid))

  def test_pscm_relay_requires_zero_lka_torque(self):
    # The controller always relays LKATorque 0 (raw 2000); any other torque is rejected.
    for _ in range(common.MAX_SAMPLE_VALS):
      self._rx(self._angle_meas_msg(10))
    for torque, allowed in ((0, True), (1, False), (-1, False)):
      msg = self.packer.make_can_msg_safety(
        "PSCM1", VOLVO_PARTY_BUS, {"SteeringAngleServo": 10, "LKATorque": torque})
      self.assertEqual(allowed, self._tx(msg), torque)

  def test_pscm_relay_preserves_full_lock_angle(self):
    for angle in (-720, 500):
      for _ in range(common.MAX_SAMPLE_VALS):
        self._rx(self._angle_meas_msg(angle))
      relayed = self.packer.make_can_msg_safety(
        "PSCM1", VOLVO_PARTY_BUS, {"SteeringAngleServo": angle, "LKATorque": 0})
      self.assertTrue(self._tx(relayed), angle)

  def test_steering_static_fields_and_checksum(self):
    self.safety.set_controls_allowed(True)
    self._reset_angle_measurement(0)
    self._reset_speed_measurement(10)
    self._set_prev_desired_angle(0)
    valid = self._angle_cmd_msg(0, True)
    self.assertTrue(self._tx(valid))

    for byte_index in (0, 1, 2, 3, 4, 6, 7):
      invalid = self._angle_cmd_msg(0, True)
      invalid[0].data[byte_index] ^= 0x4 if byte_index in (4, 7) else 0x1
      self.assertFalse(self._tx(invalid), byte_index)

  def test_controller_steering_message_is_allowed(self):
    self.safety.set_controls_allowed(True)
    self._reset_angle_measurement(0)
    self._reset_speed_measurement(10)
    self._set_prev_desired_angle(0)
    address, data, bus = create_c1_steering_control(self.packer, 0, True)
    self.assertTrue(self._tx(libsafety_py.make_CANPacket(address, bus, data)))


if __name__ == "__main__":
  unittest.main()
