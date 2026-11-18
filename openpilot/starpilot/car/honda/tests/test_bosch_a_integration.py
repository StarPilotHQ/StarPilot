import copy
import math
import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.honda.bosch_a_radar import BOSCH_A_FREQ_HZ
from opendbc.car.honda.hondacan import CanBus
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.radar_interface import RadarInterface
from opendbc.car.honda.tests.test_bosch_a_radar import sweep, make_f0, make_aux
from opendbc.car.honda.values import CAR
from openpilot.cereal import messaging
from openpilot.selfdrive.controls.radard import RadarD
from openpilot.starpilot.car.honda.tests.test_radar_pipeline import source_tick


def frames(cp, index, distance, raw=864, uncertainty=0, range_sigma=1):
  data = sweep(CanBus(cp).camera, index, 1_000_000_000 + int(index * 1e9 / BOSCH_A_FREQ_HZ))
  offset = -346 / 128 if cp.carFingerprint == CAR.HONDA_CIVIC_BOSCH else -341 / 128
  data[0] = (
    data[0][0],
    [
      f._replace(dat=make_f0(index, 7, round((distance - offset) * 16), 1024, range_sigma_raw=range_sigma))
      if f.address == 0x280
      else f._replace(dat=make_aux(index, rawca=500, direct_vrel_raw=raw, direct_vrel_uncertainty_raw=uncertainty))
      if f.address == 0x2C8
      else f
      for f in data[0][1]
    ],
  )
  return data


@pytest.mark.parametrize('identity', (CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CRV_5G))
def test_decoder_coast_and_floor_keep_geometry_without_kalman_measurements(identity):
  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
  decoder = RadarInterface(cp)
  decoder.v_ego = 20.0
  radar = RadarD(honda_bosch_a_radar=True)
  sm = messaging.SubMaster(['modelV2', 'carState', 'radarTracks'])
  for i in range(5):
    rr = decoder.update(frames(cp, i, 50.0))
    source_tick(sm, i, rr, speed=20, probability=0.99, distance=50)
    radar.update(sm, sm['radarTracks'])
  track = radar.tracks[1]
  state = copy.deepcopy(track.kf.x)
  count = track.cnt
  for i in range(5, 20):
    rr = decoder.update(frames(cp, i, 50.0, raw=0 if i >= 10 else 864, uncertainty=0 if i >= 10 else 600))
    assert len(rr.points) == 1 and not rr.points[0].deprecated.measured
    source_tick(sm, i, rr, speed=20, probability=0.99, distance=50)
    radar.update(sm, sm['radarTracks'])
    assert track.cnt == count and track.kf.x == state and not track.measured
    assert radar.radar_state.leadOne.radar
  rr = decoder.update([(3_000_000_000, [])])
  source_tick(sm, 20, rr, speed=20)
  radar.update(sm, sm['radarTracks'])
  assert not radar.tracks and not radar.radar_state.leadOne.radar


@pytest.mark.parametrize('identity', (CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CRV_5G))
def test_floor_born_stopped_object_uses_closing_range_fit_in_planner_lead(identity):
  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
  decoder = RadarInterface(cp)
  decoder.v_ego = 20.0
  radar = RadarD(honda_bosch_a_radar=True)
  sm = messaging.SubMaster(['modelV2', 'carState', 'radarTracks'])
  for i in range(10):
    distance = 50.0 - 20 * i / BOSCH_A_FREQ_HZ
    rr = decoder.update(frames(cp, i, distance, raw=0))
    source_tick(sm, i, rr, speed=20, probability=0.99, distance=distance, lead_speed=0.0)
    radar.update(sm, sm['radarTracks'])
  point = rr.points[0]
  track = radar.tracks[1]
  assert point.vRel < -19.5 and not point.deprecated.measured
  assert track.cnt == 0 and not track.measured
  assert radar.radar_state.leadOne.radar
  assert radar.radar_state.leadOne.vLeadK < 0.5
  assert track.kf.x[0][0] == 8.0
  from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc

  mpc = LongitudinalMpc()
  mpc.set_weights()
  mpc.set_cur_state(20.0, 0.0)
  mpc.update(radar.radar_state)
  assert mpc.solution_status == 0 and min(mpc.a_solution) < -3.0


def test_clarity_retains_nidec_track_decoding_and_no_bosch_metadata():
  from opendbc.can import CANPacker
  from opendbc.car.can_definitions import CanData

  cp = CarInterface.get_non_essential_params(CAR.HONDA_CLARITY)
  decoder = RadarInterface(cp)
  assert decoder.bosch_a is None and decoder.point_statuses is None
  packer = CANPacker('acura_ilx_2016_nidec')
  messages = [
    packer.make_can_msg('TRACK_0', 1, {'LONG_DIST': 50, 'LAT_DIST': 1.5, 'REL_SPEED': -2, 'NEW_TRACK': 1}),
    packer.make_can_msg('TRACK_15', 1, {'LONG_DIST': 255}),
  ]
  rr = decoder.update([(1_000_000_000, [CanData(*msg) for msg in messages])])
  assert len(rr.points) == 1
  assert rr.points[0].dRel == 50 and rr.points[0].yRel == -1.5 and rr.points[0].vRel == -2


@pytest.mark.parametrize('identity', tuple(CAR))
def test_honda_configuration_decoder_routing_stays_restricted(identity):
  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
  decoder = RadarInterface(cp)
  if identity in (CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CRV_5G):
    assert decoder.bosch_a is not None
  else:
    assert decoder.bosch_a is None
    assert decoder.point_statuses is None


@pytest.mark.parametrize('identity', (CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CRV_5G))
@pytest.mark.parametrize('rejected_range', (False, True))
def test_sustained_rejections_bound_history_without_changing_points(identity, rejected_range, monkeypatch):
  import opendbc.car.honda.bosch_a_radar as decoder_module

  cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
  batches = [
    frames(cp, i, 60.0 if rejected_range and i >= 5 else 50.0, raw=864 if rejected_range or i < 5 else 72, range_sigma=4 if rejected_range else 1)
    for i in range(300)
  ]
  reference = RadarInterface(cp)
  with monkeypatch.context() as unbounded:
    unbounded.setattr(decoder_module, '_bosch_a_trim_run', lambda run: None)
    expected = [reference.update(batch).to_bytes() for batch in batches]
  decoder = RadarInterface(cp)
  for batch, original in zip(batches, expected, strict=True):
    assert decoder.update(batch).to_bytes() == original
  track = decoder.bosch_a._tracks[1]
  run = track.rejected_run if rejected_range else track.inconsistent_run
  original_run = reference.bosch_a._tracks[1].rejected_run if rejected_range else reference.bosch_a._tracks[1].inconsistent_run
  assert len(original_run) > 250
  assert len(run) <= 2 + max(decoder_module.BOSCH_A_REANCHOR_WINDOW, math.ceil(BOSCH_A_FREQ_HZ * decoder_module.BOSCH_A_COAST_FIT_WINDOW_S))
  assert run[0] == original_run[0]
  assert run[-decoder_module.BOSCH_A_REANCHOR_WINDOW :] == original_run[-decoder_module.BOSCH_A_REANCHOR_WINDOW :]
