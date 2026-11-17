"""Optional moving-lead acceleration restraint after leaving Experimental mode."""

import math
from dataclasses import dataclass

ConditionalHandoff = tuple[str, int, int, str]
MAX_SOURCE_AGE_NS = 250_000_000
MAX_FRAME_GAP_NS = 150_000_000
HOLD_NS = 750_000_000
LOW_SPEED_HOLD_NS = 3_000_000_000
LOW_SPEED_MPS = 12.0
MIN_DELTA_A = 0.12
ACCEL_STEP = 0.06
SOURCES = ('modelV2', 'carState', 'radarState', 'carControl', 'controlsState', 'selfdriveState')


@dataclass(frozen=True)
class ReleaseLead:
  distance: float
  speed: float
  acceleration: float
  radar: bool
  probability: float
  lateral_offset: float


@dataclass(frozen=True)
class ReleaseFrame:
  now_ns: int
  model_ns: int
  experimental: bool
  speed: float
  lead: ReleaseLead | None


def project(sm, cp, now_ns: int, *, lead_index: int | None,
            conditional_handoff: ConditionalHandoff | None) -> ReleaseFrame | None:
  try:
    if not valid_key(conditional_handoff) or conditional_handoff is None or type(now_ns) is not int or now_ns <= 0:
      return None
    drive_id = conditional_handoff[2]
    for name in SOURCES:
      stamp = sm.logMonoTime[name]
      if (sm.valid[name] is not True or sm.alive[name] is not True or type(stamp) is not int or
          not drive_id <= stamp <= now_ns or now_ns - stamp > MAX_SOURCE_AGE_NS):
        return None
    car = sm['carState']
    if (not cp.openpilotLongitudinalControl or not sm['carControl'].longActive or
        not sm['selfdriveState'].enabled or not car.canValid or car.canTimeout or
        car.gasPressed or car.brakePressed or car.standstill or sm['controlsState'].forceDecel or
        sm['modelV2'].action.shouldStop):
      return None
    leads = (sm['radarState'].leadOne, sm['radarState'].leadTwo)
    selected = (leads[lead_index] if lead_index is not None else
                next((lead for lead in leads if lead.present), None))
    lead = None
    if selected is not None and selected.present:
      lead = ReleaseLead(float(selected.dRel), float(selected.vLead), float(selected.aLeadK),
                         bool(selected.radar), float(selected.modelProb), float(selected.yRel))
    return ReleaseFrame(now_ns, sm.logMonoTime['modelV2'], bool(sm['selfdriveState'].experimentalMode),
                        float(car.vEgo), lead)
  except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
    return None


def valid_key(key: ConditionalHandoff | None) -> bool:
  return (type(key) is tuple and len(key) == 4 and type(key[0]) is str and bool(key[0]) and
          type(key[1]) is int and key[1] > 0 and type(key[2]) is int and key[2] > 0 and
          key[3] in ('conditional_experimental', 'conditional_chill'))


class ExperimentalRelease:
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.key: ConditionalHandoff | None = None
    self.previous: ReleaseFrame | None = None
    self.release_until_ns = 0

  def step(self, key: ConditionalHandoff | None, frame: ReleaseFrame | None, *, previous_target: float,
           target: float, follow_seconds: float, blocked: bool) -> float | None:
    if (not valid_key(key) or frame is None or blocked or
        not all(math.isfinite(value) for value in (previous_target, target, follow_seconds, frame.speed)) or
        frame.speed < 0 or not 0.5 <= follow_seconds <= 3.0 or type(frame.experimental) is not bool or
        type(frame.now_ns) is not int or type(frame.model_ns) is not int or
        not 0 < frame.model_ns <= frame.now_ns or frame.now_ns - frame.model_ns > MAX_SOURCE_AGE_NS):
      self.reset()
      return None
    lead = frame.lead
    if lead is not None and not all(math.isfinite(value) for value in
                                    (lead.distance, lead.speed, lead.acceleration, lead.probability, lead.lateral_offset)):
      self.reset()
      return None
    if key != self.key:
      self.reset()
    previous = self.previous
    if previous is not None and (not 0 < frame.model_ns - previous.model_ns <= MAX_FRAME_GAP_NS or
                                 not 0 < frame.now_ns - previous.now_ns <= MAX_FRAME_GAP_NS):
      self.reset()
      return None
    self.key = key
    self.previous = frame
    if frame.experimental:
      self.release_until_ns = 0
    elif previous is not None and previous.experimental:
      self.release_until_ns = frame.now_ns + (LOW_SPEED_HOLD_NS if frame.speed < LOW_SPEED_MPS else HOLD_NS)
    stopped_radar_lead = (key is not None and key[3] == 'conditional_experimental' and lead is not None and lead.radar and 0.0 < frame.speed < 2.5 and
                          -0.5 <= lead.speed < 0.5 and abs(lead.acceleration) <= 0.2)
    if (frame.now_ns >= self.release_until_ns or lead is None or
        (not stopped_radar_lead and (frame.speed < 2.5 or lead.speed < 5.0)) or
        not -1.0 <= lead.speed - frame.speed <= 1.5 or max(0.0, -lead.acceleration) > 0.2 or
        (not lead.radar and lead.probability < 0.9) or abs(lead.lateral_offset) > 1.5 or
        lead.distance / max(frame.speed, 1e-3) < follow_seconds or target - previous_target < MIN_DELTA_A):
      return None
    if stopped_radar_lead:
      return min(target, max(0.0, previous_target) + ACCEL_STEP) if target > 0.0 else None
    return min(target, previous_target + ACCEL_STEP)
