import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.radar_filter import PROFILES, hardware_supported, configured
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.starpilot.car.toyota.tests.test_pedal_caller_native import firmware
from openpilot.starpilot.car.toyota.aol import qualified


@pytest.mark.parametrize('candidate',list(PROFILES))
@pytest.mark.parametrize('alpha',[False,True])
@pytest.mark.parametrize('forwarded',[False,True])
def test_four_byte_filter_matches_existing_canonical_owner(candidate,alpha,forwarded):
  fw=firmware(candidate)
  canonical=gen_empty_fingerprint()
  canonical[0][0x2AA]=8
  observed=gen_empty_fingerprint()
  observed[0][0x2FF]=4
  if forwarded:
    observed[2][0x2FF]=4
  original=CarInterface.get_params(candidate,canonical,fw,alpha,False,False)
  cp=CarInterface.get_params(candidate,observed,fw,alpha,False,False)
  assert not original.flags & ToyotaFlags.FILTER_BUTTONS
  expected_buttons = int(ToyotaFlags.FILTER_BUTTONS) if original.steerControlType == structs.CarParams.SteerControlType.torque else 0
  assert cp.flags == original.flags | expected_buttons
  observed_params = cp.to_dict()
  observed_params['flags'] = original.flags
  assert observed_params == original.to_dict()
  assert configured(cp) and qualified(cp) and hardware_supported(candidate,observed)
  assert not cp.flags & ToyotaFlags.DISABLE_RADAR


@pytest.mark.parametrize('candidate',list(PROFILES))
@pytest.mark.parametrize('bad',['short','status8','wrong_bus','bad_mirror','pedal','mixed','echo_only'])
def test_advertisement_cannot_claim_other_hardware(candidate,bad):
  fp=gen_empty_fingerprint()
  fp[0][0x2FF]=4
  if bad=='short':
    fp[0][0x2FF]=3
  elif bad=='status8':
    fp[0][0x2FF]=8
  elif bad=='wrong_bus':
    fp[1][0x2FF]=4
  elif bad=='bad_mirror':
    fp[2][0x2FF]=8
  elif bad=='pedal':
    fp[0][0x201]=6
  elif bad=='mixed':
    fp[0][0x2AA]=8
  elif bad=='echo_only':
    fp[128]={0x2FF:fp[0].pop(0x2FF)}
  assert not hardware_supported(candidate,fp)


@pytest.mark.parametrize('candidate',[CAR.TOYOTA_RAV4H,CAR.TOYOTA_COROLLA,CAR.TOYOTA_PRIUS,CAR.TOYOTA_RAV4_PRIME])
def test_four_byte_advertisement_does_not_expand_legacy_or_secoc(candidate):
  fp=gen_empty_fingerprint()
  fp[0][0x2FF]=4
  assert not hardware_supported(candidate,fp)
