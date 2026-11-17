import unittest
from functools import cache

from opendbc.can import CANPacker
from opendbc.car.structs import CarParams
from opendbc.car.hyundai import ioniq5pe_long_sender as sender
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.test_hyundai_ev9_long import Ev9LongFixture


@cache
def release_safety():
  default = libsafety_py.libsafety
  library = libsafety_py.ffi.dlopen(libsafety_py._build_libsafety(release=True))
  assert libsafety_py.libsafety is default
  return library


class PEFixture:
  RELEASE_NATIVE = False

  packet = Ev9LongFixture.packet
  feed = Ev9LongFixture.feed
  angle = Ev9LongFixture.angle
  ready = Ev9LongFixture.ready

  def setUp(self):
    self.safety = release_safety() if self.RELEASE_NATIVE else libsafety_py.libsafety
    self.packer = CANPacker('hyundai_canfd_generated')
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, 0x5495), 0)
    self.now = 1_000_000
    self.safety.set_timer(self.now)

  @staticmethod
  def can_packet(frame):
    return libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])


class TestPELongDebug(PEFixture, unittest.TestCase):
  def test_physical_set_release_required_and_cancel_withdraws(self):
    for _ in range(6):
      self.feed(buttons=False)
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)))
    self.ready()
    self.assertTrue(self.safety.safety_tx_hook(self.angle(value=.1)))
    self.assertTrue(self.safety.safety_rx_hook(self.packet('CRUISE_BUTTONS', 1, {'CRUISE_BUTTONS': 4})))
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)))

  def test_original_pe_full_fault_mask_and_legacy_fault_never_actuate(self):
    for fault in (1, 2, 3, 4, 5, 6, 7):
      self.setUp()
      self.ready()
      self.feed(fault=fault)
      self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)), fault)
    for fault in (1, 2, 3):
      self.setUp()
      self.ready()
      self.assertTrue(self.safety.safety_rx_hook(self.packet('MDPS', 1, {'MDPS_LkaFailSta': fault})))
      self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)), fault)

  def test_main_press_withdraws_cached_positive_and_requires_fresh_set_release(self):
    for key in ('ADAPTIVE_CRUISE_MAIN_BTN', 'NORMAL_CRUISE_MAIN_BTN'):
      self.setUp()
      self.ready()
      cached = self.packet('SCC_CONTROL', 1, {'ACCMode': 1, 'aReqRaw': 1., 'aReqValue': 1.})
      self.assertTrue(self.safety.safety_tx_hook(cached))
      self.assertTrue(self.safety.safety_rx_hook(self.packet('CRUISE_BUTTONS', 1, {'CRUISE_BUTTONS': 0, key: 1})))
      self.assertFalse(self.safety.get_controls_allowed())
      self.assertFalse(self.safety.safety_tx_hook(cached))
      self.assertTrue(self.safety.safety_rx_hook(self.packet('CRUISE_BUTTONS', 1, {'CRUISE_BUTTONS': 0, key: 0})))
      self.assertFalse(self.safety.get_controls_allowed())
      for value in (2, 0):
        self.assertTrue(self.safety.safety_rx_hook(self.packet('CRUISE_BUTTONS', 1, {'CRUISE_BUTTONS': value})))
      self.assertTrue(self.safety.get_controls_allowed())

  def test_main_during_held_set_cannot_reenable_on_old_release(self):
    for key in ('ADAPTIVE_CRUISE_MAIN_BTN', 'NORMAL_CRUISE_MAIN_BTN'):
      self.setUp()
      self.ready()
      for main, button in ((1, 2), (0, 2), (0, 0)):
        self.assertTrue(self.safety.safety_rx_hook(self.packet('CRUISE_BUTTONS', 1, {'CRUISE_BUTTONS': button, key: main})))
        self.assertFalse(self.safety.get_controls_allowed())
      self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)))
      for value in (2, 0):
        self.assertTrue(self.safety.safety_rx_hook(self.packet('CRUISE_BUTTONS', 1, {'CRUISE_BUTTONS': value})))
      self.assertTrue(self.safety.get_controls_allowed())

  def test_actual_estimated_angle_at_byte16_not_stock_pa_byte12(self):
    for _ in range(6):
      self.feed(measured=10., buttons=False)
      self.assertTrue(self.safety.safety_rx_hook(self.packet('CRUISE_BUTTONS', 1, {'CRUISE_BUTTONS': 0})))
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertTrue(self.safety.safety_tx_hook(self.angle(value=10., active=False)))
    for _ in range(6):
      self.feed(measured=10.)
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertTrue(self.safety.safety_tx_hook(self.angle(value=10.)))

  def test_missing_physical_buttons_cannot_pass_inactive_angle_health(self):
    for _ in range(6):
      self.feed(measured=10., buttons=False)
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=10., active=False)))

  def test_current_pe_acceleration_bounds_not_ev9_exception(self):
    def scc(raw, value):
      return self.packet('SCC_CONTROL', 1, {'ACCMode': 1, 'aReqRaw': raw, 'aReqValue': value})
    self.ready()
    for value in (3.5, -3.5):
      for raw, selected in ((value, value), (value, 0.), (0., value)):
        self.assertTrue(self.safety.safety_tx_hook(scc(raw, selected)), (raw, selected))
    for value in (3.51, -3.51):
      for raw, selected in ((value, value), (value, 0.), (0., value)):
        self.assertFalse(self.safety.safety_tx_hook(scc(raw, selected)), (raw, selected))

  def test_pe_heartbeat_reserved_bytes_and_physical_pedal_values(self):
    self.ready()
    message = sender.create_radar_heartbeat(1, False, False)
    self.assertTrue(self.safety.safety_tx_hook(self.can_packet(message)))
    for offset in range(3, 24):
      address, data, bus = message
      bad = bytearray(data)
      bad[offset] ^= 1
      bad[:2] = hkg_can_fd_checksum(address, None, bad).to_bytes(2, 'little')
      self.assertFalse(self.safety.safety_tx_hook(self.can_packet((address, bytes(bad), bus))), offset)
    self.assertFalse(self.safety.safety_tx_hook(self.can_packet(sender.create_radar_heartbeat(2, True, False))))

  def test_wrong_bus_crc_standstill_pedals_and_source_expiry_deny(self):
    self.ready()
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1, bus=0)))
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1, bad_crc=True)))
    self.now += 1_000_000
    self.safety.set_timer(self.now)
    self.safety.safety_tick()
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)))
    for name, fields in (
      ('WHEEL_SPEEDS', dict.fromkeys(('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal'), 0.)),
      ('TCS', {'DriverBraking': 1}), ('ACCELERATOR', {'GEAR': 5, 'ACCELERATOR_PEDAL': 1}),
    ):
      self.setUp()
      self.ready()
      for _ in range(6):
        self.assertTrue(self.safety.safety_rx_hook(self.packet(name, 1, fields)))
      self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)), name)

  def test_exact_selector_and_alternative_experience_boundaries(self):
    for word in (0x5491, 0x5494, 0x5497, 0x54B5):
      self.safety.init_tests()
      self.safety.set_alternative_experience(0)
      self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, word)
      self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)))
    for experience in (1, 32, 64):
      self.safety.init_tests()
      self.safety.set_alternative_experience(experience)
      self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, 0x5495)
      self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)))

  def test_replacement_source_reappearance_is_immediate_conflict_without_relay_grace(self):
    for name, bus, values in (('SCC_CONTROL', 1, {'ACCMode': 0}), ('LFA_ALT', 1, {})):
      self.setUp()
      self.ready()
      positive = self.packet('SCC_CONTROL', 1, {'ACCMode': 1, 'aReqRaw': 1., 'aReqValue': 1.})
      self.assertTrue(self.safety.safety_tx_hook(positive))
      self.assertLess(self.now, 2_000_000)
      self.safety.safety_rx_hook(self.packet(name, bus, values))
      self.assertTrue(self.safety.get_relay_malfunction())
      self.assertFalse(self.safety.safety_tx_hook(positive))

  def test_live_mrr35_receive_bank_is_not_an_ecu_conflict_or_lost_to_forwarding(self):
    self.ready()
    for address in (0x3A5, 0x3C4):
      self.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, 0, bytes(24)))
      self.assertFalse(self.safety.get_relay_malfunction())
      self.assertEqual(self.safety.safety_fwd_hook(0, address), -1)
      self.assertEqual(self.safety.safety_fwd_hook(2, address), 0)
    self.assertEqual(self.safety.safety_fwd_hook(0, 0x3A4), 2)
    self.assertEqual(self.safety.safety_fwd_hook(0, 0x3C5), 2)

  def test_longitudinal_and_ordinary_direct_angle_remain_withdrawn_on_gas(self):
    self.ready()
    self.safety.set_timer(self.now)
    self.assertTrue(self.safety.safety_rx_hook(self.packet('ACCELERATOR', 1, {'GEAR': 5, 'ACCELERATOR_PEDAL': 4})))
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)))
    self.assertFalse(self.safety.safety_tx_hook(self.packet('SCC_CONTROL', 1, {'ACCMode': 1, 'aReqRaw': 1., 'aReqValue': 1.})))


class TestPELongReleaseDenied(PEFixture, unittest.TestCase):
  RELEASE_NATIVE = True

  def test_release_never_admits_proposed_pe_long_profile(self):

    for _ in range(6):
      self.safety.set_timer(self.now)
      for name, values in (
        ('ACCELERATOR', {'GEAR': 5}), ('TCS', {'DriverBraking': 0}),
        ('WHEEL_SPEEDS', dict.fromkeys(('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal'), 40.)),
        ('MDPS', {'MDPS_EstStrAnglVal': 0., 'MDPS_PaStrAnglVal': -20., 'MDPS_ADAS_AciFltSig_Lv2': 0}),
        ('CRUISE_BUTTONS', {'CRUISE_BUTTONS': 2}), ('CRUISE_BUTTONS', {'CRUISE_BUTTONS': 0}),
      ):
        self.safety.safety_rx_hook(self.packet(name, 1, values))
      self.now += 10_000
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=.1)))
    self.assertFalse(self.safety.safety_tx_hook(self.packet('SCC_CONTROL', 1, {'ACCMode': 1, 'aReqRaw': 1., 'aReqValue': 1.})))
    self.assertFalse(self.safety.safety_tx_hook(self.can_packet(sender.create_radar_heartbeat(1, False, False))))
