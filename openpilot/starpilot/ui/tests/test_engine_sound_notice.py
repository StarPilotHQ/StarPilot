from openpilot.starpilot.ui.engine_sound_notice import NOTICE_NS, EngineSoundNotice
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert

NOW = 5_000_000_000


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
  alert = notice.update(OnroadAlert(), NOW)
  assert (alert.text1, alert.text2) == ('Engine Sounds On', 'LFA')
  notice.show(True, False, 'lfa', NOW)
  alert = notice.update(OnroadAlert(), NOW)
  assert (alert.size, alert.text1) == (AlertSize.SMALL, 'Engine Sounds Off')
  notice.show(False, False, 'lfa', NOW)
  assert notice.update(OnroadAlert(), NOW).text2 == 'Engine Sounds Off'
