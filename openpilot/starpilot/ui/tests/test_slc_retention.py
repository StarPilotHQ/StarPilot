"""Saved retention policy through Maps, the native planner, and the driving UI."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib import longitudinal_planner as native
from openpilot.selfdrive.controls.plannerd import SavedSlcLifecycle
from openpilot.starpilot.speed_limits import offset_document as od
from openpilot.starpilot.speed_limits import physical_actions
from openpilot.starpilot.speed_limits.runtime import Action, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import read_params
from openpilot.starpilot.speed_limits.tests.test_maps_runtime import BOOT, packet
from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
from openpilot.starpilot.ui.onroad_state import AlertSize, ObservationKind, OnroadInput, SlcActionKind, slc_controls
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, RuntimeSnapshotAdapter, ui_fake
from openpilot.starpilot.ui.tests.test_slc_ui_runtime import publish_request, replay_inputs
from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed

MPH = .44704
# Planner caps retain Float32 source precision, independently of SET display rounding.



@pytest.mark.parametrize('offset,delta', [(-25, 0), (-30, 0), (-24, 2), (-5, 0)])
def test_negative_offsets_cannot_remove_the_native_slc_ceiling(tmp_path, offset, delta):
  drive = RetentionDrive(tmp_path)
  params = Params(str(tmp_path))
  document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (offset * MPH, 0, 0, 0, 0, 0, 0))
  params.put('SLCOffsetSchedule', od.to_value(document), block=True)
  drive.runtime = Runtime(read_params(params))
  drive.sm['carState'].vEgoCluster += delta
  output, _, target = drive.step(0, limit=25)
  adjusted = 20 * MPH if offset == -5 else 25 * MPH
  assert not output.result.errors
  assert output.message.slcState.hasCeiling
  assert output.message.slcState.acceptedAdjustedLimit == pytest.approx(adjusted)
  assert output.message.slcState.effectiveClusterTarget == pytest.approx(adjusted)
  assert target == pytest.approx((adjusted-delta) / MPH, rel=0, abs=1e-4)


@pytest.mark.parametrize('confirm', [False, True])
@pytest.mark.parametrize('source_gap', [False, True])
def test_live_offset_edits_keep_the_accepted_limit_and_native_ceiling(tmp_path, confirm, source_gap):
  drive = RetentionDrive(tmp_path, confirm=confirm)
  params = Params(str(tmp_path))
  lifecycle = SavedSlcLifecycle(params, drive.cp, {'SLC_REPLAY_RUNTIME': '1'})
  runtime, _ = lifecycle.refresh(NOW)
  assert runtime is not None
  drive.runtime = runtime
  first, _, _ = drive.step(0)
  output, _, _ = drive.step(50, request=Action(runtime.session_id, 1, first.message.slcState.decisionId,
                                             first.message.slcState.presentationId, 'accept') if confirm else None)
  accepted = output.result.state.acceptance.accepted
  session = runtime.session_id
  for milliseconds, offset in ((1000, 5), (2000, -5)):
    document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (0, 0, 0, offset * MPH, 0, 0, 0))
    params.put('SLCOffsetSchedule', od.to_value(document), block=True)
    receipt = object()
    actions, cruise = [object()], [receipt]
    refreshed, changed = lifecycle.refresh(NOW + milliseconds * 1_000_000, pending_actions=actions, pending_cruise=cruise)
    assert changed and refreshed is runtime
    assert not actions and cruise == [receipt]
    output, _, target = drive.step(milliseconds, received=not source_gap)
    assert not output.result.errors
    assert runtime.session_id == session
    assert output.result.state.acceptance.accepted == accepted
    assert not output.message.slcState.hasPending
    assert output.message.slcState.hasCeiling
    assert target == pytest.approx(45 + offset, rel=0, abs=1e-4)
  if source_gap:
    assert drive.step(2500)[2] == pytest.approx(40, rel=0, abs=1e-4)


@pytest.mark.parametrize('key,value', [('SLCConfirmation', 1), ('SLCPriority1', 'Dashboard'),
                                     ('SLCPriority1', 'Vision'), ('SLCPriority2', 'Vision'),
                                     ('SLCConfirmation', 3)])
@pytest.mark.parametrize('source_gap', [False, True])
def test_live_policy_edits_preserve_history_until_a_new_limit_is_adopted(tmp_path, key, value, source_gap):
  drive = RetentionDrive(tmp_path)
  params = Params(str(tmp_path))
  lifecycle = SavedSlcLifecycle(params, drive.cp, {})
  original, _ = lifecycle.refresh(NOW)
  drive.runtime = original
  before, _, target = drive.step(0)
  assert target == pytest.approx(45, abs=1e-4)
  accepted = before.result.acceptance.state.accepted
  params.put(key, value, block=True)
  drive.runtime, changed = lifecycle.refresh(NOW + 1_000_000_000)
  assert changed
  assert drive.runtime is original
  assert drive.runtime.state.acceptance.accepted == accepted
  after, _, target = drive.step(1000, received=not source_gap)
  if key == 'SLCPriority1' and value == 'Dashboard':
    assert after.message.slcState.acceptedSource == 'dashboard'
    assert after.result.acceptance.history_write is not None
    assert target == pytest.approx(70, abs=1e-4)
  else:
    assert after.result.acceptance.state.accepted == accepted
    assert target == pytest.approx(45, abs=1e-4)
  assert after.message.slcState.hasCeiling
  assert drive.sm['carState'].vCruise == pytest.approx(70 * MPH * 3.6)


@pytest.mark.parametrize('key,value', [('SLCPriority2', 'Dashboard'), ('SLCConfirmation', 3)])
def test_policy_edit_renews_pending_actions_and_preserves_the_cap(tmp_path, key, value):
  drive = RetentionDrive(tmp_path, confirm=True)
  params = Params(str(tmp_path))
  lifecycle = SavedSlcLifecycle(params, drive.cp, {})
  drive.runtime, _ = lifecycle.refresh(NOW)
  first, _, _ = drive.step(0)
  drive.step(50, request=Action(drive.runtime.session_id, 1, first.message.slcState.decisionId,
                               first.message.slcState.presentationId, 'accept'))
  pending, _, _ = drive.step(100, limit=50)
  previous = pending.message.slcState
  old_action = Action(drive.runtime.session_id, 2, previous.decisionId, previous.presentationId, 'accept')
  params.put(key, value, block=True)
  queued = [old_action]
  drive.runtime, _ = lifecycle.refresh(NOW + 1_000_000_000, pending_actions=queued)
  assert not queued
  after, _, target = drive.step(1000, limit=50, request=old_action)
  assert after.message.slcState.hasPending
  assert after.message.slcState.decisionId != previous.decisionId
  assert after.message.slcState.presentationId != previous.presentationId
  assert target == pytest.approx(45, abs=1e-4)


@pytest.mark.parametrize('accepted_first', [False, True])
def test_offset_edits_invalidate_old_prompts_without_accepting_a_new_limit(tmp_path, accepted_first):
  drive = RetentionDrive(tmp_path, confirm=True)
  params = Params(str(tmp_path))
  lifecycle = SavedSlcLifecycle(params, drive.cp, {'SLC_REPLAY_RUNTIME': '1'})
  runtime, _ = lifecycle.refresh(NOW)
  assert runtime is not None
  drive.runtime = runtime
  first, _, _ = drive.step(0)
  if accepted_first:
    drive.step(50, request=Action(runtime.session_id, 1, first.message.slcState.decisionId, first.message.slcState.presentationId, 'accept'))
  old, _, _ = drive.step(100, limit=50)
  old_id = old.message.slcState.decisionId
  document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (0, 0, 0, 5 * MPH, 0, 0, 0))
  params.put('SLCOffsetSchedule', od.to_value(document), block=True)
  assert lifecycle.refresh(NOW + 1_000_000_000)[0] is runtime
  stale = Action(runtime.session_id, 2, old_id, old.message.slcState.presentationId, 'accept')
  output, _, target = drive.step(1000, limit=50, request=stale)
  assert not output.result.errors
  assert output.message.slcState.hasPending
  assert output.message.slcState.decisionId != old_id
  assert output.message.slcState.presentationId != old.message.slcState.presentationId
  assert output.message.slcState.hasAccepted == accepted_first
  assert target == pytest.approx(50 if accepted_first else 70, rel=0, abs=1e-4)


def test_offset_edits_preserve_rejected_limits(tmp_path):
  drive = RetentionDrive(tmp_path, confirm=True)
  params = Params(str(tmp_path))
  lifecycle = SavedSlcLifecycle(params, drive.cp, {'SLC_REPLAY_RUNTIME': '1'})
  runtime, _ = lifecycle.refresh(NOW)
  assert runtime is not None
  drive.runtime = runtime
  first, _, _ = drive.step(0)
  drive.step(50, request=Action(runtime.session_id, 1, first.message.slcState.decisionId, first.message.slcState.presentationId, 'accept'))
  proposed, _, _ = drive.step(100, limit=50)
  drive.step(150, limit=50, request=Action(runtime.session_id, 2, proposed.message.slcState.decisionId, proposed.message.slcState.presentationId, 'reject'))
  rejected = runtime.state.acceptance.rejected
  document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (0, 0, 0, 5 * MPH, 0, 0, 0))
  params.put('SLCOffsetSchedule', od.to_value(document), block=True)
  assert lifecycle.refresh(NOW + 1_000_000_000)[0] is runtime
  output, _, target = drive.step(1000, limit=50)
  assert not output.message.slcState.hasPending
  assert output.result.state.acceptance.rejected == rejected
  assert output.message.slcState.acceptedSpeedLimit == pytest.approx(45 * MPH)
  assert target == pytest.approx(50, rel=0, abs=1e-4)


class RetentionDrive:
  def __init__(self, path, *, confirm=False, dashboard=False):
    params = Params(str(path))
    params.put('SLCConfirmation', int(confirm), block=True)
    for key, value in {'SpeedLimitController': True,
                       'SLCPriority1': 'Map Data', 'SLCPriority2': 'Dashboard' if dashboard else 'Map Data'}.items():
      params.put(key, value, block=True)
    self.runtime = Runtime(read_params(params), session_id='retention-drive')
    assert not self.runtime.settings.errors
    self.sm, self.cp = replay_inputs()
    car = self.sm['carState']
    car.vEgo = car.vEgoCluster = 45 * MPH
    car.vCruise = car.vCruiseCluster = 70 * MPH * 3.6
    self.sm['slcDashboardObservation'].speedMps = 70 * MPH
    self.ui = ui_fake()
    self.ui.CP = self.cp
    self.ui.sm['carState'].canValid, self.ui.sm['carState'].canTimeout = True, False
    self.ui.sm['carControl'].enabled = self.ui.sm['selfdriveState'].enabled = True
    self.ui.sm['carControl'].longActive = True
    self.adapter = RuntimeSnapshotAdapter(self.ui)
    self.planner = native.LongitudinalPlanner(self.cp, init_v=45 * MPH)

  def step(self, milliseconds, *, received=True, alive=True, status='matchedLimit', limit=45, request=None, **changes):
    now, boot = NOW + milliseconds * 1_000_000, BOOT + milliseconds * 1_000_000
    previous_stamp = self.sm.logMonoTime.get('mapdOut', BOOT)
    self.sm.advance(now)
    dashboard = self.sm['slcDashboardObservation']
    dashboard.observedMonoTime, dashboard.validUntilMonoTime = now - 1, now + 1_000_000_000
    if received:
      event = packet(speedLimit=limit * MPH, computedMonoTime=boot, gpsMonoTime=boot - 100_000_000)
      event.logMonoTime, event.mapdOut.roadStatus = boot, status
      if status in ('noGps', 'noCoverage', 'noMatch'):
        event.valid = False
        event.mapdOut.speedLimit, event.mapdOut.wayId, event.mapdOut.tileLoaded = 0., 0, False
      if status == 'noGps':
        event.mapdOut.gpsSource, event.mapdOut.gpsMonoTime, event.mapdOut.sourceGeneration = 'none', 0, 2
      if status == 'matchedNoLimit':
        event.mapdOut.speedLimit = 0.
      for field, value in changes.items():
        setattr(event.mapdOut, field, value)
      self.sm.data['mapdOut'] = event.mapdOut
      self.sm.valid['mapdOut'], self.sm.logMonoTime['mapdOut'] = bool(event.valid), boot
    else:
      self.sm.logMonoTime['mapdOut'] = previous_stamp
    self.sm.updated['mapdOut'], self.sm.alive['mapdOut'] = received, alive
    output = self.runtime.step(self.sm, self.cp, now_ns=now, now_boot_ns=boot, request=request)
    self.ui.sm.put('slcState', output.message.slcState)
    self.ui.sm['carState'].vCruiseCluster = self.sm['carState'].vCruiseCluster
    for service in ('slcState', 'carState', 'carControl', 'selfdriveState'):
      self.ui.sm.logMonoTime[service] = now
      self.ui.sm.recv_time[service] = now / 1e9
    view = self.adapter.build(ShellMode.ONROAD, now_ns=now).onroad
    with patch.object(native, 'get_cruise_accel', wraps=native.get_cruise_accel) as cruise:
      self.planner.update(self.sm, cruise_ceiling=output.result.ceiling, now_ns=now)
    return output, view, cruise.call_args.args[1] / MPH


@pytest.mark.parametrize('accept_intermediate', [False, True])
def test_rejected_limit_stays_quiet_until_a_new_limit_is_accepted(tmp_path, accept_intermediate):
  drive = RetentionDrive(tmp_path, confirm=True)

  def respond(output, sequence, kind):
    state = output.message.slcState
    assert state.hasPending
    return Action(drive.runtime.session_id, sequence, state.decisionId, state.presentationId, kind)

  first, _, _ = drive.step(0, limit=45)
  drive.step(50, limit=45, request=respond(first, 1, 'accept'))
  lower, _, _ = drive.step(100, limit=35)
  declined, _, cap = drive.step(150, limit=35, request=respond(lower, 2, 'reject'))
  assert declined.message.slcState.actionStatus == 'reject'
  assert cap == pytest.approx(45.)

  intermediate, _, _ = drive.step(200, limit=40)
  assert intermediate.message.slcState.hasPending
  if accept_intermediate:
    accepted, _, cap = drive.step(250, limit=40, request=respond(intermediate, 3, 'accept'))
    assert accepted.message.slcState.actionStatus == 'accept'
    assert cap == pytest.approx(40.)

  returned, view, cap = drive.step(300, limit=35)
  assert not returned.result.errors
  assert returned.message.slcState.hasPending == accept_intermediate
  assert resolve_unified_speed(view).posted_text == '35'
  assert cap == pytest.approx(40. if accept_intermediate else 45.)


@pytest.mark.parametrize('mode', [1, 3, 4])
@pytest.mark.parametrize('limit', [40, 50])
def test_saved_direction_controls_native_slc_adoption_above_and_below_the_previous_limit(tmp_path, mode, limit):
  drive = RetentionDrive(tmp_path, confirm=True)
  params = Params(str(tmp_path))
  params.put('SLCConfirmation', mode, block=True)
  drive.runtime, _ = SavedSlcLifecycle(params, drive.cp, {}).refresh(NOW)
  first, _, target = drive.step(0)
  assert first.message.slcState.hasPending
  assert target == pytest.approx(70, abs=1e-4)
  state = first.message.slcState
  drive.step(50, request=Action(drive.runtime.session_id, 1, state.decisionId, state.presentationId, 'accept'))
  output, _, target = drive.step(100, limit=limit)
  confirm = mode in (1, 4 if limit < 45 else 3)
  assert output.message.slcState.hasPending == confirm
  assert target == pytest.approx(45 if confirm else limit, abs=1e-4)
  assert drive.sm['carState'].vCruise == pytest.approx(70 * MPH * 3.6)


@pytest.mark.parametrize('answer', ['reject', 'timeout'])
@pytest.mark.parametrize('metric', [False, True])
@pytest.mark.parametrize('limit', [40, 50])
def test_compact_rejection_or_timeout_keeps_set_speed_until_adoption(tmp_path, answer, metric, limit):
  from openpilot.starpilot.ui.tests.test_slc_ui_actions import tap_sign, tap_control
  drive = RetentionDrive(tmp_path, confirm=True)
  params = Params(str(tmp_path))
  params.put('SLCOffsetSchedule', od.to_value(od.OffsetDocument(od.IMPERIAL_BOUNDS, tuple(value * od.MPH_TO_MPS for value in (5.0,) * 7))), block=True)
  drive.runtime = Runtime(read_params(params))
  drive.ui.is_metric = metric
  first, _, _ = drive.step(0)
  approved, _, target = drive.step(50, request=Action(drive.runtime.session_id, 1, first.message.slcState.decisionId,
                                                    first.message.slcState.presentationId, 'accept'))
  accepted = approved.result.state.acceptance.accepted
  assert target == pytest.approx(50, rel=0, abs=1e-4)

  proposed, view, target = drive.step(100, limit=limit)
  assert slc_controls(Profile.COMPACT, view) == ()
  assert [control.label for control in slc_controls(Profile.COMPACT, replace(view, slc_prompt_open=True))] == ['Accept', 'Reject']
  assert target == pytest.approx(50, rel=0, abs=1e-4)
  request = None
  if answer == 'reject':
    # Use the visible Decline button, including its normal dispatcher validation.
    emitted = []
    touch = OnroadInput(emitted.append, Profile.COMPACT)
    tap_sign(touch, view)
    tap_control(touch, view, SlcActionKind.REJECT)
    reject, = emitted
    assert reject.kind == SlcActionKind.REJECT
    request = publish_request(proposed.message.slcState, reject, NOW + 100_000_000)
  elapsed = 150 if answer == 'reject' else 30_150
  rejected, view, target = drive.step(elapsed, limit=limit, request=request)
  assert not rejected.result.errors
  assert rejected.result.acceptance.basis == ('driver_reject' if answer == 'reject' else 'timeout_reject')
  assert not rejected.message.slcState.hasPending
  candidate = rejected.result.state.acceptance.observation.candidate
  assert candidate == rejected.result.state.acceptance.rejected
  assert view.alert.size == AlertSize.NONE
  assert view.speed_limit.kind == ObservationKind.VALID and view.speed_limit.action_enabled
  assert slc_controls(Profile.COMPACT, view) == ()
  assert target == pytest.approx(50, rel=0, abs=1e-4)

  # The retired button position cannot claim a tap or adopt the rejected limit.
  emitted = []
  touch = OnroadInput(emitted.append, Profile.COMPACT)
  touch.press(315, 205, view)
  assert not touch.claimed
  touch.release(315, 205, view)
  assert not emitted
  for delay, status in ((50, 'matchedLimit'), (100, 'noCoverage'), (150, 'matchedLimit')):
    output, view, target = drive.step(elapsed + delay, limit=limit, status=status)
    assert not output.result.errors
    assert output.result.state.acceptance.accepted == accepted
    assert candidate == output.result.state.acceptance.rejected
    assert not output.message.slcState.hasPending
    controls = slc_controls(Profile.COMPACT, view)
    assert controls == ()
    assert target == pytest.approx(50, rel=0, abs=1e-4)
    assert physical_actions.pending_confirmation(drive.ui.sm, NOW + (elapsed + delay) * 1_000_000,
                                               long_active=True) is None
  # The independent adoption request retains its offset and unit handling.
  from openpilot.starpilot.ui.onroad_state import SlcUiRequest
  slc = output.message.slcState
  adopt = SlcUiRequest(SlcActionKind.ADOPT, str(slc.sessionId), int(slc.decisionId),
                       int(slc.presentationId), float(slc.speedLimit))
  request = publish_request(slc, adopt, NOW + (elapsed + 150) * 1_000_000)
  output, view, target = drive.step(elapsed + 200, limit=limit, request=request)
  assert output.message.slcState.actionStatus == 'adopt'
  assert slc_controls(Profile.COMPACT, view) == ()
  assert target == pytest.approx(limit + 5, rel=0, abs=1e-4)
  # A different candidate still gets the normal two-choice confirmation.
  output, view, target = drive.step(elapsed + 250, limit=55)
  assert output.message.slcState.hasPending
  controls = slc_controls(Profile.COMPACT, replace(view, slc_prompt_open=True))
  assert [control.request.kind for control in controls] == [SlcActionKind.ACCEPT, SlcActionKind.REJECT]
  assert target == pytest.approx(limit + 5, rel=0, abs=1e-4)


@pytest.mark.parametrize('raw', [None, b'0', b'1', b'2', b'broken'])
def test_retired_fallback_cannot_reset_session_or_change_set_speed_sign_and_recovery(tmp_path, raw):
  drive = RetentionDrive(tmp_path)
  params = Params(str(tmp_path))
  path = Path(params.get_param_path('SLCFallback'))
  lifecycle = SavedSlcLifecycle(params, drive.cp, {'SLC_REPLAY_RUNTIME': '1'})
  drive.runtime, _ = lifecycle.refresh(NOW)
  runtime = drive.runtime
  drive.step(0)
  session = runtime.session_id
  from openpilot.starpilot.speed_limits.runtime import CruiseEvent
  stamp = NOW + 50_000_000
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 50 * MPH * 3.6
  drive.sm.advance(stamp)
  runtime.step(drive.sm, drive.cp, now_ns=stamp, now_boot_ns=BOOT+50_000_000,
    cruise_event=CruiseEvent(1, stamp, 70*MPH, 50*MPH, 'decel', 'card-producer', stamp))
  if raw is not None:
    path.write_bytes(raw)
  refreshed, changed = lifecycle.refresh(NOW+1_000_000_000)
  assert not changed and refreshed is runtime and runtime.session_id == session
  _, view, target = drive.step(1000, status='noCoverage')
  shown = resolve_unified_speed(view)
  assert target == pytest.approx(45, abs=1e-4)
  assert (shown.max_text, shown.posted_text, shown.source, shown.status) == ('50', '45', 'map', 'Using speed limit')
  assert shown.limit_in_use and not shown.show_last
  assert not view.speed_limit.driver_override_active
  recovered, view, target = drive.step(1100)
  assert not view.speed_limit.driver_override_active
  assert target == pytest.approx(45, abs=1e-4)
  assert (path.read_bytes() if path.exists() else None) == raw


@pytest.mark.parametrize('loss', ['noCoverage', 'noMatch', 'noGps', 'matchedNoLimit', 'missingTile',
                                 'lookahead', 'stopped', 'deadTransport', 'externalGpsStale'])
def test_maps_loss_keeps_accepted_target_display_and_control(tmp_path, loss):
  drive = RetentionDrive(tmp_path)
  drive.step(0)
  changes = {'status': loss}
  if loss in ('stopped', 'deadTransport'):
    changes = {'received': False, 'alive': loss == 'stopped'}
  elif loss == 'missingTile':
    changes = {'tileLoaded': False}
  elif loss == 'lookahead':
    changes = {'waySelectionType': 'possible'}
  elif loss == 'externalGpsStale':
    changes = {'gpsMonoTime': BOOT - 300_000_000}
  output, view, target = drive.step(251, **changes)
  assert target == pytest.approx(45, rel=0, abs=1e-4)
  assert output.message.slcState.acceptedSpeedLimit == pytest.approx(45 * MPH)
  assert output.message.slcState.hasCeiling
  assert output.message.slcState.hasAcceptedAdjustedLimit
  assert output.message.slcState.acceptedAdjustedLimit == pytest.approx(45 * MPH)
  assert view.speed_limit.kind != ObservationKind.VALID
  shown = resolve_unified_speed(view)
  assert (shown.max_text, shown.posted_text, shown.status) == ('70', '45', 'Using speed limit')
  assert shown.limit_in_use and not shown.show_last
  assert all(slc_controls(profile, view) == () for profile in Profile)
  assert not output.message.slcState.hasPending


@pytest.mark.parametrize('answer', ['accept', 'reject', 'timeout'])
@pytest.mark.parametrize('limit', [40, 50])
def test_long_gap_confirmation_and_recovery_keep_the_accepted_limit(tmp_path, answer, limit):
  drive = RetentionDrive(tmp_path, confirm=True)
  first, _, _ = drive.step(0)
  assert first.message.slcState.hasPending and not first.message.slcState.hasAccepted
  drive.step(50, request=Action(drive.runtime.session_id, 1, first.message.slcState.decisionId, first.message.slcState.presentationId, 'accept'))
  _, _, target = drive.step(100, status='noCoverage')
  assert target == pytest.approx(45, rel=0, abs=1e-4)
  _, view, target = drive.step(35_000, received=False)
  assert target == pytest.approx(45, rel=0, abs=1e-4)
  assert resolve_unified_speed(view).status == 'Using speed limit'
  pending, _, target = drive.step(40_000, limit=limit, wayId=124)
  assert target == pytest.approx(45, rel=0, abs=1e-4) and pending.message.slcState.hasPending
  action = None if answer == 'timeout' else Action(drive.runtime.session_id, 2, pending.message.slcState.decisionId,
                                                 pending.message.slcState.presentationId, answer)
  drive.step(71_000 if answer == 'timeout' else 40_050, limit=limit, wayId=124, request=action)
  output, view, target = drive.step(300_000, received=False)
  assert target == pytest.approx(limit if answer == 'accept' else 45, rel=0, abs=1e-4)
  assert not output.message.slcState.hasPending
  shown = resolve_unified_speed(view)
  assert shown.posted_text == str(limit if answer == 'accept' else 45)
  assert shown.limit_in_use and not shown.show_last
  assert view.speed_limit.last_received_speed_limit_mps == pytest.approx(limit * MPH)
  # A dead SLC publisher cannot make the widget claim retained control indefinitely.
  drive.ui.sm.alive['slcState'] = False
  expired = drive.adapter.build(ShellMode.ONROAD, now_ns=NOW + 300_001_000_000).onroad
  assert resolve_unified_speed(expired).active_side == 'max'
  assert resolve_unified_speed(expired).posted_text == '–'


def test_unchanged_map_speed_keeps_history_and_returned_accepted_limit_clears_prompt(tmp_path):
  drive = RetentionDrive(tmp_path, confirm=True)
  first, _, _ = drive.step(0)
  accepted, _, _ = drive.step(50, request=Action(drive.runtime.session_id, 1, first.message.slcState.decisionId,
                                               first.message.slcState.presentationId, 'accept'))
  unchanged, _, target = drive.step(100, wayId=124)
  assert target == pytest.approx(45, rel=0, abs=1e-4)
  assert not unchanged.message.slcState.hasPending
  assert unchanged.result.acceptance.history_write is None
  assert unchanged.result.acceptance.state.accepted == accepted.result.acceptance.state.accepted
  pending, _, _ = drive.step(150, limit=50, wayId=124)
  assert pending.message.slcState.hasPending
  recovered, _, target = drive.step(200)
  assert target == pytest.approx(45, rel=0, abs=1e-4)
  assert not recovered.message.slcState.hasPending
  assert recovered.result.acceptance.history_write is None
  assert recovered.message.slcState.acceptedSpeedLimit == pytest.approx(45 * MPH)
  assert recovered.message.slcState.decisionId != pending.message.slcState.decisionId
  assert recovered.message.slcState.presentationId != pending.message.slcState.presentationId
  assert not drive.step(250)[0].result.errors


def test_returned_map_limit_still_requires_confirmation_when_set_is_unaligned(tmp_path):
  drive = RetentionDrive(tmp_path, confirm=True)
  first, _, _ = drive.step(0)
  pending, _, _ = drive.step(50, limit=50, wayId=124)
  returned, _, target = drive.step(100)
  assert first.message.slcState.hasPending and pending.message.slcState.hasPending
  assert returned.message.slcState.hasPending and not returned.message.slcState.hasAccepted
  assert returned.result.acceptance.history_write is None
  assert returned.message.slcState.decisionId != pending.message.slcState.decisionId
  assert target == pytest.approx(70, rel=0, abs=1e-4)


@pytest.mark.parametrize('offset', [0, 5, 10])
def test_retention_respects_lower_max_and_reset(tmp_path, offset):
  drive = RetentionDrive(tmp_path / 'max')
  params = Params(str(tmp_path / 'max'))
  document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (0, 0, 0, offset * MPH, 0, 0, 0))
  params.put('SLCOffsetSchedule', od.to_value(document), block=True)
  drive.runtime = Runtime(read_params(params))
  drive.step(0)
  drive.sm['carState'].vCruise = drive.sm['carState'].vCruiseCluster = 40 * MPH * 3.6
  _, before, _ = drive.step(50)
  _, view, target = drive.step(350, received=False)
  assert target == pytest.approx(40, rel=0, abs=1e-4)
  shown = resolve_unified_speed(view)
  assert view.speed_limit.kind != ObservationKind.VALID and shown.posted_retained
  assert shown.status == 'Using set speed'
  assert shown.set_limited and not shown.show_last
  assert replace(shown, posted_retained=False) == resolve_unified_speed(before)
  _, recovered, target = drive.step(400)
  assert recovered.speed_limit.sign_recovering
  assert resolve_unified_speed(recovered) == resolve_unified_speed(before)
  assert target == pytest.approx(40, rel=0, abs=1e-4)
  drive.runtime.reset()
  output, _, target = drive.step(450, received=False)
  assert output.message.slcState.hasAccepted
  assert output.result.ceiling.speed_mps == pytest.approx((45 + offset) * MPH)
  assert target == pytest.approx(40, rel=0, abs=1e-4)


@pytest.mark.parametrize('confirm', [False, True])
@pytest.mark.parametrize('limit', [35, 55])
def test_secondary_source_is_adopted_or_offered_on_the_first_available_update(tmp_path, confirm, limit):
  drive = RetentionDrive(tmp_path, confirm=confirm, dashboard=True)
  first, _, _ = drive.step(0)
  drive.step(50, request=Action(drive.runtime.session_id, 1, first.message.slcState.decisionId,
                              first.message.slcState.presentationId, 'accept') if confirm else None)
  drive.sm['slcDashboardObservation'].speedMps = limit * MPH
  output, _, target = drive.step(100, status='noCoverage')
  state = output.message.slcState
  assert not output.result.errors
  assert state.hasPending == confirm
  assert target == pytest.approx(45 if confirm else limit, rel=0, abs=1e-4)
  assert state.acceptedSource == ('map' if confirm else 'dashboard')
  assert drive.sm['carState'].vCruise == pytest.approx(70 * MPH * 3.6)
  if confirm:
    assert state.pendingSource == 'dashboard'
    assert state.pendingSinceMonoTime == NOW + 100_000_000
    assert drive.step(30_099, status='noCoverage')[0].message.slcState.hasPending
    expired, _, target = drive.step(30_100, status='noCoverage')
    assert not expired.message.slcState.hasPending
    assert expired.result.acceptance.basis == 'timeout_reject'
    assert target == pytest.approx(45, rel=0, abs=1e-4)


@pytest.mark.parametrize('profile', list(Profile))
@pytest.mark.parametrize('kind', [SlcActionKind.ACCEPT, SlcActionKind.REJECT])
def test_confirmation_press_survives_repeated_source_gaps_and_updates_the_native_cap(tmp_path, profile, kind):
  from openpilot.starpilot.ui.tests.test_slc_ui_actions import tap_sign
  drive = RetentionDrive(tmp_path, confirm=True)
  first, _, _ = drive.step(0)
  drive.step(50, request=Action(drive.runtime.session_id, 1, first.message.slcState.decisionId,
                              first.message.slcState.presentationId, 'accept'))
  proposed, view, _ = drive.step(100, limit=35)
  original = proposed.message.slcState
  emitted = []
  touch = OnroadInput(emitted.append, profile)
  tap_sign(touch, view)
  for milliseconds, missing in ((600, True), (1100, False), (1600, True), (2100, False)):
    output, view, target = drive.step(milliseconds, limit=35, status='noCoverage' if missing else 'matchedLimit')
    state = output.message.slcState
    assert state.hasPending and state.decisionId == original.decisionId
    assert state.presentationId == original.presentationId
    assert state.pendingSinceMonoTime == original.pendingSinceMonoTime
    assert target == pytest.approx(45, abs=1e-4)
    assert touch.present(view).slc_prompt_open
    assert all(control.enabled for control in slc_controls(profile, touch.present(view)))
    assert resolve_unified_speed(view, confirmation=True).status == 'Confirm speed limit'
  control = next(item for item in slc_controls(profile, touch.present(view)) if item.request.kind == kind)
  left, top, right, bottom = control.bounds
  x, y = (left + right) / 2, (top + bottom) / 2
  touch.press(x, y, view)
  lost, view, _ = drive.step(2600, status='noCoverage')
  touch.release(x, y, view)
  assert len(emitted) == 1 and emitted[0].kind == kind
  request = publish_request(lost.message.slcState, emitted[0], NOW + 2_610_000_000)
  output, _, target = drive.step(2650, status='noCoverage', request=request)
  assert not output.result.errors and output.message.slcState.actionStatus == kind.value
  assert not output.message.slcState.hasPending
  assert output.message.slcState.observationKind != 'valid'
  assert target == pytest.approx(35 if kind == SlcActionKind.ACCEPT else 45, abs=1e-4)
  assert drive.sm['carState'].vCruise == pytest.approx(70 * MPH * 3.6)
  recovered, _, _ = drive.step(2700, limit=35)
  assert not recovered.message.slcState.hasPending


def test_compact_sign_keeps_accepted_value_without_pulsing_on_recovery(tmp_path):
  drive = RetentionDrive(tmp_path)
  _, before, _ = drive.step(0)
  _, held, _ = drive.step(300, received=False)
  _, recovered, _ = drive.step(400)
  fonts = Mock()
  fonts.measure.return_value = Mock(width=20, height=20)
  fonts.vertical_ink.return_value = (0, 20)
  fonts.horizontal_ink.return_value = (0, 20)
  renderer = CompactHudRenderer(fonts, Path('/unused'))
  with patch('openpilot.starpilot.ui.speed_limit_shapes.SpeedLimitShapes.outline'):
    for view in (before, held, recovered):
      renderer._speed_limit_sign(replace(view, appearance=replace(view.appearance, show_speed_limit_sign=True)))
  assert any(call.args[0] == '45' for call in fonts.draw.call_args_list)
  assert renderer._speed_limit_widget.pulse.start_time == -float('inf')
