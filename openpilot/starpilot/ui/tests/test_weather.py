"""Optional weather stays off the driving path and never invents a temperature."""
import copy
from dataclasses import replace
import io
import json
import threading
import time
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.ui.onroad_customization import default_document, validate_document, customization_metadata
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.weather import WeatherClient, WeatherReading, fetch_weather, valid_location, nearby


@pytest.mark.parametrize('profile', ('large', 'compact'))
def test_weather_default_off_and_saved_layouts_keep_their_positions(profile):
  document = default_document()
  assert not document['layouts'][profile]['weather']['enabled']
  assert 'Open-Meteo.com' in customization_metadata()['profiles'][profile]['widgets']['weather']['note']
  old = copy.deepcopy(document)
  for widgets in old['layouts'].values():
    del widgets['weather']
  old['widgetOrder'] = {name: list(widgets) for name, widgets in old['layouts'].items()}
  saved = copy.deepcopy(old)
  migrated = validate_document(old)
  assert old == saved
  for name in ('large', 'compact'):
    assert not migrated['layouts'][name]['weather']['enabled']
    assert [key for key in migrated['widgetOrder'][name] if key != 'weather'] == saved['widgetOrder'][name]
    assert {key: value for key, value in migrated['layouts'][name].items() if key != 'weather'} == saved['layouts'][name]
  document['layouts'][profile]['weather']['enabled'] = True
  assert validate_document(document)['layouts'][profile]['weather']['enabled']


@pytest.mark.parametrize('point', (None, (float('nan'), 0), (91, 0), (0, 181), (True, 5), (1,), '0,0'))
def test_bad_location_never_starts_network_work(point):
  with patch('openpilot.starpilot.ui.weather.threading.Thread') as thread:
    assert not valid_location(point)
    assert WeatherClient().observe(point) is None
    thread.assert_not_called()


def test_weather_is_nonblocking_single_flight_cached_and_expires():
  now = [1000.]
  entered, release, finished = threading.Event(), threading.Event(), threading.Event()
  def fetch(point):
    entered.set()
    assert release.wait(2)
    return WeatherReading(-5, 73, False)
  client = WeatherClient(fetch=fetch, clock=lambda: now[0])
  original = client._refresh
  def refresh(point):
    try:
      original(point)
    finally:
      finished.set()
  client._refresh = refresh
  try:
    start = time.monotonic()
    assert client.observe((41., -87.)) is None
    assert time.monotonic() - start < .5
    assert entered.wait(1)
    for _ in range(10):
      assert client.observe((41., -87.)) is None
    release.set()
    assert finished.wait(1)
    assert client.observe((41., -87.)) == WeatherReading(-5, 73, False)
    assert client.observe((45., -87.)) is None
    assert client.observe(None) is None
    client._next_request = 10000
    now[0] += 1801
    assert client.observe((41., -87.)) is None
  finally:
    release.set()
    assert finished.wait(2)


def test_failed_request_backs_off_and_keeps_a_still_current_reading():
  now = [1000.]
  fetch = Mock(side_effect=OSError('offline'))
  client = WeatherClient(fetch=fetch, clock=lambda: now[0])
  client._reading, client._position, client._received = WeatherReading(20, 0, True), (0, 0), 900
  with patch('openpilot.starpilot.ui.weather.threading.Thread') as thread:
    thread.return_value.start.side_effect = lambda: client._refresh((0, 0))
    assert client.observe((0, 0)).temperature_c == 20
    for _ in range(10):
      client.observe((0, 0))
    assert fetch.call_count == 1
    assert not client._running
    now[0] += 600
    client.observe((0, 0))
    assert fetch.call_count == 2


def test_dateline_is_nearby():
  assert nearby((0, 179.99), (0, -179.99))
  assert not nearby((0, 0), (0, 1))


def response(**changes):
  data = {'current_units': {'temperature_2m': '°C'}, 'current': {'temperature_2m': 21, 'weather_code': 2,
          'is_day': 1, 'time': 10000}}
  data['current'].update(changes)
  return json.dumps(data).encode()


def test_actual_http_decoder_and_bounded_celsius_request():
  with patch('urllib.request.urlopen', return_value=io.BytesIO(response())) as get, patch('openpilot.starpilot.ui.weather.datetime') as clock:
    clock.now.return_value.timestamp.return_value = 10000
    assert fetch_weather((41.1234, -87.1234)) == WeatherReading(21, 2, True)
    request = get.call_args.args[0]
    assert request.full_url.startswith('https://api.open-meteo.com/v1/forecast?')
    assert 'latitude=41.12' in request.full_url and 'temperature_unit=celsius' in request.full_url
    assert get.call_args.kwargs['timeout'] == 5


@pytest.mark.parametrize('changes', ({'temperature_2m': float('nan')}, {'temperature_2m': True}, {'weather_code': 999},
                                    {'is_day': 2}, {'time': 0}, {'time': 11000}))
def test_malformed_and_stale_weather_is_refused(changes):
  with patch('urllib.request.urlopen', return_value=io.BytesIO(response(**changes))), patch('openpilot.starpilot.ui.weather.datetime') as clock:
    clock.now.return_value.timestamp.return_value = 10000
    with pytest.raises(ValueError):
      fetch_weather((0, 0))


@pytest.mark.parametrize('profile,metric,expected', (('large', True, '21°C'), ('large', False, '70°F'),
                                                   ('compact', True, '21°C'), ('compact', False, '70°F')))
def test_native_widget_uses_units_and_real_vector_art(profile, metric, expected):
  from openpilot.starpilot.ui import onroad_weather
  from openpilot.starpilot.ui.presentation import Profile
  fonts = Mock(profile=Profile(profile))
  state = OnroadState(False, False, None, None, SpeedLimitObservation(), metric=metric,
                      weather=WeatherReading(21, 2, True))
  with patch.object(onroad_weather, 'draw_widget_frame'), patch.object(onroad_weather, 'draw_weather_icon') as icon:
    onroad_weather.render_weather(fonts, state, Mock())
  assert fonts.draw.call_args_list[0].args[0] == expected
  assert icon.call_args.args[-2:] == (2, True)


def test_disabled_and_alerted_widget_never_constructs_a_client():
  from openpilot.starpilot.ui.onroad import OnroadView
  from openpilot.starpilot.ui.presentation import Profile
  view = Mock(fonts=Mock(profile=Profile.COMPACT))
  state = OnroadState(False, False, None, None, SpeedLimitObservation())
  with patch('openpilot.starpilot.ui.weather.WeatherClient') as client:
    OnroadView._weather(view, state)
    document = default_document()
    document['layouts']['compact']['weather']['enabled'] = True
    OnroadView._weather(view, replace(state, customization=document, alert=OnroadAlert(size=AlertSize.FULL)))
    client.assert_not_called()


@pytest.mark.parametrize('case', ('off', 'valid', 'invalid', 'no_fix', 'stale', 'future', 'bad_latitude', 'replay', 'fallback'))
def test_runtime_uses_only_current_fixed_gps_and_never_fetches(case, tmp_path):
  from types import SimpleNamespace as NS
  from openpilot.starpilot.ui.tests.test_runtime_snapshot import ui_fake, RuntimeSnapshotAdapter, NOW
  from openpilot.starpilot.ui.shell import ShellMode
  ui = ui_fake()
  document = default_document()
  document['layouts']['compact']['weather']['enabled'] = case != 'off'
  ui.params.values['OnroadCustomizations'] = json.dumps(document).encode()
  ui.params.get_param_path = lambda key: str(tmp_path / key)
  (tmp_path / 'OnroadCustomizations').write_bytes(ui.params.values['OnroadCustomizations'])
  gps = NS(hasFix=case != 'no_fix', latitude=91. if case == 'bad_latitude' else 41., longitude=-87.)
  ui.sm.put('gpsLocationExternal', gps, age_ns=3_000_000_000 if case in ('stale', 'fallback') else -1_000_000_000 if case == 'future' else 0)
  if case == 'invalid':
    ui.sm.valid['gpsLocationExternal'] = False
  if case == 'fallback':
    ui.sm.put('gpsLocation', NS(hasFix=True, latitude=40., longitude=-88.))
  if case == 'replay':
    ui.replay_clock = object()
  with patch('openpilot.starpilot.ui.weather.WeatherClient') as client:
    state = RuntimeSnapshotAdapter(ui).build(ShellMode.ONROAD, now_ns=NOW).onroad
    expected = (41., -87.) if case == 'valid' else (40., -88.) if case == 'fallback' else None
    assert state.customization['layouts']['compact']['weather']['enabled'] == (case != 'off')
    assert state.weather_location == expected
    client.assert_not_called()


def test_existing_positional_state_fields_keep_their_meaning():
  from openpilot.starpilot.ui.appearance_preferences import OnroadAppearance
  state = OnroadState(False, False, None, None, SpeedLimitObservation(), OnroadAppearance(), OnroadAlert(), False, False)
  assert not state.show_slc_offset and state.weather_location is None and state.weather is None
