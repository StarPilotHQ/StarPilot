from openpilot.starpilot.ui.raylib import rl
from openpilot.system.ui.lib.application import FONT_SCALE, font_fallback
from openpilot.system.ui.lib.lru_cache import LRUCache

_CACHE_MAXSIZE = 1024
_cache: LRUCache[int, rl.Vector2] = LRUCache(_CACHE_MAXSIZE)


def measure_text_cached(font: rl.Font, text: str, font_size: int, spacing: float = 0) -> rl.Vector2:
  """Caches text measurements to avoid redundant calculations."""
  font = font_fallback(font)
  spacing = round(spacing, 4)
  key = hash((font.texture.id, text, font_size, spacing))
  cached = _cache.get(key)
  if cached is not None:
    return cached

  result = rl.measure_text_ex(font, text, font_size * FONT_SCALE, spacing)  # noqa: TID251

  _cache[key] = result
  return result
