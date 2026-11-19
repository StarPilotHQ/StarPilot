"""SLC overshoot braking eases in without weakening lead, model or acceleration responses."""
from unittest.mock import patch

import pytest

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.selfdrive.controls.lib import longitudinal_planner as planner_module
from openpilot.selfdrive.controls.lib.longitudinal_planner import (A_CRUISE_MIN, SLC_BRAKE_ONSET_ACCEL, SLC_BRAKE_ONSET_JERK,
                                                                   LongitudinalPlanner, get_cruise_accel)
from openpilot.starpilot.curve_speed.tests.test_planner import Inputs
from openpilot.starpilot.longitudinal.cruise_ceiling import CruiseCeiling, CurveCeiling
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import ACTIVE, V_EGO, messages
from openpilot.starpilot.longitudinal.tests.test_cruise_taps import camry

DT = .05
CAP = CruiseCeiling(60. / 3.6, ACTIVE)  # below the fixture's 100 km/h SET, with the car 3.3 m/s over it


def test_overshoot_braking_eases_in_then_uses_normal_jerk():
  cp, accel, trace = camry(), 1.2, []
  for _ in range(100):
    previous, accel = accel, get_cruise_accel(False, 10., 12., accel, 0., cp, DT, 0., True, ease_slc_braking=True)
    trace.append((previous, previous - accel))
  normal = get_cruise_accel(False, 10., 12., 1.2, 0., cp, DT, 0., True)
  assert trace[0][1] == pytest.approx(1.2 - normal)  # lifting off the throttle is not slowed
  onset = [step for previous, step in trace if SLC_BRAKE_ONSET_ACCEL < previous <= 0.]
  assert max(onset) <= SLC_BRAKE_ONSET_JERK * DT + 1e-9
  assert len(onset) * DT >= abs(SLC_BRAKE_ONSET_ACCEL) / SLC_BRAKE_ONSET_JERK - DT  # about 2 s to reach -0.3
  assert accel == pytest.approx(A_CRUISE_MIN)


@pytest.mark.parametrize('error', (-.2, -.4))
def test_overshoot_correction_is_halved(error):
  assert get_cruise_accel(False, 28. + error, 28., error / 2, 0., camry(), DT, 0., True,
                          ease_slc_braking=True) == pytest.approx(error / 2)


def test_braking_requested_elsewhere_keeps_normal_jerk():
  args = (False, 20., 25., 0., 0., camry(), DT, 0., True)
  assert get_cruise_accel(*args, ease_slc_braking=True) == pytest.approx(-SLC_BRAKE_ONSET_JERK * DT)
  assert get_cruise_accel(*args, a_target_prev=-1., ease_slc_braking=True) == get_cruise_accel(*args, a_target_prev=-1.)


@pytest.mark.parametrize('faster', (False, True))
def test_acceleration_is_unchanged(faster):
  args = (False, 28.4, 28., .1, 0., camry(), DT, 0., True)
  assert get_cruise_accel(*args, faster_takeoffs=faster, ease_slc_braking=True) == get_cruise_accel(*args, faster_takeoffs=faster)


def planner_easing(*, ceiling=CAP, lead=False, e2e=False):
  planner = LongitudinalPlanner(CarInterface.get_non_essential_params(CAR.HONDA_CIVIC), init_v=V_EGO)
  real, seen = planner_module.get_cruise_accel, []

  def observe(*args, **options):
    seen.append(options['ease_slc_braking'])
    return real(*args, **options)

  with patch.object(planner_module, 'get_cruise_accel', side_effect=observe):
    for _ in range(3):
      planner.update(messages(lead=lead, e2e=e2e)[0], cruise_ceiling=ceiling)
  return seen, planner


@pytest.mark.parametrize('ceiling,lead,eased', ((CAP, False, True), (CAP, True, False), (None, False, False),
                                                (CruiseCeiling(40., ACTIVE), False, False)))
def test_planner_eases_only_toward_an_applied_slc_cap_without_a_braking_lead(ceiling, lead, eased):
  assert planner_easing(ceiling=ceiling, lead=lead)[0] == [eased] * 3


def test_model_braking_still_wins_while_slc_braking_is_eased():
  seen, planner = planner_easing(e2e=True)
  assert seen == [True] * 3
  assert planner.output_a_target < planner.a_cruise


@pytest.mark.parametrize('ceiling', (None, CAP))
def test_lower_curve_keeps_normal_braking_with_or_without_slc(ceiling):
  planner = LongitudinalPlanner(CarInterface.get_non_essential_params(CAR.HONDA_CIVIC), init_v=V_EGO)
  real, seen = planner_module.get_cruise_accel, []

  def observe(*args, **options):
    seen.append(options['ease_slc_braking'])
    return real(*args, **options)

  with patch.object(planner_module, 'get_cruise_accel', side_effect=observe):
    for frame in range(3):
      stamp = (frame + 1) * 50_000_000
      fields, _ = messages()
      fields['carControl'].longActive = True
      planner.update(Inputs(fields, stamp), cruise_ceiling=ceiling, curve_ceiling=CurveCeiling(15., stamp))
  assert seen == [False] * 3
  assert planner.a_cruise < planner_easing()[1].a_cruise
