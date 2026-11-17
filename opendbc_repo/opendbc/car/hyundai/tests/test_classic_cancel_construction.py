from openpilot.starpilot.aol.tests.classic_startup_test_helpers import prepared_classic_card
import os
from unittest.mock import patch

import pytest

from opendbc.car.hyundai.classic_long_aol import independent_buttons_qualified
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.tests.test_classic_long_profiles import params
from openpilot.starpilot.car.hyundai.classic_long_intent import ClassicLongCardIntent


def test_actual_card_constructs_independent_buttons_before_final_marker():
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put_bool('AlwaysOnLateral', True, block=True)
    saved.put('LKASButtonControl', 9, block=True)
    saved.put('MainCruiseButtonControl', 0, block=True)
    cp = params(CAR.HYUNDAI_SONATA, lda=True)
    assert cp.safetyConfigs[0].safetyParam == 4 and cp.alternativeExperience == 0
    assert independent_buttons_qualified(cp, marked_only=False)
    assert not independent_buttons_qualified(cp)
    assert CarInterface.startup_required(cp)
    card = prepared_classic_card(cp)
    try:
      assert card.aol_card_intent.independent_cruise_buttons
      assert card.CP.safetyConfigs[0].safetyParam == 0xC04 and card.CP.alternativeExperience == 32
      assert independent_buttons_qualified(card.CP)
      assert not card.ci_initialized and not saved.get_bool('ControlsReady')
      assert saved.get('LKASButtonControl') == 9 and saved.get('MainCruiseButtonControl') == 0
    finally:
      card.vehicle_startup.close()


@pytest.mark.parametrize('lda', [False, True])
@pytest.mark.parametrize('lkas,main', [(9, 0), (0, 0), (0, 9), (9, 9)])
@pytest.mark.parametrize('release', [False, True])
def test_constructor_scope_preserves_saved_button_mappings_and_release(lda, lkas, main, release):
  cp = params(CAR.HYUNDAI_SONATA, lda=lda, release=release)
  before = cp.to_dict()
  settings = AolSettings(True, 0., lkas, main, (0, 0, 0), (0, 0, 0))
  owner = ClassicLongCardIntent(cp, settings)
  assert owner.independent_cruise_buttons == (lda and not release)
  assert cp.to_dict() == before and owner.settings == settings
  assert not independent_buttons_qualified(cp)
