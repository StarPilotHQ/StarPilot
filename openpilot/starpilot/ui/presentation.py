"""Bitmap typography for the two device layouts, independent of application state."""

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
import hashlib
import json

import numpy as np
from PIL import Image
from openpilot.starpilot.ui.raylib import rl

from openpilot.system.ui.lib.multilang import TRANSLATIONS_DIR, multilang, tr_display as tr
from openpilot.system.ui.lib.display_translation import native_text


class Profile(StrEnum):
  LARGE = "large"
  COMPACT = "compact"

  @property
  def size(self) -> tuple[int, int]:
    return (2160, 1080) if self == Profile.LARGE else (536, 240)

  @property
  def font_scale(self) -> float:
    return 1.242 if self == Profile.LARGE else 1.16


class FontRole(StrEnum):
  NORMAL = "normal"
  MEDIUM = "medium"
  BOLD = "bold"
  SEMI_BOLD = "semi_bold"
  BRAND = "brand"
  ROMAN = "roman"
  DISPLAY = "display"
  SPEED = "speed"
  SET_SPEED = "set_speed"
  FALLBACK = "fallback"


@dataclass(frozen=True)
class TextSize:
  width: float
  height: float


@lru_cache(maxsize=32)
def font_filename(profile: Profile, role: FontRole) -> str:
  if role == FontRole.NORMAL:
    return "Inter-Regular.fnt" if profile == Profile.LARGE else "Inter-Medium.fnt"
  return {
    FontRole.MEDIUM: "Inter-Medium.fnt",
    FontRole.BOLD: "Inter-Bold.fnt",
    FontRole.SEMI_BOLD: "Inter-SemiBold.fnt",
    FontRole.BRAND: "Sora-800.fnt",
    FontRole.ROMAN: "Inter-Regular.fnt",
    FontRole.DISPLAY: "Inter-Bold.fnt",
    FontRole.SET_SPEED: "DINish-Speed.fnt",
    FontRole.SPEED: "DINish-Speed.fnt" if profile == Profile.LARGE else "Inter-Bold.fnt",
    FontRole.FALLBACK: "unifont.fnt",
  }[role]


def default_font_directory() -> Path:
  return Path(__file__).parent / "assets/fonts"


def font_path(directory: Path, filename: str) -> Path:
  # Keep the supplied Sora wordmark pinned to its bundled asset. The other
  # fonts use the bundled directory by default or a validated caller override.
  return default_font_directory() / filename if filename in ("Sora-800.fnt", "DINish-Speed.fnt") else directory / filename


def validate_bitmap_font(path: Path) -> None:
  """Accept only reviewed descriptor/atlas bytes before the native BMFont parser.

  This manifest pins appearance as well as parser inputs. Changing either file
  requires an explicit asset review; accepting a partial descriptor grammar is
  insufficient to protect the native parser from malformed input.
  """
  manifest_name = {"Sora-800.fnt": "sora-brand-font.json", "DINish-Speed.fnt": "dinish-speed-font.json"}.get(path.name, "bitmap-fonts.json")
  manifest = json.loads(Path(__file__).with_name(manifest_name).read_text())
  expected = {entry["file"]: entry for entry in manifest["files"]}
  try:
    if path.suffix != ".fnt" or path.name not in expected:
      raise ValueError("descriptor is not in the reviewed font manifest")
    for asset in (path, path.with_suffix(".png")):
      record = expected[asset.name]
      with asset.open("rb") as stream:
        data = stream.read(record["bytes"] + 1)
      if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError(f"{asset.name} differs from the reviewed font manifest")
  except (OSError, KeyError, ValueError) as error:
    raise ValueError(f"Invalid bitmap font {path.name}: {error}") from error


class BitmapFonts:
  """Own font textures without changing global text APIs or loading device services.

  Construct after creating a native graphics context and close before destroying
  it. A caller chooses the fallback explicitly; the brand always retains its own
  glyphs. Raw draw calls avoid a second scale if an application installed its own
  text wrapper. Text containing emoji is outside this component's contract.
  """

  def __init__(self, profile: Profile, directory: Path, *, headless_context: bool = False):
    if not (rl.is_window_ready() or headless_context):
      raise RuntimeError("Bitmap fonts require an initialized graphics context")
    self._headless_context = headless_context
    self.profile = Profile(profile)
    self._directory = directory
    self._fonts: dict[str, rl.Font] = {}
    self._language_fonts: dict[str, rl.Font] = {}
    self._measurements: dict[tuple, TextSize] = {}
    self._vertical_ink: dict[tuple, tuple[float, float]] = {}
    self._horizontal_ink: dict[tuple, tuple[float, float]] = {}
    self._glyph_ink: dict[str, dict[int, tuple[int, int] | None]] = {}
    default_texture_id = rl.get_font_default().texture.id
    try:
      for filename in dict.fromkeys(font_filename(self.profile, role) for role in FontRole):
        path = font_path(directory, filename)
        if not path.is_file():
          raise FileNotFoundError(path)
        validate_bitmap_font(path)
        font = rl.load_font(str(path))
        # Failed font/atlas loads may return Raylib's shared default resource.
        # Never accept it as a requested bitmap or take ownership of its texture.
        if font.texture.id in (0, default_texture_id) or font.glyphCount == 0:
          raise RuntimeError(f"Unable to load bitmap font {filename}")
        self._fonts[filename] = font
        if filename != "unifont.fnt":
          rl.gen_texture_mipmaps(font.texture)
          rl.set_texture_filter(font.texture, rl.TextureFilter.TEXTURE_FILTER_TRILINEAR)
    except Exception:
      self.close()
      raise

  def font(self, role: FontRole, *, fallback: bool = False) -> rl.Font:
    if not self._fonts:
      raise RuntimeError("Bitmap font resources are closed")
    if fallback and role != FontRole.BRAND:
      role = FontRole.FALLBACK
    return self._fonts[font_filename(self.profile, role)]

  def _text_font(self, text: str, role: FontRole, fallback: bool = False) -> rl.Font:
    if text.isascii() or role == FontRole.BRAND:
      return self.font(role, fallback=fallback)
    language = multilang.language
    fonts = self._language_fonts
    if language not in fonts:
      filename = {"ja": "NotoSansCJKjp-Regular.otf", "ko": "NotoSansCJKkr-Regular.otf",
                  "zh-CHS": "NotoSansCJKsc-Regular.otf", "zh-CHT": "NotoSansCJKtc-Regular.otf",
                  "th": "NotoSansThai-Regular.ttf"}.get(language, "Inter-Regular.ttf")
      directory = Path(__file__).parents[2] / "selfdrive/assets/fonts"
      chars = set(map(chr, range(32, 127))) | set(text)
      chars.update(TRANSLATIONS_DIR.joinpath(f"app_{language}.po").read_text(encoding="utf-8"))
      points = sorted(map(ord, chars))
      glyphs = np.array(points, dtype=np.int32)
      font = rl.load_font_ex(str(directory / filename), 64, glyphs, len(points))
      if font.texture.id in (0, rl.get_font_default().texture.id) or font.glyphCount == 0:
        raise RuntimeError(f"Unable to load language font {filename}")
      for previous in fonts.values():
        rl.unload_font(previous)
      fonts.clear()
      self._measurements.clear()
      self._vertical_ink.clear()
      self._horizontal_ink.clear()
      fonts[language] = font
      rl.gen_texture_mipmaps(font.texture)
      rl.set_texture_filter(font.texture, rl.TextureFilter.TEXTURE_FILTER_TRILINEAR)
    return fonts[language]

  def _optical_style(self, role: FontRole, fallback: bool = False) -> tuple[float, float]:
    if fallback:
      return 1., 0.
    if self.profile == Profile.LARGE and role == FontRole.SPEED:
      return 1.04, .85
    if self.profile == Profile.COMPACT and role == FontRole.SET_SPEED:
      return 1., .65
    return 1., 0.

  def measure(self, text: str, role: FontRole, size: float, *, spacing: float = 0, fallback: bool = False) -> TextSize:
    text = native_text(tr(text)) if role != FontRole.BRAND else text
    font = self._text_font(text, role, fallback)
    spacing = round(spacing, 4)
    width_scale, weight = self._optical_style(role, fallback)
    key = font.texture.id, text, size, spacing, width_scale, weight
    if key not in self._measurements:
      measured = rl.measure_text_ex(font, text, size * self.profile.font_scale, spacing)  # noqa: TID251
      if len(self._measurements) >= 1024:
        self._measurements.clear()
      self._measurements[key] = TextSize(measured.x * width_scale + (weight if text else 0), measured.y)
    return self._measurements[key]

  def vertical_ink(self, text: str, role: FontRole, size: float) -> tuple[float, float]:
    """Visible glyph top/bottom, independent of the font's padded line box."""
    text = native_text(tr(text)) if role != FontRole.BRAND else text
    key = (text, role, size)
    if key not in self._vertical_ink:
      font = self._text_font(text, role)
      glyphs = [rl.get_glyph_index(font, ord(char)) for char in text if not char.isspace()]
      scale = size * self.profile.font_scale / font.baseSize
      top = min((font.glyphs[index].offsetY for index in glyphs), default=0)
      bottom = max((font.glyphs[index].offsetY + font.recs[index].height for index in glyphs), default=0)
      if len(self._vertical_ink) >= 1024:
        self._vertical_ink.clear()
      self._vertical_ink[key] = (top * scale, bottom * scale)
    return self._vertical_ink[key]

  def horizontal_ink(self, text: str, role: FontRole, size: float) -> tuple[float, float]:
    """Visible glyph left/right from the draw x, without the font's side bearings."""
    text = native_text(tr(text)) if role != FontRole.BRAND else text
    key = (text, role, size)
    if key not in self._horizontal_ink:
      font = self._text_font(text, role)
      width_scale, weight = self._optical_style(role)
      scale = size * self.profile.font_scale / font.baseSize
      left = right = None
      x = 0.
      columns = self._glyph_columns(role) if text.isascii() or role == FontRole.BRAND else {}
      for char in text:
        index = rl.get_glyph_index(font, ord(char))
        glyph, rec = font.glyphs[index], font.recs[index]
        # Fixed-width fonts (DINish-Speed) pad each glyph cell, so use the atlas's inked columns.
        ink = None if char.isspace() else columns.get(index, (0, rec.width - 1) if rec.width > 0 else None)
        if ink is not None:
          left = x + glyph.offsetX + ink[0] if left is None else min(left, x + glyph.offsetX + ink[0])
          right = x + glyph.offsetX + ink[1] + 1 if right is None else max(right, x + glyph.offsetX + ink[1] + 1)
        x += glyph.advanceX or rec.width
      if left is None:
        left = right = 0.
      if len(self._horizontal_ink) >= 1024:
        self._horizontal_ink.clear()
      # Match draw(): the optical width scale applies to the whole run and the weight pass shifts right.
      self._horizontal_ink[key] = (left * scale * width_scale, right * scale * width_scale + (weight if text else 0))
    return self._horizontal_ink[key]

  def _glyph_columns(self, role: FontRole) -> dict[int, tuple[int, int] | None]:
    """Measure the reviewed PNG, without GPU readback during a widget render.

    Texture readback depends on the graphics backend and can disturb the active
    framebuffer used for Galaxy's widget previews. The source atlas has the same
    pixels and works independently of the current render target.
    """
    filename = font_filename(self.profile, role)
    if filename not in self._glyph_ink:
      font = self.font(role)
      with Image.open(font_path(self._directory, filename).with_suffix('.png')) as atlas:
        alpha = np.asarray(atlas.convert('RGBA').getchannel('A'))
      columns = {}
      for index in range(font.glyphCount):
        rec = font.recs[index]
        cell = alpha[int(rec.y):int(rec.y + rec.height), int(rec.x):int(rec.x + rec.width)]
        inked = np.flatnonzero(cell.max(0) > 40) if cell.size else []
        columns[index] = (int(inked[0]), int(inked[-1])) if len(inked) else None
      self._glyph_ink[filename] = columns
    return self._glyph_ink[filename]

  def draw(self, text: str, role: FontRole, size: float, x: float, y: float, color: rl.Color = rl.WHITE,
           *, spacing: float = 0, fallback: bool = False) -> None:
    text = native_text(tr(text)) if role != FontRole.BRAND else text
    font = self._text_font(text, role, fallback)
    draw = getattr(rl, "_orig_draw_text_ex", rl.draw_text_ex)
    width_scale, weight = self._optical_style(role, fallback)
    if width_scale == 1. and weight == 0.:
      draw(font, text, rl.Vector2(x, y), size * self.profile.font_scale, spacing, color)
      return
    rl.rl_push_matrix()
    try:
      rl.rl_translatef(x, y, 0)
      rl.rl_scalef(width_scale, 1., 1.)
      draw(font, text, rl.Vector2(0, 0), size * self.profile.font_scale, spacing, color)
      if weight:
        draw(font, text, rl.Vector2(weight / width_scale, 0),
             size * self.profile.font_scale, spacing, color)
    finally:
      rl.rl_pop_matrix()

  def close(self) -> None:
    if rl.is_window_ready() or self._headless_context:
      for font in (*self._fonts.values(), *self._language_fonts.values()):
        rl.unload_font(font)
    self._fonts.clear()
    self._language_fonts.clear()
    self._vertical_ink.clear()
    self._horizontal_ink.clear()
    self._glyph_ink.clear()
    self._measurements.clear()

  def __enter__(self):
    return self

  def __exit__(self, *_):
    self.close()
