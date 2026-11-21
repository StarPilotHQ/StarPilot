import unittest

from opendbc.car import structs
from opendbc.safety.tests.libsafety import libsafety_py

DEFAULT_ALERT = bytes.fromhex("ce02000104000000")


class TestSubaruAscentAlert(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety

  def mode(self, flags):
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.subaru, flags)
    self.safety.init_tests()

  def packet(self, data=DEFAULT_ALERT, bus=0):
    return libsafety_py.make_CANPacket(0x3C4, bus, data)

  def test_exact_parameter_bus_checksum_length_and_forwarding(self):
    for flags in (0, 1, 0x11, 0xB1, 0x800, 0x811, 0x891, 0x8B3, 0x9B1):
      self.mode(flags)
      self.assertFalse(self.safety.safety_tx_hook(self.packet()), hex(flags))
      self.assertEqual(self.safety.safety_fwd_hook(2, 0x3C4), 0)
    for flags in (0x8B1, 0x91, 401, 1169, 1425):
      self.mode(flags)
      self.assertTrue(self.safety.safety_tx_hook(self.packet()), hex(flags))
      self.assertEqual(self.safety.safety_fwd_hook(2, 0x3C4), -1)
      self.assertEqual(self.safety.safety_fwd_hook(0, 0x3C4), 2)
    self.mode(0x8B1)
    for bus in (1, 2):
      self.assertFalse(self.safety.safety_tx_hook(self.packet(bus=bus)))
    for data in (bytes.fromhex("0002000104000000"), bytes(7), bytes(12)):
      self.assertFalse(self.safety.safety_tx_hook(self.packet(data)))

  def test_stock_main_frame_and_relay_malfunction_revoke(self):
    self.mode(0x8B1)
    self.safety.safety_rx_hook(self.packet(bus=2))
    self.assertFalse(self.safety.get_relay_malfunction())
    self.safety.safety_rx_hook(self.packet())
    self.assertTrue(self.safety.get_relay_malfunction())
    self.assertFalse(self.safety.safety_tx_hook(self.packet()))
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x3C4), -1)
