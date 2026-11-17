"""Static reuse must follow every mutable CP field and preserve caller state."""
from types import SimpleNamespace
import warnings

import pytest
from opendbc.car import structs
from opendbc.car.toyota import dsu_bypass, pedal_composite
from opendbc.car.toyota.config_cache import cached_config
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota import aol
from openpilot.starpilot.car.toyota.tests.test_pedal_parent_configurations import sources


CHECKS = (dsu_bypass.configured, pedal_composite.configured, aol.qualified)


def params():
  fp, fw = sources(CAR.TOYOTA_CAMRY, 'camera343_or_4CB8', 'catalog')
  fp[0][0x201] = 6
  cp = CarInterface.get_params(CAR.TOYOTA_CAMRY, fp, fw, True, False, False)
  cp.alternativeExperience = 32
  return cp


@pytest.mark.parametrize('check', CHECKS)
@pytest.mark.parametrize('field,value', [
  ('brand', 'other'), ('carFingerprint', 'unknown'), ('flags', 0), ('passive', True),
  ('dashcamOnly', True), ('notCar', True), ('pcmCruise', False), ('secOcRequired', True),
  ('openpilotLongitudinalControl', False), ('alternativeExperience', 1), ('autoResumeSng', True),
  ('alphaLongitudinalAvailable', True), ('steerControlType', 'angle'), ('carVin', 'changed'),
])
def test_mutable_builder_and_reader_alias(check, field, value):
  cp = params()
  alias = cp.as_reader()
  assert check(cp)
  before = check.cache_info().misses
  assert getattr(cp, field) != value
  setattr(cp, field, value)
  expected = check.__wrapped__(cp)
  assert check(cp) == check(alias) == expected
  assert check.cache_info().misses > before


@pytest.mark.parametrize('check', CHECKS)
def test_nested_native_configuration_and_keyword_mode(check):
  cp = params()
  assert check(cp)
  cp.safetyConfigs[0].safetyParam = 0
  assert not check(cp)
  cp = params()
  cp.safetyConfigs[0].safetyModel = 'noOutput'
  assert not check(cp)
  cp = params()
  cp.alternativeExperience = 0
  assert aol.qualified(cp) and not aol.qualified(cp, marked_only=True)


def test_snapshot_ownership_bounds_and_fallback():
  calls = []

  @cached_config
  def check(cp):
    calls.append(cp.carVin)
    return cp.carVin == 'accepted'

  cp = structs.CarParams.new_message(carVin='accepted')
  with warnings.catch_warnings():
    warnings.simplefilter('error')
    assert check(cp) and check(cp.as_reader())
    cp.to_bytes()
  assert calls == ['accepted']
  for i in range(20):
    cp.carVin = str(i)
    assert not check(cp)
  assert check.cache_info().currsize == 8
  before = check.cache_info()
  cp.carVin = 'x' * (64 * 1024)
  assert not check(cp)
  assert check.cache_info() == before
  assert check(SimpleNamespace(carVin='accepted'))
  assert check.cache_info() == before
