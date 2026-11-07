"""Real Params and native intent checks for Long Planner saved-value recovery."""

from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.longitudinal.profile_preferences import SCALARS, read_profile_health
from openpilot.starpilot.longitudinal.profile_runtime import read_settings
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import feature_row_top, FeatureInput, FeatureSettingsRequest, row_change
from openpilot.starpilot.ui import feature_settings_compact as compact
from openpilot.starpilot.ui.runtime_app import StarShellSession


class LongProfileFeatureTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.params = Params(self.temp.name)
    self.cp = SimpleNamespace(carFingerprint="TEST MODEL", openpilotLongitudinalControl=True,
                              pcmCruise=False, passive=False, notCar=False, dashcamOnly=False, carVin="VIN1")
    self.allowed = True
    self.owner = FeatureSettingsOwner(self.params, lambda _: self.allowed,
                                      vehicle_fingerprint=lambda: self.cp.carFingerprint,
                                      vehicle_params=lambda: self.cp)

  def path(self, key):
    return Path(self.params.get_param_path(key))

  def row(self, page, key):
    state = self.owner.snapshot(page, parked=True, system_long=True, lateral_context=True, metric=False)
    return next(row for row in state.rows if row.key == key)

  @staticmethod
  def confirm(row):
    return FeatureSettingsRequest(row.key, row.source, "confirm", confirmation=True,
                                  vehicle_fingerprint=row.vehicle_fingerprint, capability=row.capability,
                                  dependencies=row.dependencies)

  def test_health_runtime_and_repair_of_disabled_profile(self):
    self.params.put_bool("CustomPersonalities", True, block=True)
    self.path("RelaxedFollow").write_bytes(b"nan")
    self.assertFalse(read_profile_health(self.params).dependencies_valid)
    self.assertIsNone(read_settings(self.params))
    repair = self.row("relaxed/custom_following", "long_repair:RelaxedFollow")
    self.assertTrue(repair.available)
    self.assertIsNone(row_change(repair))
    self.assertTrue(self.owner.apply(self.confirm(repair)))
    self.assertAlmostEqual(self.params.get("RelaxedFollow"), 1.6)
    self.assertIsNotNone(read_settings(self.params))

  def test_onroad_profile_values_edit_but_repair_remains_parked(self):
    self.params.put_bool("CustomPersonalities", True, block=True)  # following settings are greyed while it is off
    owner = FeatureSettingsOwner(self.params, lambda group: group != "parked_preferences",
                                 vehicle_fingerprint=lambda: self.cp.carFingerprint,
                                 vehicle_params=lambda: self.cp)
    state = owner.snapshot("relaxed/custom_following", parked=False, system_long=True, lateral_context=True, metric=False,
                           configure_while_driving=True)
    follow = next(row for row in state.rows if row.key == "RelaxedFollow")
    self.assertTrue(follow.available)
    request = row_change(follow)
    assert request is not None
    self.assertTrue(owner.apply(request))
    self.assertFalse(owner.apply(request))  # The displayed source is now stale.
    self.path("RelaxedJerkDanger").write_bytes(b"bad")
    state = owner.snapshot("relaxed/custom_following", parked=False, system_long=True, lateral_context=True, metric=False,
                           configure_while_driving=True)
    repair = next(row for row in state.rows if row.key == "long_repair:RelaxedJerkDanger")
    self.assertFalse(repair.available)
    self.assertFalse(owner.apply(self.confirm(repair)))

  def test_tuning_requires_all_saved_dependencies_valid(self):
    self.assertTrue(read_profile_health(self.params).dependencies_valid)
    self.assertNotIn("CustomPersonalities", [row.key for row in self.owner.snapshot(
      "personalities", parked=True, system_long=True, lateral_context=True, metric=False).rows])
    self.path("RelaxedJerkDanger").write_bytes(b"201")
    self.assertIsNone(read_settings(self.params))
    repair = self.row("relaxed/custom_following", "long_repair:RelaxedJerkDanger")
    self.assertTrue(self.owner.apply(self.confirm(repair)))
    self.assertIsNotNone(read_settings(self.params))

  def test_nonfinite_and_deep_document_fail_closed(self):
    self.params.put_bool("CustomPersonalities", True, block=True)
    self.path("StandardFollow").write_bytes(b"inf")
    self.assertIsNone(read_settings(self.params))
    self.path("StandardFollow").unlink()
    self.path("LongitudinalPersonalityProfiles").write_bytes(b"[" * 1100 + b"0" + b"]" * 1100)
    self.assertFalse(read_profile_health(self.params).dependencies_valid)
    self.assertIsNone(read_settings(self.params))

  def test_invalid_retired_flags_and_switch_are_ignored(self):
    self.path("StandardPersonalityProfile").write_bytes(b"bad")
    self.path("CustomPersonalities").write_bytes(b"bad")
    self.assertIsNotNone(read_settings(self.params))  # Neither gates tuning any more.
    self.assertFalse(any(row.key.startswith("long_repair:") for page in ("personalities", "standard", "standard/custom_following")
                         for row in self.owner.snapshot(page, parked=True, system_long=True, lateral_context=False, metric=False).rows))
    self.assertEqual(self.path("StandardPersonalityProfile").read_bytes(), b"bad")
    self.assertTrue(read_profile_health(self.params).dependencies_valid)
    self.assertTrue(self.row("standard/custom_following", "long_reset:standard").available)

  def test_invalid_defaults_fail(self):
    original_default = self.params.get_default_value
    with patch.object(self.params, "get_default_value", side_effect=lambda key: 999.0 if key == "RelaxedFollow" else original_default(key)):
      self.assertFalse(read_profile_health(self.params).dependencies_valid)
    with patch.object(self.params, "get_default_value", side_effect=lambda key: "true" if key == "RelaxedPersonalityProfile" else original_default(key)):
      self.assertTrue(read_profile_health(self.params).dependencies_valid)

  def test_oversized_source_is_not_repairable_without_full_binding(self):
    self.params.put_bool("CustomPersonalities", True, block=True)
    self.path("RelaxedFollow").write_bytes(b"x" * 129)
    health = read_profile_health(self.params)
    self.assertFalse(health.values["RelaxedFollow"].readable)
    self.assertIsNone(read_settings(self.params))
    repair = self.row("relaxed/custom_following", "long_repair:RelaxedFollow")
    self.assertFalse(repair.available)
    self.assertFalse(self.owner.apply(self.confirm(repair)))
    self.assertFalse(self.row("relaxed/custom_following", "long_reset:relaxed").available)

  def test_oversized_document_blocks_reset(self):
    self.params.put_bool("CustomPersonalities", True, block=True)
    self.path("LongitudinalPersonalityProfiles").write_bytes(b"{" + b"x" * 65536)
    self.assertFalse(read_profile_health(self.params).values["LongitudinalPersonalityProfiles"].readable)
    reset = self.row("personalities", "reset_profiles")
    self.assertFalse(reset.available)
    self.assertFalse(self.owner.apply(self.confirm(reset)))
    self.assertFalse(self.row("aggressive/custom_following", "long_reset:aggressive").available)

  def test_reset_exact_seven_preserves_other_sources_and_curves(self):
    self.params.put_bool("CustomPersonalities", True, block=True)
    self.params.put("AggressiveFollow", 2.0, block=True)
    self.params.put("StandardFollow", 2.1, block=True)
    self.params.put_bool("AggressivePersonalityProfile", False, block=True)
    self.params.put("LongitudinalPersonalityProfiles", {}, block=True)
    before = {key: self.path(key).read_bytes() for key in
              ("StandardFollow", "AggressivePersonalityProfile", "LongitudinalPersonalityProfiles")}
    row = self.row("aggressive/custom_following", "long_reset:aggressive")
    self.assertTrue(self.owner.apply(self.confirm(row)))
    self.assertTrue(all(not self.path(key).exists() for key in SCALARS["aggressive"]))
    self.assertEqual(before, {key: self.path(key).read_bytes() for key in before})
    self.assertTrue(self.params.get_bool("CustomPersonalities"))
    self.assertIsNotNone(read_settings(self.params))

  def test_stale_and_changed_capability_cancel_before_write(self):
    self.path("RelaxedFollow").write_bytes(b"bad")
    row = self.row("relaxed/custom_following", "long_repair:RelaxedFollow")
    request = self.confirm(row)
    self.path("StandardJerkSpeed").write_bytes(b"151")
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(self.path("RelaxedFollow").read_bytes(), b"bad")
    request = self.confirm(self.row("relaxed/custom_following", "long_repair:RelaxedFollow"))
    self.cp.carVin = "VIN2"
    self.assertFalse(self.owner.apply(request))
    self.cp.carVin = "VIN1"
    request = self.confirm(self.row("relaxed/custom_following", "long_repair:RelaxedFollow"))
    self.allowed = False
    self.assertFalse(self.owner.apply(request))

  def test_partial_reset_leaves_master_off(self):
    self.params.put_bool("CustomPersonalities", True, block=True)
    self.params.put("AggressiveFollow", 2.0, block=True)
    row = self.row("aggressive/custom_following", "long_reset:aggressive")
    original_remove = self.params.remove
    def remove_then_lose(key):
      original_remove(key)
      self.allowed = False
    with patch.object(self.params, "remove", side_effect=remove_then_lose):
      self.assertFalse(self.owner.apply(self.confirm(row)))
    self.assertFalse(self.params.get_bool("CustomPersonalities"))

  def test_large_native_target_routes_confirm(self):
    self.path("AggressiveFollow").write_bytes(b"broken")
    state = self.owner.snapshot("aggressive/custom_following", parked=True, system_long=True, lateral_context=True, metric=False)
    index = next(i for i, row in enumerate(state.rows) if row.key == "long_repair:AggressiveFollow")
    state = replace(state, scroll=index)
    self.assertEqual(FeatureInput.target(1900, feature_row_top(state) + 77, state).kind, "reset")

  def test_compact_native_confirmation_routes_captured_request(self):
    self.path("AggressiveFollow").write_bytes(b"broken")
    class Button:
      def __init__(self, text, value):
        self.text, self.value, self.click = text, value, None
      def set_click_callback(self, callback):
        self.click = callback
      def set_enabled(self, enabled):
        self.enabled = enabled
    class Scroller:
      def __init__(self):
        self.items = []
        self._scroller = self
      def add_widgets(self, cards):
        self.items.extend(cards)
    class Dialog:
      def __init__(self, question, icon, confirm_callback, **kwargs):
        self.question, self.confirm = question, confirm_callback
    class Session(StarShellSession):
      def __init__(self, owner):
        self.owner = owner
      def feature_snapshot(self, page: str | None = None, *, favorite: bool = False):
        return self.owner.snapshot(page or "hub", parked=True, system_long=True, lateral_context=True, metric=False)
      def feature_request(self, request):
        return self.owner.apply(request)
    pushed = []
    with patch.object(compact, "BigButton", Button), patch.object(compact, "GreyBigButton", Button), \
         patch.object(compact, "NavScroller", Scroller), patch.object(compact, "BigConfirmationDialog", Dialog), \
         patch.object(compact.gui_app, "push_widget", pushed.append), patch.object(compact.gui_app, "texture", lambda *args: None):
      compact.FeatureSettingsCompact(Session(self.owner)).open("aggressive/custom_following")
      page = pushed[-1]
      next(item for item in page.items if item.text == "restore low-speed follow default").click()
      self.assertIn("may resume", pushed[-1].question)
      pushed[-1].confirm()
    self.assertAlmostEqual(self.params.get("AggressiveFollow"), 1.25)


if __name__ == "__main__":
  unittest.main()
