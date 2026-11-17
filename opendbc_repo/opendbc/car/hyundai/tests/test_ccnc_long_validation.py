from unittest.mock import patch

import pytest

from opendbc.car.hyundai import ev9_longitudinal, ioniq5pe_long_profile
from opendbc.car.hyundai.tests.test_stock_angle_aol import source_profile
from opendbc.car.hyundai.values import CAR, HyundaiFlags


def profile(identity):
  module = ioniq5pe_long_profile if identity == CAR.HYUNDAI_IONIQ_5_PE else ev9_longitudinal
  cp = module.candidate(source_profile(identity), enabled=True, is_release=False)
  return module, cp


@pytest.mark.parametrize('identity', (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9))
@pytest.mark.parametrize('reader', (False, True))
def test_canonical_long_validation_does_not_clone_carparams(identity, reader):
  module, cp = profile(identity)
  cp.alternativeExperience = 32
  value = cp.as_reader() if reader else cp
  before = cp.to_dict()
  with patch.object(module, 'copy_cp', side_effect=AssertionError('CYCLIC_LONG_QUALIFICATION_CLONED_CP')):
    assert module.qualified(value)
    cp.passive = True
    assert not module.qualified(value)
    cp.passive = False
    assert module.qualified(value)
  assert cp.to_dict() == before


@pytest.mark.parametrize('identity', (CAR.HYUNDAI_IONIQ_5_PE, CAR.KIA_EV9))
@pytest.mark.parametrize('mutation', ('passive','dashcam','notcar','brand','fuel','long','pcm','experience','available','word','model','slots'))
def test_every_admission_field_is_still_checked_on_every_call(identity, mutation):
  module, cp = profile(identity)
  assert module.qualified(cp)
  if mutation == 'passive':
    cp.passive = True
  elif mutation == 'dashcam':
    cp.dashcamOnly = True
  elif mutation == 'notcar':
    cp.notCar = True
  elif mutation == 'brand':
    cp.brand = 'toyota'
  elif mutation == 'fuel':
    cp.flags = int(cp.flags | HyundaiFlags.HYBRID)
  elif mutation == 'long':
    cp.openpilotLongitudinalControl = False
  elif mutation == 'pcm':
    cp.pcmCruise = True
  elif mutation == 'experience':
    cp.alternativeExperience = 33
  elif mutation == 'available':
    cp.alphaLongitudinalAvailable = False
  elif mutation == 'word':
    cp.safetyConfigs[0].safetyParam ^= 4
  elif mutation == 'model':
    cp.safetyConfigs[0].safetyModel = 'noOutput'
  else:
    cp.safetyConfigs = [cp.safetyConfigs[0].to_dict(), cp.safetyConfigs[0].to_dict()]
  assert not module.qualified(cp)
  assert not module.qualified(cp.as_reader())


@pytest.mark.parametrize('field,value', [('startingState',False),('startAccel',.5),('vEgoStarting',.5),('vEgoStopping',.4),
                                         ('stoppingDecelRate',.5),('startAccel',float('nan')),('startAccel',float('inf'))])
def test_pe_frozen_longitudinal_values_still_refuse_mutation(field, value):
  module, cp = profile(CAR.HYUNDAI_IONIQ_5_PE)
  assert module.qualified(cp)
  setattr(cp.deprecated, field, value)
  assert not module.qualified(cp)
  assert not module.qualified(cp.as_reader())
