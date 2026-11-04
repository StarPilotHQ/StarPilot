import numpy as np
import os
import time
import threading
import capnp
from collections.abc import Callable
from enum import Enum
from dataclasses import replace
from openpilot.cereal import messaging, log
from openpilot.cereal.services import SERVICE_LIST
from opendbc.car.structs import car
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.starpilot.schema_cache import get_cache, prewarm_cache_contracts
from openpilot.starpilot.feature_runtime import enabled as feature_enabled
from openpilot.starpilot.ui.display_preferences import AUTO, DisplayPreferences, read_preferences
from openpilot.common.realtime import drop_realtime
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.lib.prime_state import PrimeState
from openpilot.selfdrive.selfdrived.alertmanager import OFFROAD_ALERTS
from openpilot.system.ui.lib.application import gui_app
from openpilot.common.hardware import HARDWARE, PC
from openpilot.common.hardware.usb import cable_connected, get_usb_state, is_chestnut_usb_id, usb_host_connected
from openpilot.starpilot.models.manager import ModelManager
from openpilot.starpilot.models.jetlink_status import JetlinkStatusFeed
from openpilot.tools.replay.display_clock import BOOT_TIME_SERVICES, DisplayClockReader

BACKLIGHT_OFFROAD = 65 if HARDWARE.get_device_type() == "mici" else 50
PARAM_UPDATE_TIME = 1 / 5.0


class UIStatus(Enum):
  DISENGAGED = "disengaged"
  ENGAGED = "engaged"
  OVERRIDE = "override"


class ChestnutState(Enum):
  DISCONNECTED = "disconnected"
  UNCOMPILED = "uncompiled"
  READY = "ready"
  LOADING = "loading"
  ACTIVE = "active"
  FAILED = "failed"


class ProjectionParams:
  """Only the read interface needed by the projection display."""

  def __init__(self, source: Params):
    self._source = source

  def get(self, *args, **kwargs):
    return self._source.get(*args, **kwargs)

  def get_bool(self, *args, **kwargs):
    return self._source.get_bool(*args, **kwargs)

  def get_int(self, *args, **kwargs):
    return self._source.get_int(*args, **kwargs)

  def get_float(self, *args, **kwargs):
    return self._source.get_float(*args, **kwargs)

  def get_param_path(self, *args, **kwargs):
    return self._source.get_param_path(*args, **kwargs)


class ProjectionPrimeState:
  """Saved pairing label only; projection never starts the API or alert owner."""

  def __init__(self, params: ProjectionParams | Params):
    self.params = params

  def is_paired(self) -> bool:
    try:
      return int(self.params.get("PrimeType") or -1) >= 0
    except (TypeError, ValueError):
      return False

  def start(self) -> None:
    pass


class UIState:
  _instance: 'UIState | None' = None

  def __new__(cls):
    if cls._instance is None:
      cls._instance = super().__new__(cls)
      cls._instance._initialize()
    return cls._instance

  def _initialize(self):
    prewarm_cache_contracts()
    self.replay_clock = DisplayClockReader() if DisplayClockReader.enabled() else None
    self.replay_sample = None
    self._replay_state_epoch = None
    self.replay_needs_reprime = False
    self._replay_reprime_services: set[str] = set()
    self.projection_read_only = os.environ.get("STARPILOT_PROJECTION_READ_ONLY") == "1"
    params = Params()
    self.params = ProjectionParams(params) if self.projection_read_only else params
    self.sm = messaging.SubMaster(
      [
        "modelV2",
        "controlsState",
        "lateralDelay",
        "lateralTorqueParameters",
        "onroadEvents",
        "extrinsicsCalibration",
        "radarState",
        "deviceState",
        "pandaStates",
        "carParams",
        "driverMonitoringState",
        "carState",
        "starpilotCarState",
        "driverStateV2",
        "narrowRoadCameraState",
        "wideRoadCameraState",
        "managerState",
        "selfdriveState",
        "starpilotSelfdriveState",
        "starpilotLateralState",
        "starpilotNavigation",
        "longitudinalPlan",
        "gpsLocationExternal",
        "gpsLocation",
        "navInstruction",
        "navRoute",
        "carOutput",
        "carControl",
        "aolAxisState",
        "aolIntentWire",
        "slcState",
        "slcVisionObservation",
        "vehicleParameters",
        "testJoystick",
        "rawAudioData",
        *(["customReserved6", "cabinCameraState", "narrowRoadEncodeIdx", "wideRoadEncodeIdx", "cabinEncodeIdx"]
          if self.replay_clock is not None else []),
      ],
      ignore_alive=["slcVisionObservation", "starpilotSelfdriveState", "starpilotLateralState", "aolAxisState", "starpilotCarState"],
      ignore_valid=["slcVisionObservation", "starpilotSelfdriveState", "starpilotLateralState", "aolAxisState", "starpilotCarState"],
      ignore_avg_freq=["slcVisionObservation", "starpilotSelfdriveState", "starpilotLateralState", "aolAxisState", "starpilotCarState"],
    )

    self.prime_state = ProjectionPrimeState(self.params) if self.replay_clock is not None or isinstance(self.params, ProjectionParams) else PrimeState()

    # UI Status tracking
    self.status: UIStatus = UIStatus.DISENGAGED
    self.started_frame: int = 0
    self.started_time: float = 0.0
    self._engaged_prev: bool = False
    self._started_prev: bool = False

    # Core state variables
    self.is_metric: bool = self.params.get_bool("IsMetric")
    self.initial_cruise_speed: int = self.params.get("InitialCruiseSpeedKph", return_default=True)
    self.is_release = self.params.get_bool("IsReleaseBranch")
    self.always_on_dm: bool = self.params.get_bool("AlwaysOnDM")
    self.experimental_mode: bool = self.params.get_bool("ExperimentalMode")
    self.navigation_enabled = self.params.get_bool("NavigationEnabled")
    self.nav_destination_set = self.params.get("NavDestination") is not None
    self.jetlink_status_feed = None if self.replay_clock is not None else JetlinkStatusFeed()
    if self.jetlink_status_feed is not None:
      self.jetlink_status_feed.set_enabled(True)
    self.chestnut_present: bool = False
    self._gpu_artifacts = None if self.replay_clock is not None else ModelManager()
    self._gpu_artifacts_at: float | None = None
    self.chestnut_compiled: bool = False
    self.chestnut_checking: bool = False
    self.chestnut_active: bool | None = None
    self.chestnut_output_seen: bool = False
    self.chestnut_alert: bool = False
    self.chestnut_loading: bool = False
    self.usb_connected: bool = False
    self.usb_connected_ts: float | None = None
    self.usb_disconnected_ts: float | None = None
    self.usb_unknown: bool = False
    self.chestnut_state = ChestnutState.DISCONNECTED
    self.started: bool = False
    self.ignition: bool = False
    self.recording_audio: bool = False
    self.panda_type: log.PandaState.PandaType = log.PandaState.PandaType.unknown
    self.personality: log.LongitudinalPersonality = log.LongitudinalPersonality.standard
    self.has_longitudinal_control: bool = False
    self.aol_runtime: bool = False
    self.is_body: bool | None = False
    self.CP: car.CarParams | None = None
    self.light_sensor: float = -1.0

    self._params_thread: threading.Thread | None = None
    self._projection_params_at: float | None = None

    # Callbacks
    self._offroad_transition_callbacks: list[Callable[[], None]] = []
    self._engaged_transition_callbacks: list[Callable[[], None]] = []
    self._on_body_changed_callbacks: list[Callable[[], None]] = []

  def add_offroad_transition_callback(self, callback: Callable[[], None]):
    self._offroad_transition_callbacks.append(callback)

  def add_engaged_transition_callback(self, callback: Callable[[], None]):
    self._engaged_transition_callbacks.append(callback)

  def add_on_body_changed_callbacks(self, callback: Callable[[], None]):
    self._on_body_changed_callbacks.append(callback)

  @property
  def engaged(self) -> bool:
    return self.started and self.sm["selfdriveState"].enabled

  def is_onroad(self) -> bool:
    return self.started

  def is_offroad(self) -> bool:
    return not self.started

  def update(self) -> None:
    if getattr(self, "replay_clock", None) is not None:
      before = self.replay_clock.sample()
      cache_epoch = getattr(self, "_replay_state_epoch", self.replay_sample.epoch if self.replay_sample is not None else None)
      if before.epoch is not None and before.epoch != cache_epoch:
        self._reset_replay_state()
        self._replay_state_epoch = before.epoch
        self.replay_needs_reprime = False
        self._replay_reprime_services = set()
      self.sm.update(0)
      sample = self.replay_clock.sample()
      if sample.epoch is not None and sample.epoch != before.epoch:
        # A seek straddled this receive. Keep only its eligible new primer, and
        # withhold this mixed frame until the next coherent clock sample.
        self._replay_reprime_services = {name for name in self.sm.sock if self.sm.updated[name]}
        self._reset_replay_state(retain_sample=sample)
        self.replay_needs_reprime = not sample.valid
        self._replay_state_epoch = sample.epoch
        sample = replace(sample, now_ns=None, boot_ns=None, valid=False)
      if sample.valid and getattr(self, "replay_needs_reprime", False):
        # A later receive can restore the primer without another decoder seek.
        for name in self._replay_reprime_services.copy():
          target = sample.boot_ns if name in BOOT_TIME_SERVICES else sample.now_ns
          stamp = self.sm.logMonoTime[name]
          if self.sm.seen[name] and self.sm.valid[name] and type(target) is int and type(stamp) is int and 0 < stamp <= target:
            self._replay_reprime_services.remove(name)
        self.replay_needs_reprime = bool(self._replay_reprime_services)
      self.replay_sample = sample
      self.sm.replay_sample = sample
      self._update_replay_alive(sample)
      self._update_state()
      self._update_status()
      now = time.monotonic()
      if self._projection_params_at is None or now - self._projection_params_at >= 1.0:
        self.update_params()
        self._projection_params_at = now
      self._update_chestnut_state()
      return
    if self.projection_read_only:
      self.sm.update(0)
      self._update_state()
      self._update_status()
      now = time.monotonic()
      if self._projection_params_at is None or now < self._projection_params_at or now - self._projection_params_at >= 1.0:
        self.update_params()
        self._projection_params_at = now
      return
    self.prime_state.start()  # start thread after manager forks ui
    if self._params_thread is None:
      self._params_thread = threading.Thread(target=self._params_refresh_worker, daemon=True)
      self._params_thread.start()

    self.sm.update(0)
    self._update_state()
    self._update_status()
    self._update_chestnut_state()
    device.update()

  def display_time_ns(self) -> int:
    if getattr(self, "replay_clock", None) is not None:
      sample = self.replay_sample
      return sample.now_ns if sample is not None and sample.valid and sample.now_ns is not None else 0
    return time.monotonic_ns()

  def _update_replay_alive(self, sample) -> None:
    # Receipt timestamps stay on the host clock. Logical freshness follows the
    # recorded clock, including a paused primer and Panda's boot-time domain.
    for service in self.sm.static_freq_services:
      target = sample.boot_ns if service in BOOT_TIME_SERVICES else sample.now_ns
      stamp = self.sm.logMonoTime[service]
      spec = SERVICE_LIST[service]
      max_age_ns = 10e9 / spec.frequency
      if spec.decimation:
        # Qlogs sample high-rate messages sparsely. Keep replay transport alive
        # through the display window; current_message still enforces its own
        # stricter age limit for actions, independently of this UI-only flag.
        max_age_ns = max(max_age_ns, min(1e9, 2e9 * spec.decimation / spec.frequency))
      self.sm.alive[service] = bool(sample.valid and self.sm.seen[service] and
                                    type(target) is int and type(stamp) is int and
                                    0 < stamp <= target and target - stamp < max_age_ns)

  def _reset_replay_state(self, retain_sample=None) -> None:
    for service in self.sm.sock:
      target = None if retain_sample is None else retain_sample.boot_ns if service in BOOT_TIME_SERVICES else retain_sample.now_ns
      stamp = self.sm.logMonoTime[service]
      if (retain_sample is not None and retain_sample.valid and self.sm.updated[service] and
          type(target) is int and type(stamp) is int and 0 < stamp <= target):
        continue
      # Do not drain sockets: the new epoch's paused primer may already be queued.
      for field in ("seen", "updated", "alive", "valid"):
        getattr(self.sm, field)[service] = False
      for field in ("recv_frame", "recv_time", "logMonoTime"):
        getattr(self.sm, field)[service] = 0
      try:
        empty = messaging.new_message(service)
      except capnp.lib.capnp.KjException:
        empty = messaging.new_message(service, 0)
      self.sm.data[service] = getattr(empty.as_reader(), service)
    self.started = self._started_prev = self._engaged_prev = False
    self.started_frame = max(0, self.sm.frame - 1)
    self.CP = None
    self.has_longitudinal_control = False
    self.aol_runtime = False
    self.ignition = False
    self.light_sensor = -1.0
    self._projection_params_at = None
    self.chestnut_output_seen = False
    self.chestnut_present = False
    self.status = UIStatus.DISENGAGED

  def _params_refresh_worker(self):
    drop_realtime()
    while True:
      self.update_params()
      time.sleep(PARAM_UPDATE_TIME)

  def _update_state(self) -> None:
    # Handle panda states updates
    if self.sm.updated["pandaStates"]:
      panda_states = self.sm["pandaStates"]

      if len(panda_states) > 0:
        # Get panda type from first panda
        self.panda_type = panda_states[0].pandaType
        # Check ignition status across all pandas
        if self.panda_type != log.PandaState.PandaType.unknown:
          self.ignition = any(state.ignitionLine or state.ignitionCan for state in panda_states)
    elif not self.sm.alive["pandaStates"]:
      self.panda_type = log.PandaState.PandaType.unknown

    # Handle wide road camera state updates
    if self.sm.updated["wideRoadCameraState"]:
      cam_state = self.sm["wideRoadCameraState"]
      self.light_sensor = max(100.0 - cam_state.exposureValPercent, 0.0)
    elif not self.sm.alive["wideRoadCameraState"] or not self.sm.valid["wideRoadCameraState"]:
      self.light_sensor = -1

    # hardwared owns the pipeline transition; raw ignition can arrive separately.
    self.started = self.sm["deviceState"].started

    # Update body state
    if self.CP is not None and self.is_body != self.CP.notCar:
      self.is_body = self.CP.notCar
      for callback in self._on_body_changed_callbacks:
        callback()

  def _update_status(self) -> None:
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
        gui_app.measure_frame_phase("transition", callback)
      self._engaged_prev = self.engaged

    # Handle onroad/offroad transition
    if self.started != self._started_prev or self.sm.frame == 1:
      if self.started:
        replay = getattr(self, "replay_clock", None) is not None
        if not replay:
          self.status = UIStatus.DISENGAGED
        self.chestnut_output_seen = False
        # A paused replay publishes one atomic primer; its same-frame receipts
        # are eligible. Live transitions retain their stricter receive boundary.
        self.started_frame = max(0, self.sm.frame - 1) if replay else self.sm.frame
        self.started_time = time.monotonic()
        self.chestnut_present = self.sm["deviceState"].chestnutPresent or (self.chestnut_compiled and self.usb_connected and not usb_host_connected())

      for callback in self._offroad_transition_callbacks:
        gui_app.measure_frame_phase("transition", callback)

      self._started_prev = self.started

    if (self.started and self.sm.updated["selfdriveState"] and self.sm.valid["selfdriveState"] and
        self.sm.alive["selfdriveState"] and self.sm.recv_frame["selfdriveState"] > self.started_frame):
      stamp = self.sm.logMonoTime["selfdriveState"]
      if 0 < stamp <= UIState.display_time_ns(self) <= stamp + 250_000_000:
        self.personality = log.LongitudinalPersonality.schema.enumerants[self.sm["selfdriveState"].personality]

  def _update_chestnut_state(self) -> None:
    detected = self.sm["deviceState"].chestnutPresent
    feed = getattr(self, "jetlink_status_feed", None)
    jetlink = feed.snapshot() if feed is not None and getattr(self, "replay_clock", None) is None and not detected else None
    if jetlink is not None and jetlink.state != "off" and not self.started:
      self.chestnut_present = jetlink.state not in ("disconnected", "unavailable")
      self.chestnut_state = (ChestnutState.DISCONNECTED if jetlink.state == "disconnected" else
                            ChestnutState.FAILED if jetlink.state == "unavailable" else
                            ChestnutState.READY if jetlink.state in ("ready", "active") else ChestnutState.LOADING)
      return
    if not self.started:
      self.chestnut_present = detected
      self.chestnut_state = (ChestnutState.FAILED if self.chestnut_alert else
                             ChestnutState.DISCONNECTED if not detected else
                             ChestnutState.LOADING if self.chestnut_checking else
                             ChestnutState.READY if self.chestnut_compiled else ChestnutState.UNCOMPILED)
      return

    model_seen = self.sm.recv_frame["modelV2"] > self.started_frame
    model_stamp = self.sm.logMonoTime["modelV2"]
    model_fresh = (model_seen and self.sm.valid["modelV2"] and self.sm.alive["modelV2"] and
                   0 < model_stamp <= UIState.display_time_ns(self) <= model_stamp + 250_000_000)
    if jetlink is not None and jetlink.state != "off":
      self.chestnut_present = jetlink.state not in ("disconnected", "unavailable")
      if jetlink.state == "active" and model_fresh and self.sm["modelV2"].big:
        self.chestnut_output_seen = True
        self.chestnut_state = ChestnutState.ACTIVE
      elif jetlink.state == "disconnected":
        self.chestnut_state = ChestnutState.DISCONNECTED
      elif jetlink.state == "unavailable" or self.chestnut_output_seen:
        self.chestnut_state = ChestnutState.FAILED
      else:
        self.chestnut_state = ChestnutState.LOADING
      return
    if getattr(self, "replay_clock", None) is not None:
      self.chestnut_present = detected
    if model_fresh and self.sm["modelV2"].big:
      self.chestnut_output_seen = True
    if not self.chestnut_present:
      self.chestnut_state = ChestnutState.DISCONNECTED
    elif detected and model_fresh and self.sm["modelV2"].big:
      self.chestnut_state = ChestnutState.ACTIVE
    elif model_fresh and not self.sm["modelV2"].big:
      self.chestnut_state = ChestnutState.FAILED
    elif not self.chestnut_output_seen and self.chestnut_loading:
      self.chestnut_state = ChestnutState.LOADING
    elif not detected:
      self.chestnut_state = ChestnutState.FAILED
    elif not self.chestnut_output_seen and (self.chestnut_loading or not model_seen or self.chestnut_active is not False):
      self.chestnut_state = ChestnutState.LOADING
    else:
      self.chestnut_state = ChestnutState.FAILED

  def update_params(self) -> None:
    # For slower operations
    # Update longitudinal control state
    CP_bytes = get_cache(self.params, "CarParamsPersistent")
    if (getattr(self, "replay_clock", None) is not None and
        self.sm.seen["carParams"] and self.sm.valid["carParams"]):
      self.CP = self.sm["carParams"]
      self.has_longitudinal_control = self.CP.openpilotLongitudinalControl
    elif CP_bytes is not None:
      self.CP = messaging.log_from_bytes(CP_bytes, car.CarParams)
      # The saved Alpha Long choice can survive an unavailable configuration.
      self.has_longitudinal_control = self.CP.openpilotLongitudinalControl
    else:
      self.CP = None
      self.has_longitudinal_control = False

    self.aol_runtime = bool(self.CP is not None and feature_enabled(self.params, self.CP, "aol", os.environ))
    self.recording_audio = self.params.get_bool("RecordAudio") and self.started
    self.is_metric = self.params.get_bool("IsMetric")
    self.initial_cruise_speed = self.params.get("InitialCruiseSpeedKph", return_default=True)
    self.always_on_dm = self.params.get_bool("AlwaysOnDM")
    self.experimental_mode = self.params.get_bool("ExperimentalMode")
    self.navigation_enabled = self.params.get_bool("NavigationEnabled")
    self.nav_destination_set = self.params.get("NavDestination") is not None
    raw_chestnut = self.params.get("ChestnutActive")
    self.chestnut_active = raw_chestnut if type(raw_chestnut) is bool else (
      True if raw_chestnut in (b"1", "1") else False if raw_chestnut in (b"0", "0") else None)
    self.chestnut_loading = self.params.get_bool("ChestnutLoading")
    self.chestnut_alert = any(self.params.get(k) is not None for k, a in OFFROAD_ALERTS.items()
                              if k.startswith("Offroad_Chestnut") and a["severity"] >= 0)
    if getattr(self, "replay_clock", None) is not None:
      self.chestnut_compiled = self.chestnut_active is True or self.chestnut_output_seen
      return
    now = time.monotonic()
    if not self.started and (self._gpu_artifacts_at is None or now < self._gpu_artifacts_at or now - self._gpu_artifacts_at >= 1.):
      self.chestnut_compiled, self.chestnut_checking = self._gpu_artifacts.selected_gpu_status()
      self._gpu_artifacts_at = now
    if cable_connected():
      self.usb_disconnected_ts = None
      if not self.usb_connected:
        self.usb_connected = True
        self.usb_connected_ts = now
        self.usb_unknown = False
      elif self.usb_connected_ts is not None and now - self.usb_connected_ts > 10.:
        self.usb_unknown = not any(is_chestnut_usb_id(d["vendorId"], d["productId"], True) for d in get_usb_state())
        self.usb_connected_ts = None
    elif self.usb_connected:
      if self.usb_disconnected_ts is None:
        self.usb_disconnected_ts = now
      elif now - self.usb_disconnected_ts > PARAM_UPDATE_TIME:
        self.usb_connected = False
        self.usb_connected_ts = None
        self.usb_unknown = False


class Device:
  def __init__(self):
    self._ignition = False
    self._interaction_time: float = -1
    self._override_interactive_timeout: int | None = None
    self._interactive_timeout_callbacks: list[Callable] = []
    self._prev_timed_out = False
    self._awake: bool = True

    self._offroad_brightness: int = BACKLIGHT_OFFROAD
    self._last_brightness: int = 0
    self._brightness_filter = FirstOrderFilter(BACKLIGHT_OFFROAD, 10.00, 1 / gui_app.target_fps)
    self._brightness_thread: threading.Thread | None = None
    self._brightness_event = threading.Event()
    self._brightness_target: int = 0
    self._display_preferences = DisplayPreferences(driving_timeout=10 if gui_app.big_ui() else 5)
    self._display_refresh_time: float | None = None
    self._display_started = False
    self._screen_off = False
    self._screen_off_started = False
    self._wake_touch_active = False

  def invalidate_display_preferences(self) -> None:
    self._display_refresh_time = None

  def _refresh_display_preferences(self) -> None:
    now = time.monotonic()
    if self._display_refresh_time is not None and 0 <= now - self._display_refresh_time < 1.0:
      return
    previous = self._display_preferences
    self._display_preferences = read_preferences(ui_state.params, large=gui_app.big_ui())
    self._display_refresh_time = now
    if previous != self._display_preferences and self._interaction_time > 0:
      self._reset_interactive_timeout()

  @property
  def awake(self) -> bool:
    return self._awake

  def toggle_screen_off(self) -> bool:
    if ui_state.projection_read_only or not ui_state.started:
      return False
    self._screen_off = self._awake
    self._screen_off_started = ui_state.started
    self._reset_interactive_timeout()
    self._set_awake(not self._screen_off)
    return True

  def set_override_interactive_timeout(self, timeout: int | None) -> None:
    # Override the interactive timeout duration temporarily
    self._override_interactive_timeout = timeout
    self._reset_interactive_timeout()

  @property
  def interactive_timeout(self) -> int:
    if self._override_interactive_timeout is not None:
      return self._override_interactive_timeout

    if self._display_preferences.enabled:
      return self._display_preferences.driving_timeout if ui_state.started else self._display_preferences.parked_timeout

    ignition_timeout = 10 if gui_app.big_ui() else 5
    return ignition_timeout if ui_state.started else 30

  def _reset_interactive_timeout(self) -> None:
    self._interaction_time = time.monotonic() + self.interactive_timeout

  def add_interactive_timeout_callback(self, callback: Callable):
    self._interactive_timeout_callbacks.append(callback)

  def update(self):
    if ui_state.projection_read_only:
      return
    self._start_brightness_thread()  # start thread after manager forks ui
    started_changed = self._display_started != bool(ui_state.started)
    if started_changed:
      self.invalidate_display_preferences()
    self._refresh_display_preferences()

    if started_changed:
      self._reset_interactive_timeout()
    self._display_started = bool(ui_state.started)

    # do initial reset
    if self._interaction_time <= 0:
      self._reset_interactive_timeout()

    self._update_wakefulness()
    self._update_brightness()

  def _start_brightness_thread(self):
    if self._brightness_thread is None or not self._brightness_thread.is_alive():
      self._brightness_thread = threading.Thread(target=self._brightness_worker, daemon=True)
      self._brightness_thread.start()

  def _brightness_worker(self):
    drop_realtime()
    while True:
      self._brightness_event.wait()
      self._brightness_event.clear()
      HARDWARE.set_screen_brightness(self._brightness_target)

  def set_offroad_brightness(self, brightness: int | None):
    if brightness is None:
      brightness = BACKLIGHT_OFFROAD
    self._offroad_brightness = min(max(brightness, 0), 100)

  def _update_brightness(self):
    clipped_brightness = self._offroad_brightness

    if ui_state.started and ui_state.light_sensor >= 0:
      clipped_brightness = ui_state.light_sensor

      # CIE 1931 - https://www.photonstophotos.net/GeneralTopics/Exposure/Psychometric_Lightness_and_Gamma.htm
      if clipped_brightness <= 8:
        clipped_brightness = clipped_brightness / 903.3
      else:
        clipped_brightness = ((clipped_brightness + 16.0) / 116.0) ** 3.0

      clipped_brightness = float(np.interp(clipped_brightness, [0, 1], [30, 100]))

    if self._display_preferences.enabled:
      saved = (self._display_preferences.driving_brightness if ui_state.started else
               self._display_preferences.parked_brightness)
      if saved != AUTO:
        clipped_brightness = saved
    brightness = round(self._brightness_filter.update(clipped_brightness))
    if not self._awake:
      brightness = 0

    if brightness != self._last_brightness:
      self._brightness_target = brightness
      self._brightness_event.set()
      self._last_brightness = brightness

  def _update_wakefulness(self):
    # Handle interactive timeout
    ignition_just_turned_off = not ui_state.ignition and self._ignition
    ignition_changed = self._ignition != ui_state.ignition
    self._ignition = ui_state.ignition
    touched = any(ev.left_down for ev in gui_app.mouse_events)
    if not self._awake and touched:
      self._wake_touch_active = True

    if (ignition_just_turned_off or (self._display_preferences.enabled and ignition_changed) or
        touched):
      self._reset_interactive_timeout()

    road_changed = ui_state.started != self._screen_off_started
    self._screen_off_started = ui_state.started
    critical = (ui_state.sm.valid.get('selfdriveState', False) and ui_state.sm.alive.get('selfdriveState', False) and
                ui_state.sm['selfdriveState'].alertStatus == log.SelfdriveState.AlertStatus.critical)
    if not ui_state.started or road_changed or touched or critical:
      self._screen_off = False
    if critical:
      self._reset_interactive_timeout()
    gpu_loading = (ui_state.started and self._display_preferences.keep_screen_on_gpu_loading and ui_state.chestnut_loading)
    if gpu_loading:
      self._reset_interactive_timeout()
    interaction_timeout = time.monotonic() > self._interaction_time
    if interaction_timeout and not self._prev_timed_out:
      for callback in self._interactive_timeout_callbacks:
        callback()
    self._prev_timed_out = interaction_timeout

    auto_screen_off = ui_state.started and self._display_preferences.turn_off_screen and interaction_timeout and not critical and not gpu_loading
    self._set_awake(not self._screen_off and not auto_screen_off and (ui_state.started or not interaction_timeout or PC))
    # The entire wake gesture belongs to the display, including its release.
    # Widgets were not rendered while asleep, so their touch state can be stale.
    if self._wake_touch_active:
      if any(ev.left_released for ev in gui_app.mouse_events):
        self._wake_touch_active = False
      gui_app.mouse_events.clear()

  def _set_awake(self, on: bool):
    if on != self._awake:
      self._awake = on
      cloudlog.debug(f"setting display power {int(on)}")
      HARDWARE.set_display_power(on)
      gui_app.set_should_render(on)


# Global instance
ui_state = UIState()
device = Device()
