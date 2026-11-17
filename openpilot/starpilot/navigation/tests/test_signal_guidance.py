import json
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from opendbc.car import structs
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.starpilot.lateral.lane_change_preferences import KEY, LaneChangePolicy, decode, to_value
from openpilot.starpilot.navigation.intent import TurnIntent
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, row_change
from openpilot.starpilot.ui.lane_change_feature import GUIDANCE

NOW = 20_000_000_000


@pytest.fixture
def case(tmp_path):
  params = Params(str(tmp_path))
  cp = structs.CarParams.new_message(carFingerprint='real', brand='toyota', steerControlType='torque')
  cp.safetyConfigs = [structs.CarParams.SafetyConfig.new_message(safetyModel='toyota')]
  params.put(KEY, to_value(replace(LaneChangePolicy(), turn_signal_guidance=True)), block=True)
  class SM(dict):
    valid = dict.fromkeys(('carState', 'carControl', 'deviceState', 'longitudinalPlan'), True)
    alive = dict(valid)
    logMonoTime = dict.fromkeys(valid, NOW)
  sm = SM(carState=NS(canValid=True, canTimeout=False, gearShifter='drive', vEgo=5., standstill=False,
                     leftBlinker=True, rightBlinker=False, leftBlindspot=False, rightBlindspot=False),
          carControl=NS(latActive=True), deviceState=NS(started=True, startedMonoTime=1),
          longitudinalPlan=NS(shouldStop=False, modelMonoTime=NOW))
  return params, cp, sm


def select(owner, sm, desire=log.Desire.none, **kwargs):
  return owner.select(sm, NOW, desire, supported=kwargs.get('supported', True), model_stop=kwargs.get('model_stop', False))


@pytest.mark.parametrize('brand', ['toyota', 'hyundai', 'honda', 'gm', 'ford'])
def test_global_direction_and_model_selection(case, brand):
  params, cp, sm = case
  cp.brand = brand
  owner = TurnIntent(params, cp)
  assert select(owner, sm) == log.Desire.turnLeft
  sm['carState'].leftBlinker, sm['carState'].rightBlinker = False, True
  assert select(owner, sm) == log.Desire.turnRight
  for desire in (log.Desire.laneChangeLeft, log.Desire.laneChangeRight, log.Desire.turnLeft):
    assert select(owner, sm, desire) == desire


@pytest.mark.parametrize('field,value', [('canValid', False), ('canTimeout', True), ('gearShifter', 'reverse'),
  ('standstill', True), ('vEgo', 0), ('vEgo', float('nan')), ('vEgo', 10.), ('leftBlindspot', True), ('rightBlinker', True)])
def test_vehicle_guards(case, field, value):
  params, cp, sm = case
  setattr(sm['carState'], field, value)
  assert select(TurnIntent(params, cp), sm) == log.Desire.none


@pytest.mark.parametrize('service', ['carState', 'carControl', 'longitudinalPlan', 'deviceState'])
@pytest.mark.parametrize('invalid', ['stale', 'future', 'invalid', 'dead'])
def test_delivery_guards(case, service, invalid):
  params, cp, sm = case
  if invalid == 'stale':
    sm.logMonoTime[service] = 1
  if invalid == 'future':
    sm.logMonoTime[service] = NOW + 1
  if invalid == 'invalid':
    sm.valid[service] = False
  if invalid == 'dead':
    sm.alive[service] = False
  assert select(TurnIntent(params, cp), sm) == log.Desire.none


def test_stops_disabled_and_incompatible_model(case):
  params, cp, sm = case
  owner = TurnIntent(params, cp)
  assert select(owner, sm, model_stop=True) == log.Desire.none
  assert select(owner, sm) == log.Desire.none
  sm['carState'].leftBlinker = False
  select(owner, sm)
  sm['carState'].leftBlinker = True
  assert select(owner, sm) == log.Desire.turnLeft
  assert select(owner, sm, supported=False) == log.Desire.none
  sm['longitudinalPlan'].shouldStop = True
  assert select(owner, sm) == log.Desire.none
  params.remove(KEY)
  assert select(TurnIntent(params, cp), sm) == log.Desire.none


def test_galaxy_canonical_toggle_and_save(case):
  params, cp, _ = case
  params.remove(KEY)
  owner = FeatureSettingsOwner(params, lambda group: True, vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp)
  row = next(row for row in owner.snapshot(FeaturePage.TORQUE, parked=True, system_long=False, lateral_context=True, metric=False).rows if row.key == GUIDANCE)
  assert row.label == 'Turn Signal Guidance' and row.value == 'On' and row.available
  request = row_change(row, 1)
  assert request is not None
  request = replace(request, confirmation=True)
  assert owner.apply(request)
  assert not params.get(KEY)['turnSignalGuidance']
  assert not owner.apply(request)


def test_old_preferences_remain_default_on():
  old = to_value(LaneChangePolicy())
  old['version'] = 4
  del old['turnSignalGuidance']
  del old['autoRequiresCruise']
  assert decode(json.dumps(old).encode()) == LaneChangePolicy()


def test_independent_of_lane_change_master(case):
  params, cp, sm = case
  params.put(KEY, to_value(replace(LaneChangePolicy(), enabled=False, turn_signal_guidance=True)), block=True)
  assert select(TurnIntent(params, cp), sm) == log.Desire.turnLeft


@pytest.mark.parametrize('field', ['passive', 'dashcamOnly', 'notCar'])
def test_passive_vehicle_never_guided(case, field):
  params, cp, sm = case
  setattr(cp, field, True)
  assert select(TurnIntent(params, cp), sm) == log.Desire.none


@pytest.mark.parametrize('modifier', ['straight', 'right'])
def test_guiding_route_does_not_hide_user_signal(case, modifier):
  params, cp, sm = case
  sm.valid['starpilotNavigation'] = sm.alive['starpilotNavigation'] = True
  sm.logMonoTime['starpilotNavigation'] = NOW
  instruction = NS(maneuverType='turn', maneuverModifier=modifier, distanceMeters=30.)
  nav = NS(version=2, enabled=True, controlValid=True, status='guiding', sessionId='producer', revision='one',
           frameMonoTime=NOW, startedMonoTime=1, locationMonoTime=NOW, instruction=instruction, nextManeuver=instruction)
  sm['starpilotNavigation'] = NS(navigation=nav)
  assert select(TurnIntent(params, cp), sm) == log.Desire.turnLeft


@pytest.mark.parametrize('speed,expected', [(10., log.LaneChangeState.off), (25., log.LaneChangeState.preLaneChange)])
def test_actual_helper_turning_and_lane_change_regimes(case, speed, expected):
  from openpilot.common.constants import CV
  from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
  params, cp, sm = case
  sm['carState'].vEgo = speed * CV.MPH_TO_MS
  helper = DesireHelper(replace(LaneChangePolicy(), turn_signal_guidance=True))
  helper.update(sm['carState'], True, 0.)
  assert helper.lane_change_state == expected
  result = select(TurnIntent(params, cp), sm, helper.desire)
  assert result == (log.Desire.turnLeft if speed < 20 else log.Desire.none)


def test_explicit_off_preserves_existing_low_speed_lane_change(case):
  from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
  _, _, sm = case
  helper = DesireHelper(replace(LaneChangePolicy(), turn_signal_guidance=False))
  helper.update(sm['carState'], True, 0.)
  assert helper.lane_change_state == log.LaneChangeState.preLaneChange


@pytest.mark.parametrize('version', [1, 2, 3, 4])
def test_legacy_preferences_enable_guidance_without_writes(case, version):
  params, cp, sm = case
  value = to_value(LaneChangePolicy())
  value['version'] = version
  del value['turnSignalGuidance']
  del value['autoRequiresCruise']
  if version < 4:
    del value['durationS']
  if version < 3:
    del value['closeGap']
    del value['closeGapSeconds']
  if version < 2:
    for key in ('autoLaneChange', 'autoDelayS', 'minimumLaneWidthM'):
      del value[key]
  params.put(KEY, value, block=True)
  before = params.get(KEY)
  assert select(TurnIntent(params, cp), sm) == log.Desire.turnLeft
  assert params.get(KEY) == before


def test_missing_preferences_default_on_but_explicit_off_retained(case):
  params, cp, sm = case
  params.remove(KEY)
  assert select(TurnIntent(params, cp), sm) == log.Desire.turnLeft
  params.put(KEY, to_value(replace(LaneChangePolicy(), turn_signal_guidance=False)), block=True)
  assert select(TurnIntent(params, cp), sm) == log.Desire.none
  assert not params.get(KEY)['turnSignalGuidance']
