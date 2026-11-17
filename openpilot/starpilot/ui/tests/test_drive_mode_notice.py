from types import SimpleNamespace

from openpilot.starpilot.ui.drive_mode_notice import ALERT_TYPE, NOTICE_NS, DriveModeNotice, notice_text
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert

IONIQ_6 = SimpleNamespace(carFingerprint="HYUNDAI_IONIQ_6", notCar=False)
CAR_OK = SimpleNamespace(canValid=True, canTimeout=False)


class Params:
  def __init__(self, root, values):
    self.root = root
    for key, value in values.items():
      (root / key).write_bytes(value)

  def get_param_path(self, key):
    return str(self.root / key)

  def get_default_value(self, key):
    return None


def mode(name):
  return SimpleNamespace(ecoGear=name == 'eco', sportGear=name == 'sport')


def step(notice, name, now_ns, *, car=CAR_OK, cp=IONIQ_6, traffic=False, native=None):
  return notice.update(mode(name) if name else None, car, cp=cp, drive_frame=1, event_ns=now_ns, now_ns=now_ns,
                       native_alert=native or OnroadAlert(), traffic_active=traffic)


def enabled(tmp_path, **extra):
  return Params(tmp_path, {'DriveModeProfiles': b'1', **extra})


def test_text_always_shows_both_active_profiles():
  selected = ('dom_default', 'eco')
  assert notice_text('sport', (True, True), selected) == ("Sport Mode", "Accel: Sport+, Decel: Sport")
  assert notice_text('sport', (True, False), selected) == ("Sport Mode", "Accel: Sport+, Decel: Eco")
  assert notice_text('normal', (False, True), selected) == ("Normal Mode", "Accel: Default, Decel: Standard")


def test_unmatched_category_uses_saved_selected_profile(tmp_path, monkeypatch):
  from openpilot.starpilot.ui import drive_mode_notice
  document = {"selectedAccelerationProfile": "sport", "selectedDecelerationProfile": "standard"}
  monkeypatch.setattr(drive_mode_notice, "read_document_value", lambda params: SimpleNamespace(valid=True, value=document))
  notice = DriveModeNotice(enabled(tmp_path, DriveModeDeceleration=b'0'))
  step(notice, 'normal', 1_000)
  assert step(notice, 'eco', 2_000).text2 == "Accel: Eco, Decel: Standard"


def test_change_shows_briefly_but_first_observation_does_not(tmp_path):
  notice = DriveModeNotice(enabled(tmp_path))
  assert step(notice, 'normal', 1_000).size == AlertSize.NONE
  alert = step(notice, 'sport', 2_000)
  assert (alert.size, alert.text1, alert.text2, alert.alert_type) == (
    AlertSize.MID, "Sport Mode", "Accel: Sport+, Decel: Sport", ALERT_TYPE)
  assert step(notice, 'sport', 2_000 + NOTICE_NS - 1).size == AlertSize.MID
  assert step(notice, 'sport', 2_000 + NOTICE_NS).size == AlertSize.NONE


def test_quiet_when_disabled_other_car_or_traffic(tmp_path):
  off = DriveModeNotice(Params(tmp_path, {}))
  step(off, 'normal', 1_000)
  assert step(off, 'sport', 2_000).size == AlertSize.NONE
  other = DriveModeNotice(enabled(tmp_path))
  ioniq5 = SimpleNamespace(carFingerprint="HYUNDAI_IONIQ_5", notCar=False)
  step(other, 'normal', 1_000, cp=ioniq5)
  assert step(other, 'sport', 2_000, cp=ioniq5).size == AlertSize.NONE
  traffic = DriveModeNotice(enabled(tmp_path))
  step(traffic, 'normal', 1_000, traffic=True)
  assert step(traffic, 'eco', 2_000, traffic=True).size == AlertSize.NONE


def test_invalid_can_does_not_read_as_normal(tmp_path):
  notice = DriveModeNotice(enabled(tmp_path))
  step(notice, 'sport', 1_000)
  bad = SimpleNamespace(canValid=False, canTimeout=False)
  assert step(notice, 'normal', 2_000, car=bad).size == AlertSize.NONE
  assert step(notice, 'sport', 3_000).size == AlertSize.NONE


def test_other_alerts_win(tmp_path):
  notice = DriveModeNotice(enabled(tmp_path))
  step(notice, 'normal', 1_000)
  native = OnroadAlert(AlertSize.SMALL, "Other", "", alert_type="other/warning")
  assert step(notice, 'eco', 2_000, native=native) is native
  assert step(notice, 'eco', 3_000).text1 == "Eco Mode"
