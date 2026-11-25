"""Recorded ECU identity for the EU 2019 Grand Cherokee, opendbc issue 4101."""
import pytest

from opendbc.car import structs
from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR, ChryslerFlags
from opendbc.car.fingerprints import FW_VERSIONS
from opendbc.car.fw_versions import match_fw_to_car

Ecu = structs.CarParams.Ecu
RECORDED = (
  (Ecu.combinationMeter, 0x742, b'68402710AB'),
  (Ecu.srs, 0x744, b'68355363AB'),
  (Ecu.abs, 0x747, b'68408639AC'),
  (Ecu.fwdRadar, 0x753, b'04672788AA'),
  (Ecu.eps, 0x75a, b'68417281AA'),
  (Ecu.engine, 0x7e0, b'68422857AD'),
  (Ecu.transmission, 0x7e1, b'68423907AB'),
)
ADDED = {b'68402710AB', b'68422857AD', b'68423907AB'}


def firmware():
  return [structs.CarParams.CarFw(ecu=ecu, address=address, fwVersion=version, brand='chrysler', bus=0)
          for ecu, address, version in RECORDED]


def test_recorded_eu_diesel_is_a_unique_exact_match():
  fw = firmware()
  assert match_fw_to_car(fw, '', allow_fuzzy=False, log=False) == (True, {CAR.JEEP_GRAND_CHEROKEE_2019})
  # The route also contains unrelated cross-brand responses at the engine's neighbor.
  fw.append(structs.CarParams.CarFw(ecu=Ecu.transmission, address=0x7e1, fwVersion=b'\xf1\0\0E\x01\x01', brand='hyundai', bus=1))
  assert match_fw_to_car(fw, '', allow_fuzzy=False, log=False) == (True, {CAR.JEEP_GRAND_CHEROKEE_2019})


@pytest.mark.parametrize('index', range(len(RECORDED)))
def test_unknown_firmware_still_refuses_exact_match(index):
  fw = firmware()
  fw[index].fwVersion = b'UNRECOGNIZED'
  assert match_fw_to_car(fw, '', allow_fuzzy=False, log=False) == (True, set())


def test_additions_are_scoped_to_the_existing_2019_jeep():
  for identity, ecus in FW_VERSIONS.items():
    found = {version for versions in ecus.values() for version in versions} & ADDED
    assert found == (ADDED if identity == CAR.JEEP_GRAND_CHEROKEE_2019 else set())


@pytest.mark.parametrize('alpha_long', (False, True))
def test_diesel_retains_stock_acc_pid_and_native_limits(alpha_long):
  cp = CarInterface.get_params(CAR.JEEP_GRAND_CHEROKEE_2019, {b: {} for b in range(8)}, firmware(), alpha_long, False, False)
  assert cp.pcmCruise and not cp.openpilotLongitudinalControl
  assert cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.chrysler
  assert cp.safetyConfigs[0].safetyParam == 0
  assert cp.minSteerSpeed == 17.5 and cp.lateralTuning.which() == 'pid'
  assert cp.flags & ChryslerFlags.HIGHER_MIN_STEERING_SPEED
  assert not cp.flags & ChryslerFlags.STEERING_SPEED_BYPASS
