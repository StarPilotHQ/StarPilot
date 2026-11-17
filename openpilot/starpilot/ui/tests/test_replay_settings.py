"""Fake transport exercises native ownership, cancellation and passive input."""

from collections.abc import Callable
from types import SimpleNamespace as NS
import threading
import time
from typing import Any, NoReturn, cast
from unittest.mock import Mock

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.starpilot.ui.replay_settings import ReplayCompactPage, ReplayController, ReplayPage, duration, route_date
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.feature_settings_state import FEATURE_ROW_HEIGHT, feature_row_top


ROUTE = {'routeId': '00000001--0123456789', 'displayName': 'Recorded route', 'segmentCount': 2,
         'availability': 'ready', 'playable': True, 'reason': None, 'warnings': []}
STAMP = 1_791_986_730  # 2026-10-14 14:05:30 UTC


@pytest.fixture
def utc(monkeypatch):
  with monkeypatch.context() as patch:
    patch.setenv('TZ', 'UTC')
    time.tzset()
    yield
  time.tzset()


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
def test_route_date_prefers_recorded_start_and_formats_native_clock(utc, profile):
  route = {'startTime': STAMP, 'fileTime': STAMP + 60}
  calendar, clock, zone, source = route_date(route, profile)
  assert calendar == ('Wed, Oct 14, 2026' if profile == Profile.LARGE else 'Oct 14, 2026')
  assert (clock, zone, source) == ('2:05 PM', 'UTC', '')
  assert route_date(route, profile, True)[1] == '14:05'


@pytest.mark.parametrize('invalid', [None, True, '2026-10-14', 0, -1, float('nan'), float('inf'), 10**400])
def test_route_date_falls_back_and_handles_missing_or_unrepresentable_dates(utc, invalid):
  route = {'startTime': invalid, 'fileTime': STAMP + 60}
  assert route_date(route, Profile.COMPACT)[1:] == ('2:06 PM', 'UTC', 'File date')
  route['fileTime'] = invalid
  assert route_date(route, Profile.COMPACT) == ('Date unavailable', '', '', '')


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
@pytest.mark.parametrize('named', [False, True])
def test_route_rows_prioritize_dates_preserve_names_and_keep_route_identity(utc, profile, named):
  page, owner = page_for_input()
  page.profile = profile
  owner.routes = [{**ROUTE, 'displayName': 'School run' if named else ROUTE['routeId'],
                   'startTime': STAMP}]
  row = page._rows()[0]
  assert row.key == ROUTE['routeId']
  assert row.label == ('School run' if named else 'Wed, Oct 14, 2026 - 2:05 PM' if profile == Profile.LARGE else 'Oct 14, 2026')
  assert '2:05 PM' in row.label + row.value
  assert row.reason == ''
  if profile == Profile.COMPACT:
    assert row.value.count('\n') == 1
    assert '2 segments' in row.value
  else:
    assert row.segment_count == 2
    assert row.page == 'detail' and not row.actions
    assert ROUTE['routeId'] in row.value
  assert page._rows()[0] is row


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
def test_route_rows_show_newest_first_without_reordering_catalog(profile):
  page, owner = page_for_input()
  page.profile = profile
  owner.routes = [{**ROUTE, 'routeId': '00000001--0123456789', 'startTime': STAMP, 'fileTime': STAMP + 120},
                  {**ROUTE, 'routeId': '00000002--0123456789', 'startTime': STAMP + 60},
                  {**ROUTE, 'routeId': '00000003--0123456789', 'fileTime': STAMP - 60},
                  {**ROUTE, 'routeId': '00000004--0123456789'}]
  identifiers = [route['routeId'] for route in owner.routes]
  rows = page._rows()
  assert [row.key for row in rows] == [identifiers[1], identifiers[0], identifiers[2], identifiers[3]]
  assert [route['routeId'] for route in owner.routes] == identifiers
  assert page._rows() is rows


def test_compact_file_date_is_explicit_and_unavailable_reason_is_retained(utc):
  page, owner = page_for_input()
  page.profile = Profile.COMPACT
  owner.routes = [{**ROUTE, 'displayName': ROUTE['routeId'], 'fileTime': STAMP,
                   'availability': 'unavailable', 'reason': 'No road camera footage'}]
  row = page._rows()[0]
  assert row.value == '2:05 PM\nFile date - 2 segments'
  assert row.reason == 'No road camera footage'
  owner.routes = [{**ROUTE, 'displayName': 'School run', 'fileTime': STAMP, 'availability': 'limited'}]
  row = page._rows()[0]
  assert row.value == 'File date: Oct 14, 2026\n2:05 PM - 2 segments'
  assert row.reason == ''


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
@pytest.mark.parametrize('named', [False, True])
def test_details_show_source_timezone_and_full_id_without_reformatting_each_frame(utc, route_detail, monkeypatch, profile, named):
  from openpilot.starpilot.ui import replay_settings as module
  page, owner = route_detail
  page.profile = profile
  owner.detail = {**owner.detail, 'displayName': 'School run' if named else ROUTE['routeId'], 'fileTime': STAMP}
  formatter = Mock(wraps=route_date)
  monkeypatch.setattr(module, 'route_date', formatter)
  monkeypatch.setattr(module, 'draw_settings_header', Mock())
  page._button = Mock()
  page._render_detail()
  page._render_detail()
  formatter.assert_called_once()
  texts = [call.args[0] for call in page.fonts.draw.call_args_list]
  assert ROUTE['routeId'] in texts
  assert any('File date' in text and '2:05 PM UTC' in text for text in texts)
  assert ('School run' if named else 'Wed, Oct 14, 2026' if profile == Profile.LARGE else 'Oct 14, 2026') in texts


class Backend:
  def __init__(self):
    self.calls = []
    self.status: dict[str, Any] = {'schemaVersion': 1, 'ownerId': 'a' * 32, 'sessionId': None, 'state': 'idle',
                                 'routeId': None, 'profile': 'large', 'revision': 0, 'epoch': 0,
                                 'position': 0.0, 'start': 0.0, 'end': 120.0, 'rate': 1.0, 'paused': True,
                                 'frameReady': False, 'errorCode': None, 'owned': False, 'offroad': True}
    self.start_gate = None

  def __call__(self, payload):
    self.calls.append(payload)
    operation = payload['op']
    if operation == 'catalog':
      result = {'schemaVersion': 1, 'routes': [dict(ROUTE)]}
    elif operation == 'detail':
      result = dict(ROUTE)
    else:
      if operation == 'start':
        if self.start_gate is not None:
          self.start_gate.wait(2)
        self.status.update(sessionId='b' * 32, state='playing', routeId=ROUTE['routeId'],
                           revision=1, epoch=1, paused=False, owned=True, frameReady=True, framePath='/tmp/private-replay-frame')
      elif operation == 'control':
        assert payload['sessionId'] == self.status['sessionId']
        assert payload['expectedRevision'] == self.status['revision']
        self.status['revision'] += 1
        action = payload['action']
        if action == 'seek':
          self.status.update(position=payload['position'], epoch=self.status['epoch'] + 1)
        elif action == 'pause':
          self.status.update(paused=payload['paused'], state='paused' if payload['paused'] else 'playing')
        else:
          self.status['rate'] = payload['rate']
      elif operation == 'stop':
        self.status.update(sessionId=None, state='idle', frameReady=False)
      result = dict(self.status)
    return {'ok': True, 'result': result}


def pump(owner, predicate):
  deadline = time.monotonic() + 2
  while time.monotonic() < deadline:
    owner.tick()
    if predicate():
      return
    time.sleep(0.001)
  cast(Callable[[str], NoReturn], pytest.fail)('fake replay operation did not finish')


@pytest.fixture
def playback():
  backend = Backend()
  frames = []
  reader = NS(latest=lambda: frames.pop(0) if frames else None, close=Mock())
  owner = ReplayController(backend, lambda *_: reader)
  pump(owner, lambda: owner.available and owner.pending is None)
  owner.select(ROUTE)
  pump(owner, lambda: owner.pending is None)
  owner.start()
  pump(owner, lambda: owner.active)
  yield owner, backend, frames, reader
  owner.close()
  owner.executor.shutdown(wait=True)


def test_owned_start_and_controls_bind_revision_and_client(playback):
  owner, backend, _, _ = playback
  start = next(call for call in backend.calls if call['op'] == 'start')
  assert start['expectedOwnerId'] == 'a' * 32
  assert start['clientId'] == owner.client_id
  assert owner.seek(1000)
  pump(owner, lambda: owner.status['epoch'] == 2)
  command = next(call for call in backend.calls if call['op'] == 'control')
  assert command['position'] == 120
  assert command['expectedRevision'] == 1
  assert command['sessionId'] == owner.own_session
  assert not owner.seek(float('nan'))


@pytest.mark.parametrize('broken', [False, True])
def test_timeline_is_optional_reused_on_seek_and_cleared_on_authority_loss(playback, broken):
  from openpilot.starpilot.replay.timeline import LEGEND
  owner, backend, _, _ = playback
  original = owner.requester
  def requester(payload):
    if payload['op'] == 'timeline':
      backend.calls.append(payload)
      if broken:
        return {'ok': False, 'error': {'message': 'history unavailable'}}
      return {'ok': True, 'result': {'schemaVersion': 1, 'ownerId': owner.status['ownerId'], 'sessionId': owner.own_session,
                                    'rowOffset': 0, 'totalRows': 1, 'source': 'qlog', 'rows': [[0, 1, 'chill']],
                                    'nextCursor': None, 'legend': LEGEND}}
    return original(payload)
  owner.requester = requester
  backend.status['timelineState'] = 'ready'
  owner.next_status = 0
  pump(owner, lambda: bool(owner.timeline_error) if broken else owner.timeline is not None and owner.timeline.rows is not None)
  history = owner.timeline
  assert owner.error == '' and owner.available
  assert owner.seek(30)
  pump(owner, lambda: owner.status['epoch'] == 2)
  assert owner.timeline is history
  assert len([call for call in backend.calls if call['op'] == 'timeline']) == 1
  backend.status['offroad'] = False
  owner.next_status = 0
  pump(owner, lambda: not owner.available)
  assert owner.timeline is None


@pytest.mark.parametrize('bounds', [(None, 120), (0, None), (False, 120), (0, True), ('0', 120), (120, 0)])
def test_seek_rejects_missing_or_invalid_bounds_without_sending_controls(playback, bounds):
  owner, backend, _, _ = playback
  owner.status['start'], owner.status['end'] = bounds
  count = len(backend.calls)
  assert not owner.seek(30)
  assert len(backend.calls) == count


@pytest.fixture(params=[Profile.LARGE, Profile.COMPACT])
def prepared_playback(request):
  backend = Backend()
  backend.status['profile'] = request.param
  def requester(payload):
    response = backend(payload)
    if payload['op'] == 'start':
      backend.status.update(state='paused', paused=True)
      response['result'] = dict(backend.status)
    return response
  frames = []
  reader = NS(latest=lambda: frames.pop(0) if frames else None, close=Mock())
  factory = Mock(return_value=reader)
  clock = NS(now=0.0)
  owner = ReplayController(requester, factory, clock=lambda: clock.now)
  pump(owner, lambda: owner.available and owner.pending is None)
  owner.select(ROUTE)
  pump(owner, lambda: owner.pending is None)
  assert owner.start()
  pump(owner, lambda: owner.active)
  yield owner, backend, frames, clock, factory
  owner.close()
  owner.executor.shutdown(wait=True)


def test_buffering_preserves_published_native_frame(prepared_playback):
  owner, backend, frames, _, factory = prepared_playback
  frame = NS(session_id=owner.own_session, epoch=1, frame_id=1)
  frames.append(frame)
  owner.next_frame = 0
  pump(owner, lambda: owner.frame is frame)
  generation = owner.reader_generation
  backend.status.update(state='buffering', paused=False)
  owner.next_status = 0
  pump(owner, lambda: owner.status['state'] == 'buffering')
  assert owner.frame is frame and owner.reader_generation == generation
  assert factory.call_count == 1
  backend.status['offroad'] = False
  owner.next_status = 0
  pump(owner, lambda: not owner.available)
  assert owner.frame is None


def test_new_frame_is_polled_on_next_ui_frame_after_an_unchanged_read(prepared_playback):
  owner, _, frames, clock, _ = prepared_playback
  owner.autoplay_at = None
  owner.next_status = 100
  owner.next_frame = 0
  owner.tick()
  pump(owner, lambda: owner.pending is None)
  frame = NS(session_id=owner.own_session, epoch=1, frame_id=1)
  frames.append(frame)
  clock.now += 1 / 60
  pump(owner, lambda: owner.frame is frame)


def test_autoplay_waits_one_second_after_first_valid_frame_and_runs_only_once(prepared_playback):
  owner, backend, frames, clock, factory = prepared_playback
  clock.now = 10
  pump(owner, lambda: owner.available and owner.pending is None)
  assert not any(call['op'] == 'control' for call in backend.calls)
  frames.append(NS(session_id=owner.own_session, epoch=0, frame_id=1))
  owner.next_frame = 0
  pump(owner, lambda: not frames and owner.pending is None)
  assert owner.autoplay_at == float('inf')
  frames.append(NS(session_id=owner.own_session, epoch=1, frame_id=2))
  owner.next_frame = 0
  pump(owner, lambda: owner.frame is not None)
  assert factory.call_args.args[2] == backend.status['profile']
  assert owner.autoplay_at == 11
  clock.now = 10.99
  pump(owner, lambda: owner.pending is None)
  assert backend.status['paused']
  clock.now = 11
  pump(owner, lambda: backend.status['paused'] is False)
  controls = [call for call in backend.calls if call['op'] == 'control']
  assert len(controls) == 1
  assert controls[0]['action'] == 'pause' and controls[0]['paused'] is False
  pump(owner, lambda: owner.status['paused'] is False)
  assert owner.control('pause', paused=True)
  pump(owner, lambda: owner.status['paused'] is True)
  clock.now = 12
  pump(owner, lambda: owner.available and owner.pending is None)
  assert backend.status['paused']  # Later pauses must never trigger another autoplay.


@pytest.mark.parametrize('action', ['pause', 'seek', 'stop', 'close', 'offroad'])
def test_autoplay_respects_user_actions_and_offroad_authority(prepared_playback, action):
  owner, backend, frames, clock, _ = prepared_playback
  frames.append(NS(session_id=owner.own_session, epoch=1, frame_id=1))
  owner.next_frame = 0
  pump(owner, lambda: owner.frame is not None)
  if action == 'pause':
    assert owner.control('pause', paused=True)
  elif action == 'seek':
    assert owner.seek(30)
  elif action == 'stop':
    assert owner.stop()
  elif action == 'close':
    owner.close()
  else:
    backend.status['offroad'] = False
    owner.next_status = 0
    pump(owner, lambda: not owner.available)
  if action != 'close':
    pump(owner, lambda: owner.pending is None)
  clock.now = 1.1
  owner.tick()
  if action != 'close':
    pump(owner, lambda: owner.pending is None)
  assert not any(call['op'] == 'control' and call.get('paused') is False for call in backend.calls)


def test_another_client_session_has_no_implicit_native_control():
  backend = Backend()
  backend.status.update(sessionId='c' * 32, state='playing')
  owner = ReplayController(backend)
  try:
    pump(owner, lambda: owner.available)
    assert not owner.active
    assert not owner.control('pause', paused=True)
    assert not owner.stop()
    assert not any(call['op'] in ('start', 'control', 'stop') for call in backend.calls)
  finally:
    owner.close()
    owner.executor.shutdown(wait=True)


def test_close_stops_a_start_that_finishes_after_dismissal():
  backend = Backend()
  owner = ReplayController(backend)
  pump(owner, lambda: owner.available and owner.pending is None)
  owner.select(ROUTE)
  pump(owner, lambda: owner.pending is None)
  backend.start_gate = threading.Event()
  assert owner.start()
  deadline = time.monotonic() + 2
  while not any(call['op'] == 'start' for call in backend.calls) and time.monotonic() < deadline:
    time.sleep(0.001)
  owner.close()
  backend.start_gate.set()
  owner.executor.shutdown(wait=True)
  assert [call['op'] for call in backend.calls][-2:] == ['start', 'stop']
  assert backend.status['sessionId'] is None


def test_old_epoch_frames_are_rejected_and_seek_clears_previous_frame(playback):
  owner, _, frames, _ = playback
  old = NS(data=b'pixels', session_id=owner.own_session, epoch=1, frame_id=1)
  frames.append(old)
  pump(owner, lambda: owner.frame is old)
  assert owner.seek(60)
  pump(owner, lambda: owner.status['epoch'] == 2)
  assert owner.frame is None
  frames.append(old)
  owner.next_frame = 0
  pump(owner, lambda: not frames)
  assert owner.frame is None


def test_control_waits_behind_one_frame_copy_without_blocking_ui(playback):
  owner, backend, _, reader = playback
  entered, release = threading.Event(), threading.Event()
  def blocked_frame():
    entered.set()
    release.wait(2)
    return None
  reader.latest = blocked_frame
  owner.next_frame = 0
  pump(owner, entered.is_set)
  began = time.monotonic()
  assert owner.control('pause', paused=True)
  assert time.monotonic() - began < .1
  assert owner.queued['action'] == 'pause'
  release.set()
  pump(owner, lambda: backend.status['paused'])


def page_for_input():
  controller = NS(status={'epoch': 1, 'start': 0, 'end': 120, 'position': 30, 'rate': 1, 'paused': False},
                  seek=Mock(), control=Mock(), stop=Mock(), active=True)
  fonts = Mock(profile=Profile.LARGE)
  page = ReplayPage(NS(fonts=fonts, profile=Profile.LARGE), Mock(), controller)
  page.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  page.page = 'viewer'
  return page, controller


@pytest.mark.parametrize('profile,rail,tick,mark_width', [(Profile.LARGE, 28, 6, 4), (Profile.COMPACT, 16, 4, 3)])
@pytest.mark.parametrize('scale', [1, 2])
def test_timeline_cache_draws_alerts_last_and_keeps_existing_hit_area(monkeypatch, profile, rail, tick, mark_width, scale):
  from openpilot.starpilot.ui import replay_settings as module
  from openpilot.starpilot.replay.timeline import LEGEND
  page, controller = page_for_input()
  page.profile = profile
  controller.timeline = NS(rows=[[0, 120, 'chill'], [30, 30.001, 'error'], [30, 30, 'bookmark']])
  monkeypatch.setattr(module.gui_app, '_scale', scale)
  target = NS(id=1)
  upload, paint, unload = Mock(return_value=target), Mock(), Mock()
  monkeypatch.setattr(rl, 'load_render_texture', upload)
  monkeypatch.setattr(rl, 'draw_rectangle', paint)
  monkeypatch.setattr(rl, 'unload_render_texture', unload)
  for name in ('begin_texture_mode', 'end_texture_mode', 'clear_background'):
    monkeypatch.setattr(rl, name, Mock())
  page._prepare_rail()
  assert page._transport()[0][-1] == 48
  assert upload.call_args.args[1] == (rail + tick + 2) * scale
  calls = paint.call_args_list
  assert calls[-2].args[1:4:2] == ((tick + 2) * scale, rail * scale)
  assert calls[-2].args[2] >= 1 and calls[-1].args[2] >= mark_width * scale
  for call, kind in zip(calls[-3:], ('chill', 'error', 'bookmark'), strict=True):
    color = call.args[-1]
    assert [color.r, color.g, color.b] == LEGEND[kind]['rgb']
  controller.status.update(position=60, epoch=2)
  page._prepare_rail()
  upload.assert_called_once()
  controller.timeline = None
  page._prepare_rail()
  unload.assert_called_once_with(target)


def test_timeline_texture_without_cache_key_uses_neutral_rail(monkeypatch):
  page, controller = page_for_input()
  controller.timeline = NS(rows=[])
  page.rail_texture, page.rail_key = NS(id=1), None
  paint, draw = Mock(), Mock()
  monkeypatch.setattr(rl, 'draw_rectangle', paint)
  monkeypatch.setattr(rl, 'draw_texture_pro', draw)
  page._timeline_rail(100, 916, 1960, 48, 0, 120, [[0, 120]])
  draw.assert_not_called()
  paint.assert_called_once()


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
@pytest.mark.parametrize('head_x', [16, 270, 2060])
def test_scrub_preview_stays_above_touch_area_and_inside_screen(monkeypatch, profile, head_x):
  from openpilot.starpilot.ui import replay_settings as module
  from openpilot.starpilot.replay.timeline import LEGEND
  page, _ = page_for_input()
  page.profile = profile
  page.fonts.measure.side_effect = lambda text, _role, size: NS(width=len(text) * size / 2, height=size)
  fill, paint = Mock(), Mock()
  monkeypatch.setattr(module, 'draw_rounded_fill', fill)
  monkeypatch.setattr(module, 'draw_rounded_stroke', Mock())
  monkeypatch.setattr(rl, 'draw_line', Mock())
  monkeypatch.setattr(rl, 'draw_rectangle', paint)
  page._scrub_preview(head_x, 90, 'Experimental', LEGEND['experimental'])
  bounds = fill.call_args.args[0]
  assert 0 <= bounds.x < bounds.x + bounds.width <= profile.size[0]
  assert bounds.y + bounds.height < page._transport()[0][1] - (48 if profile == Profile.LARGE else 8)
  color = paint.call_args.args[-1]
  assert [color.r, color.g, color.b] == LEGEND['experimental']['rgb']
  assert page.fonts.draw.call_args.args[0] == '1:30 - Experimental'
  if profile == Profile.COMPACT:
    assert (bounds.x, bounds.y, bounds.width, bounds.height) == (8, 8, 520, 42)
  page._scrub_preview(head_x, 90, 'Unavailable gap', None)
  assert paint.call_args.args[-1] is module.TEXT_MUTED


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
def test_timeline_commits_only_on_release_and_epoch_change_cancels(profile):
  page, controller = page_for_input()
  page.profile = profile
  page.set_rect(rl.Rectangle(0, 0, *profile.size))
  x, top, width, height = page._transport()[0]
  y, destination = top + height / 2, x + width * .75
  page._handle_mouse_press(NS(x=x, y=y))
  page._handle_mouse_event(mouse_event=NS(left_down=True, left_released=False, pos=NS(x=destination, y=y + 50), t=1))
  controller.seek.assert_not_called()
  page._handle_mouse_release(NS(x=destination, y=y + 50))
  controller.seek.assert_called_once_with(90)
  controller.seek.reset_mock()
  page._handle_mouse_press(NS(x=x, y=y))
  controller.status['epoch'] = 2
  page._handle_mouse_release(NS(x=destination, y=y))
  controller.seek.assert_not_called()


def test_recorded_driving_hit_areas_are_passive():
  page, controller = page_for_input()
  page._handle_mouse_press(NS(x=1680, y=150))
  page._handle_mouse_release(NS(x=1680, y=150))
  controller.control.assert_not_called()
  controller.seek.assert_not_called()
  controller.stop.assert_not_called()


def test_unavailable_duration_does_not_become_zero():
  assert duration(None) == 'Unavailable'
  assert duration(float('nan')) == 'Unavailable'
  assert duration(65) == '1:05'


@pytest.mark.parametrize('active,available,paused,awake', [(True, True, False, True), (True, True, True, False),
                                                         (True, False, False, False), (False, True, False, False)])
def test_only_available_native_playback_keeps_display_awake(monkeypatch, active, available, paused, awake):
  from openpilot.starpilot.ui import replay_settings as module
  page, owner = page_for_input()
  owner.tick = Mock()
  owner.active, owner.available = active, available
  owner.status['paused'] = paused
  reset = Mock()
  monkeypatch.setattr(module.device, '_reset_interactive_timeout', reset)
  monkeypatch.setattr(module, 'ui_state', NS(started=False))
  page._tick()
  assert bool(reset.call_count) is awake


def test_status_recovers_owned_session_after_lost_start_response():
  backend = Backend()
  backend.status.update(sessionId='b' * 32, state='preparing', owned=True)
  owner = ReplayController(backend)
  try:
    pump(owner, lambda: owner.available)
    assert owner.own_session == 'b' * 32
    assert owner.active
    assert owner.stop()
    pump(owner, lambda: backend.status['sessionId'] is None)
  finally:
    owner.close()
    owner.executor.shutdown(wait=True)


def test_owned_ended_session_remains_seekable(playback):
  owner, backend, _, _ = playback
  backend.status.update(state='ended', paused=True, position=120)
  owner.next_status = 0
  pump(owner, lambda: owner.status['state'] == 'ended')
  assert owner.active
  assert owner.seek(30)
  pump(owner, lambda: backend.status['position'] == 30)


def test_fresh_ignition_denial_hides_frame_and_prevents_controls(playback):
  owner, backend, _, _ = playback
  backend.status['offroad'] = False
  owner.next_status = 0
  pump(owner, lambda: owner.status.get('offroad') is False)
  assert not owner.available
  assert not owner.seek(30)
  assert not owner.control('pause', paused=True)


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
def test_hud_tap_hides_and_shows_transport(profile):
  page, controller = page_for_input()
  page.profile = profile
  page.set_rect(rl.Rectangle(0, 0, *profile.size))
  page.controls = True
  page._handle_mouse_press(NS(x=220, y=20))
  page._handle_mouse_release(NS(x=220, y=20))
  assert page.last_touch == -float('inf')
  page.controls = False
  page._handle_mouse_press(NS(x=220, y=20))
  page._handle_mouse_release(NS(x=220, y=20))
  assert page.last_touch > 0
  controller.control.assert_not_called()
  controller.seek.assert_not_called()


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
def test_transport_autohides_only_during_playback_and_tap_does_not_activate_hidden_buttons(monkeypatch, profile):
  from openpilot.starpilot.ui import replay_settings as module
  page, owner = page_for_input()
  page.profile = profile
  page.set_rect(rl.Rectangle(0, 0, *profile.size))
  page.fonts.measure.return_value = NS(width=20, height=20)
  page._button = Mock()
  page._scrub_preview = Mock()
  monkeypatch.setattr(module, 'draw_rounded_fill', Mock())
  owner.available, owner.busy, owner.error = True, False, ''
  owner.frame = NS(session_id='b' * 32, epoch=1, frame_id=1, width=profile.size[0], height=profile.size[1], data=b'pixels')
  owner.status.update(state='playing', paused=False)
  monkeypatch.setattr(module, 'ui_state', NS(started=False))
  for name in ('set_texture_filter', 'update_texture', 'draw_texture_pro', 'draw_rectangle', 'draw_circle'):
    monkeypatch.setattr(rl, name, Mock())
  monkeypatch.setattr(rl, 'load_texture_from_image', Mock(return_value=NS(width=profile.size[0], height=profile.size[1])))
  now = 10
  monkeypatch.setattr(module.time, 'monotonic', lambda: now)
  page.last_touch = 0
  page._render_viewer()
  assert page.controls  # Preparation time does not consume the initial visibility timeout.
  if profile == Profile.LARGE:
    assert rl.draw_rectangle.call_args_list[0].args[-1].a == 190
  else:
    backings = [call.args[0] for call in module.draw_rounded_fill.call_args_list]
    assert any((rect.x, rect.y, rect.width, rect.height) == (8, 70, 520, 36) for rect in backings)
    assert not any(rect.height > 36 for rect in backings)
  now = 14
  page._button.reset_mock()
  page._render_viewer()
  assert not page.controls
  page._button.assert_called_once_with('Stop', page._collapsed_stop_bounds(), True)
  left, top, width, height = page._collapsed_stop_bounds()
  assert page._target(left + width / 2, top + height / 2) == 'Stop'
  timeline, buttons = page._transport()
  x, y, w, h = buttons[1][1]
  play_pos = NS(x=x + w / 2, y=y + h / 2)
  assert page._target(play_pos.x, play_pos.y) == 'show'
  page._handle_mouse_press(play_pos)
  page._render_viewer()
  page._handle_mouse_release(play_pos)
  owner.control.assert_not_called()
  owner.seek.assert_not_called()
  page._render_viewer()
  assert page.controls
  now = 16
  x, y, _, seek_height = timeline
  seek_pos = NS(x=x, y=y + seek_height / 2)
  page._handle_mouse_press(seek_pos)
  now = 20
  page._render_viewer()
  assert page.controls  # A held seek gesture must not disappear mid-drag.
  page._handle_mouse_release(seek_pos)
  assert page.last_touch == 20
  now = 24
  for state in ('paused', 'preparing', 'buffering', 'ended'):
    owner.status['state'] = state
    page._render_viewer()
    assert page.controls
  owner.status['state'] = 'playing'
  page._render_viewer()
  assert not page.controls
  page._back = Mock()
  pos = NS(x=left + width / 2, y=top + height / 2)
  page._handle_mouse_press(pos)
  page._render_viewer()
  assert not page.controls
  page._handle_mouse_release(pos)
  page._back.assert_called_once()


def test_gap_seek_snaps_to_nearest_available_footage(playback):
  owner, backend, _, _ = playback
  owner.status['seekRanges'] = [[0, 20], [80, 120]]
  assert owner.seek(35)
  pump(owner, lambda: backend.status['position'] == 20)
  assert next(call for call in backend.calls if call['op'] == 'control')['position'] == 20


def test_seek_preparation_clears_frame_before_epoch_advances(playback):
  owner, backend, _, _ = playback
  owner.frame = NS(session_id=owner.own_session, epoch=1, frame_id=1)
  backend.status.update(state='seeking', frameReady=False)
  owner.next_status = 0
  pump(owner, lambda: owner.status['state'] == 'seeking')
  assert owner.status['epoch'] == 1
  assert owner.frame is None


def test_close_stops_owned_status_that_arrives_after_dismissal():
  backend = Backend()
  backend.status.update(sessionId='b' * 32, state='playing', owned=True)
  entered, release = threading.Event(), threading.Event()
  def request(payload):
    if payload['op'] == 'status':
      entered.set()
      release.wait(2)
    return backend(payload)
  owner = ReplayController(request)
  assert owner.submit({'op': 'status', 'clientId': owner.client_id})
  assert entered.wait(2)
  owner.close()
  release.set()
  owner.executor.shutdown(wait=True)
  assert [call['op'] for call in backend.calls] == ['status', 'stop']
  assert backend.status['sessionId'] is None


@pytest.mark.parametrize('profile,size', [(Profile.LARGE, (1600, 800)), (Profile.COMPACT, (536, 240))])
def test_passive_frame_texture_upload_is_cached_and_uses_full_profile(monkeypatch, profile, size):
  from openpilot.starpilot.ui import replay_settings as module
  page, owner = page_for_input()
  page.profile = profile
  page._button = Mock()
  monkeypatch.setattr(module, 'draw_rounded_fill', Mock())
  page.fonts.measure.return_value = NS(width=20, height=20)
  owner.available, owner.busy, owner.error = True, False, ''
  owner.frame = NS(session_id='b' * 32, epoch=1, frame_id=1, width=size[0], height=size[1],
                   data=b'\0\xff\x80\x01' * (size[0] * size[1]))
  monkeypatch.setattr(module, 'ui_state', NS(started=False))
  texture = NS(width=size[0], height=size[1])
  monkeypatch.setattr(rl, 'load_texture_from_image', Mock(return_value=texture))
  monkeypatch.setattr(rl, 'set_texture_filter', Mock())
  def check_upload(actual_texture, pixels):
    assert actual_texture is texture
    assert isinstance(pixels, (bytes, bytearray, memoryview))
    assert bytes(pixels[:len(owner.frame.data)]) == owner.frame.data

  upload, draw = Mock(side_effect=check_upload), Mock()
  monkeypatch.setattr(rl, 'update_texture', upload)
  monkeypatch.setattr(rl, 'draw_texture_pro', draw)
  monkeypatch.setattr(rl, 'draw_rectangle', Mock())
  monkeypatch.setattr(rl, 'draw_circle', Mock())
  monkeypatch.setattr(rl, 'draw_ring', Mock())
  page._render_viewer()
  page._render_viewer()
  rl.draw_ring.assert_not_called()
  assert '0:30 / 2:00' in [call.args[0] for call in page.fonts.draw.call_args_list]
  assert upload.call_count == 1
  rect = draw.call_args.args[2]
  assert (rect.x, rect.y, rect.width, rect.height) == (0, 0, *profile.size)
  assert page.frame_key == ('b' * 32, 1, 1)
  owner.frame = NS(**{**vars(owner.frame), 'epoch': 2, 'data': b'\xff\0\x01\x80' * (size[0] * size[1])})
  page._render_viewer()
  assert upload.call_count == 2
  rl.load_texture_from_image.assert_called_once()


def test_paused_new_epoch_frame_can_be_reread_after_status_catches_up():
  backend = Backend()
  frame = NS(session_id='b' * 32, epoch=2, frame_id=1, data=b'paused')
  readers = []
  class FrozenReader:
    def __init__(self):
      self.used = False
    def latest(self):
      if self.used:
        return None
      self.used = True
      return frame
    def close(self):
      pass
  def factory(*_):
    reader = FrozenReader()
    readers.append(reader)
    return reader
  owner = ReplayController(backend, factory)
  try:
    pump(owner, lambda: owner.available and owner.pending is None)
    owner.select(ROUTE)
    pump(owner, lambda: owner.pending is None)
    owner.start()
    pump(owner, lambda: readers and readers[0].used and owner.pending is None)
    assert owner.frame is None  # owner is still at epoch 1
    backend.status.update(epoch=2, paused=True, state='paused')
    owner.next_status = 0
    pump(owner, lambda: owner.frame is frame)
    assert len(readers) == 2
  finally:
    owner.close()
    owner.executor.shutdown(wait=True)


@pytest.fixture
def route_detail(monkeypatch):
  monkeypatch.setattr(rl, 'draw_line', Mock())
  page, owner = page_for_input()
  page.page = 'detail'
  page.fonts.measure.side_effect = lambda text, _role, size: NS(width=len(text) * 8 * size / 18, height=size)
  owner.detail = {**ROUTE, 'segmentCount': 3, 'warnings': [f'Warning {number}' for number in range(32)],
                  'segments': [{'number': number, 'files': {'rlog': number != 3, 'qlog': False, 'fcamera': True, 'qcamera': False}}
                               for number in (0, 2, 3)]}
  owner.selected_route = ROUTE['routeId']
  owner.routes, owner.catalog_loaded = [ROUTE], True
  owner.available, owner.busy, owner.error = True, False, ''
  owner.status['state'] = 'idle'
  owner.start = Mock()
  return page, owner


@pytest.mark.parametrize('x', [30, 800, 1900, 2130])
def test_large_route_row_opens_from_anywhere_and_back_keeps_catalog_page(route_detail, x):
  page, owner = route_detail
  page.page, page.scroll = 'routes', 5
  owner.routes = [{**ROUTE, 'routeId': f'{number:08d}--0123456789', 'startTime': STAMP - number} for number in range(8)]
  owner.select = Mock(return_value=True)
  pos = NS(x=x, y=feature_row_top(page._state()) + FEATURE_ROW_HEIGHT / 2)
  page._handle_mouse_press(pos)
  page._handle_mouse_release(pos)
  owner.select.assert_called_once_with(owner.routes[5])
  assert page.page == 'detail'
  page._back()
  assert page.page == 'routes' and page.scroll == 5


def test_route_swipe_pages_without_opening_and_cancels_taps_before_page_settles(route_detail):
  page, owner = route_detail
  page.page = 'routes'
  owner.routes = [{**ROUTE, 'routeId': f'{number:08d}--0123456789'} for number in range(8)]
  owner.select = Mock()
  page.view._last_state = page._state()
  y = feature_row_top(page._state()) + FEATURE_ROW_HEIGHT / 2
  page._handle_mouse_press(NS(x=900, y=y))
  page._handle_mouse_release(NS(x=650, y=y))
  assert page.scroll == 5
  owner.select.assert_not_called()
  page._handle_mouse_press(NS(x=900, y=y))
  page._handle_mouse_release(NS(x=900, y=y))
  owner.select.assert_not_called()


def test_large_details_show_route_inventory_without_diagnostic_action(route_detail, monkeypatch):
  from openpilot.starpilot.ui import replay_settings as module
  page, _ = route_detail
  monkeypatch.setattr(module, 'draw_settings_header', Mock())
  page._button = Mock()
  page._render_detail()
  texts = [call.args[0] for call in page.fonts.draw.call_args_list]
  assert ROUTE['routeId'] in texts
  assert 'Segments' in texts and '3' in texts and '2 available to play' in texts
  assert [button[0] for button in page._detail_buttons()] == ['< Back', 'Choose segment', 'Play from beginning']


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
@pytest.mark.parametrize('busy,available,playable', [(True, True, True), (False, False, True), (False, True, False)])
def test_detail_playback_actions_disable_together(route_detail, profile, busy, available, playable):
  page, owner = route_detail
  page.profile = profile
  owner.busy, owner.available = busy, available
  owner.detail['playable'] = playable
  for _, (x, y, width, height), enabled, _ in page._detail_buttons()[1:]:
    assert not enabled
    assert page._target(x + width / 2, y + height / 2) is None


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
def test_details_are_fixed_with_pinned_play_and_do_not_paginate(route_detail, monkeypatch, profile):
  from openpilot.starpilot.ui import replay_settings as module
  page, owner = route_detail
  page.profile = profile
  page.set_rect(rl.Rectangle(0, 0, *profile.size))
  page._button = Mock()
  monkeypatch.setattr(module, 'draw_settings_header', Mock())
  page._render_detail()
  buttons = page._button.call_args_list
  assert len(buttons) == (2 if profile == Profile.LARGE else 4)
  label, bounds, enabled = buttons[-1].args
  assert label == 'Play from beginning' and enabled
  assert bounds == ((744, 900, 1344, 144) if profile == Profile.LARGE else (224, 164, 300, 64))
  for _, rect, _, _ in page._detail_buttons():
    x, y, w, h = rect
    assert 0 <= x < x + w <= profile.size[0] and 0 <= y < y + h <= profile.size[1]
  assert not any('Warning 0' in call.args[0] for call in page.fonts.draw.call_args_list)
  x, y, w, h = bounds
  pos = NS(x=x + w / 2, y=y + h / 2)
  page._handle_mouse_press(pos)
  page._handle_mouse_release(pos)
  owner.start.assert_called_once_with()
  owner.start.reset_mock()
  owner.available = False
  page._handle_mouse_press(pos)
  page._handle_mouse_release(pos)
  owner.start.assert_not_called()


def test_segment_picker_filters_missing_sources_and_starts_exact_selection(route_detail, monkeypatch):
  from openpilot.starpilot.ui import replay_settings as module
  from openpilot.system.ui.widgets import DialogResult
  page, owner = route_detail
  picker = NS(selection='Segment 2', select_button=Mock())
  factory = Mock(return_value=picker)
  monkeypatch.setattr('openpilot.system.ui.widgets.option_dialog.MultiOptionDialog', factory)
  monkeypatch.setattr(module.gui_app, 'push_widget', Mock())
  monkeypatch.setattr(module.gui_app, 'get_active_widget', lambda: page)
  page._choose_segment()
  assert factory.call_args.args[1] == ['Segment 0', 'Segment 2']
  callback = factory.call_args.kwargs['callback']
  callback(DialogResult.CANCEL)
  owner.start.assert_not_called()
  callback(DialogResult.CONFIRM)
  owner.start.assert_called_once_with(segment=2)
  owner.start.reset_mock()
  owner.selected_route = 'another-route'
  callback(DialogResult.CONFIRM)
  owner.start.assert_not_called()


def test_compact_long_message_is_readable_in_full_without_displacing_playback(route_detail, monkeypatch):
  from openpilot.starpilot.ui import replay_settings as module
  page, owner = route_detail
  page.profile = Profile.COMPACT
  page.set_rect(rl.Rectangle(0, 0, *page.profile.size))
  owner.error = 'A long service error. ' * 30
  page._button = Mock()
  page._render_detail()
  assert page.detail_message.startswith(owner.error.rstrip())
  assert 'green Chill' in page.detail_message
  status = [call.args for call in page.fonts.draw.call_args_list if call.args[4] in (114, 135)]
  assert len(status) == 2
  assert status[1][0].endswith('...')
  assert page._detail_buttons()[-1][1] == (224, 164, 300, 64)
  dialog = NS(_scroller=NS(items=['cover', 'message']))
  factory = Mock(return_value=dialog)
  monkeypatch.setattr('openpilot.selfdrive.ui.mici.widgets.dialog.SettingDescriptionDialog', factory)
  monkeypatch.setattr(module.gui_app, 'font', Mock())
  monkeypatch.setattr(module, 'wrap_text', lambda _font, word, *_: [word])
  monkeypatch.setattr(module.gui_app, 'push_widget', Mock())
  pos = NS(x=472, y=134)
  page._handle_mouse_press(pos)
  page._handle_mouse_release(pos)
  factory.assert_called_once_with('replay', page.detail_message)
  assert dialog._scroller.items == ['message']
  module.gui_app.push_widget.assert_called_once_with(dialog)
  owner.error = 'Short message'
  page._render_detail()
  assert page.detail_message.startswith('Short message\n\nReplay timeline:')
  assert page._target(pos.x, pos.y) == 'More'


def test_compact_full_message_paginates_long_tokens_without_losing_content(route_detail, monkeypatch):
  from openpilot.starpilot.ui import replay_settings as module
  page, _ = route_detail
  page.profile, page.detail_message = Profile.COMPACT, 'W' * 128
  font = NS(texture=NS(id=987654321))
  monkeypatch.setattr(module.gui_app, 'font', lambda *_: font)
  monkeypatch.setattr(module.gui_app, 'texture', Mock(return_value=NS(width=64, height=64)))
  monkeypatch.setattr(module.gui_app, 'push_widget', Mock())
  monkeypatch.setattr(rl, 'measure_text_ex', lambda _font, text, size, _spacing: rl.Vector2(len(text) * size * .9, size))
  page._show_detail_message()
  cards = module.gui_app.push_widget.call_args.args[0]._scroller.items
  assert len(cards) > 1
  assert ''.join(card.get_value().strip() for card in cards) == page.detail_message
  for card in cards:
    assert card._sub_label.get_content_height(card._subtitle_width_hint()) <= card.rect.height - 2 * card.LABEL_VERTICAL_PADDING


@pytest.mark.parametrize('message', ['No road camera footage. Choose another route.',
                                    'MMMM WWWW iiiii\n' * 20, 'W' * 300])
def test_detail_lines_fit_the_bitmap_font_and_preserve_remaining_content(route_detail, message):
  page, _ = route_detail
  page.fonts.measure.side_effect = lambda text, _role, _size: NS(width=sum(14 if char == 'W' else 4 for char in text), height=20)
  lines, remaining = page._detail_lines(message, 120)
  assert 1 <= len(lines) <= 2
  assert all(page.fonts.measure(line, None, 18).width <= 120 for line in lines)
  assert ''.join(''.join(lines).split()) + ''.join(remaining.split()) == ''.join(message.split())


@pytest.mark.parametrize('state,expected', [('partial', '2 of 3 available to play'),
                                           ('loading', 'Loading route details...'),
                                           ('unavailable', 'No road camera footage. Choose another route.'),
                                           ('unavailable-guidance', 'No road camera footage. Choose another route.'),
                                           ('single', '1 segment')])
def test_compact_detail_status_and_counts_do_not_need_diagnostics(route_detail, state, expected):
  page, owner = route_detail
  page.profile = Profile.COMPACT
  page._button = Mock()
  if state == 'loading':
    owner.busy = True
    owner.detail['reason'] = 'No road camera footage'
  elif state.startswith('unavailable'):
    owner.detail.update(playable=False, reason=expected if state == 'unavailable-guidance' else 'No road camera footage')
  elif state == 'single':
    owner.detail.update(segmentCount=1, segments=owner.detail['segments'][:1])
  page._render_detail()
  texts = [call.args[0] for call in page.fonts.draw.call_args_list]
  assert expected in texts
  assert ROUTE['routeId'] in texts
  assert [button[0] for button in page._detail_buttons()] == ['< Back', 'More', 'Choose segment', 'Play from beginning']
  assert not any('Warning' in text for text in texts)


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
def test_detail_primary_fill_survives_return_from_collapsed_playback(route_detail, monkeypatch, profile):
  from openpilot.starpilot.ui import replay_settings as module
  page, _ = route_detail
  page.profile, page.controls = profile, False
  fill = Mock()
  monkeypatch.setattr(module, 'draw_rounded_fill', fill)
  monkeypatch.setattr(module, 'draw_rounded_stroke', Mock())
  page._button(*page._detail_buttons()[-1][:3])
  color = fill.call_args.args[1]
  expected = rl.color_lerp(module.ACCENT, module.PANEL_BG, .2) if profile == Profile.COMPACT else module.ACCENT
  assert (color.r, color.g, color.b, color.a) == (expected.r, expected.g, expected.b, expected.a)


@pytest.mark.parametrize('page_name', ['detail', 'viewer'])
@pytest.mark.parametrize('movement,activates', [(6, True), (12, True), (13, False)])
def test_compact_button_taps_allow_small_drift_but_cancel_drag(route_detail, page_name, movement, activates):
  page, owner = route_detail
  page.profile, page.page = Profile.COMPACT, page_name
  page.set_rect(rl.Rectangle(0, 0, *page.profile.size))
  bounds = page._detail_buttons()[-1][1] if page_name == 'detail' else page._transport()[1][1][1]
  x, y, width, height = bounds
  initial = NS(x=x + width / 2, y=y + height / 2)
  final = NS(x=initial.x + movement, y=initial.y)
  page._handle_mouse_press(initial)
  page._handle_mouse_event(mouse_event=NS(left_down=True, left_released=False, pos=final))
  page._handle_mouse_release(final)
  if page_name == 'detail':
    assert owner.start.call_count == int(activates)
  else:
    assert owner.control.call_count == int(activates)
    if activates:
      owner.control.assert_called_once_with('pause', paused=True)


def test_compact_segment_cards_close_chooser_and_start_exact_segment(route_detail, monkeypatch):
  from openpilot.starpilot.ui import replay_settings as module
  page, owner = route_detail
  page.profile = Profile.COMPACT
  buttons = []
  picker = NS(_scroller=NS(add_widgets=lambda widgets: buttons.extend(widgets)))
  active = [page.stack_widget]
  monkeypatch.setattr('openpilot.system.ui.widgets.scroller.NavScroller', Mock(return_value=picker))
  factory = Mock(side_effect=lambda *_: Mock())
  monkeypatch.setattr(module, 'BigButton', factory)
  monkeypatch.setattr(module.gui_app, 'push_widget', lambda widget: active.__setitem__(0, widget))
  monkeypatch.setattr(module.gui_app, 'pop_widget', lambda: active.__setitem__(0, page.stack_widget))
  monkeypatch.setattr(module.gui_app, 'get_active_widget', lambda: active[0])
  page._choose_segment()
  assert [call.args[0] for call in factory.call_args_list] == ['segment 0', 'segment 2']
  assert all(call.args[1] == 'date unavailable\nstart here' for call in factory.call_args_list)
  callback = buttons[1].set_click_callback.call_args.args[0]
  callback()
  assert active[0] is page.stack_widget
  owner.start.assert_called_once_with(segment=2)
  owner.start.reset_mock()
  active[0] = picker
  owner.selected_route = 'another-route'
  callback()
  owner.start.assert_not_called()


@pytest.mark.parametrize('page_name,returns_to_routes,dismisses', [('detail', True, False), ('routes', False, True), ('viewer', False, False)])
def test_compact_swipe_down_goes_back_one_level_and_is_blocked_during_playback(monkeypatch, page_name, returns_to_routes, dismisses):
  from openpilot.starpilot.ui import replay_settings as module
  monkeypatch.setattr(module.gui_app, 'texture', Mock(return_value=NS(width=96, height=48)))
  controller = NS(close=Mock())
  page = ReplayCompactPage(NS(fonts=Mock(profile=Profile.COMPACT), profile=Profile.COMPACT), controller)
  page.set_rect(rl.Rectangle(0, 0, 536, 240))
  page.content.page = page_name
  page.content.press = (200, 40, 'held button', 1, True)
  for y, pressed, down, released in [(40, True, True, False), (160, False, True, False), (160, False, False, True)]:
    page._handle_mouse_event(mouse_event=NS(pos=NS(x=200, y=y), left_pressed=pressed, left_down=down, left_released=released))
  assert page._playing_dismiss_animation is dismisses
  assert page.content.page == ('routes' if returns_to_routes else page_name)
  if returns_to_routes:
    assert page.content.press is None
  controller.close.assert_not_called()


def test_compact_route_card_shows_segment_count_and_availability(route_detail, monkeypatch):
  from openpilot.starpilot.ui import replay_settings as module
  page, _ = route_detail
  page.page, page.profile = 'routes', Profile.COMPACT
  page.cards = NS(_scroller=NS(items=[], add_widgets=Mock()))
  factory = Mock()
  monkeypatch.setattr(module, 'BigButton', factory)
  monkeypatch.setattr(module.gui_app, '_mouse_events', [])
  page._compact_cards()
  assert factory.call_args.args[1] == 'date unavailable\n2 segments'
  factory.return_value._sub_label.set_font_size.assert_called_once_with(30)


def test_compact_underlying_buttons_are_disabled_during_overlays_and_back_gestures(monkeypatch):
  from openpilot.starpilot.ui import replay_settings as module
  monkeypatch.setattr(module.gui_app, 'texture', Mock(return_value=NS(width=96, height=48)))
  page = ReplayCompactPage(NS(fonts=Mock(profile=Profile.COMPACT), profile=Profile.COMPACT), NS(close=Mock()))
  assert page.content.enabled and page.content.cards.enabled
  page.set_enabled(False)
  assert not page.content.enabled and not page.content.cards.enabled
  page.set_enabled(True)
  page._dragging_down = True
  assert not page.content.enabled and not page.content.cards.enabled
  page._dragging_down = False
  assert page.content.enabled and page.content.cards.enabled


@pytest.mark.parametrize('profile', [Profile.LARGE, Profile.COMPACT])
@pytest.mark.parametrize('state,error', [('preparing', ''), ('seeking', ''), ('buffering', ''),
                                       ('playing', ''), ('failed', 'Replay failed')])
def test_loading_is_centered_and_warnings_and_unknown_timing_stay_out_of_viewer(monkeypatch, profile, state, error):
  from openpilot.starpilot.ui import replay_settings as module
  page, owner = page_for_input()
  page.profile = profile
  page._button = Mock()
  monkeypatch.setattr(module, 'draw_rounded_fill', Mock())
  page.fonts.measure.return_value = NS(width=200, height=32)
  owner.available, owner.busy, owner.error, owner.frame = True, False, error, None
  owner.status.update(state=state, end=None, sourceWarnings=['This route has missing segments'])
  monkeypatch.setattr(module, 'ui_state', NS(started=False))
  monkeypatch.setattr(rl, 'draw_rectangle', Mock())
  monkeypatch.setattr(rl, 'draw_circle', Mock())
  ring = Mock()
  monkeypatch.setattr(rl, 'draw_ring', ring)
  monkeypatch.setattr(module.time, 'monotonic', lambda: 1)
  page._render_viewer()
  texts = [call.args[0] for call in page.fonts.draw.call_args_list]
  assert 'Timing unavailable' not in texts
  assert not any('missing segments' in text for text in texts)
  if error:
    ring.assert_not_called()
    assert error in texts
  else:
    assert ('Replay preparing' if state == 'preparing' else 'Replay loading') in texts
    center, _, radius, angle, *_ = ring.call_args.args
    assert center.x == profile.size[0] / 2
    text = page.fonts.draw.call_args.args
    assert text[3] + 100 == center.x
    assert text[4] > center.y + radius
    monkeypatch.setattr(module.time, 'monotonic', lambda: 1.1)
    page._render_viewer()
    assert ring.call_args.args[3] != angle
