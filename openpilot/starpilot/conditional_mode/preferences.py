"""Strict saved conditional-mode preferences, without a Params or runtime owner.

Decoding a saved choice does not grant longitudinal authority. The host checks
fresh vehicle capability, SafeMode, and the current drive before using it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from collections.abc import Mapping
import json
import math
import re
from typing import Any, cast

from openpilot.starpilot.conditional_mode.policy import ManualIntent, ModeChoice, ModeSettings


SCHEMA_VERSION = 1
MAX_DOCUMENT_BYTES = 4096
MPH_TO_MPS = 0.44704
MAX_SPEED_MPS = 99 * MPH_TO_MPS  # UI caps: 99 mph or 150 km/h.
MAX_MARGIN_MPS = 30 / 3.6  # Metric cap; the imperial cap is 15 mph.


class PreferenceError(ValueError):
  """A saved document is malformed or unsupported; callers must not rewrite it."""


@dataclass(frozen=True)
class CEMOptions:
  speed_mps: float = 0.0
  speed_with_lead_mps: float = 0.0
  signal_speed_mps: float = 0.0
  open_road: bool = False
  curves: bool = False
  curves_with_lead: bool = False
  lead: bool = True
  slower_lead: bool = True
  stopped_lead: bool = True
  stop_lights: bool = True
  model_stop_s: float = 7.7
  signal_lane_detection: bool = True
  signal_lane_width_m: float = 0.0
  persist_manual: bool = False


@dataclass(frozen=True)
class CCMOptions:
  speed_mps: float = 45 * MPH_TO_MPS
  speed_with_lead_mps: float = 35 * MPH_TO_MPS
  set_speed_margin_mps: float = 3 * MPH_TO_MPS
  lead: bool = True
  launch_assist: bool = False
  persist_manual: bool = False


@dataclass(frozen=True)
class SavedPreferences:
  version: int = SCHEMA_VERSION
  mode: ModeChoice = ModeChoice.CEM
  cem: CEMOptions = field(default_factory=CEMOptions)
  ccm: CCMOptions = field(default_factory=CCMOptions)

  def mode_settings(self) -> ModeSettings:
    """Project the subset currently consumed by the pure policy in SI units."""
    return ModeSettings(
      cem_speed_mps=self.cem.speed_mps,
      cem_speed_with_lead_mps=self.cem.speed_with_lead_mps,
      cem_signal_mps=self.cem.signal_speed_mps,
      cem_open_road=self.cem.open_road,
      cem_curves=self.cem.curves,
      cem_curves_with_lead=self.cem.curves_with_lead,
      cem_lead=self.cem.lead,
      cem_stop=self.cem.stop_lights and self.cem.model_stop_s > 0.0,
      ccm_speed_mps=self.ccm.speed_mps,
      ccm_speed_with_lead_mps=self.ccm.speed_with_lead_mps,
      ccm_set_speed_margin_mps=self.ccm.set_speed_margin_mps,
      ccm_lead=self.ccm.lead,
      ccm_launch=self.ccm.launch_assist,
    )


def _number(value: object, maximum: float) -> bool:
  if type(value) not in (int, float):
    return False
  number = cast(int | float, value)
  try:
    return math.isfinite(number) and 0.0 <= number <= maximum
  except OverflowError:
    return False


def _validate(preferences: SavedPreferences) -> None:
  if (type(preferences) is not SavedPreferences or type(preferences.version) is not int or
      preferences.version != SCHEMA_VERSION or type(preferences.mode) is not ModeChoice or
      type(preferences.cem) is not CEMOptions or type(preferences.ccm) is not CCMOptions):
    raise PreferenceError('invalid conditional-mode preference type or version')
  cem, ccm = preferences.cem, preferences.ccm
  speeds = (cem.speed_mps, cem.speed_with_lead_mps, cem.signal_speed_mps,
            ccm.speed_mps, ccm.speed_with_lead_mps)
  if not all(_number(value, MAX_SPEED_MPS) for value in speeds):
    raise PreferenceError('invalid speed in conditional-mode preferences')
  if (not _number(cem.model_stop_s, 9.0) or not _number(cem.signal_lane_width_m, 15.0) or
      not _number(ccm.set_speed_margin_mps, MAX_MARGIN_MPS)):
    raise PreferenceError('invalid stop, lane, or set-speed margin value')
  booleans = (cem.open_road, cem.curves, cem.curves_with_lead, cem.lead, cem.slower_lead,
              cem.stopped_lead, cem.stop_lights, cem.signal_lane_detection, cem.persist_manual,
              ccm.lead, ccm.launch_assist, ccm.persist_manual)
  if not all(type(value) is bool for value in booleans):
    raise PreferenceError('invalid conditional-mode boolean')


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
  result = {}
  for key, value in pairs:
    if key in result:
      raise PreferenceError(f'duplicate conditional-mode field: {key}')
    result[key] = value
  return result


def _invalid_json_constant(value: str):
  raise PreferenceError(f'invalid JSON constant: {value}')


def _typed_section(value: object, section_type: type[CEMOptions] | type[CCMOptions]):
  expected = {field.name for field in fields(section_type)}
  if type(value) is not dict or set(value) != expected:
    raise PreferenceError('missing or unknown conditional-mode option')
  return section_type(**cast(dict[str, Any], value))


def decode_preferences(raw: bytes | str) -> SavedPreferences:
  """Decode an exact v1 document; absence and corruption are caller decisions."""
  if type(raw) is bytes:
    if len(raw) > MAX_DOCUMENT_BYTES:
      raise PreferenceError('conditional-mode document too large')
    try:
      raw = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError as exc:
      raise PreferenceError('conditional-mode document is not UTF-8') from exc
  if type(raw) is not str:
    raise PreferenceError('invalid conditional-mode document input')
  try:
    if len(raw.encode('utf-8')) > MAX_DOCUMENT_BYTES:
      raise PreferenceError('conditional-mode document too large')
    document = json.loads(raw, object_pairs_hook=_object_pairs, parse_constant=_invalid_json_constant)
  except (ValueError, UnicodeEncodeError, RecursionError) as exc:
    raise PreferenceError('invalid conditional-mode JSON') from exc
  if type(document) is not dict or set(document) != {'version', 'mode', 'cem', 'ccm'}:
    raise PreferenceError('missing or unknown conditional-mode document field')
  if type(document['mode']) is not str:
    raise PreferenceError('invalid conditional-mode choice')
  try:
    mode = ModeChoice(document['mode'])
  except ValueError as exc:
    raise PreferenceError('unknown conditional-mode choice') from exc
  preferences = SavedPreferences(document['version'], mode,
                                 cast(CEMOptions, _typed_section(document['cem'], CEMOptions)),
                                 cast(CCMOptions, _typed_section(document['ccm'], CCMOptions)))
  _validate(preferences)
  return preferences


def encode_preferences(preferences: SavedPreferences) -> bytes:
  """Create canonical JSON bytes; this function never writes Params."""
  _validate(preferences)
  document = {
    'version': preferences.version,
    'mode': preferences.mode.value,
    'cem': {field.name: getattr(preferences.cem, field.name) for field in fields(CEMOptions)},
    'ccm': {field.name: getattr(preferences.ccm, field.name) for field in fields(CCMOptions)},
  }
  return json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def saved_selection_for_drive(preferences: SavedPreferences, drive_id: object) -> ModeSelection | None:
  """Bind validated preferences to a drive; live authority is checked by Host."""
  _validate(preferences)
  choice = ModeChoice.CEM if preferences.mode is ModeChoice.CCM else preferences.mode
  return selection_for_drive(choice, preferences.mode_settings(), drive_id)


@dataclass(frozen=True)
class LegacyAdoptionProposal:
  """A decoded preview only; no Params read, write, or automatic adoption."""

  preferences: SavedPreferences
  source_keys: tuple[str, ...]
  units: str


# Frozen 678af783 openpilot/common/params_keys.h defaults. The factory CEM
# mode default was 1; legacy mode bits must still be supplied explicitly
# before a read-only adoption is proposed.
_LEGACY_DEFAULTS: dict[str, bool | float] = {
  'ConditionalExperimental': False, 'ConditionalChill': False,
  'CECurves': False, 'CECurvesLead': False, 'CELead': True,
  'CEOpenRoad': False, 'CESlowerLead': True, 'CEStoppedLead': False,
  'CEStopLights': True, 'CESignalLaneDetection': True,
  'PersistExperimentalState': False, 'PersistChillState': False,
  'CESpeed': 0.0, 'CESpeedLead': 0.0, 'CESignalSpeed': 0.0,
  'CEModelStopTime': 7.7, 'LaneDetectionWidth': 0.0,
  'CCMSpeed': 45.0, 'CCMSpeedLead': 35.0, 'CCMSetSpeedMargin': 3.0,
  'CCMLead': True, 'CCMLaunchAssist': False,
}
_PLAIN_DECIMAL = re.compile(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z')


def _legacy_text(value: object) -> str:
  if type(value) is bytes:
    try:
      return value.decode('ascii', errors='strict')
    except UnicodeDecodeError as exc:
      raise PreferenceError('legacy Params value is not ASCII') from exc
  if type(value) is str:
    return value
  raise PreferenceError('legacy Params value has an unsupported type')


def _legacy_value(value: object, default: bool | float) -> bool | float:
  if type(default) is bool:
    if type(value) is bool:
      return value
    text = _legacy_text(value)
    if text not in ('0', '1'):
      raise PreferenceError('legacy Params boolean must be 0 or 1')
    return text == '1'
  if type(value) in (int, float):
    numeric = cast(int | float, value)
  else:
    text = _legacy_text(value)
    if _PLAIN_DECIMAL.fullmatch(text) is None:
      raise PreferenceError('legacy Params number must be a plain nonnegative decimal')
    try:
      numeric = float(text)
    except OverflowError as exc:
      raise PreferenceError('legacy Params number is too large') from exc
  if not _number(numeric, 150.0):
    raise PreferenceError('legacy Params number is out of range')
  return float(numeric)


def _legacy_bool_at(decoded: Mapping[str, bool | float], key: str) -> bool:
  value = decoded[key]
  if not isinstance(value, bool):
    raise PreferenceError(f'legacy boolean has wrong type: {key}')
  return value


def _legacy_float_at(decoded: Mapping[str, bool | float], key: str) -> float:
  value = decoded[key]
  if not isinstance(value, float):
    raise PreferenceError(f'legacy number has wrong type: {key}')
  return value


def propose_legacy_adoption(values: Mapping[str, object], *, units: str) -> LegacyAdoptionProposal:
  """Preview old key values in SI; caller must explicitly approve any migration.

  Supply only the listed legacy keys and both old mode bits. SafeMode and the
  standard ExperimentalMode remain separate live owners, never saved here.
  """
  if not isinstance(values, Mapping) or type(units) is not str or units not in ('metric', 'imperial'):
    raise PreferenceError('invalid legacy snapshot or unit system')
  if not {'ConditionalExperimental', 'ConditionalChill'} <= set(values) or not set(values) <= set(_LEGACY_DEFAULTS):
    raise PreferenceError('legacy mode bits missing or unknown legacy key')
  decoded = {key: _legacy_value(values[key], default) if key in values else default
             for key, default in _LEGACY_DEFAULTS.items()}
  if _legacy_bool_at(decoded, 'ConditionalExperimental') and _legacy_bool_at(decoded, 'ConditionalChill'):
    raise PreferenceError('legacy conditional modes conflict')
  speed_factor = (1 / 3.6) if units == 'metric' else MPH_TO_MPS
  distance_factor = 1.0 if units == 'metric' else 0.3048
  speed_limit = 150.0 if units == 'metric' else 99.0
  margin_limit = 30.0 if units == 'metric' else 15.0
  for key in ('CESpeed', 'CESpeedLead', 'CESignalSpeed', 'CCMSpeed', 'CCMSpeedLead'):
    if _legacy_float_at(decoded, key) > speed_limit:
      raise PreferenceError(f'legacy speed exceeds frozen UI range: {key}')
  if (_legacy_float_at(decoded, 'CCMSetSpeedMargin') > margin_limit or
      _legacy_float_at(decoded, 'CEModelStopTime') > 9.0 or
      _legacy_float_at(decoded, 'LaneDetectionWidth') > 15.0):
    raise PreferenceError('legacy margin, model stop, or lane width exceeds frozen UI range')
  mode = (ModeChoice.CEM if _legacy_bool_at(decoded, 'ConditionalExperimental') else
          ModeChoice.CCM if _legacy_bool_at(decoded, 'ConditionalChill') else ModeChoice.STOCK)
  cem = CEMOptions(
    speed_mps=_legacy_float_at(decoded, 'CESpeed') * speed_factor,
    speed_with_lead_mps=_legacy_float_at(decoded, 'CESpeedLead') * speed_factor,
    signal_speed_mps=_legacy_float_at(decoded, 'CESignalSpeed') * speed_factor,
    open_road=_legacy_bool_at(decoded, 'CEOpenRoad'), curves=_legacy_bool_at(decoded, 'CECurves'),
    curves_with_lead=_legacy_bool_at(decoded, 'CECurvesLead'), lead=_legacy_bool_at(decoded, 'CELead'),
    slower_lead=_legacy_bool_at(decoded, 'CESlowerLead'), stopped_lead=_legacy_bool_at(decoded, 'CEStoppedLead'),
    stop_lights=_legacy_bool_at(decoded, 'CEStopLights'), model_stop_s=_legacy_float_at(decoded, 'CEModelStopTime'),
    signal_lane_detection=_legacy_bool_at(decoded, 'CESignalLaneDetection'),
    signal_lane_width_m=_legacy_float_at(decoded, 'LaneDetectionWidth') * distance_factor,
    persist_manual=_legacy_bool_at(decoded, 'PersistExperimentalState'),
  )
  ccm = CCMOptions(
    speed_mps=_legacy_float_at(decoded, 'CCMSpeed') * speed_factor,
    speed_with_lead_mps=_legacy_float_at(decoded, 'CCMSpeedLead') * speed_factor,
    set_speed_margin_mps=_legacy_float_at(decoded, 'CCMSetSpeedMargin') * speed_factor,
    lead=_legacy_bool_at(decoded, 'CCMLead'), launch_assist=_legacy_bool_at(decoded, 'CCMLaunchAssist'),
    persist_manual=_legacy_bool_at(decoded, 'PersistChillState'),
  )
  preferences = SavedPreferences(mode=mode, cem=cem, ccm=ccm)
  _validate(preferences)
  return LegacyAdoptionProposal(preferences, tuple(sorted(values)), units)


@dataclass(frozen=True)
class ModeSelection:
  choice: ModeChoice = ModeChoice.STOCK
  settings: ModeSettings = field(default_factory=ModeSettings)
  drive_id: int = 0


@dataclass(frozen=True)
class ManualState:
  intent: ManualIntent
  drive_id: int
  observed_mono_ns: int


def selection_for_drive(choice: object, settings: object, drive_id: object) -> ModeSelection | None:
  """Accept only a caller-validated typed choice; never infer a mode from bytes."""
  if not isinstance(choice, ModeChoice) or not isinstance(settings, ModeSettings) or type(drive_id) is not int or drive_id <= 0:
    return None
  return ModeSelection(choice, settings, drive_id)


def manual_for_drive(intent: object, drive_id: object, observed_mono_ns: object) -> ManualState | None:
  if not isinstance(intent, ManualIntent) or type(drive_id) is not int or drive_id <= 0 or type(observed_mono_ns) is not int or observed_mono_ns <= 0:
    return None
  return ManualState(intent, drive_id, observed_mono_ns)
