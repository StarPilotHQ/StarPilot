"""Model identity remains separate from guarded catalog management."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest import mock

from openpilot.starpilot.models.catalog import BUNDLED_CURRENT, BY_ID
from openpilot.starpilot.models.status import ModelHealth, ModelStatus, ModelVariant
from openpilot.starpilot.ui.feature_settings_state import (feature_action_rects, feature_page_rows, feature_row_top, feature_scroll, toolbar_rects, FeatureInput,
                                                           FeatureUiAction)
from openpilot.starpilot.ui.models_compact import ModelsCompact, _ModelsPage, _display_text
from openpilot.starpilot.ui.models_state import (model_page, model_action_allowed, model_catalog_rows, model_list_rows, model_manager_rows,
                                                model_request, model_row_intent, home_model_label)
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.shell import ShellMode, ShellView


class TestModelPage(unittest.TestCase):
  def test_runtime_stall_has_truthful_fallback_wording(self):
    status = ModelStatus(BUNDLED_CURRENT, BUNDLED_CURRENT, ModelVariant.SMALL, ModelHealth.ACTIVE,
                         False, 'chestnut-run-stalled', 'a' * 64)
    rows = model_page(status).rows
    fallback = next(row for row in rows if row.label == 'Fallback')
    self.assertEqual(fallback.reason, 'Chestnut output stopped; Small restarted for this drive')
    self.assertNotIn('load', fallback.reason.lower())

  def test_native_display_normalizes_unsupported_glyphs_only(self):
    label = "Green Watermelon v8 👀📡"
    self.assertEqual(_display_text(label), "Green Watermelon v8")
    self.assertEqual(label, "Green Watermelon v8 👀📡")
    self.assertEqual(_display_text("2 installed · 98 missing"), "2 installed • 98 missing")
    self.assertEqual(_display_text("none — always use small"), "none – always use small")
    self.assertEqual(_display_text("TR17 ⚡️  v8"), "TR17 v8")
    self.assertEqual(_display_text("gwm8223"), "gwm8223")

  def test_home_prefers_verified_loaded_identity_over_pending_selection(self):
    requested = next(model_id for model_id in BY_ID if model_id != BUNDLED_CURRENT)
    status = ModelStatus(requested, BUNDLED_CURRENT, ModelVariant.SMALL, ModelHealth.ACTIVE, True, None, "a" * 64)
    actual = "CD210"
    commit = "abcdef1234567890" * 2 + "abcdef12"
    for health in (ModelHealth.ACTIVE, ModelHealth.STALE, ModelHealth.LOADING):
      self.assertEqual(home_model_label(replace(status, health=health), commit), f"{actual} • abcdef1")
    self.assertEqual(home_model_label(replace(status, loaded_id=None, health=ModelHealth.UNAVAILABLE), commit),
                     f"{_display_text(BY_ID[requested].name)} • abcdef1")
    self.assertEqual(home_model_label(status), actual)
    self.assertEqual(home_model_label(replace(status, variant=ModelVariant.CHESTNUT)), "Cinque v2")
    self.assertEqual(home_model_label(status, "not-a-revision"), actual)

  def test_bundled_identity_health_and_inert_rows(self):
    status = ModelStatus(BUNDLED_CURRENT, BUNDLED_CURRENT, ModelVariant.SMALL, ModelHealth.ACTIVE,
                         False, None, "a" * 64)
    page = model_page(status)
    self.assertEqual(page.title, "Driving Model")
    self.assertEqual(page.rows[1].value, "Active")
    self.assertEqual(page.rows[2].value, "Small")
    self.assertTrue(all(not row.available and not row.page for row in page.rows))
    self.assertIsNone(FeatureInput.target(1980, 300, page))
    self.assertIsNone(FeatureInput.target(600, 60, page))
    self.assertEqual(FeatureInput.target(100, 60, replace(page, sidebar_expanded=False)).kind, "back")

  def test_missing_receipt_does_not_look_active(self):
    status = ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.IDENTITY_UNAVAILABLE,
                         False, None, None)
    page = model_page(status)
    self.assertEqual(page.rows[1].value, "Identity unavailable")
    self.assertEqual(page.rows[3].value, "Unavailable")
    self.assertIn("No verified load receipt", page.rows[3].reason)

  def test_large_shell_renders_model_pane_without_controls(self):
    status = ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.UNAVAILABLE, False, None, None)
    page = model_page(status)
    view = ShellView.__new__(ShellView)
    view.profile = Profile.LARGE
    view.models = mock.Mock()
    view.settings = mock.Mock()
    view.onroad = mock.Mock()
    shell = mock.Mock(mode=ShellMode.SETTINGS, selected=Destination.DRIVING_MODEL, models=page, settings=mock.Mock())
    view.render(shell)
    view.models.render.assert_called_once_with(page, 0.0)
    view.settings.render_rail.assert_called_once_with(shell.settings, selected=Destination.STAR)

  def test_compact_visible_page_refreshes_and_hidden_page_does_not_sample(self):
    active = model_page(ModelStatus(BUNDLED_CURRENT, BUNDLED_CURRENT, ModelVariant.SMALL,
                                    ModelHealth.ACTIVE, False, None, "a" * 64))
    stale = model_page(ModelStatus(BUNDLED_CURRENT, BUNDLED_CURRENT, ModelVariant.SMALL,
                                   ModelHealth.STALE, False, None, "a" * 64))
    absent = model_page(ModelStatus(BUNDLED_CURRENT, None, None,
                                    ModelHealth.UNAVAILABLE, False, None, None))
    session = mock.Mock()
    session.model_snapshot.side_effect = [stale, absent]
    now = [0.0]
    owner = ModelsCompact(session, clock=lambda: now[0])
    scroller = mock.Mock(items=[])
    page = cast(_ModelsPage, SimpleNamespace(current=active, next_refresh=0.5, _scroller=scroller))
    with mock.patch("openpilot.starpilot.ui.models_compact.GreyBigButton", side_effect=lambda *args: args), \
         mock.patch("openpilot.starpilot.ui.models_compact.gui_app.get_active_widget", return_value=page) as visible:
      now[0] = 0.49
      owner.refresh_visible(page)
      session.model_snapshot.assert_not_called()
      now[0] = 0.5
      owner.refresh_visible(page)
      self.assertEqual(page.current, stale)
      self.assertIn("model output stale", scroller.add_widgets.call_args.args[0][2][1])
      visible.return_value = object()
      now[0] = 2.0
      owner.refresh_visible(page)
      self.assertEqual(session.model_snapshot.call_count, 1)
      visible.return_value = page
      owner.refresh_visible(page)
      self.assertEqual(page.current, absent)
      self.assertIn("model not running", scroller.add_widgets.call_args.args[0][2][1])
      self.assertEqual(session.model_snapshot.call_count, 2)


class TestModelManager(unittest.TestCase):
  def setUp(self):
    self.small = {"value": "small", "label": "Small", "installed": True, "selectable": True, "requiresGpu": False}
    self.big = {"value": "big", "label": "Big", "installed": True, "selectable": True, "requiresGpu": True, "userFavorite": True}
    self.pending = {"value": "pending", "label": "Pending", "installed": False, "selectable": False, "requiresGpu": False,
                    "communityFavorite": True, "downloadAvailable": False}
    self.missing = {"value": "missing", "label": "Missing", "installed": False, "selectable": False, "requiresGpu": True,
                    "downloadAvailable": True, "gpuAvailable": False}
    self.data = {"schemaVersion": 1, "models": [self.small, self.big, self.pending, self.missing], "isOnroad": False,
                 "activeSmallModel": "small", "activeBigModel": "big", "currentModel": "small", "downloading": False,
                 "capabilities": {"select": True, "download": True, "downloadAll": True, "favorites": True, "delete": True, "cancel": True,
                                  "randomizer": True, "exclusions": True}}

  def test_verified_profiles_and_pending_catalog_are_distinct(self):
    def picks(data, profile):
      return [(row.label, row.key) for row in model_list_rows(data, profile, "installed", "name")]
    self.assertEqual(picks(self.data, "small"), [("Small", "model:small")])
    self.assertEqual(picks(self.data, "big"), [("None - always use Active Small", "model:"), ("Big", "model:big")])
    self.assertEqual(model_request(self.data, "active", "", "big"), ("active", {"profile": "big", "model": ""}))
    self.assertIsNone(model_request(self.data, "active", "small", "big"))
    self.assertIsNone(model_request(self.data, "active", "pending", "small"))
    self.assertFalse(model_action_allowed(self.data, "download", self.pending))
    self.assertTrue(model_action_allowed(self.data, "download", self.missing))
    self.assertEqual([m["value"] for m in model_catalog_rows(self.data, "community")], ["pending"])
    self.assertEqual([m["value"] for m in model_catalog_rows(self.data, "favorites")], ["big"])

  def test_jetlink_chooses_active_big_by_its_onnx(self):
    # under Jetlink the manager marks the big models with an ONNX selectable, downloaded or not, and words none
    onnx = {**self.missing, "value": "onnx", "label": "Onnx", "selectable": True}
    data = {**self.data, "models": [self.small, {**self.big, "selectable": False}, onnx], "jetlinkBig": True,
            "activeBigModel": "onnx", "bigNoneLabel": "Jetlink app's choice"}
    rows = model_list_rows(data, "big", "installed", "name")
    self.assertEqual([(row.label, row.value) for row in rows], [("Jetlink app's choice", ""), ("Onnx", "Active")])
    self.assertEqual(model_request(data, "active", "onnx", "big"), ("active", {"profile": "big", "model": "onnx"}))
    self.assertIsNone(model_request(data, "active", "big", "big"))
    # a model chosen in the Jetlink app that StarPilot does not list is named as the app's
    app = {**data, "activeBigModel": "", "bigNoneLabel": "Cinque Terre V3 Model · from the Jetlink app"}
    self.assertEqual(model_manager_rows(app)[0].value, "Small: Small\nBig: Cinque Terre V3 Model, from the Jetlink app")

  def test_parked_and_protected_guards(self):
    self.assertFalse(model_action_allowed(self.data, "delete", self.big))
    self.assertTrue(model_action_allowed({**self.data, "activeBigModel": ""}, "delete", self.big))
    self.assertFalse(model_action_allowed({**self.data, "capabilities": {}}, "active", self.small, "small"))
    moving = {**self.data, "isOnroad": True, "downloading": False}
    self.assertTrue(model_action_allowed(moving, "active", self.small, "small"))
    self.assertTrue(model_action_allowed(moving, "active", self.big, "big"))
    self.assertFalse(model_action_allowed(moving | {"jetlinkBig": True}, "active", self.big, "big"))
    self.assertFalse(model_action_allowed(moving, "download", self.missing))
    self.assertTrue(model_action_allowed(moving, "preferences", self.small))
    downloading = {**moving, "downloading": True}
    self.assertFalse(model_action_allowed(downloading, "active", self.small, "small"))
    self.assertFalse(model_action_allowed(downloading, "active", self.big, "big"))
    self.assertTrue(model_action_allowed(downloading, "cancel"))

  def test_large_profile_rows_are_open_actions_and_receipt_stays_inert(self):
    state = model_page(ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.UNAVAILABLE, False, None, None), self.data)
    self.assertEqual(state.rows[0].value, "Small: Small\nBig: Big")
    self.assertEqual(state.rows[0].actions, (("Small", True), ("Big", True)))
    x, y, width, height = feature_action_rects(state.rows[0], feature_row_top(state))[1]
    action = FeatureInput.target(x + width / 2, y + height / 2, state)
    self.assertEqual((action.kind, action.row.key, action.direction), ("action", "models:active", 1))
    self.assertEqual([row.label for row in state.rows[:3]], ["Active Models", "Model Randomizer", "Model Catalog"])
    self.assertTrue(all(not row.available for row in state.rows[3:]))

  def test_large_catalog_rows_bind_buttons_to_fresh_intent(self):
    catalog = model_page(ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.UNAVAILABLE, False, None, None), self.data,
                         "models:catalog", "all", "name")
    rows = {row.key: row for row in catalog.rows}
    self.assertEqual((rows["model:small"].value, rows["model:small"].actions),
                     ("Active", (("Delete", False), ("Favorite", True), ("Active", False))))
    self.assertEqual(rows["model:big"].actions, (("Delete", False), ("Favorited", True), ("Active", False)))
    self.assertEqual(rows["model:missing"].actions, (("Favorite", True), ("Get", True)))
    self.assertEqual(rows["model:pending"].actions, (("Favorite", True),))
    self.assertEqual(model_row_intent(self.data, "models:catalog", "model:missing", 1), ("Get", "download", "missing", ""))
    self.assertEqual(model_row_intent(self.data, "models:catalog", "model:big", 1), ("Favorited", "preferences", "big", ""))
    self.assertIsNone(model_row_intent(self.data, "models:catalog", "model:small", 2))
    self.assertEqual(model_row_intent(self.data | {"activeBigModel": ""}, "models:pick:big", "model:big", 2),
                     ("Use", "active", "big", "big"))
    self.assertEqual(model_row_intent(self.data | {"activeBigModel": ""}, "models:catalog", "model:big", 0),
                     ("Delete", "delete", "big", ""))
    self.assertEqual(model_row_intent(self.data, "models:pick:big", "model:", 0), ("Use", "active", "", "big"))
    self.assertEqual(catalog.rows[0].key, "models:downloads")
    self.assertEqual(catalog.rows[0].actions, (("Refresh", False), ("Get All", True)))  # the fixture grants no refresh capability
    self.assertEqual(model_row_intent(self.data, "models:catalog", "models:downloads", 1), ("Get All", "download_all", "", ""))
    self.assertEqual(model_row_intent(self.data | {"downloading": True}, "models:catalog", "models:downloads", 0),
                     ("Cancel", "cancel", "", ""))
    self.assertNotIn("models:downloads", [row.key for row in model_list_rows(self.data, "small", "all", "date")])
    randomized = model_list_rows(self.data | {"randomizer": True}, "", "all", "name")
    self.assertIn(("Skip", True), next(row for row in randomized if row.key == "model:missing").actions)
    self.assertEqual(catalog.toolbar, (("Show", "All"), ("Size", "All"), ("Sort", "A-Z")))
    chips = toolbar_rects(catalog)
    x, y, width, height = chips[1][2]
    self.assertEqual(FeatureInput.target(x + width / 2, y + height / 2, catalog), FeatureUiAction("toolbar", direction=1))
    self.assertTrue(all(a[2][0] + a[2][2] < b[2][0] for a, b in zip(chips, chips[1:], strict=False)))
    pick = model_page(ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.UNAVAILABLE, False, None, None), self.data,
                      "models:pick:big", "installed", "date")
    self.assertEqual(pick.toolbar, (("Show", "Installed"), ("Sort", "Newest")))
    big = model_page(ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.UNAVAILABLE, False, None, None), self.data,
                     "models:catalog", "all", "name", "big")
    self.assertEqual([row.key for row in big.rows], ["models:downloads", "model:big", "model:missing"])
    self.assertEqual(feature_row_top(catalog), y - (154 - height) / 2 + 154)
    self.assertEqual(feature_page_rows(catalog), 4)
    self.assertEqual(feature_scroll(0, 1, 9, feature_page_rows(catalog)), 4)
    first = FeatureInput.target(1000, feature_row_top(catalog) + 77, catalog)
    self.assertEqual((first.kind, first.row), ("details", catalog.rows[0]))
    self.assertEqual(model_request(self.data, "download", "missing", allow_gpu=True),
                     ("download", {"model": "missing", "allowGpuWithoutGpu": True}))

  def test_large_toolbar_chip_opens_choices_and_applies_on_select(self):
    from openpilot.starpilot.ui import runtime_app
    from openpilot.system.ui.widgets import DialogResult
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session._mode, session.selected, session.input = ShellMode.SETTINGS, Destination.DRIVING_MODEL, mock.Mock()
    session.model_page, session.model_scroll = "models:catalog", 4
    session.model_filter, session.model_size, session.model_sort = "all", "all", "date"
    with mock.patch("openpilot.system.ui.widgets.option_dialog.MultiOptionDialog") as dialog, \
         mock.patch("openpilot.system.ui.lib.application.gui_app.push_widget"):
      session._model_option(1)
      title, labels, current = dialog.call_args.args
      self.assertEqual((title, labels, current), ("Size", ["All", "Big", "Small"], "All"))
      dialog.return_value.selection = "Big"
      callback = dialog.call_args.kwargs["callback"]
      callback(DialogResult.CANCEL)
      self.assertEqual(session.model_size, "all")
      callback(DialogResult.CONFIRM)
    self.assertEqual((session.model_size, session.model_scroll), ("big", 0))

  def test_compact_catalog_keeps_installation_and_exclusion_visible_first(self):
    session = mock.Mock()
    data = self.data | {"models": [self.small | {"blacklisted": True, "userFavorite": True}]}
    owner = ModelsCompact(session)
    page = cast(_ModelsPage, SimpleNamespace(profile="", sort_mode="name"))
    with mock.patch("openpilot.starpilot.ui.models_compact.GreyBigButton"), \
         mock.patch("openpilot.starpilot.ui.models_compact.BigButton") as button:
      owner._catalog(page, data)
    self.assertEqual(button.call_args.args[1], "installed • excluded from randomizer • small • your favorite")

  def test_compact_writes_profile_and_favorites_to_owner(self):
    session = mock.Mock()
    session.model_manager_snapshot.return_value = self.data
    session.model_manager_action.return_value = {"message": "Saved for next start"}
    owner = ModelsCompact(session)
    page = cast(_ModelsPage, SimpleNamespace())
    with mock.patch("openpilot.starpilot.ui.models_compact.gui_app.get_active_widget", return_value=page), \
         mock.patch("openpilot.starpilot.ui.models_compact.gui_app.push_widget"), \
         mock.patch("openpilot.starpilot.ui.models_compact.BigDialog"), mock.patch.object(owner, "_populate"):
      owner._action(page, "active", "", "big")
      session.model_manager_action.assert_called_with("active", {"profile": "big", "model": ""})
      owner._action(page, "preferences", "small")
      session.model_manager_action.assert_called_with("preferences", {"userFavorites": ["big", "small"]})

  def test_gpu_confirmation_rechecks_parked_state(self):
    session = mock.Mock()
    session.model_manager_snapshot.return_value = self.data
    owner = ModelsCompact(session)
    page = cast(_ModelsPage, SimpleNamespace())
    with mock.patch("openpilot.starpilot.ui.models_compact.gui_app.get_active_widget", return_value=page), \
         mock.patch("openpilot.starpilot.ui.models_compact.gui_app.texture"), \
         mock.patch("openpilot.starpilot.ui.models_compact.gui_app.push_widget"), \
         mock.patch("openpilot.starpilot.ui.models_compact.BigDialog"), \
         mock.patch("openpilot.starpilot.ui.models_compact.BigConfirmationDialog") as confirm:
      owner._action(page, "download", "missing")
      session.model_manager_action.assert_not_called()
      callback = confirm.call_args.args[2]
      session.model_manager_snapshot.return_value = {**self.data, "isOnroad": True}
      callback()
      session.model_manager_action.assert_not_called()

  def test_randomizer_disables_manual_selection_but_keeps_favorites_independent(self):
    randomized = {**self.data, "randomizer": True}
    self.assertIsNone(model_request(randomized, "active", "small", "small"))
    self.assertIsNone(model_request(randomized, "active", "", "big"))
    self.assertTrue(model_action_allowed(randomized, "preferences", self.small))
    self.assertTrue(model_action_allowed(randomized, "exclusion", self.small))
    rows = model_page(ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.UNAVAILABLE, False, None, None), randomized).rows
    self.assertEqual(rows[0].actions, (("Small", False), ("Big", False)))
    self.assertEqual(rows[0].reason, "Randomizer picks at each start")

  def test_native_preferences_dispatch_and_recheck_vehicle_state(self):
    session = mock.Mock()
    session.model_manager_snapshot.return_value = self.data
    owner = ModelsCompact(session)
    page = cast(_ModelsPage, SimpleNamespace())
    with mock.patch("openpilot.starpilot.ui.models_compact.gui_app.get_active_widget", return_value=page), \
         mock.patch("openpilot.starpilot.ui.models_compact.gui_app.push_widget"), \
         mock.patch("openpilot.starpilot.ui.models_compact.BigDialog"), mock.patch.object(owner, "_populate"):
      owner._action(page, "randomizer")
      session.model_manager_action.assert_called_with("preferences", {"randomizer": True})
      owner._action(page, "exclusion", "small")
      session.model_manager_action.assert_called_with("preferences", {"blacklistedModels": ["small"]})
      self.small["blacklisted"] = True
      self.big["blacklisted"] = True
      owner._action(page, "exclusion", "small")
      session.model_manager_action.assert_called_with("preferences", {"blacklistedModels": ["big"]})
      session.model_manager_action.reset_mock()
      session.model_manager_snapshot.return_value = {**self.data, "isOnroad": True}
      owner._action(page, "randomizer")
      owner._action(page, "exclusion", "small")
      session.model_manager_action.assert_not_called()
      owner._action(page, "preferences", "small")
      session.model_manager_action.assert_called_with("preferences", {"userFavorites": ["big", "small"]})

  def test_catalog_refresh_preserves_offline_selection_guards(self):
    data = self.data | {"downloading": True, "refreshing": True}
    self.assertTrue(model_action_allowed(data, "active", self.small, "small"))
    self.assertTrue(model_action_allowed(data, "active", self.big, "big"))
    self.assertFalse(model_action_allowed(data, "active", self.pending, "small"))
    self.assertTrue(model_action_allowed(data | {"isOnroad": True}, "active", self.big, "big"))
    self.assertFalse(model_action_allowed(data | {"randomizer": True}, "active", self.big, "big"))
    self.assertFalse(model_action_allowed(data | {"refreshing": False}, "active", self.big, "big"))
    self.assertFalse(model_action_allowed(data, "delete", self.small))
    state = model_page(ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.UNAVAILABLE, False, None, None), data)
    self.assertEqual(state.rows[0].actions, (("Small", True), ("Big", True)))

  def test_reopening_selector_reuses_rows_but_rechecks_current_state(self):
    session = mock.Mock()
    session.model_manager_snapshot.return_value = self.data
    session.model_snapshot.return_value = model_page(ModelStatus(BUNDLED_CURRENT, None, None, ModelHealth.UNAVAILABLE, False, None, None))
    owner = ModelsCompact(session)
    def new_page(*args):
      return SimpleNamespace(current=None, manager=None)
    def populate(page, state, data):
      page.current, page.manager = state, data
    with mock.patch("openpilot.starpilot.ui.models_compact._ModelsPage", side_effect=new_page) as constructor, \
         mock.patch("openpilot.starpilot.ui.models_compact.gui_app.push_widget") as push, \
         mock.patch.object(owner, "_populate", side_effect=populate) as rebuild:
      owner.open("catalog", "small")
      first = push.call_args.args[0]
      owner.open("catalog", "big")
      owner.open("catalog", "small")
      self.assertIs(push.call_args.args[0], first)
      self.assertEqual(constructor.call_count, 2)
      self.assertEqual(rebuild.call_count, 2)
      session.model_manager_snapshot.return_value = self.data | {"isOnroad": True}
      owner.open("catalog", "small")
      self.assertEqual(rebuild.call_count, 3)
      self.assertTrue(push.call_args.args[0].manager['isOnroad'])


if __name__ == "__main__":
  unittest.main()
