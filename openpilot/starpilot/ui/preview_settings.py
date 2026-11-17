"""Development-only Settings capture and Home navigation preview.

Other leaf panels and compact scrolling are unavailable. This entry point never starts
device services or changes settings. Development controls sit outside the device
canvas; exported framebuffers retain only the protected presentation.
"""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from openpilot.starpilot.ui.raylib import native_library_path, rl

from openpilot.starpilot.ui.device import DeviceView
from openpilot.starpilot.ui.device_state import DeviceInput
from openpilot.starpilot.ui.home import HomeView
from openpilot.starpilot.ui.home_state import HomeInput
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.preview_home import reference_state
from openpilot.starpilot.ui.settings import SettingsView
from openpilot.starpilot.ui.settings_state import Destination, PreviewRouter, SettingsAction, SettingsActionKind, SettingsInput, SettingsState
from openpilot.starpilot.ui.software import SoftwareView
from openpilot.starpilot.ui.software_state import SoftwareInput
from openpilot.starpilot.ui.toggles import TogglesView
from openpilot.starpilot.ui.toggles_state import TogglesInput


def reference_settings_state() -> SettingsState:
  # An explicit captured entry position, not an implementation of animation.
  return SettingsState(compact_y=-17.667003631591797, nav_bar_y=4.752651691436768, nav_bar_alpha=0.062367421150705246)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--profile", choices=list(Profile), required=True)
  parser.add_argument("--font-directory", type=Path, required=True)
  parser.add_argument("--asset-directory", type=Path, required=True)
  parser.add_argument("--output", type=Path, required=True)
  parser.add_argument("--snapshot", choices=("reference", "settled"), default="reference")
  parser.add_argument("--interactive", action="store_true", help="Show development chrome, Home/Settings/large leaf routing and inert requests")
  args = parser.parse_args()
  if args.output.exists():
    raise ValueError("Refusing to overwrite existing Settings evidence")
  args.output.mkdir(parents=True)
  profile = Profile(args.profile)
  width, height = profile.size
  canvas_width = min(width, 960)
  canvas_height = round(height * canvas_width / width)
  flags = rl.ConfigFlags.FLAG_MSAA_4X_HINT | rl.ConfigFlags.FLAG_WINDOW_HIGHDPI
  if not args.interactive:
    flags |= rl.ConfigFlags.FLAG_WINDOW_HIDDEN
  rl.set_config_flags(flags)
  rl.init_window(canvas_width, canvas_height + (80 if args.interactive else 0), "Development preview — Settings entry only")
  if not rl.is_window_ready():
    raise RuntimeError("Unable to create native Settings graphics context")
  target = rl.load_render_texture(width, height)
  try:
    if not target.id or not target.texture.id:
      raise RuntimeError("Unable to create Settings framebuffer")
    with BitmapFonts(profile, args.font_directory) as fonts:
      settings = SettingsView(fonts, args.asset_directory)
      home = HomeView(fonts, args.asset_directory)
      try:
        settings.prepare()
        state = reference_settings_state() if args.snapshot == "reference" else SettingsState()
        timings = []
        for frame in range(18):
          begin = time.perf_counter()
          rl.begin_texture_mode(target)
          rl.clear_background(rl.BLACK)
          settings.render(state)
          rl.end_texture_mode()
          pixels = rl.load_image_from_texture(target.texture)
          try:
            if pixels.data is None:
              raise RuntimeError("Unable to read Settings framebuffer")
            if frame >= 6:
              timings.append((time.perf_counter() - begin) * 1000)
            if frame == 17:
              rl.image_format(pixels, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
              rl.image_flip_vertical(pixels)
              if not rl.export_image(pixels, str(args.output / f"{profile}.png")):
                raise RuntimeError("Unable to export Settings framebuffer")
              rgba_hash = hashlib.sha256(bytes(pixels.data[:width * height * 4])).hexdigest()
          finally:
            if pixels.data is not None:
              rl.unload_image(pixels)
        report = {"profile": profile, "dimensions": [width, height], "snapshot": args.snapshot, "development_only": True,
                  "state": asdict(state), "rgba_sha256": rgba_hash, "submission_readback_ms": timings,
                  "native_raylib_sha256": hashlib.sha256(Path(native_library_path).read_bytes()).hexdigest(),
                  "scope": "static entry presentation; no compact motion, leaf panels, device services or performance qualification"}
        (args.output / f"{profile}.json").write_text(json.dumps(report, indent=2) + "\n")
        if args.interactive:
          _interactive(profile, settings, home, target, canvas_width, canvas_height)
      finally:
        home.close()
        settings.close()
  finally:
    if target.id:
      rl.unload_render_texture(target)
    rl.close_window()


class SettingsPreviewInput:
  """Dispatch the interactive preview's actual Home, rail and leaf input."""

  def __init__(self, profile: Profile, router: PreviewRouter):
    self.router = router
    self.home_state = reference_state()
    self.settings_input = SettingsInput(profile, self._route_settings, lambda: router.selected)
    self.device_input = DeviceInput(router.device_action)
    self.software_input = SoftwareInput(router.software_action)
    self.toggles_input = TogglesInput(router.toggle_action)
    self.home_input = HomeInput(profile, router.home_action)

  def _route_settings(self, action: SettingsAction) -> None:
    self.settings_input.cancel()
    self.device_input.cancel()
    self.software_input.cancel()
    self.toggles_input.cancel()
    self.router.settings_action(action)

  def step(self, x: float, y: float, now: float, *, pressed: bool = False, down: bool = False,
           released: bool = False, back_pressed: bool = False, footer_pressed: bool = False) -> None:
    router = self.router
    if back_pressed or footer_pressed:
      self._route_settings(SettingsAction(SettingsActionKind.CLOSE))
      self.home_input.cancel()
    elif router.in_settings:
      if pressed:
        self.settings_input.press(x, y, router.settings)
        if router.selected == Destination.DEVICE:
          self.device_input.press(x, y, router.device)
        elif router.selected == Destination.SOFTWARE:
          self.software_input.press(x, y, router.software)
        elif router.selected == Destination.TOGGLES:
          self.toggles_input.press(x, y, router.toggles)
      if down:
        self.settings_input.move(x, y, router.settings)
        if router.selected == Destination.DEVICE:
          self.device_input.move(x, y, router.device)
        elif router.selected == Destination.SOFTWARE:
          self.software_input.move(x, y, router.software)
        elif router.selected == Destination.TOGGLES:
          self.toggles_input.move(x, y, router.toggles)
      if released:
        selected_at_release = router.selected
        self.settings_input.release(x, y, router.settings)
        if router.in_settings and router.selected == selected_at_release:
          if selected_at_release == Destination.DEVICE:
            self.device_input.release(x, y, router.device)
          elif selected_at_release == Destination.SOFTWARE:
            self.software_input.release(x, y, router.software)
          elif selected_at_release == Destination.TOGGLES:
            self.toggles_input.release(x, y, router.toggles)
    else:
      if pressed:
        self.home_input.press(x, y, now)
      if down:
        self.home_input.move(x, y, now)
        self.home_input.tick(now, self.home_state)
      if released:
        self.home_input.release(x, y, now, self.home_state)


def _interactive(profile, settings, home, target, canvas_width, canvas_height):
  router = PreviewRouter(profile)
  inputs = SettingsPreviewInput(profile, router)
  device = DeviceView(settings.fonts) if profile == Profile.LARGE else None
  software = SoftwareView(settings.fonts) if profile == Profile.LARGE else None
  toggles = TogglesView(settings.fonts, settings.assets.directory) if profile == Profile.LARGE else None
  rl.set_exit_key(0)
  rl.set_target_fps(60)
  print(router.notice, flush=True)
  while not rl.window_should_close():
    position = rl.get_mouse_position()
    x, y = position.x * profile.size[0] / canvas_width, position.y * profile.size[1] / canvas_height
    now = time.monotonic()
    # Desktop development sampler only; this is not device Widget event routing.
    pressed = rl.is_mouse_button_pressed(rl.MouseButton.MOUSE_BUTTON_LEFT)  # noqa: TID251
    released = rl.is_mouse_button_released(rl.MouseButton.MOUSE_BUTTON_LEFT)  # noqa: TID251
    back_pressed = rl.is_key_pressed(rl.KeyboardKey.KEY_ESCAPE) or rl.is_key_pressed(rl.KeyboardKey.KEY_BACKSPACE)
    inputs.step(x, y, now, pressed=pressed, down=rl.is_mouse_button_down(rl.MouseButton.MOUSE_BUTTON_LEFT),
                released=released, back_pressed=back_pressed, footer_pressed=pressed and position.y >= canvas_height)
    rl.begin_texture_mode(target)
    rl.clear_background(rl.BLACK)
    if router.in_settings:
      if router.selected == Destination.DEVICE and device is not None:
        device.render(router.device)
        settings.render_rail(router.settings, selected=Destination.DEVICE)
      elif router.selected == Destination.SOFTWARE and software is not None:
        software.render(router.software)
        settings.render_rail(router.settings, selected=Destination.SOFTWARE)
      elif router.selected == Destination.TOGGLES and toggles is not None:
        toggles.render(router.toggles)
        settings.render_rail(router.settings, selected=Destination.TOGGLES)
      else:
        settings.render(router.settings)
    else:
      home.render(inputs.home_state)
    rl.end_texture_mode()
    rl.begin_drawing()
    rl.clear_background(rl.Color(24, 24, 28, 255))
    rl.draw_texture_pro(target.texture, rl.Rectangle(0, 0, profile.size[0], -profile.size[1]),
                        rl.Rectangle(0, 0, canvas_width, canvas_height), rl.Vector2(0, 0), 0, rl.WHITE)
    font = rl.get_font_default()
    rl.draw_text_ex(font, "DEVELOPMENT PREVIEW - click footer / Esc to go back", rl.Vector2(8, canvas_height + 8), 16, 1, rl.YELLOW)
    rl.draw_text_ex(font, router.notice, rl.Vector2(8, canvas_height + 34), 14, 1, rl.WHITE)
    rl.draw_text_ex(font, "Large Device, Toggles and Software requests are inert. Other leaves unavailable; compact cards are static.",
                    rl.Vector2(8, canvas_height + 58), 12, 1, rl.GRAY)
    rl.end_drawing()
  if toggles is not None:
    toggles.close()


if __name__ == "__main__":
  main()
