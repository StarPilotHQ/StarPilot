"""Native, inert capture of the frozen large Device Settings viewport."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from openpilot.starpilot.ui.raylib import native_library_path, rl

from openpilot.starpilot.ui.device import DeviceView
from openpilot.starpilot.ui.device_state import DeviceState
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.settings import SettingsView
from openpilot.starpilot.ui.settings_state import Destination, SettingsState


def digest(path: Path) -> str:
  return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--font-directory", type=Path, required=True)
  parser.add_argument("--asset-directory", type=Path, required=True)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args()
  if args.output.exists():
    raise ValueError("Refusing to overwrite Device evidence")
  args.output.mkdir(parents=True)
  width, height = Profile.LARGE.size
  rl.set_config_flags(rl.ConfigFlags.FLAG_MSAA_4X_HINT | rl.ConfigFlags.FLAG_WINDOW_HIGHDPI | rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
  rl.init_window(960, 480, "Development preview — Device panel")
  if not rl.is_window_ready():
    raise RuntimeError("Unable to create native Device graphics context")
  target = rl.load_render_texture(width, height)
  try:
    if not target.id or not target.texture.id:
      raise RuntimeError("Unable to create Device framebuffer")
    with BitmapFonts(Profile.LARGE, args.font_directory) as fonts:
      settings = SettingsView(fonts, args.asset_directory)
      try:
        device = DeviceView(fonts)
        state = DeviceState()
        for _ in range(18):
          rl.begin_texture_mode(target)
          try:
            rl.clear_background(rl.BLACK)
            device.render(state)
            settings.render_rail(SettingsState(), selected=Destination.DEVICE)
          finally:
            rl.end_texture_mode()
        pixels = rl.load_image_from_texture(target.texture)
        try:
          if pixels.data is None:
            raise RuntimeError("Unable to read Device framebuffer")
          rl.image_format(pixels, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
          rl.image_flip_vertical(pixels)
          if not rl.export_image(pixels, str(args.output / "large.png")):
            raise RuntimeError("Unable to export Device framebuffer")
          rgba_sha = hashlib.sha256(bytes(pixels.data[:width * height * 4])).hexdigest()
        finally:
          if pixels.data is not None:
            rl.unload_image(pixels)
        module = Path(__file__).parent
        report = {
          "profile": Profile.LARGE,
          "dimensions": [width, height],
          "development_only": True,
          "state": asdict(state),
          "rgba_sha256": rgba_sha,
          "png_sha256": digest(args.output / "large.png"),
          "native_raylib_sha256": digest(Path(native_library_path)),
          "source_sha256": {name: digest(module / name) for name in (
            "device.py", "device_state.py", "preview_device.py", "settings.py", "settings_assets.py", "presentation.py",
            "settings-assets.json", "bitmap-fonts.json", "sora-brand-font.json", "dinish-speed-font.json")},
          "asset_sha256": {"icons/backspace.png": digest(args.asset_directory / "icons/backspace.png")},
          "font_sha256": {path.name: digest(path) for path in sorted(args.font_directory.glob("*"))
                          if path.name in {"Inter-Regular.fnt", "Inter-Regular.png", "Inter-Medium.fnt", "Inter-Medium.png",
                                           "Inter-Bold.fnt", "Inter-Bold.png", "Inter-SemiBold.fnt", "Inter-SemiBold.png",
                                           "unifont.fnt", "unifont.png"}} |
                         {name: digest(module / "assets/fonts" / name) for name in ("Sora-800.fnt", "Sora-800.png", "DINish-Speed.fnt", "DINish-Speed.png")},
          "scope": "large captured viewport only; no compact Device scene, scrolling, services, or action execution",
        }
        (args.output / "large.json").write_text(json.dumps(report, indent=2) + "\n")
      finally:
        settings.close()
  finally:
    if target.id:
      rl.unload_render_texture(target)
    rl.close_window()


if __name__ == "__main__":
  main()
