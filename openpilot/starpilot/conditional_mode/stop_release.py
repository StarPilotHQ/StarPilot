"""Corroborated lead departure clears only an existing CEM model hold."""
import math

from opendbc.car import structs
from openpilot.selfdrive.controls.radard import get_RadarState_from_vision
from openpilot.starpilot.model_geometry import STANDSTILL_RELEASE_SPEED_MPS
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.starpilot.conditional_mode.projection import _standstill_path_extent
from openpilot.starpilot.conditional_mode.stop import FROZEN_RAW_STOP_DISTANCE_M, STOP_MODEL_RELEASE_MARGIN_M

SOURCES = ('carState', 'carControl', 'controlsState', 'selfdriveState', 'radarState', 'modelV2')
MAX_AGE_NS = 150_000_000
CLEAR_DWELL_NS = 500_000_000


def _supported(cp):
  try:
    return bool(cp.openpilotLongitudinalControl and not (cp.passive or cp.dashcamOnly or cp.notCar))
  except (AttributeError, TypeError, ValueError):
    return False


def owner_for(cp):
  return StoppedLeadRelease() if _supported(cp) else None


class StoppedLeadRelease:
  def __init__(self):
    self.reset()

  def reset(self):
    self.key = None
    self.since_ns = self.last_ns = self.model_ns = 0

  def sample(self, sm, cp, planner, detector, *, now_ns, drive_id):
    try:
      car, model, control = sm['carState'], sm['modelV2'], sm['carControl']
      if (not _supported(cp) or type(now_ns) is not int or type(drive_id) is not int or not 0 < drive_id < now_ns or
          not detector.committed or not detector.standstill_committed or detector.standstill_reason == 'sign' or
          not all(sm.valid[name] and sm.alive[name] and drive_id < sm.logMonoTime[name] <= now_ns and
                  now_ns - sm.logMonoTime[name] <= MAX_AGE_NS and
                  drive_id < int(sm.recv_time[name] * 1e9) <= now_ns and
                  now_ns - int(sm.recv_time[name] * 1e9) <= MAX_AGE_NS for name in SOURCES) or
          not car.canValid or car.canTimeout or car.vehicleSensorsInvalid or car.accFaulted or
          car.doorOpen or car.seatbeltUnlatched or car.regenBraking or
          not car.standstill or car.vEgoRaw != 0. or car.gearShifter != structs.CarState.GearShifter.drive or
          not math.isfinite(car.vEgo) or abs(car.vEgo) > STANDSTILL_RELEASE_SPEED_MPS or
          car.gasPressed or car.brakePressed or not control.enabled or not control.longActive or
          not sm['selfdriveState'].enabled or sm['controlsState'].forceDecel or
          planner.mpc.solution_status != 0 or type(planner.output_should_stop) is not bool or
          not math.isfinite(planner.output_a_target) or
          planner.force_stop_plan.forcing or planner.force_stop_plan.should_stop or
          model.action.shouldStop or not math.isfinite(model.action.desiredAcceleration) or model.action.desiredAcceleration < .3):
        self.reset()
        return False
      extent = _standstill_path_extent(model)
      if extent is None or extent < max(FROZEN_RAW_STOP_DISTANCE_M, detector.commit_distance_m) + STOP_MODEL_RELEASE_MARGIN_M:
        self.reset()
        return False
      velocities, times = tuple(model.velocity.x), tuple(ModelConstants.T_IDXS)
      if len(velocities) != 33 or len(times) != 33 or not all(math.isfinite(v) and -.05 <= v <= 100. for v in velocities):
        self.reset()
        return False
      tail = [v for v, t in zip(velocities, times, strict=True) if t >= 2.]
      if not tail or min(tail) < .3 or tail[-1] < 5. or any(b < a for a, b in zip(tail, tail[1:], strict=False)):
        self.reset()
        return False
      leads = [(slot, lead) for slot, lead in enumerate((sm['radarState'].leadOne, sm['radarState'].leadTwo)) if lead.present]
      if not leads or any(lead.radar and lead.radarTrackId < 0 or not all(math.isfinite(v) for v in
          (lead.dRel, lead.vLead, lead.aLeadK, lead.yRel, lead.modelProb)) or
          not 6.3 <= lead.dRel <= 18. or lead.vLead < .6 or lead.aLeadK < -.15 or
          abs(lead.yRel) > 1. or lead.modelProb < .95 for _, lead in leads):
        self.reset()
        return False
      for slot, lead in leads:
        if not lead.radar:
          if lead.radarTrackId != -1:
            self.reset()
            return False
          source = model.leadsV3[slot]
          projected = get_RadarState_from_vision(source, car.vEgo, model.velocity.x[0], source.prob)
          if (source.prob < .95 or not math.isfinite(source.prob) or
              any(not math.isfinite(projected[field]) or abs(projected[field] - getattr(lead, field)) > .5
                  for field in ('dRel', 'yRel', 'vLead', 'aLeadK'))):
            self.reset()
            return False
      key = (drive_id, cp.carFingerprint, tuple((str(config.safetyModel), config.safetyParam) for config in cp.safetyConfigs),
             tuple((slot, bool(lead.radar), lead.radarTrackId if lead.radar else -1) for slot, lead in leads))
      stamp = int(sm.logMonoTime['modelV2'])
      if (key != self.key or not self.last_ns < now_ns <= self.last_ns + MAX_AGE_NS or stamp <= self.model_ns):
        self.reset()
        # Unknown derived geometry cannot establish a new light-hold proof.
        if detector.standstill_reason != 'light':
          return False
        self.key, self.since_ns = key, now_ns
      self.last_ns, self.model_ns = now_ns, stamp
      # Confirm the scene while MPC settles; its current braking/stop decision
      # remains an unconditional release veto. No target acceleration is raised.
      return bool(now_ns - self.since_ns >= CLEAR_DWELL_NS and
                  detector.standstill_reason == 'light' and not planner.output_should_stop and planner.output_a_target > .1)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError, RuntimeError):
      self.reset()
      return False
