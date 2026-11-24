"""Birds-eye stream HUD: acceleration bar colours, limits and geometry, flat torque bar, turn-signal arrows, the status
cluster (steering wheel, drive mode, follow distance), and per-frame cost with fakes."""
from types import SimpleNamespace
import time

import pytest

from openpilot.starpilot.system.starport import birds_eye_hud as hud_module
from openpilot.starpilot.system.starport.birds_eye_hud import (ACCEL_RANGE, AccelLimits, AccelReading,
                                                               BirdsEyeHud, RED, accel_geometry, accel_y, braking, dock_height,
                                                               fill_color, read_accel, read_limits, read_speeds,
                                                               readout_size, signal_alpha, torque_color, torque_geometry,
                                                               torque_strength, view_rect)
from openpilot.starpilot.ui.torque_feedback import TorqueFeedback

LOGICAL_720P = (1860, 1080)  # projection_geometry's logical canvas for a 1280x720 head unit


class FakeRl:
  def __init__(self):
    self.calls = []

  @staticmethod
  def Color(*values):
    return values

  @staticmethod
  def Rectangle(*values):
    return values

  @staticmethod
  def Vector2(*values):
    return values

  def __getattr__(self, name):
    if name.startswith('draw_'):
      return lambda *args: self.calls.append((name, *args))
    raise AttributeError(name)


class FakeText:
  def __init__(self):
    self.drawn = []

  def measure(self, text, size):
    return len(text) * size * 0.5, size

  def draw(self, text, size, x, y, color):
    self.drawn.append((text, color))


SERVICES = ('carState', 'carControl', 'longitudinalPlan', 'starpilotLongitudinalPlan', 'controlsState', 'carOutput',
            'vehicleParameters', 'selfdriveState', 'starpilotCarState', 'radarState')


class FakeSM:
  def __init__(self, **messages):
    self.messages = messages
    self.seen = {name: name in messages for name in SERVICES}

  def __getitem__(self, name):
    return self.messages[name]


def sm(*, desired=0.0, actual=0.0, long_active=True, brake=False, plan=None, limits=None, left=False, right=False,
       v_ego=None, v_cruise=255.0, cruise=True):
  """plan=None: no longitudinalPlan, so the bar falls back to the actuators' accel (`desired`)."""
  car_state = SimpleNamespace(aEgo=actual, brakePressed=brake, leftBlinker=left, rightBlinker=right,
                              vCruise=v_cruise, vCruiseCluster=v_cruise,
                              cruiseState=SimpleNamespace(enabled=cruise))
  if v_ego is not None:
    car_state.vEgo = v_ego
  messages = {'carState': car_state,
              'carControl': SimpleNamespace(longActive=long_active, actuators=SimpleNamespace(accel=desired))}
  if plan is not None:
    messages['longitudinalPlan'] = SimpleNamespace(accels=[plan], aTarget=plan)
  if limits is not None:
    messages['starpilotLongitudinalPlan'] = SimpleNamespace(accelLimitMin=limits[0], accelLimitMax=limits[1])
  return FakeSM(**messages)


def hud(source, feedback=lambda sm, cp: TorqueFeedback(0.4, 'angle_error', 'observed'), clock=lambda: 0.0):
  rl, text = FakeRl(), FakeText()
  return BirdsEyeHud(rl, text, source, feedback=feedback, clock=clock), rl, text


def accel_fills(rl, g):
  """Rounded rectangles centred in the acceleration strip, other than the full-height track."""
  return [call for call in rl.calls if call[0] == 'draw_rectangle_rounded' and call[1][0] + call[1][2] <= g.strip
          and call[1][3] < g.bottom - g.top - 1]


def torque_rects(rl):
  """The torque bar's rounded rectangles: track, indicator, notch."""
  g = accel_geometry(LOGICAL_720P[1])
  return [call for call in rl.calls if call[0] == 'draw_rectangle_rounded' and call[1][0] > g.strip]


def test_every_bar_track_is_the_same_glass_and_nothing_else_is_painted_behind_the_widgets():
  g = accel_geometry(LOGICAL_720P[1])
  view, rl, _ = status_hud(lambda: (status_sm(), None))
  view.render(*LOGICAL_720P)
  accel_track = next(call for call in rl.calls if call[0] == 'draw_rectangle_rounded' and call[1][1] == g.top)
  torque_track = torque_rects(rl)[0]
  assert accel_track[-1] == torque_track[-1] == hud_module.GLASS_FILL, 'one glass fill for both tracks'
  rims = [call for call in rl.calls if call[0] == 'draw_rectangle_rounded_lines_ex']
  assert [rim[1] for rim in rims] == [pytest.approx(accel_track[1]), pytest.approx(torque_track[1])], 'each with the rim, and no other panel'
  assert all(rim[-1] == hud_module.GLASS_RIM and rim[-2] == hud_module.GLASS_RIM_PX for rim in rims)
  # The only plain rectangle is the acceleration strip's solid background: no divider line, no band over the road.
  plain = [call for call in rl.calls if call[0] == 'draw_rectangle']
  assert [call[1:5] for call in plain] == [(0, 0, int(g.strip), LOGICAL_720P[1])]


def test_reading_shows_the_plan_target_only_while_long_active():
  assert read_accel(sm(desired=-1.0, plan=-0.6)).desired == -0.6, 'longitudinalPlan.aTarget, not the PID output'
  assert read_accel(sm(desired=-1.0)).desired == -1.0, 'no plan yet: the actuators fall back'
  reading = read_accel(sm(desired=-1.0, long_active=False, plan=0.7))
  assert reading.desired == 0.0 and not reading.long_active, 'the plan is not shown while disengaged'
  assert read_accel(FakeSM()) is None


def test_reading_falls_back_to_the_actuators_on_a_stale_plan():
  messages = sm(desired=-1.0, plan=-0.6)
  messages.recv_time = {'longitudinalPlan': 10.0}
  assert read_accel(messages, now_ns=int(10.5e9)).desired == -0.6
  assert read_accel(messages, now_ns=int(11.2e9)).desired == -1.0, 'older than 1 s'
  messages.alive = {'longitudinalPlan': False}
  assert read_accel(messages, now_ns=int(10.5e9)).desired == -1.0, 'not alive'


def test_measured_accel_tick_is_lightly_filtered():
  view, _, _ = hud(lambda: (sm(actual=0.0), None))
  assert view._actual.alpha == pytest.approx((1 / 30) / (hud_module.AEGO_RC + 1 / 30))
  assert hud_module.AEGO_RC == pytest.approx(0.15)


@pytest.mark.parametrize('reading, expected', [
  (AccelReading(-0.5, 0.0, True, False), True),
  (AccelReading(-0.1, 0.0, True, False), False),
  (AccelReading(-2.0, 0.0, False, False), False),  # not commanding: the plan's decel is not braking
  (AccelReading(1.0, 0.0, False, True), True),     # the pedal always is
  (None, False),
])
def test_braking(reading, expected):
  assert braking(reading) is expected


def test_braking_hysteresis():
  assert braking(AccelReading(-0.18, 0, True, False), was_braking=True)
  assert not braking(AccelReading(-0.18, 0, True, False), was_braking=False)


def test_fill_is_coloured_only_while_engaged():
  assert fill_color(-1.0, True, True)[:3] == RED
  assert fill_color(0.8, False, True)[:3] == hud_module.GREEN
  assert fill_color(-0.1, False, True)[:3] == hud_module.NEUTRAL
  for accel, is_braking in ((1.0, False), (-1.0, True)):
    color = fill_color(accel, is_braking, False)
    assert color[:3] == hud_module.IDLE and color[3] < 128


def test_bar_is_red_while_braking_on_screen():
  g = accel_geometry(LOGICAL_720P[1])
  view, rl, text = hud(lambda: (sm(desired=-1.5, actual=-1.2), None))
  view.render(*LOGICAL_720P)
  (fill,) = accel_fills(rl, g)
  assert fill[-1][:3] == RED
  assert text.drawn[0][0] == '-1.5' and text.drawn[0][1][:3] == RED

  view, rl, _ = hud(lambda: (sm(desired=1.0, brake=True), None))
  view.render(*LOGICAL_720P)
  assert accel_fills(rl, g)[0][-1][:3] == RED

  view, rl, _ = hud(lambda: (sm(desired=1.0), None))
  view.render(*LOGICAL_720P)
  assert accel_fills(rl, g)[0][-1][:3] == hud_module.GREEN


def test_disengaged_bar_is_grey_with_the_setpoint_at_zero():
  g = accel_geometry(LOGICAL_720P[1])
  view, rl, text = hud(lambda: (sm(desired=1.5, actual=0.8, long_active=False, plan=1.2, limits=(-1.2, 1.6)), None))
  view.render(*LOGICAL_720P)
  assert accel_fills(rl, g) == [], 'no fill away from zero'
  assert [entry for entry in text.drawn if not entry[0].endswith(('AM', 'PM'))] == [], 'only the clock'
  track = next(call for call in rl.calls if call[0] == 'draw_rectangle_rounded' and call[1][1] == g.top)
  assert track[-1] == hud_module.GLASS_FILL, 'the track is the same glass engaged or not'
  limit_ticks = [call for call in rl.calls if call[0] == 'draw_rectangle_rec' and call[-1][:3] == hud_module.LIMIT]
  assert limit_ticks == [], 'limit ticks only while engaged'
  actual = [call for call in rl.calls if call[0] == 'draw_rectangle_rec' and call[-1][:3] == hud_module.ACTUAL]
  assert len(actual) == 1 and actual[0][1][1] < g.zero, 'the measured accel tick still shows'
  # A residual setpoint decays back to zero, drawn grey.
  view._desired.x = 1.0
  rl.calls.clear()
  view.render(*LOGICAL_720P)
  (fill,) = accel_fills(rl, g)
  assert fill[-1][:3] == hud_module.IDLE and view._desired.x < 1.0


def limit_ticks(rl):
  return sorted(call[1][1] + call[1][3] / 2 for call in rl.calls
                if call[0] == 'draw_rectangle_rec' and call[-1][:3] == hud_module.LIMIT)


def test_scale_is_fixed_and_the_limit_ticks_move_on_it():
  g = accel_geometry(LOGICAL_720P[1])
  assert accel_y(g, 0.0) == pytest.approx(g.zero)
  assert accel_y(g, ACCEL_RANGE) == pytest.approx(g.top) and accel_y(g, -ACCEL_RANGE) == pytest.approx(g.bottom)
  assert accel_y(g, 1.5) == pytest.approx(g.zero - g.half / 2)
  assert accel_y(g, 9.0) == pytest.approx(g.top) and accel_y(g, -9.0) == pytest.approx(g.bottom), 'clamped at the ends'

  state = {'limits': (-1.2, 1.6), 'long_active': True}
  view, rl, _ = hud(lambda: (sm(desired=0.4, actual=0.3, long_active=state['long_active'], limits=state['limits']), None))

  def frame():
    rl.calls.clear()
    view.render(*LOGICAL_720P)
    fill = accel_fills(rl, g)
    actual = [call for call in rl.calls if call[0] == 'draw_rectangle_rec' and call[-1][:3] == hud_module.ACTUAL]
    return limit_ticks(rl), fill[0][1][1] if fill else None, actual[0][1][1]

  ticks, fill_top, actual_y = frame()
  assert ticks == pytest.approx([accel_y(g, 1.6), accel_y(g, -1.2)])
  assert fill_top == pytest.approx(accel_y(g, 0.4))
  # Tighter limits: the ticks move toward zero, the command and the measured tick stay where they were.
  state['limits'] = (-0.6, 0.8)
  for _ in range(60):
    moved, moved_fill, moved_actual = frame()
  assert moved == pytest.approx([accel_y(g, 0.8), accel_y(g, -0.6)], abs=0.5)
  assert (moved_fill, moved_actual) == pytest.approx((fill_top, actual_y))
  # Disengaging does not rescale either.
  state['long_active'] = False
  assert frame()[2] == pytest.approx(actual_y)


def test_zero_line_is_the_only_fixed_mark():
  g = accel_geometry(LOGICAL_720P[1])
  for long_active in (True, False):
    view, rl, _ = hud(lambda long_active=long_active: (sm(long_active=long_active), None))
    view.render(*LOGICAL_720P)
    marks = [call for call in rl.calls if call[0] == 'draw_rectangle_rec' and call[-1][:3] == hud_module.TICK]
    assert len(marks) == 1 and marks[0][1][1] + marks[0][1][3] / 2 == pytest.approx(g.zero)


def test_limit_ticks_stay_on_while_long_is_engaged():
  g = accel_geometry(LOGICAL_720P[1])
  state = {'limits': (-1.2, 1.6), 'long_active': True}
  view, rl, _ = hud(lambda: (sm(desired=0.0, long_active=state['long_active'], limits=state['limits']), None))

  def frame():
    rl.calls.clear()
    view.render(*LOGICAL_720P)
    return limit_ticks(rl)

  shown = frame()
  assert len(shown) == 2
  # The ceiling drops to zero while the model withholds throttle: its tick goes to the zero line, it does not vanish.
  state['limits'] = (-1.2, 0.0)
  for _ in range(60):
    ticks = frame()
  assert ticks == pytest.approx([g.zero, accel_y(g, -1.2)], abs=0.5)
  # The message drops out or turns implausible: the last limits are held.
  for dropout in (None, (-1.0, float('nan')), (0.5, 1.0)):
    state['limits'] = dropout
    assert frame() == pytest.approx(ticks, abs=0.5)
  state['long_active'] = False
  assert frame() == []
  # Re-engaged with no message yet: nothing stale from the last engagement.
  state['long_active'] = True
  assert frame() == []
  state['limits'] = (-0.6, 0.8)
  assert frame() == pytest.approx([accel_y(g, 0.8), accel_y(g, -0.6)])


def test_bar_grows_at_the_limit():
  g = accel_geometry(LOGICAL_720P[1])
  widths = {}
  for desired in (0.5, 1.6, 2.5):
    view, rl, _ = hud(lambda desired=desired: (sm(desired=desired, limits=(-1.2, 1.6)), None))
    view.render(*LOGICAL_720P)
    (fill,) = accel_fills(rl, g)
    widths[desired] = fill[1][2]
    if desired == 2.5:
      assert fill[1][1] == pytest.approx(accel_y(g, 2.5)) and fill[1][1] < accel_y(g, 1.6), 'passes the limit tick'
  assert widths[0.5] == pytest.approx(g.width) and widths[1.6] == pytest.approx(g.grown) == widths[2.5]
  assert g.grown == 2 * g.width
  # A ceiling of zero: any real command above it is at the limit.
  view, rl, _ = hud(lambda: (sm(desired=0.3, limits=(-1.2, 0.0)), None))
  view.render(*LOGICAL_720P)
  assert accel_fills(rl, g)[0][1][2] == pytest.approx(g.grown)
  # No limits read: the bar grows against its own ends.
  view, rl, _ = hud(lambda: (sm(desired=1.6), None))
  view.render(*LOGICAL_720P)
  assert accel_fills(rl, g)[0][1][2] == pytest.approx(g.width)


def test_limits_are_unread_when_absent_stale_or_implausible():
  assert read_limits(sm()) is None
  assert read_limits(sm(limits=(-1.0, 1.5))) == AccelLimits(-1.0, 1.5)
  assert read_limits(sm(limits=(-1.0, 0.0))) == AccelLimits(-1.0, 0.0), 'throttle withheld'
  assert read_limits(sm(limits=(0.0, 1.5))) == AccelLimits(0.0, 1.5)
  assert read_limits(sm(limits=(0.0, 0.0))) is None, 'an unset message'
  assert read_limits(sm(limits=(0.5, 1.5))) is None and read_limits(sm(limits=(-1.0, -0.5))) is None
  assert read_limits(sm(limits=(-1.0, float('nan')))) is None
  stale = sm(limits=(-1.0, 1.5))
  stale.alive = {'starpilotLongitudinalPlan': False}
  assert read_limits(stale) is None
  old = sm(limits=(-1.0, 1.5))
  old.recv_time = {'starpilotLongitudinalPlan': 1.0}
  assert read_limits(old, now_ns=3_000_000_000) is None and read_limits(old, now_ns=1_500_000_000) is not None
  view, rl, _ = hud(lambda: (sm(desired=0.4), None))
  view.render(*LOGICAL_720P)
  assert limit_ticks(rl) == [], 'engaged before any limits were published: nothing to mark'


def test_geometry_at_1280x720():
  g = accel_geometry(LOGICAL_720P[1])
  assert g.width == 28 and g.strip == 112 and g.x + g.width < g.strip
  assert g.center - g.grown / 2 > 0 and g.center + g.grown / 2 < g.strip
  assert g.bottom - g.top == pytest.approx(0.75 * LOGICAL_720P[1])
  t = torque_geometry(*LOGICAL_720P)
  vx, vy, vw, vh = view_rect(*LOGICAL_720P)
  assert (vx, vy, vx + vw) == (g.strip, 0, LOGICAL_720P[0])
  assert t.cx == pytest.approx(vx + vw / 2), 'centred under the view, on the ego car'
  assert t.right - t.left == pytest.approx(0.75 * LOGICAL_720P[0]) and t.left > g.strip
  assert (t.thin, t.thick) == (28, 112), '2x the arc thickness'
  assert vh == LOGICAL_720P[1] - dock_height(LOGICAL_720P[1])
  assert t.bottom - t.thick > vh, 'the grown bar never reaches into the widget area'
  assert hud_module.render_rect(*LOGICAL_720P) == (vx, 0, vw, LOGICAL_720P[1]), 'the road itself runs on under the bar'
  physical = accel_geometry(720)
  assert physical.width == 19 and physical.strip == 74


def test_filter_smooths_steps():
  view, _, _ = hud(lambda: (sm(desired=2.0, actual=0.0), None))
  view._desired.x = 0.0
  view.render(*LOGICAL_720P)
  assert 0.0 < view._desired.x < 1.0


def test_torque_strength_and_colour_follow_the_arc():
  assert torque_strength(0.4) == 0 and torque_strength(0.75) == pytest.approx(0.5) and torque_strength(-1.0) == 1
  assert torque_color(0.5, 1.0)[:3] == (255, 255, 255)
  warm = torque_color(1.0, 1.0)
  assert warm[0] == 255 and warm[2] == 0 and warm[1] < 255


def test_torque_bar_is_flat_and_stays_visible_when_inactive():
  t = torque_geometry(*LOGICAL_720P)
  view, rl, _ = hud(lambda: (sm(), 'CP'))
  view.render(*LOGICAL_720P)
  rl.calls.clear()
  view.render(*LOGICAL_720P)
  track, indicator, notch = torque_rects(rl)
  assert track[1] == pytest.approx((t.left, t.bottom - t.thin, t.right - t.left, t.thin))
  assert indicator[1][0] == pytest.approx(t.cx) and indicator[1][2] == pytest.approx(0.4 * (t.right - t.left) / 2)
  assert notch[1][0] < t.cx < notch[1][0] + notch[1][2]

  def inactive(sm, cp):
    return TorqueFeedback(0.0, 'lateral_acceleration_demand', 'lateral_inactive')
  for feedback in (inactive, lambda sm, cp: TorqueFeedback()):
    view, rl, _ = hud(lambda: (sm(), 'CP'), feedback=feedback)
    view.render(*LOGICAL_720P)
    track, notch = torque_rects(rl)
    assert track[1][2] == pytest.approx(t.right - t.left) and track[-1] == hud_module.GLASS_FILL


def test_torque_bar_grows_and_returns_to_centre():
  t = torque_geometry(*LOGICAL_720P)
  state = {'feedback': TorqueFeedback(-1.0, 'angle_error', 'observed')}
  view, rl, _ = hud(lambda: (sm(), 'CP'), feedback=lambda sm, cp: state['feedback'])
  for _ in range(60):
    view.render(*LOGICAL_720P)
  rl.calls.clear()
  view.render(*LOGICAL_720P)
  track, indicator = torque_rects(rl)
  assert track[1][3] == pytest.approx(t.thick, rel=0.02)
  assert indicator[1][0] + indicator[1][2] == pytest.approx(t.cx) and indicator[1][0] < t.left + 5, 'reaches left'
  state['feedback'] = TorqueFeedback(0.0, 'lateral_acceleration_demand', 'lateral_inactive')
  for _ in range(60):
    view.render(*LOGICAL_720P)
  assert abs(view._torque.x) < 0.01 and view._torque_alpha.x < 0.01


def test_signal_pulse_matches_the_comma_period():
  period = hud_module.TURN_SIGNAL_BLINK_PERIOD
  assert period == pytest.approx(0.75)
  assert signal_alpha(0.0) == 1.0 and signal_alpha(period * 0.95) < 0.45
  assert signal_alpha(period) == 1.0, 'restarts each period'


def test_turn_signal_arrows_in_the_corners_with_the_fallback_polygon():
  now = {'t': 0.0}
  view, rl, _ = hud(lambda: (sm(left=True), None), clock=lambda: now['t'])
  view.render(*LOGICAL_720P)
  assert view._icon_failed, 'the fake rl has no image loader'
  triangles = [call for call in rl.calls if call[0] == 'draw_triangle']
  assert len(triangles) == 1
  xs = [point[0] for point in triangles[0][1:4]]
  ys = [point[1] for point in triangles[0][1:4]]
  g = accel_geometry(LOGICAL_720P[1])
  assert g.strip < min(xs) and max(xs) < LOGICAL_720P[0] / 4, 'left edge, right of the accel strip'
  assert max(ys) - min(ys) == pytest.approx(0.6 * hud_module.SIGNAL_HEIGHT)
  assert (max(ys) + min(ys)) / 2 == pytest.approx(LOGICAL_720P[1] / 2), 'centred vertically on the frame'

  (ax, ay), (bx, by), (cx, cy) = triangles[0][1:4]
  assert (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0, 'counter-clockwise for raylib'
  bright = triangles[0][-1][3]
  now['t'] = 0.7
  rl.calls.clear()
  view.render(*LOGICAL_720P)
  assert next(call for call in rl.calls if call[0] == 'draw_triangle')[-1][3] < bright, 'pulses'

  view, rl, _ = hud(lambda: (sm(right=True), None))
  view.render(*LOGICAL_720P)
  (triangle,) = [call for call in rl.calls if call[0] == 'draw_triangle']
  assert min(point[0] for point in triangle[1:4]) > LOGICAL_720P[0] * 3 / 4, 'right edge'
  ys = [point[1] for point in triangle[1:4]]
  assert (max(ys) + min(ys)) / 2 == pytest.approx(LOGICAL_720P[1] / 2)
  (ax, ay), (bx, by), (cx, cy) = triangle[1:4]
  assert (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0

  view, rl, _ = hud(lambda: (sm(), None))
  view.render(*LOGICAL_720P)
  assert not [call for call in rl.calls if call[0] in ('draw_triangle', 'draw_texture_pro')]


def test_turn_signal_texture_is_loaded_once_flipped_for_the_right_and_unloaded():
  class TextureRl(FakeRl):
    def __init__(self):
      super().__init__()
      self.TextureFilter = SimpleNamespace(TEXTURE_FILTER_BILINEAR=1)
      self.loaded, self.unloaded = 0, []

    def load_image(self, path):
      assert path.endswith('icons_mici/onroad/turn_signal_left.png')
      return SimpleNamespace(data=b'x', width=208, height=192)

    def is_image_valid(self, image):
      return bool(image.data) and image.width > 0 and image.height > 0

    def image_resize(self, image, w, h):
      image.width, image.height = w, h

    def load_texture_from_image(self, image):
      self.loaded += 1
      return SimpleNamespace(id=7, width=image.width, height=image.height)

    def unload_image(self, image):
      pass

    def set_texture_filter(self, texture, mode):
      pass

    def unload_texture(self, texture):
      self.unloaded.append(texture.id)

  rl = TextureRl()
  view = BirdsEyeHud(rl, FakeText(), lambda: (sm(left=True, right=True), None),
                     feedback=lambda sm, cp: TorqueFeedback(), clock=lambda: 0.0)
  view.render(*LOGICAL_720P)
  view.render(*LOGICAL_720P)
  assert rl.loaded == 1
  draws = [call for call in rl.calls if call[0] == 'draw_texture_pro']
  left, right = draws[:2]
  assert left[2][2] > 0 and right[2][2] < 0, 'right side flipped horizontally'
  assert left[3][3] == pytest.approx(hud_module.SIGNAL_HEIGHT) and left[3][0] > accel_geometry(LOGICAL_720P[1]).strip
  assert right[3][0] + right[3][2] < LOGICAL_720P[0]
  for quad in (left[3], right[3]):
    assert quad[1] + quad[3] / 2 == pytest.approx(LOGICAL_720P[1] / 2), 'centred vertically on the frame'
  view.close()
  assert rl.unloaded == [7]


class SizedText(FakeText):
  def draw(self, text, size, x, y, color):
    self.drawn.append((text, size, x, y))


def test_speed_readout_shows_speed_and_set_speed_at_twice_the_lead_label():
  from openpilot.selfdrive.ui.onroad.birds_eye_math import label_font_size
  assert read_speeds(sm(v_ego=26.8224, v_cruise=112.654), metric=False) == ('60', '70')
  assert read_speeds(sm(v_ego=26.8224, v_cruise=112.654), metric=True) == ('97', '113')
  assert read_speeds(sm(v_ego=10.0, v_cruise=255.0), metric=False) == ('22', '--'), 'cruise not set'
  assert read_speeds(sm(v_ego=10.0, v_cruise=0.0), metric=False)[1] == '--'
  assert read_speeds(sm(v_ego=26.8224, v_cruise=112.654, cruise=False), metric=False) == ('60', '--'), \
    'lateral-only or disengaged: the car still reports the old set speed, the readout does not'
  assert read_speeds(sm(), metric=False) is None, 'no vEgo'
  for width, height in (LOGICAL_720P, (1280, 720), (3840, 2160)):
    lead_label = label_font_size(view_rect(width, height)[3])
    assert readout_size(width, height) == pytest.approx(2 * lead_label)

  text = SizedText()
  view = BirdsEyeHud(FakeRl(), text, lambda: (sm(v_ego=26.8224, v_cruise=112.654, long_active=False), None),
                     feedback=lambda sm, cp: TorqueFeedback(), clock=lambda: 0.0)
  view.render(*LOGICAL_720P)
  (speed, speed_size, sx, sy), (mph, cap_size, _, cy), (cruise, cruise_size, cx, _), (unit, _, _, _) = text.drawn[:4]
  assert (speed, mph, cruise, unit) == ('60', 'mph', '70', 'SET')
  assert speed_size == cruise_size == pytest.approx(2 * label_font_size(view_rect(*LOGICAL_720P)[3]))
  assert cap_size < speed_size / 2
  g = accel_geometry(LOGICAL_720P[1])
  assert g.strip < sx < cx and sy < 0.1 * LOGICAL_720P[1] and cy > sy, 'top left of the view, set speed to the right'
  view.metric = True
  text.drawn.clear()
  view.render(*LOGICAL_720P)
  assert [entry[0] for entry in text.drawn[:4]] == ['97', 'km/h', '113', 'SET']


def test_cpu_cost_per_frame_is_small():
  view, _, _ = hud(lambda: (sm(desired=-0.6, actual=-0.4, limits=(-1.2, 1.6), left=True, v_ego=20.0, v_cruise=100.0), None))
  view.render(*LOGICAL_720P)
  start = time.perf_counter()
  for _ in range(300):
    view.render(*LOGICAL_720P)
  assert (time.perf_counter() - start) / 300 < 0.001


def test_the_clock_sits_at_the_top_right_in_twelve_hour_time():
  import time as _time
  from openpilot.starpilot.system.starport.birds_eye_hud import CLOCK_SCALE, clock_text
  for hour, minute, expected in ((0, 5, '12:05 AM'), (9, 30, '9:30 AM'), (12, 0, '12:00 PM'), (16, 7, '4:07 PM')):
    assert clock_text(_time.mktime((2026, 11, 9, hour, minute, 0, 0, 0, -1))) == expected
  now = [_time.mktime((2026, 11, 9, 16, 7, 0, 0, 0, -1))]
  text = SizedText()
  text.measure = lambda value, size: (len(value) * size * 0.5, size)
  view = BirdsEyeHud(FakeRl(), text, lambda: (sm(), None), feedback=lambda sm, cp: TorqueFeedback(), clock=lambda: 0.0,
                     wall_clock=lambda: now[0])
  width, height = LOGICAL_720P
  view.render(width, height)
  value, size, x, y = text.drawn[-1]
  assert value == '4:07 PM' and size == pytest.approx(readout_size(width, height) * CLOCK_SCALE)
  assert x + len(value) * size * 0.5 == pytest.approx(width - 28 * height / 1080, abs=1) and y < 0.1 * height
  now[0] += 60
  view.render(width, height)
  assert text.drawn[-1][0] == '4:08 PM'


# --- status cluster ---
def status_sm(*, angle=12.4, lat_active=True, experimental=False, enabled=True, personality=1, traffic=None,
              lead=None, v_ego=20.0):
  """lead: dRel in metres of a present leadOne, or None for no lead. traffic=None: starpilotCarState unseen."""
  car_state = SimpleNamespace(aEgo=0.0, brakePressed=False, leftBlinker=False, rightBlinker=False, vCruise=255.0,
                              vCruiseCluster=255.0, cruiseState=SimpleNamespace(enabled=False), vEgo=v_ego,
                              steeringAngleDeg=angle)
  messages = {'carState': car_state,
              'carControl': SimpleNamespace(longActive=False, latActive=lat_active, actuators=SimpleNamespace(accel=0.0)),
              'selfdriveState': SimpleNamespace(experimentalMode=experimental, enabled=enabled,
                                                personality=SimpleNamespace(raw=personality)),
              'radarState': SimpleNamespace(leadOne=SimpleNamespace(present=lead is not None, dRel=lead or 0.0))}
  if traffic is not None:
    messages['starpilotCarState'] = SimpleNamespace(trafficModeEnabled=traffic)
  return FakeSM(**messages)


def test_steering_reader():
  from openpilot.starpilot.system.starport.birds_eye_hud import SteeringReading, read_steering
  assert read_steering(status_sm(angle=12.4)) == SteeringReading(12.4, True)
  assert read_steering(status_sm(angle=-90.0, lat_active=False)) == SteeringReading(-90.0, False)
  assert read_steering(FakeSM()) is None, 'no carState yet'
  assert read_steering(sm()) is None, 'a carState without a steering angle'
  assert read_steering(status_sm(angle=float('nan'))) is None
  only_car = FakeSM(carState=SimpleNamespace(steeringAngleDeg=3.0))
  assert read_steering(only_car) == SteeringReading(3.0, False), 'carControl unseen: not steering'
  assert read_steering(SimpleNamespace(seen={})) is None, 'a SubMaster without the service'


def test_drive_mode_reader():
  from openpilot.starpilot.system.starport.birds_eye_hud import DriveMode, read_drive_mode
  assert read_drive_mode(status_sm(experimental=True)) == DriveMode(True, True)
  assert read_drive_mode(status_sm(experimental=True)).label == 'EXPERIMENTAL'
  chill = read_drive_mode(status_sm(enabled=False))
  assert chill == DriveMode(False, False) and chill.label == 'CHILL'
  assert read_drive_mode(sm()) is None, 'selfdriveState unseen'
  assert read_drive_mode(SimpleNamespace(seen={})) is None


def test_follow_reader():
  from openpilot.starpilot.system.starport.birds_eye_hud import FollowReading, personality_index, read_follow
  for raw, name in enumerate(('AGGRESSIVE', 'STANDARD', 'RELAXED')):
    assert read_follow(status_sm(personality=raw)) == FollowReading(raw + 1, name, False), 'bars = raw + 1'
  assert personality_index(2) == 2 and personality_index('relaxed') == 2 and personality_index(SimpleNamespace(raw=0)) == 0
  assert personality_index(7) is None and personality_index(None) is None and personality_index('sport') is None
  traffic = read_follow(status_sm(personality=0, traffic=True))
  assert (traffic.bars, traffic.label, traffic.traffic) == (1, 'TRAFFIC', True)
  assert read_follow(status_sm(traffic=False)).label == 'STANDARD'
  assert not hasattr(hud_module, 'time_gap') and read_follow(status_sm(lead=36.0)) == read_follow(status_sm()), 'the setting only, no measured gap'
  assert read_follow(sm()) is None, 'selfdriveState unseen'
  unreadable = status_sm()
  unreadable.messages['selfdriveState'].personality = SimpleNamespace(raw=9)
  assert read_follow(unreadable) is None


def overlap(a, b):
  return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@pytest.mark.parametrize('width, height', [(1920, 1080), LOGICAL_720P, (1280, 720), (2880, 1080)])
def test_status_widgets_overlap_nothing(width, height):
  from openpilot.starpilot.system.starport.birds_eye_hud import status_geometry
  from openpilot.starpilot.system.starport.projection_onroad import birds_eye_card_placement
  k = height / 1080
  g = status_geometry(width, height)
  strip = accel_geometry(height).strip
  vx, vy, vw, vh = view_rect(width, height)
  margin = hud_module.SIGNAL_MARGIN * k
  size = readout_size(width, height)
  readout = (strip, 0, vw / 2, margin + hud_module.READOUT_ROWS * size)
  clock = (vx + vw / 2, 0, vw / 2, margin + hud_module.CLOCK_ROWS * hud_module.CLOCK_SCALE * size)
  arrow_h = hud_module.SIGNAL_HEIGHT * k
  arrow_w = arrow_h * 208 / 192
  arrows = [(strip + margin, height / 2 - arrow_h / 2, arrow_w, arrow_h), (width - margin - arrow_w, height / 2 - arrow_h / 2, arrow_w, arrow_h)]
  sign_h = max(56.0, 0.12 * height)   # birds_eye_renderer._speed_limit_sign: right-aligned, below the turn arrow's reserve
  sign = (width - 16 - 0.8 * sign_h, (hud_module.SIGNAL_MARGIN + hud_module.SIGNAL_HEIGHT) * k, 0.8 * sign_h, sign_h)
  t = torque_geometry(width, height)
  torque = (t.left, t.bottom - t.thick, t.right - t.left, t.thick)
  # The chips sit under the speed readout at the left margin, the mode chip over the follow chip.
  assert g.mode[0] == g.follow[0] == pytest.approx(strip + margin)
  assert readout[1] + readout[3] < g.mode[1] and g.mode[1] + g.mode[3] < g.follow[1], 'under the readout, stacked'
  # The wheel pill sits under the clock at the right margin, above the speed-limit sign.
  assert g.wheel[0] + g.wheel[2] == pytest.approx(width - margin) and clock[1] + clock[3] < g.wheel[1]
  assert g.wheel[1] + g.wheel[3] <= sign[1], 'ends above the speed-limit sign'
  cx, cy, r = g.badge
  assert (cx - r, cx + r, cy - r) == pytest.approx((g.wheel[0], g.wheel[0] + g.wheel[2], g.wheel[1])), 'the badge is the top of its box'
  assert cy + r < g.wheel[1] + g.wheel[3], 'rows left under the badge for the degrees'
  # The now-playing card where the birds-eye screen draws it: bottom left, small and big.
  cards = []
  for card_size in ('small', 'big'):
    placed = {'x': width - 30 - 520, 'y': height - 30 - 96, 'enabled': True, 'size': card_size}   # the layout's default
    placed = birds_eye_card_placement(placed, width, height, (vx, vy, vw, vh))
    cards.append((placed['x'], placed['y'], *((520, 96) if card_size == 'small' else (640, 264))))
  pills = g.pills
  for index, pill in enumerate(pills):
    assert pill[0] >= strip and pill[0] + pill[2] <= width and pill[1] >= 0
    for other in (*pills[index + 1:], *arrows, torque, *cards):
      assert not overlap(pill, other), (pill, other)
  for chip in (g.mode, g.follow):
    assert not overlap(chip, readout) and not overlap(chip, clock)
  assert not overlap(g.wheel, clock) and not overlap(g.wheel, sign)
  for card in cards:
    assert card[0] == pytest.approx(strip + margin, abs=1) and card[1] + card[3] <= vh, 'bottom left, above the torque bar'
    assert not overlap(card, torque) and not overlap(card, readout)
  assert not overlap(cards[0], arrows[0]), 'the small card is clear of the left turn arrow'


def test_status_sizes_read_on_a_1280x720_car_screen():
  from openpilot.starpilot.system.starport.birds_eye_hud import status_geometry
  g = status_geometry(*LOGICAL_720P)
  assert 2 * g.badge[2] == pytest.approx(110)
  assert hud_module.CHIP_SIZE == 34 and g.mode[3] == g.follow[3] == pytest.approx(46)


class CountingText(FakeText):
  """Records (text, size, x, y, color) draws and every measured text."""
  def __init__(self):
    super().__init__()
    self.measured = []

  def measure(self, text, size):
    self.measured.append(text)
    return super().measure(text, size)

  def draw(self, text, size, x, y, color):
    self.drawn.append((text, size, x, y, color))


def status_hud(source, rl=None):
  rl, text = rl or FakeRl(), CountingText()
  return BirdsEyeHud(rl, text, source, feedback=lambda sm, cp: TorqueFeedback(), clock=lambda: 0.0), rl, text


def test_wheel_badge_follows_lateral_and_falls_back_to_a_drawn_wheel():
  from openpilot.starpilot.system.starport.birds_eye_hud import status_geometry
  g = status_geometry(*LOGICAL_720P)
  state = {'angle': 12.4, 'lat_active': True}
  view, rl, text = status_hud(lambda: (status_sm(angle=state['angle'], lat_active=state['lat_active']), None))
  view.render(*LOGICAL_720P)
  assert view._wheel_failed, 'the fake rl has no image loader'
  (badge,) = [call for call in rl.calls if call[0] == 'draw_circle_v' and call[2] == pytest.approx(g.badge[2])]
  assert badge[1] == pytest.approx(g.badge[:2]) and badge[-1][:3] == hud_module.GREEN
  spokes = [call for call in rl.calls if call[0] == 'draw_line_ex']
  assert len(spokes) == 3 and any(call[0] == 'draw_ring' and call[1] == pytest.approx(g.badge[:2]) for call in rl.calls)
  # The right-hand spoke: a left (positive) angle lifts it, counter-clockwise on screen.
  right = spokes[0]
  assert right[2][0] > g.badge[0] and right[2][1] < g.badge[1]
  assert not any(call[0].startswith('draw_rectangle_rounded') and call[1] == pytest.approx(g.wheel) for call in rl.calls), \
    'no panel behind the wheel: it sits under the clock on the view'
  angle = next(entry for entry in text.drawn if entry[0] == '12')
  assert angle[1] == pytest.approx(hud_module.ANGLE_SIZE) and g.badge[1] + g.badge[2] <= angle[3] < g.wheel[1] + g.wheel[3]
  assert g.wheel[0] < angle[2] < g.wheel[0] + g.wheel[2]

  state.update(angle=-0.3, lat_active=False)
  rl.calls.clear()
  text.drawn.clear()
  view.render(*LOGICAL_720P)
  (badge,) = [call for call in rl.calls if call[0] == 'draw_circle_v' and call[2] == pytest.approx(g.badge[2])]
  assert badge[-1][:3] == hud_module.WHEEL_IDLE
  assert any(entry[0] == '0' for entry in text.drawn) and not any(entry[0] == '-0' for entry in text.drawn)


def test_angle_text_is_measured_only_when_the_whole_degrees_change():
  state = {'angle': 12.1}
  view, _, text = status_hud(lambda: (status_sm(angle=state['angle']), None))
  view.render(*LOGICAL_720P)
  for angle in (12.2, 12.4, 11.6):
    state['angle'] = angle
    view.render(*LOGICAL_720P)
  assert text.measured.count('12') == 1
  state['angle'] = -47.8
  view.render(*LOGICAL_720P)
  assert text.measured.count('-48') == 1
  count = len(text.measured)
  view.render(*LOGICAL_720P)
  assert len(text.measured) == count, 'a steady frame measures nothing'


class WheelRl(FakeRl):
  def __init__(self):
    super().__init__()
    self.TextureFilter = SimpleNamespace(TEXTURE_FILTER_BILINEAR=1)
    self.paths, self.unloaded = [], []

  def load_image(self, path):
    self.paths.append(path)
    return SimpleNamespace(data=b'x', width=266, height=266)

  def is_image_valid(self, image):
    return bool(image.data) and image.width > 0 and image.height > 0

  def image_resize(self, image, w, h):
    image.width, image.height = w, h

  def load_texture_from_image(self, image):
    return SimpleNamespace(id=len(self.paths), width=image.width, height=image.height)

  def unload_image(self, image):
    pass

  def set_texture_filter(self, texture, mode):
    pass

  def unload_texture(self, texture):
    self.unloaded.append(texture.id)


def test_wheel_texture_is_loaded_once_turned_by_the_angle_and_unloaded():
  from openpilot.starpilot.system.starport.birds_eye_hud import status_geometry
  g = status_geometry(*LOGICAL_720P)
  state = {'angle': 30.0}
  view, rl, _ = status_hud(lambda: (status_sm(angle=state['angle']), None), rl=WheelRl())
  view.render(*LOGICAL_720P)
  state['angle'] = -75.5
  view.render(*LOGICAL_720P)
  assert len(rl.paths) == 1 and rl.paths[0].endswith('selfdrive/assets/icons/chffr_wheel.png')
  first, second = [call for call in rl.calls if call[0] == 'draw_texture_pro']
  side = 2 * g.badge[2] * hud_module.WHEEL_ART
  assert first[3] == pytest.approx((g.badge[0], g.badge[1], side, side)), 'centred on the badge'
  assert first[4] == pytest.approx((side / 2, side / 2)), 'turned about its centre'
  assert first[5] == pytest.approx(-30.0) and second[5] == pytest.approx(75.5), 'left-positive angle, clockwise raylib'
  assert first[1].width == round(side), 'shrunk once on the CPU'
  assert not [call for call in rl.calls if call[0] == 'draw_line_ex']
  view.close()
  assert rl.unloaded == [1]


def test_drive_mode_chip_is_orange_in_experimental_and_dims_while_not_enabled():
  from openpilot.starpilot.system.starport.birds_eye_hud import status_geometry
  g = status_geometry(*LOGICAL_720P)
  state = {'experimental': True, 'enabled': True}
  view, rl, text = status_hud(lambda: (status_sm(experimental=state['experimental'], enabled=state['enabled']), None))

  def chip():
    text.drawn.clear()
    view.render(*LOGICAL_720P)
    return next(entry for entry in text.drawn if entry[0] in ('EXPERIMENTAL', 'CHILL'))

  value, size, x, y, color = chip()
  assert value == 'EXPERIMENTAL' and color == (*hud_module.EXPERIMENTAL, 255) == (255, 155, 63, 255)
  assert size <= hud_module.CHIP_SIZE and x == pytest.approx(g.mode[0], abs=0.5) and g.mode[1] < y < g.mode[1] + g.mode[3], 'left-aligned'
  assert x + len(value) * size * 0.5 <= g.mode[0] + g.mode[2], 'fits the chip'
  assert not any(call[0].startswith('draw_rectangle_rounded') and call[1] == pytest.approx(g.mode) for call in rl.calls), 'text only'
  state.update(experimental=False)
  value, size, chill_x, _, color = chip()
  assert value == 'CHILL' and size == pytest.approx(hud_module.CHIP_SIZE) and color == (*hud_module.ACTUAL, 255)
  assert chill_x == x, 'both modes start at the same left edge'
  state.update(enabled=False)
  assert chip()[4] == (*hud_module.ACTUAL, hud_module.DIMMED)
  state.update(experimental=True)
  assert chip()[4] == (*hud_module.EXPERIMENTAL, hud_module.DIMMED)
  assert text.measured.count('CHILL') == 1, 'measured when the mode changes, not when it dims'


def test_follow_row_draws_gap_bars_and_the_setting_name():
  from openpilot.starpilot.system.starport.birds_eye_hud import status_geometry
  g = status_geometry(*LOGICAL_720P)
  state = {'personality': 0, 'traffic': False, 'lead': None}
  view, rl, text = status_hud(lambda: (status_sm(personality=state['personality'], traffic=state['traffic'],
                                                 lead=state['lead'], v_ego=20.0), None))

  def inside(x, y):
    return g.follow[0] <= x < g.follow[0] + g.follow[2] and g.follow[1] <= y < g.follow[1] + g.follow[3]

  def frame():
    rl.calls.clear()
    text.drawn.clear()
    view.render(*LOGICAL_720P)
    bars = [call for call in rl.calls if call[0] == 'draw_rectangle_rounded' and call[1] != pytest.approx(g.follow)
            and inside(call[1][0], call[1][1])]
    return bars, [entry for entry in text.drawn if inside(entry[2], entry[3])]

  for raw, name in enumerate(('AGGRESSIVE', 'STANDARD', 'RELAXED')):
    state['personality'] = raw
    bars, texts = frame()
    assert len(bars) == 3 and [bar[-1][3] == 255 for bar in bars] == [index <= raw for index in range(3)]
    assert bars[0][1][1] > bars[1][1][1] > bars[2][1][1] and bars[0][1][2] > bars[2][1][2], 'nearest bar lowest and widest'
    assert [entry[0] for entry in texts] == [name] and texts[0][4] == (*hud_module.ACTUAL, 255)
    assert texts[0][2] > bars[0][1][0] + bars[0][1][2], 'the name sits right of the icon'
  state.update(traffic=True)
  bars, texts = frame()
  assert texts[0][0] == 'TRAFFIC' and texts[0][4] == (*hud_module.TRAFFIC, 255) and bars[0][-1][:3] == hud_module.TRAFFIC
  assert hud_module.TRAFFIC not in (hud_module.ACTUAL, hud_module.EXPERIMENTAL, hud_module.GREEN)
  state.update(traffic=False, personality=0, lead=36.0)
  bars, texts = frame()
  assert [entry[0] for entry in texts] == ['AGGRESSIVE'], 'a lead adds no time gap: the setting only'
  assert bars[0][1][0] == pytest.approx(g.follow[0]), 'the icon starts at the left edge, under the speed readout'
  assert not any(call[0].startswith('draw_rectangle_rounded') and call[1] == pytest.approx(g.follow) for call in rl.calls), 'no panel'
  measured = len(text.measured)
  state['lead'] = 44.0
  frame()
  assert len(text.measured) == measured, 'measured only when the displayed value changes'


def test_status_widgets_wait_for_their_services():
  view, rl, text = status_hud(lambda: (sm(long_active=False), None))
  view.render(*LOGICAL_720P)
  assert not [call for call in rl.calls if call[0] in ('draw_circle_v', 'draw_ring', 'draw_line_ex')]
  assert [entry[0] for entry in text.drawn if not entry[0].endswith(('AM', 'PM'))] == []


def test_cpu_cost_per_frame_with_the_status_cluster_is_small():
  messages = status_sm(experimental=True, traffic=False, lead=36.0)
  view, _, _ = status_hud(lambda: (messages, None))
  view.render(*LOGICAL_720P)
  start = time.perf_counter()
  for _ in range(300):
    view.render(*LOGICAL_720P)
  assert (time.perf_counter() - start) / 300 < 0.001
