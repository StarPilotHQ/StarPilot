import json
import pyray as rl
import numpy as np
import time
import threading
from collections.abc import Callable
from enum import Enum
from cereal import messaging, car, log
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.lib.prime_state import PrimeState
from openpilot.selfdrive.ui.lib.ui_param_cache import shared_ui_params
from openpilot.system.ui.lib.application import gui_app, RECORD_METRIC
from openpilot.starpilot.common.lateral_only_experimental import lateral_only_experimental_available
from openpilot.starpilot.common.car_params_capability import capability_car_params_bytes
from openpilot.system.hardware import HARDWARE, PC
from openpilot.starpilot.common.screen_settings import (
  alert_wake_key, brightness_preferences, calculate_screen_brightness, enabled_wake_keys, standby_button_press_time,
  screen_off_toggle_counter,
)

BACKLIGHT_OFFROAD = 65 if HARDWARE.get_device_type() == "mici" else 50


def _noop_progress(_phase: str) -> None:
  pass


class UIStatus(Enum):
  DISENGAGED = "disengaged"
  ENGAGED = "engaged"
  OVERRIDE = "override"


class UIState:
  _instance: 'UIState | None' = None

  def __new__(cls):
    if cls._instance is None:
      cls._instance = super().__new__(cls)
      cls._instance._initialize()
    return cls._instance

  def _initialize(self):
    self.params = Params()
    self.ui_params = shared_ui_params()
    self.params_memory = Params(memory=True)
    self.sm = messaging.SubMaster(
      [
        "modelV2",
        "controlsState",
        "onroadEvents",
        "liveCalibration",
        "radarState",
        "deviceState",
        "pandaStates",
        "carParams",
        "driverMonitoringState",
        "carState",
        "driverStateV2",
        "roadCameraState",
        "wideRoadCameraState",
        "managerState",
        "selfdriveState",
        "longitudinalPlan",
        "gpsLocationExternal",
        "mapdOut",
        "carOutput",
        "carControl",
        "liveParameters",
        "rawAudioData",
        "starpilotCarState",
        "starpilotPlan",
        "starpilotRadarState",
        "starpilotSelfdriveState",
        "liveTracks",
        "liveDelay",
        "liveTorqueParameters",
      ],
      drain_services=["carState"],
    )

    self.prime_state = PrimeState()

    # UI Status tracking
    self.status: UIStatus = UIStatus.DISENGAGED
    self.started_frame: int = 0
    self.started_time: float = 0.0
    self._engaged_prev: bool = False
    self._started_prev: bool = False

    # Core state variables
    self.is_metric: bool = RECORD_METRIC or self.params.get_bool("IsMetric")
    self.is_release = self.params.get_bool("IsReleaseBranch")
    self.always_on_dm: bool = self.params.get_bool("AlwaysOnDM")
    self.usbgpu: bool = False
    self.usbgpu_compiled: bool = self.params.get_bool("UsbGpuCompiled")
    self.usbgpu_active: bool = self.params.get_bool("UsbGpuActive")
    self.usbgpu_loading: bool = self.params.get_bool("UsbGpuLoading")
    self.started: bool = False
    self.ignition: bool = False
    self.recording_audio: bool = False
    self.panda_type: log.PandaState.PandaType = log.PandaState.PandaType.unknown
    self.personality: log.LongitudinalPersonality = log.LongitudinalPersonality.standard
    self.has_longitudinal_control: bool = False
    self.experimental_mode_available: bool = False
    self.CP: car.CarParams | None = None
    self.light_sensor: float = -1.0
    self._param_update_time: float = 0.0
    self.always_on_lateral_active: bool = False
    self.switchback_mode_enabled: bool = False
    self.traffic_mode_enabled: bool = False
    self.conditional_status: int = 0
    self._last_starpilot_toggles: str = ""
    self.starpilot_toggles: dict = {
      "debug_mode": False,
      "driver_camera_in_reverse": False,
      "force_offroad": False,
      "force_onroad": False,
      "screen_brightness": 101,
      "screen_brightness_onroad": 101,
      "screen_timeout": 30,
      "screen_timeout_onroad": 10,
      "sidebar_color1": "#FFFFFFFF",
      "sidebar_color2": "#FFFFFFFF",
      "sidebar_color3": "#FFFFFFFF",
      "simple_mode": False,
      "standby_mode": False,
      "tethering_config": 0,
    }

    # Callbacks
    self._offroad_transition_callbacks: list[Callable[[], None]] = []
    self._engaged_transition_callbacks: list[Callable[[], None]] = []

    self.update_params()

  def add_offroad_transition_callback(self, callback: Callable[[], None]):
    self._offroad_transition_callbacks.append(callback)

  def remove_offroad_transition_callback(self, callback: Callable[[], None]):
    try:
      self._offroad_transition_callbacks.remove(callback)
    except ValueError:
      pass

  def add_engaged_transition_callback(self, callback: Callable[[], None]):
    self._engaged_transition_callbacks.append(callback)

  def _update_usbgpu_presence(self, present: bool) -> None:
    # Keep the eGPU UI active until the offroad transition if the dock drops out onroad.
    self.usbgpu = present or (self.usbgpu and self.started)

  @property
  def engaged(self) -> bool:
    return self.started and self.sm["selfdriveState"].enabled

  def is_onroad(self) -> bool:
    return self.started

  def is_offroad(self) -> bool:
    return not self.started

  def update(self, progress_hook: Callable[[str], None] | None = None) -> None:
    mark_progress = progress_hook or _noop_progress

    mark_progress("ui.update.before_prime_state")
    self.prime_state.start()  # start thread after manager forks ui
    mark_progress("ui.update.before_submaster")
    self.sm.update(0)
    mark_progress("ui.update.before_state")
    self._update_state(mark_progress)
    mark_progress("ui.update.before_status")
    self._update_status(mark_progress)
    mark_progress("ui.update.before_params")
    if time.monotonic() - self._param_update_time > 5.0:
      self.update_params()
    mark_progress("ui.update.before_device")
    device.update()
    mark_progress("ui.update.after_device")

  def _update_state(self, progress_hook: Callable[[str], None] | None = None) -> None:
    mark_progress = progress_hook or _noop_progress

    # Handle panda states updates
    if self.sm.updated["pandaStates"]:
      panda_states = self.sm["pandaStates"]

      if len(panda_states) > 0:
        # Get panda type from first panda
        self.panda_type = panda_states[0].pandaType
        # Check ignition status across all pandas
        if self.panda_type != log.PandaState.PandaType.unknown:
          self.ignition = any(state.ignitionLine or state.ignitionCan for state in panda_states)
    elif self.sm.frame - self.sm.recv_frame["pandaStates"] > 5 * rl.get_fps():
      self.panda_type = log.PandaState.PandaType.unknown

    # Handle wide road camera state updates
    if self.sm.updated["wideRoadCameraState"]:
      cam_state = self.sm["wideRoadCameraState"]
      self.light_sensor = max(100.0 - cam_state.exposureValPercent, 0.0)
    elif not self.sm.alive["wideRoadCameraState"] or not self.sm.valid["wideRoadCameraState"]:
      self.light_sensor = -1

    # Trust hardwared's filtered started state; raw ignition can flap on Toyota.
    mark_progress("ui.update.before_state_params")
    params = self.ui_params
    force_onroad = params.get_bool("ForceOnroad")
    force_offroad = params.get_bool("ForceOffroad")
    started = self.sm["deviceState"].started
    started |= force_onroad
    started &= not force_offroad
    self.started = started
    self._update_usbgpu_presence(self.sm["deviceState"].chestnutPresent)

    # Update recording audio state
    self.recording_audio = params.get_bool("RecordAudio") and self.started

    self.is_metric = RECORD_METRIC or params.get_bool("IsMetric")
    self.always_on_dm = params.get_bool("AlwaysOnDM")
    self.usbgpu_compiled = params.get_bool("UsbGpuCompiled")
    self.usbgpu_active = params.get_bool("UsbGpuActive")
    self.usbgpu_loading = params.get_bool("UsbGpuLoading")
    self.switchback_mode_enabled = self.params_memory.get_bool("SwitchbackModeEnabled") if self.started else False
    self.conditional_status = self.params_memory.get_int("CEStatus", default=0) if self.started else 0
    mark_progress("ui.update.after_state_params")
    if self.sm.valid.get("starpilotCarState", False):
      starpilot_car_state = self.sm["starpilotCarState"]
      self.always_on_lateral_active = (not self.sm["selfdriveState"].enabled and
                                       starpilot_car_state.alwaysOnLateralEnabled)
      self.traffic_mode_enabled = starpilot_car_state.trafficModeEnabled
    else:
      self.always_on_lateral_active = False
      self.traffic_mode_enabled = False

    if self.sm.updated["starpilotPlan"]:
      plan = self.sm["starpilotPlan"]
      toggles_str = plan.starpilotToggles
      if toggles_str and toggles_str != self._last_starpilot_toggles:
        try:
          parsed = json.loads(toggles_str)
          if isinstance(parsed, dict):
            self.starpilot_toggles.update(parsed)
            self._last_starpilot_toggles = toggles_str
        except Exception as e:
          cloudlog.warning(f"Error parsing starpilot_toggles: {e}")

    self.starpilot_toggles["force_offroad"] = force_offroad
    self.starpilot_toggles["force_onroad"] = force_onroad

  def _update_status(self, progress_hook: Callable[[str], None] | None = None) -> None:
    mark_progress = progress_hook or _noop_progress

    if self.started and self.sm.updated["selfdriveState"]:
      ss = self.sm["selfdriveState"]
      state = ss.state

      if state in (log.SelfdriveState.OpenpilotState.preEnabled, log.SelfdriveState.OpenpilotState.overriding):
        self.status = UIStatus.OVERRIDE
      else:
        self.status = UIStatus.ENGAGED if ss.enabled else UIStatus.DISENGAGED

    # Check for engagement state changes
    if self.engaged != self._engaged_prev:
      for callback in self._engaged_transition_callbacks:
        callback_name = getattr(callback, "__name__", type(callback).__name__)
        mark_progress(f"ui.update.before_engaged_callback.{callback_name}")
        callback()
        mark_progress(f"ui.update.after_engaged_callback.{callback_name}")
      self._engaged_prev = self.engaged

    # Handle onroad/offroad transition
    if self.started != self._started_prev or self.sm.frame == 1:
      if self.started:
        self.status = UIStatus.DISENGAGED
        self.started_frame = self.sm.frame
        self.started_time = time.monotonic()

      for callback in self._offroad_transition_callbacks:
        callback_name = getattr(callback, "__name__", type(callback).__name__)
        mark_progress(f"ui.update.before_offroad_callback.{callback_name}")
        callback()
        mark_progress(f"ui.update.after_offroad_callback.{callback_name}")

      self._started_prev = self.started

  def update_params(self) -> None:
    # For slower operations
    # Update longitudinal control state
    CP_bytes = capability_car_params_bytes(self.params)
    if CP_bytes is not None:
      self.CP = messaging.log_from_bytes(CP_bytes, car.CarParams)
      if self.CP.alphaLongitudinalAvailable:
        self.has_longitudinal_control = self.params.get_bool("AlphaLongitudinalEnabled")
      else:
        self.has_longitudinal_control = self.CP.openpilotLongitudinalControl
      self.experimental_mode_available = (
        self.has_longitudinal_control or
        lateral_only_experimental_available(self.CP)
      )
    else:
      self.CP = None
      self.has_longitudinal_control = False
      self.experimental_mode_available = False
    self._param_update_time = time.monotonic()


class Device:
  SCREEN_SETTINGS_REFRESH_INTERVAL = 1.0

  def __init__(self):
    self._ignition = False
    self._screen_off = False
    self._screen_off_started = ui_state.started
    self._screen_off_counter = screen_off_toggle_counter(ui_state.params_memory)
    self._last_button_press = standby_button_press_time(ui_state.params_memory)
    self._last_car_button_frame = int(time.monotonic() * 1e9)
    self._last_turn_signal = None
    self._interaction_time: float = -1
    self._override_interactive_timeout: int | None = None
    self._interactive_timeout_callbacks: list[Callable] = []
    self._prev_timed_out = False
    self._awake: bool = True
    self._params = ui_state.ui_params

    self._screen_settings_refresh_time: float = 0.0
    self._screen_management = False
    self._screen_brightness = 101
    self._screen_brightness_onroad = 101
    self._screen_timeout = 30
    self._screen_timeout_onroad = 30
    self._standby_mode = False
    self._keep_screen_on_gpu_loading = False
    self._last_status = ui_state.status
    self._screen_offset = self._screen_offset_onroad = 0
    self._wake_keys = frozenset()
    self._refresh_screen_settings(force=True)

    self._offroad_brightness: int = BACKLIGHT_OFFROAD
    self._last_brightness: int = 0
    self._brightness_filter = FirstOrderFilter(BACKLIGHT_OFFROAD, 10.00, 1 / gui_app.target_fps)
    self._brightness_thread: threading.Thread | None = None

  @property
  def awake(self) -> bool:
    return self._awake

  def set_override_interactive_timeout(self, timeout: int | None) -> None:
    # Override the interactive timeout duration temporarily
    self._override_interactive_timeout = timeout
    self._reset_interactive_timeout()

  @property
  def interactive_timeout(self) -> int:
    if self._override_interactive_timeout is not None:
      return self._override_interactive_timeout

    timeout_onroad = self._screen_timeout_onroad
    timeout_offroad = self._screen_timeout

    if timeout_onroad <= 0:
      timeout_onroad = 10 if gui_app.big_ui() else 5
    if timeout_offroad <= 0:
      timeout_offroad = 30

    return int(timeout_onroad if ui_state.ignition else timeout_offroad)

  def _reset_interactive_timeout(self) -> None:
    self._interaction_time = time.monotonic() + self.interactive_timeout

  def reset_interactive_timeout(self) -> None:
    self._reset_interactive_timeout()

  def add_interactive_timeout_callback(self, callback: Callable):
    self._interactive_timeout_callbacks.append(callback)

  def update(self):
    self._refresh_screen_settings()

    # do initial reset
    if self._interaction_time <= 0:
      self._reset_interactive_timeout()

    self._update_wakefulness()
    self._update_brightness()

  def _refresh_screen_settings(self, force: bool = False) -> None:
    now = time.monotonic()
    if not force and now - self._screen_settings_refresh_time < self.SCREEN_SETTINGS_REFRESH_INTERVAL:
      return

    previous = (
      self._screen_management,
      self._screen_brightness,
      self._screen_brightness_onroad,
      self._screen_timeout,
      self._screen_timeout_onroad,
      self._standby_mode,
      self._keep_screen_on_gpu_loading,
      self._screen_offset,
      self._screen_offset_onroad,
      self._wake_keys,
    )

    self._screen_management = self._params.get_bool("ScreenManagement")
    if self._screen_management:
      self._screen_brightness = min(max(self._params.get_int("ScreenBrightness", return_default=True), 0), 101)
      self._screen_brightness_onroad = min(max(self._params.get_int("ScreenBrightnessOnroad", return_default=True), 0), 101)
      self._screen_timeout = self._params.get_int("ScreenTimeout", return_default=True)
      self._screen_timeout_onroad = self._params.get_int("ScreenTimeoutOnroad", return_default=True)
      self._standby_mode = self._params.get_bool("StandbyMode")
      self._keep_screen_on_gpu_loading = self._params.get_bool("KeepScreenOnGpuLoading")
      self._screen_offset = brightness_preferences(self._params, "ScreenBrightness")["offset"]
      self._screen_offset_onroad = brightness_preferences(self._params, "ScreenBrightnessOnroad")["offset"]
      self._wake_keys = frozenset(enabled_wake_keys(self._params))
    else:
      self._screen_brightness = 101
      self._screen_brightness_onroad = 101
      self._screen_timeout = 30
      self._screen_timeout_onroad = 30
      self._standby_mode = False
      self._keep_screen_on_gpu_loading = False
      self._screen_offset = self._screen_offset_onroad = 0
      self._wake_keys = frozenset()

    self._screen_settings_refresh_time = now
    current = (
      self._screen_management,
      self._screen_brightness,
      self._screen_brightness_onroad,
      self._screen_timeout,
      self._screen_timeout_onroad,
      self._standby_mode,
      self._keep_screen_on_gpu_loading,
      self._screen_offset,
      self._screen_offset_onroad,
      self._wake_keys,
    )
    if previous != current and self._interaction_time > 0:
      self._reset_interactive_timeout()

  def set_offroad_brightness(self, brightness: int | None):
    if brightness is None:
      brightness = BACKLIGHT_OFFROAD
    self._offroad_brightness = min(max(brightness, 0), 100)

  def _update_brightness(self):
    brightness = self._calculate_brightness()

    if brightness != self._last_brightness:
      if self._brightness_thread is None or not self._brightness_thread.is_alive():
        self._brightness_thread = threading.Thread(target=HARDWARE.set_screen_brightness, args=(brightness,))
        self._brightness_thread.start()
        self._last_brightness = brightness

  def _calculate_brightness(self) -> int:
    if self._screen_off:
      return 0
    clipped_brightness = self._offroad_brightness

    if ui_state.started and ui_state.light_sensor >= 0:
      clipped_brightness = ui_state.light_sensor

      # CIE 1931 - https://www.photonstophotos.net/GeneralTopics/Exposure/Psychometric_Lightness_and_Gamma.htm
      if clipped_brightness <= 8:
        clipped_brightness = clipped_brightness / 903.3
      else:
        clipped_brightness = ((clipped_brightness + 16.0) / 116.0) ** 3.0

      clipped_brightness = float(np.interp(clipped_brightness, [0, 1], [30, 100]))

    automatic = self._brightness_filter.update(clipped_brightness)
    interactive = time.monotonic() <= self._interaction_time
    return calculate_screen_brightness(
      automatic,
      self._screen_brightness_onroad if ui_state.started else self._screen_brightness,
      self._screen_offset_onroad if ui_state.started else self._screen_offset,
      interactive=interactive,
      awake=self._awake,
      standby_timed_out=ui_state.started and self._standby_mode and not interactive,
    )

  def _update_wakefulness(self):
    # Handle interactive timeout
    ignition_state_changed = ui_state.ignition != self._ignition
    self._ignition = ui_state.ignition

    status_changed = ui_state.status != self._last_status and ui_state.status != UIStatus.OVERRIDE
    self._last_status = ui_state.status
    status_key = "StandbyWakeEngage" if ui_state.status == UIStatus.ENGAGED else "StandbyWakeDisengage"
    input_events = self._wake_input_events()
    selected_status_change = status_changed and status_key in self._wake_keys
    selected_turn_signal = bool(input_events & self._wake_keys)
    button_pressed = (self._standby_mode and (ui_state.started or ui_state.ignition) and
                      "StandbyWakeButton" in self._wake_keys and "button" in input_events)
    wake_for_onroad_event = (ui_state.started and self._standby_mode and self._screen_brightness_onroad != 0 and
                             (selected_status_change or self._visible_onroad_alert() or selected_turn_signal))
    keep_on_for_gpu_loading = self._keep_screen_on_gpu_loading and ui_state.started and ui_state.usbgpu_loading

    counter = screen_off_toggle_counter(ui_state.params_memory)
    presses = counter - self._screen_off_counter
    self._screen_off_counter = counter
    road_changed = ui_state.started != self._screen_off_started
    self._screen_off_started = ui_state.started
    touched = any(ev.left_down for ev in gui_app.mouse_events)
    was_screen_off = self._screen_off
    if not ui_state.started or road_changed or ignition_state_changed:
      self._screen_off = False
    elif presses > 0:
      if presses % 2:
        self._screen_off = not self._screen_off
    elif touched or wake_for_onroad_event or "StandbyWakeCriticalAlert" in self._active_standby_alerts():
      self._screen_off = False

    if (ignition_state_changed or touched or button_pressed or wake_for_onroad_event or keep_on_for_gpu_loading or
        presses > 0 or (was_screen_off and not self._screen_off)):
      self._reset_interactive_timeout()

    interaction_timeout = time.monotonic() > self._interaction_time
    if interaction_timeout and not self._prev_timed_out:
      for callback in self._interactive_timeout_callbacks:
        callback()
    self._prev_timed_out = interaction_timeout

    standby_active = ui_state.started and self._standby_mode
    keep_display_awake = not interaction_timeout or PC
    keep_display_awake |= ui_state.ignition and not standby_active
    self._set_awake(keep_display_awake and not self._screen_off)

  @staticmethod
  def _fresh_message(name):
    sm = ui_state.sm
    try:
      return sm[name] if sm.alive[name] and sm.valid[name] else None
    except (KeyError, AttributeError):
      return None

  def _wake_input_events(self):
    button_time = standby_button_press_time(ui_state.params_memory)
    button_pressed = button_time > self._last_button_press and 0 <= time.monotonic() - button_time / 1e9 < 2
    self._last_button_press = button_time
    events = {"button"} if button_pressed else set()
    # Reuse the UI reader: another carState subscriber can exhaust msgq slots.
    frames = getattr(ui_state.sm, "drained", {}).get("carState", []) if ui_state.started else []
    now_ns = int(time.monotonic() * 1e9)
    for message in frames:
      timestamp = int(message.logMonoTime)
      if not message.valid or not 0 <= now_ns - timestamp < 2_000_000_000 or timestamp <= self._last_car_button_frame:
        continue
      self._last_car_button_frame = timestamp
      if any(event.pressed and str(event.type) not in ("unknown", "0") for event in message.carState.buttonEvents):
        events.add("button")
    car_state = self._fresh_message("carState") if ui_state.started else None
    turn_signal = (int(car_state.leftBlinker) | (int(car_state.rightBlinker) << 1)) if car_state is not None else None
    if self._last_turn_signal is not None and turn_signal and turn_signal != self._last_turn_signal:
      events.add("StandbyWakeTurnSignal")
    self._last_turn_signal = turn_signal
    return events

  def _active_standby_alerts(self):
    # Match Dom's alert predicate and primary-message precedence. The toggle
    # selects the category; it does not introduce another alert renderer.
    if not ui_state.started:
      return set()
    try:
      key = alert_wake_key(ui_state.sm["selfdriveState"])
      if key is not None:
        return {key}
    except Exception:
      pass
    try:
      alert = ui_state.sm["starpilotSelfdriveState"]
      if str(alert.alertSize) not in ("none", "0"):
        key = alert_wake_key(alert)
        return {key} if key is not None else set()
    except Exception:
      pass
    return set()

  def _visible_onroad_alert(self):
    return bool(self._active_standby_alerts() & self._wake_keys)

  def _set_awake(self, on: bool):
    if on != self._awake:
      self._awake = on
      cloudlog.debug(f"setting display power {int(on)}")
      HARDWARE.set_display_power(on)
      gui_app.set_should_render(on)


# Global instance
ui_state = UIState()
device = Device()
