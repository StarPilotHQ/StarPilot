from math import isfinite, isnan
from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.toyota.ars510_acc_summaries import CHECK, SUMMARY_ID, RadarInterface as SummariesRadarInterface
from opendbc.car.toyota.ars510_acc_target import LAT_CHECK, RadarInterface as AccRadarInterface, RADAR_DBC
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.radar_interface import RadarInterface
from opendbc.car.toyota.values import ARS510_RADAR_CAR, CAR, ToyotaFlags


@pytest.mark.parametrize("candidate", list(CAR))
@pytest.mark.parametrize("mode", [0, 1, 2, 3])
@pytest.mark.parametrize("alpha_long,can_filter", [(False, False), (True, False), (True, True)])
def test_radar_selection_is_opt_in_and_changes_only_compatible_rav4_radar_fields(candidate, mode, alpha_long, can_filter):
  fingerprint = gen_empty_fingerprint()
  if can_filter:
    fingerprint[0][0x2aa] = 8
  baseline = CarInterface.get_params(candidate, fingerprint, [], alpha_long, False, False, None)
  selected = CarInterface.get_params(candidate, fingerprint, [], alpha_long, False, False,
                                     SimpleNamespace(toyota_radar_mode_requested=mode))
  expected = baseline.to_dict()
  if candidate in ARS510_RADAR_CAR and mode in (1, 2):
    expected["flags"] |= ToyotaFlags.ARS510_RADAR
    if mode == 2:
      expected["flags"] |= ToyotaFlags.ARS510_SUMMARIES
    expected["radarUnavailable"] = False
    radar = RadarInterface(selected)
    assert isinstance(radar.ars510, SummariesRadarInterface if mode == 2 else AccRadarInterface)
    assert radar.rcp is radar.pt_cp is None
  assert selected.to_dict() == expected
  assert selected.safetyConfigs[0].to_dict() == baseline.safetyConfigs[0].to_dict()


@pytest.mark.parametrize("candidate", [CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023])
def test_default_rav4_remains_vision_only(candidate):
  CP = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], True, False, False, None)
  radar = RadarInterface(CP)
  assert CP.radarUnavailable
  assert not CP.flags & (ToyotaFlags.ARS510_RADAR | ToyotaFlags.ARS510_SUMMARIES)
  assert radar.ars510 is radar.rcp is None


def with_check_byte(frame):
  """ACC_TARGET_2 with the radar's check byte in byte 0, as every recorded frame has (LAT_DIST not wrapped)."""
  crc = 0xFE
  for i, column in enumerate(LAT_CHECK):
    crc ^= column if frame[1 + i // 8] >> (7 - i % 8) & 1 else 0
  return bytes([crc]) + frame[1:]


def checked(addr, frame):
  """0x235 / 0x237 with the radar's check byte in byte 0 (CRC-8 over the signals, linear; not wrapped)."""
  const, _, cols = CHECK[addr]
  crc = const
  for i, column in enumerate(cols):
    crc ^= column if frame[1 + i // 8] >> (7 - i % 8) & 1 else 0
  return bytes([crc]) + frame[1:]


def summary_cycle(k, acc_m, cars, fault=False):
  """One 60 ms radar cycle: the ACC target (0x235 / 0x237) at acc_m, closing at 4 m/s, and the target summaries
  [(code, range m or None for the ACC car, lateral m), ...] in 0x191 / 0x192 and 0x193 / 0x194."""
  acc_code = round(acc_m / 0.025)
  # status 5, id 7, vRel +4 m/s, REL_ACCEL +1 m/s^2, LAT_SPEED +0.5 m/s
  f235 = checked(0x235, ((1 << 59) | (5 << 48) | (108 << 40) | (1056 << 29) | (1064 << 8) | 7).to_bytes(8, "big"))
  f237 = with_check_byte(((acc_code << 39) | (2000 << 27) | (1 << 10)).to_bytes(8, "big"))
  frames = [(0x235, f235), (0x237, f237), (0x196, bytes([0xFF, int(fault), 0, 0, 0, 0, 0, 0]))]
  for (code, d, y), (info, pos) in zip(cars, ((0x191, 0x192), (0x193, 0x194)), strict=False):
    rc = 160 + (2 * acc_code) // 5 if d is None else round(d * 16 + 160)
    frames += [(info, bytes([182, 0, 0, code << 2, 0, 0, 0, 0])), (pos, rc.to_bytes(2, "big") + round(y / 0.015 + 2048).to_bytes(2, "big"))]
  return [(int(k * 0.06e9 + i * 1e6), [(addr, dat, 1)]) for i, (addr, dat) in enumerate(frames)]


def run_summaries(cycles, cars, corrupt=None, fault=lambda k: False):
  radar, reports = SummariesRadarInterface(structs.CarParams()), []
  for k in range(cycles):
    for packet in summary_cycle(k, 30.0 - 0.24 * k, [(c, d if d is None else d - 0.36 * k, y) for c, d, y in cars], fault(k)):
      if corrupt is not None:
        packet = corrupt(k, packet)
      ret = radar.update([packet])
      if ret is not None and ret.points:
        reports.append(ret)
  return reports


@pytest.mark.parametrize("far_m,n_summary", [(40.0, 1), (60.0, 0), (80.0, 0)])
def test_summaries_publish_the_acc_car_once_plus_summary_cars_within_40_m(far_m, n_summary):
  last = run_summaries(30, [(3, None, 0.0), (9, far_m, 3.6)])[-1]
  acc = [pt for pt in last.points if pt.trackId < SUMMARY_ID]
  summary = [pt for pt in last.points if pt.trackId >= SUMMARY_ID]
  assert len(acc) == 1 and len(summary) == n_summary
  assert acc[0].vRel == pytest.approx(4.0) and acc[0].measured
  assert (acc[0].aRel, acc[0].yvRel) == pytest.approx((1.0, 0.5))  # the radar's REL_ACCEL and LAT_SPEED
  if n_summary:
    assert summary[0].trackId == SUMMARY_ID + 9
    assert summary[0].vRel == pytest.approx(-6.0, abs=0.1)  # 0.36 m per 60 ms cycle
    assert summary[0].measured and isnan(summary[0].aRel) and summary[0].yvRel == pytest.approx(0.0, abs=0.01)  # lateral slope


def test_summaries_ignore_a_one_cycle_all_ones_summary_word():
  def corrupt(k, packet):
    t, ((addr, dat, bus),) = packet
    return (t, [(addr, b"\xff\xff\xff\xff", bus)]) if k == 20 and addr == 0x194 else packet
  speeds = [pt.vRel for r in run_summaries(40, [(3, None, 0.0), (9, 40.0, 3.6)], corrupt) for pt in r.points if pt.trackId >= SUMMARY_ID]
  assert speeds and all(v == pytest.approx(-6.0, abs=0.5) for v in speeds)


def test_summaries_report_unavailable_while_the_radar_fault_flag_is_held():
  radar, reports = SummariesRadarInterface(structs.CarParams()), []
  for k in range(60):  # one set cycle at k = 10 (power-up-like), held from 30 to 44
    for packet in summary_cycle(k, 30.0, [(3, None, 0.0)], fault=k == 10 or 30 <= k < 45):
      ret = radar.update([packet])
      if ret is not None:
        reports.append((k, ret))
  assert not any(r.errors.radarUnavailableTemporary for k, r in reports if k < 30)
  held = [r for k, r in reports if 32 <= k < 45]
  assert held and all(r.errors.radarUnavailableTemporary and not r.points for r in held)
  assert any(r.points and not r.errors.radarUnavailableTemporary for k, r in reports if k >= 46)


def test_summaries_report_silence_without_stale_points():
  radar = SummariesRadarInterface(structs.CarParams())
  for k in range(10):
    for packet in summary_cycle(k, 30.0, [(3, None, 0.0)]):
      radar.update([packet])
  outputs = [radar.update([(int(0.6e9) + i * 10_000_000, [])]) for i in range(100)]
  assert any(r is not None and r.errors.radarUnavailableTemporary and not r.points for r in outputs)


def acc_frames(packer, counter, cycle, present=True, distance=35, relative_speed=-2, target_id=3):
  """ACC_TARGET_1-3 as the radar sends them; ACC_TARGET_2 carries its check byte, as every recorded frame does."""
  return [(a, with_check_byte(d) if a == 0x237 else d, b) for a, d, b in (packer.make_can_msg(name, 1, values | {"COUNTER": counter % 16}) for name, values in (
    ("ACC_TARGET_1", {"STATE": 5 if present else 1, "TARGET_ID": target_id, "REL_SPEED": relative_speed}),
    ("ACC_TARGET_2", {"PRESENT": int(present), "LONG_DIST": distance, "LAT_DIST": 0.25}),
    ("ACC_TARGET_3", {"TIMESTAMP": cycle}),
  ))]


def test_acc_target_decodes_one_lead_once_per_cycle_and_clears_no_target():
  radar = AccRadarInterface(structs.CarParams())
  packer = CANPacker(RADAR_DBC)
  results = []
  for i in range(30):
    results.append(radar.update([(1_000_000_000 + i * 20_000_000, acc_frames(packer, i, 60000 * (1 + i // 3)))]))
  reports = [result for result in results if result is not None]
  assert len(reports) == 10
  for result in reports:
    assert not result.errors.canError
    pt, = result.points
    assert pt.trackId == 3
    assert (pt.dRel, pt.yRel, pt.vRel) == pytest.approx((35, 0.25, -2))
    assert pt.measured and isfinite(pt.vRel)
    assert isnan(pt.aRel) and isnan(pt.yvRel)
  ret = radar.update([(1_600_000_000, acc_frames(packer, 30, 660000, present=False))])
  assert not ret.points and not ret.errors.canError


def test_acc_target_reports_unavailable_while_the_radar_fault_flag_is_held():
  radar, packer, reports = AccRadarInterface(structs.CarParams()), CANPacker(RADAR_DBC), []
  for i in range(90):  # one set cycle at cycle 5 (power-up-like), held over cycles 10-19
    c = i // 3
    frames = acc_frames(packer, i, 60000 * (1 + c)) + [packer.make_can_msg("EVENT_STATE", 1, {"RADAR_FAULT": int(c == 5 or 10 <= c < 20)})]
    ret = radar.update([(1_000_000_000 + i * 20_000_000, frames)])
    if ret is not None:
      reports.append((c, ret))
  assert not any(r.errors.radarUnavailableTemporary for c, r in reports if c < 10)
  held = [r for c, r in reports if 11 <= c < 20]
  assert held and all(r.errors.radarUnavailableTemporary and not r.points for r in held)
  assert all(r.points and not r.errors.radarUnavailableTemporary for c, r in reports if c >= 21)


def test_acc_target_decodes_captured_rav4_hybrid_frame_bank():
  # Stock RAV4 Hybrid 2022 route 2475fb3eb2ffcc2e/2022-04-29--12-46-23, segment 0.
  frames = [(0x235, bytes.fromhex("af4563802b24040e"), 1),
            (0x237, bytes.fromhex("ff404cc3c10a9550"), 1),
            (0x239, bytes.fromhex("2a464008061677c8"), 1)]
  ret = AccRadarInterface(structs.CarParams()).update([(1_000_000_000, frames)])
  pt, = ret.points
  assert not ret.errors.canError
  assert pt.trackId == 14
  assert (pt.dRel, pt.yRel, pt.vRel) == pytest.approx((3.825, 1.68, 0.125))


@pytest.mark.parametrize("radar_cls", [AccRadarInterface, SummariesRadarInterface])
def test_acc_target_unwraps_a_wrapped_lateral_distance(radar_cls):
  # A recorded ACC_TARGET_2 whose check byte carries the hidden 13th lateral bit: LAT_DIST reads -19.70 m, the target is
  # 21.26 m to the left.
  packer = CANPacker(RADAR_DBC)
  wrapped = (0x237, bytes.fromhex("AD026580F2E8C550"), 1)
  present = (0x235, bytes.fromhex("EA056C81CBDFAA1B"), 1)
  radar, reports = radar_cls(structs.CarParams()), []
  for i in range(1, 11):
    clock = packer.make_can_msg("ACC_TARGET_3", 1, {"TIMESTAMP": 60000 * i})
    ret = radar.update([(1_000_000_000 + i * 60_000_000, [present, wrapped, clock])])
    if ret is not None and ret.points:
      reports.append(ret)
  pt, = reports[-1].points
  assert pt.yRel == pytest.approx(-19.70 + 40.96)


@pytest.mark.parametrize("bus,length", [(1, 7), (1, 9), (0, 8), (2, 8), (129, 8)])
def test_acc_target_ignores_malformed_frames_and_non_native_copies(bus, length):
  radar = AccRadarInterface(structs.CarParams())
  frames = [(addr, bytes(length), bus) for addr in (0x235, 0x237, 0x239)]
  assert radar.update([(1_000_000_000, frames)]) is None
  assert radar.last_record_t is None


@pytest.mark.parametrize("silent_clock", [False, True])
def test_acc_target_reports_silence_or_a_stuck_clock_without_stale_points(silent_clock):
  radar = AccRadarInterface(structs.CarParams())
  packer = CANPacker(RADAR_DBC)
  assert radar.update([(1_000_000_000, acc_frames(packer, 0, 60000))]).points
  outputs = []
  for i in range(1, 51):
    frames = acc_frames(packer, i, 60000) if silent_clock else []
    outputs.append(radar.update([(1_000_000_000 + i * 20_000_000, frames)]))
  assert any(r is not None and r.errors.radarUnavailableTemporary and not r.points for r in outputs)


@pytest.mark.parametrize("mode", [1, 2])
def test_rav4_startup_wait_does_not_stall_live_tracks(mode):
  CP = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2_2023, gen_empty_fingerprint(), [], False, False, False,
                               SimpleNamespace(toyota_radar_mode_requested=mode))
  radar = RadarInterface(CP)
  outputs = [radar.update([(1_000_000_000 + i * 10_000_000, [])]) for i in range(700)]
  assert sum(r is not None for r in outputs) == 140
  assert all(not r.points and not r.errors.radarUnavailableTemporary for r in outputs if r is not None)
