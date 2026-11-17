"""Reset reasons on the real producer loop, with existing fake camera fixtures."""
import unittest
from unittest.mock import patch

from openpilot.starpilot.speed_limits.vision.tests import test_producer as fixture


class TestProducerResetReasons(unittest.TestCase):
  def setUp(self):
    self.case = fixture.TestVisionProducer()
    self.case.setUp()
    self.addCleanup(self.case.doCleanups)
    self.events = self.enterContext(patch('openpilot.common.swaglog.cloudlog.event'))

  def reasons(self):
    reasons = []
    for call in self.events.call_args_list:
      self.assertEqual(call.args, ('vision.session_reset',))
      self.assertEqual(set(call.kwargs), {'reason', 'session', 'stream'})
      self.assertRegex(call.kwargs['session'], r'^[0-9a-f]{32}$')
      self.assertIn(call.kwargs['stream'], ('unknown', 'road', 'wideRoad'))
      self.assertRegex(call.kwargs['reason'], r'^[a-z_]+$')
      reasons.append(call.kwargs['reason'])
    return reasons

  def test_stream_start_and_completion_stale_have_distinct_reasons(self):
    road = {fixture.VisionStreamType.VISION_STREAM_NARROW_ROAD}
    _, _, messages = self.case.run_frame(road, completion_age_ns=2_000_000_001)
    self.assertEqual(self.reasons(), ['camera_stream_changed', 'completion_clock_or_age'])
    self.assertEqual(str(messages[-1].status), 'stale')
    self.assertEqual(messages[-1].validUntilMonoTime, 0)

  def test_admission_model_reset_and_absent_camera(self):
    road = {fixture.VisionStreamType.VISION_STREAM_NARROW_ROAD}
    _, core, _ = self.case.run_frame(road, model_states=[(True, False, 0, 0)])
    core.observe.assert_not_called()
    self.assertEqual(self.reasons(), ['camera_stream_changed', 'admission_driving_model_unready'])
    self.events.reset_mock()
    self.case.run_frame({fixture.VisionStreamType.VISION_STREAM_CABIN})
    self.assertEqual(self.reasons(), [])

  def test_heartbeat_clock_and_model_resets_are_logged_once(self):
    for failure, reason in (('clock', 'heartbeat_clock_or_age'),
                            ('model', 'heartbeat_driving_model_unready')):
      with self.subTest(failure=failure):
        self.events.reset_mock()
        self.case.cooldown_messages(heartbeat_failure=failure)
        reasons = self.reasons()
        self.assertEqual(reasons.count(reason), 1)
        self.assertEqual(reasons[0], 'camera_stream_changed')

  def test_units_and_reconnect_use_their_existing_reset_paths(self):
    for method, reason, expected in (
      ('test_live_unit_changes_discard_held_sign_and_require_fresh_consensus', 'units_changed', 6),
      ('test_same_client_reconnect_starts_new_consensus_and_session', 'camera_reconnect', 1),
    ):
      with self.subTest(reason=reason):
        self.events.reset_mock()
        getattr(self.case, method)()
        self.assertEqual(self.reasons().count(reason), expected)


if __name__ == '__main__':
  unittest.main()
