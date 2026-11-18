from openpilot.common.params import Params
from openpilot.starpilot.engine_sound import settings
from openpilot.starpilot.engine_sound.profiles import DEFAULT_PROFILE, PROFILES
from openpilot.starpilot.ui.engine_sound_notice import NOTICE_NS, EngineSoundNotice
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert

NOW = 5_000_000_000


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


def test_notice_shows_the_car_then_yields():
  notice = EngineSoundNotice()
  assert notice.update(OnroadAlert(), NOW) == OnroadAlert()
  notice.show(False, True, 'jaguar_ftype', NOW)
  alert = notice.update(OnroadAlert(), NOW + 1)
  assert (alert.size, alert.text1, alert.text2) == (AlertSize.MID, 'Jaguar F-Type', 'Engine Sound')
  native = OnroadAlert(AlertSize.FULL, 'TAKE CONTROL')
  assert notice.update(native, NOW + 1) is native
  assert notice.update(OnroadAlert(), NOW + NOTICE_NS) == OnroadAlert()


def test_notice_toggle_text():
  notice = EngineSoundNotice()
  notice.show(True, True, 'lfa', NOW)
  assert (notice.update(OnroadAlert(), NOW).text1, notice.update(OnroadAlert(), NOW).text2) == ('Engine Sounds On', 'LFA')
  notice.show(True, False, 'lfa', NOW)
  alert = notice.update(OnroadAlert(), NOW)
  assert (alert.size, alert.text1) == (AlertSize.SMALL, 'Engine Sounds Off')
  notice.show(False, False, 'lfa', NOW)
  assert notice.update(OnroadAlert(), NOW).text2 == 'Engine Sounds Off'
