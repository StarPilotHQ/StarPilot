"""Release a confirmed Tesla Chill creep request without changing acceleration."""
from dataclasses import dataclass
import math

from openpilot.starpilot.longitudinal.lead_takeoff import Lead
from openpilot.starpilot.model_geometry import tesla_stationary_zero

MAX_AGE_NS = 150_000_000
CONFIRM_NS = 500_000_000
BASE_GAP = 6.0


@dataclass(frozen=True)
class Frame:
  now_ns: int
  model_ns: int
  drive_id: int
  standstill: bool
  speed: float
  selected_speed: float
  mpc_accel: float
  cruise_accel: float
  follow_seconds: float
  leads: tuple[Lead, ...]
  release_allowed: bool = True


class SlowCreep:
  def __init__(self):
    self.reported_release = False
    self.reset()

  def reset(self):
    self.key = None
    self.last_ns = 0
    self.model_ns = 0
    self.last_gap = 0.
    self.observed_stop = False
    self.confirm_gap = None
    self.ready_since = None
    self.released = False
    self.reason = 'unavailable'

  def native(self, target, should_stop, reason):
    self.ready_since = self.confirm_gap = None
    self.released = False
    self.reason = reason
    return target, should_stop

  def step(self, frame: Frame | None, target: float, should_stop: bool):
    if frame is None:
      self.reset()
      return target, should_stop
    f = frame
    values = (f.speed, f.selected_speed, f.mpc_accel, f.cruise_accel, f.follow_seconds, target)
    if (not all(math.isfinite(v) for v in values) or not 0 <= f.speed < .8 or
        not .5 <= f.follow_seconds <= 3. or not 0 < f.drive_id < f.model_ns <= f.now_ns or
        f.now_ns - f.model_ns > MAX_AGE_NS or type(should_stop) is not bool or not f.leads):
      self.reset()
      return target, should_stop
    primary = next((lead for lead in f.leads if lead.slot == 0), None)
    if primary is None or not primary.credible(.97) or abs(primary.lateral) > .8:
      self.reset()
      return target, should_stop
    nearby = tuple(lead for lead in f.leads if lead.distance <= primary.distance + 3.)
    if any(not lead.credible(.95) or abs(lead.lateral) > 1. for lead in nearby):
      self.reset()
      return target, should_stop
    lead = min(nearby, key=lambda lead: lead.distance)
    key = (f.drive_id, tuple(item.identity for item in nearby))
    continuous = (self.key == key and 0 < f.now_ns - self.last_ns <= MAX_AGE_NS and
                  f.model_ns > self.model_ns and abs(lead.distance - self.last_gap) <= .8)
    if not continuous:
      self.reset()
      self.key = key
    self.last_ns, self.model_ns, self.last_gap = f.now_ns, f.model_ns, lead.distance
    self.observed_stop |= f.standstill and f.speed <= .05
    if not f.release_allowed:
      return self.native(target, should_stop, 'stop_owner')
    if not self.observed_stop:
      return self.native(target, should_stop, 'no_observed_stop')
    if not all(item.speed >= .08 and item.acceleration >= -.15 for item in nearby):
      return self.native(target, should_stop, 'lead_not_departing')
    if f.selected_speed <= f.speed + .1 or min(f.mpc_accel, target, f.cruise_accel) <= 0.:
      return self.native(target, should_stop, 'native_request')
    if self.ready_since is None:
      self.ready_since, self.confirm_gap = f.now_ns, lead.distance
    # Opening is measured from this confirmation, never an old noisy minimum.
    # Once admitted, genuine braking/lead/clearance guards still apply each frame.
    if not self.released and (f.now_ns - self.ready_since < CONFIRM_NS or lead.distance - self.confirm_gap < .4):
      self.reason = 'confirming_motion'
      return target, should_stop
    if lead.distance < BASE_GAP + f.speed * f.follow_seconds:
      self.released = False
      self.reason = 'clearance'
      return target, should_stop
    self.released = True
    self.reason = 'confirmed_creep'
    return target, False

  def diagnostic(self, target, native_stop):
    if self.reported_release == self.released:
      return None
    self.reported_release = self.released
    return {'admitted': self.released, 'reason': self.reason, 'native_target': float(target), 'native_stop': bool(native_stop)}


def project(sm, cp, *, now_ns, drive_id, selected_speed, mpc_accel, cruise_accel,
            follow_seconds, stop_plan, threshold_only=False, blocked=False):
  """Read current native inputs; this path never changes Experimental stopping."""
  try:
    services = ('carState', 'carControl', 'controlsState', 'selfdriveState', 'radarState', 'modelV2')
    if (blocked or cp.carFingerprint not in ('TESLA_MODEL_3', 'TESLA_MODEL_Y') or
        not cp.openpilotLongitudinalControl or cp.passive or cp.dashcamOnly or cp.notCar or
        not sm['selfdriveState'].enabled or
        not sm['carControl'].longActive or sm['controlsState'].forceDecel or
        stop_plan.forcing or stop_plan.should_stop or stop_plan.manual_hold or
        stop_plan.obstacle_m is not None or stop_plan.speed_ceiling_mps is not None):
      return None
    if not all(sm.valid[name] and sm.alive[name] and
               drive_id < sm.logMonoTime[name] <= now_ns and now_ns - sm.logMonoTime[name] <= MAX_AGE_NS and
               drive_id < int(sm.recv_time[name] * 1e9) <= now_ns and
               now_ns - int(sm.recv_time[name] * 1e9) <= MAX_AGE_NS for name in services):
      return None
    car = sm['carState']
    if (not car.canValid or car.canTimeout or car.gasPressed or car.brakePressed or
        str(car.gearShifter) != 'drive'):
      return None
    leads = tuple(Lead(slot, int(lead.radarTrackId), bool(lead.radar), float(lead.modelProb), float(lead.dRel),
                       float(lead.vLead), float(lead.aLeadK), float(lead.yRel))
                  for slot, lead in enumerate((sm['radarState'].leadOne, sm['radarState'].leadTwo)) if lead.present)
    if any(not all(math.isfinite(v) for v in (lead.distance, lead.speed, lead.acceleration,
                                             lead.probability, lead.lateral)) for lead in leads):
      return None
    return Frame(now_ns, int(sm.logMonoTime['modelV2']), drive_id, bool(car.standstill),
                 0. if tesla_stationary_zero(car, cp) else float(car.vEgo), float(selected_speed),
                 float(mpc_accel), float(cruise_accel), float(follow_seconds), leads,
                 bool(threshold_only) and not bool(sm['selfdriveState'].experimentalMode))
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RuntimeError):
    return None
