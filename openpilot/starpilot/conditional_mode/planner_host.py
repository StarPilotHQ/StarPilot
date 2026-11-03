"""Conditional proposal join and bounded saved-manual owner after native MPC."""

from dataclasses import replace
import math
import re

from openpilot.starpilot.conditional_mode.host import ConditionalModeHost, HostProposal
from openpilot.starpilot.conditional_mode.manual import Button, Gesture, ManualSession, Press, ioniq6_media_eligible, read_button_map
from openpilot.starpilot.conditional_mode.manual_saved import ManualSavedOwner
from openpilot.starpilot.conditional_mode.policy import ManualIntent, ModeChoice
from openpilot.starpilot.conditional_mode.preferences import ManualState, manual_for_drive
from openpilot.starpilot.conditional_mode.projection import ConditionalOwnerContext, ObservedBool
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner, SettingsSnapshot
from openpilot.starpilot.conditional_mode.status import StatusPublisher, settings_fingerprint
from openpilot.starpilot.conditional_mode.ui_action import current_authority, exact_settings, observation as ui_observation


# Disabled scene owners are false; enabled owners without fresh observations are unknown.
# Enable an owner only after its producer is connected.
LEGACY_SCENE_OWNERS = frozenset((
  'traffic_mode', 'stop_sign_confirmed', 'forcing_stop',
  'dashboard_stop_sign', 'committed_turn_scene', 'red_light', 'plan_forcing_stop',
))


def selected_follow_time(planner) -> float | None:
  """Read the time the native MPC actually selected in this model cycle."""
  try:
    value = float(planner.mpc.params[0, 4])
  except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
    return None
  return value if math.isfinite(value) and 0.5 <= value <= 3.0 else None


class ConditionalPlannerHost:
  def __init__(self, params, *, enabled_scene_owners: frozenset[str] = frozenset()):
    if not enabled_scene_owners <= LEGACY_SCENE_OWNERS:
      raise ValueError('unknown conditional scene owner')
    self.params = params
    self.enabled_scene_owners = enabled_scene_owners
    self.settings = ConditionalSettingsOwner(params)
    self.mode = ConditionalModeHost()
    self.status = StatusPublisher()
    self.manual = ManualSession()
    self.manual_saved = ManualSavedOwner(params, self.settings)
    self.manual_state: ManualState | None = None
    self.manual_live_code = 0
    self.pending_manual_state: ManualState | None = None
    self.pending_manual_revision = 0
    self.queued_manual_states: dict[int, tuple[ManualState, int]] = {}
    self.last_saved_manual: tuple[ManualState, int] | None = None
    self.manual_key: tuple | None = None
    self.card_session: str | None = None
    self.card_last_sequence = 0
    self.retired_card_sessions: tuple[str, ...] = ()
    self.ui_session: str | None = None
    self.ui_last_sequence = 0
    self.retired_ui_sessions: tuple[str, ...] = ()

  def reset(self) -> None:
    self.mode.reset()
    self.manual_saved.close()
    self.manual_saved = ManualSavedOwner(self.params, self.settings)
    self.manual = ManualSession()
    self.manual_state = None
    self.manual_live_code = 0
    self.pending_manual_state = None
    self.pending_manual_revision = 0
    self.queued_manual_states.clear()
    self.last_saved_manual = None
    self.manual_key = None
    self.card_session = None
    self.card_last_sequence = 0

  def close(self) -> None:
    self.manual_saved.close()

  @staticmethod
  def _fresh_service(sm, name: str, drive_id: int, now_ns: int) -> bool:
    try:
      producer = sm.logMonoTime[name]
      receipt = sm.recv_time[name]
      if type(producer) is not int or type(receipt) not in (int, float) or not math.isfinite(receipt):
        return False
      receipt_ns = int(receipt * 1e9)
      return bool(sm.seen[name] and sm.alive[name] and sm.valid[name] and
                  drive_id < producer <= now_ns and drive_id < receipt_ns <= now_ns and
                  now_ns - producer <= 250_000_000 and now_ns - receipt_ns <= 250_000_000)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return False

  def _manual_event(self, event, snapshot: SettingsSnapshot, choice: ModeChoice, drive_id: int,
                    sm, cp, now_ns: int) -> None:
    try:
      if self.manual_saved.conflicted or event is None or not event.valid or str(event.slcCruiseEvent.kind) != 'conditionalMode':
        return
      receipt = event.slcCruiseEvent.manualMode
      session = str(receipt.sessionId)
      sequence, observed = int(receipt.sequence), int(receipt.observedMonoTime)
      source_car, expiry = int(receipt.sourceCarStateMonoTime), int(receipt.validUntilMonoTime)
      event_ns = int(event.logMonoTime)
      if (receipt.version != 1 or not re.fullmatch(r'[0-9a-f]{32}', session) or
          session in self.retired_card_sessions or sequence <= 0 or
          str(event.slcCruiseEvent.producerSessionId) != session or
          int(event.slcCruiseEvent.observedMonoTime) != observed or
          int(event.slcCruiseEvent.eventId) <= 0 or
          int(receipt.driveStartMonoTime) != drive_id or
          str(receipt.settingsFingerprint) != settings_fingerprint(snapshot) or
          str(receipt.choice) != ('conditionalExperimental' if choice is ModeChoice.CEM else 'conditionalChill') or
          not drive_id < observed <= source_car <= event_ns <= now_ns or
          source_car - observed > 100_000_000 or not source_car < expiry <= source_car + 100_000_000 or
          now_ns > expiry or source_car > sm.logMonoTime['carState'] or
          not self._fresh_service(sm, 'carState', drive_id, now_ns) or
          not self._fresh_service(sm, 'carControl', drive_id, now_ns) or
          not self._fresh_service(sm, 'selfdriveState', drive_id, now_ns) or
          not cp.openpilotLongitudinalControl or not sm['carState'].canValid or sm['carState'].canTimeout or
          not sm['carControl'].longActive or not sm['carControl'].enabled or not sm['selfdriveState'].enabled):
        return
      gesture = Gesture(Button(str(receipt.button)), Press(str(receipt.press)))
      if gesture.button in (Button.MODE, Button.CUSTOM) and not ioniq6_media_eligible(cp):
        return
      if session == self.card_session and sequence <= self.card_last_sequence:
        return
      restarted = self.card_session is not None and session != self.card_session
      if restarted:
        self.retired_card_sessions = (*self.retired_card_sessions[-7:], self.card_session)
        if self.pending_manual_state is None:
          self.manual_state = self.manual.start(choice, drive_id, drive_id,
                                                persist_manual=self.manual_saved.persist,
                                                persisted_code=self.manual_live_code)
        self.card_last_sequence = 0
      self.card_session = session
      self.card_last_sequence = sequence
      if restarted and self.pending_manual_state is not None:
        return
      buttons = read_button_map(self.params, include_ioniq_media=ioniq6_media_eligible(cp))
      if buttons is None or not buttons.assigned(gesture):
        return
      effective = sm['selfdriveState'].experimentalMode
      self._apply_manual(snapshot, drive_id, observed, effective, now_ns)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return

  def _apply_manual(self, snapshot: SettingsSnapshot, drive_id: int, observed_ns: int, effective: bool, now_ns: int) -> None:
    if self.manual_saved.persist and len(self.queued_manual_states) >= 32:
      return  # Bound a deliberately stalled writer's pending gesture history.
    # Card and UI producer sequences are independent; this ordinal is planner-local.
    changed = self.manual.apply(sequence=self.manual.last_sequence + 1, drive_id=drive_id, observed_ns=observed_ns,
                                effective_experimental=effective, assigned=True)
    if changed is not None:
      if not self.manual_saved.persist:
        self.manual_state = changed
      elif self.manual_saved.queue_code(self.manual.code, snapshot=snapshot, drive_id=drive_id, now_ns=now_ns):
        self.pending_manual_state = changed
        self.pending_manual_revision = self.manual_saved.desired_revision
        self.queued_manual_states[self.pending_manual_revision] = (changed, self.manual.code)
      else:
        self.manual.ready = False

  def _ui_event(self, event, snapshot: SettingsSnapshot, choice: ModeChoice, drive_id: int, sm, cp, now_ns: int,
                *, allow_apply: bool = True) -> None:
    value = ui_observation(event, now_ns) if event is not None else None
    if (value is None or self.manual_saved.conflicted or not self.manual.ready or
        value.session in self.retired_ui_sessions or
        value.session == self.ui_session and value.sequence <= self.ui_last_sequence or
        value.drive_id != drive_id or value.fingerprint != settings_fingerprint(snapshot) or value.choice is not choice or
        value.planner_session != self.status.session or not current_authority(sm, cp, drive_id, now_ns) or
        value.car_state_ns > sm.logMonoTime['carState'] or value.selfdrive_state_ns > sm.logMonoTime['selfdriveState'] or
        not exact_settings(self.params, snapshot)):
      return
    if self.ui_session is not None and self.ui_session != value.session:
      self.retired_ui_sessions = (*self.retired_ui_sessions[-7:], self.ui_session)
    self.ui_session, self.ui_last_sequence = value.session, value.sequence
    if (not allow_apply or self.pending_manual_state is not None or value.manual_code != self.manual.code or
        value.effective_experimental is not sm['selfdriveState'].experimentalMode):
      return
    self._apply_manual(snapshot, drive_id, value.observed_ns, value.effective_experimental, now_ns)

  def sample(self, sm, cp, planner, *, now_mono_ns: int, now_boot_ns: int, sample_skew_ns: int,
             drive_id: int, manual: ManualState | None = None,
             owner_context: ConditionalOwnerContext | None = None,
             native_plan_valid: bool = False,
             cruise_ceiling_mps: float | None = None,
             manual_event=None,
             ui_event=None,
             ) -> tuple[HostProposal, SettingsSnapshot | None]:
    context = owner_context if isinstance(owner_context, ConditionalOwnerContext) else ConditionalOwnerContext()
    # Stamp absent capabilities with the model event so polling cannot renew freshness.
    # Connected owners without evidence remain unknown.
    if self._fresh_service(sm, 'modelV2', drive_id, now_mono_ns):
      model_stamp = sm.logMonoTime['modelV2']
      disabled = {name: ObservedBool(False, model_stamp) for name in LEGACY_SCENE_OWNERS - self.enabled_scene_owners - {'committed_turn_scene'}
                  if getattr(context, name) is None}
      context = replace(context, **disabled)
    if self._fresh_service(sm, 'selfdriveState', drive_id, now_mono_ns):
      effective = getattr(sm['selfdriveState'], 'experimentalMode', None)
      if context.previous_experimental is None and type(effective) is bool:
        context = replace(context, previous_experimental=ObservedBool(effective, sm.logMonoTime['selfdriveState']))
    if self._fresh_service(sm, 'carState', drive_id, now_mono_ns):
      gas = getattr(sm['carState'], 'gasPressed', None)
      if context.pedal_override is None and type(gas) is bool:
        context = replace(context, pedal_override=ObservedBool(gas, sm.logMonoTime['carState']))
    plan_stamp = sm.logMonoTime.get('modelV2')
    plan_owned = native_plan_valid is True and type(plan_stamp) is int and 0 < plan_stamp <= now_mono_ns
    should_stop = getattr(planner, 'output_should_stop', None)
    allow_throttle = getattr(planner, 'allow_throttle', None)
    context = replace(
      context,
      plan_should_stop=ObservedBool(should_stop, plan_stamp) if plan_owned and type(should_stop) is bool else None,
      plan_allow_throttle=ObservedBool(allow_throttle, plan_stamp) if plan_owned and type(allow_throttle) is bool else None,
    )
    snapshot = self.settings.refresh(now_mono_ns)
    verdict = self.settings.verdict(snapshot, now_mono_ns=now_mono_ns, drive_id=drive_id)
    if (verdict.status == 'ready' and verdict.selection is not None and
        verdict.selection.choice in (ModeChoice.CEM, ModeChoice.CCM) and snapshot.preferences is not None):
      choice = verdict.selection.choice
      key = (drive_id, snapshot.owner_token, snapshot.revision, choice)
      if key != self.manual_key:
        self.manual_saved.close()
        self.manual_saved = ManualSavedOwner(self.params, self.settings)
        self.pending_manual_state = None
        self.pending_manual_revision = 0
        self.queued_manual_states.clear()
        self.last_saved_manual = None
        self.manual_live_code = 0
        self.manual_key = key
        options = snapshot.preferences.cem if choice is ModeChoice.CEM else snapshot.preferences.ccm
        start = self.manual_saved.begin_drive(snapshot, choice=choice, drive_id=drive_id, now_ns=now_mono_ns)
        if start.status == 'active_elsewhere':
          self.manual_key = None  # Retry after the prior drive's cancelled worker releases its lease.
        if start.intent is None:
          self.manual = ManualSession()
          self.manual_state = manual_for_drive(ManualIntent.NONE, drive_id, drive_id)
        else:
          self.manual_state = self.manual.start(choice, drive_id, drive_id,
                                                persist_manual=options.persist_manual, persisted_code=start.code)
          self.manual_live_code = start.code if start.code is not None else 0
        self.card_session = None
        self.card_last_sequence = 0
      result = self.manual_saved.poll(snapshot=snapshot, drive_id=drive_id, now_ns=now_mono_ns)
      if result is not None and result.status == 'saved':
        candidate = self.queued_manual_states.get(result.revision)
        if candidate is not None:
          self.last_saved_manual = candidate
        self.queued_manual_states = {revision: state for revision, state in self.queued_manual_states.items()
                                     if revision > result.revision}
      if (result is not None and result.status == 'saved' and self.pending_manual_state is not None and
          result.revision == self.pending_manual_revision == self.manual_saved.desired_revision):
        self.manual_state = self.pending_manual_state
        self.manual_live_code = self.manual.code
        self.pending_manual_state = None
        self.pending_manual_revision = 0
        self.last_saved_manual = None
      if self.manual_saved.conflicted:
        self.manual.ready = False
        self.pending_manual_state = None
        self.pending_manual_revision = 0
        self.queued_manual_states.clear()
        if result is not None and (result.status in ('external_edit', 'verification_failed') or result.committed):
          # External/ambiguous bytes invalidate the old saved authority.
          self.manual_state = manual_for_drive(ManualIntent.NONE, drive_id, now_mono_ns)
          self.manual_live_code = 0
        elif self.last_saved_manual is not None:
          # Earlier successfully saved intent is the last durable state if a
          # later pre-rename write fails.
          self.manual_state, self.manual_live_code = self.last_saved_manual
        self.last_saved_manual = None
      if self.manual_state is not None:
        prior_observation = self.manual.last_observed_ns
        self._manual_event(manual_event, snapshot, choice, drive_id, sm, cp, now_mono_ns)
        self._ui_event(ui_event, snapshot, choice, drive_id, sm, cp, now_mono_ns,
                       allow_apply=self.manual.last_observed_ns == prior_observation)
    else:
      if self.manual_key is not None:
        self.manual_saved.close()
        self.manual_saved = ManualSavedOwner(self.params, self.settings)
      self.manual = ManualSession()
      self.manual_state = None
      self.manual_live_code = 0
      self.pending_manual_state = None
      self.pending_manual_revision = 0
      self.queued_manual_states.clear()
      self.last_saved_manual = None
      self.manual_key = None
      self.card_session = None
      self.card_last_sequence = 0
    proposal = self.mode.sample(
      sm, cp, now_mono_ns=now_mono_ns, now_boot_ns=now_boot_ns,
      sample_skew_ns=sample_skew_ns, drive_id=drive_id,
      manual=self.manual_state if manual is None else manual, owner_context=context,
      selected_t_follow_s=selected_follow_time(planner),
      selected_t_follow_observed_mono_ns=sm.logMonoTime.get('modelV2'),
      cruise_ceiling_mps=cruise_ceiling_mps,
      settings_owner=self.settings,
    )
    snapshot = self.settings.current
    if snapshot is None or proposal.settings_revision != snapshot.revision:
      self.mode.reset()
      return HostProposal(proposal.choice, proposal.projected, None, None, 'settings_revision_changed'), snapshot
    return proposal, snapshot
