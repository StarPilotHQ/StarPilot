"""Native Torque editor adapter; strict numeric policy lives in the lateral domain."""

from __future__ import annotations

from dataclasses import replace
import json

from openpilot.starpilot.lateral.torque_supported import BOLT_VEHICLES, GM_VEHICLES
from openpilot.starpilot.lateral.controller_selection import DOCUMENT_KEY as SELECTION_KEY, selection_from_bytes
from openpilot.starpilot.lateral.torque_runtime import factor_edit_supported
from openpilot.starpilot.lateral.torque_settings import (
  DOCUMENT_KEY, LEGACY_KEYS, FieldChoice, LegacyMode, PlatformProfile, bounds as torque_bounds,
  interpret_legacy, parse_document, replace_field, resolve_document, serialize_document,
)
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest, TORQUE_CONFIRM_ACTIONS

TORQUE_NUMBERS = LEGACY_KEYS


class TorqueFeature:
  def __init__(self, owner):
    self.owner = owner
    self.params = owner.params
    self.authority = owner.authority
    self.vehicle_fingerprint = owner.vehicle_fingerprint
    self._capability = owner._capability
    self._raw = owner._raw
    self._readable = owner._readable
    self._dependents = owner._dependents


  def _manual_context(self):
    from openpilot.starpilot.flm.manual_coordinates import context
    cp = self.owner.vehicle_params()
    if cp is None or not self._readable(SELECTION_KEY):
      raise ValueError("Current controller unavailable")
    selection = selection_from_bytes(cp, self._raw(SELECTION_KEY))
    if selection.source == "invalid":
      raise ValueError("Saved controller selection unavailable")
    return context(cp, selection.mode)

  def _manual_token(self):
    return json.dumps(self._manual_context(), separators=(",", ":")).encode()

  def manual_rows(self, capability, allowed):
    from openpilot.starpilot.flm.canonical_surface_adapter import NAMES, coordinate_bounds
    raw = self._raw(DOCUMENT_KEY)
    if capability is None or not self._readable(DOCUMENT_KEY):
      return (FeatureRow("", "Manual tune", "Unavailable", reason="Current vehicle and readable saved settings are required"),)
    try:
      profiles = {} if raw is None else parse_document(raw)
      profile = profiles.get(capability[0])
      basis = tuple(capability[5:8])
      token = self._manual_token()
      current = self._manual_context()
      saved = None if profile is None else profile.manual_coordinates
      matches = profile is None or (profile.basis == basis and profile.flm is None
        and (saved is None or saved.context == current))
      values = dict(saved.values) if saved is not None else dict.fromkeys(NAMES, 0.)
      common = {"source": raw, "capability": capability, "related_source": token,
        "dependencies": self._dependents(SELECTION_KEY, *TORQUE_NUMBERS, "ForceAutoTuneOff")}
      reason = ("Parked only. Saved for the next drive; zero restores this stage's original baseline. " +
                "Manual overrides pause Auto Learn and are not learned results.") if matches else (
                "Vehicle tune, controller or effect definition changed. Reset this manual tune before editing.")
      rows = []
      for name in NAMES:
        low, high = coordinate_bounds(name)
        rows.append(FeatureRow("torque:surface:" + name, name.replace("_", " ").capitalize(),
          f"{100 * values[name]:.4f}", step=.25, minimum=100 * low, maximum=100 * high, unit="%",
          available=allowed and matches, reason=reason, default_value="0", **common))
      if saved is not None:
        rows.append(FeatureRow("torque:surface:reset", "Reset manual tune", "Reset", choices=("Reset",),
          available=allowed, reason="Remove only the thirty manual overrides. Accepted Auto Learn history is preserved.", **common))
      return tuple(rows)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, UnicodeError):
      return (FeatureRow("", "Manual tune", "Unavailable", reason="Saved tuning or controller context is invalid; repair it in Toggles"),)

  def _edit_manual(self, profiles, request, fingerprint, basis):
    from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
    from openpilot.starpilot.flm.manual_coordinates import ManualCoordinates
    if not self.authority("parked_preferences") or request.related_source != self._manual_token():
      raise ValueError("Manual tuning requires the same parked vehicle and controller")
    prior = profiles.get(fingerprint, PlatformProfile(basis, FieldChoice(), FieldChoice()))
    name = request.key.removeprefix("torque:surface:")
    if name == "reset":
      if not request.confirmation or request.value != "Reset":
        raise ValueError("Explicit reset confirmation required")
      return {**profiles, fingerprint: replace(prior, manual_coordinates=None)}
    current = self._manual_context()
    saved = prior.manual_coordinates
    if (name not in NAMES or prior.basis != basis or prior.flm is not None
        or saved is not None and saved.context != current):
      raise ValueError("Manual tune context changed")
    values = dict(saved.values) if saved is not None else dict.fromkeys(NAMES, 0.)
    values[name] = float(request.value) / 100
    manual = ManualCoordinates(current, tuple((n, values[n]) for n in NAMES)).validate()
    return {**profiles, fingerprint: replace(prior, manual_coordinates=manual if any(values.values()) else None)}

  def _factor_editable(self, fingerprint: str) -> bool:
    if fingerprint not in GM_VEHICLES:
      return True
    cp = self.owner.vehicle_params()
    if cp is None or not self._readable(SELECTION_KEY):
      return False
    selection = selection_from_bytes(cp, self._raw(SELECTION_KEY))
    return selection.source != "invalid" and factor_edit_supported(cp, selection.mode)

  def _torque_dependencies(self, fingerprint: str, field: str):
    keys = ("ForceAutoTuneOff", *TORQUE_NUMBERS)
    return self._dependents(*keys, *((SELECTION_KEY,) if field == "factor" and fingerprint in GM_VEHICLES and fingerprint not in BOLT_VEHICLES else ()))

  @staticmethod
  def _legacy_label(mode: LegacyMode, saved: float | None) -> str:
    if mode == LegacyMode.ABSENT:
      return "No saved override"
    if saved is None:
      raise ValueError("Missing saved torque value")
    return f"Stock tracked ({saved:.2f})" if mode == LegacyMode.STOCK else f"Saved custom ({saved:.2f})"

  def _torque_values(self, capability: tuple | None) -> tuple[bool, tuple[FeatureRow, ...]]:
    readings = {key: self._raw(key) for key in TORQUE_NUMBERS}
    try:
      if capability is None:
        raise ValueError("Vehicle unavailable")
      legacy = interpret_legacy(readings, (capability[5], capability[6], capability[7]), fingerprint=capability[0])
      statuses = (self._legacy_label(legacy.factor_mode, legacy.factor_saved),
                  self._legacy_label(legacy.friction_mode, legacy.friction_saved))
      valid = True
    except (ValueError, OverflowError):
      statuses = tuple("Invalid saved value" if readings[key] is not None else "No saved override"
                       for key in TORQUE_NUMBERS[:2])
      valid = False
    return valid, tuple(FeatureRow(key, "Lateral acceleration" if key == "SteerLatAccel" else "Friction",
                                   status, readings[key], reason="Legacy saved value; adopt to edit")
                        for key, status in zip(TORQUE_NUMBERS[:2], statuses, strict=True))

  def rows(self, capability: tuple | None, allowed: bool, *, repair_allowed: bool | None = None) -> tuple[bool, tuple[FeatureRow, ...]]:
    repair_allowed = allowed if repair_allowed is None else repair_allowed
    raw = self._raw(DOCUMENT_KEY)
    bolt = capability is not None and capability[0] in BOLT_VEHICLES
    try:
      if capability is None:
        cp = self.owner.vehicle_params()
        identified = bool(cp and getattr(cp, "carFingerprint", None))
        return False, (FeatureRow("", "Vehicle", "Steering torque tuning is unavailable for this vehicle" if identified else
                                  "Turn on the car and wait for vehicle identification before configuring steering"),)
      fingerprint = capability[0]
      basis = (capability[5], capability[6], capability[7])
      if raw is None:
        legacy = interpret_legacy({key: None if fingerprint in GM_VEHICLES else self._raw(key) for key in TORQUE_NUMBERS}, basis, fingerprint=fingerprint)
        profiles = {fingerprint: PlatformProfile(basis,
                    FieldChoice("custom", legacy.factor) if legacy.factor is not None and fingerprint not in BOLT_VEHICLES else FieldChoice(),
                    FieldChoice("custom", legacy.friction) if legacy.friction is not None and fingerprint not in BOLT_VEHICLES else FieldChoice())}
      else:
        profiles = parse_document(raw)
      factor, friction, review = resolve_document(profiles, fingerprint, basis)
    except (ValueError, UnicodeError, OverflowError):
      reset = FeatureRow("torque_reset", "Reset invalid torque profiles", "", raw,
                         available=repair_allowed and raw is not None and self._readable(DOCUMENT_KEY), capability=capability,
                         dependencies=self._dependents("ForceAutoTuneOff", *TORQUE_NUMBERS))
      return False, (FeatureRow("", "Torque profile", "Invalid saved values; supplied tune in use"), reset)
    if review:
      profile = profiles[fingerprint]
      if bolt and profile.factor.mode == "custom":
        reset = FeatureRow("torque_reset_profile", "Reset this model's tuning", "Reset to configure friction", raw,
                           available=repair_allowed, capability=capability,
                           dependencies=self._dependents("AdvancedLateralTune", "ForceAutoTuneOff"))
        return False, (FeatureRow("", "Friction tuning paused",
                                  "This saved tune includes a lateral-acceleration adjustment that this Bolt editor cannot apply. " +
                                  "Reset this model’s tune to configure friction."), reset)
      details = []
      summary = []
      for field, label, current, choice in (("factor", "Lateral acceleration", basis[0], profile.factor),
                                            ("friction", "Friction", basis[2], profile.friction)):
        low, high = torque_bounds(basis, field, fingerprint=fingerprint)
        saved = f"{choice.custom_value:.4f}" if choice.mode == "custom" and choice.custom_value is not None else "Vehicle/learned"
        details.append(FeatureRow("", label, f"Saved {saved} · vehicle {current:.4f} · range {low:.4f}–{high:.4f}"))
        summary.append(f"{label} {saved}")
      rebase = FeatureRow("torque_rebase", "Review changed vehicle tune", ", ".join(summary), raw,
                          available=repair_allowed, capability=capability,
                          dependencies=self._dependents("ForceAutoTuneOff", *TORQUE_NUMBERS))
      reset = FeatureRow("torque_reset_profile", "Use vehicle values for this model", "", raw,
                         available=repair_allowed, capability=capability,
                         dependencies=self._dependents("ForceAutoTuneOff", *TORQUE_NUMBERS))
      return False, (FeatureRow("", "Torque profile", "Vehicle tune changed; custom values paused"), *details, rebase, reset)
    profile = profiles.get(fingerprint, PlatformProfile(basis, FieldChoice(), FieldChoice()))
    from openpilot.starpilot.lateral.gm_geometry_runtime import read_geometry
    cp = self.owner.vehicle_params()
    force_auto = cp is not None and read_geometry(self.params, cp).force_auto
    rows: list[FeatureRow] = []
    for field, label, active in (("factor", "Lateral acceleration", factor), ("friction", "Friction", friction)):
      if bolt and field == "factor":
        continue
      choice = profile.factor if field == "factor" else profile.friction
      low, high = torque_bounds(basis, field, fingerprint=fingerprint)
      dependencies = self._torque_dependencies(fingerprint, field)
      supplied = basis[0 if field == "factor" else 2]
      value = active if active is not None else supplied
      displayed = str(round(value, 8))
      editable = field != "factor" or self._factor_editable(fingerprint)
      rows.append(FeatureRow(f"torque:{field}:value", "Lat Accel" if field == "factor" else "Friction", displayed, raw,
                             step=0.05 if field == "factor" else 0.01, minimum=low, maximum=high,
                             available=allowed and editable, capability=capability, dependencies=dependencies,
                             default_value="Vehicle/learned", default_key=f"torque:{field}:mode",
                             reason=("Selected controller uses a fixed torque conversion; factor does not change its output" if not editable else
                                     "Saved choice temporarily ignored while Force auto is ON" if force_auto else
                                     "Uses the supplied vehicle tune" if active is None else "Uses your custom value")))
      rows.append(FeatureRow(f"torque:{field}:reset", label + " — Reset to Default", "", raw,
                             available=allowed and active is not None, repair_value="Reset",
                             capability=capability, dependencies=dependencies))
    if bolt:
      rows.insert(0, FeatureRow("", "Manual friction", "Compensates steering friction. First manual activation requires " +
                               "a driving-process restart; an active tuning owner refreshes changes during the drive."))
    if fingerprint in GM_VEHICLES:
      from openpilot.starpilot.ui.gain_feature import GainFeature
      rows.extend(GainFeature(self.owner).rows(capability, profiles, raw, allowed, repair_allowed))
    rows.extend(self._geometry_rows(capability, profiles, raw, allowed))
    return True, tuple(rows)

  @staticmethod
  def _geometry_token(cp):
    from openpilot.starpilot.lateral.gm_geometry_runtime import geometry_basis
    return json.dumps(geometry_basis(cp), separators=(",", ":"), allow_nan=False).encode()

  def _geometry_rows(self, capability, profiles, raw, allowed):
    from openpilot.starpilot.lateral.gm_geometry_runtime import geometry_basis, read_geometry
    from openpilot.starpilot.lateral.torque_runtime import gm_manual_supported_cp
    from openpilot.starpilot.lateral.torque_settings import GeometryProfile
    cp = self.owner.vehicle_params()
    if cp is None or not gm_manual_supported_cp(cp):
      return []
    fingerprint = capability[0]
    profile = profiles.get(fingerprint)
    basis = geometry_basis(cp)
    geometry = profile.geometry if profile is not None else None
    if geometry is not None and geometry.basis != basis:
      return [FeatureRow("", "Vehicle model tuning", "Vehicle basis changed; saved geometry paused"),
              FeatureRow("torque:geometry_reset:value", "Reset saved geometry", "Reset", raw,
                         choices=("Reset",), available=allowed, capability=capability,
                         related_source=self._geometry_token(cp), dependencies=self._dependents("ForceAutoTuneOff", SELECTION_KEY))]
    geometry = geometry or GeometryProfile(basis)
    token = self._geometry_token(cp)
    dependencies = self._dependents("ForceAutoTuneOff", SELECTION_KEY)
    selected = read_geometry(self.params, cp)
    rows = []
    learning_labels = {"source": "Selected controller", "force_auto": "Force auto", "force_off": "Force off"}
    rows.append(FeatureRow("torque:learning:value", "Automatic torque learning", learning_labels[geometry.learning], raw,
                           choices=tuple(learning_labels.values()), available=allowed, capability=capability,
                           related_source=token, dependencies=dependencies,
                           reason="Chooses vehicle learning or a manual override. Force off takes priority; " +
                                  "Force auto temporarily ignores saved factor, friction and ratio."))
    rows.append(FeatureRow("torque:automatic_delay:value", "Steering delay source", "Learned" if geometry.automatic_delay else "Vehicle/custom", raw,
                           choices=("Learned", "Vehicle/custom"), available=allowed, capability=capability,
                           related_source=token, dependencies=dependencies,
                           reason="Full delay includes the vehicle actuator delay plus 0.2 seconds; estimator continues independently."))
    for field, label, choice, supplied, low, high, step in (
      ("ratio", "Steering ratio", geometry.ratio, basis[0], .5*basis[0], 1.5*basis[0], .1),
      ("full_delay", "Full steering delay", geometry.full_delay, basis[1], .01, 1., .01),
    ):
      value = choice.custom_value if choice.mode == "custom" else supplied
      assert value is not None
      suppressed = selected.force_auto if field == "ratio" else geometry.automatic_delay
      rows.append(FeatureRow(f"torque:{field}:value", label, str(round(value, 8)), raw,
                             step=step, minimum=low, maximum=high, available=allowed,
                             related_source=token, capability=capability, dependencies=dependencies,
                             default_value="Vehicle/learned" if field == "ratio" else "Vehicle full delay",
                             default_key=f"torque:{field}:mode", unit="s" if field == "full_delay" else "",
                             reason="Saved choice temporarily ignored by automatic source" if suppressed else "Custom value; estimator remains unchanged"))
    return rows

  def _edit_geometry(self, profiles, request, fingerprint, torque_basis):
    from openpilot.starpilot.lateral.gm_geometry_runtime import geometry_basis
    from openpilot.starpilot.lateral.torque_runtime import gm_manual_supported_cp
    from openpilot.starpilot.lateral.torque_settings import GeometryProfile, replace_geometry
    cp = self.owner.vehicle_params()
    if cp is None or not gm_manual_supported_cp(cp) or request.related_source != self._geometry_token(cp):
      raise ValueError("Changed geometry source")
    if not {"ForceAutoTuneOff", SELECTION_KEY} <= dict(request.dependencies).keys():
      raise ValueError("Missing geometry source evidence")
    basis = geometry_basis(cp)
    prior = profiles.get(fingerprint)
    geometry = prior.geometry if prior is not None else None
    geometry = geometry or GeometryProfile(basis)
    _, field, part = request.key.split(":")
    if field == "geometry_reset" and part == "value" and request.value == "Reset":
      if prior is None:
        raise ValueError("No saved geometry")
      return {**profiles, fingerprint: replace(prior, geometry=None)}
    if field == "learning" and part == "value":
      value = {"Selected controller": "source", "Force auto": "force_auto", "Force off": "force_off"}[request.value]
    elif field == "automatic_delay" and part == "value":
      value = {"Learned": True, "Vehicle/custom": False}[request.value]
    elif field in ("ratio", "full_delay"):
      choice = getattr(geometry, field)
      if part == "mode" and request.value in ("Vehicle/learned", "Vehicle full delay"):
        value = FieldChoice("source", choice.custom_value)
      elif part == "value":
        value = FieldChoice("custom", float(request.value))
      else:
        raise ValueError("Unknown geometry edit")
    else:
      raise ValueError("Unknown geometry edit")
    result = replace_geometry(profiles, fingerprint, torque_basis, basis, field, value)
    if field == "full_delay":
      result = replace_geometry(result, fingerprint, torque_basis, basis, "automatic_delay", False)
    return result

  def apply(self, request: FeatureSettingsRequest) -> bool:
    """One guarded document replacement; the presentation never writes Params."""
    if request.key == "torque_gain_rebase" or request.key.startswith("torque:gain:"):
      from openpilot.starpilot.ui.gain_feature import GainFeature
      return GainFeature(self.owner).apply(request)
    if request.key in TORQUE_CONFIRM_ACTIONS and not self.authority("parked_preferences"):
      return False
    if (request.vehicle_fingerprint != self.vehicle_fingerprint() or not self.authority("torque") or
        request.capability is None or request.capability != self._capability("torque") or
        self._raw(DOCUMENT_KEY) != request.expected or not self._readable(DOCUMENT_KEY) or
        any(self._raw(name) != raw or not self._readable(name) for name, raw in request.dependencies)):
      return False
    capability = request.capability
    fingerprint = capability[0]
    basis = (capability[5], capability[6], capability[7])
    try:
      if request.key == "torque_adopt":
        if not request.confirmation or request.expected is not None:
          return False
        profiles: dict[str, PlatformProfile] = {fingerprint: PlatformProfile(basis, FieldChoice(), FieldChoice())}
      elif request.key == "torque_reset":
        if not request.confirmation or request.expected is None:
          return False
        try:
          parse_document(request.expected)
          return False
        except (ValueError, UnicodeError, OverflowError):
          profiles = {}
      else:
        if request.expected is None:
          legacy = interpret_legacy({key: None if fingerprint in GM_VEHICLES else self._raw(key) for key in TORQUE_NUMBERS}, basis, fingerprint=fingerprint)
          profiles = {fingerprint: PlatformProfile(basis,
                      FieldChoice("custom", legacy.factor) if legacy.factor is not None and fingerprint not in BOLT_VEHICLES else FieldChoice(),
                      FieldChoice("custom", legacy.friction) if legacy.friction is not None and fingerprint not in BOLT_VEHICLES else FieldChoice())}
        else:
          profiles = parse_document(request.expected)
        _, _, review = resolve_document(profiles, fingerprint, basis)
        if request.key == "torque_reset_profile":
          if not request.confirmation or not review:
            return False
          amended = dict(profiles)
          amended[fingerprint] = PlatformProfile(basis, FieldChoice(), FieldChoice())
          profiles = amended
        elif request.key == "torque_rebase":
          if not request.confirmation or not review:
            return False
          prior = profiles[fingerprint]
          updated = replace(prior, basis=basis)
          amended = dict(profiles)
          amended[fingerprint] = updated
          resolve_document(amended, fingerprint, basis)
          profiles = amended
        elif request.key.startswith("torque:surface:"):
          profiles = self._edit_manual(profiles, request, fingerprint, basis)
        elif request.key.startswith(("torque:ratio:", "torque:full_delay:", "torque:automatic_delay:", "torque:learning:", "torque:geometry_reset:")):
          profiles = self._edit_geometry(profiles, request, fingerprint, basis)
        elif request.key.startswith("torque:"):
          if review:
            return False
          _, field, part = request.key.split(":")
          if fingerprint in BOLT_VEHICLES and field == "factor":
            return False
          if fingerprint in GM_VEHICLES and field == "factor":
            if SELECTION_KEY not in dict(request.dependencies):
              return False
            withdrawing = part == "reset" or part == "mode" and request.value == "Vehicle/learned"
            if not withdrawing and not self._factor_editable(fingerprint):
              return False
          if field not in ("factor", "friction") or part not in ("mode", "value", "reset"):
            return False
          profile = profiles.get(fingerprint)
          choice = getattr(profile, field) if profile else FieldChoice()
          if part == "mode":
            if request.value == "Vehicle/learned":
              profiles = replace_field(profiles, fingerprint, basis, field, "source", None)
            elif request.value == "Custom":
              candidate = choice.custom_value if choice.custom_value is not None else basis[0 if field == "factor" else 2]
              low, high = torque_bounds(basis, field, fingerprint=fingerprint)
              if not low <= candidate <= high:
                candidate = basis[0 if field == "factor" else 2]
              profiles = replace_field(profiles, fingerprint, basis, field, "custom", candidate)
            else:
              return False
          elif part == "reset" and request.value == "Reset":
            profiles = replace_field(profiles, fingerprint, basis, field, "source", None)
          elif part == "value":
            value = float(request.value)
            supplied = basis[0 if field == "factor" else 2]
            mode = "source" if round(value, 2) == round(supplied, 2) else "custom"
            profiles = replace_field(profiles, fingerprint, basis, field, mode, value if mode == "custom" else None)
          else:
            return False
        else:
          return False
      encoded = serialize_document(profiles)
      manual_request = request.key.startswith("torque:surface:")
      geometry_request = request.key.startswith(("torque:ratio:", "torque:full_delay:", "torque:automatic_delay:",
                                                 "torque:learning:", "torque:geometry_reset:"))
      # Fresh authority and source evidence immediately before the one Params replacement.
      if (not self.authority("torque") or self.vehicle_fingerprint() != request.vehicle_fingerprint or
          self._capability("torque") != capability or not self._readable(DOCUMENT_KEY) or
          self._raw(DOCUMENT_KEY) != request.expected or
          geometry_request and request.related_source != self._geometry_token(self.owner.vehicle_params()) or
          manual_request and (not self.authority("parked_preferences") or request.related_source != self._manual_token()) or
          request.key in TORQUE_CONFIRM_ACTIONS and not self.authority("parked_preferences") or
          any(not self._readable(name) or self._raw(name) != raw for name, raw in request.dependencies)):
        return False
      if manual_request:
        from openpilot.starpilot.saved_document import commit_exact
        from openpilot.starpilot.lateral.torque_settings import MAX_DOCUMENT_BYTES
        def authorized():
          return (self.authority("torque") and self.authority("parked_preferences") and
            self.vehicle_fingerprint() == request.vehicle_fingerprint and self._capability("torque") == capability and
            request.related_source == self._manual_token() and
            all(self._readable(name) and self._raw(name) == raw for name, raw in request.dependencies))
        result = commit_exact(self.params, key=DOCUMENT_KEY, max_bytes=MAX_DOCUMENT_BYTES, raw=encoded,
          expected=request.expected, authorized=authorized, temp_prefix=".manual-steering-")
        return result.committed and result.verified
      self.params.put(DOCUMENT_KEY, json.loads(encoded), block=True)
      saved = self._raw(DOCUMENT_KEY)
      return saved is not None and parse_document(saved) == profiles
    except (OSError, ValueError, TypeError, KeyError, OverflowError, UnicodeError):
      return False
