from unittest.mock import patch

import pytest

from opendbc.car.hyundai import canfd_owner as owner
from opendbc.car.hyundai.tests.test_ioniq6_handoff import ioniq6_profiles
from opendbc.car.hyundai.values import CAR
from openpilot.starpilot.aol.tests.test_canfd_stock_profiles import params


@pytest.fixture(params=('ioniq6', 'ev6', 'gv70'))
def cp(request):
  cache = getattr(owner, '_config_index_for_bytes', None)
  if cache is not None:
    cache.cache_clear()
  if request.param == 'ioniq6':
    _, _, result = ioniq6_profiles(alternate=True, bus_offset=4)
  else:
    result = params(CAR.KIA_EV6 if request.param == 'ev6' else CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN)
    result.openpilotLongitudinalControl, result.pcmCruise = True, False
    result.safetyConfigs = [{'safetyModel': 'noOutput', 'safetyParam': 0},
      {'safetyModel': 'hyundaiCanfd', 'safetyParam': 0x15 if request.param == 'ev6' else 0x2015}]
  return result


def test_same_complete_two_panda_configuration_is_selected_once(cp):
  before = cp.to_dict()
  with patch.object(owner, 'topology_index', wraps=owner.topology_index) as select:
    for _ in range(20):
      assert owner.config_index(cp) == 1
      assert owner.config_index(cp.as_reader()) == 1
    assert select.call_count == 1
  assert cp.to_dict() == before


@pytest.mark.parametrize('field', ('brand', 'carFingerprint', 'flags', 'passive', 'notCar', 'dashcamOnly',
  'steerControlType', 'openpilotLongitudinalControl', 'pcmCruise', 'alternativeExperience', 'auxiliary',
  'owner', 'safetyParam', 'safetyConfigs', 'carVin', 'carFw', 'mass'))
def test_mutations_revalidate_the_same_builder_against_original_selector(cp, field):
  original = getattr(owner, '_config_index_uncached', owner.config_index)
  assert owner.config_index(cp) == 1
  if field in ('passive', 'notCar', 'dashcamOnly', 'openpilotLongitudinalControl', 'pcmCruise'):
    setattr(cp, field, not getattr(cp, field))
  elif field == 'brand':
    cp.brand = 'other'
  elif field == 'carFingerprint':
    cp.carFingerprint = 'UNKNOWN'
  elif field == 'steerControlType':
    cp.steerControlType = 'angle'
  elif field in ('flags', 'alternativeExperience'):
    setattr(cp, field, int(getattr(cp, field)) ^ 1)
  elif field == 'auxiliary':
    cp.safetyConfigs[0].safetyParam = 1
  elif field == 'owner':
    cp.safetyConfigs[1].safetyModel = 'noOutput'
  elif field == 'safetyParam':
    cp.safetyConfigs[1].safetyParam ^= 1
  elif field == 'safetyConfigs':
    cp.safetyConfigs = []
  elif field == 'carVin':
    cp.carVin = 'CHANGED'
  elif field == 'carFw':
    cp.carFw = [{'ecu': 'eps', 'fwVersion': b'changed'}]
  else:
    cp.mass += 1
  with patch.object(owner, 'topology_index', wraps=owner.topology_index) as select:
    assert owner.config_index(cp) == original(cp)
    assert select.call_count == 2
  assert owner.config_index(cp.as_reader()) == original(cp)


def test_unknown_wrapper_and_cache_error_keep_original_selection(cp):
  class Proxy:
    def __getattr__(self, name):
      return getattr(cp, name)
  assert owner.config_index(Proxy()) == 1
  if hasattr(owner, '_config_index_for_bytes'):
    with patch.object(owner, '_config_index_for_bytes', side_effect=RuntimeError('cache unavailable')):
      assert owner.config_index(cp) == 1
      cp.safetyConfigs[1].safetyModel = 'noOutput'
      assert owner.config_index(cp) is None


def test_single_panda_keeps_original_selection_without_snapshot_or_cache(cp):
  cp.safetyConfigs = [cp.safetyConfigs[-1].to_dict()]
  original = getattr(owner, '_config_index_uncached', owner.config_index)
  cached = getattr(owner, '_config_index_for_bytes', None)
  def check():
    for model in ('hyundaiCanfd', 'hyundai', 'noOutput', 'silent'):
      cp.safetyConfigs[0].safetyModel = model
      for word in (0, 0x8895):
        cp.safetyConfigs[0].safetyParam = word
        assert owner.config_index(cp) == original(cp)
        assert owner.config_index(cp.as_reader()) == original(cp)
  if cached is None:
    check()
  else:
    with patch.object(owner, '_config_index_for_bytes', wraps=cached) as cache:
      check()
      assert cache.call_count == 0
