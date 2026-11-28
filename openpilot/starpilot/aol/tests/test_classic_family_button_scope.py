import pytest

from opendbc.car import structs
from opendbc.car.hyundai.values import CAR

from opendbc.car.hyundai.classic_long_aol import CLASSIC_LONG_IDS, aol_word, independent_buttons_qualified
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.tests.test_classic_long_profiles import params
from openpilot.starpilot.car.hyundai.classic_long_intent import ClassicLongCardIntent


@pytest.mark.parametrize('identity', sorted(CLASSIC_LONG_IDS, key=str))
@pytest.mark.parametrize('lda', [False, True])
@pytest.mark.parametrize('release', [False, True])
def test_each_actual_factory_preserves_marker_lda_and_release_boundary(identity, lda, release):
  cp = params(identity, lda=lda, release=release)
  if identity == CAR.HYUNDAI_I30_2026:
    assert cp.dashcamOnly and not cp.openpilotLongitudinalControl
    assert cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput
    assert not independent_buttons_qualified(cp, marked_only=False)
    return
  before = cp.to_dict()
  assert independent_buttons_qualified(cp, marked_only=False) == (lda and not release)
  assert not independent_buttons_qualified(cp)
  assert cp.to_dict() == before
  cp.safetyConfigs[0].safetyParam = aol_word(cp)
  cp.alternativeExperience = 32
  before = cp.to_dict()
  assert independent_buttons_qualified(cp) == (lda and not release)
  assert cp.to_dict() == before


@pytest.mark.parametrize('identity', sorted(CLASSIC_LONG_IDS, key=str))
@pytest.mark.parametrize('lkas,main', [(9, 0), (0, 0), (0, 9), (9, 9)])
def test_each_family_profile_keeps_exact_saved_mapping(identity, lkas, main):
  cp = params(identity, lda=True)
  before = cp.to_dict()
  settings = AolSettings(True, 0., lkas, main, (0, 0, 0), (0, 0, 0))
  owner = ClassicLongCardIntent(cp, settings)
  assert owner.independent_cruise_buttons == (identity != CAR.HYUNDAI_I30_2026)
  assert owner.settings == settings and cp.to_dict() == before
