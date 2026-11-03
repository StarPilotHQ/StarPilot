"""Pair selection only; these operands grant no native or application authority."""
from types import SimpleNamespace
import unittest
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
  from openpilot.starpilot.flm.live_tracking import LiveTrackingOptimizer
  from openpilot.starpilot.flm.live_tracking_episodes import TrackingEpisodeBuilder

from openpilot.starpilot.flm.live_tracking_ingress import LiveTrackingIngress


def subject(source_tracking=True):
  owner = object.__new__(LiveTrackingIngress)
  owner.binding = SimpleNamespace(identity='1'*64, source_epoch='2'*64, baseline_revision='3'*64,
                                  controller_revision='revision', native_identity='4'*64, native_required=True)
  owner.episodes = cast("TrackingEpisodeBuilder", SimpleNamespace(source_tracking=source_tracking))
  owner.optimizer = cast("LiveTrackingOptimizer", SimpleNamespace(lag_ns=0))
  owner.last_coordinates = owner.last_profile = None
  return owner


def pair(owner, ns):
  control = SimpleNamespace(**vars(owner.binding), generation=1, live_applied_coordinates=(),
                            effect_trace=None, applied_coordinates=(0., 0.))
  return control, SimpleNamespace(apply_ns=ns)


class TestTrackingPoseInterval(unittest.TestCase):
  def test_same_original_emission_works_for_both_protocols(self):
    for source_tracking in (False, True):
      owner = subject(source_tracking)
      row = pair(owner, 1_000_000_000)
      pose = SimpleNamespace(source_lower_ns=1_005_000_000, source_upper_ns=1_007_000_000)
      selected = owner.original_pose_pair([row], pose)
      assert selected[0] is row[0] and selected[1] is row[1]

  def test_source_tracking_keeps_original_earlier_causal_emission(self):
    owner = subject()
    rows = [pair(owner, 1_000_000_000), pair(owner, 1_010_000_000)]
    pose = SimpleNamespace(source_lower_ns=1_005_000_000, source_upper_ns=1_015_000_000)
    selected = owner.original_pose_pair(rows, pose)
    assert selected[0] is rows[0][0] and selected[1] is rows[0][1]
    assert rows[0][1].apply_ns == 1_000_000_000

  def test_unknown_older_profile_outside_causal_interval_does_not_replace_pair(self):
    owner = subject()
    rows = [pair(owner, stamp) for stamp in (990_000_000, 1_000_000_000, 1_010_000_000)]
    rows[0][0].applied_coordinates = (0.001, 0.)
    pose = SimpleNamespace(source_lower_ns=1_005_000_000, source_upper_ns=1_015_000_000)
    selected = owner.original_pose_pair(rows, pose)
    assert selected is not None
    assert selected[0] is rows[1][0] and selected[1] is rows[1][1]

  def test_unknown_newer_profile_outside_causal_interval_does_not_replace_pair(self):
    owner = subject()
    rows = [pair(owner, stamp) for stamp in (1_000_000_000, 1_010_000_000, 1_020_000_000)]
    rows[-1][0].applied_coordinates = (0.001, 0.)
    pose = SimpleNamespace(source_lower_ns=1_005_000_000, source_upper_ns=1_015_000_000)
    selected = owner.original_pose_pair(rows, pose)
    assert selected is not None
    assert selected[0] is rows[0][0] and selected[1] is rows[0][1]

  def test_plant_model_keeps_single_emission_requirement(self):
    owner = subject(False)
    rows = [pair(owner, 1_000_000_000), pair(owner, 1_010_000_000)]
    pose = SimpleNamespace(source_lower_ns=1_005_000_000, source_upper_ns=1_015_000_000)
    assert owner.original_pose_pair(rows, pose) is None

  def test_source_ramp_keeps_original_causal_pair(self):
    owner = subject()
    rows = [pair(owner, 1_000_000_000), pair(owner, 1_010_000_000)]
    rows[1][0].live_applied_coordinates = (0.001,)+(0.,)*29
    pose = SimpleNamespace(source_lower_ns=1_005_000_000, source_upper_ns=1_015_000_000)
    selected = owner.original_pose_pair(rows, pose)
    assert selected[0] is rows[0][0] and selected[1] is rows[0][1]

  def test_crossing_source_or_generation_change_is_rejected(self):
    for name, value in (('source_epoch', '5'*64), ('generation', 2)):
      owner = subject()
      rows = [pair(owner, 1_000_000_000), pair(owner, 1_010_000_000)]
      setattr(rows[1][0], name, value)
      pose = SimpleNamespace(source_lower_ns=1_005_000_000, source_upper_ns=1_015_000_000)
      assert owner.original_pose_pair(rows, pose) is None

  def test_original_thirty_millisecond_join_limit_is_kept(self):
    owner = subject()
    row = pair(owner, 1_000_000_000)
    pose = SimpleNamespace(source_lower_ns=1_005_000_000, source_upper_ns=1_031_000_000)
    assert owner.original_pose_pair([row], pose) is None

  def test_no_preceding_original_emission_is_not_filled(self):
    owner = subject()
    row = pair(owner, 1_010_000_000)
    pose = SimpleNamespace(source_lower_ns=1_005_000_000, source_upper_ns=1_015_000_000)
    assert owner.original_pose_pair([row], pose) is None

  def test_unknown_consumed_profile_is_not_assumed_neutral(self):
    owner = subject()
    rows = [pair(owner, 1_000_000_000), pair(owner, 1_010_000_000)]
    rows[1][0].applied_coordinates = (0.001, 0.)
    pose = SimpleNamespace(source_lower_ns=1_005_000_000, source_upper_ns=1_015_000_000)
    assert owner.original_pose_pair(rows, pose) is None


class TestSourceWindowProbeDeferral(unittest.TestCase):
  def builder(self, source_tracking):
    from dataclasses import replace
    from openpilot.starpilot.flm.tests.test_live_tracking_episodes import completed_builder
    builder = completed_builder(count=4, error=0.2)
    builder.source_tracking = source_tracking
    builder.defer_unprobed = True
    builder.numerical_observed_ns = 2_000_000_000
    if source_tracking:
      builder.endpoints = type(builder.endpoints)(
        (replace(point, reference_ns=point.control_ns) for point in builder.endpoints), maxlen=320)
    return builder

  def test_short_source_curve_waits_for_original_probe_support(self):
    builder = self.builder(True)
    builder._finish()
    assert len(builder.completed) == 1
    assert not builder.drain(now_ns=2_000_000_001)
    row = next(iter(builder.completed.values()))
    assert row['episode'].kind == 'large'
    assert row['deadline'] == 32_000_000_000

  def test_probe_deadline_is_unchanged_and_missing_support_stays_partial(self):
    builder = self.builder(True)
    builder._finish()
    assert not builder.drain(now_ns=31_999_999_999)
    episode, = builder.drain(now_ns=32_000_000_000)
    assert not episode.native_gradient_complete
    assert episode.sensitivity == ()

  def test_non_tracking_large_curve_keeps_existing_behavior(self):
    builder = self.builder(False)
    builder._finish()
    assert not builder.completed
    episode, = builder.drain(now_ns=2_000_000_001)
    assert episode.kind == 'large'

class TestSourceExperimentEntry(unittest.TestCase):
  def test_one_measured_window_can_select_source_experiment(self):
    from openpilot.starpilot.flm.live_tracking import _discovery_support
    row = SimpleNamespace(error_rms=0.02)
    assert _discovery_support((row,), 'subtle', None) == ((row,), 1)
    assert _discovery_support((), 'subtle', None) == ((), None)
    assert _discovery_support((row,), 'subtle', 0.001) == ((), None)

  def test_source_experiment_still_allocates_three_fresh_baseline_windows(self):
    from openpilot.starpilot.flm.live_tracking import LiveTrackingOptimizer
    # Exercise the real allocation without manufacturing source/native authority.
    function = next(value for name, value in vars(LiveTrackingOptimizer).items() if name == '_comparison_allocation')
    scope = dict(function.__globals__)
    scope['_matches'] = lambda a, b: True
    from types import FunctionType
    allocation = FunctionType(function.__code__, scope)
    owner = SimpleNamespace(rmse=None, binding='binding', accepted=dict.fromkeys(scope['NAMES'], 0.), lag_ns=0)
    owner.planning_history = [SimpleNamespace(binding='binding', profile_sha256='profile', end_ns=n*2_000_000_000) for n in (1,2,3)]
    episode = SimpleNamespace(kind='subtle', profile_sha256='profile', end_ns=6_000_000_000)
    target = dict(owner.accepted)
    target[next(iter(target))] = .005
    assert allocation(owner, episode, .001, target, minimum_episodes=1) == (3,3)
