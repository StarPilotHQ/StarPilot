"""Mixed Palisade Pause/Resume retains only existing, current lateral authority."""
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
import pytest
from opendbc.car import structs
from openpilot.cereal import log
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.starpilot.aol.intent import AolSettings, AOL_TOGGLE
from openpilot.starpilot.aol.runtime import AxisDecision
from openpilot.starpilot.aol.wire import IntentState, SafetyState, PandaSlot
from openpilot.starpilot.aol.tests.test_classic_brake_withdrawal import profile
from openpilot.starpilot.car.hyundai.aol import create_intent


class Messages(dict):
  ready = True
  def all_checks(self, services=None):
    return self.ready


@pytest.mark.parametrize('identity', ('palisade_hdai', 'palisade_hdaii'))
@pytest.mark.parametrize('case', ['retain', 'prior_off', 'settings_off', 'configured_cancel', 'intent_off', 'stale_source',
                                 'native_denied', 'native_expired', 'wrong_session', 'wrong_inventory', 'hard_event',
                                 'model', 'dm', 'reverse', 'steer_fault'])
def test_actual_selfdrived_cancel_keeps_only_current_owned_lateral(identity, case):
  now = 4_000_000_000
  cp = profile(identity)
  config = cp.safetyConfigs[0]
  model, word = int(config.safetyModel.raw), int(config.safetyParam)
  intent = IntentState('card', 1, now, now, now + 200_000_000, True, False, False, True, True)
  slot = PandaSlot(0, 'panda', model, word, cp.alternativeExperience)
  inventory = (slot,)
  native = SafetyState(1, True, now, now + 200_000_000, model, word, True, False, True, False,
                       'panda', 'axis', now, inventory)
  cs = structs.CarState(canValid=True, gearShifter='drive', vEgo=10.)
  cs.cruiseState.available = True
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  sm = Messages(driverMonitoringState=SimpleNamespace(alertLevel=0, lockout=False, alwaysOnLockout=False),
                extrinsicsCalibration=SimpleNamespace(calStatus=log.ExtrinsicsCalibration.Status.calibrated))
  sd = SimpleNamespace(CP=cp, active=False, enabled=False, initialized=True, sm=sm,
                       aol_settings=AolSettings(True, 0., AOL_TOGGLE, 0, (0,0,0), (0,0,0)),
                       aol_axis_decision=AxisDecision(lateral_active=True, native_acknowledged=True),
                       aol_car_state_log_ns=now, aol_session_id='axis', aol_dm_lateral_inhibit=False,
                       nostalgia_paddle_cancel=False, events=Events())
  sd.events.add(log.OnroadEvent.EventName.buttonCancel)
  if case == 'prior_off':
    sd.aol_axis_decision = AxisDecision()
  elif case == 'settings_off':
    sd.aol_settings = replace(sd.aol_settings, enabled=False)
  elif case == 'configured_cancel':
    sd.aol_settings = replace(sd.aol_settings, cancel_actions=(3,0,0))
  elif case == 'intent_off':
    intent = replace(intent, allowedLatch=False)
  elif case == 'stale_source':
    sd.aol_car_state_log_ns = now - 30_000_001
    intent = replace(intent, carStateLogMonoTime=sd.aol_car_state_log_ns)
  elif case == 'native_denied':
    native = replace(native, lateralAllowed=False)
  elif case == 'native_expired':
    native = replace(native, validUntilMonoTime=now-1)
  elif case == 'wrong_session':
    native = replace(native, axisSessionId='other')
  elif case == 'wrong_inventory':
    native = replace(native, pandaInventory=(*inventory[:-1], replace(slot, safetyParam=word ^ 0x80)))
  elif case == 'hard_event':
    sd.events.add(log.OnroadEvent.EventName.controlsMismatch)
  elif case == 'model':
    sm.ready = False
  elif case == 'dm':
    sm['driverMonitoringState'].lockout = True
  elif case == 'reverse':
    cs.gearShifter = 'reverse'
  elif case == 'steer_fault':
    cs.steerFaultPermanent = True
  result = SelfdriveD._decide_aol_axes(cast(SelfdriveD, sd), cs, intent, native, now_ns=now)
  assert result.lateral_active == (case == 'retain')
  assert not result.longitudinal_active
  assert log.OnroadEvent.EventName.buttonCancel in sd.events.names


@pytest.mark.parametrize('identity', ('palisade_hdai', 'palisade_hdaii'))
@pytest.mark.parametrize('duration', (61_047_724, 221_004_624))
@pytest.mark.parametrize('cancel_actions', ((0, 0, 0), (3, 0, 0), (0, 3, 0), (0, 0, 3)))
def test_host_cancel_hold_keeps_only_unassigned_preexisting_latch(identity, duration, cancel_actions):
  cp = profile(identity)
  settings = AolSettings(True, 0., AOL_TOGGLE, 0, (0, 0, 0), cancel_actions)
  owner = create_intent(cp, settings)
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  owner.update(cs, now_ns=1_000_000_000)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=True)]
  owner.update(cs, now_ns=1_010_000_000)
  assert owner.allowed_latch
  edge, arm = owner._last_latch_edge_ns, owner.physical_arm_ns
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=False)]
  owner.update(cs, now_ns=1_020_000_000)
  for stamp, events in ((1_030_000_000, [('cancel', True)]),
                        (1_040_000_000, []), (1_030_000_000 + duration, [('cancel', False)])):
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=kind, pressed=pressed) for kind, pressed in events]
    owner.update(cs, now_ns=stamp)
    assert owner.allowed_latch == (not any(cancel_actions))
    assert (owner._last_latch_edge_ns, owner.physical_arm_ns) == (edge, arm)
  assert not owner.independent_cruise_buttons  # MAIN/LKAS semantics are not changed.


@pytest.mark.parametrize('identity', ('palisade_hdai', 'palisade_hdaii'))
@pytest.mark.parametrize('simultaneous', (None, 'lkas', 'mainCruise'))
def test_cold_cancel_never_arms_or_renews_gesture(identity, simultaneous):
  owner = create_intent(profile(identity), AolSettings(True, 0., AOL_TOGGLE, 0, (0,0,0), (0,0,0)))
  cs = structs.CarState(canValid=True, gearShifter='drive')
  cs.cruiseState.available = True
  owner.update(cs, now_ns=1_000_000_000)
  buttons = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  if simultaneous:
    buttons.append(structs.CarState.ButtonEvent(type=simultaneous, pressed=True))
  cs.buttonEvents = buttons
  owner.update(cs, now_ns=1_010_000_000)
  assert not owner.allowed_latch
  assert owner._last_latch_edge_ns == owner.physical_arm_ns == 0


@pytest.mark.parametrize('identity', ('palisade_hdai', 'palisade_hdaii'))
def test_card_retains_original_cancel_receipt_until_lateral_only_ack(identity):
  """Real Card caller, synthetic transport; parser receipt is tested separately."""
  from unittest.mock import Mock, patch
  from opendbc.car.hyundai.blended_longitudinal import BlendedStartup, candidate_from_stock
  from opendbc.car.hyundai.tests.test_palisade_2023 import params as mixed_params
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.starpilot.aol.tests.test_ioniq_availability import IoniqAvailabilityTests, car_state

  case = IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix():
    try:
      stock = mixed_params('hdaii' if identity == 'palisade_hdaii' else 'hdai')
      cp = candidate_from_stock(stock, alpha_requested=True, native_qualified=True)
      startup = BlendedStartup(stock, cp.as_reader().as_builder(), (Mock(), Mock()))
      startup.prepare(admission=lambda: True)
      case.addCleanup(startup.close)
      Params().put('LKASButtonControl', AOL_TOGGLE, block=True)
      sd, _, drive, card, receive = case._ioniq_availability_callers(True, cp=cp, startup_owner=startup)
      sd.aol_settings = card.aol_settings
      source = 10_020_000_000
      cs = car_state()
      cs.cruiseState.available = True
      cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
      # Native withdrawal arrives before the next SelfdriveD publication.
      drive(2, native_request_lat=True, native_request_long=True, long_allowed=False,
            panda_controls_allowed=False)
      old_axis = sd.pm.messages['aolAxisState'].as_builder()
      old_axis.logMonoTime = old_axis.aolAxisState.observedMonoTime = source - 1_000_000
      old_axis.aolAxisState.sourceCarStateMonoTime = source - 1_000_000
      old_axis.aolAxisState.validUntilMonoTime = source + 29_000_000
      old_axis.aolAxisState.mode = 'combined'
      old_axis.aolAxisState.desiredLongitudinal = old_axis.aolAxisState.longitudinalActive = True
      with patch.object(card, 'aol_cancel_source', return_value=source):
        assert receive(source, axis=old_axis.as_reader(), state=cs)
      assert card.aol_transport_feedback.longitudinal_cancel_ns == source
      # No second physical press, no newer axis yet, now the cancellation event.
      event = log.Event.new_message(logMonoTime=source)
      event.init('onroadEvents', 1)
      event.onroadEvents[0].name = 'buttonCancel'
      event.onroadEvents[0].userDisable = True
      event.onroadEvents[0].noEntry = True
      cs.buttonEvents = []
      with patch.object(card, 'aol_cancel_source', return_value=0):
        assert receive(source + 10_000_000, event=event.as_reader(), state=cs)
      assert card.aol_transport_feedback.longitudinal_cancel_ns == source
      assert not card.aol_transport_feedback.failed
      # SelfdriveD receives the same physical press; its actual LAT-only output
      # and the native LAT-only request close the bounded withdrawal handshake.
      cancel = car_state()
      cancel.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
      axis, state, command = drive(4, observed_state=cancel, native_request_lat=True,
        native_request_long=False, long_allowed=False, panda_controls_allowed=False)
      assert not state.enabled and axis.lateralActive and command.latActive
      assert not axis.longitudinalActive and not command.longActive
      with patch.object(card, 'aol_cancel_source', return_value=0):
        assert receive(source + 20_000_000, axis=sd.pm.messages['aolAxisState'],
                       event=sd.pm.messages['onroadEvents'], state=cs)
      assert card.aol_card_intent.allowed_latch
      assert card.aol_transport_feedback.longitudinal_cancel_ns == 0
    finally:
      case.doCleanups()


@pytest.mark.parametrize('identity', ('palisade_hdai', 'palisade_hdaii'))
@pytest.mark.parametrize('fault', ('none', 'wrong_bus', 'stale', 'unmarked', 'stock'))
def test_cancel_source_is_bound_to_real_mixed_parser_receipt(identity, fault):
  from opendbc.can import CANPacker
  from opendbc.car import Bus
  from opendbc.car.hyundai.interface import CarInterface
  from openpilot.selfdrive.car.card import Car
  cp = profile(identity)
  if fault == 'unmarked':
    cp.alternativeExperience = 0
  elif fault == 'stock':
    cp.openpilotLongitudinalControl, cp.pcmCruise = False, True
    cp.safetyConfigs[0].safetyParam &= ~4
  ci = CarInterface(cp)
  parser = ci.can_parsers[Bus.pt]
  packer = CANPacker(parser.dbc.name)
  for tick in range(15):
    now = 2_000_000_000 + tick * 20_000_000
    packet = packer.make_can_msg('CLU11', 2 if fault == 'wrong_bus' else parser.bus,
      {'CF_Clu_CruiseSwState': 4 if tick == 14 else 0, 'CF_Clu_AliveCnt1': tick})
    cs = ci.update([(now, [packet])])
  if fault == 'wrong_bus':
    cs.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  received = now - 30_000_001 if fault == 'stale' else now
  caller = SimpleNamespace(CI=ci, _aol_can_observation=(cs, received, now, now))
  assert Car.aol_cancel_source(cast(Car, caller), cs, now) == (now if fault == 'none' else 0)
