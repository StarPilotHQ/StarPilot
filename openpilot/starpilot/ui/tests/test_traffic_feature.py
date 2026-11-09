"""Saved Traffic controls use real Params and current Long Planner authority."""

from pathlib import Path
from types import SimpleNamespace
import os
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common.params import Params
from opendbc.car.structs import car
from openpilot.starpilot import saved_document
from openpilot.starpilot.longitudinal.profile_document import default_personality_profiles, migrate_profile_document, serialize_personality_profiles
from openpilot.starpilot.longitudinal.profile_runtime import read_traffic_settings
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, row_change
from openpilot.starpilot.ui.traffic_feature import FOLLOW


def required_change(row: FeatureRow, direction: int = 1) -> FeatureSettingsRequest:
  request = row_change(row, direction)
  assert request is not None
  return request


class TrafficFeatureTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.parked = True
    self.cp = SimpleNamespace(carFingerprint="TOYOTA COROLLA TSS2", openpilotLongitudinalControl=True, pcmCruise=False,
                              passive=False, notCar=False, dashcamOnly=False, carVin="VIN1",
                              transmissionType=car.CarParams.TransmissionType.automatic)
    self.owner = FeatureSettingsOwner(self.params, lambda group: self.parked and group in ("long", "parked_preferences"),
                                      vehicle_fingerprint=lambda: self.cp.carFingerprint, vehicle_params=lambda: self.cp)

  def row(self, key):
    # Traffic's follow and jerk values sit on its custom-following page.
    state = self.owner.snapshot("traffic/custom_following", parked=self.parked, system_long=True, lateral_context=False, metric=False)
    return next(row for row in state.rows if row.key == key)

  def test_saved_scalar_rows_and_category_editor_are_real_while_master_off(self):
    traffic = self.owner.snapshot("traffic", parked=True, system_long=True, lateral_context=False, metric=False)
    self.assertEqual(traffic.title, "Traffic Profile")
    self.assertEqual(self.row(FOLLOW).value, "0.75")
    self.assertTrue(self.row(FOLLOW).available)
    self.assertTrue(self.row(FOLLOW).reason.startswith("Following time at low speeds"))
    self.assertNotIn("TrafficPersonalityProfile", [row.key for row in traffic.rows])
    self.assertTrue(self.owner.apply(required_change(self.row(FOLLOW))))
    self.assertEqual(self.params.get(FOLLOW), 0.8)
    jerk = self.row("TrafficJerkAcceleration")
    self.assertEqual((jerk.value, jerk.minimum, jerk.maximum, jerk.step, jerk.unit), ("100.0", 25.0, 200.0, 5.0, "%"))
    self.assertTrue(self.owner.apply(required_change(jerk)))
    self.assertEqual(self.params.get("TrafficJerkAcceleration"), 105.0)
    self.assertEqual(read_traffic_settings(self.params).follow[0], 0.8)  # Read always; Following decides use.
    category = self.owner.snapshot("traffic/braking", parked=True, system_long=True, lateral_context=False, metric=False)
    preset = category.rows[0]
    self.assertEqual(preset.key, "profile:traffic:braking")
    self.assertTrue(self.owner.apply(FeatureSettingsRequest(
      preset.key, preset.source, "standard", vehicle_fingerprint=preset.vehicle_fingerprint,
      capability=preset.capability, dependencies=preset.dependencies)))
    document = migrate_profile_document(Path(self.params.get_param_path("LongitudinalPersonalityProfiles")).read_bytes())
    assert document is not None
    self.assertEqual(document["profiles"]["traffic"]["braking"]["preset"], "standard")
    self.assertFalse(document["enabled"])

  def test_half_second_saved_follow_remains_editable_without_repair(self):
    path = Path(self.params.get_param_path(FOLLOW))
    path.write_bytes(b"0.5")
    row = self.row(FOLLOW)
    self.assertEqual((row.value, row.minimum, row.maximum), ("0.5", 0.5, 3.0))
    self.assertTrue(row.available)
    self.assertEqual(path.read_bytes(), b"0.5")
    self.assertTrue(self.owner.apply(required_change(row)))
    self.assertEqual(path.read_bytes(), b"0.55")
    self.assertFalse(self.owner.apply(required_change(row)))

  def test_invalid_saved_follow_remains_unavailable_and_unchanged(self):
    path = Path(self.params.get_param_path(FOLLOW))
    for raw in (b"invalid", b"0.49", b"3.01"):
      path.write_bytes(raw)
      row = self.row(FOLLOW)
      self.assertEqual(row.value, "Invalid saved value")
      self.assertFalse(row.available)
      self.assertEqual(path.read_bytes(), raw)

  def test_half_second_edit_uses_long_authority_without_parked_repair(self):
    path = Path(self.params.get_param_path(FOLLOW))
    path.write_bytes(b"0.5")
    self.parked = False
    self.owner.authority = lambda group: group == "long"
    row = self.row(FOLLOW)
    self.assertTrue(row.available)
    self.assertTrue(self.owner.apply(required_change(row)))
    self.assertEqual(path.read_bytes(), b"0.55")
    request = required_change(self.row(FOLLOW))
    self.owner.authority = lambda group: False
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(path.read_bytes(), b"0.55")

  def test_half_second_traffic_curve_can_edit_without_replacing_preset(self):
    profiles = default_personality_profiles(False)
    profiles["traffic"]["following"] = {"preset": "custom", "curve": [0.5] * 10}
    raw = serialize_personality_profiles(profiles, False, False, enabled=False)
    path = Path(self.params.get_param_path("LongitudinalPersonalityProfiles"))
    path.write_bytes(raw.encode())
    page = self.owner.snapshot("traffic/following", parked=True, system_long=True, lateral_context=False, metric=False)
    self.assertEqual(page.rows[0].value, "Custom")
    point = page.rows[1]
    self.assertTrue(point.available)
    self.assertEqual(point.minimum, 0.5)
    self.assertEqual(path.read_bytes(), raw.encode())
    invalid = FeatureSettingsRequest(point.key, point.source, "0.49", vehicle_fingerprint=point.vehicle_fingerprint,
                                     capability=point.capability, dependencies=point.dependencies)
    self.assertFalse(self.owner.apply(invalid))
    self.assertTrue(self.owner.apply(required_change(point)))
    document = migrate_profile_document(path.read_bytes())
    assert document is not None
    self.assertEqual(document["profiles"]["traffic"]["following"], {"preset": "custom", "curve": [0.55] + [0.5] * 9})

  def test_category_edit_rechecks_system_long_capability_under_lock(self):
    row = self.owner.snapshot("traffic/braking", parked=True, system_long=True, lateral_context=False, metric=False).rows[0]
    request = required_change(row)
    self.assertIsNotNone(request)
    self.cp.openpilotLongitudinalControl = False
    self.assertFalse(self.owner.apply(request))
    self.cp.openpilotLongitudinalControl = True
    path = Path(self.params.get_param_path("LongitudinalPersonalityProfiles"))
    real_fsync = os.fsync
    def revoke(fd):
      real_fsync(fd)
      self.cp.openpilotLongitudinalControl = False
    with patch.object(saved_document.os, "fsync", side_effect=revoke):
      self.assertFalse(self.owner.apply(request))
    self.assertFalse(path.exists())

  def test_custom_starts_from_actual_traffic_defaults_and_ev_named_curve(self):
    def select(category, preset):
      row = self.owner.snapshot(f"traffic/{category}", parked=True, system_long=True,
                                lateral_context=False, metric=False).rows[0]
      request = FeatureSettingsRequest(row.key, row.source, preset, vehicle_fingerprint=row.vehicle_fingerprint,
                                       capability=row.capability, dependencies=row.dependencies)
      self.assertTrue(self.owner.apply(request))
      raw = Path(self.params.get_param_path("LongitudinalPersonalityProfiles")).read_bytes()
      document = migrate_profile_document(raw)
      assert document is not None
      return document["profiles"]["traffic"][category]

    acceleration = select("acceleration", "custom")
    self.assertEqual((acceleration["curve"][0], acceleration["curve"][-1]), (1.1, 0.23))
    braking = select("braking", "custom")
    self.assertEqual(braking["curve"], [0.6] * 10)
    self.assertEqual(self.owner.snapshot("traffic/braking", parked=True, system_long=True,
                                        lateral_context=False, metric=False).rows[1].minimum, 0.35)

    self.params.put("TrafficFollow", 0.8, block=True)
    self.params.put("RelaxedFollow", 1.8, block=True)
    following = select("following", "custom")
    self.assertEqual((following["curve"][0], following["curve"][-1]), (0.8, 1.8))

    self.cp.transmissionType = car.CarParams.TransmissionType.direct
    Path(self.params.get_param_path("LongitudinalPersonalityProfiles")).write_bytes(
      serialize_personality_profiles(default_personality_profiles(False), False, False, enabled=False).encode())
    select("acceleration", "eco")
    acceleration = select("acceleration", "custom")
    self.assertEqual(acceleration["curve"][-1], 0.58)

  def test_locked_save_rejects_staged_source_authority_and_unverified_readback(self):
    request = required_change(self.row(FOLLOW))
    self.assertIsNotNone(request)
    path = Path(self.params.get_param_path(FOLLOW))
    real_fsync = os.fsync
    calls = 0

    def concurrent(fd):
      nonlocal calls
      real_fsync(fd)
      calls += 1
      if calls == 1:
        path.write_bytes(b"1.2")

    with patch.object(saved_document.os, "fsync", side_effect=concurrent):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(path.read_bytes(), b"1.2")

    request = required_change(self.row(FOLLOW))
    calls = 0

    def revoke(fd):
      nonlocal calls
      real_fsync(fd)
      calls += 1
      if calls == 1:
        self.parked = False

    with patch.object(saved_document.os, "fsync", side_effect=revoke):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(path.read_bytes(), b"1.2")
    self.parked = True

    request = required_change(self.row(FOLLOW))
    real_read = saved_document.read_saved
    calls = 0

    def unverified(*args):
      nonlocal calls
      calls += 1
      return (b"unverified", True) if calls == 3 else real_read(*args)

    with patch.object(saved_document, "read_saved", side_effect=unverified):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(calls, 3)
    self.assertEqual(path.read_bytes(), b"1.25")


if __name__ == "__main__":
  unittest.main()
