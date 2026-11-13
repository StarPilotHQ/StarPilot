import json
import wave
from dataclasses import replace

import numpy as np
import pytest

from openpilot.starpilot.engine_sound.daemon import Engine, voice
from openpilot.starpilot.engine_sound.loops import BankError, LoopSynth, find_bank, load_bank
from openpilot.starpilot.engine_sound.profiles import PROFILES
from openpilot.starpilot.engine_sound.synth import SAMPLE_RATE, Synth

FIRING = 4  # partial that a four-cylinder bank fires at


def tone(rpm, seconds=0.5):
  cycles = round(seconds * rpm / 120)
  frames = round(cycles * 120 / rpm * SAMPLE_RATE)
  phase = np.arange(frames) / frames * cycles * FIRING / 2
  return 0.5 * np.sin(2 * np.pi * phase)


def write(path, samples, rate=SAMPLE_RATE, channels=1):
  with wave.open(str(path), 'wb') as out:
    out.setnchannels(channels)
    out.setsampwidth(2)
    out.setframerate(rate)
    out.writeframes((np.repeat(samples, channels) * 32767).astype('<i2').tobytes())


def make_bank(root, name='test', layers=(('on', (1000, 3000, 5000)), ('off', (1000, 3000, 5000))), level=1.0):
  directory = root / name
  directory.mkdir(parents=True)
  manifest = {'version': 1, 'layers': {}}
  for layer, points in layers:
    manifest['layers'][layer] = []
    for rpm in points:
      write(directory / f'{layer}_{rpm}.wav', tone(rpm) * (level if layer == 'on' else 0.3))
      manifest['layers'][layer].append({'rpm': rpm, 'file': f'{layer}_{rpm}.wav'})
  (directory / 'bank.json').write_text(json.dumps(manifest))
  return directory


def dominant_hz(samples):
  padded = 1 << 21
  spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples)), n=padded))
  return np.argmax(spectrum) * SAMPLE_RATE / padded


def test_bank_loads_sorted_layers_and_reuses_on_when_off_is_missing(tmp_path):
  bank = load_bank(make_bank(tmp_path, layers=(('on', (5000, 1000, 3000)),)))
  assert list(bank['on'][0]) == [1000, 3000, 5000]
  assert bank['off'] is bank['on']


@pytest.mark.parametrize('breakage', ['manifest', 'version', 'no_on', 'traversal', 'stereo', 'rate', 'duplicate', 'short'])
def test_bad_banks_are_refused(tmp_path, breakage):
  directory = make_bank(tmp_path)
  manifest = json.loads((directory / 'bank.json').read_text())
  if breakage == 'manifest':
    (directory / 'bank.json').write_text('{')
  elif breakage == 'version':
    manifest['version'] = 2
  elif breakage == 'no_on':
    del manifest['layers']['on']
  elif breakage == 'traversal':
    manifest['layers']['on'][0]['file'] = '../on_1000.wav'
  elif breakage == 'stereo':
    write(directory / 'on_1000.wav', tone(1000), channels=2)
  elif breakage == 'rate':
    write(directory / 'on_1000.wav', tone(1000), rate=44100)
  elif breakage == 'duplicate':
    manifest['layers']['on'][1]['rpm'] = 1000
  elif breakage == 'short':
    write(directory / 'on_1000.wav', tone(1000)[:100])
  if breakage != 'manifest':
    (directory / 'bank.json').write_text(json.dumps(manifest))
  with pytest.raises(BankError):
    load_bank(directory)


def test_pitch_follows_rpm_between_and_beyond_recorded_points(tmp_path):
  synth = LoopSynth(PROFILES['race_car'], load_bank(make_bank(tmp_path)))
  for rpm in (1000, 2000, 3000, 4200, 6000):
    for _ in range(10):
      synth.render(rpm, 1.0, 480)
    audio = np.concatenate([synth.render(rpm, 1.0, 480) for _ in range(40)])
    assert dominant_hz(audio) == pytest.approx(rpm / 120 * FIRING / 2, rel=0.03)


def test_render_is_continuous_bounded_and_load_blends_layers(tmp_path):
  synth = LoopSynth(PROFILES['race_car'], load_bank(make_bank(tmp_path)))
  blocks = [synth.render(1000 + 40 * i, min(i / 50, 1.0), 480) for i in range(100)]
  audio = np.concatenate(blocks)
  assert np.max(np.abs(audio)) <= 1.0
  steps = np.abs(np.diff(audio))
  assert steps[479::480].max() <= steps.max()
  for _ in range(20):
    synth.render(3000, 1.0, 480)
  loud = np.abs(synth.render(3000, 1.0, 480)).max()
  for _ in range(20):
    synth.render(3000, 0.0, 480)
  quiet = np.abs(synth.render(3000, 0.0, 480)).max()
  assert quiet < loud


def test_profiles_use_an_installed_bank_and_fall_back_to_the_synth(tmp_path):
  profile = replace(PROFILES['diesel'], loops='test')
  assert isinstance(voice(profile, tmp_path), Synth)
  make_bank(tmp_path)
  assert isinstance(voice(profile, tmp_path), LoopSynth)
  (tmp_path / 'test' / 'bank.json').write_text('{')
  assert isinstance(voice(profile, tmp_path), Synth)
  assert find_bank('../test', tmp_path) is None and find_bank('', tmp_path) is None


def test_engine_blocks_play_through_a_bank(tmp_path, monkeypatch):
  make_bank(tmp_path, name='race_bank')
  monkeypatch.setitem(PROFILES, 'race_car', replace(PROFILES['race_car'], loops='race_bank'))
  engine = Engine('race_car', bank_root=tmp_path)
  assert isinstance(engine.synth, LoopSynth)
  blocks = [engine.block(0.6, 0.0, 0.0, False, 1.0) for _ in range(100)]
  assert np.abs(np.frombuffer(blocks[-1], dtype='<i2')).max() > 3000
