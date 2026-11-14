from concurrent.futures import Future
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.starpilot.navigation.owner import NavigationOwner
from openpilot.starpilot.navigation.route_engine import NavigationRoute, project, distance
from openpilot.starpilot.navigation.runtime import RouteRuntime, location, publish_status


@pytest.mark.parametrize('changes, expected', [
  ({0: 1}, [0, 5, 10]),  # sparse/background measurements keep the normal heartbeat
  ({index: index + 1 for index in range(11)}, list(range(11))),
  ({0: 1, 2: 2, 3: 0}, [0, 2, 3, 8]),  # replacement and expiry are forwarded immediately
])
def test_navigation_forwards_new_phone_fixes_without_waiting_for_heartbeat(monkeypatch, changes, expected):
  from openpilot.cereal import messaging
  from openpilot.starpilot.navigation import runtime as module

  frame = [0]
  stamp = [0]
  phone = SimpleNamespace(speed=0.)
  def now():
    return 100_000_000_000 + frame[0] * 200_000_000

  def read_phone(_now):
    stamp[0] = changes.get(frame[0], stamp[0])
    return (99_000_000_000 + stamp[0], phone) if stamp[0] else None

  class Finished(Exception):
    pass

  class Rate:
    def __init__(self, hz):
      assert hz == 5.

    def keep_time(self):
      frame[0] += 1
      if frame[0] > 10:
        raise Finished

  owner, runtime = Mock(), Mock()
  # Only device-state validity is relevant to this loop; source selection is tested separately.
  class Master:
    valid, logMonoTime = {'deviceState': False}, {'deviceState': 0}
    def update(self, _timeout):
      pass
    def __getitem__(self, _service):
      return SimpleNamespace(startedMonoTime=0)

  published = []
  monkeypatch.setitem(sys.modules, 'openpilot.common.realtime', SimpleNamespace(Ratekeeper=Rate))
  monkeypatch.setattr(module, 'NavigationOwner', lambda: owner)
  monkeypatch.setattr(module, 'RouteRuntime', lambda _owner: runtime)
  monkeypatch.setattr(module.time, 'monotonic_ns', now)
  monkeypatch.setattr(module, 'read_phone_gps', read_phone)
  monkeypatch.setattr(module, 'location', lambda _sm, _now, fix: fix)
  monkeypatch.setattr(module, 'publish_status', lambda _pm, _value, fix: published.append((frame[0], fix)))
  monkeypatch.setattr(messaging, 'SubMaster', lambda _services: Master())
  monkeypatch.setattr(messaging, 'PubMaster', lambda _services: None)
  with pytest.raises(Finished):
    module.main()
  assert [index for index, _fix in published] == expected
  assert runtime.update.call_count == len(expected)
  if changes == {0: 1}:
    assert {fix[0] for _index, fix in published} == {99_000_000_001}, 'held fixes keep their measurement time'
  owner.position_store.flush.assert_called_once_with(force=True)


@pytest.fixture
def route():
  return NavigationRoute({'distance': 1112., 'duration': 100., 'geometry': {'coordinates': [[0., 0.], [.01, 0.]]}, 'legs': [{'steps': [
    {'distance': 1112., 'duration': 100., 'maneuver': {'type': 'depart', 'instruction': 'Head east'}, 'bannerInstructions': [
      {'primary': {'type': 'turn', 'modifier': 'right', 'text': 'Turn right on First Street'}}]},
    {'distance': 0., 'duration': 0., 'maneuver': {'type': 'arrive', 'instruction': 'You have arrived'}}]}]})


def test_route_interpolation_preserves_distance_and_advice(route):
  progress = route.progress((.005, 0.), 5., 90.)
  assert abs(progress.instruction['distanceMeters'] - 556.) < .01
  assert progress.instruction['text'] == 'Turn right on First Street'
  assert not progress.off_route and not progress.arrived
  assert route.progress((.005, 0.), 10., 270.).off_route
  assert not route.progress((.005, 0.), 5., 270.).off_route, "turning around on a ramp or in a lot is not off the route"
  assert not route.progress((.005, .0005), 2., 90.).off_route and route.progress((.005, .0008), 2., 90.).off_route
  assert route.progress((.01, 0.), 0.).arrived
  assert project((0, 0), (0, 0), (0, .001))[0] == 0
  assert distance((0, 0), (0, .001)) == pytest.approx(111.195, abs=.01)


class Executor:
  def __init__(self):
    self.jobs = []
  def submit(self, *args):
    future = Future()
    self.jobs.append(future)
    return future


def configured(tmp_path):
  class MetricNavigationOwner(NavigationOwner):
    def _is_metric(self) -> bool:
      return True
  owner = MetricNavigationOwner(tmp_path, runtime_source=lambda: None)
  settings = owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  owner.select({'name': 'Destination', 'longitude': .01, 'latitude': 0.}, settings['revision'], True)
  return owner


def test_generation_discard_and_current_drive_control(tmp_path, route):
  owner, executor = configured(tmp_path), Executor()
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)
  assert runtime.update(10_000_000_000, (9_999_000_000, (0., 0.), 5., 90.), 9_000_000_000)['status'] == 'routing'
  owner.clear(owner.read()['revision'], True)
  executor.jobs[0].set_result(route)
  assert runtime.update(11_000_000_000, (10_999_000_000, (0., 0.), 5., 90.), 9_000_000_000)['status'] == 'noDestination'
  assert runtime.route is None
  owner.select({'name': 'Destination', 'longitude': .01, 'latitude': 0.}, owner.read()['revision'], True)
  runtime.update(12_000_000_000, (11_999_000_000, (0., 0.), 5., 90.), 9_000_000_000)
  executor.jobs[1].set_result(route)
  result = runtime.update(13_000_000_000, (12_999_000_000, (0., 0.), 5., 90.), 9_000_000_000)
  assert result['status'] == 'guiding' and result['controlValid']
  result = runtime.update(14_000_000_000, None, 9_000_000_000)
  assert result['status'] == 'waitingForLocation' and not result['controlValid']
  result = runtime.update(15_000_000_000, (14_999_000_000, (0., 0.), 5., 90.), 14_000_000_000)
  assert result['status'] == 'routing' and not result['controlValid']


@pytest.mark.parametrize('change', ['cancelReselect', 'sameDestination', 'avoidanceRoundTrip', 'providerRoundTrip', 'disableEnable'])
def test_route_intent_changes_between_polls_discard_delayed_response(tmp_path, route, change):
  owner, executor = configured(tmp_path), Executor()
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)
  position = (10_000_000_000, (.001, 0.), 5., 90.)
  runtime.update(10_000_000_000, position, 1)
  selected = owner.read()['destination']
  if change == 'cancelReselect':
    owner.clear(owner.read()['revision'], True)
    owner.select(selected, owner.read()['revision'], True)
  elif change == 'sameDestination':
    owner.select(selected, owner.read()['revision'], True)
  else:
    key, first, second = {'avoidanceRoundTrip': ('avoidTolls', True, False),
                          'providerRoundTrip': ('routeProvider', 'google', 'mapbox'),
                          'disableEnable': ('enabled', False, True)}[change]
    owner.configure({key: first}, owner.read()['revision'], True)
    owner.configure({key: second}, owner.read()['revision'], True)
  executor.jobs[0].set_result(route)
  result = runtime.update(11_000_000_000, (11_000_000_000, *position[1:]), 1)
  assert result['status'] == 'routing' and not result['controlValid']
  assert runtime.route is None and len(executor.jobs) == 2
  executor.jobs[1].set_result(route)
  assert runtime.update(12_000_000_000, (12_000_000_000, *position[1:]), 1)['controlValid']


def test_arrival_ends_navigation_after_a_short_stop(tmp_path, route):
  owner, executor = configured(tmp_path), Executor()
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)
  def at(t, speed=0.):
    return runtime.update(t * 1_000_000_000, (t * 1_000_000_000 - 1_000_000, (.01, 0.), speed, 90.), 1_000_000_000)
  runtime.update(9_000_000_000, (8_999_000_000, (0., 0.), 5., 90.), 1_000_000_000)  # the drive starts away from it
  at(10, 5.)
  executor.jobs[0].set_result(route)
  assert at(11, 5.)['status'] == 'guiding'
  assert at(12)['status'] == 'guiding'  # stopping starts the arrival clock
  assert at(17)['status'] == 'arrived'
  assert owner.read()['destination'] is not None
  assert at(20, 5.)['status'] == 'guiding', 'driving off resets arrival'
  at(21)
  assert at(35)['status'] == 'arrived' and owner.read()['destination'] is not None
  assert at(36)['status'] == 'arrived'
  assert owner.read()['destination'] is None, 'navigation ends 15 s after stopping at the destination'
  assert at(37)['status'] == 'noDestination'


def guided(tmp_path, route):
  owner, executor = configured(tmp_path), Executor()
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)
  def at(t, coordinates=(.005, 0.), speed=5., drive=1_000_000_000):
    fix = None if coordinates is None else (t * 1_000_000_000 - 1_000_000, coordinates, speed, 90.)
    return runtime.update(t * 1_000_000_000, fix, drive)
  at(10)
  executor.jobs[0].set_result(route)
  assert at(11)['status'] == 'guiding'
  return owner, runtime, at


def test_stopping_near_the_destination_arrives_short_of_the_route_end(tmp_path, route):
  owner, _runtime, at = guided(tmp_path, route)
  # ~110 m short of the pin: a stop at a light, not an arrival
  assert at(12, (.009, 0.), 0.)['status'] == 'guiding'
  assert at(30, (.009, 0.), 0.)['status'] == 'guiding' and owner.read()['destination'] is not None
  # ~55 m short, where a parking lot entrance would be: beyond the route-end check's 40 m
  assert at(31, (.0095, 0.), 0.)['status'] == 'guiding'
  assert at(36, (.0095, 0.), 0.)['status'] == 'arrived'
  assert at(46, (.0095, 0.), 0.)['status'] == 'arrived'
  assert owner.read()['destination'] is None


def test_drive_ending_in_a_garage_near_the_destination_ends_navigation(tmp_path, route):
  owner, _runtime, at = guided(tmp_path, route)
  at(12, (.008, 0.), 3.)  # last fix ~220 m out, entering the garage
  at(13, None)  # GPS lost inside
  at(20, None, drive=0)
  at(24, None, drive=0)
  assert owner.read()['destination'] is not None, 'a deviceState hiccup is not the car turning off'
  at(25, None, drive=0)
  assert owner.read()['destination'] is None
  assert at(26, None, drive=0)['status'] == 'noDestination'


def test_brief_stop_far_from_the_destination_keeps_navigation(tmp_path, route):
  owner, _runtime, at = guided(tmp_path, route)
  at(12, (.0, 0.), 0.)  # ~1.1 km out: a gas station on the way
  at(13, None, drive=0)
  at(30, None, drive=0)
  assert owner.read()['destination'] is not None
  assert at(31, (.0, 0.), drive=31_000_000_000)['status'] == 'routing'
  assert owner.read()['destination'] is not None


def test_device_state_hiccup_near_the_destination_keeps_navigation(tmp_path, route):
  owner, _runtime, at = guided(tmp_path, route)
  at(12, (.008, 0.), 10.)
  at(13, (.008, 0.), 10., drive=0)
  at(16, (.008, 0.), 10., drive=0)
  assert at(17, (.0085, 0.), 10.)['status'] == 'routing'
  assert owner.read()['destination'] is not None


def test_drive_starting_near_the_destination_ends_navigation(tmp_path):
  # A reboot in the garage: the saved position is where the car was parked
  owner = configured(tmp_path)
  owner.position_store.record({'longitude': .008, 'latitude': 0.})
  owner.position_store.flush(force=True)
  executor = Executor()
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)
  assert runtime.update(10_000_000_000, None, 9_000_000_000)['status'] == 'noDestination'
  assert owner.read()['destination'] is None and not executor.jobs


def test_first_fix_near_the_destination_ends_navigation(tmp_path):
  owner = configured(tmp_path)
  executor = Executor()
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)
  assert runtime.update(10_000_000_000, None, 9_000_000_000)['status'] == 'waitingForLocation'
  runtime.update(11_000_000_000, (10_999_000_000, (.008, 0.), 2., 90.), 9_000_000_000)
  assert owner.read()['destination'] is None
  # Only the drive's first fix counts: a destination picked later is routed even nearby
  owner.select({'name': 'Next door', 'longitude': .009, 'latitude': 0.}, owner.read()['revision'], True)
  assert runtime.update(12_000_000_000, (11_999_000_000, (.008, 0.), 2., 90.), 9_000_000_000)['status'] == 'routing'


def test_parked_arrival_retries_after_clear_io_failure(tmp_path, monkeypatch, route):
  owner, _runtime, at = guided(tmp_path, route)
  clear = owner.clear
  calls = 0
  def fail_once(*args):
    nonlocal calls
    calls += 1
    if calls == 1:
      raise OSError('temporary write failure')
    return clear(*args)
  monkeypatch.setattr(owner, 'clear', fail_once)
  at(12, (.008, 0.), 3.)
  at(13, None, drive=0)
  at(18, None, drive=0)
  assert owner.read()['destination'] is not None
  at(19, None, drive=0)
  assert calls == 2 and owner.read()['destination'] is None


def test_arrival_does_not_override_a_newer_destination(tmp_path, route):
  owner, executor = configured(tmp_path), Executor()
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)
  def at(t):
    return runtime.update(t * 1_000_000_000, (t * 1_000_000_000 - 1_000_000, (.01, 0.), 0., 90.), 1_000_000_000)
  runtime.update(9_000_000_000, (8_999_000_000, (0., 0.), 5., 90.), 1_000_000_000)
  at(10)
  executor.jobs[0].set_result(route)
  at(11)
  stale = owner.read()['revision']
  owner.select({'name': 'Elsewhere', 'longitude': .02, 'latitude': 0.}, stale, True)
  runtime._end_route(stale)
  assert owner.read()['destination']['name'] == 'Elsewhere'


def test_arrival_retries_after_clear_io_failure(tmp_path, monkeypatch):
  owner = configured(tmp_path)
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=Executor())
  revision = owner.read()['revision']
  clear = owner.clear
  calls = 0
  def fail_once(*args):
    nonlocal calls
    calls += 1
    if calls == 1:
      raise OSError('temporary write failure')
    return clear(*args)
  monkeypatch.setattr(owner, 'clear', fail_once)
  runtime._end_route(revision)
  assert owner.read()['destination'] is not None
  runtime._end_route(revision)
  assert calls == 2 and owner.read()['destination'] is None


def test_route_failure_backoff_and_no_parallel_requests(tmp_path):
  executor = Executor()
  runtime = RouteRuntime(configured(tmp_path), engine=SimpleNamespace(fetch=None), executor=executor)
  runtime.update(10, (9, (0., 0.), 5., 90.), 1)
  for now in range(11, 20):
    runtime.update(now, (now, (0., 0.), 5., 90.), 1)
  assert len(executor.jobs) == 1
  executor.jobs[0].set_exception(ValueError('invalid route'))
  assert runtime.update(20, (19, (0., 0.), 5., 90.), 1)['status'] == 'routeUnavailable'
  assert len(executor.jobs) == 1


def test_location_requires_fix_accuracy_and_freshness():
  gps = SimpleNamespace(source='qcomdiag', latitude=0., longitude=0., speed=1., bearingDeg=0., horizontalAccuracy=5., hasFix=True)
  class SM(dict):
    valid = {'gpsLocation': True, 'gpsLocationExternal': True}
    logMonoTime = {'gpsLocation': 10_000_000_000, 'gpsLocationExternal': 9_000_000_000}
  sm = SM(gpsLocation=gps, gpsLocationExternal=SimpleNamespace(**{**vars(gps), 'source': 'ublox'}))
  sm.valid['gpsLocationExternal'] = False
  assert location(sm, 11_000_000_000)[0] == 10_000_000_000
  assert location(sm, 13_000_000_000) is None
  gps.horizontalAccuracy = 30.
  assert location(sm, 11_000_000_000) is None


def test_steps_without_banner_describe_next_maneuver_not_previous_turn():
  route = NavigationRoute({'distance': 1112., 'duration': 100.,
                           'geometry': {'coordinates': [[0., 0.], [.005, 0.], [.01, 0.]]},
                           'legs': [{'steps': [
                             {'distance': 556., 'duration': 50., 'maneuver': {'type': 'depart', 'instruction': 'Head east'}},
                             {'distance': 556., 'duration': 50., 'maneuver': {'type': 'turn', 'modifier': 'right', 'instruction': 'Turn right'}},
                             {'distance': 0., 'duration': 0., 'maneuver': {'type': 'arrive', 'instruction': 'Destination'}}]}]})
  first = route.progress((.004, 0.), 5.)
  after_turn = route.progress((.006, 0.), 5.)
  assert first.instruction['maneuverType'] == 'turn'
  assert after_turn.instruction['maneuverType'] == 'arrive'


def test_favorite_edits_preserve_inflight_and_guiding_route(tmp_path, route):
  owner, executor = configured(tmp_path), Executor()
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)
  position = (9, (.001, 0.), 5., 90.)
  runtime.update(10, position, 1)
  favorite = {'name': 'Home', 'longitude': .02, 'latitude': .01}
  owner.favorite(favorite, owner.read()['revision'], True)
  executor.jobs[0].set_result(route)
  result = runtime.update(11, position, 1)
  assert result['status'] == 'guiding' and len(executor.jobs) == 1
  assert result['revision'] == owner.read()['revision']
  identity = owner.read()['favorites'][0]['id']
  owner.remove_favorite(identity, owner.read()['revision'], True)
  result = runtime.update(12, position, 1)
  assert result['status'] == 'guiding' and len(executor.jobs) == 1
  assert runtime.route is route and result['revision'] == owner.read()['revision']
  owner.configure({'token': 'pk.replaced'}, owner.read()['revision'], True)
  assert runtime.update(13, position, 1)['status'] == 'routing'
  assert len(executor.jobs) == 2


def test_published_navigation_reaches_status_and_control_consumers(tmp_path, route):
  from openpilot.cereal import messaging
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.starpilot.navigation.intent import current_instruction
  from openpilot.starpilot.navigation.status import NavigationStatusSource

  with OpenpilotPrefix():
    services = ['starpilotNavigation', 'deviceState', 'carState', 'carControl']
    sm = messaging.SubMaster(services)
    publisher = messaging.PubMaster(['starpilotNavigation'])
    receiver = messaging.sub_sock('starpilotNavigation', timeout=1000)
    status = NavigationStatusSource()
    status.sm = sm
    now = time.monotonic_ns()
    drive = now - 1_000_000_000
    executor = Executor()
    runtime = RouteRuntime(configured(tmp_path), engine=SimpleNamespace(fetch=None), executor=executor)
    position = (now, (.001, 0.), 5., 90.)
    runtime.update(now, position, drive)
    executor.jobs[0].set_result(route)

    for active_drive in (drive, 0):
      if not active_drive:
        runtime.update(now, position, 0)
        executor.jobs[-1].set_result(route)
      value = runtime.update(now, position, active_drive)
      publish_status(publisher, value)
      received = messaging.recv_one(receiver)
      assert received is not None and received.valid
      assert received.starpilotNavigation.navigation.controlValid is bool(active_drive)

      device = messaging.new_message('deviceState', valid=True)
      device.deviceState.started, device.deviceState.startedMonoTime = bool(active_drive), active_drive
      cs = messaging.new_message('carState', valid=True)
      cs.carState.canValid, cs.carState.gearShifter = True, 'drive'
      cc = messaging.new_message('carControl', valid=True)
      sm.update_msgs(time.monotonic(), [received, device, cs, cc])
      snapshot = status.snapshot()
      assert snapshot is not None and snapshot['status'] == 'guiding'
      instruction = current_instruction(sm, time.monotonic_ns())
      assert (instruction is not None) is bool(active_drive)


def saved_origin_runtime(tmp_path, monkeypatch):
  # These routing tests do not need the native Params library or boot-specific IPC paths.
  monkeypatch.setattr(NavigationOwner, '_is_metric', lambda self: True)
  owner = NavigationOwner(tmp_path, runtime_source=lambda: None, transient_root=tmp_path / 'runtime')
  settings = owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  owner.select({'name': 'Destination', 'longitude': .01, 'latitude': 0.}, settings['revision'], True)
  executor = Executor()
  executor.calls = []
  submit = executor.submit
  def record_submit(*args):
    executor.calls.append(args)
    return submit(*args)
  monkeypatch.setattr(executor, 'submit', record_submit)
  return owner, executor, RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)


def test_saved_origin_preview_without_live_guidance(tmp_path, monkeypatch, route):
  owner, executor, runtime = saved_origin_runtime(tmp_path, monkeypatch)
  assert runtime.update(10, None, 1)['status'] == 'waitingForLocation'
  assert not executor.jobs
  owner.position_store.record({'longitude': 0., 'latitude': 0., 'bearing': 90.})
  assert runtime.update(11, None, 1)['status'] == 'routing'
  assert executor.calls[0][2] == (0., 0.)
  executor.jobs[0].set_result(route)
  preview = runtime.update(12, None, 1)
  assert preview['route'] == route.preview()
  assert preview['status'] == 'waitingForLocation'
  assert preview['locationMonoTime'] == 0 and not preview['controlValid']
  assert preview['instruction'] == {} and preview['nextManeuver'] == {}
  assert owner.route_options(owner.read()['revision'])[0]['distanceMeters'] == route.total_distance
  owner.runtime_source = lambda: {**preview, 'instruction': None}
  snapshot = owner.snapshot()
  assert snapshot['route'] and snapshot['location']['lastKnown']
  assert snapshot['location']['validForMs'] == 0 and snapshot['instruction'] is None
  for now in range(13, 20):
    assert runtime.update(now, None, 1)['route'] == route.preview()
  assert len(executor.jobs) == 1


@pytest.mark.parametrize('completed', [False, True])
def test_live_fix_replaces_saved_origin_request(tmp_path, monkeypatch, route, completed):
  owner, executor, runtime = saved_origin_runtime(tmp_path, monkeypatch)
  owner.position_store.record({'longitude': 0., 'latitude': 0.})
  runtime.update(10, None, 1)
  if completed:
    executor.jobs[0].set_result(route)
    runtime.update(11, None, 1)
  live = (12, (.001, 0.), 5., 90.)
  result = runtime.update(12, live, 1)
  assert not result['controlValid'] and result['route'] == []
  if not completed:
    assert len(executor.jobs) == 1
    executor.jobs[0].set_result(route)
    result = runtime.update(13, live, 1)
    assert not result['controlValid'] and result['route'] == []
  assert len(executor.jobs) == 2 and executor.calls[1][2] == (.001, 0.)
  executor.jobs[1].set_result(route)
  result = runtime.update(14, live, 1)
  assert result['status'] == 'guiding' and result['controlValid']


def test_saved_origin_failure_backoff_and_destination_change(tmp_path, monkeypatch, route):
  owner, executor, runtime = saved_origin_runtime(tmp_path, monkeypatch)
  owner.position_store.record({'longitude': 0., 'latitude': 0.})
  runtime.update(10, None, 0)
  executor.jobs[0].set_exception(ValueError('route unavailable'))
  assert runtime.update(11, None, 0)['status'] == 'routeUnavailable'
  runtime.update(12, None, 0)
  assert len(executor.jobs) == 1
  runtime.update(30_000_000_012, None, 0)
  assert len(executor.jobs) == 2
  owner.clear(owner.read()['revision'], True)
  executor.jobs[1].set_result(route)
  result = runtime.update(30_000_000_013, None, 0)
  assert result['status'] == 'noDestination' and result['route'] == []


def test_unknown_speed_does_not_arrive_or_authorize_control(tmp_path, route):
  owner = configured(tmp_path)
  runtime = RouteRuntime(owner, executor=Executor())
  runtime.update(10_000_000_000, (10_000_000_000, (0., 0.), None, None), 1)
  runtime.route = route
  for second in (11, 20, 40):
    now = second * 1_000_000_000
    result = runtime.update(now, (now, (.01, 0.), None, None), 1)
    assert result['status'] == 'guiding' and not result['controlValid']
  assert owner.read_routing()['destination'] is not None


def test_held_phone_position_cannot_authorize_stale_navigation_control(tmp_path, route):
  runtime = RouteRuntime(configured(tmp_path), executor=Executor())
  position = (10_000_000_000, (.005, 0.), 5., 90.)
  runtime.update(10_000_000_000, position, 1)
  runtime.route = route
  result = runtime.update(14_000_000_000, position, 1)
  assert result['status'] == 'guiding' and not result['controlValid']


def lane_route(sub):
  return NavigationRoute({'distance': 1112., 'duration': 100., 'geometry': {'coordinates': [[0., 0.], [.01, 0.]]}, 'legs': [{'steps': [
    {'distance': 1112., 'duration': 100., 'maneuver': {'type': 'depart', 'instruction': 'Head east'}, 'bannerInstructions': [
      {'primary': {'type': 'off ramp', 'modifier': 'right', 'text': 'Exit 40'}, 'sub': sub}]},
    {'distance': 0., 'duration': 0., 'maneuver': {'type': 'arrive', 'instruction': 'You have arrived'}}]}]})


def test_lane_guidance_comes_from_the_banner_and_reaches_the_instruction():
  sub = {'text': '', 'components': [
    {'type': 'lane', 'active': False, 'directions': ['straight']},
    {'type': 'lane', 'active': True, 'directions': ['straight', 'right'], 'active_direction': 'right'},
    {'type': 'text', 'text': 'ignored'}]}
  lanes = lane_route(sub).progress((.005, 0.), 5., 90.).instruction['lanes']
  assert lanes == [{'active': False, 'directions': ['straight'], 'activeDirection': ''},
                   {'active': True, 'directions': ['straight', 'right'], 'activeDirection': 'right'}]


@pytest.mark.parametrize('sub', [None, 'text', {'components': 'x'}, {'components': [None, {'type': 'lane'}]},
                                 {'components': [{'type': 'lane', 'active': False, 'directions': ['left']}]},
                                 {'components': [{'type': 'lane', 'active': True, 'directions': ['left']}] * 9}])
def test_missing_unusable_or_malformed_lane_data_is_ignored(sub):
  assert lane_route(sub).progress((.005, 0.), 5., 90.).instruction['lanes'] == []


def test_route_line_keeps_corners_and_thins_straight_road():
  from openpilot.starpilot.navigation.route_engine import PREVIEW_POINTS, simplify
  east = [[i * 1e-5, 0.] for i in range(5000)]
  north = [[east[-1][0], i * 1e-5] for i in range(1, 5000)]
  route = NavigationRoute({'distance': 1100., 'duration': 100., 'geometry': {'coordinates': east + north}, 'legs': [{'steps': [
    {'distance': 1100., 'duration': 100., 'maneuver': {'type': 'depart'}},
    {'distance': 0., 'duration': 0., 'maneuver': {'type': 'arrive'}}]}]})
  preview = route.preview()
  assert {'longitude': east[-1][0], 'latitude': 0.} in preview and len(preview) == 3
  assert route.preview() is preview
  wiggle = [[i * 1e-5, (i % 2) * 1e-4] for i in range(4000)]
  assert len(simplify(wiggle)) == PREVIEW_POINTS


def test_route_line_preserves_a_collinear_turnaround():
  from openpilot.starpilot.navigation.route_engine import simplify
  assert simplify([(0., 0.), (.01, 0.), (.005, 0.)]) == [0, 1, 2]


def garage_runtime(tmp_path, monkeypatch):
  monkeypatch.setattr(NavigationOwner, '_is_metric', lambda self: True)
  owner = NavigationOwner(tmp_path, runtime_source=lambda: None)
  settings = owner.configure({'enabled': True, 'token': 'pk.test'}, '0', True)
  owner.select({'name': 'Far', 'longitude': .05, 'latitude': 0.}, settings['revision'], True)
  executor = Executor()
  runtime = RouteRuntime(owner, engine=SimpleNamespace(fetch=None), executor=executor)
  long_route = NavigationRoute({'distance': 5560., 'duration': 400., 'geometry': {'coordinates': [[0., 0.], [.05, 0.]]}, 'legs': [{'steps': [
    {'distance': 5560., 'duration': 400., 'maneuver': {'type': 'depart'}}, {'distance': 0., 'duration': 0., 'maneuver': {'type': 'arrive'}}]}]})
  runtime.update(1, (1, (0., 0.), 0., None), 1)
  executor.jobs[0].set_result(long_route)
  return runtime, executor


def drive(runtime, start_ns, seconds, coordinates, speed, bearing):
  result = None
  for tick in range(seconds + 1):
    now = start_ns + tick * 1_000_000_000
    result = runtime.update(now, (now, coordinates, speed, bearing), 1)
  return result


def test_leaving_a_garage_before_reaching_the_route_does_not_reroute(tmp_path, monkeypatch):
  runtime, executor = garage_runtime(tmp_path, monkeypatch)
  assert drive(runtime, 2, 30, (.0005, .0015), 3., 200.)['status'] == 'guiding', "spiral ramp 170 m from the street"
  assert len(executor.jobs) == 1
  assert drive(runtime, 40_000_000_000, 5, (-.004, .002), 12., 270.)['status'] == 'routing', "really went the other way"
  assert len(executor.jobs) == 2


def test_slow_off_route_waits_longer_once_joined(tmp_path, monkeypatch):
  runtime, executor = garage_runtime(tmp_path, monkeypatch)
  drive(runtime, 2, 2, (.01, 0.), 15., 90.)
  assert drive(runtime, 10_000_000_000, 6, (.02, .001), 3., 0.)['status'] == 'guiding'
  assert drive(runtime, 17_000_000_000, 2, (.02, .001), 3., 0.)['status'] == 'routing'
  assert len(executor.jobs) == 2


def test_no_reroute_in_the_destination_garage(tmp_path, monkeypatch):
  runtime, executor = garage_runtime(tmp_path, monkeypatch)
  drive(runtime, 2, 2, (.03, 0.), 15., 90.)
  assert drive(runtime, 10_000_000_000, 20, (.049, .0015), 3., 180.)['status'] in ('guiding', 'arrived')
  assert len(executor.jobs) == 1
