"""GEN2 (TI2) steeringPressed: resting hands are not an override; lane-change nudges and torque limits unchanged.

The TI2 torque reading sits at 1000-2900 (p50-p95) with hands resting on the wheel, so the 1400 driver-torque
allowance flagged 38% of active steering as an override. values.py STEER_PRESSED_* moves only the override test.
"""
import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.mazda.carstate import CarState
from opendbc.car.mazda.values import CAR, DBC, CarControllerParams, MazdaSafetyFlags

FRAME_NS = 10_000_000


class Feed:
  def __init__(self, flags=MazdaSafetyFlags.GEN2, platform=CAR.MAZDA_3_2019):
    self.CP = structs.CarParams(carFingerprint=platform, brand="mazda", flags=int(flags))
    self.car_state = CarState(self.CP)
    self.parsers = CarState.get_can_parsers(self.CP)
    self.packer = CANPacker(DBC[self.CP.carFingerprint][Bus.pt])
    self.t = 1_000_000_000

  def step(self, torque, n=1, left=False, right=False):
    ret = None
    for _ in range(n):
      self.t += FRAME_NS
      frames = [self.packer.make_can_msg("TI_FEEDBACK", 1, {"STEER_TORQUE_SENSOR": torque,
                                                           **{f"CPU_{i}_STATE": 3 for i in range(4)}}),
                self.packer.make_can_msg("BLINK_INFO", 0, {"LEFT_BLINK": int(left), "RIGHT_BLINK": int(right)})]
      for parser in self.parsers.values():
        parser.update([self.t, frames])
      ret = self.car_state.update_gen2(self.parsers)
    return ret


def test_parameters():
  params = CarControllerParams(Feed().CP)
  assert params.STEER_PRESSED_THRESHOLD == 3000 and params.STEER_PRESSED_SAMPLES == 10
  assert params.STEER_DRIVER_ALLOWANCE == 1400   # torque limiting, mirrored by panda, is unchanged


@pytest.mark.parametrize("torque", [1500, -2000, 2900, -2999])
def test_resting_hands_are_not_an_override(torque):
  ret = Feed().step(torque, n=20)
  assert ret.steeringTorque == torque
  assert not ret.steeringPressed


@pytest.mark.parametrize("torque", [3001, -3500, 6000])
def test_a_sustained_push_is_an_override(torque):
  assert Feed().step(torque, n=10).steeringPressed


def test_a_firm_push_registers_within_100_ms():
  feed = Feed()
  feed.step(1000, n=20)
  pressed_at = next(i for i in range(1, 11) if feed.step(6000).steeringPressed)
  assert pressed_at <= 5   # mean of the last 10 frames passes 3000 at the 5th 6000 frame


def test_a_single_spike_is_not_an_override():
  feed = Feed()
  feed.step(1000, n=20)
  assert not feed.step(8000).steeringPressed   # (9 * 1000 + 8000) / 10 = 1700


def test_release_clears_once_the_mean_falls():
  feed = Feed()
  assert feed.step(5000, n=10).steeringPressed
  assert not feed.step(500, n=10).steeringPressed


@pytest.mark.parametrize("first", [6000, -6000])
def test_a_reversal_stays_an_override(first):
  # A signed mean would pass through zero here and drop the override for about 5 frames.
  feed = Feed()
  assert feed.step(first, n=10).steeringPressed
  for _ in range(10):
    assert feed.step(-first).steeringPressed


def test_alternating_resting_noise_is_not_an_override():
  feed = Feed()
  for i in range(20):
    assert not feed.step(2500 if i % 2 else -2500).steeringPressed

@pytest.mark.parametrize("side", ["left", "right"])
def test_lane_change_nudge_keeps_the_1400_test(side):
  feed = Feed()
  feed.step(0, n=20)
  torque = 1500 if side == "left" else -1500
  ret = feed.step(torque, left=side == "left", right=side == "right")
  assert ret.steeringPressed and (ret.leftBlinker or ret.rightBlinker)   # raw, no averaging, as before
  assert not feed.step(1300, left=True).steeringPressed


def test_gen3_is_unchanged():
  feed = Feed(flags=MazdaSafetyFlags.GEN3, platform=CAR.MAZDA_CX_50)
  params = CarControllerParams(feed.CP)
  assert params.STEER_PRESSED_THRESHOLD == params.STEER_DRIVER_ALLOWANCE and params.STEER_PRESSED_SAMPLES == 1
  assert feed.step(1500).steeringPressed and not feed.step(1300).steeringPressed
