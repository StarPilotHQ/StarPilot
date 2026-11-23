"""After an accelerator override, on a Tesla where SLC writes SET (Comma can set speed on Tesla dashboard is on).

SET there is the limit plus offset, so nothing sits above it for the planner to hold. Card owns SET, so card applies
"Hold the speed you reached": on lifting off, the highest speed reached above SET becomes SET as the driver's own
change, and SLC holds it until the limit changes, as it does a scroll. The other two choices return to SET.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from opendbc.car.structs import car
from openpilot.cereal import messaging
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.starpilot.speed_limits import tesla_set_speed_card
from openpilot.starpilot.speed_limits.runtime import Runtime
from openpilot.starpilot.speed_limits.runtime_settings import PEDAL_OVERRIDE_CHOICES, PEDAL_OVERRIDE_KEY, PedalOverride
from openpilot.starpilot.speed_limits.tesla_set_speed_card import Receiver, pedal_hold, refresh
from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM
from openpilot.starpilot.speed_limits.tests.test_tesla_set_speed import (AUTOMATIC, START, adopt_automatically, owned_helper, parse,
                                                                          rule_cp, scene, set_change, tick)

NOW = START + 5_000_000_000


def car_state(speed_kph, *, gas=False, enabled=True, standstill=False, valid=True, cluster=None):
  state = car.CarState.new_message()
  state.vEgo = speed_kph / 3.6
  state.vEgoCluster = (speed_kph if cluster is None else cluster) / 3.6
  state.gasPressed = gas
  state.cruiseState.enabled = enabled
  state.standstill = standstill
  state.canValid = valid
  return state


def planner_state(limit_kph):
  """What card sees of the planner: SLC live with this accepted limit plus offset in force (None: no limit yet)."""
  message = messaging.new_message('slcState')
  state = message.slcState
  state.enabled, state.sessionId, state.frameMonoTime = True, 'drive', NOW
  if limit_kph is not None:
    state.hasAcceptedAdjustedLimit, state.acceptedAdjustedLimit = True, limit_kph / 3.6
  return ReplaySM({'slcState': state}, NOW)


def make_card(*, set_kph=90., limit_kph=90., metric=True, hold=True, slc=True, switch=True):
  """Card as it is with the switch on, SLC live, its limit plus offset at limit_kph and SET at set_kph."""
  helper = owned_helper(rule_cp(), switch=switch)
  helper.v_cruise_kph = helper.v_cruise_cluster_kph = set_kph
  helper.tesla_pedal_hold = hold
  helper.slc_pcm = bool(slc and switch)
  return SimpleNamespace(slc_set_speed=Receiver(None), v_cruise_helper=helper, is_metric=metric, slc_receipts=[],
                         sm=planner_state(limit_kph))


def press_and_release(card, speeds_kph, **state):
  for speed in speeds_kph:
    assert not pedal_hold(card, car_state(speed, gas=True, **state), NOW)
    assert card.slc_receipts == [], 'nothing changes while the accelerator is pressed'
  return pedal_hold(card, car_state(speeds_kph[-1], **state), NOW)


def test_the_choices_are_the_stock_ones():
  assert PEDAL_OVERRIDE_CHOICES == ('Resume the adjusted limit', 'Hold the speed you reached', 'Use your SET speed')
  assert tesla_set_speed_card.PEDAL_HOLD == b'1' and int(PedalOverride.SPEED_REACHED) == 1


def test_lifting_off_above_set_makes_the_highest_speed_reached_the_set_speed():
  card = make_card(set_kph=90.)
  assert press_and_release(card, [88., 95., 104.4, 101.])
  assert card.v_cruise_helper.v_cruise_kph == 104. and card.v_cruise_helper.v_cruise_cluster_kph == 104.
  kind, detail, previous, selected, observed = card.slc_receipts[0]
  assert kind == 'driverChange' and detail == (pytest.approx(25.), pytest.approx(104 / 3.6), 'accel', False)
  assert (previous, selected, observed) == (pytest.approx(25.), pytest.approx(104 / 3.6), NOW)
  assert len(card.slc_receipts) == 1
  assert not pedal_hold(card, car_state(101.), NOW) and len(card.slc_receipts) == 1, 'once per press'


def test_the_speed_is_rounded_to_the_units_shown():
  card = make_card(set_kph=60 * CV.MPH_TO_KPH, limit_kph=60 * CV.MPH_TO_KPH, metric=False)
  assert press_and_release(card, [66.4 * CV.MPH_TO_KPH])
  assert card.v_cruise_helper.v_cruise_kph == pytest.approx(66 * CV.MPH_TO_KPH, abs=.05)
  again = make_card(set_kph=60 * CV.MPH_TO_KPH, limit_kph=60 * CV.MPH_TO_KPH, metric=False)
  assert not press_and_release(again, [60.4 * CV.MPH_TO_KPH]), 'less than half a unit over SET is still SET'
  assert again.slc_receipts == []


def test_the_dashboard_speed_is_the_speed_reached():
  card = make_card(set_kph=90.)
  for speed in (95., 100.):
    pedal_hold(card, car_state(speed, gas=True, cluster=speed + 2), NOW)
  assert pedal_hold(card, car_state(100., cluster=102.), NOW) and card.v_cruise_helper.v_cruise_kph == 102.


@pytest.mark.parametrize('speeds', [[60., 80., 89.], [90.4], [0.]])
def test_a_press_that_stays_at_or_below_set_changes_nothing(speeds):
  card = make_card(set_kph=90.)
  assert not press_and_release(card, speeds)
  assert card.v_cruise_helper.v_cruise_kph == 90. and card.slc_receipts == []


def test_it_is_an_override_of_the_limit_not_of_a_lower_set_speed():
  # SET scrolled down to 70 under a 90 limit: 80 is above SET but not above the limit, so lifting off returns to SET.
  card = make_card(set_kph=70., limit_kph=90.)
  assert not press_and_release(card, [80.]) and card.v_cruise_helper.v_cruise_kph == 70. and card.slc_receipts == []
  assert press_and_release(card, [96.]) and card.v_cruise_helper.v_cruise_kph == 96.
  # SET scrolled up to 100 over the 90 limit: a speed above both becomes the new held SET.
  above = make_card(set_kph=100., limit_kph=90.)
  assert not press_and_release(above, [95.]) and press_and_release(above, [108.])
  assert above.v_cruise_helper.v_cruise_kph == 108.


def test_without_a_limit_in_force_the_accelerator_changes_nothing():
  card = make_card(set_kph=90., limit_kph=None)
  assert not press_and_release(card, [100., 110.])
  assert card.v_cruise_helper.v_cruise_kph == 90. and card.slc_receipts == []
  stale = make_card(set_kph=90.)
  stale.sm = ReplaySM({'slcState': stale.sm['slcState']}, NOW + 10_000_000_000)  # the planner stopped publishing
  for speed in (100., 110.):
    pedal_hold(stale, car_state(speed, gas=True), NOW + 10_000_000_000)
  assert not pedal_hold(stale, car_state(110.), NOW + 10_000_000_000) and stale.slc_receipts == []


@pytest.mark.parametrize('case', ['another choice', 'SLC off', 'switch off'])
def test_only_hold_the_speed_you_reached_with_slc_writing_set(case):
  card = make_card(hold=case != 'another choice', slc=case != 'SLC off', switch=case != 'switch off')
  assert not press_and_release(card, [100., 110.])
  assert card.v_cruise_helper.v_cruise_kph == 90. and card.slc_receipts == []
  assert card.slc_set_speed.pedal_peak_mps == 0.


@pytest.mark.parametrize('state', [{'enabled': False}, {'standstill': True}, {'valid': False}])
def test_a_press_while_cruise_is_off_stopped_or_unreadable_is_forgotten(state):
  card = make_card()
  assert not press_and_release(card, [100., 110.], **state)
  assert card.v_cruise_helper.v_cruise_kph == 90. and card.slc_receipts == []
  # and it does not carry into a later release
  assert not pedal_hold(card, car_state(95.), NOW) and card.v_cruise_helper.v_cruise_kph == 90.


def test_the_speed_reached_before_cruise_dropped_does_not_count_after_it_returns():
  card = make_card()
  pedal_hold(card, car_state(120., gas=True), NOW)
  pedal_hold(card, car_state(120., gas=True, enabled=False), NOW)
  assert press_and_release(card, [96.]) and card.v_cruise_helper.v_cruise_kph == 96.


def test_set_never_goes_above_the_most_cruise_allows():
  from openpilot.selfdrive.car.cruise import V_CRUISE_MAX
  card = make_card(set_kph=140., limit_kph=130.)
  assert press_and_release(card, [V_CRUISE_MAX + 40.])
  assert card.v_cruise_helper.v_cruise_kph == V_CRUISE_MAX


@pytest.mark.parametrize('saved,expected', [(b'1', True), (b'0', False), (b'2', False), (None, False), (b'x', False)])
def test_card_reads_the_saved_choice(tmp_path, saved, expected):
  params = Params(str(tmp_path))
  params.put_bool('TeslaSpeedSync', True, block=True)
  if saved is not None:
    Path(params.get_param_path(PEDAL_OVERRIDE_KEY)).write_bytes(saved)
  cp = rule_cp()
  card = SimpleNamespace(CP=cp, params=params, v_cruise_helper=owned_helper(cp, switch=False))
  refresh(card)
  assert card.v_cruise_helper.tesla_set_speed and card.v_cruise_helper.tesla_pedal_hold is expected


def test_another_car_never_gets_the_choice(tmp_path):
  from openpilot.selfdrive.car.cruise import VCruiseHelper
  params = Params(str(tmp_path))
  params.put_bool('TeslaSpeedSync', True, block=True)
  Path(params.get_param_path(PEDAL_OVERRIDE_KEY)).write_bytes(b'1')
  cp = rule_cp(admitted=False)
  card = SimpleNamespace(CP=cp, params=params, v_cruise_helper=VCruiseHelper(cp))
  refresh(card)
  assert card.v_cruise_helper.tesla_pedal_hold is False and card.v_cruise_helper.tesla_set_speed is False


def test_a_command_in_the_same_frame_gives_way_to_the_held_speed():
  from unittest.mock import patch
  card = make_card(set_kph=90.)
  card.slc_replay, card.slc_producer_session = True, 'card-producer'
  pedal_hold(card, car_state(104., gas=True), NOW)
  command = SimpleNamespace(valid=True, slcCruiseCommand=SimpleNamespace(issuedMonoTime=NOW - 1, expiresMonoTime=NOW + 10**8,
                                                                         sessionId='s', commandId=1, targetMps=25.))
  state = SimpleNamespace(sessionId='s')
  seen = {}

  def applicable(command, state, observation, producer, now, previous, *, long_active, button_event):
    seen['driver'] = button_event
    return not button_event

  with patch.object(tesla_set_speed_card.messaging, 'recv_one_or_none', side_effect=[command, None]), \
       patch.object(tesla_set_speed_card.physical_actions, 'state_current', return_value=state), \
       patch.object(tesla_set_speed_card.physical_actions, 'command_applicable', side_effect=applicable):
    tesla_set_speed_card.apply_commands(card, car_state(104.), None, NOW, long_active=True)
  assert seen == {'driver': True} and card.v_cruise_helper.v_cruise_kph == 104.
  assert [receipt[0] for receipt in card.slc_receipts] == ['driverChange', 'commandRejected']


def test_the_planner_holds_the_speed_reached_until_the_limit_changes():
  sm, cp = scene(rule_cp())
  runtime = Runtime(parse(AUTOMATIC), is_metric=True)
  adopt_automatically(runtime, sm, cp)  # the 25 m/s limit, SET 90 km/h
  card = make_card(set_kph=90.)
  assert press_and_release(card, [97., 104.])
  _, detail, previous, selected, _ = card.slc_receipts[0]
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = card.v_cruise_helper.v_cruise_kph
  held = tick(runtime, sm, cp, 3.1, cruise_event=set_change(2, 3.1, previous * 3.6, selected * 3.6))
  assert held.result.override.event_receipt.consumed and detail[2] == 'accel'
  assert held.message.slcState.driverOverrideActive and held.message.slcState.overrideBasis == 'persistent'
  assert held.command is None
  assert all(tick(runtime, sm, cp, 3.2 + n * .05).command is None for n in range(40)), 'SLC leaves the held speed alone'
  sm['slcDashboardObservation'].speedMps = 18.
  tick(runtime, sm, cp, 5.3)
  adopted = tick(runtime, sm, cp, 8.4)
  assert adopted.command.slcCruiseCommand.targetMps == pytest.approx(18.) and not adopted.message.slcState.driverOverrideActive
