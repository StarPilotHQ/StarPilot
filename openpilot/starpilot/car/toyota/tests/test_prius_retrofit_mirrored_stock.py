"""Native Prius DSU traffic copied to camera CAN does not prove a bypass."""
import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota import dsu_bypass
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.starpilot.car.toyota.aol import qualified
from openpilot.starpilot.car.toyota.tests.test_dsu_bypass import acquired


@pytest.mark.parametrize('pedal', (False, True))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('camera', (b'8646F4702100' + bytes(4), b'8646F4705000' + bytes(4)))
def test_retrofit_native_dsu_mirror_remains_stock(pedal, alpha, camera):
  fw = acquired(CAR.TOYOTA_PRIUS_RETROFIT)
  for row in fw:
    if row.ecu == 'fwdCamera':
      row.fwVersion = camera
  fp = gen_empty_fingerprint()
  fp[0] = {0x343: 8, 0x4CB: 8}
  if pedal:
    fp[0][0x201] = 6
  fp[2] = dict(fp[0])
  cp = CarInterface.get_params(CAR.TOYOTA_PRIUS_RETROFIT, fp, fw, alpha, False, False)
  assert not dsu_bypass.hardware_supported(CAR.TOYOTA_PRIUS_RETROFIT, fp, fw)
  assert not cp.dashcamOnly and not cp.openpilotLongitudinalControl
  assert cp.flags == int(ToyotaFlags.HYBRID) and cp.safetyConfigs[0].safetyParam == 585
  assert not cp.alphaLongitudinalAvailable and qualified(cp)
