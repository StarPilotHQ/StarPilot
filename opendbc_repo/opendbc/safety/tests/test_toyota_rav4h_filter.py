import unittest
from opendbc.safety.tests import common
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_toyota as toyota_tests

libsafety_py.ffi.cdef("extern safety_config current_safety_config;")


class TestRav4hFilterNative(unittest.TestCase):
  init = toyota_tests.TestToyotaHighlanderAol.init
  rx = toyota_tests.TestToyotaHighlanderAol.rx
  tearDown = toyota_tests.TestToyotaHighlanderAol.tearDown

  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.packer = common.CANPackerSafety("toyota_tnga_k_pt_generated")
    # Reuse the real Toyota RX fixture without collecting its unrelated tests.
    self.base = toyota_tests.TestToyotaHighlanderAol()
    self.base.safety = self.safety
    self.base.packer = self.packer

  def physical(self, **kwargs):
    self.rx(0x2FF, bytes(8))
    self.base.physical(**kwargs)

  def test_dlc5_decoder_stock_long_permissions_and_exact_words(self):
    for word, ae, expected in ((0x9049, 32, 3), (0x9049, 160, 3), (0x9249, 32, 1)):
      self.init(word, ae)
      self.physical(cruise=True)
      self.assertEqual(self.safety.aol_get_permission_mask(), expected)
      self.assertEqual(self.safety.safety_fwd_hook(2, 0x343), 0)
      for accel, cancel, allowed in ((.1, 0, word == 0x9049), (0, 1, True), (0, 0, word == 0x9049)):
        msg = self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": accel, "CANCEL_REQ": cancel})
        self.assertEqual(bool(self.safety.safety_tx_hook(msg)), allowed)
      self.rx(0x262, bytes([0, 0, 0, 2, 0, 0, 0, 0]), checksum=True)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.physical(cruise=True)
      self.assertEqual(self.safety.aol_get_permission_mask(), expected)
      bad = self.packer.make_can_msg_safety("EPS_STATUS", 0, {"LKA_STATE": 1})
      bad.data[4] ^= 1
      self.safety.safety_rx_hook(bad)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    for word in (0x5049, 0x9048, 0x904A, 0x914A, 0x9448, 0x944A, 0x9648, 0x964A, 0xB049, 0xD049):
      self.init(word, 32)
      self.physical(cruise=True)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .1})))

  def test_exact_factory_stock_profiles_are_distinct_from_filters(self):
    # These exact words now select bare stock PCM, never adapter longitudinal.
    for word in (0x5249, 0x564D, 0x5749):
      self.init(word, 32)
      self.assertFalse(self.safety.safety_config_valid())
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .1})))
      config = self.safety.current_safety_config
      addresses = {config.rx_checks[i].msg[0].addr for i in range(config.rx_checks_len)}
      self.assertIn(0x365 if word in (0x564D, 0x5749) else 0x1D3, addresses)
      if word == 0x5249:
        self.physical(cruise=True)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .1})))

  def test_explicit_legacy_advert_words_require_physical_sources(self):
    # The four-byte advertisement owner is distinct from the eight-byte SDSU owner.
    for word, permission in ((0x9449, 3), (0x9649, 1)):
      for ae in (0, 32):
        with self.subTest(word=hex(word), ae=ae):
          self.init(word, ae)
          steer = self.packer.make_can_msg_safety("STEERING_LKA", 0, {"STEER_REQUEST": 1, "STEER_TORQUE_CMD": 0})
          accel = self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .1})
          cancel = self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"CANCEL_REQ": 1})
          self.assertFalse(self.safety.safety_tx_hook(steer))
          self.assertFalse(self.safety.safety_tx_hook(accel))
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          # Only real physical CAN supplies MAIN and EPS; no 0x2FF status is sent.
          self.base.physical(cruise=True)
          self.assertEqual(self.safety.aol_get_permission_mask(), permission if ae else 0)
          self.assertTrue(self.safety.safety_tx_hook(steer))
          self.assertEqual(bool(self.safety.safety_tx_hook(accel)), word == 0x9449)
          self.assertTrue(self.safety.safety_tx_hook(cancel))
          self.assertEqual(self.safety.safety_fwd_hook(2, 0x343), 0)
          for fault in ({"main": False}, {"gear": 32}, {"door": True}, {"eps": 9}):
            self.base.physical(cruise=True, **fault)
            self.assertEqual(self.safety.aol_get_permission_mask(), 0)
            self.assertFalse(self.safety.safety_tx_hook(steer))
            self.assertFalse(self.safety.safety_tx_hook(accel))
            self.assertTrue(self.safety.safety_tx_hook(cancel))
            self.base.physical(cruise=False)
            self.base.physical(cruise=True)
            self.assertTrue(self.safety.safety_tx_hook(steer))
            self.assertEqual(bool(self.safety.safety_tx_hook(accel)), word == 0x9449)
          self.safety.set_timer(1_400_001)
          self.safety.safety_tick()
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          self.assertFalse(self.safety.safety_tx_hook(steer))
          self.assertFalse(self.safety.safety_tx_hook(accel))
          self.assertTrue(self.safety.safety_tx_hook(cancel))

  def test_required_filter_source_expiry_and_wrong_length(self):
    self.init(0x9049, 32)
    self.physical(cruise=True)
    self.safety.safety_tick()
    self.assertEqual(self.safety.aol_get_permission_mask(), 3)
    self.safety.set_timer(1_300_001)
    self.safety.safety_tick()
    self.safety.aol_set_host_request(3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    # Unmatched DLC is optional ingress in the shared RX API, never a valid required source.
    self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x2FF, 0, bytes(7))))
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .1})))
    self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x343, 2, bytes(8))))

  def test_dlc5_permission_revocations(self):
    for kwargs in ({"main": False}, {"gear": 32}, {"gear": 8}, {"gear": 16}, {"door": True}, {"eps": 0}, {"eps": 9}, {"eps": 17}):
      self.init(0x9049, 32)
      self.physical(cruise=True)
      self.assertEqual(self.safety.aol_get_permission_mask(), 3)
      self.physical(cruise=True, **kwargs)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    for kwargs in ({"brake": True}, {"gas": True}, {"belt": False}):
      self.init(0x9049, 32)
      self.physical(cruise=True, **kwargs)
      self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_ordinary_and_manual_hold_profiles(self):
    for word in (0x9049, 0x9249):
      self.init(word, 0)
      self.physical(cruise=True)
      self.assertTrue(self.safety.get_controls_allowed())
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      accel = self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .2})
      cancel = self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"CANCEL_REQ": 1})
      self.assertEqual(bool(self.safety.safety_tx_hook(accel)), word == 0x9049)
      self.assertTrue(self.safety.safety_tx_hook(cancel))
      self.init(word, 128)
      self.physical(cruise=False, brake=True)
      self.assertTrue(self.safety.safety_rx_hook(self.packer.make_can_msg_safety("WHEEL_SPEEDS", 0, {
        "WHEEL_SPEED_FL": 0., "WHEEL_SPEED_FR": 0., "WHEEL_SPEED_RL": 0., "WHEEL_SPEED_RR": 0.})))
      self.safety.safety_tick()
      hold = self.packer.make_can_msg_safety("ACC_CONTROL", 0, {
        "ACCEL_CMD": -1., "PERMIT_BRAKING": 1, "RELEASE_STANDSTILL": 0})
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.assertEqual(bool(self.safety.safety_tx_hook(hold)), word == 0x9049)
      for fields in ({"RELEASE_STANDSTILL": 1}, {"PERMIT_BRAKING": 0}, {"CANCEL_REQ": 1}, {"ACCEL_CMD": -.999}):
        self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {
          "ACCEL_CMD": -1., "PERMIT_BRAKING": 1, "RELEASE_STANDSTILL": 0, **fields})))

  def test_profile_accel_bounds_disabled_and_bus_length(self):
    for ae in (0, 32, 128, 160):
      self.init(0x9049, ae)
      self.physical(cruise=True)
      for accel, allowed in ((-3.501, False), (-3.5, True), (2., True), (2.001, False)):
        self.assertEqual(bool(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {
          "ACCEL_CMD": accel}))), allowed, (ae, accel))
      self.safety.set_controls_allowed(False)
      if ae in (32, 160):
        self.safety.aol_set_host_request(0)
      for accel in (-.1, .1):
        self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": accel})))
      self.assertTrue(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"CANCEL_REQ": 1})))
      self.physical(cruise=True)
      for bus in (1, 2):
        self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", bus, {"ACCEL_CMD": .1})))
      for length in (7, 12):
        self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x343, 0, bytes(length))))

  def test_profile_reinit_clears_axis_and_dlc5_eps_boundaries(self):
    for word, permissions in ((0x9049, 3), (0x9249, 1), (0x9049, 3)):
      self.init(word, 32)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      self.assertEqual(self.safety.aol_get_request_mask(), 0)
      self.physical(cruise=True)
      self.assertEqual(self.safety.aol_get_permission_mask(), permissions)
      for eps in (0, 3, 9, 17):
        self.physical(cruise=True, eps=eps)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0, (word, eps))
        self.physical(cruise=True)
        self.assertEqual(self.safety.aol_get_permission_mask(), permissions)
      for length in (4, 6, 8):
        self.rx(0x262, bytes(length), checksum=True)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.physical(cruise=True)
        self.assertEqual(self.safety.aol_get_permission_mask(), permissions)
      bad = self.packer.make_can_msg_safety("EPS_STATUS", 0, {"LKA_STATE": 1})
      bad.data[4] ^= 1
      self.safety.safety_rx_hook(bad)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_sdsu_fault_aeb_and_recovery_preserve_health_meaning(self):
    for word in (0x9049, 0x9249):
      self.init(word, 32)
      self.physical(cruise=True)
      expected = 3 if word == 0x9049 else 1
      self.assertEqual(self.safety.aol_get_permission_mask(), expected)
      self.assertTrue(self.safety.safety_rx_hook(self.packer.make_can_msg_safety("SDSU", 0, {"STATE": 7})))
      self.assertTrue(self.safety.safety_config_valid())
      self.assertEqual(self.safety.aol_get_permission_mask(), 1)
      self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .1})))
      self.physical(cruise=True)
      self.assertEqual(self.safety.aol_get_permission_mask(), expected)
      for state in (1, 2, 3, 4, 5, 6, 8, 15):
        self.assertFalse(self.safety.safety_rx_hook(self.packer.make_can_msg_safety("SDSU", 0, {"STATE": state})))
        self.assertFalse(self.safety.safety_config_valid())
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.physical(cruise=True)
        # A repaired source cannot create a new stock cruise engagement edge.
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.physical(cruise=False)
        self.physical(cruise=True)
        self.assertEqual(self.safety.aol_get_permission_mask(), expected)

  def test_cold_source_and_malformed_copy_cannot_create_or_refresh_control(self):
    self.init(0x9049, 0)
    self.base.physical(cruise=True)
    self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .1})))
    self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("STEERING_LKA", 0, {"STEER_REQUEST": 1})))
    self.physical(cruise=True)
    self.assertTrue(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .1})))
    self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x2FF, 0, bytes(7))))
    # An ignored malformed copy neither replaces nor refreshes the still-valid required source.
    self.assertTrue(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .1})))
    self.safety.set_timer(1_200_001)
    self.base.physical(cruise=True)
    self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x2FF, 0, bytes(7))))
    self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"ACCEL_CMD": .1})))
    self.assertTrue(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("ACC_CONTROL", 0, {"CANCEL_REQ": 1})))

  def test_sdsu_expiry_boundary_with_other_sources_kept_live(self):
    self.init(0x9049, 32)
    self.physical(cruise=True)
    for step in range(1, 22):
      self.safety.set_timer(1_000_000 + step * 10_000)
      self.base.physical(cruise=True)
      self.assertEqual(self.safety.aol_get_permission_mask(), 3 if step <= 20 else 0)

  def test_shared_workspace_repeated_normal_alt_filtered_switches(self):
    sequence = (73, 329, 0x9049, 73, 329, 0x9249, 73, 0x9049, 73)
    for word in sequence:
      self.init(word, 32)
      config = self.safety.current_safety_config
      expected = [(0xAA, 8, 83, False), (0x260, 8, 50, True), (0x1D2, 8, 33, True),
                  (0x224 if word == 329 else 0x226, 8, 40, True)]
      if word in (0x9049, 0x9249):
        expected.append((0x2FF, 8, 50, False))
      actual = []
      for index in range(config.rx_checks_len):
        check = config.rx_checks[index]
        msg = check.msg[0]
        actual.append((msg.addr, msg.len, msg.frequency, bool(msg.ignore_quality_flag)))
        self.assertEqual(msg.bus, 0)
        self.assertFalse(self.safety.rx_status[index].msg_seen)
        self.assertEqual(self.safety.rx_status[index].last_timestamp, 0)
        self.assertEqual(self.safety.rx_status[index].wrong_counters, 0)
        self.assertFalse(self.safety.rx_status[index].lagging)
      self.assertEqual(actual, expected)
      self.base.physical(cruise=True)
      if word == 329:
        self.rx(0x224, bytes(8))
      if word in (0x9049, 0x9249):
        self.assertFalse(self.safety.safety_config_valid())
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.rx(0x2FF, bytes(8))
        self.assertEqual(self.safety.aol_get_permission_mask(), 1 if word == 0x9249 else 3)
      self.assertTrue(self.safety.safety_config_valid())
