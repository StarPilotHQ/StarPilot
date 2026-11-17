import pytest

from opendbc.car.hyundai.classic_long_aol import aol_word, independent_buttons_qualified
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.starpilot.aol.tests.test_classic_long_profiles import params
from openpilot.starpilot.aol.tests import test_classic_cancel_guards as shared


@pytest.mark.parametrize('identity,word', [(CAR.HYUNDAI_SONATA, 0xC04), (CAR.HYUNDAI_SONATA_HYBRID, 0xC06)])
@pytest.mark.parametrize('marked', [False, True])
def test_exact_gas_and_hybrid_profiles_keep_original_marker_boundary(identity, word, marked):
  cp = params(identity, lda=True)
  if marked:
    cp.safetyConfigs[0].safetyParam = word
    cp.alternativeExperience = 32
  before = cp.to_dict()
  assert independent_buttons_qualified(cp, marked_only=False)
  assert independent_buttons_qualified(cp) == marked
  assert cp.to_dict() == before


@pytest.mark.parametrize('defect', ['experience', 'wrong_gas_word', 'no_lda', 'extra_word', 'stock', 'pcm', 'passive',
                                    'fingerprint', 'identity_flags', 'release'])
def test_hybrid_added_scope_preserves_existing_profile_vetoes(defect):
  cp = params(CAR.HYUNDAI_SONATA_HYBRID, lda=True, release=defect == 'release')
  if defect != 'release':
    cp.safetyConfigs[0].safetyParam = aol_word(cp)
    cp.alternativeExperience = 32
  if defect == 'experience':
    cp.alternativeExperience = 0
  elif defect == 'wrong_gas_word':
    cp.safetyConfigs[0].safetyParam = 0xC04
  elif defect == 'no_lda':
    cp.flags = int(cp.flags) & ~int(HyundaiFlags.HAS_LDA_BUTTON)
    cp.safetyConfigs[0].safetyParam = 0x406
  elif defect == 'extra_word':
    cp.safetyConfigs[0].safetyParam |= 0x80
  elif defect == 'stock':
    cp.openpilotLongitudinalControl = False
  elif defect == 'pcm':
    cp.pcmCruise = True
  elif defect == 'passive':
    cp.passive = True
  elif defect == 'fingerprint':
    cp.carFingerprint = CAR.HYUNDAI_IONIQ_5
  elif defect == 'identity_flags':
    cp.flags = int(cp.flags) & ~int(HyundaiFlags.HYBRID)
  before = cp.to_dict()
  assert not independent_buttons_qualified(cp)
  assert cp.to_dict() == before


@pytest.mark.parametrize('case', ['retain', 'settings_off', 'default_buttons', 'configured_cancel', 'pause', 'not_latched',
  'no_raw_source', 'expired_original', 'old_native', 'native_lat_denied', 'native_long_allowed', 'no_long_request',
  'wrong_session', 'wrong_inventory', 'panda_rx', 'panda_fault', 'failed_feedback', 'changed_cp', 'producer',
  'wrong_epoch', 'nonfull', 'hard_fault', 'reverse', 'steer_fault', 'invalid_can', 'no_physical_cancel'])
def test_hybrid_uses_same_real_source_native_lease_and_fault_guards(case, monkeypatch):
  def marked():
    cp = params(CAR.HYUNDAI_SONATA_HYBRID, lda=True)
    cp.safetyConfigs[0].safetyParam = 0xC06
    cp.alternativeExperience = 32
    return cp
  monkeypatch.setattr(shared, 'marked', marked)
  shared.test_sonata_reuses_all_current_guard_fault_and_original_deadline_negatives(case, monkeypatch)
