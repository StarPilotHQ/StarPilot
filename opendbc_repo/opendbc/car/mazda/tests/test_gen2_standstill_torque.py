"""AOL-marked GEN2 releases steering torque in the same frame the car reaches standstill.

Under the AOL safety policy, panda refuses nonzero 0x249 while the car is stopped
(mazda_aol_permission needs vehicle_moving), even though steerAtStandstill is set. The
host used to learn that through the native denial, which takes about 60 ms to come back
as latActive=False. Every frame in that window was refused, so the TI2 saw no 0x249.
Across 13 logged drives a transmit gap of 55 ms or more was followed by a 10 s TI2 0x02
pause in 44 of 45 cases, and gaps under 45 ms were not. Panda accepts a zero command in
any state, so sending zero on the first stopped frame keeps the frames flowing.
"""
from types import SimpleNamespace

from opendbc.car import Bus, structs
from opendbc.car.mazda.tests.test_gen2_acc_controller import CarController, FakeParams
from opendbc.car.mazda.values import CAR, MazdaSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.common import make_msg
from opendbc.safety.tests.libsafety import libsafety_py

GEN2 = int(MazdaSafetyFlags.GEN2)
TORQUE = 0.5


def cp(alternative_experience=32, fingerprint=CAR.MAZDA_3_2019):
  params = structs.CarParams(carFingerprint=fingerprint, brand="mazda", flags=GEN2, pcmCruise=True,
                             transmissionType=structs.CarParams.TransmissionType.automatic,
                             fingerprintSource=structs.CarParams.FingerprintSource.can, steerAtStandstill=True)
  params.alternativeExperience = alternative_experience
  params.init("safetyConfigs", 1)
  params.safetyConfigs[0].safetyModel = CarParams.SafetyModel.mazda
  params.safetyConfigs[0].safetyParam = GEN2
  return params


class Rig:
  def __init__(self, params):
    self.ctl = CarController({Bus.pt: "mazda_2019"}, params)
    self.ctl.learning_projection_observer = None
    self.ctl.params = FakeParams()

  def step(self, *, standstill, lat_active=True):
    cc = structs.CarControl()
    cc.latActive = lat_active
    cc.actuators.torque = TORQUE
    cs = SimpleNamespace(out=structs.CarState(standstill=standstill, vEgo=0.0 if standstill else 5.0),
                         cam_lkas={}, acc={}, acc_standstill=standstill)
    actuators, sends = self.ctl.update(cc.as_reader(), cs, 0)
    dat = next(bytes(d) for a, d, b in sends if a == 0x249 and b == 1)
    return int.from_bytes(dat[:2], "big", signed=True), actuators.torqueOutputCan


def ramp(rig, frames=30):
  for _ in range(frames):
    sent, _ = rig.step(standstill=False)
  return sent


def test_torque_is_released_in_the_first_stopped_frame():
  rig = Rig(cp())
  assert ramp(rig) != 0
  assert rig.step(standstill=True) == (0, 0)
  assert rig.step(standstill=True) == (0, 0)


def test_moving_torque_is_unchanged():
  marked, unmarked = Rig(cp()), Rig(cp(alternative_experience=0))
  for _ in range(30):
    assert marked.step(standstill=False) == unmarked.step(standstill=False)


def test_torque_ramps_up_from_zero_when_moving_again():
  rig = Rig(cp())
  ramp(rig)
  rig.step(standstill=True)
  first, _ = rig.step(standstill=False)
  assert 0 < abs(first) <= rig.ctl.ccp.STEER_DELTA_UP


def test_without_the_aol_policy_steering_at_standstill_is_kept():
  # controls_allowed alone lets panda accept torque while stopped.
  rig = Rig(cp(alternative_experience=0))
  before = ramp(rig)
  assert abs(rig.step(standstill=True)[0]) >= abs(before)


def test_marker_set_after_construction_is_honoured():
  # card.py builds the interface first and marks alternativeExperience afterwards.
  params = cp(alternative_experience=0)
  rig = Rig(params)
  params.alternativeExperience = 32
  ramp(rig)
  assert rig.step(standstill=True)[0] == 0


def test_panda_accepts_the_release_and_refuses_the_old_frame():
  safety = libsafety_py.libsafety
  safety.set_safety_hooks(CarParams.SafetyModel.mazda, GEN2)
  safety.init_tests()
  safety.set_controls_allowed(0)
  frame = lambda torque: make_msg(1, 0x249, dat=torque.to_bytes(2, "big", signed=True) + bytes(6))
  assert not safety.safety_tx_hook(frame(1000))
  assert safety.safety_tx_hook(frame(0))
