"""Trung's (trung791997) Bosch-A decoder tests, ported with the decoder from ns-bosch-radar-testing @ 216050fd98.

Adapted to 7.0: the decoder is opendbc.car.honda.bosch_a_radar.BoschARadar (RadarInterface(CP).bosch_a), point flags
are in RadarPoint.deprecated, and there is no BoschARadar params toggle. Tests of what the port leaves out (the
NORMALIZED_CLOSING outputs, RadarPoint.existence/recovered, the DBC CHECKSUM/COUNTER and the wider Bosch-A car gate)
are removed; the newborn-publish tests switch it on explicitly, since it is off on 7.0.
"""
import math
import random

import pytest

from opendbc.can.dbc import DBC as DbcFile
from opendbc.car.can_definitions import CanData
from opendbc.car.honda.hondacan import CanBus
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.bosch_a_radar import (
  BOSCH_A_REANCHOR_MIN_SPAN_S,
  BOSCH_A_AZIMUTH_SCALE_RAD,
  BOSCH_A_AUX_IDS,
  BOSCH_A_DBC_NAME,
  BOSCH_A_DIRECT_VREL_INVALID,
  BOSCH_A_DIRECT_VREL_MAX_RAW,
  BOSCH_A_DIRECT_VREL_MIN_RAW,
  BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW,
  BOSCH_A_DIRECT_VREL_RAIL_BOUND_MPS,
  BOSCH_A_FALLBACK_RANGE_RATE_MAX_MPS,
  BOSCH_A_FREQ_HZ,
  BOSCH_A_LIFE_SATURATED,
  BOSCH_A_MAIN_IDS,
  BOSCH_A_NUM_SLOTS,
  BOSCH_A_RANGE_RATIO_INVALID,
  BOSCH_A_RANGE_OFFSET_M,
  BOSCH_A_COAST_REVERSING_MARGIN_MPS,
  BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS,
  BOSCH_A_RANGE_SCALE_M,
  BOSCH_A_STALE_S,
  BOSCH_A_SWEEP_END_MSG,
  BOSCH_A_TRIGGER_MSG,
  _bosch_a_aux_id,
  _bosch_a_direct_vrel,
  _bosch_a_direct_vrel_interval,
  _bosch_a_main_base,
  _bosch_a_range_ratio,
  _bosch_a_range_ratio_vrel,
  bosch_a_range_offset_m,
)
import opendbc.car.honda.bosch_a_radar as radar_interface_module
from opendbc.car.honda.values import CAR


def get_raw_value(dat, sig):
  return sig.get_raw_value(dat)   # 7.0 moved it onto the signal


CP = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC_BOSCH)
BUS = CanBus(CP).camera


# --- synthetic frame builders (inverse of the spec's raw-byte formulas) -----------------------------

def make_f0(frame_idx=0, status=0x7, range_raw=0, angle_raw=0, range_sigma_raw=1):
  # inverse of raw_angle = (B4 << 3) | (B5 >> 5): B4 carries the top 8 bits, B5's top 3 bits carry the
  # bottom 3 bits of the 11-bit angle (the decoder ignores B5's low 5 bits entirely).
  B0 = (range_sigma_raw & 0x7F) << 1
  B3 = (frame_idx & 0xF) | ((range_raw & 0xF) << 4)
  B2 = (range_raw >> 4) & 0xFF
  B1 = (status & 0xF) << 4
  B5 = (angle_raw & 0x7) << 5
  B4 = (angle_raw >> 3) & 0xFF
  return bytes([B0, B1, B2, B3, B4, B5, 0, 0])


def make_f1(frame_idx=0, existence_raw=126):
  return bytes([0, 0, 0, (frame_idx & 0xF) << 1, 0, existence_raw & 0x7F, 0, 0])


def make_f2(frame_idx=0, life=0, nc_raw=512, nc_sigma_raw=0):
  B1 = (frame_idx & 0xF) | ((life & 0xF) << 4)
  B0 = (life >> 4) & 0xFF
  B2 = (nc_raw >> 2) & 0xFF
  B3 = (nc_raw & 0x3) << 6
  B5 = nc_sigma_raw & 0x7F
  return bytes([B0, B1, B2, B3, 0, B5, 0, 0])


def make_f3(frame_idx=0, edge_a_raw=0, edge_b_raw=0, sigma_a_raw=0, track_id=0xFF):
  B0 = (edge_a_raw >> 3) & 0xFF
  B1 = ((edge_a_raw & 0x7) << 5) | ((frame_idx & 0xF) << 1)
  B2 = (edge_b_raw >> 3) & 0xFF
  B3 = (edge_b_raw & 0x7) << 5
  B4 = (sigma_a_raw >> 2) & 0xFF
  B5 = (sigma_a_raw & 0x3) << 6
  return bytes([B0, B1, B2, B3, B4, B5, track_id & 0xFF, 0])


def make_aux(frame_idx=0, rawc9=0, rawca=0, direct_vrel_raw=BOSCH_A_DIRECT_VREL_INVALID,
             direct_vrel_uncertainty_raw=0x3FF):
  B0 = (direct_vrel_raw >> 3) & 0xFF
  B1 = ((direct_vrel_raw & 0x7) << 5) | ((frame_idx & 0xF) << 1)
  B2 = (direct_vrel_uncertainty_raw >> 2) & 0xFF
  B3 = (direct_vrel_uncertainty_raw & 0x3) << 6
  B5 = (rawc9 & 0x3) << 6
  B4 = (rawc9 >> 2) & 0xFF
  B7 = (rawca & 0x3) << 6
  B6 = (rawca >> 2) & 0xFF
  return bytes([B0, B1, B2, B3, B4, B5, B6, B7])


def make_main_frames(slot, frame_idx, status, range_raw, angle_raw, life, track_id=1,
                     range_sigma_raw=1, existence_raw=126, nc_raw=512, nc_sigma_raw=0):
  f0, f1, f2, f3 = BOSCH_A_MAIN_IDS[slot]
  return [
    CanData(f0, make_f0(frame_idx, status, range_raw, angle_raw, range_sigma_raw), BUS),
    CanData(f1, make_f1(frame_idx, existence_raw), BUS),
    CanData(f2, make_f2(frame_idx, life, nc_raw, nc_sigma_raw), BUS),
    CanData(f3, make_f3(frame_idx, track_id=track_id), BUS),
  ]


def make_radar_interface():
  _reset_test_counters()
  return CarInterface.RadarInterface(CP).bosch_a


# The DBC now declares the Honda CHECKSUM (last byte bits 3:0) and the 2-bit rolling COUNTER
# (bits 5:4), so opendbc enforces both. Synthesized frames must therefore carry real framing or
# every message is rejected before any signal reaches the parser. Verified on real captures:
# 361,360/361,360 checksum pass and 214,400/214,400 sequential counter transitions.
_TEST_COUNTERS: dict[int, int] = {}


def _reset_test_counters():
  _TEST_COUNTERS.clear()


def _stamp(frame: CanData) -> CanData:
  """Give one synthesized frame a valid rolling counter and Honda checksum."""
  data = bytearray(frame.dat)
  counter = (_TEST_COUNTERS.get(frame.address, -1) + 1) & 0x3
  _TEST_COUNTERS[frame.address] = counter
  data[7] = (data[7] & 0xC0) | (counter << 4)
  checksum = 0
  address = frame.address
  while address > 0:
    checksum += address & 0xF
    address >>= 4
  for i, b in enumerate(data):
    if i == len(data) - 1:
      b >>= 4
    checksum += (b >> 4) + (b & 0xF)
  data[7] |= (8 - checksum) & 0xF
  return CanData(frame.address, bytes(data), frame.src)


def sweep(slot, frame_idx, status, range_raw, angle_raw, life, t_nanos, with_aux=False, aux_frame_idx=None,
          rawc9=0, rawca=BOSCH_A_RANGE_RATIO_INVALID, extra_slots=(), track_id=1, direct_vrel_raw=BOSCH_A_DIRECT_VREL_INVALID,
          direct_vrel_uncertainty_raw=0x3FF, range_sigma_raw=1, existence_raw=126, nc_raw=512, nc_sigma_raw=0):
  """Build one update() input: a full main-frame set for `slot` (+ optional aux), plus the trigger
  frame (slot 15's f3) so update() always processes the cycle unless the caller is testing slot 15
  itself or an incomplete-frame scenario via extra_slots."""
  f0, f1, f2, f3 = BOSCH_A_MAIN_IDS[slot]
  aux = BOSCH_A_AUX_IDS[slot]
  frames = make_main_frames(
    slot, frame_idx, status, range_raw, angle_raw, life, track_id,
    range_sigma_raw=range_sigma_raw, existence_raw=existence_raw, nc_raw=nc_raw, nc_sigma_raw=nc_sigma_raw,
  )
  if with_aux:
    frames.append(CanData(aux, make_aux(aux_frame_idx if aux_frame_idx is not None else frame_idx, rawc9, rawca,
                                        direct_vrel_raw, direct_vrel_uncertainty_raw), BUS))
  if slot != BOSCH_A_NUM_SLOTS - 1:
    _, _, _, trig_f3 = BOSCH_A_MAIN_IDS[BOSCH_A_NUM_SLOTS - 1]
    frames.append(CanData(trig_f3, make_f3(frame_idx), BUS))
  for extra in extra_slots:
    frames.append(extra)
  return [(t_nanos, [_stamp(f) for f in frames])]


# --- 1. all 16 slot/frame ID mappings ---------------------------------------------------------------

def test_all_16_slot_main_ids():
  for slot in range(16):
    base = 0x280 + 4 * slot if slot < 4 else 0x2D0 + 4 * (slot - 4)
    assert _bosch_a_main_base(slot) == base
    assert BOSCH_A_MAIN_IDS[slot] == [base, base + 1, base + 2, base + 3]


def test_all_16_slot_aux_ids():
  expected = {
    0: 0x2C8, 1: 0x2C9, 2: 0x2CA, 3: 0x2CB, 4: 0x2CC, 5: 0x2CD, 6: 0x2CE, 7: 0x2CF,
    8: 0x290, 9: 0x291, 10: 0x292, 11: 0x293, 12: 0x294, 13: 0x295, 14: 0x296, 15: 0x297,
  }
  for slot, addr in expected.items():
    assert _bosch_a_aux_id(slot) == addr
    assert BOSCH_A_AUX_IDS[slot] == addr


def test_no_duplicate_can_ids_across_80_messages():
  all_ids = [addr for ids in BOSCH_A_MAIN_IDS for addr in ids] + BOSCH_A_AUX_IDS
  assert len(all_ids) == 80
  assert len(set(all_ids)) == 80


# --- 2. DBC bit geometry matches the spec's raw-byte formulas exactly (section 16) -------------------

class TestDbcBitGeometry:
  dbc = DbcFile(BOSCH_A_DBC_NAME)

  def test_f0_fields(self):
    msg = self.dbc.msgs[0x280]
    rng = random.Random(0)
    for _ in range(500):
      b = [rng.randint(0, 255) for _ in range(8)]
      dat = bytes(b)
      assert get_raw_value(dat, msg.sigs['STATUS']) == (b[1] >> 4) & 0x0F
      assert get_raw_value(dat, msg.sigs['FRAME_IDX']) == b[3] & 0x0F
      assert get_raw_value(dat, msg.sigs['RANGE_RAW']) == (b[2] << 4) | (b[3] >> 4)
      assert get_raw_value(dat, msg.sigs['AZIMUTH_RAW']) == (b[4] << 3) | (b[5] >> 5)

  def test_f1_frame_idx(self):
    msg = self.dbc.msgs[0x281]
    rng = random.Random(1)
    for _ in range(500):
      b = [rng.randint(0, 255) for _ in range(8)]
      assert get_raw_value(bytes(b), msg.sigs['FRAME_IDX']) == (b[3] >> 1) & 0x0F

  def test_f2_fields(self):
    msg = self.dbc.msgs[0x282]
    rng = random.Random(2)
    for _ in range(500):
      b = [rng.randint(0, 255) for _ in range(8)]
      dat = bytes(b)
      assert get_raw_value(dat, msg.sigs['FRAME_IDX']) == b[1] & 0x0F
      assert get_raw_value(dat, msg.sigs['LIFECYCLE_RAW']) == (b[0] << 4) | (b[1] >> 4)

  def test_f3_fields(self):
    msg = self.dbc.msgs[0x283]
    rng = random.Random(3)
    for _ in range(500):
      b = [rng.randint(0, 255) for _ in range(8)]
      dat = bytes(b)
      assert get_raw_value(dat, msg.sigs['AZIMUTH_EDGE_A_RAW']) == (b[0] << 3) | (b[1] >> 5)
      assert get_raw_value(dat, msg.sigs['FRAME_IDX']) == (b[1] >> 1) & 0x0F
      assert get_raw_value(dat, msg.sigs['AZIMUTH_EDGE_B_RAW']) == (b[2] << 3) | (b[3] >> 5)
      assert get_raw_value(dat, msg.sigs['AZIMUTH_EDGE_SIGMA_A_RAW']) == (b[4] << 2) | (b[5] >> 6)

  def test_track_id_is_f3_byte6_and_does_not_overlap_existing_fields(self):
    def affected_bits(signal):
      bits = set()
      for byte in range(8):
        for bit in range(8):
          data = bytearray(8)
          data[byte] = 1 << bit
          if get_raw_value(bytes(data), signal) != 0:
            bits.add((byte, bit))
      return bits

    expected_bits = {(6, bit) for bit in range(8)}
    f3_ids = [BOSCH_A_MAIN_IDS[slot][3] for slot in range(BOSCH_A_NUM_SLOTS)]
    for message_id in f3_ids:
      msg = self.dbc.msgs[message_id]
      track_id = msg.sigs['TRACK_ID']
      assert track_id.start_bit == 55
      assert track_id.size == 8
      assert track_id.is_little_endian is False
      assert affected_bits(track_id) == expected_bits

      existing_bits = set()
      for name, signal in msg.sigs.items():
        if name != 'TRACK_ID':
          existing_bits |= affected_bits(signal)
      assert not existing_bits & expected_bits

    rng = random.Random(33)
    msg = self.dbc.msgs[BOSCH_A_MAIN_IDS[0][3]]
    for _ in range(500):
      data = bytes(rng.randint(0, 255) for _ in range(8))
      assert get_raw_value(data, msg.sigs['TRACK_ID']) == data[6]

  def test_aux_fields(self):
    msg = self.dbc.msgs[0x2C8]
    rng = random.Random(4)
    for _ in range(500):
      b = [rng.randint(0, 255) for _ in range(8)]
      dat = bytes(b)
      assert get_raw_value(dat, msg.sigs['FRAME_IDX']) == (b[1] >> 1) & 0x0F
      assert get_raw_value(dat, msg.sigs['REL_VELOCITY_RAW']) == (b[0] << 3) | (b[1] >> 5)
      assert get_raw_value(dat, msg.sigs['REL_VELOCITY_UNCERTAINTY_RAW']) == (b[2] << 2) | (b[3] >> 6)
      assert get_raw_value(dat, msg.sigs['FW_LID_00C9_RAW']) == (b[4] << 2) | (b[5] >> 6)
      assert get_raw_value(dat, msg.sigs['FW_LID_00CA_RAW']) == (b[6] << 2) | (b[7] >> 6)
      assert get_raw_value(dat, msg.sigs['AZIMUTH_EDGE_SIGMA_B_RAW']) == (b[4] << 2) | (b[5] >> 6)
      assert get_raw_value(dat, msg.sigs['RANGE_RATIO_RAW']) == (b[6] << 2) | (b[7] >> 6)

  def test_semantic_quality_aliases_exist_on_every_slot(self):
    for slot in range(BOSCH_A_NUM_SLOTS):
      f0, f1, f2, _ = BOSCH_A_MAIN_IDS[slot]
      aux = BOSCH_A_AUX_IDS[slot]
      assert self.dbc.msgs[f0].sigs['RANGE_SIGMA_RAW'].size == 7
      assert self.dbc.msgs[f1].sigs['OBJECT_EXISTENCE_PROBABILITY_RAW'].size == 7
      assert self.dbc.msgs[f1].sigs['ANGULAR_WIDTH_RAW'].size == 11
      assert self.dbc.msgs[f2].sigs['NORMALIZED_CLOSING_RAW'].size == 10
      assert self.dbc.msgs[f2].sigs['NORMALIZED_CLOSING_SIGMA_RAW'].size == 7
      assert self.dbc.msgs[aux].sigs['AZIMUTH_EDGE_SIGMA_B_RAW'].size == 10
      assert self.dbc.msgs[aux].sigs['RANGE_RATIO_RAW'].size == 10

  def test_descriptor_backed_raw_fields_cover_all_unmapped_main_bits(self):
    f0_ids = [
      ['25', '26', '28', '29'], ['34', '35', '37', '38'], ['43', '44', '46', '47'],
      ['52', '53', '55', '56'], ['61', '62', '64', '65'], ['70', '71', '73', '74'],
      ['7F', '80', '82', '83'], ['8E', '8F', '91', '92'], ['9D', '9E', 'A0', 'A1'],
      ['AC', 'AD', 'AF', 'B0'], ['BB', 'BC', 'BE', 'BF'], ['CA', 'CB', 'CD', 'CE'],
      ['D9', 'DA', 'DC', 'DD'], ['E8', 'E9', 'EB', 'EC'], ['F7', 'F8', 'FA', 'FB'],
      ['06', '07', '09', '0A'],
    ]
    f1_ids = [
      ['2B', '18', '2C', '19', '2D'], ['3A', '23', '3B', '24', '3C'], ['49', '2E', '4A', '2F', '4B'],
      ['58', '39', '59', '3A', '5A'], ['67', '44', '68', '45', '69'], ['76', '4F', '77', '50', '78'],
      ['85', '5A', '86', '5B', '87'], ['94', '65', '95', '66', '96'], ['A3', '70', 'A4', '71', 'A5'],
      ['B2', '7B', 'B3', '7C', 'B4'], ['C1', '86', 'C2', '87', 'C3'], ['D0', '91', 'D1', '92', 'D2'],
      ['DF', '9C', 'E0', '9D', 'E1'], ['EE', 'A7', 'EF', 'A8', 'F0'], ['FD', 'B2', 'FE', 'B3', 'FF'],
      ['0C', 'BD', '0D', 'BE', '0E'],
    ]
    f2_ids = [
      ['2F', '1A', '30', '1B', '1C'], ['3E', '25', '3F', '26', '27'], ['4D', '30', '4E', '31', '32'],
      ['5C', '3B', '5D', '3C', '3D'], ['6B', '46', '6C', '47', '48'], ['7A', '51', '7B', '52', '53'],
      ['89', '5C', '8A', '5D', '5E'], ['98', '67', '99', '68', '69'], ['A7', '72', 'A8', '73', '74'],
      ['B6', '7D', 'B7', '7E', '7F'], ['C5', '88', 'C6', '89', '8A'], ['D4', '93', 'D5', '94', '95'],
      ['E3', '9E', 'E4', '9F', 'A0'], ['F2', 'A9', 'F3', 'AA', 'AB'], ['01', 'B4', '02', 'B5', 'B6'],
      ['10', 'BF', '11', 'C0', 'C1'],
    ]
    positions = {
      'F0': [(44, 4), (11, 2), (7, 7), (55, 8)],
      'F1': [(46, 7), (23, 11), (15, 8), (39, 9), (7, 6)],
      'F2': [(29, 1), (23, 10), (46, 7), (39, 9), (55, 9)],
    }
    ids_by_frame = {'F0': f0_ids, 'F1': f1_ids, 'F2': f2_ids}
    for slot in range(BOSCH_A_NUM_SLOTS):
      for frame in ('F0', 'F1', 'F2'):
        msg = self.dbc.msgs[BOSCH_A_MAIN_IDS[slot][int(frame[-1])]]
        for logical_id, (start_bit, size) in zip(ids_by_frame[frame][slot], positions[frame], strict=True):
          signal = msg.sigs[f'FW_LID_{logical_id}_RAW']
          assert signal.start_bit == start_bit
          assert signal.size == size
          assert signal.is_little_endian is False

        def affected_bits(signal):
          bits = set()
          for byte in range(8):
            for bit in range(8):
              data = bytearray(8)
              data[byte] = 1 << bit
              if get_raw_value(bytes(data), signal) != 0:
                bits.add((byte, bit))
          return bits

        fields = [msg.sigs[f'FW_LID_{logical_id}_RAW'] for logical_id in ids_by_frame[frame][slot]]
        covered = set()
        for signal in fields:
          bits = affected_bits(signal)
          assert not covered & bits
          covered |= bits

        semantic_aliases = {
          'RANGE_SIGMA_RAW', 'OBJECT_EXISTENCE_PROBABILITY_RAW',
          'ANGULAR_WIDTH_RAW', 'ANGULAR_WIDTH_DEG',
          'NORMALIZED_CLOSING_RAW', 'NORMALIZED_CLOSING', 'NORMALIZED_CLOSING_SIGMA_RAW',
        }
        existing_bits = set()
        for name, signal in msg.sigs.items():
          if not name.startswith('FW_LID_') and name not in semantic_aliases:
            existing_bits |= affected_bits(signal)
        assert not existing_bits & covered


# --- 3. range / azimuth extraction + invalid sentinels ------------------------------------------------

class TestRangeAzimuth:
  def test_direct_vrel_rails_publish_the_bound(self):
    """raw 0 / 1728 are saturation rails, and a rail must still be published as a bound.

    A stationary car approached above 12.0 m/s rails on EVERY sweep, so treating a rail as "no
    measurement" made the coast permanent, outlive BOSCH_A_STALE_S and delete the radar point.
    Measured on route 000001f9 at 29:52: 88 of 88 active frames railed on two stopped cars, the
    radar lead was dropped, and the planner commanded 0.00 while closing at 76 m with a 6.6 s TTC.
    Publishing -12.0 understates the closing rate but still brakes; deleting the object does not.
    """
    assert _bosch_a_direct_vrel(BOSCH_A_DIRECT_VREL_MIN_RAW) == pytest.approx(-12.0)
    assert _bosch_a_direct_vrel(BOSCH_A_DIRECT_VREL_MAX_RAW) == pytest.approx(12.0)
    assert _bosch_a_direct_vrel(BOSCH_A_DIRECT_VREL_MIN_RAW + 1) == pytest.approx(-12.0 + 1 / 72)
    assert _bosch_a_direct_vrel(BOSCH_A_DIRECT_VREL_MAX_RAW - 1) == pytest.approx(12.0 - 1 / 72)

  def test_railed_stopped_car_keeps_its_radar_point(self):
    """The regression that motivated the above: a permanently railed object must not be deleted."""
    ri = make_radar_interface()
    rr = None
    for i, raw in enumerate((1700, 1680, 1660, 1640, 1620, 1600, 1580)):
      rr = ri.update(sweep(0, i & 0xF, 0x7, raw, 1024, 1 + 2 * i, i * 70_000_000, with_aux=True,
                           direct_vrel_raw=BOSCH_A_DIRECT_VREL_MIN_RAW, direct_vrel_uncertainty_raw=90))
    assert len(rr.points) == 1
    # BOSCH_A_U11_FLOOR_UNMEASURED: kept, but published unmeasured with its own range fit (-1.25 m per 70 ms), not -12.
    assert rr.points[0].vRel == pytest.approx(-20 * BOSCH_A_RANGE_SCALE_M / 0.07)
    assert not rr.points[0].deprecated.measured

  def test_range_scale_matches_firmware_q16_conversion(self):
    """The range scale is firmware-derived, not capture-fitted.

    Stock 36802TBA AC004 converts the internal object range with (q16 - n) / 128, and the
    wire-to-internal scaling is q16 = sat16(round(8 * raw_range)). Composing the two gives

        range_m = (8 * raw_range - n) / 128 = raw_range / 16 - n / 128

    so the scale is exactly 1/16 m per raw count and the offset term is a per-unit
    calibration value, not a constant.

    This test exists because the previous 0.05712 was 16 * 0.00357, and that 0.00357 was
    solved from a single tape measurement with the offset assumed -- one equation, two
    unknowns -- which read progressively short with distance. Pin the scale so a future
    fit against a vision or capture reference cannot silently reintroduce that error.
    """
    Q16_PER_RAW_COUNT = 8           # q16 = 8 * raw_range
    FIRMWARE_Q7_DIVISOR = 128       # AC004's (q16 - n) / 128; Q7 is this firmware's unit
    assert BOSCH_A_RANGE_SCALE_M == Q16_PER_RAW_COUNT / FIRMWARE_Q7_DIVISOR

    # The *8 exists so a full-scale 12-bit wire value exactly fills the internal int16.
    # If this ever fails the q16 relation above is wrong and the scale must be re-derived.
    assert Q16_PER_RAW_COUNT * 0xFFF <= 0x7FFF

  def test_range_scale_and_offset(self):
    ri = make_radar_interface()
    raw_range = 1000
    ri.update(sweep(0, 0, 0x7, raw_range, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, raw_range, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].dRel == pytest.approx(BOSCH_A_RANGE_SCALE_M * raw_range + BOSCH_A_RANGE_OFFSET_M)

  def test_range_invalid_sentinel_0xfff(self):
    ri = make_radar_interface()
    rr = ri.update(sweep(0, 0, 0x7, 0xFFF, 1024, 1, 0))
    assert len(rr.points) == 0

  def test_azimuth_invalid_sentinel_0x7ff(self):
    ri = make_radar_interface()
    rr = ri.update(sweep(0, 0, 0x7, 1000, 0x7FF, 1, 0))
    assert len(rr.points) == 0

  def test_yrel_sign_right_of_center_is_negative(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024 - 100, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1000, 1024 - 100, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))  # raw_angle < center -> right of center
    d = BOSCH_A_RANGE_SCALE_M * 1000 + BOSCH_A_RANGE_OFFSET_M
    expected_y = d * math.tan(-100.0 / 2048.0)
    assert rr.points[0].yRel == pytest.approx(expected_y)
    assert rr.points[0].yRel < 0

  def test_yrel_sign_left_of_center_is_positive(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024 + 100, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1000, 1024 + 100, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))  # raw_angle > center -> left of center
    assert rr.points[0].yRel > 0

  def test_yrel_zero_on_boresight(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1000, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].yRel == pytest.approx(0.0, abs=1e-9)


# --- 4. status / life invalid sentinels -> conservative object_valid ---------------------------------

class TestObjectValid:
  def test_status_invalid_0xf(self):
    ri = make_radar_interface()
    rr = ri.update(sweep(0, 0, 0xF, 1000, 1024, 1, 0))
    assert len(rr.points) == 0

  def test_life_invalid_0xfff(self):
    ri = make_radar_interface()
    rr = ri.update(sweep(0, 0, 0x7, 1000, 1024, 0xFFF, 0))
    assert len(rr.points) == 0

  def test_first_valid_sample_is_withheld(self):
    ri = make_radar_interface()
    rr = ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    assert len(rr.points) == 0

  def test_invalid_observation_does_not_mature_or_preserve_birth_history(self):
    ri = make_radar_interface()
    rr = ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    assert len(rr.points) == 0
    rr = ri.update(sweep(0, 1, 0xF, 1000, 1024, 3, 50_000_000))
    assert len(rr.points) == 0
    rr = ri.update(sweep(0, 2, 0x7, 1000, 1024, 1, 100_000_000))
    assert len(rr.points) == 0

  def test_incomplete_observation_does_not_mature_or_add_history(self):
    ri = make_radar_interface()
    rr = ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    assert len(rr.points) == 0

    f0, f1, f2, f3 = BOSCH_A_MAIN_IDS[0]
    _, _, _, trig_f3 = BOSCH_A_MAIN_IDS[15]
    frames = [
      CanData(f0, make_f0(1, 0x7, 1010, 1024), BUS),
      CanData(f1, make_f1(1), BUS),
      CanData(f2, make_f2(1, 3), BUS),
      CanData(trig_f3, make_f3(1), BUS),
    ]
    rr = ri.update([(50_000_000, [_stamp(f) for f in frames])])
    assert len(rr.points) == 0

    rr = ri.update(sweep(0, 2, 0x7, 1020, 1024, 5, 100_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert len(rr.points) == 1
    assert math.isfinite(rr.points[0].vRel)

  def test_second_same_incarnation_sample_without_u11_or_ratio_does_not_publish(self):
    # Neither native U11 nor the ratio field is available on either sample (no aux at all). The raw
    # one-sweep range derivative that would previously have matured into a measured=True point is now
    # never published: with no prior trusted velocity to coast, the point is withheld entirely.
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000))
    assert len(rr.points) == 0


# --- 5. lifecycle continuity ---------------------------------------------------------------------------

class TestLifecycle:
  def test_normal_plus_2_continuity_keeps_trackid(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    t0 = rr.points[0].trackId
    rr = ri.update(sweep(0, 2, 0x7, 1020, 1024, 5, 100_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].trackId == t0

  def test_continuity_across_dropped_sweeps(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    t0 = rr.points[0].trackId
    # 3 sweeps missed: frame_idx jumps from 0 to 4, life must jump by 2*4=8 to stay the same incarnation
    rr = ri.update(sweep(0, 4, 0x7, 1040, 1024, 9, 200_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].trackId == t0

  def test_frame_idx_wraps_mod_16(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 14, 0x7, 1000, 1024, 1, 0))
    rr = ri.update(sweep(0, 15, 0x7, 1005, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    t0 = rr.points[0].trackId
    # frame_idx wraps 14 -> 1 (delta = (1-14)&0xF = 3), life must advance by 6
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 7, 150_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].trackId == t0

  def test_life_wraps_mod_4096_stays_same_incarnation(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 14, 0x7, 1000, 1024, 4094, 0))
    rr = ri.update(sweep(0, 15, 0x7, 1005, 1024, 0, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    t0 = rr.points[0].trackId
    # frame_idx 14 -> 0 (delta=2), life 4094 -> 2 ((2-4094)&0xFFF == 4 == 2*2)
    rr = ri.update(sweep(0, 0, 0x7, 1010, 1024, 2, 100_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].trackId == t0

  def test_in_place_replacement_no_invalid_gap_resets_history_but_keeps_can_id(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    t0 = rr.points[0].trackId
    # frame_idx advances normally (+1) but life jumps by an unrelated odd amount -> NOT +2*frame_delta
    rr = ri.update(sweep(0, 2, 0x7, 50, 1024, 7, 100_000_000))
    assert len(rr.points) == 0  # replacement birth sample is withheld
    rr = ri.update(sweep(0, 3, 0x7, 50, 1024, 9, 150_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert len(rr.points) == 1
    assert rr.points[0].trackId == t0

  def test_replacement_does_not_assume_life_restarts_at_1_3_5(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    t0 = rr.points[0].trackId
    # a "replacement" that happens to restart life at a big/even value must still be treated as a
    # replacement (not death) because it fails the frame/life identity, regardless of the new value's parity
    rr = ri.update(sweep(0, 2, 0x7, 50, 1024, 4000, 100_000_000))
    assert len(rr.points) == 0  # replacement birth sample is withheld
    rr = ri.update(sweep(0, 3, 0x7, 50, 1024, 4002, 150_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].trackId == t0
    assert rr.points[0].vRel == 0.0
    assert len(rr.points) == 1

  def _drive_held_life(self, held_life, sweeps=12):
    """Two clean sweeps into `held_life`, then `sweeps` more with the counter not advancing.

    Returns the per-sweep published point count after the counter stops moving.
    """
    ri = make_radar_interface()
    kw = dict(with_aux=True, direct_vrel_raw=800, direct_vrel_uncertainty_raw=40)
    ri.update(sweep(0, 0, 0x7, 1000, 1024, held_life - 2, 0, **kw))
    ri.update(sweep(0, 1, 0x7, 997, 1024, held_life, 70_000_000, **kw))
    published = []
    for i in range(2, 2 + sweeps):
      rr = ri.update(sweep(0, i % 16, 0x7, 1000 - 3 * i, 1024, held_life, i * 70_000_000, **kw))
      published.append(len(rr.points))
    return ri, published

  def test_saturated_lifecycle_counter_keeps_publishing(self):
    """LIFECYCLE_RAW pins at 0xFFE after ~137 s of tracking and cannot advance again.

    Treating each of those sweeps as a new incarnation deletes a still-visible object for as long
    as it stays visible: measured at 121.8 s of continuous suppression of the followed lead on
    00000232--fc8dad0d18 (track 37, last published at dRel 38.9 m, yRel -0.1 m).
    """
    ri, published = self._drive_held_life(BOSCH_A_LIFE_SATURATED)
    assert all(n == 1 for n in published), published
    assert len(ri._tracks[1].samples) > 2          # range history survives, so vRel stays derivable
    assert ri.pts[1].deprecated.measured

  def test_a_stuck_but_unsaturated_counter_is_still_a_lifecycle_break(self):
    """D-009 negative control: the carve-out is for the saturation value only.

    A counter frozen anywhere else is a real discontinuity and must still clear the history --
    otherwise this fix would silently disable incarnation detection everywhere.
    """
    _ri, published = self._drive_held_life(4000)
    assert all(n == 0 for n in published), published

  def test_death_then_rebirth_reuses_can_id_with_clean_history(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    t0 = rr.points[0].trackId
    rr = ri.update(sweep(0, 1, 0xF, 1000, 1024, 3, 50_000_000))  # death
    assert len(rr.points) == 0
    rr = ri.update(sweep(0, 2, 0x7, 1000, 1024, 1, 100_000_000))  # rebirth, first sample withheld
    assert len(rr.points) == 0
    rr = ri.update(sweep(0, 3, 0x7, 1000, 1024, 3, 150_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert len(rr.points) == 1
    assert rr.points[0].trackId == t0


# --- 6. CAN track identity is the RadarPoint identity -----------------------------------------------

def test_trackid_comes_from_can_and_is_not_synthetic():
  ri = make_radar_interface()
  seen_ids = set()
  t = 0
  for i in range(10):
    frame_idx = (2 * i) % 16
    can_track_id = i + 1
    ri.update(sweep(0, frame_idx, 0x7, 1000, 1024, 1, t, track_id=can_track_id))
    t += 50_000_000
    rr = ri.update(sweep(0, (frame_idx + 1) % 16, 0x7, 1000, 1024, 3, t, track_id=can_track_id,
                         with_aux=True, direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    t += 50_000_000
    assert can_track_id in {point.trackId for point in rr.points}
    seen_ids.add(can_track_id)
  assert seen_ids == set(range(1, 11))


# --- 7. vRel derivative sign -------------------------------------------------------------------------

class TestU11Scale72:
  """D-074: U11 is 1/72 m/s per count, built in. There is no 1/64 path and no param."""

  @staticmethod
  def _publish(ri, raw=800, sweeps=2, start=0):
    rr = None
    for i in range(start, start + sweeps):
      rr = ri.update(sweep(0, i % 16, 0x7, 1000 + 10 * i, 1024, 1 + 2 * i, i * 50_000_000, with_aux=True,
                           direct_vrel_raw=raw, direct_vrel_uncertainty_raw=80))
    return rr

  def test_decode_is_1_72(self):
    ri = make_radar_interface()
    assert ri.u11_counts_per_mps == 72
    assert self._publish(ri).points[0].vRel == pytest.approx((800 - 864) / 72.0)

  def test_full_raw_domain_and_rails(self):
    for raw in range(BOSCH_A_DIRECT_VREL_MIN_RAW, BOSCH_A_DIRECT_VREL_MAX_RAW + 1):
      assert _bosch_a_direct_vrel(raw) == (raw - 864) / 72.0
    assert _bosch_a_direct_vrel(0) == -12.0
    assert _bosch_a_direct_vrel(1728) == 12.0
    assert _bosch_a_direct_vrel(BOSCH_A_DIRECT_VREL_INVALID) is None
    assert _bosch_a_direct_vrel(1729) is None

  def test_rail_interval_uses_the_1_72_rails(self):
    low, high = _bosch_a_direct_vrel(0), _bosch_a_direct_vrel(1728)
    assert _bosch_a_direct_vrel_interval(low) == (-BOSCH_A_DIRECT_VREL_RAIL_BOUND_MPS, low)
    assert _bosch_a_direct_vrel_interval(high) == (high, BOSCH_A_DIRECT_VREL_RAIL_BOUND_MPS)
    inside = _bosch_a_direct_vrel(1)
    assert _bosch_a_direct_vrel_interval(inside) == (inside, inside)


class TestRangeOffsetFirmwareDefault:
  """D-076: fixed firmware ROM-default offset per car, -346/128 Civic (cfg 5530) and -341/128 CR-V. No toggle, no CAN 0x669."""

  def test_constants(self):
    assert BOSCH_A_RANGE_OFFSET_M == -346.0 / 128.0 == -2.703125
    assert bosch_a_range_offset_m(CAR.HONDA_CIVIC_BOSCH) == -2.703125
    assert bosch_a_range_offset_m(CAR.HONDA_CRV_5G) == -2.6640625
    assert bosch_a_range_offset_m(CAR.HONDA_CRV_HYBRID) == -2.6640625
    assert bosch_a_range_offset_m() == -2.703125

  def test_interface_uses_the_cars_offset(self):
    assert make_radar_interface().range_offset_m == -2.703125

  def test_0x669_is_not_a_parser_message(self):
    # A required 0x669 would raise canError whenever the camera does not send it.
    ri = make_radar_interface()
    assert 0x669 not in ri.rcp.message_states
    assert not hasattr(radar_interface_module, "bosch_a_range_offset_fallback_enabled")

class TestVrel:
  def test_direct_aux_vrel_is_preferred_over_range_derivative(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=800, direct_vrel_uncertainty_raw=80))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                         direct_vrel_raw=800, direct_vrel_uncertainty_raw=80))
    assert rr.points[0].vRel == pytest.approx((800 - 864) / 72.0)

  def test_direct_aux_vrel_domain_and_sentinel(self):
    # The domain endpoints are saturation rails, published as bounds -- see
    # test_direct_vrel_rails_publish_the_bound.
    assert _bosch_a_direct_vrel(0) == pytest.approx(-12.0)
    assert _bosch_a_direct_vrel(BOSCH_A_DIRECT_VREL_MAX_RAW) == pytest.approx(12.0)
    assert _bosch_a_direct_vrel(1) == pytest.approx(-12.0 + 1 / 72)
    assert _bosch_a_direct_vrel(BOSCH_A_DIRECT_VREL_MAX_RAW - 1) == pytest.approx(12.0 - 1 / 72)
    assert _bosch_a_direct_vrel(1729) is None
    assert _bosch_a_direct_vrel(BOSCH_A_DIRECT_VREL_INVALID) is None
    assert _bosch_a_direct_vrel(0x7FF) is None
    assert _bosch_a_direct_vrel(None) is None
    assert _bosch_a_direct_vrel(0, BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW) == pytest.approx(-12.0)
    assert _bosch_a_direct_vrel(0, BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW + 1) is None
    assert _bosch_a_direct_vrel(864, 0x3FE) is None
    assert _bosch_a_direct_vrel(864, 0x3FF) is None

  def test_high_u10_live_vrel_without_trust_is_withheld(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=False))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                         direct_vrel_raw=0, direct_vrel_uncertainty_raw=1023))
    assert len(rr.points) == 0
    assert len(ri._tracks[1].samples) == 1

  def test_fast_clean_range_rate_without_u11_or_ratio_withholds_rather_than_synthesizes(self):
    # This range rate (~-46 m/s) is well outside native U11's representable domain ([-12.0, 12.0]
    # m/s), so it can only ever have come from the raw one-sweep derivative -- which is exactly the
    # synthesized-measurement hazard this fix closes. With no aux at all (no U11, no ratio) and no
    # prior trusted velocity to coast, the point is correctly withheld rather than published at a
    # fabricated, out-of-band rate.
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 2000, 1024, 1, 0, with_aux=False))
    ri.update(sweep(0, 1, 0x7, 1945, 1024, 3, 70_000_000, with_aux=False))
    rr = ri.update(sweep(0, 2, 0x7, 1889, 1024, 5, 140_000_000, with_aux=False))
    assert len(rr.points) == 0
    assert len(ri._tracks[1].samples) == 1  # only the birth sample; later cycles coast, not append

  def test_high_u10_live_vrel_coasts_recent_trusted_motion(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1724, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=696, direct_vrel_uncertainty_raw=84))
    rr = ri.update(sweep(0, 1, 0x7, 1724, 1024, 3, 50_000_000, with_aux=True,
                   direct_vrel_raw=696, direct_vrel_uncertainty_raw=84))
    assert rr.points[0].vRel == pytest.approx((696 - 864) / 72.0)
    # R146/S21 ID50's 19-count / 68.962 ms high-U10 step would have synthesized about -15.74 m/s.
    rr = ri.update(sweep(0, 2, 0x7, 1705, 1024, 5, 118_962_000, with_aux=True,
                   direct_vrel_raw=585, direct_vrel_uncertainty_raw=744))
    assert len(rr.points) == 1
    assert rr.points[0].dRel == pytest.approx(1705 * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M)
    assert rr.points[0].vRel == pytest.approx((696 - 864) / 72.0)
    assert not rr.points[0].deprecated.measured
    assert len(ri._tracks[1].samples) == 2

  def test_qualified_u11_recovers_immediately_after_high_u10_coast(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=800, direct_vrel_uncertainty_raw=84))
    ri.update(sweep(0, 1, 0x7, 1000, 1024, 3, 50_000_000, with_aux=True,
              direct_vrel_raw=800, direct_vrel_uncertainty_raw=84))
    ri.update(sweep(0, 2, 0x7, 981, 1024, 5, 100_000_000, with_aux=True,
              direct_vrel_raw=585, direct_vrel_uncertainty_raw=744))
    rr = ri.update(sweep(0, 3, 0x7, 980, 1024, 7, 150_000_000, with_aux=True,
                   direct_vrel_raw=760, direct_vrel_uncertainty_raw=84))
    assert rr.points[0].vRel == pytest.approx((760 - 864) / 72.0)
    assert rr.points[0].deprecated.measured
    assert ri._tracks[1].last_trusted_vrel == pytest.approx((760 - 864) / 72.0)

  def test_high_u10_coast_cannot_cross_lifecycle_incarnation(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=800, direct_vrel_uncertainty_raw=84))
    ri.update(sweep(0, 1, 0x7, 1000, 1024, 3, 50_000_000, with_aux=True,
              direct_vrel_raw=800, direct_vrel_uncertainty_raw=84))
    rr = ri.update(sweep(0, 2, 0x7, 981, 1024, 99, 100_000_000, with_aux=True,
                   direct_vrel_raw=585, direct_vrel_uncertainty_raw=744))
    assert len(rr.points) == 0
    assert ri._tracks[1].last_trusted_vrel is None

  def test_high_u10_coast_keeps_the_point_with_a_stale_velocity(self):
    # A coast means the velocity is doubtful, not that the object left. Object existence is decided
    # earlier by STATUS/existence/range validity. Dropping the point here is the 000001f9 failure:
    # a stopped car was deleted, radard fell back to vision, and the planner commanded 0.00 at 76 m.
    # The geometry must keep publishing; the stale velocity is flagged by measured=False.
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=800, direct_vrel_uncertainty_raw=84))
    ri.update(sweep(0, 1, 0x7, 1000, 1024, 3, 50_000_000, with_aux=True,
              direct_vrel_raw=800, direct_vrel_uncertainty_raw=84))
    ri.update(sweep(0, 2, 0x7, 981, 1024, 5, 100_000_000, with_aux=True,
              direct_vrel_raw=585, direct_vrel_uncertainty_raw=744))
    rr = ri.update(sweep(0, 3, 0x7, 980, 1024, 7, 300_000_000, with_aux=True,
                   direct_vrel_raw=585, direct_vrel_uncertainty_raw=744))
    assert len(rr.points) == 1
    point = rr.points[0]
    # fresh geometry, held velocity, explicitly unmeasured
    assert point.dRel == pytest.approx(980 * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M)
    assert point.vRel == pytest.approx((800 - 864) / 72.0)
    assert not point.deprecated.measured

  def test_coast_still_retires_when_the_radar_stops_reporting(self):
    # The complement of the above: holding a coasting point must not leak a track that the radar
    # has actually dropped. _bosch_a_retire_stale_tracks still owns that, via last_seen_nanos.
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=800, direct_vrel_uncertainty_raw=84))
    ri.update(sweep(0, 1, 0x7, 1000, 1024, 3, 50_000_000, with_aux=True,
              direct_vrel_raw=800, direct_vrel_uncertainty_raw=84))
    rr = ri.update(sweep(0, 2, 0x7, 981, 1024, 5, 100_000_000, with_aux=True,
                   direct_vrel_raw=585, direct_vrel_uncertainty_raw=744))
    assert len(rr.points) == 1
    # radar stops reporting this identity: a later cycle past BOSCH_A_STALE_S must retire it
    rr = ri.update(sweep(1, 0, 0x7, 900, 1024, 1, 1_000_000_000, with_aux=True, track_id=2,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert all(p.trackId != 1 for p in rr.points)

  def test_range_ratio_conversion_and_sentinel(self):
    assert _bosch_a_range_ratio(500) == pytest.approx(1.0)
    assert _bosch_a_range_ratio(509) == pytest.approx(1.009)
    assert _bosch_a_range_ratio(BOSCH_A_RANGE_RATIO_INVALID) is None
    assert _bosch_a_range_ratio(None) is None
    assert _bosch_a_range_ratio_vrel(600, 10.0, 0.1) == pytest.approx(-10.0)

  @pytest.mark.parametrize(
    ('direct_raw', 'range_raw', 'rawca', 'expected'),
    [(0, 974, 528, -12.0), (BOSCH_A_DIRECT_VREL_MAX_RAW, 1027, 472, 12.0)],
  )
  def test_range_ratio_does_not_extend_direct_vrel_rails(self, direct_raw, range_raw, rawca, expected):
    """At a rail the bound is published, and the ratio field must not be used to exceed it.

    The range-ratio channel is the only one that keeps moving past a rail, but it under-reports by
    ~25% against clean U11 (slope 0.72-0.84 over 5011 comparisons), so it cannot be trusted to
    manufacture a larger closing rate. Recovering the true value past a rail needs the range
    channel and a separate validated change.

    The low rail is that change (BOSCH_A_U11_FLOOR_UNMEASURED): raw 0 publishes the D-043 range fit, unmeasured; a
    two-sweep floor birth has no fit yet, so it publishes the floor itself as the bound, unmeasured. The ratio still
    never sets it.
    """
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=direct_raw, direct_vrel_uncertainty_raw=80, rawca=500))
    rr = ri.update(sweep(0, 1, 0x7, range_raw, 1024, 3, 50_000_000, with_aux=True,
                         direct_vrel_raw=direct_raw, direct_vrel_uncertainty_raw=80, rawca=rawca))
    if expected is None:
      assert len(rr.points) == 0
      return
    assert len(rr.points) == 1
    assert rr.points[0].vRel == pytest.approx(expected)
    assert rr.points[0].deprecated.measured == (direct_raw != 0)

  def test_first_sighting_vrel_is_zero(self):
    ri = make_radar_interface()
    rr = ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    assert len(rr.points) == 0

  def test_decreasing_range_gives_negative_vrel(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 2000, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1990, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=800, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].vRel < 0

  def test_increasing_range_gives_positive_vrel(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=928, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].vRel > 0

  def test_vrel_reflects_native_u11_not_raw_range_derivative(self):
    # The raw one-sweep (dRel-previous_range)/dt derivative is no longer an authoritative vRel source
    # (see TestFallbackNeverPublishes below) -- confirm the published value tracks native U11 exactly,
    # not the range-implied rate, even when the two would clearly disagree.
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    d1 = BOSCH_A_RANGE_SCALE_M * 1000 + BOSCH_A_RANGE_OFFSET_M
    d2 = BOSCH_A_RANGE_SCALE_M * 1010 + BOSCH_A_RANGE_OFFSET_M
    range_implied_vrel = (d2 - d1) / 0.05
    assert range_implied_vrel != pytest.approx(0.0)
    assert rr.points[0].vRel == pytest.approx(0.0)  # native U11 (864 -> 0 m/s), not the range rate

  def test_incarnation_does_not_carry_velocity_across_lifecycle_break(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 2000, 1024, 1, 0))
    rr = ri.update(sweep(0, 1, 0x7, 1990, 1024, 3, 50_000_000, with_aux=True,
                        direct_vrel_raw=800, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].vRel < 0
    # replacement: life breaks identity -> fresh incarnation; its first sample is withheld.
    rr = ri.update(sweep(0, 2, 0x7, 500, 1024, 99, 100_000_000))
    assert len(rr.points) == 0
    rr = ri.update(sweep(0, 3, 0x7, 500, 1024, 101, 150_000_000, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].vRel == 0.0

  def test_discontinuous_range_coasts_last_accepted_point_unmeasured(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=864, direct_vrel_uncertainty_raw=80, rawca=500))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=80, rawca=500))
    assert len(rr.points) == 1
    accepted_drel = rr.points[0].dRel

    rr = ri.update(sweep(0, 2, 0x7, 100, 1024, 5, 100_000_000, with_aux=True,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=80, rawca=509,
                         range_sigma_raw=4, existence_raw=0))
    assert len(rr.points) == 1
    assert rr.points[0].dRel == pytest.approx(accepted_drel)
    assert rr.points[0].deprecated.measured is False
    assert len(ri._tracks[1].samples) == 2

  def test_gross_velocity_range_disagreement_coasts(self):
    """A velocity that flatly contradicts the measured range over several sweeps is not published.

    Modelled on Peter route 000001eb at 6:59, where U11 reported -10.58 m/s closing while the range
    was actually opening at +0.46 m/s across a track-identity change. The per-sweep innovation gate
    cannot see this: over one ~70 ms sweep even a 3 m/s error moves the range only 0.2 m.
    """
    ri = make_radar_interface()
    # Four sweeps of a steadily OPENING range, with a believable velocity, to build history.
    for i, raw in enumerate((800, 810, 820, 830)):
      ri.update(sweep(0, i, 0x7, raw, 1024, 1 + 2 * i, i * 70_000_000, with_aux=True,
                      direct_vrel_raw=864 + 40, direct_vrel_uncertainty_raw=0))
    # Fifth sweep: range keeps opening, but U11 now claims hard closing. Contradiction ~11 m/s.
    rr = ri.update(sweep(0, 4, 0x7, 840, 1024, 9, 280_000_000, with_aux=True,
                         direct_vrel_raw=864 - 11 * 72, direct_vrel_uncertainty_raw=0))
    assert len(rr.points) == 1
    # Coasted, not the contradictory value, and flagged unmeasured.
    assert rr.points[0].vRel == pytest.approx(40 / 72.0)
    assert not rr.points[0].deprecated.measured
    # Geometry is still live -- only the velocity was in question.
    assert rr.points[0].dRel == pytest.approx(840 * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M)

  def test_exact_peter_reset_sequence_never_rebases_on_rejected_ranges(self):
    ri = make_radar_interface()
    rows = [
      # range_raw, U11, U10, range sigma, existence, 00CA
      (203, 713, 136, 2, 3, 512),
      (198, 740, 92, 2, 76, 510),
      (64, 463, 612, 4, 0, 509),
      (82, 554, 437, 4, 0, 508),
      (96, 727, 101, 2, 0, 508),
      (102, 795, 31, 1, 0, 508),
    ]
    times = [0, 70_696_000, 120_413_000, 200_552_000, 260_482_000, 331_286_000]

    rr = None
    for i, ((range_raw, u11, u10, sigma, existence, rawca), t_nanos) in enumerate(zip(rows, times, strict=True)):
      rr = ri.update(sweep(
        0, (11 + i) & 0xF, 0x9, range_raw, 1024, 5 + 2 * i, t_nanos,
        with_aux=True, direct_vrel_raw=u11, direct_vrel_uncertainty_raw=u10,
        rawca=rawca, range_sigma_raw=sigma, existence_raw=existence, track_id=23,
      ))

    assert rr is not None
    assert len(rr.points) == 0

    # The point of this sequence: the 64/82/96/102 reset ranges are rejected and must never enter
    # the accepted history, so they can never become the baseline for a later derivative. Asserted
    # by content rather than by sample count, which also depends on the u10 gate -- row 0 (u10=136)
    # now coasts on uncertainty, which is the intended stricter behaviour, not a regression.
    accepted = [sample[1] for sample in ri._tracks[23].samples]
    rejected = [raw * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M for raw in (64, 82, 96, 102)]
    assert not any(any(abs(a - r) < 1e-6 for r in rejected) for a in accepted)
    assert accepted[-1] == pytest.approx(198 * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M)


# --- 7a. D-054: a velocity coast must not freeze the range-innovation baseline ----------------------

class TestCoastAdvancesTheRangeGate:
  """Modelled on 00000232--3a01619ce5 track 43 at t=1258.7 s, the followed lead pulling away 78.5 ->
  84.5 m with a degraded range sigma of 7. U11 lagged the opening, the D-043 rate check coasted, and
  the gate kept predicting from the last ACCEPTED sample with that lagging U11 across a growing gap:
  the error grew 0.86 / 1.12 / 1.53 / 1.87 / 2.11 m, the degraded 2.0 m limit rejected the lead, and
  radar did not publish it again for 15 s while it closed to 27 m."""
  OPEN_RAW_PER_SWEEP = 7  # 0.4375 m per 70 ms sweep: receding at 6.25 m/s
  U11_TRUE = 864 + 400  # +6.25 m/s, what the range shows
  U11_LAGGING = 864 + 64  # +1.0 m/s: more than 3 m/s short of the range rate, so D-043 coasts
  DT_NANOS = 70_000_000

  def _drive(self, ri, i, u11, range_raw=None, existence_raw=126):
    raw = 800 + self.OPEN_RAW_PER_SWEEP * i if range_raw is None else range_raw
    rr = ri.update(sweep(0, i & 0xF, 0x7, raw, 1024, 1 + 2 * i, i * self.DT_NANOS, with_aux=True,
                         direct_vrel_raw=u11, direct_vrel_uncertainty_raw=0, range_sigma_raw=7,
                         existence_raw=existence_raw))
    return rr, raw * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M

  def _history_then_coast(self, ri, coasts):
    for i in range(4):
      rr, _ = self._drive(ri, i, self.U11_TRUE)
    assert rr.points[0].deprecated.measured is True
    history = list(ri._tracks[1].samples)
    d_rel = None
    for i in range(4, 4 + coasts):
      rr, d_rel = self._drive(ri, i, self.U11_LAGGING)
      assert len(rr.points) == 1 and rr.points[0].deprecated.measured is False
    return history, rr, d_rel

  def test_lagging_u11_coast_keeps_publishing_a_receding_lead(self):
    ri = make_radar_interface()
    history, _, _ = self._history_then_coast(ri, coasts=0)
    # 25 coasted sweeps (1.75 s): the old baseline would have rejected the lead from the sixth.
    for i in range(4, 29):
      rr, d_rel = self._drive(ri, i, self.U11_LAGGING)
      assert len(rr.points) == 1, f"lead deleted at sweep {i}"
      assert rr.points[0].dRel == pytest.approx(d_rel)
      assert rr.points[0].vRel == pytest.approx(400 / 72.0)
      assert rr.points[0].deprecated.measured is False
    # Coasted ranges moved only the gate's baseline. The velocity history is still accepted-only.
    assert list(ri._tracks[1].samples) == history
    rr, d_rel = self._drive(ri, 29, self.U11_TRUE)
    assert rr.points[0].deprecated.measured is True
    assert rr.points[0].dRel == pytest.approx(d_rel)

  def test_rejection_right_after_a_coast_holds_the_point_instead_of_deleting_it(self):
    # The last ACCEPTED sample is 0.35 s old here, but the last range that passed the gate is one sweep
    # old. A single bad sweep must hold the point unmeasured (D-041/D-042), not delete the lead.
    ri = make_radar_interface()
    _, _, last_d_rel = self._history_then_coast(ri, coasts=4)
    rr, _ = self._drive(ri, 8, self.U11_LAGGING, range_raw=800 + 7 * 8 - 128)  # 8 m short
    assert len(rr.points) == 1
    assert rr.points[0].dRel == pytest.approx(last_d_rel)
    assert rr.points[0].deprecated.measured is False
    rr, d_rel = self._drive(ri, 9, self.U11_LAGGING)
    assert len(rr.points) == 1 and rr.points[0].dRel == pytest.approx(d_rel)

  def test_reset_ranges_after_a_coast_never_publish_or_enter_history(self):
    # Negative control for the change above. The recorded reset shape (see
    # test_exact_peter_reset_sequence_never_rebases_on_rejected_ranges): a large drop that walks back
    # 1.5 / 1.0 / 0.45 m per sweep, existence 0. A coast must not let any of it become a baseline.
    ri = make_radar_interface()
    history, _, _ = self._history_then_coast(ri, coasts=4)
    lead_raw = 800 + 7 * 8
    reset_raws = [lead_raw - 128, lead_raw - 104, lead_raw - 88, lead_raw - 81, lead_raw - 81, lead_raw - 81]
    reset_ranges = [raw * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M for raw in reset_raws]
    for k, raw in enumerate(reset_raws):
      rr, _ = self._drive(ri, 8 + k, self.U11_LAGGING, range_raw=raw, existence_raw=0)
      assert not any(any(abs(p.dRel - r) < 1e-6 for r in reset_ranges) for p in rr.points)
    assert len(rr.points) == 0  # held no longer than BOSCH_A_STALE_S past the last trusted range
    assert list(ri._tracks[1].samples) == history
    assert not any(abs(ri._tracks[1].range_anchor[1] - r) < 1e-6 for r in reset_ranges)

  def test_a_persistent_range_step_is_still_rejected_and_not_re_anchored(self):
    # D-054 does NOT re-anchor on a real, lasting step: replay found returning excursions just as
    # self-consistent as lasting ones, and no lasting >= 5 m step on the lead. Recorded as open.
    ri = make_radar_interface()
    history, _, _ = self._history_then_coast(ri, coasts=0)
    for i in range(4, 14):
      rr, _ = self._drive(ri, i, self.U11_TRUE, range_raw=800 + 7 * i + 128, existence_raw=126)
    assert len(rr.points) == 0
    assert list(ri._tracks[1].samples) == history

  # 00000237--77313c5a66 track 12 from t=7649.64 s: (ms, range m, U11 m/s, range sigma, existence, u10).
  # A new object whose range walked out 25.4 -> 28.1 m while U11 said -2 m/s, then closed to 19 m.
  RECORDED_237_TRACK_12 = [
    (0, 25.44, -1.94, 10, 1, 233), (70, 26.38, -1.98, 8, 57, 184), (140, 26.75, -2.08, 8, 81, 166),
    (210, 27.31, -1.45, 7, 99, 106), (270, 27.81, -1.50, 6, 84, 93), (350, 28.06, -1.80, 6, 98, 112),
    (400, 25.31, -2.69, 5, 86, 123), (470, 23.38, -3.06, 4, 122, 94), (540, 22.12, -1.81, 4, 125, 52),
    (610, 21.31, -1.83, 3, 125, 43), (680, 20.69, -1.88, 3, 125, 30), (750, 20.12, -1.94, 3, 125, 33),
    (820, 19.62, -1.95, 3, 125, 38), (890, 19.25, -1.98, 2, 110, 37),
  ]

  def test_a_coasted_range_walk_does_not_lock_out_the_real_ranges(self):
    # The mirror of the 232 lockout: here the RANGE walked and U11 was right. The rate check coasted
    # the walk (27.81, 28.06 m); measured against that walk alone, the real 25.31 m misses by 2.6 m
    # and the anchor-only draft rejected this object for 53 s. It is consistent with the last
    # accepted sample (27.31 m), so it must stay published.
    ri = make_radar_interface()
    for i, (ms, d, v, sigma, existence, u10) in enumerate(self.RECORDED_237_TRACK_12):
      raw = round((d - BOSCH_A_RANGE_OFFSET_M) / BOSCH_A_RANGE_SCALE_M)
      rr = ri.update(sweep(0, i & 0xF, 0x7, raw, 1024, 1 + 2 * i, ms * 1_000_000, with_aux=True,
                           direct_vrel_raw=864 + round(v * 72), direct_vrel_uncertainty_raw=u10,
                           range_sigma_raw=sigma, existence_raw=existence))
      if i in (4, 5):
        assert len(rr.points) == 1 and rr.points[0].deprecated.measured is False
      if i >= 6:
        assert len(rr.points) == 1, f"real range {d} m rejected at sweep {i}"
        assert rr.points[0].dRel == pytest.approx(raw * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M)

  def test_a_high_u10_birth_coast_does_not_root_the_gate(self):
    # A coast only advances a gate that already exists. A high-u10 birth was never gated or published;
    # rooting the gate on it rejected every later sweep 8 m away, as in the first D-054 replay on
    # 00000239, where the pre-D-054 parser accepted and published them.
    ri = make_radar_interface()
    rr = ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=1023))
    assert len(rr.points) == 0 and len(ri._tracks[1].samples) == 0
    assert ri._tracks[1].range_anchor is None
    for i in range(1, 4):
      rr = ri.update(sweep(0, i, 0x7, 1128 + i, 1024, 1 + 2 * i, i * self.DT_NANOS, with_aux=True,
                           direct_vrel_raw=864, direct_vrel_uncertainty_raw=0, range_sigma_raw=7))
    assert len(rr.points) == 1 and rr.points[0].deprecated.measured is True
    assert rr.points[0].dRel == pytest.approx(1131 * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M)

  def test_lifecycle_break_clears_the_range_anchor(self):
    ri = make_radar_interface()
    self._history_then_coast(ri, coasts=3)
    assert ri._tracks[1].range_anchor is not None
    rr = ri.update(sweep(0, 7, 0x7, 900, 1024, 40, 7 * self.DT_NANOS, with_aux=True,
                         direct_vrel_raw=self.U11_TRUE, direct_vrel_uncertainty_raw=0, range_sigma_raw=7))
    assert len(rr.points) == 0
    assert ri._tracks[1].range_anchor == (pytest.approx(7 * self.DT_NANOS * 1e-9),
                                          pytest.approx(900 * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M))
    assert len(ri._tracks[1].samples) == 1


# --- 7a'. D-055: an invalid slot must not hide an identity that is valid elsewhere ------------

class TestInvalidSlotDoesNotHideAMigratedIdentity:
  """Modelled on 00000237--77313c5a66 track 9 (the followed lead, 21.2 s dark) and 00000232 track 43.
  The radar moved the object to another wire slot in the same sweep its old slot went invalid. The
  invalid-observation hide deleted the point, the D-043 rate check then coasted the valid observation,
  and a coast only updates an existing point, so the lead stayed dark until an accept."""
  DT_NANOS = 70_000_000
  U11 = 864  # 0 m/s

  def _history(self, ri, slot=0):
    for i in range(4):
      rr = ri.update(sweep(slot, i, 0x7, 1000, 1024, 1 + 2 * i, i * self.DT_NANOS, with_aux=True,
                           direct_vrel_raw=self.U11, direct_vrel_uncertainty_raw=0))
    assert len(rr.points) == 1 and rr.points[0].deprecated.measured is True

  def _migrate(self, ri, i, *, u10, invalid_track_id=0xFF, valid_elsewhere=True):
    invalid_old_slot = make_main_frames(0, i, 0xF, 1000, 1024, 1 + 2 * i, track_id=invalid_track_id)
    if valid_elsewhere:
      return ri.update(sweep(1, i, 0x7, 1000, 1024, 1 + 2 * i, i * self.DT_NANOS, with_aux=True,
                             direct_vrel_raw=self.U11, direct_vrel_uncertainty_raw=u10,
                             extra_slots=invalid_old_slot))
    return ri.update(sweep(2, i, 0x7, 1200, 1024, 1, i * self.DT_NANOS, track_id=2,
                           extra_slots=invalid_old_slot))

  @pytest.mark.parametrize("invalid_track_id", [0xFF, 1])
  def test_a_coasted_identity_that_migrated_slots_keeps_its_point(self, invalid_track_id):
    ri = make_radar_interface()
    self._history(ri)
    # u10 above the qualified limit: the valid observation in slot 1 coasts.
    rr = self._migrate(ri, 4, u10=1023, invalid_track_id=invalid_track_id)
    assert [p.trackId for p in rr.points] == [1]
    assert rr.points[0].deprecated.measured is False
    assert rr.points[0].vRel == pytest.approx(0.0)

  def test_an_accepted_identity_that_migrated_slots_still_publishes_measured(self):
    ri = make_radar_interface()
    self._history(ri)
    rr = self._migrate(ri, 4, u10=0)
    assert [p.trackId for p in rr.points] == [1] and rr.points[0].deprecated.measured is True

  def test_negative_control_an_invalid_slot_still_hides_an_identity_seen_nowhere_else(self):
    ri = make_radar_interface()
    self._history(ri)
    rr = self._migrate(ri, 4, u10=0, valid_elsewhere=False)
    assert 1 not in [p.trackId for p in rr.points]
    assert 1 in ri._tracks  # hidden, not retired: the history survives to its stale deadline


# --- 7a''. D-057: a lasting, clean range step re-anchors instead of locking the lead out ----

class TestLastingCleanStepReAnchors:
  """Modelled on 00000236--60bfb34cb1 track 38 at t=1703.6 s and 00000237 track 31 at t=761.5 s. A new
  object's birth ranges were accepted, then its range settled several metres closer and every later
  sweep contradicted both baselines. The lead stayed dark for 27.4 s / 20.4 s while it closed to 38 m /
  20 m, with non-degraded ranges moving at the U11 rate."""
  DT_NANOS = 70_000_000
  CLOSING_RAW = 2.24  # 2 m/s over a 70 ms sweep

  def _drive(self, ri, i, raw, u11_mps, sigma=1, existence=126):
    return ri.update(sweep(0, i & 0xF, 0x7, round(raw), 1024, (1 + 2 * i) & 0xFFF, i * self.DT_NANOS, with_aux=True,
                           direct_vrel_raw=864 + round(u11_mps * 72), direct_vrel_uncertainty_raw=0,
                           range_sigma_raw=sigma, existence_raw=existence))

  def _birth(self, ri):
    raw = 1650  # ~103 m
    for i in range(6):
      raw -= self.CLOSING_RAW
      rr = self._drive(ri, i, raw, -2.0)
    assert rr.points[0].deprecated.measured is True
    return raw

  def test_a_clean_lasting_step_at_the_u11_rate_is_published_again(self):
    ri = make_radar_interface()
    raw = self._birth(ri) - 136  # settles 8.5 m closer
    published_at = None
    for i in range(6, 60):
      raw -= self.CLOSING_RAW
      rr = self._drive(ri, i, raw, -2.0)
      if rr.points and rr.points[0].deprecated.measured and published_at is None:
        published_at = i
        assert rr.points[0].dRel == pytest.approx(round(raw) * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M)
    assert published_at is not None
    assert (published_at - 6) * self.DT_NANOS * 1e-9 >= BOSCH_A_REANCHOR_MIN_SPAN_S
    assert (published_at - 6) * self.DT_NANOS * 1e-9 <= BOSCH_A_REANCHOR_MIN_SPAN_S + 0.15

  # Range sigma 20 is above the D-089 range-scaled limit at ~94 m (0.15 x 94 = 14.1), so it stays degraded.
  @pytest.mark.parametrize("sigma,existence", [(20, 126), (1, 0)])
  def test_negative_control_a_degraded_lasting_step_never_re_anchors(self, sigma, existence):
    ri = make_radar_interface()
    raw = self._birth(ri) - 136
    for i in range(6, 60):
      raw -= self.CLOSING_RAW
      rr = self._drive(ri, i, raw, -2.0, sigma=sigma, existence=existence)
      assert not any(p.deprecated.measured for p in rr.points)

  def test_negative_control_a_clean_step_that_contradicts_u11_never_re_anchors(self):
    ri = make_radar_interface()
    # The range holds still while U11 claims 4 m/s closing. The step is 25 m so the anchor's U11
    # extrapolation cannot catch the range inside this window: a smaller step lets the existing
    # stale-anchor "join" publish (D-054 residual finding), which would mask what D-057 does.
    raw = self._birth(ri) - 400
    for i in range(6, 60):
      rr = self._drive(ri, i, raw, -4.0)
      assert not any(p.deprecated.measured for p in rr.points)


class TestAJoinWaitsForAFreshRateFit:
  """D-059 (STATUS item 22.5). A range-rejection run ends when a later range passes the gate against the stale anchor's
  U11 extrapolation (a "join"). D-043 then fitted the pre-gap samples plus the joined range, so the gap,
  not the object, set the fitted rate. Replayed on 7 routes (232/236/237/239/23a/23b/23e): measured vRel
  in the second after a join over-closed the next-1 s range slope by >3 m/s on 21.0 % of sweeps
  (33.1 % where the run's own slope contradicted U11) against 4.6 % for reference points. Measured vRel resumes only once a fit over the post-join ranges
  alone also agrees."""
  DT_NANOS = 70_000_000
  CLOSING_RAW = 2.24  # 2 m/s over a 70 ms sweep

  def _drive(self, ri, i, raw, u11_mps):
    return ri.update(sweep(0, i & 0xF, 0x7, round(raw), 1024, (1 + 2 * i) & 0xFFF, i * self.DT_NANOS, with_aux=True,
                           direct_vrel_raw=864 + round(u11_mps * 72), direct_vrel_uncertainty_raw=0,
                           range_sigma_raw=1, existence_raw=126))

  def _birth(self, ri):
    raw = 1650
    for i in range(6):
      raw -= self.CLOSING_RAW
      rr = self._drive(ri, i, raw, -2.0)
    assert rr.points[0].deprecated.measured is True
    return raw

  def test_negative_control_a_join_that_contradicts_u11_is_published_unmeasured(self):
    ri = make_radar_interface()
    # The range settles 8.5 m closer and holds still while U11 claims 4 m/s closing. The anchor's U11
    # extrapolation meets it after ~0.84 s. Before 22.5 that join published vRel -4.0 as measured.
    raw = self._birth(ri) - 136
    joined_d_rel = round(raw) * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M
    joined = False
    for i in range(6, 60):
      rr = self._drive(ri, i, raw, -4.0)
      assert not any(p.deprecated.measured for p in rr.points), f"measured at sweep {i}"
      if rr.points and rr.points[0].dRel == pytest.approx(joined_d_rel):
        joined = True
      elif joined:
        pytest.fail(f"joined point deleted at sweep {i}")
    assert joined

  def test_a_walk_that_returns_resumes_measured_after_a_fresh_fit(self):
    ri = make_radar_interface()
    raw = self._birth(ri)
    for i in range(6, 20):  # ~1 s walk 8 m out; the object keeps closing underneath it
      raw -= self.CLOSING_RAW
      rr = self._drive(ri, i, raw + 128, -2.0)
      assert not any(p.deprecated.measured for p in rr.points)  # held, then deleted past BOSCH_A_STALE_S (unchanged)
    measured_at = None
    for i in range(20, 40):
      raw -= self.CLOSING_RAW
      rr = self._drive(ri, i, raw, -2.0)
      assert len(rr.points) == 1, f"point deleted at sweep {i}"
      if rr.points[0].deprecated.measured and measured_at is None:
        measured_at = i
    assert measured_at is not None
    assert (measured_at - 20) * self.DT_NANOS * 1e-9 <= 0.5


class TestRateCheckCoastReRoots:
  """D-062 (STATUS 69/70). Modelled on 00000258--626242f48b track 13 at 43:29-43:46: the lead was opening, then
  braked hard. The D-043 rate check fits `samples`, which only accepted sweeps extend. Coasts had left that
  history stale, so the fit read the old opening rate, the first rejection froze it there, and from then on
  U11 minus the stale average rate grows with the lead's deceleration: every later U11 was rejected while
  the gated range closed from 124 m to 31 m at the U11 rate. The point coasted vRel -0.45 for 12.7 s and
  the planner FCW fired. A lasting, clean run of rate-check coasts whose ranges move at the U11 rate now
  re-roots `samples` on the run, as D-057 does for range rejection."""
  DT = 0.07
  RANGE_M_PER_RAW = 0.0625
  HOLD = 36  # ~2.5 s of high-u10 coast while the lead starts braking: `samples` stay on the opening ranges

  def _drive(self, ri, i, d, u11_mps, uncertainty=0, sigma=1):
    return ri.update(sweep(0, i & 0xF, 0x7, round(d / self.RANGE_M_PER_RAW), 1024, (1 + 2 * i) & 0xFFF,
                           round(i * self.DT * 1e9), with_aux=True,
                           # Clamped to the rails as the radar does: past -12.0 m/s an unclamped raw would go negative
                           # and the 11-bit packer would wrap it to a valid-looking positive U11.
                           direct_vrel_raw=min(max(864 + round(u11_mps * 72), BOSCH_A_DIRECT_VREL_MIN_RAW),
                                               BOSCH_A_DIRECT_VREL_MAX_RAW),
                           direct_vrel_uncertainty_raw=uncertainty, range_sigma_raw=sigma, existence_raw=126))

  def _run(self, ri, vrel, *, sigma=1, u11=None):
    """Opening at +2.8 m/s for 12 sweeps, then vRel(t) from `vrel`; yields (sweep, true vRel, result)."""
    d = 87.0
    for i in range(12):
      d += 2.8 * self.DT
      rr = self._drive(ri, i, d, 2.8)
    assert rr.points[0].deprecated.measured is True
    for i in range(12, 12 + self.HOLD + 60):
      v = vrel((i - 11) * self.DT)
      d += v * self.DT
      u = v if u11 is None else u11
      rr = self._drive(ri, i, d, u, uncertainty=600 if i < 12 + self.HOLD else 0, sigma=sigma)
      yield i, v, rr

  def test_a_lead_braking_after_a_coast_is_measured_again(self):
    ri = make_radar_interface()
    first = 12 + self.HOLD
    measured_at = None
    for i, v, rr in self._run(ri, lambda t: 2.8 - 3.0 * t):
      assert len(rr.points) == 1, f"point deleted at sweep {i}"  # D-041: coast, never delete
      if i >= first and rr.points[0].deprecated.measured and measured_at is None:
        measured_at = i
        assert rr.points[0].vRel == pytest.approx(v, abs=0.1)
    assert measured_at is not None
    assert (measured_at - first) * self.DT <= BOSCH_A_REANCHOR_MIN_SPAN_S + 0.15

  def test_negative_control_u11_over_closing_an_opening_range_stays_coasted(self):
    ri = make_radar_interface()
    for i, _, rr in self._run(ri, lambda t: 2.8, u11=-6.0):
      assert len(rr.points) == 1, f"point deleted at sweep {i}"
      assert rr.points[0].deprecated.measured is False, f"over-closing U11 admitted at sweep {i}"

  def test_negative_control_a_degraded_run_never_re_roots(self):
    ri = make_radar_interface()
    for i, _, rr in self._run(ri, lambda t: 2.8 - 3.0 * t, sigma=7):
      if i >= 12:
        assert not any(p.deprecated.measured for p in rr.points), f"measured at sweep {i}"


# --- 7b. residual vRel-authority fix: raw one-sweep fallback never becomes a published measurement ----
# (U11 genuinely unavailable -- sentinel/out-of-range -- is a different path than the high-u10-but-live
# case above; see radar_interface.py's u11_and_ratio_unavailable branch.)

class TestFallbackNeverPublishes:
  def test_invalid_u11_and_ratio_with_trusted_history_coasts_not_derivative(self):
    """TEST 1: invalid U11 + unusable ratio + a recent trusted velocity on record. A large range jump
    that would imply a big closing velocity via the raw derivative must not reach vRel/measured/KF --
    it should coast the trusted value instead."""
    ri = make_radar_interface()
    # Establish trust: two qualified-U11 samples at a steady range.
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    rr = ri.update(sweep(0, 1, 0x7, 1000, 1024, 3, 50_000_000, with_aux=True,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].deprecated.measured is True
    assert rr.points[0].vRel == pytest.approx(0.0)

    # Third sweep: U11 sentinel (unavailable), ratio invalid, and a range jump that -- via the raw
    # (dRel-previous_range)/dt derivative -- would imply a closing velocity of about -17 m/s over 50ms.
    # Deliberately kept under the 50 m/s generic range-rejection ceiling so this exercises the new
    # u11_and_ratio_unavailable coast path specifically, not the pre-existing range_rejected path.
    rr = ri.update(sweep(0, 2, 0x7, 985, 1024, 5, 100_000_000, with_aux=True,
                         direct_vrel_raw=BOSCH_A_DIRECT_VREL_INVALID, direct_vrel_uncertainty_raw=0,
                         rawca=BOSCH_A_RANGE_RATIO_INVALID))
    implied_fallback_vrel = (985 - 1000) * BOSCH_A_RANGE_SCALE_M / 0.05
    # Confirms the setup would poison if reached, while staying under the generic ceiling so this
    # exercises the coast path and not range_rejected. Stated as bounds, not a magic number, so a
    # range-scale change cannot silently invalidate the scenario.
    assert implied_fallback_vrel < -15.0
    assert abs(implied_fallback_vrel) < BOSCH_A_FALLBACK_RANGE_RATE_MAX_MPS
    assert abs(implied_fallback_vrel) < BOSCH_A_FALLBACK_RANGE_RATE_MAX_MPS  # and clears range_rejected
    assert len(rr.points) == 1
    assert rr.points[0].deprecated.measured is False
    assert rr.points[0].vRel == pytest.approx(0.0)  # coasted trusted value, not the -17 m/s derivative
    # The KF-facing measurement update never happened: last_trusted_vrel/age is untouched by this cycle.
    assert ri._tracks[1].last_trusted_vrel == pytest.approx(0.0)
    assert ri._tracks[1].last_trusted_vrel_nanos == 50_000_000

  def test_invalid_u11_and_ratio_without_trusted_history_withholds_point(self):
    """TEST 2: invalid U11 + unusable ratio + no prior trusted velocity. No fabricated point."""
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=BOSCH_A_DIRECT_VREL_INVALID, direct_vrel_uncertainty_raw=0))
    rr = ri.update(sweep(0, 1, 0x7, 985, 1024, 3, 50_000_000, with_aux=True,
                         direct_vrel_raw=BOSCH_A_DIRECT_VREL_INVALID, direct_vrel_uncertainty_raw=0,
                         rawca=BOSCH_A_RANGE_RATIO_INVALID))
    assert len(rr.points) == 0

  def test_invalid_u11_with_valid_ratio_still_publishes_via_ratio(self):
    """TEST 3: invalid U11 + a valid ratio field. Untouched by this fix -- still publishes via
    ratio_vrel, exactly as before. rawca=520 is chosen so the ratio-implied residual (~1.08m) clears
    innovation checking, so this exercises the ratio_vrel publish path and not range_rejected."""
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=BOSCH_A_DIRECT_VREL_INVALID, direct_vrel_uncertainty_raw=0,
                    rawca=500))
    rr = ri.update(sweep(0, 1, 0x7, 1000, 1024, 3, 50_000_000, with_aux=True,
                         direct_vrel_raw=BOSCH_A_DIRECT_VREL_INVALID, direct_vrel_uncertainty_raw=0,
                         rawca=520))
    d = BOSCH_A_RANGE_SCALE_M * 1000 + BOSCH_A_RANGE_OFFSET_M
    ratio = 0.5 + 0.001 * 520
    residual = abs(d - d * ratio)
    assert residual < 2.0  # clears innovation checking regardless of degraded status
    assert len(rr.points) == 1
    assert rr.points[0].deprecated.measured is True
    expected = d * (1.0 - ratio) / 0.05
    assert rr.points[0].vRel == pytest.approx(expected)


# --- 8. auxiliary tag join: enrichment only, never gates validity -------------------------------------

class TestAuxiliary:
  def test_aux_matching_cycle_is_attached(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True, aux_frame_idx=0, rawc9=123, rawca=456))
    st = ri._slots[0]
    assert st.logical_00c9_raw == 123
    assert st.logical_00ca_raw == 456

  def test_aux_mismatched_cycle_not_attached_and_point_withheld_without_vrel_source(self):
    # A mismatched aux frame_idx means the AUX payload (U11, ratio, and the c9/ca enrichment fields)
    # is never attached for that cycle -- it's as if aux were absent. With no vRel source and no prior
    # trusted velocity, the point is correctly withheld rather than synthesized from the range alone.
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True, aux_frame_idx=5, rawc9=123, rawca=456))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True, aux_frame_idx=5, rawc9=123, rawca=456))
    assert len(rr.points) == 0
    st = ri._slots[0]
    assert math.isnan(st.logical_00c9_raw) and math.isnan(st.logical_00ca_raw)  # never attached

  def test_aux_absent_without_trust_withholds_point(self):
    # Aux absence alone no longer implies "fall back to the raw range derivative" -- with no U11, no
    # ratio, and no prior trusted velocity to coast, the point is withheld rather than fabricated.
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=False))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=False))
    assert len(rr.points) == 0

  def test_aux_absent_does_not_suppress_point_once_trust_is_established(self):
    # A subsequent aux-absent sweep does NOT suppress a point once a recent trusted velocity exists --
    # it coasts that trusted value (measured=False) rather than withholding the point outright.
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    ri.update(sweep(0, 1, 0x7, 1000, 1024, 3, 50_000_000, with_aux=True,
              direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    rr = ri.update(sweep(0, 2, 0x7, 1000, 1024, 5, 100_000_000, with_aux=False))
    assert len(rr.points) == 1
    assert rr.points[0].vRel == pytest.approx(0.0)
    assert rr.points[0].deprecated.measured is False

  def test_aux_param_invalid_sentinel(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True, aux_frame_idx=0, rawc9=1, rawca=0x3FF))
    st = ri._slots[0]
    assert st.logical_00c9_raw == 1
    assert math.isnan(st.logical_00ca_raw)

  def test_sigma_does_not_share_aux_param_invalid_sentinel(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, with_aux=True,
                    aux_frame_idx=0, rawc9=0x3FF, rawca=500))
    st = ri._slots[0]
    assert st.logical_00c9_raw == 0x3FF
    assert st.logical_00ca_raw == 500


# --- 9. missing CAN frame within a cycle does not kill an existing point -------------------------------

def test_incomplete_main_frame_set_leaves_existing_point_untouched():
  ri = make_radar_interface()
  ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
  rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                       direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
  assert len(rr.points) == 1
  t0 = rr.points[0].trackId

  # Next cycle: only f0/f1/f2 arrive for slot 0 (f3 missing) -- still send the trigger so update() runs.
  f0, f1, f2, f3 = BOSCH_A_MAIN_IDS[0]
  _, _, _, trig_f3 = BOSCH_A_MAIN_IDS[15]
  frames = [
    CanData(f0, make_f0(1, 0x7, 1000, 1024), BUS),
    CanData(f1, make_f1(1), BUS),
    CanData(f2, make_f2(1, 3), BUS),
    CanData(trig_f3, make_f3(1), BUS),
  ]
  rr = ri.update([(50_000_000, [_stamp(f) for f in frames])])
  assert len(rr.points) == 1  # untouched, not dropped
  assert rr.points[0].trackId == t0


def test_incoherent_frame_index_across_main_frames_is_skipped():
  ri = make_radar_interface()
  ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
  rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                       direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
  t0 = rr.points[0].trackId

  f0, f1, f2, f3 = BOSCH_A_MAIN_IDS[0]
  _, _, _, trig_f3 = BOSCH_A_MAIN_IDS[15]
  frames = [
    CanData(f0, make_f0(1, 0x7, 1000, 1024), BUS),
    CanData(f1, make_f1(1), BUS),
    CanData(f2, make_f2(2, 3), BUS),  # frame idx mismatch vs f0/f1
    CanData(f3, make_f3(1), BUS),
    CanData(trig_f3, make_f3(1), BUS),
  ]
  rr = ri.update([(50_000_000, [_stamp(f) for f in frames])])
  assert len(rr.points) == 1  # unchanged from before, not re-derived, not dropped
  assert rr.points[0].trackId == t0


# --- 10. staleness gate -----------------------------------------------------------------------------

def test_stale_bus_clears_points_and_flags_temporary_unavailable():
  ri = make_radar_interface()
  ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
  rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, with_aux=True,
                       direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
  assert len(rr.points) == 1

  # Advance the parser clock well past BOSCH_A_STALE_S with no trigger frame at all.
  f0, f1, f2, f3 = BOSCH_A_MAIN_IDS[0]
  frames = [CanData(f0, make_f0(1, 0x7, 1000, 1024), BUS)]  # not a full/coherent set, and no trigger
  rr = ri.update([(300_000_000, [_stamp(f) for f in frames])])  # +300ms, no trigger msg present
  assert rr is not None
  assert len(rr.points) == 0
  assert rr.errors.radarUnavailableTemporary is True


def test_stale_bus_clears_pending_birth_history_before_maturity():
  ri = make_radar_interface()
  rr = ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0))
  assert len(rr.points) == 0
  assert len(ri._tracks[1].samples) == 1

  # The birth has started, but no RadarPoint exists yet. A silent bus must still clear the pending
  # lifecycle/history state rather than allowing it to survive indefinitely.
  f0, _, _, _ = BOSCH_A_MAIN_IDS[0]
  rr = ri.update([(300_000_000, [CanData(f0, make_f0(1, 0x7, 1000, 1024), BUS)])])
  assert rr is not None
  assert len(rr.points) == 0
  assert rr.errors.radarUnavailableTemporary is True
  assert not ri._tracks


# --- 11. persistent CAN identity is separate from wire-slot assembly -------------------------------

class TestPersistentCanIdentity:
  def test_same_slot_same_can_identity_is_stable(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, track_id=42))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, track_id=42, with_aux=True,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert len(rr.points) == 1
    assert rr.points[0].trackId == 42
    assert set(ri._tracks) == {42}

  def test_slot_migration_preserves_identity_and_ols_history(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, track_id=23))
    rr = ri.update(sweep(1, 1, 0x7, 1010, 1024, 3, 50_000_000, track_id=23, with_aux=True,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert len(rr.points) == 1
    assert rr.points[0].trackId == 23
    assert len(ri._tracks[23].samples) == 2
    assert ri._tracks[23].wire_slot == 1

  def test_slot_zero_one_zero_keeps_one_logical_track(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, track_id=17))
    ri.update(sweep(1, 1, 0x7, 1010, 1024, 3, 50_000_000, track_id=17, with_aux=True,
                    direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    rr = ri.update(sweep(0, 2, 0x7, 1020, 1024, 5, 100_000_000, track_id=17, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert len(rr.points) == 1
    assert [point.trackId for point in rr.points] == [17]
    assert len(ri._tracks) == 1
    assert len(ri._tracks[17].samples) == 3
    assert ri._tracks[17].wire_slot == 0

  def test_same_slot_replacement_preserves_returning_identity_history(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, track_id=2))
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000, track_id=2, with_aux=True,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert [point.trackId for point in rr.points] == [2]
    assert len(ri._tracks[2].samples) == 2

    # Bosch can put a different persistent object in the same wire slot while the slot's lifecycle
    # counter continues. The old logical track is not dead: retain its state until its own staleness
    # deadline so it can resume without a synthetic birth or an OLS reset.
    rr = ri.update(sweep(0, 2, 0x7, 1500, 1024, 5, 100_000_000, track_id=63))
    assert set(ri._tracks) == {2, 63}
    assert len(ri._tracks[2].samples) == 2
    assert len(rr.points) == 0
    assert 2 not in ri.pts

    rr = ri.update(sweep(0, 3, 0x7, 1020, 1024, 7, 150_000_000, track_id=2, with_aux=True,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert [point.trackId for point in rr.points] == [2]
    assert len(ri._tracks[2].samples) == 3
    assert math.isfinite(rr.points[0].vRel)

  def test_two_wire_slots_with_different_ids_publish_two_points(self):
    ri = make_radar_interface()
    first_extra = make_main_frames(1, 0, 0x7, 1400, 1024, 11, track_id=22)
    ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, extra_slots=first_extra, track_id=11))
    second_extra = make_main_frames(1, 1, 0x7, 1410, 1024, 13, track_id=22) + [
      CanData(BOSCH_A_AUX_IDS[1], make_aux(1, direct_vrel_raw=864, direct_vrel_uncertainty_raw=0), BUS),
    ]
    rr = ri.update(sweep(0, 1, 0x7, 1010, 1024, 3, 50_000_000,
                         extra_slots=second_extra, track_id=11, with_aux=True,
                         direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert {point.trackId for point in rr.points} == {11, 22}
    assert len(rr.points) == 2

  @pytest.mark.parametrize('track_id', [0, 0xFF, 0x40, 0xA5])
  def test_invalid_can_identity_never_creates_logical_track(self, track_id):
    ri = make_radar_interface()
    rr = ri.update(sweep(0, 0, 0x7, 1000, 1024, 1, 0, track_id=track_id))
    assert len(rr.points) == 0
    assert not ri._tracks

  def test_track_id_reuse_after_death_starts_with_clean_history(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 2000, 1024, 1, 0, track_id=9))
    rr = ri.update(sweep(0, 1, 0x7, 1990, 1024, 3, 50_000_000, track_id=9, with_aux=True,
                        direct_vrel_raw=800, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].trackId == 9
    rr = ri.update(sweep(0, 2, 0xF, 1900, 1024, 5, 100_000_000, track_id=9))
    assert len(rr.points) == 0
    rr = ri.update(sweep(0, 3, 0x7, 1000, 1024, 1, 150_000_000, track_id=9))
    assert len(rr.points) == 0
    rr = ri.update(sweep(0, 4, 0x7, 1000, 1024, 3, 200_000_000, track_id=9, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert len(rr.points) == 1
    assert rr.points[0].trackId == 9
    assert rr.points[0].vRel == 0.0

  def test_lifecycle_discontinuity_keeps_can_id_but_clears_derivative(self):
    ri = make_radar_interface()
    ri.update(sweep(0, 0, 0x7, 2000, 1024, 1, 0, track_id=31))
    rr = ri.update(sweep(0, 1, 0x7, 1990, 1024, 3, 50_000_000, track_id=31, with_aux=True,
                        direct_vrel_raw=800, direct_vrel_uncertainty_raw=0))
    assert rr.points[0].vRel < 0
    rr = ri.update(sweep(0, 2, 0x7, 500, 1024, 99, 100_000_000, track_id=31))
    assert len(rr.points) == 0
    rr = ri.update(sweep(0, 3, 0x7, 500, 1024, 101, 150_000_000, track_id=31, with_aux=True,
                        direct_vrel_raw=864, direct_vrel_uncertainty_raw=0))
    assert len(rr.points) == 1
    assert rr.points[0].trackId == 31
    assert rr.points[0].vRel == 0.0


def test_no_can_data_returns_none_without_crash():
  ri = make_radar_interface()
  assert ri.update([]) is None


def test_bosch_a_azimuth_scale_is_exact_firmware_scale():
  assert BOSCH_A_AZIMUTH_SCALE_RAD == pytest.approx(1.0 / 2048.0)


def test_bosch_a_observed_sweep_end_and_publish_trigger_are_distinct():
  # 0x297 is the observed final object-family frame, but 0x2FF intentionally
  # remains the publish trigger while companion data is optional.
  assert BOSCH_A_TRIGGER_MSG == 0x2FF
  assert BOSCH_A_SWEEP_END_MSG == 0x297


def test_bosch_a_timing_contract():
  assert BOSCH_A_FREQ_HZ == pytest.approx(14.35)
  assert BOSCH_A_STALE_S == pytest.approx(0.20)


# --- misc integration: DBC wiring -------------------------------------------------------------------

def test_honda_bosch_a_radar_dbc_wired_and_available():
  assert CP.radarUnavailable is False
  ri = make_radar_interface()
  assert ri.rcp is not None


def test_civic_bosch_object_feed_uses_camera_side_acc_can():
  ri = make_radar_interface()
  can = CanBus(CP)

  assert ri.rcp.bus == can.camera
  assert ri.rcp.bus != can.radar


# Computed once at collection time, like CP above -- the openpilot_function_fixture in the root
# conftest.py gives every test function its own fresh, isolated OpenpilotPrefix(), so a
# get_non_essential_params() call made from *inside* a test body runs in an environment where the
# BoschARadar=True set at module import time (before any fixture exists) was never written.
# Computing these at module scope, in the same environment CP uses, sidesteps that entirely.

# radarless and CANFD Bosch platforms respectively -- same HONDA_BOSCH family as Civic Bosch, but
# excluded from HONDA_BOSCH_A, so the gate must stay closed regardless of the tester toggle.


# --- D-063: the rail interval, built in (was BoschARailInterval) -------------------------------

def test_rail_interval_gate_reads_rail_as_bound_only_when_asked():
  from opendbc.car.honda.bosch_a_radar import (_bosch_a_range_innovation_rejected, _bosch_a_direct_vrel_interval,
                                                 BOSCH_A_DIRECT_VREL_RAIL_BOUND_MPS)
  low_rail = _bosch_a_direct_vrel_interval(-12.0)[1]
  assert _bosch_a_direct_vrel_interval(low_rail) == (-BOSCH_A_DIRECT_VREL_RAIL_BOUND_MPS, low_rail)
  assert _bosch_a_direct_vrel_interval(low_rail, exact=True) == (low_rail, low_rail)
  assert _bosch_a_direct_vrel_interval(-5.0) == (-5.0, -5.0)
  # A lead closing at 20 m/s while U11 sits on the -12.0 rail: 100 m -> 80 m in 1 s. Exact gate rejects
  # (8.0 m short of the rail's 88 m prediction, past the 5 m hard max); the interval gate accepts (on the
  # rail-to-20 m/s band's edge, residual 0).
  assert _bosch_a_range_innovation_rejected((0.0, 100.0), 1.0, 80.0, low_rail, None, False, exact=True)
  assert not _bosch_a_range_innovation_rejected((0.0, 100.0), 1.0, 80.0, low_rail, None, False, exact=False)
  # Past the physical cap the interval rejects too: 100 m -> 74 m in 1 s is 26 m/s, 6 m outside the band.
  assert _bosch_a_range_innovation_rejected((0.0, 100.0), 1.0, 74.0, low_rail, None, False, exact=False)
  # An unrailed U11 is exact either way.
  assert (_bosch_a_range_innovation_rejected((0.0, 100.0), 1.0, 91.0, -5.0, None, False, exact=False) ==
          _bosch_a_range_innovation_rejected((0.0, 100.0), 1.0, 91.0, -5.0, None, False, exact=True))


def test_rail_interval_and_coast_range_bound_built_in():
  ri = make_radar_interface()
  assert ri.rail_interval is True
  assert ri.coast_range_bound is True


class TestRailIntervalBoundsTheCoast:
  """D-063 addendum (STATUS 92). With BoschARailInterval on, a coast is bounded to within 3 m/s of a fresh
  fit over the coast's own ranges (rejoin_samples / inconsistent_run). Modelled on 0000025e 6:43 track 48:
  the rail interval admitted a range walk, the D-059 hold then coasted the -13.5 rail (1/64 units) for 1.5 s while its
  own post-join fit read +0.8 m/s. Off, the coast is the pre-D-063 verbatim last trusted vRel."""
  DT_NANOS = 70_000_000
  RAIL_RAW = 0  # U11 low rail: true closing >= 12.0 m/s
  RAIL_MPS = -12.0

  # These exercise the D-063 low-rail machinery with raw U11 0, which BOSCH_A_U11_FLOOR_UNMEASURED now takes off that
  # path (the machinery still serves the raw 1728 rail). Run them on the decoder as it was before that change.
  def setup_method(self, method=None):
    self._floor_unmeasured = radar_interface_module.BOSCH_A_U11_FLOOR_UNMEASURED
    radar_interface_module.BOSCH_A_U11_FLOOR_UNMEASURED = False

  def teardown_method(self, method=None):
    radar_interface_module.BOSCH_A_U11_FLOOR_UNMEASURED = self._floor_unmeasured

  def _drive(self, ri, i, d, u11_raw, uncertainty=0):
    return ri.update(sweep(0, i & 0xF, 0x7, round((d - BOSCH_A_RANGE_OFFSET_M) / BOSCH_A_RANGE_SCALE_M), 1024,
                           (1 + 2 * i) & 0xFFF, i * self.DT_NANOS, with_aux=True, direct_vrel_raw=u11_raw,
                           direct_vrel_uncertainty_raw=uncertainty, range_sigma_raw=1, existence_raw=126))

  def _railed_birth_then_walk(self, ri):
    """6 sweeps closing at the rail rate (measured -12.0), then the range steps 8 m closer and holds still
    with U11 still railed. The interval gate joins the settled range ~0.2 s later (rail_admitted), the exact
    gate only after ~0.6 s. Yields (sweep, result) over the 2 s that follow the step."""
    d = 60.0
    for i in range(6):
      d += self.RAIL_MPS * self.DT_NANOS * 1e-9
      rr = self._drive(ri, i, d, self.RAIL_RAW)
    assert rr.points[0].deprecated.measured is True and rr.points[0].vRel == pytest.approx(self.RAIL_MPS, abs=0.05)
    d -= 8.0
    for i in range(6, 6 + 30):
      yield i, self._drive(ri, i, d, self.RAIL_RAW)

  def test_on_a_rejoin_hold_coast_is_bounded_by_the_post_join_fit(self):
    ri = make_radar_interface()
    ri.rail_interval = True
    bounded_at = None
    for i, rr in self._railed_birth_then_walk(ri):
      for p in rr.points:
        if not p.deprecated.measured:
          if bounded_at is None and p.vRel > self.RAIL_MPS + 0.01:
            bounded_at = i
            # The post-join ranges hold still: fit 0 m/s, so the coast may not over-close it by more than 3 m/s.
            assert p.vRel == pytest.approx(-BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS, abs=0.2)
          elif bounded_at is not None:
            assert p.vRel > self.RAIL_MPS + 0.01, f"rail re-coasted at sweep {i}"
    assert bounded_at is not None
    # Bounded within the first second after the step (join ~0.2 s + 4 fresh samples over >= 0.25 s).
    assert (bounded_at - 6) * self.DT_NANOS * 1e-9 <= 1.0

  def test_off_the_same_coast_is_the_verbatim_last_trusted_vrel(self):
    ri = make_radar_interface()
    ri.rail_interval = False  # set explicitly: the toggle test above writes the shared params store under xdist
    ri.coast_range_bound = False
    coasts = 0
    for i, rr in self._railed_birth_then_walk(ri):
      for p in rr.points:
        if not p.deprecated.measured:
          coasts += 1
          assert p.vRel == pytest.approx(self.RAIL_MPS, abs=0.05), f"coast changed at sweep {i} (toggle off)"
    assert coasts > 0

  def test_range_derived_vrel_alone_never_softens_an_over_closing_coast(self):
    """STATUS 111: without the rail interval the bound is one-sided. The same railed coast whose fresh fit reads
    0 m/s keeps the rail: making a coast LESS closing is the rail-interval path's job, not this one's."""
    ri = make_radar_interface()
    ri.rail_interval = False
    ri.coast_range_bound = True
    coasts = 0
    for i, rr in self._railed_birth_then_walk(ri):
      for p in rr.points:
        if not p.deprecated.measured:
          coasts += 1
          assert p.vRel == pytest.approx(self.RAIL_MPS, abs=0.05), f"coast softened at sweep {i}"
    assert coasts > 0

  def _coasts(self, ri):
    return [(i, p.vRel) for i, rr in self._railed_birth_then_walk(ri) for p in rr.points if not p.deprecated.measured]

  def test_a_coast_that_implies_a_reversing_lead_is_bounded_outside_a_rail_hold(self):
    """STATUS 179, route 00000287 2:39: a stale over-closing coast outside any rail hold that says the car ahead
    is reversing. At 10 m/s the -12.0 coast means vLead -2.0. Before a fresh fit exists it is floored at
    vLead = -margin; once the fit (0 m/s here) exists it is pulled to within 3 m/s of it. The first sweeps
    after the 8 m step are range-rejected: that path keeps the whole last point (geometry too) untouched and
    is out of scope here."""
    ri = make_radar_interface()
    ri.rail_interval = False
    ri.coast_range_bound = True
    ri.v_ego = 10.0
    coasts = self._coasts(ri)
    rejected = [(i, v) for i, v in coasts if i < 8]
    coasts = [(i, v) for i, v in coasts if i >= 8]
    assert rejected and all(v == pytest.approx(self.RAIL_MPS, abs=0.05) for _, v in rejected)
    assert coasts
    floor = -(10.0 + BOSCH_A_COAST_REVERSING_MARGIN_MPS)
    assert all(v >= floor - 1e-6 for _, v in coasts)
    assert coasts[0][1] == pytest.approx(floor, abs=0.05)
    assert coasts[-1][1] == pytest.approx(-BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS, abs=0.2)

  @pytest.mark.parametrize("v_ego", [None, 12.0, 20.0], ids=["unknown", "stationary", "slower_car"])
  def test_a_coast_that_does_not_imply_reversing_keeps_the_one_sided_bound(self, v_ego):
    """-12.0 at 12 m/s is a stopped car and at 20 m/s a slower car: STATUS 129's protected
    over-closing coasts. Neither, nor an unknown ego speed, is softened."""
    ri = make_radar_interface()
    ri.rail_interval = False
    ri.coast_range_bound = True
    ri.v_ego = v_ego
    coasts = self._coasts(ri)
    assert coasts and all(v == pytest.approx(self.RAIL_MPS, abs=0.05) for _, v in coasts)

  def test_both_toggles_off_the_reversing_coast_is_verbatim(self):
    ri = make_radar_interface()
    ri.rail_interval = False
    ri.coast_range_bound = False
    ri.v_ego = 10.0
    coasts = self._coasts(ri)
    assert coasts and all(v == pytest.approx(self.RAIL_MPS, abs=0.05) for _, v in coasts)

  def test_on_with_fewer_than_four_fresh_samples_the_coast_is_unchanged(self):
    ri = make_radar_interface()
    ri.rail_interval = True
    first_coast = None
    for i, rr in self._railed_birth_then_walk(ri):
      for p in rr.points:
        if not p.deprecated.measured and first_coast is None:
          first_coast = (i, p.vRel)
    assert first_coast is not None and first_coast[1] == pytest.approx(self.RAIL_MPS, abs=0.05)

  def _stale_opening_then_closing(self, ri):
    """Opening at +2.8 m/s for 12 sweeps, then closing at 5 m/s with U11 agreeing. Yields each coasted point
    until the first measured one."""
    dt = self.DT_NANOS * 1e-9
    d = 87.0
    for i in range(12):
      d += 2.8 * dt
      rr = self._drive(ri, i, d, 864 + round(2.8 * 72))
    assert rr.points[0].deprecated.measured is True
    for i in range(12, 12 + 12):
      d += -5.0 * dt
      rr = self._drive(ri, i, d, 864 + round(-5.0 * 72))
      assert len(rr.points) == 1
      if rr.points[0].deprecated.measured:
        return
      yield i, rr.points[0]

  def test_off_an_inconsistent_coast_holds_the_stale_opening_vrel(self):
    """Both toggles off: the pre-STATUS 92 behaviour, the route 00000268 9:52 shape (+4.06 coasted 2.35 s)."""
    ri = make_radar_interface()
    ri.rail_interval = False
    ri.coast_range_bound = False
    coasts = [p.vRel for _, p in self._stale_opening_then_closing(ri)]
    assert coasts and all(v == pytest.approx(2.8, abs=0.05) for v in coasts)

  @pytest.mark.parametrize("rail_interval, coast_range_bound", [(True, False), (False, True)],
                           ids=["BoschARailInterval", "RangeDerivedVrel"])
  def test_on_an_inconsistent_coast_of_a_stale_opening_vrel_is_pulled_toward_the_closing_fit(
      self, rail_interval, coast_range_bound):
    """D-062 shape: opening at +2.8 m/s, then the lead brakes to 5 m/s closing with U11 agreeing. The stale
    `samples` fit rejects that U11 (one-sided, > 3 m/s below +2.8) and the coast holds +2.8 until D-062
    re-roots after 1.5 s. On, the fresh inconsistent_run fit (-5) bounds the coast to -2 within 5 sweeps.
    Either toggle turns the bound on (STATUS 111 split it out of the rail interval for route 00000268)."""
    ri = make_radar_interface()
    ri.rail_interval = rail_interval
    ri.coast_range_bound = coast_range_bound
    dt = self.DT_NANOS * 1e-9
    d = 87.0
    for i in range(12):
      d += 2.8 * dt
      rr = self._drive(ri, i, d, 864 + round(2.8 * 72))
    assert rr.points[0].deprecated.measured is True
    pulled_at = None
    for i in range(12, 12 + 12):
      d += -5.0 * dt
      rr = self._drive(ri, i, d, 864 + round(-5.0 * 72))
      assert len(rr.points) == 1
      p = rr.points[0]
      if p.deprecated.measured:
        break
      if pulled_at is None and p.vRel < 2.8 - 0.01:
        pulled_at = i
        assert p.vRel == pytest.approx(-5.0 + BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS, abs=0.2)
    assert pulled_at is not None and pulled_at - 12 <= 5


class TestRailIntervalExactOnDegradedSweeps:
  """STATUS 129 (item 92's next step). With BoschARailInterval on, a DEGRADED sweep reads a railed U11 exactly in
  the D-054 gate unless the range itself (a fit over the rejected run plus this sweep) moves at a railed speed.
  Modelled on 0000025e 402.912 s, track 48: a degraded sweep one sweep after its baseline, exact residual 2.32 m
  (over the degraded 2.0 m limit), interval residual 1.93 m, no rejected run to fit. The interval admitted it,
  and the walk it started coasted a stale -13.5 rail (1/64 units) on an opening range."""
  DT_NANOS = 70_000_000
  RAIL_RAW = 0

  # These exercise the D-063 low-rail machinery with raw U11 0, which BOSCH_A_U11_FLOOR_UNMEASURED now takes off that
  # path (the machinery still serves the raw 1728 rail). Run them on the decoder as it was before that change.
  def setup_method(self, method=None):
    self._floor_unmeasured = radar_interface_module.BOSCH_A_U11_FLOOR_UNMEASURED
    radar_interface_module.BOSCH_A_U11_FLOOR_UNMEASURED = False

  def teardown_method(self, method=None):
    radar_interface_module.BOSCH_A_U11_FLOOR_UNMEASURED = self._floor_unmeasured

  def _drive(self, ri, i, d, range_sigma_raw):
    return ri.update(sweep(0, i & 0xF, 0x7, round((d - BOSCH_A_RANGE_OFFSET_M) / BOSCH_A_RANGE_SCALE_M), 1024,
                           (1 + 2 * i) & 0xFFF, i * self.DT_NANOS, with_aux=True, direct_vrel_raw=self.RAIL_RAW,
                           direct_vrel_uncertainty_raw=0, range_sigma_raw=range_sigma_raw, existence_raw=126))

  def _step(self, ri, range_sigma_raw):
    """6 sweeps at the rail rate, then one sweep 2.3 m short of the rail's prediction (exact residual 2.3 m;
    interval residual 2.3 - 6.5 * 0.07 = 1.85 m). Returns (published point, stepped range, previous range)."""
    dt = self.DT_NANOS * 1e-9
    d = 60.0
    for i in range(6):
      d += -12.0 * dt
      rr = self._drive(ri, i, d, 1)
    assert rr.points[0].deprecated.measured is True
    d_prev = rr.points[0].dRel
    d_step = d_prev - 12.0 * dt - 2.3
    rr = self._drive(ri, 6, d_step, range_sigma_raw)
    assert len(rr.points) == 1, "the point must be kept either way (D-041)"
    return rr.points[0], rr.points[0].dRel, d_step, d_prev

  @pytest.mark.parametrize("rail_interval", [False, True], ids=["off", "on"])
  def test_degraded_sweep_is_gated_on_the_exact_rail(self, rail_interval):
    ri = make_radar_interface()
    ri.rail_interval = rail_interval
    ri.coast_range_bound = False
    p, published, d_step, d_prev = self._step(ri, range_sigma_raw=7)
    # Rejected: the point coasts at the last accepted range, and nothing starts a rail-admitted hold.
    assert p.deprecated.measured is False
    assert published == pytest.approx(d_prev, abs=0.07)
    assert ri._tracks[1].rejoin_samples is None

  def test_clean_sweep_still_uses_the_interval(self):
    """The same step on a clean sweep: under the 5 m hard limit, so it passes either way (the exact gate
    has headroom there); the interval never tightens anything."""
    ri = make_radar_interface()
    ri.rail_interval = True
    ri.coast_range_bound = False
    p, published, d_step, _ = self._step(ri, range_sigma_raw=1)
    assert published == pytest.approx(d_step, abs=0.07)

  def test_without_the_degraded_rule_the_interval_admits_it(self, monkeypatch):
    """Negative control: the item 92 behaviour. The degraded step is admitted through the interval."""
    from opendbc.car.honda import bosch_a_radar as HRI
    monkeypatch.setattr(HRI, "BOSCH_A_RAIL_INTERVAL_EXACT_WHEN_DEGRADED", False)
    ri = make_radar_interface()
    ri.rail_interval = True
    ri.coast_range_bound = False
    p, published, d_step, _ = self._step(ri, range_sigma_raw=7)
    assert published == pytest.approx(d_step, abs=0.07)
    assert p.deprecated.measured is False
    assert ri._tracks[1].rejoin_samples is not None


class TestRailIntervalDownSideOnlyInsideARailHold:
  """STATUS 129. With BoschARailInterval on, the coast bound's LESS-closing side acts only inside a hold that a
  rail-interval admission started. Modelled on 00000266 795.4: a stale over-closing coast (-8.2 held while the
  range sat still) on a track the interval never touched. Applied to every coast, the down side softened that
  protected brake; now it gets the one-sided bound, as with RangeDerivedVrel alone."""
  DT_NANOS = 70_000_000

  def _drive(self, ri, i, d, u11_mps):
    return ri.update(sweep(0, i & 0xF, 0x7, round((d - BOSCH_A_RANGE_OFFSET_M) / BOSCH_A_RANGE_SCALE_M), 1024,
                           (1 + 2 * i) & 0xFFF, i * self.DT_NANOS, with_aux=True, direct_vrel_raw=864 + round(u11_mps * 72),
                           direct_vrel_uncertainty_raw=0, range_sigma_raw=1, existence_raw=126))

  def _coasts(self, ri):
    dt = self.DT_NANOS * 1e-9
    d = 70.0
    for i in range(12):
      d += -8.0 * dt
      rr = self._drive(ri, i, d, -8.0)
    assert rr.points[0].deprecated.measured is True
    out = []
    for i in range(12, 24):
      d += -1.0 * dt
      rr = self._drive(ri, i, d, -8.0)
      assert len(rr.points) == 1
      if not rr.points[0].deprecated.measured:
        out.append(rr.points[0].vRel)
    return out

  def test_over_closing_coast_outside_a_rail_hold_is_not_softened(self):
    ri = make_radar_interface()
    ri.rail_interval = True
    ri.coast_range_bound = False
    coasts = self._coasts(ri)
    assert coasts and all(v == pytest.approx(-8.0, abs=0.05) for v in coasts)
    assert ri._tracks[1].rail_hold is False

  def test_negative_control_the_item_92_two_sided_clamp_softens_it(self, monkeypatch):
    from opendbc.car.honda import bosch_a_radar as HRI
    monkeypatch.setattr(HRI, "BOSCH_A_RAIL_INTERVAL_DOWN_SIDE_ONLY_ON_RAIL_HOLD", False)
    ri = make_radar_interface()
    ri.rail_interval = True
    ri.coast_range_bound = False
    coasts = self._coasts(ri)
    assert any(v > -8.0 + 1.0 for v in coasts)


class TestDegradedRailAdmissionNeedsRangeCorroboration:
  """STATUS 129. A degraded, railed track re-acquired after a rejected run: admitted through the interval only when
  the rejected run's own ranges close at a railed speed. Modelled on 0000025e 12:03 track 59 (degraded on every
  sweep, closing ~16-18 m/s: admitted) against 0000025e 6:43 track 48 (a walk 47.9 -> 35.3 m, then the range sat
  still while U11 stayed on the rail: not admitted)."""
  DT_NANOS = 70_000_000

  # These exercise the D-063 low-rail machinery with raw U11 0, which BOSCH_A_U11_FLOOR_UNMEASURED now takes off that
  # path (the machinery still serves the raw 1728 rail). Run them on the decoder as it was before that change.
  def setup_method(self, method=None):
    self._floor_unmeasured = radar_interface_module.BOSCH_A_U11_FLOOR_UNMEASURED
    radar_interface_module.BOSCH_A_U11_FLOOR_UNMEASURED = False

  def teardown_method(self, method=None):
    radar_interface_module.BOSCH_A_U11_FLOOR_UNMEASURED = self._floor_unmeasured

  def _drive(self, ri, i, d, sigma):
    return ri.update(sweep(0, i & 0xF, 0x7, round((d - BOSCH_A_RANGE_OFFSET_M) / BOSCH_A_RANGE_SCALE_M), 1024,
                           (1 + 2 * i) & 0xFFF, i * self.DT_NANOS, with_aux=True, direct_vrel_raw=0,
                           direct_vrel_uncertainty_raw=0, range_sigma_raw=sigma, existence_raw=126))

  def _run(self, step_m, after_step_rate):
    """Measured at the rail rate, then a step of `step_m` closer, then degraded sweeps moving at `after_step_rate`.
    Each geometry is chosen so the exact -12.0 prediction rejects every degraded sweep and the [-20, -12.0]
    interval admits some of them; only the rejected run's own range rate separates the two."""
    ri = make_radar_interface()
    ri.rail_interval = True
    ri.coast_range_bound = False
    dt = self.DT_NANOS * 1e-9
    d = 100.0
    for i in range(6):
      d += -12.0 * dt
      rr = self._drive(ri, i, d, 1)
    assert rr.points[0].deprecated.measured is True
    d -= step_m
    admitted_at, rail_hold_at_admission = None, None
    for i in range(6, 6 + 12):
      d += after_step_rate * dt
      rr = self._drive(ri, i, d, 7)
      # A range-rejected run older than BOSCH_A_STALE_S is withheld with the toggle on or off (the dark lead).
      if admitted_at is None and len(rr.points) == 1 and rr.points[0].dRel == pytest.approx(d, abs=0.07):
        admitted_at, rail_hold_at_admission = i, ri._tracks[1].rail_hold
    return rail_hold_at_admission, admitted_at

  def test_a_range_closing_at_a_railed_speed_is_admitted(self):
    # 12:03-like: a 2.5 m step, then the range keeps closing at 17 m/s (fit on the rail interval)
    rail_hold, admitted_at = self._run(2.5, -17.0)
    assert admitted_at is not None
    assert rail_hold is True

  def test_a_range_that_stopped_closing_is_not_admitted(self):
    # 6:43-like: a 15 m walk, then the range sits still while U11 stays on the rail
    _, admitted_at = self._run(15.0, 0.0)
    assert admitted_at is None

  def test_negative_control_without_the_rule_the_stopped_range_is_admitted(self, monkeypatch):
    from opendbc.car.honda import bosch_a_radar as HRI
    monkeypatch.setattr(HRI, "BOSCH_A_RAIL_INTERVAL_EXACT_WHEN_DEGRADED", False)
    _, admitted_at = self._run(15.0, 0.0)
    assert admitted_at is not None


def test_rail_interval_still_admits_a_degraded_railed_lead_across_a_long_gap():
  """STATUS 129, the case D-063 exists for: 0000025e 12:03 track 59, degraded on every sweep, re-acquired against
  a baseline 2.16 s old (121.3 m) at 79.5 m while U11 sat on the rail. The exact prediction is 95.4 m (92.1 m at the
  1/64 rail); the interval [-20, -12.0] m/s reaches 78.0 m."""
  from opendbc.car.honda.bosch_a_radar import (_bosch_a_range_innovation_rejected, _bosch_a_direct_vrel,
                                                 BOSCH_A_DIRECT_VREL_MIN_RAW)
  low_rail = _bosch_a_direct_vrel(BOSCH_A_DIRECT_VREL_MIN_RAW)
  baseline, now_s, d = (722.069, 121.31), 724.233, 79.50
  assert _bosch_a_range_innovation_rejected(baseline, now_s, d, low_rail, None, True, exact=True)
  assert not _bosch_a_range_innovation_rejected(baseline, now_s, d, low_rail, None, True, exact=False)


def test_degraded_rail_corroboration_fits_the_tail_of_the_run_not_the_finished_walk():
  """STATUS 129, 0000025e 6:43 track 48 at 403.383 s: the rejected run walked 44.8 -> 35.3 m and then sat still.
  The shortest D-043 tail reads the range as stopped (far off the rail interval); an 8-sweep fit averages the walk
  in, reads a railed speed and would have admitted it (negative control)."""
  from opendbc.car.honda.bosch_a_radar import (_bosch_a_trailing_fit_window, _bosch_a_fresh_range_rate,
                                                 _bosch_a_distance_to_interval, _bosch_a_direct_vrel_interval,
                                                 _bosch_a_direct_vrel, BOSCH_A_DIRECT_VREL_MIN_RAW,
                                                 BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS)
  run = [(402.912, 44.75), (402.973, 42.56), (403.044, 39.12), (403.113, 36.12), (403.173, 35.31),
         (403.243, 34.94), (403.313, 35.12), (403.383, 35.31)]
  interval = _bosch_a_direct_vrel_interval(_bosch_a_direct_vrel(BOSCH_A_DIRECT_VREL_MIN_RAW))
  tail = _bosch_a_trailing_fit_window(run)
  assert len(tail) == 5
  assert _bosch_a_distance_to_interval(_bosch_a_fresh_range_rate(tail), interval) > BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS
  assert _bosch_a_distance_to_interval(_bosch_a_fresh_range_rate(run), interval) <= BOSCH_A_VREL_RATE_CHECK_MAX_DISAGREEMENT_MPS


# --- NC-at-rail (stopshadow-radar, replay only) ----------------------------------------------------------


def test_newborn_vrel_is_the_range_fit_bounded_at_stationary():
  """BOSCH_A_NEWBORN_RANGE_PUBLISH: the fit rate, bounded at -vEgo; no live U11, no rail floor."""
  from opendbc.car.honda.bosch_a_radar import _bosch_a_newborn_vrel
  dt = 1.0 / BOSCH_A_FREQ_HZ
  closing = [(i * dt, 116.0 - 20.0 * i * dt) for i in range(6)]
  assert _bosch_a_newborn_vrel(closing, None) == pytest.approx(-20.0)
  assert _bosch_a_newborn_vrel(closing, 25.0) == pytest.approx(-20.0)
  # Faster than a stopped object can close: bounded at -vEgo (well past the U11 low rail).
  assert _bosch_a_newborn_vrel(closing, 19.8) == pytest.approx(-19.8)
  assert _bosch_a_newborn_vrel(closing, 12.0) == pytest.approx(-12.0)
  assert _bosch_a_newborn_vrel(closing[:2], 19.8) is None  # too short to fit
  noisy = [(i * dt, 100.0 + (3.0 if i % 2 else -3.0)) for i in range(6)]
  assert _bosch_a_newborn_vrel(noisy, 19.8) is None  # over BOSCH_A_REANCHOR_MAX_RMS_M


def test_newborn_history_is_not_seeded_by_default():
  # BOSCH_A_NEWBORN_SEED_HISTORY off: 00000284 22:36.7 track 17's seeded burst held a -10.47 coast for 1.3 s.
  from opendbc.car.honda import bosch_a_radar as radar_interface
  assert radar_interface.BOSCH_A_NEWBORN_SEED_HISTORY is False


class TestNewbornLeadsToggle:
  """BOSCH_A_NEWBORN_RANGE_PUBLISH is built in on (the BoschANewbornLeads toggle is removed) and copied into
  RadarInterface.newborn_range_publish. ON: a high-u10 newborn is published on its range fit. OFF (replays/tests):
  withheld exactly as before the newborn publish."""

  @staticmethod
  def _drive_newborn(ri, sweeps=10):
    # A stopped car from 116 m closing at 20 m/s, every sweep with U11 above BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW.
    ri.v_ego = 19.8
    dt_nanos = int(1e9 / BOSCH_A_FREQ_HZ)
    published = []
    for i in range(sweeps):
      d = 116.0 - 20.0 * i / BOSCH_A_FREQ_HZ
      rr = ri.update(sweep(0, i % 16, 0x7, int(round(d * 16)), 1024, 1 + 2 * i, i * dt_nanos, with_aux=True,
                           direct_vrel_raw=600, direct_vrel_uncertainty_raw=BOSCH_A_DIRECT_VREL_MAX_UNCERTAINTY_RAW + 200))
      if rr is not None:
        published.append(list(rr.points))
    return published

  def test_source_default_is_off_on_7_0(self):
    # off in the port: it needs Trung's radard NEWBORN_* exemptions, which 7.0's radard does not have
    from opendbc.car.honda import bosch_a_radar as radar_interface
    assert radar_interface.BOSCH_A_NEWBORN_RANGE_PUBLISH is False
    assert not hasattr(radar_interface, "bosch_a_newborn_leads_enabled")

  def test_off_never_publishes_a_newborn(self, monkeypatch):
    from opendbc.car.honda import bosch_a_radar as radar_interface
    monkeypatch.setattr(radar_interface, "BOSCH_A_NEWBORN_RANGE_PUBLISH", False)
    ri = make_radar_interface()
    assert ri.newborn_range_publish is False
    published = self._drive_newborn(ri)
    assert len(published) == 10
    assert all(len(points) == 0 for points in published)

  def test_on_publishes_the_newborn_on_its_range_fit(self):
    ri = make_radar_interface()
    ri.newborn_range_publish = True
    published = self._drive_newborn(ri)
    assert any(len(points) for points in published)
    first = next(points for points in published if points)
    assert first[0].deprecated.measured is False
    assert first[0].vRel == pytest.approx(-19.8, abs=0.5)  # the range fit (-20), bounded at -vEgo


class TestU11FloorUnmeasured:
  """BOSCH_A_U11_FLOOR_UNMEASURED: raw U11 0 is never a velocity measurement. Static evidence only."""

  @staticmethod
  def _sweep(ri, i, range_raw, u11):
    return ri.update(sweep(0, i & 0xF, 0x7, range_raw, 1024, 1 + 2 * i, i * 70_000_000, with_aux=True,
                           direct_vrel_raw=u11, direct_vrel_uncertainty_raw=90))

  def test_floor_born_track_publishes_the_floor_bound_then_the_range_fit(self):
    ri = make_radar_interface()
    published = []
    for i in range(7):
      rr = self._sweep(ri, i, 1700 - 20 * i, BOSCH_A_DIRECT_VREL_MIN_RAW)
      published.append([(p.vRel, p.deprecated.measured) for p in rr.points])
    # Same maturity as before (a second sample); until the fit exists (4 samples over 0.25 s) the floor is the bound.
    assert published[0] == []
    assert published[1:4] == [[(-12.0, False)]] * 3
    for pts in published[4:]:
      assert len(pts) == 1
      assert pts[0][0] == pytest.approx(-20 * BOSCH_A_RANGE_SCALE_M / 0.07)
      assert pts[0][1] is False
    assert ri._tracks[1].last_trusted_vrel is None

  def test_floor_never_updates_the_trusted_vrel_and_keeps_the_point(self):
    ri = make_radar_interface()
    for i in range(4):
      rr = self._sweep(ri, i, 1000 - 1 * i, 800)
    assert rr.points[0].deprecated.measured
    trusted = ri._tracks[1].last_trusted_vrel
    trusted_nanos = ri._tracks[1].last_trusted_vrel_nanos
    assert trusted == pytest.approx((800 - 864) / 72)
    for i in range(4, 40):
      rr = self._sweep(ri, i, 1000 - 1 * i, BOSCH_A_DIRECT_VREL_MIN_RAW)
      assert len(rr.points) == 1, i
      assert not rr.points[0].deprecated.measured
      assert rr.points[0].vRel != pytest.approx(-12.0)
      assert ri._tracks[1].last_trusted_vrel == trusted
      assert ri._tracks[1].last_trusted_vrel_nanos == trusted_nanos
    # The fit over the floor sweeps' own ranges: -1/16 m per 70 ms.
    assert rr.points[0].vRel == pytest.approx(-BOSCH_A_RANGE_SCALE_M / 0.07)
    # A real U11 afterwards is measured again at once; the floor never entered the accepted history.
    assert len(ri._tracks[1].samples) == 4
    rr = self._sweep(ri, 40, 960, 800)
    assert rr.points[0].deprecated.measured
    assert rr.points[0].vRel == pytest.approx((800 - 864) / 72)

  def test_fit_is_bounded_at_minus_v_ego(self):
    ri = make_radar_interface()
    ri.v_ego = 10.0
    for i in range(7):
      rr = self._sweep(ri, i, 1700 - 20 * i, BOSCH_A_DIRECT_VREL_MIN_RAW)
    assert rr.points[0].vRel == pytest.approx(-10.0)

  def test_one_above_the_floor_is_still_measured(self):
    ri = make_radar_interface()
    for i in range(3):
      rr = self._sweep(ri, i, 1700 - 20 * i, BOSCH_A_DIRECT_VREL_MIN_RAW + 1)
    assert rr.points[0].deprecated.measured
    assert rr.points[0].vRel == pytest.approx(-12.0 + 1 / 72)

  def test_floor_published_point_survives_its_first_accepted_sample(self):
    ri = make_radar_interface()
    for i in range(3):
      rr = self._sweep(ri, i, 1700 - 20 * i, BOSCH_A_DIRECT_VREL_MIN_RAW)
    assert len(rr.points) == 1
    assert len(ri._tracks[1].samples) == 0
    rr = self._sweep(ri, 3, 1640, 0x7FE)  # inactive sentinel: no U11, no ratio -> the coast branch
    assert len(rr.points) == 1
    assert not rr.points[0].deprecated.measured
    assert rr.points[0].vRel == pytest.approx(-12.0)
    rr = self._sweep(ri, 4, 1620, 800)  # the first accepted sample: not a birth, the point stays
    assert len(rr.points) == 1
    assert rr.points[0].deprecated.measured
    assert ri._tracks[1].floor_vrel is None

  @pytest.mark.parametrize('floor_on', [True, False])
  def test_range_step_rejected_floor_era_track_stays_published_unmeasured(self, floor_on):
    """Route 00000008 track 17 (217.05-223.03 s): a floor-era identity whose range then steps away from the gate's
    baseline. The D-054/D-057 gate is unchanged; only its drop outcome is: publish the reported geometry unmeasured."""
    ri = make_radar_interface()
    ri.u11_floor_unmeasured = floor_on
    for i in range(6):
      self._sweep(ri, i, 1600, BOSCH_A_DIRECT_VREL_MIN_RAW)
    for i in range(6, 9):
      rr = self._sweep(ri, i, 1600, 864)
    assert len(rr.points) == 1 and rr.points[0].deprecated.measured
    for i in range(9, 20):
      rr = self._sweep(ri, i, 1760, 864)  # a 10 m step against a U11 of 0: rejected for the whole run
      assert ri._tracks[1].rejected_run, i
      if floor_on:
        assert len(rr.points) == 1, i
        assert not rr.points[0].deprecated.measured
        if i >= 11:  # past BOSCH_A_STALE_S the reported geometry is published, not the stale one
          assert rr.points[0].dRel == pytest.approx(1760 * BOSCH_A_RANGE_SCALE_M + BOSCH_A_RANGE_OFFSET_M)
          assert rr.points[0].vRel == pytest.approx(0.0, abs=1e-6)  # the rejected run's own range fit
    if not floor_on:
      assert len(rr.points) == 0  # the shipped outcome with the switch off: unchanged

  def test_switch_off_restores_the_measured_floor(self):
    ri = make_radar_interface()
    ri.u11_floor_unmeasured = False
    for i in range(7):
      rr = self._sweep(ri, i, 1700 - 20 * i, BOSCH_A_DIRECT_VREL_MIN_RAW)
    assert rr.points[0].vRel == pytest.approx(-12.0)
    assert rr.points[0].deprecated.measured
