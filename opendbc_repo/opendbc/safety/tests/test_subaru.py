#!/usr/bin/env python3
import enum
import unittest

import numpy as np

from opendbc.car.lateral import get_max_angle_vm
from opendbc.car.subaru.carcontroller import get_safety_CP
from opendbc.car.subaru.values import CarControllerParams, SubaruSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.car.vehicle_model import VehicleModel
from opendbc.safety import ALTERNATIVE_EXPERIENCE
from opendbc.safety.tests.libsafety import libsafety_py
import opendbc.safety.tests.common as common
from opendbc.safety.tests.common import CANPackerSafety, away_round, round_speed
from functools import partial


class SubaruMsg(enum.IntEnum):
  Brake_Status      = 0x13c
  CruiseControl     = 0x240
  Throttle          = 0x40
  Steering_2        = 0x11a
  Steering_Torque   = 0x119
  Wheel_Speeds      = 0x13a
  Brake_Pedal       = 0x139
  ES_LKAS           = 0x122
  ES_LKAS_ANGLE     = 0x124
  ES_Brake          = 0x220
  ES_Distance       = 0x221
  ES_Status         = 0x222
  ES_DashStatus     = 0x321
  ES_LKAS_State     = 0x322
  ES_Infotainment   = 0x323
  ES_LKAS_Alert     = 0x3C4
  ES_UDS_Request    = 0x787
  ES_HighBeamAssist = 0x121
  ES_STATIC_1       = 0x22a
  ES_STATIC_2       = 0x325
  Dashlights        = 0x390


SUBARU_MAIN_BUS = 0
SUBARU_ALT_BUS  = 1
SUBARU_CAM_BUS  = 2


def lkas_tx_msgs(alt_bus, lkas_msg=SubaruMsg.ES_LKAS):
  return [[lkas_msg,                    SUBARU_MAIN_BUS],
          [SubaruMsg.ES_Distance,       alt_bus],
          [SubaruMsg.ES_DashStatus,     SUBARU_MAIN_BUS],
          [SubaruMsg.ES_LKAS_State,     SUBARU_MAIN_BUS],
          [SubaruMsg.ES_Infotainment,   SUBARU_MAIN_BUS]]


def long_tx_msgs(alt_bus):
  return [[SubaruMsg.ES_Brake,          alt_bus],
          [SubaruMsg.ES_Status,         alt_bus]]


def gen2_long_additional_tx_msgs():
  return [[SubaruMsg.ES_UDS_Request,    SUBARU_CAM_BUS],
          [SubaruMsg.ES_HighBeamAssist, SUBARU_MAIN_BUS],
          [SubaruMsg.ES_STATIC_1,       SUBARU_MAIN_BUS],
          [SubaruMsg.ES_STATIC_2,       SUBARU_MAIN_BUS]]


def fwd_blacklisted_addr(lkas_msg=SubaruMsg.ES_LKAS):
  return {SUBARU_CAM_BUS: [lkas_msg, SubaruMsg.ES_DashStatus, SubaruMsg.ES_LKAS_State, SubaruMsg.ES_Infotainment]}


class TestSubaruSafetyBase(common.CarSafetyTest):
  FLAGS = 0
  RELAY_MALFUNCTION_ADDRS = {SUBARU_MAIN_BUS: (SubaruMsg.ES_LKAS, SubaruMsg.ES_DashStatus, SubaruMsg.ES_LKAS_State,
                                               SubaruMsg.ES_Infotainment)}
  FWD_BLACKLISTED_ADDRS = fwd_blacklisted_addr()

  MAX_RT_DELTA = 940

  DRIVER_TORQUE_ALLOWANCE = 60
  DRIVER_TORQUE_FACTOR = 50

  ALT_MAIN_BUS = SUBARU_MAIN_BUS
  ALT_CAM_BUS = SUBARU_CAM_BUS

  DEG_TO_CAN = 100

  INACTIVE_GAS = 1818

  def setUp(self):
    self.packer = CANPackerSafety("subaru_global_2017_generated")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.subaru, self.FLAGS)
    self.safety.init_tests()

  def _set_prev_torque(self, t):
    self.safety.set_desired_torque_last(t)
    self.safety.set_rt_torque_last(t)

  def _torque_driver_msg(self, torque):
    values = {"Steer_Torque_Sensor": torque}
    return self.packer.make_can_msg_safety("Steering_Torque", 0, values)

  def _speed_msg(self, speed):
    values = {s: speed for s in ["FR", "FL", "RR", "RL"]}
    return self.packer.make_can_msg_safety("Wheel_Speeds", self.ALT_MAIN_BUS, values)

  def _angle_meas_msg(self, angle):
    values = {"Steering_Angle": angle}
    return self.packer.make_can_msg_safety("Steering_Torque", 0, values)

  def _user_brake_msg(self, brake):
    values = {"Brake": brake}
    return self.packer.make_can_msg_safety("Brake_Status", self.ALT_MAIN_BUS, values)

  def _user_gas_msg(self, gas):
    values = {"Throttle_Pedal": gas}
    return self.packer.make_can_msg_safety("Throttle", 0, values)

  def _pcm_status_msg(self, enable):
    values = {"Cruise_Activated": enable}
    return self.packer.make_can_msg_safety("CruiseControl", self.ALT_MAIN_BUS, values)

  def _toggle_aol(self, toggle_on):
    # CruiseControl, Cruise_On is the main on button
    values = {"Cruise_On": 1 if toggle_on else 0}
    return self.packer.make_can_msg_panda("CruiseControl", self.ALT_MAIN_BUS, values)


class TestSubaruStockLongitudinalSafetyBase(TestSubaruSafetyBase):
  def _cancel_msg(self, cancel, cruise_throttle=0):
    values = {"Cruise_Cancel": cancel, "Cruise_Throttle": cruise_throttle}
    return self.packer.make_can_msg_safety("ES_Distance", self.ALT_MAIN_BUS, values)

  def test_cancel_message(self):
    # test that we can only send the cancel message (ES_Distance) with inactive throttle (1818) and Cruise_Cancel=1
    for cancel in [True, False]:
      self._generic_limit_safety_check(partial(self._cancel_msg, cancel), self.INACTIVE_GAS, self.INACTIVE_GAS, 0, 2**12, 1, self.INACTIVE_GAS, cancel)


class TestSubaruLongitudinalSafetyBase(TestSubaruSafetyBase, common.LongitudinalGasBrakeSafetyTest):
  MIN_GAS = 808
  MAX_GAS = 3400
  INACTIVE_GAS = 1818
  MAX_POSSIBLE_GAS = 2**13

  MIN_BRAKE = 0
  MAX_BRAKE = 600
  MAX_POSSIBLE_BRAKE = 2**16

  MIN_RPM = 0
  MAX_RPM = 3600
  MAX_POSSIBLE_RPM = 2**13

  FWD_BLACKLISTED_ADDRS = {2: [SubaruMsg.ES_LKAS, SubaruMsg.ES_Brake, SubaruMsg.ES_Distance,
                               SubaruMsg.ES_Status, SubaruMsg.ES_DashStatus,
                               SubaruMsg.ES_LKAS_State, SubaruMsg.ES_Infotainment]}

  def test_rpm_safety_check(self):
    self._generic_limit_safety_check(self._send_rpm_msg, self.MIN_RPM, self.MAX_RPM, 0, self.MAX_POSSIBLE_RPM, 1)

  def _send_brake_msg(self, brake):
    values = {"Brake_Pressure": brake}
    return self.packer.make_can_msg_safety("ES_Brake", self.ALT_MAIN_BUS, values)

  def _send_gas_msg(self, gas):
    values = {"Cruise_Throttle": gas}
    return self.packer.make_can_msg_safety("ES_Distance", self.ALT_MAIN_BUS, values)

  def _send_rpm_msg(self, rpm):
    values = {"Cruise_RPM": rpm}
    return self.packer.make_can_msg_safety("ES_Status", self.ALT_MAIN_BUS, values)


class TestSubaruTorqueSafetyBase(TestSubaruSafetyBase, common.DriverTorqueSteeringSafetyTest, common.SteerRequestCutSafetyTest):
  MAX_RATE_UP = 50
  MAX_RATE_DOWN = 70
  MAX_TORQUE_LOOKUP = [0], [3071]

  # Safety around steering req bit
  MIN_VALID_STEERING_FRAMES = 7
  MAX_INVALID_STEERING_FRAMES = 1
  STEER_STEP = 2

  def _torque_cmd_msg(self, torque, steer_req=1):
    values = {"LKAS_Output": torque, "LKAS_Request": steer_req}
    return self.packer.make_can_msg_safety("ES_LKAS", SUBARU_MAIN_BUS, values)


class TestSubaruAngleSafetyBase(TestSubaruSafetyBase, common.AngleSteeringSafetyTest):
  ALT_MAIN_BUS = SUBARU_ALT_BUS

  TX_MSGS = lkas_tx_msgs(SUBARU_ALT_BUS, SubaruMsg.ES_LKAS_ANGLE)
  RELAY_MALFUNCTION_ADDRS = {SUBARU_MAIN_BUS: (SubaruMsg.ES_LKAS_ANGLE, SubaruMsg.ES_DashStatus,
                                               SubaruMsg.ES_LKAS_State, SubaruMsg.ES_Infotainment)}
  FWD_BLACKLISTED_ADDRS = fwd_blacklisted_addr(SubaruMsg.ES_LKAS_ANGLE)

  FLAGS = SubaruSafetyFlags.LKAS_ANGLE | SubaruSafetyFlags.GEN2

  STEER_ANGLE_MAX = 650
  DEG_TO_CAN = 100
  ANGLE_RATE_BP = None
  ANGLE_RATE_UP = None
  ANGLE_RATE_DOWN = None
  LATERAL_FREQUENCY = 50

  def setUp(self):
    self.VM = VehicleModel(get_safety_CP())
    self.angle_cmd_cnt = 0
    super().setUp()

  def _get_steer_cmd_angle_max(self, speed):
    if self.FLAGS & SubaruSafetyFlags.LEGACY_2025_ANGLE_LIMITS:
      return self.STEER_ANGLE_MAX
    return get_max_angle_vm(max(speed, 1), self.VM, CarControllerParams)

  def _angle_cmd_msg(self, angle, enabled, increment_timer=True):
    if increment_timer:
      self.safety.set_timer(self.angle_cmd_cnt * int(1e6 / self.LATERAL_FREQUENCY))
      self.angle_cmd_cnt += 1
    values = {"LKAS_Output": angle, "LKAS_Request": enabled, "SET_3": 3}
    return self.packer.make_can_msg_safety("ES_LKAS_ANGLE", SUBARU_MAIN_BUS, values)

  def _angle_meas_msg(self, angle):
    return self.packer.make_can_msg_safety("Steering_2", SUBARU_MAIN_BUS, {"Steering_Angle": angle})

  def _speed_msg(self, speed):
    values = {s: speed * 3.6 for s in ["FR", "FL", "RR", "RL"]}
    return self.packer.make_can_msg_safety("Wheel_Speeds", self.ALT_MAIN_BUS, values)

  def _pcm_status_msg(self, enable):
    bus = SUBARU_ALT_BUS if self.FLAGS & SubaruSafetyFlags.GEN2 else SUBARU_CAM_BUS
    return self.packer.make_can_msg_safety("ES_Status", bus, {"Cruise_Activated": enable})

  def _toggle_aol(self, toggle_on):
    return None

  def _acc_main_msg(self, main_on):
    values = {"Cruise_On": int(main_on)}
    return self.packer.make_can_msg_panda("ES_DashStatus", SUBARU_CAM_BUS, values)

  def test_acc_main_tracks_dash_status(self):
    self.safety.set_alternative_experience(ALTERNATIVE_EXPERIENCE.ALWAYS_ON_LATERAL)

    self._rx(self._acc_main_msg(False))
    self.assertFalse(self.safety.get_acc_main_on())
    self.assertFalse(self.safety.get_aol_allowed())

    self._rx(self._acc_main_msg(True))
    self.assertTrue(self.safety.get_acc_main_on())
    self.assertTrue(self.safety.get_aol_allowed())

  def test_angle_cmd_when_enabled(self):
    pass

  def _setup_speed(self, speed):
    self.safety.init_tests()
    self.safety.set_controls_allowed(True)
    self._reset_speed_measurement(speed + 1)

  def _find_max_allowed_angle_can(self, sign):
    lo, hi = 0, int(self.STEER_ANGLE_MAX * self.DEG_TO_CAN) + 10
    while lo < hi:
      mid = (lo + hi + 1) // 2
      self.safety.set_desired_angle_last(mid * sign)
      if self._tx(self._angle_cmd_msg(mid / self.DEG_TO_CAN * sign, True)):
        lo = mid
      else:
        hi = mid - 1
    return lo

  def _find_max_allowed_delta_can(self, sign):
    lo, hi = 0, int(self.STEER_ANGLE_MAX * self.DEG_TO_CAN) + 10
    while lo < hi:
      mid = (lo + hi + 1) // 2
      self.safety.set_desired_angle_last(0)
      if self._tx(self._angle_cmd_msg(mid / self.DEG_TO_CAN * sign, True)):
        lo = mid
      else:
        hi = mid - 1
    return lo

  def test_lateral_accel_limit(self):
    for speed in np.linspace(1, 40, 40):
      speed = round_speed(away_round(speed * 3.6 / 0.057) * 0.057 / 3.6)
      for sign in (-1, 1):
        self._setup_speed(speed)
        max_can = self._find_max_allowed_angle_can(sign)
        self.safety.set_desired_angle_last(max_can * sign)
        self.assertTrue(self._tx(self._angle_cmd_msg(max_can / self.DEG_TO_CAN * sign, True)))
        if max_can < self.STEER_ANGLE_MAX * self.DEG_TO_CAN:
          over = max_can + 1
          self.safety.set_desired_angle_last(over * sign)
          self.assertFalse(self._tx(self._angle_cmd_msg(over / self.DEG_TO_CAN * sign, True)))

  def test_lateral_jerk_limit(self):
    for speed in np.linspace(1, 40, 40):
      speed = round_speed(away_round(speed * 3.6 / 0.057) * 0.057 / 3.6)
      for sign in (-1, 1):
        self._setup_speed(speed)
        self.assertTrue(self._tx(self._angle_cmd_msg(0, True)))
        max_delta = self._find_max_allowed_delta_can(sign)
        self.safety.set_desired_angle_last(0)
        self.assertTrue(self._tx(self._angle_cmd_msg(max_delta / self.DEG_TO_CAN * sign, True)))
        over = max_delta + 1
        self.safety.set_desired_angle_last(0)
        self.assertFalse(self._tx(self._angle_cmd_msg(over / self.DEG_TO_CAN * sign, True)))


class TestSubaruGen1TorqueStockLongitudinalSafety(TestSubaruStockLongitudinalSafetyBase, TestSubaruTorqueSafetyBase):
  FLAGS = 0
  TX_MSGS = lkas_tx_msgs(SUBARU_MAIN_BUS)


class TestSubaruGen1StopAndGoSafety(TestSubaruStockLongitudinalSafetyBase, TestSubaruTorqueSafetyBase):
  FLAGS = SubaruSafetyFlags.STOP_AND_GO
  TX_MSGS = lkas_tx_msgs(SUBARU_MAIN_BUS) + [[SubaruMsg.Throttle, SUBARU_CAM_BUS],
                                             [SubaruMsg.Brake_Pedal, SUBARU_CAM_BUS]]
  RELAY_MALFUNCTION_ADDRS = {
    **TestSubaruSafetyBase.RELAY_MALFUNCTION_ADDRS,
    SUBARU_CAM_BUS: (SubaruMsg.Throttle, SubaruMsg.Brake_Pedal),
  }
  FWD_BLACKLISTED_ADDRS = {
    **fwd_blacklisted_addr(),
    SUBARU_MAIN_BUS: (SubaruMsg.Throttle, SubaruMsg.Brake_Pedal),
  }


class TestSubaruGen2TorqueSafetyBase(TestSubaruTorqueSafetyBase):
  ALT_MAIN_BUS = SUBARU_ALT_BUS
  ALT_CAM_BUS = SUBARU_ALT_BUS

  MAX_RATE_UP = 35
  MAX_RATE_DOWN = 50
  MAX_TORQUE_LOOKUP = [0], [1500]


class TestSubaruGen2TorqueStockLongitudinalSafety(TestSubaruStockLongitudinalSafetyBase, TestSubaruGen2TorqueSafetyBase):
  FLAGS = SubaruSafetyFlags.GEN2
  TX_MSGS = lkas_tx_msgs(SUBARU_ALT_BUS)


class TestSubaruGen1LongitudinalSafety(TestSubaruLongitudinalSafetyBase, TestSubaruTorqueSafetyBase):
  FLAGS = SubaruSafetyFlags.LONG
  TX_MSGS = lkas_tx_msgs(SUBARU_MAIN_BUS) + long_tx_msgs(SUBARU_MAIN_BUS)
  RELAY_MALFUNCTION_ADDRS = {SUBARU_MAIN_BUS: (SubaruMsg.ES_LKAS, SubaruMsg.ES_DashStatus, SubaruMsg.ES_LKAS_State,
                                               SubaruMsg.ES_Infotainment, SubaruMsg.ES_Brake, SubaruMsg.ES_Status,
                                               SubaruMsg.ES_Distance)}


class TestSubaruGen1AscentLongitudinalSafety(TestSubaruGen1LongitudinalSafety):
  FLAGS = SubaruSafetyFlags.LONG | SubaruSafetyFlags.ASCENT_LONG_THROTTLE
  MAX_GAS = 3800


class TestSubaruGen1AngleStockLongitudinalSafety(TestSubaruStockLongitudinalSafetyBase, TestSubaruAngleSafetyBase):
  ALT_MAIN_BUS = SUBARU_MAIN_BUS
  FLAGS = SubaruSafetyFlags.LKAS_ANGLE
  TX_MSGS = lkas_tx_msgs(SUBARU_MAIN_BUS, SubaruMsg.ES_LKAS_ANGLE)


class TestSubaruGen2AngleStockLongitudinalSafety(TestSubaruStockLongitudinalSafetyBase, TestSubaruAngleSafetyBase):
  ALT_MAIN_BUS = SUBARU_ALT_BUS
  FLAGS = SubaruSafetyFlags.GEN2 | SubaruSafetyFlags.LKAS_ANGLE
  TX_MSGS = lkas_tx_msgs(SUBARU_ALT_BUS, SubaruMsg.ES_LKAS_ANGLE)


class TestSubaruGen2FixedAngleSafety(TestSubaruGen2AngleStockLongitudinalSafety):
  FLAGS = SubaruSafetyFlags.GEN2 | SubaruSafetyFlags.LKAS_ANGLE | SubaruSafetyFlags.FIXED_ANGLE_LIMITS
  STEER_ANGLE_MAX = 545
  ANGLE_RATE_BP = [0., 5., 35.]
  ANGLE_RATE_UP = [5., .8, .15]
  ANGLE_RATE_DOWN = [5., .8, .15]

  def test_rt_limits(self):
    raise unittest.SkipTest("Breakpoint angle limits do not enforce a real-time message frequency")


class TestSubaruGen2FixedAngleStopStartSafety(TestSubaruGen2FixedAngleSafety):
  FLAGS = SubaruSafetyFlags.GEN2 | SubaruSafetyFlags.LKAS_ANGLE | SubaruSafetyFlags.FIXED_ANGLE_LIMITS | \
    SubaruSafetyFlags.STOP_START_BUTTON
  TX_MSGS = TestSubaruGen2FixedAngleSafety.TX_MSGS + [[SubaruMsg.Dashlights, SUBARU_ALT_BUS]]

  def _stop_start_msg(self, pressed):
    return self.packer.make_can_msg_safety(
      "Dashlights", SUBARU_ALT_BUS, {"COUNTER": 0, "STOP_START": pressed},
    )

  def test_stop_start_tx_requires_pressed_bit(self):
    self.assertTrue(self._tx(self._stop_start_msg(True)))
    self.assertFalse(self._tx(self._stop_start_msg(False)))


class TestSubaruGen2AscentLkasAlertSafety(TestSubaruGen2FixedAngleSafety):
  FLAGS = TestSubaruGen2FixedAngleSafety.FLAGS | SubaruSafetyFlags.ASCENT_LKAS_ALERT
  TX_MSGS = TestSubaruGen2FixedAngleSafety.TX_MSGS + [[SubaruMsg.ES_LKAS_Alert, SUBARU_MAIN_BUS]]
  RELAY_MALFUNCTION_ADDRS = {SUBARU_MAIN_BUS: (*TestSubaruAngleSafetyBase.RELAY_MALFUNCTION_ADDRS[SUBARU_MAIN_BUS],
                                            SubaruMsg.ES_LKAS_Alert)}
  FWD_BLACKLISTED_ADDRS = {SUBARU_CAM_BUS: (*TestSubaruAngleSafetyBase.FWD_BLACKLISTED_ADDRS[SUBARU_CAM_BUS],
                                         SubaruMsg.ES_LKAS_Alert)}

  def test_secondary_alert_tx_requires_checksum(self):
    from opendbc.safety.tests.libsafety.libsafety_py import make_CANPacket
    self.assertTrue(self._tx(make_CANPacket(SubaruMsg.ES_LKAS_Alert, SUBARU_MAIN_BUS, bytes.fromhex("ce02000104000000"))))
    self.assertFalse(self._tx(make_CANPacket(SubaruMsg.ES_LKAS_Alert, SUBARU_MAIN_BUS, bytes.fromhex("0002000104000000"))))

  def test_secondary_alert_permission_and_forwarding_are_flag_gated(self):
    from opendbc.safety.tests.libsafety.libsafety_py import make_CANPacket
    msg = make_CANPacket(SubaruMsg.ES_LKAS_Alert, SUBARU_MAIN_BUS, bytes.fromhex("ce02000104000000"))
    for flags in (0, SubaruSafetyFlags.GEN2, SubaruSafetyFlags.LKAS_ANGLE, TestSubaruGen2FixedAngleSafety.FLAGS,
                  TestSubaruDPlatformAngleSafety.FLAGS, TestSubaruDPlatformCameraAngleSafety.FLAGS,
                  SubaruSafetyFlags.ASCENT_LKAS_ALERT):
      with self.subTest(flags=flags):
        self.safety.set_safety_hooks(CarParams.SafetyModel.subaru, flags)
        self.safety.init_tests()
        self.assertFalse(self._tx(msg))
        self.assertEqual(self.safety.safety_fwd_hook(SUBARU_CAM_BUS, SubaruMsg.ES_LKAS_Alert), SUBARU_MAIN_BUS)


class TestSubaruDPlatformAngleSafety(TestSubaruStockLongitudinalSafetyBase, TestSubaruAngleSafetyBase):
  FLAGS = SubaruSafetyFlags.GEN2 | SubaruSafetyFlags.LKAS_ANGLE | SubaruSafetyFlags.D_PLATFORM
  ALT_MAIN_BUS = SUBARU_ALT_BUS
  TX_MSGS = [[SubaruMsg.ES_LKAS_ANGLE, SUBARU_MAIN_BUS],
             [SubaruMsg.ES_DashStatus, SUBARU_MAIN_BUS],
             [SubaruMsg.ES_LKAS_State, SUBARU_MAIN_BUS],
             [SubaruMsg.ES_Infotainment, SUBARU_MAIN_BUS],
             [SubaruMsg.ES_Distance, SUBARU_ALT_BUS]]
  RELAY_MALFUNCTION_ADDRS = {SUBARU_MAIN_BUS: (SubaruMsg.ES_LKAS_ANGLE,
                                               SubaruMsg.ES_DashStatus,
                                               SubaruMsg.ES_LKAS_State, SubaruMsg.ES_Infotainment)}
  FWD_BLACKLISTED_ADDRS = {
    SUBARU_CAM_BUS: [SubaruMsg.ES_LKAS_ANGLE, SubaruMsg.ES_DashStatus, SubaruMsg.ES_LKAS_State, SubaruMsg.ES_Infotainment],
  }

  def _torque_driver_msg(self, torque):
    return self.packer.make_can_msg_safety("Steering_Torque", SUBARU_MAIN_BUS, {"Steer_Torque_Sensor": torque})

  def _user_gas_msg(self, gas):
    return self.packer.make_can_msg_safety("Throttle", SUBARU_ALT_BUS, {"Throttle_Pedal": gas})

  def _angle_cmd_msg(self, angle, enabled, increment_timer=True):
    if increment_timer:
      self.safety.set_timer(self.angle_cmd_cnt * int(1e6 / self.LATERAL_FREQUENCY))
      self.angle_cmd_cnt += 1
    values = {"LKAS_Output": angle, "LKAS_Request": enabled, "SET_3": 3}
    return self.packer.make_can_msg_safety("ES_LKAS_ANGLE", SUBARU_MAIN_BUS, values)

  def _angle_meas_msg(self, angle):
    return self.packer.make_can_msg_safety("Steering_2", SUBARU_MAIN_BUS, {"Steering_Angle": angle})


class TestSubaruDPlatformFixedAngleSafety(TestSubaruDPlatformAngleSafety):
  FLAGS = SubaruSafetyFlags.GEN2 | SubaruSafetyFlags.LKAS_ANGLE | SubaruSafetyFlags.D_PLATFORM | \
    SubaruSafetyFlags.FIXED_ANGLE_LIMITS
  STEER_ANGLE_MAX = 545
  ANGLE_RATE_BP = [0., 5., 35.]
  ANGLE_RATE_UP = [5., .8, .15]
  ANGLE_RATE_DOWN = [5., .8, .15]

  def test_rt_limits(self):
    raise unittest.SkipTest("Breakpoint angle limits do not enforce a real-time message frequency")


class TestSubaruDPlatformStopStartSafety(TestSubaruDPlatformAngleSafety):
  FLAGS = SubaruSafetyFlags.GEN2 | SubaruSafetyFlags.LKAS_ANGLE | SubaruSafetyFlags.D_PLATFORM | \
    SubaruSafetyFlags.STOP_START_BUTTON
  TX_MSGS = TestSubaruDPlatformAngleSafety.TX_MSGS + [[SubaruMsg.Dashlights, SUBARU_ALT_BUS]]

  def _stop_start_msg(self, pressed):
    return self.packer.make_can_msg_safety(
      "Dashlights", SUBARU_ALT_BUS, {"COUNTER": 0, "STOP_START": pressed},
    )

  def test_stop_start_tx_requires_pressed_bit(self):
    self.assertTrue(self._tx(self._stop_start_msg(True)))
    self.assertFalse(self._tx(self._stop_start_msg(False)))


class TestSubaruDPlatformCameraAngleSafety(TestSubaruDPlatformAngleSafety):
  FLAGS = SubaruSafetyFlags.GEN2 | SubaruSafetyFlags.LKAS_ANGLE | SubaruSafetyFlags.D_PLATFORM | SubaruSafetyFlags.D_PLATFORM_CAMERA
  TX_MSGS = [[SubaruMsg.ES_LKAS_ANGLE, SUBARU_CAM_BUS],
             [SubaruMsg.ES_DashStatus, SUBARU_CAM_BUS],
             [SubaruMsg.ES_LKAS_State, SUBARU_CAM_BUS],
             [SubaruMsg.ES_Infotainment, SUBARU_CAM_BUS],
             [SubaruMsg.ES_Distance, SUBARU_ALT_BUS]]
  RELAY_MALFUNCTION_ADDRS = {SUBARU_CAM_BUS: (SubaruMsg.ES_LKAS_ANGLE,
                                               SubaruMsg.ES_DashStatus,
                                               SubaruMsg.ES_LKAS_State, SubaruMsg.ES_Infotainment)}
  FWD_BLACKLISTED_ADDRS = {
    SUBARU_MAIN_BUS: [SubaruMsg.ES_LKAS_ANGLE, SubaruMsg.ES_DashStatus, SubaruMsg.ES_LKAS_State, SubaruMsg.ES_Infotainment],
  }

  def _angle_cmd_msg(self, angle, enabled, increment_timer=True):
    if increment_timer:
      self.safety.set_timer(self.angle_cmd_cnt * int(1e6 / self.LATERAL_FREQUENCY))
      self.angle_cmd_cnt += 1
    values = {"LKAS_Output": angle, "LKAS_Request": enabled, "SET_3": 3}
    return self.packer.make_can_msg_safety("ES_LKAS_ANGLE", SUBARU_CAM_BUS, values)


class TestSubaruGen2LongitudinalSafety(TestSubaruLongitudinalSafetyBase, TestSubaruGen2TorqueSafetyBase):
  FLAGS = SubaruSafetyFlags.LONG | SubaruSafetyFlags.GEN2
  TX_MSGS = lkas_tx_msgs(SUBARU_ALT_BUS) + long_tx_msgs(SUBARU_ALT_BUS) + gen2_long_additional_tx_msgs()
  FWD_BLACKLISTED_ADDRS = {2: [SubaruMsg.ES_LKAS, SubaruMsg.ES_DashStatus, SubaruMsg.ES_LKAS_State,
                               SubaruMsg.ES_Infotainment]}
  RELAY_MALFUNCTION_ADDRS = {SUBARU_MAIN_BUS: (SubaruMsg.ES_LKAS, SubaruMsg.ES_DashStatus, SubaruMsg.ES_LKAS_State,
                                               SubaruMsg.ES_Infotainment),
                             SUBARU_ALT_BUS: (SubaruMsg.ES_Brake, SubaruMsg.ES_Status, SubaruMsg.ES_Distance)}

  def _rdbi_msg(self, did: int):
    return b'\x03\x22' + did.to_bytes(2) + b'\x00\x00\x00\x00'

  def _es_uds_msg(self, msg: bytes):
    return libsafety_py.make_CANPacket(SubaruMsg.ES_UDS_Request, 2, msg)

  def test_es_uds_message(self):
    tester_present = b'\x02\x3E\x80\x00\x00\x00\x00\x00'
    not_tester_present = b"\x03\xAA\xAA\x00\x00\x00\x00\x00"

    button_did = 0x1130

    # Tester present is allowed for gen2 long to keep eyesight disabled
    self.assertTrue(self._tx(self._es_uds_msg(tester_present)))

    # Non-Tester present is not allowed
    self.assertFalse(self._tx(self._es_uds_msg(not_tester_present)))

    # Only button_did is allowed to be read via UDS
    for did in range(0xFFFF):
      should_tx = (did == button_did)
      self.assertEqual(self._tx(self._es_uds_msg(self._rdbi_msg(did))), should_tx)

    # any other msg is not allowed
    for sid in range(0xFF):
      msg = b'\x03' + sid.to_bytes(1) + b'\x00' * 6
      self.assertFalse(self._tx(self._es_uds_msg(msg)))


if __name__ == "__main__":
  unittest.main()
