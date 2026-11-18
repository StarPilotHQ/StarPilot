"""Gas Highlander driver-speed response; other owners and safety arbitration retained."""
from types import SimpleNamespace as NS
import numpy as np
import pytest

from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.longitudinal.highlander_cruise import owner_for
from openpilot.starpilot.longitudinal.profile_runtime import SelectedProfileTuning
from openpilot.starpilot.longitudinal.tests.test_conditional_handoff import Frame, NOW, DRIVE
from openpilot.starpilot.longitudinal.force_stop import StopPlan


def frame(tick, *, speed=25., setting=90.):
  sm = Frame()
  now = NOW + tick * 50_000_000
  sm.logMonoTime = dict.fromkeys(sm, now - 5_000_000)
  sm.recv_time = dict.fromkeys(sm, (now - 5_000_000) / 1e9)
  sm['carState'].vEgo, sm['carState'].vCruise = speed, setting
  sm['carState'].standstill = False
  sm['controlsState'].longControlState = 'pid'
  sm['selfdriveState'].experimentalMode = False
  sm['modelV2'].action.shouldStop = False
  return sm, now


def update(owner, sm, now, **kwargs):
  owner.update(sm, now_ns=now, drive_id=DRIVE, takeoff_drive_id=DRIVE, faster_lead_takeoff=True,
               selected_profiles=SelectedProfileTuning(cruise_brake_magnitude=.6), **kwargs)


def test_registered_mappings_keep_every_other_platform_unchanged():
  from opendbc.car.values import PLATFORMS
  for fingerprint in PLATFORMS:
    for hybrid in (False, True):
      cp = NS(carFingerprint=fingerprint, flags=ToyotaFlags.HYBRID if hybrid else 1048,
              openpilotLongitudinalControl=True, passive=False, dashcamOnly=False, notCar=False,
              longitudinalActuatorDelay=.15)
      assert (owner_for(cp, .05) is not None) == (fingerprint == CAR.TOYOTA_HIGHLANDER_TSS2 and not hybrid)


@pytest.mark.parametrize('disabled', ['passive', 'dashcamOnly', 'notCar', 'stock'])
def test_actual_factory_denied_owners(disabled):
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_HIGHLANDER_TSS2)
  setattr(cp, 'openpilotLongitudinalControl' if disabled == 'stock' else disabled, disabled != 'stock')
  assert owner_for(cp, .05) is None


@pytest.mark.parametrize('magnitude', [.15, .3, .6])
def test_factory_native_cruise_converges_without_exceeding_selected_brake_cap(magnitude):
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_HIGHLANDER_TSS2)
  assert cp.flags == 1048 and cp.safetyConfigs[0].safetyParam == 73
  owner = LongitudinalPlanner(cp, init_v=25.)
  speed = 25.
  outputs = []
  for tick in range(500):
    setting = 90. if tick < 20 else 86.
    sm, now = frame(tick, speed=speed, setting=setting)
    owner.update(sm, now_ns=now, drive_id=DRIVE, takeoff_drive_id=DRIVE, faster_lead_takeoff=True,
                 selected_profiles=SelectedProfileTuning(cruise_brake_magnitude=magnitude))
    if tick >= 20:
      assert not owner.output_should_stop
      assert owner.output_a_target >= -magnitude - 1e-6
      outputs.append(owner.output_a_target)
    speed += float(owner.output_a_target) * .05
  assert min(outputs) < -.03
  assert abs(speed - 86. / 3.6) < .12


@pytest.mark.parametrize('veto', [
  'brake', 'gas', 'force', 'inactive', 'stale', 'future', 'invalid', 'low_speed', 'experimental', 'cap', 'lead', 'model_stop', 'no_throttle', 'stop'])
def test_current_veto_resets_episode_and_retains_normal_target(veto):
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_HIGHLANDER_TSS2)
  owner = LongitudinalPlanner(cp, init_v=25.)
  for tick in range(6):
    sm, now = frame(tick, setting=90. if tick == 0 else 86.)
    update(owner, sm, now)
  assert owner.button_cruise.active
  sm, now = frame(6, setting=86.)
  extra = {}
  if veto == 'brake':
    sm['carState'].brakePressed = True
  elif veto == 'gas':
    sm['carState'].gasPressed = True
  elif veto == 'force':
    sm['controlsState'].forceDecel = True
  elif veto == 'inactive':
    sm['carControl'].longActive = False
  elif veto == 'stale':
    sm.logMonoTime['carState'] = now - 150_000_001
  elif veto == 'future':
    sm.logMonoTime['radarState'] = now + 1
  elif veto == 'invalid':
    sm.valid['modelV2'] = False
  elif veto == 'low_speed':
    sm['carState'].vEgo = 2.
  elif veto == 'experimental':
    sm['selfdriveState'].experimentalMode = True
  elif veto == 'cap':
    from openpilot.starpilot.longitudinal.cruise_ceiling import CurveCeiling
    extra['curve_ceiling'] = CurveCeiling(10., sm.logMonoTime['modelV2'])
  elif veto == 'lead':
    lead = sm['radarState'].leadOne
    lead.present, lead.dRel, lead.vLead, lead.modelProb = True, 10., 0., .99
  elif veto == 'no_throttle':
    sm['modelV2'].meta.disengagePredictions.gasPressProbs = [0., 0.]
  elif veto == 'model_stop':
    sm['modelV2'].action.shouldStop = True
  elif veto == 'stop':
    extra['force_stop_provider'] = lambda follow: StopPlan(model_ns=sm.logMonoTime['modelV2'], should_stop=True, forcing=True)
  update(owner, sm, now, **extra)
  assert not owner.button_cruise.active
  assert owner.output_a_target <= owner.a_cruise + 1e-6


def test_user_increase_and_drive_restart_do_not_reuse_deceleration_episode():
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_HIGHLANDER_TSS2)
  owner = LongitudinalPlanner(cp, init_v=25.)
  for tick, setting in enumerate((90., 86., 86., 88.)):
    sm, now = frame(tick, setting=setting)
    update(owner, sm, now)
  assert not owner.button_cruise.active
  sm, now = frame(4, setting=86.)
  owner.update(sm, now_ns=now, drive_id=DRIVE+1)
  assert not owner.button_cruise.active


def test_cruise_solver_keeps_saved_jerk_factors_and_primary_solver_state():
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_HIGHLANDER_TSS2)
  owner = LongitudinalPlanner(cp, init_v=25.)
  for tick in range(4):
    sm, now = frame(tick, setting=90. if tick == 0 else 86.)
    update(owner, sm, now)
  native = owner.button_cruise.native
  assert native is not None and native is not owner.mpc
  before = (owner.mpc.params.copy(), owner.mpc.a_prev.copy(), owner.mpc._cached_cost_weights)
  native_before = np.array([owner.mpc.solver.get(i, 'x') for i in range(len(owner.mpc.params))])
  primary = owner.mpc._cached_cost_weights
  assert native._cached_cost_weights[0][4] < primary[0][4]
  sm, now = frame(4, setting=86.)
  owner.button_cruise.sample(sm, now_ns=now, drive_id=DRIVE, target_speed=86./3.6, brake_floor=-.3,
                            speed=25., previous_accel=owner.output_a_target, reference=owner.mpc, blocked=False)
  np.testing.assert_array_equal(owner.mpc.params, before[0])
  np.testing.assert_array_equal(owner.mpc.a_prev, before[1])
  assert owner.mpc._cached_cost_weights == before[2]
  np.testing.assert_array_equal(np.array([owner.mpc.solver.get(i, 'x') for i in range(len(owner.mpc.params))]), native_before)


def test_custom_deceleration_jerk_ratios_and_follow_are_preserved():
  from openpilot.starpilot.longitudinal.profile_runtime import ProfileTuning
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_HIGHLANDER_TSS2)
  owner = LongitudinalPlanner(cp, init_v=25.)
  tuning = ProfileTuning('standard', 1.45, 1.25, 1.5, 1.75, 2., .75)
  for tick in range(14):
    sm, now = frame(tick, setting=90. if tick < 11 else 86.)
    sm['carState'].aEgo = -.2
    update(owner, sm, now, profile_tuning=tuning)
  native = owner.button_cruise.native
  assert native is not None
  stage, constraints = native._cached_cost_weights
  assert stage[4] / stage[5] == pytest.approx(1.5 / 2.)
  assert constraints[3] / stage[5] == pytest.approx(.75 / 2.)
  assert native.params[0, 4] == pytest.approx(1.45)


def test_optional_solver_failure_withdraws_to_existing_cruise_path(monkeypatch):
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_HIGHLANDER_TSS2)
  owner = LongitudinalPlanner(cp, init_v=25.)
  for tick in range(4):
    sm, now = frame(tick, setting=90. if tick == 0 else 86.)
    update(owner, sm, now)
  assert owner.button_cruise.active
  monkeypatch.setattr(owner.button_cruise.native, 'run', lambda: 1)
  sm, now = frame(4, setting=86.)
  update(owner, sm, now)
  assert not owner.button_cruise.active
  assert owner.output_a_target == pytest.approx(owner.a_cruise)


def test_native_run_exposes_failure_while_preserving_existing_reset():
  from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc
  native = LongitudinalMpc()
  original = native.solver
  class FailedSolve:
    def __getattr__(self, name):
      return getattr(original, name)
    def solve(self):
      original.solve()
      return 1
  native.solver = FailedSolve()
  assert native.run() == 1
  assert native.solution_status == 0
  assert np.all(native.a_solution == 0.)


def test_aol_lateral_only_never_creates_or_applies_button_cruise_solver():
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_HIGHLANDER_TSS2)
  cp.alternativeExperience = 32
  owner = LongitudinalPlanner(cp, init_v=25.)
  for tick in range(6):
    sm, now = frame(tick, setting=90. if tick == 0 else 86.)
    sm['carControl'].latActive = True
    sm['carControl'].longActive = False
    update(owner, sm, now)
  assert not owner.button_cruise.active
  assert owner.button_cruise.native is None


@pytest.mark.parametrize('magnitude', [.6, 1.2])
def test_factory_ordinary_speed_cut_has_stable_shaped_response(magnitude):
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_HIGHLANDER_TSS2)
  owner = LongitudinalPlanner(cp, init_v=25.)
  first_second = []
  for tick in range(40):
    sm, now = frame(tick, setting=90. if tick < 20 else 88.)
    owner.update(sm, now_ns=now, drive_id=DRIVE, takeoff_drive_id=DRIVE, faster_lead_takeoff=True,
                 selected_profiles=SelectedProfileTuning(cruise_brake_magnitude=magnitude))
    if tick >= 20:
      first_second.append(float(owner.output_a_target))
  assert -.35 < min(first_second) < -.03
  assert sum(max(0., -a) * .05 for a in first_second) < .3
