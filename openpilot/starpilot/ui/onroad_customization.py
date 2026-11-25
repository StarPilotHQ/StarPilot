"""Validated onroad colors and independent logical-pixel layouts."""

from dataclasses import replace
import copy
import json
import math
import re
from functools import lru_cache
from typing import Any, NotRequired, TypedDict, cast

from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.onroad_torque_geometry import maximum_footprint
from openpilot.starpilot.ui.large_speed_geometry import LIMIT_RECTS, confirmation_footprint
from openpilot.starpilot.ui.onroad_stopped_timer import timer_size
from openpilot.starpilot.ui.onroad_favorites_geometry import shelf_metadata
from openpilot.starpilot.ui.large_speed_geometry import WIDTH, CARD_HEIGHT, sources_extent

PARAM_KEY = "OnroadCustomizations"
MAX_BYTES = 16384
# Legacy saved layouts retain this field even though the paired speed widget uses fixed alignment.
SPEED_ALIGNMENTS = ("left", "center", "right")
PALETTE = {"cardFill": "#000000A6", "cardBorder": "#C4CDD0B4", "text": "#FFFFFFFF"}
ROAD_COLORS = {"path": "#30FF9CFF", "pathEdge": "#00FF40FF", "laneLines": "#FFFFFFFF"}
PATH_MODES = ("default", "color", "rainbow", "acceleration")
WHEEL_SIZES = {"large": (144, 192, 240), "compact": (40, 50, 70)}
DM_SIZES = {"large": (64, 128, 192), "compact": (40, 60, 90)}
WIDGET_SIZES = {"steering_wheel": WHEEL_SIZES, "driver_monitor": DM_SIZES}


class Bounds(TypedDict):
  x: float
  y: float
  width: float
  height: float


class WidgetPlacement(TypedDict):
  x: float
  y: float
  enabled: bool
  size: NotRequired[int | str]
  width: NotRequired[int]
  height: NotRequired[int]
  transparency: NotRequired[int]
  hide_when_no_alert: NotRequired[bool]
  opacity: NotRequired[int]
  bluetooth: NotRequired[bool]
  wifiName: NotRequired[bool]
  rotate: NotRequired[bool]
  scale: NotRequired[int]


class Widget(TypedDict):
  label: str
  kind: str
  width: float
  height: float
  default: WidgetPlacement
  bounds: NotRequired[Bounds]
  visualInsetTop: NotRequired[float]
  visualInsetLeft: NotRequired[float]
  layer: NotRequired[str]
  note: NotRequired[str]
  defaultAnchor: NotRequired[str]
  colors: NotRequired[dict[str, str | None]]
  resizable: NotRequired[dict[str, int]]
  sourcesExtent: NotRequired[dict[str, float]]
  confirmation: NotRequired[Bounds]
  confirmationObstacle: NotRequired[Bounds]
  box: NotRequired[dict[str, int]]
  opacity: NotRequired[dict[str, int]]
  sizes: NotRequired[dict[str, list[int]]]
  scale: NotRequired[dict[str, int]]
  bluetoothToggle: NotRequired[bool]
  rotateToggle: NotRequired[bool]
  wifiNameHeight: NotRequired[int]
  anchorShift: NotRequired[float]
  expandable: NotRequired[bool]
  hidden: NotRequired[bool]


class LayoutProfile(TypedDict):
  label: str
  width: float
  height: float
  bounds: Bounds
  widgets: dict[str, Widget]
  reservedZones: list[Bounds]
  inputZones: NotRequired[list[dict[str, Any]]]
  inputZonePriority: NotRequired[str]
  widgetOrder: NotRequired[list[str]]
  runtimePreviewWidgets: NotRequired[list[str]]
  quickSelectShelves: NotRequired[list[dict[str, Any]]]


def _widget(label: str, kind: str, width: float, height: float, x: float, y: float) -> Widget:
  return {"label": label, "kind": kind, "width": width, "height": height,
          "default": {"x": x, "y": y, "enabled": True}}


PROFILES: dict[str, LayoutProfile] = {
  "large": {"label": "Large UI", "width": 2160, "height": 1080,
            "bounds": {"x": 30, "y": 30, "width": 1800, "height": 1020},
            "reservedZones": [],
            "widgets": {
              "current_speed": _widget("Current speed", "current_speed", 580, 300, 640, 30),
              "cruise_limits": _widget("Cruise and speed limit", "cruise_limits", WIDTH, CARD_HEIGHT, 88, 75),
              "steering_wheel": _widget("Steering wheel", "steering_wheel", 192, 192, 1588, 75),
              "driver_monitor": _widget("Driver monitoring", "driver_monitor", 128, 128, 270, 840),
              "following_distance": _widget("Following distance", "following_distance", 150, 200, 88, 804)}},
  "compact": {"label": "Small UI", "width": 536, "height": 240,
              "bounds": {"x": 0, "y": 0, "width": 476, "height": 240},
              "reservedZones": [],
              "widgets": {
                "max_speed": _widget("Set speed", "max_speed", 146, 162, 0, 0),
                "speed_limit": _widget("Speed limit", "speed_limit", *LIMIT_RECTS['compact'][2:], *LIMIT_RECTS['compact'][:2]),
                "steering_wheel": _widget("Steering wheel", "steering_wheel", 50, 50, 21, 176),
                "driver_monitor": _widget("Driver monitoring", "driver_monitor", 60, 60, 16, 10)}}}

PROFILES['large']['widgets']['steering_wheel']['rotateToggle'] = True

for _profile in PROFILES:
  _owner = 'cruise_limits' if _profile == 'large' else 'speed_limit'
  PROFILES[_profile]['widgets'][_owner]['confirmation'] = confirmation_footprint(_profile)

SPEED_LAYOUT_V7 = {
  'large': {'cruise_limits': (320, 210, 88, 75)},
  'compact': {'max_speed': (162, 162, 0, 0), 'speed_limit': (118, 132, 174, 12)},
}
COMPACT_SPEED_LAYOUT_V8 = {'max_speed': (146, 114, 0, 0), 'speed_limit': (264, 82, 158, 18)}

for profile, sizes in DM_SIZES.items():
  PROFILES[profile]['widgets']['driver_monitor']['default']['size'] = sizes[1]

LEGACY_WIDGETS = {"large": {"current_speed", "cruise_limits", "steering_wheel", "following_distance"},
                  "compact": {"max_speed", "speed_limit", "steering_wheel"}}
DM_WIDGETS = {profile: keys | {"driver_monitor"} for profile, keys in LEGACY_WIDGETS.items()}
for _profile, _data in PROFILES.items():
  _bounds = _data["bounds"]
  _x, _y, _width, _height = maximum_footprint(_bounds["x"], _bounds["y"], _bounds["width"], _bounds["height"], _data["width"])
  _data["widgets"]["torque_bar"] = {
    **_widget("Torque bar", "torque_bar", _width, _height, _x, _y), "layer": "underlay",
    "note": "Torque command or lateral-acceleration demand. Full width reflects the configured display scale."}
RAILLESS_WIDGETS = {profile: set(data["widgets"]) for profile, data in PROFILES.items()}
RAIL_WIDGETS = ("model_confidence", "conditional_mode", "following_distance")
for _index, (_key, _label) in enumerate(zip(RAIL_WIDGETS, ("Model confidence", "CEM and driving mode", "Following distance"), strict=True)):
  PROFILES["compact"]["widgets"][_key] = {
    **_widget(_label, _key, 60, 80, 476, _index * 80),
    "bounds": {"x": 0, "y": 0, "width": 536, "height": 240},
  }

# Keep layouts saved before camera widgets valid when the registry grows.
CAMERALESS_WIDGETS = {profile: set(data["widgets"]) for profile, data in PROFILES.items()}
CAMERA_WIDGETS = ("pip_left", "pip_right")


def camera_widgets(profile):
  return CAMERA_WIDGETS[:1] if str(profile) == "compact" else CAMERA_WIDGETS


for _profile, (_width, _height, _margin) in {"large": (600, 600, 24), "compact": (476, 240, 0)}.items():
  _bounds = PROFILES[_profile]["bounds"]
  for _side, _key in zip(("left", "right"), camera_widgets(_profile), strict=False):
    _x = _bounds["x"] + (_margin if _side == "left" else _bounds["width"] - _margin - _width)
    _y = _bounds["y"] + _bounds["height"] - _margin - _height
    PROFILES[_profile]["widgets"][_key] = {
      **_widget((f"Adj Spot Camera {_side.title()}" if _profile == "large" else "Adj Spot Camera"), "pip_camera", _width, _height, _x, _y), "layer": "underlay"}

MODE_WIDGET = "driving_mode_descriptions"
for _profile, _geometry in {"large": (580, 68, 640, 344), "compact": (168, 44, 154, 0)}.items():
  _mode_widget = _widget("Driving Mode Descriptions", MODE_WIDGET, *_geometry)
  _mode_widget["default"]["enabled"] = False
  PROFILES[_profile]["widgets"][MODE_WIDGET] = _mode_widget

# It starts disabled so an upgrade never adds a new onroad element without the
# driver's choice; validation below adds the placement to older documents.
CLOCK_WIDGET = "clock"
for _profile, _geometry in {"large": (300, 72, 780, 430), "compact": (118, 20, 330, 3)}.items():
  _clock_widget = _widget("Clock", CLOCK_WIDGET, *_geometry)
  _clock_widget["default"]["enabled"] = False
  PROFILES[_profile]["widgets"][CLOCK_WIDGET] = _clock_widget

STOPPED_TIMER_WIDGET = "stopped_timer"
for _profile, (_x, _y) in {"compact": (18, 48), "large": (600, 520)}.items():
  _timer_widget = _widget("Stopped timer", STOPPED_TIMER_WIDGET, *timer_size(_profile), _x, _y)
  _timer_widget["default"]["enabled"] = False
  _timer_widget["note"] = "Counts how long you have been stopped, after the first minute of the drive"
  PROFILES[_profile]["widgets"][STOPPED_TIMER_WIDGET] = _timer_widget

WEATHER_WIDGET = "weather"
for _profile, _geometry in {"large": (280, 100, 640, 540), "compact": (112, 48, 172, 174)}.items():
  _weather_widget = _widget("Weather", WEATHER_WIDGET, *_geometry)
  _weather_widget["default"]["enabled"] = False
  _weather_widget["note"] = "Outside temperature and conditions. Weather by Open-Meteo.com; requires internet and a GPS fix."
  PROFILES[_profile]["widgets"][WEATHER_WIDGET] = _weather_widget

NAV_CARD_WIDGET, NAV_MAP_WIDGET = "nav_card", "nav_map"
NAV_CARD_SIZE = (560, 255)  # sized for 3X glance legibility; grows up so its bottom edge stays put
MAP_MIN_SIZE = (280, 200)
MAP_OPACITY = (15, 100, 50)  # percent: min, max, default
_nav_card = _widget("Turn-by-turn", NAV_CARD_WIDGET, *NAV_CARD_SIZE, 1230, 355)
_nav_card["note"] = "The next maneuver while a route is active"
PROFILES["large"]["widgets"][NAV_CARD_WIDGET] = _nav_card
PROFILES["compact"]["widgets"][NAV_CARD_WIDGET] = {
  **_widget("Turn-by-turn", NAV_CARD_WIDGET, 368, 142, 96, 16),
  "note": "The next maneuver on the comma screen. StarPort has its own layout."}
_nav_map = _widget("Map overlay", NAV_MAP_WIDGET, 560, 420, 1230, 625)
_nav_map["default"].update(enabled=False, width=560, height=420, opacity=MAP_OPACITY[2])
_nav_map["box"] = {"minWidth": MAP_MIN_SIZE[0], "maxWidth": 1800, "minHeight": MAP_MIN_SIZE[1], "maxHeight": 1020}
_nav_map["opacity"] = dict(zip(("min", "max", "default"), MAP_OPACITY, strict=True))
_nav_map["note"] = ("Road lines and your route over the camera. Drag the corner to resize; opacity sets how much shows. "
                    + "Turning it on here turns it off in the other screen's layout.")
PROFILES["large"]["widgets"][NAV_MAP_WIDGET] = _nav_map

UNIDEN_WIDGET = "uniden_alerts"
UNIDEN_DISPLAY_DEFAULTS = {"transparency": 8, "hide_when_no_alert": True, "scale": 100}
UNIDEN_DISPLAY_FIELDS = set(UNIDEN_DISPLAY_DEFAULTS)
UNIDEN_RETIRED_FIELDS = ("hide_quiet_ride", "hide_mute_memory")
UNIDEN_SCALE_RANGE = (50, 150)  # percent


def valid_uniden_display(placement: dict) -> bool:
  return (type(placement.get("transparency")) is int and 0 <= placement["transparency"] <= 100 and
          type(placement.get("hide_when_no_alert")) is bool and
          type(placement.get("scale")) is int and UNIDEN_SCALE_RANGE[0] <= placement["scale"] <= UNIDEN_SCALE_RANGE[1])


def uniden_display(placement: dict) -> dict:
  return {**UNIDEN_DISPLAY_DEFAULTS, **{key: value for key, value in placement.items() if key not in UNIDEN_RETIRED_FIELDS}}


def scaled_size(width: float, height: float, scale: int) -> tuple[int, int]:
  """Rounds half up, like the editor's Math.round."""
  return math.floor(width * scale / 100 + .5), math.floor(height * scale / 100 + .5)
_uniden = _widget("Uniden alerts", UNIDEN_WIDGET, 460, 260, 700, 780)
_uniden["default"].update(cast(WidgetPlacement, UNIDEN_DISPLAY_DEFAULTS))
_uniden["note"] = "R4/R8 detector alerts. Pair and select the detector in Galaxy Bluetooth. Tap an alert to mute."
PROFILES["large"]["widgets"][UNIDEN_WIDGET] = _uniden

JETLINK_WIDGET = "jetlink_status"
JETLINK_SIZE = (420, 100)
JETLINK_OPACITY = (0, 100, 85)  # percent: min, max, default
_jetlink = _widget("Big model", JETLINK_WIDGET, *JETLINK_SIZE, 30, 650)
_jetlink["default"].update(enabled=False, opacity=JETLINK_OPACITY[2])
_jetlink["opacity"] = dict(zip(("min", "max", "default"), JETLINK_OPACITY, strict=True))
_jetlink["note"] = "Big or small model driving, and Jetlink's or the Chestnut's state (else Disconnected). Opacity sets the card background."
PROFILES["large"]["widgets"][JETLINK_WIDGET] = _jetlink

SIGNAL_WIDGET = "network_signal"
SIGNAL_SCALE = 120 / 88  # drawn on an 88 px grid
SIGNAL_GRIDS = {"ring": (112, 140), "horizontal": (268, 112), "vertical": (112, 196)}
SIGNAL_SIZES = {style: (round(w * SIGNAL_SCALE), round(h * SIGNAL_SCALE)) for style, (w, h) in SIGNAL_GRIDS.items()}
SIGNAL_LEGACY_GRIDS = {"ring": (88, 88), "horizontal": (268, 84), "vertical": (96, 160)}  # before the Bluetooth row
SIGNAL_SIZE = SIGNAL_SIZES["ring"]
SIGNAL_WIFI_NAME_GRID = 22  # header row for the Wi-Fi network name, on the 88 px grid
SIGNAL_WIFI_NAME_HEIGHT = round(SIGNAL_WIFI_NAME_GRID * SIGNAL_SCALE)
SIGNAL_OPACITY = (0, 100, 85)  # percent: min, max, default
SIGNAL_SCALE_RANGE = (30, 100, 100)  # percent of the style's size: min, max, default
_signal = _widget("Connections", SIGNAL_WIDGET, *SIGNAL_SIZE, 1624, 290)
_signal["default"].update(enabled=False, opacity=SIGNAL_OPACITY[2], size="ring", scale=SIGNAL_SCALE_RANGE[2], bluetooth=True,
                           wifiName=False)
_signal["opacity"] = dict(zip(("min", "max", "default"), SIGNAL_OPACITY, strict=True))
_signal["scale"] = dict(zip(("min", "max", "default"), SIGNAL_SCALE_RANGE, strict=True))
_signal["sizes"] = {style: list(size) for style, size in SIGNAL_SIZES.items()}
_signal["bluetoothToggle"] = True
_signal["wifiNameHeight"] = SIGNAL_WIFI_NAME_HEIGHT


def valid_signal_scale(placement: dict) -> bool:
  return type(placement.get("scale")) is int and SIGNAL_SCALE_RANGE[0] <= placement["scale"] <= SIGNAL_SCALE_RANGE[1]


def signal_scale(placement: dict | None) -> float:
  """The chosen fraction of the style's full size; a missing or bad value draws full size."""
  return placement["scale"] / 100 if placement and valid_signal_scale(placement) else 1.


def signal_size(placement: dict | None) -> tuple[int, int]:
  width, height = SIGNAL_SIZES.get((placement or {}).get("size"), SIGNAL_SIZE)
  if (placement or {}).get("wifiName") is True:
    height += SIGNAL_WIFI_NAME_HEIGHT
  scale = signal_scale(placement)
  return math.floor(width * scale + .5), math.floor(height * scale + .5)
_signal["note"] = ("Cellular and Wi-Fi strength with the active link highlighted, Bluetooth status and device name, " +
                   "and optionally the Wi-Fi network name on top. Opacity sets the background.")
PROFILES["large"]["widgets"][SIGNAL_WIDGET] = _signal
# Restore the Dom road graphic in the gap between the speed card and main speed.
# Center the road gauge above the driver monitor, below the speed-limit controls.
# It has no background and appears only with a current selected source.
CEM_GAUGE_WIDGET = 'cem_gauge'
# The gauge draws centered in a 344 x 300 frame; its box is the part it can actually fill, at this offset inside
# that frame. Version 6 and older layouts saved the frame's corner and move by this offset when they load.
CEM_GAUGE_FRAME = (344, 300)
CEM_GAUGE_INSET = (44, 24)
CEM_GAUGE_SIZE = (256, 276)
PROFILES['large']['widgets'][CEM_GAUGE_WIDGET] = _widget('CEM road gauge', CEM_GAUGE_WIDGET, *CEM_GAUGE_SIZE,
                                                         56, 536)
for _profile, _geometry in {"large": (112, 70, 165, 294), "compact": (45, 28, 220, 12)}.items():
  PROFILES[_profile]["widgets"]["rear_fog"] = _widget("Rear fog indicator", "rear_fog", *_geometry)

ROAD_BASE_WIDTH = 1860
SIDEBAR_EXTRA = 300
ANCHOR_SHIFT = {"steering_wheel": 1.0, "pip_right": 1.0, NAV_CARD_WIDGET: 1.0, NAV_MAP_WIDGET: 1.0,
                "current_speed": .5, "torque_bar": .5, MODE_WIDGET: .5, CLOCK_WIDGET: .5, WEATHER_WIDGET: .5}
for _key, _widget_data in PROFILES["large"]["widgets"].items():
  if _key in ANCHOR_SHIFT:
    _widget_data["anchorShift"] = ANCHOR_SHIFT[_key]
  _base = PROFILES["large"]["bounds"]
  _widget_data["bounds"] = {**_base, "x": _base["x"] - ANCHOR_SHIFT.get(_key, 0) * SIDEBAR_EXTRA, "width": _base["width"] + SIDEBAR_EXTRA}
  _widget_data["expandable"] = True
# Older documents retain every saved placement and gain this independent widget.
OPTIONAL_WIDGETS = frozenset({WEATHER_WIDGET, MODE_WIDGET, CLOCK_WIDGET, STOPPED_TIMER_WIDGET, CEM_GAUGE_WIDGET,
                              NAV_CARD_WIDGET, NAV_MAP_WIDGET, JETLINK_WIDGET, SIGNAL_WIDGET, UNIDEN_WIDGET, "rear_fog"})

# The torque arc grows when the developer sidebar closes; protect both road widths.
_torque = PROFILES['large']['widgets']['torque_bar']
_bounds = PROFILES['large']['bounds']
_x, _y, _w, _h = maximum_footprint(_bounds['x'], _bounds['y'], _bounds['width'] + SIDEBAR_EXTRA, _bounds['height'], 2160)
_left, _top = min(_x, _torque['default']['x']), min(_y, _torque['default']['y'])
_torque['confirmationObstacle'] = {
  'x': _left - _torque['default']['x'], 'y': _top - _torque['default']['y'],
  'width': max(_x + _w, _torque['default']['x'] + _torque['width']) - _left,
  'height': max(_y + _h, _torque['default']['y'] + _torque['height']) - _top}

# These editor sprites use the runtime components and their canonical assets/fonts.
RUNTIME_PREVIEW_WIDGETS = {
  "large": (WEATHER_WIDGET, "current_speed", "cruise_limits", CEM_GAUGE_WIDGET, "steering_wheel", "following_distance",
            "driver_monitor", "rear_fog", MODE_WIDGET, CLOCK_WIDGET),
  "compact": (WEATHER_WIDGET, "max_speed", "speed_limit", "steering_wheel", "conditional_mode", "following_distance",
              "model_confidence", "driver_monitor", "rear_fog", MODE_WIDGET, CLOCK_WIDGET),
}

_FRAME_COLORS = {"cardFill": "#00000000", "cardBorder": "#00000000"}
WIDGET_COLORS = {
  "large": {
    MODE_WIDGET: {"text": "#FFFFFFFF"},
    CLOCK_WIDGET: {"text": "#FFFFFFFF"},
    WEATHER_WIDGET: {"cardFill": "#0F0D17CC", "cardBorder": "#C7AEF744", "text": "#F5F2FCFF"},
    CEM_GAUGE_WIDGET: {},
    UNIDEN_WIDGET: {"cardFill": "#10191FFF", "cardBorder": "#9D72FFFF", "text": None},
    "current_speed": {"text": None},
    "cruise_limits": {"cardFill": "#10191FFF", "cardBorder": "#35434DFF", "text": None},
    "steering_wheel": {"cardFill": None, "cardBorder": "#00000000"},
    "driver_monitor": _FRAME_COLORS,
    "following_distance": _FRAME_COLORS,
  },
  "compact": {
    MODE_WIDGET: {"text": "#FFFFFFFF"},
    CLOCK_WIDGET: {"text": "#FFFFFFFF"},
    WEATHER_WIDGET: {"cardFill": "#0F0D17CC", "cardBorder": "#C7AEF744", "text": "#F5F2FCFF"},
    "max_speed": {"text": None},
    "driver_monitor": _FRAME_COLORS,
    "model_confidence": _FRAME_COLORS,
    "conditional_mode": _FRAME_COLORS,
    "following_distance": {**_FRAME_COLORS, "text": "#FFFFFFFF"},
  },
}


def _fit_legacy_signal(placement: dict, bounds: Bounds) -> None:
  """Keep layouts saved before the Bluetooth row inside their bounds."""
  grid = SIGNAL_LEGACY_GRIDS.get(placement.get("size"))
  if grid is None or not valid_signal_scale(placement):
    return
  old = [round(round(side * SIGNAL_SCALE) * placement["scale"] / 100) for side in grid]
  new = signal_size(placement)
  axes = (("x", "width"), ("y", "height"))
  if all(type(placement.get(axis)) in (int, float) and math.isfinite(placement[axis]) and
         bounds[axis] <= placement[axis] <= bounds[axis] + bounds[extent] - size for (axis, extent), size in zip(axes, old, strict=True)):
    for (axis, extent), size in zip(axes, new, strict=True):
      placement[axis] = max(bounds[axis], min(placement[axis], bounds[axis] + bounds[extent] - size))


# Back-to-front order used when a layout first opts into custom layers.
DEFAULT_WIDGET_ORDER = {
  "large": (NAV_MAP_WIDGET, "cruise_limits", CEM_GAUGE_WIDGET, "current_speed",
            "steering_wheel", "torque_bar", "following_distance", "driver_monitor", MODE_WIDGET, CLOCK_WIDGET, WEATHER_WIDGET,
            STOPPED_TIMER_WIDGET, NAV_CARD_WIDGET, JETLINK_WIDGET, SIGNAL_WIDGET, UNIDEN_WIDGET, "rear_fog", "pip_left", "pip_right"),
  "compact": ("speed_limit", "max_speed", "steering_wheel", "torque_bar",
              "driver_monitor", *RAIL_WIDGETS, MODE_WIDGET, CLOCK_WIDGET, WEATHER_WIDGET,
              STOPPED_TIMER_WIDGET, NAV_CARD_WIDGET, "rear_fog", "pip_left"),
}


def validate_widget_order(order, widgets):
  if (type(order) is not list or any(type(key) is not str for key in order) or
      len(order) != len(widgets) or set(order) != set(widgets)):
    raise ValueError("Invalid widget layer order")
  return list(order)


def widget_order(document, profile):
  return document.get("widgetOrder", {}).get(str(profile), DEFAULT_WIDGET_ORDER[str(profile)])


def widget_bounds(profile, widget):
  return PROFILES[profile]["widgets"][widget].get("bounds", PROFILES[profile]["bounds"])


def customization_metadata():
  profiles = copy.deepcopy(PROFILES)
  profiles["large"]["widgets"]["driver_monitor"]["defaultAnchor"] = "driver_side"
  profiles["large"]["widgets"]["following_distance"]["defaultAnchor"] = "driver_side"
  # The editor grows the cruise box by these while speed-limit sources are shown.
  profiles["large"]["widgets"]["cruise_limits"]["sourcesExtent"] = {
    "inline": sources_extent({'speedSources': True}), "card": sources_extent({'speedSources': True, 'speedSourcesCard': True})}
  profiles["large"]["inputZones"] = [{"id": "favorites_drawer", "label": "Quick Select drawer",
                                    "x": 30, "y": 900, "width": 340, "height": 150,
                                    "excludes": [key for key in profiles["large"]["widgets"]
                                                 if key not in ("torque_bar", "pip_left", "pip_right", "nav_map")]}]
  profiles["large"]["quickSelectShelves"] = shelf_metadata()
  profiles["compact"]["inputZones"] = [
    {"id": f"favorite_{index + 1}", "label": f"Quick Select {index + 1}",
     "x": index * 476 / 3, "y": 0, "width": 476 / 3, "height": 240} for index in range(3)]
  for profile in profiles.values():
    profile["inputZonePriority"] = "Enabled Quick Select slots; speed-limit controls and steering wheel take priority"
  profiles["large"]["inputZonePriority"] = "Visible corner cue; other controls take priority over the larger swipe area"
  for name, profile in profiles.items():
    for key, widget in profile["widgets"].items():
      widget["colors"] = dict(WIDGET_COLORS[name].get(key, {}))
    for key, sizes in WIDGET_SIZES.items():
      minimum, default, maximum = sizes[name]
      profile["widgets"][key]["resizable"] = {"min": minimum, "default": default, "max": maximum}
      profile["widgets"][key]["default"]["size"] = default
  for profile, data in profiles.items():
    data["widgetOrder"] = list(DEFAULT_WIDGET_ORDER[profile])
    data["runtimePreviewWidgets"] = list(RUNTIME_PREVIEW_WIDGETS[profile])
  return {"profiles": profiles, "roadColorFields": [
    {"id": key, "label": label, "default": ROAD_COLORS[key]}
    for key, label in (("path", "Path"), ("pathEdge", "Path edges"), ("laneLines", "Outer lane lines"))], "paletteFields": [
    {"id": key, "label": label, "default": PALETTE[key]}
    for key, label in (("cardFill", "Card fill"), ("cardBorder", "Card border"), ("text", "Text"))]}


def default_document():
  return {"version": 17, "clock24Hour": False, "reduceMotion": False, "palette": dict(PALETTE), "roadColors": {profile: {} for profile in PROFILES},
          "widgetColors": {profile: {} for profile in PROFILES}, "layouts": {
    profile: {key: {**widget["default"], **({"size": WIDGET_SIZES[key][profile][1]} if key in WIDGET_SIZES else {})}
              for key, widget in data["widgets"].items()}
    for profile, data in PROFILES.items()}}


def validate_map_box(placement, widget):
  box, opacity = widget["box"], widget["opacity"]
  if (type(placement["width"]) is not int or not box["minWidth"] <= placement["width"] <= box["maxWidth"] or
      type(placement["height"]) is not int or not box["minHeight"] <= placement["height"] <= box["maxHeight"] or
      type(placement["opacity"]) is not int or not opacity["min"] <= placement["opacity"] <= opacity["max"]):
    raise ValueError("Invalid map overlay size or opacity")


def migrate_speed_layout(layout, profile, bounds, *, previous=None, widgets=None):
  widgets = PROFILES[profile]['widgets'] if widgets is None else widgets
  if profile == 'large':
    gauge = layout[CEM_GAUGE_WIDGET]
    if (gauge['x'], gauge['y']) == (480, 99):
      default = widgets[CEM_GAUGE_WIDGET]['default']
      gauge.update(x=default['x'], y=default['y'])
  for key, (_, _, old_x, old_y) in (previous or SPEED_LAYOUT_V7[profile]).items():
    saved, widget = layout[key], widgets[key]
    area = widget.get('bounds', bounds)
    if (saved['x'], saved['y']) == (old_x, old_y):
      saved.update(x=widget['default']['x'], y=widget['default']['y'])
    for axis, extent in (('x', 'width'), ('y', 'height')):
      saved[axis] = max(area[axis], min(saved[axis], area[axis] + area[extent] - widget[extent]))


def migrate_large_sign_layout(layout, bounds, *, widgets=None, extra_height=0):
  widgets = PROFILES['large']['widgets'] if widgets is None else widgets
  card = layout['cruise_limits']
  card['y'] = min(card['y'], bounds['y'] + bounds['height'] - widgets['cruise_limits']['height'] - extra_height)


def confirmation_layout_valid(layout, profile, widgets, bounds, *, extra_height=0):
  owner = 'cruise_limits' if profile == 'large' else 'speed_limit'
  bounds = widgets[owner].get('bounds', bounds)
  at = layout[owner]
  if not at['enabled']:
    return True
  area = confirmation_footprint(profile)
  left, top = at['x'] + area['x'], at['y'] + area['y']
  right, bottom = left + area['width'], top + area['height']
  if extra_height:
    left, right = min(left, at['x']), max(right, at['x'] + widgets[owner]['width'])
    bottom = max(bottom, at['y'] + widgets[owner]['height'] + extra_height)
  if left < bounds['x'] or top < bounds['y'] or right > bounds['x'] + bounds['width'] or bottom > bounds['y'] + bounds['height']:
    return False
  for key in (('torque_bar',) if profile == 'large' else ('max_speed', 'torque_bar')):
    other, area = layout[key], widgets[key]
    area = area.get('confirmationObstacle', {'x': 0, 'y': 0, 'width': area['width'], 'height': area['height']})
    ox, oy = other['x'] + area['x'], other['y'] + area['y']
    if (other['enabled'] and left < ox + area['width'] and ox < right and top < oy + area['height'] and oy < bottom):
      return False
  return True


def migrate_confirmation_layout(layout, profile, widgets, bounds, *, extra_height=0):
  owner = 'cruise_limits' if profile == 'large' else 'speed_limit'
  bounds = widgets[owner].get('bounds', bounds)
  area, at = confirmation_footprint(profile), layout[owner]
  for axis, extent in (('x', 'width'), ('y', 'height')):
    low = bounds[axis]
    high = bounds[axis] + bounds[extent] - widgets[owner][extent]
    if at['enabled']:
      low = max(low, bounds[axis] - area[axis])
      high = min(high, bounds[axis] + bounds[extent] - area[axis] - area[extent])
    if axis == 'y':
      high = min(high, bounds['y'] + bounds['height'] - widgets[owner]['height'] - extra_height)
    at[axis] = max(math.ceil(low), min(at[axis], math.floor(high)))
  if not confirmation_layout_valid(layout, profile, widgets, bounds, extra_height=extra_height):
    # Preserve independently placed guidance; only the speed cluster needs room for the chooser.
    at.update(x=widgets[owner]['default']['x'], y=widgets[owner]['default']['y'])
    if not confirmation_layout_valid(layout, profile, widgets, bounds, extra_height=extra_height):
      for key in (('torque_bar',) if profile == 'large' else ('max_speed', 'torque_bar')):
        layout[key].update(x=widgets[key]['default']['x'], y=widgets[key]['default']['y'])


def validate_document(value):
  if type(value) is not dict or type(value.get("version")) is not int or value["version"] not in range(1, 18):
    raise ValueError("Invalid customization version")
  if value["version"] < 16:
    # Version 16 folded the separate speed limit actions widget into the speed limit sign.
    value = copy.deepcopy(value)
    for section in ("layouts", "widgetColors"):
      for widgets in value[section].values() if type(value.get(section)) is dict else ():
        if type(widgets) is dict:
          widgets.pop("speed_limit_actions", None)
    if type(value.get("widgetOrder")) is dict:
      value["widgetOrder"] = {profile: [key for key in order if key != "speed_limit_actions"] if type(order) is list else order
                              for profile, order in value["widgetOrder"].items()}
  # C4 has one shared camera panel; accept the old pair without changing C3.
  compact = value.get("layouts", {}).get("compact") if type(value.get("layouts")) is dict else None
  if type(compact) is dict and "pip_right" in compact:
    value = copy.deepcopy(value)
    compact = value["layouts"]["compact"]
    right = compact.pop("pip_right")
    left = compact.get("pip_left")
    if any(type(item) is not dict or set(item) != {"x", "y", "enabled"} or type(item["enabled"]) is not bool or
           any(type(item[axis]) not in (int, float) or not math.isfinite(item[axis]) for axis in ("x", "y")) for item in (left, right)):
      raise ValueError("Invalid legacy camera placement")
    left["enabled"] = left["enabled"] or right["enabled"]
    colors = value.get("widgetColors", {}).get("compact") if type(value.get("widgetColors")) is dict else None
    if type(colors) is dict and colors.get("pip_right") == {}:
      colors.pop("pip_right")
    order = value.get("widgetOrder", {}).get("compact") if type(value.get("widgetOrder")) is dict else None
    if type(order) is list:
      value["widgetOrder"]["compact"] = [key for key in order if key != "pip_right"]
  fields = {"version", "palette", "layouts"} | ({"widgetColors"} if value["version"] >= 2 else set())
  if value["version"] >= 3:
    fields.add("roadColors")
  if value['version'] >= 4 and 'speedSources' in value:
    fields.add('speedSources')
    if type(value['speedSources']) is not bool:
      raise ValueError('Invalid speed source drawer preference')
  if value['version'] >= 4 and 'speedSourcesCard' in value:
    fields.add('speedSourcesCard')
    if type(value['speedSourcesCard']) is not bool:
      raise ValueError('Invalid speed source card preference')
  if value['version'] >= 4 and 'speedAlign' in value:
    fields.add('speedAlign')
    if value['speedAlign'] not in SPEED_ALIGNMENTS:
      raise ValueError('Invalid speed card alignment')
  if value['version'] >= 4 and 'widgetOrder' in value:
    fields.add('widgetOrder')
  if 'clock24Hour' in value:
    fields.add('clock24Hour')
    if type(value['clock24Hour']) is not bool:
      raise ValueError('Invalid clock format preference')
  if 'reduceMotion' in value:
    fields.add('reduceMotion')
    if type(value['reduceMotion']) is not bool:
      raise ValueError('Invalid motion preference')
  if 'showMaxSpeed' in value:
    fields.add('showMaxSpeed')
    if type(value['showMaxSpeed']) is not bool:
      raise ValueError('Invalid MAX speed preference')
  if 'speedDisplayOffset' in value:
    fields.add('speedDisplayOffset')
    adjustment = value['speedDisplayOffset']
    if type(adjustment) not in (int, float) or not math.isfinite(adjustment) or not -5 <= adjustment <= 5:
      raise ValueError('Invalid speed display adjustment')
  if set(value) != fields:
    raise ValueError("Invalid customization fields")
  palette, layouts = value["palette"], value["layouts"]
  if type(palette) is not dict or set(palette) != set(PALETTE) or type(layouts) is not dict or set(layouts) != set(PROFILES):
    raise ValueError("Invalid customization fields")
  added_personality = type(layouts['large']) is dict and 'following_distance' not in layouts['large']
  if added_personality:
    layouts = copy.deepcopy(layouts)
    layouts['large']['following_distance'] = dict(PROFILES['large']['widgets']['following_distance']['default'])
  if any(all(type(layouts[profile]) is dict and
             (set(layouts[profile]) - {"vasm", *OPTIONAL_WIDGETS, *CAMERA_WIDGETS}) == keys
             for profile, keys in shape.items())
         for shape in (LEGACY_WIDGETS, DM_WIDGETS)):
    layouts = copy.deepcopy(layouts)
    for profile in PROFILES:
      for key in ("driver_monitor", "torque_bar"):
        layouts[profile].setdefault(key, dict(PROFILES[profile]["widgets"][key]["default"]))
  if all(type(layouts[profile]) is dict and (set(layouts[profile]) - {"vasm", *OPTIONAL_WIDGETS, *CAMERA_WIDGETS}) == keys
         for profile, keys in RAILLESS_WIDGETS.items()):
    layouts = copy.deepcopy(layouts)
    for key in RAIL_WIDGETS:
      layouts["compact"][key] = dict(PROFILES["compact"]["widgets"][key]["default"])
  if all(type(layouts[profile]) is dict and (set(layouts[profile]) - {"vasm", *OPTIONAL_WIDGETS}) == keys
         for profile, keys in CAMERALESS_WIDGETS.items()):
    layouts = copy.deepcopy(layouts)
    for profile in PROFILES:
      has_legacy = "vasm" in layouts[profile]
      legacy = layouts[profile].pop("vasm", None)
      if has_legacy and (type(legacy) is not dict or set(legacy) != {"x", "y", "enabled"} or
                                 type(legacy["enabled"]) is not bool or
                                 any(type(legacy[axis]) not in (int, float) or not math.isfinite(legacy[axis]) for axis in ("x", "y"))):
        raise ValueError("Invalid legacy camera placement")
      for side, key in enumerate(camera_widgets(profile)):
        widget = PROFILES[profile]["widgets"][key]
        position = dict(widget["default"])
        if legacy is not None:
          bounds = widget_bounds(profile, key)
          shift = side * widget["width"] if profile == "large" else 0
          position.update(enabled=legacy["enabled"],
                          x=max(bounds["x"], min(legacy["x"], bounds["x"] + bounds["width"] - 2 * widget["width"])) + shift,
                          y=max(bounds["y"], min(legacy["y"], bounds["y"] + bounds["height"] - widget["height"])))
        layouts[profile][key] = position
  layouts = copy.deepcopy(layouts)
  for profile, data in PROFILES.items():
    if type(layouts[profile]) is dict:
      monitor = layouts[profile].get('driver_monitor')
      if type(monitor) is dict and 'size' not in monitor:
        # Tighten the old 192px box around its 128px icon without moving the icon.
        if profile == 'large':
          for axis in ('x', 'y'):
            if type(monitor.get(axis)) in (int, float):
              monitor[axis] += 32
        monitor['size'] = DM_SIZES[profile][1]
      if (profile == 'large' and added_personality and type(monitor) is dict and
          (monitor.get('x'), monitor.get('y'), monitor.get('size')) == (120, 840, 128)):
        monitor['x'] = data['widgets']['driver_monitor']['default']['x']
      missing = set(data["widgets"]) - set(layouts[profile])
      if missing and missing <= OPTIONAL_WIDGETS:
        for optional in missing:
          layouts[profile][optional] = dict(data["widgets"][optional]["default"])
  if value['version'] < 11:
    old_defaults = {'large': {CEM_GAUGE_WIDGET: (56, 452)}, 'compact': {'clock': (168, 92), MODE_WIDGET: (162, 22)}}
  result = default_document()
  for key, color in palette.items():
    if type(color) is not str or re.fullmatch(r"#[0-9a-fA-F]{8}", color) is None:
      raise ValueError("Colors must be #RRGGBBAA")
    result["palette"][key] = color.upper()
  if value["version"] >= 2:
    colors = value["widgetColors"]
    if type(colors) is not dict or set(colors) != set(PROFILES):
      raise ValueError("Invalid widget color profiles")
    for profile, widgets in colors.items():
      if type(widgets) is not dict or not widgets.keys() <= WIDGET_COLORS[profile].keys():
        raise ValueError("Invalid widget color IDs")
      for key, overrides in widgets.items():
        if type(overrides) is not dict or not overrides.keys() <= WIDGET_COLORS[profile][key].keys():
          raise ValueError("Invalid widget color fields")
        if any(type(color) is not str or re.fullmatch(r"#[0-9a-fA-F]{8}", color) is None for color in overrides.values()):
          raise ValueError("Colors must be #RRGGBBAA")
        if overrides:
          result["widgetColors"][profile][key] = {field: color.upper() for field, color in overrides.items()}
  if value["version"] >= 3:
    colors = value["roadColors"]
    if type(colors) is not dict or set(colors) != set(PROFILES):
      raise ValueError("Invalid road color profiles")
    for profile, overrides in colors.items():
      if type(overrides) is not dict or not overrides.keys() <= ROAD_COLORS.keys() | {"pathMode", "leadMarker"}:
        raise ValueError("Invalid road color fields")
      for key, color in overrides.items():
        if key == "pathMode":
          if type(color) is not str or color not in PATH_MODES:
            raise ValueError("Invalid path color mode")
        elif type(color) is not str or re.fullmatch(r"#[0-9a-fA-F]{8}", color) is None:
          raise ValueError("Colors must be #RRGGBBAA")
        result["roadColors"][profile][key] = color if key == "pathMode" else color.upper()
  for profile, data in PROFILES.items():
    layout = layouts[profile]
    if type(layout) is not dict or set(layout) != set(data["widgets"]):
      raise ValueError("Invalid widget IDs")
    for key, widget in data["widgets"].items():
      placement = layout[key]
      legacy_signal = key == SIGNAL_WIDGET and type(placement) is dict and 'bluetooth' not in placement
      if key == SIGNAL_WIDGET and type(placement) is dict and any(field not in placement for field in ('size', 'scale', 'bluetooth', 'wifiName')):
        placement = dict(placement)
        placement.setdefault('size', 'ring')
        placement.setdefault('scale', SIGNAL_SCALE_RANGE[2])
        placement.setdefault('bluetooth', True)
        placement.setdefault('wifiName', False)
        layout[key] = placement
      if key == UNIDEN_WIDGET and type(placement) is dict:
        placement = layout[key] = uniden_display(placement)
      bounds = widget_bounds(profile, key)
      if value['version'] < 8 and key in SPEED_LAYOUT_V7[profile]:
        bounds = data['bounds']
      if legacy_signal:
        _fit_legacy_signal(placement, bounds)
      if value["version"] < 17 and profile == "compact" and key in CAMERA_WIDGETS and type(placement) is dict:
        # Move the legacy 80px widget into the shared C4 panel.
        if any(type(placement.get(axis)) not in (int, float) or not math.isfinite(placement[axis]) for axis in ("x", "y")):
          raise ValueError("Invalid widget position")
        if key in value["layouts"][profile] and not (0 <= placement["x"] <= bounds["width"] - 80 and
                                                    0 <= placement["y"] <= bounds["height"] - 80):
          raise ValueError("Invalid legacy camera position")
        placement = layout[key] = {**placement, "x": 0, "y": 0}
      if (key == JETLINK_WIDGET and type(placement) is dict and type(placement.get('y')) in (int, float) and
          math.isfinite(placement['y']) and
          bounds['y'] + bounds['height'] - JETLINK_SIZE[1] < placement['y'] <= bounds['y'] + bounds['height'] - 84):
        placement['y'] = bounds['y'] + bounds['height'] - JETLINK_SIZE[1]
      scalable = key == 'driver_monitor' or key == 'steering_wheel' and value['version'] >= 4
      expected = {"x", "y", "enabled"} | ({"size"} if scalable else set())
      has_rotation = widget.get('rotateToggle') and type(placement) is dict and 'rotate' in placement
      if has_rotation:
        expected.add('rotate')
      if widget.get("box"):
        expected |= {"width", "height", "opacity"}
      elif widget.get("opacity"):
        expected.add("opacity")
      if widget.get("sizes"):
        expected.add("size")
      if key == SIGNAL_WIDGET:
        expected |= {"scale", "bluetooth", "wifiName"}
      if key == UNIDEN_WIDGET:
        expected |= UNIDEN_DISPLAY_FIELDS
        if type(placement) is not dict or not valid_uniden_display(placement):
          raise ValueError("Invalid Uniden display settings")
      if type(placement) is not dict or set(placement) != expected or type(placement["enabled"]) is not bool:
        raise ValueError("Invalid widget placement")
      if has_rotation and type(placement['rotate']) is not bool:
        raise ValueError('Invalid wheel rotation setting')
      if widget.get("box"):
        validate_map_box(placement, widget)
      elif widget.get("opacity"):
        low, high = widget["opacity"]["min"], widget["opacity"]["max"]
        if type(placement["opacity"]) is not int or not low <= placement["opacity"] <= high:
          raise ValueError("Invalid widget opacity")
      if widget.get("sizes") and (type(placement["size"]) is not str or placement["size"] not in widget["sizes"]):
        raise ValueError("Invalid widget style")
      if key == SIGNAL_WIDGET and not valid_signal_scale(placement):
        raise ValueError("Invalid widget scale")
      if key == SIGNAL_WIDGET and type(placement["bluetooth"]) is not bool:
        raise ValueError("Invalid widget bluetooth setting")
      if key == SIGNAL_WIDGET and type(placement["wifiName"]) is not bool:
        raise ValueError("Invalid widget Wi-Fi name setting")
      size = WHEEL_SIZES[profile][1]
      if scalable:
        minimum, _, maximum = WIDGET_SIZES[key][profile]
        if type(placement["size"]) is not int or not minimum <= placement["size"] <= maximum:
          raise ValueError("Invalid widget size")
        size = placement["size"]
      # Only a gauge the saved layout actually placed moves; one filled in from defaults is already in the new box.
      saved_layout = value.get("layouts", {}).get(profile) if type(value.get("layouts")) is dict else None
      old_gauge = key == CEM_GAUGE_WIDGET and value["version"] < 7 and type(saved_layout) is dict and key in saved_layout
      for axis, extent in (("x", "width"), ("y", "height")):
        number = placement[axis]
        legacy = (176, 483) if value['version'] < 5 else (280, 412)
        dimensions = legacy if value['version'] < 6 and profile == 'large' and key == 'cruise_limits' else None
        if dimensions is None and value['version'] < 8:
          dimensions = SPEED_LAYOUT_V7[profile].get(key)
        if value['version'] == 8 and profile == 'compact':
          dimensions = COMPACT_SPEED_LAYOUT_V8.get(key)
        if dimensions is None and value['version'] < 10 and profile == 'large' and key == 'cruise_limits':
          dimensions = (WIDTH, 132)
        if dimensions is None and value['version'] < 13:
          if profile == 'large' and key == 'cruise_limits':
            dimensions = (WIDTH, 168)
          elif profile == 'compact' and key == 'speed_limit':
            dimensions = (118, 132)
        dimension = (dimensions[0 if axis == 'x' else 1] if dimensions else
                     size if key in WIDGET_SIZES else placement[extent] if widget.get("box") else
                     signal_size(placement)[0 if axis == 'x' else 1] if key == SIGNAL_WIDGET else
                     widget["sizes"][str(placement["size"])][0 if axis == 'x' else 1] if widget.get("sizes") else widget[extent])
        if old_gauge:
          dimension = CEM_GAUGE_FRAME[0 if axis == 'x' else 1]
        if type(number) not in (int, float) or not math.isfinite(number) or not bounds[axis] <= number <= bounds[axis] + bounds[extent] - dimension:
          raise ValueError("Widget outside profile bounds")
      result["layouts"][profile][key] = {**placement, **({"size": size} if key in WIDGET_SIZES else {})}
      if old_gauge:
        result["layouts"][profile][key].update(x=placement["x"] + CEM_GAUGE_INSET[0], y=placement["y"] + CEM_GAUGE_INSET[1])
    if value['version'] < 11:
      for key, old in old_defaults[profile].items():
        saved = result['layouts'][profile][key]
        if (saved['x'], saved['y']) == old:
          default = data['widgets'][key]['default']
          saved.update(x=default['x'], y=default['y'])
  if 'widgetOrder' in value:
    orders = value['widgetOrder']
    if type(orders) is not dict or not orders.keys() <= PROFILES.keys():
      raise ValueError("Invalid widget layer profiles")
    result['widgetOrder'] = {}
    for profile, order in orders.items():
      if type(order) is list:
        order = [*order, *(key for key in PROFILES[profile]['widgets']
                          if (key in OPTIONAL_WIDGETS or profile == 'large' and key == 'following_distance') and key not in order)]
      result['widgetOrder'][profile] = validate_widget_order(order, PROFILES[profile]['widgets'])
  if 'speedSources' in value:
    result['speedSources'] = value['speedSources']
  if 'speedSourcesCard' in value:
    result['speedSourcesCard'] = value['speedSourcesCard']
  if 'speedAlign' in value:
    result['speedAlign'] = value['speedAlign']
  result['reduceMotion'] = value.get('reduceMotion', False)
  if 'clock24Hour' in value:
    result['clock24Hour'] = value['clock24Hour']
  if 'showMaxSpeed' in value:
    result['showMaxSpeed'] = value['showMaxSpeed']
  if 'speedDisplayOffset' in value:
    result['speedDisplayOffset'] = value['speedDisplayOffset']
  if value['version'] < 10:
    migrate_large_sign_layout(result['layouts']['large'], PROFILES['large']['bounds'], extra_height=sources_extent(result))
  if value['version'] < 8:
    if value['version'] < 5 and value['palette'] != PALETTE:
      colors = result['widgetColors']['large'].setdefault('cruise_limits', {})
      for key in ('cardFill', 'cardBorder'):
        colors.setdefault(key, value['palette'][key])
    for profile in PROFILES:
      migrate_speed_layout(result['layouts'][profile], profile, PROFILES[profile]['bounds'])
  elif value['version'] == 8:
    migrate_speed_layout(result['layouts']['compact'], 'compact', PROFILES['compact']['bounds'], previous=COMPACT_SPEED_LAYOUT_V8)
  if value['version'] < 11:
    for profile, data in PROFILES.items():
      migrate_confirmation_layout(result['layouts'][profile], profile, data['widgets'], data['bounds'],
                                  extra_height=sources_extent(result) if profile == 'large' else 0)
    return validate_document(result)
  for profile, data in PROFILES.items():
    if value['version'] < 13:
      if profile == 'compact':
        sign = result['layouts'][profile]['speed_limit']
        if (sign['x'], sign['y']) == (330, 20):
          sign.update(x=LIMIT_RECTS['compact'][0], y=LIMIT_RECTS['compact'][1])
    if value['version'] < 16:
      migrate_confirmation_layout(result['layouts'][profile], profile, data['widgets'], data['bounds'],
                                  extra_height=sources_extent(result) if profile == 'large' else 0)
    elif not confirmation_layout_valid(result['layouts'][profile], profile, data['widgets'], data['bounds'],
                                       extra_height=sources_extent(result) if profile == 'large' else 0):
      raise ValueError('Speed-limit confirmation overlaps set speed, torque, or screen bounds')
  if len(json.dumps(result, separators=(",", ":")).encode()) > MAX_BYTES:
    raise ValueError("Customization too large")
  return result


def _unique_object(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError("Duplicate customization field")
    result[key] = value
  return result


@lru_cache(maxsize=2)
def _validated_document(raw: bytes | str):
  return validate_document(json.loads(raw, object_pairs_hook=_unique_object))


def decode_document(raw):
  if isinstance(raw, (bytes, str)) and len(raw) <= MAX_BYTES:
    return copy.deepcopy(_validated_document(raw))
  return validate_document(json.loads(raw, object_pairs_hook=_unique_object))


def read_customization(params):
  try:
    raw, readable = read_saved(params, PARAM_KEY, MAX_BYTES)
    return decode_document(raw) if readable and raw is not None else default_document()
  except (AttributeError, OSError, TypeError, ValueError, UnicodeError, RecursionError):
    return default_document()


def placement(document, profile, widget):
  return document['layouts'][str(profile)][widget]


def fit_to_road(document, viewport_width):
  """The document with expandable Big-screen widgets kept inside the road that is showing."""
  layout = document["layouts"].get("large")
  if layout is None:
    return document
  extra, moved = viewport_width - ROAD_BASE_WIDTH, {}
  for key, widget in PROFILES["large"]["widgets"].items():
    placed = layout.get(key)
    if placed is None or not widget.get("expandable"):
      continue
    shift = widget.get("anchorShift", 0) * extra
    width = widget_size(document, "large", key)[0]
    if (area := widget.get('confirmation')) and placed['enabled']:
      width = max(width, area['x'] + area['width'])
    x = max(30 - shift, min(placed["x"], viewport_width - 30 - width - shift))
    if x != placed["x"]:
      moved[key] = {**placed, "x": x}
  if not moved:
    return document
  return {**document, "layouts": {**document["layouts"], "large": {**layout, **moved}}}


def widget_size(document, profile, widget):
  default = PROFILES[str(profile)]["widgets"][widget]
  position = placement(document, profile, widget)
  if widget == "cruise_limits":
    return default["width"], default["height"] + sources_extent(document)
  if widget == UNIDEN_WIDGET and type(position.get("scale")) is int:
    return scaled_size(default["width"], default["height"], position["scale"])
  if default.get("box"):
    return position["width"], position["height"]
  if widget == SIGNAL_WIDGET:
    return signal_size(position)
  return (position.get("size", WIDGET_SIZES[widget][str(profile)][1]),) * 2 if widget in WIDGET_SIZES else (default["width"], default["height"])


def offset(document, profile, widget):
  position = placement(document, profile, widget)
  default = PROFILES[str(profile)]["widgets"][widget]["default"]
  return position["x"] - default["x"], position["y"] - default["y"]


def widget_palette(document, profile, widget):
  defaults = WIDGET_COLORS[str(profile)].get(widget, {})
  colors = {key: document["palette"][key] if value is None else value for key, value in defaults.items()}
  colors.update(document.get("widgetColors", {}).get(str(profile), {}).get(widget, {}))
  return colors


def rgba(document, key, profile=None, widget=None):
  if profile is None:
    color = document["palette"][key]
  else:
    assert widget is not None
    default = WIDGET_COLORS[str(profile)][widget][key]
    color = document.get("widgetColors", {}).get(str(profile), {}).get(widget, {}).get(key, default or document["palette"][key])
  return _rgba_value(color)


@lru_cache(maxsize=256)
def _rgba_value(color):
  return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5, 7))


def fit_road_snapshot(snapshot, width):
  state = snapshot.onroad
  fitted = fit_to_road(state.customization, width)
  if state.viewport_width != width or fitted is not state.customization:
    return replace(snapshot, onroad=replace(state, viewport_width=width, customization=fitted))
  return snapshot
