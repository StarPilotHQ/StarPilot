"""Native model-message fixtures for the standalone curvature contribution."""
from dataclasses import replace
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.cereal import messaging
from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature
from openpilot.starpilot.lateral.lane_centering import (ControlMode, LaneCenteringController, LaneCenteringInput,
                                                       LaneCenteringSettings, MAX_CORRECTION, raw_correction)


XS = [float(i) for i in range(51)]


def model(*, left=-1.5, right=2.1, path=0.0, path_std=0.1, prob=0.9, std=0.1):
  message = messaging.new_message("modelV2")
  m = message.modelV2
  m.init("laneLines", 4)
  for line, y in zip(m.laneLines, (0.0, left, right, 0.0), strict=True):
    line.x = XS
    line.y = [y] * len(XS)
  m.laneLineProbs = [0.0, prob, prob, 0.0]
  m.laneLineStds = [0.0, std, std, 0.0]
  m.position.x = XS
  m.position.y = [path] * len(XS)
  m.position.yStd = [path_std] * len(XS)
  return m


def observation(m=None, **updates):
  source = LaneCenteringInput(m if m is not None else model(), 1, True, 0.0002, 20.0,
                              ControlMode.LATERAL_ONLY, True, LaneCenteringSettings(True, 0.0, 0.0, True), 0.01)
  return replace(source, **updates)


def settled(controller, source, ticks=300):
  result = None
  for _ in range(ticks):
    result = controller.update(source)
  assert result is not None
  return result


class TestLaneCentering(unittest.TestCase):
  def test_lane_width_avoids_first_use_numpy_median_import(self):
    from openpilot.starpilot.lateral.lane_tracking import SingleLineTracker
    tracker = SingleLineTracker()
    with patch.object(np, 'median', side_effect=AssertionError('No lazy masked-array import in Controls')):
      result = tracker.target(model(), 1, 10.0, 0.01, 0.0)
    self.assertFalse(result.blocked)
    assert tracker.width is not None
    self.assertAlmostEqual(tracker.width, 3.6, places=5)

  def test_single_line_spacing_avoids_first_use_numpy_median_import(self):
    from openpilot.starpilot.lateral.lane_tracking import SingleLineTracker
    tracker = SingleLineTracker()
    with patch.object(np, 'median', side_effect=AssertionError('No lazy masked-array import in Controls')):
      for frame in range(8):
        tracker.target(single(line=-1.7, edge=10.), frame, 20., .05, 0.)
    assert tracker.spacing is not None
    self.assertAlmostEqual(tracker.spacing, 1.7, places=5)

  def test_lane_width_rejects_nonfinite_gradient_before_median(self):
    from openpilot.starpilot.lateral.lane_tracking import SingleLineTracker
    for value in (float('nan'), float('inf'), float('-inf')):
      tracker = SingleLineTracker()
      with patch.object(np, 'gradient', return_value=np.full(len(XS), value)):
        result = tracker.target(model(), 1, 10.0, 0.01, 0.0)
      self.assertTrue(result.blocked)
      self.assertIsNone(tracker.width)

  def test_reader_cache_detects_mutation_samples_and_reset(self):
    from openpilot.starpilot.lateral import lane_centering as module
    builder = model()
    reader = builder.as_reader()
    source = observation(reader)
    controller = LaneCenteringController()
    with patch.object(module, '_path_xy', wraps=module._path_xy) as evaluate:
      first = controller.update(source)
      second = controller.update(source)
      self.assertGreater(second.correction, first.correction)
      self.assertEqual(evaluate.call_count, 4)
      builder.laneLines[2].y = [2.5] * len(XS)
      controller.update(source)
      self.assertEqual(evaluate.call_count, 8)
      builder.laneLineProbs = [0., .1, .1, 0.]
      self.assertEqual(controller.update(source).reason, 'unqualified_model')
      self.assertEqual(evaluate.call_count, 12)
      controller.update(replace(source, model_sample_id=0))
      self.assertEqual(evaluate.call_count, 16)
      controller.update(replace(source, model=reader.as_builder().as_reader()))
      self.assertEqual(evaluate.call_count, 20)
      controller.reset()
      controller.update(source)
      self.assertEqual(evaluate.call_count, 24)
      controller.update(replace(source, time_discontinuity=True))
      controller.update(source)
      self.assertEqual(evaluate.call_count, 28)

  def test_explicit_axis_authority(self):
    for mode, allowed in ((ControlMode.OFF, False), (ControlMode.LATERAL_ONLY, True),
                          (ControlMode.LONGITUDINAL_ONLY, False), (ControlMode.COMBINED, True)):
      with self.subTest(mode=mode):
        result = LaneCenteringController().update(observation(mode=mode))
        self.assertEqual(result.correction > 0.0, allowed)
        assert result.candidate_curvature is not None
        self.assertAlmostEqual(result.candidate_curvature, 0.0002 + result.correction)

  def test_held_model_sample_advances_filter_as_in_frozen_controller(self):
    controller = LaneCenteringController()
    source = observation()
    first = controller.update(source).correction
    second = controller.update(source).correction
    raw = raw_correction(source.model, 20.0, 0.0, 0.0)
    assert raw is not None
    expected_target = raw * 0.30 * 1.5
    self.assertAlmostEqual(first, expected_target * (1.0 - math.exp(-0.01 / 0.4)))
    self.assertAlmostEqual(second, expected_target * (1.0 - math.exp(-0.02 / 0.4)))
    self.assertGreater(second, first)

  def test_strength_increases_sublimit_correction_without_changing_default(self):
    source = observation()
    standard, explicit, stronger = LaneCenteringController(), LaneCenteringController(), LaneCenteringController()
    for _ in range(100):
      before = standard.update(source)
      same = explicit.update(replace(source, settings=replace(source.settings, strength=1.5)))
      boosted = stronger.update(replace(source, settings=replace(source.settings, strength=3.0)))
      self.assertEqual(before, same)
      self.assertAlmostEqual(boosted.correction, before.correction * 2, delta=1e-15)
      self.assertLess(abs(boosted.correction), MAX_CORRECTION)

  def test_strength_preserves_absolute_filter_and_axis_bounds(self):
    for strength in (0.5, 1.5, 3.0):
      controller = LaneCenteringController()
      source = observation(model(left=-0.3, right=3.3), settings=LaneCenteringSettings(True, strength=strength))
      previous = 0.0
      for index in range(200):
        source = replace(source, model=model(left=-0.3, right=3.3) if index < 100 else model(left=-3.3, right=0.3))
        correction = controller.update(source).correction
        self.assertLessEqual(abs(correction), MAX_CORRECTION)
        self.assertLessEqual(abs(correction - previous), 2 * MAX_CORRECTION * (1 - math.exp(-0.01 / 0.4)) + 1e-15)
        previous = correction
      self.assertEqual(controller.update(replace(source, lateral_active=False)).correction, 0)
      self.assertEqual(controller.update(replace(source, time_discontinuity=True)).correction, 0)
    for invalid in (True, float("nan"), 0.49, 3.001):
      self.assertEqual(LaneCenteringController().update(replace(observation(), settings=LaneCenteringSettings(True, strength=invalid))).reason,
                       "invalid_input")

  def test_stronger_centering_remains_bounded_with_lane_uncertainty_and_sign_changes(self):
    for speed in (5., 35.):
      controller = LaneCenteringController()
      previous = 0.
      for index in range(120):
        dt = (.01, .02, .05)[index % 3]
        displacement = (.079, .081, .159, .161)[index % 4] * (1 if index < 60 else -1)
        source = observation(model(left=-1.8 + displacement, right=1.8 + displacement, std=.29999998211860657),
                             speed_mps=speed, elapsed_seconds=dt,
                             settings=LaneCenteringSettings(True, e2e_authority=0., strength=3.0))
        result = controller.update(source)
        self.assertEqual(result.reason, "qualified")
        self.assertLessEqual(abs(result.correction), MAX_CORRECTION)
        self.assertLessEqual(abs(result.correction - previous),
                             2 * MAX_CORRECTION * (1 - math.exp(-dt / .4)) + 1e-15)
        previous = result.correction
      self.assertEqual(controller.update(replace(source, driver_override=True)).correction, 0.)

  def test_strength_does_not_replace_model_path_authority(self):
    shifted = model(path=0.5)
    for authority in (0.0, 0.5, 1.0):
      source = observation(shifted, settings=LaneCenteringSettings(True, e2e_authority=authority))
      normal = settled(LaneCenteringController(), source).correction
      stronger = settled(LaneCenteringController(), replace(source, settings=replace(source.settings, strength=3.0))).correction
      self.assertAlmostEqual(stronger, normal * 2, delta=1e-15)

  def test_native_builder_trace_matches_frozen_source(self):
    # Frozen controller at 678af783; native modelV2, ten held 0.01 s ticks.
    expected = (8.147727264677505e-06, 1.6094286426420937e-05, 2.3844644343388891e-05,
                3.1403645241574984e-05, 3.8776013742606983e-05, 4.5966357816788907e-05,
                5.2979171663232737e-05, 5.9818838518878981e-05, 6.6489633398162377e-05,
                7.2995725765035352e-05)
    controller = LaneCenteringController()
    source = observation()
    source = replace(source, settings=replace(source.settings, strength=1.0))
    for reference in expected:
      self.assertAlmostEqual(controller.update(source).correction, reference, delta=1e-15)

  def test_filter_elapsed_time_matches_repeated_frozen_10ms_ticks(self):
    source = observation()
    ten_ms, twenty_ms = LaneCenteringController(), LaneCenteringController()
    for _ in range(10):
      result_ten = ten_ms.update(source)
    for _ in range(5):
      result_twenty = twenty_ms.update(replace(source, elapsed_seconds=0.02))
    self.assertAlmostEqual(result_ten.correction, result_twenty.correction, delta=1e-15)

  def test_candidate_passes_through_existing_curvature_limiter(self):
    result = settled(LaneCenteringController(), observation())
    self.assertIsNotNone(result.candidate_curvature)
    clipped, _ = clip_curvature(20.0, 0.0, result.candidate_curvature, 0.0)
    self.assertGreater(clipped, 0.0)
    self.assertLessEqual(clipped, result.candidate_curvature)

  def test_both_directions_offset_deadband_and_bounds(self):
    right = settled(LaneCenteringController(), observation(model())).correction
    left = settled(LaneCenteringController(), observation(model(left=-2.1, right=1.5))).correction
    self.assertGreater(right, 0.0)
    self.assertLess(left, 0.0)
    self.assertEqual(settled(LaneCenteringController(), observation(model(left=-1.75, right=1.85))).correction, 0.0)
    settings = LaneCenteringSettings(True, 0.3, 0.0)
    narrow = observation(model(left=-1.3, right=1.3), settings=settings)
    safe_limit = replace(narrow, settings=replace(settings, offset_m=0.2))
    self.assertAlmostEqual(settled(LaneCenteringController(), narrow).correction,
                           settled(LaneCenteringController(), safe_limit).correction)
    extreme = settled(LaneCenteringController(), observation(model(left=0.0, right=3.0)))
    self.assertAlmostEqual(extreme.correction, MAX_CORRECTION, delta=1e-6)

  def test_e2e_break_in_requires_confident_path(self):
    shifted = model(left=-1.0, right=2.6)
    no_e2e = settled(LaneCenteringController(), observation(shifted)).correction
    full_e2e = settled(LaneCenteringController(), observation(
      shifted, settings=LaneCenteringSettings(True, 0.0, 1.0))).correction
    uncertain = settled(LaneCenteringController(), observation(
      model(left=-1.0, right=2.6, path_std=0.6), settings=LaneCenteringSettings(True, 0.0, 1.0))).correction
    self.assertGreater(no_e2e, 0.0)
    self.assertAlmostEqual(full_e2e, 0.0)
    self.assertAlmostEqual(uncertain, no_e2e)

  def test_hard_gates_reset_and_reenable(self):
    for change in ("disabled", "override", "lane_change", "low_speed", "invalid_model", "long_only"):
      with self.subTest(change=change):
        controller = LaneCenteringController()
        source = observation()
        before = settled(controller, source).correction
        if change == "disabled":
          blocked = replace(source, settings=replace(source.settings, enabled=False))
        elif change == "override":
          blocked = replace(source, driver_override=True)
        elif change == "low_speed":
          blocked = replace(source, speed_mps=4.9)
        elif change == "invalid_model":
          blocked = replace(source, model_valid=False)
        elif change == "long_only":
          blocked = replace(source, mode=ControlMode.LONGITUDINAL_ONLY)
        else:
          changed_model = model()
          changed_model.meta.laneChangeState = "laneChangeStarting"
          blocked = replace(source, model=changed_model)
        self.assertGreater(before, 0.0)
        self.assertEqual(controller.update(blocked).correction, 0.0)
        reacquired = controller.update(source).correction
        self.assertGreater(reacquired, 0.0)
        self.assertLess(reacquired, before)

  def test_signal_and_confidence_release_then_reacquire(self):
    controller = LaneCenteringController()
    source = observation()
    before = settled(controller, source).correction
    signal = controller.update(replace(source, turn_signal_active=True))
    self.assertGreater(signal.correction, 0.0)
    self.assertLess(signal.correction, before)
    self.assertEqual(signal.reason, "signal_release")
    no_pause = replace(source, settings=replace(source.settings, pause_on_signal=False), turn_signal_active=True)
    self.assertGreater(controller.update(no_pause).correction, signal.correction)
    fading = controller.update(replace(source, model=model(prob=0.2)))
    self.assertGreater(fading.correction, 0.0)
    self.assertLess(fading.correction, before)
    self.assertEqual(fading.reason, "unqualified_model")
    self.assertGreater(controller.update(source).correction, fading.correction)

  def test_lane_change_clears_seeded_correction_even_with_signal_pause(self):
    from openpilot.cereal import log
    for state in (log.LaneChangeState.preLaneChange, log.LaneChangeState.laneChangeStarting,
                  log.LaneChangeState.laneChangeFinishing):
      for signal in (False, True):
        for pause in (False, True):
          with self.subTest(state=state, signal=signal, pause=pause):
            controller = LaneCenteringController()
            source = observation(settings=LaneCenteringSettings(True, 0., 0., pause))
            self.assertGreater(settled(controller, source).correction, 0.)
            changed_model = model()
            changed_model.meta.laneChangeState = state
            changed = controller.update(replace(source, model=changed_model, turn_signal_active=signal))
            self.assertEqual(changed.reason, 'lane_change')
            self.assertEqual(changed.correction, 0.)
            self.assertEqual(changed.candidate_curvature, source.base_curvature)
            self.assertEqual(controller._correction, 0.)
            self.assertIsNone(controller._tracker.path)
            self.assertEqual(controller.guard_applied(changed_model, source.base_curvature, source.base_curvature,
                                                     source.base_curvature, source.speed_mps, .1), source.base_curvature)


  def test_unqualified_native_models_have_no_new_correction(self):
    for bad_model in (model(prob=0.2), model(std=0.4), model(left=-0.5, right=0.5),
                      model(path=float("nan"))):
      with self.subTest(model=bad_model):
        self.assertEqual(LaneCenteringController().update(observation(bad_model)).correction, 0.0)

  def test_missing_lookahead_and_nonmonotonic_path_rejected(self):
    short = model()
    short.laneLines[1].x = XS[:10]
    short.laneLines[1].y = [-1.5] * 10
    self.assertIsNone(raw_correction(short, 20.0, 0.0, 0.0))
    reversed_path = model()
    reversed_path.position.x = list(reversed(XS))
    self.assertIsNone(raw_correction(reversed_path, 20.0, 0.0, 0.0))

  def test_malformed_inputs_rejected_without_unsafe_candidate(self):
    for updates in ({"base_curvature": float("nan")}, {"speed_mps": float("inf")},
                    {"elapsed_seconds": float("nan")}, {"elapsed_seconds": -0.01},
                    {"base_curvature": True}, {"base_curvature": "0.0002"},
                    {"speed_mps": False}, {"speed_mps": "20"},
                    {"elapsed_seconds": True}, {"elapsed_seconds": "0.01"},
                    {"settings": LaneCenteringSettings(True, float("nan"))},
                    {"settings": LaneCenteringSettings(True, True)},
                    {"settings": LaneCenteringSettings(True, cast(Any, "0.2"))},
                    {"settings": LaneCenteringSettings(True, 0.0, cast(Any, "1.0"))},
                    {"time_discontinuity": "false"}):
      with self.subTest(updates=updates):
        controller = LaneCenteringController()
        settled(controller, observation())
        result = controller.update(observation(**updates))
        self.assertEqual(result.correction, 0.0)
        self.assertEqual(result.reason, "invalid_input")
        if "base_curvature" in updates:
          self.assertIsNone(result.candidate_curvature)

  def test_invalid_observation_type_is_inert(self):
    controller = LaneCenteringController()
    settled(controller, observation())
    result = controller.update(cast(Any, object()))
    self.assertEqual(result.reason, "invalid_input")
    self.assertIsNone(result.candidate_curvature)
    self.assertEqual(result.correction, 0.0)

  def test_numeric_strings_in_model_path_rejected(self):
    native_model = model()
    wrong_path = SimpleNamespace(x=[str(x) for x in XS], y=native_model.position.y,
                                 yStd=native_model.position.yStd)
    changed_model = SimpleNamespace(laneLines=native_model.laneLines, laneLineProbs=native_model.laneLineProbs,
                                    laneLineStds=native_model.laneLineStds, position=wrong_path)
    self.assertIsNone(raw_correction(changed_model, 20.0, 0.0, 0.0))

  def test_time_discontinuity_explicitly_resets(self):
    controller = LaneCenteringController()
    settled(controller, observation())
    result = controller.update(observation(elapsed_seconds=0.2, time_discontinuity=True))
    self.assertEqual(result.reason, "time_discontinuity")
    self.assertEqual(result.correction, 0.0)

  def test_elapsed_time_does_not_infer_a_discontinuity(self):
    controller = LaneCenteringController()
    result = controller.update(observation(elapsed_seconds=0.2))
    self.assertEqual(result.reason, "qualified")
    self.assertGreater(result.correction, 0.0)


def single(*, missing_right=True, line=-0.9, edge=3.0, path=0.0):
  m = model(left=line if missing_right else -3.0, right=2.0 if missing_right else -line, path=path)
  m.laneLineProbs = [0., .95 if missing_right else .05, .05 if missing_right else .95, 0.]
  m.init('roadEdges', 2)
  for item, y in zip(m.roadEdges, (-3.0 if missing_right else -edge, edge if missing_right else 3.0), strict=True):
    item.x = XS
    item.y = [y] * len(XS)
  m.roadEdgeStds = [.1, .1]
  return m


class TestSingleBoundary(unittest.TestCase):
  def test_recorded_bd_missing_right_line_recovers_bounded_clearance(self):
    fixture = json.loads((Path(__file__).parent / 'fixtures/route_bd_single_boundary.json').read_text())
    m = messaging.new_message('modelV2').modelV2
    m.from_dict(fixture['model'])
    self.assertTrue(fixture['recordedCarControl']['latActive'])
    self.assertEqual(fixture['recordedFeedback']['requestedCorrection'], 0.)
    self.assertEqual(fixture['recordedFeedback']['reason'], 'unqualified_model')
    self.assertLess(m.laneLineProbs[2], .6)
    raw = raw_correction(m, fixture['speedMps'], 0., 0.)
    assert raw is not None
    self.assertGreater(raw, 0.)
    self.assertAlmostEqual(raw, fixture['expectedRaw'], places=12)
    result = settled(LaneCenteringController(), observation(m, speed_mps=fixture['speedMps']))
    self.assertLessEqual(abs(result.correction), MAX_CORRECTION)
    # At the exact earlier bookmark BOTH line probabilities are low and the
    # left-line std is .919 m. Preserve that refusal rather than invent a lane.
    refused = messaging.new_message('modelV2').modelV2
    refused.from_dict(fixture['firstBookmarkRefusal']['model'])
    self.assertIsNone(raw_correction(refused, 15.75, 0., 0.))

  def test_missing_either_line_recovers_clearance_without_centering_on_edge(self):
    for missing_right, sign in ((True, 1), (False, -1)):
      for edge in (2.5, 3.0):
        with self.subTest(missing_right=missing_right, edge=edge):
          m = single(missing_right=missing_right, edge=edge)
          raw = raw_correction(m, 20., 0., 0.)
          assert raw is not None
          self.assertAlmostEqual(raw, sign * 2. * (.2 - .08) / 400., places=8)
          result = settled(LaneCenteringController(), observation(m))
          self.assertEqual(result.reason, 'qualified')
          self.assertGreater(sign * result.correction, 0.)
          self.assertLessEqual(abs(result.correction), MAX_CORRECTION)

  def test_safe_model_path_is_retained_and_missing_line_geometry_is_unused(self):
    m = single(line=-1.5)
    self.assertEqual(raw_correction(m, 20., 0., 0.), 0.)
    m.laneLines[2].x = []
    m.laneLines[2].y = []
    self.assertEqual(raw_correction(m, 20., 0., 0.), 0.)

  def test_refuses_both_missing_and_unqualified_path_edge_or_corridor(self):
    for defect in ('both', 'path_std', 'edge_std', 'nan_edge', 'no_edge', 'crossed', 'wide', 'narrow',
                   'path_outside', 'near_crossing', 'missing_std', 'short_edge', 'bad_x', 'nan_prob'):
      with self.subTest(defect=defect):
        m = single()
        if defect == 'both':
          m.laneLineProbs = [0., .05, .05, 0.]
        elif defect == 'path_std':
          m.position.yStd = [.36] * len(XS)
        elif defect == 'edge_std':
          m.roadEdgeStds = [.1, .31]
        elif defect == 'nan_edge':
          m.roadEdges[1].y = [float('nan')] * len(XS)
        elif defect == 'no_edge':
          m.roadEdges = []
        elif defect == 'crossed':
          m.roadEdges[1].y = [-2.] * len(XS)
        elif defect == 'wide':
          m.roadEdges[1].y = [4.] * len(XS)
        elif defect == 'narrow':
          m.roadEdges[1].y = [1.] * len(XS)
        elif defect == 'path_outside':
          m.position.y = [-1.] * len(XS)
        elif defect == 'near_crossing':
          m.roadEdges[1].y = [-2. if x <= 10 else 3. for x in XS]
        elif defect == 'missing_std':
          m.position.yStd = []
        elif defect == 'short_edge':
          m.roadEdges[1].x = XS[:10]
          m.roadEdges[1].y = [3.] * 10
        elif defect == 'bad_x':
          m.roadEdges[1].x = list(reversed(XS))
        else:
          m.laneLineProbs = [0., .95, float('nan'), 0.]
        self.assertIsNone(raw_correction(m, 20., 0., 0.))

  def test_current_edge_is_rechecked_for_held_reader(self):
    m = single()
    source = observation(m.as_reader())
    controller = LaneCenteringController()
    first = controller.update(source)
    self.assertGreater(first.correction, 0.)
    m.roadEdgeStds = [.1, .31]
    refused = controller.update(source)
    self.assertEqual(refused.reason, 'unqualified_model')
    self.assertAlmostEqual(refused.correction, first.correction * math.exp(-.01 / .2))
    m.roadEdgeStds = [.1, .1]
    self.assertEqual(controller.update(source).reason, 'qualified')

  def test_single_boundary_still_obeys_override_freshness_signal_and_axis(self):
    source = observation(single())
    for updates in ({'driver_override':True}, {'model_valid':False}, {'lateral_active':False},
                    {'time_discontinuity':True}):
      controller = LaneCenteringController()
      self.assertGreater(settled(controller, source).correction, 0.)
      self.assertEqual(controller.update(replace(source, **updates)).correction, 0.)
    controller = LaneCenteringController()
    prior = settled(controller, source).correction
    faded = controller.update(replace(source, turn_signal_active=True))
    self.assertEqual(faded.reason, 'signal_release')
    self.assertLess(faded.correction, prior)


class TestSingleLineTracking(unittest.TestCase):
  def test_two_line_guard_rejects_auxiliary_path_bias_when_command_is_centered(self):
    for direction in (-1., 1.):
      m = model(left=-1.8, right=1.8, path=direction * .5)
      controller = LaneCenteringController()
      source = observation(m, base_curvature=0., measured_curvature=0., lateral_delay_s=.1)
      result = controller.update(source)
      self.assertEqual(result.correction, 0.)
      assert result.candidate_curvature is not None
      self.assertEqual(controller.guard_applied(m, 0., result.candidate_curvature, 0., 20., .1), 0.)
      self.assertEqual(controller._correction, 0.)

  def test_centered_planned_curves_do_not_build_an_opposing_nudge(self):
    from openpilot.starpilot.lateral.lane_clearance import _center_motion
    for curvature in (-.006, -.002, .002, .006):
      x, y, _ = _center_motion(curvature, curvature, 25., .1)
      x, y = np.r_[0., x, 50.], np.r_[0., y, y[-1]]
      m = model()
      for index, shift in ((1, -1.8), (2, 1.8)):
        m.laneLines[index].x, m.laneLines[index].y = x.tolist(), (y + shift).tolist()
      m.position.x, m.position.y = x.tolist(), (y - math.copysign(.6, curvature)).tolist()
      m.position.yStd = [.1] * len(x)
      controller = LaneCenteringController()
      source = observation(m, base_curvature=curvature, measured_curvature=curvature,
                           speed_mps=25., lateral_delay_s=.1)
      self.assertAlmostEqual(controller.update(source).correction, 0., delta=1e-8)

  def test_curve_spacing_limit_shapes_the_target_before_it_ramps(self):
    from openpilot.starpilot.lateral.lane_clearance import _center_motion, limit_tracking
    from openpilot.starpilot.lateral.lane_centering import ACQUIRE_TAU
    for sign in (-1., 1.):
      base, delta, speed = sign * .004, sign * .00004, 25.
      x, y, _ = _center_motion(base + delta, base, speed, .1)
      x, y = np.r_[0., x, 50.], np.r_[0., y, y[-1]]
      m = model()
      for index, shift in ((1, -1.8), (2, 1.8)):
        m.laneLines[index].x, m.laneLines[index].y = x.tolist(), (y + shift).tolist()
      m.position.x, m.position.y = x.tolist(), (y - sign * .7).tolist()
      m.position.yStd = [.1] * len(x)
      source = observation(m, base_curvature=base, measured_curvature=base, speed_mps=speed, lateral_delay_s=.1)
      controller = LaneCenteringController()
      first = controller.update(source)
      self.assertGreater(sign * first.correction, 0.)
      self.assertLess(abs(first.correction), abs(delta) * .04)
      raw = raw_correction(m, speed, 0., 0.)
      assert raw is not None
      path = controller._tracker.path
      assert path is not None
      target = limit_tracking(path, base, raw * .3 * 1.5, base, speed, .1)
      self.assertAlmostEqual(first.correction, target * (1. - math.exp(-.01 / ACQUIRE_TAU)), delta=1e-12)
      assert first.candidate_curvature is not None
      applied = controller.guard_applied(m, base, first.candidate_curvature, base, speed, .1) - base
      self.assertAlmostEqual(applied, first.correction, delta=1e-12)

  def test_nudge_build_and_reversal_respect_lateral_acceleration_rate(self):
    for speed in (5., 20., 35.):
      controller = LaneCenteringController()
      previous = 0.
      for tick in range(160):
        sign = 1. if tick < 80 else -1.
        dt = (.01, .02, .05)[tick % 3]
        source = observation(model(left=-1.8 + sign * 1.4, right=1.8 + sign * 1.4),
                             speed_mps=speed, elapsed_seconds=dt, model_sample_id=tick,
                             settings=LaneCenteringSettings(True, e2e_authority=0., strength=3.))
        result = controller.update(source)
        self.assertLessEqual(abs(result.correction - previous) * speed * speed / dt, 1. + 1e-12)
        previous = result.correction
      self.assertLess(previous, 0.)
      self.assertEqual(controller.update(replace(source, driver_override=True)).correction, 0.)
      self.assertEqual(controller.update(replace(source, lateral_active=False)).correction, 0.)

  def test_two_line_tracking_interpolates_shared_coordinates_and_bounds_offset(self):
    m = model(left=-1.31, right=1.31)
    m.laneLines[2].x = [float(x) + .25 for x in XS]
    controller = LaneCenteringController()
    source = observation(m, base_curvature=0., settings=LaneCenteringSettings(True, .3, 0., True),
                         measured_curvature=0., lateral_delay_s=.1)
    controller.update(source)
    path = controller._tracker.path
    assert path is not None
    self.assertEqual(path[0][0], 1.)
    np.testing.assert_allclose(path[1], 0., atol=1e-6)
    with patch('openpilot.starpilot.lateral.lane_centering.limit_tracking', return_value=0.) as guard:
      self.assertEqual(controller.guard_applied(m, 0., .002, 0., 20., .1), 0.)
    np.testing.assert_allclose(guard.call_args.args[0][1], .21, atol=1e-6)

  def test_tracking_guard_has_continuous_small_corrections_near_the_objective(self):
    from openpilot.starpilot.lateral.lane_clearance import _center_motion, limit_tracking
    for sign in (1., -1.):
      previous = 0.
      for step in range(1, 51):
        target = sign * step * 1e-6
        x, y, _ = _center_motion(target, 0., 20., .1)
        path = np.r_[0., x, 50.], np.r_[0., y, y[-1]]
        allowed = limit_tracking(path, 0., sign * .0012, 0., 20., .1)
        self.assertGreater(sign * allowed, 0.)
        self.assertLess(abs(allowed - previous), 2e-6)
        bx, by, _ = _center_motion(0., 0., 20., .1)
        cx, cy, _ = _center_motion(allowed, 0., 20., .1)
        selected = bx >= 2.
        self.assertLess(np.mean((cy[selected] - np.interp(cx[selected], *path)) ** 2),
                        np.mean((by[selected] - np.interp(bx[selected], *path)) ** 2))
        previous = allowed
      self.assertEqual(limit_tracking(path, 0., -sign * .0012, 0., 20., .1), 0.)

  def test_two_distinct_frames_confirm_line_without_learning_model_drift(self):
    from openpilot.starpilot.lateral.lane_tracking import SingleLineTracker
    tracker = SingleLineTracker()
    m = single(line=-1.5, edge=10.)
    self.assertIsNone(tracker.target(m, 1, 20., .01, 0., revision=0).y)
    m.position.y = [.3] * len(XS)
    for revision in range(1, 7):
      self.assertIsNone(tracker.target(m, 1, 20., .01, 0., revision=revision).y)
    target = tracker.target(m, 2, 20., .01, 0.)
    self.assertFalse(target.blocked)
    assert target.y is not None
    self.assertAlmostEqual(target.y, .2)
    assert tracker.spacing is not None
    self.assertAlmostEqual(tracker.spacing, 1.7)

  def test_precise_tracked_line_uses_smaller_quiet_zone_without_chasing_noise(self):
    for missing_right, sign in ((True, 1), (False, -1)):
      with self.subTest(missing_right=missing_right):
        m = single(missing_right=missing_right, line=-1.64, edge=10.)
        index = 1 if missing_right else 2
        m.laneLineStds[index] = .02
        raw = raw_correction(m, 20., 0., 0., _single_target=sign * .06)
        assert raw is not None
        self.assertAlmostEqual(raw, sign * .0001, places=8)
        self.assertEqual(raw_correction(m, 20., 0., 0., _single_target=sign * .03), 0.)
        self.assertEqual(raw_correction(m, 20., 0., 1., _single_target=sign * .6), 0.)
        m.laneLineStds[index] = .1
        self.assertEqual(raw_correction(m, 20., 0., 0., _single_target=sign * .06), 0.)
    self.assertEqual(raw_correction(model(left=-1.8, right=1.8, path=.06), 20., 0., 0.), 0.)

  def test_without_width_tracks_independent_spacing_in_both_directions(self):
    from openpilot.starpilot.lateral.lane_tracking import SingleLineTracker
    for missing_right, sign in ((True, 1), (False, -1)):
      for distance in (1.45, 1.5, 1.55):
        with self.subTest(missing_right=missing_right, distance=distance):
          tracker = SingleLineTracker()
          m = single(missing_right=missing_right, line=-distance, edge=10.)
          for frame in range(8):
            target = tracker.target(m, frame, 20., .05, 0.)
          self.assertIsNone(tracker.width)
          assert tracker.pending is not None and tracker.spacing is not None and target.y is not None
          self.assertAlmostEqual(tracker.pending, distance)
          self.assertAlmostEqual(tracker.spacing, 1.7)
          self.assertFalse(target.blocked)
          self.assertAlmostEqual(target.y, sign * (1.7 - distance))
          raw = raw_correction(m, 20., 0., 0., _single_target=target.y)
          assert raw is not None
          self.assertGreater(sign * raw, 0.)

  def test_observed_narrow_width_precedes_nominal_and_edge_still_vetoes(self):
    from openpilot.starpilot.lateral.lane_tracking import SingleLineTracker
    for known_width in (False, True):
      with self.subTest(known_width=known_width):
        tracker = SingleLineTracker()
        if known_width:
          tracker.target(model(left=-1.35, right=1.35), 0, 20., .05, 0.)
        blocked = False
        for frame in range(1, 10):
          target = tracker.target(single(line=-1.35, edge=1.35), frame, 20., .05, 0.)
          blocked |= target.blocked
          if not known_width:
            self.assertIsNone(target.y)
        if known_width:
          self.assertFalse(target.blocked)
          assert tracker.spacing is not None and target.y is not None
          self.assertAlmostEqual(tracker.spacing, 1.35)
          self.assertAlmostEqual(target.y, 0.)
        else:
          self.assertTrue(blocked)
          self.assertIsNone(target.y)

  def test_spacing_preference_cannot_bypass_current_line_geometry_or_path_uncertainty(self):
    m = single(line=-1.2, edge=10.)
    m.laneLines[1].x = XS[:10]
    m.laneLines[1].y = [-1.2] * 10
    self.assertIsNone(raw_correction(m, 20., 0., 0., _single_target=.6))
    m = single(line=-1.2, edge=10.)
    m.position.yStd = [.36] * len(XS)
    self.assertIsNone(raw_correction(m, 20., 0., 0., _single_target=.6))

  def test_trusted_width_transfers_to_current_line_across_wide_road(self):
    controller = LaneCenteringController()
    for frame in range(10):
      controller.update(observation(model(left=-1.8, right=1.8), model_sample_id=1_000_000_000 + frame * 50_000_000,
                                    elapsed_seconds=.05))
    for frame in range(10, 30):
      m = single(line=-1.2, edge=10.)
      result = controller.update(observation(m.as_reader(), model_sample_id=1_000_000_000 + frame * 50_000_000,
                                             elapsed_seconds=.05))
    self.assertEqual(result.reason, 'qualified')
    self.assertGreater(result.correction, .0002)
    assert controller._tracker.spacing is not None
    self.assertAlmostEqual(controller._tracker.spacing, 1.8, places=6)

  def test_normal_offset_tracks_a_sloping_line_in_both_directions(self):
    from openpilot.starpilot.lateral.lane_tracking import SingleLineTracker
    for side in (0, 1):
      tracker = SingleLineTracker()
      for frame in range(10):
        m = model(left=-1.8, right=1.8)
        for index, intercept in ((1, -1.8), (2, 1.8)):
          m.laneLines[index].y = [intercept + .15 * x for x in XS]
        m.position.y = [.15 * x for x in XS]
        tracker.target(m, frame, 20., .05, 0.)
      m.laneLineProbs = [0., .95 if side == 0 else .05, .05 if side == 0 else .95, 0.]
      for index, intercept in ((1, -1.8), (2, 1.8)):
        m.laneLines[index].y = [intercept + .2 + .15 * x for x in XS]
      target = tracker.target(m, 11, 20., .05, 0.)
      self.assertFalse(target.blocked)
      assert target.y is not None
      self.assertAlmostEqual(target.y, 3.2, places=5)

  def test_new_line_needs_acquisition_and_jumps_discard_spacing(self):
    from openpilot.starpilot.lateral.lane_tracking import SingleLineTracker
    tracker = SingleLineTracker()
    for frame in range(8):
      target = tracker.target(single(line=-1.7, edge=10.), frame, 20., .05, 0.)
      if frame == 0:
        self.assertIsNone(target.y)
      else:
        self.assertIsNotNone(target.y)
    assert tracker.spacing is not None
    self.assertAlmostEqual(tracker.spacing, 1.7, places=6)
    jump = tracker.target(single(line=-1.1, edge=10.), 9, 20., .05, 0.)
    self.assertTrue(jump.blocked)
    self.assertIsNone(jump.y)
    self.assertIsNone(tracker.spacing)

  def test_missing_sources_and_driver_override_cannot_keep_line_history(self):
    controller = LaneCenteringController()
    for frame in range(12):
      source = observation(single(line=-1.7, edge=10.), model_sample_id=frame + 1, elapsed_seconds=.05)
      controller.update(source)
    self.assertIsNotNone(controller._tracker.spacing)
    controller.update(replace(source, driver_override=True))
    self.assertIsNone(controller._tracker.spacing)
    for frame in range(20, 32):
      controller.update(replace(source, model_sample_id=frame))
    controller.update(replace(source, turn_signal_active=True, model_sample_id=33))
    self.assertIsNone(controller._tracker.spacing)

  def test_line_loss_and_reacquisition_remain_filtered_and_bounded(self):
    controller = LaneCenteringController()
    previous = 0.
    for frame in range(100):
      m = model(left=-1.8, right=1.8) if frame < 10 or frame >= 70 else single(line=-1.2, edge=10.)
      if 45 <= frame < 50:
        m.laneLineProbs = [0., .05, .05, 0.]
      result = controller.update(observation(m, model_sample_id=frame + 1, elapsed_seconds=.05))
      self.assertLessEqual(abs(result.correction), MAX_CORRECTION)
      self.assertLessEqual(abs(result.correction - previous), 2 * MAX_CORRECTION * (1 - math.exp(-.05 / .2)) + 1e-15)
      if 45 <= frame < 50:
        self.assertEqual(result.reason, 'unqualified_model')
        self.assertLessEqual(abs(result.correction), abs(previous))
      previous = result.correction


class TestVehicleClearance(unittest.TestCase):
  def test_required_unknown_body_preserves_nominal_and_clears_retained_state(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions
    for body in (None, BodyDimensions(1.88, math.nan)):
      with self.subTest(body=body):
        controller = LaneCenteringController(body=body, body_required=True)
        controller._correction = .001
        controller._tracker.path = (np.array(XS), np.full(len(XS), .3))
        source = observation(base_curvature=-.006)
        result = controller.update(source)
        self.assertEqual((result.candidate_curvature, result.correction, result.reason), (-.006, 0., 'unqualified_body'))
        self.assertIsNone(controller._tracker.path)
        self.assertIsNone(controller._geometry_cache)
        controller._correction = .001
        controller._tracker.path = (np.array(XS), np.full(len(XS), .3))
        self.assertEqual(controller.guard_applied(source.model, -.006, -.005, -.006, 20., .1), -.006)
        self.assertEqual(controller._correction, 0.)
        self.assertIsNone(controller._tracker.path)

  def test_required_known_body_retains_prediction_and_withdraws_when_lost(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions
    controller = LaneCenteringController(body=BodyDimensions(1.88, 4.855), body_required=True)
    m = single(edge=10.)
    m.roadEdges[0].y = [-10.] * len(XS)
    for frame in range(30):
      source = observation(m, model_sample_id=frame + 1, elapsed_seconds=.05,
                           measured_curvature=0., lateral_delay_s=.1)
      result = controller.update(source)
    self.assertGreater(result.correction, 0.)
    self.assertIsNotNone(controller._tracker.path)
    controller._body = None
    result = controller.update(replace(source, turn_signal_active=True))
    self.assertEqual((result.candidate_curvature, result.correction), (source.base_curvature, 0.))
    self.assertIsNone(controller._tracker.path)

  def test_final_command_guard_rejects_worsening_tracking_and_preserves_limits(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions
    controller = LaneCenteringController(body=BodyDimensions(1.88, 4.855))
    m = single(edge=3.)
    controller._correction = .001
    result = controller.guard_applied(m, .006, .007, .006, 20., .1)
    self.assertEqual(result, .006)
    self.assertEqual(controller._correction, 0.)
    controller._tracker.path = (np.array(XS), np.full(len(XS), .3))
    self.assertEqual(controller.guard_applied(m, -.006, -.007, -.006, 20., .1), -.006)

  def test_commanded_curvature_is_checked_independently_of_model_position(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions, limit_correction
    body = BodyDimensions(1.88, 4.855)
    m = single(line=-1.8, edge=3.)
    self.assertEqual(m.position.y[20], 0.)
    # Same perceived path, different actual commanded motion: adding rightward
    # steering to an already rightward command must not worsen edge intrusion.
    self.assertEqual(limit_correction(m, .006, .001, .006, 20., .1, body), 0.)
    self.assertEqual(limit_correction(m, .006, -.001, .006, 20., .1, body), -.001)

  def test_wide_clear_corridor_keeps_the_requested_contribution(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions, limit_correction
    m = single(line=-1.8, edge=10.)
    m.roadEdges[0].y = [-10.] * len(XS)
    body = BodyDimensions(1.88, 4.855)
    for correction in (-.001, .001):
      self.assertEqual(limit_correction(m, 0., correction, 0., 20., .1, body), correction)

  def test_short_or_malformed_edge_does_not_gain_extrapolated_clearance(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions, limit_correction
    for bad in ('short', 'nan', 'reversed', 'negative_std'):
      with self.subTest(bad=bad):
        m = single(edge=10.)
        if bad == 'short':
          m.roadEdges[1].x = XS[:5]
          m.roadEdges[1].y = [10.] * 5
        elif bad == 'nan':
          m.roadEdges[1].y = [math.nan] * len(XS)
        elif bad == 'reversed':
          m.roadEdges[1].x = list(reversed(XS))
        else:
          m.roadEdgeStds = [.1, -.1]
        self.assertEqual(limit_correction(m, 0., .001, 0., 20., .1, BodyDimensions(1.88, 4.855)), 0.)

  def test_body_guard_withdraws_extra_correction_without_changing_nominal_command(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions
    controller = LaneCenteringController(body=BodyDimensions(1.88, 4.855))
    m = single(edge=10.)
    m.roadEdges[0].y = [-10.] * len(XS)
    for frame in range(30):
      source = observation(m, model_sample_id=frame + 1, elapsed_seconds=.05,
                           measured_curvature=0., lateral_delay_s=.1)
      result = controller.update(source)
    self.assertGreater(result.correction, 0.)
    refused = controller.update(replace(source, measured_curvature=None))
    self.assertEqual(refused.reason, 'clearance_limited')
    self.assertEqual(refused.correction, 0.)
    self.assertEqual(refused.candidate_curvature, source.base_curvature)
    self.assertEqual(controller.update(replace(source, driver_override=True)).correction, 0.)

  def test_dimensions_are_explicit_and_invalid_values_are_rejected(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions, interface_dimensions
    self.assertIsNone(interface_dimensions(object(), object()))
    for width, length in ((True, 4.8), (1.8, math.nan), (0., 4.8), (1.8, 20.)):
      self.assertFalse(BodyDimensions(width, length).valid)
    for dimensions in ((1.88,), (1.88, 4.855, 0.), 'unknown', (1.88, math.nan)):
      interface = SimpleNamespace(get_lateral_body_dimensions=lambda cp, bounds=dimensions: bounds)
      self.assertIsNone(interface_dimensions(interface, object()))

  def test_height_envelope_rejects_close_edges_and_keeps_wide_observed_space(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions, limit_correction
    body = BodyDimensions(1.88, 4.855, 1.495)
    for side in (0, 1):
      for distance, expected in ((1.8, 0.), (10., .001)):
        m = single(edge=10.)
        m.roadEdgeStds = [1., 1.]
        m.roadEdgeStds[side] = .1
        m.roadEdges[side].y = [distance * (-1 if side == 0 else 1)] * len(XS)
        contribution = .001 * (-1 if side == 0 else 1)
        self.assertEqual(abs(limit_correction(m, 0., contribution, 0., 20., .1, body)), expected)

  def test_height_envelope_requires_absolute_margin_on_the_same_left_edge(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions, _center_motion, limit_correction
    m = single(edge=10.)
    x, y, _ = _center_motion(.006, .006, 20., .1)
    m.roadEdges[0].y = (np.interp(XS, np.r_[0., x], np.r_[0., y]) - 1.8).tolist()
    m.roadEdgeStds = [0., 1.]
    self.assertEqual(limit_correction(m, .006, -.001, .006, 20., .1, BodyDimensions(1.88, 4.855, 1.495)), 0.)

  def test_height_clearance_transitions_preserve_native_curvature_rate_and_nominal(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions
    controller = LaneCenteringController(body=BodyDimensions(1.88, 4.855, 1.495), body_required=True)
    previous = 0.
    nonzero = 0
    for frame in range(90):
      m = model(left=-1.2, right=2.4)
      m.init('roadEdges', 2)
      for edge, side in zip(m.roadEdges, (-1., 1.), strict=True):
        edge.x, edge.y = XS, [10. * side] * len(XS)
      m.roadEdgeStds = [.1, .1]
      if frame >= 20:
        m.laneLineProbs = [0., .9, .1, 0.]
      if 40 <= frame < 60:
        m.roadEdges[1].y = [1.8] * len(XS)
      source = observation(m, model_sample_id=frame + 1, base_curvature=0., measured_curvature=0., lateral_delay_s=.1)
      result = controller.update(source)
      self.assertIsNotNone(result.candidate_curvature)
      proposed, _ = clip_curvature(20., previous, result.candidate_curvature, 0.)
      nominal, _ = clip_curvature(20., previous, 0., 0.)
      applied = controller.guard_applied(m, nominal, proposed, 0., 20., .1)
      self.assertLessEqual(abs(applied - previous), 5. / 20. ** 2 * .01 + 1e-12)
      if 40 <= frame < 60:
        self.assertEqual((result.correction, applied), (0., nominal))
      elif frame >= 60:
        nonzero += applied != nominal
      previous = applied
    self.assertGreater(nonzero, 0)
    controller._body = None
    refused = controller.update(replace(source, model_sample_id=100))
    self.assertEqual((refused.reason, refused.correction, refused.candidate_curvature), ('unqualified_body', 0., 0.))
    self.assertIsNone(controller._tracker.path)

  def test_height_envelope_checks_the_edge_between_rectangle_corners(self):
    from openpilot.starpilot.lateral.lane_clearance import _interval_edge_margin
    bounds = np.array([0.]), np.array([10.]), np.array([2.])
    margin = _interval_edge_margin(bounds, np.array([0., 4., 5., 6., 10.]), np.array([10., 10., 1., 10., 10.]), 1)
    self.assertLess(margin[0], 0.)

  def test_height_envelope_contains_unknown_camera_reference_and_initial_roll(self):
    from itertools import product
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions, _center_motion, _interval_body_bounds
    body = BodyDimensions(1.88, 4.855, 1.495)
    bounds = _interval_body_bounds(.015, -.003, 20., .2, body)
    x, y, heading = _center_motion(.015, -.003, 20., .2)
    poses = np.column_stack((np.r_[0., x], np.r_[0., y], np.r_[0., heading]))
    corners = list(product((-body.length / 2, body.length / 2), (-body.width / 2, body.width / 2), (0., body.height)))
    combinations = np.asarray(list(product(corners, corners, corners)))
    point, reference, camera = combinations[:, 0], combinations[:, 1], combinations[:, 2]
    for index in range(16):
      for fraction in (.25, .5, .75):
        tx, ty, angle = poses[index] * (1 - fraction) + poses[index + 1] * fraction
        relative = point - reference
        px = tx + reference[:, 0] - camera[:, 0] + np.cos(angle) * relative[:, 0] - np.sin(angle) * relative[:, 1]
        py = ty + reference[:, 1] - camera[:, 1] + np.sin(angle) * relative[:, 0] + np.cos(angle) * relative[:, 1]
        pz = point[:, 2] - camera[:, 2]
        self.assertGreaterEqual(float(np.min(px)), bounds[0][index] - 1e-12)
        self.assertLessEqual(float(np.max(px)), bounds[1][index] + 1e-12)
        for roll in (-math.pi, -.7, 0., math.pi / 2, math.pi):
          projected = py * math.cos(roll) - pz * math.sin(roll)
          self.assertLessEqual(float(np.max(np.abs(projected))), bounds[2][index] + 1e-12)

  def test_height_envelope_refuses_short_or_invalid_observation_and_bounds(self):
    from openpilot.starpilot.lateral.lane_clearance import BodyDimensions, limit_correction
    for bad in ('short', 'nonfinite', 'duplicate', 'negative_std', 'nan_std'):
      m = single(edge=10.)
      m.roadEdges[0].y = [-10.] * len(XS)
      if bad == 'short':
        m.roadEdges[1].x, m.roadEdges[1].y = XS[:5], [10.] * 5
      elif bad == 'nonfinite':
        m.roadEdges[1].y = [math.nan] * len(XS)
      elif bad == 'duplicate':
        m.roadEdges[1].x = [0.] * len(XS)
      else:
        m.roadEdgeStds = [.1, -.1 if bad == 'negative_std' else math.nan]
      self.assertEqual(limit_correction(m, 0., .001, 0., 20., .1, BodyDimensions(1.88, 4.855, 1.495)), 0.)
    for height in (True, 0., math.nan, math.inf, 4.):
      self.assertFalse(BodyDimensions(1.88, 4.855, height).valid)

