"""Opt-in Mazda steering gain (Param MazdaSteerTorqueScale) on the sigmoid+linear torque table.

The table converts the controller's whole command (feedforward + PID + friction), so the gain
scales feedback too. It must be an explicit choice: unset means exactly the stock table.
"""
import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest

from opendbc.car.mazda.values import CAR


class _FakeParams:
  value = None

  def __init__(self, *args, **kwargs):
    pass

  def get(self, key, block=False, return_default=False):
    assert key == "MazdaSteerTorqueScale"
    return 1.0 if self.value is None and return_default else self.value

  def get_bool(self, key):
    return False


@pytest.fixture(scope="module")
def interface():
  try:
    from opendbc.car.mazda import interface
  except OSError:
    # Host checkouts without the compiled openpilot params library.
    stub = types.ModuleType("openpilot.common.params")
    stub.Params = _FakeParams
    sys.modules["openpilot.common.params"] = stub
    try:
      from opendbc.car.mazda import interface
    finally:
      del sys.modules["openpilot.common.params"]
  return interface


@pytest.fixture
def saved(monkeypatch, interface):
  monkeypatch.setattr(interface, "Params", _FakeParams)
  monkeypatch.setattr(_FakeParams, "value", None)
  return lambda v: monkeypatch.setattr(_FakeParams, "value", v)


def table(interface, fingerprint):
  ci = SimpleNamespace(CP=SimpleNamespace(carFingerprint=fingerprint))
  torque, lataccel = interface.CarInterface.get_lataccel_torque_siglin(ci)
  return np.array(torque), lataccel, ci


@pytest.mark.parametrize("fingerprint", (CAR.MAZDA_3_2019, CAR.MAZDA_CX_30, CAR.MAZDA_CX_50))
def test_unset_is_the_stock_table(interface, saved, fingerprint):
  base, _, _ = table(interface, fingerprint)
  saved(1.0)
  explicit, _, _ = table(interface, fingerprint)
  assert np.array_equal(base, explicit)


def test_chosen_gain_scales_the_whole_table(interface, saved):
  base, lataccel, _ = table(interface, CAR.MAZDA_3_2019)
  saved(1.2)
  scaled, _, _ = table(interface, CAR.MAZDA_3_2019)
  assert np.allclose(scaled, 1.2 * base)
  # Feedback is scaled too (reviewer's example): a pure PID command of 0.2 m/s^2.
  assert np.interp(0.2, lataccel, scaled) == pytest.approx(1.2 * np.interp(0.2, lataccel, base))


@pytest.mark.parametrize("bad", (0.5, 0.79, 1.51, 3.0, float("nan"), float("inf"), "x", None))
def test_out_of_range_or_invalid_falls_back_to_stock(interface, saved, bad):
  base, _, _ = table(interface, CAR.MAZDA_3_2019)
  saved(bad)
  assert interface.steer_torque_scale() == 1.0
  assert np.array_equal(table(interface, CAR.MAZDA_3_2019)[0], base)


def test_unreadable_params_fall_back_to_stock(interface, monkeypatch):
  def broken(*a, **k):
    raise OSError("no params")
  monkeypatch.setattr(interface, "Params", broken)
  assert interface.steer_torque_scale() == 1.0


@pytest.mark.parametrize("gain", (0.8, 1.0, 1.2, 1.5))
@pytest.mark.parametrize("fingerprint", (CAR.MAZDA_3_2019, CAR.MAZDA_CX_30))
def test_every_allowed_gain_keeps_a_valid_monotonic_inverse(interface, saved, gain, fingerprint):
  saved(gain)
  torque, lataccel, _ = table(interface, fingerprint)  # asserts it still covers [-1, 1]
  assert np.all(np.diff(torque) > 0)
  for la in (-2.0, -0.3, 0.0, 0.4, 1.5):
    t = np.interp(la, lataccel, torque)
    assert np.interp(t, torque, lataccel) == pytest.approx(la, abs=1e-3)


def test_forward_and_inverse_read_the_gain_once(interface, saved):
  saved(1.2)
  ci = SimpleNamespace(CP=SimpleNamespace(carFingerprint=CAR.MAZDA_3_2019))
  first, _ = interface.CarInterface.get_lataccel_torque_siglin(ci)
  saved(1.4)  # changed mid-drive: the running interface keeps its gain until restart
  second, _ = interface.CarInterface.get_lataccel_torque_siglin(ci)
  assert np.array_equal(first, second)
