"""Shared native and Galaxy presentation of saved conditional modes."""

from __future__ import annotations

from dataclasses import fields, replace
import hashlib

from openpilot.starpilot.conditional_mode.button_actions import (
  BUTTON_PREFIX, MEDIA_KEYS, DISTANCE_KEYS, capture_sources, commit_assignment, display_action, media_capability,
  distance_capability, assignment_capability, runtime_map_ready,
)

from openpilot.starpilot.conditional_mode.actions import (
  BOOLEAN_FIELDS, DOCUMENT_KEY, MANUAL_RESET, MODE, NUMBER_FIELDS, PREFIX, RESET, commit, commit_manual, commit_mode,
  display_number, edit, field_limit, stock_document,
)
from openpilot.starpilot.conditional_mode.preferences import (
  MAX_DOCUMENT_BYTES, PreferenceError, SavedPreferences, decode_preferences,
)
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.conditional_mode.manual_saved import KEY as MANUAL_KEY, read_codes
from openpilot.starpilot.conditional_mode.preferences import encode_preferences
from openpilot.starpilot.conditional_mode.runtime_settings import experimental_available, read_mode_sources
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest


LABELS = {
  "speed_mps": "Speed threshold", "speed_with_lead_mps": "Speed with lead", "signal_speed_mps": "Signal speed",
  "open_road": "Open road", "curves": "Curves", "curves_with_lead": "Curves with lead",
  "lead": "Lead vehicle", "slower_lead": "Slower lead", "stopped_lead": "Stopped lead",
  "stop_lights": "Stop lights", "model_stop_s": "Model stop time",
  "signal_lane_detection": "Signal lane detection", "signal_lane_width_m": "Signal lane width",
  "set_speed_margin_mps": "Set speed margin", "launch_assist": "Launch assist",
}
MODES = ("Automatic", "Chill", "Experimental")
SIGNAL_LANE_FIELDS = frozenset(("signal_lane_detection", "signal_lane_width_m"))
SIGNAL_LANE_HELP = {
  "signal_lane_detection": "Distinguish a turn from a lane change using the space beside your car. With this on, a turn signal requests " +
                           "Experimental only when there is no adjacent lane.",
  "signal_lane_width_m": "Minimum width of the space beside your car that counts as a lane, rather than a turn.",
}
MEDIA_LABELS = ("MODE press", "MODE long press", "MODE very long press",
                "Star button", "Star button long press", "Star button very long press")



def _mode_capability(cp) -> tuple | None:
  if cp is None:
    return None
  try:
    reader = cp.as_reader() if hasattr(cp, "as_reader") else cp
    encoded = reader.as_builder().to_bytes()
    return (str(cp.carFingerprint), hashlib.sha256(encoded).hexdigest())
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None

def _display(number: float) -> str:
  return f"{number:.2f}".rstrip("0").rstrip(".") if number else "0"


def _unit(field: str, metric: bool) -> str:
  if field == "model_stop_s":
    return "s"
  if field == "signal_lane_width_m":
    return "m" if metric else "ft"
  return "km/h" if metric else "mph"


def confirmation_question(request: FeatureSettingsRequest, label: str = "", unit: str = "") -> str:
  if request.key == RESET:
    return "Replace the invalid conditional mode settings with Chill defaults?"
  if request.key == MANUAL_RESET:
    return "Reset both remembered manual choices to Automatic?"
  value = f"{request.value} {unit}".rstrip()
  target = f"{label.lower()} to" if label else "to"
  tail = " This resets the mode's remembered choice to Automatic." if request.key.endswith(":persist_manual") else ""
  return f"Set {target} {value}?{tail}"


class ConditionalFeature:
  def __init__(self, owner):
    self.owner = owner

  def _source(self):
    document, readable = read_saved(self.owner.params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
    units, unit_readable = read_saved(self.owner.params, "IsMetric", 8)
    metric = units is None or units == b"0" or units == b"1"
    return document, units, readable and unit_readable, metric, units == b"1"

  def _media_rows(self, document: bytes | None, safe: bytes | None, safe_readable: bool,
                  *, config_valid: bool) -> list[FeatureRow]:
    cp = self.owner.vehicle_params()
    gm = distance_capability(cp)
    switchback_only = not gm and cp is not None and not cp.openpilotLongitudinalControl
    capability = gm or media_capability(cp, switchback_only=switchback_only)
    keys = DISTANCE_KEYS if gm else MEDIA_KEYS
    labels = ("Distance press", "Distance long press", "Distance very long press") if gm else MEDIA_LABELS
    if capability is None:
      return []
    sources = capture_sources(self.owner.params, document, safe)
    enabled = (self.owner.authority("switchback_wheel" if switchback_only else "conditional_wheel") and config_valid and
               safe_readable and safe in (None, b"0", b"1") and sources.readable)
    ready = runtime_map_ready(sources, cp=cp) and all(display_action(sources.raw(key))[0] != "Unsupported saved action"
                                                for key in keys)
    rows = [] if ready else [FeatureRow("", "Button assignments",
                       "Review saved actions",
                       reason="Some saved actions are not supported. Choose Off for those buttons before assigning a new action.")]
    for key, label in zip(keys, labels, strict=True):
      raw = sources.raw(key)
      value, choices, repair = display_action(raw)
      if gm:
        if raw not in (None, b"0", b"6"):
          value, choices, repair = "Unsupported saved action", (), "Off"
        else:
          choices = ("Off", "Toggle traffic mode")
      if switchback_only:
        choices = ("Off", "Switchback Mode") if sources.readable else ()
      rows.append(FeatureRow(BUTTON_PREFIX + key, label, value if sources.readable else "Unavailable saved action",
                             raw, choices if sources.readable else (), available=enabled,
                             reason=("Toggle Traffic Mode from this button. May take effect this drive." if gm else
                                     "Choose this button action. May take effect this drive.") if enabled else
                                    "Reload to read saved button actions",
                             vehicle_fingerprint=self.owner.vehicle_fingerprint(), capability=capability,
                             dependencies=sources.dependencies, repair_value=repair if sources.readable else "", default_value="Off"))
    return rows

  def wheel_rows(self) -> list[FeatureRow]:
    document, _, readable, _, _ = self._source()
    safe, safe_readable = read_saved(self.owner.params, "SafeMode", 8)
    try:
      if document is not None:
        decode_preferences(document)
      valid = readable
    except PreferenceError:
      valid = False
    return self._media_rows(document, safe, safe_readable, config_valid=valid)

  def rows(self, page: str, parked: bool) -> list[FeatureRow]:
    if page == "conditional/ccm":
      return []
    document, units, readable, valid_units, metric = self._source()
    allowed = self.owner.authority("preferences")
    repair_allowed = parked and self.owner.authority("parked_preferences")
    unavailable = "Saved settings are unavailable"
    dependencies = (("IsMetric", units),)
    safe, safe_readable = read_saved(self.owner.params, "SafeMode", 8) if page == "conditional" else (None, True)
    if not readable:
      return [FeatureRow("", "Saved choices", "Unavailable", reason="Saved source cannot be read; no changes allowed")]
    try:
      preferences = SavedPreferences() if document is None else decode_preferences(document)
    except PreferenceError:
      return [FeatureRow("", "Saved choices", "Invalid", reason="Existing bytes remain unchanged until explicit reset"),
              FeatureRow(RESET, "Restore Chill defaults", "", document,
                         available=repair_allowed, reason="Replaces the invalid document after review",
                         dependencies=dependencies)]
    rows = []
    manual = read_codes(self.owner.params)
    if page != "conditional":
      safe, safe_readable = read_saved(self.owner.params, "SafeMode", 8)
    manual_dependencies = dependencies + ((MANUAL_KEY, manual.raw), ("SafeMode", safe))
    manual_available = repair_allowed and safe_readable and safe in (None, b"0", b"1")
    if page == "conditional":
      (mode_document, mode_readable), (baseline, baseline_readable), (mode_safe, mode_safe_readable) = read_mode_sources(self.owner.params)
      mode_valid = (mode_readable and mode_document == document and baseline_readable and baseline in (None, b"0", b"1") and
                    mode_safe_readable and mode_safe in (None, b"0", b"1"))
      choice = {"stock": "Experimental" if baseline == b"1" else "Chill",
                "conditional_experimental": "Automatic", "conditional_chill": "Automatic"}[preferences.mode.value]
      cp = self.owner.vehicle_params()
      choices = MODES if choice == "Experimental" or experimental_available(cp) and mode_safe in (None, b"0") else tuple(
        mode for mode in MODES if mode != "Experimental")
      rows.append(FeatureRow(MODE, "Driving Mode", choice if mode_valid else "Unavailable", document,
                             choices if mode_valid else (), default_value=MODES[0], available=allowed and mode_valid,
                             reason="Automatic switches between Chill and Experimental using your saved conditions." if allowed and mode_valid else unavailable,
                             vehicle_fingerprint=self.owner.vehicle_fingerprint(), capability=_mode_capability(cp),
                             dependencies=dependencies + (("ExperimentalMode", baseline), ("SafeMode", mode_safe))))
      rows.append(FeatureRow("", "Automatic conditions", "Saved options", page="conditional/cem", available=True))
      if manual.status in ("invalid", "read_error"):
        rows.append(FeatureRow("", "Remembered manual choices", "Invalid" if manual.status == "invalid" else "Unavailable"))
      if manual.status == "invalid":
        rows.append(FeatureRow(MANUAL_RESET, "Reset remembered manual choices", "Both modes to Automatic", document,
                               available=manual_available,
                               dependencies=manual_dependencies))
      return rows
    lane_page = page == "lane_change"
    section = "cem" if page in ("conditional/cem", "lane_change") else "ccm"
    option = preferences.cem if section == "cem" else preferences.ccm
    for field in fields(option):
      name = field.name
      if (name in SIGNAL_LANE_FIELDS) != lane_page:
        continue
      if name == "persist_manual":
        rows.append(FeatureRow(PREFIX + section + ":persist_manual", "Remember manual choice",
                               "On" if getattr(option, name) else "Off", document, ("Off", "On"),
                               default_value="On" if getattr(getattr(decode_preferences(stock_document()), section), name) else "Off",
                               available=manual_available and manual.status in ("valid", "absent"),
                               reason="Keeps your manual choice across drives. Changing this setting first resets the choice to Automatic."
                               if manual_available and
                               manual.status in ("valid", "absent") else
                               "Saved manual choices need repair" if manual_available else unavailable,
                               dependencies=manual_dependencies))
        continue
      value = getattr(option, name)
      key = PREFIX + section + ":" + name
      if name in BOOLEAN_FIELDS[section]:
        rows.append(FeatureRow(key, LABELS[name], "On" if value else "Off", document, ("Off", "On"),
                               default_value="On" if getattr(getattr(decode_preferences(stock_document()), section), name) else "Off",
                               available=allowed, reason=SIGNAL_LANE_HELP.get(name, "") if allowed else unavailable,
                               dependencies=dependencies))
      elif name in NUMBER_FIELDS[section]:
        displayed = display_number(name, value, metric)
        unit = _unit(name, metric)
        rows.append(FeatureRow(key, LABELS[name], _display(displayed) if valid_units else "Invalid saved units", document,
                               default_value=_display(display_number(name, getattr(getattr(decode_preferences(stock_document()), section), name), metric)),
                               step=0.1 if name == "model_stop_s" else 1.0 if valid_units else 0.0,
                               minimum=0.0, maximum=max(field_limit(section, name, metric), float(_display(displayed))),
                               unit=unit if valid_units else "",
                               available=allowed and valid_units, reason=SIGNAL_LANE_HELP.get(name, "") if allowed and valid_units else
                               "Invalid saved units" if not valid_units else unavailable,
                               dependencies=dependencies, display_unit=unit if valid_units else ""))
    return rows

  def apply(self, request: FeatureSettingsRequest) -> bool:
    if request.key.startswith(PREFIX + "ccm:"):
      return False
    if request.key.startswith(BUTTON_PREFIX):
      key = request.key.removeprefix(BUTTON_PREFIX)
      if key not in (*MEDIA_KEYS, *DISTANCE_KEYS) or not request.vehicle_fingerprint or request.capability is None:
        return False
      cp = self.owner.vehicle_params()
      switchback_only = key in MEDIA_KEYS and cp is not None and not cp.openpilotLongitudinalControl
      if switchback_only and request.value not in ("Off", "Switchback Mode"):
        return False
      def authorized_button() -> bool:
        return bool(self.owner.vehicle_fingerprint() == request.vehicle_fingerprint and
                    (media_capability(self.owner.vehicle_params(), switchback_only=True) if switchback_only else
                     assignment_capability(self.owner.vehicle_params(), key)) == request.capability and
                    self.owner.authority("switchback_wheel" if switchback_only else "conditional_wheel"))
      result = commit_assignment(self.owner.params, key=key, choice=request.value,
                                 expected=request.expected, dependencies=request.dependencies,
                                 authorized=authorized_button)
      return result.committed and result.verified
    if request.key == MODE:
      (document, readable), (baseline, baseline_readable), (safe, safe_readable) = read_mode_sources(self.owner.params)
      units, units_readable = read_saved(self.owner.params, "IsMetric", 8)
      dependencies = (("IsMetric", units), ("ExperimentalMode", baseline), ("SafeMode", safe))
      if (not readable or not baseline_readable or not safe_readable or not units_readable or
          document != request.expected or request.dependencies != dependencies or
          baseline not in (None, b"0", b"1") or safe not in (None, b"0", b"1") or request.value not in MODES):
        return False

      def authorized_mode() -> bool:
        cp = self.owner.vehicle_params()
        return bool(self.owner.authority("preferences") and
                    (request.value != "Experimental" or request.capability is not None and
                     _mode_capability(cp) == request.capability and self.owner.vehicle_fingerprint() == request.capability[0] and
                     experimental_available(cp) and safe in (None, b"0")))

      if not authorized_mode():
        return False
      try:
        preferences = SavedPreferences() if document is None else decode_preferences(document)
        value = "Stock" if request.value in ("Chill", "Experimental") else "Conditional Experimental"
        raw = edit(preferences, MODE, value, metric=units == b"1")
      except PreferenceError:
        return False
      next_baseline = b"1" if request.value == "Experimental" else b"0" if request.value == "Chill" else baseline or b"0"
      result = commit_mode(self.owner.params, raw, next_baseline, expected=document, expected_baseline=baseline,
                           expected_units=units, expected_safe=safe, authorized=authorized_mode)
      return result.committed and result.verified
    if (request.key in (RESET, MANUAL_RESET) and not request.confirmation) or \
       request.capability is not None or request.vehicle_fingerprint is not None:
      return False
    manual_action = request.key == MANUAL_RESET or request.key in (
      PREFIX + "cem:persist_manual", PREFIX + "ccm:persist_manual")
    def authorized() -> bool:
      group = "parked_preferences" if manual_action or request.key == RESET else "preferences"
      return self.owner.authority(group)
    if not authorized():
      return False
    document, units, readable, valid_units, metric = self._source()
    if not readable or document != request.expected:
      return False
    manual = read_codes(self.owner.params) if manual_action else None
    safe, safe_readable = read_saved(self.owner.params, "SafeMode", 8) if manual_action else (None, True)
    dependencies = (("IsMetric", units),) + (((MANUAL_KEY, manual.raw), ("SafeMode", safe)) if manual is not None else ())
    if request.dependencies != dependencies or not safe_readable or safe not in (None, b"0", b"1"):
      return False
    try:
      if request.key == MANUAL_RESET:
        if request.value != "confirm" or manual is None or manual.status != "invalid":
          return False
        result = commit_manual(self.owner.params, expected_config=document, expected_units=units,
                               expected_safe=safe, expected_manual=manual.raw, authorized=authorized,
                               choice=None, config_raw=None)
        return result.committed and result.verified
      if request.key == RESET:
        if request.value != "confirm" or document is None:
          return False
        try:
          decode_preferences(document)
          return False
        except PreferenceError:
          raw = stock_document()
      else:
        preferences = SavedPreferences() if document is None else decode_preferences(document)
        if request.key in (PREFIX + "cem:persist_manual", PREFIX + "ccm:persist_manual"):
          if request.value not in ("On", "Off") or manual is None or manual.status not in ("valid", "absent"):
            return False
          section = request.key.split(":")[1]
          option = preferences.cem if section == "cem" else preferences.ccm
          raw = encode_preferences(replace(preferences, **{section: replace(option, persist_manual=request.value == "On")}))
          choice = ModeChoice.CEM if section == "cem" else ModeChoice.CCM
          result = commit_manual(self.owner.params, expected_config=document, expected_units=units,
                                 expected_safe=safe, expected_manual=manual.raw, authorized=authorized,
                                 choice=choice, config_raw=raw)
          return result.committed and result.verified
        if request.key.startswith(PREFIX) and request.key != MODE and not valid_units:
          return False
        if request.key.startswith(PREFIX) and request.key != MODE:
          field = request.key.split(":")[-1]
          expected_unit = (_unit(field, metric) if field in
                           ("model_stop_s", "signal_lane_width_m", "speed_mps", "speed_with_lead_mps",
                            "signal_speed_mps", "set_speed_margin_mps") else "")
          if request.display_unit != expected_unit:
            return False
        value = "Stock" if request.key == MODE and request.value == "Chill" else request.value
        raw = edit(preferences, request.key, value, metric=metric)
    except (PreferenceError, ValueError, TypeError, OverflowError):
      return False
    result = commit(self.owner.params, raw, document, units, authorized)
    return result.committed and result.verified
