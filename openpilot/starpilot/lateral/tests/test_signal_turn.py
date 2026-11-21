"""Forecast-qualified turns preserve lane changes, source guards and native limits."""
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from opendbc.car import structs
from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature
from openpilot.starpilot.lateral.lane_change_preferences import KEY, LaneChangePolicy, to_value
from openpilot.starpilot.lateral.lane_change_smoothing import LaneChangeSmoother, limit_rate
from openpilot.starpilot.lateral.signal_turn import SignalTurnEvidence
from openpilot.starpilot.lateral.turn_initiation_preferences import KEY as TURN_INITIATION_KEY
from openpilot.starpilot.navigation.intent import TurnIntent

BASE, DRIVE, OFFSET = 10_000_000_000, 9_000_000_000, 2_000_000_000


def forecast(desire, probability=.75):
  row = [0.] * 8
  row[0], row[int(desire)] = 1. - probability, probability
  return row * 4


def frame(tick, direction=-1):
  now = BASE + tick * 50_000_000
  class SM(dict):
    pass
  model = log.ModelDataV2.new_message(timestampEof=now + OFFSET)
  model.meta.desirePrediction = forecast(log.Desire.turnLeft if direction == -1 else log.Desire.turnRight)
  car = structs.CarState.new_message(vEgo=12., aEgo=-1., canValid=True, gearShifter='drive',
                                     leftBlinker=direction == -1, rightBlinker=direction == 1)
  sm = SM(carState=car, carControl=NS(latActive=True), deviceState=NS(started=True, startedMonoTime=DRIVE),
          longitudinalPlan=NS(shouldStop=False, modelMonoTime=now), modelV2=model)
  sm.valid, sm.alive = dict.fromkeys(sm, True), dict.fromkeys(sm, True)
  sm.logMonoTime, sm.recv_time = dict.fromkeys(sm, now), dict.fromkeys(sm, now / 1e9)
  return sm, now, model


def observe(owner, tick, direction=-1, change=None, **kwargs):
  sm, now, model = frame(tick, direction)
  if change is not None:
    change(sm, model)
  args = {'model_ns': sm.logMonoTime['modelV2'], 'now_ns': now, 'now_boot_ns': now + OFFSET, 'valid': True, 'enabled': True}
  return owner.observe(sm, model, **(args | kwargs)), sm, now


@pytest.mark.parametrize('direction', [-1, 1])
def test_confirmed_forecast_admits_earlier_hint_and_cancels_pending_lane_change(tmp_path, direction):
  params = Params(str(tmp_path))
  params.put(KEY, to_value(LaneChangePolicy()), block=True)
  cp = structs.CarParams.new_message(carFingerprint='real', brand='hyundai', steerControlType='torque')
  cp.safetyConfigs = [structs.CarParams.SafetyConfig.new_message(safetyModel='hyundaiCanfd')]
  params.put_bool(TURN_INITIATION_KEY, True, block=True)
  intent, helper = TurnIntent(params, cp), DesireHelper()
  sm, now, _ = frame(-1, direction)
  sm['carState'].leftBlinker = sm['carState'].rightBlinker = False
  assert intent.select(sm, now, helper.desire, supported=True, model_stop=False) == log.Desire.none
  assert intent.automatic_turn_enabled
  for tick in range(5):
    sm, now, model = frame(tick, direction)
    with patch('openpilot.starpilot.lateral.signal_turn.boottime_ns', return_value=now + OFFSET):
      selected = intent.select(sm, now, helper.desire, supported=True, model_stop=False)
    ready = intent.signal_model.observe(sm, model, model_ns=now, now_ns=now, now_boot_ns=now + OFFSET,
                                        valid=True, enabled=True, lane_change_state=helper.lane_change_state)
    helper.update(sm['carState'], True, 1., navigation_turn=ready)
    if tick == 0:
      assert helper.lane_change_state == log.LaneChangeState.preLaneChange
    if tick < 3:
      assert selected == log.Desire.none
    else:
      assert selected == (log.Desire.turnLeft if direction == -1 else log.Desire.turnRight)
      assert helper.lane_change_state == log.LaneChangeState.off
  sm['carState'].steeringPressed = True
  with patch('openpilot.starpilot.lateral.signal_turn.boottime_ns', return_value=now + OFFSET):
    assert intent.select(sm, now, helper.desire, supported=True, model_stop=False) == log.Desire.none
  helper.update(sm['carState'], True, 1., navigation_turn=ready)
  assert helper.lane_change_state == log.LaneChangeState.off


@pytest.mark.parametrize('direction', [-1, 1])
def test_ordinary_lane_changes_and_existing_maneuvers_are_not_reclassified(direction):
  evidence, helper = SignalTurnEvidence(), DesireHelper()
  for tick in range(5):
    def lane(sm, model, tick=tick):
      model.meta.desirePrediction = forecast(log.Desire.laneChangeLeft if direction == -1 else log.Desire.laneChangeRight)
      model.meta.desireState = forecast(log.Desire.turnLeft if direction == -1 else log.Desire.turnRight)[:8]
      sm['carState'].steeringPressed = tick > 0
      sm['carState'].steeringTorque = 1. if direction == -1 else -1.
    ready, sm, _ = observe(evidence, tick, direction, lane)
    assert not ready
    helper.update(sm['carState'], True, 1., navigation_turn=ready)
  assert helper.desire == (log.Desire.laneChangeLeft if direction == -1 else log.Desire.laneChangeRight)
  for tick in range(5, 10):
    assert not observe(evidence, tick, direction,
                       lambda sm, model: setattr(model.meta, 'laneChangeState', log.LaneChangeState.laneChangeStarting))[0]


@pytest.mark.parametrize('field,value', [('standstill', True), ('canValid', False), ('canTimeout', True),
  ('steerFaultTemporary', True), ('steerFaultPermanent', True), ('gearShifter', 'reverse'), ('rightBlinker', True),
  ('leftBlindspot', True), ('vEgo', 14.), ('vEgo', float('nan')), ('aEgo', 0.), ('aEgo', float('inf'))])
def test_vehicle_guards_withdraw_confirmation(field, value):
  owner = SignalTurnEvidence()
  for tick in range(3):
    ready, _, _ = observe(owner, tick)
  assert ready
  assert not observe(owner, 3, change=lambda sm, model: setattr(sm['carState'], field, value))[0]


@pytest.mark.parametrize('service', ['carState', 'carControl', 'deviceState', 'longitudinalPlan'])
@pytest.mark.parametrize('failure', ['stale', 'future', 'dead', 'invalid', 'receipt'])
def test_source_guards(service, failure):
  def change(sm, model):
    if failure == 'stale':
      sm.logMonoTime[service] = DRIVE - 2_000_000_000
    if failure == 'future':
      sm.logMonoTime[service] += 1
    if failure == 'dead':
      sm.alive[service] = False
    if failure == 'invalid':
      sm.valid[service] = False
    if failure == 'receipt':
      sm.recv_time[service] = (DRIVE - 2_000_000_000) / 1e9
  owner = SignalTurnEvidence()
  for tick in range(3):
    observe(owner, tick)
  assert not observe(owner, 3, change=change)[0]


def test_invalid_forecasts_stops_disabling_and_suspend_refuse_turn():
  changes = [lambda sm, m: setattr(m.action, 'shouldStop', True),
             lambda sm, m: setattr(sm['longitudinalPlan'], 'shouldStop', True),
             lambda sm, m: setattr(sm['carControl'], 'latActive', False),
             lambda sm, m: setattr(sm['deviceState'], 'started', False),
             lambda sm, m: setattr(m, 'timestampEof', 1),
             lambda sm, m: setattr(m.meta, 'desirePrediction', [0.] * 32),
             lambda sm, m: setattr(m.meta, 'desirePrediction', [float('nan')] * 32),
             lambda sm, m: setattr(m.meta, 'desirePrediction', forecast(log.Desire.turnRight)),
             lambda sm, m: setattr(m.meta, 'desirePrediction', forecast(log.Desire.turnLeft)[:8])]
  for change in changes:
    owner = SignalTurnEvidence()
    for tick in range(3):
      observe(owner, tick)
    assert not observe(owner, 3, change=change)[0]
  owner = SignalTurnEvidence()
  for tick in range(3):
    ready, sm, now = observe(owner, tick)
  assert ready
  assert not owner.current(sm, now, now_boot_ns=now + OFFSET + 1_000_000_000)
  assert not observe(owner, 3, clock_skew_ns=1_000_001)[0]
  assert not observe(owner, 4, enabled=False)[0]
  assert not observe(owner, 5, valid=False)[0]


def test_repeated_frames_cannot_confirm():
  owner = SignalTurnEvidence()
  for tick in range(3):
    assert not observe(owner, tick, change=lambda sm, model: sm.logMonoTime.__setitem__('modelV2', BASE))[0]


def test_forecast_exemption_retains_native_curvature_and_jerk_guard():
  smoother, previous = LaneChangeSmoother(), 0.
  for tick in range(200):
    state = log.LaneChangeState.laneChangeStarting if tick < 150 else log.LaneChangeState.off
    desired = .04 if tick < 100 else -.01
    factor = smoother.factor(active=True, lane_change_state=state, speed=11., minimum_speed=0., duration=5.777777777777778,
                             previous=previous, desired=desired, turn_assist=True, intersection_turn=tick == 0)
    assert factor == 1.
    output, _ = clip_curvature(11., previous, limit_rate(11., previous, desired, factor), 0.)
    assert output == clip_curvature(11., previous, desired, 0.)[0]
    previous = output


def test_recorded_turn_forecasts_and_lane_change_heads_remain_distinct():
  import json
  from pathlib import Path
  recorded = json.loads((Path(__file__).parent / 'fixtures/signal_turn_forecasts.json').read_text())
  for name, direction in (('left_forecast', -1), ('right_forecast', 1)):
    owner = SignalTurnEvidence()
    ready = False
    for row in recorded[name]:
      sm, _, model = frame(0, direction)
      now = BASE + row['dt_ns']
      sm.logMonoTime = dict.fromkeys(sm, now)
      sm.recv_time = dict.fromkeys(sm, now / 1e9)
      sm['carState'].vEgo, sm['carState'].aEgo = row['speed'], row['acceleration']
      sm['longitudinalPlan'].modelMonoTime = now
      model.timestampEof = now + OFFSET
      model.meta.desirePrediction = row['prediction']
      ready = owner.observe(sm, model, model_ns=now, now_ns=now, now_boot_ns=now + OFFSET, valid=True, enabled=True)
    assert ready
  for row in recorded['lane_change_negatives']:
    owner = SignalTurnEvidence()
    for tick in range(6):
      # Put the recorded lane-change forecast inside every other turn gate.
      ready, _, _ = observe(owner, tick, row['direction'],
                            lambda sm, model, prediction=row['prediction']: setattr(model.meta, 'desirePrediction', prediction))
      assert not ready


def test_forecast_never_replaces_opposite_near_curvature():
  smoother = LaneChangeSmoother()
  factor = smoother.factor(active=True, lane_change_state=log.LaneChangeState.preLaneChange, speed=9., minimum_speed=0.,
                           duration=5.777777777777778, previous=0., desired=.0015, turn_assist=True, intersection_turn=True)
  output, _ = clip_curvature(9., 0., limit_rate(9., 0., .0015, factor), 0.)
  assert output >= 0.
