from dataclasses import replace

import pytest
from opendbc.car import structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.gm.tests.test_volt_cc_f1 import params as f1_params
from opendbc.car.gm.tests.test_volt_cc_gas import fixture as be_fixture
from openpilot.cereal import log
from openpilot.selfdrive.car.car_events import CarEvents
from openpilot.starpilot.aol.tests.test_gm_cc_lateral_minimum import setup
from openpilot.starpilot.aol.tests.test_gm_parked_startup import _exercise_parked_main
from openpilot.starpilot.car.gm.aol import independent_lateral_below_engage


def params(f1):
  cp = f1_params() if f1 else be_fixture()[0]
  cp.alternativeExperience = 32
  return cp


@pytest.mark.parametrize('f1', [False, True])
def test_volt_cc_steering_minimum_keeps_cruise_minimum(f1):
  cp = params(f1)
  _, intent = setup()
  assert cp.minSteerSpeed == pytest.approx(7 * CV.MPH_TO_MS)
  assert cp.minEnableSpeed == pytest.approx(24 * CV.MPH_TO_MS)
  assert independent_lateral_below_engage(cp, intent, enabled=False, active=False)
  assert not independent_lateral_below_engage(cp, replace(intent, allowedLatch=False), enabled=False, active=False)
  assert not independent_lateral_below_engage(cp, intent, enabled=True, active=True)


@pytest.mark.parametrize('f1', [False, True])
@pytest.mark.parametrize('active,previous,button,valid,expected', [
  (False, False, True, True, False), (True, False, False, True, True),
  (True, True, True, True, True), (True, True, False, True, False),
  (True, False, False, False, False)])
def test_volt_cc_long_waits_for_real_cruise_ack(f1, active, previous, button, valid, expected):
  cp = params(f1)
  cs = structs.CarState(canValid=valid, buttonEnable=button, vEgo=12, gearShifter='drive')
  cs.cruiseState.available = True
  cs.cruiseState.enabled = active
  prior = structs.CarState()
  prior.cruiseState.enabled = previous
  events = CarEvents(cp).update(cs, prior, structs.CarControl())
  assert (log.OnroadEvent.EventName.buttonEnable in events.names) == expected


@pytest.mark.parametrize('f1', [False, True])
def test_volt_cc_main_already_on_arms_steering_at_seven_mph(f1):
  _exercise_parked_main(0, 0, below_engage=True, cp=params(f1))
