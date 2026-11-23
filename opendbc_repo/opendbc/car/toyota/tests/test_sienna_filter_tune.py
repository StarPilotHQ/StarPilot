import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.toyota import legacy_advert_filter
from opendbc.car.toyota.carcontroller import get_long_tune
from opendbc.car.toyota.fingerprints import FW_VERSIONS
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, CarControllerParams


@pytest.mark.parametrize('vehicle,dlc,factory_acc,expected', (
  (CAR.TOYOTA_SIENNA, 4, False, ([2., 5.], [0.5, 0.25])),
  (CAR.TOYOTA_SIENNA, 4, True, ([0., 5., 35.], [3.6, 2.4, 1.5])),
  (CAR.TOYOTA_SIENNA, 8, False, ([0., 5., 35.], [3.6, 2.4, 1.5])),
  (CAR.TOYOTA_HIGHLANDER, 4, False, ([0., 5., 35.], [3.6, 2.4, 1.5])),
))
def test_sienna_filter_integral_tune(vehicle, dlc, factory_acc, expected):
  fp = gen_empty_fingerprint()
  fp[0][0x2FF] = dlc
  fw = [structs.CarParams.CarFw(ecu=ecu, address=addr, subAddress=sub or 0, fwVersion=versions[0], brand='toyota', bus=0)
        for (ecu, addr, sub), versions in FW_VERSIONS[vehicle].items()]
  cp = CarInterface.get_params(vehicle, fp, fw, True, False, False)
  assert cp.openpilotLongitudinalControl
  assert legacy_advert_filter.enabled(cp) == (dlc == 4)
  if factory_acc:
    assert legacy_advert_filter.prepare_stock(cp)
  tune = get_long_tune(cp, CarControllerParams(cp))
  assert tune._k_i == expected
  assert tune.k_f == 1.
