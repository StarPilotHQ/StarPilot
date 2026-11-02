import bz2
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import zstandard
from openpilot.cereal import log
from tools.heatdeath.local_rlog import local_events


class TestLocalRlog(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.root = Path(self.directory.name)
    self.addCleanup(self.directory.cleanup)
    self.samples = []
    for index in range(3):
      event = log.Event.new_message(logMonoTime=100 + index, valid=True)
      event.init('carState')
      event.carState.vEgo = float(index)
      self.samples.append(event.to_bytes())

  def test_supported_formats_preserve_every_event(self):
    payload = b''.join(self.samples)
    for name, encoded in (('rlog', payload), ('rlog.zst', zstandard.compress(payload)), ('rlog.bz2', bz2.compress(payload))):
      with self.subTest(name=name):
        path = self.root / name
        path.write_bytes(encoded)
        with local_events(path) as events:
          actual = [event.as_builder().to_bytes() for event in events]
        self.assertEqual(actual, self.samples)

  def test_compressed_magic_without_extension(self):
    path = self.root / 'rlog'
    path.write_bytes(zstandard.compress(b''.join(self.samples)))
    with local_events(path) as events:
      self.assertEqual([event.as_builder().to_bytes() for event in events], self.samples)

  def test_decoder_is_reaped_after_consumer_failure(self):
    path = self.root / 'rlog.zst'
    path.write_bytes(zstandard.compress(b''.join(self.samples)))
    original = subprocess.Popen
    children = []
    def launch(*args, **kwargs):
      child = original(*args, **kwargs)
      children.append(child)
      return child
    with (patch('tools.heatdeath.local_rlog.subprocess.Popen', side_effect=launch),
          self.assertRaisesRegex(RuntimeError, 'consumer failed'), local_events(path) as events):
      next(events)
      raise RuntimeError('consumer failed')
    self.assertEqual(len(children), 1)
    self.assertIsNotNone(children[0].poll())
    self.assertTrue(children[0].stdout.closed)


if __name__ == '__main__':
  unittest.main()
