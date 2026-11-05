from collections import defaultdict
import unittest

from opendbc.car.car_helpers import interfaces
from opendbc.car.docs import get_all_car_docs
from opendbc.car.docs_definitions import Cable, CarHarness, Column, PartType, Star, SupportType
from opendbc.car.gm.values import CAR as GM
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.values import PLATFORMS


class TestCarDocs(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.all_cars = get_all_car_docs()

  def test_duplicate_years(self):
    make_model_years = defaultdict(list)
    for car in self.all_cars:
      with self.subTest(car_docs_name=car.name):
        if car.support_type != SupportType.UPSTREAM:
          raise unittest.SkipTest

        make_model = (car.make, car.model)
        for year in car.year_list:
          assert year not in make_model_years[make_model], f"{car.name}: Duplicate model year"
          make_model_years[make_model].append(year)

  def test_missing_car_docs(self):
    all_car_docs_platforms = [name for name, config in PLATFORMS.items()]
    for platform in sorted(interfaces.keys()):
      with self.subTest(platform=platform):
        assert platform in all_car_docs_platforms, f"Platform: {platform} doesn't have a CarDocs entry"

  def test_naming_conventions(self):
    # Asserts market-standard car naming conventions by brand
    for car in self.all_cars:
      with self.subTest(car=car.name):
        tokens = car.model.lower().split(" ")
        if car.brand == "hyundai":
          assert "phev" not in tokens, "Use `Plug-in Hybrid`"
          assert "hev" not in tokens, "Use `Hybrid`"
          if "plug-in hybrid" in car.model.lower():
            assert "Plug-in Hybrid" in car.model, "Use correct capitalization"
          if car.make != "Kia":
            assert "ev" not in tokens, "Use `Electric`"
        elif car.brand == "toyota":
          if "rav4" in tokens:
            assert "RAV4" in car.model, "Use correct capitalization"

  def test_torque_star(self):
    # Asserts brand-specific assumptions around steering torque star
    for car in self.all_cars:
      with self.subTest(car=car.name):
        # honda sanity check, it's the definition of a no torque star
        if car.car_fingerprint in (HONDA.HONDA_ACCORD, HONDA.HONDA_CIVIC, HONDA.HONDA_CRV, HONDA.HONDA_ODYSSEY, HONDA.HONDA_ODYSSEY_TWN, HONDA.HONDA_PILOT):
          assert car.row[Column.STEERING_TORQUE] == Star.EMPTY, f"{car.name} has full torque star"
        elif car.brand in ("toyota", "hyundai"):
          assert car.row[Column.STEERING_TORQUE] != Star.EMPTY, f"{car.name} has no torque star"

  def test_year_format(self):
    for car in self.all_cars:
      with self.subTest(car=car.name):
        if car.name == "comma body":
          raise unittest.SkipTest

        if car.brand == "gm" and not car.merged and not car.years:
          # Community configurations may have no established model-year range.
          self.assertEqual(car.support_type, SupportType.COMMUNITY)
          self.assertEqual(car.year_list, [])
          continue

        assert car.years and car.year_list, f"Format years correctly: {car.name}"

  def test_volt_docs_support_and_hardware(self):
    expected = {
      GM.CHEVROLET_VOLT: (SupportType.COMMUNITY, CarHarness.obd_ii),
      GM.CHEVROLET_VOLT_ASCM: (SupportType.COMMUNITY, CarHarness.custom),
      GM.CHEVROLET_VOLT_2019: (SupportType.COMMUNITY, CarHarness.gmsdgm),
      GM.CHEVROLET_VOLT_CAMERA: (SupportType.COMMUNITY, CarHarness.gm),
      GM.CHEVROLET_VOLT_CC: (SupportType.COMMUNITY, CarHarness.custom),
    }
    docs = {car.car_fingerprint: car for car in self.all_cars if car.car_fingerprint in expected}
    self.assertEqual(set(docs), set(expected))
    for platform, (support, harness) in expected.items():
      with self.subTest(platform=platform):
        self.assertEqual(docs[platform].make, "Chevrolet")
        self.assertEqual(docs[platform].support_type, support)
        self.assertEqual([part for part in docs[platform].car_parts.parts if isinstance(part, CarHarness)], [harness])

  def test_every_gm_platform_has_minimal_docs(self):
    from opendbc.car.gm.values import GMCommunityCarDocs

    documented = {car.car_fingerprint for car in self.all_cars}
    for platform in GM:
      with self.subTest(platform=platform):
        self.assertIn(platform, documented)
        self.assertTrue(platform.config.car_docs)
        for doc in platform.config.car_docs:
          self.assertTrue(doc.make and doc.model and doc.package)
          if isinstance(doc, GMCommunityCarDocs):
            self.assertEqual(doc.support_type, SupportType.COMMUNITY)
            self.assertFalse(doc.merged)
            expected_harness = (CarHarness.gm if platform == GM.CHEVROLET_BOLT_ACC_2022_2023 else
                                CarHarness.custom if platform == GM.CHEVROLET_SILVERADO_CC else
                                CarHarness.custom if "ASCM Harness" in doc.name or "(CC)" in doc.name else
                                CarHarness.gmsdgm if "SDGM Harness" in doc.name else
                                CarHarness.gm if "Camera Harness" in doc.name else CarHarness.obd_ii)
            self.assertIn(expected_harness, doc.car_parts.parts)

  def test_harnesses(self):
    for car in self.all_cars:
      with self.subTest(car=car.name):
        if car.name == "comma body" or car.support_type != SupportType.UPSTREAM:
          raise unittest.SkipTest

        car_part_type = [p.part_type for p in car.car_parts.all_parts()]
        car_parts = list(car.car_parts.all_parts())
        assert len(car_parts) > 0, f"Need to specify car parts: {car.name}"
        assert car_part_type.count(PartType.connector) == 1, f"Need to specify one harness connector: {car.name}"
        assert car_part_type.count(PartType.mount) == 1, f"Need to specify one mount: {car.name}"
        assert Cable.obd_c_cable_2ft in car_parts, f"Need to specify an OBD-C cable (2ft): {car.name}"
