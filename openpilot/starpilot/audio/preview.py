"""Bounded, offroad-only previews through the normal sound owner."""

import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time

from openpilot.starpilot.audio.alert_volume import SPECS
from openpilot.starpilot.engine_sound.settings import PROFILE_KEY as ENGINE_PROFILE_KEY, VOLUME_KEY as ENGINE_VOLUME_KEY

ENGINE_PREVIEW_KEYS = (ENGINE_PROFILE_KEY, ENGINE_VOLUME_KEY)
ENGINE_EXIT_OUTPUTS = {0: 'car', 3: 'comma', 4: 'engine'}

ALERTS = {
  'WarningImmediateVolume': 'warningImmediate', 'WarningSoftVolume': 'warningSoft',
  'RefuseVolume': 'refuse', 'PromptDistractedVolume': 'promptDistracted',
  'EngageVolume': 'engage', 'DisengageVolume': 'disengage', 'PromptVolume': 'prompt',
}


class AlertPreviewError(Exception):
  def __init__(self, message, status=409):
    super().__init__(message)
    self.status = status


class AlertPreview:
  def __init__(self, parked, runner=subprocess.run):
    self.parked, self.runner = parked, runner
    self.lock = threading.Lock()

  def play(self, payload):
    if type(payload) is not dict or set(payload) != {'key'} or type(payload['key']) is not str or         payload['key'] not in (*SPECS, *ENGINE_PREVIEW_KEYS):
      raise AlertPreviewError('Choose an alert volume to preview.', 400)
    if payload['key'] in ENGINE_PREVIEW_KEYS:
      return self.play_engine()
    if not self.parked():
      raise AlertPreviewError('Put the comma offroad before testing an alert.')
    if not self.lock.acquire(blocking=False):
      raise AlertPreviewError('An alert preview is already playing.')
    try:
      result = self.runner([sys.executable, '-m', 'openpilot.starpilot.audio.preview', payload['key']],
                           cwd=Path(__file__).resolve().parents[3], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
      if result.returncode == 2:
        raise AlertPreviewError('Preview stopped. Keep the comma offroad and close Driver Camera.')
      if result.returncode:
        raise AlertPreviewError('The comma could not play this alert. Try again.', 503)
      return {'played': True}
    except subprocess.TimeoutExpired as error:
      raise AlertPreviewError('The preview timed out. Try again.', 503) from error
    finally:
      self.lock.release()


  def play_engine(self):
    from openpilot.starpilot.engine_sound import preview
    if not self.lock.acquire(blocking=False):
      raise AlertPreviewError('A preview is already playing.')
    try:
      if not preview.offroad():
        if not preview.request_rev():
          raise AlertPreviewError('The comma could not play the engine. Try again.', 503)
        return {'played': True, 'output': 'engine'}
      result = self.runner(preview.command(), cwd=preview.ROOT, env=preview.environment(), stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, **preview.spawn_options())
      if result.returncode in ENGINE_EXIT_OUTPUTS:
        return {'played': True, 'output': ENGINE_EXIT_OUTPUTS[result.returncode]}
      if result.returncode == 2:
        raise AlertPreviewError('Preview stopped. Stay parked and close Driver Camera, then try again.')
      raise AlertPreviewError('The comma could not play the engine. Try again.', 503)
    except subprocess.TimeoutExpired as error:
      raise AlertPreviewError('The preview timed out. Try again.', 503) from error
    finally:
      self.lock.release()


def play_saved_alert(key, *, params, context, sound, audio, alert, now=time.monotonic, sleep=time.sleep):
  if key not in ALERTS or not context.offroad() or params.get_bool('IsDriverViewEnabled'):
    raise AlertPreviewError('Offroad audio is unavailable.')
  context.evidence_wait_ms = 0
  sound.refresh_saved_volumes(now())
  sound.update_alert(alert)
  samples = sound.loaded_sounds[alert]
  duration = 2.0 if ALERTS[key] in ('warningSoft', 'warningImmediate', 'promptDistracted') else min(3.0, len(samples) / 48000)
  if not context.offroad():
    raise AlertPreviewError('Offroad audio is unavailable.')
  stream = sound.get_stream(audio)
  if not context.offroad() or params.get_bool('IsDriverViewEnabled'):
    stream.close()
    raise AlertPreviewError('Preview stopped before playback.')
  with stream:
    deadline = now() + duration
    while now() < deadline:
      if not context.offroad() or params.get_bool('IsDriverViewEnabled'):
        raise AlertPreviewError('Preview stopped.')
      sound.update_critical_sound(now())
      if sound.native_audio is not None:
        sound.native_audio.publish()
      sleep(.05)


def main():
  if len(sys.argv) != 2 or sys.argv[1] not in ALERTS:
    return 2
  from openpilot.common.params import Params
  from openpilot.starpilot.galaxy.settings import LiveContextSource
  params = Params()
  context = LiveContextSource(params)
  path = Path(tempfile.gettempdir()) / f'starpilot-alert-preview-{os.getuid()}.lock'
  descriptor = None
  try:
    if not context.offroad() or params.get_bool('IsDriverViewEnabled'):
      return 2
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
      return 2
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    from openpilot.selfdrive.ui.soundd import Soundd, AudibleAlert
    from openpilot.system import micd
    import sounddevice
    micd.patch_sounddevice(sounddevice)
    key = sys.argv[1]
    play_saved_alert(key, params=params, context=context, sound=Soundd(params), audio=sounddevice,
                     alert=getattr(AudibleAlert, ALERTS[key]))
    return 0
  except (AlertPreviewError, BlockingIOError):
    return 2
  finally:
    context.close()
    if descriptor is not None:
      os.close(descriptor)


if __name__ == '__main__':
  sys.exit(main())
