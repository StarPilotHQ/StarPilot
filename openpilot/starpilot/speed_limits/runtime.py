"""One plannerd-owned SLC lifecycle over real, typed replayable observations."""

from openpilot.starpilot.common.slc_warning import AcceptedChangeWarning
from openpilot.starpilot.longitudinal.slc_coast import accepted_easing

import math
import uuid
from dataclasses import dataclass, replace

import openpilot.cereal.messaging as messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.selfdrive.car.cruise import slc_sets_set_speed, V_CRUISE_MIN, V_CRUISE_MAX
from openpilot.starpilot.aol.runtime import current_axis
from openpilot.starpilot.controllers.wheel_actions import LIFETIME_NS as WHEEL_ACTION_MAX_AGE_NS
from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import action_arbitration as arb
from openpilot.starpilot.speed_limits import composition as comp
from openpilot.starpilot.speed_limits import selection as sel
from openpilot.starpilot.speed_limits import speed_domain as sd
from openpilot.starpilot.speed_limits.runtime_settings import ADVANCE_SECONDS, Settings
from openpilot.starpilot.speed_limits.manual_targets import PerLimitTargets
from openpilot.starpilot.speed_limits.map_observation import MapSource, copy_frame
from openpilot.starpilot.speed_limits.mapped_timing import MappedTiming, ShortMapZone
from openpilot.starpilot.speed_limits.preview_continuity import AcceptedPreviewHold
from openpilot.starpilot.speed_limits.reengagement import Engagement, CurrentLimitZone
from openpilot.starpilot.speed_limits.preview_rollback import PreviewRollback
from openpilot.starpilot.speed_limits.transition_comfort import TransitionComfort
from openpilot.starpilot.speed_limits.advance import Advance
from openpilot.starpilot.speed_limits.vision.observation import clock_pair_ns, vision_observation


def _number(value, *, positive=False) -> bool:
  return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def _fresh(sm, service: str, now_ns: int) -> bool:
  timestamp = sm.logMonoTime.get(service, 0)
  frequency = SERVICE_LIST[service].frequency
  # SubMaster uses ten expected periods for its alive check. Apply the same
  # transport window to replay timestamps, which may be held across frames.
  max_age_ns = int(10e9 / frequency) if frequency > 0 else 0
  return (bool(sm.valid.get(service, False)) and bool(sm.alive.get(service, False)) and
          0 < timestamp <= now_ns and now_ns - timestamp <= max_age_ns)


def _paired_card_frame(sm, source_log_ns: int, now_ns: int, *, max_skew_service: str) -> bool:
  """Accept a card event preceding the latest CarState by at most two producer ticks."""
  car_log_ns = int(sm.logMonoTime.get('carState', 0))
  max_skew_ns = int(2e9 / SERVICE_LIST[max_skew_service].frequency)
  return (_fresh(sm, 'carState', now_ns) and 0 < source_log_ns <= car_log_ns <= now_ns and
          car_log_ns - source_log_ns <= max_skew_ns)


def dashboard_observation(evidence, *, car_fingerprint: str, session_id: str, now_ns: int) -> acc.Observation:
  """Project an OEM TSR observation; schema default zero is never absence."""
  status = str(evidence.status)
  timestamp, expiry = int(evidence.observedMonoTime), int(evidence.validUntilMonoTime)
  if status == "unknown" or timestamp <= 0:
    return acc.Observation(acc.ObservationKind.UNKNOWN)
  if timestamp > now_ns or expiry <= timestamp or now_ns > expiry:
    return acc.Observation(acc.ObservationKind.STALE)
  if status == "stale":
    return acc.Observation(acc.ObservationKind.STALE)
  if status == "absent":
    return acc.Observation(acc.ObservationKind.ABSENT)
  if status != "valid" or not _number(evidence.speedMps, positive=True) or int(evidence.episode) <= 0:
    return acc.Observation(acc.ObservationKind.UNKNOWN)
  producer_session = str(evidence.producerSessionId)
  if not producer_session:
    return acc.Observation(acc.ObservationKind.UNKNOWN)
  identity = acc.ObservationIdentity(acc.IdentityKind.PRODUCER_EPISODE,
                                     value=f"{session_id}:{producer_session}:{car_fingerprint}:{int(evidence.episode)}")
  return acc.Observation(acc.ObservationKind.VALID,
                         acc.Candidate(sel.Source.DASHBOARD.value, identity, float(evidence.speedMps)))


def _authority(CP, sm, host_long: bool, lateral: bool, *, long_enabled: bool) -> acc.Authority:
  car_state = sm["carState"]
  stock = not bool(CP.openpilotLongitudinalControl) and bool(car_state.cruiseState.enabled)
  if bool(CP.openpilotLongitudinalControl) and (host_long or long_enabled):
    return acc.Authority(acc.Mode.COMBINED if lateral else acc.Mode.LONGITUDINAL_ONLY,
                         acc.LongitudinalOwner.SYSTEM, lateral, host_long, False, False)
  if stock:
    return acc.Authority(acc.Mode.LATERAL_ONLY if lateral else acc.Mode.OFF,
                         acc.LongitudinalOwner.STOCK, lateral, False, True, False)
  return acc.Authority(acc.Mode.LATERAL_ONLY if lateral else acc.Mode.OFF,
                       acc.LongitudinalOwner.NONE, lateral, False, False, not lateral)


@dataclass(frozen=True)
class Action:
  session_id: str
  sequence_id: int
  decision_id: int
  presentation_id: int
  kind: str
  observed_ns: int | None = None
  wheel_observed_ns: int = 0


@dataclass(frozen=True)
class CruiseEvent:
  event_id: int
  observed_ns: int
  previous_mps: float
  selected_mps: float
  button: str
  producer_session: str = 'replay-card'
  car_state_log_ns: int = 0
  kind: str = 'driverChange'
  session_id: str = ''
  decision_id: int = 0
  presentation_id: int = 0
  command_id: int = 0
  wheel_override_since_ns: int = 0
  long_press: bool = False


@dataclass(frozen=True)
class Output:
  result: comp.Result
  message: object
  action_result: arb.Result | None = None
  command: object | None = None  # slcCruiseCommand for card where SLC sets the set speed
  settings: Settings | None = None


class Runtime:
  """Own a unique drive session, action ledger and current-frame UI receipt."""

  def __init__(self, settings: Settings, *, session_id: str | None = None, vision_enabled: bool = False,
               vision_control_qualified: bool = False, vision_display_qualified: bool = False, is_metric: bool = False,
               previous_limit: acc.AcceptedLimit | None = None):
    if type(is_metric) is not bool:
      raise ValueError('SLC display units must be Boolean')
    self.settings = settings
    self.is_metric = is_metric
    self.map_source = MapSource()
    self.mapped = MappedTiming()
    self.warning_event = None
    self.short_zones = ShortMapZone()
    self.preview_hold = AcceptedPreviewHold()
    self.raw_map_observation = None
    self.engagement = Engagement()
    self.manual_targets = PerLimitTargets()
    self.memory_recall_pending = False
    self.memory_was_engaged = False
    self.current_zone = CurrentLimitZone()
    self.rollback = PreviewRollback()
    self.rollback_target = None
    self.comfort = TransitionComfort()
    self.comfort_request = None
    self.comfort_limits = None
    self.easing_request = None
    self.command_mapped_approach = False
    self.current_observations = {}
    self.command_kind = 'adoptAcceptedLimit'
    self.reengage_target = None
    self.advance = Advance()
    self.vision_enabled = vision_enabled
    # Exact saved Vision opt-in may grant control; source identity and current
    # driver confirmation remain independent requirements for each episode.
    self.vision_control_qualified = vision_enabled and vision_control_qualified
    self.vision_display_qualified = bool(vision_enabled and vision_display_qualified and settings.display and not settings.enabled)
    self.vision_diagnostic = acc.Observation(acc.ObservationKind.UNKNOWN)
    self._last_vision_producer = ''
    self._last_vision_frame = 0
    self._last_vision_eof_boot = 0
    self._last_vision_episode = 0
    self.session_id = session_id or uuid.uuid4().hex
    self.accepted_warning = AcceptedChangeWarning(self.session_id)
    self.state = comp.new_session(self.session_id, previous_limit)
    self.history_limit = self.state.acceptance.accepted
    self.ledger = arb.new_ledger(self.session_id)
    self.last_ui_sequence = -1
    self.last_cruise_event = -1
    self.last_cruise_producer: str | None = None
    self.last_command_id = 0
    self.command_status = ''
    self.command_interrupted = False
    self._command_ui_sequence = 0
    # Where SLC sets the set speed: the command card applies to the software set speed.
    self.command_issued_ns = 0
    self.command_expiry_ns = 0
    self.set_speed_after_ns = 0
    self.command_expected_mps = 0.0
    self.command_target_mps = 0.0
    self.command_card_producer = ''
    self.commanded_acceptance: acc.AcceptedLimit | None = None
    self.set_speed_rule = False
    # A reset (new drive, error recovery) starts from the last accepted limit; planner_host turns this off where SLC
    # sets the set speed unless Use the last limit (SLCLastLimitFallback) is on, as at the start of a drive.
    self.carry_last_limit = True
    self._ui_receipt = None
    self.last_received: acc.Candidate | None = None
    self._ego_delta_key: tuple[str, str] | None = None
    self._ego_delta_mps = 0.0
    self._ego_sample_ns = 0
    self.current_presentation: acc.Presentation | None = None

  def reset(self) -> None:
    previous_limit = self.history_limit if self.carry_last_limit else None
    self.map_source.reset()
    self.mapped = MappedTiming()
    self.warning_event = None
    self.short_zones = ShortMapZone()
    self.preview_hold = AcceptedPreviewHold()
    self.raw_map_observation = None
    self.engagement = Engagement()
    self.manual_targets = PerLimitTargets()
    self.memory_recall_pending = False
    self.memory_was_engaged = False
    self.current_zone = CurrentLimitZone()
    self.rollback = PreviewRollback()
    self.rollback_target = None
    self.comfort = TransitionComfort()
    self.comfort_request = None
    self.comfort_limits = None
    self.easing_request = None
    self.command_mapped_approach = False
    self.current_observations = {}
    self.command_kind = 'adoptAcceptedLimit'
    self.reengage_target = None
    self.advance = Advance()
    self.vision_diagnostic = acc.Observation(acc.ObservationKind.UNKNOWN)
    self._last_vision_producer = ''
    self._last_vision_frame = 0
    self._last_vision_eof_boot = 0
    self._last_vision_episode = 0
    self.session_id = uuid.uuid4().hex
    self.accepted_warning = AcceptedChangeWarning(self.session_id)
    try:
      self.state = comp.new_session(self.session_id, previous_limit)
    except ValueError:
      self.state = comp.new_session(self.session_id)
    self.history_limit = self.state.acceptance.accepted
    self.ledger = arb.new_ledger(self.session_id)
    self.last_ui_sequence = -1
    self.last_cruise_event = -1
    self.last_cruise_producer = None
    # Keep the SET deadline: the previous session's command may still be in flight.
    self.last_command_id = 0
    self.command_status = ''
    self.command_interrupted = False
    self._command_ui_sequence = 0
    self.command_issued_ns = 0
    self.command_expiry_ns = 0
    self.command_expected_mps = 0.0
    self.command_target_mps = 0.0
    self.command_card_producer = ''
    self.commanded_acceptance = None
    self._ui_receipt = None
    self.last_received = None
    self._ego_delta_key = None
    self._ego_delta_mps = 0.0
    self._ego_sample_ns = 0
    self.current_presentation = None

  def update_offsets(self, offsets: sd.OffsetSchedule, *, is_metric: bool | None = None) -> None:
    if is_metric is not None:
      if type(is_metric) is not bool:
        raise ValueError('SLC display units must be Boolean')
      self.is_metric = is_metric
    changed_offsets = offsets != self.settings.offsets
    if changed_offsets:
      self.rollback.cancel()
    self.settings = replace(self.settings, offsets=offsets)
    # Renew actions for new offsets/units without forgetting driver choices.
    acceptance = replace(self.state.acceptance, observation=acc.Observation(acc.ObservationKind.UNKNOWN),
                         pending=None, presentation=None, timer_running=False)
    self.current_presentation = None
    self.state = replace(self.state, acceptance=acceptance, wheel_override_since_ns=0)
    self._ui_receipt = None
    # Display units alone must not reapply an accepted SET in different rounding.
    if changed_offsets:
      self.command_status = ''
      self.commanded_acceptance = None
      self.command_interrupted = False

  def update_advance(self, higher_seconds: int, lower_seconds: int) -> None:
    if any(type(seconds) is not int or seconds not in ADVANCE_SECONDS for seconds in (higher_seconds, lower_seconds)):
      raise ValueError('invalid SLC advance time')
    self.settings = replace(self.settings, advance_higher_seconds=higher_seconds, advance_lower_seconds=lower_seconds)
    encounter, pending = self.advance.encounter, self.state.acceptance.pending
    if encounter is not None and (higher_seconds if encounter.speed_mps > encounter.from_mps else lower_seconds) == 0:
      if (not encounter.crossed and pending is not None and
          pending.candidate.source == 'map' and pending.candidate.speed_mps == encounter.speed_mps):
        self.state = replace(self.state, acceptance=replace(self.state.acceptance,
          observation=acc.Observation(acc.ObservationKind.UNKNOWN), pending=None, presentation=None, timer_running=False))
      self.advance.disable()

  def _event_current(self, event: CruiseEvent, sm, now_ns: int) -> bool:
    return (bool(event.producer_session) and 0 < event.observed_ns <= now_ns and
            now_ns - event.observed_ns <= int(2e9 / SERVICE_LIST['modelV2'].frequency) and
            _paired_card_frame(sm, event.car_state_log_ns, now_ns, max_skew_service='modelV2') and
            (event.producer_session != self.last_cruise_producer or event.event_id > self.last_cruise_event))

  def _mark_event(self, event: CruiseEvent) -> None:
    if event.producer_session != self.last_cruise_producer:
      self.last_cruise_producer = event.producer_session
      self.last_cruise_event = -1
    self.last_cruise_event = event.event_id

  def _cruise(self, event: CruiseEvent | None, sm, now_ns: int) -> arb.Result | None:
    if (event is None or event.kind != 'driverChange' or not self._event_current(event, sm, now_ns) or
        event.button not in ('accel', 'decel') or not _number(event.previous_mps, positive=True) or
        not _number(event.selected_mps, positive=True) or event.previous_mps == event.selected_mps or
        abs(float(sm['carState'].vCruise) / 3.6 - event.selected_mps) > 0.1):
      return None
    self._mark_event(event)
    action_id = self.ledger.last_action_id + 1
    context_id = 'driver-set'
    if self.set_speed_rule and self.state.override is not None:
      # Where SLC sets the set speed, a SET change older than the current override context is stale.
      context = self.state.override.context
      if context is not None and context.issued_at_ns > event.observed_ns:
        return None
      context_id = context.context_id if context is not None else 'no-slc-context'
    begun = arb.step(self.ledger, arb.BeginAction(self.session_id, action_id, arb.Origin.DRIVER_CRUISE, context_id), now_ns=now_ns)
    self.ledger = begun.state
    if begun.errors or begun.status != 'begun':
      return None
    resolved = arb.step(self.ledger, arb.ResolveAction(self.session_id, action_id, arb.Disposition.DRIVER_INTENT), now_ns=now_ns)
    self.ledger = resolved.state
    if resolved.errors:
      return None
    # Card serializes the event's m/s as Float32, while carState carries kph.
    # The guard above already binds them within transport precision. Classify
    # in the exact current selected-speed coordinate used by the reducer.
    classified = arb.step(self.ledger, arb.CruiseChange(self.session_id, self.ledger.last_effect_id + 1, action_id,
                                                         event.previous_mps, float(sm['carState'].vCruise) / 3.6), now_ns=now_ns)
    self.ledger = classified.state
    completed = arb.step(self.ledger, arb.CompleteAction(self.session_id, action_id), now_ns=now_ns)
    self.ledger = completed.state
    return classified if not classified.errors and classified.change is not None else None

  def _physical_action(self, event: CruiseEvent, sm, now_ns: int) -> tuple[acc.DriverAction | None, acc.AdoptRequest | None, int | None]:
    if (event.kind not in ('confirmationAccept', 'confirmationReject', 'engagementAdopt') or
        not self._event_current(event, sm, now_ns)):
      return None, None, None
    self._mark_event(event)
    engagement = event.kind == 'engagementAdopt'
    pending = self.state.acceptance.pending
    shown = self.state.acceptance.presentation
    if shown is None and engagement:
      shown = self.current_presentation
    if (event.session_id != self.session_id or shown is None or
        event.producer_session != str(sm['slcDashboardObservation'].producerSessionId) or
        event.presentation_id != shown.presentation_id or
        event.button != ('accel' if event.kind == 'confirmationAccept' else 'decel')):
      return None, None, None
    if engagement:
      if (event.command_id != 0 or not _number(event.selected_mps, positive=True) or
          not _number(float(sm['carState'].vCruise), positive=True) or
          abs(float(sm['carState'].vCruise) / 3.6 - event.selected_mps) > 0.1):
        return None, None, None
    elif pending is None or event.decision_id != pending.decision_id or pending.candidate != shown.candidate:
      return None, None, None
    action_id = self.ledger.last_action_id + 1
    context = f'presentation:{shown.presentation_id}' if engagement else f'pending:{pending.decision_id}'
    begun = arb.step(self.ledger, arb.BeginAction(self.session_id, action_id, arb.Origin.DRIVER_CRUISE, context), now_ns=now_ns)
    self.ledger = begun.state
    if begun.errors or begun.status != 'begun':
      return None, None, None
    if engagement:
      return None, acc.AdoptRequest(self.session_id, action_id, shown.presentation_id, shown.candidate), action_id
    assert pending is not None
    kind = acc.ActionKind.ACCEPT if event.kind == 'confirmationAccept' else acc.ActionKind.REJECT
    return acc.DriverAction(self.session_id, action_id, pending.decision_id, kind, event.observed_ns), None, action_id

  def _command_receipt(self, event: CruiseEvent, sm, now_ns: int) -> None:
    if (event.kind not in ('commandApplied', 'commandRejected') or
        not self._event_current(event, sm, now_ns)):
      return
    self._mark_event(event)
    if (event.session_id == self.session_id and event.command_id == self.last_command_id and
        event.producer_session == self.command_card_producer and self.command_status in ('issued', 'expired') and
        self.command_issued_ns <= event.observed_ns <= self.command_expiry_ns):
      if event.kind == 'commandApplied':
        if (not _number(event.previous_mps, positive=True) or
            abs(event.previous_mps - self.command_expected_mps) > 0.1 or
            not _number(event.selected_mps, positive=True) or
            abs(event.selected_mps - self.command_target_mps) > 0.5):
          return
        self.command_status = 'applied'
        if self.command_kind == 'reengageZone':
          self.engagement.applied()
          self.state = replace(self.state, override=replace(self.state.override, persistent_selected_mps=event.selected_mps))
          if self.commanded_acceptance is not None and not self.command_mapped_approach:
            self.manual_targets.remember(event.selected_mps, self.commanded_acceptance.candidate.speed_mps)
        restored_override = self.rollback.acknowledged(command_id=self.last_command_id,
          selected=event.selected_mps, kind=self.command_kind)
        if restored_override is not None:
          self.state = replace(self.state, override=replace(self.state.override, persistent_selected_mps=restored_override))
        self.set_speed_after_ns = event.observed_ns
        if self.commanded_acceptance == self.state.acceptance.accepted:
          applied = None if restored_override is not None else event.selected_mps
          self.state = replace(self.state, override=replace(self.state.override, applied_selected_mps=applied))
      else:
        self.command_status = 'rejected'
        self.set_speed_after_ns = event.observed_ns

  def _set_speed_ready(self, sm) -> bool:
    # Where SLC sets the set speed, trust SET only after card's last command receipt has been observed in carState.
    return int(sm.logMonoTime.get('carState', 0)) > self.set_speed_after_ns

  @property
  def _maps_selected(self) -> bool:
    policy = self.settings.selection
    return sel.Source.MAP in policy.slots or policy.mode is not sel.SelectionMode.ORDERED

  def _observations(self, sm, CP, now_ns: int, now_boot_ns: int | None = None) -> dict[sel.Source, acc.Observation]:
    unknown = acc.Observation(acc.ObservationKind.UNKNOWN)
    values = dict.fromkeys(sel.Source, unknown)
    current_map = unknown
    if (_fresh(sm, "carState", now_ns) and not sm["carState"].canTimeout and
        _fresh(sm, 'slcDashboardObservation', now_ns)):
      evidence = sm['slcDashboardObservation']
      source_log_ns = int(evidence.carStateLogMonoTime)
      if (int(sm.logMonoTime['slcDashboardObservation']) == source_log_ns and
          _paired_card_frame(sm, source_log_ns, now_ns, max_skew_service='carState')):
        values[sel.Source.DASHBOARD] = dashboard_observation(
          evidence, car_fingerprint=str(CP.carFingerprint), session_id=self.session_id, now_ns=now_ns)
    elif sm.logMonoTime.get("carState", 0) or sm.logMonoTime.get('slcDashboardObservation', 0):
      values[sel.Source.DASHBOARD] = acc.Observation(acc.ObservationKind.STALE)
    if self.vision_enabled and _fresh(sm, 'slcVisionObservation', now_ns):
      pair = clock_pair_ns() if now_boot_ns is None else (now_ns, now_boot_ns)
      if pair is not None:
        evidence = sm['slcVisionObservation'].vision
        self.vision_diagnostic = vision_observation(
          evidence, receipt_mono_ns=int(sm.logMonoTime['slcVisionObservation']),
          now_mono_ns=pair[0], now_boot_ns=pair[1], car_fingerprint=str(CP.carFingerprint),
          drive_session=self.session_id)
        if self.vision_diagnostic.kind is acc.ObservationKind.VALID:
          producer = str(evidence.producerSessionId)
          frame_id, eof_boot, episode = int(evidence.frameId), int(evidence.cameraFrameEofBootTime), int(evidence.episode)
          if (producer == self._last_vision_producer and
              (frame_id < self._last_vision_frame or eof_boot < self._last_vision_eof_boot or
               episode < self._last_vision_episode)):
            self.vision_diagnostic = acc.Observation(acc.ObservationKind.STALE)
          else:
            self._last_vision_producer = producer
            self._last_vision_frame = frame_id
            self._last_vision_eof_boot = eof_boot
            self._last_vision_episode = episode
        if self.vision_control_qualified or self.vision_display_qualified:
          values[sel.Source.VISION] = self.vision_diagnostic
      else:
        self.vision_diagnostic = acc.Observation(acc.ObservationKind.STALE)
    elif self.vision_enabled and sm.logMonoTime.get('slcVisionObservation', 0):
      self.vision_diagnostic = acc.Observation(acc.ObservationKind.STALE)
    else:
      self.vision_diagnostic = acc.Observation(acc.ObservationKind.UNKNOWN)
    if self._maps_selected:
      pair = clock_pair_ns() if now_boot_ns is None else (now_ns, now_boot_ns)
      if pair is not None:
        received = bool(sm.updated.get('mapdOut', False))
        try:
          frame = (copy_frame(sm['mapdOut'], event_valid=bool(sm.valid.get('mapdOut', False)),
                              event_boot_ns=int(sm.logMonoTime.get('mapdOut', 0))) if received else None)
        except (AttributeError, ValueError, TypeError, OverflowError):
          frame = None  # malformed current message revokes held map evidence
        values[sel.Source.MAP] = self.map_source.step(frame, received=received,
          now_boot_ns=pair[1], transport_alive=bool(sm.alive.get('mapdOut', False)),
          drive_session=self.session_id, car_fingerprint=str(CP.carFingerprint))
        cs = sm['carState']
        self.raw_map_observation = values[sel.Source.MAP]
        current_map, timing_frame = self.short_zones.project(values[sel.Source.MAP],
          self.map_source.tracker.diagnostic(now_boot_ns=pair[1]).frame,
          now_ns=now_ns, speed=float(cs.vEgo),
          enabled=bool(self.settings.enabled and _fresh(sm, 'carState', now_ns) and cs.canValid and not cs.canTimeout),
          allow_start=not self.mapped.approach.active)
        self.mapped.profile = getattr(self, 'braking_profile', self.mapped.profile)
        self.mapped.acceleration_profile = getattr(self, 'acceleration_profile', self.mapped.acceleration_profile)
        timing_enabled = bool(self.settings.enabled and _fresh(sm, 'carState', now_ns) and cs.canValid and not cs.canTimeout)
        held = self.preview_hold.keep(timing_frame, accepted=self.state.acceptance.accepted,
          now_boot_ns=pair[1], speed=float(cs.vEgo), enabled=timing_enabled)
        if held is not None:
          values[sel.Source.MAP] = held
          self.mapped.current = False  # Retention is not fresh current-road authority or a new warning.
        else:
          values[sel.Source.MAP] = self.mapped.project(current_map, timing_frame,
            now_ns=now_ns, now_boot_ns=pair[1], speed=float(cs.vEgo), acceleration=float(cs.aEgo),
            cluster_delta=max(0., float(cs.vEgoCluster)-float(cs.vEgo)), settings=self.settings,
            enabled=timing_enabled, target_override=(self.manual_targets.target(timing_frame.next_speed_mps, now_ns)
              if timing_frame is not None and self.settings.custom.manual_memory == 'drive' else None))
          self.preview_hold.observe(timing_frame,values[sel.Source.MAP],active=self.mapped.current and self.mapped.approach.active)
      else:
        self.map_source.reset()
        self.short_zones = ShortMapZone()
        self.preview_hold = AcceptedPreviewHold()
        self.raw_map_observation = acc.Observation(acc.ObservationKind.STALE)
        values[sel.Source.MAP] = self.raw_map_observation
    self.current_observations = dict(values)
    self.current_observations[sel.Source.MAP] = current_map
    # Vision still requires its independent source-specific qualification.
    return values

  def _smoothed_ego_delta(self, ego, CP, sample_ns: int, *, standstill: bool) -> float | None:
    """Estimate dashboard calibration without feeding its display steps into cruise."""
    if (ego.status is not sd.DomainStatus.VALID or ego.raw_mps is None or ego.cluster_mps is None or
        standstill or ego.raw_mps <= 0. or ego.cluster_mps <= 0.):
      self._ego_delta_key = None
      return None
    key = (str(CP.brand), str(CP.carFingerprint))
    delta = ego.cluster_mps - ego.raw_mps
    if key != self._ego_delta_key or sample_ns < self._ego_sample_ns:
      self._ego_delta_mps = delta
    elif sample_ns > self._ego_sample_ns:
      dt = (sample_ns - self._ego_sample_ns) / 1e9
      self._ego_delta_mps += dt / (2.0 + dt) * (delta - self._ego_delta_mps)
    self._ego_delta_key, self._ego_sample_ns = key, sample_ns
    return max(0., self._ego_delta_mps)

  def _frame(self, sm, CP, now_ns: int, now_boot_ns: int | None, action: acc.DriverAction | None, adopt: acc.AdoptRequest | None,
             classified: arb.Result | None, *, wheel_override_blocked: bool) -> comp.Frame:
    car_fresh = _fresh(sm, "carState", now_ns) and not sm["carState"].canTimeout
    controls_fresh = _fresh(sm, "controlsState", now_ns)
    selfdrive_fresh = _fresh(sm, "selfdriveState", now_ns)
    car_control_fresh = _fresh(sm, 'carControl', now_ns)
    car_state, controls, selfdrive = sm["carState"], sm["controlsState"], sm["selfdriveState"]
    long_active = str(controls.longControlState) != "off" if controls_fresh else False
    host_long = bool(CP.openpilotLongitudinalControl and long_active and car_control_fresh and sm['carControl'].longActive and
                     selfdrive.enabled and car_fresh and selfdrive_fresh)
    long_enabled = bool(car_fresh and controls_fresh and selfdrive_fresh and car_control_fresh and
                        selfdrive.enabled and sm['carControl'].enabled)
    if 'aolAxisState' in sm.logMonoTime:
      axis = current_axis(sm, now_ns=now_ns)
      long_enabled = long_enabled and bool(axis is not None and axis.nativeAcknowledged and axis.desiredLongitudinal)
    lateral = bool(car_control_fresh and sm['carControl'].latActive and selfdrive_fresh)
    authority = _authority(CP, sm, host_long, lateral, long_enabled=long_enabled)
    # Native pedal handoff keeps the engagement while the driver controls acceleration.
    authority = replace(authority, accelerator_override=bool(CP.openpilotLongitudinalControl and long_enabled and car_state.gasPressed))
    selected = float(car_state.vCruise) if car_fresh and _number(car_state.vCruise) else 0.0
    selected_cluster = float(car_state.vCruiseCluster) if car_fresh and _number(car_state.vCruiseCluster) else None
    # Filtered ego speed can undershoot zero after the wheels stop.
    ego_speeds = (car_state.vEgo, car_state.vEgoCluster)
    ego_valid = car_fresh and all(type(speed) in (int, float) and math.isfinite(speed) and
                                  (car_state.standstill or speed >= 0.) for speed in ego_speeds)
    ego = (sd.SpeedPair(sd.DomainStatus.VALID, max(0., ego_speeds[0]), max(0., ego_speeds[1]), self.session_id, now_ns)
           if ego_valid else sd.SpeedPair(sd.DomainStatus.STALE if sm.logMonoTime.get("carState", 0) else sd.DomainStatus.UNKNOWN))
    host = comp.HostState(bool(CP.openpilotLongitudinalControl), host_long, bool(selfdrive.enabled and selfdrive_fresh),
                          bool(controls.forceDecel) if controls_fresh else True, selected, selected_cluster)
    observations = self._observations(sm, CP, now_ns, now_boot_ns)
    wheel_enabled = (self.settings.wheel_override and not CP.pcmCruise and car_fresh and car_state.canValid and
                     V_CRUISE_MIN <= selected <= V_CRUISE_MAX and selected_cluster == selected and
                     not car_state.brakePressed)
    wheel_blocked = wheel_override_blocked or car_state.standstill or car_state.cruiseState.standstill
    frame = comp.Frame(now_ns, observations, self.settings.selection, authority,
                      self.settings.acceptance, self.settings.offsets,
                      sd.DomainStatus.VALID if car_fresh and selected_cluster is not None else sd.DomainStatus.STALE,
                      ego, bool(car_state.gasPressed) if car_fresh else False, host, action=action, adopt=adopt,
                      classified_cruise=classified, is_metric=self.is_metric, pedal_override=self.settings.pedal_override,
                      wheel_override_enabled=wheel_enabled, wheel_override_blocked=wheel_blocked,
                      filtered_ego_delta_mps=self._smoothed_ego_delta(
                        ego, CP, int(sm.logMonoTime.get('carState', 0)), standstill=bool(car_state.standstill)))
    maps_selected = self._maps_selected
    encounter = self.advance.encounter
    if not maps_selected and encounter is None:
      return frame
    current = sel.select_limit(observations, self.settings.selection).observation.candidate
    accepted = self.state.acceptance.accepted
    # Confirm the old cap before a short zone can offer its exit preview.
    if (encounter is not None and accepted is not None and accepted == encounter.applied_preview and
        current is not None and current.speed_mps == accepted.candidate.speed_mps):
      self.history_limit = accepted
    shown = self.state.acceptance.presentation
    if encounter is not None and shown is not None and current is not None and current != self.last_received:
      identity = self.state.acceptance.next_presentation_id
      self.state = replace(self.state, acceptance=replace(self.state.acceptance,
        presentation=acc.Presentation(identity, shown.candidate), next_presentation_id=identity+1))
    if not maps_selected:
      self.advance = Advance()
      self.map_source.reset()
      self.current_presentation = None
      self.advance.retired_cap = encounter.applied_preview
      self.advance.retired_pending_speed_mps = encounter.speed_mps
      proposals = None
    elif (adopt is not None and encounter is not None and
        adopt.candidate == self.last_received == current):
      self.advance.use_current(adopt.candidate)
      proposals = None
    else:
      rejected = self.state.acceptance.rejected
      # Give the current Maps limit its own decision before previewing the exit.
      current_unresolved = (current is not None and current.source == 'map' and current.speed_mps not in (
                              accepted.candidate.speed_mps if accepted is not None else None,
                              rejected.speed_mps if rejected is not None else None))
      proposals = self.advance.propose(observations, self.settings.selection, self.map_source.upcoming,
        current_way_id=self.map_source.way_id, higher_seconds=self.settings.advance_higher_seconds,
        lower_seconds=self.settings.advance_lower_seconds,
        speed_mps=float(car_state.vEgo) if ego_valid else None,
        standstill=bool(getattr(car_state, 'standstill', False)) or str(getattr(car_state, 'gearShifter', '')) == 'reverse',
        can_offer=self.settings.enabled and authority.can_control and not host.force_decel,
        confirmation_active=authority.can_control, now_ns=now_ns, pending=self.state.acceptance.pending, live=current,
        current_unresolved=current_unresolved)
    pending = self.state.acceptance.pending
    if pending is not None and pending.candidate.source == 'map' and pending.candidate.speed_mps == self.advance.retired_pending_speed_mps:
      self.state = replace(self.state, acceptance=replace(self.state.acceptance,
        observation=acc.Observation(acc.ObservationKind.UNKNOWN), pending=None, presentation=None, timer_running=False))
    preview_reverted = accepted is not None and accepted == self.advance.retired_cap and accepted != self.history_limit
    if preview_reverted:
      self.state = replace(self.state, acceptance=replace(self.state.acceptance, accepted=self.history_limit))
    return replace(frame, adoption_observations=proposals, confirmation_started_ns=self.advance.started_ns,
                   preview_reverted=preview_reverted)

  def _action(self, request: Action | None, now_ns: int) -> tuple[acc.DriverAction | None, acc.AdoptRequest | None, int | None]:
    if request is None or request.session_id != self.session_id or request.kind not in ("accept", "reject", "adopt"):
      return None, None, None
    if request.sequence_id <= self.last_ui_sequence:
      return None, None, None
    self.last_ui_sequence = request.sequence_id
    shown = self.state.acceptance.presentation
    if shown is None and request.kind == 'adopt':
      shown = self.current_presentation
    if shown is None or shown.presentation_id != request.presentation_id:
      return None, None, None
    action_id = self.ledger.last_action_id + 1
    context = self.state.override.context if self.set_speed_rule and self.state.override is not None else None
    context_id = context.context_id if context is not None else 'slc-presentation'
    begun = arb.step(self.ledger, arb.BeginAction(self.session_id, action_id, arb.Origin.DRIVER_OTHER, context_id),
                     now_ns=now_ns)
    self.ledger = begun.state
    if begun.errors:
      return None, None, None
    if request.kind == "adopt":
      assert shown is not None
      return None, acc.AdoptRequest(self.session_id, action_id, request.presentation_id, shown.candidate), action_id
    kind = acc.ActionKind.ACCEPT if request.kind == "accept" else acc.ActionKind.REJECT
    return acc.DriverAction(self.session_id, action_id, request.decision_id, kind, request.observed_ns), None, action_id

  def step(self, sm, CP, *, now_ns: int, now_boot_ns: int | None = None, request: Action | None = None,
           cruise_event: CruiseEvent | None = None, confirmation_grace_ns: int = 0, wheel_override_blocked: bool = False) -> Output:
    # SLC writes SET where it sets the set speed (cruise.slc_sets_set_speed); elsewhere it supplies a ceiling.
    self.set_speed_rule = slc_sets_set_speed(CP, self.settings.custom.sets_set_speed)
    old_pending = self.state.acceptance.pending
    if self.command_status == 'issued' and now_ns > self.command_expiry_ns:
      self.command_status = 'expired'
    classified = self._cruise(cruise_event, sm, now_ns) if request is None else None
    if cruise_event is not None and request is None and self.set_speed_rule:
      self._command_receipt(cruise_event, sm, now_ns)
    action, adopt, action_id = self._action(request, now_ns)
    if cruise_event is not None and request is None:
      action, adopt, action_id = self._physical_action(cruise_event, sm, now_ns)
    if (adopt is not None and self._maps_selected and self.advance.encounter is not None and self.last_received is not None and
        (request is None or self.state.acceptance.pending is None)):
      # Explicit adoption uses the current road, including SET over an old early prompt.
      shown = acc.Presentation(adopt.presentation_id, self.last_received)
      adopt = replace(adopt, candidate=shown.candidate)
      self.state = replace(self.state, acceptance=replace(self.state.acceptance, presentation=shown, pending=None, timer_running=False,
        observation=acc.Observation(acc.ObservationKind.VALID, shown.candidate)))
    wheel_since = 0
    if (classified is not None and cruise_event is not None and cruise_event.button == 'accel' and
        not cruise_event.long_press and cruise_event.selected_mps > cruise_event.previous_mps and
        abs(float(sm['carState'].vCruise) / 3.6 - cruise_event.selected_mps) <= 0.0001 and
        0 < cruise_event.wheel_override_since_ns <= cruise_event.observed_ns and cruise_event.session_id == self.session_id and
        _fresh(sm, 'slcDashboardObservation', now_ns) and
        cruise_event.producer_session == str(sm['slcDashboardObservation'].producerSessionId)):
      wheel_since = cruise_event.wheel_override_since_ns
    frame = self._frame(sm, CP, now_ns, now_boot_ns, action, adopt, classified, wheel_override_blocked=wheel_override_blocked)
    frame = replace(frame, confirmation_grace_ns=confirmation_grace_ns, wheel_override_since_ns=wheel_since)
    if self.set_speed_rule:
      # SLC sets the set speed: automatic adoption waits 3 s and a new source settles for 2 s.
      frame = replace(frame, set_speed_rule=True, set_speed_ready=self._set_speed_ready(sm),
                      acceptance_policy=replace(frame.acceptance_policy, automatic_adoption_ns=acc.AUTOMATIC_ADOPTION_NS,
                                                source_switch_ns=acc.SOURCE_SWITCH_NS))
    selected = sel.select_limit(frame.observations, frame.selection_policy)
    if selected.selected_source is sel.Source.MAP and self.mapped.current:
      frame = replace(frame, acceptance_policy=replace(frame.acceptance_policy, automatic_adoption_ns=0))
    accepted_before = self.state.acceptance.accepted
    primary, secondary = frame.selection_policy.slots[:2]
    fallback_ns = int(self.settings.custom.fallback_delay * 1e9)
    if (frame.selection_policy.mode is sel.SelectionMode.ORDERED and primary is not None and
        secondary is not None and secondary is not primary and selected.selected_source is secondary and
        accepted_before is not None and accepted_before.candidate.source == primary.value and
        selected.observation.candidate is not None and
        abs(selected.observation.candidate.speed_mps-accepted_before.candidate.speed_mps) >= .1):
      # Fallback delay: the last primary limit is held while the secondary shows a different one for this long,
      # as the source-switch settle; once it has held that long it is adopted (or offered, with confirmation)
      # straight away. A returning primary restarts the wait.
      frame = replace(frame, acceptance_policy=replace(frame.acceptance_policy, automatic_adoption_ns=0,
                                                       source_switch_ns=fallback_ns))
    raw_selection = sel.select_limit(self.current_observations, frame.selection_policy)
    raw = raw_selection.observation.candidate
    producer = None
    if raw is not None:
      producer = (self.mapped.source_key if raw_selection.selected_source is sel.Source.MAP else
                  str(sm['slcDashboardObservation'].producerSessionId) if raw_selection.selected_source is sel.Source.DASHBOARD else
                  str(sm['slcVisionObservation'].vision.producerSessionId) if raw_selection.selected_source is sel.Source.VISION else '')
    zone_source, zone_producer, zone_limit = (raw.source if raw is not None else None,
                                             producer, raw.speed_mps if raw is not None else None)
    if raw_selection.selected_source is sel.Source.MAP and raw is not None:
      self._zone_map_seen_ns = now_ns
    elif (frame.selection_policy.mode is sel.SelectionMode.ORDERED and
          frame.selection_policy.slots[0] is sel.Source.MAP and
          accepted_before is not None and accepted_before.candidate.source == sel.Source.MAP.value and
          self.current_zone.limit is not None and
          0 <= now_ns-getattr(self, '_zone_map_seen_ns', -fallback_ns-1) < max(fallback_ns, 1)):
      # Preserve driver memory during the already agreed map-loss grace period.
      # A pending dashboard candidate has not established a new current zone.
      # This retains no source authority and cannot itself issue a command.
      zone_source, zone_producer, zone_limit = ('map', self.current_zone.producers.get('map'), self.current_zone.limit)
    zone = self.current_zone.observe(zone_source, zone_producer, zone_limit)
    fresh = all(_fresh(sm, name, now_ns) for name in ('carState','selfdriveState','carControl'))
    engaged = bool(frame.host.selfdrive_enabled and sm['carControl'].enabled and CP.openpilotLongitudinalControl)
    manual = classified.change.selected_mps if classified is not None and classified.change is not None else None
    # Remember manual speed (this drive): per posted limit until the drive ends. Recall on engaging also needs
    # Restore SLC speed on re-engagement.
    memory_enabled = bool(self.settings.enabled and self.settings.custom.manual_memory == 'drive')
    recall_on_engage = bool(memory_enabled and self.settings.custom.reengage)
    if not recall_on_engage:
      self.memory_recall_pending = False
    if fresh:
      if recall_on_engage and engaged and not self.memory_was_engaged:
        self.memory_recall_pending = True
      if not engaged:
        self.memory_recall_pending = False
      self.memory_was_engaged = engaged
    if manual is not None or action is not None or adopt is not None:
      self.memory_recall_pending = False
    # Count actual current limits, including short zones suppressed only for
    # target planning. Lookahead and fallback grace cannot age or reset memory.
    memory_observations = dict(self.current_observations)
    if self.raw_map_observation is not None:
      memory_observations[sel.Source.MAP] = self.raw_map_observation
    memory_current = sel.select_limit(memory_observations, frame.selection_policy).observation.candidate
    self.manual_targets.observe(now_ns, memory_current.speed_mps if memory_current is not None else None,
                                enabled=memory_enabled, fresh=fresh)
    if action is not None or adopt is not None:
      self.manual_targets.forget()
      if accepted_before is not None:
        self.manual_targets.forget(accepted_before.candidate.speed_mps)
    frame = replace(frame, per_limit_memory=memory_enabled)
    self.engagement.observe(now_ns=now_ns, zone=zone, enabled=engaged, fresh=fresh,
      selected=frame.host.driver_v_cruise_kph/3.6, manual=manual,
      remember=memory_enabled, match=self.settings.enabled and self.settings.custom.reengage,
      explicit=action is not None or adopt is not None)
    map_observation = self.current_observations.get(sel.Source.MAP)
    map_current = (map_observation.candidate if map_observation is not None and
                   map_observation.kind is acc.ObservationKind.VALID else None)
    clock_pair = clock_pair_ns() if now_boot_ns is None else (now_ns, now_boot_ns)
    map_frame = self.map_source.tracker.diagnostic(now_boot_ns=clock_pair[1]).frame if clock_pair is not None else None
    map_fresh = (map_frame is not None and map_current is not None and
                 0 <= clock_pair[1]-map_frame.event_mono_ns <= 250_000_000 and
                 self.mapped.current)
    self.rollback.observe(now_ns=now_ns, enabled=engaged and self.settings.enabled,
      fresh=fresh, selected=frame.host.driver_v_cruise_kph/3.6, manual=manual,
      explicit=action is not None or adopt is not None, current=map_current,
      map_selected=selected.selected_source is sel.Source.MAP, map_fresh=map_fresh,
      preview=self.mapped.approach.active, preview_identity=self.mapped.identity,
      upcoming=self.mapped.upcoming, accepted=self.state.acceptance.accepted,
      previous_override=self.state.override.persistent_selected_mps)
    if self.rollback.ready and (self.state.acceptance.accepted is None or
                                self.state.acceptance.accepted.candidate != map_current):
      # Restore only qualified acceptance, preserving current ledger and monotonic
      # decision/presentation counters. The current way supplies fresh identity.
      prior = replace(self.state.acceptance, observation=acc.Observation(acc.ObservationKind.UNKNOWN),
        accepted=acc.AcceptedLimit(map_current, self.session_id, now_ns),
        pending=None, presentation=None, timer_running=False, received_speed_mps=map_current.speed_mps)
      self.state = replace(self.state, acceptance=prior)
    result = comp.step(self.state, frame)
    self.state = result.state
    accepted_now = self.state.acceptance.accepted
    if manual is not None and fresh and accepted_now is not None:
      # File a set-speed change under the limit the driver sees: the accepted limit, which leads the map's current
      # limit while a lower limit is reached early and stays through a map dropout. No accepted limit, nothing kept.
      self.manual_targets.remember(manual, accepted_now.candidate.speed_mps)
    accepted_action = result.acceptance.action_receipt if result.acceptance is not None else None
    adopted_action = result.acceptance.adoption_receipt if result.acceptance is not None else None
    if not result.errors and ((accepted_action is not None and accepted_action.consumed and
                              action is not None and action.kind is acc.ActionKind.ACCEPT) or
                             (adopted_action is not None and adopted_action.consumed)):
      self.rollback.checkpoint = None
      self.rollback.ready = False
    self.advance.observe(result)
    if not result.errors and result.acceptance is not None:
      accepted = self.state.acceptance.accepted
      encounter = self.advance.encounter
      provisional = encounter is not None and not encounter.crossed and accepted == encounter.applied_preview
      if result.acceptance.history_write is not None and not provisional:
        self.history_limit = accepted
      elif accepted is not None and accepted != self.history_limit and result.selection is not None:
        current = sel.select_limit(frame.observations, self.settings.selection).observation.candidate
        if current is not None and current == result.selection.observation.candidate and current.speed_mps == accepted.candidate.speed_mps:
          self.history_limit = accepted
    if self.state.acceptance.presentation is None and self.advance.encounter is not None:
      current = sel.select_limit(frame.observations, self.settings.selection).observation.candidate
      if current is not None and (self.current_presentation is None or self.current_presentation.candidate != current):
        identity = self.state.acceptance.next_presentation_id
        self.current_presentation = acc.Presentation(identity, current)
        self.state = replace(self.state, acceptance=replace(self.state.acceptance, next_presentation_id=identity+1))
    action_result = None
    if action_id is not None:
      if result.acceptance is not None:
        action_result = arb.resolve_acceptance(self.ledger, action_id, result.acceptance, now_ns=now_ns)
        self.ledger = action_result.state
      completed = arb.step(self.ledger, arb.CompleteAction(self.session_id, action_id), now_ns=now_ns)
      self.ledger = completed.state
    commanded_before = self.commanded_acceptance
    command = self._command(result, sm, now_ns, old_pending, action, adopt) if self.set_speed_rule else None
    result = replace(result, state=self.state)
    # Ceiling model (SLC does not write SET): the acceleration and braking limits follow the speed the ceiling drives to,
    # the lower of the driver's SET and the accepted limit + offset, from the frame the ceiling is published.
    context = result.domain.context if result.domain is not None else None
    ceiling_target = (min(float(sm['carState'].vCruise)/3.6, context.effective_cluster_mps)
                      if not self.set_speed_rule and context is not None and result.ceiling is not None else None)
    self.comfort_request = self.comfort.step(result, frame, settings=self.settings.custom, limits=self.comfort_limits,
      ceiling=ceiling_target is not None,
      selected=ceiling_target if ceiling_target is not None else float(sm['carState'].vCruise)/3.6, speed=float(sm['carState'].vEgoCluster),
      acknowledged=self.command_status == 'applied' and self.commanded_acceptance == result.state.acceptance.accepted,
      selected_acknowledged=self.command_status == 'applied' and not self.command_interrupted and
                            abs(float(sm['carState'].vCruise)/3.6-self.command_target_mps)<=.15,
      manual=classified is not None, reengage=self.command_kind in ('reengageZone', 'previewRollback'))
    self.easing_request = accepted_easing(result, frame, selected=float(sm['carState'].vCruise)/3.6,
      acknowledged=self.command_status == 'applied' and self.commanded_acceptance == result.state.acceptance.accepted and
                   self.command_kind != 'previewRollback', manual=classified is not None, approach=self.command_mapped_approach)
    map_selected = result.selection is not None and result.selection.selected_source is sel.Source.MAP
    upcoming = self.mapped.upcoming
    # Confirmation direction follows posted limits, as in the acceptance owner.
    # The selected speed includes offsets and can lie above an upcoming higher limit.
    confirm = (map_current is None or
               (self.settings.acceptance.confirm_lower if upcoming < map_current.speed_mps else
                self.settings.acceptance.confirm_higher))
    adjusted = sd.adjusted_limit(upcoming, self.settings.offsets) if upcoming >= 1 else None
    self.mapped.warn(now_ns, eligible=bool(map_selected and frame.authority.can_control and not confirm and
                     result.state.override.persistent_selected_mps is None and not sm['carState'].gasPressed and not frame.host.force_decel),
      target_change=adjusted is not None and abs(adjusted-float(sm['carState'].vCruise)/3.6)>.1,
      speed=float(sm['carState'].vEgo), settings=self.settings)
    accepted = result.state.acceptance.accepted
    accepted_speed = accepted.candidate.speed_mps if accepted is not None else None
    announced = (map_selected and accepted_speed is not None and
                 abs(accepted_speed-self.mapped.upcoming) < .1 and
                 self.mapped.warning_identity in self.mapped.warning.announced)
    sudden = self.accepted_warning.update(now_ns/1e9, accepted_speed,
      eligible=bool(self.settings.enabled and engaged and fresh and frame.authority.can_control),
      announced=announced)
    self.warning_event = sudden or self.mapped.warning_event
    message = self._message(now_ns, result, sm, frame)
    if action_result is not None and action_result.status == 'resolved':
      self._ui_receipt = (request.sequence_id, str(message.slcState.actionStatus)) if request is not None else None
      state = message.slcState
      if (request is not None and request.kind == 'adopt' and state.actionStatus == 'adopt' and
          0 < request.wheel_observed_ns <= (request.observed_ns or 0) <= now_ns <= request.wheel_observed_ns + WHEEL_ACTION_MAX_AGE_NS and
          frame.authority.can_control and not frame.host.force_decel and state.hasAcceptedAdjustedLimit and not state.acceptedIsPreview):
        state.wheelAdoptMonoTime = request.wheel_observed_ns
        state.wheelAdoptTargetMps = state.acceptedAdjustedLimit
    receipt = getattr(self, '_ui_receipt', None)
    if receipt is not None:
      message.slcState.actionSequenceId, message.slcState.actionStatus = receipt
    if command is not None and (request is not None or commanded_before != self.commanded_acceptance):
      self._command_ui_sequence = request.sequence_id if request is not None else 0
    message.slcState.commandActionSequenceId = getattr(self, '_command_ui_sequence', 0)
    return Output(result, message, action_result, command, self.settings)

  def _command(self, result: comp.Result, sm, now_ns: int, old_pending,
               action: acc.DriverAction | None, adopt: acc.AdoptRequest | None):
    self.reengage_target = None
    self.rollback_target = self.rollback.target
    if self.rollback.blocks_commands:
      return None
    decision = result.acceptance
    if (decision is not None and decision.state.pending is not None and
        self.commanded_acceptance == decision.state.accepted and self.command_status in ('issued', 'expired', 'rejected')):
      # Retaining the old accepted limit preserves driver memory, but must not
      # revive a command interrupted by another candidate. Explicit adoption
      # or a genuinely new acceptance can establish a new command.
      self.command_interrupted = True
    if (decision is None or decision.basis not in ('accepted', 'driver_accept', 'driver_adopt') or
        decision.state.accepted is None or decision.state.pending is not None or decision.authority is None or
        not decision.authority.can_control or result.coordinate is None or
        result.domain is None or result.domain.context is None or result.selection is None or
        result.selection.observation.candidate is None or
        result.selection.observation.candidate.speed_mps != decision.state.accepted.candidate.speed_mps or
        (result.state.override.persistent_selected_mps is not None and not self.engagement.pending and
         not self.memory_recall_pending and self.rollback_target is None)):
      return None
    target = result.domain.context.effective_cluster_mps
    resume = self.engagement.resolve(limit_target=target, provisional=self.mapped.approach.active,
                                     now_ns=now_ns, keep_faster=self.settings.custom.keep_faster)
    recalled = self.manual_targets.target(decision.state.accepted.candidate.speed_mps, now_ns)
    new_limit = (self.commanded_acceptance is None or
                 abs(self.commanded_acceptance.candidate.speed_mps-decision.state.accepted.candidate.speed_mps)>=.1)
    if recalled is not None and (new_limit or self.engagement.pending or self.memory_recall_pending) and action is None and adopt is None:
      resume = recalled
    # Re-engagement may precede source recovery. The cache supplies intent only;
    # all current acceptance/authority checks above must pass before consuming it.
    self.memory_recall_pending = False
    self.reengage_target = resume
    kind = 'reengageZone' if resume is not None else 'adoptAcceptedLimit'
    if resume is not None:
      target = resume
    if self.rollback_target is not None:
      target = self.rollback_target
      kind = 'previewRollback'
      self.reengage_target = None
    if self.settings.car_cap:
      # The car's own set speed is the ceiling: aim for the lower of the two; raise again when the car's rises.
      car_mps = float(sm['carState'].cruiseState.speed)
      if 0 < car_mps < target:
        target = car_mps
    selected_kph = float(sm['carState'].vCruise)
    if (not _number(target, positive=True) or not _number(selected_kph, positive=True) or
        selected_kph >= 255 or not V_CRUISE_MIN / 3.6 <= target <= V_CRUISE_MAX / 3.6):
      return None
    if ((resume is not None or self.rollback_target is not None) and self.command_status == 'issued' and self.command_kind == kind and
        self.commanded_acceptance == decision.state.accepted and abs(target-self.command_target_mps)<.001 and
        abs(selected_kph/3.6-self.command_expected_mps)<.1):
      return None
    if sd.set_speed_matches(target, selected_kph, float(sm['carState'].vCruiseCluster), self.is_metric):
      if not self._set_speed_ready(sm):
        return None
      self.commanded_acceptance = decision.state.accepted
      self.command_status = 'applied'
      if resume is not None:
        self.engagement.applied()
        self.state = replace(self.state, override=replace(self.state.override, persistent_selected_mps=selected_kph/3.6))
        if not self.mapped.approach.active:
          self.manual_targets.remember(selected_kph/3.6, decision.state.accepted.candidate.speed_mps)
      self.state = replace(self.state, override=replace(self.state.override, applied_selected_mps=selected_kph / 3.6))
      return None
    # Under a car ceiling the target moves with the car's set speed; follow it (at most once a second).
    retarget = bool(self.settings.car_cap and self.command_status in ('applied', 'expired', 'rejected') and
                    abs(target - self.command_target_mps) > 0.1 and now_ns - self.command_issued_ns >= 1_000_000_000)
    if (self.commanded_acceptance == decision.state.accepted and decision.adoption is None and resume is None and
        self.rollback_target is None and not retarget):
      if self.command_interrupted or self.command_status in ('issued', 'applied') or abs(selected_kph / 3.6 - self.command_expected_mps) > 0.1:
        return None
    evidence = sm['slcDashboardObservation']
    expiry = now_ns + int(2e9 / SERVICE_LIST['slcState'].frequency)
    if result.selection.selected_source is sel.Source.DASHBOARD:
      expiry = min(expiry, int(evidence.validUntilMonoTime))
    elif result.selection.selected_source is sel.Source.VISION:
      evidence = sm['slcVisionObservation'].vision
      expiry = min(expiry, int(evidence.validUntilMonoTime))
    if expiry <= now_ns:
      return None
    self.commanded_acceptance = decision.state.accepted
    self.last_command_id += 1
    self.command_status = 'issued'
    self.command_interrupted = False
    self.command_kind = kind
    self.command_mapped_approach = bool(self.mapped.approach.active and result.selection.selected_source is sel.Source.MAP)
    self.command_issued_ns = now_ns
    self.command_expiry_ns = expiry
    self.set_speed_after_ns = max(self.set_speed_after_ns, expiry)
    self.command_expected_mps = selected_kph / 3.6
    self.command_target_mps = target
    self.command_card_producer = str(sm['slcDashboardObservation'].producerSessionId)
    self.rollback.issued(command_id=self.last_command_id, target=target, kind=kind)
    command = messaging.new_message('slcCruiseCommand')
    command.valid = True
    command.logMonoTime = now_ns
    record = command.slcCruiseCommand
    record.kind = kind
    record.source = result.selection.selected_source.value
    record.upcomingBoundaryId = self.advance.boundary_id
    record.sessionId = self.session_id
    record.commandId = self.last_command_id
    record.actionId = action.sequence_id if action is not None else adopt.sequence_id if adopt is not None else 0
    record.decisionId = old_pending.decision_id if old_pending is not None else 0
    record.presentationId = self.state.acceptance.presentation.presentation_id if self.state.acceptance.presentation is not None else 0
    if result.selection.selected_source in (sel.Source.DASHBOARD, sel.Source.VISION):
      record.sourceProducerSessionId = str(evidence.producerSessionId)
      record.sourceEpisode = int(evidence.episode)
      record.sourceObservedMonoTime = int(evidence.observedMonoTime)
      record.sourceValidUntilMonoTime = int(evidence.validUntilMonoTime)
    record.issuedMonoTime = now_ns
    record.expiresMonoTime = expiry
    record.expectedSelectedMps = selected_kph / 3.6
    record.targetMps = target
    return command

  def _message(self, now_ns: int, result: comp.Result, sm, frame: comp.Frame):
    message = messaging.new_message("slcState")
    message.logMonoTime = now_ns
    message.valid = not result.errors
    state = message.slcState
    current_selection = sel.select_limit(frame.observations, self.settings.selection)
    state.sessionId = self.session_id
    # Display metadata is copied from the same qualified inputs used by selection.
    enabled_sources = (set(self.settings.selection.slots) if self.settings.selection.mode == sel.SelectionMode.ORDERED
                       else set(sel.PRIMARY_ORDER if sel.Source.VISION in self.settings.selection.slots else sel.PRIMARY_ORDER[:2]))
    if self.settings.selection.online_fallback:
      enabled_sources.add(sel.Source.ONLINE)
    rows = state.init('sourceReadings', len(sel.Source))
    for row, source in zip(rows, sel.Source, strict=True):
      observation = (self.raw_map_observation if source is sel.Source.MAP and (self.short_zones.active or self.preview_hold.active) else
                     frame.observations.get(source))
      row.source = source.value
      row.enabled = bool(self.settings.display and source in enabled_sources)
      row.observationKind = observation.kind.value if observation is not None else 'unknown'
      if observation is not None and observation.kind == acc.ObservationKind.VALID and observation.candidate is not None:
        row.speedLimit = observation.candidate.speed_mps
    state.frameMonoTime = now_ns
    state.enabled = self.settings.enabled
    state.displayOnly = self.settings.display and not self.settings.enabled
    state.observationKind = current_selection.observation.kind.value
    state.source = current_selection.selected_source.value if current_selection.selected_source is not None else 'none'
    if current_selection.observation.candidate is not None:
      state.speedLimit = current_selection.observation.candidate.speed_mps
      self.last_received = current_selection.observation.candidate
    if self.advance.boundary_id and result.selection is not None and result.selection.observation.candidate is not None:
      state.upcomingBoundaryId = self.advance.boundary_id
      state.upcomingSpeedLimit = result.selection.observation.candidate.speed_mps
    accepted = result.state.acceptance.accepted
    if accepted is not None:
      state.hasAccepted = True
      state.acceptedMonoTime = accepted.accepted_at_ns
      state.acceptedSpeedLimit = accepted.candidate.speed_mps
      state.acceptedSource = accepted.candidate.source
      state.acceptedIsPreview = accepted != self.history_limit and self.advance.encounter is not None and accepted == self.advance.encounter.applied_preview
    if result.domain is not None and result.domain.context is not None:
      state.offset = result.domain.context.offset_mps
    # Publish the qualified domain coordinate; a raw cap is not a cluster value.
    context = result.domain.context if result.domain is not None else None
    if context is not None:
      state.hasEffectiveClusterTarget = True
      state.effectiveClusterTarget = result.coordinate.cluster_target_mps if result.coordinate is not None else context.effective_cluster_mps
    if result.ceiling is not None:
      state.hasCeiling = True
      state.effectiveCap = result.ceiling.speed_mps
      state.isLimitingMaxSet = result.ceiling.speed_mps < frame.host.driver_v_cruise_kph / 3.6
    elif self.set_speed_rule and context is not None and result.state.acceptance.accepted is not None:
      # SLC sets the set speed (display only): the limit is in use while SET equals the accepted limit plus offset, in
      # the units card writes. No cap is published (hasCeiling stays False).
      state.isLimitingMaxSet = sd.set_speed_matches(context.effective_cluster_mps, frame.host.driver_v_cruise_kph,
                                                    frame.host.selected_cluster_kph, self.is_metric)
    pending = result.state.acceptance.pending
    # Only an already-captured response can finish an expired presentation.
    if pending is not None and not pending.automatic and pending.active_elapsed_ns >= acc.CONFIRMATION_NS:
      pending = None
    if pending is not None:
      state.hasPending = True
      state.pendingSpeedLimit = pending.candidate.speed_mps
      state.pendingSource = pending.candidate.source
      state.decisionId = pending.decision_id
      state.automaticAdoptionPending = pending.automatic
      state.pendingSinceMonoTime = now_ns - pending.active_elapsed_ns
    wheel_active = result.coordinate is not None and result.coordinate.basis == 'wheel'
    state.wheelOverrideSince = result.state.wheel_override_since_ns if not result.errors else 0
    if self.set_speed_rule and result.state.override is not None:
      # SLC sets the set speed: an override is a remembered manual set speed; the accelerator never makes one.
      state.driverOverrideActive = result.state.override.persistent_selected_mps is not None
      state.overrideBasis = 'persistent' if state.driverOverrideActive else 'none'
      state.retainedOverride = state.driverOverrideActive
    else:
      state.driverOverrideActive = wheel_active or result.ceiling is not None and result.state.pedal_target_mps > 0.
      state.overrideBasis = 'wheel' if wheel_active else (
        ('pedal' if frame.pedal_pressed else 'persistent') if state.driverOverrideActive else 'none')
    shown = result.state.acceptance.presentation
    ego = frame.ego_pair
    ego_delta = (max(0.0, ego.cluster_mps - ego.raw_mps)
                 if ego.status is sd.DomainStatus.VALID and ego.cluster_mps is not None and ego.raw_mps is not None else 0.0)
    if self.last_received is not None:
      state.lastReceivedSpeedLimit = self.last_received.speed_mps
      state.lastReceivedSource = self.last_received.source
      state.lastReceivedAdjustedLimit = sd.adjusted_limit(self.last_received.speed_mps, self.settings.offsets, minimum_target_mps=ego_delta) or 0.0
    for candidate, value_field, flag_field in (
      (accepted.candidate if accepted is not None else None, 'acceptedAdjustedLimit', 'hasAcceptedAdjustedLimit'),
      (pending.candidate if pending is not None else None, 'pendingAdjustedLimit', 'hasPendingAdjustedLimit'),
      (current_selection.observation.candidate, 'presentationAdjustedLimit', 'hasPresentationAdjustedLimit'),
    ):
      value = (sd.adjusted_limit(candidate.speed_mps, self.settings.offsets, minimum_target_mps=ego_delta)
               if candidate is not None else None)
      if value is not None:
        setattr(state, value_field, value)
        setattr(state, flag_field, True)
    if state.hasPresentationAdjustedLimit and not self.advance.boundary_id:
      state.offset = float(state.presentationAdjustedLimit) - float(state.speedLimit)
    # A manual choice keeps its presentation through source gaps.
    if shown is not None and (pending is None or pending.candidate == shown.candidate):
      state.presentationId = shown.presentation_id
    elif pending is None and self.current_presentation is not None and self.current_presentation.candidate == current_selection.observation.candidate:
      state.presentationId = self.current_presentation.presentation_id
    warning = self.warning_event
    state.changeWarningId = warning['id'] if warning else ''
    state.changeWarningIssuedMonoTime = int(warning['issued']*1e9) if warning else 0
    state.changeWarningDeadlineMonoTime = int(warning['deadline']*1e9) if warning else 0
    state.warningPinned = bool(self.mapped.warning_pin.get('warningPinned', False))
    state.warningPinId = self.mapped.warning_pin.get('warningPinId', '')
    state.warningPinIssuedMonoTime = int(self.mapped.warning_pin.get('warningPinIssued', 0)*1e9)
    state.mapApproachActive = self.mapped.approach.active and state.source == 'map'
    state.mapBoundaryDistance = self.mapped.remaining if self.mapped.current else 0.
    state.mapTriggerDistance = self.mapped.trigger if self.mapped.current else 0.
    state.status = result.status
    if state.source == 'dashboard' and state.observationKind == 'valid':
      evidence = sm['slcDashboardObservation']
      state.sourceProducerSessionId = str(evidence.producerSessionId)
      state.sourceEpisode = int(evidence.episode)
      state.sourceObservedMonoTime = int(evidence.observedMonoTime)
      state.sourceValidUntilMonoTime = int(evidence.validUntilMonoTime)
    elif state.source == 'vision' and state.observationKind == 'valid' and (self.vision_control_qualified or self.vision_display_qualified):
      evidence = sm['slcVisionObservation'].vision
      state.sourceProducerSessionId = str(evidence.producerSessionId)
      state.sourceEpisode = int(evidence.episode)
      state.sourceObservedMonoTime = int(evidence.observedMonoTime)
      state.sourceValidUntilMonoTime = int(evidence.validUntilMonoTime)
    # The intended re-engage or rollback target is published with the command it belongs to.
    state.hasReengageTarget = self.reengage_target is not None and (self.command_kind == 'reengageZone')
    state.reengageTarget = self.reengage_target or 0.
    state.hasRollbackTarget = self.rollback_target is not None and (self.command_kind == 'previewRollback')
    state.rollbackTarget = self.rollback_target or 0.
    state.commandId = self.last_command_id
    state.commandStatus = self.command_status
    receipt = result.acceptance.action_receipt or result.acceptance.adoption_receipt if result.acceptance else None
    if receipt is not None:
      state.actionSequenceId = receipt.sequence_id
      state.actionStatus = receipt.status
    return message
