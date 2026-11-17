"""Offline native captures for SLC confirmation and limit states."""

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.device_state import DeviceState
from openpilot.starpilot.ui.onroad_state import ObservationKind, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.preview_home import reference_state
from openpilot.starpilot.ui.preview_settings import reference_settings_state
from openpilot.starpilot.ui.preview_shell import reference_onroad
from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.shell import ShellMode, ShellSnapshot, ShellView
from openpilot.starpilot.ui.software_state import SoftwareState
from openpilot.starpilot.ui.toggles_state import TogglesState


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--profile", type=Profile, choices=list(Profile), required=True)
  parser.add_argument("--font-directory", type=Path, required=True)
  parser.add_argument("--asset-directory", type=Path, required=True)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args()
  if args.output.exists():
    raise ValueError("Refusing to overwrite SLC action evidence")
  args.output.mkdir(parents=True)
  width, height = args.profile.size
  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN | rl.ConfigFlags.FLAG_MSAA_4X_HINT | rl.ConfigFlags.FLAG_WINDOW_HIGHDPI)
  rl.init_window(min(width, 960), round(height * min(width, 960) / width), "Offline SLC action capture")
  target = rl.load_render_texture(width, height)
  try:
    with BitmapFonts(args.profile, args.font_directory) as fonts:
      view = ShellView(fonts, args.asset_directory)
      try:
        scenes = {}
        for name, pending in (("notice", True), ("notice_peak", True), ("pending", True), ("automatic", False), ("adoptable", False), ("override", False)):
          observation = SpeedLimitObservation(kind=ObservationKind.VALID, source="dashboard", speed_limit_mps=22.0,
                                              accepted_speed_limit_mps=22.0 if name == 'automatic' else 18.0, accepted_source="dashboard",
                                              accepted_adjusted_limit_mps=24.0 if name == 'automatic' else 20.0,
                                              effective_cluster_target_mps=24.0 if name == 'automatic' else 20.0,
                                              pending_adjusted_limit_mps=24.0 if pending else None,
                                              presentation_adjusted_limit_mps=24.0, limiting_max_set=True,
                                              pending_source="dashboard" if pending else "none",
                                              pending_speed_limit_mps=22.0 if pending else None,
                                              session_id="preview-drive", decision_id=7 if pending else 0,
                                              presentation_id=9, action_enabled=True,
                                              pending_since_ns=10_000_000_000 if pending else 0,
                                              driver_override_active=name == "override")
          onroad = replace(reference_onroad("onroad_engaged_no_camera"), speed_limit=observation,
                           longitudinal_active=True, slc_system_long_available=True, slc_wheel_confirmation_available=True, slc_prompt_open=name == "pending",
                           observed_ns=10_000_000_000 if name == "notice" else 10_350_000_000)
          if name == 'automatic':
            onroad = replace(onroad, cruise_kph=24. * 3.6)
          snapshot = ShellSnapshot(ShellMode.ONROAD, reference_state(), reference_settings_state(), onroad,
                                   DeviceState(), SoftwareState(), TogglesState(), Destination.STAR)
          for _ in range(18):
            rl.begin_texture_mode(target)
            rl.clear_background(rl.BLACK)
            view.render(snapshot)
            rl.end_texture_mode()
          image = rl.load_image_from_texture(target.texture)
          try:
            rl.image_flip_vertical(image)
            path = args.output / f"slc_{name}.png"
            if not rl.export_image(image, str(path)):
              raise RuntimeError("Unable to export SLC action capture")
            scenes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
          finally:
            rl.unload_image(image)
        (args.output / "results.json").write_text(json.dumps({"profile": args.profile.value, "scenes": scenes}, indent=2) + "\n")
      finally:
        view.close()
  finally:
    rl.unload_render_texture(target)
    rl.close_window()


if __name__ == "__main__":
  main()
