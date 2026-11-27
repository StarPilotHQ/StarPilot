"""Small cruise increases retain the earlier gradual acceleration response."""
import pytest

from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.common.constants import CV
from openpilot.selfdrive.controls.lib.longitudinal_planner import get_cruise_accel


def camry():
  return CarInterface.get_params(CAR.TOYOTA_CAMRY, {0: {0x2FF: 4}, 1: {}, 2: {}}, [], True, False, False)


@pytest.mark.parametrize('speed', (10., 25., 28., 35.))
@pytest.mark.parametrize('step', (CV.KPH_TO_MS, CV.MPH_TO_MS))
def test_ordinary_cruise_taps_use_gradual_response(speed, step):
  cp = camry()
  ego, previous = speed, 0.
  acceleration = []
  for _ in range(40):
    target = get_cruise_accel(False, speed + step, ego, previous, 0., cp, .05, 0., True)
    assert target >= 0.
    acceleration.append(target)
    ego += target * .05
    previous = target
  # Pre-rebase native MPC peaks were .110-.118 for 1 kph and .175-.189 for 1 mph.
  assert max(acceleration) <= (.15 if step == CV.KPH_TO_MS else .23), 'CRUISE_TAP_ACCELERATION_REGRESSION'
  assert ego > speed and ego <= speed + step


@pytest.mark.parametrize('error,expected', ((-.2, -.2), (-1., -.5), (-5., -.5)))
def test_cruise_braking_is_unchanged(error, expected):
  assert get_cruise_accel(False, 28. + error, 28., expected, 0., camry(), .05, 0., True) == pytest.approx(expected)


@pytest.mark.parametrize('experimental,faster', ((True, False), (False, True)))
def test_experimental_and_requested_faster_takeoffs_keep_existing_response(experimental, faster):
  assert get_cruise_accel(experimental, 28.4, 28., .4, 0., camry(), .05, 0., True,
                          faster_takeoffs=faster) == pytest.approx(.4)


def test_large_cruise_error_still_reaches_existing_acceleration_cap():
  assert get_cruise_accel(False, 40., 25., .8, 0., camry(), .05, 0., True) == .8


def test_small_cruise_step_still_respects_a_lower_selected_cap():
  assert get_cruise_accel(False, 28.4, 28., .1, 0., camry(), .05, 0., True,
                          selected_acceleration_max=.1) == .1


def test_low_speed_launch_and_resume_keep_existing_response():
  assert get_cruise_accel(False, .5, .1, .4, 0., camry(), .05, 0., True) == .4


def test_repeated_imperial_cruise_taps_do_not_build_a_sharp_acceleration_peak():
  cp = camry()
  speed, previous = 28., 0.
  acceleration = []
  for frame in range(160):
    selected = 28. + (frame // 40 + 1) * CV.MPH_TO_MS
    previous = get_cruise_accel(False, selected, speed, previous, 0., cp, .05, 0., True)
    acceleration.append(previous)
    speed += previous * .05
  assert max(acceleration) <= .36
  assert speed > 28. + 3 * CV.MPH_TO_MS


@pytest.mark.parametrize('case', ('settling', 'large deficit', 'above set', 'lead one', 'lead two', 'experimental',
                                  'stop', 'driver pedal', 'low speed', 'speed ceiling', 'faster disabled', 'other car'))
def test_elantra_fast_takeoffs_settle_only_during_clear_cruise(case, monkeypatch):
  from opendbc.car.hyundai.interface import CarInterface as HyundaiInterface
  from opendbc.car.hyundai.values import CAR as HYUNDAI
  from openpilot.selfdrive.controls.lib import longitudinal_planner as lp
  from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling
  from openpilot.starpilot.longitudinal.tests.test_conditional_handoff import Frame, NOW, DRIVE
  from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import ACTIVE

  identity = HYUNDAI.HYUNDAI_SONATA if case == 'other car' else HYUNDAI.HYUNDAI_ELANTRA_2021
  cp = HyundaiInterface.get_params(identity, {0: {}, 1: {}, 2: {}}, [], True, False, False)
  assert cp.openpilotLongitudinalControl and not cp.pcmCruise
  speed = {'large deficit': 18., 'above set': 20.3, 'low speed': 4.}.get(case, 19.7)
  candidate, original = (lp.LongitudinalPlanner(cp, init_v=speed) for _ in range(2))
  cruise_accel = lp.get_cruise_accel

  def previous(*args, **kw):
    kw['gentle_settle'] = False
    return cruise_accel(*args, **kw)

  for tick in range(24):
    now = NOW + tick * 50_000_000
    sm = Frame()
    sm.logMonoTime = dict.fromkeys(sm, now - 5_000_000)
    sm.recv_time = dict.fromkeys(sm, (now - 5_000_000) / 1e9)
    sm['selfdriveState'].experimentalMode = case == 'experimental'
    sm['carState'].vEgo = speed
    sm['carState'].vCruise = 72.
    sm['carState'].canValid = True
    sm['carState'].gasPressed = case == 'driver pedal'
    sm['modelV2'].action.shouldStop = case == 'stop'
    sm['modelV2'].action.desiredAcceleration = 1.
    sm['radarState'].leadOne.present = case == 'lead one'
    sm['radarState'].leadTwo.present = case == 'lead two'
    kwargs = {'faster_lead_takeoff': case != 'faster disabled', 'now_ns': now, 'drive_id': DRIVE,
              'cruise_ceiling': CruiseCeiling(19.9, ACTIVE) if case == 'speed ceiling' else None}
    candidate.update(sm, **kwargs)
    with monkeypatch.context() as restore:
      restore.setattr(lp, 'get_cruise_accel', previous)
      original.update(sm, **kwargs)
    assert candidate.mpc.solution_status == original.mpc.solution_status == 0
    assert candidate.output_should_stop == original.output_should_stop
    if case != 'settling':
      assert candidate.a_cruise == original.a_cruise
      assert candidate.output_a_target == original.output_a_target
  if case == 'settling':
    error = 20. - sm['carState'].vEgo
    assert candidate.output_a_target == pytest.approx(error / 2.)
    assert original.output_a_target == pytest.approx(error)
