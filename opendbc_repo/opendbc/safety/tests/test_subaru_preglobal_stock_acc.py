"""Release steering admission for the 2015–17 Outback with stock EyeSight cruise."""
import unittest

from opendbc.car.structs import CarParams
from opendbc.car.subaru.values import SubaruSafetyFlags
from opendbc.safety.tests.libsafety import libsafety_py


class TestOutbackPreglobalStockAcc(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.addCleanup(self.safety.set_safety_hooks, CarParams.SafetyModel.noOutput, 0)
    self.safety.init_tests()
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.subaruPreglobal,
                                               SubaruSafetyFlags.PREGLOBAL_OUTBACK_STOCK_ACC), 0)

  def test_stock_cruise_forwarding_and_steering_relay(self):
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x161), 0)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x164), -1)
    self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x161, 0, bytes(8))))
    self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x164, 2, bytes(8))))
    self.assertTrue(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x164, 0, bytes(8))))
    self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x164, 0, bytes(7) + b"\x01")))
    self.safety.set_relay_malfunction(True)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x161), -1)
    self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x164, 0, bytes(8))))

  def test_stuck_throttle_counter_withdraws_controls(self):
    throttle = libsafety_py.make_CANPacket(0x140, 0, bytes(8))
    for counter in range(16):
      throttle.data[1] = counter
      self.assertTrue(self.safety.safety_rx_hook(throttle))
    self.safety.set_controls_allowed(True)
    for _ in range(16):
      self.safety.safety_rx_hook(throttle)
    self.assertFalse(self.safety.get_controls_allowed())
