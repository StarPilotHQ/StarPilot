import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.hyundaicanfd import (CanBus, create_adrv_messages, create_ioniq6_radar_heartbeat,
                                              create_ioniq6_blindspot_status, create_steering_messages, create_lfahda_cluster, hkg_can_fd_checksum)
from opendbc.car.hyundai.ioniq6_bsm import BlindspotStatus
from opendbc.car.hyundai.canfd_dash_icons import CanFDDashIcons
from opendbc.car.hyundai.ioniq6_handoff import build_ioniq6_hda2_long_candidate
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.car.hyundai.tests.test_ioniq6_longitudinal import controller_fixture, LongState


RAW_LONG = {"lkas": 0x8015, "lkas_alt": 0x8095}
RAW_AOL = {"lkas": 0x8815, "lkas_alt": 0x8895}
TESTER_PRESENT = libsafety_py.make_CANPacket(0x730, 1, b"\x02\x3e\x80\x00\x00\x00\x00\x00")


def params(topology):
  fingerprint = gen_empty_fingerprint()
  camera_addr, camera_len = (0x50, 16) if topology == "lkas" else (0x110, 32)
  support_addr, support_len = (0x2A4, 24) if topology == "lkas" else (0x362, 32)
  fingerprint[2].update({camera_addr: camera_len, support_addr: support_len})
  fingerprint[1].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                         0x1BA: 24, 0x1E5: 16, 0x36A: 16})
  fingerprint[0][0x3A5] = 24
  stock = CarInterface.get_params(CAR.HYUNDAI_IONIQ_6, fingerprint, [], False, False, False)
  return stock, build_ioniq6_hda2_long_candidate(stock, fingerprint)


class TestHyundaiIoniq6Long(unittest.TestCase):
  @staticmethod
  def packet(frame):
    return libsafety_py.make_CANPacket(frame[0], frame[2], frame[1])

  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.release = self.safety.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) != 0

  def mode(self, raw, *, init_tests=True, stamp=1_000_000):
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, raw), 0)
    if init_tests:
      self.safety.init_tests()
    self.safety.set_timer(stamp)

  def refresh_required_rx(self, packer, counter, stamp):
    self.safety.set_timer(stamp)
    for name, values in (("ACCELERATOR", {"GEAR": 5}), ("TCS", {}), ("WHEEL_SPEEDS", {}),
                         ("MDPS", {}), ("CRUISE_BUTTONS", {})):
      self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
        name, 1, {**values, "COUNTER": counter % (16 if name == "CRUISE_BUTTONS" else 256)}))), name)

  def bsm_physical(self, packer, *, left=False, right=False, left_lamp=False, right_lamp=False,
                   alternate=False, counter=1, stamp=1_000_000):
    self.refresh_required_rx(packer, counter, stamp)
    corner = packer.make_can_msg("BLINDSPOTS_FRONT_CORNER_2", 1, {
      "COUNTER": counter, "SIDE_DETECT_STATE": (0x10 if left else 0) | (0x08 if right else 0),
    })
    lamp = packer.make_can_msg("BLINKERS", 1, {
      "USE_ALT_LAMP": int(alternate),
      ("LEFT_LAMP_ALT" if alternate else "LEFT_LAMP"): int(left_lamp),
      ("RIGHT_LAMP_ALT" if alternate else "RIGHT_LAMP"): int(right_lamp),
    })
    self.assertTrue(self.safety.safety_rx_hook(self.packet(corner)))
    self.assertTrue(self.safety.safety_rx_hook(self.packet(lamp)))
    return corner, lamp

  def test_bsm_all_sixteen_physical_states_and_exact_paired_frames(self):
    if self.release:
      self.skipTest("RELEASE denies all four exact Ioniq 6 LONG profiles")
    for raw in (*RAW_LONG.values(), *RAW_AOL.values()):
      stock, cp = params("lkas_alt" if raw & 0x80 else "lkas")
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      for left in (False, True):
        for right in (False, True):
          for left_lamp in (False, True):
            for right_lamp in (False, True):
              with self.subTest(raw=hex(raw), sources=(left, right, left_lamp, right_lamp)):
                self.mode(raw)
                status = BlindspotStatus((1 + int(left_lamp)) if left else 0,
                                         (1 + int(right_lamp)) if right else 0)
                rear, front = create_ioniq6_blindspot_status(7, status)
                self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))
                self.bsm_physical(packer, left=left, right=right, left_lamp=left_lamp,
                                  right_lamp=right_lamp, alternate=left_lamp, counter=1)
                self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))
                self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
                self.assertTrue(self.safety.safety_tx_hook(self.packet(front)))
                self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))
                self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))

  def test_bsm_alternate_lamp_selected_but_off_cannot_escalate(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG status profile")
    self.mode(RAW_LONG["lkas"])
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    # USE_ALT selects byte 7 even when its left lamp bit is clear. The regular
    # lamp bit is not the selected source and cannot authorize a level-2 warning.
    self.bsm_physical(packer, left=True, left_lamp=False, alternate=True)
    rear, front = create_ioniq6_blindspot_status(1, BlindspotStatus(1, 0))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(front)))
    escalated, _ = create_ioniq6_blindspot_status(2, BlindspotStatus(2, 0))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(escalated)))

  def test_bsm_unselected_dashboard_bits_cannot_escalate(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG status profile")
    # Each valid selected lamp remains OFF. Unselected source bits and reserved
    # low bits in the same physical 0x413 must never create a level-2 warning.
    cases = (
      (True, False, False, 2, 0x01),   # left regular lamp: unrelated byte-2 bit
      (False, True, False, 2, 0x01),   # right regular lamp: unrelated byte-2 bit
      (True, False, False, 7, 0x09),   # alternate left ON, but selector OFF plus noise
      (True, False, True, 7, 0x01),    # alternate selected, left OFF plus noise
      (False, True, True, 7, 0x01),    # alternate selected, right OFF plus noise
    )
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    for left, right, alternate, byte, bitmask in cases:
      with self.subTest(left=left, right=right, alternate=alternate, byte=byte, bits=bitmask):
        self.mode(RAW_LONG["lkas"])
        _, lamp = self.bsm_physical(packer, left=left, right=right, alternate=alternate)
        payload = bytearray(lamp[1])
        payload[byte] |= bitmask
        self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x413, 1, payload)))
        escalated = BlindspotStatus(2 if left else 0, 2 if right else 0)
        forged_rear, _ = create_ioniq6_blindspot_status(1, escalated)
        self.assertFalse(self.safety.safety_tx_hook(self.packet(forged_rear)))
        expected = BlindspotStatus(1 if left else 0, 1 if right else 0)
        rear, front = create_ioniq6_blindspot_status(1, expected)
        self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
        self.assertTrue(self.safety.safety_tx_hook(self.packet(front)))

  def test_bsm_integrity_pairing_freshness_and_immediate_stock_recurrence(self):
    if self.release:
      self.skipTest("RELEASE denies all four exact Ioniq 6 LONG profiles")
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    rear, front = create_ioniq6_blindspot_status(3, BlindspotStatus(2, 0))

    self.mode(RAW_LONG["lkas"])
    corner, lamp = self.bsm_physical(packer, left=True, left_lamp=True)
    for bad in ((rear[0], rear[1], 0), (rear[0], rear[1][:16], 1)):
      self.assertFalse(self.safety.safety_tx_hook(self.packet(bad)))
    corrupted = bytearray(rear[1])
    corrupted[3] ^= 1
    self.assertFalse(self.safety.safety_tx_hook(self.packet((rear[0], bytes(corrupted), 1))))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
    self.safety.set_timer(1_010_001)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))  # paired deadline
    self.bsm_physical(packer, left=True, left_lamp=True, counter=2, stamp=1_020_000)
    rear, front = create_ioniq6_blindspot_status(4, BlindspotStatus(2, 0))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(front)))

    self.mode(RAW_LONG["lkas"])
    self.bsm_physical(packer, left=True, left_lamp=True)
    rear, front = create_ioniq6_blindspot_status(3, BlindspotStatus(2, 0))
    self.safety.set_timer(1_100_001)  # corner age > 100 ms
    self.refresh_required_rx(packer, 2, 1_100_001)
    self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))
    self.mode(RAW_LONG["lkas"])
    self.bsm_physical(packer, left=True, left_lamp=True)
    self.safety.set_timer(2_500_001)  # lamp age > 1.5 s
    self.refresh_required_rx(packer, 2, 2_500_001)
    self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "BLINDSPOTS_FRONT_CORNER_2", 1, {"COUNTER": 2, "SIDE_DETECT_STATE": 0x10}))))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))

    for addr, length in ((0x1BA, 24), (0x1E5, 16)):
      for source_len in (length, 8):
        with self.subTest(source=hex(addr), source_len=source_len):
          self.mode(RAW_LONG["lkas"])
          self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, 1, bytes(source_len))))
          self.assertTrue(self.safety.get_relay_malfunction())
          self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))

  def test_bsm_legacy_indicator_sound_alias_is_rejected(self):
    for raw in (*RAW_LONG.values(), *RAW_AOL.values()):
      for left_lamp in (False, True):
        with self.subTest(raw=hex(raw), left_lamp=left_lamp):
          self.mode(raw)
          _, cp = params("lkas_alt" if raw & 0x80 else "lkas")
          packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
          self.bsm_physical(packer, left=True, left_lamp=left_lamp)
          level = 1 + int(left_lamp)
          rear, front = create_ioniq6_blindspot_status(3, BlindspotStatus(level, 0))
          self.assertEqual(rear[1][5], 0)
          legacy = bytearray(rear[1])
          legacy[5] = level << 1
          legacy[:2] = hkg_can_fd_checksum(0x1BA, None, legacy).to_bytes(2, "little")
          self.assertFalse(self.safety.safety_tx_hook(self.packet((0x1BA, bytes(legacy), 1))))
          self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))
          if self.release:
            self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))
            self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))
          else:
            self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
            self.assertTrue(self.safety.safety_tx_hook(self.packet(front)))

  def test_bsm_valid_crc_cannot_hide_altered_status_body(self):
    if self.release:
      self.skipTest("RELEASE denies all four exact Ioniq 6 LONG profiles")
    for raw in (*RAW_LONG.values(), *RAW_AOL.values()):
      with self.subTest(raw=hex(raw)):
        self.mode(raw)
        _, cp = params("lkas_alt" if raw & 0x80 else "lkas")
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        self.bsm_physical(packer, left=True, left_lamp=True)
        rear, front = create_ioniq6_blindspot_status(3, BlindspotStatus(2, 0))

        altered_rear = bytearray(rear[1])
        altered_rear[3] ^= 1
        altered_rear[:2] = hkg_can_fd_checksum(0x1BA, None, altered_rear).to_bytes(2, "little")
        self.assertFalse(self.safety.safety_tx_hook(self.packet((0x1BA, bytes(altered_rear), 1))))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))

        self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
        altered_front = bytearray(front[1])
        altered_front[11] ^= 1
        altered_front[:2] = hkg_can_fd_checksum(0x1E5, None, altered_front).to_bytes(2, "little")
        self.assertFalse(self.safety.safety_tx_hook(self.packet((0x1E5, bytes(altered_front), 1))))

  def test_bsm_boot_without_prior_lamp_cannot_claim_held_warning(self):
    if self.release:
      self.skipTest("RELEASE denies all four exact Ioniq 6 LONG profiles")
    for raw in (*RAW_LONG.values(), *RAW_AOL.values()):
      with self.subTest(raw=hex(raw)):
        self.mode(raw, stamp=100_000)
        _, cp = params("lkas_alt" if raw & 0x80 else "lkas")
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        # The hold timestamp is still zero during the first half-second after
        # startup; a fresh blind-spot detection cannot manufacture lamp history.
        self.bsm_physical(packer, left=True, left_lamp=False, stamp=100_000)
        escalated, _ = create_ioniq6_blindspot_status(3, BlindspotStatus(2, 0))
        indicated, _ = create_ioniq6_blindspot_status(3, BlindspotStatus(1, 0))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(escalated)))
        self.assertTrue(self.safety.safety_tx_hook(self.packet(indicated)))

  def test_bsm_lamp_release_hold_and_stale_gap_rearm(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG status profile")
    self.mode(RAW_LONG["lkas"])
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])

    def accept_status(level, counter):
      for frame in create_ioniq6_blindspot_status(counter, BlindspotStatus(level, 0)):
        self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)), (level, counter, hex(frame[0])))

    self.bsm_physical(packer, left=True, left_lamp=True, counter=1, stamp=1_000_000)
    accept_status(2, 1)
    self.bsm_physical(packer, left=True, left_lamp=False, counter=2, stamp=1_200_000)
    accept_status(2, 2)
    self.bsm_physical(packer, left=True, left_lamp=False, counter=3, stamp=1_600_000)
    accept_status(2, 3)  # repeated false did not restart the hold
    self.refresh_required_rx(packer, 4, 1_700_001)
    self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "BLINDSPOTS_FRONT_CORNER_2", 1, {"COUNTER": 4, "SIDE_DETECT_STATE": 0x10}))))
    accept_status(1, 4)

    self.bsm_physical(packer, left=True, left_lamp=True, counter=5, stamp=2_000_000)
    accept_status(2, 5)
    self.bsm_physical(packer, left=True, left_lamp=False, counter=6, stamp=3_500_001)
    accept_status(1, 6)  # a false after source expiry cannot hold old true

  def test_bsm_right_lamp_release_holds_only_for_bounded_interval(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG status profile")
    self.mode(RAW_LONG["lkas"])
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])

    def accept_right(level, counter):
      for frame in create_ioniq6_blindspot_status(counter, BlindspotStatus(0, level)):
        self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)), (level, counter, hex(frame[0])))

    self.bsm_physical(packer, right=True, right_lamp=True, counter=1, stamp=1_000_000)
    accept_right(2, 1)
    self.bsm_physical(packer, right=True, right_lamp=False, counter=2, stamp=1_200_000)
    accept_right(2, 2)  # falling right lamp starts a 500 ms hold
    self.bsm_physical(packer, right=True, right_lamp=False, counter=3, stamp=1_700_001)
    accept_right(1, 3)  # repeated off frames cannot extend that hold

  def test_bsm_wrong_length_lamp_invalidates_prior_source_until_rearmed(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG status profile")
    self.mode(RAW_LONG["lkas"])
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    _, lamp = self.bsm_physical(packer, right=True, right_lamp=True)
    rear, front = create_ioniq6_blindspot_status(1, BlindspotStatus(0, 2))
    self.assertTrue(self.safety.safety_rx_hook(self.packet((lamp[0], lamp[1][:7], lamp[2]))))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))
    self.bsm_physical(packer, right=True, right_lamp=True, counter=2, stamp=1_010_000)
    self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(front)))

  def test_bsm_bad_corner_checksum_or_replay_invalidates_prior_detection(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG status profile")
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    for corruption in ("checksum", "replay"):
      with self.subTest(corruption=corruption):
        self.mode(RAW_LONG["lkas"])
        corner, _ = self.bsm_physical(packer, right=True, right_lamp=True)
        rear, front = create_ioniq6_blindspot_status(1, BlindspotStatus(0, 2))
        invalid = bytearray(corner[1])
        if corruption == "checksum":
          invalid[3] ^= 0x08  # preserve the old checksum while changing detection
        self.assertTrue(self.safety.safety_rx_hook(self.packet((corner[0], bytes(invalid), corner[2]))))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))
        self.bsm_physical(packer, right=True, right_lamp=True, counter=2, stamp=1_010_000)
        self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
        self.assertTrue(self.safety.safety_tx_hook(self.packet(front)))

  def test_bsm_stale_required_rx_invalidates_fresh_optional_sources(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG status profile")
    self.mode(RAW_LONG["lkas"])
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    self.bsm_physical(packer, right=True, right_lamp=True, counter=1, stamp=1_000_000)
    # Keep optional evidence and every required stream except MDPS fresh.
    self.safety.set_timer(2_200_001)
    for name, values in (("ACCELERATOR", {"GEAR": 5}), ("TCS", {}),
                         ("WHEEL_SPEEDS", {}), ("CRUISE_BUTTONS", {}),
                         ("BLINDSPOTS_FRONT_CORNER_2", {"SIDE_DETECT_STATE": 0x08}),
                         ("BLINKERS", {"RIGHT_LAMP": 1})):
      self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
        name, 1, {**values, "COUNTER": 2} if name != "BLINKERS" else values))), name)
    rear, _ = create_ioniq6_blindspot_status(1, BlindspotStatus(0, 2))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))
    self.safety.safety_tick()
    self.assertFalse(self.safety.safety_tx_hook(TESTER_PRESENT))  # stale required RX blocks even diagnostic TX
    self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg("MDPS", 1, {"COUNTER": 2}))))
    self.safety.safety_tick()
    self.assertTrue(self.safety.safety_tx_hook(TESTER_PRESENT))
    self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))

  @staticmethod
  def bsm_wheel_frame(packer, raw_speeds, counter):
    values = {name: raw * 0.03125 for name, raw in zip(
      ("WHL_SpdFLVal", "WHL_SpdFRVal", "WHL_SpdRLVal", "WHL_SpdRRVal"), raw_speeds, strict=True)}
    return packer.make_can_msg("WHEEL_SPEEDS", 1, {**values, "COUNTER": counter})

  def test_bsm_neutral_only_below_physical_speed_boundary(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profiles")
    # 5 mph is between representable sums 1029 and 1030; test unequal wheels too.
    samples = ((0, 0, 0, 0), (257, 257, 257, 258), (257, 257, 258, 258), (258, 258, 258, 258))
    for raw in (*RAW_LONG.values(), *RAW_AOL.values()):
      _, cp = params("lkas_alt" if raw & 0x80 else "lkas")
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      self.mode(raw)
      self.bsm_physical(packer, left=True, right=True, left_lamp=True, right_lamp=True)
      for counter, speeds in enumerate((*samples, *reversed(samples)), 2):
        with self.subTest(profile=hex(raw), speeds=speeds):
          self.assertTrue(self.safety.safety_rx_hook(self.packet(self.bsm_wheel_frame(packer, speeds, counter))))
          rear, front = create_ioniq6_blindspot_status(counter, BlindspotStatus(0, 0))
          permitted = sum(speeds) < 1030
          self.assertEqual(self.safety.safety_tx_hook(self.packet(rear)), permitted)
          self.assertEqual(self.safety.safety_tx_hook(self.packet(front)), permitted)
          # Original physical indications remain valid, independently of speed.
          for frame in create_ioniq6_blindspot_status(counter + 30, BlindspotStatus(2, 2)):
            self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)))
      # Every altered body byte remains forbidden even with a repaired CRC.
      self.assertTrue(self.safety.safety_rx_hook(self.packet(self.bsm_wheel_frame(packer, (0, 0, 0, 0), 10))))
      rear, front = create_ioniq6_blindspot_status(80, BlindspotStatus(0, 0))
      for byte in range(3, 24):
        payload = bytearray(rear[1])
        payload[byte] ^= 1
        payload[:2] = hkg_can_fd_checksum(0x1BA, None, payload).to_bytes(2, "little")
        self.assertFalse(self.safety.safety_tx_hook(self.packet((0x1BA, bytes(payload), 1))), byte)
        self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))

  def test_bsm_neutral_requires_seen_fresh_nonreplayed_valid_wheels(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profiles")
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])

    def other_sources(counter, stamp):
      self.safety.set_timer(stamp)
      for name, values in (("ACCELERATOR", {"GEAR": 5}), ("TCS", {}), ("MDPS", {}),
                           ("CRUISE_BUTTONS", {}), ("BLINDSPOTS_FRONT_CORNER_2", {"SIDE_DETECT_STATE": 0x18}),
                           ("BLINKERS", {"LEFT_LAMP": 1, "RIGHT_LAMP": 1})):
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          name, 1, {**values, "COUNTER": counter} if name != "BLINKERS" else values))))

    for raw in (*RAW_LONG.values(), *RAW_AOL.values()):
      with self.subTest(profile=hex(raw)):
        self.mode(raw)
        other_sources(1, 1_000_000)
        neutral = create_ioniq6_blindspot_status(1, BlindspotStatus(0, 0))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(neutral[0])))  # unseen, not implicit zero
        wheel = self.bsm_wheel_frame(packer, (0, 0, 0, 0), 1)
        self.assertTrue(self.safety.safety_rx_hook(self.packet(wheel)))
        other_sources(2, 1_100_000)
        for frame in neutral:
          self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)))
        other_sources(3, 1_100_001)
        rear, front = create_ioniq6_blindspot_status(2, BlindspotStatus(0, 0))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))  # one microsecond stale
        self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))
        self.safety.safety_rx_hook(self.packet(wheel))  # checksum-valid replay must not refresh permission
        self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))
        wheel = self.bsm_wheel_frame(packer, (0, 0, 0, 0), 2)
        self.assertTrue(self.safety.safety_rx_hook(self.packet(wheel)))
        payload = bytearray(self.bsm_wheel_frame(packer, (0, 0, 0, 0), 3)[1])
        payload[0] ^= 1
        self.assertFalse(self.safety.safety_rx_hook(self.packet((0xA0, bytes(payload), 1))))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))
        self.assertTrue(self.safety.safety_rx_hook(self.packet(self.bsm_wheel_frame(packer, (0, 0, 0, 0), 4))))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))  # missing valid counter 3
        self.assertTrue(self.safety.safety_rx_hook(self.packet(self.bsm_wheel_frame(packer, (0, 0, 0, 0), 5))))
        self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
        self.assertTrue(self.safety.safety_tx_hook(self.packet(front)))
        # Wheel evidence must remain fresh for the second half of the pair too.
        other_sources(4, 1_200_001)
        rear, front = create_ioniq6_blindspot_status(3, BlindspotStatus(0, 0))
        self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
        self.safety.set_timer(1_200_002)
        self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))
        # A low-speed packet on the wrong route or with the wrong length cannot
        # replace a valid above-threshold physical wheel observation.
        other_sources(5, 1_210_000)
        self.assertTrue(self.safety.safety_rx_hook(self.packet(self.bsm_wheel_frame(packer, (300, 300, 300, 300), 5))))
        low = self.bsm_wheel_frame(packer, (0, 0, 0, 0), 6)
        for invalid in ((low[0], low[1], 0), (low[0], low[1][:8], 1)):
          self.safety.safety_rx_hook(self.packet(invalid))
          self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))

  def test_bsm_neutral_rejects_backward_or_skipped_wheel_counters(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profiles")
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    for raw in (*RAW_LONG.values(), *RAW_AOL.values()):
      for invalid_counter in (0, 1, 4):
        with self.subTest(profile=hex(raw), invalid_counter=invalid_counter):
          self.mode(raw)
          self.bsm_physical(packer, left=True, right=True, counter=1)
          self.assertTrue(self.safety.safety_rx_hook(self.packet(self.bsm_wheel_frame(packer, (300, 300, 300, 300), 2))))
          self.safety.safety_rx_hook(self.packet(self.bsm_wheel_frame(packer, (0, 0, 0, 0), invalid_counter)))
          rear, front = create_ioniq6_blindspot_status(1, BlindspotStatus(0, 0))
          self.assertFalse(self.safety.safety_tx_hook(self.packet(rear)))
          self.assertFalse(self.safety.safety_tx_hook(self.packet(front)))
          self.assertTrue(self.safety.safety_rx_hook(self.packet(self.bsm_wheel_frame(packer, (0, 0, 0, 0), invalid_counter + 1))))
          self.assertTrue(self.safety.safety_tx_hook(self.packet(rear)))
          self.assertTrue(self.safety.safety_tx_hook(self.packet(front)))
      self.mode(raw)
      self.bsm_physical(packer, left=True, right=True, counter=255)
      self.assertTrue(self.safety.safety_rx_hook(self.packet(self.bsm_wheel_frame(packer, (0, 0, 0, 0), 0))))
      for frame in create_ioniq6_blindspot_status(0, BlindspotStatus(0, 0)):
        self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)))

  def test_bsm_neutral_does_not_expand_other_profiles(self):
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    for raw in (17, 145, 0x9015, 0x9095):
      with self.subTest(profile=hex(raw)):
        self.mode(raw)
        self.bsm_physical(packer, left=True, right=True)
        for frame in create_ioniq6_blindspot_status(1, BlindspotStatus(0, 0)):
          self.assertFalse(self.safety.safety_tx_hook(self.packet(frame)))

  def test_actual_parser_controller_bsm_pair_passes_native_all_four_profiles(self):
    if self.release:
      self.skipTest("RELEASE denies all four Ioniq 6 LONG profiles")
    for topology in ("lkas", "lkas_alt"):
      for aol in (False, True):
        for raw_speeds in ((0, 0, 0, 0), (257, 257, 257, 258), (257, 257, 258, 258), (300, 300, 300, 300)):
          with self.subTest(topology=topology, aol=aol, speeds=raw_speeds):
            _, cp = params(topology)
            raw = (RAW_AOL if aol else RAW_LONG)[topology]
            cp.safetyConfigs[-1].safetyParam = raw
            ci = CarInterface(cp)
            controller = CarController(DBC[cp.carFingerprint], cp)
            packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
            bus = CanBus(cp)
            inputs = [packer.make_can_msg(name, bus.ECAN, values) for name, values in (
              ("ACCELERATOR", {"GEAR": 5, "COUNTER": 1}), ("TCS", {"COUNTER": 1}),
              ("WHEEL_SPEEDS", {"COUNTER": 1}), ("MDPS", {"COUNTER": 1}),
              ("CRUISE_BUTTONS", {"COUNTER": 1}),
              ("BLINDSPOTS_FRONT_CORNER_2", {"COUNTER": 1, "SIDE_DETECT_STATE": 0x18}),
              ("BLINKERS", {"LEFT_LAMP": 1}),
            )]
            inputs = [frame for frame in inputs if frame[0] != 0xA0]
            inputs.append(self.bsm_wheel_frame(packer, raw_speeds, 1))
            inputs.append(packer.make_can_msg("CAM_0x362" if topology == "lkas_alt" else "CAM_0x2a4", bus.CAM, {}))
            ci.update([(1_000_000_000, inputs)])
            self.mode(raw)
            for frame in inputs:
              if frame[2] == bus.ECAN:
                self.assertTrue(self.safety.safety_rx_hook(self.packet(frame)), hex(frame[0]))
            control = structs.CarControl()
            _, sent = controller.update(control.as_reader(), ci.CS, 1_000_000_000)
            pair = [frame for frame in sent if frame[0] in (0x1BA, 0x1E5)]
            expected = BlindspotStatus(0, 0) if sum(raw_speeds) < 1030 else BlindspotStatus(2, 1)
            self.assertEqual(pair, create_ioniq6_blindspot_status(0, expected))
            for frame in pair:
              self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)), (topology, aol, hex(frame[0])))

  def test_exhaustive_angle_bit_namespace_and_release_denial(self):
    for low in range(0x8000):
      raw = 0x8000 | low
      self.mode(raw)
      allowed = self.safety.safety_tx_hook(TESTER_PRESENT)
      self.assertEqual(allowed, not self.release and raw in (*RAW_LONG.values(), *RAW_AOL.values(), 0x9015, 0x9095), f"raw={raw:#06x}")

  def test_stock_profiles_and_untagged_siblings_keep_their_prior_tx(self):
    for topology, raw in (("lkas", 17), ("lkas_alt", 145)):
      self.mode(raw)
      self.assertFalse(self.safety.safety_tx_hook(TESTER_PRESENT))
      stock, _ = params(topology)
      packer = CANPacker(DBC[stock.carFingerprint][Bus.pt])
      steering = create_steering_messages(packer, stock, CanBus(stock), False, False, 0)[0]
      self.assertTrue(self.safety.safety_tx_hook(self.packet(steering)))

  def test_fingerprint_elm327_diagnostics_then_long_tester_authority(self):
    # pandad holds ELM327 param 1 through fingerprinting until ControlsReady.
    # The early UDS transaction uses diagnostic 0x730/8 only; non-diagnostic
    # replacement 0x100 must wait for exact Ioniq LONG safety arming.
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.elm327, 1), 0)
    for payload in (b"\x02\x10\x03\x00\x00\x00\x00\x00",
                    b"\x03\x28\x01\x01\x00\x00\x00\x00",
                    b"\x02\x3e\x80\x00\x00\x00\x00\x00"):
      self.assertTrue(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x730, 1, payload)))
    self.assertFalse(self.safety.safety_tx_hook(self.packet(create_ioniq6_radar_heartbeat(0, False, False))))
    self.mode(0x11)
    self.assertFalse(self.safety.safety_tx_hook(TESTER_PRESENT))
    if not self.release:
      self.mode(RAW_LONG["lkas"])
      self.assertTrue(self.safety.safety_tx_hook(TESTER_PRESENT))
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(
        0x730, 1, b"\x02\x10\x03\x00\x00\x00\x00\x00")))

  def test_joined_packed_controller_frames_and_late_stock_source_fence(self):
    if self.release:
      self.skipTest("RELEASE forbids raw Ioniq 6 LONG; denial is covered above")
    for topology, raw in RAW_LONG.items():
      with self.subTest(topology=topology):
        stock, long_cp = params(topology)
        self.assertFalse(stock.openpilotLongitudinalControl)
        self.assertTrue(long_cp.openpilotLongitudinalControl)
        self.assertEqual(long_cp.safetyConfigs[-1].safetyParam, raw)
        ci = CarInterface(long_cp)
        controller = CarController(DBC[long_cp.carFingerprint], long_cp)
        can_bus = CanBus(long_cp)
        packer = CANPacker(DBC[long_cp.carFingerprint][Bus.pt])
        cam_name = "CAM_0x362" if topology == "lkas_alt" else "CAM_0x2a4"
        inputs = [
          packer.make_can_msg("ACCELERATOR", can_bus.ECAN, {"GEAR": 5}),
          packer.make_can_msg("TCS", can_bus.ECAN, {"ACCEnable": 0, "ACC_REQ": 1}),
          packer.make_can_msg("WHEEL_SPEEDS", can_bus.ECAN, {key: 45 for key in
                                                             ("WHL_SpdFLVal", "WHL_SpdFRVal", "WHL_SpdRLVal", "WHL_SpdRRVal")}),
          packer.make_can_msg("MDPS", can_bus.ECAN, {}),
          packer.make_can_msg("STEERING_SENSORS", can_bus.ECAN, {}),
          packer.make_can_msg("DOORS_SEATBELTS", can_bus.ECAN, {"DRIVER_SEATBELT": 1}),
          packer.make_can_msg("CRUISE_BUTTONS", can_bus.ECAN, {"COUNTER": 1, "CRUISE_BUTTONS": 2}),
          packer.make_can_msg(cam_name, can_bus.CAM, {}),
        ]
        ci.update([(1_000_000_000, inputs)])
        self.assertTrue(ci.CS.out.canValid)
        self.mode(raw)
        for frame in inputs:
          if frame[2] == can_bus.ECAN and frame[0] in (0x35, 0x175, 0xA0, 0xEA, 0x1CF):
            self.assertTrue(self.safety.safety_rx_hook(self.packet(frame)), hex(frame[0]))
        self.assertTrue(self.safety.safety_config_valid())
        self.safety.set_controls_allowed(True)
        control = structs.CarControl()
        control.enabled = control.latActive = control.longActive = True
        control.actuators.torque = 0.02
        control.actuators.accel = 0.1
        _, sent = controller.update(control.as_reader(), ci.CS, 1_000_000_000)
        expected = {
          (0x110 if topology == "lkas_alt" else 0x50, 0, 32 if topology == "lkas_alt" else 16),
          (0x362 if topology == "lkas_alt" else 0x2A4, 0, 32 if topology == "lkas_alt" else 24),
          (0x12A, 1, 16), (0x1E0, 1, 16), (0x1A0, 1, 32), (0x51, 0, 32),
          (0x730, 1, 8), (0x160, 1, 16), (0x1EA, 1, 32), (0x200, 1, 8),
          (0x345, 1, 8), (0x1DA, 1, 32), (0x100, 0, 24),
        }
        self.assertEqual({(address, bus, len(payload)) for address, payload, bus in sent}, expected)
        for frame in sent:
          self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)), f"{topology} {hex(frame[0])} bus={frame[2]}")
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "SCC_CONTROL", can_bus.ECAN, {"COUNTER": 4, "ACCMode": 1}))))
        self.assertTrue(self.safety.get_relay_malfunction())
        self.assertFalse(self.safety.safety_tx_hook(TESTER_PRESENT))
        for frame in sent:
          self.assertFalse(self.safety.safety_tx_hook(self.packet(frame)))

  def test_dash_icons_keep_exact_native_profile_and_fresh_health_contract(self):
    for topology in RAW_LONG:
      _, cp = params(topology)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      bus = CanBus(cp)
      for raw in (RAW_LONG[topology], RAW_AOL[topology]):
        with self.subTest(topology=topology, raw=raw):
          self.mode(raw)
          admitted = not self.release
          icons = CanFDDashIcons()
          frames = []
          for tick, enabled, lateral_active, expected in ((0, False, False, 0), (1, False, True, 2),
                                                         (2, False, False, 3), (102, False, False, 0)):
            icon = icons.update(tick, enabled, lateral_active)
            self.assertEqual(icon, expected)
            frame = create_lfahda_cluster(packer, bus, enabled, lfa_icon=icon)
            self.assertEqual((frame[0], frame[2], len(frame[1])), (0x1E0, 1, 16))
            frames.append(frame)
          if admitted:
            self.refresh_required_rx(packer, 1, 1_000_000)
          for frame in frames:
            self.assertEqual(bool(self.safety.safety_tx_hook(self.packet(frame))), admitted)
            self.assertFalse(self.safety.safety_tx_hook(self.packet((frame[0], frame[1], 0))))
            self.assertFalse(self.safety.safety_tx_hook(self.packet((frame[0], frame[1][:8], frame[2]))))
          if admitted:
            self.safety.set_timer(2_200_001)
            self.safety.safety_tick()
            for frame in frames:
              self.assertFalse(self.safety.safety_tx_hook(self.packet(frame)))
            self.refresh_required_rx(packer, 2, 2_200_001)
            self.safety.safety_tick()
            self.assertTrue(self.safety.safety_tx_hook(self.packet(frames[2])))
            stock_scc = packer.make_can_msg("SCC_CONTROL", 1, {"COUNTER": 4, "ACCMode": 1})
            self.assertTrue(self.safety.safety_rx_hook(self.packet(stock_scc)))
            self.assertTrue(self.safety.get_relay_malfunction())
            for frame in frames:
              self.assertFalse(self.safety.safety_tx_hook(self.packet(frame)))

  def test_wrong_bus_length_and_withheld_status_frames(self):
    if self.release:
      self.skipTest("RELEASE denies the profile before TX allowlisting")
    for raw in RAW_LONG.values():
      self.mode(raw)
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1BA, 1, bytes(24))))
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1E5, 1, bytes(16))))
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0xCB, 1, bytes(24))))
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1A0, 0, bytes(32))))
      self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x1A0, 1, bytes(24))))

  def test_recorded_radar_heartbeat_exact_body_pedals_freshness_and_counter(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profiles")
    for raw in (*RAW_LONG.values(), *RAW_AOL.values()):
      with self.subTest(raw=hex(raw)):
        self.mode(raw)
        _, cp = params("lkas_alt" if raw & 0x80 else "lkas")
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        for name, values in (("ACCELERATOR", {"GEAR": 5, "COUNTER": 0}),
                             ("TCS", {"DriverBraking": 0, "COUNTER": 0}),
                             ("WHEEL_SPEEDS", {"COUNTER": 0}), ("MDPS", {"COUNTER": 0}),
                             ("CRUISE_BUTTONS", {"COUNTER": 0})):
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, 1, values))))
        neutral = create_ioniq6_radar_heartbeat(0, False, False)
        self.assertTrue(self.safety.safety_tx_hook(self.packet(neutral)))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(neutral)))  # exact replay
        self.assertTrue(self.safety.safety_tx_hook(self.packet(create_ioniq6_radar_heartbeat(1, False, False))))
        forged = bytearray(create_ioniq6_radar_heartbeat(2, False, False)[1])
        forged[13] ^= 1
        forged[:2] = hkg_can_fd_checksum(0x100, None, forged).to_bytes(2, "little")
        self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x100, 0, forged)))
        bad_crc = bytearray(create_ioniq6_radar_heartbeat(2, False, False)[1])
        bad_crc[0] ^= 1
        self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x100, 0, bad_crc)))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(create_ioniq6_radar_heartbeat(2, True, False))))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(create_ioniq6_radar_heartbeat(2, False, True))))
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "TCS", 1, {"DriverBraking": 1, "COUNTER": 1}))))
        self.assertTrue(self.safety.safety_tx_hook(self.packet(create_ioniq6_radar_heartbeat(2, True, False))))
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "ACCELERATOR", 1, {"GEAR": 5, "ACCELERATOR_PEDAL": 1, "COUNTER": 1}))))
        self.assertTrue(self.safety.safety_tx_hook(self.packet(create_ioniq6_radar_heartbeat(3, True, True))))
        self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x100, 1, neutral[1])))
        self.assertFalse(self.safety.safety_tx_hook(libsafety_py.make_CANPacket(0x100, 0, neutral[1][:16])))
        self.safety.set_timer(1_200_000)
        self.assertFalse(self.safety.safety_tx_hook(self.packet(create_ioniq6_radar_heartbeat(4, True, True))))

  def test_ioniq6_50hz_required_sources_remain_healthy_at_150ms(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profiles")
    for raw in (*RAW_LONG.values(), *RAW_AOL.values()):
      with self.subTest(raw=hex(raw)):
        self.mode(raw)
        _, cp = params("lkas_alt" if raw & 0x80 else "lkas")
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        for name, values in (("ACCELERATOR", {"GEAR": 5, "COUNTER": 0}),
                             ("TCS", {"COUNTER": 0}), ("WHEEL_SPEEDS", {"COUNTER": 0}),
                             ("MDPS", {"COUNTER": 0}), ("CRUISE_BUTTONS", {"COUNTER": 0})):
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, 1, values))))
        self.safety.set_timer(1_150_000)
        for name, values in (("ACCELERATOR", {"GEAR": 5, "COUNTER": 1}),
                             ("WHEEL_SPEEDS", {"COUNTER": 1}), ("MDPS", {"COUNTER": 1})):
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, 1, values))))
        # TCS and CRUISE_BUTTONS are the 50 Hz required sources; the 100 Hz
        # sources above are fresh. The native per-source bound is 200 ms.
        self.assertTrue(self.safety.safety_tx_hook(self.packet(create_ioniq6_radar_heartbeat(0, False, False))))
        self.safety.set_timer(1_201_000)
        self.assertFalse(self.safety.safety_tx_hook(self.packet(create_ioniq6_radar_heartbeat(1, False, False))))

  def test_aol_50hz_source_age_does_not_shortchange_healthy_long_permission(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    for topology, raw in RAW_AOL.items():
      with self.subTest(topology=topology):
        _, cp = params(topology)
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        self.mode(raw)
        self.safety.set_aol_test_heartbeat(True)
        self.refresh_required_rx(packer, 0, 1_000_000)
        for counter, button in ((1, 2), (2, 0)):
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "CRUISE_BUTTONS", 1, {"COUNTER": counter, "CRUISE_BUTTONS": button}))))
        self.assertTrue(self.safety.get_controls_allowed())
        self.safety.set_timer(1_150_000)
        for name, values in (("ACCELERATOR", {"GEAR": 5}), ("WHEEL_SPEEDS", {}), ("MDPS", {})):
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            name, 1, {**values, "COUNTER": 1}))))
        self.safety.aol_set_host_request(2)
        self.assertEqual(self.safety.aol_get_permission_mask(), 2)
        self.safety.set_timer(1_201_000)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_ioniq6_accel_raw_and_value_each_veto_out_of_range_tx(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profiles")
    for topology, raw in (*RAW_LONG.items(), *RAW_AOL.items()):
      with self.subTest(topology=topology, raw=hex(raw)):
        _, cp = params(topology)
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        self.mode(raw)
        self.refresh_required_rx(packer, 0, 1_000_000)
        self.safety.set_controls_allowed(True)
        if raw in RAW_AOL.values():
          self.safety.set_aol_test_heartbeat(True)
          self.safety.aol_set_host_request(2)

        def accel(raw_value, selected_value, selected_packer=packer):
          return self.packet(selected_packer.make_can_msg("SCC_CONTROL", 1, {
            "aReqRaw": raw_value, "aReqValue": selected_value, "ACCMode": 1,
          }))
        self.assertTrue(self.safety.safety_tx_hook(accel(0, 0)))
        for raw_value, selected_value in ((2.5, 0), (0, 2.5), (-4.0, 0), (0, -4.0)):
          self.assertFalse(self.safety.safety_tx_hook(accel(raw_value, selected_value)),
                           (raw_value, selected_value))

  def test_stock_radar_heartbeat_recurrence_is_immediate_only_in_exact_long_profile(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profiles")
    for raw in (*RAW_LONG.values(), *RAW_AOL.values()):
      self.mode(raw)
      self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x100, 1, bytes(24))))
      self.assertFalse(self.safety.get_relay_malfunction())
      self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x100, 0, bytes(8))))
      self.assertTrue(self.safety.get_relay_malfunction())
      self.assertFalse(self.safety.safety_tx_hook(self.packet(create_ioniq6_radar_heartbeat(0, False, False))))
    self.mode(17)
    self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x100, 0, bytes(8))))
    self.assertFalse(self.safety.get_relay_malfunction())

  def test_stock_scc_recurrence_is_immediate_even_with_bad_length_or_crc(self):
    if self.release:
      self.skipTest("RELEASE denies the profile before relay checks")
    for raw in RAW_LONG.values():
      for payload in (bytes(32), bytes(8), b"\xff" * 32):
        with self.subTest(raw=raw, length=len(payload), first=payload[0]):
          self.mode(raw, init_tests=False)  # safety_mode_cnt remains zero
          self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x1A0, 1, payload)))
          self.assertTrue(self.safety.get_relay_malfunction())
          self.assertFalse(self.safety.safety_tx_hook(TESTER_PRESENT))
      self.mode(raw, init_tests=False)
      self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x1A0, 0, bytes(32))))
      self.assertFalse(self.safety.get_relay_malfunction())
      self.assertTrue(self.safety.safety_tx_hook(TESTER_PRESENT))

    # Ordinary generic LONG retains its existing transition grace.
    self.mode(21, init_tests=False)
    self.assertTrue(self.safety.safety_rx_hook(libsafety_py.make_CANPacket(0x1A0, 1, bytes(32))))
    self.assertFalse(self.safety.get_relay_malfunction())

  def test_second_steering_channel_is_one_fresh_mirror(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profile")
    for topology, raw in RAW_LONG.items():
      _, cp = params(topology)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      for torque, active in ((2, True), (0, False), (-2, True)):
        with self.subTest(topology=topology, torque=torque, active=active):
          self.mode(raw)
          for address, name, values in ((0x35, "ACCELERATOR", {"GEAR": 5}),
                                        (0x175, "TCS", {"ACCEnable": 0}),
                                        (0xA0, "WHEEL_SPEEDS", {}),
                                        (0xEA, "MDPS", {}),
                                        (0x1CF, "CRUISE_BUTTONS", {"COUNTER": 1})):
            frame = packer.make_can_msg(name, 1, values)
            self.assertEqual(frame[0], address)
            self.assertTrue(self.safety.safety_rx_hook(self.packet(frame)))
          self.safety.set_controls_allowed(True)
          lfa, lkas = create_steering_messages(packer, cp, CanBus(cp), True, active, torque)
          self.assertFalse(self.safety.safety_tx_hook(self.packet(lkas)))  # no independent A-CAN steering
          extra = bytearray(lfa[1])
          extra[9] ^= 0x01  # forbidden angle/enable side channel on limited LFA
          extra[:2] = hkg_can_fd_checksum(lfa[0], None, extra).to_bytes(2, "little")
          self.assertFalse(self.safety.safety_tx_hook(self.packet((lfa[0], bytes(extra), lfa[2]))))
          self.assertTrue(self.safety.safety_tx_hook(self.packet(lfa)))
          bad = bytearray(lkas[1])
          bad[5] ^= 0x04
          bad[:2] = hkg_can_fd_checksum(lkas[0], None, bad).to_bytes(2, "little")
          self.assertFalse(self.safety.safety_tx_hook(self.packet((lkas[0], bytes(bad), lkas[2]))))
          self.assertFalse(self.safety.safety_tx_hook(self.packet(lkas)))  # mismatch consumes the pair
          self.assertTrue(self.safety.safety_tx_hook(self.packet(lfa)))
          extra_lkas = bytearray(lkas[1])
          extra_lkas[14] ^= 0x01
          extra_lkas[:2] = hkg_can_fd_checksum(lkas[0], None, extra_lkas).to_bytes(2, "little")
          self.assertFalse(self.safety.safety_tx_hook(self.packet((lkas[0], bytes(extra_lkas), lkas[2]))))
          self.assertTrue(self.safety.safety_tx_hook(self.packet(lfa)))
          wrong_damping = bytearray(lkas[1])
          self.assertEqual(wrong_damping[8], 0)
          self.assertEqual(lfa[1][13], 100)
          wrong_damping[8] = 100
          wrong_damping[:2] = hkg_can_fd_checksum(lkas[0], None, wrong_damping).to_bytes(2, "little")
          self.assertFalse(self.safety.safety_tx_hook(self.packet((lkas[0], bytes(wrong_damping), lkas[2]))))
          self.assertFalse(self.safety.safety_tx_hook(self.packet(lkas)))  # rejected damping consumes the pair
          self.assertTrue(self.safety.safety_tx_hook(self.packet(lfa)))
          self.assertTrue(self.safety.safety_tx_hook(self.packet(lkas)))
          self.assertFalse(self.safety.safety_tx_hook(self.packet(lkas)))  # replay denied
          self.assertTrue(self.safety.safety_tx_hook(self.packet(lfa)))
          self.safety.set_timer(1_011_000)
          self.assertFalse(self.safety.safety_tx_hook(self.packet(lkas)))  # stale pair
          if topology == "lkas_alt":
            self.safety.set_timer(1_012_000)
            self.assertTrue(self.safety.safety_tx_hook(self.packet(lfa)))
            fca = bytearray(lkas[1])
            fca[13] |= 0x08
            fca[:2] = hkg_can_fd_checksum(lkas[0], None, fca).to_bytes(2, "little")
            self.assertFalse(self.safety.safety_tx_hook(self.packet((lkas[0], bytes(fca), lkas[2]))))

  def test_auxiliary_status_frames_reject_other_actuation_payloads(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profile")
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    for frame in create_adrv_messages(packer, CanBus(cp), 0):
      with self.subTest(address=hex(frame[0])):
        self.mode(RAW_LONG["lkas"])
        self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)))
        altered = bytearray(frame[1])
        altered[7] ^= 0x01
        altered[:2] = hkg_can_fd_checksum(frame[0], None, altered).to_bytes(2, "little")
        self.assertFalse(self.safety.safety_tx_hook(self.packet((frame[0], bytes(altered), frame[2]))))

  def test_110_actual_controller_cycles_keep_both_steering_channels_paired(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profile")
    for topology, raw in RAW_LONG.items():
      with self.subTest(topology=topology):
        _, cp = params(topology)
        ci = CarInterface(cp)
        controller = CarController(DBC[cp.carFingerprint], cp)
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        bus = CanBus(cp)
        self.mode(raw)
        control = structs.CarControl()
        control.enabled = control.latActive = control.longActive = True
        control.actuators.torque = 0.01
        control.actuators.accel = 0.1
        steering_pairs = 0
        for tick in range(110):
          self.safety.set_timer(1_000_000 + tick * 10_000)
          inputs = [
            packer.make_can_msg("ACCELERATOR", bus.ECAN, {"GEAR": 5}),
            packer.make_can_msg("TCS", bus.ECAN, {"ACCEnable": 0, "ACC_REQ": 1}),
            packer.make_can_msg("WHEEL_SPEEDS", bus.ECAN, {key: 45 for key in
                                                             ("WHL_SpdFLVal", "WHL_SpdFRVal", "WHL_SpdRLVal", "WHL_SpdRRVal")}),
            packer.make_can_msg("MDPS", bus.ECAN, {}),
            packer.make_can_msg("STEERING_SENSORS", bus.ECAN, {}),
            packer.make_can_msg("DOORS_SEATBELTS", bus.ECAN, {"DRIVER_SEATBELT": 1}),
            packer.make_can_msg("CRUISE_BUTTONS", bus.ECAN, {"CRUISE_BUTTONS": 2}),
            packer.make_can_msg("CAM_0x362" if topology == "lkas_alt" else "CAM_0x2a4", bus.CAM, {}),
          ]
          ci.update([(1_000_000_000 + tick * 10_000_000, inputs)])
          self.assertTrue(ci.CS.out.canValid)
          for frame in inputs:
            if frame[2] == bus.ECAN and frame[0] in (0x35, 0x175, 0xA0, 0xEA, 0x1CF):
              self.assertTrue(self.safety.safety_rx_hook(self.packet(frame)), (tick, hex(frame[0])))
          self.safety.set_controls_allowed(True)
          _, sent = controller.update(control.as_reader(), ci.CS, 1_000_000_000 + tick * 10_000_000)
          for frame in sent:
            self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)), (tick, hex(frame[0]), frame[2]))
          steering_pairs += int({0x12A, 0x110 if topology == "lkas_alt" else 0x50}.issubset({f[0] for f in sent}))
        self.assertEqual(steering_pairs, 110)

  def test_brake_cancel_and_rejected_lfa_revoke_pending_mirror(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 LONG profile")
    for topology, raw in RAW_LONG.items():
      _, cp = params(topology)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      bus = CanBus(cp)
      for event in ("brake", "cancel", "rejected_lfa"):
        with self.subTest(topology=topology, event=event):
          self.mode(raw)
          for name, values in (("ACCELERATOR", {"GEAR": 5}), ("TCS", {"DriverBraking": 0}),
                               ("WHEEL_SPEEDS", {key: 45 for key in
                                                 ("WHL_SpdFLVal", "WHL_SpdFRVal", "WHL_SpdRLVal", "WHL_SpdRRVal")}),
                               ("MDPS", {}), ("CRUISE_BUTTONS", {"COUNTER": 1})):
            self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, bus.ECAN, values))))
          self.safety.set_controls_allowed(True)
          lfa, lkas = create_steering_messages(packer, cp, bus, True, True, 2)
          self.assertTrue(self.safety.safety_tx_hook(self.packet(lfa)))
          if event == "brake":
            intervening = packer.make_can_msg("TCS", bus.ECAN, {"DriverBraking": 1})
          elif event == "cancel":
            intervening = packer.make_can_msg("CRUISE_BUTTONS", bus.ECAN, {"COUNTER": 2, "CRUISE_BUTTONS": 4})
          else:
            payload = bytearray(lfa[1])
            payload[9] ^= 0x01
            payload[:2] = hkg_can_fd_checksum(lfa[0], None, payload).to_bytes(2, "little")
            self.assertFalse(self.safety.safety_tx_hook(self.packet((lfa[0], bytes(payload), lfa[2]))))
            self.assertFalse(self.safety.safety_tx_hook(self.packet(lkas)))
            continue
          self.assertTrue(self.safety.safety_rx_hook(self.packet(intervening)))
          self.assertFalse(self.safety.lateral_controls_allowed())
          self.assertFalse(self.safety.safety_tx_hook(self.packet(lkas)))

  def test_aol_exact_tagged_profiles_require_physical_gesture_and_set_release(self):
    if self.release:
      for raw in RAW_AOL.values():
        self.mode(raw)
        self.safety.set_aol_test_heartbeat(True)
        self.safety.aol_set_host_request(3)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
      return
    for topology, raw in RAW_AOL.items():
      for gesture in ("LDA_BTN", "ADAPTIVE_CRUISE_MAIN_BTN"):
        with self.subTest(topology=topology, gesture=gesture):
          _, cp = params(topology)
          packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
          bus = CanBus(cp)
          self.mode(raw)
          self.safety.set_aol_test_heartbeat(True)
          for name, values in (("ACCELERATOR", {"GEAR": 5}), ("TCS", {"ACCEnable": 0}),
                               ("WHEEL_SPEEDS", {key: 45 for key in
                                                 ("WHL_SpdFLVal", "WHL_SpdFRVal", "WHL_SpdRLVal", "WHL_SpdRRVal")}),
                               ("MDPS", {}), ("CRUISE_BUTTONS", {"COUNTER": 1})):
            self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, bus.ECAN, values))))
          self.safety.aol_set_host_request(3)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)  # ACCEnable availability is not main
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "CRUISE_BUTTONS", bus.ECAN, {"COUNTER": 2, gesture: 1}))))
          self.assertEqual(self.safety.aol_get_permission_mask(), 1)  # fresh press authorizes; host request toggles
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "CRUISE_BUTTONS", bus.ECAN, {"COUNTER": 3, gesture: 0}))))
          self.assertEqual(self.safety.aol_get_permission_mask(), 1)
          self.assertFalse(self.safety.get_controls_allowed())
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "CRUISE_BUTTONS", bus.ECAN, {"COUNTER": 4, "CRUISE_BUTTONS": 2}))))
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "CRUISE_BUTTONS", bus.ECAN, {"COUNTER": 5, "CRUISE_BUTTONS": 0}))))
          self.safety.aol_set_host_request(3)
          self.assertTrue(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 3)
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "TCS", bus.ECAN, {"DriverBraking": 1}))))
          self.assertEqual(self.safety.aol_get_permission_mask(), 1)  # independent lateral persists through brake
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "CRUISE_BUTTONS", bus.ECAN, {"COUNTER": 6, "CRUISE_BUTTONS": 4}))))
          self.assertFalse(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.aol_get_permission_mask(), 1)  # Only claimed lateral survives CANCEL.
          self.safety.aol_set_host_request(1)
          self.assertEqual(self.safety.aol_get_permission_mask(), 1)
          self.safety.aol_set_host_request(0)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)  # Explicit host withdrawal remains binding.

  def test_full_engagement_without_lkas_retains_only_lateral_after_brake(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    for topology, raw in RAW_AOL.items():
      with self.subTest(topology=topology):
        _, cp = params(topology)
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        self.mode(raw)
        self.refresh_required_rx(packer, 1, 1_000_000)
        self.safety.set_aol_test_heartbeat(True)
        self.safety.set_timer(1_005_000)
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "CRUISE_BUTTONS", 1, {"COUNTER": 2, "CRUISE_BUTTONS": 2}))))
        self.safety.set_timer(1_010_000)
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "CRUISE_BUTTONS", 1, {"COUNTER": 3, "CRUISE_BUTTONS": 0}))))
        self.assertTrue(self.safety.get_controls_allowed())
        self.safety.aol_set_host_request(3)
        self.assertEqual(self.safety.aol_get_permission_mask(), 3)
        self.safety.set_timer(1_020_000)
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "TCS", 1, {"DriverBraking": 1, "COUNTER": 2}))))
        self.assertFalse(self.safety.get_controls_allowed())
        self.safety.aol_set_host_request(1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        for frame in create_steering_messages(packer, cp, CanBus(cp), True, True, 2):
          self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)))
        _, state, controller = controller_fixture(topology == "lkas_alt", aol=True)
        command = structs.CarControl(enabled=True, longActive=True)
        command.actuators.accel = .1
        command.actuators.longControlState = LongState.starting
        _, frames = controller.update(command.as_reader(), state, 1_020_000_000)
        active_scc = next(frame for frame in frames if frame[0] == 0x1A0)
        self.assertFalse(self.safety.safety_tx_hook(self.packet(active_scc)))
        for counter in range(4, 35):
          self.refresh_required_rx(packer, counter, 1_020_000 + counter * 10_000)
          self.safety.aol_set_host_request(0)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.safety.aol_set_host_request(1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_full_engagement_release_credit_cannot_survive_native_revocation(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    for topology, raw in RAW_AOL.items():
      for reset in ("heartbeat", "timeout", "unclaimed", "long_only", "zero_only"):
        with self.subTest(topology=topology, reset=reset):
          _, cp = params(topology)
          packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
          self.mode(raw)
          self.refresh_required_rx(packer, 1, 1_000_000)
          self.safety.set_aol_test_heartbeat(True)
          for counter, button in ((2, 2), (3, 0)):
            self.safety.set_timer(1_000_000 + counter * 5_000)
            self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
              "CRUISE_BUTTONS", 1, {"COUNTER": counter, "CRUISE_BUTTONS": button}))))
          self.safety.aol_set_host_request(0 if reset in ("zero_only", "unclaimed") else 2 if reset == "long_only" else 3)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0 if reset in ("zero_only", "unclaimed") else 2 if reset == "long_only" else 3)
          if reset == "heartbeat":
            self.safety.set_aol_test_heartbeat(False)
            self.assertEqual(self.safety.aol_get_permission_mask(), 0)
            self.safety.set_aol_test_heartbeat(True)
          elif reset in ("timeout", "unclaimed"):
            self.safety.set_timer(1_400_000)
            self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          stamp = 1_410_000 if reset in ("timeout", "unclaimed") else 1_020_000
          self.refresh_required_rx(packer, 4, stamp)
          if reset in ("heartbeat", "timeout", "unclaimed"):
            self.safety.aol_set_host_request(3)
            self.assertEqual(self.safety.aol_get_permission_mask(), 3)
          self.safety.set_timer(stamp + 5_000)
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "TCS", 1, {"DriverBraking": 1, "COUNTER": 5}))))
          self.safety.aol_set_host_request(1)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_new_physical_enable_release_restarts_engaged_heartbeat_grace(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    for topology, raw in RAW_AOL.items():
      for button in (1, 2):
        with self.subTest(topology=topology, button=button):
          _, cp = params(topology)
          packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
          self.mode(raw)
          self.refresh_required_rx(packer, 1, 1_000_000)
          self.safety.set_controls_allowed(True)
          self.safety.set_aol_test_heartbeat(False)
          self.safety.set_heartbeat_engaged_mismatches(2)
          self.safety.set_timer(1_005_000)
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "CRUISE_BUTTONS", 1, {"COUNTER": 2, "CRUISE_BUTTONS": button}))))
          self.assertEqual(self.safety.get_heartbeat_engaged_mismatches(), 2)
          self.safety.set_timer(1_010_000)
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "CRUISE_BUTTONS", 1, {"COUNTER": 3, "CRUISE_BUTTONS": 0}))))
          self.assertTrue(self.safety.get_controls_allowed())
          self.assertEqual(self.safety.get_heartbeat_engaged_mismatches(), 0)

  def test_aol_long_permission_requires_explicit_host_long_request(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    for topology, raw in RAW_AOL.items():
      with self.subTest(topology=topology):
        _, cp = params(topology)
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        self.mode(raw)
        self.safety.set_aol_test_heartbeat(True)
        for name, values in (("ACCELERATOR", {"GEAR": 5}), ("TCS", {"ACCEnable": 0}),
                             ("WHEEL_SPEEDS", {key: 45 for key in
                                               ("WHL_SpdFLVal", "WHL_SpdFRVal", "WHL_SpdRLVal", "WHL_SpdRRVal")}),
                             ("MDPS", {}), ("CRUISE_BUTTONS", {"COUNTER": 1})):
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, 1, values))))
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "CRUISE_BUTTONS", 1, {"COUNTER": 2, "LDA_BTN": 1}))))
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "CRUISE_BUTTONS", 1, {"COUNTER": 3}))))
        for counter, button in ((4, 2), (5, 0)):
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
            "CRUISE_BUTTONS", 1, {"COUNTER": counter, "CRUISE_BUTTONS": button}))))
        self.assertTrue(self.safety.get_controls_allowed())
        self.safety.aol_set_host_request(1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.safety.aol_set_host_request(0)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_aol_idle_bootstrap_and_rebootstrap_after_real_heartbeat_loss(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    bus = CanBus(cp)
    self.mode(RAW_AOL["lkas"])
    for name, values in (("ACCELERATOR", {}), ("TCS", {}), ("WHEEL_SPEEDS", {}),
                         ("MDPS", {}), ("CRUISE_BUTTONS", {"COUNTER": 1})):
      self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, 1, values))))
    self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "CRUISE_BUTTONS", 1, {"COUNTER": 2, "LDA_BTN": 1, "ADAPTIVE_CRUISE_MAIN_BTN": 1}))))
    # Actual neutral controller frames arrive at 100 Hz before first 10 Hz USB
    # request. They must not erase a fresh gesture merely because idle heartbeat
    # is still false. Simultaneous main/LDA is one physical edge.
    for tick in range(3, 9):
      self.safety.set_timer(1_000_000 + tick * 10_000)
      lfa, lkas = create_steering_messages(packer, cp, bus, False, False, 0)
      self.assertTrue(self.safety.safety_tx_hook(self.packet(lfa)))
      self.assertTrue(self.safety.safety_tx_hook(self.packet(lkas)))
      self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "CRUISE_BUTTONS", 1, {"COUNTER": 3}))))
    self.safety.set_aol_test_heartbeat(False)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    # A lost heartbeat revokes the old token. Healthy idle can then accept a
    # later fresh gesture through the same bounded bootstrap path.
    self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "CRUISE_BUTTONS", 1, {"COUNTER": 4}))))
    self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "CRUISE_BUTTONS", 1, {"COUNTER": 5, "LDA_BTN": 1}))))
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)

  def test_aol_armed_zero_request_preserves_token_but_never_steers(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    for topology, raw in RAW_AOL.items():
      with self.subTest(topology=topology):
        _, cp = params(topology)
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        bus = CanBus(cp)
        self.mode(raw)
        self.refresh_required_rx(packer, 1, 1_000_000)
        self.safety.set_aol_test_heartbeat(True)
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "CRUISE_BUTTONS", 1, {"COUNTER": 2, "LDA_BTN": 1}))))
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "CRUISE_BUTTONS", 1, {"COUNTER": 3}))))
        self.safety.aol_set_host_request(1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        for tick in range(4, 104):
          self.refresh_required_rx(packer, tick, 1_000_000 + tick * 10_000)
          self.safety.aol_set_host_request(0)
          self.assertEqual(self.safety.aol_get_permission_mask(), 0)
          inactive = create_steering_messages(packer, cp, bus, False, False, 0)
          active = create_steering_messages(packer, cp, bus, False, True, 1)
          for frame in inactive:
            self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)))
          self.assertFalse(self.safety.safety_tx_hook(self.packet(active[1])))
        self.safety.aol_set_host_request(1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 1)
        self.safety.set_aol_test_heartbeat(False)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)
        self.safety.set_aol_test_heartbeat(True)
        self.safety.aol_set_host_request(1)
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_aol_startup_held_cancel_timeout_and_bad_counter(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    self.mode(RAW_AOL["lkas"])
    self.safety.set_aol_test_heartbeat(True)
    for name, values in (("ACCELERATOR", {}), ("TCS", {}), ("WHEEL_SPEEDS", {}), ("MDPS", {}),
                         ("CRUISE_BUTTONS", {"COUNTER": 1, "LDA_BTN": 1})):
      self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, 1, values))))
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)  # held at safety init is ignored
    for counter, lda in ((2, 0), (3, 1)):
      self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
        "CRUISE_BUTTONS", 1, {"COUNTER": counter, "LDA_BTN": lda}))))
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "CRUISE_BUTTONS", 1, {"COUNTER": 4, "CRUISE_BUTTONS": 4}))))
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)  # Already claimed independent lateral.
    for counter, lda in ((5, 0), (6, 1)):
      self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
        "CRUISE_BUTTONS", 1, {"COUNTER": counter, "LDA_BTN": lda}))))
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    for _ in range(5):
      self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
        "CRUISE_BUTTONS", 1, {"COUNTER": 6, "LDA_BTN": 1})))
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)  # wrong counter cannot authorize
    self.mode(RAW_AOL["lkas"])
    self.safety.set_aol_test_heartbeat(True)
    for name, values in (("ACCELERATOR", {}), ("TCS", {}), ("WHEEL_SPEEDS", {}), ("MDPS", {}),
                         ("CRUISE_BUTTONS", {"COUNTER": 1})):
      self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, 1, values))))
    self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "CRUISE_BUTTONS", 1, {"COUNTER": 2, "LDA_BTN": 1}))))
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 1)
    for counter in range(1, 17):
      self.refresh_required_rx(packer, counter, 1_000_000 + counter * 20_000)
    self.assertTrue(self.safety.safety_config_valid())
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)  # request timeout clears token

  def test_aol_unclaimed_gesture_expires_before_first_request(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    self.mode(RAW_AOL["lkas"])
    _, cp = params("lkas")
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    for name, values in (("ACCELERATOR", {}), ("TCS", {}), ("WHEEL_SPEEDS", {}), ("MDPS", {}),
                         ("CRUISE_BUTTONS", {"COUNTER": 1})):
      self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, 1, values))))
    self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "CRUISE_BUTTONS", 1, {"COUNTER": 2, "LDA_BTN": 1}))))
    for counter in range(1, 17):
      self.refresh_required_rx(packer, counter + 2, 1_000_000 + counter * 20_000)
    self.assertTrue(self.safety.safety_config_valid())
    # No status/TX getter has run since the gesture. A late first request
    # cannot claim an already expired physical token.
    self.safety.set_aol_test_heartbeat(True)
    self.safety.aol_set_host_request(1)
    self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_aol_long_only_request_cannot_claim_old_lateral_gesture(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    for topology, raw in RAW_AOL.items():
      with self.subTest(topology=topology):
        self.mode(raw)
        _, cp = params(topology)
        packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
        for name, values in (("ACCELERATOR", {}), ("TCS", {}), ("WHEEL_SPEEDS", {}),
                             ("MDPS", {}), ("CRUISE_BUTTONS", {"COUNTER": 1})):
          self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, 1, values))))
        self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "CRUISE_BUTTONS", 1, {"COUNTER": 2, "LDA_BTN": 1}))))
        self.safety.set_aol_test_heartbeat(True)
        self.safety.aol_set_host_request(2)  # longitudinal only, never claims the lateral token
        self.assertEqual(self.safety.aol_get_permission_mask() & 1, 0)
        for counter in range(1, 17):
          self.refresh_required_rx(packer, counter + 2, 1_000_000 + counter * 20_000)
          self.safety.aol_set_host_request(2)
          self.assertEqual(self.safety.aol_get_permission_mask() & 1, 0)
        self.assertTrue(self.safety.safety_config_valid())
        self.safety.aol_set_host_request(1)  # no new physical gesture after the deadline
        self.assertEqual(self.safety.aol_get_permission_mask(), 0)

  def test_aol_four_actual_controller_axis_modes(self):
    if self.release:
      self.skipTest("RELEASE denies the Ioniq 6 AOL profiles")
    for topology, raw in RAW_AOL.items():
      for lateral, longitudinal in ((False, False), (True, False), (False, True), (True, True)):
        with self.subTest(topology=topology, lateral=lateral, longitudinal=longitudinal):
          cp, cs, controller = controller_fixture(topology == "lkas_alt", aol=True)
          packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
          self.mode(raw)
          self.safety.set_aol_test_heartbeat(True)
          for name, values in (("ACCELERATOR", {"GEAR": 5}), ("TCS", {"ACCEnable": 0}),
                               ("WHEEL_SPEEDS", {key: 45 for key in
                                                 ("WHL_SpdFLVal", "WHL_SpdFRVal", "WHL_SpdRLVal", "WHL_SpdRRVal")}),
                               ("MDPS", {}), ("CRUISE_BUTTONS", {"COUNTER": 1})):
            self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(name, 1, values))))
          if lateral:
            for counter, lda in ((2, 1), (3, 0)):
              self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
                "CRUISE_BUTTONS", 1, {"COUNTER": counter, "LDA_BTN": lda}))))
          if longitudinal:
            for counter, button in ((4, 2), (5, 0)):
              self.assertTrue(self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
                "CRUISE_BUTTONS", 1, {"COUNTER": counter, "CRUISE_BUTTONS": button}))))
          mask = int(lateral) | (int(longitudinal) << 1)
          self.safety.aol_set_host_request(mask)
          self.assertEqual(self.safety.aol_get_permission_mask(), mask)
          cc = structs.CarControl()
          cc.enabled = longitudinal  # actual AOL lateral-only keeps standard enable off
          cc.latActive, cc.longActive = lateral, longitudinal
          cc.actuators.torque = 0.01
          cc.actuators.accel = 0.1
          cc.actuators.longControlState = LongState.starting
          _, sent = controller.update(cc.as_reader(), cs, 1_000_000_000)
          lfa = next(f for f in sent if f[0] == 0x12A)
          parsed = CANParser(DBC[cp.carFingerprint][Bus.pt], [("LFA", 0)], 1)
          parsed.update((1_000_000_000, [lfa]))
          self.assertEqual(parsed.vl["LFA"]["LKA_SysIndReq"], 2 if lateral or longitudinal else 1)
          camera_name = "LKAS_ALT" if topology == "lkas_alt" else "LKAS"
          camera_addr = 0x110 if topology == "lkas_alt" else 0x50
          camera = CANParser(DBC[cp.carFingerprint][Bus.pt], [(camera_name, 0)], 0)
          camera.update((1_000_000_000, [next(f for f in sent if f[0] == camera_addr)]))
          self.assertEqual(camera.vl[camera_name]["LKA_SysIndReq"], 2 if lateral or longitudinal else 1)
          for frame in sent:
            self.assertTrue(self.safety.safety_tx_hook(self.packet(frame)),
                            (topology, lateral, longitudinal, hex(frame[0])))
          if not longitudinal:
            active = structs.CarControl()
            active.enabled = active.longActive = True
            active.actuators.accel = 0.1
            active.actuators.longControlState = LongState.starting
            _, active_frames = CarController(DBC[cp.carFingerprint], cp).update(active.as_reader(), cs, 1_000_000_000)
            self.assertFalse(self.safety.safety_tx_hook(self.packet(next(f for f in active_frames if f[0] == 0x1A0))))
          if not lateral:
            active_lfa = create_steering_messages(packer, cp, CanBus(cp), True, True, 2)[0]
            self.assertFalse(self.safety.safety_tx_hook(self.packet(active_lfa)))
