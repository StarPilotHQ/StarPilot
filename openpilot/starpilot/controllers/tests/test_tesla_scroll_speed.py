"""Tesla right scroll wheel speed: while the comma owns the set speed (Comma can set speed on Tesla dashboard is on), a
scroll moves it by one display unit and a fast scroll to the next multiple of five, as the Tesla's own cruise steps.

The car's parameters only say that it may (the scroll frame was admitted at startup); card's cruise helper carries the
saved switch, and with it off the Tesla's own set speed is the set speed exactly as before."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest
from openpilot.cereal import messaging
from openpilot.common.params import Params
from opendbc.car.structs import car
from openpilot.selfdrive.car.cruise import V_CRUISE_MAX, V_CRUISE_MIN, V_CRUISE_UNSET, VCruiseHelper
from openpilot.starpilot.controllers.cruise_action import (TESLA_STEPS, CruiseActionConsumer, CruiseActionPublisher, eligible, tesla_set_speed,
                                                           ui_authority)
from openpilot.starpilot.controllers.tests.test_cruise_action import State, Publisher, cp as mock_cp
from openpilot.starpilot.tests.tesla_set_speed_fixture import tesla

MPH = 1.609344
NOW = 5_000_000_000
MODELS = ('TESLA_MODEL_3', 'TESLA_MODEL_Y')


def state(mph=25, enabled=True):
  return car.CarState.new_message(canValid=True, vEgo=20, cruiseState={
    'available': True, 'enabled': enabled, 'speed': mph * .44704, 'speedCluster': mph * .44704})


def owned(cp, *, switch=True):
  """Card's cruise helper as tesla_set_speed_card.refresh keeps it: owning the set speed while the switch is on."""
  helper = VCruiseHelper(cp)
  assert helper.tesla_set_speed is False, 'never owned before the switch has been read'
  helper.tesla_set_speed = bool(switch and tesla_set_speed(cp))
  return helper


def expected_step(displayed, step):
  if abs(step) == 1:
    return displayed + step
  return (displayed // 5 + 1) * 5 if step > 0 else ((displayed - 1) // 5) * 5


class Rig:
  """One selected Tesla, the UI publisher and card's consumer; every step is a fresh message 200 ms after the last."""
  def __init__(self, cp=None, *, metric=False, start=50):
    self.cp = tesla(selected=True) if cp is None else cp
    self.metric = metric
    self.now = NOW
    self.sm = State(self.now)
    self.helper = owned(self.cp)
    self.set(start)
    self.sender, self.consumer = CruiseActionPublisher(), CruiseActionConsumer()

  def set(self, displayed):
    self.helper.v_cruise_kph = self.helper.v_cruise_cluster_kph = float(displayed if self.metric else round(displayed * MPH, 1))

  @property
  def displayed(self):
    return round(self.helper.v_cruise_kph / (1 if self.metric else MPH))

  def message(self, step):
    self.now += 200_000_000
    self.sm = State(self.now)
    publisher = Publisher()
    assert self.sender.dispatch(step > 0, self.sm, self.cp, publisher, now_ns=self.now, step_units=step)
    return publisher.message

  def apply(self, msg, **changes):
    arguments = {'now_ns': self.now, 'is_metric': self.metric, 'enabled': True}
    arguments.update(changes)
    return self.consumer.apply(msg, self.cp, state(), self.sm, self.helper, **arguments)

  def step(self, step):
    return self.apply(self.message(step))


def test_the_steps_are_one_unit_or_one_fast_step_each_way():
  assert TESLA_STEPS == (-5, -1, 1, 5)


@pytest.mark.parametrize('model', MODELS)
def test_only_a_selected_tesla_may_own_its_set_speed_beside_the_car_cruise(model):
  selected, plain = tesla(model, selected=True), tesla(model)
  assert selected.pcmCruise and plain.pcmCruise
  assert tesla_set_speed(selected) and eligible(selected)
  assert not tesla_set_speed(plain) and not eligible(plain), 'started without the scroll frame: a plain car-cruise car'
  assert not tesla_set_speed(tesla(model, cable=False, selected=True)) and not tesla_set_speed(tesla(model, long=False, selected=True))
  assert not tesla_set_speed(None) and not tesla_set_speed(mock_cp()) and not tesla_set_speed(mock_cp(True))
  assert eligible(mock_cp()) and not eligible(mock_cp(True)) and not eligible(None), 'other cars: unchanged'
  sm = State(NOW)
  assert ui_authority(sm, selected, NOW) and not ui_authority(sm, plain, NOW)


@pytest.mark.parametrize('metric,start,step,expected', [
  (False, 50, 1, 51), (False, 50, -1, 49), (False, 50, 5, 55), (False, 50, -5, 45),
  (False, 52, 5, 55), (False, 52, -5, 50), (False, 54, 5, 55), (False, 56, -5, 55), (False, 51, -5, 50), (False, 49, 5, 50),
  (True, 83, 1, 84), (True, 83, -1, 82), (True, 83, 5, 85), (True, 83, -5, 80), (True, 80, 5, 85), (True, 80, -5, 75),
])
def test_a_step_moves_one_unit_and_a_fast_step_snaps_to_the_next_multiple_of_five(metric, start, step, expected):
  r = Rig(metric=metric, start=start)
  before = r.helper.v_cruise_kph
  assert r.step(step)
  assert r.displayed == expected
  assert r.helper.v_cruise_kph == pytest.approx(expected * (1 if metric else MPH), abs=.05)
  assert r.helper.v_cruise_cluster_kph == r.helper.v_cruise_kph
  assert r.helper.slc_cruise_change == (pytest.approx(before / 3.6), pytest.approx(r.helper.v_cruise_kph / 3.6),
                                        'accel' if step > 0 else 'decel', False)


@pytest.mark.parametrize('metric', [False, True])
def test_every_display_speed_and_every_step_lands_exactly_or_is_refused_at_the_limits(metric):
  unit = 1 if metric else MPH
  low, high = (8, 145) if metric else (5, 90)
  assert (low - 1) * unit < V_CRUISE_MIN <= low * unit and high * unit <= V_CRUISE_MAX < (high + 1) * unit
  for start in range(low, high + 1):
    for step in TESLA_STEPS:
      r = Rig(metric=metric, start=start)
      assert r.displayed == start
      target = expected_step(start, step)
      applied = r.step(step)
      if low <= target <= high:
        assert applied and r.displayed == target, (start, step)
      else:
        assert not applied and r.displayed == start, (start, step)
        assert r.helper.slc_cruise_change is None


@pytest.mark.parametrize('metric,start,step,applied,expected', [
  (False, 5, -1, False, 5), (False, 5, -5, False, 5), (False, 6, -5, True, 5), (False, 9, -5, True, 5), (False, 10, -5, True, 5),
  (False, 90, 1, False, 90), (False, 90, 5, False, 90), (False, 89, 1, True, 90), (False, 86, 5, True, 90), (False, 85, 5, True, 90),
  (True, 8, -1, False, 8), (True, 8, -5, False, 8), (True, 9, -5, False, 9), (True, 10, -5, False, 10), (True, 11, -5, True, 10),
  (True, 145, 1, False, 145), (True, 145, 5, False, 145), (True, 144, 1, True, 145), (True, 141, 5, True, 145), (True, 140, 5, True, 145),
])
def test_boundaries(metric, start, step, applied, expected):
  r = Rig(metric=metric, start=start)
  assert r.step(step) is applied
  assert r.displayed == expected
  assert V_CRUISE_MIN <= r.helper.v_cruise_kph <= V_CRUISE_MAX


@pytest.mark.parametrize('metric', [False, True])
def test_steps_do_not_drift_across_the_range(metric):
  low, high = (8, 145) if metric else (5, 90)
  r = Rig(metric=metric, start=low)
  for expected in range(low + 1, high + 1):
    assert r.step(1) and r.displayed == expected
  for expected in range(high - 1, low - 1, -1):
    assert r.step(-1) and r.displayed == expected
  r.set(low + 2)
  seen = []
  while r.step(5):
    seen.append(r.displayed)
  first = (low + 2) // 5 * 5 + 5
  assert seen == list(range(first, high + 1, 5))
  seen = []
  while r.step(-5):
    seen.append(r.displayed)
  assert seen == list(range(seen[0], (low + 4) // 5 * 5 - 1, -5)) and seen[-1] == (low + 4) // 5 * 5


def test_a_step_starts_from_the_speed_shown_not_from_stored_rounding():
  # Seeded from the car's own 53 mph (85.295 km/h, not yet rounded to a tenth).
  r = Rig()
  r.helper.v_cruise_kph = 53 * MPH
  assert r.step(1) and r.helper.v_cruise_kph == pytest.approx(round(54 * MPH, 1))
  r.helper.v_cruise_kph = 53 * MPH
  assert r.step(5) and r.helper.v_cruise_kph == pytest.approx(round(55 * MPH, 1))
  # Speed Limit Controller left 60 km/h on an mph car: 37.28 mph shows as 37.
  r.helper.v_cruise_kph = 60.
  assert r.step(-1) and r.displayed == 36
  r.helper.v_cruise_kph = 60.
  assert r.step(5) and r.displayed == 40


def test_an_unset_speed_is_never_stepped():
  r = Rig()
  r.helper.v_cruise_kph = V_CRUISE_UNSET
  assert not r.helper.v_cruise_initialized
  for step in TESLA_STEPS:
    assert not r.step(step) and r.helper.v_cruise_kph == V_CRUISE_UNSET


@pytest.mark.parametrize('step', [0, 2, 3, 4, -2, 6, 10, -10, 99, 1.0, 5.0, True, '1'])
def test_the_publisher_refuses_any_other_step(step):
  r = Rig()
  publisher = Publisher()
  assert not r.sender.dispatch(True, r.sm, r.cp, publisher, now_ns=NOW, step_units=step)
  assert not hasattr(publisher, 'message') and r.sender.sequence == 0


def test_the_publisher_refuses_a_step_together_with_a_target():
  r = Rig()
  assert not r.sender.dispatch(True, r.sm, r.cp, Publisher(), now_ns=NOW, step_units=1, target_mps=25.)


@pytest.mark.parametrize('case', ['tesla without the switch', 'tesla without the cable', 'model s', 'software cruise car',
                                  'car cruise car', 'no car'])
def test_the_publisher_refuses_a_step_for_any_other_car(case):
  cp = {'tesla without the switch': tesla, 'tesla without the cable': lambda: tesla(cable=False, selected=True),
        'model s': lambda: tesla('TESLA_MODEL_S_HW1', selected=True), 'software cruise car': mock_cp,
        'car cruise car': lambda: mock_cp(True), 'no car': lambda: None}[case]()
  sender, publisher = CruiseActionPublisher(), Publisher()
  for step in TESLA_STEPS:
    assert not sender.dispatch(step > 0, State(NOW), cp, publisher, now_ns=NOW, step_units=step)
  assert not hasattr(publisher, 'message')


def test_a_step_message_carries_the_step_and_nothing_else_new():
  r = Rig()
  msg = r.message(-5)
  assert msg.slcAction.kind == 'cruiseStep' and msg.valid
  request = msg.slcAction.controllerCruise
  assert request.stepUnits == -5 and request.targetSpeedMps == 0 and request.carFingerprint == r.cp.carFingerprint
  assert request.validUntilMonoTime - request.observedMonoTime == 250_000_000
  plain = Publisher()
  assert r.sender.dispatch(True, r.sm, r.cp, plain, now_ns=r.now + 1)
  assert plain.message.slcAction.kind == 'cruiseIncrease' and plain.message.slcAction.controllerCruise.stepUnits == 0


@pytest.mark.parametrize('case', ['tesla with the switch off', 'tesla started without the frame', 'software cruise car',
                                  'car cruise car'])
def test_card_refuses_a_step_on_any_car_that_does_not_own_its_set_speed(case):
  r = Rig()
  msg = r.message(1)
  other = {'tesla with the switch off': lambda: tesla(selected=True), 'tesla started without the frame': tesla,
           'software cruise car': mock_cp, 'car cruise car': lambda: mock_cp(True)}[case]()
  other.carFingerprint = r.cp.carFingerprint
  helper = owned(other, switch=False)
  helper.v_cruise_kph = 80.
  consumer = CruiseActionConsumer()
  assert not consumer.apply(msg, other, state(), r.sm, helper, now_ns=r.now, is_metric=False, enabled=True)
  assert helper.v_cruise_kph == 80. and not consumer.tesla_receipts


def test_card_follows_the_switch_from_one_message_to_the_next():
  r = Rig()
  assert r.step(1) and r.displayed == 51
  r.helper.tesla_set_speed = False  # the owner turned the switch off (card's settings thread, within a second)
  assert not r.step(1) and r.displayed == 51
  publisher = Publisher()
  r.now += 200_000_000
  r.sm = State(r.now)
  assert r.sender.dispatch(True, r.sm, r.cp, publisher, now_ns=r.now), 'the screen may still ask'
  assert not r.apply(publisher.message) and r.displayed == 51, 'an ordinary speed action is refused too'
  r.helper.tesla_set_speed = True
  assert r.step(5) and r.displayed == 55


@pytest.mark.parametrize('step', [0, 2, -3, 10, -10, 127, -128])
def test_card_refuses_a_message_with_any_other_step(step):
  r = Rig()
  msg = r.message(1).as_builder()
  msg.slcAction.controllerCruise.stepUnits = step
  assert not r.apply(msg.as_reader())
  assert r.displayed == 50


@pytest.mark.parametrize('fault', ['expired', 'replayed', 'too soon', 'not enabled', 'can invalid', 'cruise unavailable', 'other drive',
                                   'other car', 'button event'])
def test_card_applies_a_step_only_fresh_once_and_while_engaged(fault):
  r = Rig()
  msg = r.message(1)
  cs = state()
  arguments = {'now_ns': r.now, 'is_metric': False, 'enabled': True}
  if fault == 'expired':
    arguments['now_ns'] += 250_000_001
  if fault == 'replayed':
    assert r.apply(msg)
    r.now += 150_000_000
    arguments['now_ns'] = r.now
    r.set(50)
  if fault == 'too soon':
    assert r.apply(msg)
    msg = r.message(1)
    arguments['now_ns'] = r.now = r.now - 150_000_000
    r.set(50)
  if fault == 'not enabled':
    arguments['enabled'] = False
  if fault == 'can invalid':
    cs.canValid = False
  if fault == 'cruise unavailable':
    cs.cruiseState.available = False
  if fault == 'other drive':
    r.sm['deviceState'].startedMonoTime += 1
  if fault == 'other car':
    r.cp.carFingerprint = 'TESLA_MODEL_Y'
  if fault == 'button event':
    cs.buttonEvents = [{'type': 'accelCruise', 'pressed': True}]
  assert not r.consumer.apply(msg, r.cp, cs, r.sm, r.helper, **arguments)
  assert r.displayed == 50


def test_a_selected_tesla_also_takes_the_ordinary_speed_actions():
  # Favorites and controller buttons use these: the same card-owned set speed.
  r = Rig()
  publisher = Publisher()
  r.now += 200_000_000
  r.sm = State(r.now)
  assert r.sender.dispatch(True, r.sm, r.cp, publisher, now_ns=r.now)
  assert r.apply(publisher.message) and r.displayed == 51
  r.now += 200_000_000
  r.sm = State(r.now)
  assert r.sender.dispatch(False, r.sm, r.cp, publisher, now_ns=r.now, target_mps=30 * .44704)
  assert publisher.message.slcAction.kind == 'cruiseSet'
  assert r.apply(publisher.message) and r.displayed == 30


# The set speed itself (selfdrive/car/cruise.py).

def run(helper, frames, *, metric=False):
  out = []
  for mph, engaged in frames:
    cs = state(mph, engaged) if mph is not None else car.CarState.new_message(canValid=True)
    helper.update_v_cruise(cs, engaged, metric)
    out.append((helper.v_cruise_kph, helper.v_cruise_cluster_kph))
  return out


@pytest.mark.parametrize('model', MODELS)
def test_with_the_switch_on_a_tesla_keeps_its_own_set_speed_seeded_from_the_car_while_not_engaged(model):
  h = owned(tesla(model, selected=True))
  assert h.tesla_set_speed and not h.v_cruise_initialized
  out = run(h, [(50, False), (55, False), (55, True), (60, True), (40, True), (40, False), (45, True)])
  shown = [round(kph / MPH, 3) for kph, _ in out]
  assert shown == [50, 55, 55, 55, 55, 40, 40], "follows the Tesla until engaged, then is the comma's own"
  assert all(kph == cluster for kph, cluster in out)
  assert h.slc_cruise_change is None and not h.slc_pcm


def test_with_the_switch_on_a_tesla_starts_from_the_car_if_it_engages_before_any_speed_was_seen():
  h = owned(tesla(selected=True))
  assert [round(kph / MPH, 3) for kph, _ in run(h, [(62, True), (70, True)])] == [62, 62]


def test_with_the_switch_on_a_tesla_has_no_set_speed_without_cruise_or_a_car_speed():
  h = owned(tesla(selected=True))
  assert run(h, [(50, True), (None, False)])[-1] == (V_CRUISE_UNSET, V_CRUISE_UNSET), 'cruise not available'
  assert run(h, [(0, False)])[-1] == (V_CRUISE_UNSET, V_CRUISE_UNSET), 'the car has no set speed'
  assert run(h, [(50, False), (0, True)])[-1][0] == pytest.approx(50 * MPH), 'engaged: the comma keeps its own'


def test_engaging_with_the_switch_on_keeps_the_seeded_speed():
  h = owned(tesla(selected=True))
  run(h, [(50, False)])
  for resume in (None, False, True):
    assert h.initialize_v_cruise(state(50), False, resume=resume) is False
    assert h.v_cruise_kph == pytest.approx(50 * MPH)
  assert h.initialize_v_cruise(state(50), False, slc_target_mps=30., is_metric=False) is False, 'no live Speed Limit Controller'
  assert h.v_cruise_kph == pytest.approx(50 * MPH)


def test_the_switch_changes_who_owns_the_set_speed_without_a_jump():
  h = owned(tesla(selected=True), switch=False)
  assert [round(kph / MPH, 3) for kph, _ in run(h, [(50, False), (50, True), (52, True)])] == [50, 50, 52], "off: the Tesla's own"
  h.tesla_set_speed = True  # card's settings thread read the switch on
  assert [round(kph / MPH, 3) for kph, _ in run(h, [(52, True), (60, True)])] == [52, 52], 'on: it starts from that same speed'
  h.tesla_set_speed = False
  assert [round(kph / MPH, 3) for kph, _ in run(h, [(60, True)])] == [60], "off again: the Tesla's own at once"


def car_cruise_car(brand, fingerprint):
  return car.CarParams.new_message(pcmCruise=True, brand=brand, carFingerprint=fingerprint, openpilotLongitudinalControl=True)


WITHOUT_OWNERSHIP = {
  'tesla with the switch off': lambda: tesla(selected=True),
  'model y with the switch off': lambda: tesla('TESLA_MODEL_Y', selected=True),
  'tesla started without the frame': tesla,
  'tesla without the cable': lambda: tesla(cable=False, selected=True),
  'tesla without longitudinal control': lambda: tesla(long=False, selected=True),
  'model s': lambda: tesla('TESLA_MODEL_S_HW1', selected=True),
  'mock toyota': lambda: mock_cp(True, 'toyota'),
  'toyota': lambda: car_cruise_car('toyota', 'TOYOTA_RAV4_TSS2'),
  'hyundai': lambda: car_cruise_car('hyundai', 'HYUNDAI_IONIQ_5'),
  'honda': lambda: car_cruise_car('honda', 'HONDA_CIVIC_BOSCH'),
}


@pytest.mark.parametrize('case', WITHOUT_OWNERSHIP)
def test_a_tesla_with_the_switch_off_and_any_car_cruise_car_mirror_the_car_as_before(case):
  h = VCruiseHelper(WITHOUT_OWNERSHIP[case]())
  assert h.tesla_set_speed is False, 'a new helper never owns the set speed: card turns it on from the saved switch'
  frames = [(50, False), (55, False), (55, True), (60, True), (40, True), (40, False), (45, True), (0, True), (0, False)]
  out = run(h, frames)
  for (mph, _), (kph, cluster) in zip(frames, out, strict=True):
    expected = mph * .44704 * 3.6 if mph else V_CRUISE_UNSET
    assert kph == pytest.approx(expected) and cluster == pytest.approx(expected)
  cs = car.CarState.new_message(canValid=True, cruiseState={'available': True, 'speed': -1, 'speedCluster': -1})
  h.update_v_cruise(cs, True, False)
  assert (h.v_cruise_kph, h.v_cruise_cluster_kph) == (-1, -1)
  cs = car.CarState.new_message(canValid=True, cruiseState={'available': True, 'speed': 20., 'speedCluster': 21.})
  h.update_v_cruise(cs, True, False)
  assert (h.v_cruise_kph, h.v_cruise_cluster_kph) == (pytest.approx(72.), pytest.approx(75.6)), 'the cluster speed stays its own'
  assert run(h, [(None, False)])[-1] == (V_CRUISE_UNSET, V_CRUISE_UNSET)
  assert h.initialize_v_cruise(state(50), False, slc_target_mps=30.) is False
  up = car.CarState.ButtonEvent.Type.accelCruise
  run(h, [(50, True)])
  assert h.adjust_v_cruise(up, state(50), False) is False and h.v_cruise_kph == pytest.approx(50 * MPH)
  assert not h.slc_pcm


def test_software_cruise_cars_are_unchanged():
  h = VCruiseHelper(mock_cp())
  assert not h.tesla_set_speed
  h.v_cruise_kph = 80
  h.update_v_cruise(state(50), True, True)
  assert h.v_cruise_kph == 80, "never taken from the car's own cruise"
  assert h.adjust_v_cruise(car.CarState.ButtonEvent.Type.accelCruise, state(50), True) and h.v_cruise_kph == 81


def test_speed_limit_controller_ownership_is_only_marked_while_the_comma_owns_the_set_speed():
  for cp, switch, expected in ((tesla(selected=True), True, True), (tesla(selected=True), False, False), (tesla(), True, False),
                               (mock_cp(True), True, False), (mock_cp(), True, False)):
    h = owned(cp, switch=switch)
    h.update_v_cruise(state(50), True, False, slc_enabled=True)
    assert h.slc_pcm is expected
    h.update_v_cruise(state(50), True, False, slc_enabled=False)
    assert h.slc_pcm is False


# The real card loop.

def card_instance(tmp_path, cp, helper, sm, cs):
  from openpilot.selfdrive.car import card
  instance = card.Car.__new__(card.Car)
  instance.CP = cp
  vars(instance)['sm'] = sm
  instance.CC_prev = car.CarControl(enabled=True)
  instance.CS_prev = cs
  vars(instance)['CI'] = NS(CC=None, CS=NS(), update=lambda packets: cs)
  vars(instance)['RI'] = NS(update=lambda packets: None)
  instance.car_gps_publisher = card.CarGpsPublisher()
  instance.params = Params(str(tmp_path))
  vars(instance)['can_sock'] = object()
  instance.can_rcv_cum_timeout_counter = 0
  instance.ioniq6_long_prearmed = False
  instance.conditional_replay = instance.slc_replay = instance.curve_replay = False
  instance.switchback_button_tracker = instance.switchback_settings_owner = None
  instance.wheel_publisher = card.WheelPublisher(instance.params)
  instance.aol_card_intent = None
  instance.is_metric = False
  instance.experimental_mode = False
  instance.v_cruise_helper = helper
  instance.slc_command_sock = None
  instance.controller_cruise_sock = object()
  instance.controller_cruise_consumer = CruiseActionConsumer()
  return card, instance


def card_step(tmp_path, cp, request, *, now, switch=True, safe_mode=False):
  """One real card state update: the saved switch is read as card's settings thread reads it, then a request arrives."""
  from openpilot.starpilot.speed_limits import tesla_set_speed_card
  sm = State(now)
  cs = state(60)  # the Tesla's own set speed has moved to 60 mph since engaging at 50
  helper = VCruiseHelper(cp)
  card, instance = card_instance(tmp_path, cp, helper, sm, cs)
  instance.params.put_bool('TeslaSpeedSync', switch, block=True)
  instance.params.put_bool('SafeMode', safe_mode, block=True)
  tesla_set_speed_card.refresh(instance)
  helper.update_v_cruise(state(50, False), False, False)
  with (patch.object(instance, 'pm', NS(send=Mock()), create=True),
        patch.object(card.messaging, 'drain_sock_raw', return_value=[messaging.new_message('can', 1).to_bytes()]),
        patch.object(card.messaging, 'recv_one_or_none', side_effect=[request, None]),
        patch.object(card.time, 'monotonic_ns', return_value=now)):
    selected, _ = instance.state_update()
  return selected, helper, instance


@pytest.mark.parametrize('step,expected', [(1, 51), (-1, 49), (5, 55), (-5, 45)])
def test_actual_card_applies_a_step_to_its_own_set_speed_with_the_switch_on(tmp_path, step, expected):
  r = Rig()
  selected, helper, _ = card_step(tmp_path, r.cp, r.message(step), now=r.now)
  assert helper.tesla_set_speed
  assert selected.vCruise == pytest.approx(round(expected * MPH, 1))
  assert selected.vCruiseCluster == selected.vCruise
  assert list(selected.buttonEvents) == []
  assert helper.slc_cruise_change is not None and helper.slc_cruise_change[2] == ('accel' if step > 0 else 'decel')


def test_actual_card_keeps_the_comma_speed_when_no_step_arrives(tmp_path):
  r = Rig()
  selected, _, _ = card_step(tmp_path, r.cp, None, now=r.now)
  assert selected.vCruise == pytest.approx(50 * MPH), "not the Tesla's 60"


def test_actual_card_ignores_a_step_in_safe_mode(tmp_path):
  r = Rig()
  selected, helper, _ = card_step(tmp_path, r.cp, r.message(1), now=r.now, safe_mode=True)
  assert not helper.tesla_set_speed, 'Safe Mode turns the switch off'
  assert selected.vCruise == pytest.approx(60 * MPH), "the Tesla's own set speed"


@pytest.mark.parametrize('admitted,switch', [(True, False), (False, True), (False, False)])
def test_actual_card_ignores_a_step_with_the_switch_off_or_on_a_tesla_started_without_the_frame(tmp_path, admitted, switch):
  r = Rig()
  selected, helper, _ = card_step(tmp_path, tesla(selected=admitted), r.message(1), now=r.now, switch=switch)
  assert not helper.tesla_set_speed
  assert selected.vCruise == pytest.approx(60 * MPH), "the Tesla's own set speed, as before"


def test_card_reads_the_switch_only_for_a_car_that_may_own_its_set_speed(tmp_path):
  from openpilot.starpilot.speed_limits import tesla_set_speed_card
  for index, (cp, expected) in enumerate(((tesla(selected=True), True), (tesla('TESLA_MODEL_Y', selected=True), True), (tesla(), False),
                                          (tesla(cable=False, selected=True), False), (car_cruise_car('hyundai', 'HYUNDAI_IONIQ_5'), False),
                                          (mock_cp(), False))):
    path = tmp_path / str(index)
    path.mkdir()
    params = Params(str(path))
    params.put_bool('TeslaSpeedSync', True, block=True)
    reads = []
    saved = NS(get_param_path=lambda key='', params=params, reads=reads: reads.append(key) or params.get_param_path(key))
    helper = VCruiseHelper(cp)
    owner = NS(CP=cp, params=saved, v_cruise_helper=helper, slc_transport_available=False)
    tesla_set_speed_card.refresh(owner)
    assert helper.tesla_set_speed is expected
    assert bool(reads) is expected, 'nothing is read for any other car'
    assert tesla_set_speed_card.attach(owner) is None and not tesla_set_speed_card.rule(owner)
    params.put_bool('TeslaSpeedSync', False, block=True)
    tesla_set_speed_card.refresh(owner)
    assert helper.tesla_set_speed is False
