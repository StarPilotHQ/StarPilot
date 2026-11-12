"""GEN1 Torque Interceptor (TI1): command limits, faults, and agreement between the car controller and panda.

The controller and panda are run together: every 0x243/0x249 frame the TI1 controller produces over a drive
script (ramp, a held ceiling, a driver override, a violation, recovery) must pass panda's TI1 checks, so the
two can never disagree in a way that drops 0x249 frames (the TI faults on a 100 ms CAN gap).
"""
import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.mazda.carstate import CarState
from opendbc.car.mazda.tests.test_gen2_acc_controller import CarController, FakeParams
from opendbc.car.mazda.torque_interceptor import TiCeilingGuard, violation_is_permanent, violation_name
from opendbc.car.mazda.values import CAR, DBC, TI_STATE, CarControllerParams, MazdaSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.common import make_msg
from opendbc.safety.tests.libsafety import libsafety_py

TI1 = int(MazdaSafetyFlags.GEN1 | MazdaSafetyFlags.TORQUE_INTERCEPTOR)
TI1_NO_FSC = TI1 | int(MazdaSafetyFlags.NO_FSC)
PACKER = CANPacker("mazda_2017")


def cp(flags=TI1, fingerprint=CAR.MAZDA_CX9_2021):
  params = structs.CarParams(carFingerprint=fingerprint, brand="mazda", flags=flags, pcmCruise=True,
                             transmissionType=structs.CarParams.TransmissionType.automatic)
  params.init("safetyConfigs", 1)
  params.safetyConfigs[0].safetyModel = CarParams.SafetyModel.mazda
  params.safetyConfigs[0].safetyParam = flags
  return params


# --- TiCeilingGuard and the violation table ---

def guard():
  ccp = CarControllerParams(cp())
  return TiCeilingGuard(ccp.TI_CEILING_DIP, ccp.TI_CEILING_HOLD_FRAMES, ccp.TI_CEILING_DIP_FRAMES,
                        ccp.TI_CEILING - ccp.TI_CEILING_DIP), ccp


def test_ceiling_hold_is_dipped_before_the_ti_faults():
  g, ccp = guard()
  caps = [g.update(ccp.TI_CEILING) for _ in range(ccp.TI_CEILING_HOLD_FRAMES + ccp.TI_CEILING_DIP_FRAMES)]
  first = caps.index(ccp.TI_CEILING_DIP)
  assert first == ccp.TI_CEILING_HOLD_FRAMES - 1      # 1.2 s, well inside the TI's 1.84 s
  assert caps[first:first + ccp.TI_CEILING_DIP_FRAMES] == [ccp.TI_CEILING_DIP] * ccp.TI_CEILING_DIP_FRAMES
  assert caps[first + ccp.TI_CEILING_DIP_FRAMES] is None
  assert ccp.TI_CEILING - ccp.TI_CEILING_DIP <= ccp.STEER_DELTA_DOWN


def test_any_unchanged_command_is_lowered():
  g, ccp = guard()
  caps = [g.update(-1) for _ in range(ccp.TI_CEILING_HOLD_FRAMES + 1)]
  assert caps[-1] == 0
  g, ccp = guard()
  caps = [g.update(300) for _ in range(ccp.TI_CEILING_HOLD_FRAMES + 1)]
  assert caps[-1] == 300 - (ccp.TI_CEILING - ccp.TI_CEILING_DIP)


def test_changing_or_zero_commands_are_left_alone():
  g, ccp = guard()
  assert all(g.update(300 + (i % 2)) is None for i in range(5 * ccp.TI_CEILING_HOLD_FRAMES))
  assert all(g.update(0) is None for _ in range(5 * ccp.TI_CEILING_HOLD_FRAMES))


def test_violation_names_and_classes():
  assert violation_name(0) == ""
  assert violation_name(0x11) == "0x11 LKAS_STUK (class 2)"
  assert violation_name(0x5a) == "0x5a (unknown)"
  assert violation_is_permanent(0x19)          # WDT_ERR, class 1
  assert not violation_is_permanent(0x12)      # the power-up CAN_TMO, class 3
  assert not violation_is_permanent(0x11)
  assert not violation_is_permanent(0x5a)      # unknown: recoverable alert, lateral still withheld


# --- the car controller and panda together ---

class Loop:
  """The car controller and panda, with the real CarState and parsers in between, one 100 Hz frame per step."""
  def __init__(self, flags):
    self.flags = flags
    self.CP = cp(flags)
    self.ctl = CarController({Bus.pt: "mazda_2017"}, self.CP)
    self.ctl.learning_projection_observer = None
    self.ctl.params = FakeParams()
    self.cs = CarState(self.CP)
    self.parsers = CarState.get_can_parsers(self.CP)
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.mazda, flags)
    self.safety.init_tests()
    self.safety.set_controls_allowed(1)
    self.now = 0            # us
    self.sent = []          # (stock, ti, host ti_lkas_allowed) per frame

  def step(self, torque, ti_state=TI_STATE.RUN, viol=0, feedback=True, version=1, ramp_down=0):
    self.now += 10_000
    self.safety.set_timer(self.now)
    frames = []
    if feedback:
      fb = PACKER.make_can_msg("TI_FEEDBACK", 1, {"TI_TORQUE_SENSOR": 0, "STATE": ti_state, "VIOL": viol,
                                                  "VERSION_NUMBER": version, "RAMP_DOWN": ramp_down})
      assert self.safety.safety_rx_hook(make_msg(1, 0x24A, dat=bytes(fb[1])))
      frames.append(fb)
    for parser in self.parsers.values():
      parser.update([self.now * 1000, frames])
    self.cs.out = self.cs.update(self.parsers)

    cc = structs.CarControl()
    cc.latActive = True
    cc.actuators.torque = torque
    _, sends = self.ctl.update(cc.as_reader(), self.cs, 0)
    stock = ti = None
    for addr, dat, bus in sends:
      if (addr, bus) in ((0x243, 0), (0x249, 1)):
        assert self.safety.safety_tx_hook(make_msg(bus, addr, dat=bytes(dat))), f"panda refused 0x{addr:x} at {self.now} us"
      if (addr, bus) == (0x243, 0):
        stock = ((dat[0] & 0x0F) << 8 | dat[1]) - 2048
      if (addr, bus) == (0x249, 1):
        ti = ((dat[0] & 0x0F) << 8 | dat[1]) - 2048
    assert ti is not None
    self.sent.append((stock, ti, self.cs.ti_lkas_allowed))
    return stock, ti


def seg(frames, torque, **feedback):
  return [(torque, feedback)] * frames


DRIVE = (seg(400, 1.0) +                                   # ramp to and hold the ceiling for 4 s
         seg(50, 1.0, ti_state=TI_STATE.DRIVER_OVER) +     # driver override
         seg(150, -0.4) +                                  # back, reversing direction
         seg(50, -0.4, ti_state=TI_STATE.OFF, viol=0x11) + # a violation
         seg(100, 0.3) +                                   # recovery
         seg(30, 0.3, feedback=False) +                    # 300 ms without TI_FEEDBACK
         seg(100, 0.3) +
         seg(30, 0.3, version=2, ramp_down=2) +            # ramp-down, a nonzero byte other than 1
         seg(30, 0.3, version=1, ramp_down=2) +            # version 1: the byte means nothing
         seg(100, 0.5))


@pytest.mark.parametrize("flags", [TI1, TI1_NO_FSC], ids=["fsc", "no_fsc"])
def test_panda_accepts_every_frame_of_a_drive(flags):
  loop = Loop(flags)
  for torque, feedback in DRIVE:
    loop.step(torque, **feedback)   # asserts panda accepts every 0x243 and 0x249

  ccp = loop.ctl.ccp
  tis = [ti for _, ti, _ in loop.sent]
  assert max(abs(t) for t in tis) == ccp.TI_CEILING
  # never one nonzero command held for as long as the TI allows (1.84 s)
  run = longest = 0
  for prev, cur in zip(tis, tis[1:], strict=False):
    run = run + 1 if cur != 0 and cur == prev else 0
    longest = max(longest, run)
  assert longest < ccp.TI_CEILING_HOLD_FRAMES

  prev_ti = 0
  for stock, ti, allowed in loop.sent:
    if not allowed:
      assert ti == 0
    if flags == TI1 and ti != 0:
      assert (ti > 0) == (stock > 0) and abs(ti) <= abs(stock)
      base = abs(prev_ti) if prev_ti != 0 and (prev_ti > 0) == (ti > 0) else 0
      assert abs(ti) <= base + ccp.STEER_DELTA_UP
    prev_ti = ti


def test_host_stops_before_panda_when_feedback_goes_quiet():
  loop = Loop(TI1)
  for _ in range(100):
    loop.step(0.3)
  quiet = [loop.step(0.3, feedback=False)[1] for _ in range(30)]
  # nonzero while under the host's 150 ms, zero from then on (panda refuses at 200 ms)
  assert all(t != 0 for t in quiet[:14]) and all(t == 0 for t in quiet[15:])


@pytest.mark.parametrize("flags", [TI1, TI1_NO_FSC], ids=["fsc", "no_fsc"])
def test_the_ti_ramps_from_zero_after_an_override(flags):
  loop = Loop(flags)
  for _ in range(100):
    loop.step(0.5)
  loop.step(0.5, TI_STATE.DRIVER_OVER)
  stock, ti = loop.step(0.5)
  assert 0 < ti <= loop.ctl.ccp.STEER_DELTA_UP
  if flags == TI1:
    assert stock > ti   # the stock channel kept its command


def test_ti2_has_no_ti1_guard():
  for flags in (MazdaSafetyFlags.GEN2, MazdaSafetyFlags.GEN3):
    ctl = CarController({Bus.pt: "mazda_2019"}, cp(int(flags), CAR.MAZDA_3_2019))
    assert ctl.ti1_guard is None


# --- car state ---

class Feed:
  """A TI1 CarState fed through its real parsers."""
  def __init__(self):
    self.CP = cp()
    self.cs = CarState(self.CP)
    self.parsers = CarState.get_can_parsers(self.CP)
    self.packer = CANPacker(DBC[self.CP.carFingerprint][Bus.pt])
    _ = self.parsers[Bus.pt].vl["STEER_RATE"]  # CANParser registers a message on its first read
    self.ns = 0

  def update(self, dt_ms=10, feedback=True, lkas_block=0, **values):
    self.ns += dt_ms * 1_000_000
    fb = {"STATE": TI_STATE.RUN, "VIOL": 0, "RAMP_DOWN": 0, "VERSION_NUMBER": 1}
    fb.update(values)
    frames = [self.packer.make_can_msg("STEER_RATE", 0, {"LKAS_BLOCK": lkas_block})]
    if feedback:
      frames.append(self.packer.make_can_msg("TI_FEEDBACK", 1, fb))
    for parser in self.parsers.values():
      parser.update([self.ns, frames])
    return self.cs.update(self.parsers)


def carstate_with(ti_state, viol, ramp_down=0, version=1):
  feed = Feed()
  ret = feed.update(STATE=ti_state, VIOL=viol, RAMP_DOWN=ramp_down, VERSION_NUMBER=version)
  return feed.cs, ret


@pytest.mark.parametrize("ti_state, viol, allowed, temporary, permanent", [
  (TI_STATE.RUN, 0, True, False, False),
  (TI_STATE.DRIVER_OVER, 0, False, False, False),   # the driver steering is not a fault
  (TI_STATE.OFF, 0x11, False, True, False),         # class 2, clears by itself
  (TI_STATE.OFF, 0x12, False, True, False),         # the power-up frame
  (TI_STATE.RUN, 0x33, False, True, False),         # a code while still in RUN
  (TI_STATE.OFF, 0x19, False, False, True),         # class 1: needs a power cycle
  (TI_STATE.DISCOVER, 0, False, True, False),       # start-up
])
def test_ti1_faults(ti_state, viol, allowed, temporary, permanent):
  car_state, ret = carstate_with(ti_state, viol)
  assert car_state.ti_lkas_allowed == allowed
  assert ret.steerFaultTemporary == temporary
  assert ret.steerFaultPermanent == permanent


def test_driver_override_never_alerts_even_with_the_eps_lockout():
  feed = Feed()
  ret = feed.update(lkas_block=1, STATE=TI_STATE.DRIVER_OVER)
  assert not ret.steerFaultTemporary and not ret.steerFaultPermanent


def test_ti1_ramp_down_matches_panda():
  # version > 1 and any nonzero byte, recomputed on every frame
  assert not carstate_with(TI_STATE.RUN, 0, ramp_down=1, version=2)[0].ti_lkas_allowed
  assert not carstate_with(TI_STATE.RUN, 0, ramp_down=2, version=2)[0].ti_lkas_allowed
  assert carstate_with(TI_STATE.RUN, 0, ramp_down=1, version=1)[0].ti_lkas_allowed
  feed = Feed()
  feed.update(RAMP_DOWN=1, VERSION_NUMBER=2)
  assert not feed.cs.ti_lkas_allowed
  feed.update(RAMP_DOWN=1, VERSION_NUMBER=1)
  assert feed.cs.ti_lkas_allowed


def test_ti1_stale_feedback():
  feed = Feed()
  for _ in range(3):
    feed.update()
  assert feed.cs.ti_lkas_allowed
  ret = feed.update(dt_ms=150, feedback=False)
  assert feed.cs.ti_lkas_allowed and not ret.steerFaultTemporary
  ret = feed.update(dt_ms=1, feedback=False)
  assert not feed.cs.ti_lkas_allowed and ret.steerFaultTemporary
  feed.update()
  assert feed.cs.ti_lkas_allowed


def test_ti1_no_feedback_yet():
  feed = Feed()
  ret = feed.update(feedback=False)
  assert not feed.cs.ti_lkas_allowed and ret.steerFaultTemporary


def test_ti1_ignores_the_camera_lane_keep_state():
  # No CAM_LANEINFO is fed, so LANE_LINES reads 0: the stock lane keep switched off. A TI car must not report it.
  car_state, ret = carstate_with(TI_STATE.RUN, 0)
  assert not ret.invalidLkasSetting
  assert not car_state.lkas_disabled
