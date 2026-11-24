"""With "Black screen while StarPort streams" on, the comma's own driving screen is black with alerts only while
StarPort's drive view reaches the car screen."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.ui import drive_render, onroad, onroad_state, streaming_blackout
from openpilot.starpilot.ui.developer_preview import OnroadVisualPreview
from openpilot.starpilot.ui.onroad_customization import default_document, fit_to_road, validate_document
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import Profile

PROFILES = [Profile.COMPACT, Profile.LARGE]


def state(chosen=True, **changes):
  document = default_document()
  if chosen is not None:
    document[streaming_blackout.FIELD] = chosen
  return OnroadState(True, True, 20, 80, SpeedLimitObservation(), alert=OnroadAlert('Take Control', 'x', AlertSize.FULL),
                     customization=document, **changes)


def view(profile, streaming=True, *, projection=None):
  view = object.__new__(onroad.OnroadView)
  view.fonts = NS(profile=profile)
  view.projection_viewport = projection
  view.camera_layer = Mock()
  view.alert = Mock()
  view.streaming_blackout = Mock(return_value=streaming)
  view._large, view._compact = Mock(), Mock()
  return view


def test_the_choice_is_off_unless_the_document_says_on():
  assert streaming_blackout.FIELD not in default_document()
  assert not streaming_blackout.enabled(default_document())
  assert streaming_blackout.enabled({streaming_blackout.FIELD: True})
  for bad in (False, None, 1, 'true', 'On'):
    assert not streaming_blackout.enabled({streaming_blackout.FIELD: bad})
  assert not streaming_blackout.enabled(None)


@pytest.mark.parametrize('choice', [True, False])
def test_document_keeps_a_boolean_choice(choice):
  document = default_document()
  document[streaming_blackout.FIELD] = choice
  validated = validate_document(document)
  assert validated[streaming_blackout.FIELD] is choice
  assert fit_to_road(validated, 1560)[streaming_blackout.FIELD] is choice


@pytest.mark.parametrize('bad', ['', 'On', None, 1, 0, ['x']])
def test_document_rejects_a_choice_that_is_not_a_boolean(bad):
  document = default_document()
  document[streaming_blackout.FIELD] = bad
  with pytest.raises(ValueError, match='black screen while streaming'):
    validate_document(document)


@pytest.mark.parametrize('profile', PROFILES)
def test_streaming_with_the_choice_on_draws_black_and_the_alert(profile):
  v, current = view(profile), state()
  with patch.object(onroad, 'rl') as rl, patch.object(onroad, 'clip'), patch.object(onroad, 'render_live_sidebar'):
    v.render(current)
  v.camera_layer.assert_not_called()  # no VisionIPC receive and no texture upload either: both live in this callback
  v._large.assert_not_called()
  v._compact.assert_not_called()
  v.alert.render.assert_called_once()
  assert v.alert.render.call_args.args[1] == current.alert
  assert (rl.draw_rectangle.call_args or rl.draw_rectangle_rec.call_args).args[-1] is rl.BLACK


@pytest.mark.parametrize('profile', PROFILES)
@pytest.mark.parametrize('chosen', [None, False])
def test_the_choice_off_keeps_the_normal_screen_and_never_reads_the_lease(profile, chosen):
  v = view(profile)
  v.render(state(chosen))
  (v._large if profile == Profile.LARGE else v._compact).assert_called_once()
  v.alert.render.assert_not_called()
  v.streaming_blackout.assert_not_called()


@pytest.mark.parametrize('profile', PROFILES)
def test_no_stream_keeps_the_normal_screen(profile):
  v = view(profile, streaming=False)
  v.render(state())
  (v._large if profile == Profile.LARGE else v._compact).assert_called_once()
  v.alert.render.assert_not_called()


@pytest.mark.parametrize('profile', PROFILES)
def test_projection_preview_reverse_camera_and_a_waiting_speed_limit_keep_the_normal_screen(profile):
  for v, current in ((view(profile, projection=(1860, 1080)), state()),
                     (view(profile), state(visual_preview=OnroadVisualPreview(layout_sample=True))),
                     (view(profile), state(reverse_driver_camera=True))):
    assert not v.streaming_blackout_active(current)
  v = view(profile)
  with patch.object(onroad, 'manual_slc_confirmation', return_value=True):
    assert not v.streaming_blackout_active(state())
    v.render(state())
  (v._large if profile == Profile.LARGE else v._compact).assert_called_once()
  unset = view(profile)
  del unset.streaming_blackout
  assert not unset.streaming_blackout_active(state())


def test_large_screen_keeps_quick_select_the_sidebar_and_the_uniden_border():
  v, current = view(Profile.LARGE), state()
  v.quick_select_layer, v.uniden = Mock(), Mock()
  current = replace(current, appearance=replace(current.appearance, show_developer_sidebar=True))
  with patch.object(onroad, 'rl') as rl, patch.object(onroad, 'clip'), patch.object(onroad, 'render_live_sidebar') as sidebar:
    rl.Rectangle = lambda *values: values
    v.render(current)
  content = (30, 30, current.viewport_width - 60, 1020)
  v.quick_select_layer.assert_called_once_with(content, current)
  v.alert.render.assert_called_once_with(content, current.alert)
  sidebar.assert_called_once()
  v.uniden.render_border.assert_called_once_with(current, current.viewport_width, 1080)
  rl.draw_rectangle_rounded_lines_ex.assert_not_called()  # no engagement border: the screen is black


def test_black_screen_wins_over_the_drive_render():
  v = view(Profile.LARGE)
  v.drive_render = Mock()
  v.drive_render.ready.return_value = True
  document = default_document()
  document.update({streaming_blackout.FIELD: True, drive_render.FIELD: 'device'})
  current = replace(state(), customization=document)
  assert v.streaming_blackout_active(current) and not v.drive_render_active(current)
  with patch.object(onroad, 'rl'), patch.object(onroad, 'clip'), patch.object(onroad, 'render_live_sidebar'):
    v.render(current)
  v.drive_render.render.assert_not_called()
  v.streaming_blackout.return_value = False
  assert v.drive_render_active(current)


def test_native_session_wires_the_lease_reader_and_hides_the_widgets_from_taps():
  from openpilot.starpilot.system.starport.projection_stream_lease import StreamLeaseReader
  from openpilot.starpilot.ui.runtime_app import StarShellSession
  source = Path(onroad.__file__).with_name('runtime_app.py').read_text()
  assert 'self.view.onroad.streaming_blackout = StreamLeaseReader()' in source
  assert 'self.input.onroad.widgets_hidden = self._widgets_hidden' in source
  assert 'or self._streaming_blackout(snapshot.onroad):' in source
  assert callable(StreamLeaseReader('/nonexistent')) and StreamLeaseReader('/nonexistent')() is False
  for profile in PROFILES:
    session = NS(view=NS(onroad=view(profile)))
    session._streaming_blackout = lambda current, session=session: StarShellSession._streaming_blackout(session, current)
    session._drive_render_active = lambda current: False
    assert StarShellSession._streaming_blackout(session, state()) is True
    assert StarShellSession._widgets_hidden(session, state()) is True
    assert StarShellSession._widgets_hidden(session, state(False)) is False
  assert StarShellSession._streaming_blackout(NS(view=NS()), state()) is False


@pytest.mark.parametrize('profile', PROFILES)
def test_black_screen_takes_no_personality_tap(profile):
  current = replace(state(), alert=OnroadAlert())
  drawn, hidden = onroad_state.OnroadInput(Mock(), profile), onroad_state.OnroadInput(Mock(), profile)
  hidden.widgets_hidden = lambda _state: True
  with patch.object(onroad_state, 'personality_bounds', return_value=(0, 0, 100, 100)):
    drawn.press(50, 50, current)
    hidden.press(50, 50, current)
  assert drawn._personality_press is not None
  assert hidden._personality_press is None and not hidden.claimed
