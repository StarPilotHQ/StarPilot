from concurrent.futures import Future
from unittest.mock import Mock

import pytest

from openpilot.starpilot.galaxy.weather import WeatherSource
from openpilot.starpilot.navigation.position import LastPositionStore


@pytest.fixture
def weather(tmp_path):
  store = LastPositionStore(tmp_path)
  source = WeatherSource(store, clock=lambda: source.now, wall=lambda: source.wall_now)
  source.now, source.wall_now = 0., 10000.
  source.executor.submit = Mock(side_effect=lambda *_args: Future())
  yield source, store
  source.close()


def response(value=-5, observed=10000):
  return {'current': {'temperature_2m': value, 'time': observed}, 'current_units': {'temperature_2m': '\u00b0C'}}


def test_no_location_and_nonblocking_cache(weather):
  source, store = weather
  assert source.sample()['status'] == 'no-location'
  source.executor.submit.assert_not_called()
  store.record({'latitude': 42.12345, 'longitude': -88.12345})
  assert source.sample()['status'] == 'loading'
  source.executor.submit.assert_called_once_with(source.fetch, (42.12, -88.12))
  source.sample()
  assert source.executor.submit.call_count == 1
  source.future.set_result(response())
  assert source.sample()['temperatureC'] == -5
  source.now = 599
  assert source.sample()['status'] == 'current'
  assert source.executor.submit.call_count == 1
  source.now = 600
  source.sample()
  assert source.executor.submit.call_count == 2


def test_moved_location_never_reuses_old_temperature(weather):
  source, store = weather
  store.record({'latitude': 42., 'longitude': -88.})
  source.sample()
  source.future.set_result(response())
  assert source.sample()['status'] == 'current'
  store.record({'latitude': 43., 'longitude': -88.})
  assert source.sample()['temperatureC'] is None
  source.now = 61
  assert source.sample()['status'] == 'loading'
  assert source.executor.submit.call_count == 2


@pytest.mark.parametrize('value', [None, True, float('nan'), 99])
def test_invalid_response_and_retry_backoff(weather, value):
  source, store = weather
  store.record({'latitude': 0., 'longitude': 0.})
  source.sample()
  source.future.set_result(response(value))
  assert source.sample()['status'] == 'unavailable'
  source.now = 59
  source.sample()
  assert source.executor.submit.call_count == 1
  source.now = 60
  assert source.sample()['status'] == 'loading'


def test_network_failure_expiry_and_shutdown(weather):
  source, store = weather
  store.record({'latitude': 42., 'longitude': -88.})
  source.sample()
  source.future.set_result(response())
  assert source.sample()['status'] == 'current'
  source.now = 600
  source.sample()
  source.future.set_exception(OSError('offline'))
  assert source.sample()['status'] == 'current'
  source.wall_now += 1801
  assert source.sample()['temperatureC'] is None
  source.close()
  assert source.sample()['status'] == 'unavailable'
