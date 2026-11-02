"""Wheel feedback sources, saved compatibility and actual draw-call colors."""

from dataclasses import replace
from pathlib import Path
import math
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.params import Params
from openpilot.starpilot import saved_document
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner
from openpilot.starpilot.ui.appearance_preferences import OnroadAppearance, onroad_appearance
from openpilot.starpilot.ui.feature_settings_state import row_change
from openpilot.starpilot.ui.onroad_compact_widgets import CompactHudRenderer
from openpilot.starpilot.ui.onroad_large_widgets import SteeringWheelWidget
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import NOW, ui_fake
from openpilot.starpilot.ui.wheel_feedback import ACCEL_RGB, BRAKE_RGB, WheelFeedbackFade, observe_wheel_acceleration


def car(**changes):
  return NS(**{"canValid": True, "canTimeout": False, "brakePressed": False, "regenBraking": False,
               "gasPressed": False, "aEgo": 0.0, **changes})


def control(**changes):
  return NS(longActive=True, actuators=NS(**{"accel": 0.0, "gas": 0.0, **changes}))


def required_change(row):
  request = row_change(row)
  assert request is not None
  return request


class WheelFeedbackTests(unittest.TestCase):
  def test_only_finite_measurements_with_valid_can_are_observed(self):
    for acceleration in (-2., 0, .5):
      self.assertEqual(observe_wheel_acceleration(car(aEgo=acceleration, brakePressed=True)), acceleration)
    for acceleration in (None, True, '1', float('nan'), float('inf'), -float('inf')):
      self.assertIsNone(observe_wheel_acceleration(car(aEgo=acceleration, brakePressed=True, gasPressed=True)))
    for vehicle in (None, NS(canValid=True, canTimeout=False), car(canValid=False), car(canValid=1),
                    car(canTimeout=True), car(canTimeout=0)):
      self.assertIsNone(observe_wheel_acceleration(vehicle))

  def test_runtime_freshness_and_drive_boundary_preserve_measurement_qualification(self):
    ui = ui_fake()
    vars(ui.sm.messages["carState"]).update(vars(car(aEgo=-.5)))
    vars(ui.sm.messages["carControl"]).update(vars(control(accel=2.)))
    ui.sm.messages["carControl"].actuators.torque = 0.0
    adapter = RuntimeSnapshotAdapter(ui)

    def observed():
      return adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.wheel_acceleration

    self.assertEqual(observed(), -.5)
    ui.sm.logMonoTime["carControl"] = NOW - 201_000_000
    self.assertEqual(observed(), -.5)
    ui.sm.logMonoTime["carState"] = NOW - 201_000_000
    self.assertIsNone(observed())
    for service in ("carControl", "carState"):
      ui.sm.logMonoTime[service] = NOW
    ui.sm.recv_frame["carState"] = ui.started_frame
    self.assertIsNone(observed())
    ui.sm.recv_frame["carState"] = ui.started_frame + 1
    ui.sm.valid["carState"] = False
    self.assertIsNone(observed())
    ui.sm.valid["carState"] = True
    ui.started = False
    self.assertIsNone(observed())

  def test_both_renderers_preserve_placement_and_alpha(self):
    state = OnroadState(True, True, 10, None, SpeedLimitObservation(), lateral_active=True,
                        appearance=OnroadAppearance(wheel_pedal_feedback=True),
                        wheel_acceleration=-2.0)
    state.customization["layouts"]["large"]["steering_wheel"].update(x=900, y=200)
    large = SteeringWheelWidget(Path('/unused'))
    large._texture = Mock()
    with patch.object(rl, "draw_circle"), patch.object(rl, "draw_texture_pro") as draw:
      large.render(rl.Rectangle(30, 30, 1800, 1020), replace(state, observed_ns=NOW))
      self.assertEqual(tuple(getattr(draw.call_args.args[-1], key) for key in 'rgba'), (255, 255, 255, 255))
      for elapsed_ms in (100, 200, 300, 400, 500, 600):
        large.render(rl.Rectangle(30, 30, 1800, 1020), replace(state, observed_ns=NOW + elapsed_ms * 1_000_000))
      args = draw.call_args.args
      self.assertEqual((args[2].x, args[2].y), (924, 224))
      self.assertEqual((args[-1].r, args[-1].g, args[-1].b, args[-1].a), (*BRAKE_RGB, 255))
      large.render(rl.Rectangle(30, 30, 1800, 1020), replace(state, wheel_acceleration=-1.0))
      self.assertEqual(tuple(getattr(draw.call_args.args[-1], key) for key in 'rgba'), (255, 128, 128, 255))
      large.render(rl.Rectangle(30, 30, 1800, 1020), replace(state, appearance=OnroadAppearance()))
      self.assertEqual(tuple(getattr(draw.call_args.args[-1], key) for key in 'rgba'), (255, 255, 255, 255))
    state.customization["layouts"]["compact"]["steering_wheel"].update(x=80, y=130)
    compact = CompactHudRenderer(Mock(), Path("/unused"))
    compact._wheel = Mock()
    with patch.object(compact, "_speed_limit_sign"), patch.object(rl, "draw_texture_pro") as draw:
      compact.render(state)
      args = draw.call_args.args
      self.assertEqual((args[2].x - args[3].x, args[2].y - args[3].y), (80, 130))
      self.assertEqual(tuple(getattr(args[-1], key) for key in "rgb"), BRAKE_RGB)
      self.assertEqual(args[-1].a, int(compact._wheel_alpha.x))
      self.assertLess(args[-1].a, 255)
      draw.reset_mock()
      compact.render(replace(state, lateral_active=False))
      draw.assert_not_called()

  def test_both_renderers_use_identical_tones_and_fades_with_independent_alpha(self):
    state = OnroadState(True, False, 10, None, SpeedLimitObservation(), lateral_active=True, drive_frame=1)
    large = SteeringWheelWidget(Path('/unused'))
    large._texture = Mock()
    compact = CompactHudRenderer(Mock(), Path('/unused'))
    compact._wheel = Mock()
    with patch.object(rl, 'draw_circle'), patch.object(rl, 'draw_texture_pro') as draw:
      for enabled in (True, False):
        for frame, acceleration in enumerate((.5, .5, 2., 2., -1., -1., 0., None, .2)):
          sample = replace(state, wheel_acceleration=acceleration, observed_ns=NOW + frame * 100_000_000,
                           appearance=OnroadAppearance(wheel_pedal_feedback=enabled))
          large.render(rl.Rectangle(30, 30, 1800, 1020), sample)
          large_color = draw.call_args.args[-1]
          compact.render_steering_wheel(sample)
          compact_color = draw.call_args.args[-1]
          self.assertEqual(tuple(getattr(large_color, key) for key in 'rgb'), tuple(getattr(compact_color, key) for key in 'rgb'))
          self.assertEqual(large_color.a, 255)
          self.assertEqual(compact_color.a, int(compact._wheel_alpha.x))
          self.assertLess(compact_color.a, 255)

  def test_hidden_compact_wheel_clears_hysteresis_before_reappearing(self):
    for acceleration, enabled in ((0., True), (None, True), (.26, False)):
      with self.subTest(acceleration=acceleration, enabled=enabled):
        compact = CompactHudRenderer(Mock(), Path('/unused'))
        compact._wheel = Mock()
        state = OnroadState(True, False, 10, None, SpeedLimitObservation(), lateral_active=True, drive_frame=1,
                            wheel_acceleration=.26, appearance=OnroadAppearance(wheel_pedal_feedback=True))
        with patch.object(rl, 'draw_texture_pro') as draw:
          for elapsed_ms in (0, 100, 600):
            compact.render_steering_wheel(replace(state, observed_ns=NOW + elapsed_ms * 1_000_000))
          draw.reset_mock()
          for elapsed_ms in (700, 1600):
            compact.render_steering_wheel(replace(state, lateral_active=False, wheel_acceleration=acceleration,
                                                 appearance=OnroadAppearance(wheel_pedal_feedback=enabled),
                                                 observed_ns=NOW + elapsed_ms * 1_000_000))
          draw.assert_not_called()
          compact.render_steering_wheel(replace(state, wheel_acceleration=.2, observed_ns=NOW + 1_700_000_000))
          self.assertEqual(compact._feedback._target, 0.)
          self.assertEqual(tuple(getattr(draw.call_args.args[-1], key) for key in 'rgb'), (255, 255, 255))


class WheelFeedbackFadeTests(unittest.TestCase):
  def test_hysteresis_enters_and_releases_at_separate_strict_thresholds(self):
    for sign in (-1, 1):
      with self.subTest(sign=sign):
        fade = WheelFeedbackFade()
        for index, (value, target) in enumerate(((.25, 0), (.251, .1255), (.249, .1245), (.151, .0755), (.15, 0), (.249, 0))):
          fade.update(sign * value, True, NOW + index * 20_000_000, 1)
          self.assertAlmostEqual(fade._target, sign * target)

  def test_near_threshold_jitter_does_not_flutter_between_active_and_neutral(self):
    for sign in (-1, 1):
      fade = WheelFeedbackFade()
      for index in range(80):
        acceleration = sign * (.26 if index % 2 == 0 else .24)
        fade.update(acceleration, True, NOW + index * 20_000_000, 1)
        self.assertAlmostEqual(fade._target, acceleration / 2.)
      before = abs(fade._amount)
      fade.update(0., True, NOW + 1_600_000_000, 1)
      self.assertEqual(fade._target, 0)
      self.assertLess(abs(fade._amount), before)

  def test_proportional_tones_settle_without_growing_to_full_color(self):
    for acceleration, target, expected in (
      (-5., -1., BRAKE_RGB), (-2., -1., BRAKE_RGB), (-1., -.5, (255, 128, 128)), (-.5, -.25, (255, 191, 191)),
      (0., 0., (255, 255, 255)), (.5, .25, (197, 223, 207)), (1., .5, (138, 191, 160)), (2., 1., ACCEL_RGB), (5., 1., ACCEL_RGB),
    ):
      with self.subTest(acceleration=acceleration):
        fade = WheelFeedbackFade()
        for frame in range(21):
          rgb = fade.update(acceleration, True, NOW + frame * 100_000_000, 1)
        self.assertEqual(fade._target, target)
        self.assertAlmostEqual(fade._amount, target)
        self.assertTrue(all(abs(actual - settled) <= 1 for actual, settled in zip(rgb, expected, strict=True)))

  def test_opposite_direction_must_cross_the_entry_threshold(self):
    for sign in (-1, 1):
      fade = WheelFeedbackFade()
      fade.update(sign * 2., True, NOW, 1)
      for index, (acceleration, expected) in enumerate(((.2, 0.), (.25, 0.), (.251, -sign * .1255)), start=1):
        fade.update(-sign * acceleration, True, NOW + index * 20_000_000, 1)
        self.assertAlmostEqual(fade._target, expected)

  def test_changing_strength_in_the_same_hue_fades_toward_the_new_level(self):
    fade = WheelFeedbackFade()
    for frame in range(7):
      fade.update(2., True, NOW + frame * 100_000_000, 1)
    before = fade._amount
    fade.update(.5, True, NOW + 600_000_000, 1)
    self.assertEqual(fade._amount, before)
    self.assertEqual(fade._target, .25)
    fade.update(.5, True, NOW + 700_000_000, 1)
    self.assertTrue(.25 < fade._amount < before)

  def test_runtime_measurement_works_with_inactive_or_stale_controls(self):
    ui = ui_fake()
    vars(ui.sm.messages['carState']).update(vars(car(brakePressed=True, regenBraking=True, gasPressed=True)))
    vars(ui.sm.messages['carControl']).update(vars(control(accel=-2.)))
    ui.sm.messages['carControl'].actuators.torque = 0.
    adapter = RuntimeSnapshotAdapter(ui)
    for acceleration, expected in ((.5, (197, 223, 207)), (-.5, (255, 191, 191)), (0., (255, 255, 255)), (None, (255, 255, 255))):
      for long_active, stale in ((True, False), (False, False), (True, True)):
        with self.subTest(acceleration=acceleration, long_active=long_active, stale=stale):
          ui.sm.messages['carState'].aEgo = acceleration
          ui.sm.messages['carControl'].longActive = long_active
          ui.sm.logMonoTime['carControl'] = NOW - 201_000_000 if stale else NOW
          feedback = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.wheel_acceleration
          self.assertEqual(WheelFeedbackFade().update(feedback, True, 0, None), expected)
    ui.sm.logMonoTime['carState'] = NOW - 201_000_000
    feedback = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.wheel_acceleration
    self.assertEqual(WheelFeedbackFade().update(feedback, True, 0, None), (255, 255, 255))

  def test_fade_uses_elapsed_time_and_stays_bounded(self):
    amounts = []
    for fps in (20, 30, 60, 120):
      fade = WheelFeedbackFade()
      value = 1.
      self.assertEqual(fade.update(value, True, NOW, 1), (255, 255, 255))
      for frame in range(1, round(.3 * fps) + 1):
        before = fade._amount
        rgb = fade.update(value, True, NOW + round(frame * 1e9 / fps), 1)
        self.assertTrue(before < fade._amount < .5)
        self.assertTrue(all(0 <= channel <= 255 for channel in rgb))
      self.assertAlmostEqual(fade._amount, .5 * (1 - math.exp(-.3 / .08)))
      amounts.append(fade._amount)
    self.assertLess(max(amounts) - min(amounts), 1e-12)

  def test_reversal_is_continuous_and_passes_through_neutral(self):
    fade = WheelFeedbackFade()
    gas, brake = 2., -2.
    for frame in range(7):
      green = fade.update(gas, True, NOW + frame * 100_000_000, 1)
    self.assertEqual(fade.update(brake, True, NOW + 600_000_000, 1), green)
    self.assertEqual(fade._target, -1)
    fade.update(brake, True, NOW + 650_000_000, 1)
    self.assertGreater(fade._amount, 0)
    rgb = fade.update(brake, True, NOW + 660_000_000, 1)
    self.assertLess(fade._amount, 0)
    self.assertEqual(rgb[0], 255)
    self.assertEqual(rgb[1], rgb[2])
    before = fade._amount
    fade.update(gas, True, NOW + 670_000_000, 1)
    self.assertEqual(fade._target, 1)
    self.assertGreater(fade._amount, before)
    before = fade._amount
    fade.update(0., True, NOW + 680_000_000, 1)
    self.assertEqual(fade._target, 0)
    self.assertLess(abs(fade._amount), abs(before))

  def test_disabled_invalid_drive_and_clock_reset(self):
    weak = .2
    for value, enabled, now, drive in (
      (weak, False, NOW + 300_000_000, 1), (None, True, NOW + 300_000_000, 1),
      (observe_wheel_acceleration(car(aEgo=float('nan'), brakePressed=True)), True, NOW + 300_000_000, 1),
      (observe_wheel_acceleration(car(aEgo=float('inf'), brakePressed=True)), True, NOW + 300_000_000, 1),
      (weak, True, NOW + 300_000_000, 2), (weak, True, NOW + 100_000_000, 1),
    ):
      with self.subTest(value=value, enabled=enabled, now=now, drive=drive):
        fade = WheelFeedbackFade()
        for frame in range(3):
          fade.update(2., True, NOW + frame * 100_000_000, 1)
        self.assertGreater(fade._amount, .9)
        self.assertEqual(fade.update(value, enabled, now, drive), (255, 255, 255))
        self.assertEqual(fade._target, 0)
        self.assertEqual(fade._amount, 0)

  def test_fresh_braking_survives_render_hitches_and_low_frame_rates(self):
    value = -2.
    for interval_ns in (201_000_000, 250_000_000, 1_000_000_000):
      with self.subTest(interval_ns=interval_ns):
        fade = WheelFeedbackFade()
        self.assertEqual(fade.update(value, True, NOW, 1), (255, 255, 255))
        for frame in range(1, 7):
          fade.update(value, True, NOW + frame * 100_000_000, 1)
        for frame in range(1, 4):
          self.assertEqual(fade.update(value, True, NOW + 600_000_000 + frame * interval_ns, 1), BRAKE_RGB)

  def test_first_timed_sample_fades_in_without_drive_identity(self):
    fade = WheelFeedbackFade()
    value = -2.
    self.assertEqual(fade.update(value, True, NOW, None), (255, 255, 255))
    self.assertGreater(fade.update(value, True, NOW + 100_000_000, None)[1], 0)
    self.assertLess(fade._amount, 0)

  def test_untimed_samples_are_settled_and_do_not_change_live_fade(self):
    fade = WheelFeedbackFade()
    brake = -2.
    fade.update(brake, True, NOW, 1)
    partial = fade.update(brake, True, NOW + 100_000_000, 1)
    for value, enabled, expected in (
      (brake, True, BRAKE_RGB), (2., True, ACCEL_RGB),
      (-1., True, (255, 128, 128)), (.5, True, (197, 223, 207)), (.2, True, (255, 255, 255)),
      (brake, False, (255, 255, 255)), (None, True, (255, 255, 255)),
    ):
      self.assertEqual(fade.update(value, enabled, 0, None), expected)
    self.assertEqual(fade.update(brake, True, NOW + 100_000_000, 1), partial)


class WheelPreferenceTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    self.parked = True
    self.owner = AppearanceOwner(self.params, lambda: self.parked)

  def path(self, key):
    return Path(self.params.get_param_path(key))

  def row(self, key="ShowBrakeStatus", profile=Profile.LARGE):
    return next(row for row in self.owner.snapshot(profile).rows if row.key == key)

  def test_original_default_compatibility_and_single_effective_control(self):
    self.assertFalse(onroad_appearance(self.params).wheel_pedal_feedback)
    for profile in Profile:
      self.assertEqual(self.row(profile=profile).value, "Off")
    self.path("PedalsOnUI").write_bytes(b"1")
    self.assertTrue(onroad_appearance(self.params).wheel_pedal_feedback)
    request = required_change(self.row())
    self.assertEqual(request.value, "Off")
    self.assertTrue(self.owner.apply(request))
    self.assertFalse(onroad_appearance(self.params).wheel_pedal_feedback)
    self.assertEqual(self.path("PedalsOnUI").read_bytes(), b"0")
    self.assertEqual(self.path("ShowBrakeStatus").read_bytes(), b"0")
    self.assertTrue(self.owner.apply(required_change(self.row())))
    self.assertTrue(onroad_appearance(self.params).wheel_pedal_feedback)
    self.assertEqual(self.path("PedalsOnUI").read_bytes(), b"0")

  def test_source_repair_and_parked_compound_guards(self):
    request = required_change(self.row())
    self.path("PedalsOnUI").write_bytes(b"1")
    self.assertFalse(self.owner.apply(request))
    self.assertFalse(self.path("ShowBrakeStatus").exists())
    self.path("PedalsOnUI").write_bytes(b"broken")
    request = required_change(self.row())
    self.assertEqual(request.value, "Off")
    self.parked = False
    self.assertFalse(self.owner.apply(request))
    self.assertEqual(self.path("PedalsOnUI").read_bytes(), b"broken")
    self.parked = True
    self.assertTrue(self.owner.apply(request))
    request = required_change(self.row())
    actual = saved_document.os.fsync

    def revoke(fd):
      actual(fd)
      self.parked = False

    with patch.object(saved_document.os, "fsync", side_effect=revoke):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(self.path("ShowBrakeStatus").read_bytes(), b"0")

  def test_compound_failure_does_not_claim_atomic_success(self):
    self.path("PedalsOnUI").write_bytes(b"1")
    self.path("ShowBrakeStatus").write_bytes(b"1")
    request = required_change(self.row())
    original = saved_document.commit_exact

    def commit(params, **kwargs):
      if kwargs["key"] == "ShowBrakeStatus":
        return NS(committed=False, verified=False)
      return original(params, **kwargs)

    with patch("openpilot.starpilot.ui.appearance_owner.commit_exact", side_effect=commit):
      self.assertFalse(self.owner.apply(request))
    self.assertEqual(self.row().value, "On")
    self.assertTrue(onroad_appearance(self.params).wheel_pedal_feedback)
    self.assertTrue(self.owner.apply(required_change(self.row())))
    self.assertFalse(onroad_appearance(self.params).wheel_pedal_feedback)


if __name__ == "__main__":
  unittest.main()
