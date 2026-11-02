"""Explicit desktop Home preview; supplied fixture state, no device service access."""

import argparse
import hashlib
import importlib.metadata
import json
import time
from dataclasses import asdict
from pathlib import Path

from openpilot.starpilot.ui.raylib import rl
import sys

from openpilot.starpilot.ui.home import HomeView
from openpilot.starpilot.ui.home_state import DailyDistance, DriveStatsData, DriveSummary, HomeMode, HomeState, PersonalRecord
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile


def reference_state() -> HomeState:
  """Fixed presentation inputs, intentionally independent of the local installation."""
  summary = DriveSummary()
  stats = DriveStatsData(summary, summary, summary,
                        tuple(DailyDistance(label, is_today=index == 1, is_future=index > 1) for index, label in enumerate("MTWTFSS")),
                        (PersonalRecord("Longest drive", "0.0 mi", "No drives"),
                         PersonalRecord("Most engaged day", "0%", "No drives"),
                         PersonalRecord("Best week", "0.0 mi", "No drives"),
                         PersonalRecord("Highest streak", "0 days", "No drives"),
                         PersonalRecord("Longest undistracted drive", "0 min", "No clean drives"),
                         PersonalRecord("Clean-drive streak", "0 drives", "No clean drives")))
  return HomeState(version="6.7.7", commit="678af78347d9656bc2f5dacd4204b012db861fa7", commit_date="Sep 15",
                   model_label="Baseline model", description="", mode=HomeMode.CONDITIONAL_EXPERIMENTAL,
                   experimental_enabled=False, experimental_available=True, stats=stats, temperature_c=0)


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--profile", choices=list(Profile), required=True)
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument("--font-directory", type=Path, required=True)
  parser.add_argument("--asset-directory", type=Path, required=True)
  args = parser.parse_args()
  profile = Profile(args.profile)
  state = reference_state()
  args.output.mkdir(parents=True, exist_ok=True)
  width, height = profile.size
  context_size = min(width, 960), min(height, 540)
  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN | rl.ConfigFlags.FLAG_MSAA_4X_HINT | rl.ConfigFlags.FLAG_WINDOW_HIGHDPI)
  rl.init_window(*context_size, "Home preview")
  if not rl.is_window_ready():
    raise RuntimeError("Unable to create native graphics context")
  target = rl.load_render_texture(width, height)
  try:
    with BitmapFonts(profile, args.font_directory) as fonts:
      view = HomeView(fonts, args.asset_directory)
      try:
        timings = []
        for index in range(18):
          start = time.perf_counter()
          rl.begin_texture_mode(target)
          rl.clear_background(rl.BLACK)
          view.render(state)
          rl.end_texture_mode()
          pixels = rl.load_image_from_texture(target.texture)
          try:
            if index >= 6:
              timings.append((time.perf_counter() - start) * 1000)
            if index == 17:
              rl.image_format(pixels, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
              rl.image_flip_vertical(pixels)
              if not rl.export_image(pixels, str(args.output / f"{profile}.png")):
                raise RuntimeError("Unable to export native framebuffer")
              pixel_hash = hashlib.sha256(bytes(pixels.data[:width * height * 4])).hexdigest()
          finally:
            rl.unload_image(pixels)
        report = {"profile": profile, "dimensions": [width, height], "context_dimensions": list(context_size),
                  "texture_pixel_scale": list(view._pixel_scale),
                  "native_library": {"version": importlib.metadata.version("comma-deps-raylib"),
                                     "sha256": hashlib.sha256(Path(sys.modules[rl.Color.__module__].__file__).read_bytes()).hexdigest()},
                  "rgba_sha256": pixel_hash, "state": asdict(state), "submission_readback_ms": timings,
                  "scope": "paired records Home on native desktop only; device performance and full UI not qualified"}
        (args.output / f"{profile}.json").write_text(json.dumps(report, indent=2) + "\n")
      finally:
        view.close()
  finally:
    rl.unload_render_texture(target)
    rl.close_window()


if __name__ == "__main__":
  main()
