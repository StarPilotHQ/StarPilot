from types import SimpleNamespace as NS

import pytest

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.starpilot.car.honda.radar import BoschALeadPolicy, Track


def track(identifier=1, distance=47.5, lateral=-2.0, speed=15.4, acceleration=0.0, count=5):
  value = Track.__new__(Track)
  value.identifier = identifier
  value.cnt = count
  value.dRel = distance
  value.yRel = lateral
  value.vRel = speed - 16.0
  value.vLead = value.vLeadK = speed
  value.aLeadK = acceleration
  value.aLeadTau = FirstOrderFilter(0.6, 0.45, 0.05)
  return value


def model(distance, lateral=2.0, speed=15.4, probability=0.95, y_std=0.6, acceleration=0.0):
  return NS(x=[distance + 1.52], y=[lateral], v=[speed], a=[acceleration],
            xStd=[8.0], yStd=[y_std], vStd=[1.5], prob=probability)


def step(owner, lead, points=None):
  if points is not None:
    owner.tracks = {point.identifier: point for point in points}
  owner.ready = True
  owner.v_ego = 16.0
  sm = {'carState': NS(standstill=False), 'modelV2': NS(leadsV3=[lead, lead])}
  radar = NS(lead_prob_filters=[NS(x=lead.prob), NS(x=lead.prob)])
  return owner.leads(sm, radar, 16.0)[0]


def seeded(point=None):
  point = point or track()
  owner = BoschALeadPolicy()
  lead = step(owner, model(point.dRel, lateral=-point.yRel, speed=point.vLead), [point])
  assert lead['radar'] and lead['radarTrackId'] == point.identifier
  return owner


def test_admitted_relaxed_match_does_not_retire_as_gross_distance_stale():
  owner = seeded()
  for _ in range(10):
    lead = step(owner, model(76.5, y_std=0.8))
    assert lead['radar'] and lead['radarTrackId'] == 1
    assert owner.preferred_gross_distance_stale_counts[0] == 0


def test_recorded_farther_vision_burst_keeps_continuous_moving_radar_range():
  owner = seeded()
  samples = ((73.6558, 3.3165, 0.5975), (78.3091, 3.4838, 0.6100),
             (79.8259, 3.4606, 0.7102), (75.0199, 3.1916, 0.6405), (67.4930, 2.9413, 0.5891))
  for distance, lateral, deviation in samples:
    lead = step(owner, model(distance, lateral=lateral, y_std=deviation))
    assert lead['radar'] and lead['radarTrackId'] == 1
    assert lead['dRel'] == 47.5


def test_persistent_incompatible_farther_target_expires_existing_preference():
  owner = seeded()
  for index in range(4):
    lead = step(owner, model(140.0))
    assert lead['radar'] is (index < 2)
  assert owner.prev_lead_track_ids[0] == -1


def test_strictly_matched_new_radar_target_wins_immediately():
  for distance in (12.0, 80.0):
    owner = seeded()
    challenger = track(2, distance, lateral=0.0, speed=2.0, count=1)
    lead = step(owner, model(distance, lateral=0.0, speed=2.0), [owner.tracks[1], challenger])
    assert lead['radar'] and lead['radarTrackId'] == 2
    assert lead['dRel'] == distance


@pytest.mark.parametrize('distance', (3.0, 8.0, 20.0))
def test_closer_vision_cut_in_is_not_held_behind_preferred_radar(distance):
  owner = seeded()
  lead = step(owner, model(distance, lateral=0.0, speed=0.0, acceleration=-3.0))
  assert not lead['radar'] and lead['dRel'] == pytest.approx(distance)
  assert lead['vLead'] == 0.0 and lead['aLeadK'] < -2.0


@pytest.mark.parametrize('speed,acceleration', ((0.0, 0.0), (15.4, -3.0)))
def test_farther_camera_braking_or_stationary_target_gets_no_added_grace(speed, acceleration):
  owner = seeded()
  lead = step(owner, model(140.0, speed=speed, acceleration=acceleration))
  assert not lead['radar'] and lead['dRel'] == pytest.approx(140.0)


def test_braking_disagreement_keeps_original_gross_mismatch_expiry():
  owner = seeded()
  for index in range(3):
    lead = step(owner, model(76.5, y_std=0.8, acceleration=-3.0))
    assert lead['radar'] is (index < 2)


def test_lateral_cut_out_and_missing_track_release_immediately():
  owner = seeded()
  lead = step(owner, model(100.0, lateral=-2.0))
  assert not lead['radar']
  owner = seeded()
  lead = step(owner, model(100.0), [])
  assert not lead['radar'] and not owner.tracks


def test_stationary_return_does_not_receive_moving_track_grace():
  owner = seeded(track(speed=0.0))
  assert not step(owner, model(140.0, speed=0.0))['radar']


def test_stationary_relaxed_match_keeps_original_gross_distance_expiry():
  owner = seeded(track(distance=50.0, speed=0.0))
  for index in range(3):
    lead = step(owner, model(80.0, speed=0.0))
    assert lead['radar'] is (index < 2)


def test_relaxed_radar_match_does_not_require_unused_vision_acceleration():
  owner = seeded()
  lead = model(76.5, y_std=0.8)
  lead.a = []
  assert step(owner, lead)['radar']


def test_insufficient_maturity_low_probability_and_not_ready_do_not_gain_grace():
  owner = seeded()
  owner.tracks[1].cnt = 2
  assert not step(owner, model(140.0))['radar']
  owner = seeded()
  assert not step(owner, model(140.0, probability=0.1))['present']
  owner = seeded()
  owner.ready = False
  sm = {'carState': NS(standstill=False), 'modelV2': NS(leadsV3=[model(140.0), model(140.0)])}
  radar = NS(lead_prob_filters=[NS(x=0.95), NS(x=0.95)])
  assert not owner.leads(sm, radar, 16.0)[0]['present']


@pytest.mark.parametrize('physical', (False, True))
def test_emergency_input_reaches_actual_native_mpc_without_distance_delay(physical):
  from openpilot.cereal import log
  from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc

  owner = seeded()
  points = [owner.tracks[1], track(2, 8.0, lateral=0.0, speed=0.0, acceleration=-3.0, count=1)] if physical else None
  lead = step(owner, model(8.0, lateral=0.0, speed=0.0, acceleration=-3.0), points)
  assert lead['radar'] is physical and lead['dRel'] == pytest.approx(8.0)
  radar = log.RadarState.new_message()
  radar.leadOne = lead
  radar.leadTwo.present = False
  mpc = LongitudinalMpc()
  mpc.set_weights()
  mpc.set_cur_state(16.0, 0.0)
  mpc.update(radar)
  assert mpc.solution_status == 0
  assert min(mpc.a_solution) < -3.0
