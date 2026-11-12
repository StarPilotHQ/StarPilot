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
from opendbc.car.mazda.values import CAR, GEN2_STEER_RELEASE_SPEED_RAW, MazdaSafetyFlags
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

  def step(self, *, standstill, lat_active=True, release_speed=None):
    cc = structs.CarControl()
    cc.latActive = lat_active
    cc.actuators.torque = TORQUE
    cs = SimpleNamespace(out=structs.CarState(standstill=standstill, vEgo=0.0 if standstill else 5.0),
                         cam_lkas={}, acc={}, acc_standstill=standstill,
                         steer_release_speed=standstill if release_speed is None else release_speed)
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


# --- release before the stop -------------------------------------------------------------
# SPEED (0x217) drops from 65-70 straight to 0. Panda sees the 0 first, so releasing on
# CS.out.standstill sent one nonzero frame that panda blocked; with cruise engaged the host
# read the block as a critical AOL fault (controlsMismatch, "TAKE CONTROL IMMEDIATELY").

def test_torque_is_released_below_the_release_speed_while_still_moving():
  rig = Rig(cp())
  assert ramp(rig) != 0
  assert rig.step(standstill=False, release_speed=True) == (0, 0)


def test_release_speed_without_the_aol_policy_keeps_steering():
  rig = Rig(cp(alternative_experience=0))
  before = ramp(rig)
  assert abs(rig.step(standstill=False, release_speed=True)[0]) >= abs(before)


def replay_stop(old_rule=False):
  """Replay a logged approach through the real CarState, the controller and panda's speed hook.

  The host runs one frame behind panda: each 0x249 is built from the CarState of the SPEED
  frame before the one panda has just received. Returns what was sent once panda counted
  the car as stopped. old_rule releases on CarState.standstill only, as before this fix.
  """
  from opendbc.can import CANPacker, CANParser
  from opendbc.car.mazda.carstate import CarState
  packer = CANPacker("mazda_2019")
  safety = libsafety_py.libsafety
  safety.set_safety_hooks(CarParams.SafetyModel.mazda, GEN2)
  safety.init_tests()
  params = cp()
  car_state = CarState(params)
  assert car_state.steer_release_speed is False   # unknown until the first SPEED frame: moving
  rig = Rig(params)
  ramp(rig)
  parsers = {
    Bus.pt: CANParser("mazda_2019", [("SYSTEM_SETTINGS", 0)], 0),
    Bus.body: CANParser("mazda_2019", [], 1),
    Bus.cam: CANParser("mazda_2019", [("SPEED", 0)], 2),
  }
  parsers[Bus.pt].update([0, [packer.make_can_msg("SYSTEM_SETTINGS", 0, {"IMPERIAL_UNIT": 0})]])
  host = None
  sent_while_stopped = []
  # route 00000051--a42c0843d4 at 1136.9 s (66 then 0), with the ramp-down before it
  for raw in [800, 500, 300, 200, 150, 120, 100, 90, 80, 66, 0, 0, 0]:
    speed = packer.make_can_msg("SPEED", 2, {"SPEED": raw * 0.01})
    safety.safety_rx_hook(make_msg(2, 0x217, dat=bytes(speed[1])))
    if host is not None:
      release = host.standstill if old_rule else car_state.steer_release_speed
      sent, _ = rig.step(standstill=host.standstill, release_speed=release)
      if not safety.get_vehicle_moving():
        sent_while_stopped.append(sent)
    parsers[Bus.cam].update([0, [speed]])
    host = car_state.update_gen2(parsers)
  assert sent_while_stopped
  return sent_while_stopped


def test_no_nonzero_frame_follows_panda_seeing_the_stop():
  assert all(sent == 0 for sent in replay_stop())


def test_the_replay_reproduces_the_logged_blocked_frame():
  # Releasing on standstill alone sends one nonzero frame after panda has seen the 0.
  sent = replay_stop(old_rule=True)
  assert sent[0] != 0 and all(x == 0 for x in sent[1:]), sent


def test_release_threshold_has_margin_over_the_lowest_reported_speed():
  # The last nonzero SPEED before a stop was 65-70 in 82 logged stops, so a threshold
  # below that would behave exactly like releasing on the 0.
  assert 70 < GEN2_STEER_RELEASE_SPEED_RAW <= 150
