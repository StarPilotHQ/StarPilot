from math import isfinite, isnan

import pytest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.toyota.ars510_acc_target import RadarInterface as AccRadarInterface, RADAR_DBC
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.radar_interface import RadarInterface
from opendbc.car.toyota.values import ARS510_RADAR_CAR, CAR, ToyotaFlags


def test_every_radar_acc_car_has_the_continental_radar():
  # same rule as commaai/opendbc#3860: every radar-ACC Toyota (RAV4 2022-25, C-HR, Yaris) carries the ARS510
  assert ARS510_RADAR_CAR == {CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023, CAR.TOYOTA_CHR_TSS2, CAR.TOYOTA_YARIS}


@pytest.mark.parametrize('candidate', list(CAR))
@pytest.mark.parametrize('alpha_long,can_filter', [(False, False), (True, False), (True, True)])
def test_factory_selects_only_live_supported_continental_radar(candidate, alpha_long, can_filter):
  fingerprint = gen_empty_fingerprint()
  if can_filter:
    fingerprint[0][0x2AA] = 8
  cp = CarInterface.get_params(candidate, fingerprint, [], alpha_long, False, False)
  if candidate in ARS510_RADAR_CAR:
    # with or without alpha long's radar disable: the disable leaves the radar bus running
    assert cp.flags & ToyotaFlags.ARS510_RADAR, 'AUTOMATIC_CONTINENTAL_RADAR'
    assert not cp.radarUnavailable
    assert isinstance(RadarInterface(cp).ars510, AccRadarInterface)
  else:
    assert not cp.flags & ToyotaFlags.ARS510_RADAR


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
