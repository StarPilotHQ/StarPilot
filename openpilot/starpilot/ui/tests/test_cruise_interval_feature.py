"""Parked exact-source edits for host-only cruise intervals."""

from dataclasses import replace
import tempfile
import unittest

from openpilot.common.params import Params
from opendbc.car.structs import car
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsChanged, SettingsGateway
from openpilot.starpilot.longitudinal.cruise_intervals import CruiseIntervals, read_cruise_intervals
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, row_change


class Context:
  def __init__(self, cp):
    self.value = AuthorityContext(True, cp, b"cp-source")

  def sample(self):
    return self.value


class CruiseIntervalFeatureTests(unittest.TestCase):
  def setUp(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    self.params = Params(temp.name)
    self.cp = car.CarParams.new_message(carFingerprint="HYUNDAI_IONIQ_6", openpilotLongitudinalControl=True,
                              pcmCruise=False, notCar=False, passive=False, dashcamOnly=False)
    self.context = Context(self.cp)
    self.owner = FeatureSettingsOwner(self.params, lambda group: self.context.value.parked and group == "long",
                                      vehicle_fingerprint=lambda: self.context.value.cp.carFingerprint,
                                      vehicle_params=lambda: self.context.value.cp, show_cruise_intervals=True)
    self.gateway = SettingsGateway(self.params, self.context, clock=lambda: 100.0)

  def rows(self):
    state = self.owner.snapshot(FeaturePage.WHEEL, parked=self.context.value.parked, system_long=True,
                                lateral_context=False, metric=False)
    return {row.key: row for row in state.rows if row.key in ("QOLLongitudinal", "CustomCruise", "CustomCruiseLong")}

  @staticmethod
  def change(row, direction=1):
    request = row_change(row, direction)
    assert request is not None
    return replace(request, confirmation=True)

  def test_galaxy_direct_intervals_without_retired_master(self):
    self.params.put("CustomCruise", 5.0, block=True)
    self.params.put("CustomCruiseLong", 1.0, block=True)
    page = self.gateway.page("wheel", "session", b"generation")
    rows = {row["label"]: (index, row) for index, row in enumerate(page["rows"])}
    self.assertNotIn("Custom cruise intervals", rows)
    self.assertTrue(rows["Short press"][1]["available"])
    self.assertTrue(rows["Hold"][1]["available"])
    intent = self.gateway.preview(page["view"], rows["Short press"][0], 1, "session", b"generation")
    self.assertTrue(self.gateway.confirm(intent["intent"], "session", b"generation"))
    self.assertEqual(read_cruise_intervals(self.params, self.cp), CruiseIntervals(6, 1))
    self.assertIsNone(self.params.get("QOLLongitudinal"))
    with self.assertRaises(SettingsChanged):
      self.gateway.preview(page["view"], rows["Short press"][0], 1, "session", b"generation")

  def test_pcm_system_longitudinal_allows_intervals_and_rejects_stale_requests(self):
    child = self.change(self.rows()["CustomCruise"])
    self.cp.pcmCruise = True
    self.assertFalse(self.owner.apply(child))
    self.assertTrue(all(row.available for row in self.rows().values()))
    self.assertTrue(self.owner.apply(self.change(self.rows()["CustomCruise"])))
    child = self.change(self.rows()["CustomCruise"])
    self.cp.pcmCruise = False
    self.cp.carFingerprint = "changed"
    self.assertFalse(self.owner.apply(child))
    self.assertEqual(read_cruise_intervals(self.params, self.cp), CruiseIntervals(2, 5))

  def test_pcm_stock_longitudinal_does_not_allow_interval_edits(self):
    child = self.change(self.rows()["CustomCruise"])
    self.cp.pcmCruise = True
    self.cp.openpilotLongitudinalControl = False
    self.assertFalse(self.owner.apply(child))
    self.assertFalse(any(row.available for row in self.rows().values()))

  def test_stale_units_source_and_parked_rejected(self):
    short = self.change(self.rows()["CustomCruise"])
    self.params.put_bool("IsMetric", True, block=True)
    self.assertFalse(self.owner.apply(short))
    metric = self.rows()["CustomCruise"]
    self.assertEqual(metric.unit, "km/h")
    self.assertTrue(self.owner.apply(self.change(metric)))
    stale = self.change(self.rows()["CustomCruise"])
    self.params.put("CustomCruise", 4.0, block=True)
    self.assertFalse(self.owner.apply(stale))
    request = self.change(self.rows()["CustomCruise"])
    self.context.value = AuthorityContext(False, self.cp, b"cp-source")
    self.assertFalse(self.owner.apply(request))

  def test_numeric_bounds_rejected(self):
    short = self.change(self.rows()["CustomCruise"])
    self.assertFalse(self.owner.apply(replace(short, value="151")))
    self.assertFalse(self.owner.apply(replace(short, value="nan")))
    self.assertEqual(read_cruise_intervals(self.params, self.cp), CruiseIntervals())
