"""Static admission remains exact; live authority is never cached."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from opendbc.car.gm import feature_capabilities as capabilities
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.values import CAR, is_bolt_pedal_profile
from opendbc.car.gm.interface import CarInterface
from openpilot.starpilot.aol.tests.test_gm import _configurations
from openpilot.starpilot.aol.tests.test_gm_held_native import parse, NOW


def uncached(cp):
  return bool(capabilities._healthy(cp) and cp.openpilotLongitudinalControl and not cp.pcmCruise and
              capabilities._longitudinal_owner.__wrapped__(cp))


def test_all_finalized_gm_profiles_keep_their_original_capability():
  for cp in _configurations():
    assert capabilities.longitudinal_supported(cp) == uncached(cp)
    assert capabilities.longitudinal_supported(cp.as_reader()) == uncached(cp)
  assert capabilities._longitudinal_owner.cache_info().currsize <= 8


@pytest.mark.parametrize('field,value', [('brand', 'hyundai'), ('carFingerprint', 'unknown'), ('flags', 0),
  ('passive', True), ('dashcamOnly', True), ('notCar', True), ('pcmCruise', True),
  ('openpilotLongitudinalControl', False), ('alphaLongitudinalAvailable', True), ('networkLocation', 'gateway'),
  ('safetyParam', 0), ('safetyModel', 'noOutput'), ('safetyConfigs', []), ('alternativeExperience', 1)])
def test_configuration_mutations_never_reuse_an_old_capability(field, value):
  cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
  alias = cp.as_reader()
  assert capabilities.longitudinal_supported(cp)
  if field in ('safetyParam', 'safetyModel'):
    setattr(cp.safetyConfigs[0], field, value)
  else:
    setattr(cp, field, value)
  assert capabilities.longitudinal_supported(cp) == uncached(cp)
  assert capabilities.longitudinal_supported(alias) == uncached(cp)


def test_other_brands_and_stock_longitudinal_skip_snapshot_work():
  cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
  for brand, stock in [('hyundai', False), ('honda', False), ('gm', True)]:
    cp.brand = brand
    cp.openpilotLongitudinalControl = not stock
    with patch.object(capabilities, '_longitudinal_owner', side_effect=AssertionError('unnecessary ownership snapshot')):
      assert not capabilities.longitudinal_supported(cp)


def test_carstate_qualifies_once_per_sample_and_rechecks_next_sample():
  cp = params(CAR.CHEVROLET_BOLT_CC_2018_2021, pedal=True, camera=True)
  ci = CarInterface(cp)
  shell = SimpleNamespace(CP=cp)
  for tick in range(40):
    stamp = NOW + tick * 10_000_000
    ci.update([(stamp, parse(shell, stamp, counter=tick % 4, packets_only=True))])
  with patch('opendbc.car.gm.carstate.is_bolt_pedal_profile', wraps=is_bolt_pedal_profile) as qualify:
    for tick, blocked in enumerate((False, True, False), 40):
      ci.CS.CP.notCar = blocked
      stamp = NOW + tick * 10_000_000
      before = len(qualify.call_args_list)
      ci.update([(stamp, parse(shell, stamp, counter=tick % 4, packets_only=True))])
      current = [call for call in qualify.call_args_list[before:] if not call.kwargs.get('stock_only', False)]
      assert len(current) == 1
      assert is_bolt_pedal_profile(ci.CS.CP) is not blocked
