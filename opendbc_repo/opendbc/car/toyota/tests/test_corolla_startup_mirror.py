import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.toyota.fingerprints import FW_VERSIONS
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR


@pytest.mark.parametrize('mirrored,expected', ((True, 0x5358), (False, 0x8958)))
def test_corolla_camera_relay_mirror_is_not_a_dsu_bypass(mirrored, expected):
  car = CAR.TOYOTA_COROLLA
  fp = gen_empty_fingerprint()
  fp[0].update({0xAA: 8, 0x262: 5, 0x1D3: 8, 0x343: 8, 0x4CB: 8, 0x283: 7})
  fp[2].update(fp[0] if mirrored else {0x343: 8, 0x4CB: 8})
  fw = [structs.CarParams.CarFw(ecu=ecu, address=addr, subAddress=sub or 0, fwVersion=versions[0], brand='toyota', bus=0)
        for (ecu, addr, sub), versions in FW_VERSIONS[car].items()]

  cp = CarInterface.get_params(car, fp, fw, False, True, False)
  assert cp.safetyConfigs[0].safetyParam == expected
  assert cp.pcmCruise
  assert cp.openpilotLongitudinalControl is (not mirrored)
