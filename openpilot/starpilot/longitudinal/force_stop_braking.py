"""Distance braking under the existing qualified Ioniq stop/hold intent."""

import math

from openpilot.starpilot.longitudinal.ioniq6_start import eligible as ioniq6_eligible
from opendbc.car.hyundai.canfd_stock_aol import KONA_IDLE_AOL_WORD, kona_idle_handoff_profile
from opendbc.car.hyundai.ioniq5pe_long_profile import qualified as ioniq5pe_qualified

MAX_AGE_NS = 150_000_000
STOP_RESERVE_M = .5
MIN_ACCEL = -3.5


def kona_eligible(cp):
  return (kona_idle_handoff_profile(cp) and cp.alternativeExperience == 32 and
          cp.safetyConfigs[0].safetyParam == KONA_IDLE_AOL_WORD and
          math.isfinite(cp.longitudinalActuatorDelay) and 0.0 < cp.longitudinalActuatorDelay <= 2.0)


def eligible(cp):
  try:
    if cp.passive or cp.dashcamOnly or cp.notCar:
      return False
    return ioniq6_eligible(cp) or ioniq5pe_qualified(cp) or kona_eligible(cp)
  except (AttributeError, IndexError, TypeError, ValueError, RuntimeError):
    return False


def distance_target(speed, acceleration, distance, delay, *, existing_brake=False):
  if (not all(math.isfinite(x) for x in (speed, acceleration, distance, delay)) or
      speed < 0 or distance < 0 or not 0 <= delay <= 2):
    return MIN_ACCEL
  realized_accel = max(acceleration, 0.)
  if existing_brake and acceleration < 0. and delay > 0.:
    realized_accel = max(acceleration, -speed / delay)
  remaining = distance - speed * delay - .5 * realized_accel * delay**2 - STOP_RESERVE_M
  future_speed = speed + realized_accel * delay
  return max(MIN_ACCEL, -future_speed**2 / (2 * max(remaining, .01)))


def current_braking(sm, now_ns):
  try:
    device, plan, stop = (sm[name] for name in ('deviceState', 'longitudinalPlan', 'starpilotLongitudinalPlan'))
    drive = int(device.startedMonoTime)
    if not device.started or not 0 < drive < now_ns or not plan.shouldStop:
      return False
    for name in ('carState', 'deviceState', 'longitudinalPlan', 'starpilotLongitudinalPlan'):
      stamp, receipt = int(sm.logMonoTime[name]), int(sm.recv_time[name] * 1e9)
      maximum = 1_250_000_000 if name == 'deviceState' else MAX_AGE_NS
      if (not sm.seen[name] or not sm.valid[name] or not sm.alive[name] or
          not drive < stamp <= receipt <= now_ns or now_ns - stamp > maximum):
        return False
    return bool(stop.version == 1 and stop.forceStopBraking and stop.driveStartMonoTime == drive and
                drive < stop.forceStopEpisodeMonoTime <= stop.modelMonoTime <= stop.sourcePlanMonoTime <= now_ns and
                stop.sourcePlanMonoTime == sm.logMonoTime['longitudinalPlan'] and
                stop.sourcePlanMonoTime == sm.logMonoTime['starpilotLongitudinalPlan'] and
                stop.modelMonoTime == plan.modelMonoTime and now_ns - stop.modelMonoTime <= MAX_AGE_NS and
                sm['carState'].canValid and not sm['carState'].canTimeout and
                not sm['carState'].brakePressed and not sm['carState'].gasPressed)
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    return False
