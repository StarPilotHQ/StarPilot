"""Exact CC_LONG independent LAT minimum; ordinary LONG admission stays separate.

Two integration cases use actual parser/Card/SD/Controls/native RX and TX.
The remaining cases are policy/decision guards with declared native values.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from opendbc.car.structs import car
from opendbc.car.gm.tests.test_bolt_cc import params
from opendbc.car.gm.values import CAR, GMFlags
from openpilot.selfdrive.selfdrived.events import EventName
from openpilot.starpilot.aol.runtime import decide_axes
from openpilot.starpilot.aol.tests.test_gm_parked_startup import _exercise_parked_main
from openpilot.starpilot.aol.tests.test_gm_startup_main import cases
from openpilot.starpilot.aol.wire import IntentState
from openpilot.starpilot.car.gm.aol import independent_lateral_below_engage
from openpilot.starpilot.nostalgia import aol_no_entry


def setup(identity=CAR.CHEVROLET_BOLT_CC_2018_2021):
  cp = params(identity)
  cp.alternativeExperience = 32
  intent = IntentState('card', 1, 10_000_000_000, 10_000_000_000, 10_200_000_000, True, False, False, True, True)
  return cp, intent


@pytest.mark.parametrize('main_action', [0, 9])
def test_actual_bolt_main_park_then_low_speed_drive_without_main_edge(main_action):
  _exercise_parked_main(2, main_action, below_engage=True)


@pytest.mark.parametrize('identity', [CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021, CAR.CHEVROLET_BOLT_CC_2022_2023])
def test_exact_cc_long_factory_owners(identity):
  cp, intent = setup(identity)
  assert independent_lateral_below_engage(cp, intent, enabled=False, active=False)


@pytest.mark.parametrize('enabled,active', [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize('long_grant', [False, True])
def test_minimum_exemption_cannot_engage_ordinary_long(enabled, active, long_grant):
  cp, intent = setup()
  cs = car.CarState(canValid=True, gearShifter='drive', vEgo=4.8, standstill=False)
  cs.cruiseState.available = True
  allowed = independent_lateral_below_engage(cp, intent, enabled=enabled, active=active)
  no_entry = aol_no_entry([EventName.belowEngageSpeed], cs, paddle_only_cancel=False, allow_below_engage_speed=allowed)
  native = SimpleNamespace(requestedLateral=True, lateralAllowed=True, requestedLongitudinal=long_grant, longitudinalAllowed=long_grant)
  decision = decide_axes(
    standard_lateral=active,
    standard_longitudinal=enabled,
    intent=intent,
    native=native,
    car_state=cs,
    initialized=True,
    model_ready=True,
    no_entry=no_entry,
    immediate_disable=False,
    dm_lockout=False,
    pause_brake_mps=0.0,
  )
  assert allowed == (not enabled and not active)
  assert decision.lateral_active == (not enabled and not active)
  assert not decision.desired_longitudinal and not decision.longitudinal_active


@pytest.mark.parametrize('field', ['settingsQualified', 'allowedLatch', 'lateralArmed'])
def test_current_armed_intent_required(field):
  cp, intent = setup()
  assert not independent_lateral_below_engage(cp, replace(intent, **{field: False}), enabled=False, active=False)


def test_lateral_pause_and_missing_intent_remain_denied():
  cp, intent = setup()
  assert not independent_lateral_below_engage(cp, None, enabled=False, active=False)
  assert not independent_lateral_below_engage(cp, replace(intent, pauseLateral=True), enabled=False, active=False)
  assert independent_lateral_below_engage(cp, replace(intent, pauseLongitudinal=True), enabled=False, active=False)


@pytest.mark.parametrize('index', [0, 1, 3, 4, 5, 6, 7])
def test_factory_profiles_use_independent_steering_minimum(index):
  _, intent = setup()
  cp = list(cases())[index]
  cp.alternativeExperience = 32
  assert independent_lateral_below_engage(cp, intent, enabled=False, active=False)


@pytest.mark.parametrize(
  'field,value',
  [('passive', True), ('notCar', True), ('dashcamOnly', True), ('alternativeExperience', 0), ('flags', int(GMFlags.CC_LONG | GMFlags.PEDAL_LONG))],
)
def test_changed_configuration_is_not_a_cc_long_owner(field, value):
  cp, intent = setup()
  setattr(cp, field, value)
  assert not independent_lateral_below_engage(cp, intent, enabled=False, active=False)


@pytest.mark.parametrize('event', [EventName.buttonCancel, EventName.doorOpen, EventName.controlsMismatch, EventName.commIssue, EventName.steerUnavailable])
def test_only_cruise_speed_event_is_removed(event):
  cp, intent = setup()
  cs = car.CarState(canValid=True, gearShifter='drive', vEgo=4.8)
  cs.cruiseState.available = True
  allowed = independent_lateral_below_engage(cp, intent, enabled=False, active=False)
  assert aol_no_entry([EventName.belowEngageSpeed, event], cs, paddle_only_cancel=False, allow_below_engage_speed=allowed) == (event != EventName.doorOpen)
