from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.audio.departure_alert import (
  ALERT_TYPES,
  KEYS,
  DepartureAlertDelivery,
  DepartureEpisode,
  DepartureNotifications,
  departure_alerts,
)
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.sounds_owner import SoundsOwner

NOW = 100_000_000_000
DRIVE = NOW - 10_000_000_000


def sample(owner, tick, **kwargs):
  values = {
    'drive': DRIVE,
    'model_ns': NOW + tick * 50000000,
    'now_ns': NOW + tick * 50000000,
    'stationary': True,
    'valid': True,
    'light': True,
    'model_stopped': True,
    'tracked': False,
    'distance': 10.0,
    'lead_speed': 0.0,
    'lead_id': 1,
    'enabled': (True, True),
  }
  values.update(kwargs)
  return owner.step(**values)


@pytest.mark.parametrize('kind', [1, 2])
def test_original_triggers_one_shot_and_motion_rearms(kind):
  owner = DepartureEpisode()

  def arm(t):
    return sample(owner, t, tracked=kind == 2)

  def fire(t):
    return sample(owner, t, tracked=kind == 2, model_stopped=False, light=False, distance=11.0, lead_speed=1.0)

  assert arm(1) == (0, 0)
  first = fire(2)
  assert first == (kind, NOW + 100_000_000)
  assert fire(2) == first
  assert fire(3) == first
  assert sample(owner, 70, tracked=kind == 2, model_stopped=False, light=False, distance=15.0, lead_speed=2.0) == (0, 0)
  assert arm(71) == (0, 0)
  assert fire(72) == (0, 0)
  assert sample(owner, 73, stationary=False) == (0, 0)
  assert arm(74) == (0, 0)
  assert fire(75)[0] == kind


@pytest.mark.parametrize('kind', [1, 2])
def test_no_notice_without_armed_fresh_evidence_or_enabled_setting(kind):
  owner = DepartureEpisode()
  assert sample(owner, 1, tracked=kind == 2, model_stopped=False, light=False, distance=11.0, lead_speed=1.0) == (0, 0)
  sample(owner, 2, tracked=kind == 2)
  assert sample(owner, 3, valid=False, tracked=kind == 2) == (0, 0)
  assert sample(owner, 4, tracked=kind == 2, model_stopped=False, light=False, distance=11.0, lead_speed=1.0) == (0, 0)
  sample(owner, 5, tracked=kind == 2)
  assert sample(owner, 6, tracked=kind == 2, model_stopped=False, light=False, distance=12.0, lead_speed=1.0, enabled=(False, False)) == (0, 0)


def test_lead_identity_and_moving_baseline_never_trigger_departure():
  owner = DepartureEpisode()
  sample(owner, 1, tracked=True)
  assert sample(owner, 2, tracked=True, lead_id=2, distance=30.0, lead_speed=2.0) == (0, 0)
  assert sample(owner, 3, tracked=True, lead_id=2, distance=33.0, lead_speed=2.0) == (0, 0)
  sample(owner, 4, tracked=True, lead_id=2, distance=33.0, lead_speed=0.0)
  assert sample(owner, 5, tracked=True, lead_id=2, distance=33.9, lead_speed=1.0) == (0, 0)
  assert sample(owner, 6, tracked=True, lead_id=2, distance=34.0, lead_speed=0.9) == (0, 0)
  assert sample(owner, 7, tracked=True, lead_id=2, distance=34.0, lead_speed=1.0)[0] == 2


@pytest.fixture
def delivery():
  class SM(dict):
    valid = dict.fromkeys(('starpilotLongitudinalPlan', 'deviceState'), True)
    alive = dict(valid)
    logMonoTime = dict.fromkeys(valid, NOW - 10_000_000)
    recv_time = dict.fromkeys(valid, (NOW - 5_000_000) / 1e9)

    def all_checks(self, keys):
      return all(self.valid[k] and self.alive[k] for k in keys)

  message = messaging.new_message('starpilotLongitudinalPlan')
  message.starpilotLongitudinalPlan = {
    'version': 1,
    'driveStartMonoTime': DRIVE,
    'modelMonoTime': NOW - 30000000,
    'sourcePlanMonoTime': NOW - 10000000,
    'departureAlertKind': 1,
    'departureAlertMonoTime': NOW - 50000000,
  }
  sm = SM(starpilotLongitudinalPlan=message.starpilotLongitudinalPlan, deviceState=NS(started=True, startedMonoTime=DRIVE))
  cs = NS(canValid=True, canTimeout=False, standstill=True, gasPressed=False, gearShifter='drive')
  kwargs = {'enabled': (True, True), 'now_ns': NOW, 'car_ns': NOW - 5000000, 'car_valid': True}
  return sm, cs, kwargs


@pytest.mark.parametrize('kind', [1, 2])
def test_both_actual_alert_outputs_and_duration(delivery, kind):
  sm, cs, kwargs = delivery
  sm['starpilotLongitudinalPlan'].departureAlertKind = kind
  alerts = departure_alerts(sm, cs, **kwargs)
  assert len(alerts) == 1
  a = alerts[0]
  assert a.alert_type == ALERT_TYPES[kind - 1]
  from openpilot.selfdrive.selfdrived.events import AudibleAlert, AlertStatus, Priority

  assert a.audible_alert == AudibleAlert.prompt
  assert a.alert_status == AlertStatus.userPrompt
  assert a.priority == Priority.LOW
  assert 0 < a.duration <= 300


@pytest.mark.parametrize('service', ['starpilotLongitudinalPlan', 'deviceState'])
@pytest.mark.parametrize('fault', ['invalid', 'dead', 'stale', 'future', 'old-receipt', 'future-receipt'])
def test_consumer_rejects_invalid_and_unfresh_sources(delivery, service, fault):
  sm, cs, kwargs = delivery
  if fault in ('invalid', 'dead'):
    (sm.valid if fault == 'invalid' else sm.alive)[service] = False
  elif fault in ('stale', 'future'):
    sm.logMonoTime[service] = 1 if fault == 'stale' else NOW + 1
  else:
    sm.recv_time[service] = 0 if fault == 'old-receipt' else (NOW + 1_000_000) / 1e9
  assert not departure_alerts(sm, cs, **kwargs)


@pytest.mark.parametrize('field,value', [('standstill', False), ('gasPressed', True), ('canValid', False), ('canTimeout', True), ('gearShifter', 'reverse')])
def test_consumer_vehicle_guards(delivery, field, value):
  sm, cs, kwargs = delivery
  setattr(cs, field, value)
  assert not departure_alerts(sm, cs, **kwargs)


def test_repeated_publication_does_not_replay_after_interruption(delivery):
  sm, cs, kwargs = delivery
  owner = DepartureAlertDelivery()
  assert owner.sample(sm, cs, **kwargs)
  assert owner.sample(sm, cs, **kwargs)
  sm.valid['starpilotLongitudinalPlan'] = False
  assert not owner.sample(sm, cs, **kwargs)
  sm.valid['starpilotLongitudinalPlan'] = True
  assert not owner.sample(sm, cs, **kwargs)
  sm['starpilotLongitudinalPlan'].departureAlertMonoTime += 1
  assert owner.sample(sm, cs, **kwargs)


@pytest.mark.parametrize('key', KEYS)
def test_actual_galaxy_owner_defaults_off_and_persists_exact_saved_choice(tmp_path, key):
  params = Params(str(tmp_path))
  assert params.get_default_value(key) is False
  owner = SoundsOwner(params, lambda: True)
  row = next(r for r in owner.snapshot().rows if r.key == key)
  assert row.value == row.default_value == 'Off'
  request = FeatureSettingsRequest(key, row.source, 'On')
  assert owner.apply(request)
  assert Path(params.get_param_path(key)).read_bytes() == b'1'
  assert next(r for r in SoundsOwner(params, lambda: True).snapshot().rows if r.key == key).value == 'On'
  assert not owner.apply(request)
  assert not SoundsOwner(params, lambda: False).apply(FeatureSettingsRequest(key, b'1', 'Off'))
  assert owner.apply(FeatureSettingsRequest(key, b'1', 'Off'))
  assert Path(params.get_param_path(key)).read_bytes() == b'0'


def test_defaults_skip_perception_and_never_mutate_settings(tmp_path):
  params = Params(str(tmp_path))
  owner = DepartureNotifications(params)
  assert owner.sample(None, None, None, clocks=(NOW, NOW, 0)) == (0, 0)
  assert owner.projector is None
  assert all(not Path(params.get_param_path(k)).exists() for k in KEYS)


@pytest.mark.parametrize('kind', [1, 2])
def test_existing_scene_projector_drives_each_notice_without_control_changes(tmp_path, kind):
  import numpy as np
  from openpilot.starpilot.conditional_mode.tests.test_projection import FakeSubMaster, serialized_scene

  params = Params(str(tmp_path))
  Path(params.get_param_path(KEYS[kind - 1])).write_bytes(b'1')
  owner = DepartureNotifications(params)
  cp = NS(openpilotLongitudinalControl=True, pcmCruise=False, carFingerprint='HYUNDAI_IONIQ_6', passive=False, dashcamOnly=False, notCar=False)
  planner = NS(mpc=NS(params=np.array([[0.0, 0.0, 0.0, 0.0, 1.45]])))
  before = planner.mpc.params.copy()
  emitted = []
  for tick in range(180):
    now = NOW + tick * 50_000_000
    stationary = tick >= 60
    departing = tick >= 100
    horizon = 80.0 if kind == 2 or departing else 30.0
    payloads = serialized_scene(
      stamp=now,
      speed=0.0 if stationary else 10.0,
      horizon=horizon,
      lead_present=kind == 2,
      lead_distance=11.0 if departing else 10.0,
      lead_speed=1.0 if departing else 0.0,
      standstill=stationary,
    )
    payloads = {k: v.as_builder() for k, v in payloads.items()}
    payloads['carState'].gearShifter = 'drive'
    payloads['controlsState'].curvature = 0.0
    payloads['modelV2'].action.shouldStop = not departing if kind == 1 else False
    payloads['radarState'].leadOne.radarTrackId = 7
    payloads['deviceState'] = NS(started=True, startedMonoTime=DRIVE)
    sm = FakeSubMaster(payloads, now)
    for attr in ('valid', 'alive', 'seen'):
      getattr(sm, attr)['deviceState'] = True
    sm.logMonoTime['deviceState'] = now
    sm.recv_time['deviceState'] = now / 1e9
    sm.all_checks = lambda keys, sm=sm: all(sm.valid[k] and sm.alive[k] for k in keys)
    notice = owner.sample(sm, cp, planner, clocks=(now, now, 0))
    if notice[0]:
      emitted.append(notice)
  assert emitted and {x[0] for x in emitted} == {kind}
  assert len({x[1] for x in emitted}) == 1
  np.testing.assert_array_equal(planner.mpc.params, before)


def test_stale_clear_and_out_of_order_source_cannot_rearm_a_notice():
  owner = DepartureEpisode()
  sample(owner, 1)
  assert sample(owner, 2, valid=False) == (0, 0)
  for tick in range(3, 6):
    assert sample(owner, tick, model_stopped=False, light=True) == (0, 0)
  sample(owner, 6)
  assert sample(owner, 7, model_stopped=False)[0] == 1
  assert sample(owner, 5) == (0, 0)
  sample(owner, 8)
  assert sample(owner, 80, model_stopped=False) == (0, 0)


@pytest.mark.parametrize('signal', ['status', 'audible', 'visual', 'priority'])
def test_optional_notice_yields_to_existing_safety_alerts(delivery, signal):
  from openpilot.starpilot.audio.departure_alert import protect_safety_alerts
  from openpilot.selfdrive.selfdrived.events import Alert, AlertStatus, AlertSize, Priority, AudibleAlert, VisualAlert
  sm, cs, kwargs = delivery
  optional = departure_alerts(sm, cs, **kwargs)[0]
  core = Alert('Core alert', '', AlertStatus.userPrompt if signal=='status' else AlertStatus.normal,
               AlertSize.small, Priority.LOW if signal=='priority' else Priority.LOWEST,
               VisualAlert.fcw if signal=='visual' else VisualAlert.none,
               AudibleAlert.warningSoft if signal=='audible' else AudibleAlert.none, 1.)
  core.alert_type = 'core/warning'
  assert protect_safety_alerts([optional, core]) == [core]
  assert protect_safety_alerts([optional], retained=[core]) == []
  core.alert_status, core.audible_alert, core.visual_alert = AlertStatus.normal, AudibleAlert.none, VisualAlert.none
  core.priority = Priority.LOWEST
  assert protect_safety_alerts([optional, core]) == [optional, core]


@pytest.mark.parametrize('key,label', [('GreenLightAlert', 'Green Light Alert'), ('LeadDepartingAlert', 'Lead Departing Alert')])
def test_galaxy_sounds_page_and_confirmed_save(tmp_path, key, label):
  from openpilot.starpilot.galaxy.settings import SettingsGateway
  from openpilot.starpilot.galaxy.tests.settings_http_fixture import MutableContext
  params = Params(str(tmp_path))
  gateway = SettingsGateway(params, MutableContext(None), clock=lambda: 100.)
  session, generation = 'test-session', b'test-generation'
  page = gateway.page('sounds', session, generation)
  index = next(i for i, row in enumerate(page['rows']) if row['label']==label)
  assert page['rows'][index]['value'] == 'Off'
  intent = gateway.preview(page['view'], index, 1, session, generation)
  assert gateway.confirm(intent['intent'], session, generation)
  assert params.get_bool(key)
  page = gateway.page('sounds', session, generation)
  index = next(i for i, row in enumerate(page['rows']) if row['label']==label)
  assert page['rows'][index]['value'] == 'On'
  intent = gateway.preview(page['view'], index, 0, session, generation, value='Off')
  assert gateway.confirm(intent['intent'], session, generation)
  assert not params.get_bool(key)


def test_known_stop_sign_never_becomes_a_green_light_notice_until_next_stop():
  owner = DepartureEpisode()
  sample(owner, 1, sign=True)
  assert sample(owner, 2, model_stopped=False) == (0, 0)
  sample(owner, 3)
  assert sample(owner, 4, model_stopped=False) == (0, 0)
  sample(owner, 5, stationary=False)
  sample(owner, 6)
  assert sample(owner, 7, model_stopped=False)[0] == 1
