import unittest

from opendbc.car import structs
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.safety.tests.libsafety import libsafety_py


class TestHyundaiKonaCcncFeedback(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.addCleanup(self.safety.set_alternative_experience, 0)
    self.addCleanup(self.safety.set_relay_malfunction, False)

  def mode(self, word, alternative=32):
    self.safety.init_tests()
    self.safety.set_alternative_experience(alternative)
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, word), 0)

  @staticmethod
  def packet(address, bus, body):
    body = bytearray(body)
    body[:2] = hkg_can_fd_checksum(address, None, body).to_bytes(2, 'little')
    packet = libsafety_py.make_CANPacket(address, bus, body)
    packet[0].fd = 1
    return packet

  def forward(self, packet):
    output = libsafety_py.new_CANPacket()
    replaced = libsafety_py.ffi.new('bool *')
    bus = self.safety.safety_fwd_output_hook(packet, output, replaced)
    return bus, bool(replaced[0]), output

  def debug_mode(self):
    if self.safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0:
      self.skipTest('Kona feedback profile is DEBUG only')
    self.mode(0x1C0C)

  def test_feedback_is_exact_profile_and_debug_only(self):
    feedback = self.packet(0xEA, 0, bytes(24))
    for word, alternative, forward in ((0x1C0C, 0, -1), (0x0C0C, 32, 2), (0x0008, 0, 2)):
      with self.subTest(word=word, alternative=alternative):
        self.mode(word, alternative)
        self.assertFalse(self.safety.safety_uses_forward_transform(feedback))
        self.assertEqual(self.forward(feedback)[:2], (forward, False))
    release = libsafety_py.ffi.dlopen(libsafety_py._build_libsafety(release=True))
    release.set_alternative_experience(32)
    self.assertEqual(release.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, 0x1C0C), 0)
    self.assertFalse(release.safety_uses_forward_transform(feedback))
    self.assertEqual(release.safety_fwd_packet_hook(feedback), -1)

  def test_eps_feedback_preserves_faults_and_never_changes_vehicle_side_input(self):
    self.debug_mode()
    for warning_byte, toi_byte, modify in ((0, 0, True), (0, 1, True), (0, 3, False),
                                           (1, 1, False), (0, 5, False), (0, 0x10, False), (0, 0x40, False)):
      with self.subTest(warning_byte=warning_byte, toi_byte=toi_byte):
        body = bytearray(24)
        body[2:4] = bytes((0xAA, warning_byte))
        body[6:9] = bytes((toi_byte, 0x12, 0xA3))
        packet = self.packet(0xEA, 0, body)
        original = bytes(packet[0].data[0:24])
        self.assertTrue(self.safety.safety_uses_forward_transform(packet))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(0xEA, 2, original)))
        bus, replaced, output = self.forward(packet)
        self.assertEqual((bus, replaced), (2, modify))
        self.assertEqual(bytes(packet[0].data[0:24]), original)
        if modify:
          body[6] = 4
          expected = self.packet(0xEA, 0, body)
          self.assertEqual(bytes(output[0].data[0:24]), bytes(expected[0].data[0:24]))
    packet[0].data[0] ^= 1
    self.assertFalse(self.forward(packet)[1])
    self.safety.set_relay_malfunction(True)
    self.assertEqual(self.forward(packet)[:2], (-1, False))

  def test_host_steering_is_independent_of_camera_counter_and_respects_authority(self):
    self.debug_mode()
    self.safety.set_timer(1_000_000)
    for status in self.safety.rx_status:
      status.msg_seen = True
      status.valid_checksum = True
      status.valid_quality_flag = True
      status.last_timestamp = 1_000_000

    def lfa(bus, counter, torque, request):
      body = bytearray.fromhex('0000000280000800000a000000900000')
      raw_torque = 1024 + torque
      body[2] = counter
      body[5] = (raw_torque & 127) << 1
      body[6] = (raw_torque >> 7) | (0x10 if request else 0)
      return self.packet(0x12A, bus, body)

    self.safety.set_controls_allowed(True)
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(3)
    self.assertFalse(self.safety.safety_tx_hook(lfa(0, 254, 5, True)))
    camera = lfa(2, 81, 0, False)
    self.assertEqual(self.forward(camera)[:2], (-1, False))
    self.safety.safety_rx_hook(camera)
    self.assertTrue(self.safety.safety_tx_hook(lfa(0, 254, 5, True)))
    for stamp, count, torque in ((1_010_000, 255, 10), (1_020_000, 0, 15)):
      self.safety.set_timer(stamp)
      self.assertTrue(self.safety.safety_tx_hook(lfa(0, count, torque, True)))
      self.assertFalse(self.safety.safety_tx_hook(lfa(0, count, torque, True)))
    corrupt = lfa(0, 1, 20, True)
    corrupt[0].data[0] ^= 1
    self.assertFalse(self.safety.safety_tx_hook(corrupt))
    self.assertFalse(self.safety.safety_tx_hook(lfa(0, 1, 500, True)))
    self.safety.set_timer(1_050_001)
    self.assertFalse(self.safety.safety_tx_hook(lfa(0, 1, 5, True)))
    self.safety.safety_rx_hook(lfa(2, 150, 0, False))
    self.safety.set_controls_allowed(False)
    self.safety.aol_set_host_request(0)
    self.assertFalse(self.safety.safety_tx_hook(lfa(0, 1, 5, True)))
    self.assertTrue(self.safety.safety_tx_hook(lfa(0, 1, 0, False)))
    self.safety.set_relay_malfunction(True)
    self.assertFalse(self.safety.safety_tx_hook(lfa(0, 2, 0, False)))
