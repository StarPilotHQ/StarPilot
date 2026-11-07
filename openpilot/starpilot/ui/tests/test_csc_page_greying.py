"""Curve Speed Controller page: the settings below the master switch grey out and refuse edits while it is off."""

import tempfile
import unittest

from openpilot.common.params import Params
from openpilot.starpilot.curve_speed.preferences import MASTER_KEY, NO_LEAD_KEY
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest

CHILDREN = (NO_LEAD_KEY, "ShowCSCStatus")
OFF_REASON = "Turn on Curve Speed Controller first"


class CscPageGreyingTests(unittest.TestCase):
  def setUp(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.toyota.values import CAR
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.params = Params(self.temp.name)
    cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    self.owner = FeatureSettingsOwner(self.params, lambda group: True, vehicle_fingerprint=lambda: cp.carFingerprint)
    self.owner.vehicle_params = lambda: cp

  def rows(self, parked=True):
    state = self.owner.snapshot("curve", parked=parked, system_long=True, lateral_context=True, metric=False)
    return {row.key: row for row in state.rows}

  def set(self, key, value, *, reset_default=False):
    row = self.rows()[key]
    return self.owner.apply(FeatureSettingsRequest(key, row.source, value, vehicle_fingerprint=row.vehicle_fingerprint,
                                                   capability=row.capability, dependencies=row.dependencies),
                            reset_default=reset_default)

  def test_children_follow_the_master_switch(self):
    rows = self.rows()
    self.assertEqual(rows[MASTER_KEY].value, "Off")
    self.assertTrue(rows[MASTER_KEY].available)
    for key in CHILDREN:
      with self.subTest(key=key):
        self.assertFalse(rows[key].available)
        self.assertEqual(rows[key].reason, OFF_REASON)
        self.assertTrue(rows[key].default_reset_available)
    self.assertTrue(self.set(MASTER_KEY, "On"))
    rows = self.rows()
    for key in CHILDREN:
      with self.subTest(key=key):
        self.assertTrue(rows[key].available)
        self.assertNotEqual(rows[key].reason, OFF_REASON)

  def test_greyed_children_refuse_edits_but_accept_their_default(self):
    self.assertTrue(self.set(MASTER_KEY, "On"))
    for key in CHILDREN:
      self.assertTrue(self.set(key, "On"), key)
    self.assertTrue(self.set(MASTER_KEY, "Off"))
    for key in CHILDREN:
      with self.subTest(key=key):
        self.assertEqual(self.rows()[key].value, "On")
        self.assertFalse(self.set(key, "Off"))
        self.assertFalse(self.set(key, "On", reset_default=True))  # a "reset" must carry the real default
        self.assertEqual(self.rows()[key].value, "On")
        self.assertTrue(self.set(key, "Off", reset_default=True))
        self.assertEqual(self.rows()[key].value, "Off")
    self.assertEqual(self.rows()[MASTER_KEY].value, "Off")

  def test_driving_greys_children_too(self):
    rows = self.rows(parked=False)
    for key in CHILDREN:
      with self.subTest(key=key):
        self.assertFalse(rows[key].available)

  def test_removed_features_are_gone_from_the_page(self):
    labels = {row.label for row in self.rows().values()}
    self.assertNotIn("Steering-load slowdown", labels)
    self.assertFalse({"Map corner speed", "Learn local corner speeds"} & labels)
    self.assertFalse(any(key.startswith("map_memory:") or key in ("MapCornerSpeed", "LearnCornerSpeeds", "CurveSteeringLoadSlowdown")
                         for key in self.rows()))


if __name__ == "__main__":
  unittest.main()
