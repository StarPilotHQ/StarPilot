import copy

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.honda.bosch_a_radar import BOSCH_A_FREQ_HZ
from opendbc.car.honda.hondacan import CanBus
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.radar_interface import RadarInterface
from opendbc.car.honda.tests.test_bosch_a_radar import make_f0, sweep
from opendbc.car.honda.values import CAR
from openpilot.cereal import messaging
from openpilot.selfdrive.controls.radard import RadarD


def source_tick(sm, tick, rr=None, speed=0.0, probability=0.0, distance=10.0, standstill=False, lead_speed=None):
  stamp = 1_000_000_000 + tick * 50_000_000
  model_event = messaging.new_message('modelV2', valid=True)
  model_event.logMonoTime = stamp
  model = model_event.modelV2
  model.velocity.x = [speed]
  leads = model.init('leadsV3', 2)
  for lead in leads:
    lead.x = [distance + 1.52]
    lead.y = [0.0]
    lead.v = [speed if lead_speed is None else lead_speed]
    lead.a = [0.0]
    lead.xStd = lead.yStd = lead.vStd = [1.0]
    lead.prob = probability
  car_event = messaging.new_message('carState', valid=True)
  car_event.logMonoTime = stamp
  car_event.carState.vEgo = speed
  car_event.carState.standstill = standstill
  events = [model_event.as_reader(), car_event.as_reader()]
  if rr is not None:
    radar_event = messaging.new_message('radarTracks', valid=True)
    radar_event.logMonoTime = stamp
    radar_event.radarTracks = rr
    events.append(radar_event.as_reader())
  sm.update_msgs(stamp / 1e9, events)


def radar_data(measured=True, relative_speed=0.0, distance=10.0):
  rr = structs.RadarData()
  point = rr.init('points', 1)[0]
  point.trackId = 1
  point.dRel = distance
  point.yRel = 0.0
  point.vRel = relative_speed
  point.deprecated.measured = measured
  return rr


def test_actual_radard_duplicate_and_predicted_point_hold_kalman_measurement():
  sm = messaging.SubMaster(['modelV2', 'carState', 'radarTracks'])
  radar = RadarD(honda_bosch_a_radar=True)
  source_tick(sm, 0, radar_data(), speed=10.0)
  radar.update(sm, sm['radarTracks'])
  track = radar.tracks[1]
  assert track.cnt == 1
  original = copy.deepcopy(track.kf.x)
  source_tick(sm, 1, speed=20.0)
  radar.update(sm, sm['radarTracks'])
  assert track.cnt == 1 and track.kf.x == original and not track.measured
  source_tick(sm, 2, radar_data(False, 30.0), speed=20.0)
  radar.update(sm, sm['radarTracks'])
  assert track.cnt == 1 and track.kf.x == original and not track.measured
  source_tick(sm, 3, radar_data(True, 2.0), speed=20.0)
  radar.update(sm, sm['radarTracks'])
  assert track.cnt == 2 and track.kf.x != original and track.measured
  assert radar.kalman_params.A[0][1] == pytest.approx(1.0 / BOSCH_A_FREQ_HZ)


def test_non_honda_radard_keeps_ordinary_duplicate_measurement_behavior():
  sm = messaging.SubMaster(['modelV2', 'carState', 'radarTracks'])
  radar = RadarD()
  source_tick(sm, 0, radar_data(), speed=10.0)
  radar.update(sm, sm['radarTracks'])
  source_tick(sm, 1, speed=20.0)
  radar.update(sm, sm['radarTracks'])
  assert radar.tracks[1].cnt == 2
  assert radar.kalman_params.A[0][1] == 0.05


@pytest.mark.parametrize('identity', (CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CRV_5G))
def test_actual_decoder_radard_maturity_stale_retirement_and_recovery(identity):
  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
  decoder = RadarInterface(cp)
  radar = RadarD(cp.radarDelay, honda_bosch_a_radar=True)
  sm = messaging.SubMaster(['modelV2', 'carState', 'radarTracks'])
  for index in range(4):
    frames = sweep(CanBus(cp).camera, index, 1_000_000_000 + index * 66_666_667)
    frames[0] = (frames[0][0], [frame._replace(dat=make_f0(index, 7, 227, 1024)) if frame.address == 0x280 else frame for frame in frames[0][1]])
    rr = decoder.update(frames)
    source_tick(sm, index, rr)
    radar.update(sm, sm['radarTracks'])
    if index < 3:
      assert not radar.radar_state.leadOne.present
  assert radar.radar_state.leadOne.present and radar.radar_state.leadOne.radar
  assert radar.radar_state.leadOne.radarTrackId == 1
  stale = decoder.update([(1_500_000_000, [])])
  assert stale.errors.radarUnavailableTemporary and not stale.points
  source_tick(sm, 4, stale)
  radar.update(sm, sm['radarTracks'])
  assert not radar.tracks and not radar.radar_state.leadOne.present
  rr = decoder.update(sweep(CanBus(cp).camera, 5, 1_566_666_667))
  source_tick(sm, 5, rr)
  radar.update(sm, sm['radarTracks'])
  assert not radar.tracks
