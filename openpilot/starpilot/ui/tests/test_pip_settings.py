"""Side-camera saved controls through real disposable Params."""

import fcntl
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock

from openpilot.common.params import Params
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, row_change
from openpilot.starpilot.ui.feature_settings_state import FeatureUiAction
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.pip_owner import EDITOR, FORMAT_PREFIX, PiPOwner, RESET
from openpilot.starpilot.ui.pip_preferences import BLINKER, ENABLED, MASK, decode_mask, encode_mask, read_pip, starting_mask


class PiPSettingsTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.parked = True
    self.owner = PiPOwner(self.params, lambda: self.parked, lambda: False)

  def row(self, key):
    return next(row for row in self.owner.snapshot().rows if row.key == key)

  def test_absence_is_default_off_and_reader_does_not_write(self):
    saved = read_pip(self.params)
    self.assertFalse(saved.enabled)
    self.assertIsNotNone(saved.mask)
    self.assertIsNone(saved.source(MASK)[0])
    self.assertEqual(self.row(ENABLED).value, "Off")
    self.assertEqual(self.params.get_type(MASK).name, "JSON")
    self.assertEqual(self.row("pip:mask:right:x").value, "315.0")

  def test_turning_pip_on_adds_side_cameras_to_saved_layout(self):
    import json
    from openpilot.starpilot.ui.onroad_customization import PARAM_KEY, default_document, read_customization
    document = default_document()
    document["layouts"]["large"]["pip_left"]["enabled"] = False
    document["layouts"]["compact"]["pip_left"]["enabled"] = False
    layout = Path(self.params.get_param_path(PARAM_KEY))
    layout.write_bytes(json.dumps(document).encode())
    from openpilot.starpilot.system.starport.projection_layout import (ProjectionLayoutSource, decode_layout_for_viewport,
                                                                       default_layout_for_viewport)
    viewport = (1920, 1080)
    projection = default_layout_for_viewport(viewport)
    projection["widgets"]["pip_right"]["enabled"] = False
    self.owner.projection_source = ProjectionLayoutSource(Path(self.params.get_param_path(PARAM_KEY)).parent / "projection.json")
    projection_path = Path(self.owner.projection_source.path)
    projection_path.write_bytes(json.dumps(projection).encode())
    action = row_change(self.row(ENABLED))
    assert action is not None and action.value == "On"
    self.assertTrue(self.owner.apply(action))
    saved = read_customization(self.params)
    self.assertTrue(all(saved["layouts"][profile][key]["enabled"]
                        for profile in ("large", "compact") for key in (("pip_left", "pip_right") if profile == "large" else ("pip_left",))))
    projected = decode_layout_for_viewport(projection_path.read_bytes(), viewport)
    self.assertTrue(projected["widgets"]["pip_left"]["enabled"] and projected["widgets"]["pip_right"]["enabled"])
    # A camera removed while PiP is on stays removed; turning PiP off does not touch the layout.
    document = json.loads(layout.read_bytes())
    document["layouts"]["large"]["pip_left"]["enabled"] = False
    layout.write_bytes(json.dumps(document).encode())
    off = row_change(self.row(ENABLED))
    assert off is not None and off.value == "Off"
    self.assertTrue(self.owner.apply(off))
    self.assertFalse(read_customization(self.params)["layouts"]["large"]["pip_left"]["enabled"])

  def test_explicit_edit_preserves_other_bytes_and_rejects_stale_source(self):
    action = row_change(self.row(BLINKER))
    assert action is not None
    self.assertTrue(self.owner.apply(action))
    self.assertEqual(Path(self.params.get_param_path(BLINKER)).read_bytes(), b"1")
    self.assertFalse(self.owner.apply(action))
    mask_request = row_change(self.row("pip:mask:right:x"))
    assert mask_request is not None
    self.assertTrue(self.owner.apply(mask_request))
    saved = read_pip(self.params)
    assert saved.mask is not None and saved.mask.center_left is not None
    self.assertEqual(saved.mask.center_left[0], 325)
    self.assertEqual(saved.source(BLINKER)[0], b"1")
    self.assertFalse(self.owner.apply(mask_request))
    self.assertIsNone(saved.source(ENABLED)[0])

  def test_invalid_document_requires_confirmed_reset_and_preserves_source(self):
    path = Path(self.params.get_param_path(MASK))
    invalid = b'{"width":1928,"width":1928}'
    path.write_bytes(invalid)
    self.assertIsNone(read_pip(self.params).mask)
    self.assertFalse(self.row(ENABLED).available)
    reset = self.row(RESET)
    self.assertTrue(reset.available)
    request = FeatureSettingsRequest(RESET, reset.source, "confirm", confirmation=True,
                                     dependencies=reset.dependencies)
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(RESET, reset.source, "confirm",
                                                             dependencies=reset.dependencies)))
    self.assertEqual(path.read_bytes(), invalid)
    self.assertTrue(self.owner.apply(request))
    self.assertIsNotNone(decode_mask(path.read_bytes()))
    self.assertFalse(self.owner.apply(request))

  def test_camera_format_is_automatic_without_rewriting_saved_crop(self):
    original = encode_mask(starting_mask(1344, 760))
    path = Path(self.params.get_param_path(MASK))
    path.write_bytes(original)
    page = self.owner.snapshot(include_editor=True)
    self.assertEqual(page.title, 'Adj Spot Camera & Preview')
    self.assertFalse(any(row.key.startswith(FORMAT_PREFIX) for row in page.rows))
    self.assertEqual(path.read_bytes(), original)
    editor, source = self.owner.editor_with_source()
    assert editor is not None
    self.assertEqual(editor['width'], 1344)

  def test_visual_editor_uses_integer_source_pixels_and_one_crop_write(self):
    import json

    editor, dependencies = self.owner.editor_with_source()
    self.assertEqual(editor, {"width": 1928, "height": 1208, "centerLeft": [315, 548],
                              "centerRight": [1571, 539], "cropSize": 580, "invert": False})
    row = next(row for row in self.owner.snapshot(include_editor=True).rows if row.key == EDITOR)
    self.assertEqual(dependencies, row.dependencies)
    self.assertTrue(row.available)
    draft = {"width": 1928, "height": 1208, "center_left": [320, 550],
             "center_right": None, "crop_size": 580}
    request = FeatureSettingsRequest(EDITOR, row.source, json.dumps(draft), confirmation=True,
                                     dependencies=row.dependencies)
    self.assertTrue(self.owner.apply(request))
    saved = read_pip(self.params)
    self.assertEqual(saved.mask.center_left, (320.0, 550.0))
    self.assertIsNone(saved.mask.center_right)
    self.assertFalse(self.owner.apply(request))
    self.assertIsNone(saved.source(ENABLED)[0])

  def test_visual_editor_refuses_format_change_fractional_source_and_invalid_invert(self):
    import json

    row = next(row for row in self.owner.snapshot(include_editor=True).rows if row.key == EDITOR)
    draft = {"width": 1344, "height": 760, "center_left": [320, 550],
             "center_right": None, "crop_size": 580}
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(EDITOR, row.source, json.dumps(draft), confirmation=True,
                                                             dependencies=row.dependencies)))
    draft["width"], draft["height"] = 1928, 1208
    draft["crop_size"] = 580.5
    self.assertFalse(self.owner.apply(FeatureSettingsRequest(EDITOR, row.source, json.dumps(draft), confirmation=True,
                                                             dependencies=row.dependencies)))
    path = Path(self.params.get_param_path(MASK))
    path.write_bytes(b'{"width":1928,"height":1208,"crop_size":580.5,"center_left":[315,548],"center_right":[1571,539]}')
    self.assertIsNone(self.owner.editor_with_source()[0])
    self.assertFalse(next(row for row in self.owner.snapshot(include_editor=True).rows if row.key == EDITOR).available)
    self.assertIsNotNone(read_pip(self.params).mask)
    path.write_bytes(encode_mask(starting_mask(1928, 1208)))
    Path(self.params.get_param_path("PIPPreviewInvert")).write_bytes(b"invalid")
    self.assertIsNone(self.owner.editor_with_source()[0])
    self.assertFalse(next(row for row in self.owner.snapshot(include_editor=True).rows if row.key == EDITOR).available)
    Path(self.params.get_param_path("PIPPreviewInvert")).write_bytes(b"0")
    path.write_bytes(b'{"width":1928,"height":1208,"crop_size":19,"center_left":[315,548],"center_right":[1571,539]}')
    self.assertIsNotNone(read_pip(self.params).mask)
    self.assertIsNone(self.owner.editor_with_source()[0])
    self.assertFalse(next(row for row in self.owner.snapshot(include_editor=True).rows if row.key == EDITOR).available)

  def test_corrupt_crop_can_reset_without_silent_repair(self):
    path = Path(self.params.get_param_path(MASK))
    path.write_bytes(b"{invalid")
    row = self.row(RESET)
    self.assertTrue(row.available)
    self.assertEqual(path.read_bytes(), b"{invalid")
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(row.key, row.source, "confirm", confirmation=True,
                                                            dependencies=row.dependencies)))
    self.assertEqual(path.read_bytes(), encode_mask(starting_mask(1928, 1208)))

  def test_parked_rechecks_and_source_swap_before_lock(self):
    request = row_change(self.row(ENABLED))
    assert request is not None
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.assertIsNone(read_pip(self.params).source(ENABLED)[0])
    self.parked = True
    calls = 0
    def parked():
      nonlocal calls
      calls += 1
      return calls == 1
    owner = PiPOwner(self.params, parked, lambda: False)
    self.assertFalse(owner.apply(request))
    self.assertIsNone(read_pip(self.params).source(ENABLED)[0])
    with patch("openpilot.starpilot.ui.pip_owner.tempfile.NamedTemporaryFile") as stage:
      stage.side_effect = OSError("cannot stage")
      self.assertFalse(self.owner.apply(request))
    self.assertIsNone(read_pip(self.params).source(ENABLED)[0])

  def test_nonregular_and_oversized_sources_cannot_authorize_repair(self):
    path = Path(self.params.get_param_path(MASK))
    path.write_bytes(b"x" * 5000)
    self.assertFalse(self.row(RESET).available)
    self.assertEqual(path.read_bytes(), b"x" * 5000)
    path.unlink()
    path.symlink_to(Path(self.params.get_param_path(BLINKER)))
    self.assertFalse(self.row(RESET).available)
    path.unlink()
    os.mkfifo(path)
    self.assertFalse(self.row(RESET).available)
    path.unlink()

  def test_params_lock_contention_cannot_write(self):
    request = row_change(self.row(ENABLED))
    assert request is not None
    root = Path(self.params.get_param_path(ENABLED)).parent.parent
    lock = os.open(root / ".lock", os.O_CREAT | os.O_RDONLY, 0o775)
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      self.assertFalse(self.owner.apply(request))
      self.assertIsNone(read_pip(self.params).source(ENABLED)[0])
    finally:
      os.close(lock)

  def test_large_native_parent_child_edit_and_abandoned_reset_dialog(self):
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    from openpilot.system.ui.widgets import DialogResult
    from openpilot.system.ui.widgets import confirm_dialog
    session = StarShellSession.__new__(StarShellSession)
    session._mode = ShellMode.SETTINGS
    session.selected = Destination.APPEARANCE
    session.appearance_page = "appearance"
    session.appearance_owner = AppearanceOwner(self.params, lambda: self.parked)
    session.pip_owner = self.owner
    session.profile = Profile.LARGE
    session.adapter = Mock()
    session._snapshot_cache = None
    session.appearance_scroll = 0
    session._pip_request_epoch = 0
    session.input = Mock()
    session._unavailable = Mock()
    link = FeatureRow("", "Adj Spot Camera", "", page="pip", available=True)
    session._appearance_ui(FeatureUiAction("open", link))
    self.assertEqual(session.appearance_page, "appearance")
    session.appearance_page = "pip"
    row = next(row for row in session.appearance_snapshot().rows if row.key == BLINKER)
    session._appearance_ui(FeatureUiAction("change", row))
    self.assertEqual(read_pip(self.params).on_blinker, True)
    dialogs = []
    class Dialog:
      def __init__(self, question, button, callback):
        dialogs.append((question, callback))
    with patch.object(confirm_dialog, "ConfirmDialog", Dialog), \
         patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget"):
      reset = next(row for row in session.appearance_snapshot().rows if row.key == RESET)
      session._appearance_ui(FeatureUiAction("reset", reset))
      self.assertEqual(len(dialogs), 1)
      self.assertIn("may resume on the next drive", dialogs[0][0])
      session._appearance_ui(FeatureUiAction("back"))
      session.appearance_page = "pip"
      dialogs[0][1](DialogResult.CONFIRM)
    self.assertIsNone(read_pip(self.params).source(MASK)[0])
    self.assertFalse(any(row.key.startswith(FORMAT_PREFIX) for row in session.appearance_snapshot().rows))
    session._appearance_ui(FeatureUiAction("back"))
    self.assertEqual(session.appearance_page, "appearance")
    session._appearance_ui(FeatureUiAction("back"))
    self.assertEqual(session.selected, Destination.STAR)

  def test_compact_visuals_child_edit_and_confirm_reset(self):
    from openpilot.starpilot.ui import appearance_compact
    class Button:
      def __init__(self, label, value):
        self.label, self.value = label, value
        self.callback = None
        self.enabled = True
      def set_click_callback(self, callback):
        self.callback = callback
      def set_enabled(self, enabled):
        self.enabled = enabled
      def click(self):
        assert self.enabled and self.callback is not None
        self.callback()
    class Scroller:
      def __init__(self):
        self._scroller = self
        self.items = []
      def add_widgets(self, cards):
        self.items.extend(cards)
    class Dialog:
      def __init__(self, question, icon, callback, red):
        self.question, self.callback = question, callback
    class Session:
      def __init__(inner):
        inner.feature_owner = FeatureSettingsOwner(self.params, lambda group: self.parked,
                                                   vehicle_fingerprint=lambda: "TEST CAR")
      def appearance_snapshot(inner):
        return AppearanceOwner(self.params, lambda: self.parked).snapshot(Profile.COMPACT)
      def pip_snapshot(inner):
        return self.owner.snapshot()
      def appearance_request(inner, request):
        return self.owner.apply(request)
      def feature_snapshot(inner, page):
        return inner.feature_owner.snapshot(page, parked=self.parked, system_long=self.parked,
                                            lateral_context=False, metric=False)
      def feature_request(inner, request):
        return inner.feature_owner.apply(request)
    shown = []
    with patch.object(appearance_compact, "BigButton", Button), \
         patch.object(appearance_compact, "GreyBigButton", Button), \
         patch.object(appearance_compact, "NavScroller", Scroller), \
         patch.object(appearance_compact, "BigConfirmationDialog", Dialog), \
         patch.object(appearance_compact.gui_app, "push_widget", shown.append), \
         patch.object(appearance_compact.gui_app, "get_active_widget", side_effect=lambda: shown[-1]), \
         patch.object(appearance_compact.gui_app, "texture", return_value=None):
      appearance_compact.AppearanceCompact(Session()).entry_button().click()
      parent = shown[-1]
      self.assertFalse(any(item.label == "Adj Spot Camera" for item in parent.items))
      appearance_compact.AppearanceCompact(Session()).open("pip")
      child = shown[-1]
      next(item for item in child.items if item.label == "show on turn signal").click()
      self.assertEqual(read_pip(self.params).on_blinker, True)
      next(item for item in child.items if item.label == "restore default camera crop").click()
      dialog = shown.pop()
      self.assertEqual(dialog.question, "reset crop?\npreview may\nresume if on")
      dialog.callback()
      self.assertIsNotNone(read_pip(self.params).mask)
      child = shown[-1]
      self.assertFalse(any('camera format' in getattr(item, 'label', '') for item in child.items))
