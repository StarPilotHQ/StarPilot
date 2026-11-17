"""Stinger compatibility entry point for the shared HKG stopping decision."""
from opendbc.car.hyundai.forecast_stop import forecast_should_stop as hkg_forecast_stop
from opendbc.car.hyundai.values import CAR


def forecast_should_stop(cp, speeds, time_indices, action_t):
  if cp.carFingerprint != CAR.KIA_STINGER_2022:
    return None
  return hkg_forecast_stop(cp, speeds, time_indices, action_t)
