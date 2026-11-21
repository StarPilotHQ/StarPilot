"""Source-bound turn forecasts distinguish slowing intersections from lane changes."""
import math

from openpilot.cereal import log
from openpilot.starpilot.lateral.auto_lane_change import boottime_ns

MAX_AGE_NS = 150_000_000
CONFIRM_NS = 100_000_000
MAX_SPEED_MPS = 14.


class SignalTurnEvidence:
  def __init__(self):
    self.reset()

  def reset(self):
    self.key = None
    self.since_ns = self.model_ns = self.eof_ns = 0
    self.ready = False

  @staticmethod
  def context(sm, now_ns):
    try:
      car, control, device, plan = (sm[name] for name in ('carState', 'carControl', 'deviceState', 'longitudinalPlan'))
      drive = device.startedMonoTime
      for name, age in (('carState', 100_000_000), ('carControl', 100_000_000),
                        ('deviceState', 2_000_000_000), ('longitudinalPlan', MAX_AGE_NS)):
        stamp, received = sm.logMonoTime[name], int(sm.recv_time[name] * 1e9)
        if (not sm.valid[name] or not sm.alive[name] or not 0 < drive <= stamp <= now_ns <= stamp + age or
            not drive <= received <= now_ns <= received + age):
          return None
      direction = -1 if car.leftBlinker and not car.rightBlinker else 1 if car.rightBlinker and not car.leftBlinker else 0
      if (not device.started or not control.latActive or not car.canValid or car.canTimeout or car.standstill or
          car.steerFaultTemporary or car.steerFaultPermanent or str(car.gearShifter) != 'drive' or not direction or
          car.leftBlindspot and direction == -1 or car.rightBlindspot and direction == 1 or
          not math.isfinite(car.vEgo) or not .1 < car.vEgo < MAX_SPEED_MPS or
          not math.isfinite(car.aEgo) or car.aEgo > -.5 or plan.shouldStop or
          not drive <= plan.modelMonoTime <= sm.logMonoTime['longitudinalPlan']):
        return None
      return int(drive), direction
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return None

  def current(self, sm, now_ns, *, now_boot_ns=None):
    boot = boottime_ns() if now_boot_ns is None else now_boot_ns
    return bool(self.ready and self.key == self.context(sm, now_ns) and
                0 < self.model_ns <= now_ns <= self.model_ns + MAX_AGE_NS and
                0 < self.eof_ns <= boot <= self.eof_ns + MAX_AGE_NS)

  def observe(self, sm, model, *, model_ns, now_ns, now_boot_ns, valid, enabled, clock_skew_ns=0, lane_change_state=None):
    key = self.context(sm, now_ns) if enabled else None
    try:
      if (key is None or not valid or not 0 <= clock_skew_ns <= 1_000_000 or
          not 0 < model_ns <= now_ns <= model_ns + MAX_AGE_NS or
          not 0 < model.timestampEof <= now_boot_ns <= model.timestampEof + MAX_AGE_NS or model.action.shouldStop):
        self.reset()
        return False
      prediction = tuple(model.meta.desirePrediction)
      if len(prediction) != 32 or not all(math.isfinite(x) and 0 <= x <= 1 for x in prediction):
        self.reset()
        return False
      direction = int(log.Desire.turnLeft if key[1] == -1 else log.Desire.turnRight)
      opposite = int(log.Desire.turnRight if key[1] == -1 else log.Desire.turnLeft)
      for offset in range(0, 32, 8):
        if abs(sum(prediction[offset:offset + 8]) - 1.) > .01:
          self.reset()
          return False
      # Forecasts, rather than desireState, avoid reusing an injected desire as evidence.
      if any(prediction[offset + direction] < .6 or prediction[offset + opposite] > .2 or
             prediction[offset + int(log.Desire.laneChangeLeft)] + prediction[offset + int(log.Desire.laneChangeRight)] > .2
             for offset in (0, 8, 16)):
        self.reset()
        return False
      state = model.meta.laneChangeState if lane_change_state is None else lane_change_state
      ongoing = state in (log.LaneChangeState.laneChangeStarting, log.LaneChangeState.laneChangeFinishing)
      if ongoing and not self.ready:
        self.reset()
        return False
      if key != self.key or model_ns < self.model_ns or model_ns > self.model_ns + MAX_AGE_NS:
        self.reset()
        self.key, self.since_ns = key, model_ns
      if model_ns > self.model_ns:
        self.model_ns, self.eof_ns = model_ns, int(model.timestampEof)
        self.ready = model_ns - self.since_ns >= CONFIRM_NS
      return self.current(sm, now_ns, now_boot_ns=now_boot_ns)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      self.reset()
      return False
