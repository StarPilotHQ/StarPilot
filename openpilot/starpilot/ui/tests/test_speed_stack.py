"""Regression checks for qualified values, saved geometry and request feedback."""
from dataclasses import replace
from unittest.mock import Mock

from openpilot.starpilot.ui.onroad_state import ObservationKind, OnroadInput, OnroadState, SlcActionKind, SlcUiRequest, SpeedLimitObservation
from openpilot.starpilot.ui.onroad_customization import default_document, validate_document, rgba
from openpilot.starpilot.ui.slc_action_dispatch import SlcActionDispatcher
from openpilot.starpilot.ui.tests.test_slc_ui_actions import native_state, NOW
from openpilot.starpilot.speed_limits.speed_domain import adjusted_limit, OffsetBand, OffsetSchedule
from openpilot.starpilot.speed_limits.runtime import Runtime
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.ui.tests.test_slc_ui_runtime import START, replay_inputs, publish_request


def road(document=None):
  obs = SpeedLimitObservation(ObservationKind.VALID, 'dashboard', 20, accepted_speed_limit_mps=20,
                             accepted_adjusted_limit_mps=22, session_id='drive-session', presentation_id=9,
                             decision_id=7, action_enabled=True)
  return OnroadState(True, False, 20, 100, obs, longitudinal_active=True, slc_system_long_available=True,
                     customization=document or default_document())


def test_offset_proposals_use_their_own_band_and_reject_invalid_values():
  schedule = OffsetSchedule((OffsetBand(0, 20, 1), OffsetBand(20, None, 3)))
  assert adjusted_limit(19, schedule) == 20
  assert adjusted_limit(20, schedule) == 23
  assert adjusted_limit(float('nan'), schedule) is None
  assert adjusted_limit(10, OffsetSchedule((OffsetBand(0, None, -20),))) == 10


def test_numeric_card_and_sources_row_are_not_tap_targets_while_pending():
  state = road()
  state = replace(state, speed_limit=replace(state.speed_limit, pending_speed_limit_mps=25))
  emitted = []
  touch = OnroadInput(emitted.append)
  touch.press(120, 320, state)
  touch.release(120, 320, state)
  assert not emitted
  # Where the removed "View sources" row used to sit; sources now follow the Galaxy setting only.
  touch.press(100, 709, state)
  assert not touch.claimed
  touch.release(100, 709, state)
  assert not emitted


def test_v4_migration_preserves_settings_and_clamps_only_enlarged_widgets():
  doc = default_document()
  doc['version'] = 4
  doc['palette']['cardFill'] = '#12345678'
  doc['speedSources'] = True
  doc['layouts']['large']['cruise_limits'].update(x=1654, y=75)
  doc['layouts']['large']['speed_limit_actions'].update(x=1654, y=500)
  old_speed = dict(doc['layouts']['large']['current_speed'])
  migrated = validate_document(doc)
  assert migrated['version'] == 14
  assert migrated['layouts']['large']['cruise_limits']['x'] == 1437
  assert migrated['layouts']['large']['speed_limit_actions'] == {'x': 1678, 'y': 267, 'enabled': True}
  assert migrated['layouts']['large']['current_speed'] == old_speed
  assert migrated['speedSources'] is True
  assert rgba(migrated, 'cardFill', 'large', 'cruise_limits') == (18, 52, 86, 120)
  assert validate_document(migrated) == migrated


def test_feedback_requires_matching_receipts():
  now = [NOW]
  dispatcher = SlcActionDispatcher(Mock(), lambda _: native_state(), lambda: now[0])
  assert dispatcher.dispatch(SlcUiRequest(SlcActionKind.ACCEPT, 'drive-session', 7, 9, 22))
  obs = road().speed_limit
  assert dispatcher.feedback(obs) == 'Waiting…'
  obs = replace(obs, action_sequence_id=NOW, action_status='accept')
  assert dispatcher.feedback(obs) == 'Limit accepted'
  assert dispatcher.feedback(replace(obs, session_id='next-drive')) == ''


def test_publisher_receipt_survives_next_cycle_and_resets_with_session():
  sm, cp = replay_inputs()
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}), session_id='drive')
  pending = runtime.step(sm, cp, now_ns=START).message.slcState
  request = SlcUiRequest(SlcActionKind.ACCEPT, 'drive', pending.decisionId, pending.presentationId, pending.pendingSpeedLimit)
  action = publish_request(pending, request, START)
  sm.advance(START + 50_000_000)
  accepted = runtime.step(sm, cp, now_ns=START + 50_000_000, request=action).message.slcState
  sm.advance(START + 100_000_000)
  next_frame = runtime.step(sm, cp, now_ns=START + 100_000_000).message.slcState
  assert accepted.actionSequenceId == next_frame.actionSequenceId == action.sequence_id
  assert accepted.actionStatus == next_frame.actionStatus == 'accept'
  assert accepted.commandActionSequenceId == next_frame.commandActionSequenceId == 0
  runtime.reset()
  assert runtime._ui_receipt is None

