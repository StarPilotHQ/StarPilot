"""Fresh software SET can match a qualified limit; RES preserves the previous SET."""
from collections import deque
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import math

import pytest

from opendbc.car.dashboard_speed_limit import Status, Tracker
from opendbc.car.structs import car
from openpilot.cereal import messaging
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.selfdrive.car import card, cruise
from openpilot.selfdrive.controls.plannerd import queue_cruise_event
from openpilot.starpilot.speed_limits.acceptance import CONFIRMATION_NS, AcceptedLimit, Candidate, IdentityKind, ObservationIdentity
from openpilot.starpilot.speed_limits import physical_actions
from openpilot.starpilot.speed_limits.planner_host import action_from_message
from openpilot.starpilot.speed_limits.runtime import Action, CruiseEvent, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule
from openpilot.starpilot.speed_limits.tests import test_runtime_replay as replay_fixture
from openpilot.starpilot.speed_limits.tests.test_maps_runtime import packet
from openpilot.starpilot.speed_limits.vision.observation import MODEL_ID


START = 2_000_000_000


def drive(*, metric=False, limit=45., offset=5., confirmation=True):
  fixture = replay_fixture.RuntimeReplayTests()
  fixture.setUp()
  sm, cp = fixture.sm, fixture.cp
  cp.pcmCruise = False
  sm['carState'].canValid = sm['carState'].cruiseState.available = True
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = cruise.V_CRUISE_UNSET
  sm['carControl'].enabled = sm['carControl'].longActive = False
  units = CV.KPH_TO_MS if metric else CV.MPH_TO_MS
  sm['slcDashboardObservation'].speedMps = limit * units
  settings = replace(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': int(confirmation)}),
                     offsets=OffsetSchedule((OffsetBand(0., None, offset * units),)))
  runtime = Runtime(settings, is_metric=metric)
  state = runtime.step(sm, cp, now_ns=START).message.slcState
  sm.data['slcState'] = state
  for flags in (sm.valid, sm.alive, sm.updated):
    flags['slcState'] = True
  sm.logMonoTime['slcState'] = START
  return runtime, sm, cp


def owner(sm, cp, *, metric=False, resume=None, match=True):
  instance = card.Car.__new__(card.Car)
  instance.CP, instance.sm = cp, sm
  instance.CC_prev = car.CarControl(enabled=False)
  instance.CS_prev = car.CarState(vEgo=sm['carState'].vEgo)
  instance.is_metric, instance.experimental_mode, instance.initial_cruise_speed = metric, False, 40
  instance.slc_control_enabled = instance.slc_replay = True
  instance.slc_match_on_engage = match
  instance.slc_source_floor_ns = 0
  instance.slc_producer_session, instance.slc_cruise_event_id = 'card-producer', 0
  instance.slc_receipts = []
  instance.v_cruise_helper = cruise.VCruiseHelper(cp)
  instance.vehicle_startup = Mock(consume_cruise_resume=lambda: resume)
  evidence = sm['slcDashboardObservation']
  observation = SimpleNamespace(status=Status.VALID, speed_mps=float(evidence.speedMps), episode=int(evidence.episode),
    observed_ns=int(evidence.observedMonoTime), valid_until_ns=int(evidence.validUntilMonoTime))
  instance.CI = Mock(CS=SimpleNamespace(dashboard_limit=SimpleNamespace(observation=observation)))
  return instance


def engage(instance, *, now=START + 10_000_000):
  sm = instance.sm
  sm.advance(now)
  sm['carControl'].enabled = sm['carControl'].longActive = True
  cs = car.CarState(canValid=sm['carState'].canValid, canTimeout=sm['carState'].canTimeout,
    vEgo=sm['carState'].vEgo, cruiseState={'available': True, 'enabled': True, 'speed': 60 / 3.6, 'speedCluster': 60 / 3.6})
  with patch.object(card.time, 'monotonic_ns', return_value=now), patch.object(cruise, 'monotonic_ns', return_value=now):
    instance.update_cruise_speed(cs)
  sm['carState'].vCruise = instance.v_cruise_helper.v_cruise_kph
  sm['carState'].vCruiseCluster = instance.v_cruise_helper.v_cruise_cluster_kph
  return cs


def receipt(instance, *, now=START + 10_000_000, event_id=1):
  kind, state, previous, selected, observed = instance.slc_receipts[-1]
  return CruiseEvent(event_id, observed, previous, selected, 'decel', instance.slc_producer_session, now,
                     kind, str(state.sessionId), int(state.decisionId), int(state.presentationId))


@pytest.mark.parametrize('raw,match', [(None, False), (b'0', False), (b'1', True), (b'yes', False)])
def test_engagement_matching_requires_an_enabled_saved_toggle(tmp_path, raw, match):
  _, sm, cp = drive()
  instance = owner(sm, cp)
  instance.params = Params(str(tmp_path))
  assert not instance.params.get_default_value('SLCOnEngage')
  if raw is not None:
    Path(instance.params.get_param_path('SLCOnEngage')).write_bytes(raw)
  instance.slc_match_on_engage = instance.read_slc_match_on_engage()
  assert instance.slc_match_on_engage == match
  engage(instance)
  assert instance.v_cruise_helper.v_cruise_kph == (80.5 if match else 72.)


@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('live', [False, True])
@pytest.mark.parametrize('metric', [False, True])
@pytest.mark.parametrize('v_ego,initial_speed,normal_kph', [(20., 40, 72.), (0., 40, 40.), (10., 100, 100.)])
def test_engagement_ignores_previous_drive_limit_and_preserves_resume(resume, live, metric, v_ego, initial_speed, normal_kph):
  runtime, sm, cp = drive(metric=metric)
  units = CV.KPH_TO_MS if metric else CV.MPH_TO_MS
  previous = AcceptedLimit(Candidate('map', ObservationIdentity(IdentityKind.GEOGRAPHIC, value='old-road'), 25 * units),
                           'previous-drive', 0)
  runtime = Runtime(runtime.settings, previous_limit=previous)
  sm['carState'].vEgo = sm['carState'].vEgoCluster = v_ego
  if not live:
    sm['slcDashboardObservation'].status = 'unknown'
  prepared = runtime.step(sm, cp, now_ns=START).message.slcState
  assert prepared.hasAcceptedAdjustedLimit and prepared.hasPresentationAdjustedLimit == live
  assert prepared.acceptedMonoTime == 0
  sm.data['slcState'] = prepared
  instance = owner(sm, cp, metric=metric, resume=resume)
  instance.initial_cruise_speed = initial_speed
  if resume:
    instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = 105.
  engage(instance)
  matched_kph = 50. if metric else 80.5
  assert instance.v_cruise_helper.v_cruise_kph == (105. if resume else matched_kph if live else normal_kph)
  assert bool(instance.slc_receipts) == (live and not resume)


@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('metric', [False, True])
def test_reengagement_keeps_this_session_accepted_limit_during_a_source_gap(resume, metric):
  runtime, sm, cp = drive(metric=metric)
  instance = owner(sm, cp, metric=metric, resume=resume)
  engage(instance)
  accepted = runtime.step(sm, cp, now_ns=START + 10_000_000, cruise_event=receipt(instance)).message.slcState
  assert accepted.acceptedMonoTime > 0

  sm['carControl'].enabled = sm['carControl'].longActive = False
  sm['slcDashboardObservation'].status = 'unknown'
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 105.
  instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = 105.
  gap_ns = START + 20_000_000
  sm.advance(gap_ns)
  gap = runtime.step(sm, cp, now_ns=gap_ns).message.slcState
  assert gap.observationKind != 'valid' and gap.acceptedMonoTime == accepted.acceptedMonoTime
  sm.data['slcState'] = gap
  instance.slc_receipts.clear()

  engage(instance, now=gap_ns + 10_000_000)

  assert instance.v_cruise_helper.v_cruise_kph == (105. if resume else 50. if metric else 80.5)
  assert not instance.slc_receipts


def test_new_dashboard_packet_in_same_episode_matches_set_over_saved_history():
  runtime, sm, cp = drive(limit=45., offset=0.)
  previous = AcceptedLimit(Candidate('dashboard', ObservationIdentity(IdentityKind.PRODUCER_EPISODE, value='old-road'), 30 * CV.MPH_TO_MS),
                           'previous-drive', 0)
  runtime = Runtime(runtime.settings, previous_limit=previous)
  state = runtime.step(sm, cp, now_ns=START).message.slcState
  sm.data['slcState'] = state
  assert state.hasPresentationAdjustedLimit and state.acceptedMonoTime == 0
  assert state.acceptedAdjustedLimit == pytest.approx(30 * CV.MPH_TO_MS)

  tracker = Tracker()
  tracker.update(state.sourceObservedMonoTime, Status.VALID, 45 * CV.MPH_TO_MS, valid_until_ns=state.sourceValidUntilMonoTime)
  newer = tracker.update(START + 5_000_000, Status.VALID, 45 * CV.MPH_TO_MS, valid_until_ns=START + 300_000_000)
  assert newer.episode == state.sourceEpisode and newer.observed_ns > state.sourceObservedMonoTime
  instance = owner(sm, cp, resume=False)
  instance.CI.CS.dashboard_limit.observation = newer

  engage(instance)

  assert instance.v_cruise_helper.v_cruise_kph == cruise.slc_target_kph(45 * CV.MPH_TO_MS, False)
  event = receipt(instance)
  assert event.presentation_id == state.presentationId
  evidence = sm['slcDashboardObservation']
  evidence.observedMonoTime, evidence.validUntilMonoTime = newer.observed_ns, newer.valid_until_ns
  adopted = runtime.step(sm, cp, now_ns=event.observed_ns, cruise_event=event).message.slcState
  assert adopted.actionStatus == 'adopt' and adopted.acceptedSpeedLimit == pytest.approx(45 * CV.MPH_TO_MS)


@pytest.mark.parametrize('confirmation', [False, True])
@pytest.mark.parametrize('pcm', [False, True])
@pytest.mark.parametrize('resume,previous', [(False, cruise.V_CRUISE_UNSET), (True, 30.), (None, 105.)],
                         ids=['set', 'resume-hint', 'resume-button'])
@pytest.mark.parametrize('metric,limit,offset', [(False, 45., 5.), (False, 20., -2.), (False, 55., -.4),
                                              (True, 72., 5.), (True, 30., -2.), (True, 72., -.4)])
def test_limit_matches_only_new_software_set_and_resume_preserves_ceiling(metric, limit, offset, confirmation, pcm, resume, previous):
  runtime, sm, cp = drive(metric=metric, limit=limit, offset=offset, confirmation=confirmation)
  cp.pcmCruise = pcm
  prepared = sm['slcState']
  assert prepared.observationKind == 'valid' and prepared.hasPresentationAdjustedLimit
  assert not sm['carControl'].enabled and prepared.presentationId > 0
  instance = owner(sm, cp, metric=metric, resume=resume)
  instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = previous
  if resume is None:
    instance.CS_prev.buttonEvents = [car.CarState.ButtonEvent(type='accelCruise', pressed=False)]
  engage(instance)
  matched = not pcm and resume is False
  expected = cruise.slc_target_kph(float(prepared.presentationAdjustedLimit), metric) if matched else 60. if pcm else previous
  assert instance.v_cruise_helper.v_cruise_kph == pytest.approx(expected)
  assert instance.v_cruise_helper.v_cruise_cluster_kph == pytest.approx(expected)
  adopted = runtime.step(sm, cp, now_ns=START + 10_000_000, cruise_event=receipt(instance) if matched else None)
  if not matched:
    assert not instance.slc_receipts
    return
  assert not adopted.result.errors and adopted.message.slcState.hasAccepted
  assert not adopted.message.slcState.hasPending and not adopted.message.slcState.driverOverrideActive
  assert adopted.message.slcState.actionStatus == 'adopt'
  assert runtime.ledger.transactions == ()
  instance.CC_prev.enabled = True
  instance.slc_receipts.clear()
  engage(instance, now=START + 20_000_000)
  assert instance.v_cruise_helper.v_cruise_kph == pytest.approx(expected) and instance.slc_receipts == []


@pytest.mark.parametrize('invalid', ['disabled', 'stale', 'unknown', 'stock-long', 'can-fault', 'wrong-episode', 'configuration'])
@pytest.mark.parametrize('resume', [False, True])
def test_unavailable_limit_keeps_normal_initialization(invalid, resume):
  _, sm, cp = drive()
  instance = owner(sm, cp, resume=resume)
  if resume:
    instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = 105.
  if invalid == 'disabled':
    instance.slc_control_enabled = False
  elif invalid == 'stale':
    sm['slcState'].frameMonoTime = START - physical_actions.STATE_MAX_AGE_NS
  elif invalid == 'unknown':
    sm['slcState'].observationKind = 'unknown'
  elif invalid == 'stock-long':
    cp.openpilotLongitudinalControl = False
  elif invalid == 'can-fault':
    sm['carState'].canTimeout = True
  elif invalid == 'wrong-episode':
    instance.CI.CS.dashboard_limit.observation.episode += 1
  else:
    instance.slc_source_floor_ns = START
  engage(instance)
  assert instance.v_cruise_helper.v_cruise_kph == (105. if resume else 72.)
  assert instance.slc_receipts == []


def test_first_engagement_without_limit_keeps_default_floor():
  _, sm, cp = drive()
  sm['slcState'].observationKind = 'unknown'
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 0.
  instance = owner(sm, cp)
  engage(instance)
  assert instance.v_cruise_helper.v_cruise_kph == cruise.V_CRUISE_INITIAL
  assert instance.slc_receipts == []


@pytest.mark.parametrize('resume', [True, None])
def test_resume_restores_previous_set_without_available_limit(resume):
  _, sm, cp = drive()
  sm['slcState'].observationKind = 'unknown'
  instance = owner(sm, cp, resume=resume)
  instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = 105.
  if resume is None:
    instance.CS_prev.buttonEvents = [car.CarState.ButtonEvent(type='accelCruise', pressed=False)]
  engage(instance)
  assert instance.v_cruise_helper.v_cruise_kph == 105.
  assert instance.slc_receipts == []


def test_card_publication_routes_engagement_to_runtime_and_preserves_later_confirmation():
  runtime, sm, cp = drive()
  instance = owner(sm, cp)
  cs = engage(instance)
  instance.can_log_mono_time = START + 10_000_000
  instance.car_params_published, instance.aol_replay, instance.curve_replay = True, False, False
  instance.last_actuators_output = car.CarControl.Actuators()
  instance.can_rcv_cum_timeout_counter = 0
  instance.rk = SimpleNamespace(remaining=0.)
  instance.pm, instance.car_gps_publisher = Mock(), Mock()
  sm.frame, sm.all_checks = 1, lambda _: True
  cs.vCruise, cs.vCruiseCluster = instance.v_cruise_helper.v_cruise_kph, instance.v_cruise_helper.v_cruise_cluster_kph
  with patch.object(card, 'REPLAY', True):
    instance.state_publish(cs, None)
  published = next(call.args[1] for call in instance.pm.send.call_args_list if call.args[0] == 'slcCruiseEvent')
  events = deque()
  queue_cruise_event(published.as_reader(), events, deque())
  assert len(events) == 1 and events[0].kind == 'engagementAdopt'
  assert events[0].command_id == 0
  first = runtime.step(sm, cp, now_ns=START + 10_000_000, cruise_event=events.popleft())
  assert first.message.slcState.hasAccepted
  assert first.message.slcState.wheelAdoptMonoTime == 0
  sm.advance(START + 50_000_000)
  sm['slcDashboardObservation'].speedMps = 55 * CV.MPH_TO_MS
  later = runtime.step(sm, cp, now_ns=START + 50_000_000)
  assert later.message.slcState.hasPending


@pytest.mark.parametrize('change', ['session', 'presentation', 'producer', 'replaced-source', 'changed-set', 'stale', 'replay'])
def test_engagement_receipt_cannot_adopt_unrelated_or_stale_work(change):
  runtime, sm, cp = drive()
  instance = owner(sm, cp)
  engage(instance)
  event = receipt(instance)
  if change == 'session':
    event = replace(event, session_id='old-drive')
  elif change == 'presentation':
    event = replace(event, presentation_id=event.presentation_id + 1)
  elif change == 'producer':
    event = replace(event, producer_session='old-card')
  elif change == 'replaced-source':
    sm['slcDashboardObservation'].episode += 1
    sm['slcDashboardObservation'].observedMonoTime = START + 5_000_000
  elif change == 'changed-set':
    sm['carState'].vCruise += 5.
  elif change == 'stale':
    event = replace(event, observed_ns=START - 100_000_000)
  else:
    runtime.step(sm, cp, now_ns=START + 10_000_000, cruise_event=event)
  output = runtime.step(sm, cp, now_ns=START + 10_000_000, cruise_event=event)
  assert output.message.slcState.actionStatus != 'adopt'
  assert runtime.ledger.transactions == ()


@pytest.mark.parametrize('target', [None, True, 0., -1., math.nan, math.inf, 146 / 3.6])
def test_initializer_rejects_invalid_targets(target):
  helper = cruise.VCruiseHelper(car.CarParams(pcmCruise=False, openpilotLongitudinalControl=True))
  assert not helper.initialize_v_cruise(car.CarState(), False, resume=False, slc_target_mps=target)
  assert helper.v_cruise_kph == cruise.V_CRUISE_INITIAL


@pytest.mark.parametrize('resume', [False, True])
def test_reengagement_clears_previous_decline(resume):
  runtime, sm, cp = drive()
  sm['carControl'].enabled = sm['carControl'].longActive = True
  sm.advance(START + 1_000_000)
  pending = runtime.step(sm, cp, now_ns=START + 1_000_000).message.slcState
  sm.advance(START + 2_000_000)
  rejected = runtime.step(sm, cp, now_ns=START + 2_000_000,
    request=Action(runtime.session_id, 1, pending.decisionId, pending.presentationId, 'reject'))
  assert not rejected.message.slcState.hasPending and not rejected.message.slcState.hasAccepted
  sm.data['slcState'] = rejected.message.slcState
  instance = owner(sm, cp, resume=resume)
  if resume:
    instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = 105.
  engage(instance)
  if resume:
    assert instance.v_cruise_helper.v_cruise_kph == 105. and not instance.slc_receipts
    assert runtime.state.acceptance.rejected
    return
  adopted = runtime.step(sm, cp, now_ns=START + 10_000_000, cruise_event=receipt(instance))
  assert adopted.message.slcState.actionStatus == 'adopt' and adopted.message.slcState.hasAccepted
  assert not adopted.message.slcState.hasPending


@pytest.mark.parametrize('accepted_mph,posted_mph,response,variant,expected_set_mph,adopts', [
  pytest.param(75, 30, 'timeout', 'set', 30, True, id='expired-lower'),
  pytest.param(75, 30, 'reject', 'set', 30, True, id='declined-lower'),
  pytest.param(75, 30, 'reject', 'resume', 68, False, id='resume-after-decline'),
  pytest.param(30, 55, 'reject', 'set', 30, False, id='declined-higher'),
  pytest.param(45, 40, 'reject', 'offsets', 39, False, id='lower-sign-higher-target'),
  pytest.param(40, 45, 'reject', 'offsets', 46, False, id='higher-sign-lower-target'),
  pytest.param(47, 35, 'reject', 'offsets', 41, False, id='equal-adjusted-target'),
  pytest.param(75, 30, 'timeout', 'disagreement', None, False, id='dashboard-disagreement'),
])
def test_reengagement_matches_a_lower_current_limit(accepted_mph, posted_mph, response, variant, expected_set_mph, adopts):
  runtime, sm, cp = drive(limit=accepted_mph, offset=0.)
  runtime.settings = replace(runtime.settings, acceptance=replace(runtime.settings.acceptance, confirm_higher=posted_mph > accepted_mph))
  initial = owner(sm, cp)
  engage(initial)
  accepted = runtime.step(sm, cp, now_ns=START + 10_000_000, cruise_event=receipt(initial)).message.slcState
  assert accepted.acceptedMonoTime > 0 and accepted.acceptedSpeedLimit == pytest.approx(accepted_mph * CV.MPH_TO_MS)
  if variant == 'offsets':
    boundary = 42.5 * CV.MPH_TO_MS
    runtime.update_offsets(OffsetSchedule((OffsetBand(0., boundary, 6 * CV.MPH_TO_MS),
                                           OffsetBand(boundary, None, -6 * CV.MPH_TO_MS))))

  dashboard = sm['slcDashboardObservation']
  dashboard.speedMps = posted_mph * CV.MPH_TO_MS
  dashboard.episode += 1
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 35 * CV.MPH_TO_MS
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = cruise.slc_target_kph(68 * CV.MPH_TO_MS, False)

  def tick(now_ns, *, request=None):
    sm.advance(now_ns)
    dashboard.observedMonoTime = now_ns - 1_000_000
    dashboard.validUntilMonoTime = now_ns + 500_000_000
    return runtime.step(sm, cp, now_ns=now_ns, request=request).message.slcState

  now_ns = START + 50_000_000
  pending = tick(now_ns)
  assert pending.hasPending and pending.pendingSpeedLimit == pytest.approx(posted_mph * CV.MPH_TO_MS)
  now_ns += 50_000_000
  if response == 'reject':
    rejected = tick(now_ns, request=Action(runtime.session_id, 2, pending.decisionId, pending.presentationId, 'reject'))
    assert rejected.status == 'driver_reject'
  else:
    now_ns += CONFIRMATION_NS
    assert tick(now_ns).status == 'timeout_reject'

  sm['carControl'].enabled = sm['carControl'].longActive = False
  if variant == 'disagreement':
    dashboard.speedMps = accepted_mph * CV.MPH_TO_MS
    dashboard.episode += 1
  now_ns += 50_000_000
  sm.data['slcState'] = tick(now_ns)
  instance = owner(sm, cp)
  instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = sm['carState'].vCruise
  instance.CS_prev.buttonEvents = [car.CarState.ButtonEvent(type='accelCruise' if variant == 'resume' else 'decelCruise', pressed=False)]
  if variant == 'disagreement':
    local = instance.CI.CS.dashboard_limit.observation
    local.speed_mps = posted_mph * CV.MPH_TO_MS
    local.episode += 1
    local.observed_ns = now_ns + 5_000_000

  now_ns += 10_000_000
  engage(instance, now=now_ns)
  if expected_set_mph is None:
    unpaired_kph = cruise.slc_target_kph(posted_mph * CV.MPH_TO_MS, False)
    assert sm['carState'].vCruise != pytest.approx(unpaired_kph)
  else:
    expected_kph = cruise.slc_target_kph(expected_set_mph * CV.MPH_TO_MS, False)
    assert (sm['carState'].vCruise, sm['carState'].vCruiseCluster) == pytest.approx((expected_kph, expected_kph))
  assert len(instance.slc_receipts) == int(adopts)
  event = receipt(instance, now=now_ns, event_id=2) if adopts else None
  settled = runtime.step(sm, cp, now_ns=now_ns, cruise_event=event)
  assert not settled.result.errors
  expected_limit_mph = posted_mph if adopts else accepted_mph
  assert settled.message.slcState.acceptedSpeedLimit == pytest.approx(expected_limit_mph * CV.MPH_TO_MS)
  if adopts:
    assert settled.message.slcState.status == 'driver_adopt'
    assert not settled.message.slcState.hasPending
    assert runtime.state.acceptance.rejected is None


@pytest.mark.parametrize('source', ['Maps', 'Vision'])
@pytest.mark.parametrize('dashboard_available', [True, False])
@pytest.mark.parametrize('resume', [False, True])
def test_engagement_uses_existing_priority_and_qualified_secondary_source(source, dashboard_available, resume):
  runtime, sm, cp = drive()
  boot = START + 4_000_000_000
  settings = replace(runtime.settings, selection=parse({'SLCPriority1': 'Dashboard', 'SLCPriority2': source}).selection)
  runtime = Runtime(settings, vision_enabled=True, vision_control_qualified=True)
  sm['slcDashboardObservation'].status = 'valid' if dashboard_available else 'unknown'
  if source == 'Maps':
    road = packet(speedLimit=30., gpsMonoTime=boot - 50_000_000, computedMonoTime=boot)
    sm.data['mapdOut'] = road.mapdOut
    service = 'mapdOut'
  else:
    vision = messaging.new_message('slcVisionObservation').slcVisionObservation
    vision.vision = {'status': 'valid', 'modelId': MODEL_ID, 'stream': 'road', 'speedMps': 30., 'confidence': .9,
      'supportCount': 2, 'episode': 1, 'producerSessionId': 'vision', 'frameId': 1,
      'observedMonoTime': START - 50_000_000, 'validUntilMonoTime': START + 200_000_000, 'cameraFrameEofBootTime': boot - 50_000_000}
    sm.data['slcVisionObservation'] = vision
    service = 'slcVisionObservation'
  for flags in (sm.valid, sm.alive, sm.updated):
    flags[service] = True
  sm.logMonoTime[service] = boot if source == 'Maps' else START
  prepared = runtime.step(sm, cp, now_ns=START, now_boot_ns=boot).message.slcState
  expected_source = 'dashboard' if dashboard_available else ('map' if source == 'Maps' else 'vision')
  assert prepared.source == expected_source and prepared.hasPresentationAdjustedLimit
  sm.data['slcState'] = prepared
  instance = owner(sm, cp, resume=resume)
  if resume:
    instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = 105.
  engage(instance)
  expected = 105. if resume else cruise.slc_target_kph(float(prepared.presentationAdjustedLimit), False)
  assert instance.v_cruise_helper.v_cruise_kph == pytest.approx(expected)
  if resume:
    assert not instance.slc_receipts
    return
  # ReplaySM.advance uses monotonic stamps for every service; mapd uses boottime.
  if source == 'Maps':
    sm.logMonoTime['mapdOut'] = boot
  adopted = runtime.step(sm, cp, now_ns=START + 10_000_000, now_boot_ns=boot + 10_000_000, cruise_event=receipt(instance))
  assert adopted.message.slcState.acceptedSource == expected_source and not adopted.message.slcState.hasPending


@pytest.mark.parametrize('confirmation', [False, True])
@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('live', [False, True])
def test_engagement_never_initializes_set_from_a_preview(confirmation, resume, live):
  from openpilot.starpilot.speed_limits.tests.test_advance import Drive
  preview = Drive(confirm=confirmation)
  output = preview.tick()
  assert output.message.slcState.upcomingBoundaryId
  preview.sm['carControl'].enabled = preview.sm['carControl'].longActive = False
  prepared = preview.tick(alive=live).message.slcState
  assert prepared.acceptedIsPreview == (not confirmation)
  sm, cp = preview.sm, preview.cp
  cp.pcmCruise = False
  sm.data['slcState'] = prepared
  sm.valid['slcState'] = sm.alive['slcState'] = True
  sm.logMonoTime['slcState'] = preview.now
  instance = owner(sm, cp, metric=True, resume=resume)
  if resume:
    instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = 105.
  stamp = preview.now + 10_000_000
  engage(instance, now=stamp)
  if resume:
    assert instance.v_cruise_helper.v_cruise_kph == 105. and not instance.slc_receipts
    return
  if not live:
    assert instance.v_cruise_helper.v_cruise_kph == (79. if confirmation else 72.)
    assert not instance.slc_receipts
    return
  assert instance.v_cruise_helper.v_cruise_kph == 79.
  adopted = preview.tick(at=stamp, event=receipt(instance, now=stamp))
  assert adopted.message.slcState.acceptedSpeedLimit == 20.
  assert not adopted.message.slcState.hasPending and not adopted.message.slcState.upcomingBoundaryId
  assert adopted.message.slcState.actionStatus == 'adopt'

  turned = preview.tick(way=3, target=0.).message.slcState
  assert not turned.acceptedIsPreview and turned.acceptedSpeedLimit == 20.
  assert sm['carState'].vCruise == 79.


@pytest.mark.parametrize('current', [20., 25.])
@pytest.mark.parametrize('resume', [False, True])
def test_delayed_engagement_is_bound_to_current_limit(current, resume):
  from openpilot.starpilot.speed_limits.tests.test_advance import Drive
  preview = Drive(confirm=True)
  assert preview.tick().message.slcState.upcomingBoundaryId
  preview.sm['carControl'].enabled = preview.sm['carControl'].longActive = False
  prepared = preview.tick().message.slcState
  sm, cp = preview.sm, preview.cp
  cp.pcmCruise = False
  sm.data['slcState'] = prepared
  sm.valid['slcState'] = sm.alive['slcState'] = True
  sm.logMonoTime['slcState'] = preview.now
  instance = owner(sm, cp, metric=True, resume=resume)
  if resume:
    instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = 105.
  stamp = preview.now + 10_000_000
  engage(instance, now=stamp)
  if resume:
    assert instance.v_cruise_helper.v_cruise_kph == 105. and not instance.slc_receipts
    return
  event = receipt(instance, now=stamp)
  pending = preview.tick(at=stamp + 40_000_000, current=current).message.slcState
  delayed = preview.tick(at=stamp + 90_000_000, current=current, event=event)
  state = delayed.message.slcState
  assert not delayed.result.errors
  assert sm['carState'].vCruise == 79. and state.acceptedSpeedLimit == 20.
  assert (state.actionStatus == 'adopt') == (current == 20.)
  assert state.hasPending == (current != 20.)
  if current != 20.:
    assert state.presentationId != event.presentation_id
    assert state.decisionId == pending.decisionId
    assert state.pendingSinceMonoTime == pending.pendingSinceMonoTime


def wheel_approval(runtime, sm, cp, *, wheel_ns, now):
  request = messaging.new_message('slcAction', logMonoTime=now)
  request.slcAction = {'sessionId': runtime.session_id, 'sequenceId': now, 'kind': 'adopt',
                       'presentationId': sm['slcState'].presentationId, 'wheelAdoptMonoTime': wheel_ns}
  sm.advance(now)
  sm['slcDashboardObservation'].validUntilMonoTime = now + 1_000_000_000
  state = runtime.step(sm, cp, now_ns=now, request=action_from_message(request)[1]).message.slcState
  assert state.actionStatus == 'adopt' and state.wheelAdoptMonoTime == wheel_ns
  return state


@pytest.mark.parametrize('case', ['valid', 'disengaged', 'newer-set', 'held-set-button', 'expired', 'different-press', 'no-local-press'])
def test_wheel_adopt_only_applies_to_its_own_eligible_press(case):
  runtime, sm, cp = drive(limit=45., offset=5., confirmation=True)
  instance = owner(sm, cp, match=False)
  engage(instance)
  helper = instance.v_cruise_helper
  wheel_ns = START + 20_000_000
  if case != 'no-local-press':
    instance.slc_wheel_adopt = (wheel_ns + int(case == 'different-press'), helper.v_cruise_kph)
  now = wheel_ns + (240_000_000 if case == 'expired' else 30_000_000)
  sm.data['slcState'] = wheel_approval(runtime, sm, cp, wheel_ns=wheel_ns, now=now)
  if case == 'newer-set':
    helper.v_cruise_kph -= 1.6
  elif case == 'held-set-button':
    sm['carState'].buttonEvents = [car.CarState.ButtonEvent(type='decelCruise', pressed=True)]
    helper.update_v_cruise(sm['carState'], True, False)
  before = helper.v_cruise_kph
  read_ns = wheel_ns + 250_000_001 if case == 'expired' else now
  instance.apply_slc_adopt(read_ns, host_control_enabled=case != 'disengaged')
  assert helper.v_cruise_kph == (cruise.slc_target_kph(50 * CV.MPH_TO_MS, False) if case == 'valid' else before)
  if case == 'valid':
    assert instance.slc_wheel_adopt is None
    helper.v_cruise_kph = before
    instance.apply_slc_adopt(read_ns, host_control_enabled=True)
    assert helper.v_cruise_kph == before


def test_next_automatic_limit_cannot_reuse_wheel_approval():
  runtime, sm, cp = drive(limit=45., offset=5., confirmation=False)
  instance = owner(sm, cp, match=False)
  engage(instance)
  before = instance.v_cruise_helper.v_cruise_kph
  wheel_ns = START + 20_000_000
  instance.slc_wheel_adopt = (wheel_ns, before)
  approved = wheel_approval(runtime, sm, cp, wheel_ns=wheel_ns, now=START + 50_000_000)
  assert approved.wheelAdoptTargetMps == pytest.approx(50 * CV.MPH_TO_MS)

  now = START + 100_000_000
  sm.advance(now)
  sm['slcDashboardObservation'].speedMps = 50 * CV.MPH_TO_MS
  sm['slcDashboardObservation'].episode += 1
  state = runtime.step(sm, cp, now_ns=now).message.slcState
  assert state.actionSequenceId == approved.actionSequenceId and state.actionStatus == 'adopt'
  assert state.acceptedAdjustedLimit == pytest.approx(55 * CV.MPH_TO_MS)
  assert state.wheelAdoptMonoTime == 0
  sm.data['slcState'] = state
  instance.apply_slc_adopt(now, host_control_enabled=True)
  assert instance.v_cruise_helper.v_cruise_kph == before


@pytest.mark.parametrize('proof', ['qualified', 'expired', 'unacknowledged', 'lateral-only', 'missing'])
def test_stopped_set_matches_limit_before_longitudinal_activates(proof):
  _, sm, cp = drive(limit=40., offset=0., confirmation=False)
  assert sm['slcState'].hasAccepted
  now = START + 10_000_000
  if proof != 'missing':
    sm.data['aolAxisState'] = messaging.new_message('aolAxisState').aolAxisState
    axis = sm['aolAxisState']
    axis.sessionId, axis.qualified = 'axis', True
    axis.nativeAcknowledged = proof != 'unacknowledged'
    axis.desiredLongitudinal = proof != 'lateral-only'
    axis.observedMonoTime = START
    axis.validUntilMonoTime = now - 1 if proof == 'expired' else now + 10_000_000
    sm.valid['aolAxisState'] = sm.alive['aolAxisState'] = True
    sm.seen = {'aolAxisState': True}
  sm.advance(now)
  if proof != 'missing':
    sm.logMonoTime['aolAxisState'] = START
  sm['carControl'].enabled = True
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 0.
  sm['carState'].standstill = sm['carState'].brakePressed = True
  instance = owner(sm, cp, resume=False)
  instance.v_cruise_helper.v_cruise_kph = instance.v_cruise_helper.v_cruise_cluster_kph = 64.4
  with patch.object(card.time, 'monotonic_ns', return_value=now):
    instance.update_cruise_speed(sm['carState'])
  expected = 64.4 if proof == 'qualified' else cruise.V_CRUISE_INITIAL
  assert instance.v_cruise_helper.v_cruise_kph == expected
  assert bool(instance.slc_receipts) == (proof == 'qualified')
