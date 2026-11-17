"""Exact EV9 LONG direct-angle contract; select classes by compiled variant."""
import unittest

from opendbc.can import CANPacker
from opendbc.car.structs import CarParams
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.safety.tests.libsafety import libsafety_py


class Ev9LongFixture:
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.packer = CANPacker('hyundai_canfd_generated')
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, 0x5C95), 0)
    self.now = 1_000_000
    self.safety.set_timer(self.now)

  def packet(self, name, bus, values):
    addr, data, _ = self.packer.make_can_msg(name, bus, values)
    return libsafety_py.make_CANPacket(addr, bus, data)

  def feed(self, *, measured=0.0, fault=0, gear=5, buttons=True):
    self.safety.set_timer(self.now)
    entries = [('ACCELERATOR', {'GEAR': gear}), ('TCS', {'DriverBraking': 0}),
               ('WHEEL_SPEEDS', dict.fromkeys(('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal'), 40.0)),
               ('MDPS', {'MDPS_EstStrAnglVal': measured, 'MDPS_PaStrAnglVal': -20.0,
                         'MDPS_ADAS_AciFltSig_Lv2': fault})]
    if buttons:
      entries += [('CRUISE_BUTTONS', {'CRUISE_BUTTONS': 2}), ('CRUISE_BUTTONS', {'CRUISE_BUTTONS': 0})]
    for name, values in entries:
      self.assertTrue(self.safety.safety_rx_hook(self.packet(name, 1, values)), name)
    self.now += 10_000

  def ready(self):
    for _ in range(6):
      self.feed()
    self.assertTrue(self.safety.safety_rx_hook(self.packet('CRUISE_BUTTONS', 1, {'ADAPTIVE_CRUISE_MAIN_BTN': 1})))
    self.assertTrue(self.safety.safety_rx_hook(self.packet('CRUISE_BUTTONS', 1, {})))
    self.feed()
    self.assertAlmostEqual(self.safety.get_vehicle_speed_min(), 40.0 / 3.6, places=3)
    self.assertTrue(self.safety.get_controls_allowed())

  def angle(self, *, value=0.0, active=True, bus=1, bad_crc=False):
    msg = self.packet('LFA_ALT', bus, {'ADAS_ActvACISta': 0, 'ADAS_ActvACILvl2Sta': 2 if active else 1,
                                     'ADAS_StrAnglReqVal': value, 'ADAS_ACIAnglTqRedcGainVal': 0.0,
                                     'FCA_ESA_ActvSta': 0, 'FCA_ESA_TqBstGainVal': 0.0})
    if bad_crc:
      msg[0].data[0] ^= 1
    return msg


class TestEv9LongDebugContract(Ev9LongFixture, unittest.TestCase):
  def test_active_camera_forwarding_requires_current_physical_readiness(self):
    self.ready()
    for address in (0x110, 0x362):
      self.assertEqual(self.safety.safety_test_selected_fwd(2, address), True)
    self.assertTrue(self.safety.safety_rx_hook(self.packet('TCS', 1, {'DriverBraking': 1})))
    for address in (0x110, 0x362):
      self.assertEqual(self.safety.safety_test_selected_fwd(2, address), False)
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))

  def test_direct_cb_is_bus_bound_crc_checked_and_no_110_owner(self):
    self.ready()
    self.assertTrue(self.safety.safety_tx_hook(self.angle(value=1.1)))
    self.assertFalse(self.safety.safety_tx_hook(self.angle(bus=0)))
    self.assertFalse(self.safety.safety_tx_hook(self.angle(bad_crc=True)))
    self.assertFalse(self.safety.safety_tx_hook(self.packet('LKAS_ALT', 0, {})))
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=2.3)))

  def test_exact_selector_rejects_neighbor_words_and_aol_experience(self):
    for word in (0x5C94, 0x5C97, 0x5D95, 0x5C9D, 0x5CB5, 0x5C91, 0x5491):
      with self.subTest(word=hex(word)):
        self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, word)
        self.safety.set_controls_allowed(True)
        self.assertFalse(self.safety.safety_tx_hook(self.angle()))
    self.safety.set_alternative_experience(1)
    self.safety.set_safety_hooks(CarParams.SafetyModel.hyundaiCanfd, 0x5C95)
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))

  def test_actual_controller_target_cap_inactive_measurement_and_reentry(self):
    from types import SimpleNamespace
    from opendbc.car import structs
    from opendbc.car.hyundai.interface import CarInterface
    from opendbc.car.hyundai.ev9_longitudinal import candidate
    from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params
    from opendbc.car.hyundai.tests.test_ev9_long_controller import packets
    from opendbc.car.hyundai.values import CAR

    for sign in (-1, 1):
      self.setUp()
      cp = candidate(params(candidate=CAR.KIA_EV9), enabled=True, is_release=False)
      ci = CarInterface(cp)
      # This test isolates the controller envelope after a controlled owner
      # acknowledgment; real pending/native handoff has its own lifecycle test.
      ci.CC.pe_startup = SimpleNamespace(handed_off=True, can_emit=lambda controller: True)
      observed_commands = 0
      cc = structs.CarControl(enabled=True, latActive=True, longActive=True)
      cc.actuators.steeringAngleDeg = sign * 360.0
      ci.update([])
      for tick in range(1, 701):
        self.now = tick * 10_000
        self.safety.set_timer(self.now)
        frames = packets(self.packer, tick, moving=False, advance_counter=True, cruise_button=2 if tick == 12 else 0,
                         adaptive_main=tick == 8)
        # Low physical speed permits the target cap to be observed directly.
        wheel_addr = self.packer.make_can_msg('WHEEL_SPEEDS', 1, {})[0]
        frames = [f for f in frames if f[0] != wheel_addr]
        frames.append(self.packer.make_can_msg('WHEEL_SPEEDS', 1,
                      {'COUNTER': tick, **dict.fromkeys(('WHL_SpdFLVal', 'WHL_SpdFRVal', 'WHL_SpdRLVal', 'WHL_SpdRRVal'), 7.2)}))
        measured = sign * 170.0 if 550 <= tick < 570 else 0.0
        mdps_addr = self.packer.make_can_msg('MDPS', 1, {})[0]
        frames = [f for f in frames if f[0] != mdps_addr]
        frames.append(self.packer.make_can_msg('MDPS', 1, {'COUNTER': tick, 'MDPS_EstStrAnglVal': measured}))
        for address, data, bus in frames:
          self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data)), (tick, hex(address), bus))
        ci.update([(tick * 10_000_000, frames)])
        cc.enabled = tick >= 14
        cc.latActive = tick >= 14 and not 550 <= tick < 570
        _, sends = ci.apply(cc.as_reader(), tick * 10_000_000)
        angle = next((f for f in sends if f[0] == 0xcb), None)
        if angle is None or tick < 14:
          continue
        observed_commands += 1
        for address, data, bus in sends:
          self.assertTrue(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)),
                          (sign, tick, hex(address), bus))
        if tick == 549:
          self.assertAlmostEqual(ci.CC.ev9_angle_filter.x, sign * 140.0, delta=0.1)
          self.assertLessEqual(abs(ci.CC.apply_angle_last), 140.1)
        if 550 <= tick < 570:
          self.assertAlmostEqual(ci.CC.apply_angle_last, measured, delta=0.1)
          self.assertEqual((angle[1][3] >> 4) & 0xf, 1)
        if tick == 700:
          self.assertEqual((angle[1][3] >> 4) & 0xf, 2)
          self.assertLessEqual(abs(ci.CC.ev9_angle_filter.x), 140.1)

      self.assertEqual(observed_commands, 687)

  def test_mdps_uses_byte16_and_exact_long_fault_bit(self):
    self.ready()
    self.safety.set_controls_allowed(False)
    self.feed(measured=3.0)
    self.safety.set_controls_allowed(False)
    self.assertTrue(self.safety.safety_tx_hook(self.angle(value=3.0, active=False)))
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=-20.0, active=False)))
    for _ in range(6):
      self.feed(measured=3.0, fault=1)
    self.assertTrue(self.safety.safety_tx_hook(self.angle(value=3.0)))
    self.feed(measured=3.0, fault=2)
    self.assertFalse(self.safety.safety_tx_hook(self.angle(value=3.0)))

  def test_cancel_missing_buttons_and_rx_expiry_deny_active_angle(self):
    self.ready()
    self.assertTrue(self.safety.safety_rx_hook(self.packet('CRUISE_BUTTONS', 1, {'CRUISE_BUTTONS': 4})))
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))
    self.setUp()
    for _ in range(6):
      self.feed(buttons=False)
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))
    self.ready()
    self.now += 1_000_000
    self.safety.set_timer(self.now)
    self.safety.safety_tick()
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))

  def test_ev9_effective_accel_limit_and_ten_accepted_inactive_frames(self):
    self.ready()

    def scc(mode, accel):
      return self.packet('SCC_CONTROL', 1, {'ACCMode': mode, 'aReqRaw': accel, 'aReqValue': accel})
    self.assertTrue(self.safety.safety_tx_hook(scc(1, 2.0)))
    self.assertTrue(self.safety.safety_tx_hook(scc(1, 3.5)))
    self.assertFalse(self.safety.safety_tx_hook(scc(1, 3.51)))
    self.assertFalse(self.safety.safety_tx_hook(self.packet('SCC_CONTROL', 1, {'ACCMode': 1, 'aReqRaw': 3.51, 'aReqValue': 0.0})))
    self.assertFalse(self.safety.safety_tx_hook(self.packet('SCC_CONTROL', 1, {'ACCMode': 1, 'aReqRaw': 0.0, 'aReqValue': 3.51})))
    self.assertTrue(self.safety.safety_tx_hook(scc(1, -3.5)))
    self.assertFalse(self.safety.safety_tx_hook(scc(1, -3.51)))
    bad = scc(0, 0.0)
    bad[0].data[0] ^= 1
    for _ in range(12):
      self.assertFalse(self.safety.safety_tx_hook(bad))
    self.assertTrue(self.safety.get_controls_allowed())
    for _ in range(9):
      self.assertTrue(self.safety.safety_tx_hook(scc(0, 0.0)))
      self.assertTrue(self.safety.get_controls_allowed())
    self.assertTrue(self.safety.safety_tx_hook(scc(0, 0.0)))
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))
    self.assertFalse(self.safety.safety_tx_hook(scc(1, 0.01)))
    self.assertFalse(self.safety.safety_tx_hook(scc(1, -0.01)))

  def test_neutral_lfa_and_literal_radar_host_heartbeat(self):
    self.ready()
    lfa = self.packet('LFA', 1, {'LKA_OptUsmSta': 2, 'LKA_SysIndReq': 1,
                               'StrTqReqVal': 0, 'ActToiSta': 0, 'LKA_UsmMod': 0,
                               'Damping_Gain': 100})
    self.assertTrue(self.safety.safety_tx_hook(lfa))
    data = bytearray.fromhex('00000000ff006f00e80400001201030055ffff0000000000')
    data[4] &= 0xFE  # actual physical brake is released
    data[0:2] = hkg_can_fd_checksum(0x100, None, data).to_bytes(2, 'little')
    self.assertTrue(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x100, 0, data)))
    data[4] |= 1  # cannot spoof physical brake
    data[0:2] = hkg_can_fd_checksum(0x100, None, data).to_bytes(2, 'little')
    self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x100, 0, data)))

  def test_radar_track_forwarding_is_exact_bus_range(self):
    self.ready()
    for addr in (0x3A5, 0x3C4):
      self.assertEqual(self.safety.safety_fwd_hook(0, addr), -1)
      self.assertEqual(self.safety.safety_fwd_hook(2, addr), 0)
    self.assertEqual(self.safety.safety_fwd_hook(0, 0x3A4), 2)
    self.assertEqual(self.safety.safety_fwd_hook(0, 0x3C5), 2)


class TestEv9LongReleaseDenied(Ev9LongFixture, unittest.TestCase):
  def test_long_profile_has_no_transmit_authority_even_with_controls_forced(self):
    self.safety.set_controls_allowed(True)
    self.assertFalse(self.safety.safety_tx_hook(self.angle()))
    self.assertFalse(self.safety.safety_tx_hook(self.packet('SCC_CONTROL', 1, {'ACCMode': 1})))
    self.assertFalse(self.safety.safety_tx_hook(self.packet('CAM_0x362', 0, {})))
