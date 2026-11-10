from unittest.mock import patch

import pytest

from opendbc.car.hyundai import blended_longitudinal as mixed
from opendbc.car.hyundai.tests.test_blended_stock_aol import params


@pytest.fixture(params=(False, True))
def cp(request):
  cache = getattr(mixed, '_mixed_startup_for_bytes', None)
  if cache is not None:
    cache.cache_clear()
  return params(request.param)


def test_same_static_configuration_is_qualified_once(cp):
  before = cp.to_dict()
  with patch.object(mixed, 'alpha_eligible', wraps=mixed.alpha_eligible) as evaluate:
    for _ in range(20):
      assert mixed.mixed_startup_qualified(cp)
      assert mixed.mixed_startup_qualified(cp.as_reader())
    assert evaluate.call_count == 1
  assert cp.to_dict() == before


@pytest.mark.parametrize('field', ('flags', 'brand', 'carFingerprint', 'passive', 'dashcamOnly', 'notCar',
                                 'steerControlType', 'alternativeExperience', 'safetyConfigs', 'safetyModel',
                                 'safetyParam', 'pcmCruise', 'openpilotLongitudinalControl', 'mass', 'carVin', 'carFw'))
def test_changes_on_same_builder_are_requalified(cp, field):
  uncached = getattr(mixed, '_mixed_startup_qualified_uncached', mixed.mixed_startup_qualified)
  assert mixed.mixed_startup_qualified(cp)
  if field in ('passive', 'dashcamOnly', 'notCar', 'pcmCruise', 'openpilotLongitudinalControl'):
    setattr(cp, field, not getattr(cp, field))
  elif field in ('flags', 'alternativeExperience'):
    setattr(cp, field, int(getattr(cp, field)) ^ 1)
  elif field == 'safetyConfigs':
    cp.safetyConfigs = []
  elif field == 'safetyParam':
    cp.safetyConfigs[0].safetyParam ^= 1
  elif field == 'safetyModel':
    cp.safetyConfigs[0].safetyModel = 'noOutput'
  elif field == 'steerControlType':
    cp.steerControlType = 'angle'
  elif field == 'mass':
    cp.mass += 1
  elif field == 'carFw':
    cp.carFw = [{'ecu': 'eps', 'fwVersion': b'changed'}]
  else:
    setattr(cp, field, 'changed')
  with patch.object(mixed, 'alpha_eligible', wraps=mixed.alpha_eligible) as evaluate:
    for release in (False, True):
      for marked in (False, True):
        assert mixed.mixed_startup_qualified(cp, is_release=release, allow_marked=marked) == uncached(cp, is_release=release, allow_marked=marked)
    assert evaluate.call_count > 0


@pytest.mark.parametrize('longitudinal', (False, True))
@pytest.mark.parametrize('marked', (False, True))
def test_stock_long_and_aol_profiles_retain_release_restriction(cp, longitudinal, marked):
  cp.openpilotLongitudinalControl = longitudinal
  cp.pcmCruise = not longitudinal
  if longitudinal:
    cp.safetyConfigs[0].safetyParam |= 4
  cp.alternativeExperience = 32 if marked else 0
  assert mixed.mixed_startup_qualified(cp, allow_marked=marked)
  assert not mixed.mixed_startup_qualified(cp, is_release=True, allow_marked=marked)
  if marked:
    assert not mixed.mixed_startup_qualified(cp)


def test_proxy_and_snapshot_failure_retain_original_validation(cp):
  class Proxy:
    def __getattr__(self, name):
      return getattr(cp, name)
  assert mixed.mixed_startup_qualified(Proxy())
  cached = getattr(mixed, '_mixed_startup_for_bytes', None)
  if cached is not None:
    with patch.object(mixed, '_mixed_startup_for_bytes', side_effect=RuntimeError('cache unavailable')):
      assert mixed.mixed_startup_qualified(cp)
      cp.passive = True
      assert not mixed.mixed_startup_qualified(cp)
