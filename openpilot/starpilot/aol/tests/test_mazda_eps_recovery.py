from dataclasses import replace
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.values import CAR
from openpilot.cereal import log
from openpilot.selfdrive.selfdrived.events import ET, Events
from openpilot.starpilot.aol.runtime import decide_axes
from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
from openpilot.starpilot.tests.test_ford_reverse_intent import NOW, PRODUCER, change, native_change, sample


def set_events(sm, *events):
  msg = sm.messages['onroadEvents'].as_builder()
  msg.onroadEvents = list(events)
  sm.messages['onroadEvents'] = msg.as_reader()


def setup(profile=CAR.MAZDA_CX5, *, acc=False):
  cp = CarInterface.get_params(profile, gen_empty_fingerprint(), [], False, False, False)
  cp.alternativeExperience = 32
  state = structs.CarState(canValid=True, gearShifter='drive', vEgo=20.)
  state.cruiseState.available = True
  state.cruiseState.enabled = acc
  feedback = TransportPauseFeedback(cp)
  sm, _ = sample(cp, NOW, NOW, 1)
  set_events(sm)
  assert not feedback.observe(sm, cp, state, NOW, latched=True, intent_producer_session=PRODUCER)
  assert feedback.lateral_only_baseline
  return cp, state, feedback


def output(state, native):
  return decide_axes(standard_lateral=False, standard_longitudinal=False,
    intent=SimpleNamespace(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False), native=native, car_state=state,
    initialized=True, model_ready=True, no_entry=False, immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)


@pytest.mark.parametrize('identity', [CAR.MAZDA_CX5, CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021])
@pytest.mark.parametrize('pedal', ['brake', 'gas'])
def test_mazda_eps_retains_intent_during_pedal_pulse(identity, pedal):
  cp, state, feedback = setup(identity, acc=pedal == 'gas')
  state.steerFaultTemporary = True
  state.brakePressed, state.gasPressed = pedal == 'brake', pedal == 'gas'
  stamp = NOW + 10_000_000
  sm, native = sample(cp, stamp, stamp, 2, active=False, allowed=False, event_stamp=stamp)
  set_events(sm, {'name': 'steerTempUnavailable', 'softDisable': True},
             {'name': 'pedalPressed', 'userDisable': True} if pedal == 'brake' else
             {'name': 'gasPressedOverride', 'overrideLongitudinal': True})
  assert not feedback.observe(sm, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)
  assert not feedback.failed and not output(state, native).lateral_active
  assert feedback.temporary_eps_event_ns == stamp


@pytest.mark.parametrize('fault', [None, 'cancel', 'native_fault', 'permanent_eps', 'expired_axis', 'expired_join', 'new_user_disable'])
@pytest.mark.parametrize('pending_request', [False, True])
def test_mazda_eps_recovery_needs_fresh_native_and_preserves_faults(fault, pending_request):
  cp, state, feedback = setup(acc=True)
  state.steerFaultTemporary = True
  stamp = NOW + 10_000_000
  sm, _ = sample(cp, stamp, stamp, 2, active=False, requested=False, allowed=False, event_stamp=stamp)
  change(sm, 'aolAxisState', lambda axis: setattr(axis, 'nativeAcknowledged', False))
  set_events(sm, {'name': 'steerTempUnavailable', 'softDisable': True})
  assert not feedback.observe(sm, cp, state, stamp, latched=True, intent_producer_session=PRODUCER)
  lease = feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns
  state.steerFaultTemporary = False
  state.gasPressed = True
  now = stamp + (210_000_000 if fault == 'expired_join' else 20_000_000)
  sm, native = sample(cp, now, now, 3, active=False, requested=not pending_request, allowed=False, event_stamp=now)
  change(sm, 'aolAxisState', lambda axis: setattr(axis, 'desiredLateral', True))
  set_events(sm, {'name': 'gasPressedOverride', 'overrideLongitudinal': True})
  if fault == 'cancel':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
    set_events(sm, {'name': 'buttonCancel', 'userDisable': True})
  elif fault == 'native_fault':
    native = native_change(sm, native, pandaInventory=(replace(native.pandaInventory[0], faults=1),))
  elif fault == 'permanent_eps':
    state.steerFaultPermanent = True
  elif fault == 'expired_axis':
    change(sm, 'aolAxisState', lambda axis: setattr(axis, 'validUntilMonoTime', now))
    now += 1
  elif fault == 'new_user_disable':
    set_events(sm, {'name': 'pedalPressed', 'userDisable': True})
  lost = feedback.observe(sm, cp, state, now, latched=True, intent_producer_session=PRODUCER)
  assert feedback.temporary_host_qualified == (fault is None)
  if fault is None:
    assert not lost
  elif not pending_request or fault in ('native_fault', 'permanent_eps', 'expired_axis'):
    assert lost
  assert not output(state, native).lateral_active
  if fault is None:
    assert lease == (feedback.lateral_baseline_ns, feedback.lateral_source_ns, feedback.lateral_native_ns)
    recovered, grant = sample(cp, now + 10_000_000, now + 10_000_000, 4, event_stamp=now)
    set_events(recovered)
    assert not feedback.observe(recovered, cp, state, now + 10_000_000, latched=True, intent_producer_session=PRODUCER)
    assert output(state, grant).lateral_active


def test_mazda_eps_advisory_leaves_takeover_warning_intact():
  cp, state, _ = setup()
  events = Events()
  events.add(log.OnroadEvent.EventName.steerTempUnavailableSilent)
  args = [cp, state, None, False, 100, None]
  advisory, = events.create_alerts([ET.WARNING], args)
  assert advisory.alert_text_1 == 'Steering Assist Temporarily Unavailable'
  assert advisory.visual_alert == structs.CarControl.HUDControl.VisualAlert.none
  assert advisory.audible_alert == log.SelfdriveState.AudibleAlert.none
  events.add(log.OnroadEvent.EventName.steerTempUnavailable)
  takeover, = events.create_alerts([ET.SOFT_DISABLE], args)
  assert takeover.audible_alert == log.SelfdriveState.AudibleAlert.warningSoft
  cp.alternativeExperience = 0
  standard_advisory, = events.create_alerts([ET.WARNING], args)
  assert standard_advisory.visual_alert == structs.CarControl.HUDControl.VisualAlert.steerRequired
  assert standard_advisory.audible_alert == log.SelfdriveState.AudibleAlert.prompt
