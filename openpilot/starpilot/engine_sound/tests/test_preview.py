import os
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.starpilot.engine_sound import preview
from openpilot.starpilot.engine_sound.daemon import BLOCK_BYTES, BLOCK_SECONDS, Rev
from openpilot.starpilot.engine_sound.profiles import PROFILES


def test_rev_script_stays_parked_reaches_full_pedal_and_ends_at_idle():
  script = preview.rev_script()
  assert 4.0 < len(script) * BLOCK_SECONDS < 7.0
  assert all(not in_gear and speed == 0.0 for _, speed, _, in_gear in script)
  assert max(pedal for pedal, *_ in script) == 1.0
  assert all(pedal == 0.0 for pedal, *_ in script[-100:])


@pytest.mark.parametrize('name', sorted(PROFILES))
def test_rendered_preview_revs_and_settles(name):
  blocks = preview.render(name, 0.8)
  assert len(blocks) == len(preview.rev_script()) and all(len(block) == BLOCK_BYTES for block in blocks)
  level = [np.abs(np.frombuffer(block, dtype='<i2')).max() for block in blocks]
  assert max(level) > 8000
  assert level[-1] < max(level)


class Writer:
  def __init__(self, fail_at=None):
    self.written, self.drained, self.closed, self.fail_at = [], False, False, fail_at
  def write(self, block):
    if len(self.written) == self.fail_at:
      raise RuntimeError('closed')
    self.written.append(block)
  def drain(self): self.drained = True
  def close(self): self.closed = True


def test_car_playback_writes_everything_then_drains_and_closes():
  writer, blocks = Writer(), preview.render('race_car', 0.5)
  assert preview.play_car(writer, blocks, lambda: True)
  assert writer.written == blocks and writer.drained and writer.closed


def test_car_playback_stops_when_parking_is_lost_or_the_output_closes():
  blocks = preview.render('diesel', 0.5)
  allowed = iter([True, True, False])
  writer = Writer()
  assert not preview.play_car(writer, blocks, lambda: next(allowed))
  assert len(writer.written) == 2 * preview.CHECK_BLOCKS and writer.closed and not writer.drained
  writer = Writer(fail_at=3)
  with pytest.raises(RuntimeError):
    preview.play_car(writer, blocks, lambda: True)
  assert writer.closed


class Audio:
  def __init__(self):
    self.opened, self.frames = [], []
  def OutputStream(self, **kwargs):
    self.opened.append(kwargs)
    audio = self
    class Stream:
      def __enter__(self): return self
      def __exit__(self, *exc): return False
      def write(self, data): audio.frames.append(data)
    return Stream()


def test_comma_speaker_fallback_plays_mono_int16_at_the_synth_rate():
  audio, blocks = Audio(), preview.render('space_ship', 0.5)
  assert preview.play_comma(blocks, lambda: True, audio)
  assert audio.opened == [{'channels': 1, 'samplerate': 48000, 'dtype': 'int16'}]
  assert len(audio.frames) == len(blocks) and audio.frames[0].dtype == np.dtype('<i2')
  audio = Audio()
  assert not preview.play_comma(blocks, lambda: False, audio)
  assert audio.frames == []


def test_start_spawns_the_preview_detached_with_the_bundled_libraries_and_reaps_it(monkeypatch):
  from openpilot.starpilot.soloist import backend
  monkeypatch.setattr(backend, 'library_path', lambda inherited='': 'BUNDLE' + (':' + inherited if inherited else ''))
  calls = []
  process = SimpleNamespace(wait=lambda: calls.append('waited'))
  def spawn(argv, **kwargs):
    calls.append((argv, kwargs))
    return process
  assert preview.start(spawn)
  (argv, kwargs), *_ = calls
  assert argv == [sys.executable, '-m', 'openpilot.starpilot.engine_sound.preview']
  assert kwargs['env']['LD_LIBRARY_PATH'].startswith('BUNDLE') and kwargs['start_new_session']
  assert kwargs['stdin'] is subprocess.DEVNULL and kwargs['stdout'] is subprocess.DEVNULL
  def broken(*_args, **_kwargs):
    raise OSError('no python')
  assert not preview.start(broken)


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='flock is POSIX only')
def test_newest_preview_takes_the_lock_and_records_its_pid(tmp_path):
  import fcntl
  path = tmp_path / 'preview.lock'
  first = preview.take_over(path)
  assert first is not None and path.read_text() == str(os.getpid())
  clock = iter(float(i) for i in range(100))
  assert preview.take_over(path, clock=lambda: next(clock), sleep=lambda _: None) is None
  fcntl.flock(first, fcntl.LOCK_UN)
  os.close(first)
  second = preview.take_over(path)
  assert second is not None
  os.close(second)


def test_onroad_rev_plays_only_while_parked_and_yields_to_the_pedal():
  rev, script = Rev(), preview.rev_script()
  assert not rev.start(script, 0.0, 5.0, False)
  assert not rev.start(script, 0.0, 0.0, True)
  assert not rev.start(script, 0.3, 0.0, False)
  assert rev.step(0.0, 0.0, 0.0, False) == (0.0, 0.0, 0.0, False)
  assert rev.start(script, 0.0, 0.0, False)
  played = [rev.step(0.0, 0.0, 0.0, False) for _ in script]
  assert played == script
  assert rev.step(0.0, 0.0, 0.0, False) == (0.0, 0.0, 0.0, False)
  assert rev.start(script, 0.0, 0.0, False)
  rev.step(0.0, 0.0, 0.0, False)
  assert rev.step(0.4, 0.0, 0.0, False) == (0.4, 0.0, 0.0, False)
  assert rev.step(0.0, 0.0, 0.0, False) == (0.0, 0.0, 0.0, False)


def test_galaxy_rev_button_runs_the_engine_preview_and_reports_where_it_played(monkeypatch):
  audio_preview = pytest.importorskip('openpilot.starpilot.audio.preview')
  monkeypatch.setattr(preview, 'environment', lambda: {'ENV': '1'})
  for code, output in ((0, 'car'), (3, 'comma'), (4, 'engine')):
    runs = []
    runner = lambda argv, **kwargs: runs.append((argv, kwargs)) or SimpleNamespace(returncode=code)  # noqa: B023
    result = audio_preview.AlertPreview(lambda: False, runner).play({'key': 'EngineSoundProfile'})
    assert result == {'played': True, 'output': output}
    assert runs[0][0][-2:] == ['-m', 'openpilot.starpilot.engine_sound.preview'] and runs[0][1]['env'] == {'ENV': '1'}
  for outcome in (SimpleNamespace(returncode=2), SimpleNamespace(returncode=1), subprocess.TimeoutExpired('preview', 15)):
    def runner(argv, outcome=outcome, **kwargs):
      if isinstance(outcome, Exception):
        raise outcome
      return outcome
    player = audio_preview.AlertPreview(lambda: True, runner)
    with pytest.raises(audio_preview.AlertPreviewError):
      player.play({'key': 'EngineSoundVolume'})
    assert player.lock.acquire(blocking=False)
