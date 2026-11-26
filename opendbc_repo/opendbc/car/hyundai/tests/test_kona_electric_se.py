from unittest import TestCase, mock

from opendbc.car import CanData, gen_empty_fingerprint, structs
from opendbc.car.car_helpers import get_car
from opendbc.car.hyundai.discovery import kona_electric_se_candidate
from opendbc.car.hyundai.fingerprints import FW_VERSIONS
from opendbc.car.hyundai.values import CAR

MODEL = CAR.HYUNDAI_KONA_EV_2023_NON_SCC
SE_VIN = 'KM8K23AG0PU000000'
SCC_VIN = 'KM8K53AG1PU000000'
SE_LAYOUT = ((0, 0x251, 8), (0, 0x371, 8), (0, 0x386, 8), (0, 0x394, 8), (0, 0x4F1, 4),
             (0, 0x592, 8), (1, 0x329, 8), (2, 0x340, 8))


def kona_electric_fw(include_radar=False):
  ecu = structs.CarParams.Ecu
  versions = FW_VERSIONS[CAR.HYUNDAI_KONA_EV_2022]
  firmware = [
    structs.CarParams.CarFw(ecu=ecu.eps, address=0x7D4, brand='hyundai',
                           fwVersion=b'\xf1\x00OSP MDPS C 1.00 1.02 56310K4261\x00 4OEPC102'),
    structs.CarParams.CarFw(ecu=ecu.fwdCamera, address=0x7C4, brand='hyundai',
                           fwVersion=b'\xf1\x00OSP LKA  AT USA LHD 1.00 1.04 99211-J9200 904'),
  ]
  if include_radar:
    firmware.append(structs.CarParams.CarFw(ecu=ecu.fwdRadar, address=0x7D0, brand='hyundai',
                                           fwVersion=versions[(ecu.fwdRadar, 0x7D0, None)][0]))
  return firmware


def interface(vin, layout=SE_LAYOUT, radar=False, forced_candidate=None):
  packets = [CanData(addr, bytes(length), bus) for bus, addr, length in layout]
  cached = structs.CarParams(brand='hyundai', carVin=vin, carFw=kona_electric_fw(radar), carFingerprint=forced_candidate or '')
  with mock.patch.dict('os.environ', clear=True):
    return get_car(lambda *args, **kwargs: [packets] if kwargs.get('wait_for_one') else [], mock.Mock(), mock.Mock(),
                   alpha_long_allowed=True, is_release=False, cached_params=cached, forced_candidate=forced_candidate)


class TestKonaElectricSE(TestCase):
  def test_exact_auto_and_manual_identity_cannot_transmit(self):
    for manual in (None, MODEL):
      with self.subTest(manual=manual):
        ci = interface(SE_VIN, forced_candidate=manual)
        cp = ci.CP
        self.assertEqual(cp.carFingerprint, MODEL)
        self.assertTrue(cp.dashcamOnly)
        self.assertFalse(cp.alphaLongitudinalAvailable)
        self.assertFalse(cp.openpilotLongitudinalControl)
        self.assertEqual([(s.safetyModel, s.safetyParam) for s in cp.safetyConfigs], [(structs.CarParams.SafetyModel.noOutput, 0)])
        cmd = structs.CarControl(enabled=True, latActive=True, longActive=True)
        self.assertFalse(ci.apply(cmd.as_reader(), 0)[1])

  def test_scc_firmware_still_owns_matching_sibling(self):
    ci = interface(SCC_VIN, (*SE_LAYOUT, (2, 0x420, 8), (2, 0x421, 8)), radar=True)
    self.assertEqual(ci.CP.carFingerprint, CAR.HYUNDAI_KONA_EV_2022)
    self.assertFalse(ci.CP.dashcamOnly)

  def test_evidence_is_conjunctive(self):
    fp = gen_empty_fingerprint()
    for bus, addr, length in SE_LAYOUT:
      fp[bus][addr] = length
    firmware = kona_electric_fw()
    self.assertEqual(kona_electric_se_candidate(SE_VIN, fp, firmware), MODEL)
    for vin in ('', '00000000000000000', SCC_VIN, SE_VIN[:9] + 'N' + SE_VIN[10:]):
      self.assertIsNone(kona_electric_se_candidate(vin, fp, firmware))
    self.assertIsNone(kona_electric_se_candidate(SE_VIN, fp, firmware[:1]))
    self.assertIsNone(kona_electric_se_candidate(SE_VIN, fp, kona_electric_fw(include_radar=True)))
    fp[2][0x421] = 8
    self.assertIsNone(kona_electric_se_candidate(SE_VIN, fp, firmware))
    del fp[2][0x421]
    fp[1][0x329] = 7
    self.assertIsNone(kona_electric_se_candidate(SE_VIN, fp, firmware))
