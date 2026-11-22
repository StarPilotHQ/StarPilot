import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota.interface import CarInterface, toyota_auto_hold_supported
from opendbc.car.toyota.values import CAR, ToyotaFlags
from opendbc.car.toyota.rav4h_filter import configured, hardware_supported
from openpilot.starpilot.car.toyota.aol import qualified
from openpilot.starpilot.car.toyota.tests.test_rav4h_caller_native import firmware

MODELS=(CAR.TOYOTA_HIGHLANDER,CAR.TOYOTA_SIENNA,CAR.LEXUS_NX,CAR.LEXUS_RX)


@pytest.mark.parametrize('candidate',MODELS)
def test_sdsu_factory_changes_only_owned_hardware_fields(candidate):
  fw=firmware(candidate)
  fp=gen_empty_fingerprint()
  baseline=CarInterface.get_params(candidate,fp,fw,False,False,False)
  fp[0][0x2FF]=8
  cp=CarInterface.get_params(candidate,fp,fw,False,False,False)
  expected=baseline.to_dict()
  expected['flags']|=int(ToyotaFlags.LONG_FILTER)
  expected['openpilotLongitudinalControl']=True
  expected['autoResumeSng']=candidate in (CAR.TOYOTA_HIGHLANDER,CAR.TOYOTA_SIENNA)
  expected['safetyConfigs'][0]['safetyParam']=0x9049
  if candidate == CAR.TOYOTA_SIENNA:
    expected['longitudinalActuatorDelay'] = pytest.approx(.5)
  elif candidate == CAR.TOYOTA_HIGHLANDER:
    expected['minEnableSpeed'] = -1.
    if not cp.flags & ToyotaFlags.HYBRID:
      expected['longitudinalActuatorDelay'] = pytest.approx(.4)
  assert cp.to_dict()==expected
  assert configured(cp) and qualified(cp)
  assert not toyota_auto_hold_supported(cp)


@pytest.mark.parametrize('candidate',MODELS)
@pytest.mark.parametrize('bad',['length','wrong_bus','pedal_mix','radar_filter_mix','malformed_camera_copy','unknown_firmware'])
def test_sdsu_rejects_unqualified_topologies(candidate,bad):
  fp=gen_empty_fingerprint()
  fp[0][0x2FF]=8
  fw=firmware(candidate)
  if bad=='length':
    fp[0][0x2FF]=7
  elif bad=='wrong_bus':
    fp[1][0x2FF]=8
  elif bad=='pedal_mix':
    fp[0][0x201]=6
  elif bad=='radar_filter_mix':
    fp[0][0x2AA]=8
  elif bad=='malformed_camera_copy':
    fp[2][0x343]=7
  elif bad=='unknown_firmware':
    fw=[]
  assert not hardware_supported(candidate,fp,fw)
