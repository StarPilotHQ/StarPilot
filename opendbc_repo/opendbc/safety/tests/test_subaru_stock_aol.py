"""Real Subaru native permission, without forcing controlsAllowed."""
import unittest

from opendbc.car import structs
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py


class TestSubaruStockAol(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety

  def configure(self, param=177, experience=32):
    self.safety = libsafety_py.libsafety
    self.safety.init_tests()
    self.safety.set_alternative_experience(experience)
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.subaru, param), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.packer = CANPackerSafety('subaru_global_2017_generated')
    self.param = param
    self.now = 1_000_000
    self.safety.set_timer(self.now)

  def tearDown(self):
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def frame(self, name, values, bus=0):
    return self.packer.make_can_msg_safety(name, bus, values)

  def feed(self, *, main=True, cruise=False, brake=False, gear=121, temporary=False, permanent=False,
           gear_bus=None, omit_gear=False, host_mask=1, omit_main=False, speed=54, acc_fault=False, omit_distance=False):
    self.now += 10_000
    self.safety.set_timer(self.now)
    alt = 1 if self.param & 1 else 0
    main_bus = 1 if self.param in (209, 465) else 0
    if gear_bus is None:
      gear_bus = main_bus
    frames = [
      ('Throttle', {}, main_bus),
      ('Steering_Torque', {'Steer_Error_1': int(permanent), 'Steer_Warning': int(temporary)}, 0),
      ('Steering_2', {}, 0),
      ('Wheel_Speeds', dict.fromkeys(('FL', 'FR', 'RL', 'RR'), speed), alt),
      ('Brake_Status', {'Brake': int(brake)}, alt),
      ('CruiseControl', {'Cruise_On': int(main), 'Cruise_Activated': int(cruise)}, alt),
      ('ES_Status', {'Cruise_Activated': int(cruise)}, 2 if self.param == 16 else 1),
      ('ES_DashStatus', {'Cruise_On': int(main)}, 2),
    ]
    if not omit_distance and self.param in (17, 209, 465, 4096, 4104, 4608, 4616):
      frames.append(('ES_Distance', {'Cruise_Fault': int(acc_fault)}, 1 if self.param in (17, 209, 465) else 2))
    if not omit_gear:
      frames.append(('Transmission', {'Gear': gear}, gear_bus))
    for name, fields, bus in frames:
      if omit_main and name == 'ES_DashStatus':
        continue
      self.safety.safety_rx_hook(self.frame(name, fields, bus))
    self.safety.safety_tick()
    self.safety.aol_set_host_request(host_mask)
    return self.safety.aol_get_permission_mask()

  def arm(self):
    for _ in range(12):
      self.feed(cruise=False)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertEqual(self.feed(cruise=True), 1)
    self.assertTrue(self.safety.get_controls_allowed())

  def test_all_stock_profiles_full_engagement_brake_reverse_drive_main_off(self):
    for param in (0, 1, 16, 17, 145, 177, 209, 465, 2225, 4096, 4104, 4608, 4616):
      with self.subTest(param=param):
        self.configure(param)
        self.arm()
        self.assertEqual(self.feed(brake=True), 1)
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertEqual(self.feed(gear=3), 0)
        self.assertEqual(self.feed(gear=121), 1)
        self.assertFalse(self.safety.get_controls_allowed())
        self.assertEqual(self.feed(temporary=True), 0)
        self.assertEqual(self.feed(), 1)
        self.assertEqual(self.feed(main=False), 0)
        self.assertEqual(self.feed(main=True), int(param in (17, 209, 465, 4096, 4104, 4608, 4616)))
        self.assertEqual(self.feed(cruise=True), 1)

  def test_permanent_fault_and_rx_fault_require_fresh_ordinary_engagement(self):
    self.configure()
    self.arm()
    self.assertEqual(self.feed(permanent=True, brake=True), 0)
    self.assertEqual(self.feed(), 0)
    self.assertEqual(self.feed(cruise=True), 1)
    bad = self.frame('Steering_Torque', {})
    # Mutate the wire checksum without recomputing it.
    raw = bytearray(bytes(bad.data))
    raw[0] ^= 1
    malformed = libsafety_py.make_CANPacket(0x119, 0, bytes(raw[:8]))
    self.assertFalse(self.safety.safety_rx_hook(malformed))
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_additional_gear_counter_checksum_bus_and_freshness(self):
    self.configure()
    for _ in range(12):
      self.feed(cruise=True, gear_bus=1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.assertEqual(self.feed(cruise=True), 0)
    self.assertEqual(self.feed(cruise=True), 0)
    # Correct gear alone cannot restore the lost physical engagement token.
    self.assertEqual(self.feed(cruise=False), 0)
    self.assertEqual(self.feed(cruise=True), 1)
    gear = self.frame('Transmission', {'Gear': 121})
    self.safety.safety_rx_hook(gear)
    for _ in range(12):
      self.feed(cruise=False, omit_gear=True)
      self.safety.safety_rx_hook(gear) # a replay must not renew its original receipt
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    bad = self.frame('Transmission', {'Gear': 121})
    raw = bytearray(bytes(bad.data))
    raw[0] ^= 1
    self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x48, 0, bytes(raw[:8])))
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_stock_off_host_timeout_heartbeat_and_no_longitudinal_grant(self):
    self.configure()
    self.arm()
    self.safety.aol_set_host_request(3)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.safety.aol_set_host_request(0)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_timer(self.now + 301_000)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(False)
    self.assertEqual(self.feed(cruise=True), 0)
    self.configure(experience=0)
    self.assertEqual(self.feed(cruise=True), 0) # ordinary controls is separate from AOL permission

  def test_real_angle_and_torque_tx_keep_existing_limits(self):
    for param in (0, 1, 17, 145, 177):
      self.configure(param)
      self.arm()
      self.feed(brake=True)
      angle = bool(param & 16)
      bus = 0
      command = (self.frame('ES_LKAS_ANGLE', {'LKAS_Output': 0, 'LKAS_Request': 1}, bus) if angle else
                 self.frame('ES_LKAS', {'LKAS_Output': 1, 'LKAS_Request': 1}, bus))
      self.assertTrue(self.safety.safety_tx_hook(command))
      self.assertFalse(self.safety.safety_tx_hook(self.frame('ES_Distance', {'Cruise_Throttle': 2123}, 1 if param & 1 else 0)))
      self.feed(main=False)
      self.assertFalse(self.safety.safety_tx_hook(command))

  def test_main_uses_original_required_rx_lease(self):
    self.configure(145)
    self.arm()
    for _ in range(11):
      self.assertEqual(self.feed(omit_main=True), 1)
    # Required health still fails on the original native boundary; no MAIN lease renewal.
    self.now += 1_000_001
    self.safety.set_timer(self.now)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_conflicting_gear_duplicate_withdraws_without_renewing(self):
    self.configure(145)
    self.arm()
    drive = self.frame('Transmission', {'Gear': 121})
    self.safety.safety_rx_hook(drive)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    raw = bytearray(bytes(drive.data)[:8])
    raw[3] = 3  # Same counter, contradictory Reverse payload.
    raw[0] = (0x48 + sum(raw[1:])) & 255
    self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x48, 0, bytes(raw)))
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_permanent_fault_cannot_rearm_with_cruise_held_on(self):
    self.configure()
    self.arm()
    self.assertEqual(self.feed(permanent=True, cruise=True), 0)
    self.assertEqual(self.feed(permanent=False, cruise=True), 0)
    self.assertEqual(self.feed(cruise=False), 0)
    self.assertEqual(self.feed(cruise=True), 1)

  def test_unrecognized_words_cannot_acquire_independent_permission(self):
    for param in (2, 3, 48, 80, 210, 211, 241, 464, 65535):
      self.configure(param)
      for _ in range(12):
        self.feed(cruise=False)
      self.feed(cruise=True)
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_physical_engagement_pending_host_join_and_expiry(self):
    self.configure()
    self.safety.set_aol_test_heartbeat(False)
    for _ in range(12):
      self.feed(cruise=False)
    self.assertEqual(self.feed(cruise=True), 0)
    for _ in range(8):
      self.assertEqual(self.feed(cruise=True), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.assertEqual(self.feed(cruise=True), 1)
    self.configure()
    self.safety.set_aol_test_heartbeat(False)
    for _ in range(12):
      self.feed(cruise=False)
    self.assertEqual(self.feed(cruise=True), 0)
    for _ in range(31):
      self.assertEqual(self.feed(cruise=True), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.assertEqual(self.feed(cruise=True), 0)
    self.assertEqual(self.feed(cruise=False), 0)
    self.assertEqual(self.feed(cruise=True), 1)
