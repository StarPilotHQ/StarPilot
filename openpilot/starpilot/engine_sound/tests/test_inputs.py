import time
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.starpilot.engine_sound import settings
from openpilot.starpilot.engine_sound.daemon import BLOCK_BYTES, Engine, audible


def now_ns():
  return time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))


def message():
  return SimpleNamespace(starpilotCarState=SimpleNamespace(acceleratorPedal=0.0, ecoGear=False, sportGear=False))


@pytest.fixture
def dashboard():
  return pytest.importorskip('openpilot.starpilot.car.hyundai.dashboard')


@pytest.mark.parametrize('raw, expected', [(0.0, 0.0), (127.0, 0.5), (254.0, 1.0), (255.5, 1.0)])
def test_pedal_travel_is_normalised_and_clamped(dashboard, raw, expected):
  state = SimpleNamespace(canValid=True, canTimeout=False)
  out = dashboard.write_accelerator_pedal(SimpleNamespace(accelerator_pedal=(raw, now_ns())), state, message())
  assert out.starpilotCarState.acceleratorPedal == pytest.approx(expected, abs=1e-3)


def test_stale_invalid_or_missing_pedal_is_not_published(dashboard):
  good = SimpleNamespace(canValid=True, canTimeout=False)
  stale = now_ns() - dashboard.DRIVE_MODE_MAX_AGE_NS - 1
  for parsed, state in ((SimpleNamespace(accelerator_pedal=(200.0, stale)), good),
                        (SimpleNamespace(accelerator_pedal=(200.0, now_ns())), SimpleNamespace(canValid=False, canTimeout=False)),
                        (SimpleNamespace(accelerator_pedal=(200.0, now_ns())), SimpleNamespace(canValid=True, canTimeout=True)),
                        (SimpleNamespace(accelerator_pedal=None), good), (SimpleNamespace(), good)):
    assert dashboard.write_accelerator_pedal(parsed, state, message()).starpilotCarState.acceleratorPedal == 0.0


def test_drive_mode_still_published_through_the_shared_freshness_check(dashboard):
  state = SimpleNamespace(canValid=True, canTimeout=False)
  out = dashboard.write_drive_mode(SimpleNamespace(drive_mode=(dashboard.DRIVE_MODE_SPORT, now_ns())), state, message())
  assert out.starpilotCarState.sportGear and not out.starpilotCarState.ecoGear


def samples(block):
  return np.frombuffer(block, dtype='<i2')


def test_engine_fades_in_and_out_instead_of_switching():
  engine = Engine('race_car')
  first = engine.block(0.5, 0.0, 0.0, False, 1.0)
  assert len(first) == BLOCK_BYTES
  for _ in range(50):
    loud = engine.block(0.5, 0.0, 0.0, False, 1.0)
  assert np.abs(samples(loud)).max() > 4 * np.abs(samples(first)).max()
  fading = engine.block(0.5, 0.0, 0.0, False, 0.0)
  assert np.abs(samples(fading)).max() > 0
  for _ in range(100):
    quiet = engine.block(0.5, 0.0, 0.0, False, 0.0)
  assert np.abs(samples(quiet)).max() <= 1


def test_profile_switch_restarts_from_idle():
  engine = Engine('race_car')
  for _ in range(100):
    engine.block(1.0, 0.0, 0.0, False, 1.0)
  engine.select('diesel')
  assert engine.profile.name == 'diesel' and engine.model.rpm == engine.profile.idle_rpm


class Sub:
  def __init__(self, *, alive=True, can_valid=True, timeout=False, alert='none'):
    self.alive = {'carState': alive}
    self.data = {'carState': SimpleNamespace(canValid=can_valid, canTimeout=timeout), 'selfdriveState': SimpleNamespace(alertSound=alert)}
  def __getitem__(self, key): return self.data[key]


@pytest.mark.parametrize('kwargs, expected', [({}, True), ({'alert': 'warningSoft'}, False), ({'alive': False}, False),
                                              ({'can_valid': False}, False), ({'timeout': True}, False)])
def test_engine_is_silent_during_alerts_and_without_valid_car_data(kwargs, expected):
  assert audible(Sub(**kwargs), 'none') is expected


class Params:
  def __init__(self, values): self.values = values
  def get(self, key): return self.values.get(key)
  def get_bool(self, key): return bool(self.values.get(key))


def test_settings_fall_back_on_missing_or_unknown_values():
  assert settings.profile_name(Params({})) == 'race_car'
  assert settings.profile_name(Params({'EngineSoundProfile': 'tractor'})) == 'race_car'
  assert settings.profile_name(Params({'EngineSoundProfile': b'diesel'})) == 'diesel'
  assert settings.volume(Params({})) == 0.6
  assert settings.volume(Params({'EngineSoundVolume': 80})) == 0.8
  assert settings.volume(Params({'EngineSoundVolume': 500})) == 1.0
  assert not settings.enabled(Params({}))
