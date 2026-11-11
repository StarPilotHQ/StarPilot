"""Scripted clocks only; no sleeps, native inference or model assets."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.starpilot.speed_limits.acceptance import ObservationKind
from openpilot.starpilot.speed_limits.vision import observation
from openpilot.starpilot.speed_limits.vision.tests.test_early_stop_publication import Box, Evidence, function


class TestClockPair(unittest.TestCase):
  def sample(self, mono, boot):
    with patch.object(observation.time, 'monotonic_ns', side_effect=mono) as m, \
         patch.object(observation.time, 'clock_gettime_ns', side_effect=boot) as b:
      result = observation.clock_pair_ns()
    return result, m.call_count, b.call_count

  def test_valid_first_and_exact_boundary_need_only_one_triplet(self):
    for elapsed in (0, 1, observation.MAX_PAIR_SKEW_NS):
      with self.subTest(elapsed=elapsed):
        self.assertEqual(self.sample([100, 100 + elapsed], [1000]),
                         (((200 + elapsed) // 2, 1000), 2, 1))

  def test_overlong_first_uses_only_fresh_second_triplet(self):
    self.assertEqual(self.sample([100, 5_000_101, 7_000_000, 7_000_004], [1000, 8_000_002]),
                     ((7_000_002, 8_000_002), 4, 2))

  def test_second_bad_window_is_terminal(self):
    for second in ([10_000_000, 15_000_001], [10_000_000, 9_999_999]):
      with self.subTest(second=second):
        self.assertEqual(self.sample([100, 5_000_101] + second, [1000, 2000]), (None, 4, 2))

  def test_negative_first_never_retries(self):
    self.assertEqual(self.sample([101, 100], [1000]), (None, 2, 1))

  def test_clock_exceptions_propagate_on_either_attempt(self):
    cases = [([OSError('before')], []), ([100], [OSError('boot')]),
             ([100, OSError('after')], [1000]),
             ([100, 5_000_101, OSError('second before')], [1000]),
             ([100, 5_000_101, 7_000_000], [1000, OSError('second boot')]),
             ([100, 5_000_101, 7_000_000, OSError('second after')], [1000, 2000])]
    for mono, boot in cases:
      with self.subTest(mono=mono, boot=boot), self.assertRaises(OSError):
        self.sample(mono, boot)

  def test_fresh_second_pair_does_not_refresh_consumer_EOF_or_expiry(self):
    pair, _, _ = self.sample([100, 5_000_101, 5_000_000_000, 5_000_000_004],
                             [1000, 6_000_000_002])
    event = SimpleNamespace(modelId=observation.MODEL_ID, stream='road', status='valid',
                            cameraFrameEofBootTime=pair[1] - 1_000_000,
                            observedMonoTime=pair[0] - 1_000_000,
                            validUntilMonoTime=pair[0] + 1_000_000, speedMps=10., confidence=.9,
                            supportCount=2, episode=1, producerSessionId='session')
    args = {'receipt_mono_ns': pair[0], 'now_mono_ns': pair[0], 'now_boot_ns': pair[1],
            'car_fingerprint': 'car', 'drive_session': 'drive'}
    self.assertIs(observation.vision_observation(event, **args).kind, ObservationKind.VALID)
    event.cameraFrameEofBootTime = pair[1] - observation.MAX_RESULT_AGE_NS - 1
    self.assertIs(observation.vision_observation(event, **args).kind, ObservationKind.STALE)
    event.cameraFrameEofBootTime = pair[1] - 1_000_000
    event.validUntilMonoTime = pair[0] - 1
    self.assertIs(observation.vision_observation(event, **args).kind, ObservationKind.STALE)

  def test_fresh_retry_preserves_producer_age_skew_health_and_units_checks(self):
    pair, _, _ = self.sample([100, 5_000_101, 5_000_000_000, 5_000_000_004],
                             [1000, 6_000_000_002])
    for reason in ('healthy', 'old_EOF', 'offset_skew', 'DM', 'units'):
      with self.subTest(reason=reason):
        sent = []
        ns = {'clock_pair_ns': lambda: pair, 'MAX_PAIR_SKEW_NS': observation.MAX_PAIR_SKEW_NS,
              'MAX_RESULT_AGE_NS': observation.MAX_RESULT_AGE_NS, 'STOP_MAX_AGE_NS': 1_500_000_000,
              '_driving_model_ready': lambda sm, now, reason=reason: reason != 'DM', 'StopEvidence': Evidence,
              'StopBox': Box, '_send': lambda pm, sent=sent, **kw: sent.append(kw)}
        publish = function(Path(observation.__file__).with_name('producer.py'), '_publish_ready_stop', ns)
        offset = pair[1] - pair[0]
        args = {'session': 's', 'stream': 'road', 'is_metric': False, 'offset_ns': offset,
                'source_observed_ns': pair[0] - 1_000_000, 'eof_boot_ns': pair[1] - 1_000_000,
                'frame_id': 7, 'width': 100, 'height': 50, 'stops': (Box(.8, (1, 2, 3, 4)),),
                'last_result': None, 'last_result_session': '', 'last_result_observed_ns': 0,
                'last_result_valid_until_ns': 0}
        if reason == 'old_EOF':
          args['eof_boot_ns'] = pair[1] - observation.MAX_RESULT_AGE_NS - 1
        if reason == 'offset_skew':
          args['offset_ns'] = offset - observation.MAX_PAIR_SKEW_NS - 1
        publish(None, SimpleNamespace(update=lambda timeout: None),
                SimpleNamespace(get_bool=lambda key, reason=reason: reason == 'units'), **args)
        self.assertEqual(len(sent), int(reason == 'healthy'))
        if sent:
          self.assertEqual(sent[0]['stop_sample'].eof_boot_ns, args['eof_boot_ns'])
          self.assertEqual(sent[0]['stop_sample'].observed_ns, args['source_observed_ns'])


  def test_retry_fresh_boot_preserves_actual_map_age_and_backward_clock_rejection(self):
    from openpilot.starpilot.speed_limits import map_source as ms
    from openpilot.starpilot.speed_limits.map_observation import MapSource
    from openpilot.starpilot.speed_limits.tests.test_map_source import v2_frame

    frame = replace(v2_frame(), way_selection='current')
    pair, _, _ = self.sample([100, 5_000_101, 7_000_000_000, 7_000_000_004],
                             [1000, 456_100_000_000])
    for reason in ('expired', 'backward', 'transport'):
      with self.subTest(reason=reason):
        source = MapSource()
        args = {'received': True, 'now_boot_ns': pair[1], 'transport_alive': True,
                'drive_session': 'drive', 'car_fingerprint': 'vehicle'}
        current = source.step(frame, **args)
        self.assertIs(current.kind, ObservationKind.VALID)
        self.assertEqual(source.tracker.diagnostic(now_boot_ns=pair[1]).frame, frame)
        args['received'] = False
        if reason == 'expired':
          args['now_boot_ns'] = pair[1] + ms.EVENT_MAX_AGE_NS + 1
        elif reason == 'backward':
          args['now_boot_ns'] = frame.event_mono_ns - 1
        else:
          args['transport_alive'] = False
        rejected = source.step(None, **args)
        expected = ObservationKind.UNKNOWN if reason == 'backward' else ObservationKind.STALE
        self.assertIs(rejected.kind, expected)
        self.assertIsNone(rejected.candidate)
        self.assertIsNone(source.tracker.diagnostic(now_boot_ns=args['now_boot_ns']).frame)
