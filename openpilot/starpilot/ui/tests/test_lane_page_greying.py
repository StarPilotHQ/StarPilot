"""Lane Centering page: the rows below the master switch grey out and refuse edits while it is off."""

import tempfile
import unittest

from openpilot.common.params import Params
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest

CHILDREN = {"LaneCenteringPauseOnSignal": "Off", "LaneCenterOffset": "0.1",
            "LaneCenteringE2EAuthority": "80", "LaneCenteringStrength": "150"}


class LanePageGreyingTests(unittest.TestCase):
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
    state = self.owner.snapshot("lane", parked=parked, system_long=True, lateral_context=True, metric=False)
    return {row.key: row for row in state.rows}

  def test_children_follow_the_master_switch(self):
    self.params.put_bool("LaneCentering", False, block=True)
    rows = self.rows()
    self.assertTrue(rows["LaneCentering"].available)
    for key in CHILDREN:
      with self.subTest(key=key):
        self.assertFalse(rows[key].available)
        self.assertEqual(rows[key].reason, "Turn on Lane Centering first")
        self.assertTrue(rows[key].default_reset_available)
    self.params.put_bool("LaneCentering", True, block=True)
    rows = self.rows()
    for key in CHILDREN:
      with self.subTest(key=key):
        self.assertTrue(rows[key].available)
        self.assertNotIn("Turn on Lane Centering", rows[key].reason)

  def test_greyed_children_refuse_edits_but_accept_their_default(self):
    self.params.put_bool("LaneCentering", True, block=True)
    self.params.put_bool("LaneCenteringPauseOnSignal", False, block=True)
    self.params.put("LaneCenterOffset", 0.2, block=True)
    self.params.put("LaneCenteringE2EAuthority", 0.8, block=True)
    self.params.put("LaneCenteringStrength", 2.0, block=True)
    self.params.put_bool("LaneCentering", False, block=True)
    saved = {key: self.params.get(key) for key in CHILDREN}
    rows = self.rows()
    edits = {"LaneCenteringPauseOnSignal": "On", "LaneCenterOffset": "0.1", "LaneCenteringE2EAuthority": "70",
             "LaneCenteringStrength": "150"}
    for key, value in edits.items():
      with self.subTest(key=key):
        row = rows[key]
        request = FeatureSettingsRequest(key, row.source, value, vehicle_fingerprint=row.vehicle_fingerprint)
        self.assertFalse(self.owner.apply(request))
        if value != row.default_value:  # a "reset" must carry the real default
          self.assertFalse(self.owner.apply(request, reset_default=True))
        self.assertEqual(self.params.get(key), saved[key])
    for key in CHILDREN:
      with self.subTest(reset=key):
        row = self.rows()[key]
        self.assertTrue(self.owner.apply(FeatureSettingsRequest(key, row.source, row.default_value,
                                                                vehicle_fingerprint=row.vehicle_fingerprint), reset_default=True))
    self.assertFalse(self.params.get_bool("LaneCentering"))
    self.assertTrue(self.params.get_bool("LaneCenteringPauseOnSignal"))
    self.assertEqual((self.params.get("LaneCenterOffset"), self.params.get("LaneCenteringE2EAuthority"),
                      self.params.get("LaneCenteringStrength")), (0.0, 0.5, 1.5))

  def test_children_edit_normally_while_on(self):
    self.params.put_bool("LaneCentering", True, block=True)
    for key, expected in (("LaneCenterOffset", 0.1), ("LaneCenteringE2EAuthority", 0.8), ("LaneCenteringStrength", 2.25)):
      with self.subTest(key=key):
        row = self.rows()[key]
        self.assertTrue(self.owner.apply(FeatureSettingsRequest(key, row.source, CHILDREN[key],
                                                                vehicle_fingerprint=row.vehicle_fingerprint)))
        self.assertAlmostEqual(self.params.get(key), expected)

  def test_driving_greys_children_too(self):
    self.params.put_bool("LaneCentering", False, block=True)
    rows = self.rows(parked=False)
    for key in CHILDREN:
      with self.subTest(key=key):
        self.assertFalse(rows[key].available)


if __name__ == "__main__":
  unittest.main()
