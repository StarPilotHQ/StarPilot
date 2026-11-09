import math
from dataclasses import replace

from openpilot.cereal import log
from openpilot.starpilot.lateral.lane_change_preferences import LaneChangePolicy, decode, to_value
from openpilot.starpilot.lateral.lane_change_smoothing import LaneChangeSmoother, limit_rate
from openpilot.selfdrive.controls.lib.drive_helpers import MAX_LATERAL_JERK, clip_curvature
import json


def test_duration_document_roundtrip_and_previous_versions():
  for seconds in (3., 5., 8.):
    policy = replace(LaneChangePolicy(), duration_s=seconds)
    assert decode(json.dumps(to_value(policy)).encode()) == policy
  old = to_value(LaneChangePolicy())
  old['version'] = 3
  old.pop('durationS')
  assert decode(json.dumps(old).encode()) is None  # A v3 document cannot contain a later field.
  old.pop('turnSignalGuidance')
  old.pop('autoRequiresCruise')
  decoded = decode(json.dumps(old).encode())
  assert decoded is not None
  assert decoded.duration_s == LaneChangePolicy().duration_s
  assert decoded.turn_signal_guidance == LaneChangePolicy().turn_signal_guidance
  for value in (2.9, 8.1, True, float('nan')):
    current = to_value(LaneChangePolicy()) | {'durationS': value}
    assert decode(json.dumps(current).encode()) is None


def test_rate_envelope_and_release_preserve_native_acceleration_limits():
  for speed in (1., 10., 30.):
    smoother = LaneChangeSmoother()
    previous = 0.
    factors = []
    for tick in range(600):
      desired = .005 if tick < 200 else -.005
      factor = smoother.factor(active=True, lane_change_state=log.LaneChangeState.laneChangeStarting if tick < 350 else
                               log.LaneChangeState.off, speed=speed, minimum_speed=0., duration=6.,
                               previous=previous, desired=desired)
      limited = limit_rate(speed, previous, desired, factor)
      output, _ = clip_curvature(speed, previous, limited, 0.)
      assert 0. < factor <= 1.
      assert abs(output) <= .2
      assert abs(output - previous) <= 5. / max(speed, 1.) ** 2 * .01 + 1e-12
      previous = output
      factors.append(factor)
    assert factors[0] == math.pi ** 3 * 3.5 / 6. ** 3 * 1.3 / 5.
    assert factors[-1] == 1.
    assert max(factors[200:350]) > factors[0]  # Arrest can recover quicker than turn-in.


def test_stock_duration_inactive_and_regular_driving_remain_exact():
  for active, duration, state in ((True, 3., log.LaneChangeState.laneChangeStarting),
                                 (False, 6., log.LaneChangeState.laneChangeStarting),
                                 (True, 6., log.LaneChangeState.off)):
    smoother = LaneChangeSmoother()
    factor = smoother.factor(active=active, lane_change_state=state, speed=20., minimum_speed=0., duration=duration,
                             previous=0., desired=.01)
    assert factor == 1.
    assert limit_rate(20., 0., .01, factor) == .01


def test_unwind_and_reversal_use_native_limits_during_maneuver_and_release():
  for sign in (-1., 1.):
    for speed in (1., 10., 30.):
      for duration in (3.1, 6., 8.):
        for state in (log.LaneChangeState.laneChangeStarting, log.LaneChangeState.laneChangeFinishing,
                      log.LaneChangeState.off):
          for desired in (sign * .0005, 0., -sign * .01):
            smoother = LaneChangeSmoother()
            smoother.factor(active=True, lane_change_state=log.LaneChangeState.laneChangeStarting,
                            speed=speed, minimum_speed=0., duration=duration, previous=0., desired=sign * .001)
            previous = sign * .001
            factor = smoother.factor(active=True, lane_change_state=state, speed=speed, minimum_speed=0.,
                                     duration=duration, previous=previous, desired=desired)
            assert factor < 1.
            limited = limit_rate(speed, previous, desired, factor)
            assert limited == desired
            for roll in (-.03, 0., .03):
              output, _ = clip_curvature(speed, previous, limited, roll)
              native, _ = clip_curvature(speed, previous, desired, roll)
              assert output == native
              assert abs(output - previous) <= MAX_LATERAL_JERK / max(speed, 1.) ** 2 * .01 + 1e-12


def test_release_does_not_disable_pacing_when_turn_request_builds_again():
  for sign in (-1., 1.):
    smoother = LaneChangeSmoother()
    previous = sign * .001
    for desired in (sign * .002, sign * .0005, sign * .002):
      factor = smoother.factor(active=True, lane_change_state=log.LaneChangeState.laneChangeStarting,
                               speed=30., minimum_speed=0., duration=8., previous=previous, desired=desired)
      limited = limit_rate(30., previous, desired, factor)
      native, _ = clip_curvature(30., previous, desired, 0.)
      output, _ = clip_curvature(30., previous, limited, 0.)
      if abs(desired) < abs(previous):
        assert output == native
      else:
        assert abs(output - previous) < abs(native - previous)
      previous = output


def test_old_turn_clears_without_waiting_for_lane_change_to_finish():
  for sign in (-1., 1.):
    for desired in (0., -sign * .003):
      smoother = LaneChangeSmoother()
      previous = sign * .02
      for _ in range(41):
        factor = smoother.factor(active=True, lane_change_state=log.LaneChangeState.laneChangeStarting,
                                 speed=10., minimum_speed=0., duration=8., previous=previous, desired=desired)
        output, _ = clip_curvature(10., previous, limit_rate(10., previous, desired, factor), 0.)
        native, _ = clip_curvature(10., previous, desired, 0.)
        assert output == native
        previous = output
        if sign * previous <= 0.:
          break
      assert sign * previous <= 0.
      assert smoother.release == 2.


def test_volt_turn_exit_does_not_retain_old_right_request():
  for sign in (-1., 1.):
    smoother = LaneChangeSmoother()
    smoother.factor(active=True, lane_change_state=log.LaneChangeState.laneChangeStarting,
                    speed=7.211, minimum_speed=5 * .44704, duration=LaneChangePolicy().duration_s,
                    previous=sign * .04962, desired=sign * .04386)
    for speed, previous, desired in ((9.895, .028362, .000532), (10.733, .024924, .002056),
                                     (12.172, .019012, -.001425), (13.178, .016756, -.006609)):
      previous, desired = sign * previous, sign * desired
      factor = smoother.factor(active=True, lane_change_state=log.LaneChangeState.laneChangeStarting,
                               speed=speed, minimum_speed=5 * .44704, duration=LaneChangePolicy().duration_s,
                               previous=previous, desired=desired)
      output, _ = clip_curvature(speed, previous, limit_rate(speed, previous, desired, factor), 0.)
      native, _ = clip_curvature(speed, previous, desired, 0.)
      assert output == native
      assert abs(output) < abs(previous)


def test_intersection_exit_keeps_native_rate_after_crossing_lane_change_floor():
  smoother = LaneChangeSmoother()
  def factor(speed, state, assist=True, active=True):
    return smoother.factor(active=active, lane_change_state=state, speed=speed, minimum_speed=0., duration=7.,
                           previous=-.005, desired=0., turn_assist=assist)
  starting = log.LaneChangeState.laneChangeStarting
  assert factor(8., starting) == 1.
  assert factor(10., starting) == 1.
  assert factor(12., log.LaneChangeState.off) == 1.
  assert factor(25., starting) < 1.


def test_intersection_exemption_does_not_survive_disabled_turn_assist_or_lateral():
  for disable in ({"assist": False}, {"active": False}):
    smoother = LaneChangeSmoother()
    def factor(speed, smoother=smoother, **kwargs):
      return smoother.factor(active=kwargs.get("active", True), lane_change_state=log.LaneChangeState.laneChangeStarting,
                             speed=speed, minimum_speed=0., duration=7., previous=-.005, desired=0.,
                             turn_assist=kwargs.get("assist", True))
    assert factor(8.) == 1.
    factor(10., **disable)
    assert factor(25.) < 1.
