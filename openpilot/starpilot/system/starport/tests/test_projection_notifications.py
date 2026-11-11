"""Car-screen phone notification toast: feed snapshots, visibility rules and drawing without file IO in render."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
import unittest

from openpilot.starpilot.system.starport import projection_notifications as feed_module
from openpilot.starpilot.system.starport.phone_notifications import TOAST_NS, PhoneNotifications
from openpilot.starpilot.system.starport.projection_notifications import (ProjectionNotificationFeed, Toast, fit, plain,
                                                                         toast_visible, wrap)
from openpilot.starpilot.system.starport.tests import test_projection_onroad_boundary as boundary
from openpilot.starpilot.ui.onroad_state import OnroadState, SpeedLimitObservation

NOW = 50_000_000_000


class Fonts:
  """Every character is 10 px wide at size 10."""
  def measure(self, text, role, size):
    return SimpleNamespace(width=len(text) * size, height=size)


def toast(sequence=1, text='On my way', hide_while_moving=False):
  return Toast(sequence, NOW, 'WhatsApp', 'Alice', text, hide_while_moving)


class TestText(unittest.TestCase):
  def test_plain_folds_to_drawable_ascii_and_truncates(self):
    self.assertEqual(plain('Café “now”\n\nthanks… \U0001F600', 80), 'Cafe "now" thanks...')
    self.assertEqual(plain('x' * 100, 80), 'x' * 77 + '...')
    self.assertEqual(len(plain('word ' * 40, 80)), 80)

  def test_fit_and_wrap_use_measured_width(self):
    fonts = Fonts()
    self.assertEqual(fit(fonts, 'r', 1, 'hello', 5), 'hello')
    self.assertEqual(fit(fonts, 'r', 1, 'hello world', 8), 'hello...')
    self.assertEqual(wrap(fonts, 'r', 1, 'one two three four five', 9), ('one two', 'three...'))
    self.assertEqual(wrap(fonts, 'r', 1, 'short', 9), ('short',))

  def test_visibility_window_and_motion(self):
    self.assertTrue(toast_visible(toast(), NOW + TOAST_NS, started=True, speed=30))
    self.assertFalse(toast_visible(toast(), NOW + TOAST_NS + 1, started=False, speed=None))
    self.assertFalse(toast_visible(toast(), NOW - 1, started=False, speed=None))
    hiding = toast(hide_while_moving=True)
    self.assertFalse(toast_visible(hiding, NOW, started=True, speed=0.6))
    self.assertFalse(toast_visible(hiding, NOW, started=True, speed=None))
    self.assertTrue(toast_visible(hiding, NOW, started=True, speed=0.5))
    self.assertTrue(toast_visible(hiding, NOW, started=False, speed=None))


class TestFeed(unittest.TestCase):
  def test_feed_reads_handoff_and_settings_off_thread(self):
    import tempfile
    with tempfile.TemporaryDirectory() as root:
      owner = PhoneNotifications(Path(root) / 'settings.json', Path(root) / 'shm.json', lambda: NOW,
                                 boot_id=lambda: 'b', boot_offset=lambda: 0)

      def read():
        return feed_module.phone_notifications.current(NOW, owner.handoff, boot_id=lambda: 'b', boot_offset=lambda: 0)
      feed = ProjectionNotificationFeed(read=read, settings_path=owner.settings_path, start=False)
      feed.poll()
      self.assertIsNone(feed.snapshot())
      owner.publish({'source': 'ios', 'app': 'com.apple.MobileSMS', 'appName': '', 'sender': 'Bob', 'text': 'Late — sorry',
                     'category': 'message', 'ageMs': 0})
      feed.poll()
      shown = feed.snapshot()
      self.assertEqual((shown.app, shown.sender, shown.text, shown.hide_while_moving), ('com.apple.MobileSMS', 'Bob', 'Late - sorry', False))
      owner.update({'hideWhileMoving': True})
      feed.poll()
      self.assertTrue(feed.snapshot().hide_while_moving)
      owner.update({'enabled': False})
      feed.poll()
      self.assertIsNone(feed.snapshot())

  def test_feed_survives_reader_errors_and_closes(self):
    def broken():
      raise OSError('gone')
    feed = ProjectionNotificationFeed(read=broken, settings_path='/nonexistent/settings.json')
    feed.poll()
    self.assertIsNone(feed.snapshot())
    feed.close()
    assert feed._thread is not None
    self.assertFalse(feed._thread.is_alive())


class TestRendererToast(unittest.TestCase):
  def setUp(self):
    self.native, _ = boundary.TestProjectionOnroad.dependencies(cast(Any, self))
    self.drawn, self.panels = [], []
    native = self.native

    @dataclass(frozen=True)
    class Alert:
      size: str = 'none'
      text1: str = ''
      alert_type: str = ''

    self.alert = {'value': Alert()}
    self.Alert = Alert
    native.alert, native.alert_size = Alert, SimpleNamespace(NONE='none', SMALL='small')
    native.adapter = lambda *_: SimpleNamespace(build=lambda *_a, **_k: SimpleNamespace(
      onroad=OnroadState(False, False, None, None, SpeedLimitObservation(), alert=cast(Any, self.alert['value']))))
    native.font_role = SimpleNamespace(BRAND='brand', MEDIUM='medium', BOLD='bold')
    native.rl = SimpleNamespace(**{name: getattr(boundary.FakeGraphics, name) for name in dir(boundary.FakeGraphics) if not name.startswith('_')})
    native.rl.draw_rectangle_rounded = lambda rect, *_: self.panels.append(rect)
    self.car = {'value': None}
    native.display_message = lambda _sm, service, *_a, **_k: self.car['value'] if service == 'carState' else None
    self.feed = SimpleNamespace(value=None, closed=0)
    self.feed.snapshot = lambda: self.feed.value
    self.feed.close = lambda: setattr(self.feed, 'closed', self.feed.closed + 1)
    native.notification_feed = lambda: self.feed
    drawn = self.drawn

    class Fonts:
      def __init__(self, profile, directory, *, headless_context):
        self.measured = 0
      def draw(self, text, role, size, *_args): drawn.append((text, role))
      def measure(self, text, role, size):
        self.measured += 1
        return SimpleNamespace(width=len(text) * size * .5, height=size)
      def close(self): pass

    native.fonts = Fonts
    self.clock = [NOW]
    original = boundary.projection.time
    boundary.projection.time = SimpleNamespace(monotonic_ns=lambda: self.clock[0])
    self.addCleanup(setattr, boundary.projection, 'time', original)
    self.view = boundary.projection.ProjectionOnroad(dependencies=native, viewport=(1920, 1080))
    self.view.onroad.render = lambda state: None

  def texts(self):
    return [text for text, _ in self.drawn]

  def test_toast_draws_in_standby_and_driving_then_expires(self):
    self.feed.value = toast(text='On my way, see you soon')
    self.view.render()
    self.assertIn('Alice', self.texts())
    self.assertIn('WhatsApp', self.texts())
    self.assertIn('On my way, see you soon', self.texts())
    self.assertEqual(self.panels[-1][1], 24)  # near the top
    self.assertAlmostEqual(self.panels[-1][0] + self.panels[-1][2] / 2, 960)
    self.drawn.clear()
    self.native.ui_state.started = True
    self.view.render()
    self.assertIn('Alice', self.texts())
    self.drawn.clear()
    self.clock[0] = NOW + TOAST_NS + 1
    self.view.render()
    self.assertNotIn('Alice', self.texts())

  def test_layout_is_measured_once_per_toast(self):
    self.feed.value = toast(text='word ' * 30)
    self.view.render()
    measured = self.view.fonts.measured
    self.assertGreater(measured, 0)
    for _ in range(5):
      self.view.render()
    self.assertEqual(self.view.fonts.measured, measured)
    self.feed.value = toast(sequence=2, text='another')
    self.view.render()
    self.assertGreater(self.view.fonts.measured, measured)

  def test_real_alert_wins_and_hide_while_moving(self):
    self.native.ui_state.started = True
    self.feed.value = toast()
    self.alert['value'] = self.Alert('mid', 'Take Control')
    self.view.render()
    self.assertNotIn('Alice', self.texts())
    self.alert['value'] = self.Alert()
    self.feed.value = toast(hide_while_moving=True)
    self.car['value'] = SimpleNamespace(vEgo=12.)
    self.view.render()
    self.assertNotIn('Alice', self.texts())
    self.car['value'] = None
    self.view.render()
    self.assertNotIn('Alice', self.texts())  # unknown speed while driving hides
    self.car['value'] = SimpleNamespace(vEgo=0.)
    self.view.render()
    self.assertIn('Alice', self.texts())

  def test_synthetic_notice_does_not_hide_the_toast(self):
    self.native.ui_state.started = True
    self.feed.value = toast()
    self.view.bookmark_saved(NOW)
    self.view.render()
    self.assertIn('Alice', self.texts())

  def test_feed_closes_with_the_renderer(self):
    self.view.close()
    self.assertEqual(self.feed.closed, 1)



class TestComma3XScreen(unittest.TestCase):
  def setUp(self):
    from openpilot.starpilot.ui import runtime_app
    self.app = runtime_app
    self.session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    self.session.fonts = None
    self.drawn = []
    self.session._toast_painter = SimpleNamespace(draw=lambda rl, fonts, roles, value, w, h: self.drawn.append((value.sender, w, h)))
    self.session.notification_feed = SimpleNamespace(snapshot=lambda: self.toast)
    self.toast = toast()
    self.ui = SimpleNamespace(started=False)
    for name, value in (('ui_state', self.ui), ('time', SimpleNamespace(monotonic_ns=lambda: NOW + 1))):
      original = getattr(runtime_app, name)
      setattr(runtime_app, name, value)
      self.addCleanup(setattr, runtime_app, name, original)
    self.rect = SimpleNamespace(width=2160, height=1080)

  def onroad(self, size='none', speed=0.):
    from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert
    return OnroadState(False, True, speed, None, SpeedLimitObservation(), alert=OnroadAlert(AlertSize(size)))

  def test_draws_on_home_and_onroad_at_screen_size(self):
    self.session._notification_toast(None, self.rect)
    self.ui.started = True
    self.session._notification_toast(self.onroad(), self.rect)
    self.assertEqual(self.drawn, [('Alice', 2160, 1080)] * 2)

  def test_real_alert_and_moving_hide_it(self):
    self.ui.started = True
    self.session._notification_toast(self.onroad('mid'), self.rect)
    self.toast = toast(hide_while_moving=True)
    self.session._notification_toast(self.onroad(speed=12.), self.rect)
    self.assertEqual(self.drawn, [])
    self.session._notification_toast(self.onroad(speed=0.), self.rect)
    self.assertEqual(len(self.drawn), 1)

  def test_no_feed_or_no_toast_draws_nothing(self):
    self.toast = None
    self.session._notification_toast(None, self.rect)
    del self.session.notification_feed
    self.session._notification_toast(None, self.rect)
    self.assertEqual(self.drawn, [])


if __name__ == '__main__':
  unittest.main()
