"""Exercise the registered HW1 radar factory and Card's serialized publication."""
from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.values import CAR


def hw1(alpha=False, length=8):
  fingerprint = gen_empty_fingerprint()
  if length is not None:
    fingerprint[1][0x301] = length
  return CarInterface.get_params(CAR.TESLA_MODEL_S_HW1, fingerprint, [], alpha, False, False)


@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('length', [None, 0, 1, 3, 7, 8, 9, 64])
def test_hw1_radar_detection_requires_bosch_status_length(alpha, length):
  cp = hw1(alpha, length)
  assert cp.radarUnavailable == (length != 8)
  assert cp.safetyConfigs[0].safetyParam == 16 + int(alpha)
  assert cp.openpilotLongitudinalControl == alpha
  assert cp.pcmCruise
  assert cp.mass > 2100 and cp.wheelbase == pytest.approx(2.96) and cp.steerRatio == 15


class RadarFeed:
  def __init__(self):
    self.packer = CANPacker('tesla_radar_bosch_generated')
    self.stamp = 1_000_000_000

  def frame(self, address, **signals):
    return self.packer.make_can_msg(address, 1, signals)

  def cycle(self):
    frames = [self.frame(769)]
    for i in range(32):
      frames.extend([self.frame(784 + 3 * i, LongDist=50+i, LongSpeed=-2, LatDist=1.5,
                                ProbExist=75, Tracked=1, Meas=1, Index=0),
                     self.frame(785 + 3 * i, Index2=0)])
    return frames

  def send(self, radar, frames):
    self.stamp += 125_000_000
    return radar.update([(self.stamp, frames)])


@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('missing', ['both', 'a', 'b', 'short_a', 'short_b', 'wrong_bus'])
def test_current_cycle_requires_both_current_full_length_halves(alpha, missing):
  radar = CarInterface.RadarInterface(hw1(alpha))
  feed = RadarFeed()
  assert len(feed.send(radar, feed.cycle()).points) == 32
  frames = feed.cycle()
  addresses = {'both': (784, 785), 'a': (784,), 'b': (785,),
               'short_a': (784,), 'short_b': (785,), 'wrong_bus': (784,)}[missing]
  if missing.startswith('short'):
    frames = [(a, d[:-1] if a in addresses else d, b) for a, d, b in frames]
  elif missing == 'wrong_bus':
    frames = [(a, d, 0 if a in addresses else b) for a, d, b in frames]
  else:
    frames = [f for f in frames if f[0] not in addresses]
  result = feed.send(radar, frames)
  assert len(result.points) == 31
  assert all(p.dRel != pytest.approx(50) for p in result.points)
  # Loss removes the track; the next complete observation creates a new identity.
  recovered = feed.send(radar, feed.cycle())
  point = next(p for p in recovered.points if p.dRel == pytest.approx(50))
  assert point.trackId >= 32


def test_trigger_only_does_not_republish_old_obstacles_through_card():
  from openpilot.cereal import log
  from openpilot.selfdrive.car.card import Car

  published = []
  card = Car.__new__(Car)
  card.RI = CarInterface.RadarInterface(hw1())
  card.pm = SimpleNamespace(send=lambda service, msg: published.append((service, msg.to_bytes())))
  feed = RadarFeed()
  frames = feed.cycle()
  for batch, expected in [(frames, 32), ([frames[-1]], 0), (frames, 32)]:
    feed.stamp += 125_000_000
    result = card.radar_update([(feed.stamp, batch)])
    card.radar_publish(result)
    service, raw = published[-1]
    assert service == 'radarTracks'
    with log.Event.from_bytes(raw) as event:
      assert len(event.radarTracks.points) == expected
  assert len(published) == 3


def test_split_cycle_unrelated_traffic_and_faults():
  radar = CarInterface.RadarInterface(hw1())
  feed = RadarFeed()
  frames = feed.cycle()
  # Arrival in separate parser updates is permitted before the cycle trigger.
  assert feed.send(radar, frames[:-1]) is None
  result = feed.send(radar, [frames[-1], (0x123, b'\x00', 0)])
  assert len(result.points) == 32
  frames[0] = feed.frame(769, RADC_HWFail=1)
  assert feed.send(radar, frames).errors.radarFault
  assert not feed.send(radar, feed.cycle()).errors.radarFault


def test_radar_off_factory_produces_no_obstacles():
  radar = CarInterface.RadarInterface(hw1(length=None))
  feed = RadarFeed()
  result = feed.send(radar, feed.cycle())
  assert result is None or not result.points
