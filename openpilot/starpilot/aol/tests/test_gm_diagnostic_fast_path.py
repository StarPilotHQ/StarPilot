"""The cheap rejection must preserve the original live qualification policy."""
from unittest.mock import patch
from types import SimpleNamespace

import pytest
from opendbc.car.gm.aol import qualified_gm
from opendbc.car.gm.cancel_remap import aol_enabled
from opendbc.car.gm.values import CAR, is_volt_sdgm_profile, is_bolt_present_no_acc_pedal_profile
from openpilot.starpilot.aol.panda_faults import _diagnostic_mask, blocking_fault_list, blocking_fault_mask
from openpilot.starpilot.aol.tests.test_gm_diagnostic_faults import owner


def original_mask(cp):
  try:
    if cp.brand != 'gm' or not qualified_gm(cp) or not aol_enabled(cp) or len(cp.safetyConfigs) != 1:
      return 0
    word = int(cp.safetyConfigs[0].safetyParam)
    if (cp.carFingerprint == CAR.CHEVROLET_VOLT_2019 and word == 0x1005 and
        is_volt_sdgm_profile(cp, longitudinal=False) and cp.pcmCruise and not cp.openpilotLongitudinalControl):
      return 1 << 18
    if (cp.carFingerprint == CAR.CHEVROLET_BOLT_CC_2018_2021 and word == 0x9D and
        is_bolt_present_no_acc_pedal_profile(cp) and cp.openpilotLongitudinalControl and not cp.pcmCruise):
      return 12
  except (AttributeError, TypeError, ValueError, OverflowError, IndexError):
    pass
  return 0


@pytest.mark.parametrize('label', ['volt', 'bolt'])
def test_every_safety_word_matches_prechange_policy(label):
  cp, _ = owner(label)
  for word in range(65536):
    cp.safetyConfigs[0].safetyParam = word
    assert _diagnostic_mask(cp) == original_mask(cp), (label, word)


def test_recorded_long_volt_skips_expensive_policy_but_eligible_configs_stay_live():
  from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
  cp = sdgm_params()
  assert cp.safetyConfigs[0].safetyParam == 0x5007
  with patch('opendbc.car.gm.aol.qualified_gm', side_effect=AssertionError('unnecessary qualification')):
    assert _diagnostic_mask(cp) == 0
  for label in ('volt', 'bolt'):
    cp, _ = owner(label)
    with patch('opendbc.car.gm.aol.qualified_gm', wraps=qualified_gm) as checked:
      assert _diagnostic_mask(cp) == (1 << 18 if label == 'volt' else 12)
      checked.assert_called_once_with(cp)
      cp.alternativeExperience = 0
      assert _diagnostic_mask(cp) == 0


@pytest.mark.parametrize('faults', [None, False, 0, object()])
def test_invalid_fault_lists_fail_closed(faults):
  cp, _ = owner('bolt')
  assert blocking_fault_list(faults, cp)


@pytest.mark.parametrize('label', ['volt', 'bolt'])
@pytest.mark.parametrize('bit', range(32))
def test_each_bit_and_mixed_fault_matches_original(label, bit):
  cp, _ = owner(label)
  allowed = original_mask(cp)
  for mask in (1 << bit, (1 << bit) | allowed):
    assert blocking_fault_mask(mask, cp) == bool(mask & ~allowed)


def test_full_startup_matrix_keeps_original_classification():
  from openpilot.starpilot.aol.tests.test_gm import configurations
  for cp in configurations():
    for ae in (0, 32):
      cp.alternativeExperience = ae
      assert _diagnostic_mask(cp) == original_mask(cp)


@pytest.mark.parametrize('label', ['volt', 'bolt'])
@pytest.mark.parametrize('field', ['passive', 'dashcamOnly', 'notCar', 'pcmCruise',
                                  'openpilotLongitudinalControl', 'alternativeExperience', 'flags'])
def test_mutable_configuration_is_always_requalified(label, field):
  cp, _ = owner(label)
  assert _diagnostic_mask(cp)
  setattr(cp, field, not getattr(cp, field) if field not in ('alternativeExperience', 'flags') else 0)
  assert _diagnostic_mask(cp) == original_mask(cp)


@pytest.mark.parametrize('cp', [None, {}, SimpleNamespace(brand='gm'),
                              SimpleNamespace(brand='gm', safetyConfigs=None),
                              SimpleNamespace(brand='gm', safetyConfigs=[])])
def test_malformed_configuration_matches_original(cp):
  assert _diagnostic_mask(cp) == original_mask(cp) == 0


@pytest.mark.parametrize('field', ['latAccelFactor', 'latAccelOffset', 'friction'])
def test_malformed_torque_tuning_does_not_borrow_exemption(field):
  cp, _ = owner('volt')
  cp.lateralTuning.init('torque')
  cp.lateralTuning.torque.latAccelFactor = 1.
  setattr(cp.lateralTuning.torque, field, float('nan'))
  assert _diagnostic_mask(cp) == original_mask(cp) == 0


def test_empty_fault_reports_skip_all_vehicle_diagnostic_work():
  cp, _ = owner('bolt')
  with patch('openpilot.starpilot.aol.panda_faults._diagnostic_mask', side_effect=AssertionError('empty fault report')):
    assert not blocking_fault_mask(0, cp)
    for faults in ([], (), iter(())):
      assert not blocking_fault_list(faults, cp)
