"""Parked, source-bound editor for the opt-in native display preferences."""

from collections.abc import Callable
from dataclasses import replace
import math

from openpilot.starpilot.ui.display_preferences import (
  AUTO, GPU_LOADING, BRIGHTNESS, CUSTOM_KEYS, KEYS, MASTER, SCREEN_OFF, SCREEN_OFF_DESCRIPTION, STATIC_DIM, STATIC_DIM_DESCRIPTION,
  STATIC_DIM_STRENGTH, STATIC_DIM_STRENGTH_DESCRIPTION, STATIC_DIM_STRENGTHS, SWITCHES, default_value, read_choice,
)
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, FeatureSettingsState
from openpilot.starpilot.ui.presentation import Profile


LABELS = {
  MASTER: "Custom Display Settings",
  "ScreenBrightness": "Parked Brightness",
  "ScreenBrightnessOnroad": "Driving Brightness",
  "ScreenTimeout": "Parked Screen Timeout",
  "ScreenTimeoutOnroad": "Driving Interaction Timeout",
  SCREEN_OFF: "Turn Off Comma Screen",
  GPU_LOADING: "Keep Screen On While eGPU Loads",
  STATIC_DIM: "Burn In Protection",
  STATIC_DIM_STRENGTH: "Static Pixel Dimming",
}
STANDALONE = (SCREEN_OFF, GPU_LOADING, STATIC_DIM)   # switches that work without Custom Display Settings
AUTO_PREFIX = "display:auto:"
BRIGHTNESS_CHOICES = ("Auto",) + tuple(f"{number}%" for number in range(5, 101))
TIMEOUT_CHOICES = tuple(f"{number} s" for number in range(5, 61, 5))


def _display(key: str, value: int | None, valid: bool, unsupported: bool) -> str:
  if unsupported:
    return "Saved Off (unsupported)"
  if not valid or value is None:
    return "Invalid saved choice"
  if key in SWITCHES:
    return "On" if value else "Off"
  if key in BRIGHTNESS:
    return "Auto" if value == AUTO else str(value)
  return str(value)


class DisplayOwner:
  def __init__(self, params, parked: Callable[[], bool], *, gpu_capable=None, burn_in_capable=None):
    from openpilot.common.hardware import HARDWARE
    self.gpu_capable = HARDWARE.get_device_type() in ("tici", "mici") if gpu_capable is None else bool(gpu_capable)
    # Burn In Protection is for the comma 4 only for now: it copies the picture off the display a few times a second,
    # which is cheap for that small screen and not yet proven on the big one.
    self.burn_in_capable = HARDWARE.get_device_type() == "mici" if burn_in_capable is None else bool(burn_in_capable)
    self.params = params
    self.parked = parked

  def snapshot(self, profile: Profile) -> FeatureSettingsState:
    large = profile == Profile.LARGE
    parked = self.parked()
    saved = {key: read_choice(self.params, key, large=large) for key in KEYS}
    master = saved[MASTER]
    dim = saved[STATIC_DIM]
    rows = []
    for key in KEYS:
      if key == GPU_LOADING and not self.gpu_capable:
        continue
      if key in (STATIC_DIM, STATIC_DIM_STRENGTH) and (large or not self.burn_in_capable):
        continue   # comma 4 only
      choice = saved[key]
      valid = choice.valid
      value = _display(key, choice.value, valid, choice.unsupported)
      if not choice.readable:
        reason = "Saved choice cannot be read"
      elif choice.unsupported:
        reason = "Choose Auto before enabling custom display"
      elif not valid:
        reason = "Choose default to repair"
      elif key in (SCREEN_OFF, GPU_LOADING):
        reason = (SCREEN_OFF_DESCRIPTION if key == SCREEN_OFF else
                  "Keep the driving screen awake while an eGPU model loads. Manual Screen Off still takes priority.")
      elif key == STATIC_DIM:
        reason = STATIC_DIM_DESCRIPTION
      elif key == STATIC_DIM_STRENGTH:
        reason = STATIC_DIM_STRENGTH_DESCRIPTION if dim.value == 1 else "Saved; Burn In Protection is off"
      elif key != MASTER and master.value != 1:
        reason = "Saved; custom display is off"
      elif key == "ScreenTimeoutOnroad":
        reason = "Returns to driving view; turns off screen if Turn Off Comma Screen is on"
      else:
        reason = ""
      boolean = key in SWITCHES
      strength = key == STATIC_DIM_STRENGTH
      repair = "Off" if boolean else "40%" if strength else "Auto" if key in BRIGHTNESS else "30 s" if key == "ScreenTimeout" else \
        "10 s" if large else "5 s"
      dependencies = (() if key in STANDALONE else ((STATIC_DIM, dim.raw),) if strength else
                      tuple((name, saved[name].raw) for name in CUSTOM_KEYS) if key == MASTER else ((MASTER, master.raw),))
      parent = dim if strength else master
      editable = parked and choice.readable and (boolean or parent.valid and parent.value == 1 or not valid)
      numeric = valid and not boolean and choice.value != AUTO
      rows.append(FeatureRow(key, LABELS[key], value, source=choice.raw,
                             choices=("Off", "On") if boolean else
                                     (("Auto",) if numeric else BRIGHTNESS_CHOICES) if key in BRIGHTNESS else (),
                             step=(1.0 if key in BRIGHTNESS else 5.0) if valid and not boolean else 0.0,
                             minimum=5.0,
                             maximum=100.0 if key in BRIGHTNESS else float(STATIC_DIM_STRENGTHS[-1]) if strength else 60.0,
                             unit=("%" if key in BRIGHTNESS or strength else "s") if valid and not boolean else "",
                             available=editable, reason=reason,
                             repair_value=repair if not valid and choice.readable else "",
                             dependencies=dependencies))
      if key in BRIGHTNESS and valid and choice.value != AUTO:
        rows.append(FeatureRow(AUTO_PREFIX + key, "Use Auto " + LABELS[key], "Fixed level saved",
                               source=choice.raw, available=editable,
                               repair_value="Auto", dependencies=((MASTER, master.raw),)))
    defaults = {key: ('Off' if key in SWITCHES else 'Auto' if key in BRIGHTNESS else str(default_value(key, large=large)))
                for key in KEYS}
    rows = tuple(replace(row, default_value=defaults[row.key]) if row.key in defaults else row for row in rows)
    return FeatureSettingsState(page="display", title="Display", subtitle="Saved brightness and interaction timing.",
                                rows=tuple(rows), parked=parked)

  def apply(self, request: FeatureSettingsRequest) -> bool:
    key = request.key.removeprefix(AUTO_PREFIX)
    if key not in KEYS or not self.parked() or key == GPU_LOADING and not self.gpu_capable:
      return False
    if key in (STATIC_DIM, STATIC_DIM_STRENGTH) and not self.burn_in_capable:
      return False
    if request.key.startswith(AUTO_PREFIX) and key not in BRIGHTNESS:
      return False
    if key in SWITCHES:
      if request.value not in ("Off", "On"):
        return False
      desired = int(request.value == "On")
    elif key == STATIC_DIM_STRENGTH:
      try:
        number = float(request.value.removesuffix("%"))
      except ValueError:
        return False
      if not math.isfinite(number) or number not in STATIC_DIM_STRENGTHS:
        return False
      desired = int(number)
    elif key in BRIGHTNESS:
      if request.value == "Auto":
        desired = AUTO
      else:
        try:
          number = float(request.value.removesuffix("%"))
        except ValueError:
          return False
        if not math.isfinite(number) or not number.is_integer() or not 5 <= number <= 100:
          return False
        desired = int(number)
    else:
      try:
        number = float(request.value.removesuffix(" s"))
      except ValueError:
        return False
      if not math.isfinite(number) or not number.is_integer() or int(number) % 5 or not 5 <= number <= 60:
        return False
      desired = int(number)
    for final in (False, True):
      if not self.parked():
        return False
      observed = read_choice(self.params, key, large=True)
      if not observed.readable or observed.raw != request.expected:
        return False
      # Turning the master off stays available even when other saved choices
      # are corrupt or have changed while the user was viewing the page.
      if key not in STANDALONE and not (key == MASTER and desired == 0):
        expected = dict(request.dependencies)
        required = set(CUSTOM_KEYS) if key == MASTER else {STATIC_DIM} if key == STATIC_DIM_STRENGTH else {MASTER}
        if set(expected) != required:
          return False
        for name in required:
          source = read_choice(self.params, name, large=True)
          if not source.readable or source.raw != expected[name] or (key == MASTER and not source.valid):
            return False
          if key != MASTER and observed.valid and (not source.valid or source.value != 1):
            return False
      if not final:
        continue
    try:
      if key in SWITCHES:
        self.params.put_bool(key, bool(desired), block=True)
      else:
        self.params.put(key, desired, block=True)
    except (OSError, KeyError, TypeError, ValueError):
      return False
    result = read_choice(self.params, key, large=True)
    return result.readable and result.valid and result.value == desired
