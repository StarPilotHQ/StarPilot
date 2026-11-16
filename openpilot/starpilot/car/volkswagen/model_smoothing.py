"""Restore the manual Golf's factory PID/PopV2 target and timing contract."""
import math

from opendbc.car import structs
from opendbc.car.volkswagen.values import CAR, VolkswagenFlags
from openpilot.starpilot.models.receipt import process_start_ticks, read_receipt
from openpilot.starpilot.models.status import ModelLoad, ModelVariant

POPV2_SHA256 = "e147dd4f0d7be4e7931ebc2b07eb28a0eda08a7b26e562188d53e22efc340e58"


def supported(cp):
  if (cp.brand != 'volkswagen' or cp.carFingerprint != CAR.VOLKSWAGEN_GOLF_MK7 or
      cp.transmissionType != structs.CarParams.TransmissionType.manual or
      not math.isclose(cp.steerActuatorDelay, .1, rel_tol=1e-6) or
      not math.isclose(cp.minSteerSpeed, .4, rel_tol=1e-6) or cp.passive or cp.dashcamOnly or cp.notCar or
      cp.flags & (VolkswagenFlags.PQ | VolkswagenFlags.MLB | VolkswagenFlags.MEB) or
      cp.steerControlType != structs.CarParams.SteerControlType.torque or cp.lateralTuning.which() != 'pid'):
    return False
  owners = [c for c in cp.safetyConfigs if c.safetyModel not in
            (structs.CarParams.SafetyModel.silent, structs.CarParams.SafetyModel.noOutput)]
  if len(owners) != 1 or owners[0].safetyModel != structs.CarParams.SafetyModel.volkswagen or owners[0].safetyParam not in (0, 1, 8, 9, 24, 25, 40, 41):
    return False
  pid = cp.lateralTuning.pid
  return (list(pid.kpBP) == [0.] and list(pid.kiBP) == [0.] and len(pid.kpV) == len(pid.kiV) == 1 and
          all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9) for a, b in
              ((pid.kpV[0], .6), (pid.kiV[0], .2), (pid.kf, .00006))))


def seconds(cp, load):
  return .1 if (supported(cp) and isinstance(load, ModelLoad) and load.model_id == 'pop223' and
                load.variant == ModelVariant.SMALL and load.artifact_sha256 == POPV2_SHA256 and
                load.fallback_reason is None) else 0.


class ControlSmoothing:
  def __init__(self, cp):
    self.cp = cp
    self.supported = supported(cp)
    self.load: ModelLoad | None = None
    self.next_read_ns = 0
    self.last_ns = 0

  def seconds(self, model_ns, now_ns):
    if not self.supported:
      return 0.
    if now_ns < self.last_ns:
      self.load = None
      self.next_read_ns = 0
    self.last_ns = now_ns
    if now_ns >= self.next_read_ns:
      self.next_read_ns = now_ns + 1_000_000_000
      self.load = read_receipt()
      if self.load is not None and process_start_ticks(self.load.pid) != self.load.process_start_ticks:
        self.load = None
    if self.load is None or model_ns <= 0 or not 0 < self.load.loaded_mono_ns <= now_ns:
      return 0.
    return seconds(self.cp, self.load)
