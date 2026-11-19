"""Big model card shared by the native and Android Auto screens: which model drives, and the external GPU's state."""
import re
import time
from typing import NamedTuple

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.models.catalog import BUNDLED_CURRENT, model_entries
from openpilot.starpilot.models.jetlink_status import JetlinkStatus
from openpilot.starpilot.models.manager import preferences
from openpilot.starpilot.models.receipt import read_pair_receipt, read_receipt
from openpilot.starpilot.models.status import ModelVariant
from openpilot.starpilot.ui import clip
from openpilot.starpilot.ui.models_state import model_display_text
from openpilot.starpilot.ui.onroad_customization import JETLINK_SIZE, JETLINK_WIDGET
from openpilot.starpilot.ui.presentation import FontRole

GREEN = rl.Color(70, 200, 110, 255)
ORANGE = rl.Color(255, 150, 60, 255)
ACCENT = rl.Color(199, 174, 247, 255)
GRAY = rl.Color(140, 136, 152, 255)
TEXT = rl.Color(245, 242, 252, 255)
MUTED = rl.Color(214, 210, 224, 255)
BIG, SMALL = 'Big model', 'Small model'
# 20 Hz frames: a host this close to 50 ms is about to fall behind
SLOW_MS = 40.
SCROLL_SPEED = 40
SCROLL_GAP = 60
SCROLL_PAUSE = 2.0
MODEL_TTL = 1.0
_TRAILING_DATE = re.compile(r' \([A-Za-z]+ \d{1,2}, \d{4}\)$')
ABBREVIATIONS = (
  ('Regret Driven Framework', 'RDF'),
  ('Terrific Super Fantastic', 'TSF'),
  ('Vegetarian-Filet-O-Fish', 'V-Filet-O-Fish'),
)
JETLINK_DETAIL = {'ready': ('Jetlink ready', ACCENT), 'connecting': ('Jetlink connecting', ACCENT),
                  'connected': ('Jetlink connected', ACCENT), 'disconnected': ('Jetlink offline', GRAY),
                  'unavailable': ('Jetlink error', ORANGE)}
CHESTNUT_DETAIL = {'loading': ('Chestnut loading', ACCENT), 'ready': ('Chestnut ready', ACCENT),
                   'uncompiled': ('Chestnut not built', ACCENT)}

_model_cache: tuple[float, tuple[str, str]] | None = None


def abbreviate_model_name(name: str) -> str:
  """Normalize and shorten catalog/Jetlink model names for the ASCII bitmap atlas and 420px card."""
  if '/' in name:
    return ' / '.join(part for p in name.split('/') if (part := abbreviate_model_name(p)))
  text = model_display_text(name)
  text = _TRAILING_DATE.sub('', text)
  for old in ('•', '·', '—', '–'):
    text = text.replace(old, '-')
  if text.endswith(' Model'):
    text = text[:-6].rstrip()
  for full, short in ABBREVIATIONS:
    text = text.replace(full, short)
  return text.strip()


def scroll_offset(text_width: float, width: float, seconds: float) -> float:
  """How far a too-wide line has scrolled left: still for SCROLL_PAUSE, then one pass to where its repeat starts."""
  if text_width <= width:
    return 0.0
  travel = text_width + SCROLL_GAP
  elapsed = max(0.0, seconds)
  return min(travel, max(0.0, elapsed % (SCROLL_PAUSE + travel / SCROLL_SPEED) - SCROLL_PAUSE) * SCROLL_SPEED)


def _entry_name(entries: dict, model_id: str, variant: ModelVariant | None = None) -> str:
  if not model_id:
    return ''
  if model_id == BUNDLED_CURRENT:
    return 'Cinque v2' if variant is ModelVariant.CHESTNUT else 'RDF V4'
  entry = entries.get(model_id)
  return entry.name if entry is not None else model_id


def local_model_names(now: float | None = None) -> tuple[str, str]:
  """Cached (small, big) driving model names from modeld receipts and saved preferences."""
  global _model_cache
  ts = time.monotonic() if now is None else now
  if _model_cache is not None and 0 <= ts - _model_cache[0] < MODEL_TTL:
    return _model_cache[1]
  entries = model_entries()
  pair = read_pair_receipt()
  if pair is not None:
    lat = abbreviate_model_name(_entry_name(entries, pair.lateral_id))
    lon = abbreviate_model_name(_entry_name(entries, pair.longitudinal_id))
    pair_name = ' / '.join(part for part in (lat, lon) if part)
    result = (pair_name, pair_name)
  else:
    prefs = preferences()
    small = _entry_name(entries, prefs.get('small', ''), ModelVariant.SMALL)
    big = _entry_name(entries, prefs.get('big', ''), ModelVariant.CHESTNUT)
    load = read_receipt()
    if load is not None:
      loaded = _entry_name(entries, load.model_id, load.variant)
      if load.variant is ModelVariant.CHESTNUT:
        big = loaded or big
      else:
        small = loaded or small
    result = (abbreviate_model_name(small), abbreviate_model_name(big))
  _model_cache = (ts, result)
  return result


class Card(NamedTuple):
  title: str
  detail: str
  dot: rl.Color
  latency_ms: float | None = None

  @property
  def tier(self) -> str:
    is_green = self.dot is GREEN or (self.dot.r, self.dot.g, self.dot.b) == (GREEN.r, GREEN.g, GREEN.b)
    return 'BIG' if is_green and self.detail in ('Jetlink', 'Chestnut') else 'SMALL'


def big_model_card(jetlink: JetlinkStatus, gpu: str | None = None, chestnut: bool = False,
                   *, small: str = '', big: str = '') -> Card:
  """`gpu` is UIState.chestnut_state's value or None; `chestnut` is whether one is fitted."""
  small_name = abbreviate_model_name(small) or SMALL
  jetlink_big = abbreviate_model_name(jetlink.model or big) or BIG
  chestnut_big = abbreviate_model_name(big) or BIG
  if jetlink.state != 'off':
    if jetlink.state == 'active':
      return Card(jetlink_big, 'Jetlink', GREEN, jetlink.latency_ms)
    if jetlink.offer:
      return Card(small_name, 'Re-engage to switch', ORANGE if gpu == 'failed' else ACCENT)
    if gpu == 'failed' and jetlink.state not in ('disconnected', 'unavailable'):
      return Card(small_name, 'Fell back', ORANGE)
    if jetlink.state == 'connected' and jetlink.building:
      return Card(small_name, 'Jetlink building', ACCENT)
    return Card(small_name, *JETLINK_DETAIL.get(jetlink.state, JETLINK_DETAIL['unavailable']))
  if not chestnut:
    return Card(small_name, '', GREEN)
  if gpu == 'active':
    return Card(chestnut_big, 'Chestnut', GREEN)
  if gpu == 'failed':
    return Card(small_name, 'Fell back', ORANGE)
  return Card(small_name, *CHESTNUT_DETAIL.get(gpu, ('Chestnut', ACCENT)))


def gpu_inputs(ui) -> tuple[str | None, bool]:
  """(UIState.chestnut_state's value, a Chestnut is fitted) for big_model_card."""
  if getattr(ui, 'projection_read_only', False):
    ui._update_chestnut_state()
  return ui.chestnut_state.value, bool(ui.sm["deviceState"].chestnutPresent)


class JetlinkStatusWidget:
  def __init__(self, fonts, ticks=time.monotonic, models=local_model_names):
    self.fonts = fonts
    self.ticks = ticks
    self.models = models
    self.status = JetlinkStatus()
    self.gpu: str | None = None
    self.chestnut = False
    self.small = ''
    self.big = ''
    self._scroll: tuple[str, float] | None = None

  def _fit(self, text, role, size, width):
    measured = self.fonts.measure(text, role, size)
    return size * width / measured.width if measured.width > width else size

  def render(self, state):
    placed = state.customization['layouts']['large'].get(JETLINK_WIDGET)
    if not placed or not placed['enabled'] or state.alert.size != 'none':
      return
    small, big = (self.small, self.big) if (self.small or self.big) else self.models()
    card = big_model_card(self.status, self.gpu, self.chestnut, small=small, big=big)
    rect = rl.Rectangle(placed['x'], placed['y'], *JETLINK_SIZE)
    rl.draw_rectangle_rounded(rect, 1.0, 16, rl.Color(15, 13, 23, round(235 * placed.get('opacity', 85) / 100)))
    rl.draw_circle_v(rl.Vector2(rect.x + 40, rect.y + 30), 7.5, card.dot)
    right = rect.x + rect.width - 32
    if card.latency_ms is not None:
      value, unit = f'{card.latency_ms:.1f}', 'ms'
      unit_w = self.fonts.measure(unit, FontRole.NORMAL, 18).width
      value_w = self.fonts.measure(value, FontRole.SEMI_BOLD, 24).width
      self.fonts.draw(unit, FontRole.NORMAL, 18, right - unit_w, rect.y + 18, MUTED)
      right -= unit_w + 6
      self.fonts.draw(value, FontRole.SEMI_BOLD, 24, right - value_w, rect.y + 13,
                      ORANGE if card.latency_ms >= SLOW_MS else TEXT)
      right -= value_w + 12
    tier_x = rect.x + 58
    if not card.detail:
      self.fonts.draw('DRIVING MODEL', FontRole.SEMI_BOLD, 20, tier_x, rect.y + 17, MUTED)
    else:
      tier = card.tier
      tier_w = self.fonts.measure(tier, FontRole.SEMI_BOLD, 20).width
      self.fonts.draw(tier, FontRole.SEMI_BOLD, 20, tier_x, rect.y + 17, MUTED)
      dot_x = tier_x + tier_w + 10
      rl.draw_circle_v(rl.Vector2(dot_x, rect.y + 30), 2.8, MUTED)
      detail_x = dot_x + 10
      detail = card.detail.upper()
      detail_size = self._fit(detail, FontRole.SEMI_BOLD, 20, max(40, right - detail_x))
      self.fonts.draw(detail, FontRole.SEMI_BOLD, detail_size, detail_x, rect.y + 17, MUTED)
    left = rect.x + 32
    width = rect.width - 64
    title_w = self.fonts.measure(card.title, FontRole.SEMI_BOLD, 28).width
    if title_w <= width:
      self._scroll = None
      self.fonts.draw(card.title, FontRole.SEMI_BOLD, 28, left, rect.y + 47, TEXT)
      return
    now = self.ticks()
    if self._scroll is None or self._scroll[0] != card.title:
      self._scroll = (card.title, now)
    offset = scroll_offset(title_w, width, now - self._scroll[1])
    clip.begin_scissor_mode(left, rect.y + 45, width, 44)
    try:
      for start in (left - offset, left - offset + title_w + SCROLL_GAP):
        if start < left + width:
          self.fonts.draw(card.title, FontRole.SEMI_BOLD, 28, start, rect.y + 47, TEXT)
    finally:
      clip.end_scissor_mode()
