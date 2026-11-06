from contextlib import contextmanager
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import Mock

from openpilot.starpilot.audio.alert_volume import SPECS
from openpilot.starpilot.audio.preview import ALERTS, AlertPreview, AlertPreviewError, play_saved_alert


class PreviewTests(unittest.TestCase):
  def test_all_volume_rows_have_a_real_alert(self):
    self.assertEqual(set(ALERTS), set(SPECS))

  def test_saved_key_only_and_offroad_authorization(self):
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    preview = AlertPreview(lambda: False, runner)
    for body in ({}, {'key': []}, {'key': 'bad'}, {'key': 'EngageVolume', 'volume': 100}):
      with self.assertRaises(AlertPreviewError):
        preview.play(body)
    with self.assertRaises(AlertPreviewError):
      preview.play({'key': 'EngageVolume'})
    runner.assert_not_called()

  def test_preview_uses_fixed_argv_and_reaps_with_bounded_run(self):
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    preview = AlertPreview(lambda: True, runner)
    for key in SPECS:
      self.assertEqual(preview.play({'key': key}), {'played': True})
      args, kwargs = runner.call_args
      self.assertEqual(args[0][-2:], ['openpilot.starpilot.audio.preview', key])
      self.assertEqual(kwargs['timeout'], 8)
      self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
    self.assertTrue(preview.lock.acquire(blocking=False))
    with self.assertRaises(AlertPreviewError):
      preview.play({'key': 'EngageVolume'})
    preview.lock.release()

  def test_errors_release_preview_ownership(self):
    for outcome in (SimpleNamespace(returncode=2), SimpleNamespace(returncode=1), subprocess.TimeoutExpired('preview', 8)):
      runner = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
      preview = AlertPreview(lambda: True, runner)
      with self.assertRaises(AlertPreviewError):
        preview.play({'key': 'EngageVolume'})
      self.assertTrue(preview.lock.acquire(blocking=False))
      preview.lock.release()

  def test_worker_uses_normal_sound_stream_and_stops_when_offroad_evidence_is_lost(self):
    for revoked in (False, True):
      self._worker_stream_case(revoked)

  def _worker_stream_case(self, revoked):
    clock = [0.0]
    stream = []
    context = SimpleNamespace(offroad=lambda: not revoked or clock[0] < .1, evidence_wait_ms=750)
    params = SimpleNamespace(get_bool=lambda _: False)

    @contextmanager
    def audio_stream(_):
      stream.append('opened')
      try:
        yield
      finally:
        stream.append('closed')

    sound = SimpleNamespace(refresh_saved_volumes=Mock(), update_alert=Mock(),
      loaded_sounds={7: [0] * 48000}, get_stream=audio_stream, update_critical_sound=Mock(), native_audio=None)
    def sleep(seconds):
      clock[0] += seconds
    def call():
      return play_saved_alert('EngageVolume', params=params, context=context, sound=sound, audio=object(),
                              alert=7, now=lambda: clock[0], sleep=sleep)
    if revoked:
      with self.assertRaises(AlertPreviewError):
        call()
    else:
      call()
    self.assertEqual(stream, ['opened', 'closed'])
    sound.update_alert.assert_called_once_with(7)
    sound.refresh_saved_volumes.assert_called_once()
    self.assertLessEqual(clock[0], 1.05)

  def test_driver_camera_prevents_any_audio_stream(self):
    sound = SimpleNamespace(get_stream=Mock())
    with self.assertRaises(AlertPreviewError):
      play_saved_alert('EngageVolume', params=SimpleNamespace(get_bool=lambda _: True),
                       context=SimpleNamespace(offroad=lambda: True), sound=sound, audio=None, alert=1)
    sound.get_stream.assert_not_called()

  def test_state_is_rechecked_after_initializing_the_audio_stream(self):
    permissions = iter((True, True, False))
    stream = Mock()
    sound = SimpleNamespace(refresh_saved_volumes=Mock(), update_alert=Mock(),
      loaded_sounds={7: [0] * 48000}, get_stream=Mock(return_value=stream))
    with self.assertRaises(AlertPreviewError):
      play_saved_alert('EngageVolume', params=SimpleNamespace(get_bool=lambda _: False),
                       context=SimpleNamespace(offroad=lambda: next(permissions)), sound=sound, audio=None, alert=7)
    stream.close.assert_called_once()
