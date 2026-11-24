import unittest

from opendbc.car import structs
from opendbc.safety.tests.libsafety import libsafety_py


class TestHyundaiKonaCcncHandoff(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.addCleanup(self.safety.set_alternative_experience, 0)
    self.addCleanup(self.safety.set_relay_malfunction, False)

  def mode(self, word, alternative=32):
    self.safety.init_tests()
    self.safety.set_alternative_experience(alternative)
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, word), 0)

  def packet(self, bus, body):
    packet = libsafety_py.make_CANPacket(0x12A, bus, bytes.fromhex(body))
    packet[0].fd = 1
    return packet

  def test_handoff_is_debug_only_and_requires_exact_alternative_experience(self):
    debug = self.safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
    camera = self.packet(2, 'a6770a1880001800000a000000900000')
    for experience in ((0,) if debug else (0, 32)):
      self.mode(0x1C0C, experience)
      self.assertEqual(self.safety.safety_fwd_packet_hook(camera), -1)
      self.assertFalse(self.safety.safety_tx_hook(self.packet(0, 'a6770a1880001800000a000000900000')))
      self.assertEqual(self.safety.safety_fwd_hook(2, 0x123), -1)

  def test_handoff_requires_current_camera_and_one_output_owner(self):
    if self.safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0:
      self.skipTest('Kona factory handoff is DEBUG only')
    camera = self.packet(2, 'a6770a1880001800000a000000900000')
    self.mode(0x0C0C)
    self.assertEqual(self.safety.safety_fwd_packet_hook(camera), -1)

    self.mode(0x1C0C)
    self.safety.set_timer(1_000_000)
    self.assertEqual(self.safety.safety_fwd_packet_hook(camera), -1)
    for status in self.safety.rx_status:
      status.msg_seen = True
      status.valid_checksum = True
      status.valid_quality_flag = True
      status.last_timestamp = 1_000_000

    self.assertEqual(self.safety.safety_fwd_packet_hook(camera), 0)
    self.assertEqual(self.safety.safety_fwd_packet_hook(camera), -1)
    corrupt = self.packet(2, 'a6770a1881001800000a000000900000')
    self.assertEqual(self.safety.safety_fwd_packet_hook(corrupt), -1)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(0, 'a6770a1880001800000a000000900000')))
    warning = self.packet(2, '784d1400000108f0000a000000640000')
    self.assertEqual(self.safety.safety_fwd_packet_hook(warning), 0)
    self.safety.set_controls_allowed(True)
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(3)
    following = self.packet(2, '110d1600000108f0000a000000640000')
    host = self.packet(0, 'a009150280001800000a000000900000')
    self.safety.set_timer(1_009_999)
    self.assertFalse(self.safety.safety_tx_hook(host))  # Require the matching camera counter first.
    camera_match = self.packet(2, '5ce51500000108f0000a000000640000')
    self.assertEqual(self.safety.safety_fwd_packet_hook(camera_match), -1)
    self.assertFalse(self.safety.safety_tx_hook(host))
    self.safety.set_timer(1_010_000)
    self.assertTrue(self.safety.safety_tx_hook(host))
    self.assertFalse(self.safety.safety_tx_hook(host))

    self.safety.set_controls_allowed(False)
    self.safety.aol_set_host_request(0)
    self.safety.set_timer(1_019_999)
    self.assertEqual(self.safety.safety_fwd_packet_hook(following), -1)
    self.safety.set_timer(1_020_000)
    self.assertEqual(self.safety.safety_fwd_packet_hook(following), 0)
    self.assertEqual(self.safety.safety_fwd_packet_hook(following), -1)
    self.safety.set_relay_malfunction(True)
    self.assertEqual(self.safety.safety_fwd_packet_hook(following), -1)
    self.assertFalse(self.safety.safety_tx_hook(host))
