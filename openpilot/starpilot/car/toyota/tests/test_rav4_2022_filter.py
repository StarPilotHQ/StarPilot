import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.starpilot.car.toyota.aol import radar_filter_qualified


def factory(alpha):
  fp = gen_empty_fingerprint()
  fp[0][0x2AA] = 8
  return CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2_2022, fp, [], alpha, False, False)


@pytest.mark.parametrize('alpha', [False, True])
def test_2022_filter_factory_preserves_current_tune(alpha):
  cp = factory(alpha)
  plain = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2_2022, gen_empty_fingerprint(), [], alpha, False, False)
  expected = plain.to_dict()
  expected['flags'] = (expected['flags'] & ~int(ToyotaFlags.DISABLE_RADAR)) | int(ToyotaFlags.RADAR_CAN_FILTER | ToyotaFlags.ARS510_RADAR)
  expected['radarUnavailable'] = False
  expected['safetyConfigs'][0]['safetyParam'] = 0x1049 if alpha else 0x1249
  assert cp.to_dict() == expected
  assert radar_filter_qualified(cp)


@pytest.mark.parametrize('field,value', [('passive', True), ('dashcamOnly', True), ('notCar', True),
                                        ('pcmCruise', False), ('alternativeExperience', 128),
                                        ('flags', 0), ('flags', 1 << 18)])
def test_2022_filter_aol_rejects_invalid_configuration(field, value):
  cp = factory(True)
  setattr(cp, field, value)
  assert not radar_filter_qualified(cp)


@pytest.mark.parametrize('word', [73, 585, 0x1048, 0x104A, 0x1449, 0x1649])
def test_2022_filter_aol_rejects_wrong_native_profile(word):
  cp = factory(True)
  cp.safetyConfigs[0].safetyParam = word
  assert not radar_filter_qualified(cp)
