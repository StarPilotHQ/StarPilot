"""Source-independent CEM/Conditional Chill mode selection.

Only a validated adapter may construct ``SceneEvidence``. This pure policy
never reads Params, IPC, or car hardware; the runtime adapter uses it after
qualifying fresh scene and vehicle authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math


MPH_TO_MPS = 0.44704
SCENE_MAX_AGE_S = 0.25  # Provisional adapter freshness budget; not a qualified detector threshold.
CEM_HOLD_S = 0.5
CEM_EXIT_BUFFER_S = 0.25
CEM_SLOW_LEAD_HOLD_S = 1.5
CEM_OPEN_ROAD_HANDOFF_S = 0.75
CEM_STOP_RELEASE_SUPPRESS_S = 2.0
CCM_SPEED_CONFIRM_S = 0.35
CCM_LEAD_CONFIRM_S = 1.0
CCM_LAUNCH_CONFIRM_S = 0.0
CCM_MIN_DWELL_S = 1.2
CCM_EXIT_BUFFER_S = 0.35


class ModeChoice(StrEnum):
  STOCK = 'stock'
  CEM = 'conditional_experimental'
  CCM = 'conditional_chill'


class ManualIntent(StrEnum):
  NONE = 'none'
  FORCE_EXPERIMENTAL = 'force_experimental'
  FORCE_CHILL = 'force_chill'


class Reason(StrEnum):
  STOCK = 'stock'
  UNAVAILABLE = 'unavailable'
  MANUAL_EXPERIMENTAL = 'manual_experimental'
  MANUAL_CHILL = 'manual_chill'
  CEM_SPEED = 'cem_speed'
  CEM_OPEN_ROAD = 'cem_open_road'
  CEM_SIGNAL = 'cem_signal'
  CEM_CURVE = 'cem_curve'
  CEM_LEAD = 'cem_lead'
  CEM_STOP = 'cem_stop'
  CEM_SPEED_LIMIT = 'cem_speed_limit'  # Historical route labels only.
  CEM_HOLD = 'cem_hold'
  CCM_SPEED = 'ccm_speed'
  CCM_LEAD = 'ccm_lead'
  CCM_LAUNCH = 'ccm_launch'
  CCM_HOLD = 'ccm_hold'
  CCM_VETO = 'ccm_veto'
  SCENE_UNAVAILABLE = 'scene_unavailable'
  NO_TRIGGER = 'no_trigger'


@dataclass(frozen=True)
class Authority:
  """Fresh capability is separate from current lateral/longitudinal actuation."""

  fresh: bool
  system_long_capable: bool
  safe_mode: bool
  driving_enabled: bool
  long_active: bool
  lat_active: bool


@dataclass(frozen=True)
class LeadEvidence:
  present: bool
  tracked: bool
  radar: bool
  distance_m: float | None = None
  speed_mps: float | None = None
  accel_mps2: float | None = None
  model_probability: float | None = None


@dataclass(frozen=True)
class SceneEvidence:
  """Optional means unavailable, never an implicit negative observation."""

  observed_mono_s: float | None = None
  speed_mps: float | None = None
  set_speed_mps: float | None = None
  lead: LeadEvidence | None = None
  following_lead: bool | None = None
  left_blinker: bool | None = None
  right_blinker: bool | None = None
  lane_available: bool | None = None
  curve_detected: bool | None = None
  slow_lead_detected: bool | None = None
  stop_light_detected: bool | None = None
  standstill: bool | None = None
  traffic_mode: bool | None = None
  stop_sign_confirmed: bool | None = None
  forcing_stop: bool | None = None
  committed_stop: bool | None = None
  adjacent_lead_ambiguous: bool | None = None
  low_speed_stop_scene: bool | None = None
  launch_candidate: bool | None = None
  standstill_stop_hold: bool | None = None
  launch_forced_exit: bool | None = None
  launch_lead: bool | None = None


@dataclass(frozen=True)
class ModeSettings:
  """Frozen defaults converted to SI; selection and input validation are external."""

  cem_speed_mps: float = 0.0
  cem_speed_with_lead_mps: float = 0.0
  cem_signal_mps: float = 0.0
  cem_open_road: bool = False
  cem_curves: bool = False
  cem_curves_with_lead: bool = False
  cem_lead: bool = True
  cem_stop: bool = True
  ccm_speed_mps: float = 45 * MPH_TO_MPS
  ccm_speed_with_lead_mps: float = 35 * MPH_TO_MPS
  ccm_set_speed_margin_mps: float = 3 * MPH_TO_MPS
  ccm_lead: bool = True
  ccm_launch: bool = False


@dataclass(frozen=True)
class Decision:
  requested_experimental: bool
  effective_longitudinal: bool
  qualified: bool
  warm: bool
  reason: Reason
  status_code: int


def _finite(value: object, *, low: float = 0.0, high: float = 1000.0) -> bool:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return False
  try:
    return math.isfinite(value) and low <= value <= high
  except OverflowError:
    return False


def _bool(value: object) -> bool:
  return type(value) is bool


def _valid_settings(settings: ModeSettings) -> bool:
  numeric = (
    settings.cem_speed_mps,
    settings.cem_speed_with_lead_mps,
    settings.cem_signal_mps,
    settings.ccm_speed_mps,
    settings.ccm_speed_with_lead_mps,
    settings.ccm_set_speed_margin_mps,
  )
  flags = (
    settings.cem_open_road,
    settings.cem_curves,
    settings.cem_curves_with_lead,
    settings.cem_lead,
    settings.cem_stop,
    settings.ccm_lead,
    settings.ccm_launch,
  )
  return all(_finite(value, high=70.0) for value in numeric) and all(_bool(value) for value in flags)


def restore_manual_status(choice: ModeChoice, session_code: int, persisted_code: int, persist: bool) -> ManualIntent:
  """Pure frozen CEStatus/CCStatus normalization; no storage side effects."""
  if type(persist) is not bool:
    return ManualIntent.NONE
  allowed = (
    {1: ManualIntent.FORCE_CHILL, 2: ManualIntent.FORCE_EXPERIMENTAL}
    if choice is ModeChoice.CEM
    else ({1: ManualIntent.FORCE_EXPERIMENTAL, 2: ManualIntent.FORCE_CHILL} if choice is ModeChoice.CCM else {})
  )
  if type(session_code) is int and session_code in allowed:
    return allowed[session_code]
  if persist and type(persisted_code) is int:
    return allowed.get(persisted_code, ManualIntent.NONE)
  return ManualIntent.NONE


def next_manual_status(choice: ModeChoice, current_code: int, current_experimental: bool) -> int:
  """Frozen wheel-button cycle: manual -> automatic; automatic -> opposite manual mode."""
  if type(current_experimental) is not bool:
    return 0
  if choice is ModeChoice.CEM:
    return 0 if type(current_code) is int and current_code in (1, 2) else (1 if current_experimental else 2)
  if choice is ModeChoice.CCM:
    return 0 if type(current_code) is int and current_code in (1, 2) else (2 if current_experimental else 1)
  return 0


def _manual_code(choice: ModeChoice, manual: ManualIntent) -> int:
  if choice is ModeChoice.CEM:
    return {ManualIntent.FORCE_CHILL: 1, ManualIntent.FORCE_EXPERIMENTAL: 2}.get(manual, 0)
  if choice is ModeChoice.CCM:
    return {ManualIntent.FORCE_EXPERIMENTAL: 1, ManualIntent.FORCE_CHILL: 2}.get(manual, 0)
  return 0


def _scene_current(now_s: float, scene: SceneEvidence) -> bool:
  observed = scene.observed_mono_s
  speed = scene.speed_mps
  return observed is not None and speed is not None and _finite(observed, high=1e12) and 0 <= now_s - observed <= SCENE_MAX_AGE_S and _finite(speed, high=80.0)


def _lead_valid(lead: LeadEvidence | None) -> bool:
  return lead is not None and all(_bool(value) for value in (lead.present, lead.tracked, lead.radar))


def _stable_lead(scene: SceneEvidence) -> bool:
  lead = scene.lead
  speed = scene.speed_mps
  if lead is None or speed is None or not _lead_valid(lead) or not lead.present or not lead.tracked or not _finite(speed, high=80.0):
    return False
  distance = lead.distance_m
  lead_speed = lead.speed_mps
  acceleration = lead.accel_mps2
  probability = lead.model_probability
  if distance is None or lead_speed is None or acceleration is None:
    return False
  if not all(_finite(value, high=500.0) for value in (distance, lead_speed)):
    return False
  if not _finite(acceleration, low=-20.0, high=20.0):
    return False
  if not lead.radar and not _finite(probability, high=1.0):
    return False
  confident = lead.radar or probability is not None and probability >= 0.9
  max_distance = min(90.0, max(35.0, speed * 4.5))
  max_closing_speed = max(1.25, 0.05 * speed)
  return bool(
    confident and distance < max_distance and lead_speed > 1.5 and max(0.0, -acceleration) <= 0.2 and max(0.0, speed - lead_speed) <= max_closing_speed
  )


def _credible_slow_lead(scene: SceneEvidence) -> bool:
  lead, speed = scene.lead, scene.speed_mps
  if lead is None or speed is None or not _lead_valid(lead) or not lead.present or not _finite(speed, high=80.0):
    return False
  if not lead.radar and not (_finite(lead.model_probability, high=1.0) and lead.model_probability is not None and lead.model_probability >= 0.85):
    return False
  return bool(_finite(lead.distance_m, high=500.0) and lead.distance_m is not None and lead.distance_m < max(40.0, speed * 4.0))


def _open_road_handoff(scene: SceneEvidence) -> bool:
  lead, speed = scene.lead, scene.speed_mps
  if lead is None or speed is None or not _lead_valid(lead) or not lead.present or not _finite(speed, high=80.0):
    return False
  if lead.distance_m is None or lead.speed_mps is None or not all(_finite(value, high=500.0) for value in (lead.distance_m, lead.speed_mps)):
    return False
  return lead.speed_mps >= 1.0 and lead.distance_m >= max(35.0, speed * 1.5) and max(0.0, speed - lead.speed_mps) <= 3.0


class ConditionalModePolicy:
  """One stateful policy per drive/session; old scene state never crosses modes."""

  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.choice = ModeChoice.STOCK
    self.last_now: float | None = None
    self._reset_cem()
    self.ccm_candidate: Reason | None = None
    self.ccm_candidate_since = 0.0
    self.ccm_chill = False
    self.ccm_active_reason = Reason.NO_TRIGGER
    self.ccm_launch_lead = False
    self.ccm_hold_until = 0.0
    self.ccm_false_since: float | None = None

  def _reset_cem(self) -> None:
    self.cem_hold_until = 0.0
    self.cem_false_since: float | None = None
    self.cem_active = False
    self.cem_active_reason = Reason.NO_TRIGGER
    self.cem_slow_lead_until = 0.0
    self.cem_open_road_until = 0.0
    self.cem_previous_open_road = False
    self.cem_previous_standstill = False
    self.cem_previous_stop_hold = False
    self.cem_stop_release_pending = False
    self.cem_launch_suppress_until = 0.0

  @staticmethod
  def _decision(requested: bool, authority: Authority, qualified: bool, warm: bool, reason: Reason, status: int) -> Decision:
    return Decision(requested, bool(requested and qualified and authority.driving_enabled and authority.long_active), qualified, warm, reason, status)

  def step(
    self,
    now_s: float,
    choice: ModeChoice,
    manual: ManualIntent,
    authority: Authority,
    scene: SceneEvidence,
    settings: ModeSettings | None = None,
    *,
    stock_experimental: bool = False,
  ) -> Decision:
    if settings is None:
      settings = ModeSettings()
    if not isinstance(choice, ModeChoice):
      self.reset()
      return self._decision(False, authority, False, False, Reason.UNAVAILABLE, 0)
    if not _finite(now_s, high=1e12) or (self.last_now is not None and now_s < self.last_now):
      self.reset()
      return self._decision(False, authority, False, False, Reason.UNAVAILABLE, 0)
    if choice is not self.choice or (self.last_now is not None and now_s - self.last_now > SCENE_MAX_AGE_S):
      self.reset()
      self.choice = choice
    self.last_now = now_s
    authority_valid = all(
      _bool(value)
      for value in (authority.fresh, authority.system_long_capable, authority.safe_mode, authority.driving_enabled, authority.long_active, authority.lat_active)
    )
    qualified = bool(authority_valid and authority.fresh and authority.system_long_capable and not authority.safe_mode)
    warm = bool(authority_valid and authority.fresh and (authority.driving_enabled or authority.lat_active))
    if choice is ModeChoice.STOCK and scene.committed_stop is not True:
      return self._decision(bool(stock_experimental) if type(stock_experimental) is bool else False, authority, qualified, warm, Reason.STOCK, 0)
    if choice not in (ModeChoice.CEM, ModeChoice.CCM, ModeChoice.STOCK) or not qualified or not _valid_settings(settings):
      self.reset()
      return self._decision(False, authority, False, False, Reason.UNAVAILABLE, 0)
    if (warm and authority.driving_enabled and authority.long_active and
        _scene_current(now_s, scene) and scene.committed_stop is True):
      return self._decision(True, authority, True, warm, Reason.CEM_STOP, 8)
    if not isinstance(manual, ManualIntent):
      manual = ManualIntent.NONE
    if manual is not ManualIntent.NONE:
      # Manual intent clears confirmation/dwell but preserves the observed stop-release quiet period.
      suppress_until = self.cem_launch_suppress_until if choice is ModeChoice.CEM else 0.0
      current_scene = _scene_current(now_s, scene)
      if (
        choice is ModeChoice.CEM
        and current_scene
        and scene.standstill is False
        and (self.cem_previous_standstill and self.cem_previous_stop_hold or self.cem_stop_release_pending)
      ):
        suppress_until = now_s + CEM_STOP_RELEASE_SUPPRESS_S
      self.reset()
      self.choice = choice
      self.last_now = now_s
      if choice is ModeChoice.CEM:
        self.cem_launch_suppress_until = suppress_until
        self.cem_previous_standstill = current_scene and scene.standstill is True
      requested = manual is ManualIntent.FORCE_EXPERIMENTAL
      return self._decision(
        requested, authority, qualified, warm, Reason.MANUAL_EXPERIMENTAL if requested else Reason.MANUAL_CHILL, _manual_code(choice, manual)
      )
    if not warm or not _scene_current(now_s, scene):
      self._reset_cem()
      self.ccm_chill = False
      self.ccm_candidate = None
      self.ccm_false_since = None
      return self._decision(choice is ModeChoice.CCM, authority, qualified, warm, Reason.SCENE_UNAVAILABLE, 0)
    return self._cem(now_s, authority, warm, scene, settings) if choice is ModeChoice.CEM else self._ccm(now_s, authority, warm, scene, settings)

  def _cem_trigger(self, scene: SceneEvidence, settings: ModeSettings, *, launch_suppressed: bool = False) -> Reason | None:
    speed = scene.speed_mps
    if speed is None:
      return None
    lead = scene.lead
    following = scene.following_lead
    if not launch_suppressed and _bool(following) and speed >= 1.0:
      limit = settings.cem_speed_with_lead_mps if following else settings.cem_speed_mps
      if limit > speed:
        return Reason.CEM_SPEED
    set_speed = scene.set_speed_mps
    if (
      not launch_suppressed
      and settings.cem_open_road
      and lead is not None
      and _lead_valid(lead)
      and _bool(following)
      and not (lead.present or lead.tracked or following)
      and set_speed is not None
      and _finite(set_speed, high=80.0)
    ):
      if 0 < set_speed <= speed <= set_speed + MPH_TO_MPS:
        return Reason.CEM_OPEN_ROAD
    if (
      _bool(scene.left_blinker)
      and _bool(scene.right_blinker)
      and _bool(scene.lane_available)
      and settings.cem_signal_mps > speed
      and (scene.left_blinker or scene.right_blinker)
      and not scene.lane_available
    ):
      return Reason.CEM_SIGNAL
    if settings.cem_curves and scene.curve_detected is True and _bool(following) and (settings.cem_curves_with_lead or not following):
      return Reason.CEM_CURVE
    if not launch_suppressed and settings.cem_lead and scene.slow_lead_detected is True and speed <= 35.31:
      return Reason.CEM_LEAD
    if settings.cem_stop and (scene.stop_light_detected is True or scene.forcing_stop is True):
      return Reason.CEM_STOP
    return None

  def _cem(self, now_s: float, authority: Authority, warm: bool, scene: SceneEvidence, settings: ModeSettings) -> Decision:
    committed = settings.cem_stop and scene.forcing_stop is True and authority.driving_enabled and authority.long_active
    if scene.standstill is True and scene.standstill_stop_hold is None and not committed and self.cem_previous_stop_hold:
      return self._decision(True, authority, True, warm, Reason.CEM_STOP, 8)
    if not _bool(scene.standstill) or (scene.standstill and not committed and not _bool(scene.standstill_stop_hold)):
      self._reset_cem()
      return self._decision(False, authority, True, warm, Reason.SCENE_UNAVAILABLE, 0)
    released = not scene.standstill and (self.cem_previous_standstill and self.cem_previous_stop_hold or self.cem_stop_release_pending)
    if released:
      self.cem_launch_suppress_until = now_s + CEM_STOP_RELEASE_SUPPRESS_S
      self.cem_hold_until = self.cem_slow_lead_until = 0.0
      self.cem_false_since = None
      self.cem_active = False
      self.cem_stop_release_pending = False
    if scene.standstill:
      held = committed or scene.standstill_stop_hold is True
      self.cem_hold_until = self.cem_slow_lead_until = self.cem_open_road_until = 0.0
      self.cem_false_since = None
      self.cem_previous_open_road = False
      if held:
        self.cem_stop_release_pending = False
      elif self.cem_previous_stop_hold:
        self.cem_stop_release_pending = True
      if self.cem_stop_release_pending:
        self.cem_launch_suppress_until = now_s + CEM_STOP_RELEASE_SUPPRESS_S
      self.cem_active = held
      self.cem_previous_standstill = True
      self.cem_previous_stop_hold = held
      self.cem_active_reason = Reason.CEM_STOP if held else Reason.NO_TRIGGER
      return self._decision(held, authority, True, warm, self.cem_active_reason, 8 if held else 0)
    self.cem_previous_standstill = self.cem_previous_stop_hold = False
    trigger = self._cem_trigger(scene, settings, launch_suppressed=now_s < self.cem_launch_suppress_until)
    if trigger is not None:
      self.cem_hold_until = now_s + CEM_HOLD_S
      self.cem_false_since = None
      self.cem_open_road_until = 0.0
      self.cem_slow_lead_until = now_s + CEM_SLOW_LEAD_HOLD_S if trigger is Reason.CEM_LEAD else 0.0
      self.cem_active_reason = trigger
    else:
      if self.cem_active and self.cem_false_since is None:
        self.cem_false_since = now_s
      elif not self.cem_active:
        self.cem_false_since = None
    if not settings.cem_open_road:
      self.cem_open_road_until = 0.0
    elif self.cem_previous_open_road and _open_road_handoff(scene):
      self.cem_open_road_until = now_s + CEM_OPEN_ROAD_HANDOFF_S
    open_road_hold = trigger is None and now_s < self.cem_open_road_until and _open_road_handoff(scene)
    slow_lead_hold = settings.cem_lead and now_s < self.cem_slow_lead_until and _credible_slow_lead(scene)
    if not slow_lead_hold:
      self.cem_slow_lead_until = 0.0
    if open_road_hold:
      self.cem_active_reason = Reason.CEM_OPEN_ROAD
    if slow_lead_hold and trigger is None:
      self.cem_active_reason = Reason.CEM_LEAD
    self.cem_active = bool(
      trigger is not None
      or slow_lead_hold
      or open_road_hold
      or now_s < self.cem_hold_until
      or self.cem_false_since is not None
      and now_s - self.cem_false_since < CEM_EXIT_BUFFER_S
    )
    self.cem_previous_open_road = trigger is Reason.CEM_OPEN_ROAD
    reason = trigger if trigger is not None else Reason.CEM_HOLD if self.cem_active else Reason.NO_TRIGGER
    status_reason = self.cem_active_reason if self.cem_active else Reason.NO_TRIGGER
    status = {
      Reason.CEM_CURVE: 3,
      Reason.CEM_LEAD: 4,
      Reason.CEM_SIGNAL: 5,
      Reason.CEM_SPEED: 6,
      Reason.CEM_OPEN_ROAD: 6,
      Reason.CEM_STOP: 8,
    }.get(status_reason, 0)
    return self._decision(self.cem_active, authority, True, warm, reason, status)

  def _ccm(self, now_s: float, authority: Authority, warm: bool, scene: SceneEvidence, settings: ModeSettings) -> Decision:
    speed = scene.speed_mps
    if speed is None:
      return self._decision(True, authority, True, warm, Reason.SCENE_UNAVAILABLE, 0)
    veto_fields = (
      scene.standstill,
      scene.left_blinker,
      scene.right_blinker,
      scene.traffic_mode,
      scene.curve_detected,
      scene.slow_lead_detected,
      scene.stop_light_detected,
      scene.stop_sign_confirmed,
      scene.forcing_stop,
      scene.adjacent_lead_ambiguous,
      scene.low_speed_stop_scene,
    )
    launch = settings.ccm_launch and scene.launch_candidate is True
    hard_veto = veto_fields[1:-1]
    if any(value is True for value in hard_veto) or (scene.standstill is True and not launch) or (scene.low_speed_stop_scene is True and not launch):
      self.ccm_chill = False
      self.ccm_candidate = None
      self.ccm_false_since = None
      return self._decision(True, authority, True, warm, Reason.CCM_VETO, 0)
    if any(not _bool(value) for value in veto_fields):
      self.ccm_chill = False
      self.ccm_candidate = None
      self.ccm_false_since = None
      return self._decision(True, authority, True, warm, Reason.SCENE_UNAVAILABLE, 0)
    if settings.ccm_launch and (not _bool(scene.launch_candidate) or not _bool(scene.launch_forced_exit) or launch and not _bool(scene.launch_lead)):
      self.ccm_chill = False
      self.ccm_candidate = None
      self.ccm_false_since = None
      return self._decision(True, authority, True, warm, Reason.SCENE_UNAVAILABLE, 0)
    lead = scene.lead
    candidate = None
    if launch:
      candidate = Reason.CCM_LAUNCH
    elif (
      lead is not None
      and _lead_valid(lead)
      and not lead.present
      and not lead.tracked
      and scene.set_speed_mps is not None
      and _finite(scene.set_speed_mps, high=80.0)
      and speed >= settings.ccm_speed_mps
      and scene.set_speed_mps - speed >= settings.ccm_set_speed_margin_mps
    ):
      candidate = Reason.CCM_SPEED
    elif settings.ccm_lead and speed >= settings.ccm_speed_with_lead_mps and _stable_lead(scene):
      candidate = Reason.CCM_LEAD
    if candidate is not None:
      if candidate is not self.ccm_candidate:
        self.ccm_candidate = candidate
        self.ccm_candidate_since = now_s
      self.ccm_false_since = None
      confirm = {Reason.CCM_SPEED: CCM_SPEED_CONFIRM_S, Reason.CCM_LEAD: CCM_LEAD_CONFIRM_S, Reason.CCM_LAUNCH: CCM_LAUNCH_CONFIRM_S}[candidate]
      if self.ccm_chill or now_s - self.ccm_candidate_since >= confirm:
        self.ccm_chill = True
        self.ccm_active_reason = candidate
        self.ccm_launch_lead = candidate is Reason.CCM_LAUNCH and scene.launch_lead is True
        self.ccm_hold_until = max(self.ccm_hold_until, now_s + CCM_MIN_DWELL_S)
    else:
      self.ccm_candidate = None
      if settings.ccm_launch and scene.launch_forced_exit is True:
        self.ccm_chill = False
        self.ccm_active_reason = Reason.NO_TRIGGER
        self.ccm_false_since = None
        self.ccm_hold_until = 0.0
      if self.ccm_chill and self.ccm_false_since is None:
        self.ccm_false_since = now_s
      if self.ccm_chill and not (now_s < self.ccm_hold_until or self.ccm_false_since is not None and now_s - self.ccm_false_since < CCM_EXIT_BUFFER_S):
        self.ccm_chill = False
        self.ccm_active_reason = Reason.NO_TRIGGER
    reason = self.ccm_active_reason if self.ccm_chill and candidate else Reason.CCM_HOLD if self.ccm_chill else Reason.NO_TRIGGER
    lead_status = self.ccm_active_reason is Reason.CCM_LEAD or self.ccm_active_reason is Reason.CCM_LAUNCH and self.ccm_launch_lead
    status = 4 if self.ccm_chill and lead_status else 6 if self.ccm_chill else 0
    return self._decision(not self.ccm_chill, authority, True, warm, reason, status)
