"""Opt-in stopped-lead departure; Dom confirmation/floors, native braking retained.

Source: Dom 4ac874f6 longitudinal_planner.py, introduced by 78e9b5139c.
This does not modify MPC costs, obstacles, trajectories or ordinary following.
"""
from dataclasses import dataclass
import math

from opendbc.car.toyota.values import CAR as TOYOTA, ToyotaFlags

BREAKAWAY_MIN_DISTANCE = 3.75
BREAKAWAY_MIN_LEAD_SPEED = .6


@dataclass(frozen=True)
class Lead:
  slot: int
  track_id: int
  radar: bool
  probability: float
  distance: float
  speed: float
  acceleration: float
  lateral: float

  @property
  def identity(self):
    return (self.slot, self.radar, self.track_id)

  def credible(self, probability=.85):
    return (all(math.isfinite(x) for x in (self.probability, self.distance, self.speed, self.acceleration, self.lateral)) and
            self.distance > 0 and (self.radar or self.probability >= probability) and abs(self.lateral) <= 1.75)


@dataclass(frozen=True)
class Frame:
  now_ns: int
  model_ns: int
  drive_id: int
  eligible: bool
  standstill: bool
  speed: float
  model_accel: float
  mpc_accel: float
  acceleration_max: float
  follow_seconds: float
  leads: tuple[Lead, ...]
  blocked: bool = False
  maximum_breakaway: bool = True


def confident(lead, speed):
  return (lead.credible() and 3.75 <= lead.distance <= 5.25 and lead.speed >= .3 and
          lead.speed - speed >= .25 and lead.acceleration >= .2)


def departure_floor(lead, speed, model_accel):
  close = confident(lead, speed)
  if (not lead.credible() or speed > 2.0 or lead.speed < (.3 if close else .6) or
      lead.speed - speed < (.25 if close else .5) or lead.distance < (3.75 if close else 3.5) or
      lead.acceleration < -.2 or model_accel < (0.0 if close else .12)):
    return None
  gap = min(1., max(0., (lead.distance - 3.5) / 2.5))
  moving = min(1., max(0., (lead.speed - .6) / 1.6))
  cap = .25 + .30 * (.55 * moving + .45 * gap)
  return min(cap, max(model_accel + .10, .25))


class LeadTakeoff:
  def __init__(self):
    self.reset()

  def reset(self):
    self.key = None
    self.last_ns = 0
    self.last_model_ns = 0
    self.confident_since = None
    self.creep_since = None
    self.hold_until = 0
    self.hold_floor = None
    self.release_since = None
    self.release_until = 0
    self.previous_should_stop = False

  def step(self, enabled, frame: Frame | None, target, should_stop):
    # Keep the native numeric/type path untouched when the preference is absent.
    if enabled is not True or frame is None:
      self.reset()
      return target, should_stop
    f = frame
    values = (f.speed, f.model_accel, f.mpc_accel, f.acceleration_max, f.follow_seconds, target)
    if (not f.eligible or f.blocked or f.drive_id <= 0 or not all(math.isfinite(x) for x in values) or
        not 0 <= f.speed <= 2.0 or f.mpc_accel < 0 or f.acceleration_max <= 0 or not .5 <= f.follow_seconds <= 3 or
        not f.drive_id < f.model_ns <= f.now_ns or f.now_ns - f.model_ns > 150_000_000):
      self.reset()
      return target, should_stop
    credible = [lead for lead in f.leads if lead.credible()]
    if not credible:
      self.reset()
      return target, should_stop
    lead = min(credible, key=lambda lead: lead.distance)
    if any(other.identity != lead.identity and other.distance <= lead.distance + 3 and other.speed < .25
           for other in credible):
      self.reset()
      return target, should_stop
    key = (f.drive_id, lead.identity)
    if key != self.key or f.now_ns <= self.last_ns or f.now_ns - self.last_ns > 150_000_000 or f.model_ns <= self.last_model_ns:
      self.reset()
      self.key = key
    self.last_ns, self.last_model_ns = f.now_ns, f.model_ns
    safe_release = (lead.credible(.95) and abs(lead.lateral) <= 1. and lead.distance >= 3. and
                    lead.speed >= .55 and lead.speed - f.speed >= .25 and lead.acceleration >= -.15 and f.speed <= 1.5)
    self.release_since = (f.now_ns if self.release_since is None else self.release_since) if safe_release else None
    release_ready = self.release_since is not None and f.now_ns - self.release_since >= 150_000_000
    if not safe_release:
      self.release_until = 0
    if safe_release and f.now_ns < self.release_until:
      should_stop = False
    close = f.standstill and confident(lead, f.speed)
    creep = (f.standstill and lead.credible(.95) and lead.distance >= 5.6 and
             lead.speed >= .25 and lead.acceleration >= .08)
    self.confident_since = (f.now_ns if self.confident_since is None else self.confident_since) if close else None
    self.creep_since = (f.now_ns if self.creep_since is None else self.creep_since) if creep else None
    close_ready = self.confident_since is not None and f.now_ns - self.confident_since >= 350_000_000
    creep_ready = self.creep_since is not None and f.now_ns - self.creep_since >= 300_000_000
    regular = (f.speed <= 1.5 and lead.speed >= .6 and lead.distance >= 6.3 and
               lead.acceleration >= -.2 and f.model_accel >= .08)
    floor = None
    if close_ready or regular:
      floor = .35
    elif creep_ready:
      floor = .18
    # A model-only stopped plan may release only a confirmed live departure.
    if should_stop and floor is None:
      self.hold_floor = None
      self.hold_until = 0
      self.previous_should_stop = should_stop
      return target, should_stop
    computed = departure_floor(lead, f.speed, f.model_accel) if floor is not None or not should_stop else None
    if computed is not None:
      floor = max(computed, floor or 0.)
      if f.standstill:
        self.hold_floor, self.hold_until = floor, f.now_ns + 1_200_000_000
    if floor is None and self.hold_floor is not None and f.now_ns <= self.hold_until:
      if (lead.distance >= 3.75 and lead.acceleration >= -.2 and max(f.speed - lead.speed, 0.) <= .45 and
          lead.distance / max(f.speed, .001) - f.follow_seconds >= .1):
        floor = self.hold_floor
    if floor is None:
      self.previous_should_stop = should_stop
      return target, should_stop
    bounded = min(floor, f.acceleration_max)
    result = max(target, bounded)
    # Modern stop threshold is .1 m/s²: a lower saved/physical cap cannot prove release.
    output_stop = False if bounded >= .1 else should_stop
    # Gas Highlander TSS2 keeps the departure floor instead of a maximum-accel pulse.
    if (f.maximum_breakaway and f.standstill and not output_stop and lead.distance >= BREAKAWAY_MIN_DISTANCE and
        lead.speed >= BREAKAWAY_MIN_LEAD_SPEED and lead.speed - f.speed >= .25 and lead.acceleration >= -.2):
      result = max(result, f.acceleration_max)
    if self.previous_should_stop and not output_stop and release_ready:
      self.release_until = f.now_ns + 1_500_000_000
    self.previous_should_stop = output_stop
    return result, output_stop


def project(sm, cp, *, now_ns, drive_id, stop_plan, acceleration_max, mpc_accel, follow_seconds, blocked=False):
  """No CEM handoff prerequisite: validate the current native planner inputs."""
  try:
    services = ('carState', 'carControl', 'controlsState', 'selfdriveState', 'radarState', 'modelV2')
    current = all(sm.valid[name] and sm.alive[name] and drive_id < sm.logMonoTime[name] <= now_ns and
                  now_ns - sm.logMonoTime[name] <= 150_000_000 and
                  drive_id < int(sm.recv_time[name] * 1e9) <= now_ns and
                  now_ns - int(sm.recv_time[name] * 1e9) <= 150_000_000 for name in services)
    car = sm['carState']
    eligible = (cp.openpilotLongitudinalControl and not (cp.passive or cp.dashcamOnly or cp.notCar) and
                current and sm['selfdriveState'].enabled and sm['carControl'].longActive and
                car.canValid and not car.canTimeout and not car.gasPressed and not car.brakePressed and
                not sm['controlsState'].forceDecel)
    light_current = stop_plan.takeoff_light_observed and stop_plan.model_ns == sm.logMonoTime['modelV2']
    model = sm['modelV2']
    leads = tuple(Lead(slot, int(lead.radarTrackId), bool(lead.radar), float(lead.modelProb), float(lead.dRel),
                       float(lead.vLead), float(lead.aLeadK), float(lead.yRel))
                  for slot, lead in enumerate((sm['radarState'].leadOne, sm['radarState'].leadTwo)) if lead.present)
    # Preserve Dom's centered stopped-model/offcenter radar mismatch veto.
    conflict = any(float(lead.prob) >= .95 and 0 < float(lead.x[0]) <= 18 and
                   abs(float(lead.y[0])) <= .9 and max(float(lead.v[0]), 0.) <= 2. and
                   any(radar.radar and radar.distance <= 18 and abs(radar.lateral) >= 1.5 and
                       abs(radar.distance - float(lead.x[0])) <= 4. for radar in leads)
                   for lead in model.leadsV3)
    return Frame(now_ns, int(sm.logMonoTime['modelV2']), drive_id, bool(eligible), bool(car.standstill),
                 float(car.vEgo), float(model.action.desiredAcceleration), float(mpc_accel), float(acceleration_max),
                 float(follow_seconds), leads, bool(blocked or not light_current or stop_plan.takeoff_light or
                                                  stop_plan.forcing or stop_plan.should_stop or conflict),
                 maximum_breakaway=(getattr(cp, "carFingerprint", "") != TOYOTA.TOYOTA_HIGHLANDER_TSS2 or
                                    bool(getattr(cp, "flags", 0) & ToyotaFlags.HYBRID)))
  except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError, RuntimeError):
    return None
