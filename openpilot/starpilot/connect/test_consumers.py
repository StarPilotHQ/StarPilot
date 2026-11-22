"""Actual cloud consumers exclude recordings owned by another provider."""

from pathlib import Path
from types import SimpleNamespace
from queue import Queue
import tempfile
import unittest
from unittest.mock import patch

from openpilot.starpilot.connect import provider
from openpilot.system.athena import athenad
from openpilot.system.loggerd import uploader


class TestCloudConsumers(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.root = Path(temporary.name)
    self.enterContext(patch.object(provider, 'active_provider', return_value=provider.PROVIDERS['konik']))
    self.enterContext(patch.object(athenad.Paths, 'log_root', return_value=str(self.root)))
    self.enterContext(patch.object(athenad, 'CLOUD_PROVIDER', provider.PROVIDERS['konik']))
    self.konik = self.root/'aaaaaaaa--bbbbbbbbbb--0'
    self.comma = self.root/'cccccccc--dddddddddd--0'
    for directory in (self.konik,self.comma):
      directory.mkdir()
      (directory/'qlog.zst').write_bytes(b'log')
      (directory/'fcamera.hevc').write_bytes(b'camera')
    provider.mark_recording(self.konik,'konik')

  def test_actual_uploader_and_athena_listing_keep_legacy_comma_out(self):
    with patch.object(uploader, 'Api', return_value=SimpleNamespace()), patch.object(uploader.os, 'nice') as nice:
      instance = uploader.Uploader('0'*16, str(self.root))
    nice.assert_called_once_with(10)
    with patch.object(uploader,'getxattr',return_value=None):
      files = list(instance.list_upload_files(False))
    self.assertTrue(files)
    self.assertTrue(all(Path(path).parent == self.konik for _,_,path in files))
    listed = athenad.scan_dir(str(self.root),'')
    self.assertIn(self.konik.name+'/qlog.zst',listed)
    self.assertNotIn(self.comma.name+'/qlog.zst',listed)

  def test_actual_athena_upload_queue_rejects_foreign_source(self):
    with (patch.object(athenad,'upload_queue',Queue()),
          patch.object(athenad.UploadQueueCache,'cache'),
          patch.object(athenad,'listUploadQueue',return_value=[])):
      result=athenad.uploadFilesToUrls([
        {'fn':self.comma.name+'/qlog.zst','url':'https://upload.invalid/comma','headers':{}},
        {'fn':self.konik.name+'/qlog.zst','url':'https://upload.invalid/konik','headers':{}},
      ])
    self.assertEqual(result['enqueued'],1)
    self.assertEqual(result['failed'],[self.comma.name+'/qlog.zst'])

  def clips(self):
    with patch.object(athenad.threading.Thread,'start'):
      return athenad.VideoClips()

  def test_clip_source_and_derived_directory_are_provider_owned(self):
    clips=self.clips()
    self.assertEqual(Path(clips.clip_path),self.root/'clips-konik')
    self.assertEqual(clips._available_ranges('cccccccc--dddddddddd'),{})
    own=clips._source_inputs('aaaaaaaa--bbbbbbbbbb','fcamera.hevc',0,1)
    self.assertEqual(own,[str(self.konik/'fcamera.hevc')])
    with self.assertRaises(ValueError):
      clips._source_inputs('cccccccc--dddddddddd','fcamera.hevc',0,1)
    provider.mark_recording(clips.clip_path,'konik')
    (Path(clips.clip_path)/'test.mp4').write_bytes(b'video')
    self.assertEqual(clips.getClipChunk('test.mp4',0)['size'],5)
    with patch.object(provider,'active_provider',return_value=provider.PROVIDERS['comma']):
      self.assertFalse(provider.owns_recording(Path(clips.clip_path)/'test.mp4',self.root))

  def test_existing_comma_clips_keep_legacy_path_and_foreign_marker_denied(self):
    with patch.object(athenad,'CLOUD_PROVIDER',provider.PROVIDERS['comma']):
      clips=self.clips()
    self.assertEqual(Path(clips.clip_path),self.root/'clips')
    self.assertEqual(clips._source_inputs('cccccccc--dddddddddd','fcamera.hevc',0,1),
                     [str(self.comma/'fcamera.hevc')])
    directory=self.root/'clips-konik'
    provider.mark_recording(directory,'konik')
    with self.assertRaises(ValueError):
      provider.mark_recording(directory,'comma')
