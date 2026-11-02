"""Capture the complete two-profile offline UI shell with supplied scene data.

This desktop development preview never constructs a device service, Params,
camera IPC client, updater, installer or network manager. Images are evidence
under an explicit output directory and are not runtime UI assets.
"""

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from openpilot.starpilot.ui.raylib import rl

from openpilot.starpilot.ui.device_state import DeviceState
from openpilot.starpilot.ui.display_preferences import MASTER, SCREEN_OFF, SCREEN_OFF_DESCRIPTION
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsState, feature_parent_title
from openpilot.starpilot.ui.onroad_state import AlertSize, OnroadAlert, OnroadState, SpeedLimitObservation
from openpilot.starpilot.ui.presentation import BitmapFonts, Profile
from openpilot.starpilot.ui.preview_home import reference_state
from openpilot.starpilot.ui.preview_settings import reference_settings_state
from openpilot.starpilot.ui.settings_state import Destination
from openpilot.starpilot.ui.maps_state import map_page
from openpilot.starpilot.ui.software_state import SoftwareState
from openpilot.starpilot.ui.toggles_state import TogglesState
from openpilot.starpilot.ui.shell import ShellMode, ShellSnapshot, ShellView


FEATURE_SCENES = ("settings_driving_controls", "settings_sounds", "settings_appearance", "settings_system",
                  "settings_system_maps", "settings_system_collapsed",
                  "settings_system_offroad", "settings_system_onroad", "settings_system_unavailable",
                  "settings_system_display_off", "settings_system_display_off_collapsed",
                  "settings_display_preferences", "settings_display_preferences_off", "settings_display_preferences_off_collapsed",
                  "settings_models", "settings_vehicle_features", "settings_pip", "settings_feature_states",
                  "settings_feature_states_scrolled", "settings_long_text")
LARGE_SCENES = ("home", "settings_starpilot", "settings_device", "settings_toggles", "settings_software",
                "onroad_engaged_no_camera", "onroad_disengaged_no_camera", "onroad_alert_small",
                "onroad_alert_mid", "onroad_alert_full", "settings_starpilot_collapsed",
                *FEATURE_SCENES)
COMPACT_SCENES = ("home", "settings", "onroad_engaged_no_camera", "onroad_disengaged_no_camera",
                  "onroad_alert_small", "onroad_alert_mid", "onroad_alert_full")


def reference_feature_scene(scene: str) -> tuple[Destination, str, FeatureSettingsState]:
  """Supplied offline rows, not owner snapshots or new runtime destinations."""
  def boolean(key: str, value: str, *, available: bool = True) -> FeatureRow:
    return FeatureRow(key, key.replace("_", " ").title(), value, b"1" if value == "On" else b"0",
                      ("Off", "On"), available=available, reason="" if available else "Unavailable while driving")

  number = FeatureRow("brightness", "Brightness", "75", b"75", step=5, minimum=0, maximum=100,
                      unit="%", available=True)
  reset = FeatureRow("pip:reset", "Restore default camera crop", "Reset to Default", available=True,
                     reason="Requires confirmation")
  if scene == "settings_driving_controls":
    rows = tuple(FeatureRow("", label, "Saved settings", page=page, available=True) for page, label in (
      ("slc", "Speed Limit Controller"), ("lane", "Lane Centering"), ("lane_change", "Lane Changes"),
      ("profiles", "Long Planner"), ("conditional", "Conditional Driving Modes"), ("curve", "Curve Speed Controller"),
      ("torque", "Steering and Torque"), ("aol", "Always On Lateral"), ("wheel", "Wheel Controls")))
    destination, field, page, title = Destination.DRIVING_CONTROLS, "features", "hub", "Driving Controls"
  elif scene == "settings_sounds":
    rows = (FeatureRow("soundpack", "Sound Pack", "Standard", choices=("Standard", "Classic"), available=True),
            FeatureRow("volume", "Alert Volume", "Auto", choices=("Auto",), step=5, maximum=100, available=True), number)
    destination, field, page, title = Destination.SOUNDS, "sounds", "sounds", "Sounds & Alerts"
  elif scene.startswith(("settings_system", "settings_display_preferences")):
    available = not scene.endswith("_unavailable")
    mode = "Offroad" if scene.endswith("_offroad") else "Onroad" if scene.endswith("_onroad") else "Auto"
    display_on = "_display_off" not in scene and not scene.endswith(("_off", "_off_collapsed"))
    display_reason = "" if display_on else "Saved; custom display is off"
    rows = (FeatureRow("drive_state", "Force Drive State", mode, available=available,
                       reason="Device: " + ("unavailable" if not available else "onroad" if mode == "Onroad" else "offroad"),
                       actions=tuple((label, available and label != mode) for label in ("Auto", "Offroad", "Onroad"))),
            FeatureRow("device_power", "Device Power", "", available=True, actions=(("Reboot", True), ("Power Off", mode != "Onroad"))),
            FeatureRow(MASTER, "Custom Display Settings", "On" if display_on else "Off", choices=("Off", "On"), available=True),
            FeatureRow("ScreenBrightness", "Parked Brightness", "Auto", page="ScreenBrightness", available=display_on,
                       reason=display_reason, presets=(("Auto", "Auto"),)),
            FeatureRow("ScreenBrightnessOnroad", "Driving Brightness", "Auto", page="ScreenBrightnessOnroad", available=display_on,
                       reason=display_reason, presets=(("Auto", "Auto"),)),
            FeatureRow("ScreenTimeout", "Parked Screen Timeout", "30 s", page="ScreenTimeout", available=display_on,
                       reason=display_reason, presets=(("30 s", "30 s"),)),
            FeatureRow("ScreenTimeoutOnroad", "Driving Interaction Timeout", "10 s", page="ScreenTimeoutOnroad", available=display_on,
                       reason=display_reason or "Returns to driving view; turns off screen if Turn Off Comma Screen is on",
                       presets=(("10 s", "10 s"),)),
            FeatureRow(SCREEN_OFF, "Turn Off Comma Screen", "Off", choices=("Off", "On"), available=True,
                       reason=SCREEN_OFF_DESCRIPTION),
            FeatureRow("power:enabled", "Parked Power", "Stock", choices=("Stock", "On"), available=True),
            FeatureRow("power:hours", "Maximum Parked Time", "30 h", page="power:hours", available=True,
                       reason="Saved; stock power policy applies", presets=(("30 h", "30 h"),)),
            FeatureRow("power:volts", "Low-Voltage Cutoff", "11.8 V", page="power:volts", available=True,
                       reason="Saved; stock power policy applies", presets=(("11.8 V", "11.8 V"),))) + map_page({"schemaVersion": 1, "state": "transferring",
                                                                              "selectedGeneration": "", "completedGroups": 2,
                                                                              "totalGroups": 6}).rows
    destination, field, page, title = Destination.SYSTEM, "display", "system", "System"
    if scene.startswith("settings_display_preferences"):
      rows = rows[2:8]
      page, title = "display", "Custom Display Settings"
    else:
      rows = rows[:2] + (replace(rows[2], page="display", reason="Brightness and screen timing"),) + rows[8:]
  elif scene == "settings_models":
    rows = (FeatureRow("", "Active Small", "Bundled driving model", page="models:small", available=True),
            FeatureRow("", "Active Big", "Bundled driving model", page="models:big", available=True),
            FeatureRow("", "Runtime", "Healthy", reason="Updates while driving"),
            FeatureRow("", "Compiled artifact", "0123456789abcdef"))
    destination, field, page, title = Destination.DRIVING_MODEL, "models", "models", "Driving Model"
  elif scene == "settings_vehicle_features":
    rows = (boolean("ToyotaAutoHold", "On"), boolean("LongPitch", "Off", available=False))
    destination, field, page, title = Destination.DRIVING_CONTROLS, "features", "vehicle", "Vehicle Settings"
  elif scene == "settings_pip":
    rows = (boolean("pip:enabled", "On"), boolean("pip:blinker", "Off"), number, reset,
            FeatureRow("", "Camera availability", "No fresh cabin frame", reason="Check the live crop preview while parked"))
    destination, field, page, title = Destination.APPEARANCE, "appearance", "pip", "Blind Spot Camera and Preview"
  else:
    rows = (boolean("enabled_off", "Off"), boolean("enabled_on", "On"),
            boolean("disabled_off", "Off", available=False), boolean("disabled_on", "On", available=False),
            FeatureRow("repair", "Repair saved preference", "Invalid saved choice", choices=("Off", "On"),
                       available=True, reason="Choose Off to repair", repair_value="Off"),
            FeatureRow("unreadable", "Saved preference", "Unreadable", reason="Saved source cannot be read"),
            FeatureRow("", "Following", ""), number, reset,
            FeatureRow("", "Child settings", "Saved settings", page="lane", available=True),
            FeatureRow("stock", "Parked Power", "Stock", choices=("Stock", "On"), available=True),
            boolean("last_off", "Off"), boolean("last_on", "On"))
    destination, field, page, title = Destination.DRIVING_CONTROLS, "features", "lane", "Feature States"
    if scene == "settings_appearance":
      destination, field, page, title = Destination.APPEARANCE, "appearance", "appearance", "Onroad HUD"
    elif scene == "settings_long_text":
      title = "Long settings title with descenders and enough text to exercise measured title overflow " * 2
      rows = tuple(replace(row, label=row.label + " with a very long explanatory label " * 4,
                           reason="Long explanatory reason with descenders, units, and saved source information. " * 3)
                   for row in rows)
  state = FeatureSettingsState(page=page, title=title,
                               subtitle="Brightness and screen timing." if page == "display" else
                                        "Display, drive state, power, and offline maps." if field == "display" else
                                        "Saved display preferences; changes remain governed by existing settings owners.",
                               parent_title=feature_parent_title(page) if field == "features" else "Appearance" if page == "pip" else
                                            "System" if page == "display" else "StarPilot",
                               rows=rows, parked=True,
                               scroll=5 if scene == "settings_system_maps" or scene.endswith("_scrolled") else 0,
                               sidebar_expanded=not scene.endswith("_collapsed"))
  return destination, field, state


def reference_onroad(scene: str) -> OnroadState:
  alert = OnroadAlert()
  if scene.startswith("onroad_alert_"):
    size = AlertSize(scene.removeprefix("onroad_alert_"))
    alert = OnroadAlert(size=size, text1="TAKE CONTROL IMMEDIATELY" if size == AlertSize.FULL else "Baseline alert",
                        text2="Protected UI fixture", critical=size == AlertSize.FULL)
  engaged = scene == "onroad_engaged_no_camera"
  return OnroadState(engaged=engaged, camera_available=False,
                     speed_mps=20.0, cruise_kph=80.0, speed_limit=SpeedLimitObservation(), alert=alert,
                     personality=0, lateral_active=engaged, longitudinal_active=engaged,
                     slc_system_long_available=engaged)


class ShellViews:
  def __init__(self, profile: Profile, fonts: BitmapFonts, asset_directory: Path):
    self.profile = profile
    self.view = ShellView(fonts, asset_directory)

  def render(self, scene: str) -> None:
    if scene not in LARGE_SCENES + COMPACT_SCENES:
      raise ValueError(f"Unknown scene {scene}")
    if scene.startswith("onroad_"):
      mode, selected = ShellMode.ONROAD, Destination.STAR
    elif scene.startswith("settings"):
      mode = ShellMode.SETTINGS
      selected = {"settings_device": Destination.DEVICE, "settings_software": Destination.SOFTWARE,
                  "settings_toggles": Destination.TOGGLES}.get(scene, Destination.STAR)
    else:
      mode, selected = ShellMode.HOME, Destination.STAR
    settings = reference_settings_state()
    features = {}
    if scene in FEATURE_SCENES:
      selected, field, state = reference_feature_scene(scene)
      # Capture each supplied page at rest, independently of the previous scene's animation.
      getattr(self.view, field).reset(state)
      settings = replace(settings, sidebar_expanded=state.sidebar_expanded)
      features[field] = state
    elif scene == "settings_starpilot_collapsed":
      settings = replace(settings, sidebar_expanded=False)
    self.view.render(ShellSnapshot(mode=mode, home=reference_state(), settings=settings,
                                   onroad=reference_onroad(scene), device=DeviceState(), software=SoftwareState(),
                                   toggles=TogglesState(), selected=selected, **features))

  def close(self) -> None:
    self.view.close()


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--profile", type=Profile, choices=list(Profile), required=True)
  parser.add_argument("--font-directory", type=Path, required=True)
  parser.add_argument("--asset-directory", type=Path, required=True)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args()
  if args.output.exists():
    raise ValueError("Refusing to overwrite shell evidence")
  args.output.mkdir(parents=True)
  width, height = args.profile.size
  canvas_width = min(width, 960)
  canvas_height = round(height * canvas_width / width)
  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN | rl.ConfigFlags.FLAG_MSAA_4X_HINT | rl.ConfigFlags.FLAG_WINDOW_HIGHDPI)
  rl.init_window(canvas_width, canvas_height, "Offline UI shell capture")
  if not rl.is_window_ready():
    raise RuntimeError("Unable to initialize native graphics")
  target = rl.load_render_texture(width, height)
  try:
    with BitmapFonts(args.profile, args.font_directory) as fonts:
      views = ShellViews(args.profile, fonts, args.asset_directory)
      try:
        report = {"profile": args.profile.value, "development_only": True, "dimensions": [width, height], "scenes": {}}
        for scene in LARGE_SCENES if args.profile == Profile.LARGE else COMPACT_SCENES:
          for _frame in range(18):
            rl.begin_texture_mode(target)
            rl.clear_background(rl.BLACK)
            views.render(scene)
            rl.end_texture_mode()
          image = rl.load_image_from_texture(target.texture)
          try:
            rl.image_format(image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
            rl.image_flip_vertical(image)
            path = args.output / f"{scene}.png"
            if not rl.export_image(image, str(path)):
              raise RuntimeError(f"Unable to export {scene}")
            report["scenes"][scene] = hashlib.sha256(bytes(image.data[:width * height * 4])).hexdigest()
          finally:
            rl.unload_image(image)
        (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
      finally:
        views.close()
  finally:
    rl.unload_render_texture(target)
    rl.close_window()


if __name__ == "__main__":
  main()
