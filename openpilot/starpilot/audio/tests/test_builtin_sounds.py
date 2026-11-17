from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest

from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.selfdrive.ui.soundd import Soundd, AudibleAlert, sound_list
from openpilot.starpilot.audio.sound_pack import (
  BUILTIN_FILES, DEFAULT_PACK, installed_packs, read_selection, read_wav, migrate_removed_selection,
)


def test_default_builtin_assets_flow_through_soundd_callback(tmp_path):
  params = Params(str(tmp_path / 'params'))
  assert read_selection(params) == (None, DEFAULT_PACK, True)
  sound = Soundd(params)
  root = Path(BASEDIR) / 'openpilot/selfdrive/assets/sounds_starpilot'
  for alert, (logical, _, _) in sound_list.items():
    expected = read_wav(root / BUILTIN_FILES[logical]) if logical in BUILTIN_FILES else read_wav(root.parent / "sounds" / logical)
    np.testing.assert_array_equal(sound.loaded_sounds[alert], expected)
    sound.current_alert, sound.current_sound, sound.current_sound_frame, sound.current_volume = alert, alert, 0, .5
    output = np.zeros((len(expected), 1), dtype=np.float32)
    with patch('pathlib.Path.open', side_effect=AssertionError('callback file I/O')):
      sound.callback(output, len(expected), None, None)
    np.testing.assert_array_equal(output[:, 0], expected * .5)
  assert sound_list[AudibleAlert.warningImmediate][1] is None
  assert BUILTIN_FILES[sound_list[AudibleAlert.warningImmediate][0]] == 'warning_3.wav'


@pytest.mark.parametrize('name', ['frog', 'Frog', 'frogpilot', 'stalin', 'STALIN'])
def test_removed_choice_migrates_without_loading_old_pack(tmp_path, name):
  params = Params(str(tmp_path / 'params'))
  params.put('SoundPack', name, block=True)
  sound = Soundd(params)
  assert params.get('SoundPack') == DEFAULT_PACK
  assert read_selection(params)[1] == DEFAULT_PACK
  assert not migrate_removed_selection(params)
  assert len(sound.pack_loader.builtin_sounds) == 5


@pytest.mark.parametrize('name', ['stock', 'tesla', 'custom'])
def test_explicit_user_choice_is_preserved(tmp_path, name):
  params = Params(str(tmp_path / 'params'))
  params.put('SoundPack', name, block=True)
  root = tmp_path / 'packs'
  if name != 'stock':
    (root / name / 'sounds').mkdir(parents=True)
  assert not migrate_removed_selection(params, root)
  assert params.get('SoundPack') == name


def test_removed_and_builtin_packs_cannot_be_shadowed(tmp_path):
  for name in ('starpilot', 'stock', 'frog', 'Frog', 'stalin', 'STALIN', 'custom'):
    (tmp_path / name / 'sounds').mkdir(parents=True, exist_ok=True)
  assert installed_packs(tmp_path) == ('starpilot', 'stock', 'custom')


def test_builtin_labels_keep_persisted_sound_selection_stable(tmp_path):
  from openpilot.starpilot.ui.sounds_owner import SoundsOwner
  from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
  params = Params(str(tmp_path / 'params'))
  owner = SoundsOwner(params, lambda: True, tmp_path / 'packs')
  rows = [row for row in owner.snapshot().rows if row.key == 'SoundPack']
  assert len(rows) == 1
  row = rows[0]
  assert row.value == 'StarPilot Sounds'
  assert row.choices == ('StarPilot Sounds', 'Stock Sounds')
  assert owner.apply(FeatureSettingsRequest(key='SoundPack', expected=None, value='Stock Sounds'))
  assert params.get('SoundPack') == 'stock'
  assert owner.apply(FeatureSettingsRequest(key='SoundPack', expected=b'stock', value='StarPilot Sounds'))
  assert params.get('SoundPack') == 'starpilot'
