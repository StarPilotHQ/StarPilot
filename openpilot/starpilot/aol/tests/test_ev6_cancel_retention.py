"""EV6 CANCEL retains an established native-granted lateral owner."""
from contextlib import ExitStack
from dataclasses import replace

import pytest
from opendbc.car import structs
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_ev6_physical_authorization import ev6_params
from openpilot.starpilot.aol.tests.test_hkg_shared_cancel_guard import captured_context
from openpilot.starpilot.aol.transport_pause import cp_identity
from openpilot.starpilot.aol.wire import IntentState, PandaSlot, SafetyState
from openpilot.starpilot.car.hyundai.aol import physical_cancel_lateral_allowed, retained_shared_long_cancel_intent
from openpilot.starpilot.car.hyundai.ev6_intent import Ev6CardIntent
from openpilot.starpilot.aol.runtime import AxisDecision


@pytest.mark.parametrize('lkas,main', [(0, 0), (9, 0), (0, 9)])
def test_ev6_cancel_keeps_native_granted_lateral_with_default_or_mapped_buttons(lkas, main):
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    cleanup.enter_context(OpenpilotPrefix())
    sd, _, drive = case._authority_callers(True, cp=ev6_params(), genuine_events=True)
    sd.aol_settings = AolSettings(True, 0., lkas, main, (0, 0, 0), (0, 0, 0))
    for tick in range(10):
      axis, state, command = drive(tick)
      assert state.enabled and axis.lateralActive and command.latActive
    for tick, pressed in ((10, True), (11, False)):
      state = fixture.car_state()
      state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=pressed)]
      axis, ordinary, command = drive(tick, observed_state=state, native_request_lat=True,
        native_request_long=False, long_allowed=False, panda_controls_allowed=False)
      assert not ordinary.enabled
      assert axis.lateralActive and command.latActive
      assert not axis.longitudinalActive and not command.longActive


@pytest.mark.parametrize('restriction', ['healthy', 'mapped_cancel', 'old_source', 'old_native', 'wrong_session',
                                       'foreign_inventory', 'lat_denied', 'settings_off', 'not_latched',
                                       'not_previously_active', 'no_cancel', 'unmarked'])
def test_ev6_cancel_preservation_requires_current_existing_lateral(restriction):
  cp = ev6_params()
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  now, source = 10_000_000_000, 9_990_000_000
  settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
  state = structs.CarState(canValid=True, gearShifter='drive', vEgo=10.)
  state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  intent = IntentState('ev6-card', 1, source, source, source + 200_000_000, True, False, False, True, True)
  config = cp.safetyConfigs[-1]
  slot = PandaSlot(0, 'ev6-panda', int(config.safetyModel.raw), config.safetyParam, 32)
  native = SafetyState(1, True, now, now + 200_000_000, int(config.safetyModel.raw), config.safetyParam,
                       True, False, True, False, 'ev6-panda', 'ev6-session', now, (slot,))
  previous = AxisDecision(lateral_active=True, native_acknowledged=True)
  if restriction == 'mapped_cancel':
    settings = replace(settings, cancel_actions=(9, 0, 0))
  elif restriction == 'old_source':
    source = now - 30_000_001
    intent = replace(intent, carStateLogMonoTime=source, observedMonoTime=source)
  elif restriction == 'old_native':
    native = replace(native, validUntilMonoTime=now - 1)
  elif restriction == 'wrong_session':
    native = replace(native, axisSessionId='other')
  elif restriction == 'foreign_inventory':
    native = replace(native, pandaInventory=(replace(slot, safetyParam=0),))
  elif restriction == 'lat_denied':
    native = replace(native, lateralAllowed=False)
  elif restriction == 'settings_off':
    settings = replace(settings, enabled=False)
  elif restriction == 'not_latched':
    intent = replace(intent, allowedLatch=False)
  elif restriction == 'not_previously_active':
    previous = replace(previous, lateral_active=False)
  elif restriction == 'no_cancel':
    state.buttonEvents = []
  elif restriction == 'unmarked':
    cp.safetyConfigs[-1].safetyParam = 0x15
    cp.alternativeExperience = 0
  allowed = physical_cancel_lateral_allowed(cp, state, intent, native, settings=settings, previous=previous,
    source_ns=source, now_ns=now, axis_session_id='ev6-session')
  assert allowed == (restriction == 'healthy')


@pytest.mark.parametrize('restriction', ['healthy', 'missing_physical', 'stale_buttons', 'mapped_cancel', 'not_latched'])
def test_ev6_card_cancel_join_requires_its_dedicated_physical_owner(restriction):
  _, cs, sm, feedback, _, _, now = captured_context()
  cp = ev6_params()
  cp.safetyConfigs[-1].safetyParam |= 0x800
  cp.alternativeExperience = 32
  feedback.cp = cp_identity(cp)
  settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
  if restriction == 'mapped_cancel':
    settings = replace(settings, cancel_actions=(9, 0, 0))
  owner = Ev6CardIntent(settings)
  owner.allowed_latch = restriction != 'not_latched'
  owner.physical.lkas_on = restriction != 'missing_physical'
  owner._buttons_current = restriction != 'stale_buttons'
  owner._last_latch_edge_ns = now - 10_000_000
  assert retained_shared_long_cancel_intent(cp, cs, sm, feedback, owner,
    cancel_source_ns=now, now_ns=now, producer='authority-card') == (restriction == 'healthy')


@pytest.mark.parametrize('ev6', (False, True))
@pytest.mark.parametrize('restriction', ('healthy', 'no_source', 'old_source', 'lat_denied', 'rx',
                                       'heartbeat', 'same_health', 'wrong_session'))
def test_cancel_can_join_new_raw_long_off_health_on_its_first_physical_sample(ev6, restriction):
  from openpilot.starpilot.aol.wire import encode_safety

  cp, cs, sm, feedback, _, native, now = captured_context()
  if ev6:
    cp = ev6_params()
    cp.safetyConfigs[0].safetyParam |= 0x800
    cp.alternativeExperience = 32
    feedback.cp = cp_identity(cp)
  slot = replace(native.pandaInventory[0], controlsAllowed=True)
  native = replace(native, longitudinalAllowed=True, pandaInventory=(slot,),
                   sourcePandaStatesMonoTime=now - 10_000_000)
  source = now
  if restriction == 'no_source':
    source = 0
  elif restriction == 'old_source':
    source = now - 30_000_001
  elif restriction == 'lat_denied':
    native = replace(native, lateralAllowed=False)
  elif restriction == 'same_health':
    native = replace(native, sourcePandaStatesMonoTime=now)
  elif restriction == 'wrong_session':
    native = replace(native, axisSessionId='foreign')
  from openpilot.cereal import messaging
  pandas = messaging.new_message('pandaStates', 1)
  pandas.pandaStates[0] = sm['pandaStates'][0]
  pandas.pandaStates[0].safetyRxChecksInvalid = restriction == 'rx'
  pandas.pandaStates[0].heartbeatLost = restriction == 'heartbeat'
  sm['pandaStates'] = pandas.as_reader().pandaStates
  sm['aolSafetyWire'] = encode_safety(native)
  assert feedback.longitudinal_cancel_ns == 0
  cleared = feedback.observe(sm, cp, cs, now, latched=True, cancel_source_ns=source,
                             intent_producer_session='authority-card')
  assert cleared == (restriction != 'healthy')
  if restriction == 'healthy':
    assert feedback.longitudinal_cancel_ns == now
    cs.buttonEvents = []
    assert not feedback.observe(sm, cp, cs, now + 10_000_000, latched=True,
                                intent_producer_session='authority-card')
    assert feedback.longitudinal_cancel_ns == now
