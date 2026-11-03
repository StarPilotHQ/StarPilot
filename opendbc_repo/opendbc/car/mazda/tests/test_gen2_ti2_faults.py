"""Regression tests for Gen2 (TI2) steering-fault propagation in CarState.

Mazda TI2 safety remediation: update_gen2() maps the interceptor's CPU
violation bytes onto the steering-fault fields. These tests pin the required
mappings (they fail against a CarState that hardcodes both fields to False):

  - any CPU_x_VIOL != 0            -> steerFaultPermanent
  - no violation                   -> no steerFaultPermanent
  - a 0x02-only pause (every CPU in RUN, starting from proven health, within
    the 30 s episode cap)          -> steerFaultTemporary, held through panda's
    10-clean-frame recovery window plus a release grace, then cleared
  - a permanent fault clears only after 10 consecutive clean frames

TI2 operating-state (CPU_x_STATE) semantics are established by the Phase 2
capture contract; not-ready/override mappings are covered by Phase 4 tests.
Agreement with panda frame for frame is enforced separately by
opendbc/safety/tests/test_mazda_gen2_ti2_parity.py.
Adapted to Dom's CarState conventions (CP-only constructor, no starpilot params).
"""
import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.mazda.values import CAR, DBC, TI2_HEALTH, MazdaSafetyFlags
from opendbc.car.mazda.carstate import CarState

PLATFORM = CAR.MAZDA_3_2019


def make_car_state():
  CP = structs.CarParams(carFingerprint=PLATFORM, brand="mazda", flags=int(MazdaSafetyFlags.GEN2))
  return CarState(CP), CP


def update_with_ti_feedback(CP, car_state, viol=(0, 0, 0, 0), cpu_state=(0, 0, 0, 0)):
  dbc = DBC[CP.carFingerprint][Bus.pt]
  parsers = {
    Bus.pt: CANParser(dbc, [], 0),
    Bus.body: CANParser(dbc, [("TI_FEEDBACK", 50)], 1),
    Bus.cam: CANParser(dbc, [], 2),
  }
  values = {f"CPU_{i}_VIOL": viol[i] for i in range(4)}
  values.update({f"CPU_{i}_STATE": cpu_state[i] for i in range(4)})
  msg = CANPacker(dbc).make_can_msg("TI_FEEDBACK", 1, values)
  parsers[Bus.body].update([0, [msg]])
  return car_state.update_gen2(parsers)


class TestGen2TI2FaultPropagation:
  @pytest.mark.parametrize("cpu", range(4))
  def test_ti_cpu_violation_sets_permanent_fault(self, cpu):
    car_state, CP = make_car_state()
    viol = [0, 0, 0, 0]
    viol[cpu] = 1
    ret = update_with_ti_feedback(CP, car_state, viol=viol)
    assert ret.steerFaultPermanent, f"CPU_{cpu}_VIOL must map to steerFaultPermanent"

  def test_ti_clean_feedback_has_no_steering_fault(self):
    # passes on unfixed code too; guards the fix against over-reporting faults.
    # steerFaultTemporary is intentionally not asserted: its mapping depends on
    # CPU_x_STATE semantics from the Phase 2 contract and is covered by Phase 4.
    car_state, CP = make_car_state()
    ret = update_with_ti_feedback(CP, car_state)
    assert not ret.steerFaultPermanent


RUN = (3, 3, 3, 3)
CLEAN = (0, 0, 0, 0)
ALL_02 = (2, 2, 2, 2)
FRAME_NS = 10_000_000


class Ti2Feed:
  """A CarState fed through its real parsers, one 100 Hz 0x24B frame per call."""
  def __init__(self):
    self.CP = structs.CarParams(carFingerprint=PLATFORM, brand="mazda", flags=int(MazdaSafetyFlags.GEN2))
    self.car_state = CarState(self.CP)
    self.parsers = CarState.get_can_parsers(self.CP)
    self.packer = CANPacker(DBC[self.CP.carFingerprint][Bus.pt])
    self.t = 1_000_000_000

  def frame(self, viol=CLEAN, states=RUN):
    values = {f"CPU_{i}_VIOL": viol[i] for i in range(4)}
    values.update({f"CPU_{i}_STATE": states[i] for i in range(4)})
    return self.packer.make_can_msg("TI_FEEDBACK", 1, values)

  def step(self, viol=CLEAN, states=RUN, n=1, dt=FRAME_NS):
    ret = None
    for _ in range(n):
      self.t += dt
      for parser in self.parsers.values():
        parser.update([self.t, [self.frame(viol, states)]])
      ret = self.car_state.update_gen2(self.parsers)
    return ret

  def healthy(self):
    return self.step(n=TI2_HEALTH.RECOVERY_FRAMES)


def is_temporary(ret):
  return ret.steerFaultTemporary and not ret.steerFaultPermanent


def is_permanent(ret):
  return ret.steerFaultPermanent and not ret.steerFaultTemporary


class TestGen2TI2Pause:
  @pytest.mark.parametrize("viol", [ALL_02, (0, 2, 0, 2), (2, 0, 0, 0)])
  def test_lone_02_after_proven_health_is_temporary(self, viol):
    feed = Ti2Feed()
    feed.healthy()
    assert is_temporary(feed.step(viol))

  @pytest.mark.parametrize("viol", [(2, 1, 0, 0), (0x13,) * 4, (1, 0, 0, 0)])
  def test_other_codes_are_permanent(self, viol):
    feed = Ti2Feed()
    feed.healthy()
    assert is_permanent(feed.step(viol))

  def test_02_with_a_cpu_not_in_run_is_permanent(self):
    feed = Ti2Feed()
    feed.healthy()
    assert is_permanent(feed.step(ALL_02, (3, 3, 2, 3)))

  def test_restriction_holds_through_recovery_window_and_grace(self):
    feed = Ti2Feed()
    feed.healthy()
    feed.step(ALL_02, n=200)
    # Panda withholds lateral until 10 clean frames: the host must stay restricted.
    for frame in range(1, TI2_HEALTH.RECOVERY_FRAMES + 1):
      assert is_temporary(feed.step()), f"clean frame {frame}"
    # Then for RELEASE_GRACE_NS, so a denial sampled before release is not judged a rejection.
    grace_frames = TI2_HEALTH.RELEASE_GRACE_NS // FRAME_NS
    for frame in range(1, grace_frames):
      assert is_temporary(feed.step()), f"grace frame {frame}"
    ret = feed.step(n=2)
    assert not ret.steerFaultTemporary and not ret.steerFaultPermanent

  def test_recovery_window_counts_frames_not_updates(self):
    feed = Ti2Feed()
    feed.healthy()
    feed.step(ALL_02)
    # Ten clean frames in one parser update complete the recovery, as on panda.
    feed.t += FRAME_NS
    frames = [(feed.t + i, [feed.frame()]) for i in range(TI2_HEALTH.RECOVERY_FRAMES)]
    for parser in feed.parsers.values():
      parser.update(frames)
    feed.car_state.update_gen2(feed.parsers)
    assert feed.car_state.ti2_pause_clean_frames == TI2_HEALTH.RECOVERY_FRAMES
    assert feed.car_state.ti2_release_ns == feed.t + TI2_HEALTH.RECOVERY_FRAMES - 1

  def test_first_frame_is_counted(self):
    feed = Ti2Feed()
    feed.step(n=TI2_HEALTH.RECOVERY_FRAMES)
    assert feed.car_state.ti2_clean_frames == TI2_HEALTH.RECOVERY_FRAMES

  @pytest.mark.parametrize("clean_before", [0, 1, TI2_HEALTH.RECOVERY_FRAMES - 1])
  def test_startup_02_before_proven_health_is_permanent(self, clean_before):
    feed = Ti2Feed()
    if clean_before:
      feed.step(n=clean_before)
    assert is_permanent(feed.step(ALL_02))

  @pytest.mark.parametrize("fault", [(1, 0, 0, 0), (0x13,) * 4])
  def test_fault_then_short_recovery_then_02_is_permanent(self, fault):
    feed = Ti2Feed()
    feed.healthy()
    feed.step(fault)
    assert is_permanent(feed.step())
    assert is_permanent(feed.step(ALL_02))

  def test_permanent_fault_clears_only_after_full_recovery(self):
    feed = Ti2Feed()
    feed.healthy()
    feed.step((1, 0, 0, 0))
    for frame in range(1, TI2_HEALTH.RECOVERY_FRAMES):
      assert is_permanent(feed.step()), f"clean frame {frame}"
    ret = feed.step()
    assert not ret.steerFaultPermanent and not ret.steerFaultTemporary
    assert is_temporary(feed.step(ALL_02))

  def test_non_run_state_requires_proven_health_again(self):
    feed = Ti2Feed()
    feed.healthy()
    ret = feed.step(CLEAN, (2, 2, 2, 2))
    assert not ret.steerFaultPermanent  # reporting unchanged for a state-only condition
    assert is_permanent(feed.step(ALL_02))

  def test_feedback_gap_is_permanent(self):
    feed = Ti2Feed()
    feed.healthy()
    assert is_permanent(feed.step(dt=TI2_HEALTH.FEEDBACK_TIMEOUT_NS + FRAME_NS))
    assert is_permanent(feed.step(ALL_02))

  def test_pause_expires_into_permanent(self):
    feed = Ti2Feed()
    feed.healthy()
    pause_frames = TI2_HEALTH.PAUSE_MAX_NS // FRAME_NS
    assert is_temporary(feed.step(ALL_02, n=pause_frames))
    assert is_permanent(feed.step(ALL_02, n=2))
    assert is_permanent(feed.step(ALL_02))

  def test_flicker_does_not_restart_the_episode_timer(self):
    feed = Ti2Feed()
    feed.healthy()
    feed.step(ALL_02, n=25 * 100)
    feed.step(n=3)
    ret = feed.step(ALL_02, n=6 * 100)
    assert is_permanent(ret)

  def test_new_episode_after_full_recovery_gets_a_fresh_timer(self):
    feed = Ti2Feed()
    feed.healthy()
    feed.step(ALL_02, n=25 * 100)
    feed.step(n=TI2_HEALTH.RECOVERY_FRAMES)
    assert is_temporary(feed.step(ALL_02, n=25 * 100))
