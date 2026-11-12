from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.values import CAR, DBC
from openpilot.cereal import messaging
from openpilot.selfdrive.car.card import Car


def factory(car, alpha, release):
  fp = gen_empty_fingerprint()
  fp[1][0x410] = 8
  fp[2][0x489] = 8
  cp = CarInterface.get_params(car, fp, [], alpha, release, False)
  assert not cp.radarUnavailable
  assert not cp.dashcamOnly
  return cp


def bank(packer, slot, *, index=7, companion=7, tracked=True, bus=1):
  messages = [packer.make_can_msg('RadarStatus', bus, {})]
  for point in range(40):
    messages.append(packer.make_can_msg(f'RadarPoint{point}_A', bus, {
      'Index': index, 'Tracked': tracked and point == slot,
      'LongDist': 42.5, 'LatDist': 2., 'LongSpeed': -2.,
    }))
    messages.append(packer.make_can_msg(f'RadarPoint{point}_B', bus, {'Index2': companion}))
  return messages


def publish(result):
  sent = []
  caller = SimpleNamespace(pm=SimpleNamespace(send=lambda service, event: sent.append((service, event.to_bytes()))))
  Car.radar_publish(caller, result)
  assert len(sent) == 1 and sent[0][0] == 'radarTracks'
  event = messaging.log_from_bytes(sent[0][1])
  assert event.valid
  return event.radarTracks


@pytest.mark.parametrize('car', (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('release', (False, True))
@pytest.mark.parametrize('slot', (0, 39))
def test_mismatched_object_pair_retires_published_target_and_recovers(car, alpha, release, slot):
  cp = factory(car, alpha, release)
  original = cp.to_dict()
  radar = CarInterface.RadarInterface(cp)
  packer = CANPacker(DBC[car][Bus.radar])
  result = radar.update([(1_000_000_000, bank(packer, slot))])
  assert result is not None and not result.errors.canError
  first = publish(result)
  assert len(first.points) == 1
  assert (first.points[0].dRel, first.points[0].yRel, first.points[0].vRel) == pytest.approx((42.5, 2., -2.))
  first_id = first.points[0].trackId
  result = radar.update([(1_062_500_000, bank(packer, slot))])
  assert result is not None and publish(result).points[0].trackId == first_id
  result = radar.update([(1_125_000_000, bank(packer, slot, companion=8))])
  assert result is not None and not result.errors.canError
  assert not publish(result).points, 'MISMATCHED_RADAR_PAIR_REPUBLISHES_OLD_TARGET'
  result = radar.update([(1_187_500_000, bank(packer, slot, index=8, companion=8))])
  assert result is not None
  restored = publish(result)
  assert len(restored.points) == 1 and restored.points[0].trackId != first_id
  result = radar.update([(1_250_000_000, bank(packer, slot, tracked=False))])
  assert result is not None and not publish(result).points
  assert cp.to_dict() == original


@pytest.mark.parametrize('car', (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y))
def test_wrong_bus_cannot_create_a_radar_target(car):
  cp = factory(car, False, False)
  radar = CarInterface.RadarInterface(cp)
  packer = CANPacker(DBC[car][Bus.radar])
  assert radar.update([(1_000_000_000, bank(packer, 0, bus=0))]) is None
