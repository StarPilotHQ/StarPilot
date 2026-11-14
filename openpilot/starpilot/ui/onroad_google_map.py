"""Google map tiles for the map widget: decoded on a worker thread, uploaded on the render thread."""

from __future__ import annotations

import io
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.navigation.google_tiles import (
  GoogleTileStore, choose_zoom, google_root, key_digest, plan_tiles, read_status, want_path, write_want,
)
from openpilot.starpilot.navigation.offline_roads import read_json
from openpilot.starpilot.navigation.road_tiles import TileKey

TEXTURE_CACHE = 16
TEXTURE_SPARE = 8            # textures kept past the wanted set, for a zoom step back
UPLOADS_PER_FRAME = 3
SETTINGS_EVERY_S = 5.0
STATUS_EVERY_S = 10.0
WANT_EVERY_S = 1.0
MISSING_RETRY_S = 1.0
ATTRIBUTION = "Google Maps"


def _settings_path() -> Path:
  from openpilot.starpilot.storage import starpilot_storage_root
  return starpilot_storage_root() / "navigation" / "settings.json"


def chosen_key(path: Path) -> str:
  """The Google key while Google is the chosen map style, else empty."""
  value = read_json(path, 256 * 1024)
  if not isinstance(value, dict) or value.get("mapProvider") != "google":
    return ""
  key = value.get("googleKey")
  return key if isinstance(key, str) else ""


def decode_png(data: bytes | None) -> tuple[int, int, bytes] | None:
  """RGBA pixels of a tile, or None for a missing or broken one."""
  if not data:
    return None
  from PIL import Image, UnidentifiedImageError
  try:
    with Image.open(io.BytesIO(data)) as image:
      if image.format != "PNG" or not 0 < image.width <= 1024 or not 0 < image.height <= 1024:
        return None
      rgba = image.convert("RGBA")
      return rgba.width, rgba.height, rgba.tobytes()
  except (UnidentifiedImageError, OSError, ValueError):
    return None


class GoogleRaster:
  def __init__(self, root: Path | None = None, settings: Path | None = None, want: Path | None = None,
               clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):  # noqa: TID251
    self.root = Path(root) if root is not None else google_root()
    self.store = GoogleTileStore(self.root)
    self.settings = settings or _settings_path()
    self.want = want or want_path()
    self.clock, self.wall = clock, wall
    self._lock = threading.Condition()
    self._digest: str | None = None
    self._zoom: int | None = None
    self._wanted: tuple[TileKey, ...] = ()
    self._center: tuple[float, float] | None = None
    self._decoded: dict[TileKey, tuple[int, int, bytes]] = {}
    self._expires: dict[TileKey, float] = {}
    self._missing: dict[TileKey, float] = {}
    self._reset = False
    self._textures: OrderedDict[TileKey, rl.Texture] = OrderedDict()
    self.generation = 0
    self.copyright = ""
    self._stopped = False
    self._thread = threading.Thread(target=self._run, name="google-tiles", daemon=True)
    self._thread.start()

  @property
  def active(self) -> bool:
    with self._lock:
      return self._digest is not None

  def plan(self, latitude: float, longitude: float, m_per_px: float, radius_m: float) -> tuple[TileKey, ...]:
    """The tiles a canvas around this point should draw; also what navtilesd is asked to fetch."""
    with self._lock:
      if self._digest is None:
        self._wanted, self._center = (), None
        return ()
      self._zoom = choose_zoom(latitude, m_per_px, self._zoom)
      self._wanted = tuple(plan_tiles(latitude, longitude, radius_m, self._zoom))
      self._center = (latitude, longitude)
      self._lock.notify()
      return self._wanted

  def upload(self, limit: int = UPLOADS_PER_FRAME) -> None:
    """Render thread: turn a few decoded tiles into textures; ``generation`` changes when there is something new to draw."""
    with self._lock:
      reset, self._reset = self._reset, False
      ready = list(self._decoded.items())[:limit]
      for key, _ in ready:
        del self._decoded[key]
      keep = max(TEXTURE_CACHE, len(self._wanted) + TEXTURE_SPARE)
    changed = False
    if reset and self._textures:
      for texture in self._textures.values():
        rl.unload_texture(texture)
      self._textures.clear()
      changed = True
    for key, (width, height, pixels) in ready:
      texture = rl.load_texture_from_image(rl.Image(None, width, height, 1, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8))
      if not texture.id:
        continue
      rl.update_texture(texture, pixels)
      rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
      rl.set_texture_wrap(texture, rl.TextureWrap.TEXTURE_WRAP_CLAMP)
      old = self._textures.pop(key, None)
      if old is not None:
        rl.unload_texture(old)
      self._textures[key] = texture
      changed = True
    evicted = []
    while len(self._textures) > keep:
      key, texture = self._textures.popitem(last=False)
      rl.unload_texture(texture)
      evicted.append(key)
    if evicted:
      with self._lock:
        for key in evicted:
          self._expires.pop(key, None)      # read from disk again if it is wanted later
    if changed:
      self.generation += 1

  def textures(self, keys: Sequence[TileKey]) -> dict[TileKey, rl.Texture]:
    found = {}
    for key in keys:
      texture = self._textures.get(key)
      if texture is not None:
        self._textures.move_to_end(key)
        found[key] = texture
    return found

  # ---- worker

  def _run(self) -> None:
    settings_at = status_at = want_at = -math.inf
    while True:
      with self._lock:
        if self._stopped:
          return
        self._lock.wait(timeout=0.25)
        if self._stopped:
          return
      now = self.clock()
      if now - settings_at >= SETTINGS_EVERY_S:
        settings_at = now
        key = chosen_key(self.settings)
        digest = key_digest(key) if key else None
        with self._lock:
          if digest != self._digest:
            self._digest, self._reset = digest, True
            self._decoded.clear()
            self._expires.clear()
            self._missing.clear()
            self._wanted, self._center = (), None
      with self._lock:
        digest, wanted, center = self._digest, self._wanted, self._center
      if digest is None:
        continue
      if now - status_at >= STATUS_EVERY_S:
        status_at = now
        text = read_status(self.root).get("copyright")
        self.copyright = text if isinstance(text, str) else ""
      if wanted and center is not None and now - want_at >= WANT_EVERY_S:
        want_at = now
        try:
          write_want(self.want, wanted, *center, now=self.wall())
        except OSError:
          pass
      self._load(digest, wanted)

  def _load(self, digest: str, wanted: tuple[TileKey, ...]) -> None:
    wall, now = self.wall(), self.clock()
    with self._lock:
      # An expired tile stays on screen until navtilesd's fresh copy replaces it.
      for key in [key for key, expires in self._expires.items() if expires <= wall]:
        del self._expires[key]
      todo = [key for key in wanted if key not in self._expires and key not in self._decoded and
              self._missing.get(key, -math.inf) <= now]
    for key in todo[:8]:
      image = decode_png(self.store.read(digest, key, wall))
      if image is None:
        with self._lock:
          self._missing[key] = now + MISSING_RETRY_S
        continue
      try:
        expires = self.store.path(digest, key).stat().st_mtime
      except OSError:
        expires = wall + 60.0
      with self._lock:
        if self._digest != digest:
          return
        self._missing.pop(key, None)
        self._decoded[key] = image
        self._expires[key] = expires

  def close(self) -> None:
    with self._lock:
      self._stopped = True
      self._lock.notify()
    self._thread.join(timeout=1.0)
    with self._lock:
      self._decoded.clear()
    if rl.is_window_ready():
      for texture in self._textures.values():
        rl.unload_texture(texture)
    self._textures.clear()
