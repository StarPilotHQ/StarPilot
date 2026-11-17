from math import isfinite, isnan
import zlib

import pytest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.toyota.ars510_acc_target import RadarInterface as AccRadarInterface, RADAR_DBC
from opendbc.car.toyota.ars510_radar import Ars510Radar, IDLE_SLOT, RadarInterface as FusedRadarInterface
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.radar_interface import RadarInterface
from opendbc.car.toyota.values import ARS510_RADAR_CAR, CAR, ToyotaFlags


@pytest.mark.parametrize('candidate', list(CAR))
@pytest.mark.parametrize('alpha_long,can_filter', [(False, False), (True, False), (True, True)])
def test_factory_selects_only_live_supported_continental_radar(candidate, alpha_long, can_filter):
  fingerprint = gen_empty_fingerprint()
  if can_filter:
    fingerprint[0][0x2AA] = 8
  cp = CarInterface.get_params(candidate, fingerprint, [], alpha_long, False, False)
  if candidate in ARS510_RADAR_CAR and not cp.flags & ToyotaFlags.DISABLE_RADAR:
    assert cp.flags & ToyotaFlags.ARS510_RADAR, 'AUTOMATIC_CONTINENTAL_RADAR'
    assert not cp.flags & ToyotaFlags.ARS510_FUSED and not cp.radarUnavailable
    assert isinstance(RadarInterface(cp).ars510, AccRadarInterface)
  else:
    assert not cp.flags & (ToyotaFlags.ARS510_RADAR | ToyotaFlags.ARS510_FUSED)
    if candidate in ARS510_RADAR_CAR:
      assert cp.radarUnavailable and RadarInterface(cp).ars510 is None


def set_bits(data, start, length, value):
  raw = int.from_bytes(data, "little")
  mask = ((1 << length) - 1) << start
  data[:] = ((raw & ~mask) | (value << start)).to_bytes(len(data), "little")


def object_record(age=60, distance=30, lateral=0, speed_code=645, uncertainty=1, count=1):
  record = bytearray(742)
  record[0] = 0xE4
  for slot in range(20):
    data = bytearray(IDLE_SLOT)
    if slot < count:
      set_bits(data, 24, 7, age)
      set_bits(data, 32, 12, round(160 + 16 * distance))
      set_bits(data, 44, 12, round(2048 + lateral / 0.015))
      set_bits(data, 64, 10, speed_code)
      set_bits(data, 240, 7, uncertainty)
    record[17 + slot * 36 : 17 + (slot + 1) * 36] = data
  record[737:741] = zlib.crc32(record[1:737]).to_bytes(4, "little")
  return bytes(record)


def record_frames(record, bus=1):
  return [(0x80, bytes([0x12 if i == 0 else 0x20 | i % 16]) + record[i * 7 : (i + 1) * 7], bus) for i in range(106)]


def ego_frames(speed=20, yaw_code=512):
  speed_data = bytearray(8)
  speed_data[5:7] = round(speed * 3.6 * 100).to_bytes(2, "big")
  yaw_data = bytes([yaw_code >> 8, yaw_code & 0xFF]) + bytes(6)
  return [(0xB4, bytes(speed_data), 0), (0x24, yaw_data, 0)]


def fused_radar():
  return FusedRadarInterface(structs.CarParams(carFingerprint=CAR.TOYOTA_RAV4_TSS2_2023))


def test_fused_decodes_20_slots_and_preserves_left_positive_geometry():
  radar = fused_radar()
  ret = radar.update([(1_000_000_000, ego_frames() + record_frames(object_record(count=20, lateral=0.3)))])
  assert len(ret.points) == 20
  assert len({pt.trackId for pt in ret.points}) == 20
  for pt in ret.points:
    assert (pt.dRel, pt.yRel, pt.vRel) == pytest.approx((30, 0.3, (645 - 510.5) * 0.149 - 20))
    assert pt.deprecated.measured
    assert isnan(pt.deprecated.aRel) and isnan(pt.deprecated.yvRel)
  assert not ret.errors.canError


@pytest.mark.parametrize("bad_input", ["crc", "short", "truncated", "reordered", "wrong_bus"])
def test_fused_rejects_incomplete_corrupt_or_non_native_records(bad_input):
  radar = fused_radar()
  record = bytearray(object_record())
  if bad_input == "crc":
    record[25] ^= 1
  frames = record_frames(record, bus=2 if bad_input == "wrong_bus" else 1)
  if bad_input == "short":
    frames[50] = (0x80, frames[50][1][:-1], 1)
  elif bad_input == "truncated":
    frames.pop(50)
  elif bad_input == "reordered":
    frames[30], frames[31] = frames[31], frames[30]
  assert radar.update([(1_000_000_000, ego_frames() + frames)]) is None
  assert radar.last_record_t is None


def test_fused_requires_fresh_ego_speed_and_converged_tracks():
  radar = Ars510Radar()
  assert radar.points(1.0, object_record()) == []
  radar.v_ego = (1.0, 20)
  assert radar.points(1.0, object_record(age=59)) == []
  assert radar.points(1.1, object_record(age=60))
  assert radar.points(1.6, object_record(age=61)) == []


def test_fused_lifecycle_age_reset_allocates_a_new_track():
  radar = Ars510Radar()
  radar.v_ego = (1.0, 20)
  first = radar.points(1.0, object_record(age=60))[0][0]
  assert radar.points(1.06, object_record(age=61))[0][0] == first
  radar.points(1.12, object_record(age=1))
  radar.v_ego = (1.18, 20)
  assert radar.points(1.18, object_record(age=60))[0][0] != first
  saturated = radar.track_id(2.0, 0, 126)
  assert radar.track_id(2.06, 0, 126) == saturated


@pytest.mark.parametrize("kwargs", [{"uncertainty": 127}, {"distance": 61}, {"lateral": 3}])
def test_fused_gates_uncertain_far_and_out_of_path_objects(kwargs):
  radar = Ars510Radar()
  radar.v_ego = (1.0, 20)
  radar.yaw = (1.0, 0)
  assert radar.points(1.0, object_record(**kwargs)) == []


def test_fused_damps_a_high_uncertainty_speed_excursion():
  radar = Ars510Radar()
  radar.v_ego = (1.0, 20)
  first = radar.points(1.0, object_record(age=126))[0]
  raw_closing = (600 - 510.5) * 0.149 - 20
  second = radar.points(1.06, object_record(age=126, speed_code=600, uncertainty=127))[0]
  assert raw_closing < -6
  assert second[0] == first[0]
  assert abs(second[3] - first[3]) < 0.2


def test_fused_keeps_healthy_tracks_between_scans_then_reports_actual_silence(monkeypatch):
  radar = fused_radar()
  monkeypatch.setattr(radar.radar, "update", lambda t, *_: [(1, 30, 0, -1)] if round(t * 100) % 6 == 0 else None)
  for i in range(1, 101):
    ret = radar.update([(i * 10_000_000, [(0x80, bytes(8), 1)])])
    if i % 6 == 0:
      assert len(ret.points) == 1
    elif i > 6:
      assert ret is None
  monkeypatch.setattr(radar.radar, "update", lambda *_: None)
  outputs = [radar.update([(2_000_000_000 + i * 10_000_000, [])]) for i in range(10)]
  assert any(r is not None and r.errors.radarUnavailableTemporary and not r.points for r in outputs)
  monkeypatch.setattr(radar.radar, "update", lambda *_: [])
  ret = radar.update([(2_200_000_000, [(0x80, bytes(8), 1)])])
  assert not ret.points and not ret.errors.radarUnavailableTemporary


def acc_frames(packer, counter, cycle, present=True, distance=35, relative_speed=-2, target_id=3):
  return [
    packer.make_can_msg(name, 1, values | {"COUNTER": counter % 16})
    for name, values in (
      ("ACC_TARGET_1", {"STATE": 5 if present else 1, "TARGET_ID": target_id, "REL_SPEED": relative_speed}),
      ("ACC_TARGET_2", {"PRESENT": int(present), "LONG_DIST": distance, "LAT_DIST": 0.25}),
      ("ACC_TARGET_3", {"TIMESTAMP": cycle}),
    )
  ]


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
    (pt,) = result.points
    assert pt.trackId == 3
    assert (pt.dRel, pt.yRel, pt.vRel) == pytest.approx((35, 0.25, -2))
    assert pt.deprecated.measured and isfinite(pt.vRel)
    assert isnan(pt.deprecated.aRel) and isnan(pt.deprecated.yvRel)
  ret = radar.update([(1_600_000_000, acc_frames(packer, 30, 660000, present=False))])
  assert not ret.points and not ret.errors.canError


def test_acc_target_decodes_captured_rav4_hybrid_frame_bank():
  # Stock RAV4 Hybrid 2022 route 2475fb3eb2ffcc2e/2022-04-29--12-46-23, segment 0.
  frames = [(0x235, bytes.fromhex("af4563802b24040e"), 1), (0x237, bytes.fromhex("ff404cc3c10a9550"), 1), (0x239, bytes.fromhex("2a464008061677c8"), 1)]
  ret = AccRadarInterface(structs.CarParams()).update([(1_000_000_000, frames)])
  (pt,) = ret.points
  assert not ret.errors.canError
  assert pt.trackId == 14
  assert (pt.dRel, pt.yRel, pt.vRel) == pytest.approx((3.825, 1.68, 0.125))


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


def test_rav4_startup_wait_does_not_stall_live_tracks():
  CP = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2_2023, gen_empty_fingerprint(), [], False, False, False)
  radar = RadarInterface(CP)
  outputs = [radar.update([(1_000_000_000 + i * 10_000_000, [])]) for i in range(700)]
  assert sum(r is not None for r in outputs) == 140
  assert all(not r.points and not r.errors.radarUnavailableTemporary for r in outputs if r is not None)
