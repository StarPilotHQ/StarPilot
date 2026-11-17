"""Actual stock factory/host cruise cancellation; native pairing is separate."""
import pytest
from opendbc.car import structs
from opendbc.car.hyundai.canfd_stock_aol import STOCK_EV_CARS
from openpilot.starpilot.aol.intent import AolSettings, AOL_TOGGLE
from openpilot.starpilot.aol.tests.test_canfd_stock_profiles import params
from openpilot.starpilot.car.hyundai.aol import create_intent


@pytest.mark.parametrize('identity', sorted(STOCK_EV_CARS, key=str))
@pytest.mark.parametrize('alternate', [False, True])
@pytest.mark.parametrize('incidental', ['cancel', 'mainCruise'])
def test_stock_lkas_owned_aol_survives_unassigned_cruise_gesture(identity, alternate, incidental):
  cp = params(identity, alternate=alternate)
  settings = AolSettings(True, 0., AOL_TOGGLE, 0, (0, 0, 0), (0, 0, 0))
  owner = create_intent(cp, settings)
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  owner.update(cs, now_ns=1, fault_active=False)
  assert not owner.allowed_latch  # Process start is not a driver gesture.
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.update(cs, now_ns=2, fault_active=False)
  assert owner.allowed_latch
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=False)]
  owner.update(cs, now_ns=3, fault_active=False)
  cs.cruiseState.enabled = True
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=incidental, pressed=True)]
  owner.update(cs, now_ns=4, fault_active=False, standard_enabled=True)
  cs.cruiseState.enabled = False  # Stock ACC confirms cancellation.
  cs.buttonEvents = [structs.CarState.ButtonEvent(type=incidental, pressed=False)]
  owner.update(cs, now_ns=5, fault_active=False)
  assert owner.allowed_latch
  assert not owner.pause_lateral
  # A new LKAS press still removes lateral intent.
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.update(cs, now_ns=6, fault_active=False)
  assert not owner.allowed_latch


def test_classic_long_qualified_profile_keeps_lkas_owned_cancel_intent():
  from openpilot.starpilot.aol.tests.test_classic_long_profiles import params as classic_params
  from opendbc.car.hyundai.classic_long_aol import qualified
  cp = classic_params(lda=True)
  assert qualified(cp)
  owner = create_intent(cp, AolSettings(True, 0., AOL_TOGGLE, 0, (0, 0, 0), (0, 0, 0)))
  assert owner.independent_cruise_buttons
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  owner.update(cs, now_ns=1)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.update(cs, now_ns=2)
  assert owner.allowed_latch
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=False)]
  owner.update(cs, now_ns=3)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  owner.update(cs, now_ns=4)
  assert owner.allowed_latch


@pytest.mark.parametrize('identity', sorted(STOCK_EV_CARS, key=str))
def test_default_mapping_preserves_latch_on_cancel_but_honors_deliberate_toggle(identity):
  owner = create_intent(params(identity), AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  owner.update(cs, now_ns=1, fault_active=False)
  assert not owner.allowed_latch
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.update(cs, now_ns=2, fault_active=False)
  assert owner.allowed_latch  # Existing implicit gesture when neither button is mapped.
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=False)]
  owner.update(cs, now_ns=3, fault_active=False)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  owner.update(cs, now_ns=4, fault_active=False)
  assert owner.allowed_latch
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=False)]
  owner.update(cs, now_ns=5, fault_active=False)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.update(cs, now_ns=6, fault_active=False)
  assert not owner.allowed_latch


@pytest.mark.parametrize('configured_cancel', [False, True])
def test_main_only_mapping_ignores_unassigned_lkas_but_honors_configured_cancel(configured_cancel):
  from opendbc.car.hyundai.values import CAR
  cancel_actions = (3, 0, 0) if configured_cancel else (0, 0, 0)
  owner = create_intent(params(CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN),
                        AolSettings(True, 0., 0, AOL_TOGGLE, (0, 0, 0), cancel_actions))
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  owner.update(cs, now_ns=1, fault_active=False)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='mainCruise', pressed=True)]
  owner.update(cs, now_ns=2, fault_active=False)
  assert owner.allowed_latch
  edge = owner._last_latch_edge_ns
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='mainCruise', pressed=False)]
  owner.update(cs, now_ns=3, fault_active=False)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.update(cs, now_ns=4, fault_active=False)
  assert owner.allowed_latch and owner._last_latch_edge_ns == edge
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  owner.update(cs, now_ns=5, fault_active=False)
  assert owner.allowed_latch == (not configured_cancel)
  assert owner._last_latch_edge_ns == edge
