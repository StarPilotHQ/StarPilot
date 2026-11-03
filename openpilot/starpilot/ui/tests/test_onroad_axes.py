"""Fresh control axes drive the existing large and compact onroad indicators."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui import onroad
from openpilot.starpilot.ui.onroad import axis_status_color, clock_text, driving_mode_description
from openpilot.starpilot.ui.onroad_customization import MODE_WIDGET
from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
from openpilot.starpilot.ui.onroad_large_widgets import UnifiedSpeedWidget
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadInput
from openpilot.starpilot.ui.onroad_torque import TorqueBarWidget
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.preview_shell import reference_onroad
from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, ui_fake
from openpilot.starpilot.ui.tests.test_onroad_torque_draw import record_gpu


def rgba(color: rl.Color) -> tuple[int, int, int, int]:
  return color.r, color.g, color.b, color.a


class TestOnroadAxes(unittest.TestCase):
  def setUp(self):
    for name in ('draw_rectangle_rounded', 'draw_rectangle_rounded_lines_ex', 'draw_circle', 'draw_ring'):
      self.enterContext(patch('openpilot.starpilot.ui.speed_limit_widget.rl.' + name, new_callable=Mock))
    for name in ('circle', 'arrow', 'outline', 'button'):
      self.enterContext(patch('openpilot.starpilot.ui.speed_limit_shapes.SpeedLimitShapes.' + name, new_callable=Mock))
    self.ui = ui_fake()
    self.ui.CP = NS(carFingerprint='', openpilotLongitudinalControl=True, pcmCruise=False)
    car = self.ui.sm["carState"]
    car.canValid = True
    car.canTimeout = False
    car.cruiseState = NS(enabled=False)
    self.ui.sm["selfdriveState"].enabled = True
    self.ui.sm.put("controlsState", NS(longControlState="pid"))
    self.adapter = RuntimeSnapshotAdapter(self.ui)

  def state(self, lateral: bool, longitudinal: bool):
    control = self.ui.sm["carControl"]
    control.latActive = lateral
    control.longActive = longitudinal
    return self.adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad

  def publish_latch(self, armed=True, **changes):
    from openpilot.starpilot.aol.wire import IntentState, encode_intent
    intent = IntentState('card-ui', 1, NOW, NOW, NOW + 200_000_000, False, True, False, True, armed)
    self.ui.sm.put('aolIntentWire', encode_intent(replace(intent, **changes)))

  def test_latch_keeps_border_blue_through_standstill_min_speed_and_reverse(self):
    self.ui.aol_runtime = True
    self.publish_latch()
    for brand in ('hyundai', 'gm', 'toyota', 'ford', 'honda'):
      self.ui.CP.brand = brand
      for speed, gear in ((0, 'drive'), (2, 'drive'), (0, 'reverse')):
        with self.subTest(brand=brand, speed=speed, gear=gear):
          self.ui.sm['carState'].vEgo = speed
          self.ui.sm['carState'].gearShifter = gear
          paused = self.state(False, False)
          self.assertTrue(getattr(paused, "aol_latched", False))
          self.assertFalse(paused.lateral_active or paused.longitudinal_active or paused.engaged)
          self.assertEqual(rgba(axis_status_color(paused)), (10, 186, 181, 255))
    self.publish_latch(False)
    self.assertFalse(self.state(False, False).aol_latched)
    self.assertEqual(rgba(axis_status_color(self.state(False, False))), (18, 40, 57, 255))

  def test_cleared_latch_does_not_borrow_previous_active_steering_color(self):
    self.ui.aol_runtime = True
    self.publish_latch()
    self.assertEqual(rgba(axis_status_color(self.state(True, False))), (10, 186, 181, 255))
    self.publish_latch(False)
    current = self.state(True, False)
    self.assertFalse(current.aol_latched)
    self.assertTrue(current.lateral_active)
    self.assertEqual(rgba(axis_status_color(current)), (18, 40, 57, 255))

  def test_latch_display_requires_fresh_current_drive_valid_qualified_intent(self):
    self.ui.aol_runtime = True
    for changes in ({'settingsQualified': False}, {'producerSessionId': ''}, {'sequence': 0},
                    {'observedMonoTime': NOW + 1}, {'carStateLogMonoTime': NOW - 1},
                    {'validUntilMonoTime': NOW - 1}, {'lateralArmed': False}):
      self.publish_latch(**changes)
      self.assertFalse(self.state(False, False).aol_latched, changes)
    self.ui.sm.put('aolIntentWire', b'invalid')
    self.assertFalse(self.state(False, False).aol_latched)
    self.publish_latch()
    self.assertTrue(self.state(False, False).aol_latched)
    for flag in (self.ui.sm.valid, self.ui.sm.alive):
      flag['aolIntentWire'] = False
      self.assertFalse(self.state(False, False).aol_latched)
      flag['aolIntentWire'] = True
    self.ui.sm.recv_frame['aolIntentWire'] = self.ui.started_frame
    self.assertFalse(self.state(False, False).aol_latched)
    self.publish_latch()
    self.assertTrue(self.adapter.build(ShellMode.ONROAD, now_ns=NOW + 150_000_000).onroad.aol_latched)
    self.assertFalse(self.adapter.build(ShellMode.ONROAD, now_ns=NOW + 201_000_000).onroad.aol_latched)
    self.ui.aol_runtime = False
    self.assertFalse(self.state(False, False).aol_latched)

  def test_four_axis_modes_drive_border_max_and_compact_wheel(self):
    colors = {(False, False): (18, 40, 57, 255), (True, False): (10, 186, 181, 255),
              (False, True): (255, 105, 180, 255), (True, True): (22, 127, 64, 255)}
    for (lateral, longitudinal), expected in colors.items():
      with self.subTest(lateral=lateral, longitudinal=longitudinal):
        state = self.state(lateral, longitudinal)
        self.assertEqual((state.lateral_active, state.longitudinal_active), (lateral, longitudinal))
        self.assertEqual(rgba(axis_status_color(state)), expected)
        fonts = Mock(profile=Profile.LARGE)
        fonts.measure.return_value = NS(width=20, height=10)
        fonts.vertical_ink.return_value = (0, 10)
        fonts.horizontal_ink.return_value = (0, 20)
        with patch("openpilot.starpilot.ui.onroad_large_widgets.draw_control_card"), patch.object(rl, "draw_line_ex"):
          UnifiedSpeedWidget(fonts).render(rl.Rectangle(30, 30, 1800, 1020), state)
        from openpilot.starpilot.ui.tests.test_unified_speed_native import foreground_calls
        label_color = foreground_calls(fonts.draw)[0].args[-1]
        self.assertEqual(rgba(label_color), (255, 255, 255, 255))
        fonts.profile = Profile.COMPACT
        compact = CompactHudRenderer(fonts, Path("/unused"))
        with patch.object(compact, "prepare"), patch("openpilot.starpilot.ui.onroad_compact_widgets.rl.draw_texture_pro"), \
             patch("openpilot.starpilot.ui.onroad_compact_widgets.rl.draw_circle_gradient"):
          compact.render(state)
          compact.render(replace(state, observed_ns=state.observed_ns + 20_000_000))
        self.assertEqual(compact._wheel_alpha.x > 0, lateral)
        self.assertEqual(compact._set_speed_alpha.x > 0, longitudinal)

  def test_pedal_override_keeps_cruise_display_and_uses_gray_border(self):
    self.ui.sm['carState'].gasPressed = True
    self.ui.sm['selfdriveState'].state = 'overriding'
    state = self.state(True, False)
    self.assertTrue(state.longitudinal_overridden)
    self.assertTrue(state.driver_override_active)
    self.assertTrue(self.state(False, False).driver_override_active)
    self.assertTrue(state.cruise_active)
    self.assertTrue(state.lateral_active)
    self.assertEqual(rgba(axis_status_color(state)), (145, 155, 149, 255))
    self.ui.sm['carState'].gasPressed = False
    self.assertFalse(self.state(True, False).longitudinal_overridden)
    self.ui.sm['carState'].gasPressed = True
    self.ui.sm['selfdriveState'].enabled = False
    self.assertFalse(self.state(True, False).longitudinal_overridden)
    self.ui.sm['selfdriveState'].enabled = True
    self.ui.sm.logMonoTime['selfdriveState'] = NOW - 300_000_000
    self.assertFalse(self.state(True, False).longitudinal_overridden)

  def test_driver_inputs_only_gray_their_active_axis_and_clear_on_release(self):
    car = self.ui.sm['carState']
    gray, blue, cyan, pink = (145, 155, 149, 255), (18, 40, 57, 255), (10, 186, 181, 255), (255, 105, 180, 255)
    for lateral, longitudinal, colors in ((False, False, (blue, blue, blue)), (True, False, (cyan, gray, gray)),
                                           (False, True, (gray, pink, gray)), (True, True, (gray, gray, gray))):
      self.ui.sm['selfdriveState'].enabled = longitudinal
      for (gas, steering), color in zip(((True, False), (False, True), (True, True)), colors, strict=True):
        with self.subTest(lateral=lateral, longitudinal=longitudinal, gas=gas, steering=steering):
          car.gasPressed, car.steeringPressed = gas, steering
          state = self.state(lateral, longitudinal)
          self.assertEqual(state.driver_override_active, color == gray)
          self.assertEqual((state.lateral_active, state.longitudinal_active), (lateral, longitudinal))
          self.assertFalse(state.longitudinal_overridden)
          self.assertEqual(state.cruise_active, longitudinal)
          self.assertEqual(rgba(axis_status_color(state)), color)
          car.gasPressed = car.steeringPressed = False
          released = self.state(lateral, longitudinal)
          self.assertFalse(released.driver_override_active)
          self.assertNotEqual(rgba(axis_status_color(released)), (145, 155, 149, 255))

  def test_driver_override_requires_valid_current_sources(self):
    car = self.ui.sm['carState']
    self.ui.sm['selfdriveState'].enabled = False
    for field in ('gasPressed', 'steeringPressed'):
      with self.subTest(field=field):
        longitudinal = field == 'gasPressed'
        setattr(car, field, True)
        self.assertTrue(self.state(True, longitudinal).driver_override_active)
        for service in ('carState', 'carControl'):
          self.ui.sm.logMonoTime[service] = NOW - 300_000_000
          self.assertFalse(self.state(True, longitudinal).driver_override_active)
          self.ui.sm.logMonoTime[service] = NOW
        for valid, timeout in ((False, False), (True, True)):
          car.canValid, car.canTimeout = valid, timeout
          self.assertFalse(self.state(True, longitudinal).driver_override_active)
        car.canValid, car.canTimeout = True, False
        setattr(car, field, False)

  def test_independent_long_request_distinguishes_pause_from_pedal_override(self):
    self.ui.aol_runtime = True
    self.publish_latch()
    self.ui.sm['selfdriveState'].state = 'overriding'
    axis = NS(qualified=True, sessionId='ui-axis', observedMonoTime=NOW, validUntilMonoTime=NOW + 30_000_000,
              nativeAcknowledged=True, desiredLongitudinal=False)
    self.ui.sm.put('aolAxisState', axis)
    car = self.ui.sm['carState']
    for requested, gas, steering, gray, pedal in (
        (False, False, False, False, False), (False, True, False, False, False),
        (False, False, True, True, False), (False, True, True, True, False),
        (True, False, False, False, False), (True, True, False, True, True),
        (True, False, True, True, False), (True, True, True, True, True)):
      with self.subTest(requested=requested, gas=gas, steering=steering):
        axis.desiredLongitudinal = requested
        car.gasPressed, car.steeringPressed = gas, steering
        state = self.state(True, False)
        self.assertEqual(state.longitudinal_overridden, pedal)
        self.assertEqual(state.driver_override_active, gray)
        self.assertEqual(state.cruise_active, pedal)
        self.assertEqual(driving_mode_description(state), 'ACC - Override' if pedal else 'Always On Lateral')
        self.assertEqual(rgba(axis_status_color(state)), (145, 155, 149, 255) if gray else (10, 186, 181, 255))

  def test_independent_pedal_override_requires_current_axis_request(self):
    self.ui.aol_runtime = True
    self.ui.sm['selfdriveState'].state = 'overriding'
    self.ui.sm['carState'].gasPressed = True
    self.assertFalse(self.state(True, False).longitudinal_overridden)
    axis = NS(qualified=True, sessionId='ui-axis', observedMonoTime=NOW, validUntilMonoTime=NOW + 30_000_000,
              nativeAcknowledged=True, desiredLongitudinal=True)
    self.ui.sm.put('aolAxisState', axis)
    self.assertTrue(self.state(True, False).longitudinal_overridden)
    for field, value in (('qualified', False), ('sessionId', ''), ('nativeAcknowledged', False), ('desiredLongitudinal', False)):
      with self.subTest(field=field):
        original = getattr(axis, field)
        setattr(axis, field, value)
        state = self.state(True, False)
        self.assertFalse(state.longitudinal_overridden or state.driver_override_active or state.cruise_active)
        setattr(axis, field, original)
    self.ui.sm.logMonoTime['aolAxisState'] = NOW - 60_000_000
    self.assertTrue(self.state(True, False).longitudinal_overridden,
                    'a late UI frame past the 30 ms control window does not blink the override edge')
    for stamp in (NOW + 1, NOW - 201_000_000):
      self.ui.sm.logMonoTime['aolAxisState'] = stamp
      self.assertFalse(self.state(True, False).longitudinal_overridden)
    self.ui.sm.logMonoTime['aolAxisState'] = NOW
    self.ui.sm.recv_frame['aolAxisState'] = self.ui.started_frame
    self.assertFalse(self.state(True, False).longitudinal_overridden)
    self.ui.sm.recv_frame['aolAxisState'] = self.ui.started_frame + 1
    for flags in (self.ui.sm.valid, self.ui.sm.alive):
      flags['aolAxisState'] = False
      self.assertFalse(self.state(True, False).longitudinal_overridden)
      flags['aolAxisState'] = True
    self.assertTrue(self.state(True, False).longitudinal_overridden)

  def test_traffic_returns_after_conditional_stop_without_changing_intent(self):
    from openpilot.starpilot.conditional_mode.policy import ModeChoice
    from openpilot.starpilot.ui.conditional_status import ConditionalDisplay

    traffic = replace(self.state(True, True), traffic_mode=True)
    stopping = replace(traffic, conditional_effective=ConditionalDisplay(ModeChoice.CEM, True, 'cem_stop', 8, 'test', 1))
    self.assertEqual(rgba(axis_status_color(traffic)), (201, 34, 49, 255))
    for _ in range(3):
      self.assertEqual(rgba(axis_status_color(stopping)), (218, 111, 37, 255))
      self.assertTrue(stopping.traffic_mode)
      self.assertEqual(rgba(axis_status_color(traffic)), (201, 34, 49, 255))
      self.assertTrue(traffic.traffic_mode)
    self.assertEqual(rgba(axis_status_color(replace(stopping, longitudinal_overridden=True))), (145, 155, 149, 255))

  def test_experimental_border_requires_active_combined_axes(self):
    combined = replace(self.state(True, True), experimental_enabled=True)
    self.assertEqual(rgba(axis_status_color(combined)), (218, 111, 37, 255))
    self.assertEqual(rgba(axis_status_color(replace(combined, longitudinal_active=False))), (10, 186, 181, 255))
    self.assertEqual(rgba(axis_status_color(replace(combined, lateral_active=False))), (255, 105, 180, 255))
    self.assertEqual(rgba(axis_status_color(replace(combined, lateral_active=False, longitudinal_active=False))), (18, 40, 57, 255))

  def test_both_profile_compositions_use_axis_border_and_fixtures_remain_combined(self):
    engaged_fixture = reference_onroad("onroad_engaged_no_camera")
    self.assertTrue(engaged_fixture.lateral_active and engaged_fixture.longitudinal_active)
    disengaged_fixture = reference_onroad("onroad_disengaged_no_camera")
    self.assertFalse(disengaged_fixture.lateral_active or disengaged_fixture.longitudinal_active)
    gray, blue = (145, 155, 149, 255), (18, 40, 57, 255)
    cases = [(False, False, False, OnroadAlert(), blue),
             (True, False, False, OnroadAlert(), (10, 186, 181, 255)),
             (False, True, False, OnroadAlert(), (255, 105, 180, 255)),
             (True, True, False, OnroadAlert(), (22, 127, 64, 255)),
             (True, False, False, OnroadAlert(size=AlertSize.SMALL), blue)]
    cases += [(True, longitudinal, True, alert, color) for longitudinal in (False, True) for alert, color in (
      (OnroadAlert(), gray), (OnroadAlert(size=AlertSize.SMALL), gray), (OnroadAlert(size=AlertSize.MID), gray),
      (OnroadAlert(size=AlertSize.FULL), blue), (OnroadAlert(size=AlertSize.SMALL, critical=True), blue))]
    cases = [(*case, None) for case in cases]
    cases += [(False, False, False, alert, color, True) for alert, color in (
      (OnroadAlert(), (10, 186, 181, 255)),
      (OnroadAlert(size=AlertSize.SMALL, text1='Steering Paused'), (10, 186, 181, 255)),
      (OnroadAlert(size=AlertSize.MID), (10, 186, 181, 255)),
      (OnroadAlert(size=AlertSize.FULL), blue), (OnroadAlert(size=AlertSize.SMALL, critical=True), blue))]
    cases = [(*case, False) for case in cases]
    cases += [(True, True, override, alert, color, None, True) for override, alert, color in (
      (False, OnroadAlert(), (201, 34, 49, 255)),
      (False, OnroadAlert(size=AlertSize.SMALL, critical=True), blue),
      (False, OnroadAlert(), (201, 34, 49, 255)),
      (True, OnroadAlert(), gray),
      (False, OnroadAlert(size=AlertSize.FULL), blue),
      (False, OnroadAlert(), (201, 34, 49, 255)))]
    for lateral, longitudinal, override, alert, compact_color, latched, traffic in cases:
      state = replace(self.state(lateral, longitudinal), driver_override_active=override, alert=alert,
                      aol_latched=latched, traffic_mode=traffic)
      for layout in state.customization['layouts'].values():
        layout[MODE_WIDGET]['enabled'] = True
      for profile in (Profile.LARGE, Profile.COMPACT):
        with self.subTest(profile=profile, lateral=lateral, longitudinal=longitudinal, override=override, alert=alert):
          view = onroad.OnroadView.__new__(onroad.OnroadView)
          view.personality = Mock()
          object.__setattr__(view, "fonts", NS(profile=profile, draw=Mock(), measure=Mock(return_value=NS(width=100, height=30)),
                                              vertical_ink=Mock(return_value=(0, 20)), horizontal_ink=Mock(return_value=(0, 100))))
          view.camera_layer = Mock()
          view.extra_overlays = None
          view.alert = Mock()
          view.torque_bar = Mock()
          view.unified_speed = Mock()
          view.navigation = Mock()
          view.navigation_favorites = Mock()
          view.background_layer = None
          view.projection_viewport = None
          view._corner_cache = Mock()
          view.current_speed = Mock()
          view.steering_wheel = Mock()
          view.compact_hud = Mock()
          view.compact_sidebar = Mock()
          with patch.object(onroad.clip, "begin_scissor_mode"), patch.object(onroad.clip, "end_scissor_mode"), \
               patch.object(onroad.rl, "draw_rectangle_rec"), patch.object(onroad.rl, "draw_rectangle_gradient_v"), \
               patch.object(onroad.rl, "draw_rectangle_lines_ex"), patch.object(onroad.rl, "draw_rectangle"), \
               patch.object(onroad.rl, "draw_texture_ex"), patch.object(onroad.rl, "draw_rectangle_rounded_lines_ex") as border, \
               patch.object(onroad, "render_corner_hint"), \
               patch.object(view, "_slc_actions"):
            view.render(state)
          expected = compact_color if profile == Profile.COMPACT else rgba(axis_status_color(state))
          self.assertEqual(rgba(border.call_args.args[-1]), expected)
          if alert.size == AlertSize.NONE:
            self.assertIn(driving_mode_description(state), [call.args[0] for call in view.fonts.draw.call_args_list])

  def test_driving_mode_text_uses_active_axes_and_effective_mode(self):
    from openpilot.starpilot.conditional_mode.policy import ModeChoice
    from openpilot.starpilot.ui.conditional_status import ConditionalDisplay

    base = self.state(True, True)
    for changes, expected in [
      ({}, 'ACC - Chill'),
      ({'experimental_enabled': True}, 'ACC - Experimental'),
      ({'longitudinal_active': False}, 'Always On Lateral'),
      ({'lateral_active': False}, 'ACC - Chill'),
      ({'switchback_mode': True, 'traffic_mode': True}, 'Switchback Mode'),
      ({'traffic_mode': True, 'experimental_enabled': True}, 'Traffic Mode'),
      ({'longitudinal_overridden': True}, 'ACC - Override'),
      ({'lateral_active': False, 'longitudinal_active': False, 'traffic_mode': True,
        'switchback_mode': True, 'experimental_enabled': True}, 'Disengaged'),
      ({'lateral_active': False, 'longitudinal_active': False, 'stock_cruise_active': True}, 'Stock ACC'),
      ({'longitudinal_active': False, 'stock_cruise_active': True}, 'Always On Lateral'),
    ]:
      with self.subTest(changes=changes):
        self.assertEqual(driving_mode_description(replace(base, **changes)), expected)
    stop = ConditionalDisplay(ModeChoice.CEM, True, 'cem_stop', 8, 'test', 1)
    chill = replace(stop, effective_experimental=False, reason='manual_chill')
    self.assertEqual(driving_mode_description(replace(base, conditional_effective=stop)), 'ACC - Experimental')
    self.assertEqual(driving_mode_description(replace(base, experimental_enabled=True, conditional_effective=chill)), 'ACC - Chill')
    self.assertEqual(driving_mode_description(replace(base, traffic_mode=True, conditional_effective=stop)), 'Traffic Mode')

  def test_mode_widget_is_optional_and_yields_to_alerts(self):
    from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert

    state = self.state(True, True)
    for profile in (Profile.LARGE, Profile.COMPACT):
      view = onroad.OnroadView.__new__(onroad.OnroadView)
      view.personality = Mock()
      self.enterContext(patch.object(view, 'fonts', NS(profile=profile, draw=Mock()), create=True))
      view._driving_mode(state)
      view.fonts.draw.assert_not_called()
      state.customization['layouts'][profile][MODE_WIDGET]['enabled'] = True
      for size in (AlertSize.SMALL, AlertSize.MID, AlertSize.FULL):
        view._driving_mode(replace(state, alert=OnroadAlert(size=size)))
        view.fonts.draw.assert_not_called()

  def test_stock_cruise_is_distinct_from_system_long_and_lateral(self):
    self.ui.CP = NS(carFingerprint='', openpilotLongitudinalControl=False, pcmCruise=True)
    self.ui.sm["carState"].cruiseState.enabled = True
    state = self.state(False, False)
    self.assertTrue(state.stock_cruise_active)
    self.assertTrue(state.cruise_active)
    self.assertFalse(state.longitudinal_active)
    self.assertEqual(rgba(axis_status_color(state)), (18, 40, 57, 255))
    fonts = Mock(profile=Profile.LARGE)
    fonts.measure.return_value = NS(width=20, height=10)
    fonts.vertical_ink.return_value = (0, 10)
    fonts.horizontal_ink.return_value = (0, 20)
    with patch("openpilot.starpilot.ui.onroad_large_widgets.draw_control_card"), patch.object(rl, "draw_line_ex"):
      UnifiedSpeedWidget(fonts).render(rl.Rectangle(30, 30, 1800, 1020), state)
    from openpilot.starpilot.ui.tests.test_unified_speed_native import foreground_calls
    self.assertEqual(rgba(foreground_calls(fonts.draw)[0].args[-1]), (255, 255, 255, 255))
    fonts.profile = Profile.COMPACT
    compact = CompactHudRenderer(fonts, Path("/unused"))
    with patch("openpilot.starpilot.ui.onroad_compact_widgets.rl.draw_circle_gradient"):
      compact.render(state)
      compact.render(replace(state, observed_ns=state.observed_ns + 20_000_000))
    self.assertGreater(compact._set_speed_alpha.x, 0)
    self.assertEqual(compact._wheel_alpha.x, 0)
    self.ui.sm["carState"].canValid = False
    invalid = self.state(False, False)
    self.assertFalse(invalid.cruise_active)
    self.assertIsNone(invalid.cruise_kph)

  def test_lost_control_clears_steering_and_cruise_immediately_and_cancels_touch(self):
    self.ui.sm.put("controlsState", NS(longControlState="pid", lateralControlState=NS(which=lambda: "torqueState")))
    self.ui.sm.put("carOutput", NS(actuatorsOutput=NS(torque=0.4)))
    active = self.state(True, True)
    self.assertTrue(active.torque_source_available)
    self.assertAlmostEqual(active.torque_utilization, -0.4)
    requests = []
    touch = OnroadInput(requests.append, Profile.LARGE)
    touch.press(120, 530, active)
    self.ui.sm.logMonoTime["carControl"] = NOW - 300_000_000
    stale = self.adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
    self.assertFalse(stale.lateral_active or stale.longitudinal_active or stale.cruise_active)
    self.assertEqual(rgba(axis_status_color(stale)), (18, 40, 57, 255))
    touch.release(120, 530, stale)
    self.assertEqual(requests, [])
    fonts = Mock(profile=Profile.COMPACT)
    fonts.measure.return_value = NS(width=20, height=10)
    fonts.vertical_ink.return_value = (0, 10)
    compact = CompactHudRenderer(fonts, Path("/unused"))
    fonts.horizontal_ink.return_value = (0, 20)
    with patch.object(compact, "prepare"), patch("openpilot.starpilot.ui.onroad_compact_widgets.rl.draw_texture_pro"), \
         patch("openpilot.starpilot.ui.onroad_compact_widgets.rl.draw_circle_gradient"):
      compact.render(active)
      compact.render(replace(active, observed_ns=active.observed_ns + 20_000_000))
      self.assertGreater(compact._wheel_alpha.x, 0)
      self.assertGreater(compact._set_speed_alpha.x, 0)
      compact.render(replace(stale, observed_ns=active.observed_ns + 40_000_000))
    self.assertEqual(compact._wheel_alpha.x, 0)
    assert compact._last_cruise_ns is not None
    self.assertEqual(compact._set_speed_opacity(stale, compact._last_cruise_ns + 1), 0)
    self.assertEqual(compact._set_speed_opacity(stale, compact._last_cruise_ns + 150_000_001), 0)
    self.assertEqual(compact._set_speed_alpha.x, 0)
    torque = TorqueBarWidget()
    with record_gpu() as gpu:
      torque.render(rl.Rectangle(0, 0, 476, 240), active, 536)
      self.assertGreater(torque._alpha_filter.x, 0)
      gpu.frames.clear()
      torque.render(rl.Rectangle(0, 0, 476, 240), stale, 536)
      self.assertFalse(gpu.frames)
    self.assertEqual(torque._alpha_filter.x, 0)

  def test_banked_demand_reaches_actual_widget_and_resets_on_feedback_kind_change(self):
    self.ui.CP.maxLateralAccel = 1.5
    self.ui.sm["carState"].vEgo = 20
    self.ui.sm.put("vehicleParameters", NS(valid=True, roll=.08))
    self.ui.sm.put("controlsState", NS(longControlState="pid", lateralControlState=NS(which=lambda: "curvatureState"),
                                        curvature=.001, desiredCurvature=.001))
    state = self.state(True, False)
    self.assertTrue(state.torque_is_demand and state.torque_source_available)
    self.assertAlmostEqual(state.torque_utilization, .4 / 1.5)
    widget = TorqueBarWidget()
    rect = rl.Rectangle(0, 0, 476, 240)
    with record_gpu() as gpu:
      for _ in range(30):
        widget.render(rect, state, 536)
      self.assertGreater(gpu.frames[-1].uniforms['indicatorNormals'][2], 0)
      self.assertEqual(gpu.frames[-1].uniforms['gradient'][1:], (1, 0, 1))
      widget.render(rect, replace(state, torque_is_demand=False, torque_utilization=-1), 536)
      self.assertLess(widget._torque_filter.x, 0, "never color retained demand as actual torque")
      widget.render(rect, replace(state, torque_drive_frame=99, torque_utilization=-1), 536)
      self.assertLess(widget._torque_filter.x, 0, "new drive resets prior direction")
      self.ui.sm.logMonoTime["vehicleParameters"] = NOW - 1_000_000_000
      stale = self.state(True, False)
      self.assertFalse(stale.torque_source_available)
      gpu.frames.clear()
      widget.render(rect, stale, 536)
      self.assertFalse(gpu.frames)
      self.assertEqual(widget._torque_filter.x, 0)

  def test_missing_actual_torque_output_stays_hidden_with_active_axes(self):
    self.ui.sm.put("controlsState", NS(longControlState="pid", lateralControlState=NS(which=lambda: "torqueState")))
    state = self.state(True, True)
    self.assertTrue(state.lateral_active)
    self.assertFalse(state.torque_source_available)
    torque = TorqueBarWidget()
    with record_gpu() as gpu:
      torque.render(rl.Rectangle(0, 0, 476, 240), state, 536)
    self.assertFalse(gpu.frames or gpu.loads)
    self.assertEqual(torque._alpha_filter.x, 0)

  def test_pressed_control_cancels_on_axis_transition_even_with_fresh_transport(self):
    active = self.state(True, True)
    requests = []
    touch = OnroadInput(requests.append, Profile.LARGE)
    touch.press(120, 530, active)
    touch.release(120, 530, self.state(True, False))
    self.assertEqual(requests, [])
    self.assertFalse(replace(active, lateral_active=False, longitudinal_active=False).cruise_active)

  def test_clock_text_supports_12_and_24_hour_formats(self):
    evening = datetime(2026, 10, 7, 18, 0)
    self.assertEqual(clock_text(evening), "6:00 PM")
    self.assertEqual(clock_text(evening, use_24_hour=True), "18:00")

    midnight = datetime(2026, 10, 8, 0, 0)
    self.assertEqual(clock_text(midnight), "12:00 AM")
    self.assertEqual(clock_text(midnight, use_24_hour=True), "00:00")



  def test_stock_acc_green_keeps_system_long_inactive_and_aol_cyan(self):
    from opendbc.car.subaru.interface import CarInterface
    from opendbc.car.subaru.values import CAR

    self.ui.CP = CarInterface.get_non_essential_params(CAR.SUBARU_CROSSTREK_2025)
    self.ui.sm['carState'].cruiseState.enabled = True
    state = self.state(True, False)
    self.assertTrue(state.stock_cruise_active)
    self.assertFalse(state.longitudinal_active)
    self.assertEqual(rgba(axis_status_color(state)), (22, 127, 64, 255))
    self.assertEqual(rgba(axis_status_color(replace(state, experimental_enabled=True))), (22, 127, 64, 255))
    self.ui.sm['carState'].gasPressed = True
    self.assertEqual(rgba(axis_status_color(self.state(True, False))), (145, 155, 149, 255))
    self.ui.sm['carState'].gasPressed = False
    self.ui.sm['carState'].cruiseState.enabled = False
    self.assertEqual(rgba(axis_status_color(self.state(True, False))), (10, 186, 181, 255))
    self.ui.sm['carState'].cruiseState.enabled = True
    self.ui.sm.logMonoTime['carState'] = NOW - 300_000_000
    self.assertFalse(self.state(True, False).stock_cruise_active)


if __name__ == "__main__":
  unittest.main()
