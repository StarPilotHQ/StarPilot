"""Shared full-engagement intent, independent of native steering permission."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from opendbc.car.structs import car
from openpilot.starpilot.aol.intent import AolCardIntent, AolSettings, AOL_TOGGLE
from openpilot.starpilot.car.ford.aol import FordCardIntent
from openpilot.starpilot.car.gm.aol import GmAolCardIntent
from openpilot.starpilot.car.honda.stock_intent import HondaStockCardIntent
from openpilot.starpilot.car.hyundai.classic_long_intent import ClassicLongCardIntent
from openpilot.starpilot.car.hyundai.classic_scc_intent import ClassicSccCardIntent
from openpilot.starpilot.car.hyundai.ev6_intent import Ev6CardIntent
from openpilot.starpilot.car.hyundai.forte_intent import ForteCardIntent
from openpilot.starpilot.car.mazda.stock_intent import MazdaStockCardIntent
from openpilot.starpilot.car.tesla.screen_intent import TeslaScreenCardIntent
from openpilot.starpilot.car.tesla.stock_intent import TeslaPreapCardIntent
from openpilot.starpilot.car.toyota.aol import ToyotaCardIntent

SETTINGS = AolSettings(True, 0., AOL_TOGGLE, 0, (0, 0, 0), (0, 0, 0))
CP = SimpleNamespace(carFingerprint='', flags=0, safetyConfigs=[SimpleNamespace(safetyParam=0)])


def state():
  cs = car.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  return cs


def observe(owner, cs, stamp, *, enabled=True, active=True, valid=True, fault=False, rejection=0):
  owner.observe_full_engagement(cs, enabled=enabled, active=active, source_ns=stamp,
                               observation_valid=valid, now_ns=max(1, stamp),
                               fault_active=fault, native_rejection_ns=rejection)


@pytest.mark.parametrize('factory', [
  lambda: AolCardIntent(SETTINGS, explicit_latch=True),
  lambda: FordCardIntent(CP, SETTINGS), lambda: GmAolCardIntent(SETTINGS),
  lambda: HondaStockCardIntent(CP, SETTINGS), lambda: MazdaStockCardIntent(CP, SETTINGS),
  lambda: ClassicLongCardIntent(CP, SETTINGS), lambda: ClassicSccCardIntent(CP, SETTINGS),
  lambda: ForteCardIntent(CP, SETTINGS), lambda: ToyotaCardIntent(SETTINGS),
  lambda: TeslaScreenCardIntent(CP, SETTINGS),
])
def test_full_engagement_arms_each_intent_owner_and_brake_does_not_clear_it(factory):
  owner, cs = factory(), state()
  observe(owner, cs, 1, enabled=False, active=False)
  observe(owner, cs, 2, active=False)  # preEnabled is not active engagement.
  assert not owner.allowed_latch
  observe(owner, cs, 3)
  assert owner.allowed_latch and not owner.pause_lateral
  cs.brakePressed = True
  owner.update(cs, now_ns=4, fault_active=False)
  observe(owner, cs, 4, enabled=False, active=False)
  assert owner.output(cs)[0]
  assert not owner.pause_longitudinal


def test_startup_or_replayed_enabled_packet_is_not_an_engagement():
  owner, cs = AolCardIntent(SETTINGS, explicit_latch=True), state()
  for stamp in (10, 11, 12):
    observe(owner, cs, stamp)
    assert not owner.allowed_latch
  observe(owner, cs, 13, enabled=False, active=False)
  observe(owner, cs, 14)
  assert owner.allowed_latch


@pytest.mark.parametrize('valid,stamp', [(False, 3), (True, 2), (True, 1)])
def test_missing_duplicate_or_reordered_control_cannot_rearm_after_manual_off(valid, stamp):
  owner, cs = AolCardIntent(SETTINGS, explicit_latch=True), state()
  observe(owner, cs, 1, enabled=False, active=False)
  observe(owner, cs, 2)
  assert owner.allowed_latch
  owner.allowed_latch, owner.pause_lateral = False, True
  observe(owner, cs, stamp, enabled=False, active=False, valid=valid)
  observe(owner, cs, 4, active=False)
  observe(owner, cs, 5)
  assert not owner.allowed_latch and owner.pause_lateral
  observe(owner, cs, 6, enabled=False, active=False)
  observe(owner, cs, 7)
  assert owner.allowed_latch and not owner.pause_lateral


@pytest.mark.parametrize('valid,stamp', [(False, 3), (True, 2), (True, 1)])
def test_manual_off_during_pending_activation_consumes_the_epoch(valid, stamp):
  owner, cs = AolCardIntent(SETTINGS, explicit_latch=True), state()
  observe(owner, cs, 1, enabled=False, active=False)
  observe(owner, cs, 2, active=False)
  cs.buttonEvents = [car.CarState.ButtonEvent(type='lkas', pressed=True)]
  observe(owner, cs, stamp, active=False, valid=valid)
  cs.buttonEvents = []
  observe(owner, cs, 4)
  assert not owner.allowed_latch


@pytest.mark.parametrize('veto', ['disabled', 'fault', 'rejection', 'calibration', 'cancel', 'main', 'lkas'])
def test_current_veto_consumes_engagement_without_replay(veto):
  owner, cs = AolCardIntent(SETTINGS, explicit_latch=True), state()
  observe(owner, cs, 10, enabled=False, active=False)
  if veto == 'disabled':
    owner.settings = replace(SETTINGS, enabled=False)
  if veto == 'calibration':
    owner._calibration_rearm_required = True
  if veto in ('cancel', 'main', 'lkas'):
    cs.buttonEvents = [car.CarState.ButtonEvent(type='mainCruise' if veto == 'main' else veto, pressed=True)]
  observe(owner, cs, 20, fault=veto == 'fault', rejection=15 if veto == 'rejection' else 0)
  assert not owner.allowed_latch
  owner.settings, owner._calibration_rearm_required = SETTINGS, False
  cs.buttonEvents = []
  observe(owner, cs, 21)
  assert not owner.allowed_latch


@pytest.mark.parametrize('factory,field', [
  (lambda: ForteCardIntent(CP, SETTINGS), 'fault_rearm'),
])
def test_vehicle_fault_rearm_cannot_be_bypassed(factory, field):
  owner, cs = factory(), state()
  observe(owner, cs, 1, enabled=False, active=False)
  setattr(owner, field, True)
  observe(owner, cs, 2)
  assert not owner.allowed_latch



def test_gm_resolved_fault_rearms_only_after_healthy_main_observation():
  owner, cs = GmAolCardIntent(SETTINGS), state()
  observe(owner, cs, 1, enabled=False, active=False)
  owner.update(cs, now_ns=2, fault_active=True)
  assert owner._main_cycle_required and not owner.allowed_latch
  owner.update(cs, now_ns=3, fault_active=False)
  assert not owner._main_cycle_required and owner.allowed_latch
  observe(owner, cs, 3, active=False)
  assert not owner._main_cycle_required and owner.allowed_latch
  observe(owner, cs, 4)
  assert owner.allowed_latch and not owner._main_cycle_required
  assert not owner.pause_lateral
  cs.cruiseState.available = False
  owner.update(cs, now_ns=5, fault_active=False)
  assert not owner.allowed_latch


def test_physical_owners_cannot_be_forged_by_host_engagement():
  for owner in (Ev6CardIntent(SETTINGS), TeslaPreapCardIntent(CP, SETTINGS)):
    cs = state()
    observe(owner, cs, 1, enabled=False, active=False)
    observe(owner, cs, 2)
    assert not owner.allowed_latch and not owner.output(cs)[0]
    if isinstance(owner, Ev6CardIntent):
      assert not owner.physical.authorized and owner._last_latch_edge_ns == 0


def test_tesla_screen_retains_explicit_brake_disengage_and_gear_policy():
  for gear, brake in (('sport', False), ('drive', True)):
    owner = TeslaScreenCardIntent(SimpleNamespace(safetyConfigs=[SimpleNamespace(safetyParam=1024)]), SETTINGS)
    cs = state()
    observe(owner, cs, 1, enabled=False, active=False)
    cs.gearShifter, cs.brakePressed = gear, brake
    owner.update(cs, now_ns=2, fault_active=False)
    observe(owner, cs, 2)
    assert not owner.allowed_latch


def test_delayed_disabled_and_active_before_manual_off_cannot_reopen_epoch():
  owner, cs = AolCardIntent(SETTINGS, explicit_latch=True), state()
  observe(owner, cs, 80, enabled=False, active=False)
  cs.buttonEvents = [car.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.observe_full_engagement(cs, enabled=False, active=False, source_ns=90,
                               observation_valid=True, now_ns=100, fault_active=False)
  cs.buttonEvents = []
  owner.observe_full_engagement(cs, enabled=True, active=True, source_ns=95,
                               observation_valid=True, now_ns=110, fault_active=False)
  assert not owner.allowed_latch
  observe(owner, cs, 120, enabled=False, active=False)
  observe(owner, cs, 130)
  assert owner.allowed_latch


def test_missing_source_does_not_consume_pending_activation():
  owner, cs = AolCardIntent(SETTINGS, explicit_latch=True), state()
  observe(owner, cs, 1, enabled=False, active=False)
  observe(owner, cs, 2, active=False)
  observe(owner, cs, 0, valid=False)
  observe(owner, cs, 3)
  assert owner.allowed_latch


def test_auxiliary_off_after_pending_observation_cannot_be_undone_by_activation():
  owner, cs = AolCardIntent(SETTINGS, explicit_latch=False), state()
  observe(owner, cs, 1, enabled=False, active=False)
  observe(owner, cs, 2, active=False)
  owner.allowed_latch = True
  owner._perform(AOL_TOGGLE)
  assert not owner.allowed_latch
  observe(owner, cs, 3)
  assert not owner.allowed_latch


@pytest.mark.parametrize('factory', (ClassicLongCardIntent, ClassicSccCardIntent))
@pytest.mark.parametrize('main', (0, AOL_TOGGLE))
@pytest.mark.parametrize('lkas', (0, AOL_TOGGLE))
def test_classic_hkg_gas_override_defers_arming_until_release(factory, main, lkas):
  owner, cs = factory(CP, replace(SETTINGS, main_action=main, lkas_action=lkas)), state()
  observe(owner, cs, 1, enabled=False, active=False)
  cs.gasPressed = True
  observe(owner, cs, 2)
  observe(owner, cs, 3)
  assert not owner.allowed_latch
  cs.gasPressed = False
  observe(owner, cs, 4)
  assert owner.allowed_latch and owner._last_latch_edge_ns == 4
  cs.brakePressed = True
  owner.update(cs, now_ns=5, fault_active=False)
  observe(owner, cs, 5, enabled=False, active=False)
  assert owner.allowed_latch and owner.output(cs)[0]


@pytest.mark.parametrize('factory', (ClassicLongCardIntent, ClassicSccCardIntent))
@pytest.mark.parametrize('veto', ('cancel', 'lkas', 'fault', 'rejection', 'invalid', 'main_off', 'gear', 'disabled', 'calibration'))
def test_classic_hkg_pending_gas_engagement_cannot_replay_after_withdrawal(factory, veto):
  owner, cs = factory(CP, SETTINGS), state()
  observe(owner, cs, 1, enabled=False, active=False)
  cs.gasPressed = True
  observe(owner, cs, 2)
  if veto in ('cancel', 'lkas'):
    cs.buttonEvents = [car.CarState.ButtonEvent(type=veto, pressed=True)]
  elif veto == 'invalid':
    cs.canValid = False
  elif veto == 'main_off':
    cs.cruiseState.available = False
  elif veto == 'gear':
    cs.gearShifter = 'park'
  elif veto == 'disabled':
    owner.settings = replace(SETTINGS, enabled=False)
  elif veto == 'calibration':
    owner._calibration_rearm_required = True
  observe(owner, cs, 3, fault=veto == 'fault', rejection=3 if veto == 'rejection' else 0)
  owner.settings, owner._calibration_rearm_required = SETTINGS, False
  cs.gasPressed, cs.canValid = False, True
  cs.gearShifter, cs.cruiseState.available, cs.buttonEvents = 'drive', True, []
  observe(owner, cs, 4)
  assert not owner.allowed_latch
