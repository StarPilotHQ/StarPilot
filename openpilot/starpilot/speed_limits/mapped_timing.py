"""SLC mapped timing using the cruise braking curve and command ownership.

Predictions integrate the selected speed-dependent brake curve and the cruise
jerk schedule. They are a comfort forecast, not a stopping-distance guarantee.
Only the existing v2 MapTracker can admit new map evidence.
"""

from dataclasses import dataclass, replace
from bisect import bisect_right
import hashlib
import math
import uuid
from openpilot.starpilot.longitudinal.set_speed_comfort import braking_lower_bound, CruiseTransition
from openpilot.starpilot.longitudinal.slc_coast import shaped_slc_floor
from openpilot.selfdrive.car.cruise import slc_target_kph

from openpilot.starpilot.common.slc_transition import MapApproach, LowerLimitDistance
from openpilot.starpilot.common.slc_warning import BoundaryWarningIdentity, SpeedChangeWarning, WarningBoundaryPin
from openpilot.starpilot.speed_limits import acceptance as acc, speed_domain as sd


def interpolate(x, xp, fp):
  index = min(max(bisect_right(xp, x) - 1, 0), len(xp) - 2)
  t = min(max((x - xp[index]) / (xp[index + 1] - xp[index]), 0.0), 1.0)
  return fp[index] + t * (fp[index + 1] - fp[index])


@dataclass(frozen=True)
class BrakingProfile:
  speeds: tuple[float, ...] = (0.0, 70.0)
  floors: tuple[float, ...] = (-0.5, -0.5)
  limit: float | None = None
  jerk: float | None = None
  style: str = "standard"
  easing: bool = True

  def floor(self, speed):
    # More braking allowance does not guarantee a stronger actual request.
    floor = max(-3.5, min(-0.1, interpolate(speed, self.speeds, self.floors)))
    return max(floor, -self.limit) if self.limit is not None else floor


def braking_distance(speed, target, acceleration, profile):
  if speed <= target + 0.05:
    return 0.0
  if not all(math.isfinite(x) for x in (speed, target, acceleration)) or not 0 <= target < speed <= 70 or target < 1:
    return 0.0
  distance = 0.0
  a = min(max(acceleration, -3.5), 3.5)
  # One command/actuation allowance; the SLC ceiling bounds the brake curve only.
  # Continue measured acceleration during handover, including existing braking.
  delay = 0.6
  transition = CruiseTransition() if profile.jerk is not None else None
  for i in range(12000):
    # Same target-error clipping and speed-dependent jerk as get_cruise_accel.
    floor = profile.floor(speed)
    if profile.easing:
      floor = shaped_slc_floor(speed, target, floor, profile.style, approach=True)
    requested = max(target - speed, floor) if i * 0.05 >= delay else a
    if transition is not None and i * 0.05 >= delay:
      requested = transition.shape(
        selected=target, speed=speed, desired=requested, previous=a, now_ns=1_000_000_000 + i * 50_000_000, enabled=True, response_time=0.3
      )
    jerk = interpolate(speed, (0.0, 10.0, 25.0, 40.0), (1.6, 1.2, 0.8, 0.6))
    down_jerk = min(jerk, profile.jerk) if profile.jerk is not None else jerk
    next_a = min(max(requested, braking_lower_bound(a, 0.05, jerk, down_jerk)), a + jerk * 0.05)
    mean = next_a
    dt = min(0.05, (speed - target - 0.05) / -mean) if mean < 0 else 0.05
    next_speed = speed + mean * dt
    distance += (speed + next_speed) * 0.5 * dt
    speed, a = next_speed, next_a
    if speed <= target + 0.05 + 1e-8:
      return max(distance, 0.0)
  return math.inf


def profile_from_host(host, now_ns, personality, cp, *, traffic=False, enabled=False, previous=None, qualified=True, slc_limit=0.8):
  # A Tesla Model 3/Y eases cruise slowing with its set-speed comfort jerk; other cars keep the native schedule.
  from openpilot.starpilot.longitudinal.set_speed_comfort import DOWN_JERK
  jerk = DOWN_JERK if getattr(cp, "carFingerprint", None) in ("TESLA_MODEL_3", "TESLA_MODEL_Y") else None
  # Runtime freshness is not a different saved braking curve. Unknown inputs
  # prohibit a new lower preview; they must not manufacture a softer profile.
  if qualified is not True or type(traffic) is not bool or type(slc_limit) not in (int, float) or not math.isfinite(slc_limit) or not 0.1 <= slc_limit <= 3.5:
    return None
  if host is None or not enabled:
    return BrakingProfile(floors=(-1.2, -1.2), jerk=jerk, easing=traffic is False, limit=slc_limit)
  from openpilot.starpilot.longitudinal.profile_document import BRAKING_SPEEDS_MPH, _NATIVE_ACCELERATION_SPEEDS_MS

  # Include every native/custom interpolation breakpoint; a regular grid can miss a dip.
  speeds = tuple(sorted({0.0, 70.0, 25.0, *_NATIVE_ACCELERATION_SPEEDS_MS, *(v * 0.44704 for v in BRAKING_SPEEDS_MPH)}))
  floors = []
  style = "standard"
  for speed in speeds:
    legacy = host.sample(now_ns, personality, speed, cp, traffic_mode=traffic)
    selected = host.sample_selected(now_ns, personality, speed, cp, traffic_mode=traffic, legacy=legacy)
    magnitude = selected.cruise_brake_magnitude if selected is not None else None
    if magnitude is None:
      magnitude = legacy.cruise_brake_magnitude if legacy is not None else None
    if magnitude is None:
      if not getattr(host, "disabled", False):
        return None
      magnitude = 1.2
    if previous is not None:
      magnitude = min(magnitude, previous.cruise_brake_magnitude)
    floors.append(-float(magnitude))
    style = getattr(selected, "braking_style", getattr(legacy, "slc_braking_style", "standard"))
  return BrakingProfile(
    speeds, tuple(floors), limit=slc_limit, jerk=jerk, style=style if style in ("eco", "standard", "sport") else "standard", easing=traffic is False
  )


# openpilot's stock cruise acceleration ceiling (longitudinal_planner A_CRUISE_MAX) when no profile applies.
STOCK_ACCEL_BP = (0.0, 10.0, 25.0, 40.0)
STOCK_ACCEL_MAX = (1.6, 1.2, 0.8, 0.6)


@dataclass(frozen=True)
class AccelerationProfile:
  speeds: tuple[float, ...] = STOCK_ACCEL_BP
  ceilings: tuple[float, ...] = STOCK_ACCEL_MAX
  limit: float | None = None  # SLC acceleration limit of the active personality (Use SLC acceleration limits)

  def ceiling(self, speed):
    ceiling = max(0.1, min(3.5, interpolate(speed, self.speeds, self.ceilings)))
    return min(ceiling, self.limit) if self.limit is not None else ceiling


def acceleration_distance(speed, target, acceleration, profile):
  """Forecast distance to speed up from speed to target: the profile's ceiling, the same target-error approach and
  speed-dependent jerk as braking_distance, and a 0.6 s hand-over at the measured acceleration. A comfort
  forecast for when to start, not a promise of how hard the car will actually accelerate."""
  if target <= speed + 0.05:
    return 0.0
  if not all(math.isfinite(x) for x in (speed, target, acceleration)) or not 0 <= speed < target <= 70:
    return 0.0
  distance = 0.0
  a = min(max(acceleration, -3.5), 3.5)
  delay = 0.6
  for i in range(12000):
    requested = min(target - speed, profile.ceiling(speed)) if i * 0.05 >= delay else a
    jerk = interpolate(speed, (0.0, 10.0, 25.0, 40.0), (1.6, 1.2, 0.8, 0.6))
    next_a = max(min(requested, a + jerk * 0.05), a - jerk * 0.05)
    dt = min(0.05, (target - 0.05 - speed) / next_a) if next_a > 0 else 0.05
    next_speed = max(0.0, speed + next_a * dt)
    distance += (speed + next_speed) * 0.5 * dt
    speed, a = next_speed, next_a
    if speed >= target - 0.05 - 1e-8:
      return max(distance, 0.0)
  return math.inf


def acceleration_profile_from_host(host, now_ns, personality, cp, *, traffic=False, enabled=False, qualified=True, slc_limit=None):
  """The active personality's acceleration ceiling by speed (its own or Long Planner's profile), else stock."""
  limit = slc_limit if type(slc_limit) in (int, float) and math.isfinite(slc_limit) and 0.1 <= slc_limit <= 3.5 else None
  if qualified is not True or type(traffic) is not bool:
    return None
  if host is None or not enabled:
    return AccelerationProfile(limit=limit)
  from openpilot.starpilot.longitudinal.profile_document import _NATIVE_ACCELERATION_SPEEDS_MS

  speeds = tuple(sorted({0.0, 70.0, *STOCK_ACCEL_BP, *_NATIVE_ACCELERATION_SPEEDS_MS}))
  ceilings = []
  for speed in speeds:
    legacy = host.sample(now_ns, personality, speed, cp, traffic_mode=traffic)
    selected = host.sample_selected(now_ns, personality, speed, cp, traffic_mode=traffic, legacy=legacy)
    value = selected.acceleration_max if selected is not None else None
    if value is None:
      value = legacy.acceleration_max if legacy is not None else None
    if value is None:
      value = interpolate(speed, STOCK_ACCEL_BP, STOCK_ACCEL_MAX)
    ceilings.append(float(value))
  return AccelerationProfile(speeds, tuple(ceilings), limit=limit)


def start_distance(mode, speed, distance, seconds):
  """How far before the sign a change starts in the At the limit, Distance and Time modes."""
  if mode == 'distance':
    return distance
  if mode == 'time':
    return max(0.0, speed) * seconds
  return 0.0


class MappedTiming:
  def __init__(self):
    self.scope = uuid.uuid4().hex
    self.approach = MapApproach()
    self.distance = LowerLimitDistance()
    self.warning = SpeedChangeWarning()
    self.pin = WarningBoundaryPin()
    self.profile = BrakingProfile()
    self.acceleration_profile = AccelerationProfile()
    self.cache = None
    self.higher_cache = None
    self.identity = ''
    self.warning_identity = ''
    self.warning_boundary = BoundaryWarningIdentity()
    self.upcoming = 0.0
    self.remaining = 0.0
    self.trigger = 0.0
    self.eta = math.inf
    self.current = False
    self.warning_event = None
    self.warning_pin = {}
    self.source_key = None
    self.lower = False

  def project(self, observation, map_frame, *, now_ns, now_boot_ns, speed, acceleration, cluster_delta, settings, enabled, target_override=None):
    self.current = False
    previous_identity = self.identity
    self.identity = ''
    self.warning_identity = ''
    self.upcoming = 0.0
    self.eta = math.inf
    if not enabled or observation.kind is not acc.ObservationKind.VALID or observation.candidate is None or map_frame is None:
      # A short gap with no current limit keeps an active approach (and its identity) but gives the map no limit.
      if enabled and self.approach.hold(now_ns / 1e9, speed):
        self.identity = previous_identity
      else:
        self.approach.update(0, 0, 0, '', 0, 0, False)
      return observation
    source = (map_frame.producer_session, map_frame.generation, map_frame.source)
    if self.source_key is not None and source != self.source_key:
      self.approach = MapApproach()
      self.distance = LowerLimitDistance()
      self.cache = None
      self.higher_cache = None
    self.source_key = source
    self.current = True
    current = observation.candidate.speed_mps
    upcoming, distance = map_frame.next_speed_mps, map_frame.next_distance_m
    if (
      not all(math.isfinite(v) for v in (upcoming, distance, speed, acceleration, cluster_delta))
      or not 0 <= upcoming <= 80
      or not -200 <= distance <= 10000
      or not 0 <= speed <= 70
      or not isinstance(map_frame.road_name, str)
      or len(map_frame.road_name) > 512
    ):
      self.approach.update(0, 0, 0, '', 0, 0, False)
      return observation
    if upcoming < 1:
      distance = 0.0
    if distance < 0:
      # A negative boundary must not create a fresh forecast. Existing software
      # set speed remains governed by its acknowledgement and source history.
      return acc.Observation(acc.ObservationKind.UNKNOWN)
    raw_distance = distance
    now = now_ns / 1e9
    custom = settings.custom
    lower = 1 <= upcoming < current
    self.lower = lower
    forecast_valid = isinstance(self.profile, BrakingProfile)
    # A fresh map sample may start a change; Reach by also needs a qualified braking forecast.
    fresh = 0 <= now_boot_ns - map_frame.event_mono_ns <= 250_000_000
    allow_activate = fresh
    if lower:
      distance = self.distance.update(current, upcoming, distance, map_frame.road_name, speed, now, map_frame.event_mono_ns)
      mode = custom.lower_mode
      if mode == 'reach':
        allow_activate = forecast_valid
        target = sd.adjusted_limit(upcoming, settings.offsets)
        remembered = type(target_override) in (int, float) and math.isfinite(target_override) and 8 / 3.6 <= target_override <= 145 / 3.6
        if remembered:
          target = target_override
        # SLC adopts directly into the card-selected speed. Apply its exact
        # rounding; the obsolete separate-cap ego/cluster subtraction does not apply.
        target = slc_target_kph(target or upcoming, settings.metric) / 3.6
        if forecast_valid:
          profile = replace(self.profile, limit=None) if remembered or not custom.limit_braking else self.profile
          key = (target, profile, custom.lower_timing)
          old = self.cache
          near = old is None or distance <= old[1] + speed * (custom.lower_warning_lead + 0.5)
          if old is None or near or now - old[0] >= 0.2 or key != old[2] or abs(speed - old[3]) > 0.25 or abs(acceleration - old[4]) > 0.15:
            trigger = braking_distance(speed, target, acceleration, profile)
            trigger = max(0.0, trigger + speed * custom.lower_timing)
            self.cache = (now, trigger, key, speed, acceleration)
          trigger = self.cache[1]
          if not math.isfinite(trigger):
            trigger = distance + speed * (custom.lower_warning_lead + 0.5)
        else:
          self.cache = None
          trigger = 0.0  # No fresh forecast; an already active approach may continue.
      else:
        self.cache = None
        trigger = start_distance(mode, speed, custom.lower_distance, custom.lower_time)
    else:
      self.distance = LowerLimitDistance()
      self.cache = None
      trigger = 0.0
      mode = custom.higher_mode
      if upcoming > current and mode == 'speed':
        # Speed offset: pass the sign the chosen amount above the current limit (20 -> 30 mph at 5 mph = 25 mph
        # at the sign, never above the new set speed), starting where the acceleration profile gets there in time.
        # Without a qualified acceleration profile it starts at the sign.
        if isinstance(self.acceleration_profile, AccelerationProfile):
          target = sd.adjusted_limit(upcoming, settings.offsets)
          if type(target_override) in (int, float) and math.isfinite(target_override) and 8 / 3.6 <= target_override <= 145 / 3.6:
            target = target_override
          target = slc_target_kph(target or upcoming, settings.metric) / 3.6
          at_sign = min(current + custom.higher_over, target)
          profile = self.acceleration_profile if custom.limit_acceleration else replace(self.acceleration_profile, limit=None)
          key = (at_sign, profile)
          old = self.higher_cache
          if old is None or now - old[0] >= 0.2 or key != old[2] or abs(speed - old[3]) > 0.25 or abs(acceleration - old[4]) > 0.15:
            self.higher_cache = (now, acceleration_distance(speed, at_sign, acceleration, profile), key, speed, acceleration)
          if math.isfinite(self.higher_cache[1]):
            trigger = self.higher_cache[1]
      else:
        self.higher_cache = None
        if upcoming > current:
          trigger = start_distance(mode, speed, custom.higher_distance, custom.higher_time)
    selected = self.approach.update(
      current, upcoming, distance, map_frame.road_name, speed, trigger, True, identity_distance=raw_distance, allow_activate=allow_activate, now=now
    )
    self.remaining, self.trigger = distance, trigger
    self.upcoming = upcoming
    if upcoming >= 1 and abs(current - upcoming) >= 0.1:
      ident = self.scope + repr(source) + ':' + self.approach.identity + ':' + str(upcoming)
      self.identity = hashlib.sha256(ident.encode()).hexdigest()
      key = (
        (map_frame.producer_session, map_frame.source, map_frame.way_id, map_frame.next_way_id, round(current, 3), round(upcoming, 3))
        if map_frame.way_id > 0 and map_frame.next_way_id > 0
        else None
      )
      self.warning_identity = self.warning_boundary.update(self.identity, key=key, now=now, distance=raw_distance, speed=speed)
      self.eta = (distance - trigger) / max(speed, 0.1) if not lower or custom.lower_mode != 'reach' or forecast_valid else math.inf
    if self.approach.active and self.identity:
      return acc.Observation(
        acc.ObservationKind.VALID, acc.Candidate('map', acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value=self.identity), selected)
      )
    return observation

  def warn(self, now_ns, *, eligible, target_change, speed, settings):
    now = now_ns / 1e9
    self.warning_event = self.warning.update(now, self.warning_identity, self.eta, target_change, eligible, settings.custom.warning_lead(self.lower))
    self.warning_pin = self.pin.update(now, self.warning_event, self.warning_identity, self.remaining, speed, eligible)


class ShortMapZone:
  """Skip an intermediate zone only with two fresh, uniquely connected boundaries.

  The provider's facts remain unchanged. This consumer transforms the selected
  target path, retaining the prior zone through the known short interval. Any
  loss of route continuity releases the policy; it cannot invent map authority.
  """

  def __init__(self):
    self.checkpoint = None
    self.active = False

  def project(self, observation, frame, *, now_ns, speed, enabled, allow_start=True):
    self.active = False
    old = self.checkpoint
    fresh = (
      enabled
      and frame is not None
      and observation.kind is acc.ObservationKind.VALID
      and observation.candidate is not None
      and math.isfinite(speed)
      and 0 <= speed <= 70
    )
    if not fresh:
      if old is not None and (not enabled or not 0 <= now_ns - old['seen'] <= 1_000_000_000):
        self.checkpoint = None
      return observation, frame
    source = (frame.producer_session, frame.generation, frame.source)
    current = observation.candidate.speed_mps
    if old is not None:
      continuous = source == old['source'] and 0 <= now_ns - old['seen'] <= 1_000_000_000 and 0 <= now_ns - old['start'] <= 600_000_000_000
      before = (
        abs(current - old['current']) < 0.1
        and abs(frame.next_speed_mps - old['short']) < 0.1
        and frame.next_way_id == old['next_way']
        and frame.following_way_id == old['end_way']
        and abs(frame.following_speed_mps - old['following']) < 0.1
        and 0 <= frame.next_distance_m < frame.next_end_distance_m <= 2000
      )
      inside = (
        abs(current - old['short']) < 0.1
        and abs(frame.next_speed_mps - old['following']) < 0.1
        and frame.next_way_id == old['end_way']
        and 0 <= frame.next_distance_m <= 2000
      )
      remaining = frame.next_end_distance_m if before else frame.next_distance_m
      progress = max(20.0, speed * max(0.0, (now_ns - old['seen']) / 1e9) + 10.0)
      length = frame.next_end_distance_m - frame.next_distance_m if before else old['length']
      still_short = speed > 1.0 and math.isfinite(length) and 0 < length / speed < 5.0
      if (
        not still_short
        or not continuous
        or not (before or inside)
        or not math.isfinite(remaining)
        or not old['remaining'] - progress <= remaining <= old['remaining'] + 20.0
      ):
        self.checkpoint = old = None
    if old is None and allow_start and speed > 1.0:
      length = frame.next_end_distance_m - frame.next_distance_m
      qualified = (
        all(math.isfinite(x) for x in (length, frame.next_distance_m, frame.next_end_distance_m, frame.next_speed_mps, frame.following_speed_mps))
        and 0 <= frame.next_distance_m < frame.next_end_distance_m <= 2000
        and 1 <= frame.next_speed_mps <= 80
        and 1 <= frame.following_speed_mps <= 80
        and abs(current - frame.next_speed_mps) >= 0.1
        and abs(frame.next_speed_mps - frame.following_speed_mps) >= 0.1
        and frame.next_way_id > 0
        and frame.following_way_id > 0
        and frame.next_way_id != frame.following_way_id
        and length / speed < 5.0
      )
      if qualified:
        old = self.checkpoint = {
          "source": source,
          "current": current,
          "short": frame.next_speed_mps,
          "following": frame.following_speed_mps,
          "next_way": frame.next_way_id,
          "end_way": frame.following_way_id,
          "start": now_ns,
          "seen": now_ns,
          "remaining": frame.next_end_distance_m,
          "length": length,
        }
        before = True
    if old is None:
      return observation, frame
    if before:
      old['length'] = frame.next_end_distance_m - frame.next_distance_m
    old['seen'] = now_ns
    old['remaining'] = frame.next_end_distance_m if before else frame.next_distance_m
    self.active = True
    upcoming = old['following'] if abs(old['following'] - old['current']) >= 0.1 else 0.0
    projected = replace(
      frame,
      speed_mps=old['current'],
      next_speed_mps=upcoming,
      next_distance_m=old['remaining'] if upcoming else 0.0,
      next_end_distance_m=0.0,
      following_speed_mps=0.0,
      following_way_id=0,
      next_way_id=old['end_way'] if upcoming else 0,
    )
    selected = replace(observation, candidate=replace(observation.candidate, speed_mps=old['current']))
    return selected, projected
