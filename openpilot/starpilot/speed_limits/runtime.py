"""One plannerd-owned SLC lifecycle over real, typed replayable observations."""

import math
import uuid
from dataclasses import dataclass, replace

import openpilot.cereal.messaging as messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.selfdrive.car.cruise import V_CRUISE_MIN, V_CRUISE_MAX
from openpilot.starpilot.aol.runtime import current_axis
from openpilot.starpilot.controllers.wheel_actions import LIFETIME_NS as WHEEL_ACTION_MAX_AGE_NS
from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import action_arbitration as arb
from openpilot.starpilot.speed_limits import composition as comp
from openpilot.starpilot.speed_limits import selection as sel
from openpilot.starpilot.speed_limits import speed_domain as sd
from openpilot.starpilot.speed_limits.runtime_settings import ADVANCE_SECONDS, Settings
from openpilot.starpilot.speed_limits.map_observation import MapSource, copy_frame
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
    self.state = comp.new_session(self.session_id, previous_limit)
    self.history_limit = self.state.acceptance.accepted
    self.ledger = arb.new_ledger(self.session_id)
    self.last_ui_sequence = -1
    self.last_cruise_event = -1
    self.last_cruise_producer: str | None = None
    self._ui_receipt = None
    self.last_received: acc.Candidate | None = None
    self._ego_delta_key: tuple[str, str] | None = None
    self._ego_delta_mps = 0.0
    self._ego_sample_ns = 0
    self.current_presentation: acc.Presentation | None = None

  def reset(self) -> None:
    previous_limit = self.history_limit
    self.map_source.reset()
    self.advance = Advance()
    self.vision_diagnostic = acc.Observation(acc.ObservationKind.UNKNOWN)
    self._last_vision_producer = ''
    self._last_vision_frame = 0
    self._last_vision_eof_boot = 0
    self._last_vision_episode = 0
    self.session_id = uuid.uuid4().hex
    try:
      self.state = comp.new_session(self.session_id, previous_limit)
    except ValueError:
      self.state = comp.new_session(self.session_id)
    self.history_limit = self.state.acceptance.accepted
    self.ledger = arb.new_ledger(self.session_id)
    self.last_ui_sequence = -1
    self.last_cruise_event = -1
    self.last_cruise_producer = None
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
    self.settings = replace(self.settings, offsets=offsets)
    # Renew actions for new offsets/units without forgetting driver choices.
    acceptance = replace(self.state.acceptance, observation=acc.Observation(acc.ObservationKind.UNKNOWN),
                         pending=None, presentation=None, timer_running=False)
    self.current_presentation = None
    self.state = replace(self.state, acceptance=acceptance, wheel_override_since_ns=0)
    self._ui_receipt = None

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
    begun = arb.step(self.ledger, arb.BeginAction(self.session_id, action_id, arb.Origin.DRIVER_CRUISE, 'driver-set'), now_ns=now_ns)
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

  @property
  def _maps_selected(self) -> bool:
    policy = self.settings.selection
    return sel.Source.MAP in policy.slots or policy.mode is not sel.SelectionMode.ORDERED

  def _observations(self, sm, CP, now_ns: int, now_boot_ns: int | None = None) -> dict[sel.Source, acc.Observation]:
    unknown = acc.Observation(acc.ObservationKind.UNKNOWN)
    values = dict.fromkeys(sel.Source, unknown)
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
      else:
        self.map_source.reset()
        values[sel.Source.MAP] = acc.Observation(acc.ObservationKind.STALE)
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
    begun = arb.step(self.ledger, arb.BeginAction(self.session_id, action_id, arb.Origin.DRIVER_OTHER, 'slc-presentation'),
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
    classified = self._cruise(cruise_event, sm, now_ns) if request is None else None
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
    result = comp.step(self.state, frame)
    self.state = result.state
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
    result = replace(result, state=self.state)
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
    return Output(result, message, action_result)

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
      observation = frame.observations.get(source)
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
    receipt = result.acceptance.action_receipt or result.acceptance.adoption_receipt if result.acceptance else None
    if receipt is not None:
      state.actionSequenceId = receipt.sequence_id
      state.actionStatus = receipt.status
    return message
