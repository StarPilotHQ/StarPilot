"""The shared API retains exact snapshot ownership and Toyota compatibility."""
import warnings
from types import SimpleNamespace

import pytest
from opendbc.car import structs
from opendbc.car.config_cache import cached_config
from opendbc.car.toyota.config_cache import cached_config as toyota_cached_config


def test_compatibility_import_is_same_api():
  assert cached_config is toyota_cached_config


def test_snapshot_arguments_and_nested_mutations():
  calls = []

  @cached_config
  def check(cp, word, *, marked_only=False):
    calls.append((cp.safetyConfigs[0].safetyParam, marked_only))
    return cp.safetyConfigs[0].safetyParam == word and (not marked_only or cp.alternativeExperience == 32)

  cp = structs.CarParams.new_message(safetyConfigs=[{'safetyModel': 'toyota', 'safetyParam': 73}])
  alias = cp.as_reader()
  assert check(cp, 73) and check(alias, 73)
  assert calls == [(73, False)]
  assert not check(cp, 73, marked_only=True)
  cp.alternativeExperience = 32
  assert check(alias, 73, marked_only=True)
  cp.safetyConfigs[0].safetyParam = 585
  assert not check(alias, 73) and check(cp, 585)
  assert not check.__wrapped__(cp, 73)


def test_complete_cp_key_and_builder_write_state():
  @cached_config
  def check(cp):
    return not cp.passive

  cp = structs.CarParams.new_message()
  with warnings.catch_warnings():
    warnings.simplefilter('error')
    assert check(cp) and check(cp.as_reader())
    cp.to_bytes()
  before = check.cache_info().misses
  cp.carVin = 'unrelated field still invalidates'
  assert check(cp)
  assert check.cache_info().misses == before + 1
  cp.passive = True
  assert not check(cp)


def test_bounded_size_and_unknown_object_fallback():
  @cached_config
  def check(cp):
    return cp.carVin == 'accepted'

  cp = structs.CarParams.new_message()
  for i in range(20):
    cp.carVin = str(i)
    assert not check(cp)
  assert check.cache_info().currsize == 8
  before = check.cache_info()
  cp.carVin = 'x' * (64 * 1024)
  assert not check(cp)
  assert check(SimpleNamespace(carVin='accepted'))
  assert check.cache_info() == before
  check.cache_clear()
  assert check.cache_info().currsize == 0


def test_original_exception_and_unhashable_argument_fallback():
  @cached_config
  def check(cp, values):
    if cp.passive:
      raise ValueError('refused')
    return values == [73]

  cp = structs.CarParams.new_message()
  assert check(cp, [73])
  assert check.cache_info().currsize == 0
  cp.passive = True
  with pytest.raises(ValueError, match='refused'):
    check(cp, [73])
