import unittest
from pathlib import Path

from opendbc.car.values import PLATFORMS
from opendbc.car.tests.routes import non_tested_cars, routes, unrecorded_route_gaps
from opendbc.car.gm.values import CAR as GM
from opendbc.car.tesla.values import CAR as TESLA
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.volvo.values import CAR as VOLVO


class TestRoutes(unittest.TestCase):
  def test_test_route_present(self):
    tested_platforms = [r.car_model for r in routes]
    for platform in PLATFORMS.keys():
      with self.subTest(platform=platform):
        assert platform in set(tested_platforms) | set(non_tested_cars), \
          f"Missing test route for {platform}. Add a route to opendbc/car/tests/routes.py"

  def test_explicit_unrecorded_configuration_gaps(self):
    self.assertEqual(set(unrecorded_route_gaps), {
      GM.CHEVROLET_VOLT_CC, GM.CHEVROLET_MALIBU_HYBRID_CC, TOYOTA.TOYOTA_PRIUS_RETROFIT, TESLA.TESLA_MODEL_S_HW1,
      VOLVO.VOLVO_XC40_RECHARGE, VOLVO.VOLVO_S60_RECHARGE, VOLVO.POLESTAR_2, VOLVO.VOLVO_V60,
    })
    self.assertFalse(set(unrecorded_route_gaps) & {route.car_model for route in routes})
    self.assertTrue(set(unrecorded_route_gaps) <= set(non_tested_cars))
    car_root = Path(__file__).resolve().parents[1]
    for platform, source_test in unrecorded_route_gaps.items():
      with self.subTest(platform=platform):
        self.assertTrue((car_root / source_test).is_file())
