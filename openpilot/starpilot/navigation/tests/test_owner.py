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


def test_share_intent_retry_after_cancel_or_restart_does_not_restart_route(owner):
  owner.configure({'token': 'pk.test'}, '0', True)
  first_revision = owner.read()['revision']
  first = owner.share('geo:40.5,-89.5', first_revision, True, intent_id='phone-intent-1')
  assert first['destination'] is not None
  owner.clear(owner.read()['revision'], True)
  cancelled_revision = owner.read()['revision']
  restarted = NavigationOwner(owner.root, runtime_source=lambda: None, transient_root=owner.transient_root)
  replay = restarted.share('geo:40.5,-89.5', first_revision, True, intent_id='phone-intent-1')
  assert replay['destination'] is None and replay['revision'] == cancelled_revision
  with pytest.raises(ValidationError, match='already used'):
    restarted.share('geo:41,-89.5', cancelled_revision, True, intent_id='phone-intent-1')
  with pytest.raises(PermissionError):
    restarted.share('geo:40.5,-89.5', cancelled_revision, False, intent_id='phone-intent-1')
  restarted.share('geo:40.5,-89.5', cancelled_revision, True, intent_id='phone-intent-2')
  assert restarted.read()['destination'] is not None


@pytest.mark.parametrize('ledger', [None, [{}], [{'id': '../bad', 'digest': '0' * 64}],
                                    [{'id': 'test', 'digest': 'bad'}],
                                    [{'id': 'test', 'digest': '0' * 64}] * 2,
                                    [{'id': str(i), 'digest': '0' * 64} for i in range(65)]])
def test_corrupt_share_intent_history_is_rejected(owner, ledger):
  owner.configure({'token': 'pk.test'}, '0', True)
  saved = json.loads(owner.path.read_text())
  owner.path.write_text(json.dumps({**saved, 'shareIntents': ledger}))
  with pytest.raises(ValidationError):
    owner.read()


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


def test_google_key_test_checks_all_three_apis_whatever_the_route_provider(owner):
  with (patch('openpilot.starpilot.navigation.google_tiles.test_key', return_value={'ok': True}) as tested,
        patch('openpilot.starpilot.navigation.google_routes.test_routes', return_value={'ok': False}) as routed,
        patch('openpilot.starpilot.navigation.google_places.test_places', return_value={'ok': True, 'message': 'works'}) as placed):
    with pytest.raises(ValidationError):
      owner.test_google_key()
    both = {'ok': True, 'routes': {'ok': False}, 'places': {'ok': True, 'message': 'works'}}
    assert owner.test_google_key(GOOGLE_KEY) == both
    saved = owner.configure({'googleKey': GOOGLE_KEY}, '0', True)
    assert owner.read()['routeProvider'] == 'mapbox' and owner.test_google_key() == both
    owner.configure({'routeProvider': 'google'}, saved['revision'], True)
    assert owner.test_google_key() == both
  for mock in (tested, routed, placed):
    assert [call.args[0] for call in mock.call_args_list] == [GOOGLE_KEY] * 3


class GoogleRefusal:
  def __init__(self, status, text):
    self.status, self.text = status, text

  def _answer(self, *args, **kwargs):
    from contextlib import nullcontext
    from types import SimpleNamespace
    return nullcontext(SimpleNamespace(status_code=self.status, iter_content=lambda size: [json.dumps({'error': {'message': self.text}}).encode()]))

  post = get = _answer


@pytest.mark.parametrize('status, text, failure, words', [
  (403, 'Places API (New) has not been used in project 1 before or it is disabled.', 'disabled',
   "Enable it in Google Cloud and allow it in the key's API restrictions"),
  (403, 'Requests to this API are blocked. API_KEY_SERVICE_BLOCKED', 'disabled', "allow it in the key's API restrictions"),
  (400, 'API key not valid. Please pass a valid API key.', 'key', 'copied whole'),
  (403, 'This API method requires billing to be enabled.', 'billing', 'Add a billing account')])
def test_google_key_test_names_each_api_and_what_to_fix(tmp_path, status, text, failure, words):
  from openpilot.starpilot.navigation.google_places import places_budget, test_places
  from openpilot.starpilot.navigation.google_routes import essentials_budget, test_routes
  from openpilot.starpilot.navigation.google_tiles import GoogleBudget, test_key
  http = GoogleRefusal(status, text)
  results = {'Map Tiles API': test_key(GOOGLE_KEY, http, GoogleBudget(tmp_path)), 'Routes API': test_routes(GOOGLE_KEY, http, essentials_budget(tmp_path)),
             'Places API (New)': test_places(GOOGLE_KEY, http, places_budget(tmp_path))}
  for api, result in results.items():
    assert result['ok'] is False and result['failure'] == failure and words in result['message']
    assert failure == 'billing' or api in result['message']


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


def geocoded(places):
  def respond(session, url, params):
    assert url.endswith('/geocode/v6/forward')
    hit = places.get(params['q'])
    return {'features': [{'properties': {'name': hit[0], 'full_address': hit[1]}, 'geometry': {'coordinates': [hit[3], hit[2]]}}] if hit else []}
  return patch('openpilot.starpilot.navigation.owner.response_json', side_effect=respond)


@pytest.fixture
def keyed(owner):
  owner.configure({'token': 'pk.synthetic'}, owner.read()['revision'], True)
  return owner


def test_share_with_coordinates_turns_navigation_on_and_routes(keyed):
  with geocoded({}) as provider:
    status = keyed.share('https://www.google.com/maps/place/Bellagio,+3600+S+Las+Vegas+Blvd/@36.2,-115.2,17z/data=!3d36.1126!4d-115.1767',
                         keyed.read()['revision'], True)
  provider.assert_not_called()
  assert status['enabled'] is True
  assert status['shared'] == {'name': 'Bellagio', 'latitude': 36.1126, 'longitude': -115.1767}
  assert status['destination']['address'] == '3600 S Las Vegas Blvd'
  assert keyed.read()['destination']['latitude'] == 36.1126
  geo = keyed.share('geo:40.5,-89.5?q=40.5,-89.5(Library)', status['revision'], True)
  assert geo['shared'] == {'name': 'Library', 'latitude': 40.5, 'longitude': -89.5}
  apple = keyed.share('https://maps.apple.com/?ll=41.25,-88.75&q=Park', geo['revision'], True)
  assert apple['shared'] == {'name': 'Park', 'latitude': 41.25, 'longitude': -88.75}
  apple = keyed.share('https://maps.apple.com/place?coordinate=41.5,-88.5&name=Apple+Park&place-id=I1', apple['revision'], True)
  assert apple['shared'] == {'name': 'Apple Park', 'latitude': 41.5, 'longitude': -88.5}
  plain = keyed.share('https://maps.google.com/?q=41.75,-88.25', apple['revision'], True)
  assert plain['shared']['name'] == 'Shared destination'


def test_share_geocodes_place_links_without_coordinates(keyed):
  places = {'3600 S Las Vegas Blvd, Las Vegas, NV 89109': ('3600 S Las Vegas Blvd', '3600 S Las Vegas Blvd, Las Vegas, NV 89109', 36.11, -115.17),
            'Springfield Library, Springfield, IL 62701': ('Springfield', 'Springfield, Illinois', 39.8, -89.6),
            '123 Main St, Springfield, IL 62701': ('123 Main St', '123 Main St, Springfield, IL 62701', 39.7, -89.7)}
  with geocoded(places) as provider:
    status = keyed.share('Bellagio\nhttps://www.google.com/maps/place/Bellagio,+3600+S+Las+Vegas+Blvd,+Las+Vegas,+NV+89109/data=!4m2!3m1!1s0x80c8:0x1',
                         keyed.read()['revision'], True)
    assert [call.args[2]['q'] for call in provider.call_args_list] == ['3600 S Las Vegas Blvd, Las Vegas, NV 89109']
    assert status['shared'] == {'name': 'Bellagio', 'latitude': 36.11, 'longitude': -115.17}
    assert status['destination']['address'] == '3600 S Las Vegas Blvd, Las Vegas, NV 89109' and status['enabled']
    provider.reset_mock()
    status = keyed.share('https://maps.google.com/?q=Springfield+Library,+Springfield,+IL+62701&ftid=0x1:0x2', status['revision'], True)
    assert [call.args[2]['q'] for call in provider.call_args_list] == ['Springfield Library, Springfield, IL 62701']
    assert status['shared']['name'] == 'Springfield Library'
    provider.reset_mock()
    status = keyed.share('https://www.google.com/maps/place/123+Main+St,+Springfield,+IL+62701/data=!4m2', status['revision'], True)
    assert [call.args[2]['q'] for call in provider.call_args_list] == ['123 Main St, Springfield, IL 62701']
    assert status['shared']['name'] == '123 Main St'
    provider.reset_mock()
    with pytest.raises(ValidationError, match='No map location found for Nowhere') as error:
      keyed.share('https://www.google.com/maps/place/Nowhere,+1+Lost+Rd,+Atlantis/data=!4m2', status['revision'], True)
    assert error.value.query == 'Nowhere, 1 Lost Rd, Atlantis'
    assert [call.args[2]['q'] for call in provider.call_args_list] == ['1 Lost Rd, Atlantis', 'Nowhere, 1 Lost Rd, Atlantis']


def test_share_without_a_map_place_asks_for_a_search(keyed):
  revision = keyed.read()['revision']
  with geocoded({}) as provider:
    with pytest.raises(ValidationError, match='Open StarPilot to search for Starbucks') as error:
      keyed.share('Starbucks', revision, True)
    assert error.value.query == 'Starbucks'
    with pytest.raises(ValidationError) as error:
      keyed.share('geo:0,0?q=1600+Amphitheatre+Pkwy', revision, True)
    assert error.value.query == '1600 Amphitheatre Pkwy'
  provider.assert_not_called()
  keyed.session = Redirects({})
  with pytest.raises(ValidationError, match='could not be opened') as error:
    keyed.share('https://maps.app.goo.gl/abc123', revision, True)
  assert not hasattr(error.value, 'query')
  assert keyed.read()['revision'] == revision and not keyed.read()['enabled']


def test_share_follows_short_links_and_checks_token_revision_and_authority(owner):
  with pytest.raises(ValidationError, match='Mapbox access token'):
    owner.share('geo:40.5,-89.5', owner.read()['revision'], True)
  revision = owner.configure({'token': 'pk.synthetic'}, owner.read()['revision'], True)['revision']
  with pytest.raises(ConflictError):
    owner.share('geo:40.5,-89.5', '0', True)
  with pytest.raises(PermissionError):
    owner.share('geo:40.5,-89.5', revision, lambda: False)
  with pytest.raises(ValidationError):
    owner.share('', revision, True)
  owner.session = Redirects({'https://maps.app.goo.gl/abc123': 'https://www.google.com/maps/place/X/data=!3d36.1!4d-115.1'})
  status = owner.share('X https://maps.app.goo.gl/abc123', revision, True)
  assert status['shared'] == {'name': 'X', 'latitude': 36.1, 'longitude': -115.1} and status['enabled']


class GooglePlaces:
  def __init__(self, status=200, places=()):
    self.status, self.places, self.posts = status, list(places), []

  def post(self, url, **kwargs):
    from contextlib import nullcontext
    from types import SimpleNamespace
    self.posts.append((url, kwargs))
    body = json.dumps({'places': self.places} if self.status == 200 else {'error': {'status': 'PERMISSION_DENIED',
                      'message': 'Places API (New) has not been used in project 1 before or it is disabled.'}}).encode()
    return nullcontext(SimpleNamespace(status_code=self.status, iter_content=lambda size: [body]))


@pytest.fixture
def google(keyed):
  keyed.configure({'routeProvider': 'google', 'googleKey': GOOGLE_KEY}, keyed.read()['revision'], True)
  with patch.object(keyed, '_search_context', return_value={'proximity': '-115.200000,36.200000'}):
    yield keyed


def test_share_with_google_routing_looks_the_place_up_with_google(google):
  google.session = GooglePlaces(places=[{'displayName': {'text': 'Bellagio Hotel & Casino'}, 'formattedAddress': '3600 S Las Vegas Blvd, Las Vegas, NV 89109',
                                          'location': {'latitude': 36.1126, 'longitude': -115.1767}}])
  with geocoded({}) as mapbox:
    status = google.share('https://www.google.com/maps/place/Bellagio,+3600+S+Las+Vegas+Blvd,+Las+Vegas,+NV+89109/data=!4m2!3m1!1s0x1:0x2',
                          google.read()['revision'], True)
  mapbox.assert_not_called()
  assert status['shared'] == {'name': 'Bellagio Hotel & Casino', 'latitude': 36.1126, 'longitude': -115.1767} and status['enabled']
  assert status['destination']['address'] == '3600 S Las Vegas Blvd, Las Vegas, NV 89109'
  url, request = google.session.posts[0]
  assert url == 'https://places.googleapis.com/v1/places:searchText'
  assert request['json']['textQuery'] == 'Bellagio, 3600 S Las Vegas Blvd, Las Vegas, NV 89109' and request['json']['pageSize'] == 1
  assert request['json']['locationBias']['circle']['center'] == {'latitude': 36.2, 'longitude': -115.2}
  assert request['headers'] == {'X-Goog-Api-Key': GOOGLE_KEY, 'X-Goog-FieldMask': 'places.displayName,places.formattedAddress,places.location'}
  google.session = GooglePlaces()
  with pytest.raises(ValidationError, match='No map location found for Nowhere') as error:
    google.share('https://maps.google.com/?q=Nowhere', status['revision'], True)
  assert error.value.query == 'Nowhere'
  google.session = GooglePlaces(places=[{'location': {'latitude': 1.5, 'longitude': 2.5}}])
  assert google.share('geo:40.5,-89.5', status['revision'], True)['shared']['latitude'] == 40.5
  assert not google.session.posts


def test_share_falls_back_to_mapbox_when_google_lookup_is_refused(google):
  google.session = GooglePlaces(status=403)
  with geocoded({'Springfield Library': ('Springfield Library', 'Springfield, IL', 39.8, -89.6)}) as mapbox:
    status = google.share('https://maps.google.com/?q=Springfield+Library', google.read()['revision'], True)
  assert len(google.session.posts) == 1 and mapbox.call_args.args[2]['q'] == 'Springfield Library'
  assert status['shared'] == {'name': 'Springfield Library', 'latitude': 39.8, 'longitude': -89.6}
  from openpilot.starpilot.navigation.google_places import places_budget
  budget = places_budget(google.google_root)
  assert budget.snapshot()['monthUsed'] == 1 and budget.month_limit == 4950
  with patch('openpilot.starpilot.navigation.google_places.GoogleBudget.spend', return_value=False):
    google.session = GooglePlaces(places=[{'location': {'latitude': 1.5, 'longitude': 2.5}}])
    with geocoded({'Springfield Library': ('Springfield Library', 'Springfield, IL', 39.8, -89.6)}):
      assert google.share('https://maps.google.com/?q=Springfield+Library', status['revision'], True)['shared']['latitude'] == 39.8
    assert not google.session.posts


def test_share_with_google_only_setup_still_needs_the_mapbox_token_routing_uses(owner):
  owner.configure({'routeProvider': 'google', 'googleKey': GOOGLE_KEY, 'mapProvider': 'google'}, owner.read()['revision'], True)
  owner.session = GooglePlaces(places=[{'location': {'latitude': 1.5, 'longitude': 2.5}}])
  with pytest.raises(ValidationError, match='Add your Mapbox access token first') as error:
    owner.share('https://maps.google.com/?q=Bellagio', owner.read()['revision'], True)
  assert not hasattr(error.value, 'query') and not owner.session.posts
  assert owner.snapshot()['status'] == 'disabled' and not owner.snapshot()['hasKey']


def test_google_place_lookup_test_reports_disabled_api():
  from openpilot.starpilot.navigation.google_places import places_budget, test_places
  import tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    assert test_places(GOOGLE_KEY, GooglePlaces(status=403), places_budget(root)) == {'ok': False, 'failure': 'disabled',
      'message': "Places API (New) is off for this key. Enable it in Google Cloud and allow it in the key's API restrictions, then test again."}
    assert test_places(GOOGLE_KEY, GooglePlaces(places=[{'location': {'latitude': 1.5, 'longitude': 2.5}}]), places_budget(root))['ok']
