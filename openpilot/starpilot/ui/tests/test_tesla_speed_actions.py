"""The comma-speed actions the Tesla's own controls run: the right scroll wheel's steps and Resume Speed Limit + Offset.

They are looked up apart from the favorites catalog, and every step still passes the publisher's own checks, so nothing
is sent for a car that does not own its set speed."""
import atexit
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from opendbc.car.structs import car
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.common.car_control_config import SPEED_DOWN, SPEED_RESUME, SPEED_UP
from openpilot.starpilot.controllers.cruise_action import CruiseActionPublisher
from openpilot.starpilot.controllers.tests.test_cruise_action import State, cp as mock_cp
from openpilot.starpilot.tests.tesla_set_speed_fixture import tesla
from openpilot.starpilot.ui.tesla_speed_actions import TeslaSpeedActions

_param_dir = tempfile.TemporaryDirectory(prefix="starpilot-speed-actions-test-")
atexit.register(_param_dir.cleanup)
_original_params_init = Params.__init__
with patch.object(Params, "__init__", lambda self, d="": _original_params_init(self, _param_dir.name)):
  from openpilot.starpilot.ui import runtime_app

NOW = 5_000_000_000


class SavedParams:
  def __init__(self):
    self.safe = False

  def get_bool(self, key):
    return self.safe if key == 'SafeMode' else False


class Publisher:
  def __init__(self):
    self.sent = []

  def send(self, topic, message):
    self.sent.append(messaging.log_from_bytes(message.to_bytes()))


class Shell(TeslaSpeedActions):
  def __init__(self):
    self.cruise_actions = CruiseActionPublisher()
    self.slc_car_actions = None

  def _live_slc_message(self, now_ns, *, car_input=False):
    return None

  def _favorite_authority(self):
    return False  # a car button never needs the screen's own authority


@pytest.fixture
def rig():
  ui = NS(params=SavedParams(), replay_clock=None, started_frame=123, CP=tesla(selected=True), sm=State(NOW))
  publisher = Publisher()
  with patch.object(runtime_app, 'ui_state', ui), patch.object(runtime_app, '_slc_action_publisher', lambda: publisher), \
       patch('openpilot.starpilot.ui.tesla_speed_actions.time.monotonic_ns', return_value=NOW):
    yield NS(ui=ui, publisher=publisher, shell=Shell())


def steps(rig):
  return [(message.slcAction.kind, message.slcAction.controllerCruise.stepUnits) for message in rig.publisher.sent]


def test_the_lookup_is_the_two_steps_and_resume(rig):
  actions = rig.shell._comma_speed_actions()
  assert set(actions) == {SPEED_UP, SPEED_DOWN, SPEED_RESUME}
  assert (actions[SPEED_UP].label, actions[SPEED_DOWN].label) == ('Increase Comma Set Speed', 'Decrease Comma Set Speed')
  assert actions[SPEED_UP].available and actions[SPEED_DOWN].available
  assert actions[SPEED_RESUME].label == 'Resume Speed Limit + Offset' and not actions[SPEED_RESUME].available
  assert not rig.publisher.sent, 'looking up sends nothing'


@pytest.mark.parametrize('key,sign', [(SPEED_UP, 1), (SPEED_DOWN, -1)])
def test_a_scroll_is_one_unit_and_a_fast_scroll_one_fast_step(rig, key, sign):
  action = rig.shell._comma_speed_actions()[key]
  assert action.invoke_value(0) is True
  assert action.invoke_value(1) is True
  assert action.invoke() is True
  assert steps(rig) == [('cruiseStep', sign), ('cruiseStep', sign * 5), ('cruiseStep', sign)]
  assert all(message.slcAction.controllerCruise.carFingerprint == 'TESLA_MODEL_3' for message in rig.publisher.sent)


@pytest.mark.parametrize('value', [2, -1, 5, .5, None, '1', float('nan')])
def test_any_other_value_sends_nothing(rig, value):
  for key in (SPEED_UP, SPEED_DOWN):
    assert rig.shell._comma_speed_actions()[key].invoke_value(value) is False
  assert not rig.publisher.sent


def test_safe_mode_and_replay_block_the_lookup_and_an_earlier_lookup(rig):
  actions = rig.shell._comma_speed_actions()
  rig.ui.params.safe = True
  assert not rig.shell._comma_speed_actions()[SPEED_UP].available
  assert actions[SPEED_UP].invoke_value(0) is False and actions[SPEED_DOWN].invoke_value(1) is False
  rig.ui.params.safe = False
  rig.ui.replay_clock = object()
  assert not rig.shell._comma_speed_actions()[SPEED_DOWN].available
  assert actions[SPEED_UP].invoke_value(0) is False, 'a callback found earlier is refused during playback too'
  assert not rig.publisher.sent


@pytest.mark.parametrize('fault', ['not engaged', 'can invalid', 'stale car state', 'offroad'])
def test_the_steps_need_fresh_engaged_driving(rig, fault):
  actions = rig.shell._comma_speed_actions()
  if fault == 'not engaged':
    rig.ui.sm['carControl'].enabled = False
  if fault == 'can invalid':
    rig.ui.sm['carState'].canValid = False
  if fault == 'stale car state':
    rig.ui.sm.logMonoTime['carState'] = NOW - 1_000_000_000
  if fault == 'offroad':
    rig.ui.sm['deviceState'].started = False
  assert not rig.shell._comma_speed_actions()[SPEED_UP].available
  assert actions[SPEED_UP].invoke_value(0) is False and not rig.publisher.sent


@pytest.mark.parametrize('make', [tesla, lambda: tesla('TESLA_MODEL_Y'), lambda: tesla(cable=False, selected=True),
                                  lambda: tesla(long=False, selected=True), lambda: tesla('TESLA_MODEL_S_HW1', selected=True),
                                  mock_cp, lambda: mock_cp(True), lambda: None])
def test_no_step_is_ever_sent_for_a_car_that_does_not_own_its_set_speed(rig, make):
  rig.ui.CP = make()
  actions = rig.shell._comma_speed_actions()
  for key in (SPEED_UP, SPEED_DOWN):
    for fast in (0, 1):
      assert actions[key].invoke_value(fast) is False
    assert actions[key].invoke() is False
  assert not rig.publisher.sent


def test_the_token_follows_the_drive_the_car_and_availability(rig):
  first = rig.shell._comma_speed_actions()[SPEED_UP].token
  assert rig.shell._comma_speed_actions()[SPEED_UP].token == first
  rig.ui.started_frame = 124
  second = rig.shell._comma_speed_actions()[SPEED_UP].token
  rig.ui.params.safe = True
  third = rig.shell._comma_speed_actions()[SPEED_UP].token
  assert len({first, second, third}) == 3


@pytest.mark.parametrize('model', ['TESLA_MODEL_3', 'TESLA_MODEL_Y'])
@pytest.mark.parametrize('selected', [False, True])
def test_resume_joins_the_favorites_catalog_on_a_model_3_or_y_with_the_cable(rig, model, selected):
  rig.ui.CP = tesla(model, selected=selected)
  catalog = {'existing': object()}
  result = rig.shell._tesla_favorite_actions(catalog, 'drive')
  assert result is catalog and set(catalog) == {'existing', SPEED_RESUME}
  assert catalog[SPEED_RESUME].label == 'Resume Speed Limit + Offset' and not catalog[SPEED_RESUME].available
  assert SPEED_UP not in catalog and SPEED_DOWN not in catalog, 'the scroll steps are never favorites'


@pytest.mark.parametrize('make', [lambda: None, lambda: tesla('TESLA_MODEL_X'), lambda: tesla('TESLA_MODEL_S_HW1'),
                                  lambda: tesla(cable=False), lambda: tesla('TESLA_MODEL_Y', cable=False, selected=True),
                                  lambda: car.CarParams.new_message(brand='hyundai', carFingerprint='HYUNDAI_IONIQ_5', flags=8),
                                  lambda: car.CarParams.new_message(brand='mock', carFingerprint='TESLA_MODEL_3', flags=8)])
def test_other_cars_and_a_tesla_without_the_cable_keep_the_favorites_catalog_as_built(rig, make):
  rig.ui.CP = make()
  catalog = {'existing': object()}
  before = dict(catalog)
  assert rig.shell._tesla_favorite_actions(catalog, 'drive') is catalog and catalog == before


def test_the_favorites_condition_reads_the_switch_at_most_once_a_second(rig, tmp_path):
  # The catalog is built many times a second; the saved switch behind its speed actions is not read each time.
  params = Params(str(tmp_path))
  opened = []
  rig.ui.params = NS(get_param_path=lambda key='': opened.append(key) or params.get_param_path(key))
  now = [100.]
  cp = tesla(selected=True)
  assert rig.shell._tesla_set_speed(None) is False and opened == [], 'made on first use; nothing read without a car'
  polled = rig.shell._tesla_set_speed_polled
  assert polled.params is rig.ui.params
  polled.clock = lambda: now[0]
  assert rig.shell._tesla_set_speed(cp) is False
  params.put_bool('TeslaSpeedSync', True, block=True)
  for _ in range(50):
    now[0] += .01
    assert rig.shell._tesla_set_speed(cp) is False
  assert opened == ['TeslaSpeedSync'], 'one read for all of those'
  now[0] = 101.
  assert rig.shell._tesla_set_speed(cp) is True and rig.shell._tesla_set_speed_polled is polled
  assert rig.shell._tesla_set_speed(tesla()) is False, 'started without the scroll frame'
  assert rig.shell._tesla_set_speed(None) is False and rig.shell._tesla_set_speed(mock_cp(True)) is False
  assert opened == ['TeslaSpeedSync', 'TeslaSpeedSync', 'SafeMode']


def test_nothing_is_read_for_the_favorites_condition_of_another_car(rig):
  def forbidden(key=''):
    raise AssertionError('the switch was read for another car')
  rig.ui.params = NS(get_param_path=forbidden)
  for cp in (None, mock_cp(), mock_cp(True), tesla(), tesla(cable=False, selected=True), tesla('TESLA_MODEL_S_HW1', selected=True)):
    assert rig.shell._tesla_set_speed(cp) is False


def test_the_session_mixes_the_actions_in():
  assert issubclass(runtime_app.StarShellSession, TeslaSpeedActions)
  assert runtime_app.StarShellSession._comma_speed_actions is TeslaSpeedActions._comma_speed_actions
