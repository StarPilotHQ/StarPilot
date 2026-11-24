"""Always On Lateral on a Tesla Model 3/Y that started with the scroll frame admitted: the panda reports the whole
safety word, speed-sync bit included, and Always On Lateral must accept it exactly where it accepted the word without."""
from pathlib import Path
import re

import pytest
from opendbc.car.tesla.screen_button import (AOL_WORDS, ORDINARY_WORDS, SCREEN_WORDS, SPEED_SYNC, SYNC_WORDS, apply_modern_aol,
                                             modern_aol_qualified, modern_aol_supported)
from opendbc.car.tesla.speed_sync import prepare as admit_scroll_frame, selected
from openpilot.starpilot.aol import vehicle
from openpilot.starpilot.car.tesla import aol
from openpilot.starpilot.car.tesla.coop_preferences import prepare as apply_cooperative
from openpilot.starpilot.tests.tesla_set_speed_fixture import tesla

TESLA, PREAP = 10, 39
# Every Always On Lateral word: the screen tap (512) or ordinary (2048), with brake disengage (1024), co-op steering
# (256) and longitudinal control (1) or not.
EVERY_AOL_WORD = sorted(AOL_WORDS)
WITH_SPEED_SYNC = sorted(SPEED_SYNC | word for word in AOL_WORDS if word & 1)
HEADER = Path(__file__).resolve().parents[1] / 'aol_policy.h'


def test_the_word_lists_are_the_sixteen_always_on_lateral_words():
  assert SPEED_SYNC == 4096 and len(EVERY_AOL_WORD) == 16 and len(WITH_SPEED_SYNC) == 8
  assert AOL_WORDS == SCREEN_WORDS | ORDINARY_WORDS
  assert set(WITH_SPEED_SYNC) == SYNC_WORDS - {4097, 4353}, 'every speed-sync word but the two without Always On Lateral'


@pytest.mark.parametrize('word', EVERY_AOL_WORD)
def test_every_always_on_lateral_word_is_accepted_with_and_without_the_speed_sync_bit(word):
  assert aol.native_profile_supported(TESLA, word) and vehicle.native_profile_supported(TESLA, word)
  with_sync = word | SPEED_SYNC
  assert aol.native_profile_supported(TESLA, with_sync) is bool(word & 1), 'the bit needs longitudinal control'
  assert vehicle.native_profile_supported(TESLA, with_sync) is bool(word & 1)
  assert not aol.native_profile_supported(PREAP, with_sync)


def test_no_other_word_is_accepted():
  accepted = {word for word in range(65536) if aol.native_profile_supported(TESLA, word)}
  assert accepted == set(EVERY_AOL_WORD) | set(WITH_SPEED_SYNC)
  assert {word for word in range(65536) if aol.native_profile_supported(PREAP, word)} == {0, 1, 2, 3, 6, 7}


@pytest.mark.parametrize('word', [4096, 4097, 4353, 4096 | 256, 4096 | 512, 4096 | 768, 4096 | 1536, 4096 | 2048, 4096 | 2304,
                                  4096 | 3072, 4096 | 513 | 2048, 4096 | 513 | 2, 8192 | 513, 4096 | 8192 | 513])
def test_the_bit_on_a_word_without_always_on_lateral_or_without_longitudinal_control_is_rejected(word):
  assert not aol.native_profile_supported(TESLA, word)
  assert not vehicle.native_profile_supported(TESLA, word)


def header_accepts(word):
  """tesla_screen_aol_param in the pandad header, read from its text: co-op steering is ignored, the speed-sync bit is
  dropped from a word with longitudinal control, and what is left must be one of the listed words."""
  text = HEADER.read_text()
  body = text[text.index('inline bool tesla_screen_aol_param'):text.index('TESLA_SCREEN_AOL_PROFILE')]
  assert 'param &= ~256U;' in body
  assert re.search(r'if \(\(\(param & 4096U\) != 0U\) && \(\(param & 1U\) != 0U\)\) \{\s*param &= ~4096U;\s*\}', body)
  listed = {int(value) for value in re.findall(r'param == (\d+)U', body)}
  word &= ~256
  if word & 4096 and word & 1:
    word &= ~4096
  return word in listed


def test_the_pandad_header_accepts_exactly_the_same_words():
  assert {word for word in range(65536) if header_accepts(word)} == set(EVERY_AOL_WORD) | set(WITH_SPEED_SYNC)


def model_3(*, screen, brake=False, coop=True):
  """A Model 3 as startup leaves it with Always On Lateral on: the scroll frame admitted, marked for the panda."""
  cp = tesla(selected=True)
  apply_modern_aol(cp, True, screen, brake)
  apply_cooperative(cp, coop)
  admit_scroll_frame(cp, True)
  cp.alternativeExperience = 32
  return cp


@pytest.mark.parametrize('screen,word', [(False, 4096 | 2305), (True, 4096 | 769)])
def test_a_model_3_with_the_speed_sync_bit_is_matched_to_the_word_the_panda_reports(screen, word):
  cp = model_3(screen=screen)
  assert int(cp.safetyConfigs[0].safetyParam) == word and selected(cp)
  assert modern_aol_supported(cp) and modern_aol_qualified(cp, marked_only=True)
  assert aol.native_accepts_cp(cp, TESLA, word)
  assert vehicle.native_matches_cp(cp, TESLA, word)
  policy = aol.policy_for(cp)
  assert policy.intent_supported and policy.runtime_supported and policy.alternative_experience_addition == 32
  for other in (word & ~SPEED_SYNC, word & ~256, word ^ 1024, word | 2, 4097):
    assert not aol.native_accepts_cp(cp, TESLA, other), other
    assert not vehicle.native_matches_cp(cp, TESLA, other), other
  assert not aol.native_accepts_cp(cp, PREAP, word) and not vehicle.native_matches_cp(cp, PREAP, word)


@pytest.mark.parametrize('screen', [False, True])
@pytest.mark.parametrize('brake', [False, True])
@pytest.mark.parametrize('coop', [False, True])
@pytest.mark.parametrize('model', ['TESLA_MODEL_3', 'TESLA_MODEL_Y'])
def test_every_startup_combination_with_the_bit_is_matched(model, screen, brake, coop):
  cp = tesla(model, selected=True)
  apply_modern_aol(cp, True, screen, brake)
  apply_cooperative(cp, coop)
  cp.alternativeExperience = 32
  word = int(cp.safetyConfigs[0].safetyParam)
  assert word & SPEED_SYNC and word in WITH_SPEED_SYNC
  assert aol.native_accepts_cp(cp, TESLA, word) and vehicle.native_matches_cp(cp, TESLA, word)
  without = tesla(model)
  apply_modern_aol(without, True, screen, brake)
  apply_cooperative(without, coop)
  without.alternativeExperience = 32
  assert int(without.safetyConfigs[0].safetyParam) == word & ~SPEED_SYNC
  assert aol.native_accepts_cp(without, TESLA, word & ~SPEED_SYNC), 'as before, without the bit'
  assert not aol.native_accepts_cp(without, TESLA, word), "never a word the car's parameters do not carry"


def test_without_always_on_lateral_the_bit_alone_is_not_an_always_on_lateral_profile():
  cp = tesla(selected=True)
  apply_cooperative(cp, True)
  assert int(cp.safetyConfigs[0].safetyParam) == 4353
  assert not aol.native_accepts_cp(cp, TESLA, 4353) and not vehicle.native_matches_cp(cp, TESLA, 4353)
