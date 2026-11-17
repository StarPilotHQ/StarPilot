"""Explicit desktop typography preview; no manager, Params or device service access."""

import argparse
import hashlib
import json
from pathlib import Path

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile


def samples(profile: Profile) -> list[dict]:
  if profile == Profile.LARGE:
    return [
      {"text": "StarPilot", "role": "brand", "size": 96, "x": 40, "y": 25},
      {"text": "StarPilot - Baseline model", "role": "normal", "size": 48, "x": 40, "y": 180},
      {"text": "CONDITIONAL EXPERIMENTAL", "role": "normal", "size": 45, "x": 40, "y": 290},
      {"text": "ALL TIME     PAST WEEK     PERSONAL RECORDS", "role": "semi_bold", "size": 40, "x": 40, "y": 390},
      {"text": "TAKE CONTROL IMMEDIATELY", "role": "bold", "size": 76, "x": 40, "y": 500},
      {"text": "45 mph   120 kilometers   0.0 miles", "role": "medium", "size": 58, "x": 40, "y": 650},
      {"text": "ABC xyz 0123456789 – ° ✓", "role": "roman", "size": 44, "x": 40, "y": 790},
      {"text": "ABC xyz 0123456789", "role": "fallback", "size": 36, "x": 40, "y": 900},
    ]
  return [
    {"text": "StarPilot", "role": "brand", "size": 96, "x": 6, "y": -16},
    {"text": "6.7.7  Sep 15  Baseline model", "role": "roman", "size": 36, "x": 6, "y": 96},
    {"text": "678af78", "role": "normal", "size": 36, "x": 6, "y": 138},
    {"text": "45 mph   50 MAX", "role": "bold", "size": 32, "x": 6, "y": 185},
  ]


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--profile", choices=list(Profile), required=True)
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument("--font-directory", type=Path, required=True)
  args = parser.parse_args()
  profile = Profile(args.profile)
  args.output.mkdir(parents=True, exist_ok=True)
  width, height = profile.size
  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN | rl.ConfigFlags.FLAG_MSAA_4X_HINT)
  # The framebuffer is independent of the desktop window; a small hidden context
  # also works when the logical device resolution exceeds the desktop monitor.
  context_size = min(width, 960), min(height, 540)
  rl.init_window(*context_size, "Typography preview")
  if not rl.is_window_ready():
    raise RuntimeError("Unable to create native graphics context")
  texture = rl.load_render_texture(width, height)
  try:
    with BitmapFonts(profile, args.font_directory) as fonts:
      measured = []
      rl.begin_texture_mode(texture)
      rl.clear_background(rl.BLACK)
      for sample in samples(profile):
        size = fonts.measure(sample["text"], FontRole(sample["role"]), sample["size"])
        measured.append({**sample, "width": size.width, "height": size.height})
        fonts.draw(sample["text"], FontRole(sample["role"]), sample["size"], sample["x"], sample["y"])
      rl.end_texture_mode()
      pixels = rl.load_image_from_texture(texture.texture)
      try:
        rl.image_format(pixels, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
        rl.image_flip_vertical(pixels)
        image_path = args.output / f"{profile}.png"
        if not rl.export_image(pixels, str(image_path)):
          raise RuntimeError("Unable to export native framebuffer")
        pixel_hash = hashlib.sha256(bytes(pixels.data[:width * height * 4])).hexdigest()
      finally:
        rl.unload_image(pixels)
      report = {"profile": profile, "dimensions": [width, height], "context_dimensions": list(context_size), "rgba_sha256": pixel_hash,
                "measurements": measured, "scope": "native desktop typography only; device rendering and full UI not qualified"}
      (args.output / f"{profile}.json").write_text(json.dumps(report, indent=2) + "\n")
  finally:
    rl.unload_render_texture(texture)
    rl.close_window()


if __name__ == "__main__":
  main()
