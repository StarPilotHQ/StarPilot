"""Polestar modulo-15 receiver counters retain physical-source watchdogs."""
import unittest

from opendbc.safety.tests import test_volvo_cma as cma


class TestPolestarCounters(unittest.TestCase):
  PARAM = 5
  setUp = cma.TestVolvoCMASafety.setUp
  reset = cma.TestVolvoCMASafety.reset
  rx = cma.TestVolvoCMASafety.rx
  tx = cma.TestVolvoCMASafety.tx
  base_sources = cma.TestVolvoCMASafety.sources
  feed = cma.TestVolvoCMASafety.feed
  relay = cma.TestVolvoCMASafety.relay
  lca_command = cma.TestVolvoCMASafety.lca_command

  def sources(self, sample=0, one=None, two=None, **kwargs):
    messages = self.base_sources(sample=sample, **kwargs)
    result = []
    for address, payload, bus in messages:
      if address == 0x69:
        data = bytearray(payload)
        data[1] = (data[1] & 0xF0) | (sample * 2 % 15 if one is None else one)
        data[5] = (data[5] & 0xF0) | (sample * 4 % 15 if two is None else two)
        payload = cma.checksummed((address, data, bus))[1]
      result.append((address, payload, bus))
    return result

  def test_nominal_and_jittered_counters_wrap_without_losing_permission(self):
    self.feed()
    one = two = 0
    for sample in range(1, 46):
      self.safety.set_timer(10000 + sample * 20000)
      one = (one + (1, 2, 3)[sample % 3]) % 15
      two = (two + (3, 4, 5)[sample % 3]) % 15
      self.feed(sample=sample, one=one, two=two)
      self.assertTrue(self.tx(self.lca_command()))

  def test_each_frozen_or_jumped_clock_withdraws_permission(self):
    for clock, delta in (('one', 0), ('one', 7), ('two', 0), ('two', 8)):
      with self.subTest(clock=clock, delta=delta):
        self.reset()
        self.feed()
        rejected = False
        for sample in range(1, 12):
          self.safety.set_timer(10000 + sample * 20000)
          for message in self.sources(sample=sample, **{clock: sample * delta % 15}):
            accepted = self.rx(message)
            if message[0] == 0x69:
              rejected |= not accepted
            else:
              self.assertTrue(accepted)
          self.safety.safety_tick()
        self.assertTrue(rejected)
        self.assertFalse(self.safety.safety_config_valid())
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertFalse(self.tx(self.lca_command()))

  def test_invalid_nibbles_and_checksum_fail_closed(self):
    for one, two, corrupt in ((15, 0, False), (0, 15, False), (0, 0, True)):
      with self.subTest(one=one, two=two, corrupt=corrupt):
        self.reset()
        self.feed()
        message = next(m for m in self.sources(one=one, two=two) if m[0] == 0x69)
        if corrupt:
          data = bytearray(message[1])
          data[2] ^= 1
          message = message[0], bytes(data), message[2]
        self.assertFalse(self.rx(message))
        self.safety.safety_tick()
        self.assertFalse(self.safety.safety_config_valid())
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertFalse(self.tx(self.lca_command()))

  def test_healthy_steps_clear_secondary_counter_error_tolerance(self):
    self.feed()
    # A single duplicate is within the shared tolerance, then healthy frames
    # must recover it rather than accumulate isolated errors indefinitely.
    for sample in range(1, 30):
      self.safety.set_timer(10000 + sample * 20000)
      self.feed(sample=sample)
      if sample % 3 == 0:
        message = next(m for m in self.sources(sample=sample) if m[0] == 0x69)
        self.assertTrue(self.rx(message))
    self.assertTrue(self.safety.safety_config_valid())
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertTrue(self.tx(self.lca_command()))

  def unknown_pscm_status(self):
    return 0x17, bytes.fromhex('d400a0858000ffff'), 2

  def test_unknown_status_relays_without_becoming_control_evidence(self):
    for cruise in (False, True):
      with self.subTest(cruise=cruise):
        self.reset()
        incoming = self.feed(cruise=cruise)
        status = self.unknown_pscm_status()
        self.assertFalse(self.rx(status))
        self.assertFalse(self.safety.get_controls_allowed())
        self.safety.safety_tick()
        self.assertFalse(self.safety.safety_config_valid())
        self.assertTrue(self.tx(self.relay(status)))
        self.assertFalse(self.tx(self.relay(incoming[0x17])))
        self.assertFalse(self.tx(self.lca_command()))
        self.safety.set_controls_allowed(True)
        self.assertTrue(self.tx(self.relay(status)))
        self.assertFalse(self.tx(self.lca_command()))
        for address in cma.RELAY_BUSES:
          if address != 0x17:
            self.assertTrue(self.tx(self.relay(incoming[address])))
        self.assertEqual(self.safety.safety_fwd_hook(2, 0x17), -1)

  def test_unknown_status_requires_exact_native_bytes(self):
    self.feed()
    status = self.unknown_pscm_status()
    self.assertFalse(self.rx(status))
    for bit in range(64):
      data = bytearray(status[1])
      data[bit // 8] ^= 1 << (bit % 8)
      self.assertFalse(self.tx((0x17, data, 0)), bit)
    self.assertTrue(self.tx(self.relay(status)))

  def test_unknown_status_source_identity_cannot_be_forged(self):
    status = self.unknown_pscm_status()
    for field in ('returned', 'rejected', 'extended', 'fd'):
      with self.subTest(field=field):
        self.reset()
        self.feed(cruise=False)
        packet = cma.libsafety_py.make_CANPacket(status[0], 2, status[1])
        setattr(packet[0], field, 1)
        self.safety.safety_rx_hook(packet)
        self.assertFalse(self.tx(self.relay(status)))
    for bus in (0, 1, 3, 4, 5, 6, 7):
      self.reset()
      self.feed(cruise=False)
      self.rx((0x17, status[1], bus))
      self.assertFalse(self.tx(self.relay(status)))
    for length in (*range(8), 12):
      self.reset()
      self.feed(cruise=False)
      self.rx((0x17, (status[1] + bytes(4))[:length], 2))
      self.assertFalse(self.tx(self.relay(status)))

  def test_unknown_status_tx_cannot_seed_or_refresh_source(self):
    self.feed(cruise=False)
    status = self.unknown_pscm_status()
    self.assertFalse(self.tx(self.relay(status)))
    self.assertFalse(self.rx(status))
    self.safety.set_timer(110000)
    self.assertTrue(self.tx(self.relay(status)))
    self.safety.set_timer(110001)
    self.assertFalse(self.tx(self.relay(status)))
    self.assertFalse(self.tx(self.relay(status)))

  def test_unknown_status_original_age_survives_timer_wrap(self):
    self.feed(cruise=False)
    start = 0xFFFFFF00
    self.safety.set_timer(start)
    status = self.unknown_pscm_status()
    self.assertFalse(self.rx(status))
    for age, accepted in ((90000, True), (100000, True), (100001, False)):
      self.safety.set_timer((start + age) & 0xFFFFFFFF)
      self.assertEqual(bool(self.tx(self.relay(status))), accepted)

  def test_status_preserves_four_frame_transport_tolerance(self):
    for unknown in (False, True):
      with self.subTest(unknown=unknown):
        self.reset()
        incoming = self.feed(cruise=False)
        source = self.unknown_pscm_status() if unknown else incoming[0x17]
        frames = [(0x17, source[1][:-1] + bytes([i]), 2) for i in range(5)]
        for i, frame in enumerate(frames):
          self.safety.set_timer(20000 + i * 10000)
          self.assertEqual(bool(self.rx(frame)), not unknown)
          for retained in frames[max(0, i - 3):i + 1]:
            self.assertTrue(self.tx(self.relay(retained)))
        self.assertFalse(self.tx(self.relay(frames[0])))
        # A new native arrival never refreshes another slot's original age.
        self.safety.set_timer(130000)
        self.assertTrue(self.tx(self.relay(frames[1])))
        self.safety.set_timer(130001)
        self.assertFalse(self.tx(self.relay(frames[1])))
        self.assertTrue(self.tx(self.relay(frames[2])))

  def test_native_status_transition_flushes_old_transport_evidence(self):
    incoming = self.feed(cruise=False)
    healthy = incoming[0x17]
    first = self.unknown_pscm_status()
    second = first[0], first[1][:-1] + b'\xfe', first[2]
    restored = healthy[0], healthy[1][:-1] + b'\x01', healthy[2]
    self.assertFalse(self.rx(first))
    self.assertTrue(self.tx(self.relay(first)))
    self.assertFalse(self.tx(self.relay(healthy)))
    self.assertFalse(self.rx(second))
    self.assertTrue(self.tx(self.relay(first)))
    self.assertTrue(self.tx(self.relay(second)))
    self.assertTrue(self.rx(restored))
    for stale in (healthy, first, second):
      self.assertFalse(self.tx(self.relay(stale)))
    self.assertTrue(self.tx(self.relay(restored)))
    self.assertFalse(self.safety.get_controls_allowed())

  def test_each_native_validation_class_change_flushes_transport_evidence(self):
    healthy = self.feed(cruise=False)[0x17]
    # Keep the high nibble at 8 and change one validation boolean at a time:
    # (checksum, quality): TT -> FT -> FF -> TF -> TT.
    bad_checksum = bytearray(healthy[1])
    bad_checksum[0] ^= 1
    bad_both = bytearray(bad_checksum)
    bad_both[1] |= 8  # This quality bit is unconstrained by the checksum.
    bad_quality = bytearray(healthy[1])
    bad_quality[1] |= 8
    frames = [healthy, (0x17, bad_checksum, 2), (0x17, bad_both, 2),
              (0x17, bad_quality, 2), (0x17, healthy[1][:-1] + b'\x01', 2)]
    for i, frame in enumerate(frames):
      self.assertEqual(bool(self.rx(frame)), i in (0, 4))
      self.assertTrue(self.tx(self.relay(frame)))
      for stale in frames[:i]:
        self.assertFalse(self.tx(self.relay(stale)))
      self.assertFalse(self.safety.get_controls_allowed())

  def test_unknown_status_cache_does_not_cross_mode_reset(self):
    self.feed(cruise=False)
    status = self.unknown_pscm_status()
    self.assertFalse(self.rx(status))
    self.assertTrue(self.tx(self.relay(status)))
    self.reset()
    self.assertFalse(self.tx(self.relay(status)))
    self.feed(cruise=False)
    self.assertFalse(self.tx(self.relay(status)))

  def test_unknown_status_does_not_expand_other_profiles(self):
    status = self.unknown_pscm_status()
    for profile in (0, 1):
      self.assertEqual(self.safety.set_safety_hooks(36, profile), 0)
      self.safety.init_tests()
      self.safety.set_timer(10000)
      self.assertFalse(self.rx(status))
      self.assertFalse(self.tx(self.relay(status)))
    self.assertNotEqual(self.safety.set_safety_hooks(36, 4), 0)

  def test_unknown_status_remains_blocked_on_relay_malfunction(self):
    self.feed(cruise=False)
    status = self.unknown_pscm_status()
    self.assertFalse(self.rx(status))
    self.assertTrue(self.tx(self.relay(status)))
    self.rx((0x58, bytes(8), 2))
    self.assertTrue(self.safety.get_relay_malfunction())
    self.assertFalse(self.tx(self.relay(status)))

  def test_unknown_status_tx_identity_matches_native_framing(self):
    self.feed(cruise=False)
    status = self.unknown_pscm_status()
    self.assertFalse(self.rx(status))
    for field in ('returned', 'rejected', 'extended', 'fd'):
      packet = cma.libsafety_py.make_CANPacket(0x17, 0, status[1])
      setattr(packet[0], field, 1)
      self.assertFalse(self.safety.safety_tx_hook(packet), field)
      self.assertTrue(self.tx(self.relay(status)))
    for bus in range(1, 8):
      self.assertFalse(self.tx((0x17, status[1], bus)))
    for length in (*range(8), 12):
      self.assertFalse(self.tx((0x17, (status[1] + bytes(4))[:length], 0)))
    self.assertFalse(self.tx((0x16, status[1], 0)))
    self.assertTrue(self.tx(self.relay(status)))

  def test_modified_status_is_rejected_until_controller_returns_to_stock(self):
    self.feed()
    status = self.unknown_pscm_status()
    stock = {
      'CHECKSUM_1': 0xD4, 'LCA_ENABLED_ECHO': 0, 'SIG1_BYTE_1_HI_NIBBLE': 0,
      'SIG1_REPLICA_BYTE_2_LO_NIBLE': 0, 'NEW_SIGNAL_2': 10, 'CHECKSUM_2': 0x85,
      'BYTE_4_5': 0x8000, 'BYTE_6': 0xFF, 'BYTE_7': 0xFF,
    }
    self.assertFalse(self.rx(status))
    self.assertFalse(self.safety.get_controls_allowed())
    modified = cma.cma_can.create_pscm_related_message(self.mid, True, False, stock, 1)
    self.assertNotEqual(modified[1], status[1])
    self.assertFalse(self.tx(modified))
    unchanged = cma.cma_can.create_pscm_related_message(self.mid, False, False, stock, 1)
    self.assertEqual(unchanged[1], status[1])
    self.assertTrue(self.tx(unchanged))
    self.assertFalse(self.safety.get_controls_allowed())
