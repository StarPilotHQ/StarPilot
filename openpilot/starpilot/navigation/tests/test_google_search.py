import json
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openpilot.starpilot.navigation.google_places import places_budget, suggestions_budget
from openpilot.starpilot.navigation.owner import NavigationOwner, ValidationError

KEY = 'AIzaSyFixtureKey_0123456789abcdef'
BELLAGIO = {'displayName': {'text': 'Bellagio'}, 'formattedAddress': '3600 S Las Vegas Blvd, Las Vegas, NV',
            'location': {'latitude': 36.1126, 'longitude': -115.1767}}
CAESARS = {'displayName': {'text': 'Caesars Palace'}, 'formattedAddress': '3570 S Las Vegas Blvd', 'location': {'latitude': 36.1162, 'longitude': -115.1745}}
PREDICTION = {'placePrediction': {'placeId': 'ChIJbellagio', 'text': {'text': 'Bellagio, Las Vegas'},
                                  'structuredFormat': {'mainText': {'text': 'Bellagio'}, 'secondaryText': {'text': 'Las Vegas Blvd, Las Vegas, NV'}}}}
MAPBOX = {'features': [{'properties': {'name': 'Mapbox Place', 'full_address': '1 Main St'}, 'geometry': {'coordinates': [-90., 40.]}}]}


class Google:
  def __init__(self, status=200, places=(), suggestions=(), details=None):
    self.status, self.places, self.suggestions, self.details, self.calls = status, list(places), list(suggestions), details, []

  def _answer(self, value):
    refusal = {'error': {'message': 'Places API (New) has not been used in project 1 before or it is disabled.'}}
    body = json.dumps(value if self.status == 200 else refusal).encode()
    return nullcontext(SimpleNamespace(status_code=self.status, iter_content=lambda size: [body]))

  def post(self, url, **kwargs):
    self.calls.append(('post', url, kwargs))
    return self._answer({'places': self.places} if url.endswith(':searchText') else {'suggestions': self.suggestions})

  def get(self, url, **kwargs):
    self.calls.append(('get', url, kwargs))
    return self._answer(self.details)


@pytest.fixture
def owner(tmp_path):
  result = NavigationOwner(tmp_path / 'saved', runtime_source=lambda: None, transient_root=tmp_path / 'boot')
  saved = result.configure({'enabled': True, 'token': 'pk.synthetic', 'googleKey': KEY}, '0', True)
  result.configure({'routeProvider': 'google'}, saved['revision'], True)
  with patch.object(result, '_search_context', return_value={'proximity': '-115.200000,36.200000'}):
    yield result


def search(owner, query='bellagio', autocomplete=False, search_id=None, client=None):
  return owner.search_places(query, 'caller', search_id or str(uuid.uuid4()), client or str(uuid.uuid4()), autocomplete=autocomplete)


def test_full_search_uses_google_text_search_when_google_gives_directions(owner):
  owner.session = Google(places=[BELLAGIO, CAESARS])
  with patch('openpilot.starpilot.navigation.owner.response_json') as mapbox:
    results = search(owner)
  mapbox.assert_not_called()
  assert results.provider == 'google'
  assert [(row['name'], row['address'], row['latitude']) for row in results] == [
    ('Bellagio', '3600 S Las Vegas Blvd, Las Vegas, NV', 36.1126), ('Caesars Palace', '3570 S Las Vegas Blvd', 36.1162)]
  assert all('searchId' not in row and 'temporary' not in row for row in results), "plain select and favorite take these"
  _, url, request = owner.session.calls[0]
  assert url.endswith('places:searchText') and request['json']['pageSize'] == 8 and request['json']['textQuery'] == 'bellagio'
  assert request['json']['locationBias']['circle']['center'] == {'latitude': 36.2, 'longitude': -115.2}
  assert places_budget(owner.google_root).snapshot()['monthUsed'] == 1
  saved = owner.favorite(results[0], owner.read()['revision'], True)
  assert saved['favorites'][0]['name'] == 'Bellagio'
  assert owner.select(results[1], saved['revision'], True)['destination']['name'] == 'Caesars Palace'
  owner.session = Google()
  empty = search(owner, 'nowhere')
  assert empty == [] and empty.provider == 'google', "Google finding nothing is an answer, not a reason to ask Mapbox"


def test_full_search_falls_back_to_mapbox_when_google_refuses_or_runs_out(owner):
  owner.session = Google(status=403)
  with patch('openpilot.starpilot.navigation.owner.response_json', return_value={'suggestions': []}), \
       patch.object(owner, 'search', return_value=[{'id': 'x', 'name': 'Mapbox Place', 'latitude': 40., 'longitude': -90.}]) as geocode:
    results = search(owner)
    assert getattr(results, 'provider', 'mapbox') == 'mapbox' and results[0]['name'] == 'Mapbox Place'
    geocode.assert_called_once_with('bellagio')
    owner.session = Google(places=[BELLAGIO])
    with patch('openpilot.starpilot.navigation.google_places.GoogleBudget.spend', return_value=False):
      assert getattr(search(owner), 'provider', 'mapbox') == 'mapbox'
    assert not owner.session.calls, "a used-up allowance never reaches Google"


def test_mapbox_directions_keep_mapbox_search(owner):
  owner.configure({'routeProvider': 'mapbox'}, owner.read()['revision'], True)
  owner.session = Google(places=[BELLAGIO])
  with patch('openpilot.starpilot.navigation.owner.response_json', return_value=MAPBOX | {'suggestions': []}):
    results = search(owner)
  assert results[0]['name'] == 'Mapbox Place' and not owner.session.calls


def test_typing_uses_one_google_session_and_choosing_ends_it_with_location_only_details(owner):
  owner.session = Google(suggestions=[PREDICTION], details={'location': {'latitude': 36.1126, 'longitude': -115.1767},
                                                           'formattedAddress': '3600 S Las Vegas Blvd, Las Vegas, NV 89109'})
  search_id, client = str(uuid.uuid4()), str(uuid.uuid4())
  with patch('openpilot.starpilot.navigation.owner.response_json') as mapbox:
    first = search(owner, 'bel', True, search_id, client)
    second = search(owner, 'bella', True, search_id, client)
  mapbox.assert_not_called()
  assert first.provider == 'google' and second[0] == {'id': 'ChIJbellagio', 'name': 'Bellagio', 'description': 'Las Vegas Blvd, Las Vegas, NV',
                                                      'searchId': search_id, 'temporary': True}
  tokens = [call[2]['json']['sessionToken'] for call in owner.session.calls]
  assert len(tokens) == 2 and tokens[0] == tokens[1] and 'X-Goog-FieldMask' not in owner.session.calls[0][2]['headers']
  assert suggestions_budget(owner.google_root).snapshot()['monthUsed'] == 2
  status = owner.select_place('ChIJbellagio', search_id, 'caller', owner.read()['revision'], True)
  assert status['destination']['name'] == 'Bellagio' and status['destination']['address'] == '3600 S Las Vegas Blvd, Las Vegas, NV 89109'
  method, url, request = owner.session.calls[-1]
  assert (method, url) == ('get', 'https://places.googleapis.com/v1/places/ChIJbellagio')
  assert request['params'] == {'sessionToken': tokens[0]} and request['headers']['X-Goog-FieldMask'] == 'location,formattedAddress'
  with pytest.raises(ValidationError, match='cannot be saved'):
    owner.favorite_place('ChIJbellagio', search_id, 'caller', status['revision'], True)


def test_typing_continues_on_mapbox_when_google_fails(owner):
  owner.session = Google(status=403)
  search_id, client = str(uuid.uuid4()), str(uuid.uuid4())
  poi = {'mapbox_id': 'poi/b', 'name': 'Bellagio Mapbox', 'feature_type': 'poi'}
  with patch('openpilot.starpilot.navigation.owner.response_json', return_value={'suggestions': [poi]}) as mapbox:
    first = search(owner, 'bel', True, search_id, client)
    second = search(owner, 'bell', True, search_id, client)
  assert first.provider == second.provider == 'mapbox' and second[0]['name'] == 'Bellagio Mapbox'
  assert len(owner.session.calls) == 1, "after one refusal the session stays on Mapbox"
  assert mapbox.call_args_list[0].args[2]['session_token'] == mapbox.call_args_list[1].args[2]['session_token']
  owner.session = Google(suggestions=[PREDICTION])
  with patch('openpilot.starpilot.navigation.google_places.GoogleBudget.spend', return_value=False), \
       patch('openpilot.starpilot.navigation.owner.response_json', return_value={'suggestions': [poi]}):
    assert search(owner, 'bel', True).provider == 'mapbox'
  assert not owner.session.calls


def test_snapshot_reports_google_place_use_without_the_key(owner):
  places = owner.snapshot()['googlePlaces']
  assert places['usage']['monthLimit'] == 4950 and places['suggestions']['monthLimit'] == 9900
  assert KEY not in json.dumps(owner.snapshot())
