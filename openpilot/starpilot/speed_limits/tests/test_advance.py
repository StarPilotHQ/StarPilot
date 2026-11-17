"""Mapped previews through the actual runtime, confirmations and Card guards."""
from collections import deque
from dataclasses import replace
from pathlib import Path
import base64
import json

import pytest

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.starpilot.speed_limits import acceptance as acc, physical_actions
from openpilot.starpilot.speed_limits.map_source import decode_event
from openpilot.starpilot.speed_limits.map_observation import MapSource, copy_frame
from openpilot.starpilot.speed_limits.runtime import Action, CruiseEvent, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import ADVANCE_KEYS, parse, read_params
from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule
from openpilot.starpilot.speed_limits.tests.test_maps_runtime import packet
from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM
from openpilot.starpilot.speed_limits.vision.observation import MODEL_ID
from openpilot.starpilot.ui.onroad_state import speed_limit_from_message
from openpilot.starpilot.ui.tests.test_slc_ui_runtime import START, replay_inputs
from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed
from opendbc.car.structs import car


BOUNDARY = 'a' * 64
STEP = 50_000_000


class Drive:
  def __init__(self, *, confirm=False, higher=5, lower=5):
    self.sm, self.cp = replay_inputs()
    self.now = START
    self.sm['carState'].canValid = True
    self.sm['carState'].vCruise = self.sm['carState'].vCruiseCluster = 79.2
    settings = replace(parse({'SpeedLimitController': True, 'SLCPriority1': 'Maps',
      'SLCPriority2': 'Dashboard', 'SLCAdvanceHigherSeconds': higher, 'SLCAdvanceLowerSeconds': lower}),
      offsets=OffsetSchedule((OffsetBand(0., None, 2.),)))
    self.runtime = Runtime(settings, session_id='drive', is_metric=True)
    self.tick(target=0.)
    assert self.runtime.state.acceptance.accepted.candidate.speed_mps == 20.
    self.runtime.settings = replace(settings, acceptance=acc.Policy(confirm_lower=confirm, confirm_higher=confirm))

  def tick(self, *, current=20., target=15., distance=60., way=1, target_way=2, speed=20., stopped=False,
           alive=True, producer=41, boundary=BOUNDARY, action=None, event=None, at=None, dashboard=None, vision_speed=None,
           road_status='matchedLimit'):
    self.now = self.now + STEP if at is None else at
    self.sm.advance(self.now)
    boot = self.now + 4_000_000_000
    road = packet(roadStatus=road_status, speedLimit=current, previewSpeedLimit=target, previewDistance=distance,
      previewBoundaryId=boundary if target else '', previewWayId=target_way, wayId=way, producerSession=producer,
      gpsMonoTime=boot-50_000_000, computedMonoTime=boot)
    road.logMonoTime = boot
    self.sm.data['mapdOut'] = road.mapdOut
    for values in (self.sm.valid, self.sm.alive, self.sm.updated):
      values['mapdOut'] = alive
    self.sm.logMonoTime['mapdOut'] = boot
    self.sm['carState'].vEgo = self.sm['carState'].vEgoCluster = speed
    self.sm['carState'].standstill = stopped
    source = self.sm['slcDashboardObservation']
    source.status, source.speedMps = ('unknown', 0.) if dashboard is None else ('valid', dashboard)
    source.observedMonoTime, source.validUntilMonoTime = self.now, self.now+1_000_000_000
    if vision_speed is not None or 'slcVisionObservation' in self.sm.data:
      camera = messaging.new_message('slcVisionObservation').slcVisionObservation
      vision = camera.init('vision')
      vision.producerSessionId, vision.status, vision.stream, vision.modelId = 'camera', 'valid', 'road', MODEL_ID
      vision.speedMps, vision.confidence, vision.supportCount = vision_speed or 0., .9, 2
      vision.episode, vision.frameId = 1, self.now // STEP
      vision.observedMonoTime, vision.validUntilMonoTime, vision.cameraFrameEofBootTime = self.now, self.now + STEP, boot
      self.sm.data['slcVisionObservation'] = camera
      self.sm.logMonoTime['slcVisionObservation'] = self.now
      for values in (self.sm.valid, self.sm.alive, self.sm.updated):
        values['slcVisionObservation'] = vision_speed is not None
    return self.runtime.step(self.sm, self.cp, now_ns=self.now, now_boot_ns=boot, request=action, cruise_event=event)

  def action(self, state, kind):
    return Action(self.runtime.session_id, self.now+STEP, state.decisionId, state.presentationId, kind)

  def apply(self, output):
    from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
    assert output.result.ceiling is not None
    selected = self.sm['carState'].vCruise
    LongitudinalPlanner(self.cp).update(self.sm, cruise_ceiling=output.result.ceiling)
    assert self.sm['carState'].vCruise == selected
    return self.tick()


@pytest.mark.parametrize('target', [15., 25.])
def test_threshold_uses_physical_speed_for_both_directions(target):
  drive = Drive()
  outside = drive.tick(target=target, distance=100.01)
  assert not outside.message.slcState.upcomingBoundaryId
  inside = drive.tick(target=target, distance=100.)
  state = inside.message.slcState
  assert state.speedLimit == 20. and state.upcomingSpeedLimit == target
  assert state.acceptedSpeedLimit == target and state.acceptedAdjustedLimit == target+2.
  assert inside.result.ceiling.speed_mps == target+2.


@pytest.mark.parametrize('target,higher,lower,threshold', [
  (15., 1, 7, 140.), (25., 1, 7, 20.), (15., 0, 7, 140.), (25., 7, 0, 140.),
])
def test_direction_selects_its_own_time_before_offsets(target, higher, lower, threshold):
  drive = Drive(higher=higher, lower=lower)
  # Reversed offset targets must not change the posted-limit direction.
  drive.runtime.update_offsets(OffsetSchedule((OffsetBand(0., 18., 10.), OffsetBand(18., None, -2.))))
  outside = drive.tick(target=target, distance=threshold+.01)
  assert not outside.message.slcState.upcomingBoundaryId
  inside = drive.tick(target=target, distance=threshold)
  assert inside.message.slcState.upcomingSpeedLimit == target
  assert inside.message.slcState.acceptedSpeedLimit == target


@pytest.mark.parametrize('target,higher,lower', [(15., 5, 0), (25., 0, 5)])
def test_each_direction_can_be_off_without_disabling_current_limit_adoption(target, higher, lower):
  drive = Drive(higher=higher, lower=lower)
  before = drive.tick(target=target, distance=1.).message.slcState
  assert not before.upcomingBoundaryId and before.acceptedSpeedLimit == 20.
  crossed = drive.tick(current=target, target=0., way=2).message.slcState
  assert crossed.acceptedSpeedLimit == target


@pytest.mark.parametrize('speed,stopped', [(0., True), (0., False), (20., True)])
def test_no_first_offer_at_standstill(speed, stopped):
  drive = Drive()
  output = drive.tick(speed=speed, stopped=stopped, distance=1.)
  assert not output.message.slcState.upcomingBoundaryId


def test_off_preserves_current_limit_behavior():
  drive = Drive(higher=0, lower=0)
  assert drive.tick().message.slcState.acceptedSpeedLimit == 20.
  output = drive.tick(current=15., target=0., way=2)
  assert output.message.slcState.acceptedSpeedLimit == 15.
  assert output.result.ceiling is not None


@pytest.mark.parametrize('primary,dashboard,target,early', [
  ('Dashboard', 25., 15., False), ('Highest', 25., 15., False), ('Lowest', 10., 15., False),
  ('Highest', 25., 25., False), ('Highest', 22., 25., True), ('Lowest', 22., 15., True),
])
def test_preview_respects_existing_source_selection(primary, dashboard, target, early):
  drive = Drive()
  drive.runtime.settings = replace(drive.runtime.settings,
    selection=parse({'SLCPriority1': primary, 'SLCPriority2': 'Maps'}).selection)
  output = drive.tick(dashboard=dashboard, target=target)
  assert bool(output.message.slcState.upcomingBoundaryId) == early
  if early:
    assert output.message.slcState.acceptedSource == 'map'
    assert output.result.ceiling.speed_mps == target + 2.


def vision_only_settings():
  return parse({'SpeedLimitController': True, 'SLCPriority1': 'Vision', 'SLCPriority2': 'Off',
                'SLCPriority3': 'Off', 'SLCAdvanceHigherSeconds': 5, 'SLCAdvanceLowerSeconds': 5})


@pytest.mark.parametrize('maps_alive', [False, True])
def test_vision_only_ignores_map_readings_previews_and_road_changes(maps_alive):
  drive = Drive()
  drive.runtime = Runtime(vision_only_settings(), vision_enabled=True, vision_control_qualified=True)
  for vision_speed, expected, source, road in ((16., 16., 'vision', {}),
                                                (None, 16., 'none', {'way': 3, 'target': 25.}),
                                                (18., 18., 'vision', {'way': 4, 'target': 0.})):
    output = drive.tick(vision_speed=vision_speed, alive=maps_alive, **road)
    state = output.message.slcState
    assert state.source == source and state.acceptedSource == 'vision'
    assert state.effectiveCap == expected
    assert not state.upcomingBoundaryId and drive.runtime.advance.encounter is None


@pytest.mark.parametrize('vision_at_switch', [False, True])
def test_removing_maps_retires_the_preview_and_resumes_vision_decisions(vision_at_switch):
  drive = Drive()
  early = drive.tick(target=25.).message.slcState
  assert early.upcomingBoundaryId and early.acceptedSpeedLimit == 25.
  settings = vision_only_settings()
  drive.runtime.update_offsets(settings.offsets)
  drive.runtime.settings = replace(settings, acceptance=acc.Policy())
  drive.runtime.vision_enabled = drive.runtime.vision_control_qualified = True
  output = drive.tick(vision_speed=20. if vision_at_switch else None, way=3, target=0.)
  state = output.message.slcState
  assert drive.runtime.advance.encounter is None and not state.upcomingBoundaryId
  assert not state.hasPending and output.result.ceiling.speed_mps == 20.
  output = drive.tick(vision_speed=20., way=3, target=0.)
  assert output.message.slcState.source == 'vision' and not output.message.slcState.hasPending
  assert output.result.ceiling.speed_mps == 20.


def test_reverse_and_inactive_control_do_not_start_advance():
  for reason in ('reverse', 'inactive', 'stale_car'):
    drive = Drive()
    if reason == 'reverse':
      drive.sm['carState'].gearShifter = 'reverse'
    elif reason == 'inactive':
      drive.sm['carControl'].longActive = False
    else:
      drive.sm['carState'].canTimeout = True
    assert not drive.tick(distance=1.).message.slcState.upcomingBoundaryId


def test_confirmation_latches_on_slowdown_and_stop_then_times_out_once():
  drive = Drive(confirm=True)
  first = drive.tick().message.slcState
  start = drive.runtime.advance.encounter.started_ns
  assert start is not None
  slower = drive.tick(speed=1., distance=59.).message.slcState
  stopped = drive.tick(speed=0., stopped=True, distance=59.).message.slcState
  assert first.decisionId == slower.decisionId == stopped.decisionId
  expired = drive.tick(at=start+acc.CONFIRMATION_NS, speed=0., stopped=True).message.slcState
  assert expired.actionStatus in ('', 'timeout_reject') and not expired.hasPending
  crossed = drive.tick(current=15., target=0., way=2)
  assert not crossed.message.slcState.hasPending


@pytest.mark.parametrize('kind', ['accept', 'reject'])
def test_exact_deadline_action_wins_and_crossing_does_not_repeat(kind):
  drive = Drive(confirm=True)
  state = drive.tick().message.slcState
  start = drive.runtime.advance.encounter.started_ns
  assert start is not None
  action = drive.action(state, kind)
  output = drive.tick(at=start+acc.CONFIRMATION_NS, action=action)
  assert output.message.slcState.actionStatus == kind
  assert output.result.ceiling.speed_mps == (17. if kind == 'accept' else 22.)
  crossed = drive.tick(current=15., target=0., way=2)
  assert not crossed.message.slcState.hasPending


@pytest.mark.parametrize('producer', [41, 42])
def test_recovery_keeps_deadline_and_retires_actions_only_for_a_new_producer(producer):
  drive = Drive(confirm=True)
  first = drive.tick().message.slcState
  start = drive.runtime.advance.encounter.started_ns
  assert start is not None
  lost = drive.tick(alive=False)
  assert lost.message.slcState.hasPending
  assert lost.message.slcState.decisionId == first.decisionId and lost.message.slcState.presentationId == first.presentationId
  recovered = drive.tick(at=start+9_000_000_000, producer=producer).message.slcState
  assert recovered.hasPending and (recovered.presentationId == first.presentationId) == (producer == 41)
  expired = drive.tick(at=start+acc.CONFIRMATION_NS+1)
  assert not expired.message.slcState.hasPending


@pytest.mark.parametrize('kind', ['accept', 'reject'])
@pytest.mark.parametrize('gap', [{'alive': False}, {'target': 0.}], ids=['source-lost', 'preview-missing'])
@pytest.mark.parametrize('wheel', [False, True], ids=['touch', 'wheel'])
def test_source_gap_keeps_early_confirmation_actionable_and_does_not_repeat_at_the_boundary(kind, gap, wheel):
  drive = Drive(confirm=True)
  first = drive.tick().message.slcState
  lost = drive.tick(**gap).message.slcState
  assert lost.presentationId == first.presentationId
  if wheel:
    pending = physical_actions.pending_confirmation(ReplaySM({'slcState': lost}, drive.now), drive.now, long_active=True)
    assert pending is not None and pending.presentation_id == first.presentationId
    drive.cp.pcmCruise = False
    helper = VCruiseHelper(drive.cp)
    helper.v_cruise_kph = drive.sm['carState'].vCruise
    button = 'accelCruise' if kind == 'accept' else 'decelCruise'
    cs = car.CarState(cruiseState={'available': True}, buttonEvents=[car.CarState.ButtonEvent(type=button, pressed=True)])
    helper.update_v_cruise(cs, True, True, pending, slc_enabled=True)
    consumed = helper.slc_consumed_button
    assert consumed is not None
    cs.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=False)]
    helper.update_v_cruise(cs, True, True, slc_enabled=True)
    assert helper.v_cruise_kph == drive.sm['carState'].vCruise
    stamp = drive.now + STEP
    event = CruiseEvent(1, stamp, 0., 0., consumed.button, 'card-producer', stamp,
                        'confirmationAccept' if kind == 'accept' else 'confirmationReject',
                        consumed.pending.session_id, consumed.pending.decision_id, consumed.pending.presentation_id)
    output = drive.tick(**gap, event=event)
  else:
    output = drive.tick(**gap, action=drive.action(first, kind))
  assert not output.result.errors and output.message.slcState.actionStatus == kind
  assert not output.message.slcState.hasPending
  assert output.result.ceiling.speed_mps == (17. if kind == 'accept' else 22.)
  encounter = drive.runtime.advance.encounter
  assert (encounter.accepted_ns is not None) == (kind == 'accept')
  assert encounter.declined == (kind == 'reject')
  crossed = drive.tick(current=15., target=0., way=2)
  assert not crossed.message.slcState.hasPending


def test_disengagement_cancels_early_confirmation_and_reengagement_gets_a_fresh_deadline():
  drive = Drive(confirm=True)
  first = drive.tick().message.slcState
  start = drive.now
  drive.sm['carControl'].enabled = drive.sm['carControl'].longActive = False
  drive.sm['selfdriveState'].enabled = False
  drive.sm['controlsState'].longControlState = 'off'
  inactive = drive.tick(at=start+2_000_000_000)
  assert not inactive.message.slcState.hasPending
  assert not drive.runtime.advance.encounter.declined
  drive.sm['carControl'].enabled = drive.sm['carControl'].longActive = True
  drive.sm['selfdriveState'].enabled = True
  drive.sm['controlsState'].longControlState = 'pid'
  resumed = drive.tick(at=start+20_000_000_000).message.slcState
  assert resumed.hasPending and resumed.decisionId != first.decisionId
  assert resumed.pendingSinceMonoTime == drive.now
  restarted = drive.now
  stale_action = drive.tick(action=drive.action(first, 'accept'))
  assert stale_action.message.slcState.hasPending
  assert drive.tick(at=restarted+9_000_000_000).message.slcState.hasPending
  assert not drive.tick(at=restarted+acc.CONFIRMATION_NS).message.slcState.hasPending
  assert drive.runtime.advance.encounter.declined


@pytest.mark.parametrize('distance', [60., 160.])
def test_fresh_different_boundary_supersedes_without_accepting_stale_action(distance):
  drive = Drive(confirm=True)
  first = drive.tick().message.slcState
  changed = drive.tick(boundary='b'*64, target=25., target_way=3, distance=distance, action=drive.action(first, 'accept'))
  state = changed.message.slcState
  assert state.acceptedSpeedLimit == 20.
  assert state.hasPending == (distance == 60.)
  assert state.decisionId != first.decisionId
  if state.hasPending:
    assert state.pendingSpeedLimit == 25.
  else:
    nearer = drive.tick(boundary='b'*64, target=25., target_way=3).message.slcState
    assert nearer.hasPending and nearer.pendingSpeedLimit == 25.
    assert nearer.decisionId != first.decisionId


def test_boundary_id_change_alone_does_not_disprove_the_latched_limit():
  drive = Drive(confirm=True)
  first = drive.tick().message.slcState
  changed = drive.tick(boundary='b'*64, speed=5., distance=55.).message.slcState
  assert changed.hasPending and changed.decisionId == first.decisionId
  assert changed.pendingSinceMonoTime == first.pendingSinceMonoTime


def test_far_replacement_does_not_reopen_a_declined_limit_if_it_returns():
  drive = Drive(confirm=True)
  first = drive.tick().message.slcState
  declined = drive.tick(action=drive.action(first, 'reject')).message.slcState
  assert declined.actionStatus == 'reject'
  assert not drive.tick(boundary='b'*64, target=25., target_way=3, distance=160.).message.slcState.hasPending
  assert not drive.tick().message.slcState.hasPending


def test_short_zone_can_qualify_exit_immediately_without_hidden_dwell():
  drive = Drive()
  drive.apply(drive.tick())
  exit_offer = drive.tick(current=15., way=2, target=20., target_way=3, boundary='b'*64, distance=10.)
  assert exit_offer.message.slcState.speedLimit == 15.
  assert exit_offer.message.slcState.acceptedSpeedLimit == 20.
  assert exit_offer.result.ceiling.speed_mps == 22.
  assert drive.runtime.history_limit.candidate.speed_mps == 15.
  drive.runtime.reset()
  assert drive.tick(alive=False).message.slcState.acceptedSpeedLimit == 15.


def test_time_edits_keep_decision_and_off_withdraws_without_set_rollback():
  drive = Drive(confirm=True)
  first = drive.tick().message.slcState
  drive.runtime.update_advance(5, 1)
  second = drive.tick(distance=60.).message.slcState
  assert second.decisionId == first.decisionId and second.pendingSinceMonoTime == first.pendingSinceMonoTime
  drive.runtime.update_advance(5, 0)
  assert not drive.tick().message.slcState.hasPending
  drive.runtime.update_advance(5, 10)
  assert not drive.tick().message.slcState.hasPending
  assert drive.tick(current=15., target=0., way=2).message.slcState.hasPending


@pytest.mark.parametrize('target', [15., 25.])
def test_applied_cap_survives_advance_off_source_loss_and_crossing(target):
  drive = Drive()
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 140.
  applied = drive.tick(target=target)
  assert applied.result.ceiling.speed_mps == target+2.
  drive.runtime.update_advance(0 if target > 20. else 5, 0 if target < 20. else 5)
  for changes in ({'target': target}, {'target': target, 'alive': False}, {'current': target, 'target': 0., 'way': 2}):
    output = drive.tick(**changes)
    assert drive.sm['carState'].vCruise == 140.
    assert output.result.ceiling.speed_mps == target+2.


def test_wheel_adopt_keeps_meaning_the_current_displayed_limit():
  drive = Drive()
  applied = drive.apply(drive.tick())
  adopted = drive.tick(action=drive.action(applied.message.slcState, 'adopt'))
  assert not adopted.result.errors
  assert adopted.message.slcState.acceptedSpeedLimit == 20.
  assert adopted.message.slcState.actionStatus == 'adopt'
  assert adopted.result.ceiling.speed_mps == 22.
  assert not adopted.message.slcState.upcomingBoundaryId


def test_stale_current_adopt_cannot_cancel_or_replace_a_fresh_producer_proposal():
  drive = Drive()
  applied = drive.apply(drive.tick())
  rejected = drive.tick(producer=42, action=drive.action(applied.message.slcState, 'adopt'))
  assert not rejected.result.errors
  assert rejected.message.slcState.acceptedSpeedLimit == 15.
  assert rejected.message.slcState.upcomingBoundaryId == BOUNDARY
  assert not drive.runtime.advance.encounter.disabled


def test_driver_ceiling_changes_keep_advance_and_explicit_current_adoption():
  drive = Drive()
  drive.apply(drive.tick())
  previous = drive.sm['carState'].vCruise / 3.6
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 80.
  event = CruiseEvent(2, drive.now+STEP, previous, 80./3.6, 'accel', 'card-producer', drive.now+STEP)
  overridden = drive.tick(event=event)
  assert not overridden.message.slcState.driverOverrideActive
  assert overridden.result.ceiling.speed_mps == 17.
  withdrawn = drive.tick()
  assert withdrawn.message.slcState.presentationId > 0
  current = drive.tick(action=drive.action(withdrawn.message.slcState, 'adopt'))
  assert not current.result.errors
  assert current.message.slcState.acceptedSpeedLimit == 20.
  assert current.message.slcState.actionStatus == 'adopt'
  assert not current.message.slcState.driverOverrideActive


def test_offset_edit_uses_upcoming_band_without_changing_decision_or_current_sign():
  drive = Drive()
  first = drive.tick()
  accepted_at = first.message.slcState.acceptedMonoTime
  drive.runtime.update_offsets(OffsetSchedule((OffsetBand(0., 18., 1.), OffsetBand(18., None, 3.))))
  output = drive.tick(at=drive.now+200_000_000)
  state = output.message.slcState
  assert state.acceptedMonoTime == accepted_at and not state.hasPending
  assert state.speedLimit == 20. and state.presentationAdjustedLimit == 23.
  assert state.acceptedAdjustedLimit == 16. and output.result.ceiling.speed_mps == 16.


def test_upcoming_acceptance_never_commands_card_set_speed():
  drive = Drive()
  output = drive.tick()
  assert output.result.ceiling.speed_mps == 17.
  assert drive.sm['carState'].vCruise == pytest.approx(79.2)


def test_existing_confirmation_resolver_shows_proposal_without_changing_current_sign():
  from openpilot.starpilot.ui.onroad_state import OnroadState
  drive = Drive(confirm=True)
  state = drive.tick().message.slcState
  obs = speed_limit_from_message(state)
  shown_state = OnroadState(True, False, 20., 79.2, obs, metric=True, show_slc_offset=True)
  normal = resolve_unified_speed(shown_state)
  prompt = resolve_unified_speed(shown_state, confirmation=True)
  assert normal.posted_text == '72' and normal.offset_text == '+7'
  assert prompt.posted_text == '54' and prompt.proposal_text == '61' and prompt.offset_text == '+7'


@pytest.mark.parametrize('field,value', [('previewSpeedLimit', float('nan')), ('previewDistance', -1.),
  ('previewDistance', 501.), ('previewBoundaryId', 'bad'), ('previewWayId', 0)])
def test_bad_optional_preview_cannot_disable_or_authorize_current_map(field, value):
  event = packet()
  event.mapdOut.previewSpeedLimit = 15.
  event.mapdOut.previewDistance = 50.
  event.mapdOut.previewBoundaryId = BOUNDARY
  event.mapdOut.previewWayId = 2
  setattr(event.mapdOut, field, value)
  source = MapSource()
  current = source.step(copy_frame(event.mapdOut, event_valid=True, event_boot_ns=event.logMonoTime),
    received=True, now_boot_ns=event.logMonoTime, transport_alive=True, drive_session='drive', car_fingerprint='car')
  assert current.kind is acc.ObservationKind.VALID and source.upcoming is None


def test_actual_go_fixture_qualifies_current_and_upcoming_fields_with_host_schema():
  root = Path(__file__).resolve().parents[4]
  fixture = json.loads((root / 'mapd_repo/cereal/testdata/mapd_preview_wire.json').read_text())
  frame = decode_event(base64.b64decode(fixture['event_base64']))
  source = MapSource()
  current = source.step(frame, received=True, now_boot_ns=frame.event_mono_ns, transport_alive=True,
    drive_session='drive', car_fingerprint='car')
  assert current.candidate.speed_mps == 20.
  assert source.upcoming.observation.candidate.speed_mps == 15.
  assert source.upcoming.boundary_id == BOUNDARY and source.upcoming.way_id == 2 and source.upcoming.distance_m == 60.


@pytest.mark.parametrize('key', ADVANCE_KEYS)
@pytest.mark.parametrize('raw', [b'0', b'1', b'5', b'10', b'-1', b'11', b'1.5', b'broken', b'\xff', b'5\n', b'05'])
def test_saved_advance_values_are_independent_bounded_and_not_rewritten(tmp_path, key, raw):
  params = Params(str(tmp_path))
  params.put_bool('SpeedLimitController', True, block=True)
  other = next(name for name in ADVANCE_KEYS if name != key)
  params.put(other, 7, block=True)
  path = Path(params.get_param_path(key))
  path.write_bytes(raw)
  settings = read_params(params)
  assert settings.enabled and not settings.errors
  values = dict(zip(ADVANCE_KEYS, (settings.advance_higher_seconds, settings.advance_lower_seconds), strict=True))
  assert values[key] == ({b'1': 1, b'5': 5, b'10': 10}.get(raw, 0))
  assert values[other] == 7
  assert path.read_bytes() == raw


def test_timer_rejects_symlink_without_disabling_other_direction(tmp_path):
  params = Params(str(tmp_path))
  params.put_bool('SpeedLimitController', True, block=True)
  params.put('SLCAdvanceLowerSeconds', 6, block=True)
  source = tmp_path / 'linked-value'
  source.write_bytes(b'9')
  Path(params.get_param_path('SLCAdvanceHigherSeconds')).symlink_to(source)
  settings = read_params(params)
  assert settings.enabled
  assert (settings.advance_higher_seconds, settings.advance_lower_seconds) == (0, 6)


@pytest.mark.parametrize('target,key', [(15., 'SLCAdvanceLowerSeconds'), (25., 'SLCAdvanceHigherSeconds')])
def test_time_only_lifecycle_refresh_keeps_session_decision_deadline_and_decline(tmp_path, target, key):
  from openpilot.selfdrive.controls.plannerd import SavedSlcLifecycle
  params = Params(str(tmp_path))
  for name, value in {'SpeedLimitController': True, 'IsMetric': True}.items():
    params.put_bool(name, value, block=True)
  params.put('SLCConfirmation', 1, block=True)
  params.put('SLCPriority1', 'Map Data', block=True)
  params.put(key, 5, block=True)
  drive = Drive()
  lifecycle = SavedSlcLifecycle(params, drive.cp, {'SLC_REPLAY_RUNTIME': '1'})
  runtime, _ = lifecycle.refresh(START)
  drive.runtime = runtime
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 72.
  drive.tick(target=0.)
  first = drive.tick(target=target).message.slcState
  assert first.hasPending
  reject = drive.action(first, 'reject')
  queued = deque([reject])
  params.put(key, 1, block=True)
  now = drive.now+1_000_000_000
  assert lifecycle.refresh(now, pending_actions=queued)[0] is runtime
  pending = drive.tick(at=now, target=target).message.slcState
  assert pending.decisionId == first.decisionId and pending.pendingSinceMonoTime == first.pendingSinceMonoTime
  assert list(queued) == [reject]
  drive.tick(target=target, action=queued.popleft())
  params.put(key, 10, block=True)
  now = drive.now+1_000_000_000
  assert lifecycle.refresh(now)[0] is runtime
  assert not drive.tick(at=now, target=target).message.slcState.hasPending
  crossed = drive.tick(current=target, target=0., way=2)
  assert not crossed.message.slcState.hasPending


@pytest.mark.parametrize('key', ADVANCE_KEYS)
def test_existing_settings_owner_saves_guards_and_repairs_each_timing_option(tmp_path, key):
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
  from openpilot.starpilot.ui.feature_settings_state import row_change, row_default
  params = Params(str(tmp_path))
  params.put_bool('SpeedLimitController', True, block=True)
  drive = Drive()
  allowed = [True]
  owner = FeatureSettingsOwner(params, lambda group: allowed[0], vehicle_fingerprint=lambda: drive.cp.carFingerprint)
  owner.vehicle_params = lambda: drive.cp
  owner.configuration_vehicle = lambda: True
  def rows():
    return owner.snapshot('slc', parked=True, system_long=True, lateral_context=True, metric=True).rows
  def row():
    return next(item for item in rows() if item.key == key)
  keys = [item.key for item in rows()]
  assert keys.index(ADVANCE_KEYS[0]) == keys.index('SLCConfirmation')+1
  assert keys.index(ADVANCE_KEYS[1]) == keys.index(ADVANCE_KEYS[0])+1
  assert 'SLCAdvanceSeconds' not in keys
  assert row().available and row().value == 'Off'
  request = row_change(row())
  assert request is not None
  assert owner.apply(request)
  settings = read_params(params)
  assert (settings.advance_higher_seconds, settings.advance_lower_seconds) == ((1, 0) if key == ADVANCE_KEYS[0] else (0, 1))
  assert not owner.apply(request)
  request = row_change(row())
  assert request is not None
  allowed[0] = False
  assert not owner.apply(request)
  allowed[0] = True
  Path(params.get_param_path(key)).write_bytes(b'broken')
  assert row().choices == ('Off',)
  request = row_default(row())
  assert request is not None and owner.apply(request)
  settings = read_params(params)
  assert settings.advance_higher_seconds == settings.advance_lower_seconds == 0 and settings.enabled


@pytest.mark.parametrize('target', [15., 25.])
def test_editing_the_other_direction_does_not_cancel_a_latched_choice(target):
  drive = Drive(confirm=True)
  first = drive.tick(target=target).message.slcState
  drive.runtime.update_advance(5 if target > 20. else 0, 5 if target < 20. else 0)
  retained = drive.tick(target=target, speed=0., stopped=True).message.slcState
  assert retained.hasPending and retained.decisionId == first.decisionId
  assert retained.pendingSinceMonoTime == first.pendingSinceMonoTime
  drive.runtime.update_advance(0 if target > 20. else 5, 0 if target < 20. else 5)
  assert not drive.tick(target=target).message.slcState.hasPending


@pytest.mark.parametrize('higher', [0, 5])
def test_revised_limit_at_same_boundary_retires_old_choice(higher):
  drive = Drive(confirm=True, higher=higher, lower=5)
  first = drive.tick(target=15.).message.slcState
  assert first.hasPending
  revised = drive.tick(target=25., action=drive.action(first, 'accept')).message.slcState
  assert revised.acceptedSpeedLimit == 20.
  assert revised.hasPending == bool(higher)
  if higher:
    assert revised.pendingSpeedLimit == 25.
    assert revised.decisionId != first.decisionId


@pytest.mark.parametrize('boundary,way', [(BOUNDARY, 2), ('b'*64, 3)])
def test_ineligible_boundary_revision_keeps_the_applied_cap(boundary, way):
  drive = Drive(higher=0, lower=5)
  assert drive.tick(target=15.).message.slcState.acceptedSpeedLimit == 15.
  revised = drive.tick(target=25., boundary=boundary, target_way=way).message.slcState
  assert revised.acceptedSpeedLimit == 15.
  assert not revised.upcomingBoundaryId
  assert drive.tick(current=25., target=0., way=way).message.slcState.acceptedSpeedLimit == 25.


@pytest.mark.parametrize('target', [15., 25.])
@pytest.mark.parametrize('confirm', [False, True])
def test_confirmed_destination_corrects_an_early_cap_even_at_the_approach_speed(target, confirm):
  drive = Drive(confirm=confirm)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 108.
  early = drive.tick(target=target).message.slcState
  if confirm:
    early = drive.tick(target=target, action=drive.action(early, 'accept')).message.slcState
  assert early.acceptedSpeedLimit == target

  for inputs in ({'alive': False}, {'target': 0.}):
    held = drive.tick(**inputs).message.slcState
    assert held.effectiveCap == target + 2.
    assert not held.hasPending

  arrived = drive.tick(current=20., target=0., way=2).message.slcState
  assert not arrived.hasPending
  assert arrived.acceptedSpeedLimit == 20.
  assert arrived.effectiveCap == 22.


@pytest.mark.parametrize('confirm', [False, True])
def test_fresh_other_road_reverts_applied_preview_even_at_the_same_approach_speed(confirm):
  drive = Drive(confirm=confirm)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 108.
  preview = drive.tick(target=25.).message.slcState
  if confirm:
    preview = drive.tick(target=25., action=drive.action(preview, 'accept')).message.slcState
  assert preview.effectiveCap == 27.

  assert drive.tick(target=25., way=3).message.slcState.effectiveCap == 27.
  assert drive.tick(alive=False).message.slcState.effectiveCap == 27.
  assert drive.tick(target=0., way=3).message.slcState.effectiveCap == 27.
  departed = drive.tick(target=0., way=4)
  assert departed.message.slcState.effectiveCap == 22.
  assert not departed.message.slcState.hasPending
  assert departed.message.slcState.acceptedSpeedLimit == 20.


def test_departure_keeps_the_preview_limit_when_a_preferred_live_source_confirms_it():
  drive = Drive(confirm=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 108.
  drive.runtime.settings = replace(drive.runtime.settings,
    selection=parse({'SLCPriority1': 'Dashboard', 'SLCPriority2': 'Maps'}).selection)
  preview = drive.tick(target=15.).message.slcState
  assert drive.tick(target=15., action=drive.action(preview, 'accept')).message.slcState.effectiveCap == 17.
  for _ in range(2):
    state = drive.tick(way=3, target=0., dashboard=15.).message.slcState
    assert state.source == 'dashboard' and state.acceptedSpeedLimit == 15.
    assert state.effectiveCap == 17. and not state.hasPending


def test_departure_restores_the_previous_cap_while_a_different_road_limit_waits():
  drive = Drive(confirm=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 108.
  preview = drive.tick(target=25.).message.slcState
  assert drive.tick(target=25., action=drive.action(preview, 'accept')).message.slcState.effectiveCap == 27.
  # The preview still describes the same approach after a tile refresh.
  assert drive.tick(target=25., way=3, boundary='b'*64).message.slcState.effectiveCap == 27.
  state = drive.tick(current=16., way=4, target=0.).message.slcState
  assert state.acceptedSpeedLimit == 20. and state.effectiveCap == 22.
  assert state.hasPending and state.pendingSpeedLimit == 16.
  accepted = drive.tick(current=16., way=4, target=0., action=drive.action(state, 'accept')).message.slcState
  assert accepted.acceptedSpeedLimit == 16. and accepted.effectiveCap == 18.


def test_departure_without_previous_acceptance_drops_the_preview_cap():
  drive = Drive(confirm=True)
  drive.runtime = Runtime(drive.runtime.settings)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 108.
  current = drive.tick(target=0.).message.slcState
  declined = drive.tick(target=0., action=drive.action(current, 'reject')).message.slcState
  assert declined.actionStatus == 'reject' and not declined.hasAccepted
  preview = drive.tick(target=25.).message.slcState
  assert drive.tick(target=25., action=drive.action(preview, 'accept')).message.slcState.hasCeiling
  state = drive.tick(current=16., way=3, target=0.).message.slcState
  assert not state.hasAccepted and not state.hasCeiling
  assert state.hasPending and state.pendingSpeedLimit == 16.


@pytest.mark.parametrize('accept_replacement', [False, True], ids=['pending', 'accepted'])
def test_departure_restores_72_after_90_preview_is_replaced_by_108(accept_replacement):
  drive = Drive(confirm=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  drive.runtime.update_offsets(vision_only_settings().offsets)
  first = drive.tick(target=25.).message.slcState
  assert drive.tick(target=25., action=drive.action(first, 'accept')).message.slcState.effectiveCap == 25.
  revised = {'target': 30., 'target_way': 3, 'boundary': 'b'*64}
  replacement = drive.tick(**revised).message.slcState
  assert replacement.hasPending and replacement.pendingSpeedLimit == 30.
  if accept_replacement:
    assert drive.tick(**revised, action=drive.action(replacement, 'accept')).message.slcState.effectiveCap == 30.
  # A late response to the old preview must not accept the new road's limit.
  state = drive.tick(current=16., way=4, target=0., action=drive.action(replacement, 'accept')).message.slcState
  assert state.acceptedSpeedLimit == state.effectiveCap == 20.
  assert state.hasPending and state.pendingSpeedLimit == 16.
  assert drive.tick(current=16., way=4, target=0.).message.slcState.decisionId == state.decisionId


def test_far_revision_retires_only_the_unanswered_preview():
  drive = Drive(confirm=True)
  first = drive.tick(target=25.).message.slcState
  drive.tick(target=25., action=drive.action(first, 'accept'))
  pending = drive.tick(target=30., target_way=3, boundary='b'*64).message.slcState
  assert pending.hasPending
  far = drive.tick(target=32., target_way=5, boundary='c'*64, distance=160., action=drive.action(pending, 'accept')).message.slcState
  assert not far.hasPending and far.acceptedSpeedLimit == 25.
  departed = drive.tick(current=16., way=4, target=0.).message.slcState
  assert departed.acceptedSpeedLimit == 20. and departed.pendingSpeedLimit == 16.


@pytest.mark.parametrize('maps_alive', [False, True])
def test_preferred_dashboard_can_restore_the_approach_limit(maps_alive):
  drive = Drive()
  drive.runtime.settings = replace(drive.runtime.settings,
    selection=parse({'SLCPriority1': 'Dashboard', 'SLCPriority2': 'Maps'}).selection)
  assert drive.tick(target=25.).message.slcState.acceptedSpeedLimit == 25.
  assert drive.tick(alive=False).message.slcState.acceptedSpeedLimit == 25.
  state = drive.tick(alive=maps_alive, target=25., dashboard=20.).message.slcState
  assert state.source == 'dashboard' and state.acceptedSpeedLimit == 20.
  assert not state.hasPending and not state.upcomingBoundaryId


@pytest.mark.parametrize('confirm_higher', [False, True])
def test_corrected_arrival_compares_direction_to_the_accepted_road(confirm_higher):
  drive = Drive(confirm=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  preview = drive.tick(target=25.).message.slcState
  drive.tick(target=25., action=drive.action(preview, 'accept'))
  drive.runtime.settings = replace(drive.runtime.settings, acceptance=acc.Policy(confirm_lower=not confirm_higher,
                                                                                confirm_higher=confirm_higher))
  arrived = drive.tick(current=22.5, way=2, target=0.).message.slcState
  assert arrived.hasPending == confirm_higher
  assert arrived.acceptedSpeedLimit == (20. if confirm_higher else 22.5)


def test_same_approach_correction_does_not_reopen_the_old_preview():
  drive = Drive(confirm=True)
  preview = drive.tick(target=25.).message.slcState
  drive.tick(target=25., action=drive.action(preview, 'accept'))
  corrected = drive.tick(current=16., target=25.).message.slcState
  assert corrected.effectiveCap == 22. and corrected.pendingSpeedLimit == 16.
  next_frame = drive.tick(current=16., target=25.).message.slcState
  assert next_frame.decisionId == corrected.decisionId and not next_frame.upcomingBoundaryId


@pytest.mark.parametrize('mode,preview', [('Dashboard', 25.), ('Lowest', 25.), ('Highest', 15.)])
@pytest.mark.parametrize('confirm', [False, True])
def test_source_recovery_does_not_reopen_the_same_early_offer(mode, preview, confirm):
  drive = Drive(confirm=confirm)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  drive.runtime.settings = replace(drive.runtime.settings, selection=parse({'SLCPriority1': mode, 'SLCPriority2': 'Maps'}).selection)
  first = drive.tick(target=preview).message.slcState
  assert first.hasPending == confirm and first.upcomingBoundaryId
  for dashboard, boundary in ((20., BOUNDARY), (None, 'b'*64), (20., 'b'*64), (None, BOUNDARY)):
    shown = drive.tick(target=preview, dashboard=dashboard, boundary=boundary).message.slcState
    assert not shown.hasPending and not shown.upcomingBoundaryId and shown.effectiveCap == 22.
  arrived = drive.tick(current=preview, way=2, target=0.).message.slcState
  assert arrived.hasPending == confirm
  assert arrived.acceptedSpeedLimit == (20. if confirm else preview)


@pytest.mark.parametrize('mode,preview,rival', [('Lowest', 25., 26.), ('Highest', 15., 14.)])
def test_extrema_keep_preview_when_maps_still_wins(mode, preview, rival):
  drive = Drive()
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  drive.runtime.settings = replace(drive.runtime.settings, selection=parse({'SLCPriority1': mode}).selection)
  assert drive.tick(target=preview).message.slcState.acceptedSpeedLimit == preview
  assert drive.tick(target=preview, dashboard=rival).message.slcState.acceptedSpeedLimit == preview


def test_map_gap_allows_a_qualified_different_fallback_limit():
  drive = Drive()
  assert drive.tick(target=25.).message.slcState.acceptedSpeedLimit == 25.
  assert drive.tick(alive=False, dashboard=20.).message.slcState.acceptedSpeedLimit == 25.
  fallback = drive.tick(alive=False, dashboard=16.).message.slcState
  assert fallback.acceptedSpeedLimit == 16. and fallback.source == 'dashboard'


def test_pending_replacement_keeps_its_deadline_on_arrival_and_retires_the_old_cap():
  drive = Drive(confirm=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  first = drive.tick(target=25.).message.slcState
  drive.tick(target=25., action=drive.action(first, 'accept'))
  replacement = drive.tick(target=30., target_way=3, boundary='b'*64).message.slcState
  arrived = drive.tick(current=30., way=3, target=20., target_way=4, boundary='c'*64, distance=10.).message.slcState
  assert arrived.effectiveCap == 22. and arrived.pendingSpeedLimit == 30.
  assert (arrived.decisionId, arrived.pendingSinceMonoTime) == (replacement.decisionId, replacement.pendingSinceMonoTime)


def test_dashboard_only_ignores_maps_and_vision_during_a_source_gap():
  drive = Drive()
  drive.runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCPriority2': 'Off',
                                 'SLCAdvanceHigherSeconds': 5}), vision_enabled=True, vision_control_qualified=True)
  for dashboard, expected in ((18., 18.), (None, 18.), (16., 16.)):
    state = drive.tick(dashboard=dashboard, vision_speed=22., target=25., distance=10.).message.slcState
    assert state.effectiveCap == expected and state.acceptedSource == 'dashboard'
    assert not state.upcomingBoundaryId and drive.runtime.advance.encounter is None


@pytest.mark.parametrize('mode,preview,rival', [('Lowest', 25., 26.), ('Highest', 15., 14.)])
def test_losing_rival_cannot_replace_a_held_cap_while_maps_is_current(mode, preview, rival):
  drive = Drive()
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  drive.runtime.settings = replace(drive.runtime.settings, selection=parse({'SLCPriority1': mode}).selection)
  assert drive.tick(target=preview).message.slcState.acceptedSpeedLimit == preview
  state = drive.tick(target=0., dashboard=rival).message.slcState
  assert state.source == 'map' and state.acceptedSpeedLimit == preview


@pytest.mark.parametrize('mode', ['Highest', 'Lowest'])
def test_applied_cap_and_pending_replacement_compete_independently(mode):
  drive = Drive(confirm=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  drive.runtime.settings = replace(drive.runtime.settings, selection=parse({'SLCPriority1': mode}).selection)
  first = drive.tick(target=25.).message.slcState
  drive.tick(target=25., action=drive.action(first, 'accept'))
  revised = {'target': 15., 'target_way': 3, 'boundary': 'b'*64}
  pending = drive.tick(**revised).message.slcState
  assert pending.pendingSpeedLimit == 15.
  if mode == 'Highest':
    state = drive.tick(**revised, dashboard=20., action=drive.action(pending, 'accept')).message.slcState
    assert state.acceptedSpeedLimit == 25. and not state.hasPending
    assert not drive.tick(**revised).message.slcState.hasPending
  else:
    state = drive.tick(**revised, dashboard=20.).message.slcState
    assert state.acceptedSpeedLimit == 20. and state.pendingSpeedLimit == 15.
    assert (state.decisionId, state.pendingSinceMonoTime) == (pending.decisionId, pending.pendingSinceMonoTime)
    assert drive.tick(**revised, dashboard=20., action=drive.action(state, 'accept')).message.slcState.acceptedSpeedLimit == 15.


def test_corrected_current_limit_prompt_precedes_the_exit_preview():
  drive = Drive(confirm=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  preview = drive.tick(target=25.).message.slcState
  drive.tick(target=25., action=drive.action(preview, 'accept'))
  zone = {'current': 16., 'way': 2, 'target': 30., 'target_way': 3, 'boundary': 'b'*64, 'distance': 10.}
  corrected = drive.tick(**zone).message.slcState
  next_frame = drive.tick(**zone).message.slcState
  assert next_frame.effectiveCap == 22. and next_frame.pendingSpeedLimit == 16.
  assert (next_frame.decisionId, next_frame.pendingSinceMonoTime) == (corrected.decisionId, corrected.pendingSinceMonoTime)
  accepted = drive.tick(**zone, action=drive.action(next_frame, 'accept')).message.slcState
  assert accepted.acceptedSpeedLimit == 16.
  assert drive.tick(**zone).message.slcState.pendingSpeedLimit == 30.


def test_early_limit_replaces_history_only_after_current_road_confirms_it():
  drive = Drive()
  early = drive.tick(target=25.).message.slcState
  assert early.acceptedSpeedLimit == 25. and early.acceptedIsPreview
  assert drive.runtime.history_limit.candidate.speed_mps == 20.
  gap = drive.tick(alive=False).message.slcState
  assert gap.acceptedSpeedLimit == 25. and gap.acceptedIsPreview
  assert drive.runtime.history_limit.candidate.speed_mps == 20.
  drive.runtime.reset()
  assert drive.tick(alive=False).message.slcState.acceptedSpeedLimit == 20.

  assert drive.tick(target=25.).message.slcState.acceptedSpeedLimit == 25.
  arrived = drive.tick(current=25., way=2, target=0.).message.slcState
  assert arrived.acceptedSpeedLimit == 25. and not arrived.acceptedIsPreview
  assert drive.runtime.history_limit.candidate.speed_mps == 25.
  drive.runtime.reset()
  assert drive.tick(alive=False).message.slcState.acceptedSpeedLimit == 25.


@pytest.mark.parametrize('choice', [None, 'accept', 'reject'])
@pytest.mark.parametrize('cold_start', [False, True], ids=['known-limit', 'first-map-reading'])
def test_first_current_road_decision_precedes_its_exit(choice, cold_start):
  drive = Drive(confirm=choice is not None)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  if cold_start:
    drive.runtime = Runtime(drive.runtime.settings)
    unavailable = drive.tick(alive=False).message.slcState
    assert not unavailable.hasAccepted and not unavailable.hasPending
  zone = {'current': 16., 'way': 2, 'target': 30., 'target_way': 3, 'distance': 10.}
  entered = drive.tick(**zone).message.slcState
  assert not entered.upcomingBoundaryId
  if choice is None:
    assert entered.acceptedSpeedLimit == 16.
    assert drive.tick(**zone).message.slcState.acceptedSpeedLimit == 30.
  else:
    assert entered.pendingSpeedLimit == 16.
    held = drive.tick(**zone).message.slcState
    assert held.decisionId == entered.decisionId and held.pendingSpeedLimit == 16.
    resolved = drive.tick(**zone, action=drive.action(held, choice)).message.slcState
    assert resolved.actionStatus == choice
    assert drive.tick(**zone).message.slcState.pendingSpeedLimit == 30.


@pytest.mark.parametrize('accepted', [False, True])
def test_known_turn_away_without_posted_speed_retires_preview(accepted):
  drive = Drive(confirm=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  early = drive.tick(target=25.).message.slcState
  if accepted:
    drive.tick(target=25., action=drive.action(early, 'accept'))
  gap = drive.tick(alive=False).message.slcState
  assert gap.acceptedSpeedLimit == (25. if accepted else 20.) and gap.hasPending != accepted
  same_road = drive.tick(current=0., target=0., road_status='matchedNoLimit').message.slcState
  assert same_road.acceptedSpeedLimit == gap.acceptedSpeedLimit and same_road.hasPending == gap.hasPending
  turned = drive.tick(current=0., target=0., way=3, road_status='matchedNoLimit',
                      action=drive.action(early, 'accept') if not accepted else None).message.slcState
  assert turned.observationKind == 'unknown'
  assert turned.acceptedSpeedLimit == 20. and not turned.hasPending


def test_retired_cap_cannot_hide_later_current_limit_on_same_way():
  drive = Drive()
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  assert drive.tick(target=25.).message.slcState.acceptedSpeedLimit == 25.
  assert drive.tick(current=27., target=0.).message.slcState.acceptedSpeedLimit == 27.
  assert drive.tick(current=20., target=0.).message.slcState.acceptedSpeedLimit == 20.


def test_current_reading_matching_the_older_cap_retires_the_replacement():
  drive = Drive(confirm=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  first = drive.tick(target=25.).message.slcState
  drive.tick(target=25., action=drive.action(first, 'accept'))
  assert drive.tick(target=30., target_way=3).message.slcState.pendingSpeedLimit == 30.
  current = drive.tick(current=25., target=0.).message.slcState
  assert current.acceptedSpeedLimit == 25. and not current.hasPending and not current.acceptedIsPreview
  assert drive.tick(current=20., target=0.).message.slcState.pendingSpeedLimit == 20.


@pytest.mark.parametrize('mode,preview,dashboard', [('Highest', 25., 22.), ('Lowest', 15., 18.)])
def test_adopt_current_releases_preview_priority(mode, preview, dashboard):
  drive = Drive()
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  drive.runtime.settings = replace(drive.runtime.settings, selection=parse({'SLCPriority1': mode}).selection)
  early = drive.tick(target=preview).message.slcState
  assert drive.tick(target=preview, action=drive.action(early, 'adopt')).message.slcState.acceptedSpeedLimit == 20.
  assert drive.tick(target=preview, dashboard=dashboard).message.slcState.acceptedSpeedLimit == dashboard


def test_losing_replacement_does_not_reopen_approach_during_map_gap():
  drive = Drive(confirm=True)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 126.
  drive.runtime.settings = replace(drive.runtime.settings, selection=parse({'SLCPriority1': 'Highest'}).selection)
  early = drive.tick(target=25.).message.slcState
  drive.tick(target=25., action=drive.action(early, 'accept'))
  replacement = {'target': 15., 'target_way': 3, 'boundary': 'b'*64}
  assert drive.tick(**replacement).message.slcState.pendingSpeedLimit == 15.
  assert not drive.tick(**replacement, dashboard=20.).message.slcState.hasPending
  gap = drive.tick(alive=False, dashboard=20.).message.slcState
  assert gap.acceptedSpeedLimit == 25. and not gap.hasPending
  fallback = drive.tick(alive=False, dashboard=18.).message.slcState
  assert fallback.acceptedSpeedLimit == 20. and fallback.pendingSpeedLimit == 18.


def test_same_speed_replacement_gets_a_fresh_decision_and_deadline():
  drive = Drive(confirm=True)
  first = drive.tick().message.slcState
  deadline = first.pendingSinceMonoTime + acc.CONFIRMATION_NS
  replacement = {'target_way': 3, 'boundary': 'b'*64}
  revised = drive.tick(**replacement, at=deadline-100_000_000, action=drive.action(first, 'accept')).message.slcState
  assert revised.pendingSpeedLimit == first.pendingSpeedLimit and revised.acceptedSpeedLimit == 20.
  assert revised.decisionId != first.decisionId and revised.pendingSinceMonoTime == drive.now
  assert drive.tick(**replacement, at=deadline+1).message.slcState.decisionId == revised.decisionId
