"""GEN2 CarController.update tests for the 0x220 contract (safety plan 5.3).

These drive the real controller, not the CAN helpers, and offer every emitted
ACC frame to the native safety model holding the same stock reference. A
controller that relied on the panda routinely rejecting its frames fails here
rather than on the car.
"""
import itertools
import sys
import types
from types import SimpleNamespace

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.mazda.values import MazdaSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.common import CANPackerSafety, make_msg
from opendbc.safety.tests.libsafety import libsafety_py

LongCtrlState = structs.CarControl.Actuators.LongControlState
ACC_MIN, ACC_MAX = 1300, 2400


class FakeParams:
  """The controller only reads BlendedACC from Params."""
  blended = False

  def __init__(self, *args, **kwargs):
    pass

  def get_bool(self, key):
    return self.blended if key == "BlendedACC" else False


def _controller_class():
  try:
    from opendbc.car.mazda.carcontroller import CarController
  except OSError:
    # Host checkouts without the compiled openpilot params library.
    stub = types.ModuleType("openpilot.common.params")
    stub.Params = FakeParams
    sys.modules["openpilot.common.params"] = stub
    try:
      from opendbc.car.mazda.carcontroller import CarController
    finally:
      del sys.modules["openpilot.common.params"]
  return CarController


CarController = _controller_class()
PACKER = CANPacker("mazda_2019")
SAFETY_PACKER = CANPackerSafety("mazda_2019")


def accel_cmd(dat):
  return ((dat[2] & 1) << 11) | (dat[3] << 3) | (dat[4] >> 5)


class Rig:
  """One controller plus the native model, advanced in 10 ms controller frames."""

  def __init__(self, op_long=True, blended=False, experimental=False, stock_accel=1995, acc_enabled=1):
    self.cp = structs.CarParams(brand="mazda", carFingerprint="MAZDA_3_2019", flags=int(MazdaSafetyFlags.GEN2),
                                openpilotLongitudinalControl=op_long)
    self.ctl = CarController({Bus.pt: "mazda_2019"}, self.cp)
    self.ctl.learning_projection_observer = None
    self.ctl.params = FakeParams()
    self.ctl.params.blended = blended
    self.ctl.experimental_mode = experimental
    self.stock = {"ACCEL_CMD": stock_accel, "ACC_ENABLED": acc_enabled, "ACC_ENABLED_2": acc_enabled,
                  "NEW_SIGNAL_1": 1, "NEW_SIGNAL_4": 0}
    self.stock_dat = None
    self.parser = CANParser("mazda_2019", [("ACC", 0)], 0)
    self.parse_ns = 0
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.mazda, int(MazdaSafetyFlags.GEN2))
    self.safety.init_tests()
    self.now = 1_000_000
    self.safety.set_timer(self.now)
    for _ in range(10):
      self._ti()
    self.outputs = []

  def _ti(self):
    self.safety.safety_rx_hook(SAFETY_PACKER.make_can_msg_safety("TI_FEEDBACK", 1, {
      "CPU_0_STATE": 3, "CPU_1_STATE": 3, "CPU_2_STATE": 3, "CPU_3_STATE": 3}))

  def step(self, long_active, accel=0.0, resume=False, speed_raw=0, gas=False,
           lcs=LongCtrlState.off, **stock_changes):
    self.now += 10_000
    self.safety.set_timer(self.now)
    self._ti()
    self.safety.safety_rx_hook(SAFETY_PACKER.make_can_msg_safety("SPEED", 2, {"SPEED": speed_raw * 0.01}))
    self.safety.safety_rx_hook(SAFETY_PACKER.make_can_msg_safety("ENGINE_DATA", 2, {"PEDAL_GAS": int(gas)}))
    self.stock.update(stock_changes)
    if self.ctl.frame % 2 == 0 or stock_changes or self.stock_dat is None:
      # 50 Hz stock frame with a rolling byte-0 value, received before the host copies it
      self.stock["NEW_SIGNAL_4"] = (self.stock["NEW_SIGNAL_4"] + 1) % 16
      self.stock_dat = bytes(PACKER.make_can_msg("ACC", 0, self.stock)[1])
      self.safety.safety_rx_hook(make_msg(0, 0x220, dat=self.stock_dat))
      self.parse_ns += 20_000_000
      self.parser.update([(self.parse_ns, [(0x220, self.stock_dat, 0)])])
    self.safety.set_controls_allowed(int(long_active))

    cc = structs.CarControl()
    cc.enabled = long_active
    cc.longActive = long_active
    cc.actuators.accel = accel
    cc.actuators.longControlState = lcs
    cc.cruiseControl.resume = resume
    speed_ms = speed_raw * 0.01 * CV.KPH_TO_MS
    cs = SimpleNamespace(out=structs.CarState(standstill=speed_ms < 0.1, gasPressed=gas, vEgo=speed_ms),
                         cam_lkas={}, acc=dict(self.parser.vl["ACC"]), acc_standstill=speed_raw <= 10)
    _, sends = self.ctl.update(cc.as_reader(), cs, 0)
    dat = next((bytes(d) for a, d, b in sends if a == 0x220 and b == 2), None)
    accepted = None if dat is None else self.safety.safety_tx_hook(make_msg(2, 0x220, dat=dat))
    self.outputs.append((dat, accepted))
    return dat, accepted


def assert_frame(rig, dat, accepted, synthesized):
  assert accepted, (dat.hex(), rig.stock_dat.hex())
  if synthesized:
    assert ACC_MIN <= accel_cmd(dat) <= ACC_MAX, dat.hex()
  else:
    assert dat == rig.stock_dat, (dat.hex(), rig.stock_dat.hex())


def test_transition_matrix():
  # Round-3 probe made persistent: both send parities, one to three inactive
  # frames, old/new resume requests, OP-long capability, moving/stopped and
  # BlendedACC, with an out-of-envelope planner request while active.
  scenarios = 0
  for parity, old_req, gap, new_req, op_long, stopped, blended in itertools.product(
      (0, 1), (False, True), (1, 2, 3), (False, True), (False, True), (False, True), (False, True)):
    scenarios += 1
    rig = Rig(op_long=op_long, blended=blended)
    speed = 0 if stopped else 500
    rig.step(False, speed_raw=speed)
    for _ in range(60):  # past the 0.5 s hold delay
      rig.step(True, accel=3.0, resume=old_req, speed_raw=speed)
    if parity:
      rig.step(True, accel=3.0, resume=old_req, speed_raw=speed)
    for _ in range(gap):
      dat, ok = rig.step(False, speed_raw=speed)
      if dat is not None:
        assert_frame(rig, dat, ok, synthesized=False)
    for _ in range(2):
      dat, ok = rig.step(True, accel=-9.0, resume=new_req, speed_raw=speed)
      if dat is not None:
        assert_frame(rig, dat, ok, synthesized=op_long)
        if not new_req:
          assert not dat[6] & 0x40, ("stale RESUME", parity, old_req, gap, op_long, stopped, blended)
        assert not dat[6] & 0x02 or stopped, "HOLD while moving"
    for dat, ok in rig.outputs:
      if dat is not None:
        assert ok, dat.hex()
      if dat is not None and not op_long:
        assert dat[6] & 0x42 == 0 and accel_cmd(dat) == 1995, dat.hex()
  assert scenarios == 192


def test_accel_is_clamped_to_the_native_envelope():
  for accel, expected in ((-10.0, ACC_MIN), (-3.6, ACC_MIN), (-3.5, ACC_MIN), (-1.0, 1800), (0.0, 2000),
                          (1.5, 2300), (2.0, ACC_MAX), (2.1, ACC_MAX), (10.0, ACC_MAX)):
    rig = Rig()
    rig.step(False, speed_raw=500)
    for _ in range(2):
      dat, ok = rig.step(True, accel=accel, speed_raw=500)
    assert ok and accel_cmd(dat) == expected, (accel, accel_cmd(dat))


def test_hold_only_at_native_standstill():
  # Raw SPEED 11..35 is standstill for CarState (< 0.1 m/s) but moving for the
  # panda, so no HOLD may be emitted there however long the car crawls.
  for speed_raw, expect_hold in ((11, False), (35, False), (10, True), (0, True)):
    rig = Rig()
    rig.step(False, speed_raw=speed_raw)
    holds = []
    for _ in range(120):
      dat, ok = rig.step(True, accel=-0.5, speed_raw=speed_raw, lcs=LongCtrlState.stopping)
      if dat is not None:
        assert ok, (speed_raw, dat.hex())
        holds.append(bool(dat[6] & 0x02))
    assert any(holds) == expect_hold, speed_raw


def test_resume_is_cleared_once_moving_and_does_not_survive_a_new_stop():
  rig = Rig()
  rig.step(False, speed_raw=0)
  for _ in range(60):
    rig.step(True, accel=-0.5, speed_raw=0, lcs=LongCtrlState.stopping)
  resumes = []
  for _ in range(4):
    dat, ok = rig.step(True, accel=0.5, speed_raw=0, lcs=LongCtrlState.starting)
    if dat is not None:
      assert ok
      resumes.append(bool(dat[6] & 0x40))
  assert any(resumes)
  # Raw 11..35 is still standstill for CarState but moving for the panda; held
  # for several frames so a send frame always falls inside it.
  sent_while_crawling = 0
  for speed_raw in (11, 11, 11, 200, 0, 0, 0, 0):
    dat, ok = rig.step(True, accel=0.0, speed_raw=speed_raw, lcs=LongCtrlState.pid)
    if dat is not None:
      sent_while_crawling += speed_raw == 11
      assert ok and not dat[6] & 0x40, (speed_raw, dat.hex())
  assert sent_while_crawling >= 1


def test_gas_override_relays_the_stock_frame():
  for speed_raw in (0, 500):
    rig = Rig()
    rig.step(False, speed_raw=speed_raw)
    for _ in range(60):
      rig.step(True, accel=1.0, speed_raw=speed_raw)
    for _ in range(4):
      dat, ok = rig.step(True, accel=1.0, speed_raw=speed_raw, gas=True)
      if dat is not None:
        assert_frame(rig, dat, ok, synthesized=False)
    for _ in range(2):
      dat, ok = rig.step(True, accel=1.0, speed_raw=speed_raw)
      if dat is not None:
        assert_frame(rig, dat, ok, synthesized=True)
        assert dat != rig.stock_dat


def test_stock_acc_disabled_frame_is_relayed():
  # ACCEL_CMD idles at 4000 whenever stock ACC_ENABLED is 0 (all 11,492 such
  # frames in the target drive); openpilot never commands into that frame.
  rig = Rig(stock_accel=4000, acc_enabled=0)
  for blended in (False, True):
    rig.ctl.params.blended = blended
    for _ in range(4):
      dat, ok = rig.step(True, accel=1.0, speed_raw=500)
      if dat is not None:
        assert_frame(rig, dat, ok, synthesized=False)


def test_synthesis_requires_op_long_and_long_active():
  for op_long, long_active in ((False, True), (True, False), (False, False)):
    rig = Rig(op_long=op_long)
    for _ in range(4):
      dat, ok = rig.step(long_active, accel=1.0, speed_raw=0, lcs=LongCtrlState.starting)
      if dat is not None:
        assert_frame(rig, dat, ok, synthesized=False)


def test_inactive_frames_reset_timers_every_frame():
  # One inactive controller frame that is not a send frame must still clear a
  # running RESUME pulse.
  rig = Rig()
  rig.step(False, speed_raw=0)
  for _ in range(60):
    rig.step(True, accel=-0.5, speed_raw=0, lcs=LongCtrlState.stopping)
  rig.step(True, accel=0.5, speed_raw=0, lcs=LongCtrlState.starting)
  if rig.ctl.frame % 2 == 0:
    rig.step(True, accel=0.5, speed_raw=0, lcs=LongCtrlState.starting)
  assert rig.ctl.frame % 2 == 1
  dat, _ = rig.step(False, speed_raw=0)
  assert dat is None
  for _ in range(2):
    dat, ok = rig.step(True, accel=0.0, speed_raw=0, lcs=LongCtrlState.pid)
    if dat is not None:
      assert ok and not dat[6] & 0x40, dat.hex()


def test_blended_acc_transitions():
  for experimental in (False, True):
    rig = Rig(blended=True, experimental=experimental)
    plan = [(False, 0.0)] * 3 + [(True, 1.0)] * 10 + [(False, 0.0)] * 3 + [(True, -1.0)] * 10 + [(False, 0.0)] * 3
    first_synth = []
    previous = False
    for long_active, accel in plan:
      dat, ok = rig.step(long_active, accel=accel, speed_raw=500)
      if dat is None:
        continue
      assert_frame(rig, dat, ok, synthesized=long_active)
      if long_active and not previous:
        first_synth.append(accel_cmd(dat))
      previous = long_active
    # The filter restarts at every stock->OP transition instead of carrying the
    # previous session's state.
    target = (lambda a: round(2000 + 200 * a)) if experimental else (lambda a: 1995)
    assert first_synth == [target(1.0), target(-1.0)], first_synth


def test_blended_filter_restarts_after_gas_interrupts_synthesis():
  # longActive stays true across the gas press; synthesis does not, so the
  # filter must restart on the target rather than resume from the old command.
  rig = Rig(blended=True, experimental=True)
  rig.step(False, speed_raw=500)
  for _ in range(6):
    rig.step(True, accel=1.0, speed_raw=500)
  for _ in range(4):
    rig.step(True, accel=-1.0, speed_raw=500, gas=True)
  first = None
  for _ in range(2):
    dat, ok = rig.step(True, accel=-1.0, speed_raw=500)
    if dat is not None:
      assert_frame(rig, dat, ok, synthesized=True)
      first = accel_cmd(dat) if first is None else first
  assert first == 1800, first


def test_carstate_standstill_matches_native_vehicle_moving():
  # The controller trusts CS.acc_standstill to emit HOLD/RESUME only where the
  # panda allows them, so the real CarState must agree with native vehicle_moving
  # for every raw SPEED around the threshold, in both unit systems.
  from opendbc.car.mazda.carstate import CarState
  CP = structs.CarParams(carFingerprint="MAZDA_3_2019", brand="mazda", flags=int(MazdaSafetyFlags.GEN2))
  car_state = CarState(CP)
  assert car_state.acc_standstill is False  # unknown until the first SPEED frame: moving
  safety = libsafety_py.libsafety
  safety.set_safety_hooks(CarParams.SafetyModel.mazda, int(MazdaSafetyFlags.GEN2))
  safety.init_tests()
  for imperial in (0, 1):
    for speed_raw in list(range(0, 41)) + [100, 1000]:
      parsers = {
        Bus.pt: CANParser("mazda_2019", [("SYSTEM_SETTINGS", 0)], 0),
        Bus.body: CANParser("mazda_2019", [], 1),
        Bus.cam: CANParser("mazda_2019", [("SPEED", 0)], 2),
      }
      parsers[Bus.pt].update([0, [PACKER.make_can_msg("SYSTEM_SETTINGS", 0, {"IMPERIAL_UNIT": imperial})]])
      speed = PACKER.make_can_msg("SPEED", 2, {"SPEED": speed_raw * 0.01})
      parsers[Bus.cam].update([0, [speed]])
      ret = car_state.update_gen2(parsers)
      safety.safety_rx_hook(make_msg(2, 0x217, dat=bytes(speed[1])))
      native_moving = safety.get_vehicle_moving()
      assert car_state.acc_standstill == (not native_moving), (imperial, speed_raw)
      assert not car_state.acc_standstill or ret.standstill, (imperial, speed_raw)
