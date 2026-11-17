"""Only C3 can expose a distinct recording drive, with actual available bytes."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpilot.starpilot.galaxy.system_monitor import SystemMonitor


class SystemMonitorStorageTest(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.root = Path(self.directory.name)
    (self.root / 'stat').write_text('cpu 100 0 0 100 0 0 0 0\n')
    (self.root / 'meminfo').write_text('MemTotal: 4096000 kB\nMemAvailable: 1024000 kB\n')
    (self.root / 'uptime').write_text('300 0\n')
    self.recordings = Path('/data/media/0/realdata')
    self.devices = {Path('/data'): 1, self.recordings: 2}
    self.sizes = {Path('/data'): (21, 30, 7.6), self.recordings: (598, 916, 273)}
    self.calls = []

  def usage(self, path):
    self.calls.append(path)
    used, total, free = self.sizes[path]
    return SimpleNamespace(used=used * 2 ** 30, total=total * 2 ** 30, free=free * 2 ** 30)

  def monitor(self, **kwargs):
    options = {'recording_storage': self.recordings, 'device_type': 'tici', 'disk_usage': self.usage,
               'stat': lambda path: SimpleNamespace(st_dev=self.devices[path])}
    return SystemMonitor(self.root, **(options | kwargs))

  def test_c3_added_drive_and_reserved_space(self):
    sample = self.monitor().sample()
    self.assertEqual(self.calls, [Path('/data'), self.recordings])
    self.assertEqual(sample['deviceType'], 'tici')
    self.assertEqual(sample['storage'], {'usedGiB': 21, 'totalGiB': 30, 'freeGiB': 7.6})
    self.assertEqual(sample['recordingStorage'], {'usedGiB': 598, 'totalGiB': 916, 'freeGiB': 273})
    self.assertNotEqual(sample['recordingStorage']['freeGiB'], 916 - 598)

  def test_c3_without_added_drive_and_unmount_never_duplicates_internal(self):
    monitor = self.monitor()
    self.assertIsNotNone(monitor.sample()['recordingStorage'])
    self.devices[self.recordings] = self.devices[Path('/data')]
    self.calls.clear()
    sample = monitor.sample()
    self.assertIsNone(sample['recordingStorage'])
    self.assertEqual(self.calls, [Path('/data')])
    self.assertEqual(sample['storage']['totalGiB'], 30)

  def test_other_hardware_never_probes_or_displays_added_drive(self):
    for model in ('tizi', 'mici', 'pc', 'unknown', 'tici2', ''):
      with self.subTest(model=model):
        stat = Mock(side_effect=AssertionError('Not a C3'))
        self.calls.clear()
        sample = self.monitor(device_type=model, stat=stat).sample()
        self.assertIsNone(sample['recordingStorage'])
        self.assertEqual(self.calls, [Path('/data')])
        stat.assert_not_called()

  def test_mount_change_during_sample_discards_recording_capacity(self):
    identities = iter((1, 2, 1, 1))
    def stat(_):
      return SimpleNamespace(st_dev=next(identities))
    self.assertIsNone(self.monitor(stat=stat).sample()['recordingStorage'])

  def test_unreadable_mount_identity_does_not_claim_a_drive(self):
    stat = Mock(side_effect=OSError('Unavailable filesystem'))
    self.assertIsNone(self.monitor(stat=stat).sample()['recordingStorage'])

  def test_storage_read_failures_are_independent_and_not_zero(self):
    for failed in (Path('/data'), self.recordings):
      with self.subTest(failed=failed):
        def usage(path, failed=failed):
          if path == failed:
            raise OSError('Unavailable filesystem')
          return self.usage(path)
        sample = self.monitor(disk_usage=usage).sample()
        bad = 'storage' if failed == Path('/data') else 'recordingStorage'
        self.assertEqual(sample[bad], {'usedGiB': None, 'totalGiB': None, 'freeGiB': None})
        good = 'recordingStorage' if bad == 'storage' else 'storage'
        self.assertIsNotNone(sample[good]['freeGiB'])

  def test_zero_free_space_is_not_unavailable(self):
    self.sizes[self.recordings] = (900, 916, 0)
    self.assertEqual(self.monitor().sample()['recordingStorage']['freeGiB'], 0)

  def test_hardware_identity_and_configured_recording_root(self):
    with patch('openpilot.starpilot.galaxy.system_monitor.HARDWARE.get_device_type', return_value='tici'), \
         patch.dict(os.environ, {'LOG_ROOT': '/custom/recordings'}):
      monitor = SystemMonitor()
      self.assertEqual(monitor.device_type, 'tici')
      self.assertEqual(monitor.recording_storage, Path('/custom/recordings'))
      self.assertEqual(SystemMonitor(recording_storage='/explicit/recordings').recording_storage,
                       Path('/explicit/recordings'))
