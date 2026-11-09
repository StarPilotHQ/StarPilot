import json
from unittest.mock import patch

import pytest

from openpilot.starpilot.navigation.owner import NavigationOwner, ValidationError, ConflictError, response_json

PLACE = {'name': 'Home', 'latitude': 40., 'longitude': -90.}


@pytest.fixture
def owner(tmp_path):
  return NavigationOwner(tmp_path, runtime_source=lambda: None)


def test_settings_private_atomic_and_revision_scoped(owner):
  assert owner.snapshot()['enabled'] is False
  saved = owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  assert saved['hasKey'] and 'token' not in saved and saved['status'] == 'noDestination'
  assert owner.path.stat().st_mode & 0o777 == 0o600
  with pytest.raises(ConflictError):
    owner.select(PLACE, '0', True)
  selected = owner.select(PLACE, saved['revision'], True)
  assert selected['destination']['name'] == 'Home'
  assert owner.favorite(PLACE, selected['revision'], True)['favorites'][0]['name'] == 'Home'
  assert 'pk.test' not in json.dumps(owner.snapshot())


def test_authority_rechecked_after_flush(owner):
  calls = iter((True, False))
  with pytest.raises(PermissionError):
    owner.configure({'enabled': True}, '0', lambda: next(calls))
  assert not owner.path.exists()
  assert list(owner.root.glob('.settings-*')) == []


def test_corrupt_existing_settings_not_overwritten(owner):
  owner.path.write_text('{invalid')
  with pytest.raises(ValidationError):
    owner.configure({'enabled': True}, '0', True)
  assert owner.path.read_text() == '{invalid'


@pytest.mark.parametrize('value', [dict(PLACE, latitude=float('nan')), dict(PLACE, latitude=True), dict(PLACE, longitude=181),
                                   dict(PLACE, name=''), None])
def test_invalid_destination(owner, value):
  with pytest.raises(ValidationError):
    owner.select(value, '0', True)


def test_current_units_and_stale_revision_not_presented(owner):
  saved = owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  selected = owner.select(PLACE, saved['revision'], True)
  owner.runtime_source = lambda: {'revision': saved['revision'], 'status': 'guiding', 'instruction': {'text': 'old'}, 'route': []}
  with patch('openpilot.common.params.Params') as params:
    params.return_value.get_bool.return_value = True
    status = owner.snapshot()
  assert status['isMetric'] and status['instruction'] is None
  assert selected['revision'] != saved['revision']


def test_response_limits_and_private_failures():
  from unittest.mock import Mock
  response = Mock(status_code=200)
  response.__enter__ = Mock(return_value=response)
  response.__exit__ = Mock(return_value=False)
  response.iter_content.return_value = [b'{"ok":true}']
  session = Mock()
  session.get.return_value = response
  assert response_json(session, 'https://api.mapbox.com', {'access_token': 'private'}) == {'ok': True}
  with patch('openpilot.starpilot.navigation.owner.time.monotonic', side_effect=[0, 11]):
    with pytest.raises(ValidationError, match='too long'):
      response_json(session, 'https://api.mapbox.com', {})


def test_status_polls_skip_route_lines_the_client_already_holds(owner):
  saved = owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  selected = owner.select(PLACE, saved['revision'], True)
  line = [{'latitude': 40.123456789, 'longitude': -90.987654321}, {'latitude': 40.2, 'longitude': -90.1}]
  owner.runtime_source = lambda: {'revision': selected['revision'], 'status': 'guiding', 'instruction': None, 'route': line}
  full = owner.snapshot()
  assert full['route'][0] == {'latitude': 40.123457, 'longitude': -90.987654}, "drawn lines are sent at ~10 cm precision"
  light = owner.snapshot(full['routeKey'])
  assert light['routeUnchanged'] is True and 'route' not in light and 'alternatives' not in light
  assert light['routeKey'] == full['routeKey'] and light['destination'] == full['destination']
  line.append({'latitude': 40.3, 'longitude': -90.})
  changed = owner.snapshot(full['routeKey'])
  assert len(changed['route']) == 3 and changed['routeKey'] != full['routeKey'] and 'routeUnchanged' not in changed


def test_reads_reparse_only_after_a_write(owner):
  saved = owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  first = owner.read()
  first['favorites'].append('mutated by a caller')
  with patch('openpilot.starpilot.navigation.owner.json.loads', side_effect=AssertionError('reparsed')):
    assert owner.read()['favorites'] == [], "cached copies are independent of what callers change"
  assert owner.select(PLACE, saved['revision'], True)['destination']['name'] == 'Home'
  assert owner.read()['destination']['name'] == 'Home'


GOOGLE_KEY = 'AIzaSyFixtureKey_0123456789abcdef'


def test_google_map_style_is_saved_without_exposing_the_key(owner):
  assert owner.snapshot()['mapProvider'] == 'mapbox' and owner.snapshot()['hasGoogleKey'] is False
  selected = owner.select(PLACE, owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)['revision'], True)
  saved = owner.configure({'mapProvider': 'google', 'googleKey': GOOGLE_KEY}, selected['revision'], True)
  assert saved['mapProvider'] == 'google' and saved['hasGoogleKey'] is True
  assert saved['destination']['name'] == 'Home'
  assert GOOGLE_KEY not in json.dumps(owner.snapshot())
  assert saved['googleMap']['usage']['monthLimit'] == 99_000 and saved['googleMap']['failure'] is None
  assert owner.read()['googleKey'] == GOOGLE_KEY


@pytest.mark.parametrize('patch', [{'mapProvider': 'apple'}, {'googleKey': 'short'}, {'googleKey': 'AIza key with spaces 0123456789'},
                                   {'googleKey': 7}, {'googleKey': 'A' * 129}])
def test_invalid_google_settings_rejected(owner, patch):
  with pytest.raises(ValidationError):
    owner.configure(patch, '0', True)


def test_older_settings_files_read_with_google_defaults(owner):
  owner.path.write_text(json.dumps({'version': 1, 'revision': 'abc', 'enabled': True, 'token': 'pk.test', 'destination': None,
                                    'favorites': []}))
  settings = owner.read()
  assert settings['mapProvider'] == 'mapbox' and settings['googleKey'] == ''


def test_google_key_test_uses_typed_or_saved_key(owner):
  with (patch('openpilot.starpilot.navigation.google_tiles.test_key', return_value={'ok': True}) as tested,
        patch('openpilot.starpilot.navigation.google_routes.test_routes', return_value={'ok': False}) as routed):
    with pytest.raises(ValidationError):
      owner.test_google_key()
    assert owner.test_google_key(GOOGLE_KEY) == {'ok': True}
    saved = owner.configure({'googleKey': GOOGLE_KEY}, '0', True)
    owner.test_google_key()
    assert not routed.called
    owner.configure({'routeProvider': 'google'}, saved['revision'], True)
    assert owner.test_google_key() == {'ok': True, 'routes': {'ok': False}}
  assert [call.args[0] for call in tested.call_args_list] == [GOOGLE_KEY] * 3
  assert [call.args[0] for call in routed.call_args_list] == [GOOGLE_KEY]


def test_route_provider_is_saved_and_reports_google_route_use(owner):
  snapshot = owner.snapshot()
  assert snapshot['routeProvider'] == 'mapbox' and snapshot['googleRoutes']['usage']['monthLimit'] == 4950
  assert snapshot['googleRoutes']['failure'] is None
  saved = owner.configure({'routeProvider': 'google', 'googleKey': GOOGLE_KEY}, snapshot['revision'], True)
  assert saved['routeProvider'] == 'google' and owner.read()['routeProvider'] == 'google'
  from openpilot.starpilot.navigation.google_routes import write_route_status
  write_route_status(owner.google_root, 'budget')
  assert 'used up' in owner.snapshot()['googleRoutes']['failure']
  with pytest.raises(ValidationError, match='route provider'):
    owner.configure({'routeProvider': 'apple'}, saved['revision'], True)


class Redirects:
  def __init__(self, hops):
    self.hops, self.calls = dict(hops), []

  def get(self, url, **kwargs):
    from contextlib import nullcontext
    from types import SimpleNamespace
    self.calls.append((url, kwargs))
    location = self.hops.get(url)
    return nullcontext(SimpleNamespace(status_code=302 if location else 200, headers={'Location': location} if location else {}))


def test_shared_short_link_follows_google_redirects_only(owner):
  owner.session = Redirects({'https://maps.app.goo.gl/abc123': 'https://www.google.com/maps/place/X/@36.1,-115.1,17z/data=!3d36.1!4d-115.1'})
  resolved = owner.resolve_share('Bellagio\nhttps://maps.app.goo.gl/abc123')
  assert resolved == 'Bellagio\nhttps://www.google.com/maps/place/X/@36.1,-115.1,17z/data=!3d36.1!4d-115.1'
  assert owner.session.calls[0][1]['allow_redirects'] is False and owner.session.calls[0][1]['stream'] is True
  owner.session = Redirects({'https://maps.app.goo.gl/abc123': 'https://evil.example/maps?q=1'})
  assert owner.resolve_share('https://maps.app.goo.gl/abc123') == 'https://maps.app.goo.gl/abc123'
  owner.session = Redirects({'https://maps.app.goo.gl/abc123': 'http://www.google.com/maps'})
  assert owner.resolve_share('https://maps.app.goo.gl/abc123') == 'https://maps.app.goo.gl/abc123'
  owner.session = Redirects({})
  assert owner.resolve_share('Starbucks') == 'Starbucks' and not owner.session.calls
  with pytest.raises(ValidationError):
    owner.resolve_share(None)
