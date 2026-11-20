"""Explicit controller-bound steering response edits in the shared torque document."""

import json

from openpilot.starpilot.lateral.gain_runtime import gain_supported, pid_gain_basis
from openpilot.starpilot.lateral.torque_supported import BOLT_VEHICLES, GM_VEHICLES
from openpilot.starpilot.lateral.controller_selection import DOCUMENT_KEY as SELECTION_KEY, ControllerMode, selection_from_bytes
from openpilot.starpilot.lateral.torque_settings import (DOCUMENT_KEY, GainBasis, FieldChoice, gain_bounds, parse_document, replace_gain, serialize_document)
from openpilot.starpilot.ui.feature_settings_state import FeatureRow


class GainFeature:
  def __init__(self, owner):
    self.owner = owner

  def _basis(self, capability):
    cp = self.owner.vehicle_params()
    if cp is None or not gain_supported(cp) or capability != (self.owner._capability("torque") or self.owner._capability("gain")):
      raise ValueError("Qualified vehicle unavailable")
    if cp.lateralTuning.which() == "pid":
      return pid_gain_basis(cp)
    if not self.owner._readable(SELECTION_KEY):
      raise ValueError("Controller selection unavailable")
    selection = selection_from_bytes(cp, self.owner._raw(SELECTION_KEY))
    if selection.source == "invalid":
      raise ValueError("Controller selection needs review")
    if selection.mode == ControllerMode.STARPILOT:
      table = ((0,), (0.6,))
    else:
      from openpilot.selfdrive.controls.lib.latcontrol_torque import INTERP_SPEEDS, KP_INTERP
      table = (tuple(INTERP_SPEEDS), tuple(KP_INTERP))
    return GainBasis(selection.mode.value, table, (capability[5], capability[6], capability[7]))

  def rows(self, capability, profiles, raw, allowed, repair_allowed):
    try:
      basis = self._basis(capability)
    except (AttributeError, ValueError, TypeError, OverflowError):
      return (FeatureRow("", "Steering response", "Controller selection unavailable"),)
    profile = profiles.get(capability[0])
    choice = profile.proportional_gain if profile is not None else FieldChoice()
    dependencies = self.owner._dependents(*(("AdvancedLateralTune",) if capability[0] in BOLT_VEHICLES else ()), "ForceAutoTuneOff", SELECTION_KEY)
    if choice.mode == "custom" and profile.gain_basis != basis:
      return (FeatureRow("torque_gain_rebase", "Review steering response",
                         "Controller or vehicle tune changed. Confirm this response for the selected controller.", raw,
                         available=repair_allowed, capability=capability, dependencies=dependencies),
              FeatureRow("torque:gain:mode", "Steering response source", "Custom", raw,
                         choices=("Selected controller",), available=allowed, capability=capability, dependencies=dependencies,
                         default_value="Selected controller"))
    apply_note = ("First manual activation requires a driving-process restart."
                  if capability[0] in GM_VEHICLES else "Applies at restart. Learned steering adjustments pause while custom Kp is active.")
    rows = [FeatureRow("", "Manual steering response", "Higher values respond more strongly. " + apply_note)]
    rows.append(FeatureRow("torque:gain:mode", "Steering response source",
                           "Custom" if choice.mode == "custom" else "Selected controller", raw,
                           choices=("Selected controller", "Custom"), available=allowed, capability=capability, dependencies=dependencies,
                           default_value="Selected controller"))
    if choice.mode == "custom":
      low, high = gain_bounds(capability[0], basis)
      rows.append(FeatureRow("torque:gain:value", "Steering response (Kp)", f"{choice.custom_value:.2f}", raw,
                             minimum=low, maximum=high, step=0.05 if capability[0] in GM_VEHICLES else 0.01, available=allowed,
                             default_value="Selected controller", default_key="torque:gain:mode",
                             capability=capability, dependencies=dependencies))
    return tuple(rows)

  def apply(self, request):
    owner = self.owner
    def current():
      required = {"ForceAutoTuneOff", SELECTION_KEY}
      if request.capability is not None and request.capability[0] in BOLT_VEHICLES:
        required.add("AdvancedLateralTune")
      return ((request.capability is not None and request.capability[0] in GM_VEHICLES or owner.authority("parked_preferences")) and
              owner.authority("torque") and request.vehicle_fingerprint == owner.vehicle_fingerprint() and
              request.capability is not None and request.capability == (owner._capability("torque") or owner._capability("gain")) and
              required.issubset(key for key, _ in request.dependencies) and
              owner._readable(DOCUMENT_KEY) and owner._raw(DOCUMENT_KEY) == request.expected and
              all(owner._readable(key) and owner._raw(key) == value for key, value in request.dependencies))
    try:
      if not current() or request.expected is None and request.capability[0] in BOLT_VEHICLES:
        return False
      basis = self._basis(request.capability)
      profiles = {} if request.expected is None else parse_document(request.expected)
      prior = profiles.get(request.capability[0])
      choice = prior.proportional_gain if prior is not None else FieldChoice()
      review = request.key == "torque_gain_rebase"
      if review:
        if not request.confirmation or not owner.authority("parked_preferences") or choice.mode != "custom":
          return False
        mode, value = choice.mode, choice.custom_value
      elif request.key == "torque:gain:mode":
        if request.value == "Selected controller":
          mode, value = "source", None
        elif request.value == "Custom":
          initial = 0.6 if request.capability[0] in BOLT_VEHICLES else basis.source_table[1][-1]
          mode, value = "custom", choice.custom_value if choice.custom_value is not None else initial
        else:
          return False
      elif request.key == "torque:gain:value" and choice.mode == "custom":
        mode, value = "custom", float(request.value)
      else:
        return False
      updated = replace_gain(profiles, request.capability[0], basis, mode, value, review=review)
      if not current() or self._basis(request.capability) != basis or review and not owner.authority("parked_preferences"):
        return False
      owner.params.put(DOCUMENT_KEY, json.loads(serialize_document(updated)), block=True)
      return parse_document(owner._raw(DOCUMENT_KEY)) == updated
    except (OSError, ValueError, TypeError, AttributeError, OverflowError, UnicodeError):
      return False
