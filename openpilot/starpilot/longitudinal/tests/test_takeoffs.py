"""One opt-in, shared launch admission and unchanged vehicle-owned limits."""
from dataclasses import replace
from unittest.mock import patch

import pytest
import numpy as np
from opendbc.car.honda.interface import CarInterface as Honda
from opendbc.car.honda.values import CAR as H
from opendbc.car.toyota.interface import CarInterface as Toyota
from opendbc.car.toyota.values import CAR as T
from openpilot.common.params import Params
from openpilot.cereal import messaging
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway
from openpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner, get_cruise_accel, get_max_accel
from openpilot.starpilot.longitudinal.extension import LongitudinalContext
from openpilot.starpilot.longitudinal.inputs import LongitudinalInputs
from openpilot.starpilot.longitudinal.ioniq6_start import StartEvidence
from openpilot.starpilot.longitudinal.profile_runtime import SelectedProfileTuning
from openpilot.starpilot.longitudinal.tests.test_ioniq6_start import candidate, car_state
from openpilot.starpilot.longitudinal.tests.test_conditional_handoff import Frame, NOW, DRIVE
from openpilot.starpilot.longitudinal.takeoffs import eligible


def evidence(tick=0, **changes):
  return replace(StartEvidence(True, True, False, 1, 1_000_000_000 + tick * 10_000_000), **changes)


def context(tick=0, **changes):
  return LongitudinalContext(faster_takeoffs=True, start_evidence=evidence(tick, **changes))


@pytest.mark.parametrize('factory', [lambda: Honda.get_non_essential_params(H.HONDA_CIVIC),
                                    lambda: Toyota.get_non_essential_params(T.TOYOTA_COROLLA_TSS2),
                                    lambda: candidate()[1]])
def test_actual_factory_longcontrol_first_start_target_bounds_and_ordinary_exit(factory):
  cp = factory()
  assert eligible(cp)
  ctrl = LongControl(cp)
  cs = car_state(standstill=False)
  output = ctrl.update(True, cs, 1.8, False, (np.float64(-3.), np.float64(1.5)), context=replace(context(), profile_max_accel=1.5))
  assert ctrl.takeoff_starting and ctrl.long_control_state == LongCtrlState.starting
  assert output <= 1.5
  ctrl.update(True, car_state(speed=.11, standstill=False), .8, False, (-3., 2.), context=context(1))
  assert not ctrl.takeoff_starting and ctrl.long_control_state == LongCtrlState.pid
  ctrl.update(False, cs, .8, False, (-3., 2.), context=context(2))
  assert ctrl.long_control_state == LongCtrlState.off


@pytest.mark.parametrize('kind', ['off', 'missing', 'stale', 'unknown_lead', 'brake', 'gas', 'can', 'timeout', 'stop', 'weak'])
def test_optin_never_creates_authority_from_missing_evidence_or_pedals(kind):
  ctrl = LongControl(candidate()[1])
  cs = car_state(standstill=False)
  target, stop = .9, False
  ctx = context()
  if kind == 'off':
    ctx = replace(ctx, faster_takeoffs=False)
  if kind == 'missing':
    ctx = replace(ctx, start_evidence=None)
  if kind == 'stale':
    ctx = context(plan_fresh=False)
  if kind == 'unknown_lead':
    ctx = context(lead_clear=None)
  if kind == 'brake':
    cs = car_state(brake=True)
  if kind == 'gas':
    cs = car_state(gas=True)
  if kind == 'can':
    cs = car_state(valid=False)
  if kind == 'timeout':
    cs = car_state(timeout=True)
  if kind == 'stop':
    stop = True
  if kind == 'weak':
    target = .74
  ctrl.update(True, cs, target, stop, (-3., 2.), context=ctx)
  assert not ctrl.takeoff_starting and ctrl.long_control_state != LongCtrlState.starting


@pytest.mark.parametrize('loss', ['toggle', 'evidence', 'duplicate', 'drive'])
def test_generic_starting_unwinds_when_optin_or_evidence_is_lost(loss):
  ctrl = LongControl(Honda.get_non_essential_params(H.HONDA_CIVIC))
  cs = car_state(standstill=False)
  ctrl.update(True, cs, .9, False, (-3., 2.), context=context())
  assert ctrl.takeoff_starting
  ctx = context(1)
  if loss == 'toggle':
    ctx = replace(ctx, faster_takeoffs=False)
  if loss == 'evidence':
    ctx = replace(ctx, start_evidence=None)
  if loss == 'duplicate':
    ctx = context()
  if loss == 'drive':
    ctx = context(1, drive_id=2)
  ctrl.update(True, cs, .9, False, (-3., 2.), context=ctx)
  assert not ctrl.takeoff_starting and ctrl.long_control_state == LongCtrlState.pid


def test_native_stock_standstill_and_non_longitudinal_configuration_still_win():
  cp = Honda.get_non_essential_params(H.HONDA_CIVIC)
  ctrl = LongControl(cp)
  ctrl.update(True, car_state(standstill=True), .9, False, (-3., 2.), context=context())
  assert not ctrl.takeoff_starting and ctrl.long_control_state == LongCtrlState.stopping
  cp.openpilotLongitudinalControl = False
  assert not eligible(cp)
  assert LongControl(cp).takeoff_start is None


def test_takeoff_budget_is_off_by_default_tapers_by_5ms_and_respects_selected_caps():
  cp = Honda.get_non_essential_params(H.HONDA_CIVIC)
  args = (False, 30., 0., 1.7, 0., cp, 1., 0., True)
  assert get_cruise_accel(*args, selected_acceleration_max=2.) == 1.7
  assert get_cruise_accel(*args, selected_acceleration_max=2., faster_takeoffs=True) == 2.
  assert get_cruise_accel(*args, selected_acceleration_max=.5, faster_takeoffs=True) == .5
  for speed in (5., 10., 20., 40.):
    args = (False, 50., speed, 4., 0., cp, .05, 0., True)
    assert get_cruise_accel(*args, faster_takeoffs=True) == get_cruise_accel(*args)


def test_actual_native_planner_uses_same_toggle_for_launch_budget():
  cp = Honda.get_non_essential_params(H.HONDA_CIVIC)
  off, enabled = LongitudinalPlanner(cp), LongitudinalPlanner(cp)
  for tick in range(65):
    sm = Frame()
    stamp = NOW + tick * 50_000_000
    sm.logMonoTime = dict.fromkeys(sm, stamp - 5_000_000)
    sm.recv_time = dict.fromkeys(sm, (stamp - 5_000_000) / 1e9)
    sm['carState'].vEgo, sm['carState'].standstill = 0., True
    sm['selfdriveState'].experimentalMode = False
    sm['carControl'].longActive = True
    sm['modelV2'].meta.disengagePredictions.gasPressProbs = [1., 1.]
    for planner, switch in ((off, False), (enabled, True)):
      planner.update(sm, now_ns=stamp, selected_profiles=SelectedProfileTuning(acceleration_max=2.),
                     faster_lead_takeoff=switch, takeoff_drive_id=DRIVE)
      assert planner.output_a_target <= 2.
  assert enabled.a_cruise > off.a_cruise
  assert off.a_cruise <= 1.7 and enabled.a_cruise <= 2.


def test_galaxy_single_toggle_roundtrip_global_and_both_runtime_readers(tmp_path):
  params = Params(str(tmp_path))
  cp = Honda.get_non_essential_params(H.HONDA_CIVIC)
  class ParkedContext:
    def sample(self) -> AuthorityContext:
      return AuthorityContext(True, cp, b'cp')
  gateway = SettingsGateway(params, ParkedContext(), clock=lambda: 10.)
  page = gateway.page('profiles', 'session', b'generation')
  rows = [(i, row) for i, row in enumerate(page['rows']) if row['label'] == 'Faster Takeoffs']
  assert len(rows) == 1
  index, row = rows[0]
  assert row['label'] == 'Faster Takeoffs' and row['value'] == 'Off'
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
  assert gateway.confirm(intent['intent'], 'session', b'generation')
  assert params.get_bool('FasterLeadTakeoff')
  inputs = LongitudinalInputs(cp, params, lambda: None)
  assert inputs.takeoff_preferences.sample(1_000_000_000)
  from openpilot.starpilot.longitudinal.lead_takeoff_preferences import TakeoffPreferences
  assert TakeoffPreferences(params).sample(1_000_000_000)


def test_real_ioniq_host_starting_and_packed_scc_same_first_tick():
  from opendbc.can import CANParser
  from opendbc.car import Bus, structs
  from opendbc.car.hyundai.values import DBC
  from opendbc.car.hyundai.tests.test_ioniq6_longitudinal import controller_fixture
  for alternate in (False, True):
    cp, state, controller = controller_fixture(alternate)
    host = LongControl(cp)
    parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [('SCC_CONTROL', 0)], 1)
    state.out.canValid, state.out.canTimeout = True, False
    state.out.vEgo, state.out.aEgo = 0., 0.
    control = structs.CarControl(enabled=True, longActive=True)
    for tick in range(8):
      target = 1.8
      output = host.update(True, state.out, target, False, (-3.5, 2.), context=context(tick))
      assert host.long_control_state == LongCtrlState.starting and output == target
      control.actuators.longControlState = host.long_control_state
      control.actuators.accel = float(output)
      sent, frames = controller.update(control.as_reader(), state, 1_000_000_000 + tick * 10_000_000)
      scc = [message for message in frames if message[0] == 0x1A0]
      if scc:
        parser.update((1_000_000_000 + tick * 10_000_000, scc))
        assert abs(parser.vl['SCC_CONTROL']['aReqValue'] - sent.accel) <= .0051
        assert sent.accel <= target
    assert controller.ioniq6_longitudinal.state.launch_active


@pytest.mark.parametrize('factory', [lambda: Honda.get_non_essential_params(H.HONDA_CIVIC),
                                    lambda: Toyota.get_non_essential_params(T.TOYOTA_COROLLA_TSS2),
                                    lambda: candidate()[1]])
def test_actual_inputs_qualify_current_serialized_drive_and_plan(factory, tmp_path):
  cp = factory()
  params = Params(str(tmp_path))
  params.put_bool('FasterLeadTakeoff', True, block=True)
  sm = Frame()
  sm['longitudinalPlan'] = messaging.new_message('longitudinalPlan').longitudinalPlan
  sm.seen['longitudinalPlan'] = sm.alive['longitudinalPlan'] = sm.valid['longitudinalPlan'] = True
  sm.logMonoTime['longitudinalPlan'] = NOW - 5_000_000
  sm.recv_time['longitudinalPlan'] = (NOW - 5_000_000) / 1e9
  inputs = LongitudinalInputs(cp, params, lambda: sm)
  assert 'radarState' in inputs.optional_services and 'deviceState' in inputs.optional_services
  first = NOW - 100_000_000
  with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(first, first + 500_000_000)):
    assert not inputs.context(True).start_evidence.plan_fresh
  with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(NOW, NOW + 500_000_000)):
    ctx = inputs.context(True)
    assert ctx.faster_takeoffs and ctx.start_evidence.car_fresh and ctx.start_evidence.plan_fresh
    assert ctx.start_evidence.drive_id == DRIVE
    ctrl = LongControl(cp)
    ctrl.update(True, car_state(standstill=False), 1.5, False, (-3., 2.), context=ctx)
    assert ctrl.takeoff_starting and ctrl.long_control_state == LongCtrlState.starting
    sm.logMonoTime['longitudinalPlan'] = NOW - 150_000_001
    assert not inputs.context(True).start_evidence.plan_fresh
    sm.logMonoTime['longitudinalPlan'] = NOW - 5_000_000
    sm.valid['radarState'] = False
    assert inputs.context(True).start_evidence.lead_clear is None


@pytest.mark.parametrize('speed', [0., .5, 2., 4., 5.])
def test_fast_takeoff_ceiling_is_3_point_5(speed):
  assert get_max_accel(speed, True) == 3.5
  assert get_max_accel(speed, False) <= 2.

@pytest.mark.parametrize('speed', [0., 3., 5.])
@pytest.mark.parametrize('experimental', [False, True])
@pytest.mark.parametrize('cap', [None, .5, 1.2, 2.4, 3.5])
def test_user_cap_survives_without_hidden_two_clip(speed, experimental, cap):
  cp = candidate()[1]
  expected = 3.5 if cap is None else cap
  actual = get_cruise_accel(experimental, 30., speed, expected, 0., cp, .05, 0., True,
                           selected_acceleration_max=cap, faster_takeoffs=True)
  assert actual == expected

@pytest.mark.parametrize('enabled', [False, True])
def test_actual_mpc_uses_toggle_dependent_ceiling(enabled):
  planner=LongitudinalPlanner(candidate()[1])
  sm=Frame()
  planner.mpc.update(sm['radarState'], faster_takeoffs=enabled)
  assert planner.mpc.solution_status == 0
  assert np.all(planner.mpc.params[:,1] == (3.5 if enabled else 2.))

@pytest.mark.parametrize('brand', ['hyundai', 'toyota', 'honda', 'ford', 'gm', 'volkswagen', 'rivian', 'tesla'])
def test_controls_ceiling_is_not_ioniq_gated(brand):
  cp = candidate()[1]
  cp.brand=brand
  control = LongControl(cp)
  control.extension=None
  cs=car_state(speed=4.,standstill=False)
  out=control.update(True,cs,3.5,False,(-3.5,3.5),context=LongitudinalContext(faster_takeoffs=True))
  assert out==3.5
  assert control.update(True,cs,3.5,False,(-3.5,2.),context=LongitudinalContext()) == 2.

@pytest.mark.parametrize('cap', [0., .5, 1.2, 2.4])
def test_controls_respects_personality_cap(cap):
  cp=candidate()[1]
  control=LongControl(cp)
  control.extension=None
  cs=car_state(speed=4.,standstill=False)
  assert control.update(True,cs,3.5,False,(-3.5,3.5),context=LongitudinalContext(faster_takeoffs=True,profile_max_accel=cap)) == cap


def test_real_planner_reaches_three_point_five_without_losing_model_stop():
  cp=candidate()[1]
  planner=LongitudinalPlanner(cp)
  outputs=[]
  for tick in range(100):
    now=NOW+tick*50_000_000
    sm=Frame()
    sm.logMonoTime=dict.fromkeys(sm,now-5_000_000)
    sm.recv_time=dict.fromkeys(sm,(now-5_000_000)/1e9)
    sm['carState'].vEgo=0.
    sm['carState'].standstill=True
    sm['carState'].vCruise=72.
    sm['selfdriveState'].experimentalMode=tick<8
    sm['modelV2'].action.shouldStop=tick<8
    sm['modelV2'].action.desiredAcceleration=-.04 if tick<8 else 4.
    planner.update(sm,faster_lead_takeoff=True,takeoff_drive_id=DRIVE,now_ns=now,drive_id=DRIVE)
    assert planner.mpc.solution_status == 0
    assert planner.output_a_target <= 3.5
    if tick<8:
      assert planner.output_should_stop
    outputs.append(float(planner.output_a_target))
  assert max(outputs)==3.5


@pytest.mark.parametrize('brand', ['hyundai', 'toyota', 'honda', 'ford', 'gm', 'volkswagen', 'rivian', 'tesla'])
def test_actual_interface_takeoff_ceiling_and_default_are_independent(brand):
  from importlib import import_module
  interface = import_module('opendbc.car.' + brand + '.interface').CarInterface
  models = import_module('opendbc.car.' + brand + '.values').CAR
  cp = interface.get_non_essential_params(next(iter(models)))
  normal = interface.get_pid_accel_limits(cp, 0., 30.)
  assert normal == interface.get_pid_accel_limits(cp, 0., 30., faster_takeoffs=False)
  fast = interface.get_pid_accel_limits(cp, 0., 30., faster_takeoffs=True)
  assert fast[0] == normal[0]
  assert fast[1] == 3.5


@pytest.mark.parametrize('brand', ['ford', 'honda'])
def test_takeoff_keeps_near_set_speed_pid_windup_limit(brand):
  from importlib import import_module
  interface = import_module('opendbc.car.' + brand + '.interface').CarInterface
  models = import_module('opendbc.car.' + brand + '.values').CAR
  cp = interface.get_non_essential_params(next(iter(models)))
  if brand == 'honda':
    cp = Honda.get_non_essential_params(H.HONDA_CIVIC)
  upper = interface.get_pid_accel_limits(cp, 30., 30., faster_takeoffs=True)[1]
  assert upper == .2


@pytest.mark.parametrize('speed,distance', [(5., 8.), (10., 20.), (15., 38.)])
def test_faster_takeoffs_keep_mpc_braking_for_a_stopped_lead(speed, distance):
  sm = Frame()
  lead = sm['radarState'].leadOne
  lead.present = True
  lead.dRel = distance
  lead.vLead = 0.
  lead.aLeadK = 0.
  lead.aLeadTau = 1.5
  lead.modelProb = 1.
  for enabled in (False, True):
    planner = LongitudinalPlanner(candidate()[1])
    planner.mpc.set_cur_state(speed, 0.)
    planner.mpc.update(sm['radarState'], faster_takeoffs=enabled)
    assert planner.mpc.solution_status == 0
    assert np.min(planner.mpc.a_solution) < 0.
    assert np.max(planner.mpc.a_solution[1:5]) <= 0.


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('cap', [0., .5, 2., 2.4, 3.5])
def test_saved_takeoff_preference_keeps_ordinary_profile_context_bound(tmp_path, enabled, cap):
  params = Params(str(tmp_path))
  params.put_bool('FasterLeadTakeoff', enabled, block=True)
  owner = LongitudinalInputs(candidate()[1], params, lambda: Frame())
  with patch.object(owner, '_vehicle_context', return_value=LongitudinalContext(profile_max_accel=cap)):
    assert owner.context(False).profile_max_accel == min(cap, 3.5 if enabled else 2.)
