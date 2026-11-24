"""Tesla wheel nudges look up only their own mapped action.

Building the full Favorites catalog checks every settings page (including the maps package), which can take seconds on
a cold first press: longer than a press stays fresh, so it would be dropped. A keyed lookup avoids that work.
"""
import json
from types import SimpleNamespace as NS

import pytest

from openpilot.starpilot.common.car_control_config import PARAM
from openpilot.starpilot.controllers.tesla_receiver import TeslaReceiver
from openpilot.starpilot.controllers.tests.test_tesla_decoder import wheel
from openpilot.starpilot.controllers.tests.tesla_worker_fixture import selected_rig

LANE = 'LaneCentering'
PERSONALITY = '__starpilot_controller_action__:cycle_driving_personality'


@pytest.fixture
def setup(tmp_path):
  return selected_rig(tmp_path)


def bind(s, **bindings):
  s.doc['bindings'].update({name: {'key': key, 'value': None, 'label': key} for name, key in bindings.items()})
  s.params.put(PARAM, json.dumps(s.doc).encode(), block=True)


def keyed_receiver(s, keyed, full=None):
  def full_catalog():
    if full is None:
      raise AssertionError('full Favorites catalog must not be built for a wheel press')
    return full
  s.receiver = TeslaReceiver(s.params, full_catalog, lambda: s.cp, clock=lambda: s.clock[0],
                             messages=lambda: s.sm, channel_factory=lambda: s.channel,
                             speed_actions=lambda: s.actions, keyed_actions=keyed)
  s.receiver.poll()  # the first poll with a supported Tesla starts the worker
  assert s.receiver.channel is s.channel


def press_nudge(s, name='right_wheel_left'):
  s.feed(wheel(), dispatch=False)
  s.feed(wheel(**{name: 2}), dispatch=False)
  s.feed(wheel(), dispatch=False)
  s.receiver.poll()


def test_nudge_uses_keyed_lookup_only(setup):
  s = setup
  bind(s, right_wheel_left=LANE, right_wheel_right=PERSONALITY)
  calls, asked = [], []

  def keyed(keys):
    asked.append(set(keys))
    return {key: NS(available=True, invoke=lambda key=key: calls.append(key), invoke_value=None) for key in keys}
  keyed_receiver(s, keyed)
  press_nudge(s, 'right_wheel_left')
  press_nudge(s, 'right_wheel_right')
  assert asked == [{LANE}, {PERSONALITY}]
  assert calls == [LANE, PERSONALITY]


def test_first_press_survives_when_only_full_catalog_is_slow(setup):
  # The full catalog would take seconds (cold maps check); the keyed lookup is quick.
  s = setup
  bind(s, right_wheel_left=LANE)
  calls = []

  def keyed(keys):
    s.clock[0] += .002
    return {LANE: NS(available=True, invoke=lambda: calls.append(LANE), invoke_value=None)}
  keyed_receiver(s, keyed)
  press_nudge(s)
  assert calls == [LANE]


def test_slow_lookup_still_expires(setup):
  # The 0.25 s freshness limit is unchanged: a press is never applied late.
  s = setup
  bind(s, right_wheel_left=LANE)
  calls = []

  def keyed(keys):
    s.clock[0] += 2.3
    return {LANE: NS(available=True, invoke=lambda: calls.append(LANE), invoke_value=None)}
  keyed_receiver(s, keyed)
  press_nudge(s)
  assert not calls


def test_unavailable_keyed_action_is_not_invoked(setup):
  s = setup
  bind(s, right_wheel_left=LANE)
  calls = []
  keyed_receiver(s, lambda keys: {LANE: NS(available=False, invoke=lambda: calls.append(LANE), invoke_value=None)})
  press_nudge(s)
  assert not calls


def test_speed_scroll_keeps_its_own_catalog(setup):
  s = setup

  def keyed(keys):
    raise AssertionError('speed must not use keyed lookup')
  keyed_receiver(s, keyed)
  s.feed(wheel(), dispatch=False)
  s.feed(wheel(right_scroll=1), dispatch=False)
  s.feed(wheel(), dispatch=False)
  s.receiver.poll()
  assert s.calls == [0]


def test_without_keyed_lookup_behavior_is_unchanged(setup):
  s = setup
  bind(s, right_wheel_left=LANE)
  calls = []
  keyed_receiver(s, None, full={LANE: NS(available=True, invoke=lambda: calls.append(LANE), invoke_value=None)})
  press_nudge(s)
  assert calls == [LANE]
