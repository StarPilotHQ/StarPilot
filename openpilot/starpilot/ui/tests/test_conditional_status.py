"""Saved family and actual selfdrived acknowledgment remain distinct in C3 UI."""

from typing import Any
import json
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.conditional_mode.consumer import ConsumerResult
from openpilot.starpilot.conditional_mode.effective_status import publish_ack
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.preferences import SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.tests.test_effective_status import NOW, SESSION, proposal
from openpilot.starpilot.ui.conditional_status import ConditionalDisplayProjector, configured_choice
from openpilot.starpilot.ui.onroad_conditional import status as visual_status
from openpilot.starpilot.ui.onroad_compact_widgets import MiciSidebarWidgets
from openpilot.starpilot.ui.home_state import HomeMode
from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import ui_fake


DRIVE = NOW - 1_000_000_000
SOURCE = NOW - 1_000_000


def acknowledged(*, sequence=1, accepted=True, experimental=True, session=SESSION, observed_ns=NOW):
  event = publish_ack(session=session, sequence=sequence, observed_ns=observed_ns, selfdrive_state_ns=SOURCE,
                      drive_id=DRIVE, effective_experimental=experimental,
                      result=ConsumerResult(experimental, accepted, 'proposed' if accepted else 'unavailable'),
                      accepted_proposal=proposal())
  return messaging.log_from_bytes(event.to_bytes()).starpilotSelfdriveState


class TestConditionalStatus(unittest.TestCase):
  def test_factory_choice_and_saved_stock_remain_distinct(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      path = params.get_param_path('ConditionalModeConfig')
      self.assertIs(configured_choice(params), ModeChoice.CEM)
      self.assertIsNone(params.get('ConditionalModeConfig'))
      params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.STOCK))), block=True)
      self.assertIs(configured_choice(params), ModeChoice.STOCK)
      with open(path, 'wb') as stream:
        stream.write(b'{invalid')
      self.assertIsNone(configured_choice(params))

  def test_pure_join_rejects_wrong_axis_mode_replay_and_old_session(self):
    projector = ConditionalDisplayProjector()
    def project(state, *, now_ns: int = NOW, event_ns: int = NOW, selfdrive_ns: int = SOURCE,
                selfdrive_experimental: bool = True, selfdrive_enabled: bool = True,
                long_active: bool = True, car_valid: bool = True, system_long: bool = True):
      return projector.project(state, now_ns=now_ns, event_ns=event_ns, selfdrive_ns=selfdrive_ns,
                               drive_id=DRIVE, selfdrive_experimental=selfdrive_experimental,
                               selfdrive_enabled=selfdrive_enabled, long_active=long_active,
                               car_valid=car_valid, system_long=system_long)
    first = acknowledged()
    display = project(first)
    self.assertIsNotNone(display)
    assert display is not None
    self.assertEqual(display.reason, 'cem_speed')
    self.assertIsNotNone(project(first, selfdrive_ns=SOURCE - 1))
    self.assertIsNone(project(first, selfdrive_ns=SOURCE - 50_000_001))
    self.assertIsNone(project(first, selfdrive_experimental=False))
    self.assertIsNone(project(first, long_active=False))
    self.assertIsNone(project(acknowledged(sequence=2, accepted=False, experimental=False,
                                           observed_ns=NOW + 1_000_000), now_ns=NOW + 1_000_000,
                              event_ns=NOW + 1_000_000, selfdrive_experimental=False))
    self.assertIsNone(project(first))  # Old accepted reason cannot reappear.
    self.assertIsNone(project(acknowledged(sequence=1, session='d' * 32)))
    self.assertIsNone(project(first))  # Retired producer session.

  def test_display_frame_budget_does_not_extend_control_receipt(self):
    from openpilot.starpilot.conditional_mode.effective_status import observation
    state = acknowledged()
    arguments: dict[str, Any] = {'event_ns': NOW, 'selfdrive_ns': SOURCE, 'drive_id': DRIVE,
                 'selfdrive_experimental': True, 'selfdrive_enabled': True,
                 'long_active': True, 'car_valid': True, 'system_long': True}
    projector = ConditionalDisplayProjector()
    for elapsed in (0, 60_000_000, 120_000_000, 198_000_000):
      self.assertIsNotNone(projector.project(state, now_ns=NOW + elapsed, **arguments))
    self.assertIsNone(observation(state, NOW + 60_000_000))
    self.assertIsNone(projector.project(state, now_ns=NOW + 201_000_000, **arguments))
    self.assertIsNone(projector.project(state, now_ns=NOW - 1, **arguments))
    self.assertIsNone(projector.project(state, now_ns=NOW, **{**arguments, 'selfdrive_experimental': False}))

  def test_actual_snapshot_saved_gradient_and_same_frame_ack(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CEM))), block=True)
      ui = ui_fake()
      for service in ui.sm.logMonoTime:
        ui.sm.logMonoTime[service] = NOW
      ui.params = params
      ui.CP = type('CP', (), {'openpilotLongitudinalControl': True, 'passive': False, 'pcmCruise': True, 'carFingerprint': 'OTHER'})()
      ui.sm['deviceState'].startedMonoTime = DRIVE
      ui.sm['carState'].canValid = True
      ui.sm['carState'].canTimeout = False
      ui.sm['carControl'].longActive = True
      ui.sm['selfdriveState'].enabled = True
      ui.sm['selfdriveState'].experimentalMode = True
      ui.sm.logMonoTime['selfdriveState'] = SOURCE
      ui.sm.put('starpilotSelfdriveState', acknowledged())
      ui.sm.logMonoTime['starpilotSelfdriveState'] = NOW
      adapter = RuntimeSnapshotAdapter(ui)
      shown = adapter.build(ShellMode.ONROAD, now_ns=NOW)
      self.assertEqual(shown.home.mode, HomeMode.CONDITIONAL_EXPERIMENTAL)
      self.assertIsNotNone(shown.onroad.conditional_effective)
      assert shown.onroad.conditional_effective is not None
      self.assertEqual(shown.onroad.conditional_effective.reason, 'cem_speed')
      self.assertTrue(shown.onroad.longitudinal_active)

      # Presentation keeps the same joined receipt across ordinary UI-frame
      # gaps and stale health messages, without authorizing SLC commands.
      ui.sm.alive['pandaStates'] = False
      ui.sm.alive['deviceState'] = False
      for service in ('carState', 'carControl'):
        ui.sm.logMonoTime[service] = NOW - 50_000_000
      held = adapter.build(ShellMode.ONROAD, now_ns=NOW)
      self.assertEqual(held.onroad.conditional_effective, shown.onroad.conditional_effective)
      self.assertTrue(held.onroad.camera_available)
      self.assertFalse(held.onroad.slc_system_long_available)

      ui.sm.logMonoTime['selfdriveState'] = SOURCE - 50_000_001
      stale_join = adapter.build(ShellMode.ONROAD, now_ns=NOW)
      self.assertIsNone(stale_join.onroad.conditional_effective)
      self.assertEqual(stale_join.home.mode, HomeMode.CONDITIONAL_EXPERIMENTAL)  # Config remains independently truthful.
      ui.sm.logMonoTime['selfdriveState'] = SOURCE
      ui.sm['carControl'].longActive = False
      self.assertIsNone(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.conditional_effective)

  def test_stop_light_and_orange_border_share_accepted_stop_state(self):
    from openpilot.starpilot.ui.onroad import axis_status_color
    from openpilot.starpilot.ui.onroad_conditional import stop_active
    from openpilot.starpilot.ui.onroad_state import OnroadAlert, AlertSize
    state = RuntimeSnapshotAdapter(ui_fake()).build(ShellMode.ONROAD, now_ns=10_000_000_000).onroad
    orange = (218, 111, 37)
    def border_is_orange(value):
      color = axis_status_color(value)
      return (color.r, color.g, color.b) == orange
    for reason, code, experimental in (('cem_stop', 8, True), ('cem_hold', 8, True),
                                        ('cem_curve', 3, True), ('no_trigger', 0, False)):
      accepted = replace(state, longitudinal_active=True, conditional_configured=ModeChoice.CEM,
                         experimental_enabled=experimental, conditional_effective=NS(
                           choice=ModeChoice.CEM, effective_experimental=experimental, reason=reason, status_code=code))
      self.assertEqual(stop_active(accepted), reason in ("cem_stop", "cem_hold") and experimental)
      self.assertEqual(border_is_orange(accepted), experimental)
      for cleared in (replace(accepted, longitudinal_overridden=True),
                      replace(accepted, conditional_effective=None),
                      replace(accepted, alert=OnroadAlert(AlertSize.FULL)),
                      replace(accepted, longitudinal_active=False)):
        self.assertFalse(stop_active(cleared))
        if cleared.conditional_effective is None or not cleared.longitudinal_active:
          self.assertFalse(border_is_orange(cleared))

  def test_accepted_mode_renders_in_rail_and_effective_axis_border(self):
    from openpilot.starpilot.ui.onroad import axis_status_color
    state = RuntimeSnapshotAdapter(ui_fake()).build(ShellMode.ONROAD, now_ns=10_000_000_000).onroad
    accepted = replace(state, longitudinal_active=True, conditional_effective=NS(
      choice=ModeChoice.CEM, effective_experimental=True, reason='cem_speed'))
    visible = visual_status(accepted)
    assert visible is not None
    self.assertEqual(visible[:2], ('CEM', 'SPEED'))
    def rgba(color):
      return color.r, color.g, color.b, color.a
    self.assertEqual(rgba(axis_status_color(accepted)), (218, 111, 37, 255))
    self.assertNotEqual(rgba(axis_status_color(accepted)), rgba(axis_status_color(replace(accepted, conditional_effective=None))))
    fonts = Mock()
    fonts.measure.return_value = NS(width=40, height=20)
    rail = MiciSidebarWidgets(fonts)
    with patch.object(rail, '_confidence_ball'), patch.object(rail, '_personality'), \
         patch.object(rl, 'draw_rectangle'), patch.object(rail, '_chill_icon') as chill, \
         patch.object(rail, '_speed_icon') as speed:
      rail.render(rl.Rectangle(0, 0, 536, 240), accepted)
    chill.assert_not_called()
    speed.assert_called_once()
    self.assertIsNone(visual_status(replace(accepted, conditional_effective=None)))
    self.assertIsNone(visual_status(replace(accepted, longitudinal_active=False)))


if __name__ == '__main__':
  unittest.main()
