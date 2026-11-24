"""Shared R4/R8 alert card and touch target for comma 3X and Android Auto."""
import math
import time

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.presentation import FontRole
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.onroad_customization import PALETTE, UNIDEN_SCALE_RANGE, UNIDEN_WIDGET, offset, widget_palette
from openpilot.starpilot.uniden.feed import DetectorFeed
from openpilot.starpilot.uniden.protocol import BORDER_SWATCHES, DEFAULT_CONFIG, alert_key

SIZE = (460, 150)
MAX_ROWS = 4
ROW, PAD, RADIUS, BORDER = 60, 8, 14, 2
MEMORY_MUTES = ('memory', 'auto_memory')
NOTE_SIZE = 23
HOLD_S = .8
BADGE_TOP = 42
EDGE_FRAME, EDGE_ROUNDNESS = 30, .12   # onroad's engagement edge, whose rounded corners the warning follows
DIRECTION_SIDES = {'front': ('top',), 'rear': ('bottom',), 'side': ('left', 'right')}
EDGE_GAP, EDGE_LINE, DASH, DASH_GAP = 12, 8, 56, 30
GLOW_DEPTH, GLOW_STEPS, VEIL_ALPHA = (35, 135), 12, .62
CHEVRON_SIZE, CHEVRON_WIDTH, CHEVRON_FROM_SPEED, CHEVRON_FROM_EDGE = 48, 13, 300, 44
FADE_S = .5
BAND_RANK = {'K': 0, 'KA': 1, 'LASER': 2}
OFFLINE_SIZE = (330, 56)


def mute_reason(alert):
  """Why an alert is quiet. StarPilot's own reasons come first, since they are why it sent the mute; then the detector's."""
  if alert.get('locked'):
    return 'Lockout'
  if alert.get('ruled'):
    return 'Frequency rule'
  if alert.get('filtered'):
    return 'Filtered'
  if alert.get('quiet'):
    return 'Below limit'
  if alert.get('timed'):
    return 'Timed mute'
  if not alert['muted']:
    return ''
  mode = alert.get('mute_mode')
  return 'Q-Ride' if mode == 'quiet_ride' else 'Mute Memory' if mode in MEMORY_MUTES else 'Manual mute' if mode == 'manual' else 'Muted'


def mute_note(alerts):
  """The reason in the card's top edge: one shared reason, counted when only some alerts have it, or plain Muted."""
  reasons = [reason for reason in map(mute_reason, alerts) if reason]
  if not reasons:
    return ''
  name = reasons[0] if len(set(reasons)) == 1 else 'Muted'
  return name if len(reasons) == len(alerts) else f'{len(reasons)} {name}'


def shown_alerts(alerts, config):
  """Alerts the driver wants on screen, by the detector settings shared by the card and the full-screen warning."""
  return [item for item in alerts if not (
    (config.get('hide_filtered') and item.get('filtered')) or
    (config.get('hide_below_limit') and item.get('quiet')) or
    (config.get('hide_q_ride') and item.get('mute_mode') == 'quiet_ride') or
    (config.get('hide_mute_memory') and item.get('mute_mode') in MEMORY_MUTES))]


def hex_color(value):
  return rl.Color(*(int(value[i:i + 2], 16) for i in (1, 3, 5, 7)))


def threat(alerts, bands):
  """The most serious shown alert in a band the driver chose; muted ones count, so the warning stays up, dimmed."""
  for band in ('LASER', 'KA', 'K'):
    if band in bands:
      for alert in alerts:
        if alert['band'] == band:
          return alert
  return None


class RoundedFrame:
  """A rounded line's clockwise path from the top-left end of the top edge: straight sides and quarter-circle corners."""
  CORNERS = {'top': 270, 'right': 0, 'bottom': 90, 'left': 180}   # raylib angle where the corner after each side starts

  def __init__(self, rect, thickness, radius):
    self.rect, self.thickness, self.radius = rect, thickness, radius
    self.arc = math.pi / 2 * (radius - thickness / 2)             # quarter-circle length along the stroke's centre
    straight = {'top': rect.width - 2 * radius, 'right': rect.height - 2 * radius}
    straight.update(bottom=straight['top'], left=straight['right'])
    self.pieces, at = [], 0.
    for side in ('top', 'right', 'bottom', 'left'):
      self.pieces += [(side, at, straight[side]), (side + '_corner', at + straight[side], self.arc)]
      at += straight[side] + self.arc
    self.total = at

  def draw(self, start, length, color):
    start %= self.total
    for begin, end in [(start, min(start + length, self.total))] + ([(0., start + length - self.total)] if start + length > self.total else []):
      for name, at, size in self.pieces:
        low, high = max(begin, at), min(end, at + size)
        if high > low:
          self._piece(name, low - at, high - at, color)

  def _piece(self, name, low, high, color):
    x, y, width, height, t, r = self.rect.x, self.rect.y, self.rect.width, self.rect.height, self.thickness, self.radius
    if name.endswith('_corner'):
      side = name[:-7]
      cx = x + r if side in ('bottom', 'left') else x + width - r
      cy = y + r if side in ('top', 'left') else y + height - r
      start = self.CORNERS[side] + math.degrees(low / (r - t / 2))
      end = self.CORNERS[side] + math.degrees(high / (r - t / 2))
      rl.draw_ring(rl.Vector2(cx, cy), r - t, r, start, end, max(2, round(16 * (high - low) / self.arc)), color)
    elif name == 'top':
      rl.draw_rectangle_rec(rl.Rectangle(x + r + low, y, high - low, t), color)
    elif name == 'right':
      rl.draw_rectangle_rec(rl.Rectangle(x + width - t, y + r + low, t, high - low), color)
    elif name == 'bottom':
      rl.draw_rectangle_rec(rl.Rectangle(x + width - r - high, y + height - t, high - low, t), color)
    else:
      rl.draw_rectangle_rec(rl.Rectangle(x, y + height - r - high, t, high - low), color)

  def ring(self, color):
    t, rect = self.thickness, self.rect
    inner = rl.Rectangle(rect.x + t, rect.y + t, rect.width - 2 * t, rect.height - 2 * t)
    # raylib strokes rounded lines outward from the rectangle, so `inner` carries the stroke's inner radius.
    rl.draw_rectangle_rounded_lines_ex(inner, 2 * (self.radius - t) / min(inner.width, inner.height), 24, t, color)


def card_scale(config):
  value = config.get('scale', 100)
  return min(UNIDEN_SCALE_RANGE[1], max(UNIDEN_SCALE_RANGE[0], value)) / 100 if type(value) is int else 1.


def signal_color(strength, index):
  """Galaxy update progress colors: secondary teal to primary purple."""
  t = index / max(1, strength - 1)
  return rl.Color(math.floor(77 + 80 * t + .5), math.floor(232 - 118 * t + .5), math.floor(232 + 23 * t + .5), 255)


def card_size(alerts):
  """One row per alert, the band, frequency, signal and arrow side by side."""
  return (SIZE[0], PAD * 2 + ROW * min(len(alerts), MAX_ROWS)) if alerts else SIZE


def outline(x, y, width, height, color, gaps=()):
  """Rounded card edge, with openings for inline labels on the top edge."""
  r, t = RADIUS, BORDER
  top, start = [], x + r
  for left, right in sorted(gaps):
    top.append((start, left))
    start = max(start, right)
  top.append((start, x + width - r))
  for left, right in top:
    if right > left:
      rl.draw_rectangle_rec(rl.Rectangle(left, y, right - left, t), color)
  rl.draw_rectangle_rec(rl.Rectangle(x + r, y + height - t, width - 2 * r, t), color)
  rl.draw_rectangle_rec(rl.Rectangle(x, y + r, t, height - 2 * r), color)
  rl.draw_rectangle_rec(rl.Rectangle(x + width - t, y + r, t, height - 2 * r), color)
  for cx, cy, start in ((x + r, y + r, 180), (x + width - r, y + r, 270), (x + width - r, y + height - r, 0), (x + r, y + height - r, 90)):
    rl.draw_ring(rl.Vector2(cx, cy), r - t, r, start, start + 90, 8, color)


def direction_arrows(cx, cy, direction, accent, radius):
  # Side means both sides: this detector protocol cannot distinguish left/right.
  for name, dx, dy in (('front', 0, -1), ('rear', 0, 1), ('side', -1, 0), ('side', 1, 0)):
    if name != direction:
      continue
    # Center the active glyph in its row, rather than leaving it on a compass edge.
    center_x = cx + dx * radius if direction == 'side' else cx
    half_length = radius * (.65 if direction == 'side' else .85)
    tip = rl.Vector2(center_x + dx * half_length, cy + dy * half_length)
    left = rl.Vector2(center_x - dx * half_length - dy * radius, cy - dy * half_length + dx * radius)
    right = rl.Vector2(center_x - dx * half_length + dy * radius, cy - dy * half_length - dx * radius)
    rl.draw_triangle(tip, right, left, accent)


class EdgeMotion:
  """Pulses when an alert starts or gets worse, a short fade when it ends, and stillness in between."""

  def __init__(self):
    self.alert, self.baseline, self.pulses, self.ended = None, 0, [], None

  def update(self, alert, now, still):
    if alert is None:
      if self.alert is not None:
        self.ended, self.alert = (now, self.alert), None
      return
    previous, self.alert, self.ended = self.alert, alert, None
    new = previous is None or previous['band'] != alert['band'] or \
      (alert['frequency'] is not None and previous['frequency'] is not None and abs(alert['frequency'] - previous['frequency']) > .01)
    if new:
      self.baseline = alert['strength']
      if previous is None or BAND_RANK.get(alert['band'], 0) > BAND_RANK.get(previous['band'], 0):
        self._pulse(now, still, 3 if alert['band'] == 'LASER' else 2 if previous is None else 1)
    elif alert['strength'] >= self.baseline + 2:
      self.baseline = alert['strength']
      self._pulse(now, still, 1)
    else:
      self.baseline = min(self.baseline, alert['strength'])

  def _pulse(self, now, still, count):
    if not still:
      self.pulses.append((now, count, .25 if count == 3 else .3))

  def boost(self, now):
    self.pulses = [p for p in self.pulses if now - p[0] < p[1] * p[2]]
    return max((math.sin(math.pi * (((now - at) / period) % 1)) for at, _, period in self.pulses), default=0.)

  def fading(self, now):
    """The last alert and how much of it is left while it fades out, or None."""
    if self.ended is None or now - self.ended[0] >= FADE_S:
      self.ended = None
      return None
    return self.ended[1], 1 - (now - self.ended[0]) / FADE_S


def tint(color, alpha):
  return rl.Color(color.r, color.g, color.b, max(0, min(255, round(255 * alpha))))


def rounded_band(content, radius, inset, width, color):
  """A stroke `width` wide whose outer edge is `inset` inside `content`, concentric with its rounded corners."""
  inner = rl.Rectangle(content.x + inset + width, content.y + inset + width, content.width - 2 * (inset + width), content.height - 2 * (inset + width))
  r = max(1., radius - inset - width)
  rl.draw_rectangle_rounded_lines_ex(inner, min(1., 2 * r / min(inner.width, inner.height)), 16, width, color)


def glow_ring(content, radius, depth, alpha, color):
  """Concentric strokes fading inward: a soft band along the whole edge."""
  band = depth / GLOW_STEPS
  for i in range(GLOW_STEPS):
    fall = 1 - i / GLOW_STEPS
    rounded_band(content, radius, i * band, band + .6, tint(color, alpha * fall * fall))


def chevron_points(side, along, content, index):
  """Apex and the two arm ends of one chevron pointing toward `side`, `index` steps in from that edge."""
  size, off = CHEVRON_SIZE, CHEVRON_FROM_EDGE + index * CHEVRON_SIZE * .8 + CHEVRON_SIZE * .4
  if side == 'top':
    cx, cy, dx, dy = along, content.y + off, 0, -1
  elif side == 'bottom':
    cx, cy, dx, dy = along, content.y + content.height - off, 0, 1
  elif side == 'left':
    cx, cy, dx, dy = content.x + off, along, -1, 0
  else:
    cx, cy, dx, dy = content.x + content.width - off, along, 1, 0
  apex = rl.Vector2(cx + dx * size * .5, cy + dy * size * .5)
  return (rl.Vector2(cx - dy * size - dx * size * .5, cy + dx * size - dy * size * .5), apex,
          rl.Vector2(cx + dy * size - dx * size * .5, cy - dx * size - dy * size * .5))


def chevron_sets(direction, width, height, speed_x):
  """Ahead (and the R4, which gives no direction) flank the speed; behind is bottom centre; beside is both side edges."""
  top = [('top', speed_x - CHEVRON_FROM_SPEED), ('top', speed_x + CHEVRON_FROM_SPEED)]
  return {'front': top, '': top, 'rear': [('bottom', speed_x)], 'side': [('left', height / 2), ('right', height / 2)]}.get(direction, top)


class UnidenWidget:
  def __init__(self, fonts, feed=None):
    self.fonts = fonts
    self.feed = feed or DetectorFeed()
    self.pressed = None
    self.held = False
    self.motion = EdgeMotion()
    self.runtime_active = False
    self.preview_data = None
    self.visible_until = 0.
    self.session = None
    self.claimed = False

  def activate(self, session):
    self.session, self.runtime_active = session, True
    return self

  @staticmethod
  def placement(state):
    if state is None or state.alert.size != 'none':
      return None
    p = state.customization['layouts']['large'].get(UNIDEN_WIDGET)
    return p if p and p['enabled'] else None

  def render(self, state):
    p = self.placement(state)
    self.visible_until = 0.
    if self.session is None:  # the comma shell drives its own feed
      self.feed.set_enabled(self.runtime_active)
    data = self.preview_data or self.feed.snapshot()
    if not p or not data.get('config', {}).get('enabled'):
      return
    config = {**data.get('config', {}), **p}
    if data.get('offline'):
      self._offline(state, config, p['x'], p['y'])
      return
    if self.pressed and not self.held and time.monotonic() - self.pressed[3] >= HOLD_S:
      self.held = True
      self.feed.lock_here(data)
    alerts = shown_alerts(data.get('alerts', []), data.get('config', {}))
    if not alerts and config.get('hide_when_no_alert', True):
      return
    self.visible_until = time.monotonic() + 1
    scale = card_scale(config)
    if scale == 1:
      self._card(state, data, alerts, config, p['x'], p['y'])
      return
    rl.rl_push_matrix()
    try:
      rl.rl_translatef(p['x'], p['y'], 0)
      rl.rl_scalef(scale, scale, 1)
      self._card(state, data, alerts, config, 0, 0)
    finally:
      rl.rl_pop_matrix()

  def _offline(self, state, config, x, y):
    width, height = OFFLINE_SIZE
    palette = widget_palette({'palette': PALETTE, **state.customization}, 'large', UNIDEN_WIDGET)
    fill = hex_color(palette['cardFill'])
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, height), .5, 12,
                              rl.Color(fill.r, fill.g, fill.b, round(fill.a * (100 - config.get('transparency', 8)) / 100)))
    outline(x, y, width, height, hex_color(palette['cardBorder']))
    self.fonts.draw('DETECTOR OFFLINE', FontRole.SEMI_BOLD, 26, x + 24, y + 13, rl.Color(255, 186, 78, 255))

  def render_border(self, state, width, height):
    """Frame the view for a live Ka, K or Laser alert, on top of the engagement border and any openpilot alert."""
    data = self.feed.snapshot()
    if data.get('offline') and data.get('config', {}).get('enabled') and self.placement(state) is None:
      self._offline(state, data['config'], (width - OFFLINE_SIZE[0]) / 2, BADGE_TOP)
    config = data.get('config', {})
    alert = threat(shown_alerts(data.get('alerts', []), config), config.get('border_bands', ())) if data.get('connected') else None
    still = state.customization.get('reduceMotion')
    now = time.monotonic()
    self.motion.update(alert, now, still)
    if alert is None:
      if (fade := self.motion.fading(now)) is not None:
        self._edge(state, data.get('config', {}), fade[0], width, height, now, fade[1])
      return
    self._edge(state, data['config'], alert, width, height, now, 1.)

  @staticmethod
  def _color(config, alert):
    """The band's frame color, and how much of it a mute leaves: 0 hides the frame while muted."""
    colors = {**DEFAULT_CONFIG['border_colors'], **config.get('border_colors', {})}
    # Muted by the detector, or quieted by StarPilot (timed mute, below the limit, lockout or frequency rule).
    muted = alert.get('muted') or alert.get('timed') or alert.get('quiet') or alert.get('filtered')
    return hex_color(BORDER_SWATCHES[colors[alert['band']]] + 'FF'), \
      config.get('border_muted_percent', DEFAULT_CONFIG['border_muted_percent']) / 100 if muted else 1.

  def _edge(self, state, config, alert, width, height, now, presence):
    color, mute = self._color(config, alert)
    if mute == 0:
      return
    parts = {**DEFAULT_CONFIG['border_edge'], **config.get('border_edge', {})}
    content = rl.Rectangle(EDGE_FRAME, EDGE_FRAME, width - 2 * EDGE_FRAME, height - 2 * EDGE_FRAME)
    radius = EDGE_ROUNDNESS * min(content.width, content.height) / 2
    s, boost, laser = (alert['strength'] - 1) / 7, self.motion.boost(now), alert['band'] == 'LASER'
    alpha = (.95 if laser else .45 + .45 * s) if parts['glow'] else (1. if laser else .85)
    alpha = min(1., alpha * (1 + .9 * boost)) * mute * presence
    depth = (GLOW_DEPTH[0] + (GLOW_DEPTH[1] - GLOW_DEPTH[0]) * s) * (1 + .35 * boost)
    sides = DIRECTION_SIDES.get(alert['direction'])
    if parts['veil']:
      glow_ring(content, radius, depth * 1.3, VEIL_ALPHA * min(1., alpha / .6), rl.Color(0, 0, 0, 255))
    if parts['glow'] and sides:
      # The facing edge carries the glow; the rest keeps a faint rim. Corners stay with the rim.
      glow_ring(content, radius, max(18., depth * .3), alpha * .5, color)
      deep, x, y, w, h = depth * 1.6, content.x, content.y, content.width, content.height
      for side in sides:
        for near, far, a, b in ((0, .45, 1., .35), (.45, 1., .35, 0.)):
          start, size = deep * near, deep * (far - near)
          first, second = tint(color, alpha * a), tint(color, alpha * b)
          if side == 'top':
            rl.draw_rectangle_gradient_v(int(x + radius), int(y + start), int(w - 2 * radius), int(size), first, second)
          elif side == 'bottom':
            rl.draw_rectangle_gradient_v(int(x + radius), int(y + h - start - size), int(w - 2 * radius), int(size), second, first)
          elif side == 'left':
            rl.draw_rectangle_gradient_h(int(x + start), int(y + radius), int(size), int(h - 2 * radius), first, second)
          else:
            rl.draw_rectangle_gradient_h(int(x + w - start - size), int(y + radius), int(size), int(h - 2 * radius), second, first)
    elif parts['glow']:
      glow_ring(content, radius, depth, alpha, color)
    if parts['line']:
      frame = RoundedFrame(rl.Rectangle(content.x + EDGE_GAP, content.y + EDGE_GAP, content.width - 2 * EDGE_GAP, content.height - 2 * EDGE_GAP),
                           EDGE_LINE, radius - EDGE_GAP)
      line = tint(color, min(1., alpha * 1.15))
      if parts['dashes']:
        at = 0.
        while at < frame.total:
          frame.draw(at, min(DASH, frame.total - at), line)
          at += DASH + DASH_GAP
      else:
        frame.ring(line)
    if parts['chevrons']:
      try:
        speed_x = width / 2 + offset(state.customization, 'large', 'current_speed')[0]
      except (KeyError, TypeError):
        speed_x = width / 2
      mark = tint(color, min(1., alpha * 1.1))
      for side, along in chevron_sets(alert['direction'], width, height, speed_x):
        for i in range(math.ceil(alert['strength'] / 2)):
          first, apex, last = chevron_points(side, along, content, i)
          rl.draw_line_ex(first, apex, CHEVRON_WIDTH, mark)
          rl.draw_line_ex(apex, last, CHEVRON_WIDTH, mark)

  def _card(self, state, data, alerts, config, x, y):
    width, height = card_size(alerts)
    palette = widget_palette({'palette': PALETTE, **state.customization}, 'large', UNIDEN_WIDGET)
    ink, fill, border = (hex_color(palette[key]) for key in ('text', 'cardFill', 'cardBorder'))
    text = rl.Color(ink.r, ink.g, ink.b, 255)
    dim = rl.Color(round(ink.r * .7), round(ink.g * .7), round(ink.b * .7), 255)
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, height), RADIUS * 2 / min(width, height), 12,
                              rl.Color(fill.r, fill.g, fill.b, round(fill.a * (100 - config.get('transparency', 8)) / 100)))
    def label(value, size, dx, dy, color=text):
      self.fonts.draw(value, FontRole.SEMI_BOLD, size, x + dx, y + dy, color)
    if not data.get('connected') or not alerts:
      outline(x, y, width, height, border)
      label('UNIDEN', 22, 24, 20, dim)
      if not data.get('connected'):
        label('Detector disconnected', 28, 24, 56)
        label('Waiting for fresh alerts', 20, 24, 105, dim)
      else:
        label('Detector ready', 28, 24, 56, rl.Color(100, 214, 151, 255))
        label('No active alerts', 20, 24, 105, dim)
      return

    # Mute status has its own right-hand label so strength and notices cannot hide it.
    muted = sum(bool(item['muted']) for item in alerts)
    quieted = sum(bool(mute_reason(item)) for item in alerts)
    gaps = []
    note_right = x + width - 20
    if quieted:
      status = 'MUTED' if quieted == len(alerts) else f'{quieted} MUTED'
      measured = self.fonts.measure(status, FontRole.SEMI_BOLD, NOTE_SIZE)
      mute_gap = (x + width - 20 - measured.width - 16, x + width - 20)
      gaps.append(mute_gap)
      self.fonts.draw(status, FontRole.SEMI_BOLD, NOTE_SIZE, mute_gap[0] + 8,
                      y + BORDER / 2 - measured.height / 2, rl.Color(255, 91, 101, 255))
      note_right = mute_gap[0] - 12
    notes = [data.get('notice') or ('Strong signal' if any(a.get('breakthrough') for a in alerts) else
                                    mute_note(alerts) or ('Mute sent' if data.get('command') and muted < len(alerts) else ''))]
    if len(alerts) > MAX_ROWS:
      notes.append(f'+{len(alerts) - MAX_ROWS} more')
    note = '  •  '.join(n for n in notes if n)
    if note:
      size = NOTE_SIZE
      available = note_right - (x + 20) - 16
      original = note
      while note and self.fonts.measure(note, FontRole.SEMI_BOLD, size).width > available:
        original = original[:-1].rstrip()
        note = original + '…' if original else ''
      measured = self.fonts.measure(note, FontRole.SEMI_BOLD, size)
      if note:
        gap = (x + 20, x + 20 + measured.width + 16)
        gaps.append(gap)
        self.fonts.draw(note, FontRole.SEMI_BOLD, size, gap[0] + 8, y + BORDER / 2 - measured.height / 2, border)
    outline(x, y, width, height, border, gaps)

    shown = alerts[:MAX_ROWS]
    band_size, frequency_size = 36, 28
    band_width = max(self.fonts.measure(item['band'], FontRole.SEMI_BOLD, band_size).width for item in shown)
    frequency_x = 24 + max(64, band_width + 18)
    arrows = data.get('model') == 'R8'
    arrow_x = width - 24 - 25                       # room for the two side arrows, which sit either side of this
    bars_x = (arrow_x - 25 - 18 if arrows else width - 24) - 8 * 11 + 3
    for row, item in enumerate(shown):
      cy = PAD + ROW * row + ROW / 2
      faded = item['muted'] and muted < len(alerts)
      tone = rl.Color(255, 91, 101, 255) if item['band'] in ('KA', 'LASER') else rl.Color(255, 186, 78, 255)
      if faded:
        tone = rl.Color(round(tone.r * .55), round(tone.g * .55), round(tone.b * .55), 255)
      band = self.fonts.measure(item['band'], FontRole.SEMI_BOLD, band_size)
      label(item['band'], band_size, 24, cy - band.height / 2, tone)
      if item['frequency'] is not None or item['band'] != 'LASER':
        frequency = f"{item['frequency']:.3f} GHz" if item['frequency'] is not None else 'Gatso'
        measured = self.fonts.measure(frequency, FontRole.SEMI_BOLD, frequency_size)
        label(frequency, frequency_size, frequency_x, cy - measured.height / 2, dim if faded else text)
      for i in range(8):
        bar_height = 8 + i * 3
        lit = i < item['strength']
        fill = signal_color(item['strength'], i) if lit else rl.Color(53, 67, 77, 255)
        if lit and faded:
          fill = rl.Color(round(fill.r * .55), round(fill.g * .55), round(fill.b * .55), 255)
        rl.draw_rectangle(int(x + bars_x + i * 11), int(y + cy + 15 - bar_height), 8, bar_height, fill)
      if arrows:
        direction_arrows(x + arrow_x, y + cy, item['direction'], tone, 15)

  def touch(self, kind, x, y, state):
    p, data = self.placement(state), self.feed.snapshot()
    config = {**data.get('config', {}), **(p or {})}
    shown = shown_alerts(data.get('alerts', []), data.get('config', {}))
    valid = (p is not None and time.monotonic() < self.visible_until and data.get('connected') and
             data.get('can_mute') and bool(shown))
    scale = card_scale(config)
    width, height = (side * scale for side in card_size(shown))
    inside = valid and p['x'] <= x <= p['x'] + width and p['y'] <= y <= p['y'] + height
    key = (data.get('address'), alert_key(data.get('alerts', [])))
    if kind == 'down':
      self.pressed = (key, x, y, time.monotonic()) if inside else None
      self.held = False
    claimed = self.pressed is not None
    if kind in ('move', 'up') and self.pressed:
      old, px, py, _ = self.pressed
      if not inside or old != key or (px - x) ** 2 + (py - y) ** 2 > 36 ** 2:
        self.pressed = None
      elif kind == 'up' and not self.held:
        self.feed.mute(data)
    if kind in ('up', 'cancel'):
      self.pressed, self.held = None, False
    return claimed

  def _state(self, mode):
    return self.session.snapshot(mode).onroad if mode == ShellMode.ONROAD else None

  def press(self, mode, x, y):
    session = self.session
    self.claimed = bool(mode == ShellMode.ONROAD and not session.favorites.is_open and self.touch('down', x, y, self._state(mode)))
    if self.claimed:
      session.input.cancel()
    return self.claimed

  def move(self, mode, x, y):
    if self.claimed:
      self.touch('move', x, y, self._state(mode))
    return self.claimed

  def release(self, mode, x, y):
    claimed, self.claimed = self.claimed, False
    if claimed:
      self.touch('up', x, y, self._state(mode))
    return claimed

  def cancel(self):
    self.claimed = False
    self.touch('cancel', 0, 0, None)

  def close(self):
    self.feed.close()
