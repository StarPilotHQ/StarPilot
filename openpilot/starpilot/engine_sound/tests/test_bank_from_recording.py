import wave

import numpy as np

from openpilot.starpilot.engine_sound.tools.bank_from_recording import (LOOP_SECONDS, SAMPLE_RATE, read_recording, shuffled, stretch,
                                                                        steady, track, usable)


def v8_sweep(path, seconds=6.0, rate=44100):
  """Idle at 1000 rpm for 1.5 s, then a steady climb to 3000, as whole engine orders with the firing order loudest."""
  t = np.arange(int(seconds * rate)) / rate
  rpm = np.where(t < 1.5, 1000.0, 1000.0 + (t - 1.5) / (seconds - 1.5) * 2000.0)
  cycles = np.cumsum(rpm / 120.0 / rate)
  audio = sum((1.0 if order % 8 else 3.0) * np.sin(2 * np.pi * order * cycles) for order in range(2, 33, 2))
  audio = 0.5 * audio / np.abs(audio).max()
  stereo = np.repeat((audio * 32767).astype('<i2'), 2)
  with wave.open(str(path), 'wb') as out:
    out.setnchannels(2)
    out.setsampwidth(2)
    out.setframerate(rate)
    out.writeframes(stereo.tobytes())
  return t, rpm


def test_tracks_a_sweep_from_its_anchor_and_loops_play_at_the_bank_rpm(tmp_path):
  truth_t, truth_rpm = v8_sweep(tmp_path / 'v8.wav')
  audio = read_recording(tmp_path / 'v8.wav')
  assert abs(len(audio) / SAMPLE_RATE - 6.0) < 0.01
  times, rpm = track(audio, 8, [(1.0, 1000.0)], half_orders=False)
  expected = np.interp(times, truth_t, truth_rpm)
  inside = (times > 0.4) & (times < 5.6)
  assert np.max(np.abs(rpm[inside] / expected[inside] - 1)) < 0.03

  span = stretch(times, rpm, usable(times, rpm, [(1.5, 6.0)], 1), 2000.0)
  assert span is not None
  loop = steady(audio, times, rpm, span, 2000.0)
  assert loop is not None and len(loop) >= 0.25 * SAMPLE_RATE
  spectrum = np.abs(np.fft.rfft(loop * np.hanning(len(loop)), 1 << 20))
  peak = np.argmax(spectrum) * SAMPLE_RATE / (1 << 20)
  assert abs(peak / (2000.0 * 8 / 120) - 1) < 0.02
  assert abs(loop[0] - loop[-1]) <= np.percentile(np.abs(np.diff(loop)), 99) * 1.5


def test_a_short_stretch_becomes_a_full_loop_of_shuffled_whole_cycles():
  rpm = 1800.0
  cycle = 120.0 / rpm * SAMPLE_RATE
  t = np.arange(round(6 * cycle)) / SAMPLE_RATE
  rng = np.random.default_rng(0)
  audio = np.sin(2 * np.pi * 8 * rpm / 120 * t) + 0.2 * rng.standard_normal(len(t))
  loop = shuffled(audio, cycle, rpm)
  assert loop is not None and len(loop) >= LOOP_SECONDS * SAMPLE_RATE - cycle
  assert len(loop) % round(cycle) == 0
  spectrum = np.abs(np.fft.rfft(np.tile(loop, 2) * np.hanning(2 * len(loop)), 1 << 20))
  assert abs(np.argmax(spectrum) * SAMPLE_RATE / (1 << 20) / (8 * rpm / 120) - 1) < 0.01
  steps = np.abs(np.diff(np.append(loop, loop[0])))
  assert steps.max() < 4 * np.percentile(steps, 99)
  assert shuffled(audio[:round(3 * cycle)], cycle, rpm) is None
