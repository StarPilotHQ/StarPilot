from contextlib import ExitStack
from dataclasses import replace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.canfd_angle_aol import MODELS
from opendbc.car.hyundai.tests.test_stock_angle_aol import source_profile
from opendbc.car.hyundai.values import CAR
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.common.params import Params
from openpilot.starpilot.aol.intent import AolSettings, read_settings
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.car.hyundai.aol import create_intent, physical_cancel_lateral_allowed
from openpilot.starpilot.aol.runtime import AxisDecision
from openpilot.starpilot.aol.wire import IntentState, PandaSlot, SafetyState


@pytest.mark.parametrize('identity', sorted(MODELS))
@pytest.mark.parametrize('tick', [2, 6])
def test_unassigned_cancel_retains_current_angle_lateral_and_card_latch(identity, tick):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    with OpenpilotPrefix():
      saved = Params()
      saved.put('LKASButtonControl', 9, block=True)
      saved.put('MainCruiseButtonControl', 0, block=True)
      for key in ('CancelButtonControl', 'LongCancelButtonControl', 'VeryLongCancelButtonControl'):
        saved.put(key, 0, block=True)
      sd, _, step, card, receive = case._ioniq_availability_callers(full_op=False, stock_angle=identity)
      sd.aol_settings = read_settings(saved)
      assert sd.aol_settings == card.aol_card_intent.settings
      state = fixture.car_state()
      state.vEgoRaw = state.vEgo
      state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
      axis, _, command = step(tick, observed_state=state)
      assert axis.lateralActive and command.latActive
      assert not axis.longitudinalActive and not command.longActive and not sd.enabled
      assert receive(10_000_000_000 + tick * 10_000_000, state=state, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'])
      assert card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('identity', sorted(MODELS))
@pytest.mark.parametrize('action', [0, 3, 4, 9])
def test_angle_cancel_mapping_preserves_saved_action_semantics(identity, action):
  cp = source_profile(CAR(identity))
  cp.alternativeExperience = 32
  settings = AolSettings(True, 0., 9, 0, (0, 0, 0), (action, 0, 0))
  owner = create_intent(cp, settings)
  state = structs.CarState(canValid=True, gearShifter='drive')
  state.cruiseState.available = True
  owner.update(state, now_ns=1)
  state.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.update(state, now_ns=2)
  assert owner.allowed_latch
  state.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=False)]
  owner.update(state, now_ns=3)
  state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  owner.update(state, now_ns=4)
  assert owner.allowed_latch == (action == 0)
  assert owner.settings == settings


@pytest.mark.parametrize('identity', [*sorted(MODELS), CAR.HYUNDAI_PALISADE_2023])
@pytest.mark.parametrize('restriction', ['none', 'unclaimed', 'mapped_cancel', 'paused', 'old_source', 'old_native',
                                       'wrong_session', 'foreign_inventory', 'rx_invalid', 'lat_denied', 'no_cancel'])
def test_angle_cancel_preservation_requires_current_claim_and_native_inventory(identity, restriction):
  if identity == CAR.HYUNDAI_PALISADE_2023:
    observed = gen_empty_fingerprint()
    observed[2][0x50] = 16
    cp = CarInterface.get_params(identity, observed, [], False, False, False)
    assert cp.safetyConfigs[0].safetyParam == 0x2010
  else:
    cp = source_profile(CAR(identity))
  cp.alternativeExperience = 32
  now, source = 10_000_000_000, 9_990_000_000
  settings = AolSettings(True, 0., 9, 0, (0, 0, 0), (0, 0, 0))
  state = structs.CarState(canValid=True, gearShifter='drive', vEgo=10.)
  state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  intent = IntentState('angle-card', 1, source, source, source + 200_000_000, True, False, False, True, True)
  config = cp.safetyConfigs[0]
  slot = PandaSlot(0, 'angle-panda', int(config.safetyModel.raw), config.safetyParam, 32)
  native = SafetyState(1, True, now, now + 200_000_000, int(config.safetyModel.raw), config.safetyParam,
                       True, False, True, False, 'angle-panda', 'angle-session', now, (slot,))
  previous = AxisDecision(lateral_active=True, native_acknowledged=True)
  if restriction == 'unclaimed':
    previous = replace(previous, lateral_active=False)
  elif restriction == 'mapped_cancel':
    settings = replace(settings, cancel_actions=(3, 0, 0))
  elif restriction == 'paused':
    intent = replace(intent, pauseLateral=True)
  elif restriction == 'old_source':
    source = now - 30_000_001
    intent = replace(intent, carStateLogMonoTime=source, observedMonoTime=source)
  elif restriction == 'old_native':
    native = replace(native, validUntilMonoTime=now - 1)
  elif restriction == 'wrong_session':
    native = replace(native, axisSessionId='foreign-session')
  elif restriction == 'foreign_inventory':
    native = replace(native, pandaInventory=(replace(slot, safetyParam=config.safetyParam ^ 1),))
  elif restriction == 'rx_invalid':
    native = replace(native, pandaInventory=(replace(slot, safetyRxChecksInvalid=True),))
  elif restriction == 'lat_denied':
    native = replace(native, lateralAllowed=False)
  elif restriction == 'no_cancel':
    state.buttonEvents = []
  assert physical_cancel_lateral_allowed(cp, state, intent, native, settings=settings, previous=previous,
    source_ns=source, now_ns=now, axis_session_id='angle-session') == (restriction == 'none')
