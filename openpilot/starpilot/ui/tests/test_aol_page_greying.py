"""Always On Lateral page: the switch leads, Brake pause below greys while AOL is off, and the steering-pause rows
(which pause all steering, not only AOL) stay editable."""

from pathlib import Path
import tempfile
import unittest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR as HONDA
from openpilot.common.params import Params
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest, row_change

THRESHOLD = "AolBrakePauseSpeedMps"
PAUSE_KEYS = ("PauseLateralSpeed", "PauseLateralOnSignal", "LateralResumeDelay")


class AolPageGreyingTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.params = Params(self.temp.name)
    self.cp = CarInterface.get_params(HONDA.HONDA_CIVIC_BOSCH, gen_empty_fingerprint(), [], True, False, False)
    self.cp.safetyConfigs[-1].safetyParam |= 0x20
    self.owner = FeatureSettingsOwner(self.params, lambda group: True,
                                      vehicle_fingerprint=lambda: str(self.cp.carFingerprint), vehicle_params=lambda: self.cp)

  def rows(self, parked=True):
    return self.owner.snapshot("aol", parked=parked, system_long=True, lateral_context=True, metric=False).rows

  def row(self, key):
    return next(row for row in self.rows() if row.key == key)

  def test_switch_leads_then_brake_pause_then_steering_pause(self):
    self.assertEqual([row.key for row in self.rows()], ["AlwaysOnLateral", THRESHOLD, *PAUSE_KEYS])

  def test_every_row_below_the_switch_follows_it(self):
    self.params.put_bool("AlwaysOnLateral", False, block=True)
    rows = {row.key: row for row in self.rows()}
    self.assertTrue(rows["AlwaysOnLateral"].available)
    for key in (THRESHOLD, *PAUSE_KEYS):
      with self.subTest(key=key):
        self.assertFalse(rows[key].available)
        self.assertEqual(rows[key].reason, "Turn on Always On Lateral first")
        self.assertTrue(rows[key].default_reset_available)
    self.params.put_bool("AlwaysOnLateral", True, block=True)
    rows = {row.key: row for row in self.rows()}
    for key in (THRESHOLD, *PAUSE_KEYS):
      with self.subTest(on=key):
        self.assertTrue(rows[key].available)
        self.assertNotIn("Turn on", rows[key].reason)

  def test_greyed_threshold_refuses_edits_but_accepts_restore_default(self):
    self.params.put_bool("AlwaysOnLateral", True, block=True)
    self.params.put(THRESHOLD, 5.0, block=True)
    self.params.put_bool("AlwaysOnLateral", False, block=True)
    row = self.row(THRESHOLD)
    edit = FeatureSettingsRequest(THRESHOLD, row.source, "20", vehicle_fingerprint=row.vehicle_fingerprint,
                                  capability=row.capability, dependencies=row.dependencies, display_unit=row.display_unit)
    self.assertFalse(self.owner.apply(edit))
    self.assertFalse(self.owner.apply(edit, reset_default=True))  # a "reset" must carry the real default
    self.assertEqual(self.params.get(THRESHOLD), 5.0)
    reset = FeatureSettingsRequest(THRESHOLD, row.source, row.default_value, vehicle_fingerprint=row.vehicle_fingerprint,
                                   capability=row.capability, dependencies=row.dependencies, display_unit=row.display_unit)
    self.assertTrue(self.owner.apply(reset, reset_default=True))
    self.assertEqual(self.params.get(THRESHOLD), 0.0)
    self.assertFalse(self.params.get_bool("AlwaysOnLateral"))

  def test_invalid_threshold_stays_repairable_while_off(self):
    self.params.put_bool("AlwaysOnLateral", False, block=True)
    Path(self.params.get_param_path(THRESHOLD)).write_bytes(b"bad")
    repair = self.row(THRESHOLD)
    self.assertTrue(repair.available)
    self.assertEqual(repair.repair_value, "0")
    self.assertTrue(self.owner.apply(row_change(repair, 1)))
    self.assertEqual(self.params.get(THRESHOLD), 0.0)
    self.assertFalse(self.row(THRESHOLD).available)  # repaired and valid, so greyed again while AOL is off

  def test_steering_pause_edits_only_while_aol_is_on(self):
    self.params.put_bool("AlwaysOnLateral", True, block=True)
    self.assertTrue(self.owner.apply(row_change(self.row("PauseLateralSpeed"), 1)))
    self.assertEqual(self.params.get("PauseLateralSpeed"), 1.0)
    self.params.put_bool("AlwaysOnLateral", False, block=True)
    for key, value in (("PauseLateralSpeed", "5"), ("PauseLateralOnSignal", "On"), ("LateralResumeDelay", "2")):
      with self.subTest(key=key):
        row = self.row(key)
        request = FeatureSettingsRequest(key, row.source, value, vehicle_fingerprint=row.vehicle_fingerprint,
                                         capability=row.capability, dependencies=row.dependencies, display_unit=row.display_unit)
        self.assertFalse(self.owner.apply(request))
        self.assertFalse(self.owner.apply(request, reset_default=True))  # a "reset" must carry the real default
    self.assertEqual(self.params.get("PauseLateralSpeed"), 1.0)
    row = self.row("PauseLateralSpeed")  # Restore defaults still resets a greyed row
    self.assertTrue(self.owner.apply(FeatureSettingsRequest("PauseLateralSpeed", row.source, row.default_value,
                                                            vehicle_fingerprint=row.vehicle_fingerprint, capability=row.capability,
                                                            dependencies=row.dependencies, display_unit=row.display_unit),
                                     reset_default=True))
    self.assertEqual(self.params.get("PauseLateralSpeed"), 0.0)
    self.assertFalse(self.params.get_bool("AlwaysOnLateral"))


if __name__ == "__main__":
  unittest.main()
