"""Checksums of the frames openpilot rebuilds match the ones captured on the car."""
import unittest
from opendbc.car.volvo.helpers import (
  checksum_1_pscm_related_message,
  checksum_2_pscm_related_message,
  checksum_2_lca_2_message,
  checksum_lca_5_message,
)


# Stock LCA_2 (0x69) frames from a drive on 2026-11-18, one per byte-1 value seen: the
# 4-bit counter with PILOT_ASSIST_ENGAGED (byte 1 bit 4) both off and on, since that is
# the bit openpilot changes. CHECKSUM_2 (byte 2) covers bytes 0, 1, 3 and 4.
LCA_2_FRAMES = [
  "1800d60000b6f700", "1801bc0000b80200", "1802020000ba2500", "1803680000bc4c00",
  "1804630000be6b00", "1805090000b10300", "1806b70000b32400", "1807dd0000b54d00",
  "1808a10000b76a00", "1809cb0000b99f00", "180a750000bbb800", "180b1f0000bdd100",
  "180c140000b09e00", "180d7e0000b2b900", "180ec00000b4d000",
  "18104b0000b09e00", "1811210000b2b900", "18129f0000b4d000", "1813f50000b6f700",
  "1814fe0000b80200", "1815940000ba2500", "18162a0000bc4c00", "1817400000be6b00",
  "18183c0000b10300", "1819560000b32400", "181ae80000b54d00", "181b820000b76a00",
  "181c890000b99f00", "181de30000bbb800", "181e5d0000bdd100",
]


# Stock LCA_5 (0x67) frames from the same drive: for every counter value (0-14, byte 3
# high nibble), one at standstill, one moving, and one with stock commanding an angle
# (bytes 6-7 != ba00). Plus a stock frame from an earlier drive. CHECKSUM (byte 2) covers
# bytes 0, 1, 3, 4 and 5.
LCA_5_FRAMES = [
  "80005b0f0000ba00", "90c33b0f2190ba00", "9684a20f2d288012",
  "8000061f0000ba00", "90c3661f2190ba00", "967d7e1f2d048009",
  "8000e12f0000ba00", "90b6a82f2178ba00", "9684972f2d12800a",
  "8000bc3f0000ba00", "90c8f33f218cba00", "9684243f2d22800e",
  "8000324f0000ba00", "90c3444f2182ba00", "9675f54f2d08800a",
  "80006f5f0000ba00", "90b9f35f2182ba00", "9684975f2d048009",
  "8000886f0000ba00", "90cde56f2186ba00", "968c416f2d1e800a",
  "8000d57f0000ba00", "90c84f7f21a6ba00", "9689047f2d228010",
  "8000898f0000ba00", "90be238f218cba00", "9672558f2d0e800a",
  "8000d49f0000ba00", "90b1529f2178ba00", "967a589f2d1e8009",
  "800033af0000ba00", "90c1d9af2178ba00", "968279af2d1e800b",
  "80006ebf0000ba00", "90c182bf2190ba00", "96946dbf2d188010",
  "8000e0cf0000ba00", "90c30ecf2186ba00", "967ae2cf2d088009",
  "8000bddf0000ba00", "90b951df215eba00", "9675aedf2d0e8009",
  "80005aef0000ba00", "90c3eeef2196ba00", "967aedef2d04800c",
  "88c04cef1190ba00",
]


# (b1, b2) -> byte[0], observed on the bus.
# b1 = RACK_COUNTER (high nibble) | LCA_ENABLED_ECHO (low nibble),
# b2 = PSCM_STATUS 8 (high nibble) | RACK_COUNTER_REPLICA (low nibble).
# ECHO 0/1/4 are normal driving; ECHO 6 only appears while ESC is intervening and was
# the case that used to fall through to a 0x00 checksum.
PSCM_RELATED_CHECKSUM_1 = {
  (0x00, 0x80): 0xD4, (0x01, 0x80): 0xC9, (0x04, 0x80): 0xA0, (0x06, 0x80): 0x9A,
  (0x10, 0x81): 0x98, (0x11, 0x81): 0x85, (0x14, 0x81): 0xEC, (0x16, 0x81): 0xD6,
  (0x20, 0x82): 0x4C, (0x21, 0x82): 0x51, (0x24, 0x82): 0x38, (0x26, 0x82): 0x02,
  (0x30, 0x83): 0x00, (0x31, 0x83): 0x1D, (0x34, 0x83): 0x74, (0x36, 0x83): 0x4E,
  (0x40, 0x84): 0xF9, (0x41, 0x84): 0xE4, (0x44, 0x84): 0x8D, (0x46, 0x84): 0xB7,
  (0x50, 0x85): 0xB5, (0x51, 0x85): 0xA8, (0x54, 0x85): 0xC1, (0x56, 0x85): 0xFB,
  (0x60, 0x86): 0x61, (0x61, 0x86): 0x7C, (0x64, 0x86): 0x15, (0x66, 0x86): 0x2F,
  (0x70, 0x87): 0x2D, (0x71, 0x87): 0x30, (0x74, 0x87): 0x59, (0x76, 0x87): 0x63,
  (0x80, 0x88): 0x8E, (0x81, 0x88): 0x93, (0x84, 0x88): 0xFA, (0x86, 0x88): 0xC0,
  (0x90, 0x89): 0xC2, (0x91, 0x89): 0xDF, (0x94, 0x89): 0xB6, (0x96, 0x89): 0x8C,
  (0xA0, 0x8A): 0x16, (0xA1, 0x8A): 0x0B, (0xA4, 0x8A): 0x62, (0xA6, 0x8A): 0x58,
  (0xB0, 0x8B): 0x5A, (0xB1, 0x8B): 0x47, (0xB4, 0x8B): 0x2E, (0xB6, 0x8B): 0x14,
  (0xC0, 0x8C): 0xA3, (0xC1, 0x8C): 0xBE, (0xC4, 0x8C): 0xD7, (0xC6, 0x8C): 0xED,
  (0xD0, 0x8D): 0xEF, (0xD1, 0x8D): 0xF2, (0xD4, 0x8D): 0x9B, (0xD6, 0x8D): 0xA1,
  (0xE0, 0x8E): 0x3B, (0xE1, 0x8E): 0x26, (0xE4, 0x8E): 0x4F, (0xE6, 0x8E): 0x75,
}


# b2 -> byte[3], same source
PSCM_RELATED_CHECKSUM_2 = {
  0x80: 0xBF, 0x81: 0xF3, 0x82: 0x27, 0x83: 0x6B, 0x84: 0x92,
  0x85: 0xDE, 0x86: 0x0A, 0x87: 0x46, 0x88: 0xE5, 0x89: 0xA9,
  0x8A: 0x7D, 0x8B: 0x31, 0x8C: 0xC8, 0x8D: 0x84, 0x8E: 0x50,
}


class TestPscmRelatedChecksums(unittest.TestCase):
  def test_checksum_1_matches_the_car(self):
    for (b1, b2), expected in PSCM_RELATED_CHECKSUM_1.items():
      with self.subTest(b1=hex(b1), b2=hex(b2)):
        assert checksum_1_pscm_related_message(b1, b2) == expected

  def test_checksum_1_covers_esc_echo(self):
    # regression: ECHO=6 used to miss the lookup table and return 0x00, which the
    # receiving ECU logged as a checksum fault for as long as ESC was active
    for counter in range(15):
      b1, b2 = (counter << 4) | 6, 0x80 | counter
      with self.subTest(counter=counter):
        assert checksum_1_pscm_related_message(b1, b2) == PSCM_RELATED_CHECKSUM_1[(b1, b2)]

  def test_checksum_2_matches_the_car(self):
    for b2, expected in PSCM_RELATED_CHECKSUM_2.items():
      with self.subTest(b2=hex(b2)):
        assert checksum_2_pscm_related_message(b2) == expected


class TestLca2Checksum(unittest.TestCase):
  def test_checksum_2_matches_the_car(self):
    for frame in LCA_2_FRAMES:
      d = bytes.fromhex(frame)
      with self.subTest(frame=frame):
        assert checksum_2_lca_2_message(d[0], d[1], d[3], d[4]) == d[2]


class TestLca5Checksum(unittest.TestCase):
  def test_checksum_matches_the_car(self):
    for frame in LCA_5_FRAMES:
      d = bytes.fromhex(frame)
      with self.subTest(frame=frame):
        assert checksum_lca_5_message(d[0], d[1], d[3], d[4], d[5]) == d[2]
