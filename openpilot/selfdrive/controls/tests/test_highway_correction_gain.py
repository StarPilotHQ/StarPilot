import math

import numpy as np

from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.highway_correction_gain import KEY, HighwayCorrectionGain, predicted_lateral_accels, read_gain


_V_HWY = 31.0


def _run(hcg, curvature, gain=0.7, v_ego=_V_HWY, lat_active=True, bypass=None, predicted=None):
  bypass = np.zeros(len(curvature), bool) if bypass is None else bypass
  predicted = [None] * len(curvature) if predicted is None else [tuple(p) for p in predicted]
  return np.array([hcg.update(float(c), v_ego, lat_active, gain, bool(b), predicted=p)
                   for c, b, p in zip(curvature, bypass, predicted, strict=True)])


def _lead(lat, seconds):
  n = int(seconds / DT_CTRL)
  ahead = np.concatenate([lat[n:], np.full(n, lat[-1])])
  return np.c_[ahead, ahead]


def _weave(seconds=30.0, freq=0.6, lat_accel_amp=0.08, v_ego=_V_HWY, bias=0.0):
  t = np.arange(0.0, seconds, DT_CTRL)
  return bias + (lat_accel_amp / v_ego ** 2) * np.sin(2 * math.pi * freq * t)


def _fit_sine(x, freq):
  t = np.arange(len(x)) * DT_CTRL
  basis = np.c_[np.sin(2 * math.pi * freq * t), np.cos(2 * math.pi * freq * t), np.ones(len(x))]
  (s, c, _), *_ = np.linalg.lstsq(basis, x, rcond=None)
  return math.hypot(s, c), math.atan2(c, s)


def test_gain_one_is_exact_pass_through():
  raw = _weave(bias=0.1 / _V_HWY ** 2)
  np.testing.assert_array_equal(_run(HighwayCorrectionGain(), raw, gain=1.0), raw)


def test_weave_band_scaled_by_gain_with_little_lag():
  for freq in (0.4, 0.6, 0.8):
    raw = _weave(freq=freq)
    out = _run(HighwayCorrectionGain(), raw, gain=0.7)
    tail = slice(len(raw) // 2, None)
    amp_in, ph_in = _fit_sine(raw[tail], freq)
    amp_out, ph_out = _fit_sine(out[tail], freq)
    assert 0.68 < amp_out / amp_in < 0.76, freq
    lag_deg = math.degrees((ph_in - ph_out + math.pi) % (2 * math.pi) - math.pi)
    assert 0.0 <= lag_deg < 8.0, (freq, lag_deg)


def test_steady_offset_passes_unchanged():
  bias = 0.15 / _V_HWY ** 2
  raw = np.full(int(30 / DT_CTRL), bias)
  out = _run(HighwayCorrectionGain(), raw, gain=0.5)
  np.testing.assert_allclose(out[-100:], bias, rtol=1e-6)


def test_steady_curve_wobble_is_smoothed():
  raw = _weave(seconds=40.0, bias=1.0 / _V_HWY ** 2)
  out = _run(HighwayCorrectionGain(), raw, gain=0.5)
  tail = slice(len(raw) // 2, None)
  amp_in, _ = _fit_sine(raw[tail], 0.6)
  amp_out, _ = _fit_sine(out[tail], 0.6)
  assert 0.45 < amp_out / amp_in < 0.62
  assert abs(out[tail].mean() - raw[tail].mean()) * _V_HWY ** 2 < 0.01


def test_tight_curves_pass_through():
  raw = _weave(seconds=40.0, bias=2.5 / _V_HWY ** 2)
  out = _run(HighwayCorrectionGain(), raw, gain=0.5)
  tail = slice(len(raw) // 2, None)
  np.testing.assert_allclose(out[tail], raw[tail], rtol=0, atol=1e-12)


def test_curve_entry_is_not_delayed_much():
  t = np.arange(0.0, 20.0, DT_CTRL)
  lat = np.interp(t, [0, 5, 7, 20], [0, 0, 1.2, 1.2])
  raw = lat / _V_HWY ** 2
  out = _run(HighwayCorrectionGain(), raw, gain=0.5)
  shortfall = (raw - out) * _V_HWY ** 2
  # the gate only fully releases at LAT_ACCEL_OFF, so curve entry is briefly softened
  assert np.max(np.abs(shortfall)) < 0.15
  assert np.argmax(out * _V_HWY ** 2 >= 0.6) == np.argmax(lat >= 0.6)
  assert np.max(np.abs(shortfall[t > 12.0])) < 0.01


def test_curve_exit_is_not_held():
  t = np.arange(0.0, 30.0, DT_CTRL)
  lat = np.interp(t, [0, 2, 20, 22, 30], [1.2, 1.2, 1.2, 0, 0])
  raw = lat / _V_HWY ** 2
  hcg = HighwayCorrectionGain()
  hcg.reset(raw[0])
  out = _run(hcg, raw, gain=0.5)
  excess = (out - raw)[t > 19.0] * _V_HWY ** 2
  assert np.max(np.abs(excess)) < 0.15
  i_raw = np.argmax(lat[t > 19.0] <= 0.6)
  i_out = np.argmax(out[t > 19.0] * _V_HWY ** 2 <= 0.6)
  assert (i_out - i_raw) * DT_CTRL < 0.15


def test_below_speed_gate_passes_through():
  v = 13.0
  raw = _weave(v_ego=v)
  np.testing.assert_allclose(_run(HighwayCorrectionGain(), raw, gain=0.5, v_ego=v), raw, rtol=0, atol=1e-12)


def test_bypass_fades_without_steps():
  raw = _weave(seconds=30.0)
  bypass = np.zeros(len(raw), bool)
  bypass[int(15 / DT_CTRL):int(20 / DT_CTRL)] = True
  hcg = HighwayCorrectionGain()
  out = _run(hcg, raw, gain=0.5, bypass=bypass)
  mid = slice(int(17 / DT_CTRL), int(20 / DT_CTRL))
  np.testing.assert_allclose(out[mid], raw[mid], rtol=0, atol=1e-12)
  max_raw_step = np.max(np.abs(np.diff(raw)))
  assert np.max(np.abs(np.diff(out))) < 1.5 * max_raw_step


def test_active_from_40_mph():
  v = 40.0 * 0.44704
  raw = _weave(v_ego=v)
  out = _run(HighwayCorrectionGain(), raw, gain=0.5, v_ego=v)
  tail = slice(len(raw) // 2, None)
  amp_in, _ = _fit_sine(raw[tail], 0.6)
  amp_out, _ = _fit_sine(out[tail], 0.6)
  assert 0.48 < amp_out / amp_in < 0.56


def test_inactive_resets_and_passes_through():
  hcg = HighwayCorrectionGain()
  raw = _weave(seconds=10.0)
  _run(hcg, raw, gain=0.5)
  assert hcg.weight > 0.9
  out = _run(hcg, raw[:50], gain=0.5, lat_active=False)
  np.testing.assert_array_equal(out, raw[:50])
  assert hcg.weight == 0.0 and hcg.bypass_weight == 0.0


def test_gain_is_clamped():
  raw = _weave()
  low = _run(HighwayCorrectionGain(), raw, gain=0.0)
  floor = _run(HighwayCorrectionGain(), raw, gain=0.1)
  np.testing.assert_allclose(low, floor, rtol=0, atol=1e-15)


def test_predicted_lateral_accels():
  t = 10.0 * (np.arange(33) / 32) ** 2
  assert predicted_lateral_accels(t, np.full(33, 30.0), np.full(33, 0.02)) == (0.6, 0.6)
  assert predicted_lateral_accels(t[:10], np.full(10, 30.0), np.full(10, 0.02)) is None
  assert predicted_lateral_accels(t, np.full(32, 30.0), np.full(33, 0.02)) is None


def test_prediction_smooths_wobbly_curve_more():
  raw = _weave(seconds=40.0, lat_accel_amp=0.2, bias=1.0 / _V_HWY ** 2)
  pred = np.full((len(raw), 2), 1.0)
  tail = slice(len(raw) // 2, None)
  amp_in, _ = _fit_sine(raw[tail], 0.6)
  without = _fit_sine(_run(HighwayCorrectionGain(), raw, gain=0.5)[tail], 0.6)[0] / amp_in
  with_pred = _fit_sine(_run(HighwayCorrectionGain(), raw, gain=0.5, predicted=pred)[tail], 0.6)[0] / amp_in
  assert 0.45 < with_pred < 0.62
  assert with_pred < without - 0.1


def test_prediction_releases_before_curve_exit():
  t = np.arange(0.0, 30.0, DT_CTRL)
  lat = np.interp(t, [0, 20, 22, 30], [1.2, 1.2, 0, 0])
  raw = lat / _V_HWY ** 2
  def excess(predicted):
    hcg = HighwayCorrectionGain()
    hcg.reset(raw[0])
    out = _run(hcg, raw, gain=0.5, predicted=predicted)
    return np.max(np.abs((out - raw)[t > 19.0])) * _V_HWY ** 2
  with_pred = excess(_lead(lat, 1.5))
  assert with_pred < 0.02
  assert with_pred < excess(None)


def test_prediction_releases_before_curve_entry():
  t = np.arange(0.0, 20.0, DT_CTRL)
  lat = np.interp(t, [0, 5, 7, 20], [0, 0, 1.2, 1.2])
  raw = lat / _V_HWY ** 2
  def shortfall(predicted):
    out = _run(HighwayCorrectionGain(), raw, gain=0.5, predicted=predicted)
    return np.max(np.abs(raw - out)) * _V_HWY ** 2
  with_pred = shortfall(_lead(lat, 1.5))
  assert with_pred < 0.02
  assert with_pred < shortfall(None)


def test_straights_smooth_the_same_with_prediction():
  raw = _weave()
  pred = np.zeros((len(raw), 2))
  tail = slice(len(raw) // 2, None)
  a = _fit_sine(_run(HighwayCorrectionGain(), raw, gain=0.7)[tail], 0.6)[0]
  b = _fit_sine(_run(HighwayCorrectionGain(), raw, gain=0.7, predicted=pred)[tail], 0.6)[0]
  assert math.isclose(a, b, rel_tol=0.02)


def test_read_gain_maps_setting_and_treats_invalid_as_off(tmp_path):
  params = type("P", (), {"get_param_path": lambda self, key: str(tmp_path / key)})()
  assert read_gain(params) == 1.0
  for raw, gain in ((b"10", 1.0), (b"3", 0.3), (b"1", 0.1), (b"0", 1.0), (b"0.5", 1.0), (b"11", 1.0), (b"nan", 1.0), (b"x", 1.0)):
    (tmp_path / KEY).write_bytes(raw)
    assert math.isclose(read_gain(params), gain), raw
