"""Static Bolt classification stays exact after saved/manual profile changes."""
import pytest
from opendbc.car.gm.values import CAR, _bolt_pedal_profile, is_bolt_pedal_profile
from opendbc.car.gm.tests.test_bolt_pedal import params
from openpilot.starpilot.aol.tests.test_gm import _configurations


def test_all_existing_gm_profiles_match_without_cache():
  for cp in _configurations():
    for stock in (False, True):
      expected = _bolt_pedal_profile.__wrapped__(cp, stock_only=stock)
      assert is_bolt_pedal_profile(cp, stock_only=stock) == expected
      assert is_bolt_pedal_profile(cp.as_reader(), stock_only=stock) == expected
  assert _bolt_pedal_profile.cache_info().currsize <= 8


@pytest.mark.parametrize('field,value', [('brand', 'hyundai'), ('carFingerprint', 'unknown'), ('flags', 0),
  ('passive', True), ('dashcamOnly', True), ('notCar', True), ('pcmCruise', True),
  ('openpilotLongitudinalControl', False), ('alphaLongitudinalAvailable', True), ('networkLocation', 'gateway'),
  ('safetyParam', 0), ('safetyModel', 'noOutput'), ('safetyConfigs', [])])
def test_builder_and_reader_alias_requalify_after_mutation(field, value):
  cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
  alias = cp.as_reader()
  assert is_bolt_pedal_profile(cp)
  if field in ('safetyParam', 'safetyModel'):
    setattr(cp.safetyConfigs[0], field, value)
  else:
    setattr(cp, field, value)
  assert not _bolt_pedal_profile.__wrapped__(cp)
  assert not is_bolt_pedal_profile(cp)
  assert not is_bolt_pedal_profile(alias)


def test_other_brands_and_non_bolt_profiles_skip_serialization():
  cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
  for field, value in [('brand', 'hyundai'), ('brand', 'honda'), ('carFingerprint', 'unknown')]:
    cp.brand = 'gm'
    setattr(cp, field, value)
    previous = _bolt_pedal_profile.cache_info()
    assert not is_bolt_pedal_profile(cp)
    assert _bolt_pedal_profile.cache_info() == previous
