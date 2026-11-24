"""The right scroll wheel nudged left or right and held: its own control, apart from the plain nudge.
Wheel frames arrive every 0.1 s."""
import json
from types import SimpleNamespace as NS

import pytest

from openpilot.starpilot.common.car_control_config import ACTIVE_INPUTS, INPUTS, PARAM, WHEEL_HOLDS, effective_bindings, parse_assignments
from openpilot.starpilot.controllers.tesla_controls import TeslaInputDecoder, WHEEL_FRAME_GAP_SECONDS, WHEEL_HOLD_SECONDS, WheelHold
from openpilot.starpilot.controllers.tests.test_tesla_decoder import wheel
from openpilot.starpilot.controllers.tests.tesla_worker_fixture import selected_rig
from openpilot.starpilot.favorites.actions import BOOKMARK

UP = wheel()
SWITCHES = ('right_wheel_left', 'right_wheel_right')
HOLDS = tuple(WHEEL_HOLDS.values())
HOLD_FRAMES = round(WHEEL_HOLD_SECONDS / .1) + 1    # the first pressed frame, then 0.6 s of them


@pytest.fixture
def setup(tmp_path):
  return selected_rig(tmp_path)


def on(switch, **more):
  return wheel(**{switch: 2}, **more)


class Feed:
  def __init__(self, *holds):
    self.decoder = TeslaInputDecoder(bus=1, wheel_holds=frozenset(holds))
    self.t = 1.0
    self.out = []

  def __call__(self, *frames, dt=.1):
    result = []
    for frame in frames:
      self.t += dt
      result += self.decoder.feed(0x3c2, frame, 1, self.t)
    self.out += result
    return result


def test_the_inputs_exist_and_act():
  assert WHEEL_HOLDS == {'right_wheel_left': 'right_wheel_left_hold', 'right_wheel_right': 'right_wheel_right_hold'}
  assert INPUTS['right_wheel_left_hold'] == ('Right scroll wheel', 'Nudge left — hold')
  assert INPUTS['right_wheel_right_hold'] == ('Right scroll wheel', 'Nudge right — hold')
  assert set(HOLDS) <= ACTIVE_INPUTS
  document = parse_assignments(json.dumps({'version': 1, 'vehicle': 'TESLA_MODEL_3', 'bindings': {
    'right_wheel_left_hold': {'key': BOOKMARK, 'value': None, 'label': 'Bookmark'}}}))
  assert effective_bindings(document)['right_wheel_left_hold']['key'] == BOOKMARK


def test_holds_only_and_nudges_only():
  # No double nudge, and no hold or double of the inward press.
  wheel_inputs = {name for name in INPUTS if '_wheel_' in name}
  assert not [name for name in wheel_inputs if 'double' in name]
  assert {name for name in wheel_inputs if name.endswith('_hold')} == set(HOLDS)
  assert 'right_wheel_press' not in ACTIVE_INPUTS and not [name for name in ACTIVE_INPUTS if name.startswith('left_wheel_')]
  for name in ('right_wheel_press_hold', 'right_wheel_left_double', 'right_wheel_press_double', 'left_wheel_left_hold'):
    assert parse_assignments(json.dumps({'version': 1, 'vehicle': 'TESLA_MODEL_3', 'bindings': {
      name: {'key': BOOKMARK, 'value': None, 'label': 'Bookmark'}}})) is None, name


@pytest.mark.parametrize('switch', SWITCHES)
def test_hold_fires_once_while_still_held(switch):
  hold = WHEEL_HOLDS[switch]
  feed = Feed(hold)
  feed(UP)
  assert feed(*[on(switch)] * (HOLD_FRAMES - 1)) == [], 'held for 0.5 s: not yet'
  assert feed(on(switch)) == [hold], 'the frame 0.6 s after the first one'
  assert feed(*[on(switch)] * 10) == [], 'once per hold'
  assert feed(UP, UP) == [], 'the release is part of the hold: the plain nudge does not also act'
  assert feed(*[on(switch)] * HOLD_FRAMES) == [hold], 'the next hold works'


@pytest.mark.parametrize('switch', SWITCHES)
def test_release_after_a_hold_gives_nothing(switch):
  feed = Feed(*HOLDS)
  feed(UP)
  assert feed(*[on(switch)] * 13, UP, UP, UP) == [WHEEL_HOLDS[switch]], 'a 1.3 s hold reads as thirteen frames'
  assert switch not in feed.out


@pytest.mark.parametrize('switch', SWITCHES)
def test_a_quick_nudge_with_the_hold_assigned_still_acts_at_once(switch):
  feed = Feed(WHEEL_HOLDS[switch])
  feed(UP)
  for frames in (1, 2, 4, HOLD_FRAMES - 1):
    assert feed(*[on(switch)] * frames) == []
    assert feed(UP) == [switch], f'{frames} frames: on the release frame, with no wait'
    assert feed(UP) == []


def test_each_side_is_assigned_separately():
  feed = Feed('right_wheel_left_hold')
  feed(UP)
  assert feed(*[on('right_wheel_right')] * 10, UP) == ['right_wheel_right'], 'nudge right has no hold: a long one is a nudge'
  assert feed(*[on('right_wheel_left')] * 10, UP) == ['right_wheel_left_hold']
  assert feed(*[on('right_wheel_press')] * 10, UP) == ['right_wheel_press'], 'the inward press never has a hold'
  feed = Feed(*HOLDS)
  feed(UP)
  assert feed(*[on('right_wheel_press')] * 10, UP) == ['right_wheel_press']
  assert feed(*[on('left_wheel_left')] * 10, UP) == ['left_wheel_left'], 'the left wheel has no holds'


def test_unknown_or_unassigned_hold_names_are_ignored():
  for names in ((), ('right_wheel_press_hold',), ('right_wheel_left',), ('left_wheel_left_hold',)):
    decoder = TeslaInputDecoder(bus=1, wheel_holds=frozenset(names))
    assert decoder.wheel_holds == {}
  assert TeslaInputDecoder(bus=1, wheel_holds=frozenset(HOLDS)).wheel_holds == WHEEL_HOLDS


def test_unassigned_changes_nothing():
  script = ([UP, on('right_wheel_left'), UP, on('right_wheel_left'), on('right_wheel_left'), UP, UP] +
            [on('right_wheel_right')] * 9 + [UP, on('right_wheel_press'), UP, on('right_wheel_press'), UP,
            wheel(right_scroll=2), UP, on('right_wheel_left'), wheel(right_scroll=1), UP])
  plain, assigned = TeslaInputDecoder(bus=1), Feed()
  t, expected = 1.0, []
  for frame in script:
    t += .1
    expected.append(plain.feed(0x3c2, frame, 1, t))
  assert [assigned(frame) for frame in script] == expected
  assert not set(assigned.out) & set(HOLDS)
  assert assigned.out.count('right_wheel_left') == 3 and assigned.out.count('right_wheel_right') == 1


def test_only_an_assigned_hold_changes_what_the_other_frames_give():
  # With one side's hold assigned, everything but that side's long nudge decodes exactly as with none assigned.
  script = ([UP, on('right_wheel_left'), UP] + [on('right_wheel_right')] * 9 + [UP, on('right_wheel_press'), UP,
            wheel(right_scroll=2), UP, wheel(left_scroll=-1), UP, on('right_wheel_left'), wheel(right_scroll=1), UP])
  plain, assigned = Feed(), Feed('right_wheel_left_hold')
  assert [assigned(frame) for frame in script] == [plain(frame) for frame in script]


def test_a_nudge_that_ends_with_a_stray_scroll_click_still_counts():
  feed = Feed(*HOLDS)
  nudge = on('right_wheel_left')
  feed(UP)
  assert feed(nudge, wheel(right_scroll=1)) == ['right_wheel_left'], 'the click ends the nudge and is not a scroll'
  assert feed(UP) == []
  # A click while the wheel is still tilted does not stop the hold from counting.
  assert feed(nudge, on('right_wheel_left', right_scroll=1), *[nudge] * (HOLD_FRAMES - 2)) == ['right_wheel_left_hold']
  assert feed(UP, UP) == []
  # A hold whose release frame carries the click gives the hold alone.
  assert feed(*[nudge] * HOLD_FRAMES, wheel(right_scroll=-1), UP) == ['right_wheel_left_hold']


def test_doing_two_things_with_the_right_wheel_at_once_is_not_a_hold():
  feed = Feed(*HOLDS)
  feed(UP)
  both = wheel(right_wheel_press=2, right_wheel_left=2)
  assert feed(on('right_wheel_left'), on('right_wheel_left'), both, *[on('right_wheel_left')] * 8) == [], 'needs a release first'
  feed(UP, UP)
  both = wheel(right_wheel_left=2, right_wheel_right=2)
  assert feed(on('right_wheel_left'), both, *[on('right_wheel_left')] * 8) == []
  feed(UP, UP)
  assert feed(on('right_wheel_left'), on('right_wheel_left', right_scroll=2), *[on('right_wheel_left')] * 8) == [], 'a real scroll'
  feed(UP, UP)
  # The left wheel is its own business: a hold on the right still counts while the left one scrolls or is pressed.
  frames = [on('right_wheel_right')] * 3 + [on('right_wheel_right', left_scroll=3), on('right_wheel_right', left_wheel_press=2)]
  assert feed(*frames, *[on('right_wheel_right')] * (HOLD_FRAMES - 5)) == ['right_wheel_right_hold']


def test_already_down_at_the_start_or_after_lost_frames_does_not_count():
  feed = Feed(*HOLDS)
  nudge = on('right_wheel_left')
  assert feed(*[nudge] * 10) == [], 'never seen released'
  feed(UP)
  assert feed(*[nudge] * 4) == []
  assert feed(*[nudge] * 8, dt=.5) == [], 'frames went missing mid-hold'
  feed(UP, dt=.1)
  assert feed(*[nudge] * HOLD_FRAMES) == ['right_wheel_left_hold']
  feed(UP)
  feed.decoder.reset()
  assert feed(*[nudge] * 10) == [], 'a reset decoder must see the wheel released first'


def test_two_lost_frames_are_bridged_and_three_are_not():
  assert .3 < WHEEL_FRAME_GAP_SECONDS < .4
  hold = WheelHold()
  assert not hold.update(False, 1.0) and not hold.update(True, 1.1)
  assert not hold.update(True, 1.4), 'two frames lost: still the same nudge'
  assert hold.update(True, 1.7), 'held 0.6 s since it was first seen'
  assert not hold.update(True, 1.8)
  hold = WheelHold()
  hold.update(False, 1.0)
  hold.update(True, 1.1)
  assert not hold.update(True, 1.5) and not hold.update(True, 1.9), 'three lost: start again, from a release'
  assert not hold.armed
  hold = WheelHold()
  hold.update(False, 1.0)
  hold.update(True, 1.1)
  assert not hold.update(True, 1.05) and not hold.armed, 'time going backward starts again'


def test_frame_time_jitter_does_not_delay_a_hold():
  hold = WheelHold()
  hold.update(False, 1.0)
  hold.update(True, 1.1004)
  for i in range(1, 6):
    assert not hold.update(True, 1.1 + i * .1)
  assert hold.update(True, 1.6996), 'six frames after the first, a few ms early'


def test_unreadable_switches_start_again():
  feed = Feed(*HOLDS)
  nudge = on('right_wheel_right')
  feed(UP)
  assert feed(nudge, nudge, wheel(right_wheel_right=3), *[nudge] * 8) == [], 'a faulted switch frame'
  feed(UP)
  assert feed(nudge, nudge, wheel(left_wheel_left=0), *[nudge] * 8) == [], 'any unreadable switch in the frame'
  feed(UP)
  assert feed(*[nudge] * HOLD_FRAMES) == ['right_wheel_right_hold'], 'works again after a release'


def test_other_mux_frames_between_wheel_frames_do_not_break_a_hold():
  feed = Feed(*HOLDS)
  nudge = on('right_wheel_left')
  feed(UP)
  out = []
  for _ in range(HOLD_FRAMES):
    out += feed(nudge, dt=.1)
    out += feed(bytes(8), dt=0.)   # the message's other multiplexed half, sent in between
  assert out == ['right_wheel_left_hold']


def test_the_worker_sends_an_assigned_hold(setup):
  calls = []
  names = {'right_wheel_left': 'single', 'right_wheel_left_hold': 'hold', 'right_wheel_right': 'other'}
  setup.doc['bindings'] = {name: {'key': action, 'value': None, 'label': action} for name, action in names.items()}
  setup.params.put(PARAM, json.dumps(setup.doc).encode(), block=True)
  for action in names.values():
    setup.actions[action] = NS(available=True, invoke=lambda action=action: calls.append(action), invoke_value=None)
  nudge = on('right_wheel_left')
  setup.feed(UP)
  for _ in range(HOLD_FRAMES):
    setup.feed(nudge)
  assert calls == ['hold']
  for frame in (UP, UP, nudge, UP):
    setup.feed(frame)
  assert calls == ['hold', 'single']
  for frame in [on('right_wheel_right')] * 10 + [UP]:
    setup.feed(frame)
  assert calls == ['hold', 'single', 'other'], 'the unassigned hold on the other side stays a plain nudge'
  assert setup.pump.decoder.wheel_holds == {'right_wheel_left': 'right_wheel_left_hold'}


def test_the_worker_treats_an_explicitly_unassigned_hold_as_unassigned(setup):
  calls = []
  setup.doc['bindings'] = {'right_wheel_left': {'key': 'single', 'value': None, 'label': 'single'},
                           'right_wheel_left_hold': {'key': '__starpilot_controller_action__:none', 'value': None, 'label': 'Unassigned'}}
  setup.params.put(PARAM, json.dumps(setup.doc).encode(), block=True)
  setup.actions['single'] = NS(available=True, invoke=lambda: calls.append('single'), invoke_value=None)
  setup.feed(UP)
  for _ in range(10):
    setup.feed(on('right_wheel_left'))
  setup.feed(UP)
  assert calls == ['single'] and setup.pump.decoder.wheel_holds == {}
