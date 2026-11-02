"""Native settings update/render lifecycle keeps one current projection per frame."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import runtime_app
from openpilot.starpilot.ui.tests.test_runtime_snapshot import RuntimeSnapshotAdapter, ui_fake
from openpilot.starpilot.ui.settings_state import SettingsState, compact_menu
from openpilot.system.ui.lib.scroll_panel2 import ScrollState
from openpilot.system.ui import widgets


@pytest.fixture
def lifecycle(monkeypatch):
  ui = ui_fake()
  paired = [True]
  ui.prime_state.is_paired = lambda: paired[0]
  adapter = RuntimeSnapshotAdapter(ui)
  adapter.build = Mock(wraps=adapter.build)
  session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
  session.slc_actions = None
  monkeypatch.setattr(session, 'drive_state', SimpleNamespace(snapshot=lambda: {"mode": "auto", "revision": None, "available": False,
                                                  "effective": None, "overrideAllowed": False}), raising=False)
  session.adapter = adapter
  session.profile = runtime_app.Profile.COMPACT
  session.selected = runtime_app.Destination.STAR
  session.compact_y = session.compact_scroll_x = 0
  session.sidebar_expanded = True
  session._snapshot_cache = None
  session.input = Mock()
  session.favorites = Mock()
  session.pip_warning = Mock()
  session.view = Mock()
  session.notice = ""
  monkeypatch.setattr(runtime_app, "placed_at", lambda *_: nullcontext())
  monkeypatch.setattr(widgets, "device", SimpleNamespace(awake=True))
  monkeypatch.setattr(rl, "get_time", lambda: 100.0)
  monkeypatch.setattr(rl, "get_frame_time", lambda: 1 / 60)
  for name in ("draw_rectangle_rec", "draw_rectangle_rounded", "draw_rectangle_rounded_lines_ex"):
    monkeypatch.setattr(rl, name, lambda *_: None)
  monkeypatch.setattr(runtime_app.gui_app, "_mouse_events", [])
  monkeypatch.setattr(runtime_app.gui_app, "_show_touches", False)
  page = runtime_app.StarCompactSettings(session)
  page.set_rect(rl.Rectangle(0, 0, 536, 240))
  return page, session, ui, paired


@pytest.mark.parametrize("state", list(ScrollState))
def test_main_menu_push_resets_position_and_momentum(lifecycle, monkeypatch, state):
  page, session, _, _ = lifecycle
  monkeypatch.setattr(runtime_app.gui_app, "_nav_stack", [Mock()])
  for offset in (-422, -1266):
    page._panel.set_offset(offset)
    page._panel._state = state
    page._panel._velocity = -1500
    page._panel._velocity_buffer.extend([-1200, -1500])
    session.compact_scroll_x = offset
    session._snapshot_cache = object()
    runtime_app.gui_app.push_widget(page)
    assert page._panel.get_offset() == session.compact_scroll_x == 0
    assert page._panel.state == ScrollState.STEADY
    assert page._panel.velocity == 0
    assert not page._panel._velocity_buffer
    assert session._snapshot_cache is None
    page._update_state()
    assert page._panel.get_offset() == session.compact_scroll_x == 0
    runtime_app.gui_app.pop_widget()


def test_submenu_pop_keeps_main_menu_position(lifecycle, monkeypatch):
  page, session, _, _ = lifecycle
  monkeypatch.setattr(runtime_app.gui_app, "_nav_stack", [Mock()])
  runtime_app.gui_app.push_widget(page)
  page._panel.set_offset(-844)
  session.compact_scroll_x = -844
  panel = page._panel
  runtime_app.gui_app.push_widget(Mock())
  assert not page.enabled
  runtime_app.gui_app.pop_widget()
  assert page.enabled
  assert page._panel is panel
  assert page._panel.get_offset() == session.compact_scroll_x == -844


@pytest.mark.parametrize("paired_value", [False, True])
@pytest.mark.parametrize("moving", [False, True])
def test_native_frame_projects_once_after_real_panel_update(lifecycle, paired_value, moving):
  page, session, ui, paired = lifecycle
  paired[0] = paired_value
  if moving:
    page._panel._state = ScrollState.AUTO_SCROLL
    page._panel._velocity = -1500
    page._panel.set_offset(-140)
  offsets = []
  for _ in range(60):
    ui.sm.frame += 1
    page.render()
    snapshot = session.view.render.call_args.args[0]
    assert snapshot.settings.compact_scroll_x == page._panel.get_offset()
    assert snapshot.settings.paired is paired_value
    offsets.append(page._panel.get_offset())
  assert session.adapter.build.call_count == 60
  assert session.adapter.build.call_args.kwargs["menu_only"] is True
  assert (len(set(offsets)) > 1) is moving
  assert page._panel._horizontal
  session.favorites.cancel.assert_called()


def test_pairing_changes_recompute_width_without_early_snapshot(lifecycle):
  page, session, ui, paired = lifecycle
  update = Mock(wraps=page._panel.update)
  page._panel.update = update
  for index, value in enumerate((True, False, True)):
    paired[0] = value
    ui.sm.frame += 1
    page._update_state()
    assert session.adapter.build.call_count == index
    assert update.call_args.args[1] == 20 + len(compact_menu(SettingsState(paired=value))) * 422
    page._render(page.rect)
    assert session.view.render.call_args.args[0].settings.paired is value
  assert session.adapter.build.call_count == 3


def test_hidden_settings_still_updates_navigation_but_does_not_project(lifecycle):
  page, session, _, _ = lifecycle
  page.set_visible(False)
  page._panel.set_offset(-140)
  page._panel._state = ScrollState.AUTO_SCROLL
  page._panel._velocity = -1500
  page.render()
  assert page._panel.get_offset() != -140
  session.adapter.build.assert_not_called()
  session.view.render.assert_not_called()


@pytest.mark.parametrize("started", [False, True])
def test_driving_controls_card_opens_shared_editor_from_live_menu(lifecycle, monkeypatch, started):
  from dataclasses import replace
  from openpilot.starpilot.ui import feature_settings_compact
  from openpilot.starpilot.ui.settings_state import SettingsInput
  from openpilot.starpilot.ui.shell import ShellRequest
  _, session, ui, _ = lifecycle
  ui.started = started
  session._mode = runtime_app.ShellMode.SETTINGS
  layout = runtime_app.StarMiciMainLayout.__new__(runtime_app.StarMiciMainLayout)
  layout.star = session
  session._on_compact_destination = layout._open_compact_destination
  editor = Mock()
  monkeypatch.setattr(feature_settings_compact, "FeatureSettingsCompact", editor)
  snapshot = session.snapshot(runtime_app.ShellMode.SETTINGS)
  index = next(i for i, item in enumerate(compact_menu(snapshot.settings))
               if item[0] == runtime_app.Destination.DRIVING_CONTROLS)
  state = replace(snapshot.settings, compact_scroll_x=-422 * index)
  inputs = SettingsInput(session.profile, lambda action: session._emit(ShellRequest("settings", action)))
  inputs.press(221, 120, state)
  editor.assert_not_called()
  inputs.release(221, 120, state)
  assert session.notice == ""
  editor.assert_called_once_with(session)
  editor.return_value.open.assert_called_once_with(runtime_app.FeaturePage.HUB)


@pytest.mark.parametrize("mode,allowed,label,target", [
  ("auto", True, "Force Off-road", "offroad"),
  ("auto", False, "Force Off-road", "offroad"),
  ("offroad", True, "Force On-road", "onroad"),
  ("onroad", True, "Return to Auto", "auto"),
  ("offroad", False, "Force On-road", "onroad"),
])
def test_force_menu_names_requested_action_with_supported_glyphs(lifecycle, mode, allowed, label, target):
  from pathlib import Path
  import re
  page, session, ui, _ = lifecycle
  revision = "actual-owner-revision"
  session.drive_state.snapshot = lambda: {"mode": mode, "revision": revision, "available": True,
                                        "effective": None, "overrideAllowed": allowed}
  view = session.snapshot(runtime_app.ShellMode.SETTINGS)
  assert view.settings.force_drive_label == label
  action = view.settings.destination(runtime_app.Destination.FORCE_DRIVE)
  assert (action.request_value, action.request_revision) == (target, revision)
  # The shipped bitmap font is the actual Small card's glyph source.
  font = Path(runtime_app.__file__).parent / "assets/fonts/Inter-Bold.fnt"
  glyphs = {int(value) for value in re.findall(r"char id=(\d+)", font.read_text())}
  assert all(ord(char) in glyphs for char in label)
  assert ord("→") not in glyphs


@pytest.mark.parametrize('allowed,target', [(True, 'offroad'), (False, 'offroad')])
def test_actual_compact_force_card_hitbox_dispatches_captured_owner_request(lifecycle, allowed, target):
  from dataclasses import replace
  from openpilot.starpilot.ui.settings_state import SettingsInput
  from openpilot.starpilot.ui.shell import ShellRequest
  _, session, _, _ = lifecycle
  session._mode = runtime_app.ShellMode.SETTINGS
  session._drive_change = Mock()
  revision = 'current-owner-revision'
  session.drive_state.snapshot = lambda: {'mode': 'auto', 'revision': revision, 'available': True,
                                        'effective': 'onroad', 'overrideAllowed': allowed, 'reason': None if allowed else 'Current Panda status is stale'}
  snapshot = session.snapshot(runtime_app.ShellMode.SETTINGS)
  assert snapshot.settings.destination(runtime_app.Destination.FORCE_DRIVE).reason is None
  index = next(i for i, item in enumerate(compact_menu(snapshot.settings)) if item[0] == runtime_app.Destination.FORCE_DRIVE)
  state = replace(snapshot.settings, compact_scroll_x=-422 * index, compact_y=0)
  inputs = SettingsInput(session.profile, lambda action: session._emit(ShellRequest('settings', action)))
  inputs.press(221, 120, state)
  session._drive_change.assert_not_called()
  inputs.release(221, 120, state)
  session._drive_change.assert_called_once_with(target, revision)
  session._drive_change.reset_mock()
  inputs.press(221, 120, state)
  inputs.move(230, 120, state)
  inputs.release(230, 120, state)
  session._drive_change.assert_not_called()
