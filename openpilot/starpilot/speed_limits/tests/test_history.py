"""The fixed previous-limit fallback survives restarts without inventing source evidence."""

import json
from dataclasses import replace

import pytest

from openpilot.common.params import Params
from openpilot.selfdrive.controls.plannerd import SavedSlcLifecycle
from openpilot.starpilot.speed_limits import acceptance as acc, history
from openpilot.starpilot.speed_limits.runtime import Action
from openpilot.starpilot.ui.tests.test_slc_retention import MPH, NOW, RetentionDrive
from openpilot.starpilot.ui.unified_speed_presentation import resolve_unified_speed


@pytest.mark.parametrize('source', ['dashboard', 'map', 'vision'])
def test_saved_previous_limit_caps_native_planner_after_restart_without_a_current_source(tmp_path, source):
  drive = RetentionDrive(tmp_path)
  accepted, _, _ = drive.step(0)
  previous = accepted.result.state.acceptance.accepted
  previous = replace(previous, candidate=replace(previous.candidate, source=source))
  params = Params(str(tmp_path))
  params.put('SLCQualifiedHistory', json.loads(history.encode(previous, 1)), block=True)
  params.put('SLCConfirmation', 1, block=True)
  lifecycle = SavedSlcLifecycle(params, drive.cp, {})
  drive.runtime, _ = lifecycle.refresh(NOW)
  drive.sm['slcDashboardObservation'].status = 'unknown'
  output, view, target = drive.step(1000, received=False)
  assert not output.result.errors
  assert target == pytest.approx(45, abs=1e-4)
  assert output.message.slcState.observationKind != 'valid'
  assert output.message.slcState.presentationId == 0
  assert output.message.slcState.acceptedMonoTime == 0
  assert output.message.slcState.hasCeiling and not output.message.slcState.hasPending
  shown = resolve_unified_speed(view)
  assert (shown.posted_text, shown.status) == ('45', 'Using speed limit')
  assert shown.source == source

  proposed, _, target = drive.step(1050, limit=50)
  assert proposed.message.slcState.hasPending
  assert target == pytest.approx(45, abs=1e-4)
  state = proposed.message.slcState
  action = Action(drive.runtime.session_id, 1, state.decisionId, state.presentationId, 'accept')
  adopted, _, target = drive.step(1100, limit=50, request=action)
  assert not adopted.message.slcState.hasPending
  assert target == pytest.approx(50, abs=1e-4)
  assert drive.sm['carState'].vCruise == pytest.approx(70 * MPH * 3.6)


@pytest.mark.parametrize('field,value', [
  ('source_schema', 'wrong'), ('version', True), ('version', 2), ('saved_wall_ns', 0),
  ('source', 'online'), ('source', 'unknown'), ('speed_mps', 0), ('speed_mps', -1),
  ('speed_mps', True), ('speed_mps', float('nan')), ('speed_mps', float('inf')),
  ('identity_kind', 'invalid'), ('identity_value', ''), ('origin_session', ' '),
])
def test_invalid_saved_previous_limit_cannot_authorize_a_cap(field, value):
  candidate = acc.Candidate('dashboard', acc.ObservationIdentity(acc.IdentityKind.PRODUCER_EPISODE, 'card:1'), 20.)
  saved = json.loads(history.encode(acc.AcceptedLimit(candidate, 'drive', 1), 1))
  saved[field] = value
  assert history.decode(saved, session_id='next-drive') is None


@pytest.mark.parametrize('raw', [None, b'', b'{', b'null', b'[]', b'{}', b'[' * 1500 + b']' * 1500])
def test_unreadable_previous_limit_is_absent(raw):
  assert history.decode(raw, session_id='next-drive') is None


def test_oversized_saved_previous_limit_does_not_stop_runtime_startup(tmp_path):
  drive = RetentionDrive(tmp_path)
  params = Params(str(tmp_path))
  with open(params.get_param_path('SLCQualifiedHistory'), 'wb') as stream:
    stream.write(b' ' * (history.MAX_BYTES + 1))
  drive.runtime, _ = SavedSlcLifecycle(params, drive.cp, {}).refresh(NOW)
  output, _, target = drive.step(1000, received=False)
  assert not output.result.errors
  assert not output.message.slcState.hasCeiling
  assert target == pytest.approx(70, abs=1e-4)
