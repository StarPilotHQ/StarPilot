import math
import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car.hyundai.ioniq6_media import Ioniq6MediaButtons, MEDIA_ADDRESS, MEDIA_MAX_GAP_NS


START = 10_000_000_000


def packet(stamp, *, mode=False, custom=False, bus=1):
  payload = bytearray(8)
  payload[2] = 0x40 if mode else 0
  payload[5] = 0x10 if custom else 0
  return (stamp, [(MEDIA_ADDRESS, bytes(payload), bus)])


class TestIoniq6Media(unittest.TestCase):
  def test_dbc_matches_independent_physical_bits(self):
    packer = CANPacker('hyundai_canfd_generated')
    parser = CANParser('hyundai_canfd_generated', [('STEERING_WHEEL_MEDIA_BUTTONS', math.nan)], 1)
    source = Ioniq6MediaButtons(1)
    for index, (mode, custom) in enumerate(((False, False), (True, False), (False, True), (True, True))):
      expected = packet(START + index * 200_000_000, mode=mode, custom=custom)
      frame = packer.make_can_msg('STEERING_WHEEL_MEDIA_BUTTONS', 1, {'MODE_BUTTON': mode, 'CUSTOM_BUTTON': custom})
      self.assertEqual(tuple(frame), expected[1][0])
      parser.update([expected])
      self.assertEqual(parser.vl['STEERING_WHEEL_MEDIA_BUTTONS']['MODE_BUTTON'], int(mode))
      self.assertEqual(parser.vl['STEERING_WHEEL_MEDIA_BUTTONS']['CUSTOM_BUTTON'], int(custom))
      sample = source.update([expected]).samples[0]
      self.assertEqual((sample.mode_held, sample.custom_held), (mode, custom))

  def test_keeps_packet_order_and_never_repeats_cached_samples(self):
    source = Ioniq6MediaButtons(1)
    observed = source.update([packet(START), packet(START + 100_000_000, mode=True), packet(START + 200_000_000)])
    self.assertTrue(observed.valid)
    self.assertEqual([s.mode_held for s in observed.samples], [False, True, False])
    self.assertEqual([s.source_boot_ns for s in observed.samples], [START, START + 100_000_000, START + 200_000_000])
    cached = source.update([(START + 250_000_000, [(0x123, b'other', 1)])])
    self.assertTrue(cached.valid)
    self.assertEqual(cached.samples, ())
    self.assertEqual(cached.source_epoch, observed.source_epoch)

  def test_wrong_bus_and_echo_cannot_create_or_refresh_source(self):
    for bus in (0, 2, 3, 129):
      with self.subTest(bus=bus):
        source = Ioniq6MediaButtons(1)
        self.assertFalse(source.update([packet(START, mode=True, bus=bus)]).valid)
        self.assertTrue(source.update([packet(START + 1)]).valid)
        result = source.update([packet(START + MEDIA_MAX_GAP_NS + 2, mode=True, bus=bus)])
        self.assertFalse(result.valid)
        self.assertEqual(result.samples, ())
    for bus in (True, -1, 6, 129):
      with self.assertRaises(ValueError):
        Ioniq6MediaButtons(bus)

  def test_short_long_and_duplicate_packets_revoke_whole_batch(self):
    for payload in (b'', b'\0' * 5, b'\0' * 7, b'\0' * 9, b'\0' * 64):
      with self.subTest(length=len(payload)):
        source = Ioniq6MediaButtons(1)
        initial = source.update([packet(START)])
        observed = source.update([packet(START + 10, mode=True), (START + 20, [(MEDIA_ADDRESS, payload, 1)])])
        self.assertFalse(observed.valid)
        self.assertEqual(observed.samples, ())
        self.assertGreater(observed.source_epoch, initial.source_epoch)
    source = Ioniq6MediaButtons(1)
    source.update([packet(START)])
    self.assertFalse(source.update([packet(START, mode=True)]).valid)
    same_stamp = source.update([packet(START + 10, mode=True), packet(START + 10)])
    self.assertFalse(same_stamp.valid)
    self.assertEqual(same_stamp.samples, ())

  def test_gap_missing_can_and_clock_rewind_start_new_epoch(self):
    cases = ([packet(START + MEDIA_MAX_GAP_NS + 1, mode=True)],
             [(START + MEDIA_MAX_GAP_NS + 1, [])], [packet(START - 1)], [])
    for next_packets in cases:
      with self.subTest(packets=next_packets):
        source = Ioniq6MediaButtons(1)
        first = source.update([packet(START, mode=True)])
        invalid = source.update(next_packets)
        self.assertFalse(invalid.valid)
        self.assertGreater(invalid.source_epoch, first.source_epoch)
        # Recovery still supplies only observations; the gesture owner must
        # observe neutral in the new epoch before accepting a held button.
        recovered = source.update([packet(START + 1_000_000_000, mode=True)])
        self.assertTrue(recovered.valid)
        self.assertEqual(recovered.source_epoch, invalid.source_epoch)
    source = Ioniq6MediaButtons(1)
    source.update([packet(START)])
    self.assertTrue(source.update([packet(START + MEDIA_MAX_GAP_NS)]).valid)


if __name__ == '__main__':
  unittest.main()
