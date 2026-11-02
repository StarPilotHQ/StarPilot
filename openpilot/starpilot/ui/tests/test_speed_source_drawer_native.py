"""Same-cycle native source readings and UI-only drawer intent boundaries."""
from unittest.mock import Mock
from openpilot.starpilot.ui.presentation import BitmapFonts


from dataclasses import replace
from types import SimpleNamespace
import unittest
import pytest
from unittest.mock import patch
from openpilot.starpilot.ui.raylib import rl
from openpilot.cereal import log, messaging
from openpilot.starpilot.speed_limits.runtime import Runtime
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.ui.tests.test_slc_ui_runtime import START, replay_inputs
from openpilot.starpilot.ui.onroad_state import OnroadState, OnroadInput, SpeedLimitObservation, ObservationKind, speed_limit_from_message
from openpilot.starpilot.ui.presentation import Profile, FontRole
from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
from openpilot.starpilot.ui.runtime_snapshot import display_message
from openpilot.starpilot.ui.onroad_customization import default_document, validate_document
from openpilot.starpilot.ui import clip


class DrawerNativeTest(unittest.TestCase):
  def test_qualified_source_rows_roundtrip_and_do_not_change_selection(self):
    sm, cp = replay_inputs()
    runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCPriority2': 'Map Data'}),
                      session_id='drawer-native')
    out = runtime.step(sm, cp, now_ns=START).message
    with log.Event.from_bytes(out.to_bytes()) as decoded:
      obs = speed_limit_from_message(decoded.slcState)
      rows = {row.source: row for row in obs.source_readings}
      self.assertEqual(rows['dashboard'].kind, 'valid')
      assert rows['dashboard'].speed_mps is not None
      self.assertAlmostEqual(rows['dashboard'].speed_mps, 25)
      self.assertTrue(rows['dashboard'].enabled)
      self.assertTrue(rows['map'].enabled)
      self.assertEqual(rows['map'].kind, 'stale')
      self.assertIsNone(rows['map'].speed_mps)
      self.assertFalse(rows['online'].enabled)
      self.assertEqual(obs.source, 'dashboard')
      self.assertEqual(decoded.slcState.sourceReadings[0].source, 'dashboard')
    self.assertEqual(speed_limit_from_message(None).source_readings, ())
    empty = messaging.new_message('slcState')
    self.assertEqual(len(empty.slcState.sourceReadings), 0)

  def test_sources_have_no_onscreen_tap_target(self):
    obs = SpeedLimitObservation(ObservationKind.VALID, 'dashboard', 25, session_id='drawer', presentation_id=1)
    state = OnroadState(False, False, 20, 100, obs)
    doc = default_document()
    doc['speedSources'] = True
    state = replace(state, customization=doc)
    emitted = []
    touch = OnroadInput(emitted.append, Profile.LARGE)
    from openpilot.starpilot.ui.large_speed_geometry import CARD_HEIGHT
    card = doc['layouts']['large']['cruise_limits']
    left, top = card['x'], card['y'] + CARD_HEIGHT + 22
    # The sources panel follows the Galaxy layout setting only; the old toggle row is not a touch target.
    touch.press(left + 12, top + 12, state)
    self.assertFalse(touch.claimed)
    touch.release(left + 12, top + 12, state)
    pending = replace(state, speed_limit=replace(obs, pending_speed_limit_mps=30, decision_id=2))
    touch.press(left + 12, top + 12, pending)
    touch.release(left + 12, top + 12, pending)
    self.assertEqual(emitted, [])

  def test_nested_drawer_clip_restores_parent_after_exception(self):
    calls = []
    with patch.object(rl, 'rl_draw_render_batch_active'), patch.object(rl, 'begin_scissor_mode', side_effect=lambda *v: calls.append(v)):
      with self.assertRaises(ValueError):
        with clip.clipped(rl.Rectangle(264, 271, 248, 215), rl.Rectangle(30, 30, 1800, 1020)):
          raise ValueError('render failed')
    self.assertEqual(calls, [(264, 271, 248, 215), (30, 30, 1800, 1020)])

  def test_saved_document_flag_is_optional_strict_and_preserved(self):
    doc = default_document()
    self.assertNotIn('speedSources', validate_document(doc))
    doc['speedSources'] = True
    self.assertIs(validate_document(doc)['speedSources'], True)
    doc['speedSourcesCard'] = True
    self.assertIs(validate_document(doc)['speedSourcesCard'], True)
    for key in ('speedSources', 'speedSourcesCard'):
      with self.assertRaises(ValueError):
        validate_document({**doc, key: 1})
    self.assertNotIn('speedAlign', validate_document(default_document()))  # Left, the original look, by default.
    for align in ('left', 'center', 'right'):
      self.assertEqual(validate_document({**doc, 'speedAlign': align})['speedAlign'], align)
    for bad in ('justify', 1, None):
      with self.assertRaises(ValueError):
        validate_document({**doc, 'speedAlign': bad})

  def test_sources_grow_the_cruise_widget_by_one_row_or_an_attached_card(self):
    from openpilot.starpilot.ui.large_speed_geometry import CARD_HEIGHT, GAP, SOURCE_ROW_HEIGHT, SOURCE_TOP, WIDTH
    from openpilot.starpilot.ui.onroad_customization import customization_metadata, widget_size
    doc = default_document()
    self.assertEqual(widget_size(doc, 'large', 'cruise_limits'), (WIDTH, CARD_HEIGHT))
    doc['speedSources'] = True
    self.assertEqual(widget_size(doc, 'large', 'cruise_limits'), (WIDTH, SOURCE_TOP + SOURCE_ROW_HEIGHT))
    doc['speedSourcesCard'] = True
    self.assertEqual(widget_size(doc, 'large', 'cruise_limits'), (WIDTH, SOURCE_TOP + GAP + SOURCE_ROW_HEIGHT))
    self.assertEqual(customization_metadata()['profiles']['large']['widgets']['cruise_limits']['sourcesExtent'],
                     {'inline': SOURCE_TOP - CARD_HEIGHT + SOURCE_ROW_HEIGHT, 'card': SOURCE_TOP - CARD_HEIGHT + GAP + SOURCE_ROW_HEIGHT, 'actionGap': GAP})

  def test_expired_producer_and_old_drive_transport_cannot_supply_readings(self):
    sm, cp = replay_inputs()
    runtime = Runtime(parse({'SpeedLimitController': True}), session_id='drawer-expiry')
    runtime.step(sm, cp, now_ns=START)
    expired = runtime.step(sm, cp, now_ns=START + 6_000_000_000).message.slcState
    rows = {str(row.source): row for row in expired.sourceReadings}
    self.assertNotEqual(str(rows['dashboard'].observationKind), 'valid')
    fresh = SimpleNamespace(logMonoTime={'slcState': START}, alive={'slcState': True}, valid={'slcState': True}, recv_frame={'slcState': 2})

    # A dict-backed transport exposes the same freshness fields as SubMaster.
    class Transport(dict):
      pass

    transport = Transport(slcState=expired)
    for key in ('logMonoTime', 'alive', 'valid', 'recv_frame'):
      setattr(transport, key, getattr(fresh, key))
    self.assertIsNone(display_message(transport, 'slcState', START + 200_000_001))
    self.assertIsNone(display_message(transport, 'slcState', START, after_frame=2))
    self.assertEqual(speed_limit_from_message(None).source_readings, ())

  def test_disabled_and_invalid_rows_have_no_value_or_ui_authority(self):
    sm, cp = replay_inputs()
    runtime = Runtime(parse({'ShowSpeedLimits': False}), session_id='drawer-disabled')
    message = runtime.step(sm, cp, now_ns=START).message.slcState
    self.assertFalse(message.enabled)
    self.assertTrue(all(not row.enabled for row in message.sourceReadings))
    message.observationKind = 'valid'
    message.speedLimit = 25
    message.sessionId = 'diagnostic'
    row = message.sourceReadings[0]
    row.observationKind = 'valid'
    row.speedLimit = float('nan')
    rows = speed_limit_from_message(message).source_readings
    self.assertEqual(rows[0].kind, 'unknown')
    self.assertIsNone(rows[0].speed_mps)
    row.observationKind = 'unknown'
    row.speedLimit = 99
    self.assertIsNone(speed_limit_from_message(message).source_readings[0].speed_mps)

  def test_accepted_source_is_highlighted_instead_of_selected_equal_value_source(self):
    from openpilot.starpilot.ui.onroad_state import SourceReading
    from openpilot.starpilot.ui.tests.test_unified_speed_native import foreground_calls

    obs = SpeedLimitObservation(
      ObservationKind.VALID,
      'vision',
      25,
      accepted_source='dashboard',
      source_readings=(SourceReading('dashboard', True, 'valid', 25), SourceReading('vision', True, 'valid', 25)),
    )
    state = OnroadState(False, False, 20, 100, obs)
    fonts = SimpleNamespace(profile=Profile.LARGE, measure=lambda *args: SimpleNamespace(width=20, height=20), draw=Mock())
    widget = UnifiedSpeedWidget(Mock(spec=BitmapFonts, **vars(fonts)))
    widget._draw_sources(rl.Rectangle(88, 545, 320, 76), state)
    drawn = [(call.args[0], call.args[1]) for call in foreground_calls(fonts.draw)]
    self.assertEqual([text for text, _ in drawn], ['Dash', '56', 'Vision', '56'])
    self.assertIn(('56', FontRole.MEDIUM), drawn)  # The accepted source's reading is emphasized.
    self.assertIn(('56', FontRole.NORMAL), drawn)


@pytest.fixture(autouse=True)
def mock_sign_graphics(monkeypatch):
  monkeypatch.setattr('openpilot.starpilot.ui.speed_limit_shapes.SpeedLimitShapes.outline', Mock())
  monkeypatch.setattr('openpilot.starpilot.ui.speed_limit_shapes.SpeedLimitShapes.confirmation', Mock())
  for name in ('draw_rectangle_rounded_lines_ex', 'draw_circle', 'draw_ring'):
    monkeypatch.setattr('openpilot.starpilot.ui.speed_limit_widget.rl.' + name, Mock())
