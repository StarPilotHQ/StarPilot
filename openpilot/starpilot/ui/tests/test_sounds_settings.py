"""Stock alert preferences through actual temporary Params and native requests."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openpilot.common.params import Params
from openpilot.selfdrive.ui.soundd import ALERT_VOLUME_KEYS, CRITICAL_MAX, AudibleAlert, Soundd, sound_list
from openpilot.starpilot.audio.alert_volume import AUTO, SPECS, VOLUMES, effective_volume, read_volume
from openpilot.starpilot.ui import sounds_compact
from openpilot.starpilot.ui.feature_settings_state import (
  feature_row_top, FeatureInput, FeatureSettingsRequest, FeatureUiAction, row_change, value_buttons, value_done_rect,
)
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.runtime_app import SOUND_PRESETS
from openpilot.starpilot.ui.settings_state import Destination, SettingsInput, SettingsState, tile_rects
from openpilot.starpilot.ui.sounds_owner import SoundsOwner
from openpilot.starpilot.ui.shell import ShellInput, ShellMode, ShellSnapshot


class SoundsSettingsTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.parked = True
    self.owner = SoundsOwner(self.params, lambda: self.parked)

  def row(self, key):
    return next(row for row in self.owner.snapshot().rows if row.key == key)

  def large_session(self):
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    session = StarShellSession.__new__(StarShellSession)
    session.profile = Profile.LARGE
    session._mode = ShellMode.SETTINGS
    session.selected = Destination.SOUNDS
    session.sounds_owner = self.owner
    session.sounds_scroll = 0
    session.sounds_edit_key = None
    session._snapshot_cache = None
    session.input = ShellInput(Profile.LARGE, lambda _: None)
    return session

  def test_absence_auto_no_writes_and_native_registry(self):
    state = self.owner.snapshot()
    self.assertEqual(len(state.rows), 12)
    for key, _, _ in VOLUMES:
      self.assertEqual(self.row(key).value, "Auto")
      self.assertEqual((self.row(key).step, self.row(key).maximum, self.row(key).unit), (5.0, 100.0, "%"))
      self.assertIsNone(read_volume(self.params, key).raw)
      self.assertEqual(self.params.get_type(key).name, "INT")

  def test_saved_choices_corruption_and_exact_source(self):
    engage = self.row("EngageVolume")
    engage_request = row_change(engage, 1)
    assert engage_request is not None
    self.assertTrue(self.owner.apply(engage_request))
    self.assertEqual(self.params.get("EngageVolume"), 0)
    self.assertFalse(self.owner.apply(engage_request))
    path = Path(self.params.get_param_path("WarningSoftVolume"))
    path.write_bytes(b"\xff")
    corrupt = self.row("WarningSoftVolume")
    self.assertEqual(corrupt.value, "Invalid saved level")
    self.assertEqual(path.read_bytes(), b"\xff")
    repair = row_change(corrupt)
    assert repair is not None
    self.assertTrue(self.owner.apply(repair))
    self.assertEqual(read_volume(self.params, "WarningSoftVolume").value, AUTO)
    self.assertFalse(self.owner.apply(repair))

  def test_historical_zero_levels_for_refuse_and_disengage(self):
    for key in ("RefuseVolume", "DisengageVolume"):
      request = row_change(self.row(key), 1)
      assert request is not None
      self.assertTrue(self.owner.apply(request))
      self.assertEqual(read_volume(self.params, key).value, 0)

  def test_fixed_volume_slider_preserves_warning_floor_and_auto(self):
    self.params.put("WarningImmediateVolume", 40, block=True)
    row = self.row("WarningImmediateVolume")
    self.assertEqual((row.value, row.minimum, row.maximum, row.step, row.unit, row.choices), ("40", 25.0, 100.0, 5.0, "%", ("Auto",)))
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(row.key, row.source, "20")))
    request = row_change(row)
    assert request is not None
    self.assertTrue(self.owner.apply(request))
    self.assertEqual(read_volume(self.params, row.key).value, 45)
    auto = row_change(self.row("sounds:auto:WarningImmediateVolume"))
    assert auto is not None
    self.assertTrue(self.owner.apply(auto))
    self.assertEqual(read_volume(self.params, row.key).value, AUTO)

  def test_parked_and_final_read_guards(self):
    request = row_change(self.row("RefuseVolume"), 1)
    assert request is not None
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.assertIsNone(read_volume(self.params, "RefuseVolume").raw)
    self.parked = True
    original = self.params.get_param_path
    calls = 0
    def path(key):
      nonlocal calls
      calls += 1
      if calls == 2:
        raise OSError("lost source")
      return original(key)
    with patch.object(self.params, "get_param_path", side_effect=path):
      self.assertFalse(self.owner.apply(request))
    self.assertIsNone(read_volume(self.params, "RefuseVolume").raw)

  def test_oversized_and_unreadable_source_does_not_write(self):
    path = Path(self.params.get_param_path("WarningImmediateVolume"))
    original = b"9" * 100_000
    path.write_bytes(original)
    self.assertEqual(read_volume(self.params, "WarningImmediateVolume").value, None)
    oversized = self.row("WarningImmediateVolume")
    self.assertFalse(oversized.available)
    self.assertIsNone(row_change(oversized))
    self.assertEqual(path.read_bytes(), original)
    # Equal bounded prefixes do not authorize edits when the tail changed.
    path.write_bytes(original[:-1] + b"8")
    self.assertFalse(self.owner.apply(FeatureSettingsRequest("WarningImmediateVolume", oversized.source, "Auto")))
    self.assertFalse(self.row("WarningImmediateVolume").available)
    self.assertEqual(path.read_bytes(), original[:-1] + b"8")
    path.unlink()
    path.mkdir()
    unreadable = self.row("WarningImmediateVolume")
    self.assertFalse(unreadable.available)
    self.assertTrue(path.is_dir())

  def test_soundd_poll_reads_saved_values_without_touching_samples(self):
    sound = Soundd.__new__(Soundd)
    sound.pack_loader = Mock(is_builtin=Mock(return_value=False))
    sound.volume_params = self.params
    sound.saved_volumes = {key: AUTO for key, _, _ in VOLUMES}
    sound.volume_read_at = 0.0
    self.params.put("DisengageVolume", 45, block=True)
    sound.refresh_saved_volumes(1.0)
    self.assertEqual(sound.saved_volumes["DisengageVolume"], 45)
    self.params.put("DisengageVolume", 65, block=True)
    sound.refresh_saved_volumes(1.1)
    self.assertEqual(sound.saved_volumes["DisengageVolume"], 45)
    sound.refresh_saved_volumes(1.3)
    self.assertEqual(sound.saved_volumes["DisengageVolume"], 65)
    Path(self.params.get_param_path("WarningImmediateVolume")).write_bytes(b"0")
    sound.refresh_saved_volumes(1.6)
    self.assertIsNone(sound.saved_volumes["WarningImmediateVolume"])
    self.assertEqual(effective_volume("WarningImmediateVolume", sound.saved_volumes["WarningImmediateVolume"], 0.14,
                                      immediate_ramp=0.14), 0.14)

  def test_large_tile_swipe_and_sound_row_request(self):
    actions = []
    settings_input = SettingsInput(Profile.LARGE, actions.append)
    x, y, width, height = tile_rects(SettingsState())[0]
    settings_input.press(x + width / 2, y + height / 2, SettingsState())
    settings_input.release(x + width / 2, y + height / 2, SettingsState())
    self.assertEqual(actions[0].destination.destination, Destination.SOUNDS)
    received = []
    native = FeatureInput(received.append)
    summary = self.large_session().sounds_snapshot()
    native.press(900, 600, summary)
    native.move(720, 600, summary)
    native.release(720, 600, summary)
    self.assertEqual(received, [FeatureUiAction("scroll")])
    received.clear()
    session = self.large_session()
    session.sounds_edit_key = "WarningImmediateVolume"
    state = session.sounds_snapshot()
    _, (x, y, width, height), _ = next(button for button in value_buttons(state, state.rows[0], feature_row_top(state)) if button[0] == "25")
    x, y = x + width / 2, y + height / 2
    native.press(x, y, state)
    native.move(x - 180, y, state)
    native.release(x - 180, y, state)
    self.assertEqual(received, [])
    received.clear()
    native.press(x, y, state)
    native.release(x, y, state)
    self.assertEqual(received[0].kind, "action")
    self.assertEqual(received[0].row.key, "WarningImmediateVolume")
    self.assertEqual(value_buttons(state, received[0].row)[received[0].direction][0], "25")

  def test_large_session_routes_saved_edit_and_back(self):
    session = self.large_session()
    session._sounds_ui(FeatureUiAction("open", next(row for row in session.sounds_snapshot().rows if row.key == "EngageVolume")))
    row = session.sounds_snapshot().rows[0]
    session._sounds_ui(FeatureUiAction("change", row, 1))
    self.assertIsNone(read_volume(self.params, "EngageVolume").raw)
    session._sounds_ui(FeatureUiAction("action", row, 1))
    self.assertEqual(read_volume(self.params, "EngageVolume").value, 0)
    self.parked = False
    session._sounds_ui(FeatureUiAction("change", self.row("EngageVolume"), 1))
    self.assertEqual(read_volume(self.params, "EngageVolume").value, 0)
    session._sounds_ui(FeatureUiAction("back"))
    self.assertEqual(session.selected, Destination.SOUNDS)
    self.assertIsNone(session.sounds_edit_key)
    session._sounds_ui(FeatureUiAction("back"))
    self.assertEqual(session.selected, Destination.STAR)

  def test_large_hybrid_presets_and_steps_save_on_release_with_source_guards(self):
    session = self.large_session()
    for expanded in (True, False):
      for key, (_, minimum) in SPECS.items():
        session.sounds_edit_key = key
        for value in ("-", *SOUND_PRESETS, "+"):
          if value not in ("-", "+", "Auto") and int(value) < minimum:
            continue
          self.params.put(key, 42, block=True)
          state = session.sounds_snapshot()
          self.assertEqual(len(state.rows), 1)
          self.assertEqual(state.title, SPECS[key][0])
          state = replace(state, sidebar_expanded=expanded, scroll=0)
          row = state.rows[0]
          self.assertEqual(row.key, key)
          buttons = value_buttons(state, row, feature_row_top(state))
          _, (x, y, width, height), enabled = next(button for button in buttons if button[0] == value)
          self.assertTrue(enabled)
          self.assertLessEqual(x + width, 2094.01)
          self.assertEqual(height, 200)
          x, y = x + width / 2, y + height / 2
          received = []
          native = FeatureInput(received.append)
          native.press(x, y, state)
          self.assertEqual(received, [])
          self.assertEqual(self.params.get(key), 42)
          native.release(x, y, state)
          self.assertEqual(len(received), 1)
          self.assertEqual(received[0].row.source, b"42")
          session._sounds_ui(received[0])
          expected = 37 if value == "-" else 47 if value == "+" else AUTO if value == "Auto" else int(value)
          self.assertEqual(self.params.get(key), expected)
          if value not in ("-", "+"):
            fresh = replace(session.sounds_snapshot(), sidebar_expanded=expanded, scroll=state.scroll)
            self.assertIsNone(FeatureInput.target(x, y, fresh))
        for value in (minimum, 100, AUTO):
          self.params.put(key, value, block=True)
          buttons = value_buttons(session.sounds_snapshot(), session.sounds_snapshot().rows[0])
          self.assertEqual(buttons[0][2], value != minimum and value != AUTO)
          self.assertEqual(buttons[-1][2], value != 100 and value != AUTO)
    row = self.row("EngageVolume")
    session.sounds_edit_key = row.key
    self.params.put(row.key, 60, block=True)
    session._sounds_ui(FeatureUiAction("action", row, 1))
    self.assertEqual(self.params.get(row.key), 60)
    session._mode = ShellMode.ONROAD
    session._sounds_ui(FeatureUiAction("action", self.row(row.key), 1))
    self.assertEqual(self.params.get(row.key), 60)
    session.profile = Profile.COMPACT
    self.assertEqual(session.sounds_snapshot(), self.owner.snapshot())
    self.assertGreater(len(self.owner.snapshot().rows), 8)
    state = session.sounds_snapshot()
    repair = replace(self.row(row.key), step=0, repair_value="Auto", value="Invalid saved level")
    self.assertEqual(value_buttons(state, repair), ())
    self.assertEqual(value_buttons(state, next(row for row in state.rows if row.key == "SoundPack")), ())

  def test_summary_opens_one_editor_and_returns_to_same_page_without_writes(self):
    session = self.large_session()
    self.params.put("EngageVolume", 40, block=True)
    session.sounds_scroll = 5
    state = replace(session.sounds_snapshot(), scroll=5)
    self.assertEqual(state.page, "sounds:overview")
    self.assertEqual(len(state.rows), 12)
    self.assertTrue(all(value_buttons(state, row) == () for row in state.rows))
    self.assertEqual(next(row for row in state.rows if row.key == "EngageVolume").value, "40")
    received = []
    native = FeatureInput(received.append)
    x, y = 800, feature_row_top(state) + 154 + 77
    native.press(x, y, state)
    self.assertEqual(received, [])
    native.release(x, y, state)
    self.assertEqual(received[0].kind, "open")
    session._sounds_ui(received[0])
    editor = session.sounds_snapshot()
    self.assertEqual(session.sounds_edit_key, "PromptVolume")
    self.assertEqual([row.key for row in editor.rows], ["PromptVolume"])
    self.assertEqual(session.sounds_scroll, 5)
    self.assertIsNone(read_volume(self.params, "PromptVolume").raw)
    received.clear()
    x, y, width, height = value_done_rect(editor)
    native.press(x + width / 2, y + height / 2, editor)
    native.release(x + width / 2, y + height / 2, editor)
    session._sounds_ui(received[0])
    self.assertIsNone(session.sounds_edit_key)
    self.assertEqual(session.sounds_scroll, 5)
    self.assertEqual(session.sounds_snapshot(), replace(state, scroll=0))
    summary_row = next(row for row in state.rows if row.key == "EngageVolume")
    session._sounds_ui(FeatureUiAction("change", summary_row, 1))
    self.assertEqual(self.params.get("EngageVolume"), 40)
    self.parked = False
    blocked = next(row for row in session.sounds_snapshot().rows if row.key == "EngageVolume")
    session._sounds_ui(FeatureUiAction("open", blocked))
    self.assertIsNone(session.sounds_edit_key)
    self.parked = True
    Path(self.params.get_param_path("WarningSoftVolume")).write_bytes(b"bad")
    broken = next(row for row in session.sounds_snapshot().rows if row.key == "WarningSoftVolume")
    session._sounds_ui(FeatureUiAction("open", broken))
    editor = session.sounds_snapshot()
    repair = FeatureInput.target(1900, feature_row_top(editor) + 77, editor)
    self.assertEqual(repair.kind, "change")
    session._sounds_ui(repair)
    self.assertEqual(self.params.get("WarningSoftVolume"), AUTO)

  def test_editor_touches_dispatch_with_retained_overview_page(self):
    from openpilot.starpilot.ui import runtime_app
    for expanded in (True, False):
      for scroll in (0, 5):
        for key in ("DisengageVolume", "PromptVolume", "SoundPack"):
          for control in ("level", "done", *(("sidebar",) if expanded else ())):
            with self.subTest(expanded=expanded, scroll=scroll, key=key, control=control):
              self.params.put(key, "starpilot" if key == "SoundPack" else 42, block=True)
              session = self.large_session()
              session.sounds_scroll = scroll
              session.sidebar_expanded = expanded
              session.confirmed_offroad = Mock(return_value=True)
              session.favorites = Mock()
              session.view = Mock()
              session.view.sounds.reset.return_value = False
              session._on_destination_change = None
              row = next(row for row in session.sounds_snapshot().rows if row.key == key)
              session._sounds_ui(FeatureUiAction("open", row))
              editor = replace(session.sounds_snapshot(), scroll=0, sidebar_expanded=expanded)
              snapshot = ShellSnapshot(ShellMode.SETTINGS, Mock(), SettingsState(sidebar_expanded=expanded), Mock(),
                                       selected=Destination.SOUNDS, sounds=editor)
              session.snapshot = Mock(return_value=snapshot)
              session.input = ShellInput(Profile.LARGE, session._emit)
              session.sounds_request = Mock(wraps=session.sounds_request)
              if control == "done":
                x, y, width, height = value_done_rect(editor)
                x, y = x + width / 2, y + height / 2
              elif control == "sidebar":
                x, y = 240, 345
              elif key == "SoundPack":
                _, (x, y, width, height), _ = next(button for button in value_buttons(editor, editor.rows[0], feature_row_top(editor))
                                                 if button[0] == "Stock Sounds")
                x, y = x + width / 2, y + height / 2
              else:
                _, (x, y, width, height), _ = next(button for button in value_buttons(editor, editor.rows[0], feature_row_top(editor))
                                                 if button[0] == "25")
                x, y = x + width / 2, y + height / 2
              with patch.object(runtime_app, "ui_state", SimpleNamespace(started=False, started_frame=0)):
                session.press(ShellMode.SETTINGS, x, y)
                session.move(ShellMode.SETTINGS, x, y)
                self.assertTrue(session.release(ShellMode.SETTINGS, x, y))
              self.assertEqual(session.sounds_scroll, scroll)
              if control == "level":
                session.sounds_request.assert_called_once()
                if key != "SoundPack":
                  self.assertEqual(self.params.get(key), 25)
                else:
                  self.assertEqual(self.params.get(key), "stock")
              else:
                session.sounds_request.assert_not_called()
                if control == "done":
                  self.assertIsNone(session.sounds_edit_key)
                  self.assertEqual(session.selected, Destination.SOUNDS)
                else:
                  self.assertEqual(session.selected, Destination.STAR)

  def test_done_returns_from_volume_pack_and_repair_without_writing(self):
    session = self.large_session()
    session.sounds_scroll = 5
    Path(self.params.get_param_path("WarningSoftVolume")).write_bytes(b"bad")
    for expanded in (True, False):
      for key in ("PromptVolume", "SoundPack", "WarningSoftVolume"):
        self.parked = True
        row = next(row for row in session.sounds_snapshot().rows if row.key == key)
        session._sounds_ui(FeatureUiAction("open", row))
        self.parked = False
        state = replace(session.sounds_snapshot(), sidebar_expanded=expanded)
        before = tuple(row.source for row in self.owner.snapshot().rows)
        x, y, width, height = value_done_rect(state)
        x, y = x + width / 2, y + height / 2
        received = []
        native = FeatureInput(received.append)
        for changed in (state, replace(state, sidebar_expanded=not expanded)):
          native.press(x, y, state)
          if changed == state:
            native.move(x - 50, y, state)
          native.release(x, y, changed)
          self.assertEqual(received, [])
        native.press(x, y, state)
        native.release(x, y, state)
        self.assertEqual(received, [FeatureUiAction("back")])
        session._sounds_ui(received[0])
        self.assertIsNone(session.sounds_edit_key)
        self.assertEqual(session.sounds_scroll, 5)
        self.assertEqual(session.selected, Destination.SOUNDS)
        self.assertEqual(tuple(row.source for row in self.owner.snapshot().rows), before)

  def test_volume_commit_rejects_competing_native_write_before_lock(self):
    from openpilot.starpilot import saved_document
    self.params.put("EngageVolume", 40, block=True)
    row = self.row("EngageVolume")
    acquire = saved_document.acquire_native_lock
    def competing_write(fd):
      self.params.put(row.key, 60, block=True)
      acquire(fd)
    with patch.object(saved_document, "acquire_native_lock", side_effect=competing_write):
      self.assertFalse(self.owner.apply(FeatureSettingsRequest(row.key, row.source, "50")))
    self.assertEqual(self.params.get(row.key), 60)
    root = Path(self.params.get_param_path(row.key)).parent.parent
    self.assertEqual(list(root.glob(".sound-volume-*")), [])

  def test_compact_child_refreshes_after_saved_edit(self):
    class Button:
      def __init__(self, label, value):
        self.label, self.value, self.click = label, value, None
      def set_click_callback(self, callback):
        self.click = callback
    class Scroller:
      def __init__(self):
        self._scroller = self
        self.items = []
      def add_widgets(self, cards):
        self.items.extend(cards)
    class Session:
      def sounds_snapshot(inner):
        return self.owner.snapshot()
      def sounds_request(inner, request):
        return self.owner.apply(request)
    shown = []
    with patch.object(sounds_compact, "BigButton", Button), patch.object(sounds_compact, "GreyBigButton", Button), \
         patch.object(sounds_compact, "NavScroller", Scroller), patch.object(sounds_compact.gui_app, "push_widget", shown.append):
      compact = sounds_compact.SoundsCompact(Session())
      compact.entry_button().click()
      page = shown[0]
      self.assertEqual(page.items[0].label, "sounds & alerts")
      plus = next(item for item in page.items if item.label == "engagement chime +")
      plus.click()
      self.assertEqual(read_volume(self.params, "EngageVolume").value, 0)
      self.assertIn("0%", next(item for item in page.items if item.label == "engagement chime +").value)
      for key, label, _ in VOLUMES:
        with self.subTest(key=key):
          self.params.put(key, 50, block=True)
          compact._populate(page)
          next(item for item in page.items if item.label == label.lower() + " +").click()
          self.assertEqual(read_volume(self.params, key).value, 55)
          next(item for item in page.items if item.label == label.lower() + " −").click()
          self.assertEqual(read_volume(self.params, key).value, 50)
          stale = next(item for item in page.items if item.label == label.lower() + " +")
          self.params.put(key, 60, block=True)
          stale.click()
          self.assertEqual(read_volume(self.params, key).value, 60)
          compact._populate(page)
          next(item for item in page.items if item.label == "use auto " + label.lower() + " set auto").click()
          self.assertEqual(read_volume(self.params, key).value, AUTO)
          self.assertEqual(next(item for item in page.items if item.label == label.lower() + " +").value, "auto")
          self.parked = False
          next(item for item in page.items if item.label == label.lower() + " +").click()
          self.assertEqual(read_volume(self.params, key).value, AUTO)
          self.parked = True

  def test_soundd_auto_samples_and_protected_warning_ramp(self):
    sound = Soundd.__new__(Soundd)
    sound.pack_loader = Mock(is_builtin=Mock(return_value=False))
    sound.current_alert = AudibleAlert.engage
    sound.current_sound = AudibleAlert.engage
    sound.current_sound_frame = 0
    sound.pending_stop = False
    sound.current_volume = 0.17
    sound.loaded_sounds = {AudibleAlert.engage: np.array([0.5, -0.5], dtype=np.float32),
                           AudibleAlert.warningImmediate: np.array([0.5, -0.5], dtype=np.float32)}
    sound.saved_volumes = {"EngageVolume": AUTO, "WarningImmediateVolume": AUTO}
    np.testing.assert_array_equal(sound.get_sound_data(2), np.array([0.085, -0.085], dtype=np.float32))
    sound.current_sound_frame = 0
    sound.saved_volumes["EngageVolume"] = 0
    np.testing.assert_array_equal(sound.get_sound_data(2), np.zeros(2, dtype=np.float32))
    sound.current_alert = AudibleAlert.warningImmediate
    sound.current_sound = AudibleAlert.warningImmediate
    sound.current_sound_frame = 0
    sound.saved_volumes["WarningImmediateVolume"] = 25
    np.testing.assert_array_equal(sound.get_sound_data(2), np.array([0.125, -0.125], dtype=np.float32))
    sound.current_sound_frame = 0
    sound.current_volume = 1.0
    np.testing.assert_array_equal(sound.get_sound_data(2), np.array([0.5, -0.5], dtype=np.float32))
    self.assertEqual(effective_volume("WarningImmediateVolume", None, 0.17, immediate_ramp=0.17), 0.17)

  def test_auto_matches_prior_gain_for_every_stock_family_and_timeout(self):
    sound = Soundd.__new__(Soundd)
    sound.pack_loader = Mock(is_builtin=Mock(return_value=False))
    waveform = np.array([0.5, -0.5], dtype=np.float32)
    sound.loaded_sounds = dict.fromkeys(sound_list, waveform)
    sound.saved_volumes = {key: AUTO for key, _, _ in VOLUMES}
    sound.current_volume = 0.17
    sound.pending_stop = False
    for alert in sound_list:
      if alert == CRITICAL_MAX:
        self.assertNotIn(alert, ALERT_VOLUME_KEYS)
      else:
        self.assertIn(alert, ALERT_VOLUME_KEYS)
      sound.current_alert = alert
      sound.current_sound = alert
      sound.current_sound_frame = 0
      np.testing.assert_array_equal(sound.get_sound_data(2), waveform * 0.17)
    # The existing timeout path produces the same immediate-warning alert.
    sound.current_alert = AudibleAlert.none
    sound.current_sound = AudibleAlert.none
    sound.current_sound_frame = 0
    sound.selfdrive_timeout_alert = False
    sm = type("State", (), {"updated": {"selfdriveState": False}})()
    with patch("openpilot.selfdrive.ui.soundd.check_selfdrive_timeout_alert", return_value=True):
      sound.get_audible_alert(sm)
    self.assertEqual(sound.current_alert, AudibleAlert.warningImmediate)
    self.assertTrue(sound.selfdrive_timeout_alert)
    np.testing.assert_array_equal(sound.get_sound_data(2), waveform * 0.17)
    sound.current_sound_frame = 0
    sound.current_volume = 0.83
    np.testing.assert_array_equal(sound.get_sound_data(2), waveform * 0.83)

  def test_looping_alert_final_chunk_keeps_producing_alert_gain(self):
    sound = Soundd.__new__(Soundd)
    sound.pack_loader = Mock(is_builtin=Mock(return_value=False))
    sound.current_alert = AudibleAlert.promptRepeat
    sound.current_sound = AudibleAlert.promptRepeat
    sound.current_sound_frame = 2
    sound.pending_stop = True
    sound.current_volume = 0.17
    sound.loaded_sounds = {AudibleAlert.promptRepeat: np.array([0.5, -0.5], dtype=np.float32)}
    sound.saved_volumes = {"PromptVolume": 0}
    np.testing.assert_array_equal(sound.get_sound_data(2), np.zeros(2, dtype=np.float32))
    self.assertEqual(sound.current_alert, AudibleAlert.none)
    sound.current_alert = AudibleAlert.promptRepeat
    sound.current_sound = AudibleAlert.promptRepeat
    sound.current_sound_frame = 2
    sound.pending_stop = True
    sound.saved_volumes["PromptVolume"] = AUTO
    np.testing.assert_array_equal(sound.get_sound_data(2), np.array([0.085, -0.085], dtype=np.float32))
