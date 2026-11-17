import json
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch, Mock

import pytest

from opendbc.car.honda.interface import CarInterface as HondaInterface
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.hyundai.interface import CarInterface as HyundaiInterface
from opendbc.car.hyundai.values import CAR as HYUNDAI
from opendbc.car.toyota.interface import CarInterface as ToyotaInterface
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling, CurveCeiling
from openpilot.starpilot.longitudinal.encouragement import BOOST_RATE, Encouragement, MAX_BOOST, SOURCES, NudgePreferences, SETTLE_SECONDS
from openpilot.starpilot.longitudinal.tests.test_conditional_handoff import Frame, NOW
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import ACTIVE


class PlannerOptions(TypedDict, total=False):
  curve_ceiling: CurveCeiling
  cruise_ceiling: CruiseCeiling
  traffic_mode: bool


def frame(tick):
  sm = Frame()
  now = NOW + tick * 50_000_000
  sm.logMonoTime = dict.fromkeys(sm, now - 5_000_000)
  sm['selfdriveState'].experimentalMode = True
  sm['modelV2'].action.desiredAcceleration = 0.
  sm['modelV2'].action.shouldStop = False
  sm['radarState'].leadOne.present = sm['radarState'].leadTwo.present = False
  return sm, now


def cp():
  return HondaInterface.get_non_essential_params(HONDA.HONDA_CIVIC)


def step(owner, tick, *, observed_accel=None, **changes):
  sm, now = frame(tick)
  if observed_accel is not None:
    sm['carState'].aEgo = observed_accel
  args = {'now_ns': now, 'drive_id': 1, 'dt': .05, 'target_speed': 27., 'model_accel': 0.,
              'mpc_accel': .8, 'cruise_accel': .8, 'lateral_accel': 0., 'blocked': False}
  args.update(changes)
  return owner.update(sm, cp(), **args)


@pytest.mark.parametrize('model_accel,observed_accel', [(0., .35), (.4, .4), (.7, .7)])
def test_normal_recovery_receives_no_extra_acceleration(model_accel, observed_accel):
  owner = Encouragement()
  for tick in range(240):
    assert step(owner, tick, model_accel=model_accel, observed_accel=observed_accel) == model_accel
    assert owner.boost == 0.


def test_minor_deficit_and_short_hesitation_do_not_trigger():
  owner = Encouragement()
  for tick in range(120):
    assert step(owner, tick, target_speed=20.8) == 0.
  for tick in range(120, 140):
    assert step(owner, tick) == 0.
  assert step(owner, 140, observed_accel=.7) == 0.
  assert owner.clear_seconds == owner.boost == 0.
  for tick in range(141, 161):
    assert step(owner, tick) == 0.


def test_persistent_weak_recovery_gets_help_within_four_seconds():
  owner = Encouragement()
  values = [step(owner, tick, observed_accel=.2, model_accel=.2) for tick in range(80)]
  assert values[:20] == [.2] * 20
  assert values[-1] == pytest.approx(MAX_BOOST)
  assert max(b - a for a, b in zip(values, values[1:], strict=False)) <= BOOST_RATE * .05 + 1e-9


@pytest.mark.parametrize('factory,car', [(HondaInterface, HONDA.HONDA_CIVIC),
                                       (HyundaiInterface, HYUNDAI.HYUNDAI_IONIQ_6),
                                       (ToyotaInterface, TOYOTA.TOYOTA_RAV4_TSS2)])
def test_native_planner_helps_weak_clear_road_recovery_toward_set_speed(factory, car):
  params = factory.get_non_essential_params(car)
  params.openpilotLongitudinalControl = True
  planner = LongitudinalPlanner(params, init_v=20.)
  outputs = []
  for tick in range(80):
    sm, now = frame(tick)
    sm['carState'].aEgo = .2
    sm['carState'].vCruise = 22.24 * 3.6
    sm['modelV2'].action.desiredAcceleration = .2
    planner.update(sm, now_ns=now)
    assert planner.mpc.solution_status == 0
    assert planner.output_a_target <= planner.a_cruise + 1e-6
    assert not planner.output_should_stop
    outputs.append(planner.output_a_target)
  assert outputs[-1] >= .5
  for tick in range(80, 82):
    sm, now = frame(tick)
    sm['carState'].aEgo = .2
    sm['carState'].vCruise = 22.24 * 3.6
    sm['modelV2'].action.desiredAcceleration = .2
    planner.update(sm, now_ns=now)
  assert planner.encouragement.boost == pytest.approx(MAX_BOOST)


def test_started_recovery_keeps_building_assistance_past_weak_acceleration():
  owner = Encouragement()
  for tick in range(60):
    step(owner, tick, model_accel=.2, observed_accel=.2)
  assert .2 < owner.boost < MAX_BOOST
  for tick in range(60, 100):
    step(owner, tick, model_accel=.2, observed_accel=.4)
  assert owner.boost == pytest.approx(MAX_BOOST)


def test_recovery_floor_does_not_stack_on_model_acceleration():
  owner = Encouragement()
  for tick in range(250):
    step(owner, tick)
  assert owner.boost == pytest.approx(MAX_BOOST)
  assert step(owner, 250, model_accel=.2) == pytest.approx(MAX_BOOST)
  assert step(owner, 251, model_accel=.7, observed_accel=.4) == pytest.approx(.7)
  assert step(owner, 252, mpc_accel=.25) == pytest.approx(.25)
  assert step(owner, 253, cruise_accel=.2) == pytest.approx(.2)


@pytest.mark.parametrize('factory,car', [(HondaInterface, HONDA.HONDA_CIVIC),
                                       (HyundaiInterface, HYUNDAI.HYUNDAI_IONIQ_6),
                                       (ToyotaInterface, TOYOTA.TOYOTA_RAV4_TSS2)])
def test_native_planner_normal_acceleration_is_unchanged(factory, car):
  params = factory.get_non_essential_params(car)
  params.openpilotLongitudinalControl = True
  original, current = (LongitudinalPlanner(params, init_v=20.) for _ in range(2))
  for tick in range(160):
    sm, now = frame(tick)
    sm['carState'].aEgo = .4
    sm['modelV2'].action.desiredAcceleration = .4
    with patch.object(original.encouragement, 'update', side_effect=lambda *a, **kw: kw['model_accel']):
      original.update(sm, now_ns=now)
    current.update(sm, now_ns=now)
    assert current.output_a_target == original.output_a_target
    assert current.output_should_stop == original.output_should_stop
    assert current.encouragement.boost == 0.


def test_ramp_settling_bound_and_near_target_taper():
  owner = Encouragement()
  values = [step(owner, tick) for tick in range(250)]
  settling_ticks = int(SETTLE_SECONDS / .05) - 1
  assert values[:settling_ticks] == [0.] * settling_ticks
  assert values[-1] == pytest.approx(MAX_BOOST)
  assert all(0 <= b - a <= BOOST_RATE * .05 + 1e-9 for a, b in zip(values, values[1:], strict=False))
  assert step(owner, 250, target_speed=20.3) == pytest.approx(MAX_BOOST / 18.)
  assert step(owner, 251, target_speed=20.) == 0.
  assert owner.boost == 0.


@pytest.mark.parametrize('field', ['blocked', 'negative', 'curve', 'stale', 'future', 'invalid', 'dead', 'frequency',
                                  'missing', 'radar_fault', 'lead1', 'lead2', 'stop', 'brake', 'gas', 'force', 'off', 'standard',
                                  'standstill', 'nan', 'duplicate', 'drive'])
def test_immediate_withdrawal_and_fresh_ramp(field):
  owner = Encouragement()
  for tick in range(90):
    step(owner, tick)
  assert owner.boost > 0
  sm, now = frame(90)
  args = {'now_ns': now, 'drive_id': 1, 'dt': .05, 'target_speed': 27., 'model_accel': 0.,
              'mpc_accel': .8, 'cruise_accel': .8, 'lateral_accel': 0., 'blocked': False}
  if field == 'blocked':
    args['blocked'] = True
  if field == 'negative':
    args['model_accel'] = -.001
  if field == 'curve':
    args['lateral_accel'] = .5
  if field == 'stale':
    sm.logMonoTime['modelV2'] = now - 250_000_001
  if field == 'future':
    sm.logMonoTime['radarState'] = now + 1
  if field == 'invalid':
    sm.valid['carState'] = False
  if field == 'dead':
    sm.alive['radarState'] = False
  if field == 'frequency':
    sm.freq_ok = {'modelV2': False}
  if field == 'missing':
    del sm.logMonoTime['carState']
  if field == 'radar_fault':
    sm['radarState'].radarErrors.radarFault = True
  if field == 'lead1':
    sm['radarState'].leadOne.present = True
  if field == 'lead2':
    sm['radarState'].leadTwo.present = True
  if field == 'stop':
    sm['modelV2'].action.shouldStop = True
  if field == 'brake':
    sm['carState'].brakePressed = True
  if field == 'gas':
    sm['carState'].gasPressed = True
  if field == 'force':
    sm['controlsState'].forceDecel = True
  if field == 'off':
    sm['carControl'].longActive = False
  if field == 'standard':
    sm['selfdriveState'].experimentalMode = False
  if field == 'standstill':
    sm['carState'].standstill = True
  if field == 'nan':
    args['target_speed'] = float('nan')
  if field == 'duplicate':
    sm.logMonoTime['modelV2'] = owner.last_model_ns
  if field == 'drive':
    args['drive_id'] = 2
  assert owner.update(sm, cp(), **args) == args['model_accel']
  assert owner.boost == 0.
  assert step(owner, 91) == 0.


@pytest.mark.parametrize('source', SOURCES)
def test_each_input_must_be_fresh(source):
  owner = Encouragement()
  for tick in range(90):
    step(owner, tick)
  sm, now = frame(90)
  sm.logMonoTime[source] = now - 250_000_001
  assert owner.update(sm, cp(), now_ns=now, drive_id=1, dt=.05, target_speed=27., model_accel=0.,
                      mpc_accel=.8, cruise_accel=.8, lateral_accel=0., blocked=False) == 0.


@pytest.mark.parametrize('factory,car', [(HondaInterface, HONDA.HONDA_CIVIC),
                                       (HyundaiInterface, HYUNDAI.HYUNDAI_IONIQ_6),
                                       (ToyotaInterface, TOYOTA.TOYOTA_RAV4_TSS2)])
def test_real_native_planner_clear_road_across_families(factory, car):
  params = factory.get_non_essential_params(car)
  params.openpilotLongitudinalControl = True
  original, current = (LongitudinalPlanner(params, init_v=20.) for _ in range(2))
  for tick in range(120):
    sm, now = frame(tick)
    with patch.object(original.encouragement, 'update', side_effect=lambda *a, **kw: kw['model_accel']):
      original.update(sm, now_ns=now)
    current.update(sm, now_ns=now)
    assert current.mpc.solution_status == original.mpc.solution_status == 0
    assert current.output_a_target >= original.output_a_target
    assert current.output_a_target <= current.a_cruise + 1e-6
    assert current.output_a_target - original.output_a_target <= MAX_BOOST + 1e-6
  assert current.output_a_target > original.output_a_target + .05


@pytest.mark.parametrize('case', ['model_brake', 'stop', 'lead1', 'lead2', 'curve', 'slc', 'turn', 'force',
                                 'throttle', 'standard', 'brake', 'gas', 'stale', 'traffic'])
def test_native_planner_limits_and_immediate_withdrawal(case):
  original, current = (LongitudinalPlanner(cp(), init_v=20.) for _ in range(2))
  for tick in range(100):
    sm, now = frame(tick)
    options: PlannerOptions = {}
    if tick == 90:
      original.output_a_target = current.output_a_target
      original.a_cruise = current.a_cruise
    if tick >= 90:
      if case == 'model_brake':
        sm['modelV2'].action.desiredAcceleration = -.01
      if case == 'stop':
        sm['modelV2'].action.shouldStop = True
      if case in ('lead1', 'lead2'):
        lead = sm['radarState'].leadOne if case == 'lead1' else sm['radarState'].leadTwo
        lead.present, lead.dRel, lead.vLead, lead.vLeadK, lead.vRel = True, 35., 15., 15., -5.
      if case == 'curve':
        options['curve_ceiling'] = CurveCeiling(19., sm.logMonoTime['modelV2'])
      if case == 'slc':
        options['cruise_ceiling'] = CruiseCeiling(19., ACTIVE)
      if case == 'turn':
        sm['carState'].steeringAngleDeg = 5.
      if case == 'force':
        sm['controlsState'].forceDecel = True
      if case == 'throttle':
        sm['modelV2'].meta.disengagePredictions.gasPressProbs = [0.] * 6
      if case == 'standard':
        sm['selfdriveState'].experimentalMode = False
      if case == 'brake':
        sm['carState'].brakePressed = True
      if case == 'gas':
        sm['carState'].gasPressed = True
      if case == 'stale':
        sm.logMonoTime['modelV2'] = now - 250_000_001
      if case == 'traffic':
        options['traffic_mode'] = True
    with patch.object(original.encouragement, 'update', side_effect=lambda *a, **kw: kw['model_accel']):
      original.update(sm, now_ns=now, **options)
    current.update(sm, now_ns=now, **options)
    assert current.mpc.solution_status == original.mpc.solution_status == 0
    if tick >= 90:
      assert current.output_a_target <= original.output_a_target + 1e-6
      assert current.encouragement.boost == 0.
      assert current.output_should_stop == original.output_should_stop


@pytest.mark.parametrize('factory,car', [(HondaInterface, HONDA.HONDA_CIVIC),
                                       (HyundaiInterface, HYUNDAI.HYUNDAI_IONIQ_6),
                                       (ToyotaInterface, TOYOTA.TOYOTA_RAV4_TSS2)])
def test_native_planner_override_release_uses_higher_request_without_stacking(factory, car):
  params = factory.get_non_essential_params(car)
  params.openpilotLongitudinalControl = True
  planner = LongitudinalPlanner(params, init_v=20.)
  for tick in range(220):
    sm, now = frame(tick)
    planner.update(sm, now_ns=now)
  assert planner.encouragement.boost == pytest.approx(MAX_BOOST)
  for tick in range(220, 260):
    sm, now = frame(tick)
    sm['carState'].gasPressed = True
    planner.update(sm, now_ns=now)
    assert planner.encouragement.boost == 0.
  assert planner.accel_boost.total_boost == pytest.approx(.05)
  for tick in range(260, 480):
    sm, now = frame(tick)
    planner.update(sm, now_ns=now)
    assert planner.mpc.solution_status == 0
    assert planner.accel_boost.total_boost == pytest.approx(.05)
    assert planner.output_a_target <= max(.05, planner.encouragement.boost) + 1e-6
    assert planner.output_a_target <= planner.a_cruise + 1e-6
  assert planner.encouragement.boost == pytest.approx(MAX_BOOST)
  assert planner.output_a_target == pytest.approx(MAX_BOOST, abs=.01)
  sm, now = frame(480)
  sm['selfdriveState'].enabled = False
  planner.update(sm, now_ns=now)
  assert planner.accel_boost.total_boost == planner.encouragement.boost == 0.


def test_native_planner_preserves_manual_override_input_and_boost():
  planner = LongitudinalPlanner(cp(), init_v=20.)
  for tick in range(120):
    sm, now = frame(tick)
    with patch.object(planner.accel_boost, 'update', wraps=planner.accel_boost.update) as manual:
      planner.update(sm, now_ns=now)
    assert manual.call_args.args[1] == 0.
  assert planner.encouragement.boost > 0.
  assert planner.accel_boost.boost_eligible
  for tick in range(120, 160):
    sm, now = frame(tick)
    sm['carState'].gasPressed = True
    planner.update(sm, now_ns=now)
    assert planner.encouragement.boost == 0.
  assert planner.accel_boost.total_boost == pytest.approx(.05)


@pytest.mark.parametrize('factory,car', [(HondaInterface, HONDA.HONDA_CIVIC),
                                       (HyundaiInterface, HYUNDAI.HYUNDAI_IONIQ_6),
                                       (ToyotaInterface, TOYOTA.TOYOTA_RAV4_TSS2)])
def test_native_planner_reaches_clear_road_boost_plateau(factory, car):
  params = factory.get_non_essential_params(car)
  params.openpilotLongitudinalControl = True
  planner = LongitudinalPlanner(params, init_v=20.)
  for tick in range(220):
    sm, now = frame(tick)
    planner.update(sm, now_ns=now)
    assert planner.mpc.solution_status == 0
    assert planner.output_a_target <= planner.a_cruise + 1e-6
  assert planner.encouragement.boost == pytest.approx(MAX_BOOST)
  assert planner.output_a_target == pytest.approx(MAX_BOOST, abs=.01)


@pytest.mark.parametrize('factory,car', [(HondaInterface, HONDA.HONDA_CIVIC),
                                       (HyundaiInterface, HYUNDAI.HYUNDAI_IONIQ_6),
                                       (ToyotaInterface, TOYOTA.TOYOTA_RAV4_TSS2)])
def test_native_planner_recovers_despite_recorded_acceleration_noise(factory, car):
  tape = json.loads((Path(__file__).parent / 'fixtures' / 'encouragement_clear_road_noise.json').read_text())
  params = factory.get_non_essential_params(car)
  params.openpilotLongitudinalControl = True
  planner = LongitudinalPlanner(params, init_v=tape['rows'][0][0])
  maximum_boost = maximum_extra = 0.
  for tick in range(40):
    sm, now = frame(tick)
    sm['carState'].vEgo = tape['rows'][0][0]
    sm['modelV2'].action.desiredAcceleration = -.1
    planner.update(sm, now_ns=now, curve_ceiling=CurveCeiling(20., sm.logMonoTime['modelV2']))
    assert planner.encouragement.boost == 0.
    assert planner.output_a_target <= planner.a_cruise + 1e-6
  for offset, (speed, accel, model_accel, set_kph, experimental) in enumerate(tape['rows']):
    sm, now = frame(offset + 40)
    sm['carState'].vEgo, sm['carState'].aEgo, sm['carState'].vCruise = speed, accel, set_kph
    sm['selfdriveState'].experimentalMode = experimental
    sm['modelV2'].action.desiredAcceleration = model_accel
    planner.update(sm, now_ns=now)
    assert planner.mpc.solution_status == 0
    assert planner.output_a_target <= planner.a_cruise + 1e-6
    assert not planner.output_should_stop
    maximum_boost = max(maximum_boost, planner.encouragement.boost)
    maximum_extra = max(maximum_extra, planner.output_a_target - model_accel)
  assert maximum_boost > .1
  assert maximum_extra > .05


@pytest.mark.parametrize('raw,readable,enabled', [(None, True, True), (b'1', True, True), (b'0', True, False),
                                               (b'true', True, False), (None, False, False)])
def test_speed_nudge_preference_defaults_on_and_rejects_invalid_sources(raw, readable, enabled):
  with patch('openpilot.starpilot.longitudinal.encouragement.read_saved', return_value=(raw, readable)) as read:
    preferences = NudgePreferences(object())
    assert preferences.sample(1_000_000_000) is enabled
    assert preferences.sample(1_500_000_000) is enabled
    assert read.call_count == 1
    read.return_value = (b'0', True)
    assert not preferences.sample(2_000_000_000)
    assert read.call_count == 2
    read.return_value = (b'1', True)
    assert preferences.sample(500_000_000)
    assert read.call_count == 3


@pytest.mark.parametrize('factory,car', [(HondaInterface, HONDA.HONDA_CIVIC),
                                       (HyundaiInterface, HYUNDAI.HYUNDAI_IONIQ_6),
                                       (ToyotaInterface, TOYOTA.TOYOTA_RAV4_TSS2)])
def test_native_planner_speed_nudge_off_removes_entire_automatic_floor(factory, car):
  params = factory.get_non_essential_params(car)
  params.openpilotLongitudinalControl = True
  planner = LongitudinalPlanner(params, init_v=20.)
  for tick in range(220):
    sm, now = frame(tick)
    planner.update(sm, now_ns=now)
  assert planner.encouragement.boost > 0.
  for tick in range(220, 300):
    sm, now = frame(tick)
    planner.update(sm, now_ns=now, experimental_speed_nudge=False)
    assert planner.encouragement.boost == 0.
    assert planner.output_a_target == 0.
  for tick in range(300, 520):
    sm, now = frame(tick)
    planner.update(sm, now_ns=now, experimental_speed_nudge=True)
  assert planner.encouragement.boost == pytest.approx(MAX_BOOST)


@pytest.mark.parametrize('host', [None, object()])
def test_plannerd_forwards_speed_nudge_choice_through_both_curve_paths(host):
  from openpilot.selfdrive.controls.plannerd import update_curve_frame
  planner = Mock()
  sm, now = frame(1)
  with patch('openpilot.selfdrive.controls.plannerd.confirm_curve_frame'):
    update_curve_frame(planner, sm, cp(), now, host=host, experimental_speed_nudge=False)
  assert planner.update.call_args.kwargs['experimental_speed_nudge'] is False
