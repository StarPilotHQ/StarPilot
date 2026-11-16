"""Disposable local segment trees only; no log decoding or cloud access."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from openpilot.starpilot.galaxy import drive_history


class DriveHistoryTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name) / 'logs'
    self.root.mkdir()
    self.reader = drive_history.DriveHistory(self.root)

  def segment(self, name: str, *files: str) -> Path:
    path = self.root / name
    path.mkdir()
    for filename in files:
      (path / filename).write_bytes(b'x')
    return path

  def test_current_local_legacy_and_downloaded_names_without_invented_stats(self):
    current = '0000021e--371eaf116b'
    self.segment(current + '--0', 'rlog.zst', 'qlog.zst', 'fcamera.hevc')
    self.segment(current + '--2', 'qlog.bz2', 'dcamera.hevc')
    self.segment('2021-02-03--04-05-06--1', 'qlog.zst')
    self.segment('8bc6f0b166ab516e_0000021e--371eaf116b--3', 'rlog.bz2')
    output = self.reader.snapshot()
    self.assertEqual(output['source'], 'local')
    self.assertTrue(output['partialHistory'])
    self.assertFalse(output['scanIncomplete'])
    by_id = {route['routeId']: route for route in output['routes']}
    self.assertEqual(set(by_id), {current, '2021-02-03--04-05-06',
                                  '8bc6f0b166ab516e|0000021e--371eaf116b'})
    self.assertEqual(by_id[current]['segmentCount'], 2)
    self.assertEqual([s['number'] for s in by_id[current]['segments']], [0, 2])
    self.assertTrue(by_id[current]['segments'][0]['files']['rlog'])
    self.assertFalse(by_id[current]['segments'][1]['files']['rlog'])
    self.assertTrue(by_id[current]['segments'][1]['files']['dcamera'])
    self.assertEqual(by_id[current]['segments'][0]['segmentName'], current + '--0')
    self.assertEqual(by_id['8bc6f0b166ab516e|0000021e--371eaf116b']['segments'][0]['segmentName'],
                     '8bc6f0b166ab516e_0000021e--371eaf116b--3')
    self.assertNotIn('duration', str(output).lower())
    self.assertNotIn('distance', str(output).lower())

  def test_recording_dates_and_connect_links_keep_observed_device_identity(self):
    local = '0000021e--371eaf116b'
    saved = self.segment(local + '--0', 'qlog.zst')
    os.utime(saved / 'qlog.zst', (1760000000, 1760000000))
    self.segment('0123456789abcdef_' + local + '--1', 'rlog.zst')
    result = drive_history.recording_details(self.reader.snapshot(), {local: 1759999900}, 'fedcba9876543210')
    routes = {route['routeId']: route for route in result['routes']}
    self.assertEqual(routes[local]['startTime'], 1759999900)
    self.assertEqual(routes[local]['fileTime'], 1760000000)
    self.assertEqual(routes[local]['connectUrl'], 'https://connect.comma.ai/fedcba9876543210/' + local)
    downloaded = routes['0123456789abcdef|' + local]
    self.assertIsNone(downloaded['startTime'])
    self.assertEqual(downloaded['connectUrl'], 'https://connect.comma.ai/0123456789abcdef/' + local)
    invalid = drive_history.recording_details(self.reader.snapshot(), {}, '../not-a-device')
    self.assertIsNone(next(route for route in invalid['routes'] if route['routeId'] == local)['connectUrl'])

  def test_active_empty_symlink_and_unrecognized_entries_are_excluded(self):
    self.segment('00000001--aaaaaaaaaa--0', 'rlog.zst', 'rlog.lock')
    self.segment('00000002--bbbbbbbbbb--0', 'unrelated.txt')
    valid = self.segment('00000003--cccccccccc--0', 'qlog.zst')
    (valid / 'fcamera.hevc').symlink_to(self.root / 'outside.hevc')
    (self.root / '00000004--dddddddddd--0').symlink_to(valid, target_is_directory=True)
    result = self.reader.snapshot()
    self.assertEqual([r['routeId'] for r in result['routes']], ['00000003--cccccccccc'])
    self.assertFalse(result['routes'][0]['segments'][0]['files']['fcamera'])
    self.assertTrue(result['scanIncomplete'])  # matching symlink directory cannot be inventoried

  def test_disappearing_file_skips_uncertain_segment(self):
    segment = self.segment('00000001--aaaaaaaaaa--0', 'rlog.zst')
    original = os.stat
    removed = False
    def disappearing(path, *args, **kwargs):
      nonlocal removed
      if path == 'rlog.zst' and kwargs.get('dir_fd') is not None and not removed:
        removed = True
        (segment / 'rlog.zst').unlink()
      return original(path, *args, **kwargs)
    with mock.patch.object(drive_history.os, 'stat', side_effect=disappearing):
      result = self.reader.snapshot()
    self.assertEqual(result['routes'], [])
    self.assertTrue(result['scanIncomplete'])

  def test_root_and_segment_scan_bounds_are_explicit(self):
    for n in range(4):
      self.segment(f'{n:08x}--aaaaaaaaaa--0', 'rlog.zst')
    with mock.patch.object(drive_history, 'MAX_ROOT_ENTRIES', 2):
      result = self.reader.snapshot()
    self.assertTrue(result['scanIncomplete'])
    self.assertLessEqual(sum(r['segmentCount'] for r in result['routes']), 2)
    too_many = self.segment('00000009--bbbbbbbbbb--0', 'qlog.zst')
    for n in range(4):
      (too_many / f'extra{n}').write_bytes(b'')
    with mock.patch.object(drive_history, 'MAX_SEGMENT_ENTRIES', 2):
      result = self.reader.snapshot()
    self.assertTrue(result['scanIncomplete'])
    self.assertNotIn('00000009--bbbbbbbbbb', [r['routeId'] for r in result['routes']])

  def test_default_includes_long_route_and_explicit_limit_remains_bounded(self):
    route_id = '2026-09-25--12-00-00'
    for number in range(129):
      self.segment(f'{route_id}--{number}', 'qlog.zst')
    ordinary = self.reader.snapshot()
    self.assertFalse(ordinary['scanIncomplete'])
    self.assertEqual(ordinary['routes'][0]['segmentCount'], 129)
    capped = drive_history.DriveHistory(self.root, max_segments_per_route=64).snapshot()
    self.assertTrue(capped['scanIncomplete'])
    self.assertEqual(capped['routes'][0]['segmentCount'], 64)
    with self.assertRaises(ValueError):
      drive_history.DriveHistory(self.root, max_segments_per_route=513)

  def test_stats_total_limit_keeps_competing_route_visible(self):
    long_id = '2026-09-25--12-00-00'
    short_id = '2026-09-26--12-00-00'
    for number in range(512):
      self.segment(f'{long_id}--{number}', 'qlog.zst')
    self.segment(f'{short_id}--0', 'qlog.zst')
    capped = drive_history.DriveHistory(self.root, max_segments_per_route=512).snapshot()
    self.assertTrue(capped['scanIncomplete'])
    self.assertEqual(sum(route['segmentCount'] for route in capped['routes']), 512)
    stats = drive_history.DriveHistory(self.root, max_segments_per_route=512, max_segments=2048).snapshot()
    self.assertFalse(stats['scanIncomplete'])
    self.assertEqual([(route['routeId'], route['segmentCount']) for route in stats['routes']],
                     [(long_id, 512), (short_id, 1)])
    for invalid in (True, 0, 2049):
      with self.assertRaises(ValueError):
        drive_history.DriveHistory(self.root, max_segments_per_route=512, max_segments=invalid)

  def test_explicit_route_budget_preserves_default_and_rejects_invalid_limits(self):
    for counter in range(101):
      self.segment(f'{counter:08x}--aaaaaaaaaa--0', 'rlog.zst')
    ordinary = self.reader.snapshot()
    self.assertTrue(ordinary['scanIncomplete'])
    self.assertEqual(len(ordinary['routes']), 100)
    expanded = drive_history.DriveHistory(self.root, max_routes=2048).snapshot()
    self.assertFalse(expanded['scanIncomplete'])
    self.assertEqual(len(expanded['routes']), 101)
    for invalid in (True, 0, -1, 2049):
      with self.assertRaises(ValueError):
        drive_history.DriveHistory(self.root, max_routes=invalid)

  def test_missing_root_is_empty_but_root_symlink_is_unavailable(self):
    self.assertEqual(drive_history.DriveHistory(self.root / 'missing').snapshot()['routes'], [])
    link = Path(self.temp.name) / 'linked'
    link.symlink_to(self.root, target_is_directory=True)
    with self.assertRaises(drive_history.DriveHistoryUnavailable):
      drive_history.DriveHistory(link).snapshot()

  def test_delete_videos_keeps_logs_and_refuses_open_or_foreign_segments(self):
    name = '0000021e--371eaf116b--0'
    path = self.segment(name, 'rlog.zst', 'qlog.zst', 'fcamera.hevc', 'ecamera.hevc', 'qcamera.ts')
    result = self.reader.delete_videos(name)
    self.assertEqual(result, {'segmentName': name, 'deleted': ['fcamera', 'ecamera', 'qcamera'], 'freedBytes': 3})
    self.assertEqual(sorted(p.name for p in path.iterdir()), ['qlog.zst', 'rlog.zst'])
    self.assertEqual(self.reader.delete_videos(name)['deleted'], [])

    open_segment = self.segment('0000021e--371eaf116b--1', 'rlog.lock', 'fcamera.hevc')
    with self.assertRaises(drive_history.SegmentInUse):
      self.reader.delete_videos(open_segment.name)
    self.assertTrue((open_segment / 'fcamera.hevc').exists())

    with self.assertRaises(drive_history.SegmentMissing):
      self.reader.delete_videos('0000021e--371eaf116b--9')
    for bad in ('../logs', '0000021e--371eaf116b', 'x/0000021e--371eaf116b--0', None):
      with self.assertRaises(ValueError):
        self.reader.delete_videos(bad)

    outside = Path(self.temp.name) / 'outside'
    outside.mkdir()
    (outside / 'fcamera.hevc').write_bytes(b'x')
    os.symlink(outside, self.root / '0000021e--371eaf116b--2')
    with self.assertRaises(OSError):
      self.reader.delete_videos('0000021e--371eaf116b--2')
    self.assertTrue((outside / 'fcamera.hevc').exists())

    linked = self.segment('0000021e--371eaf116b--3', 'rlog.zst')
    os.symlink(outside / 'fcamera.hevc', linked / 'qcamera.ts')
    self.assertEqual(self.reader.delete_videos(linked.name)['deleted'], [])
    self.assertTrue((outside / 'fcamera.hevc').exists())


if __name__ == '__main__':
  unittest.main()
