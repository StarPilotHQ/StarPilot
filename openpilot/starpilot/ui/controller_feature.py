"""Shared saved controller preference for Galaxy and native driving settings."""

from __future__ import annotations

from dataclasses import replace
import math
import json

from openpilot.starpilot.lateral.controller_selection import (
  DOCUMENT_KEY, LEARNING_OFF_KEY, MAX_DOCUMENT_BYTES, ControllerMode, policy_for, replace_mode, selection_from_bytes, torque_choice_cp,
)
from openpilot.starpilot.lateral.learning_preferences import (
  KEY as LEARNING_PREFERENCES_KEY, MAX_BYTES as LEARNING_PREFERENCES_LIMIT, read_preference, replace_preference,
)
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.lateral.lane_change_preferences import (
  KEY as LANE_CHANGE_KEY, MAX_BYTES as LANE_CHANGE_LIMIT, LaneChangePolicy, decode as decode_lane_change, to_value,
)
from openpilot.starpilot.ui.feature_settings_state import SettingLevel, FeatureRow, FeatureSettingsRequest


CHOICES = ('Stock Controller', 'StarPilot Controller')
TORQUE_CHOICE = 'Torque Controller'
MODES = dict(zip(CHOICES, (ControllerMode.STANDARD, ControllerMode.STARPILOT), strict=True))
MODES[TORQUE_CHOICE] = ControllerMode.STANDARD
TUNING_GUIDANCE = ("StarPilot's torque controller can provide higher low-speed steering accuracy, but may require additional tuning. " +
                   "Join the StarPilot Discord and open a tuning request: https://firestar.link/discord")
SETUP_ACTION = 'torque_prepare_firestar'
SETUP_QUESTION = 'Change tuning preparation? Turning it off restores your previous steering preferences.'
SETUP_DEFAULTS = {'TurnAssist': b'0', LEARNING_OFF_KEY: b'1', 'LaneCentering': b'0',
                  'LaneCenterOffset': b'0.0', 'LaneCenteringE2EAuthority': b'0.5', 'LaneCenteringPauseOnSignal': b'1'}
SETUP_LIMITS = {**dict.fromkeys(SETUP_DEFAULTS, 128), LEARNING_PREFERENCES_KEY: LEARNING_PREFERENCES_LIMIT,
                LANE_CHANGE_KEY: LANE_CHANGE_LIMIT, DOCUMENT_KEY: MAX_DOCUMENT_BYTES}


def steering_controller(CP) -> str | None:
  try:
    from opendbc.car.values import PLATFORMS

    platform = PLATFORMS.get(str(CP.carFingerprint))
    if (platform is None or platform.__class__.__module__.split('.')[-2] != str(CP.brand) or
        CP.passive or CP.dashcamOnly or CP.notCar):
      return None
    kind = str(CP.steerControlType)
    if kind in ('angle', 'curvature'):
      return kind
    tuning = str(CP.lateralTuning.which())
    return tuning if kind == 'torque' and tuning in ('torque', 'pid') else None
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None


class ControllerFeature:
  def __init__(self, owner):
    self.params = owner.params
    self.authority = owner.authority
    self.vehicle_fingerprint = owner.vehicle_fingerprint
    self.vehicle_params = owner.vehicle_params

  def capability(self, CP=None) -> tuple | None:
    CP = self.vehicle_params() if CP is None else CP
    if CP is None or CP.notCar:
      return None
    try:
      prepared = torque_choice_cp(CP)
      policy = policy_for(prepared) if prepared is not None else None
      if policy is None:
        return None
      tune = prepared.lateralTuning.torque
      firmware = tuple((str(fw.ecu), bytes(fw.fwVersion)) for fw in CP.carFw)
      return (str(CP.brand), str(CP.carFingerprint), policy, str(CP.steerControlType),
              str(CP.lateralTuning.which()), bool(CP.passive), bool(CP.dashcamOnly), bool(CP.notCar),
              int(CP.flags), int(CP.alternativeExperience), float(tune.latAccelFactor),
              float(tune.latAccelOffset), float(tune.friction), float(tune.steeringAngleDeadzoneDeg),
              float(CP.steerActuatorDelay), float(CP.steerLimitTimer), str(CP.carVin), firmware,
              json.dumps(CP.lateralTuning.to_dict(), sort_keys=True, separators=(',', ':')))
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

  def learning_capability(self, CP=None) -> tuple | None:
    CP = self.vehicle_params() if CP is None else CP
    try:
      if steering_controller(CP) != 'torque':
        return None
      tune = CP.lateralTuning.torque
      basis = (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction),
               float(tune.steeringAngleDeadzoneDeg), float(CP.steerActuatorDelay), float(CP.steerLimitTimer))
      if not all(math.isfinite(value) for value in basis) or basis[0] <= 0 or any(value < 0 for value in basis[2:]):
        return None
      firmware = tuple((str(fw.ecu), bytes(fw.fwVersion)) for fw in CP.carFw)
      return (str(CP.brand), str(CP.carFingerprint), str(CP.steerControlType), str(CP.lateralTuning.which()),
              int(CP.flags), int(CP.alternativeExperience), *basis, str(CP.carVin), firmware)
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

  def _setup_sources(self) -> dict[str, bytes | None] | None:
    from openpilot.starpilot.ui.tuning_preparation import KEY as JOURNAL_KEY, LIMIT, decode
    limits = SETUP_LIMITS
    journal_raw, readable = read_saved(self.params, JOURNAL_KEY, LIMIT)
    legacy_limits = {key: limit for key, limit in limits.items() if key != LEARNING_PREFERENCES_KEY}
    # Existing preparations restore the exact keys they originally journaled.
    if readable and journal_raw is not None and isinstance(decode(journal_raw, legacy_limits), dict):
      limits = legacy_limits
    sources = {}
    for key, limit in limits.items():
      raw, readable = read_saved(self.params, key, limit)
      if not readable:
        return None
      sources[key] = raw
    return sources

  @staticmethod
  def _setup_values(CP, sources: dict[str, bytes | None]) -> dict[str, bytes]:
    lane_raw = sources[LANE_CHANGE_KEY]
    lane = LaneChangePolicy() if lane_raw is None else decode_lane_change(lane_raw)
    if lane is None:
      raise ValueError('Invalid lane-change preferences')
    # Gap reduction is longitudinal; retain it while resetting lateral behavior.
    lane = replace(LaneChangePolicy(), close_gap=lane.close_gap, close_gap_seconds=lane.close_gap_seconds)
    learning = ({LEARNING_PREFERENCES_KEY: replace_preference(sources[LEARNING_PREFERENCES_KEY], CP, False)}
                if LEARNING_PREFERENCES_KEY in sources else {})
    return {**SETUP_DEFAULTS, **learning, LANE_CHANGE_KEY: json.dumps(to_value(lane), sort_keys=True, separators=(',', ':')).encode(),
            DOCUMENT_KEY: replace_mode(sources[DOCUMENT_KEY], CP, ControllerMode.STARPILOT,
                                       force_torque=steering_controller(CP) == 'pid' or
                                         selection_from_bytes(CP, sources[DOCUMENT_KEY]).force_torque)}

  def setup_row(self) -> FeatureRow | None:
    CP = self.vehicle_params()
    capability = self.capability(CP)
    if capability is None:
      return None
    sources = self._setup_sources()
    valid = sources is not None
    if sources is not None:
      try:
        self._setup_values(CP, sources)
      except (ValueError, TypeError, UnicodeError, OverflowError):
        valid = False
    from openpilot.starpilot.ui.tuning_preparation import KEY, LIMIT, decode
    journal_raw, readable = read_saved(self.params, KEY, LIMIT)
    journal = decode(journal_raw, sources if sources is not None else SETUP_LIMITS)
    active = isinstance(journal, dict) and journal['vehicle'] == str(CP.carFingerprint)
    complete = active and sources == journal['prepared']
    conflict = isinstance(journal, dict) and (journal['vehicle'] != str(CP.carFingerprint) or
      any(raw not in (journal['prior'][key], journal['prepared'][key]) for key, raw in (sources or {}).items()))
    return FeatureRow(SETUP_ACTION, 'Prep My Vehicle for Tuning', 'On' if active else 'Off',
                      journal_raw, choices=('Off', 'On'), default_value='Off',
                      available=(active or valid and not conflict) and sources is not None and readable and journal is not False and
                                self.authority('parked_preferences') and self.authority('preferences'),
                      reason=('Enable before recording a route for a tuning request on the StarPilot Discord. ' +
                              'Turn off to restore your previous tune.' if not conflict and (not active or complete) else
                              'Preparation was interrupted. Turn off to restore your previous tune.' if not conflict else
                              'Turn off to restore previous settings while keeping changes made after preparation.'),
                      capability=capability, dependencies=tuple((key, raw) for key, raw in (sources or {}).items()), level=SettingLevel.ADVANCED)

  def apply_setup(self, request: FeatureSettingsRequest) -> bool:
    from openpilot.starpilot.ui.tuning_preparation import transition
    row = self.setup_row()
    if (row is None or not row.available or request.key != SETUP_ACTION or request.value not in row.choices or
        request.expected != row.source or request.capability != row.capability or request.dependencies != row.dependencies or
        request.vehicle_fingerprint != self.vehicle_fingerprint()):
      return False
    CP = self.vehicle_params()
    sources = dict(row.dependencies)
    try:
      desired = self._setup_values(CP, sources) if request.value == 'On' else {}
    except (ValueError, TypeError, UnicodeError, OverflowError):
      return False
    capability = row.capability
    def authorized():
      return (self.authority('parked_preferences') and self.authority('preferences') and
              self.vehicle_fingerprint() == request.vehicle_fingerprint and self.capability() == capability)
    return transition(self.params, str(CP.carFingerprint), sources, desired, request.expected,
                      request.value == 'On', authorized)

  def row(self) -> FeatureRow | None:
    CP = self.vehicle_params()
    capability = self.capability(CP)
    if capability is None:
      return None
    raw, readable = read_saved(self.params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
    selected = selection_from_bytes(CP, raw) if readable else None
    valid = readable and selected is not None and selected.source != 'invalid'
    value = (TORQUE_CHOICE if selected.force_torque and selected.mode == ControllerMode.STANDARD else
             CHOICES[0] if selected.mode == ControllerMode.STANDARD else CHOICES[1]) if valid else 'Invalid saved controller choice'
    choices = (CHOICES[0], TORQUE_CHOICE, CHOICES[1]) if steering_controller(CP) == 'pid' or valid and selected.force_torque else CHOICES
    learning, learning_readable = read_saved(self.params, LEARNING_OFF_KEY, 1)
    preference = read_preference(self.params, CP, legacy_snapshot=(learning, learning_readable))
    learning_valid = learning_readable and preference.valid
    allowed = valid and learning_valid and self.authority('preferences')
    reason = ('Choose the steering controller for your next drive. ' + TUNING_GUIDANCE if valid and learning_valid else
              'Saved steering preferences are unreadable or invalid; no change was made.')
    return FeatureRow(DOCUMENT_KEY, 'Steering Controller', value, raw, choices if valid else (),
                      available=allowed, reason=reason, capability=capability,
                      dependencies=((LEARNING_OFF_KEY, learning), (LEARNING_PREFERENCES_KEY, preference.raw)),
                      default_value=CHOICES[0] if selection_from_bytes(CP, None).mode == ControllerMode.STANDARD else CHOICES[1], level=SettingLevel.ADVANCED)

  def learning_row(self, CP=None) -> FeatureRow | None:
    CP = self.vehicle_params() if CP is None else CP
    capability = self.learning_capability(CP)
    if capability is None:
      return None
    controller, controller_readable = read_saved(self.params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
    selection = selection_from_bytes(CP, controller)
    legacy, legacy_readable = read_saved(self.params, LEARNING_OFF_KEY, 1)
    preference = read_preference(self.params, CP, legacy_snapshot=(legacy, legacy_readable))
    valid = preference.valid and legacy_readable and controller_readable and selection.source != 'invalid'
    value = ('On' if preference.enabled else 'Off') if valid else 'Invalid saved preference'
    reason = 'StarPilot’s FLM tuning will attempt to automatically correct torque profiles as you drive.'
    return FeatureRow(LEARNING_OFF_KEY, 'Use StarPilot Auto Learn', value, preference.raw, ('Off', 'On') if valid else (),
                      available=valid and self.authority('preferences'),
                      reason=reason if valid else 'Saved controller or learning preference is unreadable or invalid.',
                      capability=capability, dependencies=((DOCUMENT_KEY, controller), (LEARNING_OFF_KEY, legacy)),
                      default_value="On", level=SettingLevel.ADVANCED)

  def apply_learning(self, request: FeatureSettingsRequest) -> bool:
    CP = self.vehicle_params()
    if CP is None:
      return False
    row = self.learning_row(CP)
    if (row is None or not row.available or request.key != LEARNING_OFF_KEY or request.value not in row.choices or
        request.capability != row.capability or request.expected != row.source or request.dependencies != row.dependencies or
        request.related_source is not None or request.display_unit or request.direction or
        not request.vehicle_fingerprint or request.vehicle_fingerprint != self.vehicle_fingerprint()):
      return False

    def authorized() -> bool:
      current = self.learning_row()
      return (self.vehicle_fingerprint() == request.vehicle_fingerprint and current is not None and current.available and
              current.capability == request.capability and current.dependencies == request.dependencies)

    try:
      encoded = replace_preference(request.expected, CP, request.value == 'On')
    except (AttributeError, TypeError, ValueError, UnicodeError):
      return False
    result = commit_exact(self.params, key=LEARNING_PREFERENCES_KEY, max_bytes=LEARNING_PREFERENCES_LIMIT,
                          raw=encoded, expected=request.expected,
                          authorized=authorized, temp_prefix='.torque-learning-')
    return result.verified

  def apply(self, request: FeatureSettingsRequest) -> bool:
    if (request.key != DOCUMENT_KEY or request.value not in MODES or request.capability is None or
        request.related_source is not None or request.display_unit or request.direction or
        not request.vehicle_fingerprint or request.vehicle_fingerprint != self.vehicle_fingerprint()):
      return False
    CP = self.vehicle_params()
    row = self.row()
    if CP is None or row is None or request.value not in row.choices or request.capability != self.capability(CP):
      return False
    raw, readable = read_saved(self.params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
    learning, learning_readable = read_saved(self.params, LEARNING_OFF_KEY, 1)
    preference = read_preference(self.params, CP, legacy_snapshot=(learning, learning_readable))
    if (not readable or raw != request.expected or not self.authority('preferences') or
        not learning_readable or not preference.valid or
        request.dependencies != ((LEARNING_OFF_KEY, learning), (LEARNING_PREFERENCES_KEY, preference.raw))):
      return False
    try:
      selected = selection_from_bytes(CP, raw)
      if selected.source == 'invalid':
        return False
      force_torque = request.value == TORQUE_CHOICE or request.value == CHOICES[1] and (
        steering_controller(CP) == 'pid' or selected.force_torque)
      encoded = replace_mode(raw, CP, MODES[request.value], force_torque=force_torque)
    except (UnicodeError, ValueError, TypeError, OverflowError):
      return False

    def authorized() -> bool:
      current, readable = read_saved(self.params, LEARNING_OFF_KEY, 1)
      return (self.authority('preferences') and self.vehicle_fingerprint() == request.vehicle_fingerprint and
              self.capability() == request.capability and readable and current == learning and
              read_preference(self.params, CP, legacy_snapshot=(current, readable)).raw == preference.raw)

    result = commit_exact(self.params, key=DOCUMENT_KEY, max_bytes=MAX_DOCUMENT_BYTES,
                          raw=encoded, expected=request.expected, authorized=authorized,
                          temp_prefix='.lateral-controller-')
    return result.verified
