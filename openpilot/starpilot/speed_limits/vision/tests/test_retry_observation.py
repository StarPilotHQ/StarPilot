from unittest.mock import patch
import unittest
import numpy as np
from openpilot.starpilot.speed_limits.vision import model
from openpilot.starpilot.speed_limits.vision.tests.test_retry_consistency import core_fixture, probabilities


class ProposalLifecycleTests(unittest.TestCase):
  def setUp(self):
    guard = patch('cv2.dnn.readNetFromONNX', side_effect=AssertionError('No ML loads in lifecycle fixtures'))
    guard.start()
    self.addCleanup(guard.stop)
    self.frame = np.zeros((200, 200, 3), dtype=np.uint8)

  def contradictory_core(self, repeats):
    reject = probabilities()
    numeric = probabilities(25, .75)
    core = core_fixture([(reject, reject), (reject, reject), (numeric, numeric)] * repeats,
                        [False, True, True] * repeats)
    core.temporal_tracking_enabled = True
    core.reset()
    return core

  def test_rejected_proposal_number_does_not_seed_observation_history(self):
    core = self.contradictory_core(2)
    for now in (1000., 1000.5):
      self.assertIsNone(core.observe(self.frame, now=now))
      self.assertEqual(core.latest_detector_proposal.speed_limit_mph, 25)
      self.assertEqual(list(core.history), [])
      self.assertEqual(core.published_speed_limit_mph, 0)
      self.assertEqual(core.last_detection_at, 0.)
      self.assertEqual(core.last_published_support_at, 0.)
    self.assertEqual(core.classifier_net.calls, 6)

  def test_veto_does_not_replace_or_restamp_previous_publication(self):
    core = self.contradictory_core(1)
    core.published_speed_limit_mph = 40
    core.published_confidence = .9
    core.published_support_count = 3
    core.episode = 7
    core.last_detection_at = 999.
    core.last_published_support_at = 999.
    core.history.append(model.HistoryEntry(40, .9, 999., False))
    with patch.object(core, '_confirm_detection', side_effect=AssertionError('Veto must not confirm a new read')):
      result = core.observe(self.frame, now=1000.)
    self.assertEqual(result, (40, .9, 3, 7))
    self.assertEqual(core.latest_detector_proposal.speed_limit_mph, 25)
    self.assertEqual(core.last_detection_at, 999.)
    self.assertEqual(core.last_published_support_at, 999.)
    self.assertEqual(len(core.history), 1)
