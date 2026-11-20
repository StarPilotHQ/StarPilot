#!/usr/bin/env python3
import unittest

from opendbc.can import CANPacker
from opendbc.car.structs import CarParams
from opendbc.car.mazda.values import MazdaSafetyFlags
import opendbc.safety.tests.common as common
from opendbc.safety.tests.common import make_msg

ACC_PACKER = CANPacker("mazda_2019")


def acc_bytes(values):
  return bytes(ACC_PACKER.make_can_msg("ACC", 2, values)[1])


class TestMazdaSafety(common.CarSafetyTest, common.DriverTorqueSteeringSafetyTest):

  DBC = "mazda_2017"
  SAFETY_MODEL = CarParams.SafetyModel.mazda

  TX_MSGS = [[0x243, 0], [0x09d, 0], [0x440, 0]]
  STANDSTILL_THRESHOLD = .1
  RELAY_MALFUNCTION_ADDRS = {0: (0x243, 0x440)}
  FWD_BLACKLISTED_ADDRS = {2: [0x243, 0x440]}

  MAX_RATE_UP = 10
  MAX_RATE_DOWN = 25
  MAX_TORQUE_LOOKUP = [0], [800]

  MAX_RT_DELTA = 300

  DRIVER_TORQUE_ALLOWANCE = 15
  DRIVER_TORQUE_FACTOR = 1

  # Mazda actually does not set any bit when requesting torque
  NO_STEER_REQ_BIT = True

  SAFETY_PARAM = MazdaSafetyFlags.GEN1

  def _torque_meas_msg(self, torque):
    values = {"STEER_TORQUE_MOTOR": torque}
    return self.packer.make_can_msg_safety("STEER_TORQUE", 0, values)

  def _torque_driver_msg(self, torque):
    values = {"STEER_TORQUE_SENSOR": torque}
    return self.packer.make_can_msg_safety("STEER_TORQUE", 0, values)

  def _torque_cmd_msg(self, torque, steer_req=1):
    values = {"LKAS_REQUEST": torque}
    return self.packer.make_can_msg_safety("CAM_LKAS", 0, values)

  def _speed_msg(self, speed):
    values = {"SPEED": speed}
    return self.packer.make_can_msg_safety("ENGINE_DATA", 0, values)

  def _user_brake_msg(self, brake):
    values = {"BRAKE_ON": brake}
    return self.packer.make_can_msg_safety("PEDALS", 0, values)

  def _user_gas_msg(self, gas):
    values = {"PEDAL_GAS": gas}
    return self.packer.make_can_msg_safety("ENGINE_DATA", 0, values)

  def _pcm_status_msg(self, enable):
    values = {"CRZ_ACTIVE": enable}
    return self.packer.make_can_msg_safety("CRZ_CTRL", 0, values)

  def _button_msg(self, resume=False, cancel=False):
    values = {
      "CAN_OFF": cancel,
      "CAN_OFF_INV": (cancel + 1) % 2,
      "RES": resume,
      "RES_INV": (resume + 1) % 2,
    }
    return self.packer.make_can_msg_safety("CRZ_BTNS", 0, values)

  def test_buttons(self):
    # only cancel allows while controls not allowed
    self.safety.set_controls_allowed(0)
    self.assertTrue(self._tx(self._button_msg(cancel=True)))
    self.assertFalse(self._tx(self._button_msg(resume=True)))

    # do not block resume if we are engaged already
    self.safety.set_controls_allowed(1)
    self.assertTrue(self._tx(self._button_msg(cancel=True)))
    self.assertTrue(self._tx(self._button_msg(resume=True)))


class TestMazdaIgnition(common.SafetyTestBase):
  SAFETY_MODEL = None

  TX_MSGS: list = []

  def _msg(self, byte0):
    return make_msg(0, 0x9E, dat=bytes([byte0]) + b"\x00" * 7)

  # 0x9E byte 0 high 3 bits == 6 (0xC0)
  def test_ignition_on(self):
    self.safety.ignition_can_hook(self._msg(0xC0))
    self.assertTrue(self.safety.get_ignition_can())

  def test_ignition_off(self):
    self.safety.ignition_can_hook(self._msg(0xC0))
    self.assertTrue(self.safety.get_ignition_can())
    self.safety.ignition_can_hook(self._msg(0x20))
    self.assertFalse(self.safety.get_ignition_can())


TI1_KEY = 3294744160
TI_RUN, TI_DRIVER_OVER, TI_OFF = 3, 2, 1


class Ti1Common:
  """TI1 (GEN1 Torque Interceptor) rules shared by the camera and NoFSC configurations."""
  TI_STEER_STEP = 10  # MAZDA_STEERING_LIMITS.max_rate_up

  def setUp(self):
    super().setUp()
    self._rx(self._ti1_feedback_msg())

  def _ti1_feedback_msg(self, torque=0, state=TI_RUN, viol=0, version=1, ramp_down=0):
    values = {"TI_TORQUE_SENSOR": torque, "STATE": state, "VIOL": viol, "VERSION_NUMBER": version, "RAMP_DOWN": ramp_down}
    return self.packer.make_can_msg_safety("TI_FEEDBACK", 1, values)

  def _torque_driver_msg(self, torque):
    return self._ti1_feedback_msg(torque=torque)

  def _ti1_cmd_msg(self, torque, chksum=None, key=TI1_KEY):
    values = {"LKAS_REQUEST": torque, "CHKSUM": torque if chksum is None else chksum, "KEY": key}
    return self.packer.make_can_msg_safety("CAM_LKAS2", 1, values)

  def _steer(self, torque):
    """One host frame steering `torque` on every channel this configuration sends; returns the TI result."""
    raise NotImplementedError

  def _engaged_with(self, torque):
    """Engage, then ramp every channel to `torque` the way the host would."""
    self.safety.set_controls_allowed(1)
    self._set_prev_torque(0)
    step = self.TI_STEER_STEP if torque > 0 else -self.TI_STEER_STEP
    for t in range(step, torque + step, step):
      self.assertTrue(self._steer(t))

  def test_ti1_zero_always_allowed(self):
    for controls in (0, 1):
      self.safety.set_controls_allowed(controls)
      self.assertTrue(self._tx(self._ti1_cmd_msg(0)))
    self._rx(self._ti1_feedback_msg(state=TI_OFF, viol=0x11))
    self.assertTrue(self._tx(self._ti1_cmd_msg(0)))

  def test_ti1_malformed_frames_blocked(self):
    self.safety.set_controls_allowed(1)
    self.assertFalse(self._tx(self._ti1_cmd_msg(0, chksum=1)))
    self.assertFalse(self._tx(self._ti1_cmd_msg(0, key=TI1_KEY + 1)))
    msg = self._ti1_cmd_msg(0)
    msg.data[0] |= 0x10
    self.assertFalse(self._tx(msg))

  def test_ti1_unhealthy_blocks_torque(self):
    unhealthy = {
      "violation": self._ti1_feedback_msg(state=TI_OFF, viol=0x11),
      "violation in RUN": self._ti1_feedback_msg(viol=0x33),
      "driver override": self._ti1_feedback_msg(state=TI_DRIVER_OVER),
      "off": self._ti1_feedback_msg(state=TI_OFF),
      "discover": self._ti1_feedback_msg(state=0),
      "ramp down": self._ti1_feedback_msg(version=2, ramp_down=1),
      "ramp down, nonzero byte": self._ti1_feedback_msg(version=2, ramp_down=2),
    }
    for name, feedback in unhealthy.items():
      with self.subTest(name):
        self._engaged_with(100)
        self._rx(feedback)
        self.assertFalse(self._steer(100))
        # not a disengagement
        self.assertTrue(self.safety.get_controls_allowed())
        self._rx(self._ti1_feedback_msg())

  def test_ti1_ramp_down_ignored_on_version_1(self):
    self._rx(self._ti1_feedback_msg(version=1, ramp_down=1))
    self._engaged_with(100)

  def test_ti1_ramp_down_clears_with_a_version_1_frame(self):
    self._rx(self._ti1_feedback_msg(version=2, ramp_down=1))
    self.safety.set_controls_allowed(1)
    self.assertFalse(self._steer(self.TI_STEER_STEP))
    self._rx(self._ti1_feedback_msg(version=1, ramp_down=1))
    self._engaged_with(100)

  def test_ti1_no_feedback_blocks_torque(self):
    self.safety.init_tests()
    self.safety.set_safety_hooks(self.SAFETY_MODEL, self.SAFETY_PARAM)
    self.safety.set_controls_allowed(1)
    self._set_prev_torque(0)
    self.assertFalse(self._steer(self.TI_STEER_STEP))

  def test_ti1_stale_feedback_blocks_torque(self):
    self.safety.set_timer(0)
    self._rx(self._ti1_feedback_msg())
    self._engaged_with(100)
    self.safety.set_timer(200000)
    self.assertTrue(self._steer(110))
    self.safety.set_timer(200001)
    self.assertFalse(self._steer(110))

  def test_ti1_ramps_from_zero_after_the_ti_stops_steering(self):
    # Review finding: commands refused while the TI1 was not steering must not move its ramp. After
    # DRIVER_OVER, a return to 110 is refused; the ramp restarts at one step.
    self._engaged_with(100)
    self._rx(self._ti1_feedback_msg(state=TI_DRIVER_OVER))
    for t in range(10, 110, 10):
      self.assertFalse(self._steer(t))
    self._rx(self._ti1_feedback_msg())
    self.assertFalse(self._steer(110))
    self.assertTrue(self._steer(self.TI_STEER_STEP))


class TestMazdaTorqueInterceptorSafety(Ti1Common, TestMazdaSafety):
  """Gen1 with the original Torque Interceptor and the camera: 0x243 and 0x249 every frame."""
  TX_MSGS = [[0x243, 0], [0x249, 1], [0x09d, 0], [0x440, 0]]
  SAFETY_PARAM = MazdaSafetyFlags.GEN1 | MazdaSafetyFlags.TORQUE_INTERCEPTOR

  def _steer(self, torque, ti=None):
    """Stock 0x243 first, then 0x249 (the host's order). The stock frame is expected to pass; returns the TI's."""
    self._tx(self._torque_cmd_msg(torque))
    return self._tx(self._ti1_cmd_msg(torque if ti is None else ti))

  def test_ti1_follows_the_accepted_stock_command(self):
    self._engaged_with(100)
    self.assertTrue(self._steer(110))

  def test_ti1_may_be_below_the_stock_command(self):
    self._engaged_with(100)
    self.assertTrue(self._steer(110, ti=50))
    self.assertTrue(self._steer(120, ti=60))

  def test_ti1_rejects_more_than_or_opposite_to_the_stock_command(self):
    self._engaged_with(100)
    self.assertFalse(self._steer(110, ti=115))
    self._engaged_with(100)
    self.assertFalse(self._steer(110, ti=-10))

  def test_ti1_needs_its_own_stock_command(self):
    self._engaged_with(100)
    self.assertFalse(self._tx(self._ti1_cmd_msg(100)))           # no 0x243 before it
    self.assertTrue(self._tx(self._torque_cmd_msg(100)))
    self.assertFalse(self._tx(self._ti1_cmd_msg(100)))           # its ramp restarted after the refusal
    self.assertTrue(self._tx(self._torque_cmd_msg(100)))
    self.assertTrue(self._tx(self._ti1_cmd_msg(10)))
    self.assertFalse(self._tx(self._ti1_cmd_msg(10)))            # each 0x243 pairs once

  def test_ti1_pairing_window(self):
    for gap, ok in ((20000, True), (20001, False)):
      with self.subTest(gap=gap):
        self.safety.set_timer(0)
        self._rx(self._ti1_feedback_msg())
        self._engaged_with(100)
        self.assertTrue(self._tx(self._torque_cmd_msg(100)))
        self.safety.set_timer(gap)
        self.assertEqual(ok, self._tx(self._ti1_cmd_msg(100)))

  def test_ti1_blocked_when_the_stock_command_is_rejected(self):
    self._engaged_with(100)
    self.assertFalse(self._tx(self._torque_cmd_msg(400)))  # rate violation
    self.assertFalse(self._tx(self._ti1_cmd_msg(110)))
    self.safety.set_controls_allowed(0)
    self.assertFalse(self._tx(self._torque_cmd_msg(10)))
    self.assertFalse(self._tx(self._ti1_cmd_msg(10)))

  def test_ti1_ramps_back_after_an_override(self):
    # The stock channel keeps its command through the override; the TI1 is not sent it in one step, it
    # ramps back by max_rate_up per frame.
    self._engaged_with(200)
    self._rx(self._ti1_feedback_msg(state=TI_DRIVER_OVER))
    self.assertTrue(self._steer(200, ti=0))
    self._rx(self._ti1_feedback_msg())
    self.assertFalse(self._steer(200))
    for ti in range(10, 210, 10):
      self.assertTrue(self._steer(200, ti=ti))
    self.assertTrue(self._steer(200))


class TestMazdaTorqueInterceptorNoFscSafety(Ti1Common, TestMazdaSafety):
  """Gen1 TI1 without the camera: 0x249 is the only steering command and gets the full torque checks,
  and 0x243 is not allowed at all."""
  TX_MSGS = [[0x249, 1], [0x09d, 0], [0x440, 0]]
  RELAY_MALFUNCTION_ADDRS = {0: (0x440,)}
  FWD_BLACKLISTED_ADDRS = {2: [0x440]}
  SAFETY_PARAM = MazdaSafetyFlags.GEN1 | MazdaSafetyFlags.TORQUE_INTERCEPTOR | MazdaSafetyFlags.NO_FSC

  def _torque_cmd_msg(self, torque, steer_req=1):
    return self._ti1_cmd_msg(torque)

  def _steer(self, torque):
    return self._tx(self._ti1_cmd_msg(torque))

  def test_stock_command_not_allowed(self):
    # Review finding: 0x243 shared the torque limiter with 0x249. It is no longer on the TX list, so it can
    # neither pass nor advance 0x249's ramp.
    self.safety.set_controls_allowed(1)
    self._set_prev_torque(0)
    stock = self.packer.make_can_msg_safety("CAM_LKAS", 0, {"LKAS_REQUEST": 10})
    self.assertFalse(self._tx(stock))
    self.assertFalse(self._steer(20))
    self.assertTrue(self._steer(10))

  def test_malformed_frame_does_not_advance_the_ramp(self):
    # Review finding: a refused malformed 10 let a following 20 through.
    self.safety.set_controls_allowed(1)
    self._set_prev_torque(0)
    self.assertFalse(self._tx(self._ti1_cmd_msg(10, key=TI1_KEY + 1)))
    self.assertFalse(self._steer(20))
    self.assertTrue(self._steer(10))

  def test_realtime_limits(self):
    # common.DriverTorqueSteeringSafetyTest.test_realtime_limits, with the TI1 feedback kept fresh across
    # its 250 ms clock step (past MAZDA_TI_FEEDBACK_TIMEOUT_US) as the 50 Hz interceptor would.
    self.safety.set_controls_allowed(True)
    for sign in [-1, 1]:
      self.safety.init_tests()
      self.safety.set_timer(0)
      self._rx(self._ti1_feedback_msg())
      self._set_prev_torque(0)
      self._reset_torque_driver_measurement(0)
      for t in range(self.MAX_RT_DELTA):
        self.assertTrue(self._tx(self._torque_cmd_msg(sign * t)))
      self.assertFalse(self._tx(self._torque_cmd_msg(sign * (self.MAX_RT_DELTA + 1))))

      self._set_prev_torque(0)
      for t in range(self.MAX_RT_DELTA):
        self.assertTrue(self._tx(self._torque_cmd_msg(sign * t)))

      self.safety.set_timer(common.RT_INTERVAL + 1)
      self._rx(self._ti1_feedback_msg())
      self.assertTrue(self._tx(self._torque_cmd_msg(sign * (self.MAX_RT_DELTA - 1))))
      self.assertTrue(self._tx(self._torque_cmd_msg(sign * (self.MAX_RT_DELTA + 1))))


class TestMazda2019Safety(common.CarSafetyTest, common.DriverTorqueSteeringSafetyTest):
  """Gen2 (2019+ Mazda 3, CX-30, CX-50) with Torque Interceptor 2."""
  TX_MSGS = [[0x249, 1], [0x220, 2]]
  STANDSTILL_THRESHOLD = .1
  RELAY_MALFUNCTION_ADDRS = {2: (0x220,)}
  FWD_BLACKLISTED_ADDRS = {0: [0x220]}

  MAX_RATE_UP = 45
  MAX_RATE_DOWN = 80
  MAX_TORQUE_LOOKUP = [0], [8000]
  MAX_RT_DELTA = 1688
  DRIVER_TORQUE_ALLOWANCE = 1400
  DRIVER_TORQUE_FACTOR = 1
  NO_STEER_REQ_BIT = True

  DBC = "mazda_2019"
  SAFETY_MODEL = CarParams.SafetyModel.mazda
  SAFETY_PARAM = MazdaSafetyFlags.GEN2

  # Mirrors MAZDA_TI_RECOVERY_FRAMES in modes/mazda.h: several consecutive valid
  # frames are required before unhealthy status clears (safety plan 4.1 item 6).
  TI_RECOVERY_FRAMES = 10

  def setUp(self):
    super().setUp()
    # Every GEN2/GEN3 car carries a TI2, and native actuation is refused until the
    # interceptor has reported health for several consecutive frames. Establish
    # that steady state once per test so arming controls afterwards is a
    # legitimate authorization. Tests that need a startup or timeout state reset
    # the hooks themselves.
    self._healthy()

  def _rx_raw(self, msg):
    """Deliver one frame unchanged; used by tests that depend on timing order."""
    return super()._rx(msg)

  def _healthy(self, frames=None):
    for _ in range(self.TI_RECOVERY_FRAMES if frames is None else frames):
      self._rx_raw(self._ti2_feedback_msg())

  def _torque_driver_msg(self, torque):
    # The full interceptor frame, not just the torque field: an unset state nibble
    # packs as DISCOVER (0), which is correctly classified as unhealthy.
    return self.packer.make_can_msg_safety("TI_FEEDBACK", 1, {"STEER_TORQUE_SENSOR": torque,
      "CPU_0_STATE": 3, "CPU_1_STATE": 3, "CPU_2_STATE": 3, "CPU_3_STATE": 3})

  def _torque_cmd_msg(self, torque, steer_req=1):
    return self.packer.make_can_msg_safety("EPS_LKAS", 1, {"LKAS_REQUEST": int(torque) & 0xFFFF})

  def _speed_msg(self, speed):
    return self.packer.make_can_msg_safety("SPEED", 2, {"SPEED": speed})

  def _user_brake_msg(self, brake):
    return self.packer.make_can_msg_safety("BRAKE_PEDAL_SLOW", 0, {"BRAKE_PEDAL_PRESSED": brake})

  def _user_gas_msg(self, gas):
    return self.packer.make_can_msg_safety("ENGINE_DATA", 2, {"PEDAL_GAS": gas})

  def _pcm_status_msg(self, enable):
    return self.packer.make_can_msg_safety("CRUZE_STATE", 0, {"CRZ_STATE": 3 if enable else 1})

  def test_acc_main_follows_cruise_state(self):
    if not (self.SAFETY_PARAM & MazdaSafetyFlags.GEN2):
      self.skipTest("GEN2 evidence only; GEN3 still hardcodes MAIN")
    # GEN2 evidence (reports/mazda-gen2-aol-gate-report.md section 8): CRZ_STATE
    # 0 means the ACC main switch is off, 1 is main on in standby, and 2..7 keep
    # it on. acc_main_on must track that instead of being hardcoded true.
    self.safety.set_controls_allowed(0)
    for state, expected in ((0, False), (1, True), (2, True), (3, True), (4, True), (7, True)):
      self._rx(self.packer.make_can_msg_safety("CRUZE_STATE", 0, {"CRZ_STATE": state}))
      self.assertEqual(bool(self.safety.get_acc_main_on()), expected, f"CRZ_STATE={state}")

  def test_realtime_limits(self):
    """Shared realtime-limit contract, with the interceptor frames a real car sends.

    Native TX refuses torque when 0x24B is stale, so a bare timer jump without a
    feedback frame is not a car state; this override supplies the frame instead of
    relaxing the freshness rule.
    """
    self.safety.set_controls_allowed(True)

    for sign in (-1, 1):
      self.safety.init_tests()
      self.safety.set_timer(0)
      # A mode re-init is a restart: health is unknown and controls are disarmed
      # until the interceptor reports and the driver engages again.
      self.safety.set_controls_allowed(0)
      self._healthy()
      self.safety.set_controls_allowed(True)
      self._set_prev_torque(0)
      self._reset_torque_driver_measurement(0)
      for t in range(self.MAX_RT_DELTA):
        self.assertTrue(self._tx(self._torque_cmd_msg(t * sign)))
      self.assertFalse(self._tx(self._torque_cmd_msg(sign * (self.MAX_RT_DELTA + 1))))

      self._set_prev_torque(0)
      for t in range(self.MAX_RT_DELTA):
        self.assertTrue(self._tx(self._torque_cmd_msg(t * sign)))

      # Advance the clock in 20 ms steps with the 50 Hz interceptor frames a real
      # car sends, so the rate limit is exercised without a feedback outage.
      now = 0
      for _ in range(14):
        now += 20000
        self.safety.set_timer(now)
        self._rx_raw(self._ti2_feedback_msg())
      self.assertTrue(self._tx(self._torque_cmd_msg(sign * (self.MAX_RT_DELTA - 1))))
      self.assertTrue(self._tx(self._torque_cmd_msg(sign * (self.MAX_RT_DELTA + 1))))

  def test_steer_tx_needs_controls(self):
    self.safety.set_controls_allowed(0)
    self.assertFalse(self._tx(self._torque_cmd_msg(100)))
    self.assertTrue(self._tx(self._torque_cmd_msg(0)))

  # --- Gen2 TI2 / ACC safety-gap regression tests (from the safety action plan) ---
  # These FAIL against the unremediated safety mode, which allowlists ACC
  # (0x220, bus 2) with no content checks and never inspects TI2 CPU
  # violation/state fields. They must pass once the corresponding
  # remediation lands. Guard tests that pass today prevent over-blocking fixes.

  def _stock_acc_msg(self, accel_cmd=2000):
    # stock ACC frame as it arrives from the vehicle side of the relay (main bus)
    return self.packer.make_can_msg_safety("ACC", 0, {"ACCEL_CMD": accel_cmd})

  def _acc_cmd_msg(self, accel_cmd=2000, hold=0, resume=0):
    # outgoing ACC frame on the camera bus (openpilot side of the relay)
    values = {"ACCEL_CMD": accel_cmd, "HOLD": hold, "RESUME": resume}
    return self.packer.make_can_msg_safety("ACC", 2, values)

  def _ti2_feedback_msg(self, viol=(0, 0, 0, 0), cpu_state=(3, 3, 3, 3)):
    values = {
      "STEER_TORQUE_SENSOR": 0,
      "CPU_0_VIOL": viol[0], "CPU_1_VIOL": viol[1], "CPU_2_VIOL": viol[2], "CPU_3_VIOL": viol[3],
      "CPU_0_STATE": cpu_state[0], "CPU_1_STATE": cpu_state[1], "CPU_2_STATE": cpu_state[2], "CPU_3_STATE": cpu_state[3],
    }
    return self.packer.make_can_msg_safety("TI_FEEDBACK", 1, values)

  def test_acc_modified_rejected_when_controls_not_allowed(self):
    self._rx(self._stock_acc_msg(accel_cmd=2000))
    self.safety.set_controls_allowed(0)
    for bad_accel in (0, 1000, 1300, 2401, 3000, 4095):
      self.assertFalse(self._tx(self._acc_cmd_msg(accel_cmd=bad_accel)), f"{bad_accel=}")

  def test_acc_exact_stock_passthrough_allowed(self):
    for controls_allowed in (0, 1):
      self._reset_safety_hooks()
      self._rx(self._stock_acc_msg(accel_cmd=2000))
      self.safety.set_controls_allowed(controls_allowed)
      self.assertTrue(self._tx(self._acc_cmd_msg(accel_cmd=2000)), controls_allowed)

  def test_acc_stale_stock_replay_rejected(self):
    if not (self.SAFETY_PARAM & MazdaSafetyFlags.GEN2):
      self.skipTest("GEN2 ACC passthrough only")
    # A byte-exact stock copy is only a passthrough while the reference is fresh;
    # replaying it after the window must be rejected.
    self._rx(self._stock_acc_msg(accel_cmd=2000))
    self.safety.set_controls_allowed(0)
    self.assertTrue(self._tx(self._acc_cmd_msg(accel_cmd=2000)))
    self.safety.set_timer(200001)  # past MAZDA_STOCK_ACC_FRESHNESS_US
    self.assertFalse(self._tx(self._acc_cmd_msg(accel_cmd=2000)))

  def test_ti_cpu_violations_disengage(self):
    for cpu in range(4):
      self._reset_safety_hooks()
      self.safety.set_controls_allowed(1)
      viol = [0, 0, 0, 0]
      viol[cpu] = 1
      self._rx(self._ti2_feedback_msg(viol=viol))
      self.assertFalse(self.safety.get_controls_allowed(), f"CPU_{cpu}_VIOL")

  def test_ti_violation_blocks_steering_and_modified_acc(self):
    self._rx(self._stock_acc_msg(accel_cmd=2000))
    self.safety.set_controls_allowed(1)
    self._rx(self._ti2_feedback_msg(viol=(1, 0, 0, 0)))
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    self.assertTrue(self._tx(self._torque_cmd_msg(0)))
    self.assertFalse(self._tx(self._acc_cmd_msg(accel_cmd=3000)))
    self.assertTrue(self._tx(self._acc_cmd_msg(accel_cmd=2000)))

  def test_ti_fault_survives_a_cruise_reengagement(self):
    # controls_allowed can be set again by a stock-cruise rising edge between two
    # feedback frames. A verified violation must still block torque.
    self._rx(self._stock_acc_msg())
    self.safety.set_controls_allowed(1)
    self._rx(self._ti2_feedback_msg(viol=(0, 1, 0, 0)))
    self.assertFalse(self.safety.get_controls_allowed())
    self._rx_raw(self._pcm_status_msg(False))
    self._rx_raw(self._pcm_status_msg(True))
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    self.assertTrue(self._tx(self._torque_cmd_msg(0)))
    if self.SAFETY_PARAM & MazdaSafetyFlags.GEN2:
      self.assertFalse(self._tx(self._acc_cmd_msg(accel_cmd=3000)))
      self.assertTrue(self._tx(self._acc_cmd_msg(accel_cmd=2000)))

  def test_ti_fault_does_not_rearm_from_a_clean_frame(self):
    # Independent-validation D1: a cruise edge taken during a fault re-armed
    # controls, and one clean frame then allowed torque with no new engagement.
    self.safety.set_controls_allowed(0)
    self._rx_raw(self._ti2_feedback_msg())
    self._rx_raw(self._pcm_status_msg(True))
    self.assertTrue(self.safety.get_controls_allowed())
    self._rx_raw(self._ti2_feedback_msg(viol=(0, 1, 0, 0)))
    self.assertFalse(self.safety.get_controls_allowed())
    self._rx_raw(self._pcm_status_msg(False))
    self._rx_raw(self._pcm_status_msg(True))  # re-set while the fault is live
    self.assertFalse(self.safety.get_controls_allowed())
    self._rx_raw(self._ti2_feedback_msg())    # fault clears, still no new engagement
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    # A deliberate disengage, a qualified recovery and a re-engage are allowed.
    self._rx_raw(self._pcm_status_msg(False))
    self._healthy()
    self._rx_raw(self._pcm_status_msg(True))
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertTrue(self._tx(self._torque_cmd_msg(45)))

  def test_ti_requires_consecutive_healthy_frames(self):
    # Safety plan phase 4.1 item 6 and its named test: unhealthy status must not
    # clear on a single good frame.
    self._rx_raw(self._ti2_feedback_msg(viol=(1, 0, 0, 0)))
    self._rx_raw(self._pcm_status_msg(False))
    self._healthy(self.TI_RECOVERY_FRAMES - 1)
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    self._rx_raw(self._ti2_feedback_msg())
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    self._rx_raw(self._pcm_status_msg(True))
    self.assertTrue(self._tx(self._torque_cmd_msg(45)))

  def test_no_feedback_blocks_torque_at_startup(self):
    # Independent-validation D2/R2: with no interceptor feedback at all, torque was
    # permitted as soon as cruise engaged, and one clean frame then authorized it
    # without a new engagement.
    self._reset_safety_hooks()
    self.safety.set_controls_allowed(0)
    self._rx_raw(self._pcm_status_msg(True))
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    self.assertTrue(self._tx(self._torque_cmd_msg(0)))
    # Recovery is qualified over several consecutive frames. Health arriving is
    # what revokes the authorization acquired without it, so torque stays blocked
    # until a new engagement edge.
    self._healthy(self.TI_RECOVERY_FRAMES - 1)
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    self._rx_raw(self._ti2_feedback_msg())  # recovery completes
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    self._rx_raw(self._pcm_status_msg(False))
    self._rx_raw(self._pcm_status_msg(True))
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertTrue(self._tx(self._torque_cmd_msg(45)))

  def test_feedback_timeout_recovery_needs_a_new_engagement(self):
    # Independent-validation R1: returning feedback restored steering and GEN2
    # modified ACC with the authorization that predated the timeout.
    self.safety.set_controls_allowed(0)
    self._rx_raw(self._ti2_feedback_msg())
    self._rx_raw(self._pcm_status_msg(True))
    self.assertTrue(self.safety.get_controls_allowed())
    self.safety.set_timer(200001)          # feedback goes stale, no other RX
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    self._healthy()  # qualified recovery, but no new engagement edge
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    if self.SAFETY_PARAM & MazdaSafetyFlags.GEN2:
      self.assertFalse(self._tx(self._acc_cmd_msg(accel_cmd=3000)))
    self._rx_raw(self._pcm_status_msg(False))
    self._rx_raw(self._pcm_status_msg(True))
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertTrue(self._tx(self._torque_cmd_msg(45)))

  def test_stale_feedback_blocks_torque_without_another_frame(self):
    # Independent-validation D2: the freshness rule only ran on RX, so a clock
    # advance with no frame still permitted torque.
    self.safety.set_controls_allowed(0)
    self._rx_raw(self._ti2_feedback_msg())
    self._rx_raw(self._pcm_status_msg(True))
    self.assertTrue(self._tx(self._torque_cmd_msg(45)))
    self.safety.set_timer(200001)
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))

  def test_ti_unknown_state_is_not_healthy(self):
    # Safety plan 4.1 item 3 and its named test: only RUN (3) is healthy.
    for state in (0, 1, 2, 4, 7, 15):
      self._reset_safety_hooks()
      self.safety.set_controls_allowed(0)
      self._healthy()
      self.safety.set_controls_allowed(1)
      self.assertTrue(self.safety.get_controls_allowed())
      self._rx_raw(self._ti2_feedback_msg(cpu_state=(state, 3, 3, 3)))
      self.assertFalse(self.safety.get_controls_allowed(), f"state={state}")
      self.assertFalse(self._tx(self._torque_cmd_msg(45)), f"state={state}")
      self.assertTrue(self._tx(self._torque_cmd_msg(0)), f"state={state}")

  def test_ti_driver_override_is_not_permanent_fault(self):
    # DRIVER_OVER (2) revokes while present, but recovery back to RUN followed by
    # a deliberate re-engagement works: it must not latch permanently.
    self.safety.set_controls_allowed(0)
    self._healthy()
    self.safety.set_controls_allowed(1)
    self._rx_raw(self._ti2_feedback_msg(cpu_state=(2, 2, 2, 2)))
    self.assertFalse(self.safety.get_controls_allowed())
    self._healthy()
    self.assertFalse(self.safety.get_controls_allowed())
    self._rx_raw(self._pcm_status_msg(False))
    self._rx_raw(self._pcm_status_msg(True))
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertTrue(self._tx(self._torque_cmd_msg(45)))

  def test_lateral_only_authority_cannot_modify_acc(self):
    # Force ordinary controls and longitudinal-axis permission apart. With
    # controls_allowed=1, reverting the TX gate to !controls_allowed would permit
    # the modified frame and fail this test.
    if not (self.SAFETY_PARAM & MazdaSafetyFlags.GEN2):
      self.skipTest("GEN2 ACC TX only")
    self._reset_safety_hooks()
    self.safety.set_aol_test_permission_mask(0x1)  # lateral axis only
    self._healthy()
    self.safety.set_controls_allowed(1)

    # Use the captured stock frame with HOLD/RESUME clear; the longitudinal
    # positive control must not depend on unknown standstill evidence.
    stock = bytearray(self.ACC_BASE)
    stock[7] = (0x2A + sum(stock[:7])) % 256
    self._rx_raw(make_msg(0, 0x220, dat=bytes(stock)))
    # Change only ACCEL_CMD, to an in-envelope value, so the frame is a valid
    # openpilot command once longitudinal authority exists.
    modified = bytearray(self._acc_raw(bytes(stock), accel=2100))

    self.assertFalse(self._tx(make_msg(2, 0x220, dat=bytes(modified))))
    self.assertTrue(self._tx(make_msg(2, 0x220, dat=bytes(stock))))

    # With longitudinal authority the same modification is permitted.
    self.safety.set_aol_test_permission_mask(0x3)
    self.safety.set_controls_allowed(1)
    self.assertTrue(self._tx(make_msg(2, 0x220, dat=bytes(modified))))
    self.safety.clear_aol_test_policy()

  def test_stock_acc_reference_must_be_a_valid_frame(self):
    # Independent-validation D3: the native reference boundary accepted corrupt
    # or truncated stock frames, and a checksum-only mutation could be relayed.
    if not (self.SAFETY_PARAM & MazdaSafetyFlags.GEN2):
      self.skipTest("GEN2 ACC passthrough only")
    self._rx_raw(self._ti2_feedback_msg())
    self._rx_raw(make_msg(0, 0x220, dat=bytes.fromhex('123456789abcde00')))
    self.assertFalse(self._tx(make_msg(2, 0x220, dat=bytes.fromhex('123456789abcde00'))))

    good = bytearray(bytes.fromhex('123456789abcde00'))
    good[7] = (0x2A + sum(good[:7])) % 256
    self._rx_raw(make_msg(0, 0x220, dat=bytes(good)))
    self.assertTrue(self._tx(make_msg(2, 0x220, dat=bytes(good))))
    mutated = bytes(good[:7]) + bytes([good[7] ^ 0xFF])
    self.assertFalse(self._tx(make_msg(2, 0x220, dat=mutated)))

    self._reset_safety_hooks()
    self._rx_raw(self._ti2_feedback_msg())
    self._rx_raw(make_msg(0, 0x220, dat=b'\x00'))
    self.assertFalse(self._tx(make_msg(2, 0x220, dat=b'\x00' * 8)))

  def test_ti_stale_feedback_revokes_controls(self):
    # 0x24B is a required 50 Hz message. Silence past the timeout is a fault and
    # must revoke actuation without the host noticing first.
    self.safety.set_controls_allowed(0)
    self._rx_raw(self._ti2_feedback_msg())  # clears the startup latch while disengaged
    self.safety.set_controls_allowed(1)
    self.assertTrue(self.safety.get_controls_allowed())
    self.safety.set_timer(200001)  # past MAZDA_TI_FEEDBACK_TIMEOUT_US
    self._rx_raw(self._speed_msg(30))
    self.assertFalse(self.safety.get_controls_allowed())

  def test_ti_recovery_does_not_auto_reengage(self):
    self.safety.set_controls_allowed(1)
    self._rx(self._ti2_feedback_msg(viol=(0, 0, 1, 0)))
    self.assertFalse(self.safety.get_controls_allowed())
    for _ in range(10):
      self._rx(self._ti2_feedback_msg())
    self.assertFalse(self.safety.get_controls_allowed())

  # --- TI2 violation 0x02: a lateral pause, not a fault (MAZDA_TI_VIOL_TRANSIENT) ---
  # Episode-cap timing is covered in test_mazda_gen2_aol.py, whose harness keeps
  # every required message fresh while time advances.
  TI_ALL_02 = (2, 2, 2, 2)

  def test_ti_02_pause_blocks_torque_without_revoking(self):
    self._rx(self._stock_acc_msg(accel_cmd=2000))
    self.safety.set_controls_allowed(1)
    for viol in (self.TI_ALL_02, (0, 2, 0, 2)):
      self._rx_raw(self._ti2_feedback_msg(viol=viol))
      self.assertTrue(self.safety.get_controls_allowed(), viol)
      self.assertFalse(self._tx(self._torque_cmd_msg(45)), viol)
      self.assertTrue(self._tx(self._torque_cmd_msg(0)), viol)
      if self.SAFETY_PARAM & MazdaSafetyFlags.GEN2:
        # Lateral only: an in-bounds modified ACC command is still allowed.
        self.assertTrue(self._tx(self._acc_cmd_msg(accel_cmd=2200)), viol)

  def test_ti_02_pause_resumes_after_recovery_frames(self):
    self.safety.set_controls_allowed(1)
    self._rx_raw(self._ti2_feedback_msg(viol=self.TI_ALL_02))
    self._healthy(self.TI_RECOVERY_FRAMES - 1)
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    self._rx_raw(self._ti2_feedback_msg())
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertTrue(self._tx(self._torque_cmd_msg(45)))

  def test_ti_02_with_other_code_or_state_is_fault(self):
    for viol, states in (((2, 1, 0, 0), (3, 3, 3, 3)), (self.TI_ALL_02, (3, 3, 2, 3))):
      self._reset_safety_hooks()
      self._healthy()
      self.safety.set_controls_allowed(1)
      self._rx_raw(self._ti2_feedback_msg(viol=viol, cpu_state=states))
      self.assertFalse(self.safety.get_controls_allowed(), (viol, states))

  def test_ti_02_needs_proven_health(self):
    # Startup: no feedback yet, then fewer than the recovery frames.
    for clean_before in (0, 1, self.TI_RECOVERY_FRAMES - 1):
      self._reset_safety_hooks()
      self._healthy(clean_before)
      self.safety.set_controls_allowed(1)
      self._rx_raw(self._ti2_feedback_msg(viol=self.TI_ALL_02))
      self.assertFalse(self.safety.get_controls_allowed(), clean_before)
    # After a real fault and a short recovery: the 0x02 is a fault too, so it
    # restarts the recovery count instead of letting it finish.
    self._reset_safety_hooks()
    self._healthy()
    self._rx_raw(self._ti2_feedback_msg(viol=(1, 0, 0, 0)))
    self._healthy(1)
    self._rx_raw(self._ti2_feedback_msg(viol=self.TI_ALL_02))
    self._healthy(self.TI_RECOVERY_FRAMES - 1)
    self.assertTrue(self.safety.get_mazda_ti_unhealthy())
    self._healthy(1)
    self.assertFalse(self.safety.get_mazda_ti_unhealthy())

  def test_refused_torque_during_a_pause_does_not_advance_the_ramp(self):
    # Independent review P1: commands refused during a pause advanced the rate limiter, so after
    # recovery the TI2 was sent 0 -> 225 in one step.
    self.safety.set_controls_allowed(1)
    self._set_prev_torque(0)
    self._rx_raw(self._ti2_feedback_msg(viol=self.TI_ALL_02))
    for t in (45, 90, 135, 180):
      self.assertFalse(self._tx(self._torque_cmd_msg(t)))
    self._healthy()
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertFalse(self._tx(self._torque_cmd_msg(225)))
    self.assertTrue(self._tx(self._torque_cmd_msg(45)))

  def test_recovery_ramps_from_what_the_ti2_was_sent(self):
    # Steering at 180 when a pause starts: the host releases to zero during it. After recovery the limiter
    # must start from that zero, not from 180.
    self.safety.set_controls_allowed(1)
    self._set_prev_torque(0)
    for t in (45, 90, 135, 180):
      self.assertTrue(self._tx(self._torque_cmd_msg(t)))
    self._rx_raw(self._ti2_feedback_msg(viol=self.TI_ALL_02))
    self.assertTrue(self._tx(self._torque_cmd_msg(0)))
    self._healthy()
    self.assertFalse(self._tx(self._torque_cmd_msg(225)))
    self.assertTrue(self._tx(self._torque_cmd_msg(45)))

  def test_refused_torque_while_unhealthy_does_not_advance_the_ramp(self):
    self._reset_safety_hooks()
    self.safety.set_controls_allowed(1)
    self._set_prev_torque(0)
    for t in (45, 90, 135, 180):
      self.assertFalse(self._tx(self._torque_cmd_msg(t)))   # no feedback yet
    self._healthy()
    self._rx_raw(self._pcm_status_msg(False))
    self._rx_raw(self._pcm_status_msg(True))
    self.assertFalse(self._tx(self._torque_cmd_msg(225)))
    self.assertTrue(self._tx(self._torque_cmd_msg(45)))

  def test_returning_feedback_after_a_gap_needs_full_recovery(self):
    # Independent review P1: the returning 0x24B itself was exempt from the staleness check, so one clean
    # frame restored health, and a returning 0x02 was admitted as a pause.
    for first_viol in ((0, 0, 0, 0), self.TI_ALL_02):
      with self.subTest(first_viol=first_viol):
        self._reset_safety_hooks()
        self.safety.set_timer(1_000_000)
        self._healthy()
        self.safety.set_timer(1_000_000 + 200001)       # no frame of any kind in between
        self._rx_raw(self._ti2_feedback_msg(viol=first_viol))
        self.assertTrue(self.safety.get_mazda_ti_unhealthy())
        self.assertFalse(self.safety.get_mazda_ti_lateral_paused())
        self._healthy(self.TI_RECOVERY_FRAMES - 1)
        self._rx_raw(self._pcm_status_msg(False))
        self._rx_raw(self._pcm_status_msg(True))
        self.assertEqual(first_viol != (0, 0, 0, 0), bool(self.safety.get_mazda_ti_unhealthy()))

  def test_feedback_timeout_boundary(self):
    for gap, stale in ((200000, False), (200001, True)):
      with self.subTest(gap=gap):
        self._reset_safety_hooks()
        self.safety.set_timer(1_000_000)
        self._healthy()
        self.safety.set_timer(1_000_000 + gap)
        self._rx_raw(self._ti2_feedback_msg())
        self.assertEqual(stale, bool(self.safety.get_mazda_ti_unhealthy()))

  # --- GEN2 0x220 modified-command contract (safety plan 5.1, 5.2, 5.4) ---
  # Raw frames are built bit by bit rather than through the DBC packer, so these
  # tests also pin the native decode. ACCEL_CMD is d2 bit 0, d3, d4 bits 7..5;
  # HOLD is d6 bit 1, RESUME d6 bit 6; d7 is the checksum. The base payload is an
  # engaged stock frame from the target car's closed-course drive.
  ACC_BASE = bytes.fromhex('520004f87284006e')
  ACC_APPROVED = bytes([0x00, 0x00, 0x01, 0xFF, 0xE0, 0x00, 0x42, 0x00])

  @staticmethod
  def _acc_raw(base, accel=None, hold=None, resume=None, counter=None):
    d = bytearray(base[:7])
    if accel is not None:
      d[2] = (d[2] & 0xFE) | ((accel >> 11) & 0x01)
      d[3] = (accel >> 3) & 0xFF
      d[4] = (d[4] & 0x1F) | ((accel & 0x07) << 5)
    if hold is not None:
      d[6] = (d[6] & ~0x02 & 0xFF) | (0x02 if hold else 0)
    if resume is not None:
      d[6] = (d[6] & ~0x40 & 0xFF) | (0x40 if resume else 0)
    if counter is not None:
      d[0] = (d[0] & 0x0F) | ((counter & 0x0F) << 4)
    d.append((0x2A + sum(d)) % 256)
    return bytes(d)

  def _gen2_acc_only(self):
    if not (self.SAFETY_PARAM & MazdaSafetyFlags.GEN2):
      self.skipTest("GEN2 0x220 contract only")

  def _acc_step(self, now_us, stock, moving=False):
    # One healthy 20 ms ACC period: TI2 feedback, speed and a fresh stock frame.
    self.safety.set_timer(now_us)
    self._rx_raw(self._ti2_feedback_msg())
    self._rx_raw(self._speed_msg(1.0 if moving else 0.0))
    self._rx_raw(make_msg(0, 0x220, dat=stock))

  def _acc_ready(self, controls=1, stock=None, moving=False):
    self._gen2_acc_only()
    self.safety.set_timer(1000000)
    self._healthy()
    stock = stock or self.ACC_BASE
    self._acc_step(1000000, stock, moving)
    self.safety.set_controls_allowed(controls)
    return stock

  def _tx_acc(self, dat, bus=2):
    return self._tx(make_msg(bus, 0x220, dat=dat))

  def test_acc_raw_layout_matches_dbc(self):
    # The native decode and the tests' bit builder must agree with the DBC.
    self._gen2_acc_only()
    for accel in (0, 1, 1299, 1300, 2000, 2047, 2048, 2400, 2401, 4095):
      packed = acc_bytes({"ACCEL_CMD": accel})
      self.assertEqual(self._acc_raw(bytes(8), accel=accel), packed, accel)
    for sig, kw in (("HOLD", {"hold": 1}), ("RESUME", {"resume": 1})):
      packed = acc_bytes({sig: 1})
      self.assertEqual(self._acc_raw(bytes(8), **kw), packed, sig)

  def test_acc_exact_stock_allowed_disengaged(self):
    stock = self._acc_ready(controls=0, moving=True)
    self.assertTrue(self._tx_acc(stock))
    # Stock HOLD/RESUME and an out-of-envelope stock value are stock behaviour,
    # not openpilot commands, and still relay exactly while moving.
    for s in (self._acc_raw(stock, hold=1), self._acc_raw(stock, resume=1), self._acc_raw(stock, accel=4000)):
      self._acc_step(1020000, s, moving=True)
      self.assertTrue(self._tx_acc(s), s.hex())
      self.assertFalse(self._tx_acc(self._acc_raw(s, accel=2000)), s.hex())

  def test_acc_exact_stock_allowed_engaged(self):
    stock = self._acc_ready(controls=1, moving=True)
    self.assertTrue(self._tx_acc(stock))
    for s in (self._acc_raw(stock, hold=1), self._acc_raw(stock, resume=1), self._acc_raw(stock, accel=4000)):
      self._acc_step(1020000, s, moving=True)
      self.assertTrue(self._tx_acc(s), s.hex())

  def test_acc_previous_stock_frame_is_a_passthrough(self):
    # The target car's drive shows the host relaying the stock frame one period
    # behind the newest one in up to 61% of frames. Only that one previous frame
    # qualifies; two back is a replay.
    a = self._acc_ready(controls=0)
    b = self._acc_raw(a, counter=6)
    c = self._acc_raw(a, counter=7)
    self._acc_step(1020000, b)
    self.assertTrue(self._tx_acc(a))
    self.assertTrue(self._tx_acc(b))
    self._acc_step(1040000, c)
    self.assertFalse(self._tx_acc(a))
    self.assertTrue(self._tx_acc(b))
    self.assertTrue(self._tx_acc(c))

  def test_acc_corrupt_stock_frame_invalidates_both_references(self):
    a = self._acc_ready(controls=1)
    b = self._acc_raw(a, counter=6)
    self._acc_step(1020000, b)
    self._rx_raw(make_msg(0, 0x220, dat=b[:7] + bytes([b[7] ^ 0x01])))
    for dat in (a, b, self._acc_raw(b, accel=2100)):
      self.assertFalse(self._tx_acc(dat), dat.hex())

  def test_acc_stale_previous_stock_frame_rejected(self):
    a = self._acc_ready(controls=0)
    b = self._acc_raw(a, counter=6)
    self._acc_step(1020000, b)
    self.safety.set_timer(1000000 + 200001)
    self.assertFalse(self._tx_acc(a))
    self.assertTrue(self._tx_acc(b))
    self.safety.set_timer(1020000 + 200001)
    self.assertFalse(self._tx_acc(b))

  def test_acc_modified_requires_controls_allowed(self):
    stock = self._acc_ready(controls=0)
    modified = self._acc_raw(stock, accel=2100)
    self.assertFalse(self._tx_acc(modified))
    self.safety.set_controls_allowed(1)
    self.assertTrue(self._tx_acc(modified))

  def test_acc_modified_requires_longitudinal_allowed(self):
    # controls_allowed alone is not the longitudinal axis once a policy exists.
    stock = self._acc_ready(controls=1)
    modified = self._acc_raw(stock, accel=2100)
    self.safety.set_aol_test_permission_mask(0x1)
    self.safety.set_controls_allowed(1)
    self.assertFalse(self._tx_acc(modified))
    self.assertTrue(self._tx_acc(stock))
    self.safety.set_aol_test_permission_mask(0x3)
    self.safety.set_controls_allowed(1)
    self.assertTrue(self._tx_acc(modified))
    self.safety.clear_aol_test_policy()

  def test_acc_modified_requires_fresh_stock_reference(self):
    self._gen2_acc_only()
    self.safety.set_timer(1000000)
    self._healthy()
    self.safety.set_controls_allowed(1)
    self.assertFalse(self._tx_acc(self._acc_raw(self.ACC_BASE, accel=2000)))
    self._acc_step(1000000, self.ACC_BASE)
    self.assertTrue(self._tx_acc(self._acc_raw(self.ACC_BASE, accel=2100)))
    self.safety.set_timer(1000000 + 150000)
    self._rx_raw(self._ti2_feedback_msg())
    self.safety.set_timer(1000000 + 200001)
    self._rx_raw(self._ti2_feedback_msg())
    self.assertFalse(self._tx_acc(self._acc_raw(self.ACC_BASE, accel=2100)))

  def test_acc_ti_fault_only_allows_stock_path(self):
    stock = self._acc_ready(controls=1)
    modified = self._acc_raw(stock, accel=2100)
    self.assertTrue(self._tx_acc(modified))
    self._rx_raw(self._ti2_feedback_msg(viol=(0, 1, 0, 0)))
    self.safety.set_controls_allowed(1)  # even a racing re-engagement
    self.assertFalse(self._tx_acc(modified))
    self.assertTrue(self._tx_acc(stock))

  def test_acc_accel_limits(self):
    stock = self._acc_ready(controls=1)
    for accel, allowed in ((1300, True), (2400, True), (1299, False), (2401, False),
                           (0, False), (1000, False), (3000, False), (4000, False), (4095, False)):
      self.assertEqual(allowed, self._tx_acc(self._acc_raw(stock, accel=accel)), accel)

  def test_acc_neutral_value(self):
    # 2000 is the zero-acceleration command (stock cruise holds speed at about
    # 1990-2005). A neutral command differing from stock is a modification.
    stock = self._acc_ready(controls=0, stock=self._acc_raw(self.ACC_BASE, accel=1995))
    neutral = self._acc_raw(stock, accel=2000)
    self.assertFalse(self._tx_acc(neutral))
    self.safety.set_controls_allowed(1)
    self.assertTrue(self._tx_acc(neutral))

  def test_acc_unknown_actuation_fields_rejected(self):
    # Every payload bit outside ACCEL_CMD/HOLD/RESUME must equal the stock
    # reference, even with full authority and a recomputed checksum.
    stock = self._acc_ready(controls=1)
    base = self._acc_raw(stock, accel=2100)
    self.assertTrue(self._tx_acc(base))
    for byte in range(7):
      for bit in range(8):
        if self.ACC_APPROVED[byte] & (1 << bit):
          continue
        d = bytearray(base[:7])
        d[byte] ^= 1 << bit
        d.append((0x2A + sum(d)) % 256)
        self.assertFalse(self._tx_acc(bytes(d)), f"byte {byte} bit {bit}")

  def test_acc_modified_aeb_fields_rejected(self):
    # No 0x220 bit is verified as AEB, so all of them are protected. Named fields
    # that a modified command must never change: the ACC enable flags and the
    # observed rolling value in byte 0.
    self._gen2_acc_only()
    stock_values = {"ACCEL_CMD": 2000, "ACC_ENABLED": 1, "ACC_ENABLED_2": 1, "NEW_SIGNAL_1": 1, "NEW_SIGNAL_4": 5}
    stock = acc_bytes(stock_values)
    self._acc_ready(controls=1, stock=stock)
    self.assertTrue(self._tx_acc(acc_bytes(dict(stock_values, ACCEL_CMD=2100))))
    for sig, value in (("ACC_ENABLED", 0), ("ACC_ENABLED_2", 0), ("NEW_SIGNAL_1", 0), ("NEW_SIGNAL_3", 1),
                       ("NEW_SIGNAL_7", 1), ("NEW_SIGNAL_8", 3), ("NEW_SIGNAL_13", 2), ("NEW_SIGNAL_18", 1)):
      values = dict(stock_values, ACCEL_CMD=2100, **{sig: value})
      self.assertFalse(self._tx_acc(acc_bytes(values)), sig)

  def test_acc_bad_counter_rejected(self):
    # Byte 0's high nibble steps by one per stock frame on the target car. It is
    # not approved for modification, so a modified frame must carry the counter
    # of a fresh stock frame; no independent counter rule is invented.
    stock = self._acc_ready(controls=1)
    self.assertTrue(self._tx_acc(self._acc_raw(stock, accel=2100)))
    for counter in range(16):
      if counter == stock[0] >> 4:
        continue
      self.assertFalse(self._tx_acc(self._acc_raw(stock, accel=2100, counter=counter)), counter)

  def test_acc_hold_only_at_standstill(self):
    stock = self._acc_ready(controls=1, moving=True)
    self.assertFalse(self._tx_acc(self._acc_raw(stock, hold=1)))
    self.assertTrue(self._tx_acc(self._acc_raw(stock, accel=1900)))
    self._acc_step(1020000, stock, moving=False)
    self.assertTrue(self._tx_acc(self._acc_raw(stock, hold=1)))
    self.assertTrue(self._tx_acc(self._acc_raw(stock, accel=1900, hold=1)))

  def test_acc_resume_only_at_standstill(self):
    stock = self._acc_ready(controls=1, moving=True)
    self.assertFalse(self._tx_acc(self._acc_raw(stock, resume=1)))
    self._acc_step(1020000, stock, moving=False)
    self.assertTrue(self._tx_acc(self._acc_raw(stock, resume=1)))
    # A standstill threshold of 0.1 kph: raw speed 10 is stopped, 11 is moving.
    self._rx_raw(self._speed_msg(0.11))
    self.assertFalse(self._tx_acc(self._acc_raw(stock, resume=1)))
    self._rx_raw(self._speed_msg(0.10))
    self.assertTrue(self._tx_acc(self._acc_raw(stock, resume=1)))

  def test_acc_hold_resume_require_current_standstill_evidence(self):
    # vehicle_moving defaults false, which is safe for steering but not proof of
    # standstill. Keep TI2 and the stock reference fresh so only SPEED evidence
    # can be responsible for the decision.
    self._gen2_acc_only()
    stock = self.ACC_BASE
    hold = self._acc_raw(stock, hold=1)
    resume = self._acc_raw(stock, resume=1)

    fed = [0]

    def refresh(now_us, speed=None):
      # TI2 feedback every 100 ms up to now: a longer gap is itself a TI2 fault.
      for t in range(fed[0] + 100000, now_us, 100000):
        self.safety.set_timer(t)
        self._rx_raw(self._ti2_feedback_msg())
      fed[0] = now_us
      self.safety.set_timer(now_us)
      self._rx_raw(self._ti2_feedback_msg())
      if speed is not None:
        self._rx_raw(self._speed_msg(speed))
      self._rx_raw(make_msg(0, 0x220, dat=stock))
      self.safety.set_controls_allowed(1)

    refresh(100000)
    self.assertFalse(self._tx_acc(hold))    # startup window: no SPEED observation,
    self.assertFalse(self._tx_acc(resume))  # and a default ts is not stale evidence
    refresh(1000000)
    self.assertFalse(self._tx_acc(hold))
    self.assertFalse(self._tx_acc(resume))
    refresh(1020000, speed=0.0)
    self.assertTrue(self._tx_acc(hold))
    self.assertTrue(self._tx_acc(resume))
    refresh(1320000)                        # exactly 300 ms old: still current
    self.assertTrue(self._tx_acc(hold))
    self.assertTrue(self._tx_acc(resume))
    refresh(1320001)                        # stale standstill is not standstill
    self.assertFalse(self._tx_acc(hold))
    self.assertFalse(self._tx_acc(resume))
    refresh(1340000, speed=1.0)             # fresh moving evidence also denies
    self.assertFalse(self._tx_acc(hold))
    self.assertFalse(self._tx_acc(resume))

  def test_acc_stock_hold_resume_passthrough(self):
    for controls in (0, 1):
      stock = self._acc_ready(controls=controls, stock=self._acc_raw(self.ACC_BASE, hold=1, resume=1), moving=True)
      self.assertTrue(self._tx_acc(stock), controls)
      self.assertEqual(bool(controls), self._tx_acc(self._acc_raw(stock, hold=0, resume=0)), controls)

  def test_acc_gas_override_uses_stock_path(self):
    stock = self._acc_ready(controls=1)
    modified = self._acc_raw(stock, accel=2100)
    self.assertTrue(self._tx_acc(modified))
    self._rx_raw(self._user_gas_msg(1))
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertFalse(self._tx_acc(modified))
    self.assertTrue(self._tx_acc(stock))
    self._rx_raw(self._user_gas_msg(0))
    self.assertTrue(self._tx_acc(modified))

  def test_acc_bad_checksum_rejected(self):
    stock = self._acc_ready(controls=1)
    for dat in (stock, self._acc_raw(stock, accel=2100)):
      self.assertTrue(self._tx_acc(dat))
      for flip in (0x01, 0x80, 0xFF):
        self.assertFalse(self._tx_acc(dat[:7] + bytes([dat[7] ^ flip])), (dat.hex(), flip))

  def test_acc_wrong_bus_rejected(self):
    stock = self._acc_ready(controls=1)
    for dat in (stock, self._acc_raw(stock, accel=2100)):
      self.assertTrue(self._tx_acc(dat))
      for bus in (0, 1):
        self.assertFalse(self._tx_acc(dat, bus=bus), bus)
      self.assertFalse(self._tx_acc(dat[:7]))

  def _acc_drive(self, plan):
    """Walk 20 ms periods; plan yields (controls, kind) with kind stock/op/blend."""
    stock = self._acc_ready(controls=0)
    now = 1000000
    for i, (controls, kind) in enumerate(plan):
      now += 20000
      stock = self._acc_raw(stock, accel=1990 + (i % 7), counter=(stock[0] >> 4) + 1)
      self._acc_step(now, stock)
      self.safety.set_controls_allowed(controls)
      accel = {"stock": None, "op": 2150, "blend": 1990 + (i % 7) + 40}[kind]
      dat = stock if accel is None else self._acc_raw(stock, accel=accel)
      self.assertTrue(self._tx_acc(dat), (i, controls, kind))
      if not controls:
        self.assertFalse(self._tx_acc(self._acc_raw(stock, accel=2150)), i)

  def test_acc_stock_to_op_transition(self):
    self._acc_drive([(0, "stock")] * 3 + [(1, "stock")] * 2 + [(1, "op")] * 5)

  def test_acc_op_to_stock_transition(self):
    self._acc_drive([(1, "op")] * 5 + [(1, "stock")] * 2 + [(0, "stock")] * 5)

  def test_acc_blended_stock_to_op_and_back(self):
    # BlendedACC filters between stock and openpilot values while engaged; every
    # blended value is an ordinary bounded modification of the fresh frame.
    self._acc_drive([(0, "stock")] * 2 + [(1, "blend")] * 4 + [(1, "op")] * 3 +
                    [(1, "blend")] * 4 + [(0, "stock")] * 3)


class TestMazda2023Safety(TestMazda2019Safety):
  """Gen3 (2023+ Mazda 3/CX-30) — TI steering only; no openpilot longitudinal."""
  DBC = "mazda_2023"
  SAFETY_PARAM = MazdaSafetyFlags.GEN3
  TX_MSGS = [[0x249, 1]]
  RELAY_MALFUNCTION_ADDRS = {}
  FWD_BLACKLISTED_ADDRS = {}

  def _stock_acc_msg(self, accel_cmd=2000):
    # GEN3's DBC names 0x21E "ACC"; build raw 0x220 frames explicitly so these
    # tests actually exercise the GEN2 ACC address, not the GEN3 alias.
    # These are address-rejection checks; payload only encodes accel_cmd so
    # stock and modified variants differ.
    return make_msg(0, 0x220, dat=bytes([accel_cmd & 0xFF, (accel_cmd >> 8) & 0xFF, 0, 0, 0, 0, 0, 0]))

  def _acc_cmd_msg(self, accel_cmd=2000, hold=0, resume=0):
    return make_msg(2, 0x220, dat=bytes([accel_cmd & 0xFF, (accel_cmd >> 8) & 0xFF, 0, 0, 0, 0, 0, 0]))

  def _speed_msg(self, speed):
    # Gen3 wheel speeds arrive on the camera bus (bus 2)
    return self.packer.make_can_msg_safety("WHEEL_SPEEDS", 2, {"FL": speed})

  def _user_brake_msg(self, brake):
    values = {"BRAKE_PRESSED": brake, "BRAKE_NOT_PRESSED": int(not brake)}
    return self.packer.make_can_msg_safety("BRAKE_PEDAL", 0, values)

  def _pcm_status_msg(self, enable):
    # Gen3 CRUZE_STATE lives on the aux bus, engaged state == 3
    return self.packer.make_can_msg_safety("CRUZE_STATE", 1, {"CRZ_STATE": 3 if enable else 1})

  def test_acc_exact_stock_passthrough_allowed(self):
    # Gen3 must not transmit 0x220 at all, even a byte-exact stock copy
    self._rx(self._stock_acc_msg(accel_cmd=2000))
    self.safety.set_controls_allowed(0)
    self.assertFalse(self._tx(self._acc_cmd_msg(accel_cmd=2000)))

  def test_ti_violation_blocks_steering_and_modified_acc(self):
    # GEN3 is steering-only: BOTH modified and stock-copy ACC must be rejected.
    self._rx(self._stock_acc_msg())
    self.safety.set_controls_allowed(1)
    self._rx(self._ti2_feedback_msg(viol=(1, 0, 0, 0)))
    self.assertFalse(self._tx(self._torque_cmd_msg(45)))
    self.assertTrue(self._tx(self._torque_cmd_msg(0)))
    self.assertFalse(self._tx(self._acc_cmd_msg(accel_cmd=3000)))
    self.assertFalse(self._tx(self._acc_cmd_msg(accel_cmd=2000)))

  def test_gen3_acc_not_in_tx_whitelist(self):
    self.assertFalse(self._tx(self._acc_cmd_msg(accel_cmd=2000)))

  def test_gen3_brake_while_moving_blocks_engage(self):
    # A5: wheel speed arrives on bus 2. If the rx hook only reads bus 0,
    # vehicle_moving stays false and brake-while-moving engages (unsafe).
    self._rx(self._speed_msg(30.0))
    self._rx(self._user_brake_msg(1))
    self.assertTrue(self.safety.get_brake_pressed_prev())
    self._rx(self._pcm_status_msg(True))
    self.assertFalse(self.safety.get_controls_allowed())


if __name__ == "__main__":
  unittest.main()
