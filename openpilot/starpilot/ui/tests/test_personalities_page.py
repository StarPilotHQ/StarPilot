"""Driving Personalities (7 Nov 2026 layout): each personality holds all of its settings. Acceleration and braking have
a Custom toggle (off = Long Planner's choice); Following = Custom is that personality's custom gap and smoothness,
edited on its own page in Simple (low/high-speed gap) or Advanced (gap at each 10 mph) mode."""

import json
import tempfile
import unittest

from openpilot.common.params import Params
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest


class PersonalitiesPageTests(unittest.TestCase):
  def setUp(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.toyota.values import CAR
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.params = Params(self.temp.name)
    cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    cp.openpilotLongitudinalControl = True
    self.cp = cp
    self.owner = FeatureSettingsOwner(self.params, lambda group: True, vehicle_fingerprint=lambda: cp.carFingerprint)
    self.owner.vehicle_params = lambda: cp

  def rows(self, page):
    return self.owner.snapshot(page, parked=True, system_long=True, lateral_context=True, metric=False).rows

  def row(self, page, label):
    return next(row for row in self.rows(page) if row.label == label)

  def set(self, page, label, value):
    row = self.row(page, label)
    request = FeatureSettingsRequest(row.key, row.source, value, vehicle_fingerprint=row.vehicle_fingerprint,
                                     capability=row.capability, dependencies=row.dependencies, display_unit=row.display_unit)
    self.assertTrue(self.owner.apply(request), f"{page} {label} -> {value}")

  def document(self):
    saved = self.params.get("LongitudinalPersonalityProfiles")
    return saved if isinstance(saved, dict) else json.loads(saved)

  def labels(self, page):
    return [row.label for row in self.rows(page)]

  def test_hub_lists_personalities_with_a_summary_and_no_global_switch(self):
    hub = self.rows("personalities")
    self.assertEqual([row.page for row in hub], ["aggressive", "standard", "relaxed", "traffic"])
    self.assertNotIn("CustomPersonalities", [row.key for row in self.rows("profiles")])
    self.assertTrue(hub[0].value.startswith("Following: "), hub[0].value)

  def test_default_page_is_toggles_then_following(self):
    self.assertEqual(self.labels("aggressive"), ["Custom acceleration", "Custom braking", "Following"])
    self.assertEqual(self.row("aggressive", "Custom acceleration").value, "Off")
    self.assertIn("Long Planner", self.row("aggressive", "Custom acceleration").reason)
    self.assertEqual(self.row("aggressive", "Following").value, "StarPilot Default")

  def test_custom_acceleration_shows_its_choice_without_selected_profile(self):
    self.set("aggressive", "Custom acceleration", "On")
    preset = self.row("aggressive", "Acceleration")
    self.assertNotIn("Selected Profile", preset.choices)
    self.set("aggressive", "Acceleration", "Custom")
    self.assertIn("Edit custom acceleration", self.labels("aggressive"))
    self.assertEqual(self.row("aggressive", "Edit custom acceleration").page, "aggressive/acceleration")
    self.set("aggressive", "Custom acceleration", "Off")
    self.assertEqual(self.document()["profiles"]["aggressive"]["acceleration"]["preset"], "selected_profile")
    self.assertNotIn("Acceleration", self.labels("aggressive"))

  def test_following_custom_starts_simple_and_switches_to_advanced(self):
    self.set("standard", "Following", "Custom")
    self.assertEqual(self.document()["profiles"]["standard"]["following"]["preset"], "custom_simple")
    self.assertEqual(self.row("standard", "Edit custom following").page, "standard/custom_following")
    page = "standard/custom_following"
    self.assertEqual(self.labels(page), ["Customisation", "Low-speed follow", "High-speed follow", "Acceleration jerk", "Deceleration jerk",
                                         "Speed jerk", "Speed decrease jerk", "Danger jerk", "Reset Profile to Default"])
    self.set(page, "Customisation", "Advanced")
    following = self.document()["profiles"]["standard"]["following"]
    self.assertEqual(following["preset"], "custom")
    self.assertEqual(len(following["curve"]), 10)
    advanced = self.labels(page)
    self.assertEqual(advanced[1:11], [f"Following at {speed} mph" for speed in range(0, 91, 10)])
    self.assertNotIn("Low-speed follow", advanced)
    curve = following["curve"]
    self.set(page, "Customisation", "Simple")  # back to Simple keeps the Advanced curve for later
    self.assertEqual(self.document()["profiles"]["standard"]["following"], {"preset": "custom_simple", "curve": curve})
    self.set("standard", "Following", "Close")
    self.assertEqual(self.document()["profiles"]["standard"]["following"]["preset"], "close")
    self.assertNotIn("Edit custom following", self.labels("standard"))

  def test_traffic_uses_the_same_layout(self):
    self.assertEqual(self.labels("traffic")[-1], "Following")
    self.assertIn("Custom acceleration", self.labels("traffic"))


if __name__ == "__main__":
  unittest.main()
