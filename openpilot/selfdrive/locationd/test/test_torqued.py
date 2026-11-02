import numpy as np

from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR as HYUNDAI
from opendbc.car.structs import car
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.locationd.torqued import (TorqueEstimator, LAT_ACC_THRESHOLD, FACTOR_SANITY,
                                                   IONIQ_6_LAT_ACC_THRESHOLD, IONIQ_6_FACTOR_SANITY)


def _fill_line(est, slope, seed=0):
  rng = np.random.default_rng(seed)
  for (low, high), min_pts in zip(est.filtered_points.buckets.keys(),
                                  est.filtered_points.buckets_min_points.values(), strict=True):
    for _ in range(int(min_pts)):
      x = rng.uniform(low, high)
      est.filtered_points.add_point(x, slope * x + rng.normal(0.0, 0.02))
  keys = list(est.filtered_points.buckets)
  i = 0
  while len(est.filtered_points) < est.min_points_total:
    x = rng.uniform(*keys[i % len(keys)])
    est.filtered_points.add_point(x, slope * x + rng.normal(0.0, 0.02))
    i += 1


class TestTorqued(OpenpilotTestCase):
  def test_cal_percent(self):
    est = TorqueEstimator(car.CarParams())
    msg = est.get_msg()
    assert msg.lateralTorqueParameters.calPerc == 0

    for (low, high), min_pts in zip(est.filtered_points.buckets.keys(),
                                    est.filtered_points.buckets_min_points.values(), strict=True):
      for _ in range(int(min_pts)):
        est.filtered_points.add_point((low + high) / 2.0, 0.0)

    # enough bucket points, but not enough total points
    msg = est.get_msg()
    assert msg.lateralTorqueParameters.calPerc == (len(est.filtered_points) / est.min_points_total * 100 + 100) / 2

    # add enough points to bucket with most capacity
    key = list(est.filtered_points.buckets)[0]
    for _ in range(est.min_points_total - len(est.filtered_points)):
      est.filtered_points.add_point((key[0] + key[1]) / 2.0, 0.0)

    msg = est.get_msg()
    assert msg.lateralTorqueParameters.calPerc == 100

  def test_ioniq_6_learner_limits(self):
    cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    est = TorqueEstimator(cp)
    factor = cp.lateralTuning.torque.latAccelFactor
    assert est.lat_acc_threshold == IONIQ_6_LAT_ACC_THRESHOLD
    assert np.isclose(est.max_lataccel_factor, factor * (1 + IONIQ_6_FACTOR_SANITY))

    other = TorqueEstimator(interfaces[HYUNDAI.HYUNDAI_IONIQ_5].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_5))
    assert other.lat_acc_threshold == LAT_ACC_THRESHOLD
    assert other.factor_sanity == FACTOR_SANITY

  def test_ioniq_6_learned_slope_is_not_clamped(self):
    cp = interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)
    est = TorqueEstimator(cp)
    slope = cp.lateralTuning.torque.latAccelFactor * 1.45
    _fill_line(est, slope)
    captured = {}
    est.update_params = lambda params: captured.update(params)
    msg = est.get_msg()
    assert msg.lateralTorqueParameters.valid
    assert abs(captured["latAccelFactor"] - slope) < 0.1
