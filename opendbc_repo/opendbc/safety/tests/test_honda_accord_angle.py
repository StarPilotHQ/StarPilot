#!/usr/bin/env python3
"""Accord angle-loop EPS: the panda bounds the 0xE4 angle setpoint by the lateral accel and jerk limits."""
import math
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.honda.carcontroller import CarController
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR, DBC, CarControllerParams, HondaSafetyFlags
from opendbc.car.lateral import MAX_LATERAL_ACCEL, get_max_angle_delta_vm, get_max_angle_vm
from opendbc.car.structs import CarParams
from opendbc.car.vehicle_model import VehicleModel, calc_slip_factor
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests import test_honda  # the module, so its test classes are not collected again here

P = CarControllerParams
ARM = P.ANGLE_ARM
DT_US = 10_000  # 0xE4 is sent at 100 Hz
SPEEDS = (1.0, 3.0, 5.0, 8.0, 10.0, 12.0, 17.5, 22.0, 26.9, 31.0, 38.0)


def accord_cp():
  fw = [CarParams.CarFw(ecu=CarParams.Ecu.eps, fwVersion=b"39990-TVA,A16C", address=0x18DA30F1, subAddress=0)]
  return CarInterface.get_params(CAR.HONDA_ACCORD, gen_empty_fingerprint(), fw, False, False, False)


class TestHondaAccordAngleSetpoint(unittest.TestCase):
  def setUp(self):
    self.fixture = test_honda.TestHondaBoschAltBrakeSafety('test_spam_cancel_safety_check')
    self.fixture.setUp()
    self.safety = self.fixture.safety
    self.packer = self.fixture.packer
    self.CP = accord_cp()
    self.angle_flag = self.CP.safetyConfigs[0].safetyParam
    self.assertNotEqual(self.angle_flag, 0)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hondaBosch, self.angle_flag | 1), 0)
    self.safety.init_tests()
    self.VM = VehicleModel(self.CP)
    self.params = P(self.CP)
    self.t = 1_000_000
    self.safety.set_timer(self.t)

  # ---- helpers
  def _angle_frame(self, raw, steer_req, arm=ARM, bus=None):
    values = {"STEER_TORQUE": raw, "STEER_TORQUE_REQUEST": steer_req, "STEER_CONTROL_ARM": arm}
    return self.packer.make_can_msg_safety("STEERING_CONTROL", self.fixture.STEER_BUS if bus is None else bus, values)

  def _tx(self, raw, steer_req=True, arm=ARM, bus=None):
    self.t += DT_US
    self.safety.set_timer(self.t)
    return self.fixture._tx(self._angle_frame(raw, steer_req, arm, bus))

  def _rx_speed(self, v_ms):
    self.fixture._rx(self.fixture._speed_msg(v_ms * 3.6))

  def _rx_wheel(self, raw):
    # STEER_ANGLE is -0.1 deg per count: the raw count equals the 0xE4 raw for the same angle
    self.fixture._rx(self.packer.make_can_msg_safety("STEERING_SENSORS", self.fixture.PT_BUS, {"STEER_ANGLE": -raw / 10.0}))

  def _settle(self, v_ms, wheel_raw=0):
    for _ in range(6):
      self._rx_speed(v_ms)
      self._rx_wheel(wheel_raw)

  def _panda_max_angle_raw(self, v_ms):
    # the panda evaluates the model 1 m/s below its slowest speed sample and allows one count of rounding
    return int(get_max_angle_vm(max(v_ms - 1.0, 1.0), self.VM, self.params) * 10 + 1.0)

  def _panda_max_delta_raw(self, v_ms):
    return int(get_max_angle_delta_vm(max(v_ms - 1.0, 1.0), self.VM, self.params) * 10 + 1.0)

  def test_stock_profiles_and_incompatible_flag_combinations_reject_angle_frames(self):
    is_debug = self.safety.set_safety_hooks(CarParams.SafetyModel.allOutput, 0) == 0
    ordinary = (0, 1, 2, 3, 2082, 2083)
    invalid = (self.angle_flag | 8, self.angle_flag | 16, self.angle_flag | 128)
    if not is_debug:
      invalid += tuple(self.angle_flag | word for word in ordinary if word & 2)
    for word in ordinary + invalid:
      with self.subTest(word=word):
        self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hondaBosch, word), 0)
        self.safety.init_tests()
        self._settle(10.0)
        self.safety.set_controls_allowed(True)
        bus = int(bool(is_debug and word & 2))
        self.assertFalse(self._tx(10, bus=bus), word)
        self.assertFalse(self._tx(0, steer_req=False, bus=bus), word)
        if word in ordinary:
          self.assertTrue(self._tx(10, arm=0, bus=bus), word)

  def test_flag_preserves_lateral_on_supported_classic_bosch_configurations(self):
    is_debug = self.safety.set_safety_hooks(CarParams.SafetyModel.allOutput, 0) == 0
    base_words = (0, 1, 2, 3, 2082, 2083) if is_debug else (0, 1)
    for base in base_words:
      with self.subTest(base=base):
        self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.hondaBosch, self.angle_flag | base), 0)
        self.safety.init_tests()
        self._settle(10.0)
        self.safety.set_controls_allowed(True)
        self.assertTrue(self._tx(10, bus=int(bool(base & 2))), base)

  # ---- the model the panda uses is the one openpilot uses
  def test_vehicle_model_constants_match_openpilot(self):
    src = (Path(__file__).parents[1] / "modes" / "honda.h").read_text(encoding="utf-8")
    block = src[src.index("HONDA_ACCORD_ANGLE_PARAMS = {"):]
    block = block[:block.index("};")]
    read = {k: float(v) for k, v in re.findall(r"\.(\w+)\s*=\s*(-?[\d.]+)", block)}
    self.assertAlmostEqual(read["slip_factor"], calc_slip_factor(self.VM), places=12)
    self.assertAlmostEqual(read["steer_ratio"], self.CP.steerRatio, places=5)
    self.assertAlmostEqual(read["wheelbase"], self.CP.wheelbase, places=5)
    self.assertAlmostEqual(MAX_LATERAL_ACCEL, 3.589, places=3)
    self.assertEqual(self.params.ANGLE_LIMITS.MAX_LATERAL_ACCEL, MAX_LATERAL_ACCEL)

  # ---- lateral acceleration limit
  def test_setpoint_beyond_the_lateral_accel_limit_is_blocked(self):
    for v in SPEEDS:
      limit = self._panda_max_angle_raw(v)
      if limit > 4000:
        continue  # below ~5 m/s the 400 deg ceiling binds first (covered below)
      for sign in (-1, 1):
        self._settle(v)
        self.safety.set_controls_allowed(True)
        self.safety.set_desired_angle_last(sign * limit)
        self.assertTrue(self._tx(sign * limit), (v, sign))
        self.assertFalse(self._tx(sign * (limit + 1)), (v, sign))
        # the first blocked frame resets the panda to the wheel; a setpoint this far out stays blocked
        self.assertFalse(self._tx(sign * (limit + 1)), (v, sign))

  def test_the_old_order_counterexample_is_blocked(self):
    # 26.9 m/s, wheel held at 42 deg: the old code sent 29.8 deg (5.4 m/s^2) against a 19.8 deg limit
    v = 26.9
    self._settle(v, wheel_raw=420)
    self.safety.set_controls_allowed(True)
    self.safety.set_desired_angle_last(298)
    self.assertFalse(self._tx(298))
    self.assertGreater(298, self._panda_max_angle_raw(v))

  def test_setpoint_beyond_400_deg_is_blocked(self):
    self._settle(1.0, wheel_raw=4000)
    self.safety.set_controls_allowed(True)
    for raw, ok in ((4000, True), (4001, False), (-4000, True), (-4001, False), (4096, False)):
      self.safety.set_desired_angle_last(raw)
      self.assertEqual(ok, self._tx(raw), raw)

  # ---- lateral jerk limit
  def test_step_beyond_the_lateral_jerk_limit_is_blocked(self):
    for v in SPEEDS:
      delta = self._panda_max_delta_raw(v)
      for sign in (-1, 1):
        for start in (0, 50):
          if start + delta + 1 > min(self._panda_max_angle_raw(v), 4000):
            continue
          self._settle(v, wheel_raw=sign * start)
          self.safety.set_controls_allowed(True)
          self.safety.set_desired_angle_last(sign * start)
          self.assertTrue(self._tx(sign * (start + delta)), (v, sign, start))
          self.safety.set_desired_angle_last(sign * start)
          self.assertFalse(self._tx(sign * (start + delta + 1)), (v, sign, start))

  def test_a_blocked_frame_restarts_from_the_wheel(self):
    v = 20.0
    self._settle(v, wheel_raw=30)
    self.safety.set_controls_allowed(True)
    self.safety.set_desired_angle_last(30)
    self.assertFalse(self._tx(30 + self._panda_max_delta_raw(v) + 1))
    self.assertEqual(self.safety.get_desired_angle_last(), 30)
    self.assertTrue(self._tx(30 + self._panda_max_delta_raw(v)))

  # ---- request off
  def test_request_off_frames(self):
    v = 15.0
    self._settle(v, wheel_raw=-234)
    for allowed in (True, False):
      self.safety.set_controls_allowed(allowed)
      self.assertTrue(self._tx(0, steer_req=False))        # nothing commanded
      self.assertEqual(self.safety.get_desired_angle_last(), -234)
      self.assertEqual(allowed, self._tx(-234, steer_req=False))  # hold at the wheel: only while lateral is allowed
      self.assertFalse(self._tx(-200, steer_req=False))    # anything else is not the wheel
      self.assertFalse(self._tx(100, steer_req=False))

  def test_request_needs_controls_allowed(self):
    self._settle(15.0, wheel_raw=0)
    self.safety.set_controls_allowed(False)
    self.assertFalse(self._tx(0, steer_req=True))
    self.assertFalse(self._tx(5, steer_req=True))
    self.safety.set_controls_allowed(True)
    self.assertTrue(self._tx(0, steer_req=True))

  def test_engage_from_a_zero_field_starts_at_the_wheel(self):
    # always-on lateral case: the carcontroller sends a zero field until the request, then starts from the wheel
    v = 25.0
    self._settle(v, wheel_raw=150)
    self.safety.set_controls_allowed(True)
    for _ in range(5):
      self.assertTrue(self._tx(0, steer_req=False))
    self.assertTrue(self._tx(150 + self._panda_max_delta_raw(v), steer_req=True))

  # ---- torque frames (arm 0 or 1) are not angle setpoints
  def test_torque_frames_are_unaffected(self):
    self._settle(30.0)
    self.safety.set_controls_allowed(True)
    for arm in (0, 1):
      for raw in (-4096, -1000, 0, 1000, 4096):
        self.assertTrue(self._tx(raw, steer_req=True, arm=arm), (arm, raw))
    self.safety.set_controls_allowed(False)
    self.assertTrue(self._tx(0, steer_req=False, arm=0))
    self.assertFalse(self._tx(1000, steer_req=True, arm=0))

  # ---- message rate
  def test_message_rate_limit(self):
    self._settle(20.0)
    self.safety.set_controls_allowed(True)
    blocked = False
    for _ in range(40):  # 40 frames inside one 250 ms window: more than 100 Hz allows
      blocked |= not self.fixture._tx(self._angle_frame(0, True))
    self.assertTrue(blocked)

  # ---- the carcontroller's own output is what the panda checks, and it always passes
  def _controller_frames(self, v_of, wheel_of, desired_of, frames):
    cc = CarController({Bus.pt: DBC[CAR.HONDA_ACCORD][Bus.pt]}, self.CP)
    blocked = withheld = 0
    for i in range(frames):
      v, wheel = v_of(i), wheel_of(i)
      wheel_raw = math.floor(-10.0 * wheel + 0.5)
      self._rx_speed(v)
      self._rx_wheel(wheel_raw)
      active = i >= 12  # as on the road, request-off frames come first: they fill the panda's samples
      CC = SimpleNamespace(actuators=SimpleNamespace(steeringAngleDeg=desired_of(i)), latActive=active, enabled=True)
      # carState reads the angle off the same STEER_ANGLE counts and the speed off the wheels
      CS = SimpleNamespace(out=SimpleNamespace(steeringAngleDeg=-wheel_raw / 10.0, vEgoRaw=v,
                                               cruiseState=SimpleNamespace(available=True)))
      _, raw = cc._update_angle(CC, CS)
      withheld += active and not cc.angle_steer_req
      blocked += not self._tx(raw, steer_req=cc.angle_steer_req)
    return blocked, withheld

  def test_carcontroller_output_is_never_blocked(self):
    self.safety.set_controls_allowed(True)
    rng = np.random.default_rng(1)
    cases = {
      "lane keeping": (lambda i: 28.0, lambda i: 2.0 * math.sin(i / 60.0), lambda i: 3.0 * math.sin(i / 50.0)),
      "saturating request": (lambda i: 20.0, lambda i: 0.0, lambda i: 300.0 * math.sin(i / 100.0)),
      "random request": (lambda i: 12.0, lambda i: 10.0 * math.sin(i / 40.0), lambda i: float(rng.uniform(-400, 400))),
      "tight turn": (lambda i: 4.0, lambda i: min(i * 2.0, 380.0), lambda i: 400.0),
      "driver past the limit": (lambda i: 26.9, lambda i: min(i * 0.3, 45.0), lambda i: 0.0),
      "driver yank": (lambda i: 17.5, lambda i: 60.0 * math.sin(i / 8.0), lambda i: 5.0),
      "braking": (lambda i: max(30.0 - i * 0.03, 2.0), lambda i: 5.0, lambda i: 400.0),
      "accelerating": (lambda i: min(3.0 + i * 0.03, 35.0), lambda i: 0.0, lambda i: -400.0),
    }
    for name, (v_of, wheel_of, desired_of) in cases.items():
      self.safety.set_controls_allowed(True)
      blocked, withheld = self._controller_frames(v_of, wheel_of, desired_of, 900)
      self.assertEqual(0, blocked, name)
      # the request is withheld only where the wheel is past the limit when it would start: the yank reaches 60 deg
      # against a 38 deg limit.  Everywhere else every frame carries the request.
      self.assertEqual(name == "driver yank", withheld > 0, (name, withheld))


class TestHondaAngleSetpointOnOtherBosch(unittest.TestCase):
  """A radarless or CAN FD Honda has no angle-loop EPS: an armed frame is never sent, and is blocked if it is."""

  def test_armed_frame_is_blocked(self):
    fixture = test_honda.TestHondaBoschRadarlessSafety('test_spam_cancel_safety_check')
    fixture.setUp()
    fixture.safety.set_safety_hooks(CarParams.SafetyModel.hondaBosch, HondaSafetyFlags.RADARLESS)
    fixture.safety.init_tests()
    fixture.safety.set_controls_allowed(True)
    for arm, ok in ((0, True), (1, True), (ARM, False)):
      # bytes 0-1 zero, STEER_TORQUE_REQUEST set, STEER_CONTROL_ARM in byte 2 bits 3:2
      packet = libsafety_py.make_CANPacket(0xE4, fixture.STEER_BUS, bytes([0, 0, 0x80 | (arm << 2), 0, 0]))
      self.assertEqual(ok, fixture._tx(packet), arm)


if __name__ == "__main__":
  unittest.main()
