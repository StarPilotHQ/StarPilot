"""Tesla Model 3/Y controls in Wheel Controls: the scroll shown as fixed information, then the assignable nudges, their
holds, the right stalk and the three-finger tap, saved as one exact document for one vehicle."""
from dataclasses import replace
import json
from types import SimpleNamespace as NS

import pytest
from opendbc.car.structs import car
from opendbc.car.tesla.values import TeslaFlags
from openpilot.starpilot.common.car_control_config import (DEFAULT_BINDINGS, FIXED_BINDINGS, NO_ACTION, PARAM, SPEED_DOWN, SPEED_RESUME, SPEED_SET,
                                                           SPEED_UP, STALK_HOLD, STALK_TAP, STALK_TAP_FULL, effective_bindings, parse_assignments)
from openpilot.starpilot.favorites.actions import BOOKMARK, CYCLE_PERSONALITY
from openpilot.starpilot.tests.port_settings_fixture import Saved, request
from openpilot.starpilot.tests.tesla_set_speed_fixture import switch, tesla
from openpilot.starpilot.ui.tesla_controls_feature import (AOL_ACTION, AOL_KEY, AOL_NOTE, AOL_PENDING, KEYS, OPTIONS, PREFIX,
                                                           SET_SPEED_NEEDS, TESLA_INPUTS, TeslaControlsFeature)
from openpilot.starpilot.ui.tesla_set_speed_feature import CABLE_MISSING

SCROLL_LABELS = ['Right scroll wheel / Scroll up', 'Right scroll wheel / Fast scroll up',
                 'Right scroll wheel / Scroll down', 'Right scroll wheel / Fast scroll down']
ROW_LABELS = SCROLL_LABELS + [
  'Right scroll wheel / Nudge left', 'Right scroll wheel / Nudge left — hold',
  'Right scroll wheel / Nudge right', 'Right scroll wheel / Nudge right — hold',
  'Right stalk / Down', 'Right stalk / Up',
  'Right stalk / Down — half press while engaged', 'Right stalk / Down — full press while engaged', 'Right stalk / Down — hold',
  'Tesla screen / Three-finger tap']
LEFT = PREFIX + 'right_wheel_left'
SCREEN = PREFIX + 'screen_three_finger_tap'


DEFAULT = object()


class Rig:
  def __init__(self, tmp_path, cp=DEFAULT):
    self.params = Saved(tmp_path)
    self.cp = tesla() if cp is DEFAULT else cp
    self.parked = True
    owner = NS(params=self.params, vehicle_params=lambda: self.cp, authority=lambda _: self.parked,
               vehicle_fingerprint=lambda: getattr(self.cp, 'carFingerprint', None))
    self.feature = TeslaControlsFeature(owner)

  def rows(self):
    return {row.label: row for row in self.feature.rows()}

  def row(self, key):
    return next(row for row in self.feature.rows() if row.key == key)

  def choose(self, key, value):
    return self.feature.apply(request(self.row(key), value))

  def document(self):
    return json.loads((self.params.path / PARAM).read_bytes())

  def saved(self, key):
    path = self.params.path / key
    return path.read_bytes() if path.exists() else None


@pytest.fixture
def rig(tmp_path):
  return Rig(tmp_path)


def cabled_request(tmp_path, value='Bookmark'):
  """The request a Model 3 with the cable would send for its nudge left."""
  path = tmp_path / 'cabled'
  path.mkdir()
  return request(Rig(path).row(LEFT), value)


def test_the_keys_are_every_control_and_its_speed():
  assert TESLA_INPUTS == ('right_wheel_left', 'right_wheel_left_hold', 'right_wheel_right', 'right_wheel_right_hold',
                          STALK_TAP, STALK_TAP_FULL, STALK_HOLD, 'screen_three_finger_tap')
  assert KEYS == frozenset(PREFIX + name + suffix for name in TESLA_INPUTS for suffix in ('', ':speed'))
  assert PREFIX == 'tesla_input:'


@pytest.mark.parametrize('model', ['TESLA_MODEL_3', 'TESLA_MODEL_Y'])
def test_rows_are_the_scroll_information_then_the_controls_in_order(tmp_path, model):
  rig = Rig(tmp_path, tesla(model))
  rows = rig.feature.rows()
  assert [row.label for row in rows] == ROW_LABELS
  assert [row.key for row in rows] == [''] * 4 + [PREFIX + name for name in TESLA_INPUTS[:4]] + ['', ''] + \
                                      [PREFIX + name for name in TESLA_INPUTS[4:]]
  assert [row.value for row in rows[:4]] == ['Increase set speed'] * 2 + ['Decrease set speed'] * 2
  assert not any(row.available or row.choices for row in rows[:4]), 'the scroll is never assignable'
  down, up = rows[8], rows[9]
  assert (down.value, down.available, up.value, up.available) == ('Engage StarPilot', False, 'Disengage StarPilot', False)
  hold = rig.row(PREFIX + STALK_HOLD)
  assert (hold.value, hold.available) == ('Resume Speed Limit + Offset', False)
  half = rig.row(PREFIX + STALK_TAP)
  assert (half.value, half.available) == ('Resume Speed Limit + Offset', True), 'starts on Resume Speed Limit + Offset'
  right = rig.row(PREFIX + 'right_wheel_right')
  assert (right.value, right.available) == ('Cycle Driving Personality', True), 'starts on Cycle Driving Personality'
  for name in ('right_wheel_left', 'right_wheel_left_hold', 'right_wheel_right_hold', STALK_TAP_FULL, 'screen_three_finger_tap'):
    row = rig.row(PREFIX + name)
    assert (row.value, row.available, row.vehicle_fingerprint) == ('Unassigned', True, model)
  # No description on any control: the title says what it is. Only the tap says when its choice takes effect.
  steps = ['By 1 mph', 'To the next 5 mph', 'By 1 mph', 'To the next 5 mph']
  assert [row.reason for row in rows] == steps + [''] * (len(rows) - 5) + [AOL_NOTE], 'only how far each scroll moves it'
  assert AOL_NOTE == 'Always On Lateral on/off applies after the next startup'


@pytest.mark.parametrize('selected,comma', [(True, True), (True, False), (False, True), (False, False)])
def test_the_rows_are_the_same_whoever_owns_the_set_speed(tmp_path, selected, comma):
  rig = Rig(tmp_path, tesla(selected=selected))
  switch(rig.params, comma)
  assert [(row.value, row.reason) for row in rig.feature.rows()[:4]] == [
    ('Increase set speed', 'By 1 mph'), ('Increase set speed', 'To the next 5 mph'),
    ('Decrease set speed', 'By 1 mph'), ('Decrease set speed', 'To the next 5 mph')]
  rig.params.put('IsMetric', b'1', block=True)
  assert [row.reason for row in rig.feature.rows()[:4]] == ['By 1 km/h', 'To the next 5 km/h'] * 2, 'in the units shown'
  assert [row.label for row in rig.feature.rows()] == ROW_LABELS, 'the same rows either way'


def test_the_comma_set_speed_steps_are_not_choices(rig):
  assert 'Increase Comma Set Speed' not in OPTIONS and 'Decrease Comma Set Speed' not in OPTIONS
  assert SPEED_UP not in OPTIONS.values() and SPEED_DOWN not in OPTIONS.values()
  assert OPTIONS['Unassigned'] == '' and OPTIONS['Set Speed To'] == SPEED_SET and OPTIONS['Resume Speed Limit + Offset'] == SPEED_RESUME
  for row in rig.feature.rows():
    assert 'Increase Comma Set Speed' not in row.choices and 'Decrease Comma Set Speed' not in row.choices
  for value in ('Increase Comma Set Speed', 'Decrease Comma Set Speed'):
    assert not rig.choose(LEFT, value)
  assert rig.saved(PARAM) is None


@pytest.mark.parametrize('saved', [SPEED_UP, SPEED_DOWN, BOOKMARK])
def test_a_saved_scroll_binding_is_not_shown_does_not_act_and_cannot_be_edited(rig, saved):
  document = {'version': 1, 'vehicle': 'TESLA_MODEL_3', 'bindings': {
    'right_wheel_up': {'key': saved, 'value': None, 'label': 'Saved by an older build'}}}
  rig.params.put(PARAM, json.dumps(document).encode(), block=True)
  assert [row.label for row in rig.feature.rows()] == ROW_LABELS
  assert rig.feature.rows()[0].value == 'Increase set speed'
  parsed = parse_assignments(rig.saved(PARAM))
  assert 'right_wheel_up' not in effective_bindings(parsed) and effective_bindings(parsed, comma_speed=True)['right_wheel_up']['key'] == SPEED_UP
  stale = request(rig.row(LEFT), 'Bookmark')
  assert not rig.feature.apply(replace(stale, key=PREFIX + 'right_wheel_up'))
  assert rig.choose(LEFT, 'Bookmark')
  assert rig.document()['bindings']['right_wheel_up'] == document['bindings']['right_wheel_up'], 'left as saved'


def test_shared_settings_exact_document_and_vehicle_guards(rig):
  row = rig.row(LEFT)
  assert rig.feature.apply(request(row, 'Bookmark'))
  assert rig.document() == {'version': 1, 'vehicle': 'TESLA_MODEL_3', 'bindings': {
    'right_wheel_left': {'key': BOOKMARK, 'value': None, 'label': 'Bookmark'}}}
  assert rig.row(LEFT).value == 'Bookmark'
  assert not rig.feature.apply(request(row, 'Experimental Mode')), 'the row shown before the save is stale'
  rig.cp.flags = int(rig.cp.flags) | int(TeslaFlags.AOL_SCREEN_BUTTON)
  # Reserved for screen-tap AOL this drive, but still assignable: the choice applies after the next startup.
  assert rig.row(SCREEN).available and rig.row(SCREEN).reason == AOL_PENDING
  current = rig.row(LEFT)
  rig.parked = False
  assert not rig.row(LEFT).available
  assert not rig.feature.apply(request(current, 'Experimental Mode'))
  rig.parked = True
  assert not rig.feature.apply(replace(request(current, 'Experimental Mode'), vehicle_fingerprint='TESLA_MODEL_Y'))
  assert not rig.feature.apply(replace(request(current, 'Experimental Mode'), vehicle_fingerprint=None))
  assert rig.document()['bindings']['right_wheel_left']['key'] == BOOKMARK
  assert rig.feature.apply(request(current, 'Experimental Mode'))


def test_unassigned_removes_a_binding_and_is_kept_over_a_default(rig):
  assert rig.choose(LEFT, 'Bookmark') and rig.choose(LEFT, 'Unassigned')
  assert rig.document()['bindings'] == {}
  assert STALK_TAP in DEFAULT_BINDINGS
  assert rig.choose(PREFIX + STALK_TAP, 'Unassigned')
  assert rig.document()['bindings'] == {STALK_TAP: {'key': NO_ACTION, 'value': None, 'label': 'Unassigned'}}
  assert rig.row(PREFIX + STALK_TAP).value == 'Unassigned'
  assert STALK_TAP not in effective_bindings(parse_assignments(rig.saved(PARAM)))
  assert rig.choose(PREFIX + STALK_TAP, 'Resume Speed Limit + Offset')
  assert rig.row(PREFIX + STALK_TAP).value == 'Resume Speed Limit + Offset'


def test_nudge_right_starts_on_cycle_driving_personality_and_can_be_changed(rig):
  right = PREFIX + 'right_wheel_right'
  assert set(DEFAULT_BINDINGS) == {STALK_TAP, 'right_wheel_right'}
  assert DEFAULT_BINDINGS['right_wheel_right'] == {'key': CYCLE_PERSONALITY, 'value': None, 'label': 'Cycle Driving Personality'}
  assert effective_bindings(parse_assignments(None))['right_wheel_right']['key'] == CYCLE_PERSONALITY
  assert rig.row(right).value == 'Cycle Driving Personality'
  assert rig.choose(right, 'Bookmark') and rig.row(right).value == 'Bookmark'
  assert effective_bindings(parse_assignments(rig.saved(PARAM)))['right_wheel_right']['key'] == BOOKMARK
  assert rig.choose(right, 'Unassigned') and rig.row(right).value == 'Unassigned'
  assert rig.document()['bindings']['right_wheel_right'] == {'key': NO_ACTION, 'value': None, 'label': 'Unassigned'}
  assert 'right_wheel_right' not in effective_bindings(parse_assignments(rig.saved(PARAM))), 'Unassigned is kept over the default'
  assert rig.choose(right, 'Cycle Driving Personality') and rig.row(right).value == 'Cycle Driving Personality'


def test_the_stalk_hold_is_fixed(rig):
  assert STALK_HOLD in FIXED_BINDINGS
  row = rig.row(PREFIX + STALK_HOLD)
  for value in ('Bookmark', 'Unassigned'):
    assert not rig.feature.apply(request(row, value))
  assert rig.saved(PARAM) is None
  rig.params.put(PARAM, json.dumps({'version': 1, 'vehicle': 'TESLA_MODEL_3', 'bindings': {
    STALK_HOLD: {'key': BOOKMARK, 'value': None, 'label': 'Bookmark'}}}).encode(), block=True)
  assert rig.row(PREFIX + STALK_HOLD).value == 'Resume Speed Limit + Offset', 'whatever was saved for it'


@pytest.mark.parametrize('value', ['Not a choice', '', AOL_ACTION])
def test_only_listed_choices_are_saved(rig, value):
  assert not rig.choose(LEFT, value)
  assert rig.saved(PARAM) is None


def test_set_speed_to_adds_its_speed_in_the_device_units(rig):
  assert rig.choose(LEFT, 'Set Speed To')
  assert rig.document()['bindings']['right_wheel_left'] == {'key': SPEED_SET, 'value': 30, 'label': 'Set Speed To'}
  speed = rig.row(LEFT + ':speed')
  assert (speed.value, speed.unit, speed.minimum, speed.maximum, speed.step) == ('30', 'mph', 5, 90, 1.)
  assert speed.dependencies == (('IsMetric', None),) and speed.available
  assert [row.label for row in rig.feature.rows()][4:6] == ['Right scroll wheel / Nudge left', 'Right scroll wheel / Nudge left: set speed']
  assert rig.choose(LEFT + ':speed', '65')
  assert rig.document()['bindings']['right_wheel_left']['value'] == 65.
  stale = rig.row(LEFT + ':speed')
  for bad in ('4', '91', 'fast', 'nan'):
    assert not rig.feature.apply(request(stale, bad)), bad
  rig.params.put('IsMetric', b'1', block=True)
  assert not rig.feature.apply(request(stale, '70')), 'the units changed under the row'
  metric = rig.row(LEFT + ':speed')
  assert (metric.unit, metric.minimum, metric.maximum) == ('km/h', 8, 145)
  assert rig.feature.apply(request(metric, '110')) and rig.document()['bindings']['right_wheel_left']['value'] == 110.


@pytest.mark.parametrize('selected,comma', [(True, True), (True, False), (False, True)])
def test_set_speed_to_says_when_it_needs_the_switch(tmp_path, selected, comma):
  rig = Rig(tmp_path, tesla(selected=selected))
  switch(rig.params, comma)
  assert rig.row(LEFT).reason == ''
  assert rig.choose(LEFT, 'Set Speed To')
  row = rig.row(LEFT)
  assert row.value == 'Set Speed To' and row.available, 'it can be assigned either way'
  if selected and comma:
    assert row.reason == ''
  else:
    assert row.reason == SET_SPEED_NEEDS
    assert SET_SPEED_NEEDS == 'Set Speed To needs Comma can set speed on Tesla dashboard turned on in Vehicle Settings'
  assert rig.row(PREFIX + 'right_wheel_right').reason == ''
  switch(rig.params, True)
  assert (SET_SPEED_NEEDS in rig.row(LEFT).reason) is (not selected), 'the note follows the switch at once'


def test_a_speed_cannot_be_set_on_a_control_that_is_not_on_set_speed_to(rig):
  assert rig.choose(LEFT, 'Set Speed To')
  speed = request(rig.row(LEFT + ':speed'), '50')
  assert not rig.feature.apply(replace(speed, key=PREFIX + 'right_wheel_right:speed'))
  assert 'right_wheel_right' not in rig.document()['bindings']


@pytest.mark.parametrize('raw', [b'not json', b'[]', json.dumps({'version': 2, 'vehicle': 'TESLA_MODEL_3', 'bindings': {}}).encode(),
                                 json.dumps({'version': 1, 'vehicle': 'TESLA_MODEL_S', 'bindings': {}}).encode(),
                                 json.dumps({'version': 1, 'vehicle': 'TESLA_MODEL_3', 'bindings': {'unknown_control': {}}}).encode()])
def test_invalid_assignments_are_shown_and_never_overwritten(rig, raw):
  rig.params.put(PARAM, raw, block=True)
  rows = rig.feature.rows()
  assert [row.label for row in rows] == ROW_LABELS
  for row in rows:
    if row.key:
      assert row.value == 'Invalid saved assignments' and not row.available
  assert not rig.choose(LEFT, 'Bookmark')
  assert rig.saved(PARAM) == raw


@pytest.mark.parametrize('here,there', [('TESLA_MODEL_3', 'TESLA_MODEL_Y'), ('TESLA_MODEL_Y', 'TESLA_MODEL_3')])
def test_assignments_saved_on_the_other_model_are_used_and_kept(tmp_path, here, there):
  # A Model 3 and a Model Y have the same controls: a comma moved between them keeps its assignments, and the next
  # change saves them under the car it is in.
  rig = Rig(tmp_path, tesla(here))
  saved = {'version': 1, 'vehicle': there, 'bindings': {'right_wheel_left': {'key': BOOKMARK, 'value': None, 'label': 'Bookmark'}}}
  rig.params.put(PARAM, json.dumps(saved).encode(), block=True)
  row = rig.row(LEFT)
  assert row.value == 'Bookmark' and row.available
  assert rig.choose(PREFIX + 'right_wheel_right', 'Experimental Mode')
  document = rig.document()
  assert document['vehicle'] == here
  assert document['bindings']['right_wheel_left'] == saved['bindings']['right_wheel_left']
  assert document['bindings']['right_wheel_right']['label'] == 'Experimental Mode'


def test_three_finger_tap_can_be_always_on_lateral_on_off(rig):
  row = rig.row(SCREEN)
  assert row.choices[-1] == AOL_ACTION and row.choices[:-1] == tuple(OPTIONS)
  assert AOL_ACTION not in rig.row(LEFT).choices, 'only on the tap'
  assert rig.choose(SCREEN, 'Bookmark') and rig.saved(AOL_KEY) == b'0', 'on by default, so another choice saves it off'
  assert rig.choose(SCREEN, AOL_ACTION)
  assert rig.saved(AOL_KEY) == b'1' and 'screen_three_finger_tap' not in rig.document()['bindings']
  assert rig.row(SCREEN).value == AOL_ACTION
  assert rig.choose(SCREEN, 'Bookmark')
  assert rig.saved(AOL_KEY) == b'0' and rig.row(SCREEN).value == 'Bookmark'
  assert rig.choose(SCREEN, AOL_ACTION) and rig.choose(SCREEN, 'Unassigned')
  assert rig.saved(AOL_KEY) == b'0' and rig.row(SCREEN).value == 'Unassigned'


def test_the_tap_shows_its_saved_switch(rig):
  rig.params.put(AOL_KEY, b'1', block=True)  # as startup saves it by default, or as chosen here
  assert rig.row(SCREEN).value == AOL_ACTION
  rig.params.put(AOL_KEY, b'0', block=True)
  assert rig.row(SCREEN).value == 'Unassigned'


def test_without_the_cable_every_row_says_so_and_nothing_can_be_assigned(tmp_path):
  rig = Rig(tmp_path, tesla(cable=False))
  rows = rig.feature.rows()
  assert [row.label for row in rows] == ROW_LABELS
  assert all(row.reason.split('.\n')[0] == CABLE_MISSING and not row.available for row in rows)
  assert [row.reason for row in rows[4:]] == [CABLE_MISSING] * (len(rows) - 4)
  assert rows[0].reason == CABLE_MISSING + '.\nBy 1 mph'
  assert CABLE_MISSING == 'Needs the Tesla CAN add-on cable'
  assert not rig.feature.apply(cabled_request(tmp_path))
  assert rig.saved(PARAM) is None


@pytest.mark.parametrize('make', [lambda: None, lambda: tesla('TESLA_MODEL_X'), lambda: tesla('TESLA_MODEL_S_HW1'),
                                  lambda: car.CarParams.new_message(brand='hyundai', carFingerprint='HYUNDAI_IONIQ_5', flags=8),
                                  lambda: car.CarParams.new_message(brand='toyota', carFingerprint='TOYOTA_RAV4_TSS2', flags=8)])
def test_other_cars_have_no_rows_and_nothing_is_saved_for_them(tmp_path, make):
  rig = Rig(tmp_path, make())
  assert rig.feature.rows() == []
  assert not rig.feature.apply(cabled_request(tmp_path))
  assert rig.saved(PARAM) is None
