#!/usr/bin/env python3
import unittest

from opendbc.car.structs import CarParams
from opendbc.car.mazda.values import MazdaSafetyFlags
import opendbc.safety.tests.common as common
from opendbc.safety.tests.common import make_msg


class TestMazdaSafety(common.CarSafetyTest, common.DriverTorqueSteeringSafetyTest):

  DBC = "mazda_2017"
  SAFETY_MODEL = CarParams.SafetyModel.mazda

  TX_MSGS = [[0x243, 0], [0x09d, 0], [0x440, 0]]
  STANDSTILL_THRESHOLD = .1
  RELAY_MALFUNCTION_ADDRS = {0: (0x243, 0x440)}
  FWD_BLACKLISTED_ADDRS = {2: [0x243, 0x440]}

  MAX_RATE_UP = 10
  MAX_RATE_DOWN = 25
  MAX_TORQUE_LOOKUP = [0], [800]

  MAX_RT_DELTA = 300

  DRIVER_TORQUE_ALLOWANCE = 15
  DRIVER_TORQUE_FACTOR = 1

  # Mazda actually does not set any bit when requesting torque
  NO_STEER_REQ_BIT = True

  SAFETY_PARAM = MazdaSafetyFlags.GEN1

  def _torque_meas_msg(self, torque):
    values = {"STEER_TORQUE_MOTOR": torque}
    return self.packer.make_can_msg_safety("STEER_TORQUE", 0, values)

  def _torque_driver_msg(self, torque):
    values = {"STEER_TORQUE_SENSOR": torque}
    return self.packer.make_can_msg_safety("STEER_TORQUE", 0, values)

  def _torque_cmd_msg(self, torque, steer_req=1):
    values = {"LKAS_REQUEST": torque}
    return self.packer.make_can_msg_safety("CAM_LKAS", 0, values)

  def _speed_msg(self, speed):
    values = {"SPEED": speed}
    return self.packer.make_can_msg_safety("ENGINE_DATA", 0, values)

  def _user_brake_msg(self, brake):
    values = {"BRAKE_ON": brake}
    return self.packer.make_can_msg_safety("PEDALS", 0, values)

  def _user_gas_msg(self, gas):
    values = {"PEDAL_GAS": gas}
    return self.packer.make_can_msg_safety("ENGINE_DATA", 0, values)

  def _pcm_status_msg(self, enable):
    values = {"CRZ_ACTIVE": enable}
    return self.packer.make_can_msg_safety("CRZ_CTRL", 0, values)

  def _button_msg(self, resume=False, cancel=False):
    values = {
      "CAN_OFF": cancel,
      "CAN_OFF_INV": (cancel + 1) % 2,
      "RES": resume,
      "RES_INV": (resume + 1) % 2,
    }
    return self.packer.make_can_msg_safety("CRZ_BTNS", 0, values)

  def test_buttons(self):
    # only cancel allows while controls not allowed
    self.safety.set_controls_allowed(0)
    self.assertTrue(self._tx(self._button_msg(cancel=True)))
    self.assertFalse(self._tx(self._button_msg(resume=True)))

    # do not block resume if we are engaged already
    self.safety.set_controls_allowed(1)
    self.assertTrue(self._tx(self._button_msg(cancel=True)))
    self.assertTrue(self._tx(self._button_msg(resume=True)))


class TestMazdaIgnition(common.SafetyTestBase):
  SAFETY_MODEL = None

  TX_MSGS: list = []

  def _msg(self, byte0):
    return make_msg(0, 0x9E, dat=bytes([byte0]) + b"\x00" * 7)

  # 0x9E byte 0 high 3 bits == 6 (0xC0)
  def test_ignition_on(self):
    self.safety.ignition_can_hook(self._msg(0xC0))
    self.assertTrue(self.safety.get_ignition_can())

  def test_ignition_off(self):
    self.safety.ignition_can_hook(self._msg(0xC0))
    self.assertTrue(self.safety.get_ignition_can())
    self.safety.ignition_can_hook(self._msg(0x20))
    self.assertFalse(self.safety.get_ignition_can())


class TestMazdaTorqueInterceptorSafety(TestMazdaSafety):
  """Gen1 with the original Torque Interceptor: driver torque comes from the interceptor on bus 1."""
  TX_MSGS = [[0x243, 0], [0x249, 1], [0x09d, 0], [0x440, 0]]
  SAFETY_PARAM = MazdaSafetyFlags.GEN1 | MazdaSafetyFlags.TORQUE_INTERCEPTOR

  def _torque_driver_msg(self, torque):
    values = {"TI_TORQUE_SENSOR": torque}
    return self.packer.make_can_msg_safety("TI_FEEDBACK", 1, values)


class TestMazda2019Safety(common.CarSafetyTest, common.DriverTorqueSteeringSafetyTest):
  """Gen2 (2019+ Mazda 3, CX-30, CX-50) with Torque Interceptor 2."""
  TX_MSGS = [[0x249, 1], [0x220, 2]]
  STANDSTILL_THRESHOLD = .1
  RELAY_MALFUNCTION_ADDRS = {2: (0x220,)}
  FWD_BLACKLISTED_ADDRS = {0: [0x220]}

  MAX_RATE_UP = 45
  MAX_RATE_DOWN = 80
  MAX_TORQUE_LOOKUP = [0], [8000]
  MAX_RT_DELTA = 1688
  DRIVER_TORQUE_ALLOWANCE = 1400
  DRIVER_TORQUE_FACTOR = 1
  NO_STEER_REQ_BIT = True

  DBC = "mazda_2019"
  SAFETY_MODEL = CarParams.SafetyModel.mazda
  SAFETY_PARAM = MazdaSafetyFlags.GEN2

  def _torque_driver_msg(self, torque):
    return self.packer.make_can_msg_safety("TI_FEEDBACK", 1, {"STEER_TORQUE_SENSOR": torque})

  def _torque_cmd_msg(self, torque, steer_req=1):
    return self.packer.make_can_msg_safety("EPS_LKAS", 1, {"LKAS_REQUEST": int(torque) & 0xFFFF})

  def _speed_msg(self, speed):
    return self.packer.make_can_msg_safety("SPEED", 2, {"SPEED": speed})

  def _user_brake_msg(self, brake):
    return self.packer.make_can_msg_safety("BRAKE_PEDAL_SLOW", 0, {"BRAKE_PEDAL_PRESSED": brake})

  def _user_gas_msg(self, gas):
    return self.packer.make_can_msg_safety("ENGINE_DATA", 2, {"PEDAL_GAS": gas})

  def _pcm_status_msg(self, enable):
    return self.packer.make_can_msg_safety("CRUZE_STATE", 0, {"CRZ_STATE": 3 if enable else 1})

  def test_steer_tx_needs_controls(self):
    self.safety.set_controls_allowed(0)
    self.assertFalse(self._tx(self._torque_cmd_msg(100)))
    self.assertTrue(self._tx(self._torque_cmd_msg(0)))


if __name__ == "__main__":
  unittest.main()
