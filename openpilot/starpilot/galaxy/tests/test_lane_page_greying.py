"""Galaxy Lane Centering page: rows below the master switch are greyed while it is off; Restore defaults still resets them."""
from typing import cast
from dataclasses import replace
from unittest import TestCase
from openpilot.starpilot.galaxy.defaults_recovery import DefaultsRecovery
from openpilot.starpilot.galaxy.settings import SettingsChanged
from openpilot.starpilot.galaxy.tests import test_settings as fixtures

CHILDREN = ("Pause on signal", "Lane offset", "Model path preference", "Lane Centering Strength")


class TestLanePageGreying(TestCase):
  def setUp(self):
    fixtures.SettingsGatewayTest.setUp(cast(fixtures.SettingsGatewayTest, self))
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.toyota.values import CAR
    cp = interfaces[CAR.TOYOTA_COROLLA_TSS2].get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
    self.context.value = replace(self.context.value, cp=cp)

  def page(self):
    return self.gateway.page("lane", self.session, self.generation)

  def test_rows_grey_out_and_explain_while_off(self):
    self.params.put_bool("LaneCentering", False, block=True)
    rows = {row["label"]: row for row in self.page()["rows"]}
    self.assertTrue(rows["Enable Lane Centering"]["available"])
    for label in CHILDREN:
      with self.subTest(label=label):
        self.assertFalse(rows[label]["available"])
        self.assertTrue(rows[label]["reason"].endswith("Turn on Lane Centering first"))
        self.assertGreater(len(rows[label]["reason"]), len("Turn on Lane Centering first"))  # description kept
    self.params.put_bool("LaneCentering", True, block=True)
    rows = {row["label"]: row for row in self.page()["rows"]}
    for label in CHILDREN:
      with self.subTest(on=label):
        self.assertTrue(rows[label]["available"])
        self.assertNotIn("Turn on Lane Centering", rows[label]["reason"])

  def test_greyed_rows_refuse_edits(self):
    self.params.put_bool("LaneCentering", False, block=True)
    page = self.page()
    for label, value in (("Lane offset", 0.1), ("Model path preference", 70), ("Lane Centering Strength", 150)):
      index = next(i for i, row in enumerate(page["rows"]) if row["label"] == label)
      with self.subTest(label=label), self.assertRaises(SettingsChanged):
        self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=value)

  def test_restore_defaults_resets_greyed_rows_without_turning_lane_centering_on(self):
    self.params.put_bool("LaneCentering", False, block=True)
    self.params.put_bool("LaneCenteringPauseOnSignal", False, block=True)
    self.params.put("LaneCenterOffset", 0.1, block=True)
    self.params.put("LaneCenteringE2EAuthority", 0.8, block=True)
    self.params.put("LaneCenteringStrength", 2.0, block=True)
    recovery = DefaultsRecovery(self.gateway)
    view = recovery.snapshot(self.session, self.generation)
    rows = [row for row in view["rows"] if row["page"] == "lane" and row["label"] in CHILDREN]
    self.assertEqual(sorted(row["label"] for row in rows), sorted(CHILDREN))
    self.assertTrue(all(row["resetAvailable"] and not row["available"] for row in rows))
    pending = recovery.preview(view["view"], [row["id"] for row in rows], self.session, self.generation)
    self.assertTrue(recovery.confirm(pending["intent"], self.session, self.generation)["saved"])
    self.assertFalse(self.params.get_bool("LaneCentering"))
    self.assertTrue(self.params.get_bool("LaneCenteringPauseOnSignal"))
    self.assertEqual((self.params.get("LaneCenterOffset"), self.params.get("LaneCenteringE2EAuthority"),
                      self.params.get("LaneCenteringStrength")), (0.0, 0.5, 1.5))
