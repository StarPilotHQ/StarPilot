"""The Mazda TI2 power-up window is shown as temporary, not "LKAS Fault: Restart the car".

At ignition the TI2 reports start-up violation codes before it has proven healthy
(route 0000003a--396e50b715: 02020202 until 20.6 s; 2026-11-03: 13131313, then 02...).
CarState and panda keep that a fault, so arming stays refused, but it clears without a
restart. CarEvents maps that window to steerTempUnavailable (no permanent banner, and a
NO_ENTRY "Steering Temporarily Unavailable" on an engage attempt). Any fault after the
window, or one lasting past TI2_STARTUP_SECONDS, is steerUnavailable as before.
"""
import importlib
import sys
import types

import pytest
from opendbc.car import DT_CTRL, structs
from openpilot.cereal import log
from openpilot.starpilot.car.mazda.tests.test_standstill_release_grace import marked_gen2_cp

EVENT = log.OnroadEvent.EventName


class _FakeParams:
  def __init__(self, *args, **kwargs):
    pass

  def get(self, *args, **kwargs):
    return None

  def get_bool(self, *args, **kwargs):
    return False


@pytest.fixture(scope='module')
def car_events():
  # Host checkouts lack msgq's IPC extension and the params library; stand in for
  # them only if they cannot be imported, and remove everything imported against them.
  before = set(sys.modules)
  stubs = {}
  try:
    importlib.import_module('openpilot.cereal.messaging')
  except ModuleNotFoundError:
    stubs['openpilot.cereal.messaging'] = types.ModuleType('openpilot.cereal.messaging')
    stubs['openpilot.cereal.messaging'].SubMaster = stubs['openpilot.cereal.messaging'].PubMaster = object
  try:
    importlib.import_module('openpilot.common.params')
  except OSError:
    stubs['openpilot.common.params'] = types.ModuleType('openpilot.common.params')
    stubs['openpilot.common.params'].Params = _FakeParams
  sys.modules.update(stubs)
  module = importlib.import_module('openpilot.selfdrive.car.car_events')
  events = importlib.import_module('openpilot.selfdrive.selfdrived.events')
  yield module, events
  if stubs:
    for name in set(sys.modules) - before:
      del sys.modules[name]
    for name in stubs:
      sys.modules.pop(name, None)


def gen1_cp():
  from opendbc.car.mazda.values import CAR, MazdaSafetyFlags
  return structs.CarParams(carFingerprint=CAR.MAZDA_CX5_2022, brand="mazda", flags=int(MazdaSafetyFlags.GEN1), pcmCruise=True)


class Feed:
  def __init__(self, module, cp):
    self.events = module.CarEvents(cp)
    self.prev = structs.CarState()

  def step(self, *, permanent, can_valid=True, cruise_enabled=False):
    cs = structs.CarState(canValid=can_valid, steerFaultPermanent=permanent, gearShifter='park', standstill=True)
    cs.cruiseState.available = True
    cs.cruiseState.enabled = cruise_enabled
    out = self.events.update(cs.as_reader(), self.prev.as_reader(), structs.CarControl().as_reader())
    self.prev = cs
    return out

  def names(self, **kwargs):
    return set(self.step(**kwargs).names)


def test_powerup_window_is_temporary_and_still_blocks_engagement(car_events):
  module, events = car_events
  feed = Feed(module, marked_gen2_cp())
  # Before the first valid CAN sample, then the TI2 start-up codes (~15 s, as logged).
  for _ in range(20):
    names = feed.names(permanent=True, can_valid=False)
    assert EVENT.steerTempUnavailable in names and EVENT.steerUnavailable not in names
  for _ in range(int(15 / DT_CTRL)):
    out = feed.step(permanent=True)
    assert EVENT.steerTempUnavailable in out.names and EVENT.steerUnavailable not in out.names
  assert out.contains(events.ET.NO_ENTRY)  # engagement is still refused


def test_fault_after_the_window_is_shown_as_before(car_events):
  module, _ = car_events
  feed = Feed(module, marked_gen2_cp())
  for _ in range(500):
    feed.step(permanent=True)
  assert EVENT.steerUnavailable not in feed.names(permanent=False)  # TI2 healthy: window over
  names = feed.names(permanent=True)
  assert EVENT.steerUnavailable in names and EVENT.steerTempUnavailable not in names


def test_a_fault_lasting_past_the_window_is_shown_as_before(car_events):
  module, _ = car_events
  feed = Feed(module, marked_gen2_cp())
  frames = int(module.TI2_STARTUP_SECONDS / DT_CTRL)
  for _ in range(frames):
    assert EVENT.steerTempUnavailable in feed.names(permanent=True)
  assert EVENT.steerUnavailable in feed.names(permanent=True)


def test_engaged_cruise_ends_the_window(car_events):
  module, _ = car_events
  feed = Feed(module, marked_gen2_cp())
  feed.step(permanent=True)
  assert EVENT.steerUnavailable in feed.names(permanent=True, cruise_enabled=True)
  assert EVENT.steerUnavailable in feed.names(permanent=True)


def test_gen1_mazda_is_unchanged(car_events):
  module, _ = car_events
  feed = Feed(module, gen1_cp())
  assert EVENT.steerUnavailable in feed.names(permanent=True)
