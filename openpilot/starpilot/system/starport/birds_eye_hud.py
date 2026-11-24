"""HUD over the birds-eye-only car screen: an acceleration bar on the left edge, a flat torque bar at the bottom, large
blinking turn-signal arrows at the side edges, a speed / set-speed readout at the top left and a status cluster
(steering wheel, drive mode, follow distance) at the bottom left.

Acceleration bar: a vertical track hugging the left edge, zero at its middle, accelerating up, braking down. Only while
carControl.longActive is it coloured: the fill is the planner's target (longitudinalPlan.aTarget; the 100 Hz
carControl.actuators.accel adds the long PID on measured aEgo and jitters, so it is only the fallback while the plan is
unseen or older than PLAN_MAX_AGE_NS), green accelerating, neutral below zero, red while braking (target below
BRAKE_ACCEL, or the brake pedal). The scale is fixed: +/-ACCEL_RANGE at the bar's ends, engaged or not, with a line
at zero and no other graduations. While
longActive a tick marks each of the planner's published cruise limits (starpilotLongitudinalPlan.accelLimitMin/Max,
the floor and ceiling the StarPilot profile toggles and overrides leave in force; no planner publishes these two
fields yet, and until one does read_limits returns None: no ticks, and the bar grows against the fixed range) and
moves along the bar as they change; a ceiling of zero (throttle withheld, lateral budget spent) sits on the zero line, and the last limits read
are held through a missing or stale message, so the ticks never blink. As the command reaches a limit the bar grows
thicker; it may pass the tick. Disengaged, the track is neutral grey, the setpoint stays at zero (the plan's accel is
not shown) and no limit ticks are drawn. A white tick is always the measured carState.aEgo, lightly filtered (AEGO_RC).

Torque bar: a flat, rounded bar centred at the bottom, LENGTH_FRACTION of the frame width, always visible. Its
indicator runs from the centre toward the sign of the same caller-qualified utilization as the full composition's arc
(observe_torque_feedback, see starpilot/ui/torque_feedback.py). Thickness and colour follow the arc: 2x its base thickness, growing to 2x its
maximum as |utilization| passes 0.5, warming toward orange past 0.75. Grey with the indicator at centre while lateral
is not active.

Turn signals: carState.leftBlinker/rightBlinker pulse the comma's turn_signal_left icon, scaled up, at the left / right
edge of the view, centred vertically on the frame, with the comma screen's period; a filled arrow when the texture
cannot be loaded.

Speed readout: top left of the view, carState.vEgo and right of it the cruise set speed (the onroad HUD's rule:
vCruiseCluster, else vCruise, valid in (0, SET_SPEED_NA); "--" otherwise and whenever carState.cruiseState.enabled is
false, i.e. disengaged or lateral-only), mph or km/h by `metric`. The numbers are
twice the birds-eye lead label's size (birds_eye_math.label_font_size over the view rect), with small unit captions.

Status widgets, drawn straight on the view with no panel behind them: the drive mode and the follow distance as
left-aligned text under the speed readout, and the steering wheel with its angle under the clock; all above the
turn arrows. The bars (the acceleration track, the torque track) are glass, like the birds-eye lead label and the
music card: a smoked tint under a bright rim.
- Steering wheel: the comma's chffr_wheel art on a round badge, turned by carState.steeringAngleDeg (left-positive, so
  counter-clockwise on screen), green while carControl.latActive and grey otherwise, the whole degrees under it; a
  drawn ring with three spokes when the texture cannot be loaded.
- Drive mode: EXPERIMENTAL (the comma's orange) or CHILL from selfdriveState.experimentalMode, dimmed while
  selfdriveState.enabled is false.
- Follow distance: selfdriveState.personality as one to three gap bars (hudControl.leadDistanceBars) with its name,
  TRAFFIC in its own colour while starpilotCarState.trafficModeEnabled, and the measured time gap to
  radarState.leadOne (dRel / vEgo) while a lead is present and the car moves.
A widget whose service has not been seen is not drawn.

Per frame this is a handful of rectangles, a few cached-text draws and at most three texture quads; nothing allocates.
"""
from dataclasses import dataclass
import math
from pathlib import Path
import time

ACCEL_RANGE = 3.0          # m/s^2 at either end of the bar, always
MIN_LIMIT = 0.1            # m/s^2; a limit nearer zero than this counts as this much for the bar's growth
BRAKE_ACCEL = -0.2         # commanded accel below this while longActive counts as braking
BRAKE_RELEASE = -0.15      # hysteresis: braking ends above this
FPS = 30.0                 # the stream's frame rate; filters are tuned for it
ACCEL_RC = 0.12            # seconds; enough to calm 30 fps jitter without visible lag
AEGO_RC = 0.15             # seconds; the measured-accel tick's light filter
TORQUE_RC = 0.1            # the full composition's torque filter time constant
LIMITS_MAX_AGE_NS = 1_000_000_000  # starpilotLongitudinalPlan older than this is not read; the last limits are held
PLAN_MAX_AGE_NS = 1_000_000_000    # longitudinalPlan older than this falls back to carControl.actuators.accel

ACCEL_WIDTH, ACCEL_GROWN = 28, 56              # logical px at 1080 rows: 2x the former bar, doubling at the limit
TORQUE_MIN, TORQUE_MAX = 2 * 14, 2 * 56        # 2x the arc's MIN_HEIGHT / MAX_HEIGHT (onroad_torque_geometry)
TORQUE_MARGIN = 14                             # gap under the torque bar and above its grown top
LENGTH_FRACTION = 0.75                         # torque bar length over the frame width
SIGNAL_HEIGHT = 260                            # turn-signal arrow height at 1080 rows (the icon has glow padding)
SIGNAL_MARGIN = 28
READOUT_SCALE = 2.0                            # readout numbers over the birds-eye lead label size
CAPTION_SCALE = 0.3                            # unit captions over the readout numbers
CLOCK_SCALE = 0.6                              # the clock over the readout numbers
SET_SPEED_NA = 255                             # onroad hud_renderer: a set speed at or above this is not set
MPH_PER_MPS, KPH_PER_MPS = 2.2369362921, 3.6
TURN_SIGNAL_BLINK_PERIOD = 1 / (80 / 60)       # the comma screen's period (mici alert_renderer)
TURN_SIGNAL_RC = 0.3                           # its pulse filter: jumps bright each period, then decays to 20 %
TURN_SIGNAL_ICON = Path(__file__).parents[3] / 'selfdrive/assets/icons_mici/onroad/turn_signal_left.png'
WHEEL_ICON = Path(__file__).parents[3] / 'selfdrive/assets/icons/chffr_wheel.png'

STATUS_GAP = 24                                # status widgets: clear space under the readout or the clock, on an 8 px rhythm
STATUS_ROW_GAP = 16                            # between the drive-mode and follow-distance rows
READOUT_ROWS = 1.65                            # the speed readout's height (number and caption, as the fonts draw them) over its number size
CLOCK_ROWS = 1.15                              # the clock's height over its text size
WHEEL_BADGE = 110                              # steering-wheel badge diameter
WHEEL_ART = 0.78                               # wheel art over the badge diameter (the comma's 144 in a 192 button)
ANGLE_SIZE = 36                                # the degrees under the badge
ANGLE_ROW = 46                                 # rows under the badge for the degrees
CHIP_SIZE = 34                                 # drive-mode and follow-distance text
CHIP_HEIGHT = 46                               # each text row
MODE_WIDTH = 320
FOLLOW_WIDTH = 440
GAP_BARS = ((38, 9), (30, 9), (22, 9))         # the gap icon's bars, nearest first (bottom up): (width, height)
GAP_BAR_STEP = 14                              # bar pitch
PERSONALITIES = ('AGGRESSIVE', 'STANDARD', 'RELAXED')   # log.LongitudinalPersonality by raw value

BACKGROUND = (0, 0, 0)                                       # birds_eye_renderer's solid background
GLASS_FILL, GLASS_RIM, GLASS_RIM_PX = (46, 50, 60, 218), (255, 255, 255, 112), 1.5   # birds_eye_math's glass panel
IDLE = (220, 224, 232)                                       # disengaged fill / track
TICK = (170, 180, 198)                                       # birds_eye_renderer's rung-label grey
LIMIT = (235, 238, 245)
GREEN = (64, 220, 120)                                       # birds_eye_renderer's accent green
SIGNAL_GREEN = (40, 230, 110)                                # the comma icon's green
NEUTRAL = (170, 180, 198)
RED = (255, 72, 72)
ACTUAL = (245, 247, 250)
SHADOW = (0, 0, 0)
DOT = (182, 182, 182)                                        # the arc's centre dot
WHEEL_IDLE = (84, 90, 104)                                   # the badge while lateral is not active
EXPERIMENTAL = (255, 155, 63)                                # the comma's experimental-mode orange
TRAFFIC = (96, 190, 255)
DIMMED = 110                                                 # alpha of the mode chip's text while not enabled


def _finite(value, default=None):
  try:
    value = float(value)
  except (TypeError, ValueError):
    return default
  return value if math.isfinite(value) else default


def _clamp(value, low=0.0, high=1.0):
  return max(low, min(high, value))


@dataclass(frozen=True)
class AccelReading:
  desired: float | None
  actual: float | None
  long_active: bool
  brake_pressed: bool


@dataclass(frozen=True)
class AccelGeometry:
  strip: float     # reserved width at the left edge
  x: float         # bar left edge at the base width
  width: float     # base bar width
  grown: float     # bar width at a limit
  top: float
  bottom: float
  zero: float      # y of 0 m/s^2

  @property
  def center(self) -> float:
    return self.x + self.width / 2

  @property
  def half(self) -> float:
    return (self.bottom - self.top) / 2


def accel_y(g: AccelGeometry, accel: float) -> float:
  """The bar's fixed scale: +/-ACCEL_RANGE at its ends, clamped there."""
  return g.zero - _clamp(accel / ACCEL_RANGE, -1.0, 1.0) * g.half


@dataclass(frozen=True)
class AccelLimits:
  """The planner's cruise floor (`lower` <= 0) and ceiling (`upper` >= 0), m/s^2."""
  lower: float
  upper: float

  def utilization(self, accel: float) -> float:
    """Fraction of the limit on the accel's side; 1.0 at the limit."""
    return abs(accel) / max(self.upper if accel >= 0 else -self.lower, MIN_LIMIT)


FULL_RANGE = AccelLimits(-ACCEL_RANGE, ACCEL_RANGE)  # what the bar grows against while no limits have been read


def accel_geometry(height: float) -> AccelGeometry:
  """Sized from the frame height so 1080 logical rows (a 1280x720 head unit) keep the designed proportions."""
  k = height / 1080
  width = max(6.0, round(ACCEL_WIDTH * k))
  grown = max(width, round(ACCEL_GROWN * k))
  strip = round(grown + 56 * k)
  top, bottom = height * 0.10, height * 0.85   # 75 % of the height, like the torque bar's share of the width
  return AccelGeometry(strip, strip / 2 - width / 2, width, grown, top, bottom, (top + bottom) / 2)


def dock_height(height: float) -> float:
  """Bottom band reserved for the torque bar at its grown thickness, so the view never draws under it."""
  return round((TORQUE_MAX + 2 * TORQUE_MARGIN) * height / 1080)


def view_rect(width: float, height: float) -> tuple[float, float, float, float]:
  """The area clear of the HUD's own bars, where widgets and labels are laid out: right of the acceleration strip
  and above the torque bar's band."""
  strip = accel_geometry(height).strip
  return strip, 0, width - strip, height - dock_height(height)


def render_rect(width: float, height: float) -> tuple[float, float, float, float]:
  """Where the birds-eye view draws: right of the acceleration strip, down to the bottom edge. The road runs on
  under the torque bar, whose glass track is drawn over it."""
  strip = accel_geometry(height).strip
  return strip, 0, width - strip, height


@dataclass(frozen=True)
class TorqueGeometry:
  cx: float        # centre (zero) x
  left: float
  right: float
  bottom: float    # the bar grows upward from here
  thin: float      # thickness at |utilization| <= 0.5
  thick: float     # thickness at |utilization| >= 1


def torque_geometry(width: float, height: float) -> TorqueGeometry:
  """Flat bar LENGTH_FRACTION of the frame width, centred under the birds-eye view (on the ego car)."""
  k = height / 1080
  strip = accel_geometry(height).strip
  cx = strip + (width - strip) / 2
  half = min(LENGTH_FRACTION * width / 2, width - cx - TORQUE_MARGIN * k, cx - strip - TORQUE_MARGIN * k)
  return TorqueGeometry(cx, cx - half, cx + half, height - TORQUE_MARGIN * k, TORQUE_MIN * k, TORQUE_MAX * k)


def torque_strength(utilization: float) -> float:
  """The arc's growth: 0 up to |utilization| 0.5, 1 at full."""
  return _clamp((abs(utilization) - 0.5) * 2)


def torque_color(utilization: float, alpha: float) -> tuple[int, int, int, int]:
  """The arc's indicator colour mid-reach: white, warming to orange past 0.75."""
  warmth = _clamp((abs(utilization) - 0.75) * 4)
  green = 1 - warmth + warmth * (115 / 255) + warmth * (85 / 255) * 0.5
  return 255, round(255 * green), round(255 * (1 - warmth)), round(255 * 0.9 * alpha)


def _current(sm, name: str, now_ns: int | None, max_age_ns: int):
  """sm[name] when seen, alive and received within max_age_ns of now_ns (when recv_time is known); else None."""
  if not sm.seen[name]:
    return None
  alive = getattr(sm, 'alive', None)
  if isinstance(alive, dict) and alive.get(name) is False:
    return None
  received = getattr(sm, 'recv_time', None)
  if isinstance(received, dict) and now_ns is not None and received.get(name):
    if now_ns - int(received[name] * 1e9) > max_age_ns:
      return None
  return sm[name]


def read_accel(sm, now_ns: int | None = None) -> AccelReading | None:
  """The planner's target and measured accel from the SubMaster; None before carState has been seen.

  The setpoint is longitudinalPlan.aTarget while longActive (carControl.actuators.accel while the plan is unseen or
  stale) and 0.0 otherwise: the plan's accel is not shown while openpilot is not commanding.
  """
  seen = sm.seen
  if not seen['carState']:
    return None
  car_state = sm['carState']
  long_active, desired = False, 0.0
  if seen['carControl']:
    control = sm['carControl']
    long_active = bool(control.longActive)
    if long_active:
      try:
        plan = _current(sm, 'longitudinalPlan', now_ns, PLAN_MAX_AGE_NS)
        target = _finite(plan.aTarget) if plan is not None else None
      except (AttributeError, KeyError, TypeError):
        target = None
      desired = target if target is not None else _finite(control.actuators.accel)
  return AccelReading(desired, _finite(car_state.aEgo), long_active, bool(car_state.brakePressed))


def read_limits(sm, now_ns: int | None = None) -> AccelLimits | None:
  """The planner's cruise floor and ceiling from starpilotLongitudinalPlan; None when absent, stale or implausible.

  Either may be zero: the ceiling is while the model withholds throttle or the lateral budget is spent.
  """
  try:
    plan = _current(sm, 'starpilotLongitudinalPlan', now_ns, LIMITS_MAX_AGE_NS)
    if plan is None:
      return None
    lower, upper = _finite(plan.accelLimitMin), _finite(plan.accelLimitMax)
  except (AttributeError, KeyError, TypeError, ValueError):
    return None
  if lower is None or upper is None or not lower <= 0 <= upper or lower == upper:
    return None
  return AccelLimits(lower, upper)


def read_blinkers(sm) -> tuple[bool, bool]:
  try:
    if not sm.seen['carState']:
      return False, False
    car_state = sm['carState']
    return bool(getattr(car_state, 'leftBlinker', False)), bool(getattr(car_state, 'rightBlinker', False))
  except (AttributeError, KeyError, TypeError):
    return False, False


def set_speed_kph(car_state) -> float | None:
  """The cruise set speed in km/h by the onroad HUD's rule (vCruiseCluster, else vCruise); None when not set."""
  cluster = _finite(getattr(car_state, 'vCruiseCluster', None), 0.0)
  speed = cluster if cluster != 0.0 else _finite(getattr(car_state, 'vCruise', None))
  return speed if speed is not None and 0 < speed < SET_SPEED_NA else None


def read_speeds(sm, metric: bool) -> tuple[str, str] | None:
  """(current speed, set speed or '--') as displayed in mph or km/h; None before carState with a finite vEgo."""
  try:
    if not sm.seen['carState']:
      return None
    car_state = sm['carState']
    v_ego = _finite(getattr(car_state, 'vEgo', None))
    if v_ego is None:
      return None
    # Only while cruise is engaged: lateral-only and disengaged show "--", not the last set speed the car still reports.
    cruise = set_speed_kph(car_state) if getattr(getattr(car_state, 'cruiseState', None), 'enabled', False) else None
  except (AttributeError, KeyError, TypeError):
    return None
  speed = f"{round(max(0.0, v_ego) * (KPH_PER_MPS if metric else MPH_PER_MPS)):d}"
  return speed, '--' if cruise is None else f"{round(cruise if metric else cruise / KPH_PER_MPS * MPH_PER_MPS):d}"


@dataclass(frozen=True)
class SteeringReading:
  angle: float       # carState.steeringAngleDeg, left-positive
  lat_active: bool


@dataclass(frozen=True)
class DriveMode:
  experimental: bool
  enabled: bool

  @property
  def label(self) -> str:
    return 'EXPERIMENTAL' if self.experimental else 'CHILL'


@dataclass(frozen=True)
class FollowReading:
  bars: int                # 1..3, hudControl.leadDistanceBars
  label: str               # the personality's name, or TRAFFIC
  traffic: bool


def _message(sm, name: str):
  """sm[name] once seen; None when unseen or the SubMaster does not carry the service."""
  try:
    return sm[name] if sm.seen[name] else None
  except (AttributeError, KeyError, TypeError):
    return None


def read_steering(sm) -> SteeringReading | None:
  """The steering angle and whether openpilot steers; None before carState with a finite angle."""
  try:
    car_state = _message(sm, 'carState')
    angle = _finite(getattr(car_state, 'steeringAngleDeg', None))
    if angle is None:
      return None
    return SteeringReading(angle, bool(getattr(_message(sm, 'carControl'), 'latActive', False)))
  except (AttributeError, KeyError, TypeError):
    return None


def read_drive_mode(sm) -> DriveMode | None:
  """Experimental or chill, and whether openpilot is enabled; None before selfdriveState."""
  try:
    state = _message(sm, 'selfdriveState')
    if state is None:
      return None
    return DriveMode(bool(getattr(state, 'experimentalMode', False)), bool(getattr(state, 'enabled', False)))
  except (AttributeError, KeyError, TypeError):
    return None


def personality_index(personality) -> int | None:
  """0 aggressive / 1 standard / 2 relaxed from the capnp enum, its raw value or its name; None when unreadable."""
  raw = getattr(personality, 'raw', personality)
  if isinstance(raw, str):
    name = raw.upper()
    return PERSONALITIES.index(name) if name in PERSONALITIES else None
  try:
    raw = int(raw)
  except (TypeError, ValueError):
    return None
  return raw if 0 <= raw < len(PERSONALITIES) else None


def read_follow(sm) -> FollowReading | None:
  """The driving personality as gap bars, traffic mode and the measured time gap; None before selfdriveState."""
  try:
    state = _message(sm, 'selfdriveState')
    index = personality_index(getattr(state, 'personality', None)) if state is not None else None
    if index is None:
      return None
    traffic = bool(getattr(_message(sm, 'starpilotCarState'), 'trafficModeEnabled', False))
  except (AttributeError, KeyError, TypeError):
    return None
  return FollowReading(index + 1, 'TRAFFIC' if traffic else PERSONALITIES[index], traffic)


@dataclass(frozen=True)
class StatusGeometry:
  """The status widgets' boxes as (x, y, width, height), and the wheel badge inside the first."""
  wheel: tuple[float, float, float, float]
  badge: tuple[float, float, float]   # centre x, centre y, radius
  mode: tuple[float, float, float, float]
  follow: tuple[float, float, float, float]

  @property
  def pills(self):
    return self.wheel, self.mode, self.follow


def status_geometry(width: float, height: float) -> StatusGeometry:
  """Top left, under the speed readout: the drive-mode row over the follow-distance row, left-aligned with it. Top
  right, under the clock: the wheel badge with its degrees. All end above the turn arrows (mid height) and the
  speed-limit sign (right, below the wheel)."""
  k = height / 1080
  vx, _, vw, _ = view_rect(width, height)
  margin, gap, size = SIGNAL_MARGIN * k, STATUS_GAP * k, readout_size(width, height)
  x = vx + margin
  row_h = CHIP_HEIGHT * k
  mode = (x, margin + READOUT_ROWS * size + gap, MODE_WIDTH * k, row_h)
  follow = (x, mode[1] + row_h + STATUS_ROW_GAP * k, FOLLOW_WIDTH * k, row_h)
  side = WHEEL_BADGE * k
  wheel = (vx + vw - margin - side, margin + CLOCK_ROWS * CLOCK_SCALE * size + gap, side, side + ANGLE_ROW * k)
  return StatusGeometry(wheel, (wheel[0] + side / 2, wheel[1] + side / 2, side / 2), mode, follow)


def clock_text(now: float | None = None) -> str:
  """The comma's local time as the car would show it: 12-hour, no leading zero, e.g. 4:07 PM."""
  t = time.localtime(now)
  return f"{t.tm_hour % 12 or 12}:{t.tm_min:02d} {'AM' if t.tm_hour < 12 else 'PM'}"


def readout_size(width: float, height: float) -> float:
  """The readout numbers' size: READOUT_SCALE x the birds-eye lead label's size over the same view rect."""
  from openpilot.selfdrive.ui.onroad.birds_eye_math import label_font_size
  return READOUT_SCALE * label_font_size(view_rect(width, height)[3])


def braking(reading: AccelReading | None, was_braking: bool = False) -> bool:
  if reading is None:
    return False
  if reading.brake_pressed:
    return True
  if not reading.long_active or reading.desired is None:
    return False
  return reading.desired < (BRAKE_RELEASE if was_braking else BRAKE_ACCEL)


def fill_color(accel: float, is_braking: bool, long_active: bool) -> tuple[int, int, int, int]:
  """Coloured only while longActive; neutral grey/white otherwise."""
  if not long_active:
    return (*IDLE, 110)
  if is_braking:
    return (*RED, 240)
  return (*(GREEN if accel >= 0 else NEUTRAL), 235)


def signal_alpha(elapsed: float) -> float:
  """The comma's turn-signal pulse (0..1): bright at each period start, decaying toward 20 %."""
  alpha = (1 / FPS) / (TURN_SIGNAL_RC + 1 / FPS)
  frames = (elapsed % TURN_SIGNAL_BLINK_PERIOD) * FPS
  return min(1.0, 0.2 + (2 - 0.2) * (1 - alpha) ** frames)


class _Filter:
  def __init__(self, rc):
    self.alpha = (1 / FPS) / (rc + 1 / FPS)
    self.x = None

  def update(self, value):
    self.x = value if self.x is None else self.x + self.alpha * (value - self.x)
    return self.x


def torque_feedback(sm, car_params):
  """observe_torque_feedback over the SubMaster, as the full composition qualifies it."""
  from openpilot.starpilot.ui.torque_feedback import observe_torque_feedback
  seen = sm.seen
  def message(name):
    return sm[name] if seen[name] else None
  return observe_torque_feedback(message('carState'), message('carControl'), message('controlsState'),
                                 message('carOutput'), message('vehicleParameters'), car_params)


class BirdsEyeHud:
  def __init__(self, rl, text, source, *, feedback=torque_feedback, clock=time.monotonic, icon_path=TURN_SIGNAL_ICON,
               wall_clock=time.time, wheel_path=WHEEL_ICON):
    """source() -> (SubMaster, CarParams or None). clock() -> seconds, for the turn-signal pulse."""
    self.rl, self.text, self.source = rl, text, source
    self._feedback = feedback
    self._clock = clock
    self._wall_clock = wall_clock
    self._clock_key = None
    self._clock_draw = None
    self._icon_path = icon_path
    self._desired = _Filter(ACCEL_RC)
    self._actual = _Filter(AEGO_RC)
    self._grow = _Filter(ACCEL_RC)
    self._lower = _Filter(ACCEL_RC)
    self._upper = _Filter(ACCEL_RC)
    self._limits = None   # the last limits read while longActive; held through a dropout, cleared on disengage
    self._torque = _Filter(TORQUE_RC)
    self._torque_alpha = _Filter(TORQUE_RC)
    self._braking = False
    self._label_key = None
    self._label = None
    self._geometry_key = None
    self._geometry = None
    self._signal_since = None
    self._icon = None
    self._icon_failed = False
    self.metric = False   # km/h when set (the owner copies ui_state.is_metric); mph by default
    self._readout_key = None
    self._readout = None
    self._wheel_path = wheel_path
    self._wheel = None
    self._wheel_failed = False
    self._status_key = None
    self._status = None
    self._angle_key = None
    self._angle = None
    self._mode_key = None
    self._mode = None
    self._follow_key = None
    self._follow = None

  @staticmethod
  def view_rect(width: float, height: float) -> tuple[float, float, float, float]:
    return view_rect(width, height)

  @staticmethod
  def render_rect(width: float, height: float) -> tuple[float, float, float, float]:
    return render_rect(width, height)

  def geometry(self, height: float) -> AccelGeometry:
    if self._geometry_key != height:
      self._geometry, self._geometry_key = accel_geometry(height), height
    return self._geometry

  def render(self, width: float, height: float) -> None:
    sm, car_params = self.source()
    geometry = self.geometry(height)
    now_ns = time.monotonic_ns()
    self._draw_accel(geometry, read_accel(sm, now_ns), read_limits(sm, now_ns), height)
    self._draw_torque(sm, car_params, width, height)
    self._draw_signals(sm, width, height, geometry.strip)
    self._draw_readout(sm, width, height, geometry.strip)
    self._draw_status(sm, width, height)
    self._draw_clock(width, height)

  def _glass_rim(self, rect, roundness, segments=8) -> None:
    """The bright hairline rim that makes a filled rounded rectangle read as glass."""
    rl = self.rl
    rl.draw_rectangle_rounded_lines_ex(rl.Rectangle(*rect), roundness, segments, GLASS_RIM_PX, rl.Color(*GLASS_RIM))

  # --- acceleration bar ---
  def _held_limits(self, limits: AccelLimits | None, long_active: bool) -> AccelLimits | None:
    """The limits to mark, smoothed: this frame's while longActive, else the last read; None until one was read."""
    if not long_active:
      self._limits = self._lower.x = self._upper.x = None
      return None
    if limits is not None:
      self._limits = limits
    if self._limits is None:
      return None
    return AccelLimits(self._lower.update(self._limits.lower), self._upper.update(self._limits.upper))

  def _draw_accel(self, g: AccelGeometry, reading: AccelReading | None, limits: AccelLimits | None, height: float) -> None:
    rl = self.rl
    strip = int(g.strip)
    k = height / 1080
    rl.draw_rectangle(0, 0, strip, int(height), rl.Color(*BACKGROUND, 255))
    long_active = reading is not None and reading.long_active
    limits = self._held_limits(limits, long_active)
    rl.draw_rectangle_rounded(rl.Rectangle(g.x, g.top, g.width, g.bottom - g.top), 1.0, 8, rl.Color(*GLASS_FILL))
    self._glass_rim((g.x, g.top, g.width, g.bottom - g.top), 1.0)
    tick = 8 * k
    # The zero line is the only fixed mark.
    rl.draw_rectangle_rec(rl.Rectangle(g.x - 2 * tick, g.zero - k, g.width + 4 * tick, 2 * k), rl.Color(*TICK, 150))
    if limits is not None:
      # The planner's floor and ceiling: ticks across the bar that move on the fixed scale as the limits change.
      for accel in (limits.lower, limits.upper):
        y = accel_y(g, accel)
        rl.draw_rectangle_rec(rl.Rectangle(g.x - 2 * tick, y - 1.5 * k, g.width + 4 * tick, 3 * k), rl.Color(*LIMIT, 220))
    scale = limits if limits is not None else FULL_RANGE
    if reading is None:
      self._desired.x = self._actual.x = self._grow.x = None
      self._braking = False
      return
    self._braking = braking(reading, self._braking)
    if reading.desired is not None:
      desired = self._desired.update(reading.desired)
      color = rl.Color(*fill_color(desired, self._braking, long_active))
      # Like the torque bar's strength: thicker as the command reaches the limit (full at it).
      grow = self._grow.update(_clamp((scale.utilization(desired) - 0.8) * 5) if long_active else 0.0)
      width = g.width + (g.grown - g.width) * grow
      y = accel_y(g, desired)
      top, bottom = min(y, g.zero), max(y, g.zero)
      if bottom - top >= 0.5:
        rl.draw_rectangle_rounded(rl.Rectangle(g.center - width / 2, top, width, bottom - top), 1.0, 8, color)
      if long_active:
        self._draw_label(desired, color, g, k)
    if reading.actual is not None:
      y = accel_y(g, self._actual.update(reading.actual))
      w, h = g.grown + 12 * k, max(3.0, 5 * k)
      x = g.center - w / 2
      rl.draw_rectangle_rec(rl.Rectangle(x - k, y - h / 2 - k, w + 2 * k, h + 2 * k), rl.Color(*SHADOW, 170))
      rl.draw_rectangle_rec(rl.Rectangle(x, y - h / 2, w, h), rl.Color(*ACTUAL, 245))

  def _draw_label(self, accel: float, color, g: AccelGeometry, k: float) -> None:
    if self.text is None:
      return
    value = round(accel, 1)
    size = 28 * k
    key = (value, size)
    if key != self._label_key:
      text = f"{value:+.1f}" if value != 0 else "0.0"
      w, h = self.text.measure(text, size)
      self._label, self._label_key = (text, w, h), key
    text, w, _ = self._label
    self.text.draw(text, size, g.strip / 2 - w / 2, g.bottom + 16 * k, color)

  # --- torque bar ---
  def _draw_torque(self, sm, car_params, width, height) -> None:
    try:
      feedback = self._feedback(sm, car_params)
    except (AttributeError, KeyError, TypeError, ValueError):
      feedback = None
    utilization = _finite(getattr(feedback, 'utilization', None))
    active = utilization is not None and getattr(feedback, 'reason', '') != 'lateral_inactive'
    # Inactive: the indicator settles back to the centre and fades; the track stays.
    torque = self._torque.update(_clamp(utilization, -1.0, 1.0) if active else 0.0)
    alpha = self._torque_alpha.update(1.0 if active else 0.0) if self._torque_alpha.x is not None else self._torque_alpha.update(0.0)
    g = torque_geometry(width, height)
    strength = torque_strength(torque) * alpha
    thickness = g.thin + (g.thick - g.thin) * strength
    top = g.bottom - thickness
    rl = self.rl
    # The track is glass like every other panel; the road shows through it and the indicator carries the contrast.
    rl.draw_rectangle_rounded(rl.Rectangle(g.left, top, g.right - g.left, thickness), 1.0, 12, rl.Color(*GLASS_FILL))
    self._glass_rim((g.left, top, g.right - g.left, thickness), 1.0, 12)
    reach = torque * (g.right - g.left) / 2
    if alpha > 0.01 and abs(reach) >= 1.0:
      x = min(g.cx, g.cx + reach)
      rl.draw_rectangle_rounded(rl.Rectangle(x, top, abs(reach), thickness), 1.0, 12, rl.Color(*torque_color(torque, alpha)))
    if abs(torque) < 0.5 or not active:
      # The arc's centre dot, as a short notch across the bar.
      k = height / 1080
      rl.draw_rectangle_rounded(rl.Rectangle(g.cx - 3 * k, top - 4 * k, 6 * k, thickness + 8 * k), 1.0, 6,
                                rl.Color(*DOT, 230))

  # --- turn signals ---
  def _load_texture(self, path, height):
    """An icon file as a bilinear texture, shrunk on the CPU to `height` rows; raises when it cannot be loaded."""
    rl = self.rl
    image = rl.load_image(str(path))
    try:
      if not rl.is_image_valid(image):
        raise ValueError(f'{path} unreadable')
      if image.height > height:
        rl.image_resize(image, round(image.width * height / image.height), round(height))
      texture = rl.load_texture_from_image(image)
    finally:
      if rl.is_image_valid(image):
        rl.unload_image(image)
    if texture.id == 0:
      raise ValueError(f'{path}: texture not created')
    rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
    return texture

  def _load_icon(self, height):
    """The comma's turn-signal icon as a texture, loaded once; None (and never retried) on failure."""
    if self._icon is not None or self._icon_failed:
      return self._icon
    try:
      self._icon = self._load_texture(self._icon_path, height)
    except Exception:
      self._icon_failed = True
    return self._icon

  def _load_wheel(self, height):
    """The comma's steering-wheel art as a texture, loaded once; None (and never retried) on failure."""
    if self._wheel is not None or self._wheel_failed:
      return self._wheel
    try:
      self._wheel = self._load_texture(self._wheel_path, height)
    except Exception:
      self._wheel_failed = True
    return self._wheel

  def _draw_signals(self, sm, width, height, strip) -> None:
    left, right = read_blinkers(sm)
    if not (left or right):
      self._signal_since = None
      return
    now = self._clock()
    if self._signal_since is None or now < self._signal_since:
      self._signal_since = now
    alpha = signal_alpha(now - self._signal_since)
    k = height / 1080
    h = SIGNAL_HEIGHT * k
    icon = self._load_icon(h)
    w = h * (icon.width / icon.height if icon is not None else 208 / 192)
    margin = SIGNAL_MARGIN * k
    for on, x, flip in ((left, strip + margin, False), (right, width - margin - w, True)):
      if on:
        self._draw_arrow(icon, x, height / 2 - h / 2, w, h, flip, alpha)

  # --- speed readout ---
  def _draw_readout(self, sm, width, height, strip) -> None:
    """Current speed, then the set speed, at the top left of the view; numbers bottom-aligned, captions under them."""
    if self.text is None:
      return
    speeds = read_speeds(sm, self.metric)
    if speeds is None:
      return
    key = (speeds, self.metric, width, height)
    if key != self._readout_key:
      k = height / 1080
      size = readout_size(width, height)
      caption = size * CAPTION_SCALE
      x, y = strip + SIGNAL_MARGIN * k, SIGNAL_MARGIN * k
      items = []
      for value, unit, gap in ((speeds[0], 'km/h' if self.metric else 'mph', 0.0), (speeds[1], 'SET', 0.6 * size)):
        x += gap
        w, h = self.text.measure(value, size)
        cw, _ = self.text.measure(unit, caption)
        items.append((value, size, round(x), round(y), unit, caption, round(x + (w - cw) / 2), round(y + h)))
        x += w
      self._readout, self._readout_key = items, key
    rl = self.rl
    for (value, size, x, y, unit, caption, cx, cy), rgb in zip(self._readout, (ACTUAL, TICK), strict=True):
      self.text.draw(value, size, x, y, rl.Color(*rgb, 255))
      self.text.draw(unit, caption, cx, cy, rl.Color(*TICK, 230))

  # --- status cluster ---
  def _draw_status(self, sm, width, height) -> None:
    key = (width, height)
    if key != self._status_key:
      self._status, self._status_key = status_geometry(width, height), key
    g, k = self._status, height / 1080
    steering = read_steering(sm)
    if steering is not None:
      self._draw_wheel(steering, g, k)
    mode = read_drive_mode(sm)
    if mode is not None:
      self._draw_mode(mode, g.mode, k)
    follow = read_follow(sm)
    if follow is not None:
      self._draw_follow(follow, g.follow, k)

  def _fitted(self, text, size, room):
    """(size, width, height) of `text` at `size`, shrunk to fit `room`."""
    w, h = self.text.measure(text, size)
    if w > room > 0:
      size *= room / w
      w, h = self.text.measure(text, size)
    return size, w, h

  def _draw_wheel(self, reading: SteeringReading, g: StatusGeometry, k: float) -> None:
    rl = self.rl
    cx, cy, r = g.badge
    fill = (*GREEN, 235) if reading.lat_active else (*WHEEL_IDLE, 235)
    rl.draw_circle_v(rl.Vector2(cx, cy), r, rl.Color(*fill))
    rotation = -reading.angle   # raylib turns clockwise; a left (positive) angle turns the wheel counter-clockwise
    side = 2 * r * WHEEL_ART
    icon = self._load_wheel(side)
    if icon is not None:
      rl.draw_texture_pro(icon, rl.Rectangle(0, 0, icon.width, icon.height), rl.Rectangle(cx, cy, side, side),
                          rl.Vector2(side / 2, side / 2), rotation, rl.Color(255, 255, 255, 255))
    else:
      # Fallback: a rim with spokes to the left, right and bottom, turned with the wheel.
      color = rl.Color(*ACTUAL, 255)
      rim = side / 2
      thick = max(2.0, 0.16 * rim)
      rl.draw_ring(rl.Vector2(cx, cy), rim - thick, rim, 0, 360, 36, color)
      centre = rl.Vector2(cx, cy)
      for spoke in (0.0, 90.0, 180.0):
        a = math.radians(spoke + rotation)
        rl.draw_line_ex(centre, rl.Vector2(cx + (rim - thick / 2) * math.cos(a), cy + (rim - thick / 2) * math.sin(a)),
                        thick, color)
      rl.draw_circle_v(centre, 0.22 * rim, color)
    if self.text is None:
      return
    degrees = round(reading.angle) + 0   # + 0: no "-0"
    key = (degrees, g.wheel)
    if key != self._angle_key:
      text = f"{degrees:d}"
      x, y, w, h = g.wheel
      ring = 0.11 * ANGLE_SIZE * k   # the degree sign is drawn: the bitmap atlases hold ASCII only
      size, tw, th = self._fitted(text, ANGLE_SIZE * k, w - 3 * ring)
      row = y + w   # the rows under the square badge area
      tx = x + (w - tw - 3 * ring) / 2
      ty = row + (h - w - th) / 2
      self._angle = (text, size, round(tx), round(ty), tx + tw + 2 * ring, ty + 0.2 * th + ring, ring)
      self._angle_key = key
    text, size, tx, ty, ring_x, ring_y, ring = self._angle
    color = rl.Color(*ACTUAL, 255)
    self.text.draw(text, size, tx, ty, color)
    rl.draw_ring(rl.Vector2(ring_x, ring_y), 0.55 * ring, ring, 0, 360, 16, color)

  def _draw_mode(self, mode: DriveMode, rect, k: float) -> None:
    if self.text is None:
      return
    key = (mode.experimental, rect)
    if key != self._mode_key:
      x, y, w, h = rect
      size, _, th = self._fitted(mode.label, CHIP_SIZE * k, w)
      self._mode, self._mode_key = (mode.label, size, round(x), round(y + (h - th) / 2)), key
    rgb = EXPERIMENTAL if mode.experimental else ACTUAL
    self.text.draw(*self._mode, self.rl.Color(*rgb, 255 if mode.enabled else DIMMED))

  def _draw_follow(self, follow: FollowReading, rect, k: float) -> None:
    rl = self.rl
    x, y, w, h = rect
    rgb = TRAFFIC if follow.traffic else ACTUAL
    # The gap icon: bars widening toward the car, the nearest `bars` of them lit.
    icon_w = GAP_BARS[0][0] * k
    centre = x + icon_w / 2
    base = y + h / 2 + (len(GAP_BARS) * GAP_BAR_STEP - (GAP_BAR_STEP - GAP_BARS[0][1])) * k / 2
    for index, (bar_w, bar_h) in enumerate(GAP_BARS):
      top = base - (index * GAP_BAR_STEP + bar_h) * k
      color = rl.Color(*rgb, 255) if index < follow.bars else rl.Color(*TICK, 70)
      rl.draw_rectangle_rounded(rl.Rectangle(centre - bar_w * k / 2, top, bar_w * k, bar_h * k), 1.0, 4, color)
    if self.text is None:
      return
    key = (follow.label, rect)
    if key != self._follow_key:
      left = x + icon_w + 16 * k
      size, _, th = self._fitted(follow.label, CHIP_SIZE * k, x + w - left)
      self._follow, self._follow_key = (follow.label, size, round(left), round(y + (h - th) / 2)), key
    self.text.draw(*self._follow, rl.Color(*rgb, 255))

  def _draw_clock(self, width, height) -> None:
    """The time of day at the top right of the view, on the readout's top margin."""
    if self.text is None:
      return
    text = clock_text(self._wall_clock())
    key = (text, width, height)
    if key != self._clock_key:
      size = readout_size(width, height) * CLOCK_SCALE
      margin = SIGNAL_MARGIN * height / 1080
      w, _ = self.text.measure(text, size)
      self._clock_draw, self._clock_key = (text, size, round(width - margin - w), round(margin)), key
    self.text.draw(*self._clock_draw, self.rl.Color(*ACTUAL, 255))

  def _draw_arrow(self, icon, x, y, w, h, flip, alpha) -> None:
    rl = self.rl
    if icon is not None:
      source = rl.Rectangle(0, 0, -icon.width if flip else icon.width, icon.height)
      rl.draw_texture_pro(icon, source, rl.Rectangle(x, y, w, h), rl.Vector2(0, 0), 0.0,
                          rl.Color(255, 255, 255, round(255 * alpha)))
      return
    # Fallback: a filled arrow in the icon's footprint (head 55 % of the width, shaft 40 % of the height).
    color = rl.Color(*SIGNAL_GREEN, round(255 * alpha))
    pad = 0.12 * w
    head = 0.45 * (w - 2 * pad)
    shaft_h = 0.36 * h
    cy = y + h / 2
    if flip:
      tip, base = x + w - pad, x + w - pad - head
      rl.draw_rectangle_rec(rl.Rectangle(x + pad, cy - shaft_h / 2, base - x - pad, shaft_h), color)
    else:
      tip, base = x + pad, x + pad + head
      rl.draw_rectangle_rec(rl.Rectangle(base, cy - shaft_h / 2, x + w - pad - base, shaft_h), color)
    points = [(tip, cy), (base, y + 0.8 * h), (base, y + 0.2 * h)]
    (ax, ay), (bx, by), (cx, cy2) = points
    if (bx - ax) * (cy2 - ay) - (by - ay) * (cx - ax) > 0:
      points[1], points[2] = points[2], points[1]  # raylib wants counter-clockwise on screen
    rl.draw_triangle(*(rl.Vector2(px, py) for px, py in points), color)

  def close(self) -> None:
    if self._icon is not None:
      self.rl.unload_texture(self._icon)
      self._icon = None
    if self._wheel is not None:
      self.rl.unload_texture(self._wheel)
      self._wheel = None
