"""One frog, one button, bounded scenery; Arabesque owns the window and input."""

from importlib.resources import as_file
from pathlib import Path
import random

from openpilot.starpilot.ui.raylib import rl

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.layouts.arabesque.theme import DEFAULT_THEME as THEME
from openpilot.system.ui.lib.application import ASSETS_DIR, gui_app, TextAlignment
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget, device
from openpilot.system.ui.widgets.label import gui_label

_STEP = 1 / 60
_BLACK_HOLE = 2
_WORMHOLE = 3
_SHIELD_PICKUP = 4
_FEATHER_PICKUP = 5
_STAR_PICKUP = 6
_FEATHER_DURATION = 5.0
_FEATHER_TRIGGER_DISTANCE = 155
_STAR_RANGE = 320
_DIFFICULTY_DISTANCE = 30000
_SCORE_FILE = Path(__file__).with_name('rebecca-score.txt')
_INK = rl.Color(118, 98, 139, 255)
_STONE = rl.Color(169, 139, 153, 255)
_CREAM = rl.Color(239, 221, 183, 255)
_CORAL = rl.Color(222, 145, 136, 255)
_FIELD = rl.Color(118, 98, 139, 70)
_BIZARRO_BACKGROUND = rl.Color(27, 13, 31, 255)
_BIZARRO_INK = _CORAL
_BIZARRO_STONE = _INK
_BIZARRO_CREAM = rl.Color(205, 181, 247, 255)
_BIZARRO_CORAL = _STONE
_BIZARRO_FIELD = rl.Color(222, 145, 136, 70)


class Rebecca(Widget):
  def __init__(self):
    super().__init__()
    self._sprite = None
    self._sounds, self._owns_audio = {}, False
    self._best, self._notice = 0, ''
    self._pressed_button = False
    self._rng = random.Random()
    self._reset()
    self._state = 'ready'

  def _reset(self):
    self._state = 'flying'
    self._y, self._vy, self._vx = 208.0, -160.0, 150.0
    self._distance = self._elapsed = self._accumulator = self._cooldown = 0.0
    self._objects = []
    self._next, self._encounter, self._corridor = 0.0, 0, 208.0
    self._record = False
    self._bizarro = False
    self._shield = False
    self._feather = self._invulnerable = 0.0
    self._feather_armed = self._star = False
    self._star_flash = 0.0

  def show_event(self):
    super().show_event()
    self._sprite = gui_app.texture('arabesque/rebecca.png', 256, 256)
    try:
      self._best = max(0, int(_SCORE_FILE.read_text(encoding='ascii')))
    except (FileNotFoundError, ValueError):
      self._best = 0
    except OSError:
      self._notice = tr('Best score unavailable')
    try:
      self._owns_audio = not rl.is_audio_device_ready()
      if self._owns_audio:
        rl.init_audio_device()
      if rl.is_audio_device_ready():
        for name in ('flap', 'loss', 'best'):
          with as_file(ASSETS_DIR.joinpath('arabesque', name + '.wav')) as path:
            sound = rl.load_sound(str(path))
          if sound.frameCount:
            self._sounds[name] = sound
            rl.set_sound_volume(sound, 0.35)
    except (OSError, RuntimeError) as error:
      cloudlog.warning(f'Rebecca audio unavailable: {error}')

  def hide_event(self):
    self._save_best()
    for sound in self._sounds.values():
      rl.stop_sound(sound)
      rl.unload_sound(sound)
    self._sounds.clear()
    if self._owns_audio and rl.is_audio_device_ready():
      rl.close_audio_device()
    self._owns_audio = False
    self._state, self._accumulator, self._bizarro = 'ready', 0.0, False
    self._shield = False
    self._feather = self._invulnerable = 0.0
    self._feather_armed = self._star = False
    self._star_flash = 0.0
    super().hide_event()

  def _play(self, name):
    if name in self._sounds:
      rl.play_sound(self._sounds[name])

  def _save_best(self):
    score = int(self._distance / 10)
    if score > self._best:
      try:
        # shortcut: no power-loss durability; add fsync if records must survive abrupt power cuts.
        temporary = _SCORE_FILE.with_suffix('.tmp')
        temporary.write_text(f'{score}\n', encoding='ascii')
        temporary.replace(_SCORE_FILE)
        self._best, self._record = score, True
      except OSError:
        self._notice = tr('Best score could not be saved')

  def _update_layout_rects(self):
    rect = self._rect
    self._scale = s = min(rect.width / 1080, rect.height / 488)
    self._ox = rect.x + (rect.width - 1080 * s) / 2
    self._oy = rect.y + (rect.height - 488 * s) / 2
    self._button = rl.Rectangle(rect.x, rect.y + rect.height - 72 * s, rect.width, 72 * s)

  def _handle_mouse_press(self, pos):
    self._pressed_button = rl.check_collision_point_rec(pos, self._button)
    if not self._pressed_button or self._cooldown:
      return
    if self._state != 'flying':
      self._reset()
    self._vy = 160.0 if self._bizarro else -160.0
    self._play('flap')

  def _spawn(self):
    self._corridor = max(156, min(260, self._corridor + self._rng.uniform(-60, 60)))
    x, y = self._distance + 1140, self._corridor
    encounter = self._encounter
    kind = self._encounter % 3
    if kind == _BLACK_HOLE and self._encounter % 12 == 8:
      kind = _WORMHOLE
      # Passed black holes must not consume the return portal's slot.
      self._objects = [o for o in self._objects if o[0] != _BLACK_HOLE or o[1] - self._distance >= 230]
    if kind == 0 and len(self._objects) <= 4:
      self._objects.extend((0, x, y + offset, self._rng.uniform(44, 54)) for offset in (-120, 120))
    elif len(self._objects) < 6:
      if kind in (_BLACK_HOLE, _WORMHOLE) and any(o[0] in (_BLACK_HOLE, _WORMHOLE) for o in self._objects):
        kind = 1
      self._objects.append((kind, x, self._rng.choice((76, 340)), 26))
    if encounter % 6 == 4 and len(self._objects) < 6:
      reward = (_SHIELD_PICKUP, _FEATHER_PICKUP, _STAR_PICKUP)[encounter // 6 % 3]
      self._objects.append((reward, x - 220, y, 18))
    self._encounter += 1
    self._next = self._distance + 440 - 80 * self._difficulty()

  def _difficulty(self):
    return min(1.0, self._distance / _DIFFICULTY_DISTANCE)

  def _update_state(self):
    if not self.enabled or not device.awake:
      self._accumulator = 0.0
      return
    dt = max(0.0, min(rl.get_frame_time(), 4 * _STEP))
    self._cooldown = max(0.0, self._cooldown - dt)
    if self._state != 'flying':
      return
    self._accumulator += dt
    while self._accumulator >= _STEP and self._state == 'flying':
      self._accumulator -= _STEP
      self._step()

  def _step(self):
    self._star_flash = max(0.0, self._star_flash - _STEP)
    if self._feather_armed and any(
      kind in (_BLACK_HOLE, _WORMHOLE) and
      (x - self._distance - 230) ** 2 + (y - self._y) ** 2 < _FEATHER_TRIGGER_DISTANCE ** 2
      for kind, x, y, _radius in self._objects
    ):
      self._feather_armed = False
    if self._feather and not self._feather_armed:
      self._feather = max(0.0, self._feather - _STEP)
    self._invulnerable = max(0.0, self._invulnerable - _STEP)
    difficulty = self._difficulty()
    gravity = -1 if self._bizarro else 1
    gravity *= 110.0 if self._feather and not self._feather_armed else 330.0
    force_sign = -1 if self._bizarro else 1
    ax, ay = (150 + 55 * difficulty - self._vx) * 3, gravity
    for kind, x, y, _radius in self._objects:
      dx, dy = x - self._distance - 230, y - self._y
      d2 = dx * dx + dy * dy
      if kind in (_BLACK_HOLE, _WORMHOLE) and d2 < 185 ** 2:
        force = force_sign * min(4.5, 90000 / (d2 + 6400))
        ax, ay = ax + dx * force, ay + dy * force
    self._vx = max(90, min(240, self._vx + ax * _STEP))
    self._vy = max(-240, min(260, self._vy + ay * _STEP))
    self._distance += self._vx * _STEP
    self._y += self._vy * _STEP
    self._elapsed += _STEP
    self._objects = [o for o in self._objects if o[1] - self._distance > -200]
    if any(kind == _WORMHOLE and (x - self._distance - 230) ** 2 + (y - self._y) ** 2 < (radius + 30) ** 2
           for kind, x, y, radius in self._objects):
      self._bizarro = not self._bizarro
      self._y, self._vy, self._corridor = 416 - self._y, -self._vy, 416 - self._corridor
      self._objects.clear()
      self._next = self._distance + 440
      return
    collected = [o for o in self._objects if o[0] in (_SHIELD_PICKUP, _FEATHER_PICKUP, _STAR_PICKUP) and
                 (o[1] - self._distance - 230) ** 2 + (o[2] - self._y) ** 2 < (o[3] + 30) ** 2]
    for kind, _, _, _ in collected:
      if kind == _SHIELD_PICKUP:
        self._shield = True
      elif kind == _FEATHER_PICKUP:
        self._feather = _FEATHER_DURATION
        self._feather_armed = True
      else:
        self._star = True
    if collected:
      self._objects = [o for o in self._objects if o not in collected]
    if self._distance >= self._next:
      self._spawn()
    if self._star:
      hazards = [o for o in self._objects if o[0] in (0, 1, _BLACK_HOLE) and
                 0 < o[1] - self._distance - 230 <= _STAR_RANGE]
      if hazards:
        self._objects.remove(min(hazards, key=lambda o: o[1]))
        self._star = False
        self._star_flash = 0.6
    collisions = [o for o in self._objects if o[0] not in (_WORMHOLE, _SHIELD_PICKUP, _FEATHER_PICKUP, _STAR_PICKUP) and
                  (o[1] - self._distance - 230) ** 2 + (o[2] - self._y) ** 2 < (o[3] + 30) ** 2]
    out_of_bounds = not 30 < self._y < 386
    if collisions or out_of_bounds:
      if self._shield:
        self._shield = False
        self._invulnerable = 0.75
      if self._shield or self._invulnerable:
        self._objects = [o for o in self._objects if o not in collisions]
        self._y, self._vy = 208.0, 0.0
        return
      self._state, self._cooldown = 'lost', 0.4
      self._save_best()
      self._play('loss')
      if self._record:
        self._play('best')

  def _label(self, x, y, width, height, text, size=18, color=None):
    s = self._scale
    if color is None:
      color = _BIZARRO_CREAM if self._bizarro else _CREAM
    gui_label(rl.Rectangle(self._ox + x * s, self._oy + y * s, width * s, height * s),
              text, round(size * s), color)

  def _render(self, rect):
    s, ox, oy = self._scale, self._ox, self._oy
    background = _BIZARRO_BACKGROUND if self._bizarro else THEME.background
    ink = _BIZARRO_INK if self._bizarro else _INK
    stone = _BIZARRO_STONE if self._bizarro else _STONE
    cream = _BIZARRO_CREAM if self._bizarro else _CREAM
    coral = _BIZARRO_CORAL if self._bizarro else _CORAL
    field = _BIZARRO_FIELD if self._bizarro else _FIELD
    control = _BIZARRO_CREAM if self._bizarro else THEME.control
    pressed = _BIZARRO_CORAL if self._bizarro else THEME.pressed
    rl.draw_rectangle_rec(rect, background)
    direction = -1 if self._bizarro else 1
    for i in range(32):
      x = (i * 137.51 - direction * self._distance * (0.12 + i % 3 * 0.08)) % 1080
      y = 24 + i * 83 % 360
      rl.draw_circle_v(rl.Vector2(ox + x * s, oy + y * s), (0.7 + i % 3 * 0.35) * s, ink if i % 4 else cream)
    streak = self._elapsed % 11
    if 0 < streak < 0.7:
      x, y = ox + (1000 - streak * 400) * s, oy + (45 + streak * 110) * s
      rl.draw_line_ex(rl.Vector2(x, y), rl.Vector2(x + 36 * s, y - 10 * s), s, cream)
    for kind, x, y, radius in self._objects:
      center = rl.Vector2(ox + (x - self._distance) * s, oy + y * s)
      r = radius * s
      if kind == 0:
        rl.draw_poly(center, 7, r, x % 360, stone)
        rl.draw_circle_v(rl.Vector2(center.x - r * 0.25, center.y - r * 0.22), r * 0.23, ink)
        rl.draw_circle_v(rl.Vector2(center.x + r * 0.32, center.y + r * 0.3), r * 0.12, ink)
      elif kind == 1:
        rl.draw_line_ex(rl.Vector2(center.x - r, center.y), rl.Vector2(center.x + r, center.y), 2 * s, cream)
        for offset in (-0.75, 0.75):
          rl.draw_rectangle_rec(rl.Rectangle(center.x + offset * r - 8 * s, center.y - 13 * s, 16 * s, 26 * s), ink)
        rl.draw_poly(center, 4, 12 * s, 0, cream)
      elif kind == _SHIELD_PICKUP:
        shield = ((-14, -12), (14, -12), (12, 2), (8, 10), (0, 17), (-8, 10), (-12, 2))
        for (ax, ay), (bx, by) in zip(shield, shield[1:] + shield[:1], strict=False):
          rl.draw_line_ex(rl.Vector2(center.x + ax * s, center.y + ay * s),
                          rl.Vector2(center.x + bx * s, center.y + by * s), 2.5 * s, coral)
        rl.draw_line_ex(rl.Vector2(center.x - 7 * s, center.y), rl.Vector2(center.x + 7 * s, center.y),
                        1.5 * s, coral)
      elif kind == _FEATHER_PICKUP:
        shaft_start = rl.Vector2(center.x - 12 * s, center.y + 13 * s)
        shaft_end = rl.Vector2(center.x + 12 * s, center.y - 13 * s)
        rl.draw_line_ex(shaft_start, shaft_end, 2.5 * s, cream)
        for index in range(1, 7):
          t = index / 8
          x = shaft_start.x + (shaft_end.x - shaft_start.x) * t
          y = shaft_start.y + (shaft_end.y - shaft_start.y) * t
          span = (8 - 3 * t) * s
          for side in (-1, 1):
            tip = rl.Vector2(x + side * span * 0.7, y + side * span * 0.7)
            rl.draw_line_ex(rl.Vector2(x, y), tip, 1.5 * s, cream)
      elif kind == _STAR_PICKUP:
        rl.draw_ring(center, 15 * s, 19 * s, 0, 360, 24, coral)
        for dx, dy in ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)):
          rl.draw_line_ex(rl.Vector2(center.x + dx * 4 * s, center.y + dy * 4 * s),
                          rl.Vector2(center.x + dx * 14 * s, center.y + dy * 14 * s), 2 * s, cream)
        rl.draw_circle_v(center, 3 * s, cream)
      else:
        rl.draw_ring(center, 184 * s, 185 * s, 0, 360, 48, field)
        if kind == _WORMHOLE:
          start = self._elapsed * 24 % 360
          rl.draw_ring(center, 46 * s, 48 * s, start, start + 300, 32, coral)
        rl.draw_ring(center, 32 * s, 34 * s, 0, 360, 32, coral)
        rl.draw_ring(center, 26 * s, 27 * s, 0, 360, 32, cream)
        rl.draw_circle_v(center, r, background)
    if self._sprite is not None:
      height = 96 * self._sprite.height / self._sprite.width
      destination = rl.Rectangle(ox + 230 * s, oy + self._y * s, 96 * s, height * s)
      if self._shield:
        player = rl.Vector2(ox + 230 * s, oy + self._y * s)
        rl.draw_ring(player, 56 * s, 60 * s, 0, 360, 32, coral)
      if self._feather:
        if not self._feather_armed:
          player = rl.Vector2(ox + 230 * s, oy + self._y * s)
          rl.draw_ring(player, 64 * s, 66 * s, 0, 360, 32, cream)
      rl.draw_texture_pro(self._sprite, rl.Rectangle(0, 0, self._sprite.width, self._sprite.height),
                          destination, rl.Vector2(48 * s, height * s / 2), 180 if self._bizarro else 0, rl.WHITE)
    self._label(24, 12, 400, 36, f'{int(self._distance / 10)} m', 24)
    self._label(810, 12, 246, 36, tr('Best') + f'  {max(self._best, int(self._distance / 10))} m', color=ink)
    if self._bizarro:
      self._label(450, 12, 180, 36, tr('Bizarro'), 18, coral)
    if self._shield:
      self._label(24, 52, 300, 32, tr('Shield ready'), 16, coral)
    if self._feather:
      label = tr('Feather ready') if self._feather_armed else tr('Feather') + f'  {self._feather:.1f}s'
      self._label(24, 80, 300, 32, label, 16, cream)
    if self._star_flash:
      self._label(24, 108, 300, 32, tr('Starburst!'), 16, coral)
    if self._state != 'flying':
      self._label(440, 142, 600, 64, tr('Rebecca, Adrift') if self._state == 'ready' else tr('Still unbothered.'), 36)
      caption = tr('Tap to Float. Dodge everything.') if self._state == 'ready' else tr('New best!') if self._record else tr('Space is rude. Try again.')
      self._label(444, 208, 580, 48, caption, color=coral if self._bizarro else THEME.secondary_text)
    if self._notice:
      self._label(24, 368, 1032, 40, self._notice, 16, coral)
    button_color = pressed if self._pressed_button and self.is_pressed and self.enabled else control
    rl.draw_rectangle_rec(self._button, button_color)
    label = {'ready': tr('Start floating'), 'flying': tr('Float'), 'lost': tr('Again')}[self._state]
    gui_label(self._button, label, round(24 * s), background, alignment=TextAlignment.CENTER)
