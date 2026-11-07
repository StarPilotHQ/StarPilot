"""Each personality's Following choice decides its follow time and smoothness (7 Nov 2026):
StarPilot Default = openpilot's own; a named curve = that curve with standard jerk; Custom = the saved values."""

import json
import unittest
from copy import deepcopy
from pathlib import Path

from openpilot.cereal import log
from opendbc.car.car_helpers import interfaces
from opendbc.car.toyota.values import CAR
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import get_T_FOLLOW, get_jerk_factor
from openpilot.common.params import Params
from openpilot.starpilot.longitudinal.profile_document import (PERSONALITY_PROFILES_PARAM, default_personality_profiles, profile_document,
                                                               serialize_personality_profiles)
from openpilot.starpilot.longitudinal.profile_runtime import (
  STOCK_FOLLOW_SECONDS, STOCK_JERK, TRAFFIC_DEFAULT_FOLLOW, ProfileSettings, TrafficSettings, resolve,
)

AGGRESSIVE = log.LongitudinalPersonality.aggressive
LOW_SPEED, HIGH_SPEED = 10.0, 35.0  # m/s: below 45 mph and above 70 mph


class PersonalityFollowingTests(unittest.TestCase):
  def setUp(self):
    self.cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    self.follow = {name: (1.1, 2.2) for name in ("aggressive", "standard", "relaxed")}
    self.jerk = {name: (1.5, 1.6, 1.7, 1.8, 1.9) for name in ("aggressive", "standard", "relaxed")}

  def settings(self, preset, curve=None):
    profiles = deepcopy(default_personality_profiles(False, False))
    profiles["aggressive"]["following"] = {"preset": preset, "curve": curve or []}
    document = profile_document(profiles, enabled=True)
    return ProfileSettings(document, dict.fromkeys(("aggressive", "standard", "relaxed"), True), self.follow, self.jerk)

  def test_stock_values_match_openpilot(self):
    for name, personality in (("aggressive", AGGRESSIVE), ("standard", log.LongitudinalPersonality.standard),
                              ("relaxed", log.LongitudinalPersonality.relaxed)):
      self.assertEqual(STOCK_FOLLOW_SECONDS[name], get_T_FOLLOW(personality))
      self.assertEqual(STOCK_JERK[name], get_jerk_factor(personality))

  def test_starpilot_default_leaves_openpilot_in_charge(self):
    self.assertIsNone(resolve(self.settings("dom_default"), AGGRESSIVE, LOW_SPEED, self.cp))

  def test_named_curve_uses_the_curve_with_standard_jerk(self):
    tuning = resolve(self.settings("far"), AGGRESSIVE, HIGH_SPEED, self.cp)
    self.assertAlmostEqual(tuning.follow_seconds, 1.4)  # Far ends at 1.4 s
    self.assertEqual((tuning.acceleration_jerk, tuning.danger_jerk), (0.5, 0.5))

  def test_custom_simple_uses_low_and_high_speed_follow_and_saved_jerk(self):
    settings = self.settings("custom_simple", [1.0] * 10)
    self.assertAlmostEqual(resolve(settings, AGGRESSIVE, LOW_SPEED, self.cp).follow_seconds, 1.1)
    tuning = resolve(settings, AGGRESSIVE, HIGH_SPEED, self.cp)
    self.assertAlmostEqual(tuning.follow_seconds, 2.2)
    self.assertEqual(tuning.acceleration_jerk, 1.5)

  def test_custom_advanced_uses_the_curve_and_saved_jerk(self):
    tuning = resolve(self.settings("custom", [2.0] * 10), AGGRESSIVE, LOW_SPEED, self.cp)
    self.assertAlmostEqual(tuning.follow_seconds, 2.0)
    self.assertEqual(tuning.danger_jerk, 1.9)

  def test_traffic(self):
    def traffic(preset):
      profiles = deepcopy(default_personality_profiles(False, False))
      profiles["traffic"]["following"] = {"preset": preset, "curve": []}
      settings = TrafficSettings(True, "valid", profile_document(profiles, enabled=True), True, (1.0, 2.0),
                                 tuple((1.3, 1.3) for _ in range(5)))
      return resolve(settings, AGGRESSIVE, 0.0, self.cp, traffic_mode=True)
    simple = traffic("custom_simple")
    self.assertAlmostEqual(simple.follow_seconds, 1.0)
    self.assertAlmostEqual(simple.acceleration_jerk, 1.3)
    default = traffic("dom_default")
    self.assertAlmostEqual(default.follow_seconds, TRAFFIC_DEFAULT_FOLLOW[0])
    self.assertEqual(default.acceleration_jerk, 1.0)


if __name__ == "__main__":
  unittest.main()


def _migrate_case(tmp_path, switch, presets=None):
  from openpilot.starpilot.longitudinal.profile_preferences import migrate_retired_switch
  params = Params(str(tmp_path))
  if switch is not None:
    params.put_bool("CustomPersonalities", switch, block=True)
  if presets is not None:
    profiles = default_personality_profiles(False, False)
    for name, preset in presets.items():
      profiles[name]["following"] = {"preset": preset, "curve": profiles[name]["following"]["curve"] if preset == "custom" else []}
    params.put(PERSONALITY_PROFILES_PARAM, json.loads(serialize_personality_profiles(profiles, False, enabled=True)), block=True)
  assert migrate_retired_switch(params)
  saved = params.get(PERSONALITY_PROFILES_PARAM)
  document = saved if isinstance(saved, dict) or saved is None else json.loads(saved)
  return params, document


def test_retired_switch_on_keeps_saved_follow_and_jerk_in_use(tmp_path):
  params, document = _migrate_case(tmp_path, True)
  assert {name: p["following"]["preset"] for name, p in document["profiles"].items()} == dict.fromkeys(
    ("traffic", "aggressive", "standard", "relaxed"), "custom_simple")
  assert not Path(params.get_param_path("CustomPersonalities")).exists(), "the retired switch is removed once carried over"


def test_retired_switch_off_drops_following_choices_that_never_applied(tmp_path):
  params, document = _migrate_case(tmp_path, False, {"standard": "close"})
  assert document["profiles"]["standard"]["following"]["preset"] == "dom_default"
  assert not Path(params.get_param_path("CustomPersonalities")).exists()


def test_no_retired_switch_changes_nothing(tmp_path):
  params, document = _migrate_case(tmp_path, None, {"standard": "close"})
  assert document["profiles"]["standard"]["following"]["preset"] == "close"
