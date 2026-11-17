from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock

from openpilot.cereal import messaging
import zstandard as zstd

from openpilot.starpilot.models.catalog import BY_ID
from openpilot.starpilot.galaxy import drive_analysis
from openpilot.starpilot.galaxy.drive_analysis import analyze_route


ROUTE = '2026-09-25--12-00-00'
BASE = 1_000_000_000


def event(kind, stamp, **fields):
  alerts = fields.pop('onroadEvents', None)
  message = messaging.new_message(kind, size=len(alerts), valid=True) if alerts is not None else messaging.new_message(kind, valid=True)
  message.logMonoTime = stamp
  payload = getattr(message, kind)
  if alerts is not None:
    for item, alert in zip(payload, alerts, strict=True):
      item.name = alert['name']
  for key, value in fields.items():
    setattr(payload, key, value)
  return message


def write(root, number, events, *, lock=False, kind='rlog'):
  name = f'{ROUTE}--{number}'
  segment = root / name
  segment.mkdir()
  (segment / f'{kind}.zst').write_bytes(zstd.compress(b''.join(item.as_reader().as_builder().to_bytes() for item in events)))
  if lock:
    (segment / 'rlog.lock').touch()
  return {'number': number, 'segmentName': name, 'files': {kind: True}}


def selection(*segments):
  return {'routeId': ROUTE, 'segmentCount': len(segments), 'segments': list(segments)}


class DriveAnalysisTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name).resolve()

  def sample(self, stamp, speed, enabled, alerts=()):
    return [event('carState', stamp, vEgo=speed), event('selfdriveState', stamp, enabled=enabled),
            event('onroadEvents', stamp, onroadEvents=[{'name': name} for name in alerts])]

  def test_aol_counts_control_activity_separately_across_segments(self):
    samples = []
    for index, (enabled, active) in enumerate(((False, True), (False, True), (True, True), (False, False), (False, False))):
      stamp = BASE + index * 500_000_000
      samples.append([*self.sample(stamp, 10, enabled),
                      event('carControl', stamp, enabled=enabled, latActive=active),
                      # An admitted axis is not proof of active control.
                      event('aolAxisState', stamp, lateralActive=True, desiredLateral=True)])
    first = [*samples[0], *samples[1], event('sentinel', BASE + 750_000_000, type='endOfSegment')]
    second = [*samples[2], *samples[3], *samples[4], event('sentinel', BASE + 2_000_000_000, type='endOfRoute')]
    result = analyze_route(self.root, selection(write(self.root, 0, first, kind='qlog'),
                                               write(self.root, 1, second, kind='qlog')), permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertEqual((result['aolSeconds'], result['aolPercent']), (1.0, 50.0))
    self.assertEqual((result['engagedSeconds'], result['engagedPercent']), (.5, 25.0))
    self.assertEqual(result['distanceMeters'], 20)

  def test_gapped_control_does_not_invalidate_existing_statistics(self):
    events = [item for index in range(5) for item in self.sample(BASE + index * 500_000_000, 10, True)]
    events += [event('carControl', BASE, enabled=False, latActive=True),
               event('carControl', BASE + 2_000_000_000, enabled=False, latActive=True),
               event('sentinel', BASE + 2_000_000_000, type='endOfRoute')]
    result = analyze_route(self.root, selection(write(self.root, 0, events)), permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertEqual(result['engagedSeconds'], 2)
    self.assertIsNone(result['aolSeconds'])
    self.assertIsNone(result['aolPercent'])

  def test_missing_qlog_end_marker_recovers_from_complete_rlog(self):
    samples = [*self.sample(BASE, 5, True), *self.sample(BASE + 500_000_000, 5, True)]
    segment = write(self.root, 0, samples, kind='qlog')
    full = [*samples, event('sentinel', BASE + 500_000_000, type='endOfRoute')]
    (self.root / segment['segmentName'] / 'rlog.zst').write_bytes(
      zstd.compress(b''.join(item.as_reader().as_builder().to_bytes() for item in full)))
    segment['files']['rlog'] = True
    result = analyze_route(self.root, selection(segment), permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertEqual(result['distanceMeters'], 2.5)

  def test_complete_contiguous_route_uses_observed_values_and_event_edges(self):
    first = [event('clocks', BASE, wallTimeNanos=1_790_334_400_000_000_000),
             *self.sample(BASE, 0, True),
             *self.sample(BASE + 500_000_000, 10, True, ('driverDistracted2',)),
             event('sentinel', BASE + 900_000_000, type='endOfSegment')]
    second = [*self.sample(BASE + 1_000_000_000, 10, False, ('driverDistracted2', 'driverUnresponsive3')),
              *self.sample(BASE + 1_500_000_000, 10, False),
              event('sentinel', BASE + 1_500_000_000, type='endOfRoute')]
    route = selection(write(self.root, 0, first), write(self.root, 1, second))
    result = analyze_route(self.root, route, permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertEqual(result['distanceMeters'], 12.5)
    self.assertEqual(result['durationSeconds'], 1.5)
    self.assertEqual(result['engagedSeconds'], 1)
    self.assertAlmostEqual(result['engagedPercent'], 66.7)
    self.assertEqual(result['distractedMoments'], 1)
    self.assertEqual(result['unresponsiveMoments'], 1)
    self.assertEqual(result['startTime'], 1_790_334_400)
    self.assertIsNone(result['model'])
    self.assertIsNone(result['aolSeconds'])
    self.assertIsNone(result['aolPercent'])

  def test_missing_sentinel_gap_and_noncontiguous_segments_are_incomplete(self):
    events = [*self.sample(BASE, 5, True), *self.sample(BASE + 2_000_000_000, 5, True)]
    route = selection(write(self.root, 0, events))
    result = analyze_route(self.root, route, permitted=lambda: True)
    self.assertFalse(result['complete'])
    self.assertIsNone(result['distanceMeters'])
    self.assertIn('End of route', result['reason'])
    route['segments'][0]['number'] = 1
    self.assertIn('missing segments', analyze_route(self.root, route, permitted=lambda: True)['reason'])

  def test_lock_and_added_segment_invalidate_inventory(self):
    first = write(self.root, 0, [*self.sample(BASE, 5, False), *self.sample(BASE + 500_000_000, 5, False),
                                 event('sentinel', BASE + 500_000_000, type='endOfRoute')], lock=True)
    result = analyze_route(self.root, selection(first), permitted=lambda: True)
    self.assertFalse(result['complete'])
    (self.root / first['segmentName'] / 'rlog.lock').unlink()
    write(self.root, 1, [event('sentinel', BASE + 1_000_000_000, type='endOfRoute')])
    result = analyze_route(self.root, selection(first), permitted=lambda: True)
    self.assertFalse(result['complete'])
    self.assertIn('segments changed', result['reason'])

  def test_cancellation_is_incomplete(self):
    first = write(self.root, 0, [*self.sample(BASE, 5, True), *self.sample(BASE + 500_000_000, 5, True),
                                 event('sentinel', BASE + 500_000_000, type='endOfRoute')])
    self.assertFalse(analyze_route(self.root, selection(first), permitted=lambda: False)['complete'])

  def test_qlog_without_attention_samples_preserves_unknown_moments(self):
    events = [event('sentinel', BASE, type='startOfRoute'),
              event('carState', BASE + 2_000_000_000, vEgo=5),
              event('selfdriveState', BASE + 2_000_000_000, enabled=True),
              event('carState', BASE + 2_500_000_000, vEgo=5),
              event('selfdriveState', BASE + 2_500_000_000, enabled=False),
              event('sentinel', BASE + 3_000_000_000, type='endOfRoute')]
    route = selection(write(self.root, 0, events, kind='qlog'))
    result = analyze_route(self.root, route, permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertEqual(result['distanceMeters'], 2.5)
    self.assertEqual(result['engagedSeconds'], .5)
    self.assertIsNotNone(result['startTime'])
    self.assertIsNone(result['distractedMoments'])
    self.assertIsNone(result['unresponsiveMoments'])

  def test_model_is_only_the_selection_recorded_in_init_data(self):
    init = messaging.new_message('initData', valid=True)
    init.logMonoTime = BASE
    entries = init.initData.init('params').init('entries', 1)
    entries[0].key = 'DrivingModelName'
    entries[0].value = b'Recorded choice'
    events = [init, event('carState', BASE, vEgo=5), event('selfdriveState', BASE, enabled=False),
              event('carState', BASE + 500_000_000, vEgo=5),
              event('selfdriveState', BASE + 500_000_000, enabled=False),
              event('sentinel', BASE + 500_000_000, type='endOfRoute')]
    route = selection(write(self.root, 0, events))
    result = analyze_route(self.root, route, permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertEqual(result['model'], 'Selected: Recorded choice')

  def model_events(self, big=False):
    return [*self.sample(BASE, 5, True), event('drivingModelData', BASE + 100_000_000, big=big),
            *self.sample(BASE + 500_000_000, 5, True), event('sentinel', BASE + 500_000_000, type='endOfRoute')]

  def load_event(self, model_id='sc23', big=False, stamp=BASE):
    message = messaging.new_message(None, valid=True)
    message.logMonoTime = stamp
    message.logMessage = json.dumps({'process': 123, 'msg': {'event': 'modeld.loaded', 'version': 1,
      'pid': 123, 'processStartTicks': 17, 'loadedMonoNs': stamp, 'modelId': model_id,
      'variant': 'chestnut' if big else 'small', 'artifactSha256': 'a' * 64, 'fallbackReason': None}})
    return message

  def test_qlog_reports_actual_small_fallback_without_a_recorded_identity(self):
    events = self.model_events()
    result = analyze_route(self.root, selection(write(self.root, 0, events, kind='qlog')), permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertEqual(result['model'], 'Small model (identity not recorded)')
    self.assertEqual(result['analysisVersion'], drive_analysis.ANALYSIS_VERSION)

  def test_retained_rlog_supplies_verified_identity_while_qlog_supplies_stats(self):
    events = self.model_events()
    segment = write(self.root, 0, events, kind='qlog')
    rlog = self.root / segment['segmentName'] / 'rlog.zst'
    rlog.write_bytes(zstd.compress(b''.join(item.as_reader().as_builder().to_bytes() for item in [self.load_event(), *events])))
    segment['files']['rlog'] = True
    result = analyze_route(self.root, selection(segment), permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertEqual(result['model'], f'{BY_ID["sc23"].name} (Small)')
    rlog.write_bytes(b'corrupt optional metadata')
    result = analyze_route(self.root, selection(segment), permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertEqual(result['model'], 'Small model (identity not recorded)')

  def test_model_fallback_and_restart_do_not_keep_prior_identity(self):
    events = [self.load_event('cinquev3', True), *self.sample(BASE, 5, True),
              event('drivingModelData', BASE + 50_000_000, big=True),
              self.load_event(stamp=BASE + 100_000_000), event('drivingModelData', BASE + 200_000_000, big=False),
              *self.sample(BASE + 500_000_000, 5, True), event('sentinel', BASE + 500_000_000, type='endOfRoute')]
    segment = write(self.root, 0, events)
    result = analyze_route(self.root, selection(segment), permitted=lambda: True)
    self.assertEqual(result['model'], f'{BY_ID["cinquev3"].name} (Chestnut big) → {BY_ID["sc23"].name} (Small)')
    restart = messaging.new_message(None, valid=True)
    restart.logMonoTime = BASE + 150_000_000
    restart.logMessage = json.dumps({'msg': 'modeld init', 'module': 'modeld', 'process': 124})
    events.insert(6, restart)
    (self.root / segment['segmentName'] / 'rlog.zst').write_bytes(zstd.compress(b''.join(item.as_reader().as_builder().to_bytes() for item in events)))
    result = analyze_route(self.root, selection(segment), permitted=lambda: True)
    self.assertIn('Small model (identity not recorded)', result['model'])
    self.assertNotIn(f'{BY_ID["sc23"].name} (Small)', result['model'])

  def test_failed_or_unconfirmed_load_never_overrides_actual_output(self):
    events = [self.load_event('cinquev3', True), *self.model_events()]
    result = analyze_route(self.root, selection(write(self.root, 0, events)), permitted=lambda: True)
    self.assertEqual(result['model'], 'Small model (identity not recorded)')
    with mock.patch.object(drive_analysis, 'MAX_MODEL_METADATA_SECONDS', 0):
      result = analyze_route(self.root, selection({'number': 0, 'segmentName': f'{ROUTE}--0', 'files': {'rlog': True}}),
                             permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertEqual(result['model'], 'Small model (identity not recorded)')

  def test_sample_gap_and_missing_wall_time_cannot_be_complete(self):
    events = [event('carState', BASE, vEgo=10), event('selfdriveState', BASE, enabled=True),
              event('carState', BASE + 2_000_000_000, vEgo=10),
              event('selfdriveState', BASE + 2_000_000_000, enabled=True),
              event('sentinel', BASE + 2_000_000_000, type='endOfRoute')]
    segment = write(self.root, 0, events, kind='qlog')
    gap = analyze_route(self.root, selection(segment), permitted=lambda: True)
    self.assertFalse(gap['complete'])
    self.assertIsNone(gap['distanceMeters'])
    self.assertIsNone(gap['engagedSeconds'])
    self.assertIn('gapped', gap['reason'])

    old_name = segment['segmentName']
    no_date = selection(segment)
    no_date['routeId'] = '00000231--8c5f1c4f8b'
    no_date['segments'][0]['segmentName'] = '00000231--8c5f1c4f8b--0'
    (self.root / old_name).rename(self.root / no_date['segments'][0]['segmentName'])
    (self.root / no_date['segments'][0]['segmentName'] / 'qlog.zst').write_bytes(zstd.compress(b''.join(
      item.as_reader().as_builder().to_bytes() for item in [event('carState', BASE, vEgo=10), event('selfdriveState', BASE, enabled=True),
                                    event('carState', BASE + 500_000_000, vEgo=10),
                                    event('selfdriveState', BASE + 500_000_000, enabled=False),
                                    event('sentinel', BASE + 500_000_000, type='endOfRoute')])))
    result = analyze_route(self.root, no_date, permitted=lambda: True)
    self.assertFalse(result['complete'])
    self.assertIsNone(result['startTime'])
    self.assertIn('wall time', result['reason'])

  def test_129_segment_qlog_route_and_source_guards(self):
    segments = []
    for number in range(129):
      events = []
      for sample in range(67):
        stamp = BASE + number * 60_000_000_000 + sample * 900_000_000
        events.extend([event('carState', stamp, vEgo=10), event('selfdriveState', stamp, enabled=True)])
      if number == 128:
        events.append(event('sentinel', stamp, type='endOfRoute'))
      segments.append(write(self.root, number, events, kind='qlog'))
    route = selection(*segments)
    real_open = drive_analysis.os.open
    real_close = drive_analysis.os.close
    opened = set()
    peak = [0]
    def tracking_open(*args, **kwargs):
      fd = real_open(*args, **kwargs)
      opened.add(fd)
      peak[0] = max(peak[0], len(opened))
      return fd
    def tracking_close(fd):
      opened.discard(fd)
      return real_close(fd)
    with mock.patch.object(drive_analysis.os, 'open', side_effect=tracking_open), \
         mock.patch.object(drive_analysis.os, 'close', side_effect=tracking_close):
      result = analyze_route(self.root, route, permitted=lambda: True)
    self.assertTrue(result['complete'], result)
    self.assertLessEqual(peak[0], 3)
    self.assertFalse(opened)
    self.assertEqual(result['segmentCount'], 129)
    self.assertEqual(result['distanceMeters'], 77394)
    self.assertEqual(result['durationSeconds'], 7739.4)
    self.assertEqual(result['engagedSeconds'], 7739.4)

    middle = self.root / segments[64]['segmentName']
    original = (middle / 'qlog.zst').read_bytes()
    (middle / 'qlog.zst').unlink()
    middle.rmdir()
    self.assertFalse(analyze_route(self.root, route, permitted=lambda: True)['complete'])
    middle.mkdir()
    (middle / 'qlog.zst').write_bytes(original)
    (middle / 'qlog.lock').touch()
    self.assertFalse(analyze_route(self.root, route, permitted=lambda: True)['complete'])
    (middle / 'qlog.lock').unlink()

    first_log = self.root / segments[0]['segmentName'] / 'qlog.zst'
    decode = drive_analysis.decode_segment
    changed = [False]
    def changing_decode(*args, **kwargs):
      if not changed[0]:
        changed[0] = True
        first_log.write_bytes(first_log.read_bytes())
      return decode(*args, **kwargs)
    with mock.patch.object(drive_analysis, 'decode_segment', side_effect=changing_decode):
      result = analyze_route(self.root, route, permitted=lambda: True)
    self.assertFalse(result['complete'])
    self.assertIn('source changed', result['reason'].lower())


if __name__ == '__main__':
  unittest.main()
