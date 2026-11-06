from types import SimpleNamespace as NS

import pytest

from openpilot.starpilot.audio.blindspot_alert import ALERT_TYPE, KEYS, blindspot_alert
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Alert, AlertSize, AlertStatus, AudibleAlert, ET, Priority, VisualAlert
from openpilot.common.params import Params
from openpilot.starpilot.ui.sounds_owner import SoundsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest

NOW = 10_000_000_000


class Messages(dict):
  def __init__(self, active=False):
    super().__init__(deviceState=NS(started=True), carControl=NS(latActive=active))
    self.logMonoTime = dict.fromkeys(('deviceState', 'carControl'), NOW - 10_000_000)
    self.healthy = True

  def all_checks(self, _services):
    return self.healthy


def car(**kw):
  return NS(**({'leftBlinker': True, 'rightBlinker': False, 'leftBlindspot': True, 'rightBlindspot': False,
               'canValid': True, 'canTimeout': False} | kw))


def test_independent_switches_and_active_lane_change():
  sm = Messages(active=True)
  assert blindspot_alert(car(), sm, loud=True, without_lateral=False, lane_change_blocked=True, now_ns=NOW, car_ns=NOW - 10_000_000, car_valid=True)
  assert not blindspot_alert(car(), sm, loud=False, without_lateral=True, lane_change_blocked=False, now_ns=NOW, car_ns=NOW - 10_000_000, car_valid=True)
  sm['carControl'].latActive = False
  assert blindspot_alert(car(), sm, loud=False, without_lateral=True, lane_change_blocked=False, now_ns=NOW, car_ns=NOW - 10_000_000, car_valid=True)
  assert blindspot_alert(car(), sm, loud=False, without_lateral=True, lane_change_blocked=True, now_ns=NOW, car_ns=NOW - 10_000_000, car_valid=True)
  assert not blindspot_alert(car(), sm, loud=True, without_lateral=False, lane_change_blocked=False, now_ns=NOW, car_ns=NOW - 10_000_000, car_valid=True)
  assert not blindspot_alert(car(), sm, loud=False, without_lateral=False, lane_change_blocked=True, now_ns=NOW, car_ns=NOW - 10_000_000, car_valid=True)


@pytest.mark.parametrize('fields', [{'leftBlinker': False}, {'rightBlinker': True},
  {'leftBlindspot': False, 'rightBlindspot': True}, {'canValid': False}, {'canTimeout': True}])
def test_no_wrong_side_hazards_or_invalid_can(fields):
  assert not blindspot_alert(car(**fields), Messages(), loud=False, without_lateral=True,
                            lane_change_blocked=False, now_ns=NOW, car_ns=NOW - 10_000_000, car_valid=True)


def test_right_side_and_stale_or_offroad_inputs():
  cs = car(leftBlinker=False, leftBlindspot=False, rightBlinker=True, rightBlindspot=True)
  sm = Messages()
  def alert():
    return blindspot_alert(cs, sm, loud=False, without_lateral=True, lane_change_blocked=False, now_ns=NOW, car_ns=NOW - 10_000_000, car_valid=True)
  assert alert()
  for key in sm.logMonoTime:
    stamp = sm.logMonoTime[key]
    sm.logMonoTime[key] = NOW - 3_000_000_000
    assert not alert()
    sm.logMonoTime[key] = NOW + 1
    assert not alert()
    sm.logMonoTime[key] = stamp
  sm.healthy = False
  assert not alert()
  sm.healthy = True
  sm['deviceState'].started = False
  assert not alert()


def test_disengaged_alert_survives_warning_filter_but_never_masks_takeover():
  alert = blindspot_alert(car(), Messages(), loud=False, without_lateral=True, lane_change_blocked=False, now_ns=NOW, car_ns=NOW - 10_000_000, car_valid=True)
  assert alert.audible_alert == AudibleAlert.warningSoft
  manager = AlertManager()
  manager.add_many(1, [alert])
  manager.process_alerts(1, {ET.WARNING})
  assert manager.current_alert.alert_type == ALERT_TYPE
  takeover = Alert('Take Control', '', AlertStatus.critical, AlertSize.full, Priority.HIGHEST,
                   VisualAlert.steerRequired, AudibleAlert.warningImmediate, 1.)
  takeover.alert_type, takeover.event_type = 'takeover', ET.IMMEDIATE_DISABLE
  manager.add_many(2, [takeover])
  manager.process_alerts(2, {ET.WARNING})
  assert manager.current_alert.alert_type == 'takeover'


def test_switch_defaults_persistence_and_exact_authority(tmp_path):
  params = Params(str(tmp_path))
  allowed = True
  owner = SoundsOwner(params, lambda: allowed)
  for key in KEYS:
    row = next(row for row in owner.snapshot().rows if row.key == key)
    assert row.value == 'Off' and row.default_value == 'Off'
    request = FeatureSettingsRequest(key, row.source, 'On')
    assert owner.apply(request)
    assert params.get_bool(key)
    assert not owner.apply(request)
    allowed = False
    assert not owner.apply(FeatureSettingsRequest(key, b'1', 'Off'))
    allowed = True
    assert owner.apply(FeatureSettingsRequest(key, b'1', 'Off'))


def test_galaxy_sounds_switches_save_and_reset_with_session_checks(tmp_path):
  from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway, SettingsChanged
  params = Params(str(tmp_path))
  class Context:
    def sample(self) -> AuthorityContext:
      return AuthorityContext(True, None, None)

  context = Context()
  gateway = SettingsGateway(params, context)
  for key, label in zip(KEYS, ('Loud Blindspot Alert', 'Blindspot Alert While Lateral Is Off'), strict=True):
    page = gateway.page('sounds', 'session', b'generation')
    index, row = next((i, row) for i, row in enumerate(page['rows']) if row['label'] == label)
    assert row['choices'] == ['Off', 'On'] and row['defaultValue'] == 'Off'
    pending = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
    assert gateway.confirm(pending['intent'], 'session', b'generation')
    assert params.get_bool(key)
    page = gateway.page('sounds', 'session', b'generation')
    reset = gateway.preview(page['view'], index, 0, 'session', b'generation', reset_default=True)
    with pytest.raises(SettingsChanged):
      gateway.confirm(reset['intent'], 'session', b'generation', session_valid=lambda: False)
    assert params.get_bool(key)
    reset = gateway.preview(page['view'], index, 0, 'session', b'generation', reset_default=True)
    assert gateway.confirm(reset['intent'], 'session', b'generation')
    assert not params.get_bool(key)


@pytest.mark.parametrize('car_ns,valid', [(0, True), (NOW + 1, True), (NOW - 200_000_001, True), (NOW, False)])
def test_separate_car_socket_requires_current_valid_source(car_ns, valid):
  assert not blindspot_alert(car(), Messages(), loud=False, without_lateral=True,
                            lane_change_blocked=False, now_ns=NOW, car_ns=car_ns, car_valid=valid)


def test_actual_selfdrived_socket_live_setting_publication_and_sound(tmp_path):
  import threading
  import time
  from unittest.mock import patch

  import numpy as np

  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.subaru.fingerprints import FW_VERSIONS
  from opendbc.car.subaru.values import CAR
  from openpilot.cereal import messaging
  from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
  from openpilot.selfdrive.ui.soundd import Soundd
  from openpilot.starpilot.car.subaru.tests.caller_helpers import actual_factory, firmware_records

  candidate = CAR.SUBARU_IMPREZA_2020
  fw = firmware_records(candidate, FW_VERSIONS, 'subaru')
  with actual_factory(candidate, gen_empty_fingerprint(), fw, '0' * 17, manual=False, aol=False) as (card, params, _):
    sd = SelfdriveD(CP=card.CP)
    assert 'carState' not in sd.sm.data
    now = time.monotonic_ns()

    def sample(tick, *, age=0, valid=True, active=False):
      stamp = now + tick * 10_000_000
      cs = messaging.new_message('carState', valid=valid, logMonoTime=stamp - age)
      cs.carState.canValid = True
      cs.carState.rightBlindspot = cs.carState.rightBlinker = True
      cs.carState.gearShifter = 'drive'
      cs.carState.vEgo = 8.
      control = messaging.new_message('carControl', valid=True, logMonoTime=stamp)
      control.carControl.latActive = active
      device = messaging.new_message('deviceState', valid=True, logMonoTime=stamp)
      device.deviceState.started = True
      device.deviceState.startedMonoTime = now
      with (patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=cs.as_reader()),
            patch.object(sd.sm, 'update', side_effect=lambda _: sd.sm.update_msgs(stamp / 1e9, [control.as_reader(), device.as_reader()])),
            patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=stamp)):
        state = sd.data_sample()
        sd.events.clear()
        sd.update_alerts(state)
      return state, stamp

    for tick in range(20):
      state, stamp = sample(tick)
    assert sd.AM.current_alert.alert_type != ALERT_TYPE
    params.put_bool(KEYS[1], True, block=True)
    stop = threading.Event()
    with patch('openpilot.selfdrive.selfdrived.selfdrived.time.sleep', side_effect=lambda _: stop.set()):
      sd.params_thread(stop)
    assert sd.blindspot_alert_settings == (False, True)
    state, stamp = sample(20)
    assert sd.AM.current_alert.alert_type == ALERT_TYPE, 'BLINDSPOT_CALLER_SILENT'
    output = {}
    with patch.object(sd.pm, 'send', side_effect=lambda service, msg: output.update({service: msg.to_bytes()})):
      sd.publish_selfdriveState(state)
    published = messaging.log_from_bytes(output['selfdriveState'])
    assert not published.selfdriveState.enabled
    assert published.selfdriveState.alertSound == AudibleAlert.warningSoft

    audio = Soundd(params=params, pack_root=tmp_path)
    receiver = messaging.SubMaster(['selfdriveState'])
    receiver.update_msgs(stamp / 1e9, [published])
    with patch('openpilot.selfdrive.ui.soundd.time.monotonic_ns', return_value=stamp):
      audio.get_audible_alert(receiver)
    assert audio.current_alert == AudibleAlert.warningSoft
    assert np.any(audio.get_sound_data(4096))

    for tick, fields in [(21, {'valid': False}), (22, {'age': 200_000_001}),
                         (23, {'age': -1}), (24, {'active': True})]:
      sample(tick, **fields)
      assert ALERT_TYPE not in sd.AM.alerts
    params.put_bool(KEYS[1], False, block=True)
    stop.clear()
    with patch('openpilot.selfdrive.selfdrived.selfdrived.time.sleep', side_effect=lambda _: stop.set()):
      sd.params_thread(stop)
    sample(25)
    assert ALERT_TYPE not in sd.AM.alerts
