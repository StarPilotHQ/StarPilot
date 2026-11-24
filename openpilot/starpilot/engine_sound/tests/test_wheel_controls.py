from openpilot.common.params import Params
from openpilot.starpilot.engine_sound import settings
from openpilot.starpilot.engine_sound.profiles import DEFAULT_PROFILE, PROFILES


def test_toggle_flips_saved_state(tmp_path):
  params = Params(str(tmp_path))
  assert settings.toggle(params) is True
  assert params.get_bool('EngineSound')
  assert settings.toggle(params) is False
  assert not params.get_bool('EngineSound')


def test_toggle_leaves_an_unreadable_value(tmp_path):
  params = Params(str(tmp_path))
  path = params.get_param_path('EngineSound')
  with open(path, 'wb') as f:
    f.write(b'7')
  assert settings.toggle(params) is None
  with open(path, 'rb') as f:
    assert f.read() == b'7'


def test_next_profile_cycles_every_car_and_wraps(tmp_path):
  params = Params(str(tmp_path))
  names = list(PROFILES)
  start = names.index(DEFAULT_PROFILE)
  seen = [settings.next_profile(params) for _ in names]
  assert seen == [names[(start + i + 1) % len(names)] for i in range(len(names))]
  assert settings.profile_name(params) == DEFAULT_PROFILE


def test_next_profile_recovers_an_unknown_saved_car(tmp_path):
  params = Params(str(tmp_path))
  params.put('EngineSoundProfile', 'tractor', block=True)
  assert settings.next_profile(params) == DEFAULT_PROFILE
