"""The Android Auto now-playing card; both live rendering and previews use this drawing."""
import time

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.soloist.now_playing import elapsed_ms
from openpilot.starpilot.system.starport.projection_layout import NOW_PLAYING_SIZE, NOW_PLAYING_WIDGET
from openpilot.starpilot.ui.presentation import FontRole

ACCENT = rl.Color(199, 174, 247, 255)
INK = rl.Color(15, 13, 23, 255)
TEXT = rl.Color(245, 242, 252, 255)
MUTED = rl.Color(184, 178, 200, 255)
BUTTON_R = 30


def _play_pause(center: rl.Vector2, playing: bool) -> None:
  """An accent disc with the pause bars while playing, else the play triangle."""
  rl.draw_circle_v(center, BUTTON_R, ACCENT)
  if playing:
    for dx in (-9, 3):
      rl.draw_rectangle_rounded(rl.Rectangle(center.x + dx, center.y - 12, 6, 24), .5, 4, INK)
  else:
    rl.draw_triangle(rl.Vector2(center.x - 7, center.y - 13), rl.Vector2(center.x - 7, center.y + 13), rl.Vector2(center.x + 13, center.y), INK)


class NowPlayingWidget:
  def __init__(self, fonts, clock=time.time):  # noqa: TID251 epoch-ms track anchors
    self.fonts = fonts
    self.clock = clock
    self.track = None

  def _fit(self, text, role, size, width):
    measured = self.fonts.measure(text, role, size)
    return size * width / measured.width if measured.width > width else size

  def render(self, state):
    placed = state.customization['layouts']['large'].get(NOW_PLAYING_WIDGET)
    track = self.track
    if not placed or not placed['enabled'] or not track or state.alert.size != 'none':
      return
    rect = rl.Rectangle(placed['x'], placed['y'], *NOW_PLAYING_SIZE)
    h = rect.height
    background = rl.Color(15, 13, 23, round(235 * placed.get('opacity', 85) / 100))
    playing = track['status'] == 'playing'
    if not playing and placed.get('collapse'):
      # Paused: just the play button, at whichever end of the card sits nearer the screen edge.
      right = rect.x + rect.width / 2 > getattr(state, 'viewport_width', 0) / 2
      button = rl.Rectangle(rect.x + rect.width - h if right else rect.x, rect.y, h, h)
      rl.draw_rectangle_rounded(button, 1.0, 16, background)
      _play_pause(rl.Vector2(button.x + h / 2, button.y + h / 2), False)
      return
    rl.draw_rectangle_rounded(rect, 1.0, 16, background)
    _play_pause(rl.Vector2(rect.x + h / 2, rect.y + h / 2), playing)
    left = rect.x + h + 4
    width = rect.x + rect.width - h / 2 - left
    rows = ((track['title'], FontRole.SEMI_BOLD, 30, 12, TEXT), (track['artist'] or track['album'], FontRole.NORMAL, 22, 46, MUTED))
    for text, role, size, y, color in rows:
      if text:
        self.fonts.draw(text, role, self._fit(text, role, size, width), left, rect.y + y, color)
    duration = track.get('durationMs')
    if duration and track.get('position'):
      bar = rl.Rectangle(left, rect.y + h - 16, width, 4)
      rl.draw_rectangle_rounded(bar, 1.0, 4, rl.Color(255, 255, 255, 50))
      done = min(1.0, elapsed_ms(track, self.clock() * 1000) / duration)
      if done > 0:
        rl.draw_rectangle_rounded(rl.Rectangle(bar.x, bar.y, max(bar.height, bar.width * done), bar.height), 1.0, 4, ACCENT)
