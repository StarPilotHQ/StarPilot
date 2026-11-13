"""Authenticated local projection of the shared saved-feature owner."""

from openpilot.starpilot.ui.lane_change_feature import confirm_question as lane_change_question

from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
import secrets
import threading
import time
from typing import Protocol, cast

from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner, AGGRESSIVE_COOLING_KEY
from openpilot.starpilot.galaxy.setting_descriptions import description
from openpilot.starpilot.galaxy.localization_settings import LocalizationSettingsOwner
from openpilot.starpilot.ui.controller_feature import SETUP_ACTION, SETUP_QUESTION
from openpilot.starpilot.ui.vehicle_bool import VEHICLE_BOOL_KEYS, confirmation_question as vehicle_question
from openpilot.starpilot.ui.conditional_feature import confirmation_question as conditional_question
from openpilot.starpilot.ui.appearance_owner import AppearanceOwner, LABELS as APPEARANCE_LABELS
from openpilot.starpilot.ui.display_owner import AUTO_PREFIX as DISPLAY_AUTO_PREFIX, DisplayOwner, LABELS as DISPLAY_LABELS
from openpilot.starpilot.ui.sounds_owner import SoundsOwner
from openpilot.starpilot.ui.power_owner import PowerOwner, HOURS, VOLTS, POWER_KEYS, confirm_question as power_question
from openpilot.starpilot.audio.alert_volume import SPECS as SOUND_SPECS
from openpilot.starpilot.ui.presentation import Profile
from openpilot.common.hardware import HARDWARE
from openpilot.starpilot.ui.feature_settings_state import (
  FeatureRow, FeatureSettingsRequest, FEATURE_CONFIRM_ACTIONS, is_heading, is_long_confirm_action, row_change, row_default,
)
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.galaxy.developer_settings import CALIBRATION_RESET, DRIVER_MONITORING_RESET, DeveloperSettingsOwner
from openpilot.starpilot.speed_limits.vision_gate import diagnostic_choice_enabled
from openpilot.starpilot.parked_evidence import ParkedEvidence, RESUME_SKEW_NS, fresh_offroad, host_parked
from openpilot.starpilot.ui.pip_owner import EDITOR as PIP_EDITOR, FORMAT_PREFIX as PIP_FORMAT_PREFIX, RESET as PIP_RESET, PiPOwner, validated_editor_draft
from openpilot.starpilot.ui.pip_preferences import read_pip
from openpilot.starpilot.ui.sentry_owner import RESET as SENTRY_RESET, SentryOwner
from openpilot.starpilot.ui.vasm_owner import ANNOTATION as VASM_ANNOTATION, RESET as VASM_RESET, VASMOwner, validated_annotation


PAGES = frozenset(("hub", "localization", "developer", "vehicle", "slc", "lane", "lane_change", "profiles", "personalities",
                   "traffic", "aggressive", "standard", "relaxed",
                   "curve", "torque", "tuning", "torque/bluepilot", "aol", "wheel", "appearance", "display", "data", "power", "sounds", "pip", "sentry", "vasm",
                   "conditional", "conditional/cem",
                   *(f"{name}/{category}" for name in ("traffic", "aggressive", "standard", "relaxed")
                     for category in ("acceleration", "braking", "following"))))
CONFIRM_ACTIONS = FEATURE_CONFIRM_ACTIONS | {DRIVER_MONITORING_RESET, CALIBRATION_RESET}
MAX_VIEWS = 64
MAX_INTENTS = 64
TTL = 60.0

# Android Auto lists binary controls only.
CAR_CONTROL_SECTIONS = (
  ("lateral", "Lateral (Steering)", ("aol", "lane_change", "torque", "lane", "vehicle")),
  ("longitudinal", "Longitudinal (Speed & Following)",
   ("profiles", "conditional", "curve", "slc", "aggressive", "standard", "relaxed", "traffic")),
  ("wheel", "Wheel Controls", ("wheel",)),
  ("visual", "Visual (Display & UI)", ("appearance", "pip")),
  ("sounds", "Sounds & Alerts", ("sounds",)),
  ("device", "Device & Data", ("display", "data")),
  ("localization", "Localization", ("localization",)),
  ("developer", "Developer", ("developer",)),
)


def _power_request(request: FeatureSettingsRequest) -> FeatureSettingsRequest:
  if request.key == HOURS:
    return replace(request, value=f"{float(request.value):.0f} h")
  if request.key == VOLTS:
    return replace(request, value=f"{float(request.value):.1f} V")
  return request


class SettingsUnavailable(Exception):
  pass


class SettingsChanged(Exception):
  pass


def boot_time_ns() -> int:
  return time.clock_gettime_ns(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))


@dataclass(frozen=True)
class AuthorityContext:
  parked: bool
  cp: object | None
  cp_raw: bytes | None
  metric: bool = False
  editing_saved_vehicle: bool = False
  configuration_longitudinal: bool = False
  configuration_vehicle: bool = False


class ContextSource(Protocol):
  def sample(self) -> AuthorityContext: ...


def _qualified(ctx: AuthorityContext, group: str) -> bool:
  cp = ctx.cp
  if group == "preferences":
    return True
  if group == "parked_preferences":
    return ctx.parked
  if group == "lane_live":
    from openpilot.starpilot.lateral.lane_runtime import runtime_supported
    return bool(cp is not None and not getattr(cp, "notCar", True) and not getattr(cp, "dashcamOnly", True) and
                not getattr(cp, "passive", True) and runtime_supported(cp))
  if cp is None:
    return False
  try:
    if not cp.carFingerprint:
      return False
    if group == "toyota_cruise":
      from openpilot.starpilot.controllers.toyota_cruise import capability
      return capability(cp) is not None
    if group == "vehicle":
      from opendbc.car.toyota.interface import toyota_auto_hold_supported
      return toyota_auto_hold_supported(cp)
    if group == "switchback_wheel":
      from openpilot.starpilot.feature_capabilities import lateral_supported
      return lateral_supported(cp)
    if group in ("conditional", "conditional_wheel", "long_output"):
      return bool((cp.openpilotLongitudinalControl or ctx.configuration_longitudinal) and not cp.notCar and not cp.dashcamOnly and not cp.passive)
    if group == "slc":
      return bool((cp.openpilotLongitudinalControl or ctx.configuration_longitudinal) and
                  not cp.notCar and not cp.dashcamOnly and not cp.passive)
    if group == "long":
      return bool((cp.openpilotLongitudinalControl or ctx.configuration_longitudinal) and
                  not cp.notCar and not cp.dashcamOnly and not cp.passive)
    if group in ("aol", "aol_wheel") and ctx.configuration_vehicle and ctx.parked:
      from openpilot.starpilot.aol.vehicle import configuration_settings_policy
      policy = configuration_settings_policy(cp)
      if policy is not None:
        return bool(policy.settings_supported and not cp.notCar and not cp.passive)
    if group in ("torque", "aol", "aol_wheel"):
      return bool(not cp.notCar and not cp.dashcamOnly and not cp.passive)
    return group in ("lane", "lane_change") and not cp.notCar and not cp.dashcamOnly and not cp.passive
  except (AttributeError, TypeError, ValueError):
    return False


def _onroad_preference(page: str, key: str) -> bool:
  # Regular saved choices are consumed by their runtime owners. Only explicit
  # multi-key reset/repair operations need the offroad transaction boundary;
  # their owners independently recheck that requirement at commit time.
  return bool(page not in ("developer", "power") and key and key != SETUP_ACTION and key not in (
    "IsRhdDetected", "AlphaLongitudinalEnabled", "reset_profiles", "curve_reset", "slc_reset",
    "conditional:reset", "conditional:manual_reset",
  ) and not is_long_confirm_action(key) and not key.startswith("torque_repair:"))


class LiveContextSource:
  """Samples registered Params and the two actual publisher clock domains."""

  MAX_CP_BYTES = 1_048_576
  MAX_LIVE_CP_AGE_NS = 100_000_000_000  # Card republishes finalized parameters every 50 seconds.

  def __init__(self, params, messages=None, *, mono_clock=time.monotonic_ns, boot_clock=boot_time_ns,
               evidence_wait_ms=750, wait_clock=time.monotonic, borrowed_messages=False):
    if borrowed_messages and messages is None:
      raise ValueError("Borrowed authority requires a message source")
    self.borrowed_messages = borrowed_messages
    self.params = params
    self.messages = messages
    self.mono_clock = mono_clock
    self.boot_clock = boot_clock
    self.evidence_wait_ms = evidence_wait_ms
    self.wait_clock = wait_clock
    self.lock = threading.Lock()
    self.closed = False
    self.physical = None
    self.device_offset_ns: int | None = None
    # A pre-start cached Python message can look mono-fresh after suspend.
    # Require a publisher stamp strictly after this collector starts.
    self.device_after_mono_ns = getattr(messages, "after_mono_ns", mono_clock())
    self.last_pair_offset_ns: int | None = None

  def _messages(self):
    if self.closed:
      return None
    if self.messages is None:
      from openpilot.cereal import messaging
      self.messages = messaging.SubMaster(["deviceState", "pandaStates", "carState", "selfdriveState", "carParams"])
    return cast(Callable[[], object], self.messages.snapshot)() if hasattr(self.messages, "snapshot") else self.messages

  def close(self) -> None:
    with self.lock:
      self.closed = True
      if self.physical is not None:
        self.physical.close()
        self.physical = None
      if self.messages is not None and not self.borrowed_messages:
        for sock in getattr(self.messages, "sock", {}).values():
          close = getattr(sock, "close", None)
          if close is not None:
            close()
      self.messages = None

  def observe_borrowed_device_clock(self) -> None:
    """Capture device publisher clock evidence at the collector's update frame.

    A borrowed collector's updated flag lasts only one UI frame. This observes
    clock metadata without advancing sockets or reading saved settings.
    """
    if not self.borrowed_messages:
      return
    with self.lock:
      if self.closed or self.messages is None:
        return
      before = self.mono_clock()
      boot = self.boot_clock()
      now = self.mono_clock()
      if now < before or now - before > RESUME_SKEW_NS:
        self.device_after_mono_ns = max(self.device_after_mono_ns, now)
        self.device_offset_ns = None
        return
      offset = boot - ((before + now) // 2)
      if self.last_pair_offset_ns is not None and abs(offset - self.last_pair_offset_ns) > RESUME_SKEW_NS:
        self.device_after_mono_ns = max(self.device_after_mono_ns, now)
        self.device_offset_ns = None
      self.last_pair_offset_ns = offset
      sm = self.messages
      if sm.updated['deviceState'] and int(sm.logMonoTime['deviceState']) > self.device_after_mono_ns:
        self.device_offset_ns = offset

  def parked(self) -> bool:
    """Settings and offroad operations follow the effective drive state."""
    return self.offroad()

  def offroad(self) -> bool:
    """Effective offroad mode also permits setup with the car powered on."""
    if host_parked():
      return not self.closed
    offroad, readable = read_saved(self.params, "IsOffroad", 8)
    if not readable or offroad != b"1":
      return False
    with self.lock:
      if self.closed:
        return False
      try:
        deadline = self.wait_clock() + self.evidence_wait_ms / 1000
        sm = self._messages()
        timeout = 0
        while True:
          if sm is None:
            return False
          if hasattr(sm, "after_mono_ns"):
            self.device_after_mono_ns = max(self.device_after_mono_ns, sm.after_mono_ns)
            self.device_offset_ns = sm.device_offset_ns
          elif not self.borrowed_messages:
            sm.update(timeout)
          mono_before = self.mono_clock()
          boot_now = self.boot_clock()
          mono_now = self.mono_clock()
          if mono_now < mono_before or mono_now - mono_before > RESUME_SKEW_NS:
            return False
          pair_offset = boot_now - ((mono_before + mono_now) // 2)
          if self.last_pair_offset_ns is not None and abs(pair_offset - self.last_pair_offset_ns) > RESUME_SKEW_NS:
            self.device_after_mono_ns = max(self.device_after_mono_ns, mono_now)
            self.device_offset_ns = None
          self.last_pair_offset_ns = pair_offset
          if sm.updated["deviceState"] and int(sm.logMonoTime["deviceState"]) > self.device_after_mono_ns:
            self.device_offset_ns = pair_offset
          pandas = sm["pandaStates"]
          if sm.seen["deviceState"] and sm["deviceState"].started:
            return False
          evidence = ParkedEvidence(
            True, bool(sm.seen["deviceState"]), bool(sm.alive["deviceState"]), bool(sm.valid["deviceState"]),
            bool(sm["deviceState"].started), int(sm.logMonoTime["deviceState"]),
            int(sm.recv_time["deviceState"] * 1e9), self.device_offset_ns, self.device_after_mono_ns,
            bool(sm.seen["pandaStates"]), bool(sm.alive["pandaStates"]), bool(sm.valid["pandaStates"]),
            int(sm.logMonoTime["pandaStates"]), int(sm.recv_time["pandaStates"] * 1e9),
            tuple(bool(p.ignitionLine or p.ignitionCan) for p in pandas),
          )
          if fresh_offroad(evidence, now_mono_ns=mono_now, now_boot_ns=boot_now):
            offroad, readable = read_saved(self.params, "IsOffroad", 8)
            return bool(readable and offroad == b"1")
          remaining_ms = int((deadline - self.wait_clock()) * 1000)
          if remaining_ms <= 0:
            return False
          timeout = min(50, remaining_ms)
      except (OSError, RuntimeError, AttributeError, TypeError, ValueError, OverflowError):
        return False

  def configuration_allowed(self) -> bool:
    """Allow configuration in effective offroad mode or a fresh stationary Park."""
    if self.offroad():
      return True
    with self.lock:
      if self.closed:
        return False
      try:
        self._messages()
        if not self.borrowed_messages and not hasattr(self.messages, 'snapshot'):
          self.messages.update(0)
        if self.physical is None:
          from openpilot.starpilot.drive_state.evidence import PhysicalSource
          self.physical = PhysicalSource(self.messages, mono=self.mono_clock, boot=self.boot_clock)
        return self.physical.allowed()
      except (OSError, RuntimeError, AttributeError, KeyError, TypeError, ValueError):
        return False

  def runtime_vehicle(self):
    try:
      with self.lock:
        if self.closed:
          return None, None
        sm = self._messages() if self.messages is not None else None
        if sm is not None:
          if not self.borrowed_messages and not hasattr(self.messages, 'snapshot'):
            sm.update(0)
          now = self.mono_clock()
          stamp = int(sm.logMonoTime.get('carParams', 0))
          floor = max(self.device_after_mono_ns, getattr(sm, 'after_mono_ns', 0))
          if (sm.seen.get('carParams', False) and sm.valid.get('carParams', False) and sm.alive.get('carParams', False) and
              floor < stamp <= now and now - stamp <= self.MAX_LIVE_CP_AGE_NS):
            from opendbc.car.values import PLATFORMS
            from opendbc.car.mock.values import CAR as MOCK
            from opendbc.car.structs import car
            cp = sm['carParams']
            if (cp.schema.node.id == car.CarParams.schema.node.id and not cp.notCar and
                cp.carFingerprint in PLATFORMS and cp.carFingerprint != MOCK.MOCK):
              raw = cp.as_builder().to_bytes()
              if len(raw) <= self.MAX_CP_BYTES:
                return cp, b'live-vehicle:' + raw
    except (OSError, RuntimeError, AttributeError, KeyError, TypeError, ValueError, OverflowError):
      pass
    cp_raw, readable = read_saved(self.params, "CarParamsPersistent", self.MAX_CP_BYTES)
    cp = None
    if readable and cp_raw is not None:
      try:
        from openpilot.cereal import messaging
        from opendbc.car.structs import car
        from openpilot.starpilot.schema_cache import inspect_cache
        inspected = inspect_cache("CarParamsPersistent", cp_raw)
        if inspected.status == "valid" and inspected.payload is not None:
          cp = messaging.log_from_bytes(inspected.payload, car.CarParams)
      except (OSError, RuntimeError, TypeError, ValueError, OverflowError):
        cp = None
    return cp, cp_raw if readable else None

  def sample(self) -> AuthorityContext:
    parked = self.configuration_allowed()
    cp, cp_raw = self.runtime_vehicle()
    readable = True
    editing_saved = configurable_long = configuration_vehicle = False
    if parked and self.offroad():
      from openpilot.starpilot.galaxy.vehicle_configuration import settings_configuration
      cp, cp_raw, editing_saved, configurable_long = settings_configuration(self.params, cp, cp_raw if readable else None)
      configuration_vehicle = cp is not None
      readable = True
    units, unit_readable = read_saved(self.params, "IsMetric", 8)
    return AuthorityContext(parked, cp, cp_raw if readable else None, bool(unit_readable and units == b"1"),
                            editing_saved, configurable_long, configuration_vehicle)


@dataclass(frozen=True)
class _View:
  session: bytes
  generation: bytes
  page: str
  rows: tuple[FeatureRow, ...]
  cp_raw: bytes | None
  expires: float


@dataclass(frozen=True)
class _Intent:
  session: bytes
  generation: bytes
  page: str
  request: FeatureSettingsRequest
  cp_raw: bytes | None
  expires: float
  reset_row: FeatureRow | None = None
  recovery_default: bool = False


def _session_key(token: str) -> bytes:
  return hashlib.sha256(token.encode("ascii")).digest()


def _projection(row: FeatureRow) -> dict:
  return {"label": row.label, "value": row.value, "choices": list(row.choices), "step": row.step,
          "minimum": row.minimum, "maximum": row.maximum, "unit": row.unit,
          "available": row.available, "reason": description(row),
          "page": row.page if row.page in PAGES else "", "action": bool(row.key and row.available and not row.page),
          "confirm": bool(row.key in CONFIRM_ACTIONS or is_long_confirm_action(row.key) or
                          row.key in (PIP_RESET, PIP_EDITOR, VASM_RESET, VASM_ANNOTATION) or row.key.startswith(PIP_FORMAT_PREFIX)),
          "repairValue": row.repair_value, "defaultValue": row.default_value, "resetAvailable": row_default(row) is not None,
          "displayScale": "lane-strength" if row.key == "LaneCenteringStrength" else None,
          **({"coordinate": row.key.removeprefix("torque:surface:")} if row.key.startswith("torque:surface:") and row.key != "torque:surface:reset" else {}),
          "heading": is_heading(row.key), "collapsed": bool(row.collapsed),
          **({"previewSound": row.key} if row.key in SOUND_SPECS else {})}


def _question(row: FeatureRow, request: FeatureSettingsRequest) -> str:
  key = request.key
  if key in POWER_KEYS:
    return power_question(_power_request(request))
  if key == "LaneCenteringStrength":
    raw = float(request.value)
    level = (1 + (raw - row.minimum) * 4 / (100 - row.minimum) if raw <= 100 else
             5 + (raw - 100) * 5 / (row.maximum - 100))
    return f"Set lane centering strength to {level:.2f}? 5 is normal."
  if key == "UseStarPilotLongitudinalPlanner":
    return f"Select {request.value} for {row.label.lower()}? The selected planner starts with the next driving process."
  if key in ("CustomCruise", "CustomCruiseLong"):
    if row.capability and row.capability[0] == "toyota":
      return f"Save {row.label.lower()} as {request.value}? Toyota cruise preferences refresh while this compatible drive is active."
    value = f"{request.value} {row.unit}" if row.unit else request.value
    return f"Set {row.label.lower()} to {value}? Cruise-button increments refresh during the drive."
  if key in SOUND_SPECS:
    value = request.value + ("%" if request.value != "Auto" and not request.value.endswith("%") else "")
    return f"Save {row.label.lower()} as {value}? The normal alert sound still decides when it plays."
  if key in DISPLAY_LABELS or key.startswith(DISPLAY_AUTO_PREFIX):
    value = request.value + (" " + row.unit if row.unit and request.value != "Auto" and not request.value.endswith(row.unit) else "")
    return f"Save {row.label.lower()} as {value}? This changes only the display."
  if key in APPEARANCE_LABELS:
    return f"Save {row.label} as {request.value}? This changes only the display."
  if key == VASM_RESET:
    return "Replace saved spot-monitor settings with Off and unconfigured defaults? Saved regions will be lost."
  if key == VASM_ANNOTATION:
    annotation = validated_annotation(request.value)
    if annotation is None:
      return "Invalid camera regions"
    return "Save these camera window regions?"
  if key.startswith("vasm:"):
    value = f"{request.value} {row.unit}".rstrip()
    return f"Save {row.label.lower()} as {value}?"
  if key == SENTRY_RESET:
    return "Replace invalid saved motion settings with Off and the default sensitivity and warning time?"
  if key == CALIBRATION_RESET:
    return "Reset camera and steering calibration? Automatic steering learning will be invalidated and the device will recalibrate on the next drive."
  if key == DRIVER_MONITORING_RESET:
    return "Clear saved driver monitoring wheel-side detection? Automatic detection will run again on the next drive."
  if key.startswith("sentry:"):
    value = f"{request.value} {row.unit}".rstrip()
    return (f"Save {row.label.lower()} as {value}? This is a saved choice; " +
            "development opt-in and fresh parked sensor evidence remain separate.")
  if key == PIP_RESET:
    return "Replace the saved side-camera crop with the default? Prior crop alignment will be lost."
  if key == PIP_EDITOR:
    return "Save these source-image crop positions? Confirm alignment on the device before using the preview."
  if key.startswith(PIP_FORMAT_PREFIX):
    return f"{row.label}? This replaces the saved crop; check alignment on the device."
  if key == "slc_reset":
    return f"Reset speed-limit offsets to zero? {row.value}. Saved control may resume if its switch is on."
  if key == "curve_reset":
    return "Erase learned curve data? Curve Speed Controller keeps its current On or Off choice."
  if key.startswith("conditional:"):
    return conditional_question(request, row.label, row.unit)
  if key == "reset_profiles":
    return "Reset invalid saved profiles and turn their saved switch off?"
  if key in ("profile:global_acceleration", "profile:global_braking"):
    return (f"Save {row.label.lower()} as {request.value}? Personalities set to Selected Profile follow this choice; " +
            "explicit personality overrides stay unchanged.")
  if key == "torque:surface:reset":
    return "Remove the thirty manual overrides for this vehicle? Saved Auto Learn history is kept."
  if key == "torque_gain_rebase":
    return "Apply the saved steering response to the selected controller and current vehicle tune? Friction and other models stay unchanged."
  if key == "torque_rebase":
    return f"Reapply saved torque values ({row.value}) with the current vehicle tune?"
  if key == "LateralControllerSelection":
    return f"Select {request.value}? The controller and its feedback state are created when the driving process starts."
  if key == SETUP_ACTION:
    return SETUP_QUESTION
  if key.startswith("torque_"):
    return f"{row.label}? Prior saved values remain but may become inactive."
  if key.startswith("long_"):
    from openpilot.starpilot.ui.long_profile_feature import long_confirm_question
    return long_confirm_question(row)
  if key.startswith("lane_change:"):
    return lane_change_question(request)
  if key in VEHICLE_BOOL_KEYS:
    return vehicle_question(request)
  if key == "ToyotaAutoHold":
    return f"Set automatic brake hold to {request.value}? The next startup configures the required brake-hold safety permissions."
  value = f"{request.value} {row.unit}".rstrip()
  return f"Set {row.label.lower()} to {value}?"


class SettingsGateway:
  def __init__(self, params, context: ContextSource | None = None, *, clock=time.monotonic, starport=None):
    self.params = params
    self.context = context if context is not None else LiveContextSource(params)
    self.clock = clock
    self.starport = starport
    self.lock = threading.Lock()
    self.views: dict[str, _View] = {}
    self.intents: dict[str, _Intent] = {}

  def close(self) -> None:
    close = getattr(self.context, "close", None)
    if close is not None:
      close()

  def diagnostics(self) -> dict:
    """Read the same bounded, non-secret presentation used by driving settings."""
    with self.lock:
      ctx = self.context.sample()
      sections = []
      for page in ("torque", "lane", "lane_change", "aol", "conditional", "profiles", "slc", "curve"):
        state = self._state(page, ctx)
        sections.append({"title": state.title,
                         "rows": [{"label": row.label, "value": row.value} for row in state.rows[:64]]})
      cp = ctx.cp
      vehicle = {"available": cp is not None, "fingerprint": str(getattr(cp, "carFingerprint", "")),
                 "brand": str(getattr(cp, "brand", "")),
                 "longitudinal": bool(getattr(cp, "openpilotLongitudinalControl", False)),
                 "steering": str(getattr(cp, "steerControlType", ""))}
      return {"schemaVersion": 1, "vehicle": vehicle, "sections": sections,
              "snapshot": [{"label": "Units", "value": "Metric" if ctx.metric else "Imperial"}],
              "note": "Saved configuration is shown here. It does not confirm that a feature is active while driving. Change settings in Toggles."}

  def _context(self, page: str) -> AuthorityContext:
    return self.context.sample()

  def _owner(self, ctx: AuthorityContext, *, live: bool = False,
             session_valid: Callable[[], bool] = lambda: True) -> FeatureSettingsOwner:
    def current() -> AuthorityContext:
      if not session_valid():
        return AuthorityContext(False, None, None)
      fresh = self.context.sample() if live else ctx
      return fresh if fresh.cp_raw == ctx.cp_raw else AuthorityContext(False, None, fresh.cp_raw)
    def authorized(group: str) -> bool:
      if not session_valid():
        return False
      fresh = self.context.sample() if live else ctx
      return fresh.cp_raw == ctx.cp_raw and _qualified(fresh, group)
    return FeatureSettingsOwner(self.params, authorized,
                                vehicle_fingerprint=lambda: getattr(current().cp, "carFingerprint", None),
                                vehicle_params=lambda: current().cp,
                                vision_development=lambda: diagnostic_choice_enabled(os.environ, current().cp),
                                show_cruise_intervals=True,
                                configuration_longitudinal=lambda: current().configuration_longitudinal,
                                configuration_vehicle=lambda: current().configuration_vehicle,
                                galaxy=lambda: not current().editing_saved_vehicle)

  def _pip_owner(self, ctx: AuthorityContext, *, live: bool = False,
                 session_valid: Callable[[], bool] = lambda: True) -> PiPOwner:
    def parked() -> bool:
      if not session_valid():
        return False
      fresh = self.context.sample() if live else ctx
      return fresh.cp_raw == ctx.cp_raw
    return PiPOwner(self.params, parked, lambda: False)

  def _appearance_owner(self, ctx: AuthorityContext, *, live: bool = False,
                        session_valid: Callable[[], bool] = lambda: True) -> AppearanceOwner:
    def authorized() -> bool:
      if not session_valid():
        return False
      fresh = self.context.sample() if live else ctx
      return fresh.cp_raw == ctx.cp_raw
    return AppearanceOwner(self.params, authorized)

  def _sounds_owner(self, ctx: AuthorityContext, *, live: bool = False,
                    session_valid: Callable[[], bool] = lambda: True) -> SoundsOwner:
    def authorized() -> bool:
      if not session_valid():
        return False
      fresh = self.context.sample() if live else ctx
      return fresh.cp_raw == ctx.cp_raw
    return SoundsOwner(self.params, authorized)

  def _power_owner(self, ctx: AuthorityContext, *, live: bool = False,
                   session_valid: Callable[[], bool] = lambda: True) -> PowerOwner:
    def parked() -> bool:
      if not session_valid():
        return False
      fresh = self.context.sample() if live else ctx
      return fresh.parked and fresh.cp_raw == ctx.cp_raw
    return PowerOwner(self.params, parked)

  def _display_owner(self, ctx: AuthorityContext, *, live: bool = False,
                     session_valid: Callable[[], bool] = lambda: True) -> DisplayOwner:
    def parked() -> bool:
      if not session_valid():
        return False
      fresh = self.context.sample() if live else ctx
      return fresh.cp_raw == ctx.cp_raw
    return DisplayOwner(self.params, parked)

  def _sentry_owner(self, ctx: AuthorityContext, *, live: bool = False,
                    session_valid: Callable[[], bool] = lambda: True) -> SentryOwner:
    def parked() -> bool:
      if not session_valid():
        return False
      fresh = self.context.sample() if live else ctx
      return fresh.cp_raw == ctx.cp_raw
    return SentryOwner(self.params, parked)

  def _vasm_owner(self, ctx: AuthorityContext, *, live: bool = False,
                  session_valid: Callable[[], bool] = lambda: True) -> VASMOwner:
    def parked() -> bool:
      if not session_valid():
        return False
      fresh = self.context.sample() if live else ctx
      return fresh.cp_raw == ctx.cp_raw
    return VASMOwner(self.params, parked)

  def _developer_owner(self, ctx: AuthorityContext, *, live: bool = False,
                       session_valid: Callable[[], bool] = lambda: True) -> DeveloperSettingsOwner:
    def authorized() -> bool:
      if not session_valid():
        return False
      fresh = self.context.sample() if live else ctx
      return bool(fresh.parked and not fresh.editing_saved_vehicle and fresh.cp_raw == ctx.cp_raw)
    return DeveloperSettingsOwner(self.params, authorized,
                                  panda_wake=self._owner(ctx, live=live, session_valid=session_valid).panda_wake,
                                  starport=self.starport)

  def _localization_owner(self, ctx: AuthorityContext, *, live: bool = False,
                          session_valid: Callable[[], bool] = lambda: True) -> LocalizationSettingsOwner:
    def authorized() -> bool:
      return session_valid()
    def parked() -> bool:
      return session_valid() and (self.context.sample() if live else ctx).parked
    return LocalizationSettingsOwner(self.params, authorized, parked)

  def _state(self, page: str, ctx: AuthorityContext):
    if page == "localization":
      return self._localization_owner(ctx).snapshot()
    if page == "developer":
      return self._developer_owner(ctx).snapshot()
    if page == "appearance":
      return self._appearance_owner(ctx).snapshot(Profile.COMPACT, include_large=True)
    if page == "sounds":
      return self._sounds_owner(ctx).snapshot()
    if page == "power":
      state = self._power_owner(ctx).snapshot()
      rows = tuple(replace(row, value=row.value.removesuffix(" h").removesuffix(" V"), choices=(),
                           default_value=row.default_value.removesuffix(" h").removesuffix(" V"),
                           minimum=1 if row.key == HOURS else 11.8, maximum=30 if row.key == HOURS else 12.5,
                           step=1 if row.key == HOURS else 0.1, unit="h" if row.key == HOURS else "V")
                   if row.key in (HOURS, VOLTS) else row for row in state.rows)
      cooling = self._owner(ctx).snapshot("power", parked=ctx.parked, system_long=False, lateral_context=False,
                                          metric=ctx.metric, configure_while_driving=True)
      return replace(state, title="Power and Cooling", rows=rows + tuple(cooling.rows))
    if page == "display":
      return self._display_owner(ctx).snapshot(Profile.COMPACT if HARDWARE.get_device_type() == "mici" else Profile.LARGE)
    if page == "pip":
      return self._pip_owner(ctx).snapshot(include_editor=True)
    if page == "sentry":
      return self._sentry_owner(ctx).snapshot()
    if page == "vasm":
      return self._vasm_owner(ctx).snapshot()
    state = self._owner(ctx).snapshot(page, parked=ctx.parked,
                                      system_long=_qualified(ctx, "long"),
                                      lateral_context=_qualified(ctx, "lane") or (page == "lane" and _qualified(ctx, "lane_live")),
                                      metric=ctx.metric, configure_while_driving=True)
    if page == "torque":
      state = replace(state, rows=tuple(row for row in state.rows if row.key not in ("torque:factor:reset", "torque:friction:reset")))
    if ctx.editing_saved_vehicle:
      from opendbc.car.values import PLATFORMS
      platform = PLATFORMS[ctx.cp.carFingerprint]
      label = platform.config.car_docs[0].name if platform.config.car_docs else str(ctx.cp.carFingerprint).replace("_", " ").title()
      return replace(state, subtitle=f"{label} · Saved vehicle settings. {state.subtitle}")
    return state

  def _clean(self) -> None:
    now = self.clock()
    self.views = {key: value for key, value in self.views.items() if value.expires > now}
    self.intents = {key: value for key, value in self.intents.items() if value.expires > now}

  def page(self, page: str, token: str, generation: bytes) -> dict:
    if page not in PAGES:
      raise SettingsUnavailable("Unknown page")
    ctx = self._context(page)
    state = self._state(page, ctx)
    editor = None
    if page == "vasm":
      editor, editor_source = self._vasm_owner(ctx).editor_with_source()
      row_source = next((row.source for row in state.rows if row.key), None)
      if any(row.key for row in state.rows) and editor_source != row_source:
        raise SettingsChanged("Saved camera regions changed")
    elif page == "pip":
      editor, editor_dependencies = self._pip_owner(ctx).editor_with_source()
      row_dependencies = state.rows[0].dependencies if state.rows else None
      if row_dependencies is not None and editor_dependencies != row_dependencies:
        raise SettingsChanged("Saved camera crop changed")
    with self.lock:
      self._clean()
      if len(self.views) >= MAX_VIEWS:
        self.views.pop(next(iter(self.views)))
      view_id = secrets.token_urlsafe(24)
      self.views[view_id] = _View(_session_key(token), generation, page, state.rows, ctx.cp_raw, self.clock() + TTL)
    scope = hashlib.sha256(_session_key(token) + generation + (ctx.cp_raw or b"")).digest()
    result = {"page": page, "title": state.title, "subtitle": state.subtitle, "parked": state.parked,
              "vehicleIdentified": bool(ctx.cp is not None and getattr(ctx.cp, "carFingerprint", None)),
              "rows": [{**_projection(row), "revision": hashlib.sha256(scope + repr(row).encode()).hexdigest()}
                       for row in state.rows], "view": view_id}
    if page == "hub" or any(row.page for row in state.rows):
      rows_by_page = {page: state}
      visible: dict[str, bool] = {}

      def page_state(name: str):
        if name not in rows_by_page:
          rows_by_page[name] = self._state(name, ctx)
        return rows_by_page[name]

      def has_car_control(name: str, visited: frozenset[str] = frozenset()) -> bool:
        if name in visited or name not in PAGES:
          return False
        if name in visible:
          return visible[name]
        try:
          rows = page_state(name).rows
        except SettingsUnavailable:
          visible[name] = False
          return False
        found = any((len(row.choices) == 2 and set(row.choices) == {"On", "Off"}) or
                    has_car_control(row.page, visited | {name}) for row in rows)
        visible[name] = found
        return found
      if page == "hub":
        result["sections"] = []
        for section_id, title, names in CAR_CONTROL_SECTIONS:
          pages = [{"id": name, "title": page_state(name).title} for name in names if has_car_control(name)]
          if pages:
            result["sections"].append({"id": section_id, "title": title, "pages": pages})
      else:
        for source, projected in zip(state.rows, result["rows"], strict=False):
          if source.page:
            projected["carVisible"] = has_car_control(source.page)
    if page == "vasm":
      result["editor"] = editor
      result["editorRow"] = next((index for index, row in enumerate(state.rows) if row.key == VASM_ANNOTATION), -1)
    elif page == "pip":
      result["editor"] = editor
      result["editorRow"] = next((index for index, row in enumerate(state.rows)
                                  if row.key == PIP_EDITOR and row.available), -1)
    return result

  def preview(self, view_id: str, index: int, direction: int, token: str, generation: bytes,
              *, draft: dict | None = None, value: str | int | float | None = None, reset_default: bool = False, recovery_default: bool = False) -> dict:
    with self.lock:
      self._clean()
      view = self.views.get(view_id)
    if view is None or view.session != _session_key(token) or view.generation != generation or \
       type(index) is not int or not 0 <= index < len(view.rows) or type(direction) is not int or direction not in (-1, 0, 1):
      raise SettingsChanged("Refresh settings")
    ctx = self._context(view.page)
    if ctx.cp_raw != view.cp_raw:
      raise SettingsChanged("Vehicle context changed")
    state = self._state(view.page, ctx)
    if state.rows != view.rows:
      raise SettingsChanged("Saved settings changed")
    row = state.rows[index]
    if ((not row.available and not (reset_default and recovery_default and ctx.parked and
                                    row_default(row, allow_inactive=True) is not None)) or
        not (ctx.parked or _onroad_preference(view.page, row.key))):
      raise SettingsChanged("Settings are unavailable")
    if draft is not None and not ((view.page == "vasm" and row.key == VASM_ANNOTATION) or
                                 (view.page == "pip" and row.key == PIP_EDITOR)):
      raise SettingsChanged("Unexpected camera region draft")
    special = (row.key in CONFIRM_ACTIONS or is_long_confirm_action(row.key) or
               row.key in (PIP_RESET, PIP_EDITOR, SENTRY_RESET, VASM_RESET, VASM_ANNOTATION) or row.key.startswith(PIP_FORMAT_PREFIX))
    if reset_default:
      if direction != 0 or value is not None or draft is not None:
        raise SettingsChanged("Invalid default reset")
      request = row_default(row, allow_inactive=recovery_default)
      if request is None:
        raise SettingsChanged("Default is unavailable")
    elif value is not None:
      if direction != 0 or draft is not None or special or row.repair_value or type(value) not in (str, int, float):
        raise SettingsChanged("Invalid value selection")
      if isinstance(value, str) and len(value) > 128:
        raise SettingsChanged("Invalid value selection")
      if row.choices and isinstance(value, str) and value in row.choices:
        selected = value
      elif row.step > 0 and (not row.choices or "Auto" in row.choices):
        try:
          number = float(value)
        except (ValueError, OverflowError) as error:
          raise SettingsChanged("Invalid numeric value") from error
        if not math.isfinite(number) or not row.minimum <= number <= row.maximum:
          raise SettingsChanged("Value outside setting range")
        steps = (number - row.minimum) / row.step
        if not math.isclose(steps, round(steps), rel_tol=0, abs_tol=1e-7):
          raise SettingsChanged("Value does not match setting step")
        selected = str(number)
      elif row.choices:
        raise SettingsChanged("Unavailable choice")
      else:
        raise SettingsChanged("Unavailable value selection")
      request = FeatureSettingsRequest(row.key, row.source, selected, confirmation=True,
                                       related_source=row.related_source, vehicle_fingerprint=row.vehicle_fingerprint,
                                       capability=row.capability, dependencies=row.dependencies, display_unit=row.display_unit)
    elif special:
      if direction != 0:
        raise SettingsChanged("Invalid action")
      value = ("Off" if row.value == "On" else "On") if row.key == SETUP_ACTION else "confirm"
      if row.key == "torque:surface:reset":
        value = "Reset"
      if row.key == VASM_ANNOTATION:
        if draft is None:
          raise SettingsChanged("Camera regions are required")
        try:
          value = json.dumps(draft, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
          raise SettingsChanged("Invalid camera regions") from error
        if validated_annotation(value) is None:
          raise SettingsChanged("Invalid camera regions")
      elif row.key == PIP_EDITOR:
        if draft is None:
          raise SettingsChanged("Camera crop is required")
        try:
          value = json.dumps(draft, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
          raise SettingsChanged("Invalid camera crop") from error
        if validated_editor_draft(value, read_pip(self.params).mask) is None:
          raise SettingsChanged("Invalid camera crop")
      request = FeatureSettingsRequest(row.key, row.source, value, confirmation=True,
                                       related_source=row.related_source, vehicle_fingerprint=row.vehicle_fingerprint,
                                       capability=row.capability, dependencies=row.dependencies, display_unit=row.display_unit)
    else:
      if direction == 0:
        raise SettingsChanged("Invalid action")
      request = row_change(row, direction)
      if request is None:
        raise SettingsChanged("Unavailable choice")
      request = replace(request, confirmation=True)
    with self.lock:
      self._clean()
      if len(self.intents) >= MAX_INTENTS:
        self.intents.pop(next(iter(self.intents)))
      intent_id = secrets.token_urlsafe(24)
      self.intents[intent_id] = _Intent(_session_key(token), generation, view.page, request, ctx.cp_raw, self.clock() + TTL,
                                         row if reset_default else None, recovery_default)
    question = _question(row, request)
    if reset_default:
      question = f"Reset {row.label} to its default? " + question
    return {"intent": intent_id, "question": question, "proposed": request.value}

  def confirm(self, intent_id: str, token: str, generation: bytes,
              *, session_valid: Callable[[], bool] = lambda: True) -> bool:
    with self.lock:
      self._clean()
      intent = self.intents.get(intent_id)
      if intent is None or intent.session != _session_key(token) or intent.generation != generation:
        raise SettingsChanged("Confirmation expired")
      del self.intents[intent_id]
    ctx = self._context(intent.page)
    if not session_valid() or not (ctx.parked or _onroad_preference(intent.page, intent.request.key)) or ctx.cp_raw != intent.cp_raw:
      raise SettingsChanged("Vehicle changed or this operation requires offroad mode")
    if intent.reset_row is not None:
      fresh_rows = self._state(intent.page, ctx).rows
      if intent.reset_row not in fresh_rows or row_default(intent.reset_row, allow_inactive=intent.recovery_default) != intent.request:
        raise SettingsChanged("Default or saved settings changed")
    if intent.page == "pip":
      return self._pip_owner(ctx, live=True, session_valid=session_valid).apply(intent.request)
    if intent.page == "appearance":
      return self._appearance_owner(ctx, live=True, session_valid=session_valid).apply(intent.request)
    if intent.page == "sounds":
      return self._sounds_owner(ctx, live=True, session_valid=session_valid).apply(intent.request)
    if intent.page == "power" and intent.request.key != AGGRESSIVE_COOLING_KEY:
      return self._power_owner(ctx, live=True, session_valid=session_valid).apply(_power_request(intent.request))
    if intent.page == "display":
      return self._display_owner(ctx, live=True, session_valid=session_valid).apply(intent.request)
    if intent.page == "sentry":
      return self._sentry_owner(ctx, live=True, session_valid=session_valid).apply(intent.request)
    if intent.page == "vasm":
      return self._vasm_owner(ctx, live=True, session_valid=session_valid).apply(intent.request)
    if intent.page == "localization":
      return self._localization_owner(ctx, live=True, session_valid=session_valid).apply(intent.request)
    if intent.page == "developer":
      return self._developer_owner(ctx, live=True, session_valid=session_valid).apply(intent.request)
    owner = self._owner(ctx, live=True, session_valid=session_valid)
    return owner.apply(intent.request, reset_default=True) if intent.recovery_default else owner.apply(intent.request)
