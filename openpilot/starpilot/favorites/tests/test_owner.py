import json
from pathlib import Path
import tempfile
import unittest

from openpilot.starpilot.favorites.actions import BOOKMARK, SET_SPEED, mapped_actions
from openpilot.starpilot.favorites.owner import FAVORITE_SLOTS_PARAM, MAX_BYTES, FavoritesChanged, FavoritesOwner, default_slots, read_slots
from openpilot.starpilot.favorites.state import FavoriteAction, FavoriteRequest
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState
from openpilot.starpilot.ui.presentation import Profile


class FileParams:
  def __init__(self, root):
    self.directory = Path(root) / "d"
    self.directory.mkdir()

  def get_param_path(self, key):
    return str(self.directory / key)


class TestFavoritesOwner(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.params = FileParams(directory.name)
    self.path = Path(self.params.get_param_path(FAVORITE_SLOTS_PARAM))
    self.configurable = True
    self.available = True
    self.value = False
    self.calls = 0
    self.owner = FavoritesOwner(self.params, self.actions, lambda: self.configurable)

  def actions(self):
    return {BOOKMARK: FavoriteAction(BOOKMARK, "Bookmark", available=self.available, token=str(self.value), invoke=self.invoke_action)}

  def invoke_action(self):
    if not self.available:
      return False
    self.calls += 1
    self.value = not self.value
    return True

  def assign(self, index=0):
    self.assertTrue(self.owner.configure_index(index, BOOKMARK, self.owner.snapshot().revision))
    return self.owner.snapshot().slots[index].request

  def test_original_default_storage_three_slots_and_read_only_snapshot(self):
    snapshot = self.owner.snapshot()
    self.assertEqual(len(snapshot.slots), 3)
    self.assertFalse(any(slot.enabled or slot.show_onroad for slot in snapshot.slots))
    self.assertFalse(self.path.exists())
    self.assign(1)
    slots = json.loads(self.path.read_bytes())
    self.assertEqual(slots[0], default_slots()[0])
    self.assertEqual(slots[1], {"enabled": True, "show_onroad": True, "key": BOOKMARK, "label": "Bookmark"})
    self.assertEqual(slots[2], default_slots()[2])

  def test_activation_rechecks_config_identity_state_and_action_authority(self):
    request = self.assign()
    self.assertTrue(self.owner.invoke(request).success)
    self.assertEqual(self.calls, 1)
    self.assertFalse(self.owner.invoke(request).success)
    request = self.owner.snapshot().slots[0].request
    self.available = False
    self.assertFalse(self.owner.invoke(request).success)
    self.available = True
    self.assertTrue(self.owner.clear_index(0, self.owner.snapshot().revision))
    self.assertFalse(self.owner.invoke(request).success)
    self.assertEqual(self.calls, 1)

  def test_native_assignment_cas_does_not_need_parked_but_requires_config_authority(self):
    before = self.owner.snapshot()
    self.assign(2)
    self.assertFalse(self.owner.configure_index(0, BOOKMARK, before.revision))
    self.configurable = False
    self.assertFalse(self.owner.snapshot().configurable)
    self.assertFalse(self.owner.clear_index(2, self.owner.snapshot().revision))
    self.assertTrue(read_slots(self.params)[0][2]["enabled"])

  def test_config_authority_is_checked_again_at_file_commit(self):
    checks = 0
    def authorized():
      nonlocal checks
      checks += 1
      return checks < 2
    with self.assertRaises(FavoritesChanged):
      self.owner.save(default_slots(), self.owner.snapshot().revision, authorized=authorized)
    self.assertFalse(self.path.exists())

  def test_unknown_legacy_control_and_set_speed_value_survive_other_slot_edits(self):
    original = default_slots()
    original[0] = {"enabled": True, "show_onroad": True, "key": SET_SPEED, "label": "Cruise 45", "value": 45}
    original[1] = {"enabled": True, "show_onroad": True, "key": "OldUnsupportedSetting", "label": "Keep me"}
    self.path.write_text(json.dumps({"slots": original}))
    self.assertTrue(self.owner.snapshot().valid)
    self.assertFalse(self.owner.snapshot().slots[0].available)
    self.assertEqual(self.owner.snapshot().slots[0].value, 45)
    self.assign(2)
    self.assertEqual(json.loads(self.path.read_bytes())[:2], original[:2])
    before = self.path.read_bytes()
    attempted = read_slots(self.params)[0]
    attempted[2]["key"] = "ArbitraryParam"
    with self.assertRaises(ValueError):
      self.owner.save(attempted, self.owner.snapshot().revision)
    self.assertEqual(self.path.read_bytes(), before)
    self.assertFalse(self.owner.invoke(FavoriteRequest(1, "OldUnsupportedSetting", self.owner.snapshot().revision, "")).success)
    self.assertEqual(self.calls, 0)

  def test_invalid_saved_bytes_are_not_silently_rewritten_and_oversize_is_unavailable(self):
    self.path.write_bytes(b'[{"enabled":true,"enabled":false}]')
    self.assertFalse(self.owner.snapshot().valid)
    self.assertEqual(self.path.read_bytes(), b'[{"enabled":true,"enabled":false}]')
    self.assign()
    self.assertTrue(self.owner.snapshot().valid)
    self.path.write_bytes(b'x' * (MAX_BYTES + 1))
    self.assertFalse(self.owner.snapshot().configurable)
    self.assertFalse(self.owner.configure_index(0, BOOKMARK, self.owner.snapshot().revision))

  def test_mapped_visual_action_uses_real_appearance_owner_parked_and_saved_source_guards(self):
    parked = False
    appearance = AppearanceOwner(self.params, lambda: parked)
    def empty(_page):
      return FeatureSettingsState()
    def provider():
      return mapped_actions(empty, lambda _request: self.fail("No driving action expected"),
                            lambda: appearance.snapshot(Profile.LARGE), appearance.apply)
    owner = FavoritesOwner(self.params, provider, lambda: True)
    self.assertTrue(owner.configure_index(0, "SignalMetrics", owner.snapshot().revision))
    blocked = owner.snapshot().slots[0]
    self.assertFalse(blocked.available)
    self.assertFalse(owner.invoke(blocked.request).success)
    self.assertFalse(Path(self.params.get_param_path("SignalMetrics")).exists())
    parked = True
    request = owner.snapshot().slots[0].request
    self.assertTrue(owner.invoke(request).success)
    self.assertEqual(Path(self.params.get_param_path("SignalMetrics")).read_bytes(), b"1")
    self.assertFalse(owner.invoke(request).success)

    # A slot saved with a key this provider does not offer keeps its assignment but cannot act.
    retired = FavoritesOwner(self.params, lambda: mapped_actions(empty, lambda _request: None,
                                                                 lambda: appearance.snapshot(Profile.LARGE), appearance.apply),
                             lambda: True)
    self.assertFalse(retired.configure_index(1, "RainbowPath", retired.snapshot().revision))
    self.path.write_bytes(self.path.read_bytes().replace(b'"SignalMetrics"', b'"RainbowPath"'))
    slot = retired.snapshot().slots[0]
    self.assertEqual((slot.key, slot.available, slot.request), ("RainbowPath", False, None))
    self.assertEqual(slot.reason, "This saved control is not available in this build")

  def test_retired_slc_fallback_shortcut_preserves_saved_assignment_without_acting(self):
    slots = default_slots()
    slots[0].update(enabled=True, show_onroad=True, key='SLCFallback', label='Previous limit')
    raw = json.dumps(slots).encode()
    self.path.write_bytes(raw)
    legacy = Path(self.params.get_param_path('SLCFallback'))
    legacy.write_bytes(b'1')
    row = FeatureRow('SLCFallback', 'Previous accepted limit', 'On', choices=('Off', 'On'), available=True)
    owner = FavoritesOwner(self.params,
      lambda: mapped_actions(lambda _page: FeatureSettingsState(rows=(row,)),
                             lambda _request: self.fail('Retired action must not run'),
                             lambda: self.fail('No appearance read expected'), lambda _request: None,
                             requested_keys=frozenset({'SLCFallback'})), lambda: True)
    snapshot = owner.snapshot()
    self.assertFalse(any(action.key == 'SLCFallback' for action in snapshot.options))
    slot = snapshot.slots[0]
    self.assertEqual(slot.key, 'SLCFallback')
    self.assertFalse(slot.available)
    self.assertIsNone(slot.request)
    self.assertFalse(owner.invoke(FavoriteRequest(0, 'SLCFallback', snapshot.revision, '')).success)
    self.assertEqual(self.path.read_bytes(), raw)
    self.assertEqual(legacy.read_bytes(), b'1')

  def test_registry_excludes_arbitrary_repairs_and_reset_operations(self):
    rows = (FeatureRow("ArbitraryParam", "Other", "Off", choices=("Off", "On"), available=True),
            FeatureRow("LaneCentering", "Lane centering", "Invalid", choices=("Off", "On"), available=True, repair_value="Off"),
            FeatureRow("reset_profiles", "Reset", "Confirm", available=True))
    actions = mapped_actions(lambda _page: FeatureSettingsState(rows=rows), lambda _request: self.fail("No repair expected"),
                             lambda: FeatureSettingsState(), lambda _request: False)
    self.assertNotIn("ArbitraryParam", actions)
    self.assertNotIn("reset_profiles", actions)
    self.assertFalse(actions["LaneCentering"].available)
    self.assertIsNone(actions["LaneCentering"].invoke)


if __name__ == "__main__":
  unittest.main()
