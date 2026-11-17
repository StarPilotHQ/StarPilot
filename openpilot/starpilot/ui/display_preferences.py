"""Strict saved display choices; absent/invalid values preserve native policy."""

from dataclasses import dataclass
import os
import stat
from typing import Any


MASTER = "StarPilotDisplayPreferencesEnabled"
BRIGHTNESS = ("ScreenBrightness", "ScreenBrightnessOnroad")
TIMEOUTS = ("ScreenTimeout", "ScreenTimeoutOnroad")
GPU_LOADING = "KeepScreenOnGpuLoading"
SCREEN_OFF = "TurnOffCommaScreen"
STATIC_DIM = "DimStaticPixels"
STATIC_DIM_STRENGTH = "DimStaticPixelsStrength"
CUSTOM_KEYS = (*BRIGHTNESS, *TIMEOUTS)
KEYS = (MASTER, *CUSTOM_KEYS, SCREEN_OFF, GPU_LOADING, STATIC_DIM, STATIC_DIM_STRENGTH)
SWITCHES = (MASTER, SCREEN_OFF, GPU_LOADING, STATIC_DIM)
STATIC_DIM_STRENGTHS = tuple(range(5, 55, 5))   # percent taken off: 5 to 50 in steps of 5
SCREEN_OFF_DESCRIPTION = "Pauses Comma UI rendering to save CPU/GPU. Tap once to wake."
STATIC_DIM_DESCRIPTION = "Dims any static areas of the display to reduce burn in on the comma screen."
STATIC_DIM_STRENGTH_DESCRIPTION = "How much brightness is taken off a pixel that has stopped changing. Higher means more dimming on static elements."
MAX_RAW_BYTES = 16
AUTO = 101


@dataclass(frozen=True)
class SavedChoice:
  raw: bytes | None
  value: int | None
  readable: bool = True
  unsupported: bool = False

  @property
  def valid(self) -> bool:
    return self.readable and self.value is not None and not self.unsupported


def default_value(key: str, *, large: bool) -> int:
  if key in SWITCHES:
    return 0
  if key == STATIC_DIM_STRENGTH:
    return 40
  if key in BRIGHTNESS:
    return AUTO
  if key == "ScreenTimeout":
    return 30
  if key == "ScreenTimeoutOnroad":
    return 10 if large else 5
  raise KeyError(key)


def read_choice(params: Any, key: str, *, large: bool) -> SavedChoice:
  if key not in KEYS:
    raise KeyError(key)
  try:
    descriptor = os.open(params.get_param_path(key), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
  except FileNotFoundError:
    return SavedChoice(None, default_value(key, large=large))
  except (AttributeError, OSError, TypeError, ValueError):
    return SavedChoice(None, None, False)
  try:
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
      return SavedChoice(None, None, False)
    raw = os.read(descriptor, MAX_RAW_BYTES + 1)
  except OSError:
    return SavedChoice(None, None, False)
  finally:
    os.close(descriptor)
  if len(raw) > MAX_RAW_BYTES:
    return SavedChoice(raw, None, False)
  if not raw or not raw.isascii() or not raw.isdigit() or (len(raw) > 1 and raw[0] == ord("0")):
    return SavedChoice(raw, None)
  value = int(raw)
  if key in SWITCHES:
    return SavedChoice(raw, value if value in (0, 1) else None)
  if key == STATIC_DIM_STRENGTH:
    return SavedChoice(raw, value if value in STATIC_DIM_STRENGTHS else None)
  if key in BRIGHTNESS:
    return SavedChoice(raw, value if 5 <= value <= 100 or value in (0, AUTO) else None,
                       unsupported=value == 0)
  return SavedChoice(raw, value if 5 <= value <= 60 and value % 5 == 0 else None)


@dataclass(frozen=True)
class DisplayPreferences:
  keep_screen_on_gpu_loading: bool = False
  enabled: bool = False
  turn_off_screen: bool = False
  parked_brightness: int = AUTO
  driving_brightness: int = AUTO
  parked_timeout: int = 30
  driving_timeout: int = 10
  static_dim: bool = False
  static_dim_strength: int = 40


def read_preferences(params: Any, *, large: bool) -> DisplayPreferences:
  gpu = read_choice(params, GPU_LOADING, large=large)
  keep_gpu = gpu.valid and gpu.value == 1
  master = read_choice(params, MASTER, large=large)
  screen_off = read_choice(params, SCREEN_OFF, large=large)
  turn_off_screen = screen_off.valid and screen_off.value == 1
  dim = read_choice(params, STATIC_DIM, large=large)
  strength = read_choice(params, STATIC_DIM_STRENGTH, large=large)
  static = {"static_dim": dim.valid and dim.value == 1,
            "static_dim_strength": strength.value if strength.valid and strength.value is not None else 40}
  if not master.valid or master.value != 1:
    return DisplayPreferences(keep_screen_on_gpu_loading=keep_gpu, turn_off_screen=turn_off_screen,
                              driving_timeout=default_value("ScreenTimeoutOnroad", large=large), **static)
  values = {key: read_choice(params, key, large=large) for key in CUSTOM_KEYS}
  def effective(key: str) -> int:
    saved = values[key]
    return saved.value if saved.valid and saved.value is not None else default_value(key, large=large)
  return DisplayPreferences(keep_screen_on_gpu_loading=keep_gpu, enabled=True, turn_off_screen=turn_off_screen,
                            parked_brightness=effective("ScreenBrightness"),
                            driving_brightness=effective("ScreenBrightnessOnroad"),
                            parked_timeout=effective("ScreenTimeout"),
                            driving_timeout=effective("ScreenTimeoutOnroad"), **static)
