from concurrent.futures import Future
from itertools import product
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openpilot.starpilot.navigation.owner import ConflictError, NavigationOwner, ROUTE_AVOIDANCES, ValidationError
from openpilot.starpilot.navigation.route_engine import MapboxRouteEngine, NavigationRoute
from openpilot.starpilot.navigation.runtime import RouteRuntime

PLACE = {'name': 'Library', 'longitude': .01, 'latitude': 0.}
ROUTE = {'distance': 1112., 'duration': 100., 'geometry': {'coordinates': [[0., 0.], [.01, 0.]]},
         'legs': [{'steps': [{'distance': 1112., 'duration': 100., 'maneuver': {'type': 'depart', 'instruction': 'Head east'}}]}]}


@pytest.fixture
def owner(tmp_path, monkeypatch):
  monkeypatch.setattr(NavigationOwner, '_is_metric', lambda self: True)
  return NavigationOwner(tmp_path, runtime_source=lambda: None, transient_root=tmp_path / 'runtime')


def test_gps_source_defaults_saves_and_rejects_unknown(owner):
  assert owner.snapshot()['gpsSource'] == 'backup'
  saved = owner.configure({'gpsSource': 'phone'}, '0', True)
  assert owner.snapshot()['gpsSource'] == 'phone'
  assert json.loads(owner.path.read_text())['gpsSource'] == 'phone'
  with pytest.raises(ValidationError):
    owner.configure({'gpsSource': 'tunnel'}, saved['revision'], True)


def test_preferences_default_and_survive_restart(owner):
  assert all(owner.snapshot()[key] is False for key in ROUTE_AVOIDANCES)
  saved = owner.configure({'enabled': True, 'token': 'pk.test', 'avoidTolls': True}, '0', True)
  owner.select(PLACE, saved['revision'], True)
  restarted = NavigationOwner(owner.root, runtime_source=lambda: None, transient_root=owner.transient_root)
  assert restarted.read()['avoidTolls'] is True
  assert restarted.read()['avoidHighways'] is False
  assert restarted.snapshot()['avoidFerries'] is False
  assert restarted.snapshot()['destination']['name'] == 'Library'


def test_turn_slowdown_preferences_are_saved_without_rerouting(owner):
  assert owner.snapshot()['turnSlowdownPercent'] == 50
  saved = owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  selected = owner.select(PLACE, saved['revision'], True)
  routing_revision = owner.read()['routingRevision']
  for value in (True, -1, 101, 50.0, '50'):
    with pytest.raises(ValidationError):
      owner.configure({'turnSlowdownPercent': value}, selected['revision'], True)
  with pytest.raises(PermissionError):
    owner.configure({'turnSlowdownPercent': 0}, selected['revision'], False)
  updated = owner.configure({'turnSlowdownPercent': 0}, selected['revision'], True)
  assert updated['turnSlowdownPercent'] == 0
  assert updated['destination'] == selected['destination']
  assert owner.read()['routingRevision'] == routing_revision
  restarted = NavigationOwner(owner.root, runtime_source=lambda: None, transient_root=owner.transient_root)
  assert restarted.read()['turnSlowdownPercent'] == 0


def test_old_settings_default_without_rewriting(owner):
  owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  document = json.loads(owner.path.read_text())
  for key in ROUTE_AVOIDANCES:
    document.pop(key)
  old = json.dumps(document)
  owner.path.write_text(old)
  assert all(owner.read()[key] is False for key in ROUTE_AVOIDANCES)
  assert owner.path.read_text() == old
  with patch('openpilot.starpilot.navigation.owner.json.loads', side_effect=AssertionError('reparsed')):
    assert owner.read()['avoidTolls'] is False


@pytest.mark.parametrize('key', ROUTE_AVOIDANCES)
@pytest.mark.parametrize('value', ['true', 1, None, [], {}])
def test_preferences_reject_non_boolean_values(owner, key, value):
  with pytest.raises(ValidationError, match='on or off'):
    owner.configure({key: value}, '0', True)
  assert not owner.path.exists()


def test_preferences_require_current_revision_and_authorization(owner):
  saved = owner.configure({'avoidTolls': True}, '0', True)
  with pytest.raises(ConflictError):
    owner.configure({'avoidTolls': False}, '0', True)
  with pytest.raises(PermissionError):
    owner.configure({'avoidTolls': False}, saved['revision'], False)
  assert owner.read()['avoidTolls'] is True


@pytest.mark.parametrize('temporary', [False, True])
def test_changing_preferences_preserves_destination_and_resets_alternative(owner, temporary):
  saved = owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  if temporary:
    owner._change(lambda doc: doc.update(destination=None), saved['revision'], True, active_destination=PLACE)
  else:
    owner.select(PLACE, saved['revision'], True)
  owner.record_routes(owner.read()['revision'], [NavigationRoute(ROUTE), NavigationRoute(ROUTE)])
  selected = owner.select_route(1, owner.read()['revision'], True)
  assert selected['selectedRoute'] == 1
  changed = owner.configure({'avoidFerries': True}, selected['revision'], True)
  assert changed['destination']['name'] == 'Library'
  assert bool(changed['destination'].get('temporary')) is temporary
  assert changed['selectedRoute'] == 0 and changed['alternatives'] == []
  assert (owner.read()['destination'] is None) is temporary


@pytest.mark.parametrize('flags', list(product([False, True], repeat=3)))
def test_mapbox_exclusions_for_every_combination(owner, flags):
  settings = owner.configure(dict(zip(ROUTE_AVOIDANCES, flags, strict=True)), '0', True)
  exclusions = tuple(value for key, value in ROUTE_AVOIDANCES.items() if settings[key])
  with patch('openpilot.starpilot.navigation.route_engine.response_json', return_value={'code': 'Ok', 'routes': [ROUTE]}) as request:
    route = MapboxRouteEngine(None).fetch('pk.test', (0., 0.), PLACE, 90., exclusions)
  params = request.call_args.args[2]
  assert params.get('exclude') == (','.join(exclusions) if exclusions else None)
  assert params['bearings'] == '90,90;' and params['alternatives'] == 'true'
  assert route.total_distance == 1112.


class Executor:
  def __init__(self):
    self.calls = []
    self.jobs = []

  def submit(self, *args):
    self.calls.append(args)
    future = Future()
    self.jobs.append(future)
    return future


@pytest.mark.parametrize('saved_origin', [False, True])
@pytest.mark.parametrize('inflight', [False, True])
def test_preference_change_discards_old_route_and_fetches_again(owner, saved_origin, inflight):
  saved = owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  owner.select(PLACE, saved['revision'], True)
  owner.position_store.record({'longitude': 0., 'latitude': 0., 'bearing': 90.})
  position = None if saved_origin else (9, (0., 0.), 5., 90.)
  executor = Executor()
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)
  runtime.update(10, position, 1)
  assert executor.calls[0][-1] == ()
  if not inflight:
    executor.jobs[0].set_result(NavigationRoute(ROUTE))
    assert runtime.update(11, position, 1)['route']
  owner.configure({'avoidTolls': True, 'avoidHighways': True, 'avoidFerries': True}, owner.read()['revision'], True)
  changed = runtime.update(12, position, 1)
  assert changed['route'] == [] and not changed['controlValid']
  if inflight:
    executor.jobs[0].set_result(NavigationRoute(ROUTE))
    assert runtime.update(13, position, 1)['route'] == []
  assert len(executor.jobs) == 2
  assert executor.calls[1][-1] == ('toll', 'motorway', 'ferry')
  executor.jobs[1].set_result(NavigationRoute(ROUTE))
  assert runtime.update(14, position, 1)['route']
  if not saved_origin:
    off_route = (15, (0., .01), 5., 90.)
    runtime.update(15, off_route, 1)
    runtime.update(2_000_000_016, off_route, 1)
    runtime.update(2_000_000_017, off_route, 1)
    assert len(executor.jobs) == 3
    assert executor.calls[2][-1] == ('toll', 'motorway', 'ferry')
