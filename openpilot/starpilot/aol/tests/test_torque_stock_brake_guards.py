from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.hyundai.canfd_stock_aol import qualified
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from openpilot.cereal import log
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests.stock_lateral_test_helpers import car_state, hkg_family_case

MODELS = [(CAR.GENESIS_GV70_1ST_GEN, False), (CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN, True), (CAR.KIA_EV6, True)]


@contextmanager
def configured_callers(identity, lka, guard, *, hybrid=False, lkas_action=9):
  with ExitStack() as cleanup:
    with OpenpilotPrefix():
      saved = Params()
      saved.put_bool('AlwaysOnLateral', True, block=True)
      saved.put('LKASButtonControl', lkas_action, block=True)
      saved.put('MainCruiseButtonControl', 9 if guard == 'mapped_main' else 0, block=True)
      for key in ('CancelButtonControl', 'LongCancelButtonControl', 'VeryLongCancelButtonControl'):
        saved.put(key, 3 if guard == 'configured_cancel' and key == 'CancelButtonControl' else 0, block=True)
      saved.put('AolBrakePauseSpeedMps', 20.0 if guard == 'brake_pause_speed' else 0.0, block=True)
      case = hkg_family_case('hkg_canfd', False)
      cleanup.callback(case.doCleanups)
      fingerprint = gen_empty_fingerprint()
      if lka:
        fingerprint[CanBus(None, fingerprint).CAM][0x50] = 16
      if hybrid:
        fingerprint[CanBus(None, fingerprint, lka_steering=lka).ECAN][0xFA] = 32
      cp = CarInterface.get_params(identity, fingerprint, [], False, False, False)
      assert bool(cp.flags & HyundaiFlags.HYBRID) == hybrid
      assert qualified(cp) and not cp.openpilotLongitudinalControl and cp.pcmCruise
      case.family_cp = cp.as_reader()
      sd, _, step, card, receive = case.family_callers(False)
      sd.aol_settings = read_settings(saved)
      assert sd.aol_settings == card.aol_card_intent.settings
      released = car_state()
      released.buttonEvents = [structs.CarState.ButtonEvent(type='lkas', pressed=False)]
      card.aol_card_intent.update(released, now_ns=10_015_000_000)
      assert card.aol_card_intent.allowed_latch
      engaged = car_state()
      engaged.vEgoRaw = engaged.vEgo
      engaged.cruiseState.enabled = True
      axis, _, command = step(2, observed_state=engaged, panda_controls_allowed=True)
      assert sd.enabled and axis.lateralActive and command.latActive
      assert not axis.longitudinalActive and not command.longActive
      assert receive(10_020_000_000, state=engaged, axis=sd.pm.messages['aolAxisState'],
                     event=sd.pm.messages['onroadEvents'])
      yield SimpleNamespace(sd=sd, step=step, card=card, receive=receive)


@pytest.mark.parametrize('identity,lka', MODELS)
@pytest.mark.parametrize('guard', ['native_denied', 'native_long', 'native_invalid', 'native_no_request',
                                   'panda_fault', 'relay_fault', 'critical_event', 'uncalibrated',
                                   'invalid_can', 'reverse', 'brake_pause_speed', 'stale_source',
                                   'future_source', 'both_pedals', 'configured_cancel', 'mapped_main',
                                   'mapped_lkas', 'nonfinite_raw', 'regen', 'unmarked_cp', 'aol_disabled',
                                   'invalid_pause', 'negative_pause', 'excess_pause'])
def test_late_brake_continuity_cannot_override_existing_stock_guards(identity, lka, guard):
  with configured_callers(identity, lka, guard) as context:
    sd, step, card = context.sd, context.step, context.card
    state = car_state(brake=True)
    state.vEgoRaw = state.vEgo
    state.cruiseState.enabled = True
    kwargs = {'panda_controls_allowed': False}
    if guard == 'native_denied':
      kwargs['allowed'] = False
    elif guard == 'native_long':
      kwargs['native_request_long'] = True
      kwargs['long_allowed'] = False
    elif guard == 'native_invalid':
      kwargs['native_valid'] = False
    elif guard == 'native_no_request':
      kwargs['native_request_lat'] = False
      kwargs['allowed'] = False
    elif guard == 'panda_fault':
      kwargs['panda_fault'] = True
    elif guard == 'relay_fault':
      kwargs['relay_fault'] = True
    elif guard == 'critical_event':
      kwargs['critical_event'] = log.OnroadEvent.EventName.controlsMismatch
    elif guard == 'uncalibrated':
      kwargs['calibrated'] = False
    elif guard == 'brake_pause_speed':
      assert sd.aol_settings.pause_brake_mps == 20.0
      state.vEgoRaw = state.vEgo = 19.0
      assert not state.standstill and state.vEgo < sd.aol_settings.pause_brake_mps
    elif guard == 'invalid_can':
      state.canValid = False
    elif guard == 'reverse':
      state.gearShifter = 'reverse'
    elif guard == 'stale_source':
      kwargs['state_stamp'] = 10_020_000_000
    elif guard == 'future_source':
      kwargs['state_stamp'] = 10_080_000_000
    elif guard == 'both_pedals':
      kwargs['gas'] = True
    elif guard in ('configured_cancel', 'mapped_main', 'mapped_lkas'):
      button = {'configured_cancel': 'cancel', 'mapped_main': 'mainCruise', 'mapped_lkas': 'lkas'}[guard]
      state.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=True)]
      card.aol_card_intent.update(state, now_ns=10_070_000_000)
      assert not card.aol_card_intent.allowed_latch
    elif guard == 'nonfinite_raw':
      state.vEgoRaw = float('nan')
    elif guard == 'regen':
      state.regenBraking = True
    elif guard == 'aol_disabled':
      sd.params.put_bool('AlwaysOnLateral', False, block=True)
      sd.aol_settings = read_settings(sd.params)
      card.aol_card_intent.settings = sd.aol_settings
      card.aol_card_intent.update(state, now_ns=10_070_000_000)
      assert not sd.aol_settings.enabled and not card.aol_card_intent.allowed_latch
    elif guard in ('invalid_pause', 'negative_pause', 'excess_pause'):
      raw = {'invalid_pause': b'not-a-number', 'negative_pause': b'-1', 'excess_pause': b'101'}[guard]
      Path(sd.params.get_param_path('AolBrakePauseSpeedMps')).write_bytes(raw)
      sd.aol_settings = read_settings(sd.params)
      card.aol_card_intent.settings = sd.aol_settings
      card.aol_card_intent.update(state, now_ns=10_070_000_000)
      assert not sd.aol_settings.enabled and not card.aol_card_intent.allowed_latch
    elif guard == 'unmarked_cp':
      cp = sd.CP.as_builder()
      cp.safetyConfigs[0].safetyParam &= ~0x0800
      sd.CP = cp.as_reader()
    tick = 7
    if guard == 'uncalibrated':
      period = round(1e9 / min(SERVICE_LIST['extrinsicsCalibration'].frequency, 100))
      next_due = int(sd.sm.logMonoTime['extrinsicsCalibration']) + period
      tick = max(tick, (next_due - 10_000_000_000 + 9_999_999) // 10_000_000)
    axis, _, command = step(tick, observed_state=state, **kwargs)
    if guard == 'uncalibrated':
      assert int(sd.sm.logMonoTime['extrinsicsCalibration']) == 10_000_000_000 + tick * 10_000_000
      assert sd.sm['extrinsicsCalibration'].calStatus == log.ExtrinsicsCalibration.Status.uncalibrated
    assert not axis.lateralActive and not command.latActive
    assert not axis.longitudinalActive and not command.longActive


@pytest.mark.parametrize('identity,lka', MODELS)
@pytest.mark.parametrize('boundary', ['unmarked', 'longitudinal', 'unknown', 'passive', 'second_panda', 'no_brake_role'])
def test_stock_brake_exception_does_not_expand_configuration_admission(identity, lka, boundary):
  from openpilot.starpilot.aol.transport_pause import stock_lateral_off

  fingerprint = gen_empty_fingerprint()
  if lka:
    fingerprint[CanBus(None, fingerprint).CAM][0x50] = 16
  cp = CarInterface.get_params(identity, fingerprint, [], False, False, False)
  cp.safetyConfigs[0].safetyParam |= 0x0800
  assert qualified(cp, marked_only=True)
  state = car_state(brake=True)
  state.vEgoRaw = state.vEgo
  state.cruiseState.enabled = True
  state.cruiseState.available = True
  native = SimpleNamespace(requestedLateral=True, lateralAllowed=True, requestedLongitudinal=False)
  assert stock_lateral_off(cp, state, native, allow_independent_brake=True)
  if boundary == 'unmarked':
    cp.safetyConfigs[0].safetyParam &= ~0x0800
  elif boundary == 'longitudinal':
    cp.openpilotLongitudinalControl = True
    cp.pcmCruise = False
  elif boundary == 'unknown':
    cp.carFingerprint = 'UNKNOWN_HYUNDAI'
  elif boundary == 'passive':
    cp.passive = True
  elif boundary == 'second_panda':
    word = cp.safetyConfigs[0].safetyParam
    configs = cp.init('safetyConfigs', 2)
    configs[0].safetyModel = 'noOutput'
    configs[1].safetyModel = 'hyundaiCanfd'
    configs[1].safetyParam = word
    from opendbc.car.hyundai.canfd_owner import config_index

    assert config_index(cp) == 1
    assert qualified(cp, marked_only=True)
  assert not stock_lateral_off(cp, state, native, allow_independent_brake=boundary != 'no_brake_role')


@pytest.mark.parametrize('tick', [3, 7])
@pytest.mark.parametrize('gesture', ['cancel', 'brake'])
def test_source_selected_hybrid_stock_brake_continuity(tick, gesture):
  with configured_callers(CAR.HYUNDAI_TUCSON_4TH_GEN, False, 'baseline', hybrid=True) as context:
    sd, step, card, receive = context.sd, context.step, context.card, context.receive
    state = car_state(brake=gesture == 'brake')
    state.vEgoRaw = state.vEgo
    state.cruiseState.enabled = True
    if gesture == 'cancel':
      state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
    axis, _, command = step(tick, observed_state=state, panda_controls_allowed=gesture == 'cancel')
    assert axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                   axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    settled = car_state(brake=gesture == 'brake')
    settled.vEgoRaw = settled.vEgo
    axis, _, command = step(tick + 1, observed_state=settled)
    assert not sd.enabled and axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert receive(10_000_000_000 + (tick + 1) * 10_000_000, state=settled,
                   axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    assert card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('identity,lka', MODELS)
@pytest.mark.parametrize('tick', [3, 7])
@pytest.mark.parametrize('gesture', ['cancel', 'brake'])
def test_default_button_mapping_retains_independent_lateral_on_cruise_withdrawal(identity, lka, tick, gesture):
  with configured_callers(identity, lka, 'baseline', lkas_action=0) as context:
    sd, step, card, receive = context.sd, context.step, context.card, context.receive
    state = car_state(brake=gesture == 'brake')
    state.vEgoRaw = state.vEgo
    state.cruiseState.enabled = True
    if gesture == 'cancel':
      state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
    card.aol_card_intent.update(state, now_ns=10_000_000_000 + tick * 10_000_000)
    assert card.aol_card_intent.allowed_latch
    axis, _, command = step(tick, observed_state=state, panda_controls_allowed=gesture == 'cancel')
    assert axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert receive(10_000_000_000 + tick * 10_000_000, state=state,
                   axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
    settled = car_state(brake=gesture == 'brake')
    settled.vEgoRaw = settled.vEgo
    axis, _, command = step(tick + 1, observed_state=settled)
    assert not sd.enabled
    assert axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert receive(10_000_000_000 + (tick + 1) * 10_000_000, state=settled,
                   axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])


@pytest.mark.parametrize('identity,lka', MODELS)
@pytest.mark.parametrize('pause_mps', [0.0, 20.0])
def test_existing_independent_brake_policy_keeps_standstill_exception(identity, lka, pause_mps):
  guard = 'brake_pause_speed' if pause_mps else 'baseline'
  with configured_callers(identity, lka, guard) as context:
    sd, step, card, receive = context.sd, context.step, context.card, context.receive
    assert sd.aol_settings.pause_brake_mps == pause_mps
    state = car_state(brake=True)
    state.vEgoRaw = state.vEgo = 0.0
    state.standstill = True
    state.cruiseState.enabled = True
    axis, _, command = step(7, observed_state=state, panda_controls_allowed=False)
    assert not sd.enabled and not sd.CP.steerAtStandstill
    assert axis.lateralActive and not command.latActive
    assert not axis.longitudinalActive and not command.longActive
    assert receive(10_070_000_000, state=state, axis=sd.pm.messages['aolAxisState'],
                   event=sd.pm.messages['onroadEvents'])
    assert card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('identity,lka', MODELS)
def test_existing_brake_pause_threshold_equality_remains_allowed(identity, lka):
  with configured_callers(identity, lka, 'brake_pause_speed') as context:
    sd, step = context.sd, context.step
    state = car_state(brake=True)
    state.vEgoRaw = state.vEgo = sd.aol_settings.pause_brake_mps
    state.cruiseState.enabled = True
    assert state.vEgo == 20.0 and not state.standstill
    axis, _, command = step(7, observed_state=state, panda_controls_allowed=False)
    assert not sd.enabled and axis.lateralActive and command.latActive
    assert not axis.longitudinalActive and not command.longActive
