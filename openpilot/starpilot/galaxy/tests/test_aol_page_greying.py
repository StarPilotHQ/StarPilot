"""Galaxy Always On Lateral page: switch first, Brake pause below greyed while AOL is off (Restore defaults still resets it),
steering-pause rows unaffected."""
from typing import cast
from unittest import TestCase
from opendbc.car import gen_empty_fingerprint
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR as HONDA
from openpilot.starpilot.galaxy.defaults_recovery import DefaultsRecovery
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsChanged
from openpilot.starpilot.galaxy.tests import test_settings as fixtures

PAUSE = ("Pause steering below", "Pause only while signaling", "Steering resume delay")


class TestAolPageGreying(TestCase):
  def setUp(self):
    fixtures.SettingsGatewayTest.setUp(cast(fixtures.SettingsGatewayTest, self))
    honda = CarInterface.get_params(HONDA.HONDA_CIVIC_BOSCH, gen_empty_fingerprint(), [], True, False, False)
    honda.safetyConfigs[-1].safetyParam |= 0x20
    self.context.value = AuthorityContext(True, honda, b"honda-aol-greying")

  def page(self):
    return self.gateway.page("aol", self.session, self.generation)

  def test_order_and_greying_follow_the_switch(self):
    self.params.put_bool("AlwaysOnLateral", False, block=True)
    rows = self.page()["rows"]
    self.assertEqual([row["label"] for row in rows], ["Enable Always On Lateral", "Brake pause below", *PAUSE])
    by_label = {row["label"]: row for row in rows}
    self.assertTrue(by_label["Enable Always On Lateral"]["available"])
    self.assertFalse(by_label["Brake pause below"]["available"])
    for label in ("Brake pause below", *PAUSE):
      with self.subTest(label=label):
        self.assertFalse(by_label[label]["available"])
        self.assertTrue(by_label[label]["reason"].endswith("Turn on Always On Lateral first"))
    self.params.put_bool("AlwaysOnLateral", True, block=True)
    by_label = {row["label"]: row for row in self.page()["rows"]}
    for label in ("Brake pause below", *PAUSE):
      with self.subTest(on=label):
        self.assertTrue(by_label[label]["available"])
        self.assertNotIn("Turn on", by_label[label]["reason"])

  def test_greyed_threshold_refuses_edits(self):
    self.params.put_bool("AlwaysOnLateral", False, block=True)
    page = self.page()
    index = next(i for i, row in enumerate(page["rows"]) if row["label"] == "Brake pause below")
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page["view"], index, 0, self.session, self.generation, value=20)

  def test_restore_defaults_resets_greyed_rows_without_turning_aol_on(self):
    self.params.put_bool("AlwaysOnLateral", False, block=True)
    self.params.put("AolBrakePauseSpeedMps", 5.0, block=True)
    recovery = DefaultsRecovery(self.gateway)
    self.params.put("PauseLateralSpeed", 15.0, block=True)
    self.params.put("LateralResumeDelay", 2.0, block=True)
    view = recovery.snapshot(self.session, self.generation)
    rows = [row for row in view["rows"] if row["page"] == "aol" and row["label"] in ("Brake pause below", *PAUSE)]
    self.assertEqual(sorted(row["label"] for row in rows), sorted(("Brake pause below", "Pause steering below", "Steering resume delay")))
    self.assertTrue(all(row["resetAvailable"] and not row["available"] for row in rows))
    pending = recovery.preview(view["view"], [row["id"] for row in rows], self.session, self.generation)
    self.assertTrue(recovery.confirm(pending["intent"], self.session, self.generation)["saved"])
    self.assertEqual(self.params.get("AolBrakePauseSpeedMps"), 0.0)
    self.assertEqual((self.params.get("PauseLateralSpeed"), self.params.get("LateralResumeDelay")), (0.0, 0.0))
    self.assertFalse(self.params.get_bool("AlwaysOnLateral"))
