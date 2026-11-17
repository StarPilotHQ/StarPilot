"""Read-only projection of current planner inputs into one curve runtime frame."""

from dataclasses import replace
import math
import time

from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.realtime import DT_MDL
from openpilot.starpilot.curve_speed.runtime import Frame, INPUT_MAX_AGE_NS, MODEL_MAX_AGE_NS, Runtime
from openpilot.starpilot.curve_speed.target import CurveProfile
from openpilot.starpilot.lead_tracking import FILTER_THRESHOLD, FILTER_TIME_S, LeadDetector

CLOCK_PAIR_MAX_SKEW_NS = 5_000_000
LEAD_WARMUP_FRAMES = math.ceil(math.log(1 - FILTER_THRESHOLD) / math.log(1 - DT_MDL / (FILTER_TIME_S + DT_MDL)))


def clock_pair_ns() -> tuple[int, int] | None:
  """Bracket BOOTTIME with Python MONOTONIC; never compare raw epochs."""
  try:
    before = time.monotonic_ns()
    boot_clock = getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC)
    boot = time.clock_gettime_ns(boot_clock)
    after = time.monotonic_ns()
  except OSError:
    return None
  return ((before + after) // 2, boot) if 0 <= after - before <= CLOCK_PAIR_MAX_SKEW_NS else None


def _fresh(sm, service: str, now_ns: int) -> bool:
  stamp = sm.logMonoTime.get(service, 0)
  frequency = SERVICE_LIST[service].frequency
  age_limit = min(INPUT_MAX_AGE_NS, int(2e9 / frequency)) if frequency > 0 else 0
  return (type(stamp) is int and 0 < stamp <= now_ns and now_ns - stamp <= age_limit and
          bool(sm.valid.get(service, False)) and bool(sm.alive.get(service, False)))


def frame_from_submaster(sm, CP, now_ns: int, *, drive_id: int = 0) -> Frame:
  """Missing transport clears authority without manufacturing a no-lead claim."""
  services = ('modelV2', 'carState', 'carControl', 'controlsState', 'selfdriveState', 'radarState')
  current = all(_fresh(sm, name, now_ns) for name in services)
  model_ns = sm.logMonoTime.get('modelV2', 0)
  model_ns = model_ns if type(model_ns) is int else 0
  try:
    model = sm['modelV2']
    profile = CurveProfile.from_model(model, model_ns) if _fresh(sm, 'modelV2', now_ns) else None
    car_state, car_control = sm['carState'], sm['carControl']
    controls, selfdrive = sm['controlsState'], sm['selfdriveState']
    cruise_mps = float(car_state.vCruise) / 3.6
    system_longitudinal = bool(CP.openpilotLongitudinalControl) and not any(getattr(CP, flag, False) for flag in ('passive', 'dashcamOnly', 'notCar'))
    return Frame(now_ns=now_ns, model_ns=model_ns, inputs_fresh=current and bool(car_state.canValid) and not bool(car_state.canTimeout),
                 profile=profile, system_longitudinal=system_longitudinal, long_active=bool(car_control.longActive),
                 enabled=bool(selfdrive.enabled), force_decel=bool(controls.forceDecel), cruise_mps=cruise_mps,
                 ego_mps=float(car_state.vEgo), measured_curvature=float(controls.curvature), tracking_lead=None,
                 blinker=bool(car_state.leftBlinker or car_state.rightBlinker), gas_pressed=bool(car_state.gasPressed),
                 brake_pressed=bool(car_state.brakePressed), drive_id=drive_id)
  except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
    return Frame(now_ns, model_ns, False, None, False, False, False, False, 0.0, 0.0,
                 0.0, None, False, False, False, drive_id)


class CurveHost:
  """Planner-owned candidate/status shell; no Params writes or plan publication."""

  def __init__(self, document: object = None, *, enabled: bool = False, no_lead: bool = False,
               clock_pair=clock_pair_ns, replay: bool = False):
    self.runtime = Runtime(document, enabled=enabled, no_lead=no_lead)
    self.clock_pair = clock_pair
    self.replay = replay
    self.boot_offset_ns: int | None = None
    self.source_floor_ns = 0
    self.leads = LeadDetector()
    self.lead_samples = 0
    self.lead_drive_id = 0
    self.previous_long_active = False
    self.previous_active_headway: float | None = None

  def _reset_leads(self) -> None:
    self.leads.reset()
    self.lead_samples = 0
    self.previous_long_active = False
    self.previous_active_headway = None

  def sample(self, sm, CP, now_ns: int, *, drive_id: int = 0, event=None, follow_time_s: float | None = None,
             cruise_ceiling_mps: float | None = None):
    frame = frame_from_submaster(sm, CP, now_ns, drive_id=drive_id)
    if cruise_ceiling_mps is not None:
      if not math.isfinite(cruise_ceiling_mps) or cruise_ceiling_mps <= 0:
        frame = replace(frame, inputs_fresh=False)
      else:
        frame = replace(frame, cruise_mps=min(frame.cruise_mps, cruise_ceiling_mps))
    if drive_id and self.lead_drive_id and drive_id != self.lead_drive_id:
      self._reset_leads()
    if drive_id:
      self.lead_drive_id = drive_id
    if not self.replay:
      pair = self.clock_pair()
      if pair is None or any(type(value) is not int or value <= 0 for value in pair):
        self.boot_offset_ns = None
        self.source_floor_ns = max(self.source_floor_ns, now_ns)
        self.runtime.reset(drive_id=drive_id)
        self._reset_leads()
        return self.runtime.step(replace(frame, inputs_fresh=False), event=None)
      offset = pair[1] - pair[0]
      rearmed = False
      if self.boot_offset_ns is None or abs(offset - self.boot_offset_ns) > CLOCK_PAIR_MAX_SKEW_NS:
        self.boot_offset_ns = offset
        self.source_floor_ns = pair[0]
        self.runtime.reset(drive_id=drive_id)
        self._reset_leads()
        rearmed = True
      required = ('modelV2', 'carState', 'carControl', 'controlsState', 'selfdriveState', 'radarState')
      try:
        camera_eof = sm['modelV2'].timestampEof
        camera_age = pair[1] - camera_eof if type(camera_eof) is int and camera_eof > 0 else -1
        camera_current = (0 <= camera_age <= MODEL_MAX_AGE_NS and camera_eof <= now_ns + offset)
      except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
        camera_current = False
      # A newly published model envelope cannot renew the age of its camera
      # input. EOF uses BOOTTIME; only the bracketed clock pair may compare it.
      # Offline replay intentionally keeps its separate recorded timeline.
      if (not camera_current or not 0 < frame.model_ns <= now_ns or
          pair[0] - frame.model_ns > MODEL_MAX_AGE_NS or not 0 <= pair[0] - now_ns <= INPUT_MAX_AGE_NS):
        frame = replace(frame, inputs_fresh=False)
      if rearmed or any(type(sm.logMonoTime.get(name, 0)) is not int or sm.logMonoTime[name] <= self.source_floor_ns
             for name in required):
        frame = replace(frame, inputs_fresh=False)
    if frame.inputs_fresh and frame.profile is not None:
      try:
        # Tracking precedes headway reset: the first inactive tick retains the prior headway,
        # then uses zero. Active control requires this cycle's MPC headway.
        detector_headway = (follow_time_s if frame.long_active else
                            self.previous_active_headway if self.previous_long_active and
                            self.previous_active_headway is not None else 0.0)
        headway_fresh = (not frame.long_active or
                         (detector_headway is not None and type(detector_headway) in (int, float) and math.isfinite(detector_headway) and
                          0.5 <= detector_headway <= 3.0))
        observed = self.leads.step(sm['radarState'].leadOne, sm['modelV2'], speed_mps=frame.ego_mps,
                                   t_follow_s=detector_headway, standstill=bool(sm['carState'].standstill),
                                   observed_mono_s=frame.model_ns / 1e9, now_mono_s=now_ns / 1e9,
                                   radar_fresh=_fresh(sm, 'radarState', now_ns), model_fresh=_fresh(sm, 'modelV2', now_ns),
                                   car_fresh=_fresh(sm, 'carState', now_ns), headway_fresh=headway_fresh)
        if self.leads.last_observed_mono_s is None:
          self.lead_samples = 0
        elif observed.tracked is not None and not sm['carState'].standstill:
          self.lead_samples += 1
        tracked = observed.tracked
        following = (observed.following if frame.long_active else
                     False if observed.tracked is not None else None)
        if self.lead_samples < LEAD_WARMUP_FRAMES and not tracked and observed.raw_present:
          tracked = following = None
        frame = replace(frame, tracking_lead=tracked, following_lead=following)
        self.previous_long_active = frame.long_active if observed.tracked is not None else False
        self.previous_active_headway = (float(detector_headway) if detector_headway is not None and frame.long_active and headway_fresh and
                                        observed.tracked is not None else None)
      except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
        self._reset_leads()
    return self.runtime.step(frame, event=event)
