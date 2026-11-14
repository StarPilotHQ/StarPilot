"""Favorite actions use real navigation storage, layouts and the touch transport."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.navigation.owner import NavigationOwner, destination
from openpilot.starpilot.system.starport.current_car_ui import projected_touches
from openpilot.starpilot.system.starport.projection_favorites import ProjectionFavorites, destination_label
from openpilot.starpilot.system.starport.projection_geometry import projection_geometry
from openpilot.starpilot.system.starport.projection_layout import (
  BOOKMARK_WIDGET, CAR_EXIT, FAVORITE_WIDGETS, default_layout_for_viewport, layout_metadata_for_viewport, projection_customization,
  validate_layout_for_viewport,
)
from openpilot.starpilot.system.starport.touch import InputConfig, TouchMapper, TouchReceiver, TouchSender
from openpilot.starpilot.system.starport.wire import field
from openpilot.starpilot.ui.onroad_customization import default_document

VIEWPORT = (2880, 1080)


@pytest.fixture
def favorites(tmp_path, monkeypatch):
  owner = NavigationOwner(tmp_path, runtime_source=dict, transient_root=tmp_path / 'transient')
  monkeypatch.setattr(owner, '_is_metric', lambda: True)
  home = dict(destination({'name': 'My home', 'latitude': 36.1, 'longitude': -115.1}), label='home')
  work = dict(destination({'name': 'My work', 'latitude': 36.2, 'longitude': -115.2}), label='work')
  owner.path.write_text(json.dumps({'version': 1, 'revision': 'initial', 'enabled': True, 'token': 'test-token',
                                    'destination': None, 'favorites': [home, work], 'recents': []}))
  control = ProjectionFavorites(lambda: True, owner)
  control.refresh(force=True)
  yield control
  control.close()


def test_destination_label_names_home_and_work(favorites):
  document = favorites.document
  home, work = document['favorites']
  assert destination_label(None) == ''
  assert destination_label(document) == ''
  assert destination_label({**document, 'destination': home}) == 'Home'
  assert destination_label({**document, 'destination': work}) == 'Work'
  other = destination({'name': 'Costco', 'latitude': 36.3, 'longitude': -115.3})
  assert destination_label({**document, 'destination': other}) == 'Costco'


@pytest.mark.parametrize('key', ['nav_home', 'nav_work', 'bookmark'])
def test_music_overlay_and_actions_follow_saved_layer_order(favorites, key):
  view = state(ordered=True)
  placed = view.customization['layouts']['large'][key]
  placed.update(enabled=True, display='icons')
  overlay = ('now_playing', NS(x=placed['x'], y=placed['y'], width=520, height=96))
  order = view.customization['widgetOrder']['large']
  order.remove('now_playing')
  order.append('now_playing')
  x, y = point(view, key)
  assert favorites.hit(x, y, view, overlay=overlay) == 'now_playing'
  order.remove(key)
  order.append(key)
  assert favorites.hit(x, y, view, overlay=overlay) == key


def state(*, ordered=False):
  layout = default_layout_for_viewport(VIEWPORT)
  for key in FAVORITE_WIDGETS:
    layout['widgets'][key]['enabled'] = True
  if ordered:
    layout['widgetOrder'] = layout_metadata_for_viewport(VIEWPORT)['widgetOrder']
  return NS(customization=projection_customization(layout, default_document()), alert=NS(size='none'))


def point(state, key):
  placed = state.customization['layouts']['large'][key]
  return placed['x'] + (55 if placed.get('display') == 'icons' else 160), placed['y'] + 55


def tap(control, state, key):
  x, y = point(state, key)
  control.touch('down', x, y, state, 1)
  control.touch('up', x, y, state, 1)


@pytest.mark.parametrize('display', ['icons', 'words'])
def test_bookmark_tap_works_without_navigation_and_preserves_navigation(favorites, display):
  view = state()
  placed = view.customization['layouts']['large'][BOOKMARK_WIDGET]
  favorites.bookmark = Mock(return_value=True)
  tap(favorites, view, BOOKMARK_WIDGET)
  favorites.bookmark.assert_not_called()  # opt in only
  placed.update(enabled=True, display=display, opacity=30, background=False)
  tap(favorites, view, 'nav_home')
  destination_before = favorites.owner.read()['destination']
  tap(favorites, view, BOOKMARK_WIDGET)
  favorites.bookmark.assert_called_once_with()
  assert favorites.owner.read()['destination'] == destination_before
  favorites.owner.read = Mock(side_effect=OSError('navigation unavailable'))
  tap(favorites, view, BOOKMARK_WIDGET)
  assert favorites.bookmark.call_count == 2
  if display == 'icons':
    assert favorites.hit(placed['x'] + 150, placed['y'] + 55, view) != BOOKMARK_WIDGET


@pytest.mark.parametrize('cancel', ['drag', 'alert', 'offroad', 'new_drive', 'disabled', 'cancel'])
def test_bookmark_requires_valid_release_in_same_drive(favorites, cancel):
  view = state()
  view.customization['layouts']['large'][BOOKMARK_WIDGET]['enabled'] = True
  favorites.bookmark = Mock()
  x, y = point(view, BOOKMARK_WIDGET)
  favorites.touch('down', x, y, view, 1)
  if cancel == 'alert':
    view.alert.size = 'full'
  elif cancel == 'offroad':
    favorites.authorized = lambda: False
  elif cancel == 'disabled':
    view.customization['layouts']['large'][BOOKMARK_WIDGET]['enabled'] = False
  elif cancel == 'cancel':
    favorites.touch('cancel', x, y, view, 1)
  favorites.touch('up', x + (30 if cancel == 'drag' else 0), y, view, 2 if cancel == 'new_drive' else 1)
  favorites.bookmark.assert_not_called()


def test_bookmark_and_favorites_respect_saved_order(favorites):
  view = state(ordered=True)
  placements = view.customization['layouts']['large']
  placements[BOOKMARK_WIDGET].update(enabled=True, x=placements['nav_home']['x'], y=placements['nav_home']['y'])
  favorites.bookmark = Mock(return_value=True)
  tap(favorites, view, 'nav_home')
  favorites.bookmark.assert_called_once()
  assert favorites.owner.read()['destination'] is None
  order = view.customization['widgetOrder']['large']
  order.remove('nav_home')
  order.insert(-1, 'nav_home')
  tap(favorites, view, BOOKMARK_WIDGET)
  assert favorites.bookmark.call_count == 1
  assert favorites.owner.read()['destination']['name'] == 'My home'


def test_map_above_home_blocks_touch(favorites):
  view = state(ordered=True)
  placed = view.customization['layouts']['large']['nav_home']
  view.customization['layouts']['large']['nav_map'].update(enabled=True, x=placed['x'], y=placed['y'])
  order = view.customization['widgetOrder']['large']
  order.remove('nav_map')
  order.append('nav_map')
  assert favorites.hit(*point(view, 'nav_home'), view) is None
  tap(favorites, view, 'nav_home')
  assert favorites.owner.read()['destination'] is None
  order.remove('nav_home')
  order.append('nav_home')
  assert favorites.hit(*point(view, 'nav_home'), view) == 'nav_home'


@pytest.mark.parametrize('ordered', [True, False])
@pytest.mark.parametrize('display', ['words', 'icons'])
def test_home_work_start_hide_other_and_end_navigation(favorites, ordered, display):
  view = state(ordered=ordered)
  for key in FAVORITE_WIDGETS:
    view.customization['layouts']['large'][key]['display'] = display
  tap(favorites, view, 'nav_home')
  doc = favorites.owner.read()
  assert doc['destination']['name'] == 'My home'
  assert favorites.action('nav_home')[0] == 'end'
  assert favorites.bounds('nav_work', view) is None
  assert doc['recents'][0]['name'] == 'My home'
  tap(favorites, view, 'nav_work')
  assert favorites.owner.read()['destination']['name'] == 'My home', 'hidden buttons cannot replace the route'
  tap(favorites, view, 'nav_home')
  assert favorites.owner.read()['destination'] is None
  tap(favorites, view, 'nav_work')
  assert favorites.owner.read()['destination']['name'] == 'My work'
  assert favorites.bounds('nav_home', view) is None
  tap(favorites, view, 'nav_work')
  assert favorites.owner.read()['destination'] is None
  assert favorites.action('nav_work')[0] == 'start'
  assert favorites.bounds('nav_home', view) is not None


@pytest.mark.parametrize('change', ['drag', 'cancel', 'offroad', 'drive', 'alert', 'remove', 'relabeled', 'external_route'])
def test_changed_or_cancelled_press_does_not_navigate(favorites, change):
  view = state()
  x, y = point(view, 'nav_home')
  favorites.touch('down', x, y, view, 1)
  if change == 'drag':
    favorites.touch('move', x + 40, y, view, 1)
  elif change == 'cancel':
    favorites.touch('cancel', 0, 0, view, 1)
  elif change == 'offroad':
    favorites.authorized = lambda: False
  elif change == 'alert':
    view.alert.size = 'full'
  elif change == 'remove':
    view.customization['layouts']['large']['nav_home']['enabled'] = False
  elif change == 'relabeled':
    favorites.owner.label_favorite(favorites.document['favorites'][0]['id'], None, 'initial', True)
  elif change == 'external_route':
    favorites.owner.select(favorites.document['favorites'][1], 'initial', True)
  expected = favorites.owner.read()
  favorites.touch('up', x, y, view, 2 if change == 'drive' else 1)
  assert favorites.owner.read() == expected


def test_unconfigured_and_disabled_navigation_do_not_write(favorites):
  view = state()
  for doc in [dict(favorites.document, enabled=False), dict(favorites.document, token=''), dict(favorites.document, favorites=[])]:
    favorites.owner.path.write_text(json.dumps(doc))
    tap(favorites, view, 'nav_home')
    assert favorites.owner.read()['destination'] is None
  # Ending a selected route still works after navigation has been disabled.
  doc: dict = dict(favorites.document, favorites=[dict(destination({'name': 'Home', 'latitude': 1, 'longitude': 2}), label='home')])
  doc['destination'] = doc['favorites'][0]
  favorites.owner.path.write_text(json.dumps(doc))
  tap(favorites, view, 'nav_home')
  assert favorites.owner.read()['destination'] is None


def test_overlap_uses_saved_order_and_removed_widget_cannot_intercept(favorites):
  view = state(ordered=True)
  placements = view.customization['layouts']['large']
  placements['nav_work'].update(x=placements['nav_home']['x'], y=placements['nav_home']['y'])
  tap(favorites, view, 'nav_home')
  assert favorites.owner.read()['destination']['name'] == 'My work'
  order = view.customization['widgetOrder']['large']
  order.remove('nav_home')
  order.append('nav_home')
  tap(favorites, view, 'nav_home')
  assert favorites.owner.read()['destination'] is None, 'the active work button is the only overlapping touch target'
  placements['nav_work']['enabled'] = False
  tap(favorites, view, 'nav_home')
  assert favorites.owner.read()['destination']['name'] == 'My home'
  placements['nav_home']['enabled'] = False
  placements['nav_work']['enabled'] = True
  tap(favorites, view, 'nav_work')
  assert favorites.owner.read()['destination']['name'] == 'My work'


def test_icon_touches_do_not_intercept_the_old_word_card_area(favorites):
  view = state()
  placed = view.customization['layouts']['large']['nav_home']
  placed['display'] = 'icons'
  assert favorites.hit(placed['x'] + 55, placed['y'] + 55, view) == 'nav_home'
  assert favorites.hit(placed['x'] + 160, placed['y'] + 55, view) is None
  assert favorites.hit(placed['x'] + 110, placed['y'] + 55, view) is None
  x, y = placed['x'] + 55, placed['y'] + 55
  favorites.touch('down', x, y, view, 1)
  favorites.touch('up', x, y, view, 1)
  assert favorites.owner.read()['destination']['name'] == 'My home'


def test_old_layout_migrates_favorites_without_enabling_or_reordering_existing_widgets():
  layout = default_layout_for_viewport(VIEWPORT)
  layout['widgetOrder'] = layout_metadata_for_viewport(VIEWPORT)['widgetOrder']
  for key in FAVORITE_WIDGETS:
    layout['widgets'].pop(key)
    layout['widgetOrder'].remove(key)
  original = copy.deepcopy(layout)
  migrated = validate_layout_for_viewport(layout, VIEWPORT)
  assert migrated['widgetOrder'] == [*(key for key in original['widgetOrder'] if key != CAR_EXIT), *FAVORITE_WIDGETS, CAR_EXIT]
  assert all(not migrated['widgets'][key]['enabled'] for key in FAVORITE_WIDGETS)
  assert {key: placed for key, placed in migrated['widgets'].items() if key not in FAVORITE_WIDGETS} == original['widgets']
  assert layout == original
  migrated['widgets']['nav_home'].update(x=100, y=200, enabled=True)
  assert validate_layout_for_viewport(migrated, VIEWPORT) == migrated


def test_head_unit_touch_packet_reaches_real_navigation_owner(favorites):
  view = state()
  x, y = point(view, 'nav_home')
  # 1280x720 frame with 240 vertical margin -> a 2880x1080 scene.
  config = InputConfig(touch_width=1280, touch_height=720)
  mapper = TouchMapper(config, 1280, 720, 0, 240)
  geometry = projection_geometry(1280, 720, 0, 240)
  with tempfile.TemporaryDirectory(prefix='aa-touch-') as directory:
    path = str(Path(directory) / 'touch.sock')
    receiver, sender = TouchReceiver(path), TouchSender(path)
    try:
      for action in (0, 1):
        location = field(1, round(x / VIEWPORT[0] * 1280)) + field(2, round(y / VIEWPORT[1] * 480 + 120)) + field(3, 0)
        packet = field(3, field(1, location) + field(3, action))
        sender.send(mapper.decode(packet))
      for event in projected_touches(receiver.drain(), geometry):
        favorites.touch(event.kind, event.x * VIEWPORT[0], event.y * VIEWPORT[1], view, 1)
      assert favorites.owner.read()['destination']['name'] == 'My home'
    finally:
      sender.close()
      receiver.close()


def test_letterboxed_touches_map_to_scene_and_padding_cancels():
  from openpilot.starpilot.system.starport.touch import TouchEvent
  geometry = projection_geometry(1920, 1080, 1800, 0)  # bounded fallback canvas with vertical letterboxing
  events = projected_touches([TouchEvent('down', .5, .5), TouchEvent('down', .5, 0)], geometry)
  assert events == [TouchEvent('down', .5, .5), TouchEvent('cancel', 0, 0)]


def test_current_renderer_enables_touch_forwarding_on_custom_socket():
  from openpilot.starpilot.system.starport.view import ViewSource
  from openpilot.starpilot.system.starport.frame_source import FrameRequest
  from openpilot.starpilot.system.starport.touch import TouchEvent
  process = Mock(pid=123)
  process.poll.return_value = 0
  with patch('openpilot.starpilot.system.starport.view.subprocess.Popen', return_value=process) as popen, \
       patch.object(ViewSource, '_consumer', return_value=Mock()), \
       patch('openpilot.starpilot.system.starport.view.TouchSender') as sender, \
       patch('openpilot.starpilot.system.starport.view.ProjectionControlReceiver') as control:
    view = ViewSource('car', FrameRequest(1280, 720, 0, 240, 33333), Mock(), touch_path='/tmp/test-aa-touch',
                      control_path='/tmp/test-aa-control')
    assert popen.call_args.args[0][-4:] == ['--touch', '/tmp/test-aa-touch', '--control', '/tmp/test-aa-control']
    event = TouchEvent('down', .2, .3)
    view.send_touches([event])
    sender.return_value.send.assert_called_once_with([event])
    control.return_value.drain.return_value = ['native_focus']
    assert view.drain_controls() == ['native_focus']
    view.close()
    sender.return_value.close.assert_called_once()
    control.return_value.close.assert_called_once()
