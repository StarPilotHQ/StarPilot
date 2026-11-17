"""Qualified Toyota stop-and-go ownership and manual latch override."""
from opendbc.car import structs
from opendbc.car.toyota.values import ToyotaFlags


def supported(cp):
  from opendbc.car.toyota import prius_longitudinal, rav4h_filter, advert_filter, legacy_stop_timer
  return bool(cp is not None and cp.brand == 'toyota' and cp.openpilotLongitudinalControl and
              not cp.deprecated.enableGasInterceptor and
              not cp.flags & ToyotaFlags.GAS_INTERCEPTOR and
              not cp.passive and not cp.dashcamOnly and not cp.notCar and
              len(cp.safetyConfigs) == 1 and cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.toyota and
              (prius_longitudinal.enabled(cp) or rav4h_filter.enabled(cp) or
               advert_filter.configured(cp) or legacy_stop_timer.enabled(cp)))


def eligible(cp):
  return supported(cp) and not cp.autoResumeSng
