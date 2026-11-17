"""The live SLC cap remains independent of the driver's software or native SET."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from opendbc.car.structs import car
from openpilot.common.constants import CV
from openpilot.selfdrive.car.cruise import VCruiseHelper, slc_target_kph
from openpilot.selfdrive.controls.lib import longitudinal_planner as planner
from openpilot.starpilot.speed_limits import acceptance as acc, physical_actions
from openpilot.starpilot.speed_limits.runtime import Action, CruiseEvent, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM
from openpilot.starpilot.speed_limits.tests import test_runtime_replay as replay_fixture
from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule

START = 2_000_000_000


@pytest.fixture
def drive():
  fixture = replay_fixture.RuntimeReplayTests()
  fixture.setUp()
  fixture.cp.pcmCruise = False
  fixture.sm['carState'].vCruise = fixture.sm['carState'].vCruiseCluster = 60.
  return fixture.sm, fixture.cp


def tick(runtime, sm, cp, seconds, **kwargs):
  now = START + round(seconds * 1e9)
  sm.advance(now)
  sm['slcDashboardObservation'].validUntilMonoTime = now + 1_000_000_000
  return runtime.step(sm, cp, now_ns=now, **kwargs)



@pytest.mark.parametrize('wheel', [False, True])
@pytest.mark.parametrize('response', ['accept', 'reject'])
def test_retained_confirmation_uses_the_original_choice_without_a_speed_command(drive, wheel, response):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}))
  first = tick(runtime, sm, cp, 0).message.slcState
  sm['slcDashboardObservation'].status = 'unknown'
  lost = tick(runtime, sm, cp, .05)
  held = lost.message.slcState
  assert held.hasPending and held.decisionId == first.decisionId
  assert held.pendingSinceMonoTime == first.pendingSinceMonoTime
  assert held.presentationId == first.presentationId
  invalid_accept = Action(runtime.session_id, 1, held.decisionId, 0, 'accept')
  denied = tick(runtime, sm, cp, .075, request=invalid_accept)
  assert denied.message.slcState.hasPending
  if wheel:
    card_sm = ReplaySM({'slcState': held}, START + 50_000_000)
    pending = physical_actions.pending_confirmation(card_sm, START + 50_000_000, long_active=True)
    assert pending is not None and pending.presentation_id == first.presentationId
    helper = VCruiseHelper(cp)
    helper.v_cruise_kph = helper.v_cruise_cluster_kph = 60.
    cs = car.CarState(cruiseState={'available': True},
                      buttonEvents=[car.CarState.ButtonEvent(type='accelCruise' if response == 'accept' else 'decelCruise', pressed=True)])
    helper.update_v_cruise(cs, True, False, pending, slc_enabled=True)
    assert helper.slc_consumed_button is not None and helper.v_cruise_kph == 60.
    event = CruiseEvent(1, START + 100_000_000, 0., 0., 'accel' if response == 'accept' else 'decel',
                       'card-producer', START + 100_000_000, 'confirmationAccept' if response == 'accept' else 'confirmationReject',
                       runtime.session_id, held.decisionId, held.presentationId)
    result = tick(runtime, sm, cp, .1, cruise_event=event)
  else:
    request = Action(runtime.session_id, 2, held.decisionId, held.presentationId, response)
    result = tick(runtime, sm, cp, .1, request=request)
  assert not result.result.errors and result.message.slcState.actionStatus == response
  assert not result.message.slcState.hasPending
  assert (result.result.ceiling is not None) == (response == 'accept')
  assert sm['carState'].vCruise == 60.
  sm['slcDashboardObservation'].status = 'valid'
  recovered = tick(runtime, sm, cp, .15)
  assert not recovered.message.slcState.hasPending


@pytest.mark.parametrize('manual', [False, True])
def test_provider_changes_are_immediate_and_vision_gaps_preserve_the_current_decision(drive, manual):
  from openpilot.cereal import messaging
  from openpilot.starpilot.speed_limits.vision.observation import MODEL_ID

  sm, cp = drive
  envelope = messaging.new_message('slcVisionObservation')
  vision = envelope.slcVisionObservation.vision
  vision.producerSessionId, vision.modelId, vision.stream = 'vision-producer', MODEL_ID, 'road'
  vision.speedMps, vision.confidence, vision.supportCount, vision.episode = 35 * CV.MPH_TO_MS, .95, 2, 4
  sm = ReplaySM({**sm.data, 'slcVisionObservation': envelope.slcVisionObservation}, START)
  sm['slcDashboardObservation'].speedMps = 30 * CV.MPH_TO_MS
  selected = slc_target_kph(30 * CV.MPH_TO_MS, False)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = selected
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard',
                          'SLCPriority2': 'Vision', 'SLCConfirmation': int(manual)}),
                    vision_enabled=True, vision_control_qualified=True)

  def observe(seconds, dashboard=False, available=True, **kwargs):
    now = START + round(seconds * 1e9)
    sm['slcDashboardObservation'].status = 'valid' if dashboard else 'unknown'
    vision.status = 'valid' if available else 'stale'
    vision.observedMonoTime = vision.cameraFrameEofBootTime = now
    vision.validUntilMonoTime = now + 2_000_000_000
    vision.frameId += 1
    return tick(runtime, sm, cp, seconds, now_boot_ns=now, **kwargs)

  initial = observe(0, dashboard=True)
  assert initial.message.slcState.hasAccepted
  for seconds, primary in ((.1, False), (.5, True), (1., False), (1.5, True), (2., False)):
    output = observe(seconds, dashboard=primary)
    assert output.message.slcState.hasPending == (manual and not primary)
    if manual:
      assert output.message.slcState.acceptedMonoTime == initial.message.slcState.acceptedMonoTime
      assert output.message.slcState.acceptedSource == 'dashboard'
    else:
      assert output.message.slcState.acceptedMonoTime == START + round(seconds * 1e9)
      assert output.message.slcState.acceptedSource == ('dashboard' if primary else 'vision')
  settled = observe(4.)
  assert not settled.result.errors
  assert settled.message.slcState.hasPending == manual
  assert settled.message.slcState.acceptedMonoTime == output.message.slcState.acceptedMonoTime
  if manual:
    assert settled.message.slcState.decisionId == output.message.slcState.decisionId
    assert settled.message.slcState.pendingSinceMonoTime == START + 2_000_000_000
  if not manual:
    assert settled.result.ceiling is not None
  lost = observe(4.05, available=False)
  recovered = observe(4.15)
  assert lost.message.slcState.hasPending == recovered.message.slcState.hasPending == manual
  assert recovered.message.slcState.acceptedMonoTime == settled.message.slcState.acceptedMonoTime
  if manual:
    assert lost.message.slcState.presentationId == settled.message.slcState.presentationId
    assert lost.message.slcState.decisionId == recovered.message.slcState.decisionId == settled.message.slcState.decisionId
    assert lost.message.slcState.pendingSinceMonoTime == recovered.message.slcState.pendingSinceMonoTime == settled.message.slcState.pendingSinceMonoTime
  vision.speedMps = 40 * CV.MPH_TO_MS
  changed = observe(4.2)
  assert changed.message.slcState.hasPending == manual
  assert changed.result.ceiling is not None
  if manual:
    assert changed.message.slcState.decisionId != settled.message.slcState.decisionId


@pytest.mark.parametrize('button,kind', [('accel', 'confirmationAccept'), ('decel', 'confirmationReject')])
def test_wheel_confirmation_does_not_follow_same_step_source_replacement(drive, button, kind):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}))
  pending = tick(runtime, sm, cp, 0).message.slcState
  sm['slcDashboardObservation'].episode += 1
  sm['slcDashboardObservation'].observedMonoTime = START + 40_000_000
  event = CruiseEvent(1, START + 50_000_000, 0.0, 0.0, button, 'card-producer', START + 50_000_000,
                      kind, runtime.session_id, pending.decisionId, pending.presentationId)
  output = tick(runtime, sm, cp, .05, cruise_event=event)
  assert output.message.slcState.hasPending and not output.message.slcState.hasAccepted
  assert output.message.slcState.actionStatus == 'different_presentation'
  assert runtime.ledger.transactions == ()


@pytest.mark.parametrize('confirmation', [False, True])
@pytest.mark.parametrize('metric,limit,offset', [(False, 45., 0.), (False, 45., 5.), (False, 45., -.4),
                                              (True, 72., 0.), (True, 72., 5.), (True, 72., -.4)])
def test_matching_set_needs_neither_confirmation_nor_command(drive, confirmation, metric, limit, offset):
  sm, cp = drive
  conversion = CV.KPH_TO_MS if metric else CV.MPH_TO_MS
  settings = replace(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': int(confirmation)}),
                     offsets=OffsetSchedule((OffsetBand(0., None, offset * conversion),)))
  runtime = Runtime(settings, is_metric=metric)
  sm['slcDashboardObservation'].speedMps = limit * conversion
  selected = slc_target_kph((limit + offset) * conversion, metric)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = selected
  selected = float(sm['carState'].vCruise)
  for seconds in (0, .05, 11):
    output = tick(runtime, sm, cp, seconds)
    assert not output.result.errors and not output.message.slcState.hasPending
    assert output.message.slcState.hasAccepted
    assert output.message.slcState.commandStatus == ''
    assert sm['carState'].vCruise == selected


@pytest.mark.parametrize('source_name', ['Maps', 'Vision'])
def test_secondary_source_matching_set_skips_confirmation_and_command(drive, source_name):
  from openpilot.cereal import messaging
  from openpilot.starpilot.speed_limits.selection import Source
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': source_name, 'SLCConfirmation': 1}),
                    vision_enabled=True, vision_control_qualified=True)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(65 * CV.MPH_TO_MS, False)
  source = Source.MAP if source_name == 'Maps' else Source.VISION
  observations = dict.fromkeys(Source, acc.Observation(acc.ObservationKind.UNKNOWN))
  observations[source] = acc.Observation(acc.ObservationKind.VALID, acc.Candidate(source.value,
    acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value='current-road'), 65 * CV.MPH_TO_MS))
  vision = messaging.new_message('slcVisionObservation').slcVisionObservation
  sm.data['slcVisionObservation'] = vision
  with patch.object(runtime, '_observations', return_value=observations):
    aligned = tick(runtime, sm, cp, 0)
  assert aligned.message.slcState.acceptedSource == source.value
  assert not aligned.message.slcState.hasPending


@pytest.mark.parametrize('selected', [65., 70.])
def test_returned_accepted_limit_clears_confirmation_independently_of_set(drive, selected):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}))
  sm['slcDashboardObservation'].speedMps = 65 * CV.MPH_TO_MS
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(65 * CV.MPH_TO_MS, False)
  first = tick(runtime, sm, cp, 0).message.slcState
  tick(runtime, sm, cp, .05, request=Action(runtime.session_id, 1, 0, first.presentationId, 'adopt'))
  sm['slcDashboardObservation'].speedMps = 55 * CV.MPH_TO_MS
  assert tick(runtime, sm, cp, .1).message.slcState.hasPending
  assert tick(runtime, sm, cp, 30.1).result.acceptance.basis == 'timeout_reject'
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(selected * CV.MPH_TO_MS, False)
  sm['slcDashboardObservation'].speedMps = 65 * CV.MPH_TO_MS
  returned = tick(runtime, sm, cp, 30.2)
  assert not returned.message.slcState.hasPending


@pytest.mark.parametrize('action_kind', ['accept', 'reject'])
@pytest.mark.parametrize('seconds', [.05, 30.])
def test_presented_driver_choice_wins_when_set_reaches_the_proposal(drive, action_kind, seconds):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}))
  sm['slcDashboardObservation'].speedMps = 55 * CV.MPH_TO_MS
  pending = tick(runtime, sm, cp, 0).message.slcState
  selected = slc_target_kph(55 * CV.MPH_TO_MS, False)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = selected
  output = tick(runtime, sm, cp, seconds,
    request=Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, action_kind))
  assert output.result.acceptance.action_receipt.consumed
  assert output.result.acceptance.basis == ('driver_accept' if action_kind == 'accept' else 'driver_reject')
  assert sm['carState'].vCruise == selected
  held = tick(runtime, sm, cp, seconds + .05)
  assert not held.message.slcState.hasPending
  if action_kind == 'reject':
    assert held.result.acceptance.basis == 'rejected' and not held.message.slcState.hasAccepted


@pytest.mark.parametrize('seconds', [30., 30.001, 31.])
def test_set_alignment_cannot_accept_an_expired_confirmation(drive, seconds):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}))
  sm['slcDashboardObservation'].speedMps = 55 * CV.MPH_TO_MS
  pending = tick(runtime, sm, cp, 0)
  assert pending.message.slcState.hasPending
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(55 * CV.MPH_TO_MS, False)
  expired = tick(runtime, sm, cp, seconds)
  assert expired.result.acceptance.basis == 'timeout_reject'
  assert not expired.message.slcState.hasAccepted and not expired.message.slcState.hasPending


@pytest.mark.parametrize('unavailable', ['off', 'lateral', 'stock', 'stale_set', 'stale_ego', 'unset', 'force_decel'])
def test_set_alignment_requires_current_system_speed_authority(drive, unavailable):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}))
  sm['slcDashboardObservation'].speedMps = 65 * CV.MPH_TO_MS
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(65 * CV.MPH_TO_MS, False)
  sm.advance(START)
  if unavailable in ('off', 'lateral', 'stock'):
    sm['carControl'].longActive = False
    sm['controlsState'].longControlState = 'off'
    if unavailable == 'stock':
      cp.openpilotLongitudinalControl = False
      sm['carState'].cruiseState.enabled = True
    else:
      sm['carControl'].enabled = sm['selfdriveState'].enabled = False
      sm['carControl'].latActive = unavailable == 'lateral'
  elif unavailable == 'stale_set':
    sm.valid['carState'] = False
  elif unavailable == 'stale_ego':
    sm['carState'].vEgoCluster = float('nan')
  elif unavailable == 'unset':
    sm['carState'].vCruise = sm['carState'].vCruiseCluster = 255.
  else:
    sm['controlsState'].forceDecel = True
  output = runtime.step(sm, cp, now_ns=START)
  assert not output.message.slcState.hasAccepted


def test_reaching_actual_speed_does_not_accept_a_different_set_target(drive):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}))
  sm['slcDashboardObservation'].speedMps = 45 * CV.MPH_TO_MS
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 45 * CV.MPH_TO_MS
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = slc_target_kph(65 * CV.MPH_TO_MS, False)
  output = tick(runtime, sm, cp, 0)
  assert output.message.slcState.hasPending and not output.message.slcState.hasAccepted


@pytest.mark.parametrize('direction', [-1, 1])
def test_matching_limit_keeps_wheel_buttons_available_for_normal_set_changes(drive, direction):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}))
  sm['slcDashboardObservation'].speedMps = 65 * CV.MPH_TO_MS
  selected = slc_target_kph(65 * CV.MPH_TO_MS, False)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = selected
  output = tick(runtime, sm, cp, 0)
  pending = physical_actions.pending_confirmation(ReplaySM({'slcState': output.message.slcState}, START), START, long_active=True)
  assert pending is None
  helper = VCruiseHelper(cp)
  helper.v_cruise_kph = selected
  button = 'accelCruise' if direction > 0 else 'decelCruise'
  cs = car.CarState(cruiseState={'available': True}, buttonEvents=[car.CarState.ButtonEvent(type=button, pressed=True)])
  helper.update_v_cruise(cs, True, False, pending, slc_enabled=True)
  assert helper.slc_consumed_button is None
  cs.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=False)]
  helper.update_v_cruise(cs, True, False, slc_enabled=True)
  assert helper.v_cruise_kph == pytest.approx(round(selected + direction * round(CV.MPH_TO_KPH, 1), 1))


@pytest.mark.parametrize('limit,offset,bounded', [(140., 10., 145.), (10., -5., 8.)])
@pytest.mark.parametrize('metric', [False, True])
def test_adoption_and_display_use_existing_cruise_bounds(drive, limit, offset, bounded, metric):
  sm, cp = drive
  sm['slcDashboardObservation'].speedMps = limit / 3.6
  settings = replace(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}),
                     offsets=OffsetSchedule((OffsetBand(0., None, offset / 3.6),)))
  output = tick(Runtime(settings, is_metric=metric), sm, cp, 0)
  assert not output.result.errors
  assert output.result.ceiling.speed_mps == pytest.approx(bounded / 3.6)
  assert output.message.slcState.acceptedAdjustedLimit == pytest.approx(bounded / 3.6)
  assert output.message.slcState.offset == pytest.approx((bounded-limit) / 3.6)
  assert sm['carState'].vCruise == 60.


def test_source_loss_preserves_latest_received_limit_and_offset_without_actions(drive):
  from openpilot.starpilot.ui.onroad_state import OnroadState, speed_limit_from_message, slc_controls
  from openpilot.starpilot.ui.presentation import Profile
  from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed
  sm, cp = drive
  settings = replace(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}),
                     offsets=OffsetSchedule((OffsetBand(0., None, 2 / 3.6),)))
  runtime = Runtime(settings)
  first = tick(runtime, sm, cp, 0).message.slcState
  tick(runtime, sm, cp, 1, request=Action(runtime.session_id, 1, first.decisionId, first.presentationId, 'accept'))
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 92.
  sm['slcDashboardObservation'].speedMps = 20.
  newer = tick(runtime, sm, cp, 2).message.slcState
  tick(runtime, sm, cp, 2.1, request=Action(runtime.session_id, 2, newer.decisionId, newer.presentationId, 'reject'))
  sm['slcDashboardObservation'].status = 'unknown'
  lost = tick(runtime, sm, cp, 2.2)
  observation = speed_limit_from_message(lost.message.slcState)
  screen = OnroadState(True, False, 20., 92., observation, metric=True)
  shown = resolve_unified_speed(screen)
  assert (shown.posted_text, shown.offset_text, shown.source, shown.status) == ('72', '+2', 'dashboard', 'Unavailable')
  assert not shown.pending
  for profile in Profile:
    assert not slc_controls(profile, replace(screen, slc_prompt_open=True))
  runtime.reset()
  reset = tick(runtime, sm, cp, 2.3)
  shown = resolve_unified_speed(replace(screen, speed_limit=speed_limit_from_message(reset.message.slcState)))
  assert (shown.posted_text, shown.offset_text, shown.status) == ('90', '+2', 'Unavailable')


@pytest.mark.parametrize('limit', [12., 25.])
@pytest.mark.parametrize('pcm', [False, True])
def test_automatic_adoption_updates_only_the_slc_cap(drive, limit, pcm):
  sm, cp = drive
  cp.pcmCruise = pcm
  sm['slcDashboardObservation'].speedMps = limit
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}))
  adopted = tick(runtime, sm, cp, 0)
  assert adopted.message.slcState.hasAccepted and not adopted.message.slcState.hasPending
  assert not adopted.message.slcState.automaticAdoptionPending
  assert adopted.result.ceiling.speed_mps == pytest.approx(limit)
  assert sm['carState'].vCruise == 60.
  assert tick(runtime, sm, cp, .05).result.ceiling.speed_mps == pytest.approx(limit)


@pytest.mark.parametrize('response', ['accept', 'reject', 'timeout'])
def test_confirmation_outcomes_preserve_set_speed(drive, response):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCConfirmation': 1, 'SLCPriority1': 'Dashboard'}))
  pending = tick(runtime, sm, cp, 0).message.slcState
  assert pending.hasPending
  action = Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, response) if response != 'timeout' else None
  output = tick(runtime, sm, cp, 30 if action is None else 1, request=action)
  assert not output.message.slcState.hasPending
  assert (output.result.ceiling is not None) == (response == 'accept')
  assert sm['carState'].vCruise == 60.
  assert not tick(runtime, sm, cp, 32).message.slcState.hasPending


def test_accelerator_with_override_off_keeps_set_speed_and_release_keeps_profile(drive):
  sm, cp = drive
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCOverrideMode': 0}))
  tick(runtime, sm, cp, 0)
  tick(runtime, sm, cp, 3)
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 90.
  sm['carState'].gasPressed = True
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 30.
  pressed = tick(runtime, sm, cp, 4)
  assert not pressed.message.slcState.driverOverrideActive
  assert sm['carState'].vCruise == 90.
  sm['carState'].gasPressed = False
  released = tick(runtime, sm, cp, 5)
  assert not released.message.slcState.driverOverrideActive and released.result.ceiling is not None
  native = planner.LongitudinalPlanner(cp)
  with patch.object(planner, 'get_cruise_accel', wraps=planner.get_cruise_accel) as cruise:
    native.update(sm, global_braking_response='eco', cruise_ceiling=released.result.ceiling)
  assert cruise.call_args.args[1] == 25.
  assert cruise.call_args.kwargs['base_brake_floor'] == -.6
  assert native.last_cruise_ceiling_status == 'above_driver'


def test_automatic_adoption_does_not_consume_confirmation_actions_and_late_manual_accept_is_declined():
  candidate = acc.Candidate('dashboard', acc.ObservationIdentity(acc.IdentityKind.SESSION_VALUE, session_id='drive'), 20.)
  observation = acc.Observation(acc.ObservationKind.VALID, candidate)
  authority = acc.Authority(acc.Mode.LONGITUDINAL_ONLY, acc.LongitudinalOwner.SYSTEM, False, True, False, False)
  for automatic in (False, True):
    policy = acc.Policy(not automatic, not automatic)
    first = acc.step(acc.new_session('drive'), observation, authority, policy, now_ns=0)
    action = acc.DriverAction('drive', 1, first.state.next_decision_id - 1, acc.ActionKind.ACCEPT)
    output = acc.step(first.state, observation, authority, policy, now_ns=1 if automatic else acc.CONFIRMATION_NS + 1, action=action)
    assert not output.action_receipt.consumed
    assert (output.state.accepted is not None) == automatic


@pytest.mark.parametrize('confirmation', [False, True])
def test_new_limit_can_be_adopted_while_accelerator_temporarily_suspends_longitudinal_output(drive, confirmation):
  sm, cp = drive
  sm['carState'].gasPressed = True
  sm['carControl'].longActive = False
  sm['controlsState'].longControlState = 'off'
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': int(confirmation)}))
  first = tick(runtime, sm, cp, 0).message.slcState
  action = Action(runtime.session_id, 1, first.decisionId, first.presentationId, 'accept') if confirmation else None
  output = tick(runtime, sm, cp, 1 if confirmation else 3, request=action)
  assert output.result.ceiling.speed_mps == 25.
  assert not output.message.slcState.driverOverrideActive
  assert not output.result.acceptance.authority.longitudinal_active
  assert output.result.acceptance.authority.accelerator_override
  assert output.result.ceiling is not None


def test_pcm_confirmation_release_is_retired_even_if_slc_is_switched_off(drive):
  from openpilot.selfdrive.car.cruise import SlcPendingConfirmation
  _, cp = drive
  cp.pcmCruise = True
  helper = VCruiseHelper(cp)
  cs = car.CarState(cruiseState={'available': True, 'enabled': True, 'speed': 20.},
                     buttonEvents=[car.CarState.ButtonEvent(type='accelCruise', pressed=True)])
  helper.update_v_cruise(cs, True, True, SlcPendingConfirmation('drive', 1, 1), slc_enabled=True)
  assert helper.slc_suppressed_buttons
  cs.buttonEvents = [car.CarState.ButtonEvent(type='accelCruise', pressed=False)]
  helper.update_v_cruise(cs, True, True, slc_enabled=False)
  assert not helper.slc_suppressed_buttons
  assert not any(helper.button_timers.values())
