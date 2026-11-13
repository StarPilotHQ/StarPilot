import numpy as np
import pytest

from openpilot.starpilot.engine_sound.tools.make_bank import SAMPLE_RATE, resample, seamless


def test_resample_keeps_pitch_and_level():
  t = np.arange(44100) / 44100
  out = resample(np.sin(2 * np.pi * 150 * t), 44100)
  assert len(out) == SAMPLE_RATE
  spectrum = np.abs(np.fft.rfft(out))
  assert np.argmax(spectrum) * SAMPLE_RATE / len(out) == pytest.approx(150, abs=1)
  assert np.sqrt(np.mean(out[1000:-1000] ** 2)) == pytest.approx(np.sqrt(0.5), rel=0.02)
  same = np.random.default_rng(0).standard_normal(480)
  assert resample(same, SAMPLE_RATE) is same


def test_loop_is_whole_cycles_and_wraps_without_a_jump():
  rpm = 3000.0
  t = np.arange(4 * SAMPLE_RATE) / SAMPLE_RATE
  rng = np.random.default_rng(1)
  wobble = np.cumsum(rng.standard_normal(len(t))) * 1e-4
  recording = np.sin(2 * np.pi * (rpm / 120 * 3) * t + wobble) + 0.05 * rng.standard_normal(len(t))
  loop = seamless(recording, rpm)
  cycle = 120 / rpm * SAMPLE_RATE
  assert len(loop) / cycle == pytest.approx(round(len(loop) / cycle), abs=0.01)
  steps = np.abs(np.diff(loop))
  assert abs(loop[0] - loop[-1]) <= np.percentile(steps, 99.5)


def test_too_short_recording_is_refused():
  with pytest.raises(ValueError):
    seamless(np.zeros(1000), 1000.0)
