"""A TI2 0x02 pause must not be judged a native rejection by the AOL host.

During a pause, and until panda's recovery frames have arrived, panda withholds
lateral permission while keeping its AOL token. The host must read that denial
as a temporary restriction (retain_on_native_denial), or it drops its arming and
the driver has to press MAIN again: the "press ACC twice" symptom. This drives a
real GEN2 CarState through a pause and asks the real host predicate at each step.
The car is moving in Drive so that no other temporary restriction masks the result.
"""
from types import SimpleNamespace

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.mazda.carstate import CarState
from opendbc.car.mazda.values import CAR, DBC, TI2_HEALTH, MazdaSafetyFlags
from openpilot.starpilot.aol.vehicle import native_latch_rejected

GEN2 = int(MazdaSafetyFlags.GEN2)
MAZDA_MODEL = int(structs.CarParams.SafetyModel.mazda)
FRAME_NS = 10_000_000
ALL_02 = (2, 2, 2, 2)
CLEAN = (0, 0, 0, 0)


def marked_gen2_cp():
  cp = structs.CarParams(carFingerprint=CAR.MAZDA_3_2019, brand="mazda", flags=GEN2, pcmCruise=True,
                         openpilotLongitudinalControl=False, passive=False, notCar=False, dashcamOnly=False,
                         minSteerSpeed=0.0, transmissionType=structs.CarParams.TransmissionType.automatic,
                         fingerprintSource=structs.CarParams.FingerprintSource.can)
  cp.alternativeExperience = 32
  cp.init("safetyConfigs", 1)
  cp.safetyConfigs[0].safetyModel = MAZDA_MODEL
  cp.safetyConfigs[0].safetyParam = GEN2
  return cp


class Car:
  def __init__(self):
    self.cp = marked_gen2_cp()
    self.car_state = CarState(self.cp)
    self.parsers = CarState.get_can_parsers(self.cp)
    self.packer = CANPacker(DBC[self.cp.carFingerprint][Bus.pt])
    self.t = 1_000_000_000

  def step(self, viol=CLEAN, n=1):
    for _ in range(n):
      self.t += FRAME_NS
      values = {f"CPU_{i}_VIOL": viol[i] for i in range(4)}
      values.update({f"CPU_{i}_STATE": 3 for i in range(4)})
      frame = self.packer.make_can_msg("TI_FEEDBACK", 1, values)
      for parser in self.parsers.values():
        parser.update([self.t, [frame]])
      cs = self.car_state.update_gen2(self.parsers)
    cs.canValid = True
    cs.canTimeout = False
    cs.gearShifter = structs.CarState.GearShifter.drive
    cs.standstill = False
    cs.vEgo = 15.0
    return cs

  def denial_rejected(self, cs):
    native = SimpleNamespace(safetyModel=MAZDA_MODEL, safetyParam=GEN2, requestedLateral=True, lateralAllowed=False)
    return native_latch_rejected(self.cp, native, state=cs)


def test_denial_without_a_pause_is_a_rejection():
  # Control: with healthy TI2 and nothing else restricting, a denial does disarm.
  car = Car()
  assert car.denial_rejected(car.step(n=TI2_HEALTH.RECOVERY_FRAMES))


def test_pause_recovery_and_grace_are_never_a_rejection():
  car = Car()
  car.step(n=TI2_HEALTH.RECOVERY_FRAMES)
  for frame in range(200):
    assert not car.denial_rejected(car.step(ALL_02)), f"pause frame {frame}"
  # Panda still withholds lateral for its recovery frames, and the host may judge
  # a denial sampled then for up to SAFETY_MAX_AGE_NS afterwards.
  for frame in range(TI2_HEALTH.RECOVERY_FRAMES + TI2_HEALTH.RELEASE_GRACE_NS // FRAME_NS - 1):
    assert not car.denial_rejected(car.step()), f"recovery/grace frame {frame}"
  # Once the grace has passed, a denial is real again.
  assert car.denial_rejected(car.step(n=2))
