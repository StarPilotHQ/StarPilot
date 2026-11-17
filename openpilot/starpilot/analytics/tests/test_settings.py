"""Usage settings use owner values without raw private data or mutations."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.starpilot.analytics import settings
from openpilot.starpilot.ui.feature_settings_state import FeatureRow
from openpilot.starpilot.ui.presentation import Profile


class SecretTrap:
  def __init__(self, params):
    self.params = params
    self.secret_reads = []

  def __getattr__(self, name):
    if name in ("put", "put_bool", "remove", "clear_all", "all_keys"):
      raise AssertionError(f"Collector attempted {name}")
    function = getattr(self.params, name)

    def call(key, *args, **kwargs):
      if key in ("AccessToken", "SecOCKey", "LastGPSPosition", "GalaxyPairing", "CarParams", "DongleId"):
        self.secret_reads.append(key)
        raise AssertionError("Private data read")
      return function(key, *args, **kwargs)
    return call


class TestScalarProjection(unittest.TestCase):
  def row(self, **kwargs):
    return FeatureRow("NewFeature", "New Feature", "On", choices=("Off", "On"), default_value="Off", **kwargs)

  def test_new_rows_are_automatic_without_exporting_sources(self):
    row = self.row(source=b"private-bytes", related_source=b"private-related", dependencies=(("AccessToken", b"private-token"),))
    with patch.object(settings, "_owner_rows", return_value=iter((("data", row),))):
      result = settings.collect_settings(object(), None)
    self.assertTrue(result["setting_data_NewFeature"])
    self.assertFalse(result["default_data_NewFeature"])
    self.assertTrue(result["valid_data_NewFeature"])
    self.assertNotIn("private", json.dumps(result))

  def test_invalid_numbers_fixed_enums_and_commands(self):
    number = FeatureRow("Gain", "Gain", ".75", step=.05, minimum=.5, maximum=1.5, default_value="1")
    mode = FeatureRow("Mode", "Mode", "Auto", choices=("Auto", "Manual"), default_value="Manual")
    rows = (("lane", number), ("lane", mode), ("lane", replace(number, key="BadGain", value="NaN")),
            ("lane", replace(number, key="TooLarge", value="2")),
            ("lane", replace(mode, key="FreeText", choices=(), default_value=None, value="SECRET")),
            ("sounds", replace(mode, key="SoundPack", choices=("SECRET",), value="SECRET")),
            ("lane", replace(self.row(), key="lane:reset")),
            ("lane", replace(self.row(), key="torque_prepare_firestar")))
    result = settings._fields(rows)
    self.assertEqual(result["setting_lane_Gain"], .75)
    self.assertEqual(result["default_lane_Gain"], 1.0)
    self.assertEqual(result["setting_lane_Mode"], "Auto")
    self.assertFalse(result["valid_lane_BadGain"])
    self.assertNotIn("setting_lane_BadGain", result)
    self.assertFalse(result["valid_lane_TooLarge"])
    self.assertNotIn("SECRET", json.dumps(result))
    self.assertFalse(any("reset" in key or "prepare" in key for key in result))

  def test_deterministic_bounds_and_field_id_collisions(self):
    rows = [("page", replace(self.row(), key=f"Feature{i}")) for i in range(settings.MAX_SETTINGS + 10)]
    first, second = settings._fields(rows), settings._fields(reversed(rows))
    self.assertEqual(first, second)
    self.assertTrue(first["settings_truncated"])
    self.assertLessEqual(len(json.dumps(first).encode()), settings.MAX_BYTES)
    collision = settings._fields((("page", replace(self.row(), key="a:b")), ("page", replace(self.row(), key="a_b"))))
    self.assertFalse(collision["valid_page_a_b"])
    self.assertNotIn("setting_page_a_b", collision)


class TestActualSettingsOwners(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.params = Params(self.temp.name)
    self.params.put_bool("IsOffroad", True, block=True)

  def files(self):
    return {str(path.relative_to(self.temp.name)): path.read_bytes()
            for path in Path(self.temp.name).rglob("*") if path.is_file()}

  def test_real_owners_default_values_and_profile_defaults(self):
    self.params.put_bool("LaneCentering", True, block=True)
    self.params.put_bool("StockConfidenceBallWidget", True, block=True)
    trap = SecretTrap(self.params)
    before = self.files()
    large = settings.collect_settings(trap, None, profile=Profile.LARGE)
    compact = settings.collect_settings(trap, None, profile=Profile.COMPACT)
    self.assertNotIn("setting_data_ShareUsageStats", large)
    self.assertNotIn("default_data_ShareUsageStats", large)
    self.assertTrue(large["setting_lane_LaneCentering"])
    self.assertTrue(large["default_lane_LaneCentering"])
    self.assertFalse(compact["setting_appearance_StockConfidenceBallWidget"])
    self.assertTrue(compact["default_appearance_StockConfidenceBallWidget"])
    self.assertEqual(large["default_display_ScreenTimeoutOnroad"], 10.0)
    self.assertEqual(compact["default_display_ScreenTimeoutOnroad"], 5.0)
    self.assertEqual(large["setting_display_ScreenBrightness"], "Auto")
    self.assertEqual(self.files(), before)
    self.assertEqual(trap.secret_reads, [])
    self.assertTrue(all(type(value) in (bool, float, str) for value in large.values()))

  def test_malformed_saved_value_and_secret_files_remain_private(self):
    Path(self.params.get_param_path("LaneCentering")).write_bytes(b"PRIVATE_MALFORMED")
    for key in ("AccessToken", "LastGPSPosition", "SecOCKey"):
      Path(self.params.get_param_path(key)).write_bytes(b"PRIVATE_SECRET")
    before = self.files()
    trap = SecretTrap(self.params)
    result = settings.collect_settings(trap, None, profile=Profile.LARGE)
    self.assertFalse(result["valid_lane_LaneCentering"])
    self.assertNotIn("setting_lane_LaneCentering", result)
    self.assertNotIn("PRIVATE", json.dumps(result))
    self.assertEqual(self.files(), before)
    self.assertEqual(trap.secret_reads, [])

  def test_nested_routes_and_saved_monitor_choices_exclude_layouts(self):
    from openpilot.starpilot.sentry_mode.preferences import KEY as SENTRY, Preferences as Sentry, encode as encode_sentry
    from openpilot.starpilot.sentry_mode.policy import Settings
    from openpilot.starpilot.spot_monitor.preferences import KEY as VASM, Preferences as Visual, encode as encode_visual

    self.params.put_bool("PIPPreviewShowOnBlinker", True, block=True)
    self.params.put(SENTRY, json.loads(encode_sentry(Sentry(True, Settings(.123, 2.5)))), block=True)
    self.params.put(VASM, json.loads(encode_visual(Visual(confidence=.96, smooth_seconds=.3))), block=True)
    before = self.files()
    result = settings.collect_settings(SecretTrap(self.params), None, profile=Profile.COMPACT)
    self.assertTrue(result["setting_pip_PIPPreviewShowOnBlinker"])
    self.assertTrue(result["setting_sentry_sentry_enabled"])
    self.assertEqual(result["setting_sentry_sentry_sensitivity"], .123)
    self.assertEqual(result["setting_sentry_sentry_warning"], 2.5)
    self.assertEqual(result["setting_vasm_vasm_confidence"], .96)
    self.assertEqual(result["setting_vasm_vasm_smooth"], .3)
    for personality in ("traffic", "aggressive", "standard", "relaxed"):
      for category in ("acceleration", "braking", "following"):
        key = f"setting_{personality}_{category}_profile_{personality}_{category}"
        self.assertIn(key, result)
        self.assertIsInstance(result[key], str)
    self.assertTrue(any(key.startswith("setting_conditional_cem_") for key in result))
    self.assertFalse(any(key.startswith("setting_conditional_ccm_") for key in result))
    self.assertFalse(any("pip_mask" in key or "pip_format" in key or "annotation" in key for key in result))
    self.assertEqual(self.files(), before)
