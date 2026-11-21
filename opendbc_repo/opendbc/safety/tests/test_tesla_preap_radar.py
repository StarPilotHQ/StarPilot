import unittest
from opendbc.safety.tests.native_callback import assert_native_callback_finishes

from opendbc.safety.tests.common import make_msg
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.libsafety.libsafety_py import new_CANPacket
from opendbc.safety.tests import test_tesla_preap_stock as stock_fixture


class TestTeslaPreapRadar(unittest.TestCase):
  SOURCES = {0x45: (8, (0x219,)), 0x108: (8, (0x109,)), 0x145: (8, (0x149,)),
             0x20A: (8, (0x159,)), 0x308: (8, (0x209,)), 0x30A: (8, (0x2D9,)),
             0x405: (8, (0x2B9,)), 0x398: (8, (0x2A9,)), 0xE: (8, (0x199,)),
             0x115: (6, (0x129, 0x1A9)), 0x118: (6, (0x119, 0x169))}

  def test_native_gateway_rx_callbacks_finish(self):
    for parameter in (2, 6):
      for address in (0x45, 0x398):
        with self.subTest(parameter=parameter, address=address):
          packet = self.source(address)[0]
          assert_native_callback_finishes(self, 39, parameter, address, 0, bytes(packet.data[0:8]))

  def test_native_gateway_rejects_host_tx_without_ticket(self):
    packet = self.source(0x45)[0]
    for parameter in (2, 6):
      with self.subTest(parameter=parameter):
        assert_native_callback_finishes(self, 39, parameter, 0x219, 1, bytes(packet.data[0:8]), receive=False)

  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.safety.init_tests()
    self.safety.set_alternative_experience(32)
    self.safety.set_safety_hooks(39, 2)
    self.safety.set_timer(1_000_000)
    self.safety.reset_recorded_can()

  def tearDown(self):
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(19, 0)

  def source(self, address, counter=0):
    length = self.SOURCES[address][0]
    data = bytearray(length)
    if address == 0x45:
      data[0] = 64
      data[6] = counter << 4
    if address in (0x45, 0xE):
      data[-1] = stock_fixture.TestTeslaPreapStock.crc(data[:-1])
    return make_msg(0, address, dat=bytes(data))

  def recorded(self):
    packets = []
    for index in range(self.safety.get_recorded_can_count()):
      packet = new_CANPacket()
      self.assertTrue(self.safety.get_recorded_can(index, packet))
      packets.append(packet)
    return packets

  def test_all_sources_exact_outputs_and_no_host_injection(self):
    for parameter in (2, 6):
      for address, (_, destinations) in self.SOURCES.items():
        with self.subTest(parameter=parameter, address=address):
          self.setUp()
          self.safety.set_safety_hooks(39, parameter)
          self.safety.safety_rx_hook(self.source(address))
          packets = self.recorded()
          self.assertEqual(tuple(p[0].addr for p in packets), destinations)
          for packet in packets:
            self.assertEqual(packet[0].bus, 1)
            self.assertFalse(packet[0].fd)
            self.assertFalse(self.safety.safety_tx_hook(packet))
          self.safety.safety_tick()
          self.assertEqual(self.safety.get_recorded_can_count(), len(destinations))

  def test_wrong_bus_length_fd_and_crc_cannot_mint_ticket(self):
    for address in self.SOURCES:
      for fault in ('bus', 'length', 'fd', 'extended', 'returned', 'rejected'):
        with self.subTest(address=address, fault=fault):
          self.setUp()
          packet = self.source(address)
          if fault == 'length':
            packet = make_msg(0, address, dat=bytes(self.SOURCES[address][0] - 1))
          elif fault == 'bus':
            packet[0].bus = 1
          else:
            setattr(packet[0], fault, 1)
          self.safety.safety_rx_hook(packet)
          self.assertEqual(self.safety.get_recorded_can_count(), 0)
    for address in (0x45, 0xE):
      self.setUp()
      packet = self.source(address)
      packet[0].data[7] ^= 1
      self.safety.safety_rx_hook(packet)
      self.assertEqual(self.safety.get_recorded_can_count(), 0)

  def test_physical_counter_and_accepted_echo_are_not_gateway_sources(self):
    fixture = stock_fixture.TestTeslaPreapStock()
    fixture.setUp()
    self.safety.set_safety_hooks(39, 2)
    fixture.arm()
    self.safety.reset_recorded_can()
    duplicate = fixture.stalk(2)
    self.safety.safety_rx_hook(duplicate)
    self.assertEqual(self.safety.get_recorded_can_count(), 0)
    fixture.arm()
    echo = fixture.stalk(1, (fixture.counter + 1) % 16)
    self.assertTrue(self.safety.safety_tx_hook(echo))
    self.safety.reset_recorded_can()
    self.safety.safety_rx_hook(echo)
    self.assertEqual(self.safety.get_recorded_can_count(), 0)
    fixture.feed()
    self.assertIn(0x219, [p[0].addr for p in self.recorded()])

  def test_relay_failure_consumes_ticket_and_fresh_source_can_retry(self):
    self.safety.set_relay_malfunction(True)
    self.safety.safety_rx_hook(self.source(0x145))
    self.assertEqual(self.safety.get_recorded_can_count(), 0)
    self.safety.set_relay_malfunction(False)
    self.assertFalse(self.safety.safety_tx_hook(make_msg(1, 0x149, dat=bytes(8))))
    self.safety.safety_rx_hook(self.source(0x145))
    self.assertEqual(self.safety.get_recorded_can_count(), 1)
    self.assertFalse(self.safety.safety_tx_hook(self.recorded()[0]))

  def test_exact_all_16bit_profiles_and_stock_gateway_invariant(self):
    for parameter in range(65536):
      self.safety.set_safety_hooks(39, parameter)
      self.safety.reset_recorded_can()
      self.safety.safety_rx_hook(self.source(0x145))
      self.assertEqual(self.safety.get_recorded_can_count(), int(parameter in (2, 6)), parameter)

  def test_mount_and_counter_transforms_have_independent_literal_bytes(self):
    for parameter, expected in ((2, bytes.fromhex('40050000000000f0')),
                                (6, bytes.fromhex('4005000010000000'))):
      self.setUp()
      self.safety.set_safety_hooks(39, parameter)
      self.safety.safety_rx_hook(self.source(0x398))
      packet = self.recorded()[0]
      self.assertEqual(bytes(packet[0].data[0:8]), expected[:8])
    self.setUp()
    self.safety.safety_rx_hook(make_msg(0, 0x115, dat=bytes.fromhex('00000000a000')))
    packet = self.recorded()[1]
    self.assertEqual(bytes(packet[0].data[0:5]), bytes.fromhex('00000ca0e4'))

  def test_exact_alternative_experience_and_no_timer_replay(self):
    for parameter in (2, 6):
      for experience in (0, 1, 16, 32, 33):
        self.safety.init_tests()
        self.safety.set_alternative_experience(experience)
        self.safety.set_safety_hooks(39, parameter)
        self.safety.reset_recorded_can()
        self.safety.set_timer(1_000_000)
        self.safety.safety_rx_hook(self.source(0x145))
        self.assertEqual(self.safety.get_recorded_can_count(), int(experience in (0, 32)))
        self.safety.set_timer(3_000_000)
        self.safety.safety_tick()
        self.assertEqual(self.safety.get_recorded_can_count(), int(experience in (0, 32)))

  def test_full_transport_queue_cannot_leave_reusable_ticket(self):
    for _ in range(32):
      self.safety.safety_rx_hook(self.source(0x145))
    self.assertEqual(self.safety.get_recorded_can_count(), 32)
    self.safety.safety_rx_hook(self.source(0x145))
    self.assertEqual(self.safety.get_recorded_can_count(), 32)
    self.assertFalse(self.safety.safety_tx_hook(make_msg(1, 0x149, dat=bytes(8))))
    self.safety.reset_recorded_can()
    self.safety.safety_rx_hook(self.source(0x145))
    self.assertEqual(self.safety.get_recorded_can_count(), 1)
    self.assertFalse(self.safety.safety_tx_hook(self.recorded()[0]))

  def test_invalid_steering_status_is_rewritten_with_literal_crc(self):
    for parameter in (2, 6):
      with self.subTest(parameter=parameter):
        self.setUp()
        self.safety.set_safety_hooks(39, parameter)
        data = bytearray.fromhex('cdabffff78563400')
        data[7] = stock_fixture.TestTeslaPreapStock.crc(data[:7])
        self.safety.safety_rx_hook(make_msg(0, 0xE, dat=bytes(data)))
        packets = self.recorded()
        self.assertEqual(len(packets), 1)
        self.assertEqual((packets[0][0].addr, packets[0][0].bus), (0x199, 1))
        self.assertEqual(bytes(packets[0][0].data[0:8]), bytes.fromhex('cdabe0007456343d'))
        self.assertFalse(self.safety.safety_tx_hook(packets[0]), 'scheduled ticket cannot be replayed by host')
