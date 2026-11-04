"""Malformed and contradictory physical sources withdraw receiver credits."""
import unittest
from opendbc.car.structs import CarParams
from opendbc.safety.tests import test_subaru_software_cruise as software
from opendbc.safety.tests import test_subaru_sng as sng
from opendbc.safety.tests import test_subaru_stock_aol as stock
from opendbc.safety.tests.libsafety import libsafety_py


def changed(packet, index, value):
  data = bytearray(packet.data[0:8])
  data[index] = value
  data[0] = ((packet.addr & 255) + (packet.addr >> 8) + sum(data[1:])) & 255
  return libsafety_py.make_CANPacket(packet.addr, packet.bus, bytes(data))


class TestSubaruReceiveGuards(unittest.TestCase):
  def test_software_bad_gear_or_button_source_withdraws(self):
    for source in ('Transmission', 'Cruise_Buttons'):
      for fault in ('length', 'checksum', 'contradiction'):
        with self.subTest(source=source, fault=fault):
          case = software.TestSubaruSoftwareCruise()
          case.setup()
          packet = case.source() if source == 'Cruise_Buttons' else case.base.frame('Transmission', {'Gear': 121})
          case.safety.safety_rx_hook(packet)
          if fault == 'length':
            packet = libsafety_py.make_CANPacket(packet.addr, packet.bus, bytes(packet.data[0:7]))
          elif fault == 'checksum':
            packet.data[0] ^= 1
          else:
            packet = changed(packet, 3 if source == 'Transmission' else 2, 3 if source == 'Transmission' else packet.data[2] ^ 1)
          case.safety.safety_rx_hook(packet)
          self.assertFalse(case.safety.safety_tx_hook(case.button()))

  def test_software_second_copy_deadline_is_not_renewed(self):
    case = software.TestSubaruSoftwareCruise()
    case.setup()
    self.assertTrue(case.safety.safety_tx_hook(case.button()))
    case.safety.set_timer(case.base.now + 10_001)
    self.assertFalse(case.safety.safety_tx_hook(case.button(copy=2)))

  def test_stock_aol_truncated_optional_gear_withdraws(self):
    case = stock.TestSubaruStockAol()
    self.addCleanup(case.tearDown)
    case.configure()
    case.arm()
    gear = case.frame('Transmission', {'Gear': 121})
    case.safety.safety_rx_hook(libsafety_py.make_CANPacket(gear.addr, gear.bus, bytes(gear.data[0:7])))
    self.assertEqual(case.safety.aol_get_permission_mask(), 0)

  def test_stock_aol_cleanup_runs_on_success_failure_and_error(self):
    """The manually owned helper must not leave its native AOL profile for the next test."""
    safety = libsafety_py.libsafety
    for outcome in ('success', 'failure', 'error'):
      with self.subTest(outcome=outcome):
        class OwnedCase(TestSubaruReceiveGuards):
          def runTest(self, expected_outcome=outcome):
            self.test_stock_aol_truncated_optional_gear_withdraws()
            if expected_outcome == 'failure':
              self.fail('deliberate failure after the real receiver assertions')
            if expected_outcome == 'error':
              raise RuntimeError('deliberate error after the real receiver assertions')

        result = unittest.TestResult()
        OwnedCase().run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.failures), int(outcome == 'failure'))
        self.assertEqual(len(result.errors), int(outcome == 'error'))
        self.assertEqual(safety.get_current_safety_mode(), CarParams.SafetyModel.noOutput)
        self.assertFalse(safety.get_controls_allowed())
        self.assertEqual(safety.aol_get_request_mask(), 0)
        self.assertEqual(safety.aol_get_permission_mask(), 0)

  def test_sng_conflicting_duplicate_withdraws_source_and_forwarding_lease(self):
    case = sng.TestSubaruSng()
    case.setup()
    sources = case.feed()
    self.assertTrue(case.send(sources[0]))
    address, data, bus = sources[0]
    packet = libsafety_py.make_CANPacket(address, bus, data)
    packet = changed(packet, 7, packet.data[7] ^ 1)
    case.safety.safety_rx_hook(packet)
    self.assertFalse(case.send(sources[0]))
    self.assertEqual(case.safety.safety_fwd_hook(0, 0x40), 2)

  def test_software_new_burst_requires_fresh_sources_and_full_cooldown(self):
    case = software.TestSubaruSoftwareCruise()
    case.setup()
    self.assertTrue(case.safety.safety_tx_hook(case.button()))
    self.assertTrue(case.safety.safety_tx_hook(case.button(copy=2)))
    case.base.feed(cruise=True)
    case.source()
    case.source()
    self.assertFalse(case.safety.safety_tx_hook(case.button()))
    for _ in range(10):
      case.base.feed(cruise=True)
    case.source()
    case.source()
    self.assertTrue(case.safety.safety_tx_hook(case.button()))

  def test_sng_pulse_repeats_only_inside_pulse_or_after_cooldown(self):
    case = sng.TestSubaruSng()
    case.setup()
    for _ in range(52):
      sources = case.feed()
    self.assertTrue(case.send(sources[1], speed=True))
    sources = case.feed()
    self.assertTrue(case.send(sources[1], speed=True))
    for _ in range(11):
      sources = case.feed()
    self.assertFalse(case.send(sources[1], speed=True))
    for _ in range(44):
      sources = case.feed()
    self.assertTrue(case.send(sources[1], speed=True))

  def test_avh_malformed_expired_and_interrupted_pair_never_retries(self):
    from opendbc.car import structs
    from opendbc.car.subaru.avh import avh_request
    from opendbc.safety.tests import test_subaru_avh_startup as avh
    for fault in ('length', 'checksum', 'expiry', 'pair_source_lease'):
      with self.subTest(fault=fault):
        debug = libsafety_py.libsafety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
        safety, last = avh.ready(1169, 0)
        addr, data, bus = avh_request(last[0x6BB], 1)
        if fault == 'pair_source_lease':
          self.assertEqual(bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data))), debug)
          safety.set_timer(11_350_001)
          safety.safety_rx_hook(libsafety_py.make_CANPacket(0x40, 0, last[0x40]))
          data = avh_request(last[0x6BB], 2)[1]
        elif fault == 'expiry':
          safety.set_timer(31_000_001)
          safety.safety_rx_hook(libsafety_py.make_CANPacket(0x40, 0, last[0x40]))
        else:
          raw = bytearray(last[0x32B])
          if fault == 'length':
            raw = raw[:-1]
          else:
            raw[0] ^= 1
          safety.safety_rx_hook(libsafety_py.make_CANPacket(0x32B, 1, bytes(raw)))
        self.assertFalse(safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data)))
