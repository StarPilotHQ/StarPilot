import time
import uuid
from unittest.mock import patch

import pytest

from openpilot.starpilot.navigation.owner import NavigationOwner, ValidationError, ConflictError
from openpilot.starpilot.navigation.runtime import RouteRuntime
from openpilot.starpilot.navigation.tests.test_route_runtime import Executor

POI = {'mapbox_id':'poi/id', 'name':'Coffee Shop', 'feature_type':'poi', 'full_address':'123 Main St, Springfield'}
FEATURE = {'features':[{'properties':POI, 'geometry':{'coordinates':[-90.,40.]}}]}
ADDRESS = {'name':'Old address', 'latitude':39., 'longitude':-91.}


@pytest.fixture
def owner(tmp_path):
  result = NavigationOwner(tmp_path/'saved', runtime_source=lambda:None, transient_root=tmp_path/'boot')
  saved = result.configure({'enabled':True, 'token':'pk.synthetic'}, '0', True)
  result.select(ADDRESS, saved['revision'], True)
  return result


def suggest(owner, caller='caller', client=None):
  search_id, client = str(uuid.uuid4()), client or str(uuid.uuid4())
  with patch('openpilot.starpilot.navigation.owner.response_json', return_value={'suggestions':[POI]}) as provider:
    result = owner.search_places('coffee', caller, search_id, client)
  return result[0], provider.call_args.args[2]['session_token'], client


def choose(owner, result, caller='caller', authorized=True):
  with patch('openpilot.starpilot.navigation.owner.response_json', return_value=FEATURE) as provider:
    value = owner.select_place(result['id'], result['searchId'], caller, owner.read()['revision'], authorized)
  return value, provider


def test_selected_place_routes_without_persisting_temporary_coordinates(owner):
  result, session, _ = suggest(owner)
  assert 'latitude' not in result and result['temporary'] is True
  status, provider = choose(owner, result)
  assert provider.call_args.args[2]['session_token'] == session
  assert status['destination']['name'] == 'Coffee Shop'
  assert status['destination']['address'] == '123 Main St, Springfield'
  assert status['destination']['temporary'] is True
  assert owner.read()['destination'] is None
  assert [row['name'] for row in status['recents']] == ['Old address']
  assert 'Coffee Shop' not in owner.path.read_text()
  second = NavigationOwner(owner.root, runtime_source=lambda:None, transient_root=owner.transient_root)
  executor = Executor()
  runtime = RouteRuntime(second, engine=type('Engine',(),{'fetch':None})(), executor=executor)
  assert runtime.update(10, (9,(-90.01,40.),0.,0.),1)['destinationName'] == 'Coffee Shop'
  assert len(executor.jobs) == 1
  with pytest.raises(ValidationError):
    choose(owner, result)
  with pytest.raises(ValidationError, match='cannot be saved'):
    owner.favorite(status['destination'], status['revision'], True)
  unmarked = {key:value for key,value in status['destination'].items() if key != 'temporary'}
  with pytest.raises(ValidationError, match='cannot be saved'):
    owner.select(unmarked, status['revision'], True)
  with patch('openpilot.starpilot.navigation.owner.time.monotonic', return_value=time.monotonic() + 50000):
    assert owner.read_routing()['destination'] is None
  assert not list(owner.transient_root.glob('*.json'))


def test_recents_move_to_top_dedupe_and_are_editable(owner):
  owner.select({'name': 'Coffee Shop', 'latitude': 40., 'longitude': -90.}, owner.read()['revision'], True)
  cleared = owner.clear(owner.read()['revision'], True)
  assert cleared['destination'] is None and len(cleared['recents']) == 2
  again = owner.select(ADDRESS, cleared['revision'], True)
  assert [row['name'] for row in again['recents']] == ['Old address', 'Coffee Shop']
  removed = owner.remove_recent(again['recents'][1]['id'], again['revision'], True)
  assert [row['name'] for row in removed['recents']] == ['Old address']
  assert owner.clear_recents(removed['revision'], True)['recents'] == []
  revision = owner.read()['revision']
  for index in range(12):
    revision = owner.select({'name': f'Place {index}', 'latitude': 30. + index, 'longitude': -90.}, revision, True)['revision']
  recents = owner.read()['recents']
  assert len(recents) == 10 and recents[0]['name'] == 'Place 11'


def test_favorite_edits_keep_route_and_labels_are_unique(owner):
  status = owner.select({'name': 'Coffee Shop', 'latitude': 40., 'longitude': -90.}, owner.read()['revision'], True)
  saved = owner.favorite(ADDRESS,status['revision'],True)
  saved = owner.favorite(status['destination'],saved['revision'],True)
  assert saved['destination']['name'] == 'Coffee Shop'
  assert [row['name'] for row in saved['favorites']] == ['Old address', 'Coffee Shop']
  coffee, old = saved['favorites'][1]['id'], saved['favorites'][0]['id']
  home = owner.label_favorite(coffee, 'home', saved['revision'], True)
  assert [(row['name'], row.get('label')) for row in home['favorites']] == [('Coffee Shop', 'home'), ('Old address', None)]
  moved = owner.label_favorite(old, 'home', home['revision'], True)
  assert [(row['name'], row.get('label')) for row in moved['favorites']] == [('Old address', 'home'), ('Coffee Shop', None)]
  work = owner.label_favorite(coffee, 'work', moved['revision'], True)
  assert [row.get('label') for row in work['favorites']] == ['home', 'work']
  unlabeled = owner.label_favorite(coffee, None, work['revision'], True)
  assert 'label' not in unlabeled['favorites'][1]
  with pytest.raises(ValidationError):
    owner.label_favorite(coffee, 'gym', unlabeled['revision'], True)
  with pytest.raises(ValidationError):
    owner.label_favorite('missing', 'home', unlabeled['revision'], True)
  removed = owner.remove_favorite(old,unlabeled['revision'],True)
  assert removed['destination']['name'] == 'Coffee Shop' and [row['name'] for row in removed['favorites']] == ['Coffee Shop']


def test_suggestions_cannot_be_saved_but_remain_choosable(owner):
  result, _, _ = suggest(owner)
  before = owner.read()
  with patch('openpilot.starpilot.navigation.owner.response_json') as provider:
    with pytest.raises(ValidationError, match='cannot be saved'):
      owner.favorite_place(result['id'], result['searchId'], 'caller', before['revision'], True)
    provider.assert_not_called()
  assert owner.read() == before
  assert choose(owner, result)[0]['destination']['temporary'] is True


def test_address_search_requests_only_temporary_results(owner):
  with patch('openpilot.starpilot.navigation.owner.response_json', return_value={'features': []}) as provider:
    assert owner.search('100 Main Street') == []
  request = provider.call_args.args[2]
  assert request['permanent'] == 'false'
  assert request['autocomplete'] == 'false'
  assert owner.geocode_budget.used == 1


def test_address_search_stops_before_temporary_free_allowance(owner):
  assert owner.geocode_budget.limit == 99_000
  owner.geocode_budget.used = owner.geocode_budget.limit
  with patch('openpilot.starpilot.navigation.owner.response_json') as provider:
    with pytest.raises(ValidationError, match='free Mapbox address lookups'):
      owner.search('100 Main Street')
  provider.assert_not_called()


def test_place_fallback_never_requests_permanent_geocoding(owner):
  with patch('openpilot.starpilot.navigation.owner.response_json', side_effect=[{'suggestions': []}, {'features': []}]) as provider:
    assert owner.search_places('100 Main Street', 'caller', str(uuid.uuid4()), str(uuid.uuid4())) == []
  assert len(provider.call_args_list) == 2
  assert provider.call_args_list[-1].args[2]['permanent'] == 'false'


def test_home_work_remain_on_device_after_restart_without_search(owner):
  revision = owner.read()['revision']
  for label, place in (('home', ADDRESS), ('work', {'name': 'Work', 'latitude': 40., 'longitude': -90.})):
    revision = owner.favorite(place, revision, True, label=label)['revision']
  with patch('openpilot.starpilot.navigation.owner.response_json', side_effect=AssertionError('saved destinations must not search')):
    restored = NavigationOwner(owner.root, runtime_source=lambda: None, transient_root=owner.transient_root)
    favorites = restored.read()['favorites']
    assert {place['label'] for place in favorites} == {'home', 'work'}
    selected = restored.select(favorites[0], restored.read()['revision'], True)
    assert selected['destination']['name'] == favorites[0]['name']


def test_temporary_destination_supports_full_unicode_address(owner):
  result, _, _ = suggest(owner)
  feature = {'features': [{'properties': {**POI, 'name': '🏠' * 256, 'full_address': '🏠' * 512},
                           'geometry': {'coordinates': [-90., 40.]}}]}
  with patch('openpilot.starpilot.navigation.owner.response_json', return_value=feature):
    status = owner.select_place(result['id'], result['searchId'], 'caller', owner.read()['revision'], True)
  assert status['destination']['address'] == '🏠' * 512
  assert owner.read()['destination'] is None


def test_save_home_and_work_in_one_change_without_changing_destination(owner):
  saved = owner.favorite(ADDRESS, owner.read()['revision'], True, label='home')
  before = owner.read()
  coffee = {'name': 'Coffee Shop', 'latitude': 40., 'longitude': -90.}
  saved = owner.favorite(coffee, before['revision'], True, label='home')
  assert [(row['name'], row.get('label')) for row in saved['favorites']] == [('Coffee Shop', 'home'), ('Old address', None)]
  assert saved['destination']['name'] == 'Old address'
  before = owner.read()
  with pytest.raises(ValidationError):
    owner.favorite(ADDRESS, before['revision'], True, label='invalid')
  assert owner.read() == before


def test_legacy_settings_without_recents_still_load(owner):
  import json
  value = json.loads(owner.path.read_text())
  del value['recents']
  owner.path.write_text(json.dumps(value))
  assert owner.read()['recents'] == []
  assert owner.snapshot()['destination']['name'] == 'Old address'


def test_rejected_authority_and_interrupted_commit_keep_previous_route(owner):
  result, _, _ = suggest(owner)
  choose(owner,result)
  prior = owner.snapshot()
  result, _, _ = suggest(owner)
  checks = iter((True,True,False))
  with pytest.raises(PermissionError):
    choose(owner,result,authorized=lambda:next(checks))
  assert owner.snapshot()['destination'] == prior['destination']
  assert owner.snapshot()['revision'] == prior['revision']
  result, _, _ = suggest(owner)
  with patch('openpilot.starpilot.navigation.owner.os.replace', side_effect=lambda source,dest,**kw:
             (_ for _ in ()).throw(OSError('synthetic crash')) if dest == owner.path else
             __import__('os').rename(source,dest,**kw)):
    with pytest.raises(OSError):
      choose(owner,result)
  assert owner.snapshot()['destination'] == prior['destination']


def test_sessions_are_caller_and_tab_scoped_cancelled_expired_and_single_use(owner):
  first, session1, client = suggest(owner)
  other, session2, _ = suggest(owner)
  assert session1 != session2
  newest, _, _ = suggest(owner, client=client)
  with pytest.raises(ValidationError):
    choose(owner,first)
  with pytest.raises(ValidationError):
    choose(owner,newest,caller='other-caller')
  owner.cancel_search('caller', newest['searchId'])
  with pytest.raises(ValidationError):
    choose(owner,newest)
  with patch('openpilot.starpilot.navigation.owner.time.monotonic',return_value=time.monotonic()+181):
    with pytest.raises(ValidationError):
      choose(owner,other)
  for _ in range(8):
    suggest(owner,caller='bounded')
  with pytest.raises(ValidationError,match='Too many'):
    suggest(owner,caller='bounded')


def test_invalid_retrieve_and_stale_revision_do_not_change_saved_destination(owner):
  result, _, _ = suggest(owner)
  before = owner.path.read_bytes()
  with patch('openpilot.starpilot.navigation.owner.response_json',return_value={'features':[]}):
    with pytest.raises(ValidationError):
      owner.select_place(result['id'],result['searchId'],'caller',owner.read()['revision'],True)
  assert owner.path.read_bytes() == before
  result, _, _ = suggest(owner)
  owner.favorite(ADDRESS,owner.read()['revision'],True)
  with pytest.raises(ValidationError):
    choose(owner,result)


def test_address_fallback_and_symlink_safe_ephemeral_state(owner,tmp_path):
  with patch('openpilot.starpilot.navigation.owner.response_json',return_value={'suggestions':[]}), \
       patch.object(owner,'search',return_value=[ADDRESS]) as fallback:
    assert owner.search_places('100 Main','caller',str(uuid.uuid4()),str(uuid.uuid4())) == [ADDRESS]
  fallback.assert_called_once_with('100 Main')
  owner.transient_root.rmdir()
  owner.transient_root.symlink_to(tmp_path)
  with pytest.raises(OSError):
    owner.record_routes(owner.read()['revision'], [])


def test_cancel_during_provider_does_not_start_address_fallback(owner):
  search_id, client_id = str(uuid.uuid4()), str(uuid.uuid4())
  def cancelled(*args):
    owner.cancel_search('caller',search_id)
    return {'suggestions':[]}
  with patch('openpilot.starpilot.navigation.owner.response_json',side_effect=cancelled), \
       patch.object(owner,'search') as fallback:
    with pytest.raises(ValidationError):
      owner.search_places('coffee','caller',search_id,client_id)
    fallback.assert_not_called()


def test_slow_retrieve_allows_keychange_and_rejects_stale_result(owner):
  import threading
  result, _, _ = suggest(owner)
  entered, release = threading.Event(), threading.Event()
  errors = []
  def slow(*args):
    entered.set()
    assert release.wait(2)
    return FEATURE
  def worker():
    try:
      owner.select_place(result['id'],result['searchId'],'caller',owner.read()['revision'],True)
    except Exception as error:
      errors.append(error)
  with patch('openpilot.starpilot.navigation.owner.response_json',side_effect=slow):
    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(2)
    try:
      changed = owner.configure({'token':'pk.changed'},owner.read()['revision'],True)
      assert changed['destination']['name'] == 'Old address'
    finally:
      release.set()
      thread.join(2)
  assert not thread.is_alive() and len(errors) == 1 and isinstance(errors[0], ConflictError)
  assert owner.snapshot()['destination']['name'] == 'Old address'
