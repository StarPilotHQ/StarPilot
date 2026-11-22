#!/usr/bin/env python3
import unittest

import numpy as np

from opendbc.car.structs import CarParams
from opendbc.car.volvo.values import VolvoSafetyFlags
from opendbc.safety.tests.libsafety import libsafety_py
import opendbc.safety.tests.common as common
from opendbc.safety.tests.common import CANPackerSafety

VOLVO_MAIN_BUS = 0
VOLVO_PARTY_BUS = 2


AOL = 32
LATERAL = 1
LONGITUDINAL = 2
DRIVE, REVERSE = 3, 1


class TestVolvoEUCD(common.CarSafetyTest, common.AngleSteeringSafetyTest):
  """Volvo EUCD (V60 2010-18) with openpilot longitudinal over the relayed stock FSM1/FSM3."""
  TX_MSGS = [[0x051, VOLVO_MAIN_BUS], [0x127, VOLVO_MAIN_BUS], [0x260, VOLVO_MAIN_BUS], [0x262, VOLVO_MAIN_BUS],
             [0x270, VOLVO_MAIN_BUS], [0x31A, VOLVO_MAIN_BUS], [0x246, VOLVO_PARTY_BUS],
             [0x220, VOLVO_PARTY_BUS], [0x299, VOLVO_PARTY_BUS], [0x1FF, VOLVO_PARTY_BUS],
             [0x127, VOLVO_PARTY_BUS], [0x325, VOLVO_PARTY_BUS], [0x12A, VOLVO_PARTY_BUS]]
  GAS_PRESSED_THRESHOLD = 10
  STANDSTILL_THRESHOLD = 0.1
  RELAY_MALFUNCTION_ADDRS = {VOLVO_MAIN_BUS: [0x051, 0x262, 0x31A],
                             VOLVO_PARTY_BUS: [0x246, 0x220, 0x299, 0x1FF, 0x325, 0x12A, 0x127]}
  FWD_BLACKLISTED_ADDRS = {VOLVO_PARTY_BUS: [0x051, 0x262, 0x31A],
                           VOLVO_MAIN_BUS: [0x051, 0x246, 0x220, 0x299, 0x1FF, 0x325, 0x12A, 0x127]}

  # Angle control limits
  STEER_ANGLE_MAX = 90  # deg, matches CarControllerEUCDParams.EUCD_ANGLE_LIMITS
  DEG_TO_CAN = 100

  ANGLE_RATE_BP = [0., 5., 15.]
  ANGLE_RATE_UP = [5., .8, .15]  # windup limit
  ANGLE_RATE_DOWN = [5., 3.5, .4]  # unwind limit

  def setUp(self):
    self.packer = CANPackerSafety("volvo_v60_2015_pt")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.volvo, VolvoSafetyFlags.EUCD.value)
    self.safety.init_tests()

  def _angle_cmd_msg(self, angle: float, enabled: bool, increment_timer: bool = True):
    # The safety checks LKAAngleReq only; LKASteerDirection drops to NONE in the anti-windup pause
    direction = (2 if angle > 0 else 1) if enabled else 0
    values = {"LKAAngleReq": angle, "LKASteerDirection": direction}
    return self.packer.make_can_msg_safety("FSM2", VOLVO_MAIN_BUS, values)

  def _angle_meas_msg(self, angle: float):
    return self.packer.make_can_msg_safety("PSCM1", VOLVO_MAIN_BUS, {"SteeringAngleServo": angle})

  def _pcm_status_msg(self, enable):
    # engagement comes from the ECM (EngineData), not from the camera's FSM0
    values = {"ACC_State_ECM": 3 if enable else 2}  # engaged / standby
    return self.packer.make_can_msg_safety("EngineData", VOLVO_MAIN_BUS, values)

  def _speed_msg(self, speed: float):
    return self.packer.make_can_msg_safety("VehicleSpeed1", VOLVO_MAIN_BUS, {"VehicleSpeed": speed * 3.6})

  def _user_brake_msg(self, brake):
    return self.packer.make_can_msg_safety("Brake_Info", VOLVO_MAIN_BUS, {"BrakePedal": 2 if brake else 0})

  def _user_gas_msg(self, gas):
    return self.packer.make_can_msg_safety("AccPedal", VOLVO_MAIN_BUS, {"AccPedal": 10 if gas else 0})

  def test_gas_press_and_release_have_hysteresis(self):
    # A foot held at 10% flickered gas each frame and OP's long with it (route 00000007): 10% presses, under 9% releases
    for pedal, pressed in ((9.9, False), (10, True), (9.9, True), (9, True), (8.9, False), (9.9, False), (10, True)):
      self._rx(self.packer.make_can_msg_safety("AccPedal", VOLVO_MAIN_BUS, {"AccPedal": pedal}))
      self.assertEqual(pressed, self.safety.get_gas_pressed_prev(), f"{pedal=}")

  def test_ecm_acc_off_is_not_engaged(self):
    # ACC_State_ECM: 1 off, 2 standby, 3 engaged; only 3 allows controls (route 823)
    for state, engaged in ((1, False), (2, False), (3, True)):
      self.safety.set_controls_allowed(False)
      self._rx(self._pcm_status_msg(False))
      self._rx(self.packer.make_can_msg_safety("EngineData", VOLVO_MAIN_BUS, {"ACC_State_ECM": state}))
      self.assertEqual(engaged, self.safety.get_controls_allowed(), f"{state=}")

  def test_camera_fsm0_does_not_engage(self):
    # the camera's FSM0 is not an engagement source
    self.safety.set_controls_allowed(False)
    self._rx(self.packer.make_can_msg_safety("FSM0", VOLVO_PARTY_BUS, {"ACC_Enabled": 1}))
    self.assertFalse(self.safety.get_controls_allowed())

  def _fsm0_msg(self, acc_enabled: bool):
    return self.packer.make_can_msg_safety("FSM0", VOLVO_MAIN_BUS, {"ACC_Enabled": 1 if acc_enabled else 0, "ACC_Available": 1})

  def test_fsm0_acc_enabled_only_with_ecm_engaged(self):
    # OP relays FSM0 to the car; it must never report ACC enabled while the ECM is not engaged
    for ecm_engaged in (False, True):
      self._rx(self._pcm_status_msg(ecm_engaged))
      self.assertTrue(self._tx(self._fsm0_msg(False)))
      self.assertEqual(ecm_engaged, self._tx(self._fsm0_msg(True)), f"{ecm_engaged=}")

  def test_button_rules_only_to_the_car(self):
    # Resume/cancel to the car need controls allowed / cruise engaged; the camera bus gets every CEM frame, not checked
    resume = {"ACCResumeBtn": 1, "ACCResumeBtnInv": 0, "ACCOnOffBtnInv": 1}
    cancel = {"ACCOnOffBtn": 1, "ACCOnOffBtnInv": 0, "ACCResumeBtnInv": 1}
    self._rx(self._pcm_status_msg(False))
    for bus in (VOLVO_MAIN_BUS, VOLVO_PARTY_BUS):
      for controls_allowed in (False, True):
        self.safety.set_controls_allowed(controls_allowed)
        msg = self.packer.make_can_msg_safety("CCButtons", bus, resume)
        self.assertEqual(controls_allowed or bus == VOLVO_PARTY_BUS, self._tx(msg), f"{bus=} {controls_allowed=}")
      msg = self.packer.make_can_msg_safety("CCButtons", bus, cancel)
      self.assertEqual(bus == VOLVO_PARTY_BUS, self._tx(msg), f"{bus=}")

  def _longitudinal_cmd_msg(self, accel: float):
    values = {"ACC_AccelerationRequest": accel, "ACC_Check": 0}
    return self.packer.make_can_msg_safety("FSM3", VOLVO_MAIN_BUS, values)

  def test_longitudinal_relay_when_controls_not_allowed(self):
    # FSM3 relayed without controls: only the inactive accel passes
    self.safety.set_controls_allowed(False)
    self.assertTrue(self._tx(self._longitudinal_cmd_msg(0.0)))    # neutral — must pass
    self.assertFalse(self._tx(self._longitudinal_cmd_msg(1.0)))   # any accel — must be blocked
    self.assertFalse(self._tx(self._longitudinal_cmd_msg(-1.0)))  # any decel — must be blocked

  def test_longitudinal_accel_limits_when_controls_allowed(self):
    # When controls are allowed, FSM3 accel is range-checked via longitudinal_accel_checks.
    self.safety.set_controls_allowed(True)
    self.assertTrue(self._tx(self._longitudinal_cmd_msg(0.0)))   # neutral — must pass
    self.assertTrue(self._tx(self._longitudinal_cmd_msg(2.0)))   # max accel — must pass
    self.assertTrue(self._tx(self._longitudinal_cmd_msg(-3.5)))  # max decel — must pass
    self.assertFalse(self._tx(self._longitudinal_cmd_msg(2.1)))  # above max accel — must block
    self.assertFalse(self._tx(self._longitudinal_cmd_msg(-3.6))) # below min accel — must block

  def _cam_frame(self, name, values, bus):
    return self.packer.make_can_msg_safety(name, bus, values)

  def test_camera_hard_brake_frame_passes_unchanged(self):
    # Below the limit only the camera's own FSM3, just received and unchanged, and only while OP's long is allowed
    hard = {"ACC_AccelerationRequest": -4.5, "ACC_Check": 0, "FrameKind": 0x81}
    self.safety.set_timer(100000)
    self.safety.set_controls_allowed(True)
    self.assertFalse(self._tx(self._cam_frame("FSM3", hard, VOLVO_MAIN_BUS)))  # not received from the camera
    self._rx(self._cam_frame("FSM3", hard, VOLVO_PARTY_BUS))
    self.assertTrue(self._tx(self._cam_frame("FSM3", hard, VOLVO_MAIN_BUS)))
    self.assertFalse(self._tx(self._cam_frame("FSM3", hard | {"ACC_Check": 1}, VOLVO_MAIN_BUS)))  # changed
    self.safety.set_timer(100000 + 200000)
    self.assertFalse(self._tx(self._cam_frame("FSM3", hard, VOLVO_MAIN_BUS)))  # too old
    self._rx(self._cam_frame("FSM3", hard, VOLVO_PARTY_BUS))
    self._rx(self._user_gas_msg(True))
    self.assertFalse(self._tx(self._cam_frame("FSM3", hard, VOLVO_MAIN_BUS)))  # driver on the gas
    self._rx(self._user_gas_msg(False))
    self.safety.set_controls_allowed(False)
    self.assertFalse(self._tx(self._cam_frame("FSM3", hard, VOLVO_MAIN_BUS)))  # controls not allowed

  def test_fsm4_brake_request_needs_long_allowed_or_camera_frame(self):
    brake = {"ACC_BrakeRequest": 1, "ACC_BrakeTarget": 12900, "Byte_5_b7": 1}
    no_brake = {"ACC_BrakeRequest": 0, "ACC_BrakeTarget": 13328, "Byte_5_b7": 1}
    self.safety.set_timer(100000)
    for controls_allowed in (False, True):
      for gas in (False, True):
        self.safety.set_controls_allowed(controls_allowed)
        self._rx(self._user_gas_msg(gas))
        allowed = controls_allowed and not gas
        self.assertTrue(self._tx(self._cam_frame("FSM4", no_brake, VOLVO_MAIN_BUS)), f"{controls_allowed=} {gas=}")
        self.assertEqual(allowed, self._tx(self._cam_frame("FSM4", brake, VOLVO_MAIN_BUS)), f"{controls_allowed=} {gas=}")
    # the camera's own brake request goes back unchanged even without OP's long (ACC off, driver on the gas)
    self.safety.set_controls_allowed(False)
    self._rx(self._cam_frame("FSM4", brake, VOLVO_PARTY_BUS))
    self.assertTrue(self._tx(self._cam_frame("FSM4", brake, VOLVO_MAIN_BUS)))
    self.assertFalse(self._tx(self._cam_frame("FSM4", brake | {"ACC_BrakeTarget": 12000}, VOLVO_MAIN_BUS)))

  def test_fwd_hook_blocks_fsm_when_controls_allowed(self):
    # With controls and no gas OP sends FSM1/FSM3 itself, so the stock ones are blocked
    for controls_allowed in (True, False):
      for gas_pressed in (True, False):
        self.safety.set_controls_allowed(controls_allowed)
        self._rx(self._user_gas_msg(gas_pressed))
        for addr in (0x260, 0x270):  # VOLVO_EUCD_FSM1, VOLVO_EUCD_FSM3
          result = self.safety.safety_fwd_hook(VOLVO_PARTY_BUS, addr)
          should_block = controls_allowed and not gas_pressed
          expected = -1 if should_block else VOLVO_MAIN_BUS
          self.assertEqual(expected, result, f"{addr=:#x} {controls_allowed=} {gas_pressed=}")

  def test_gas_in_op_stop_keeps_camera_fsm_off_the_ecm(self):
    # In OP's stop the camera's FSM1/FSM3 stay off the ECM also on the gas; OP's FSM3 carries no accel
    def forwarded():
      return {addr: self.safety.safety_fwd_hook(VOLVO_PARTY_BUS, addr) == VOLVO_MAIN_BUS for addr in (0x260, 0x270)}
    standby_view = self.packer.make_can_msg_safety("EngineData", VOLVO_PARTY_BUS, {"ACC_State_ECM": 2})
    engaged_view = self.packer.make_can_msg_safety("EngineData", VOLVO_PARTY_BUS, {"ACC_State_ECM": 3})
    self.safety.set_controls_allowed(True)
    self._rx(self._pcm_status_msg(True))
    self._rx(self._user_gas_msg(True))
    self.assertEqual({0x260: True, 0x270: True}, forwarded())  # driver's gas: the camera's frames to the ECM
    self.assertTrue(self._tx(engaged_view))
    self.assertEqual({0x260: True, 0x270: True}, forwarded())  # the camera sees the ECM: not OP's stop
    self.assertTrue(self._tx(standby_view))
    self.assertEqual({0x260: False, 0x270: False}, forwarded())
    self.assertTrue(self._tx(self._longitudinal_cmd_msg(0.0)))
    self.assertFalse(self._tx(self._longitudinal_cmd_msg(1.0)))
    self.assertFalse(self._tx(self._longitudinal_cmd_msg(-1.0)))
    # kept a few ECM frames after OP stops showing standby, then the camera's frames again
    for _ in range(2):
      self._rx(self._pcm_status_msg(True))
    self.assertEqual({0x260: False, 0x270: False}, forwarded())
    self._rx(self._pcm_status_msg(True))
    self.assertEqual({0x260: True, 0x270: True}, forwarded())
    # standby shown without the ECM engaged, or without controls allowed: not OP's stop
    self._rx(self._pcm_status_msg(False))
    self.safety.set_controls_allowed(True)
    self.assertTrue(self._tx(standby_view))
    self.assertEqual({0x260: True, 0x270: True}, forwarded())
    self._rx(self._pcm_status_msg(True))
    self.safety.set_controls_allowed(False)
    self.assertTrue(self._tx(standby_view))
    self.safety.set_controls_allowed(True)
    self.assertEqual({0x260: True, 0x270: True}, forwarded())
    # a new safety mode starts without it
    self.assertTrue(self._tx(standby_view))
    self.safety.set_safety_hooks(CarParams.SafetyModel.volvo, VolvoSafetyFlags.EUCD.value)
    self.safety.set_controls_allowed(True)
    self._rx(self._user_gas_msg(True))
    self.assertEqual({0x260: True, 0x270: True}, forwarded())

  # AngleSteeringSafetyTest with the angle gated on engagement and only zero while disengaged
  def test_angle_cmd_when_disabled(self):
    for controls_allowed in (True, False):
      self.safety.set_controls_allowed(controls_allowed)

      for angle_meas in np.arange(-90, 91, 10):
        self._reset_angle_measurement(angle_meas)

        for angle_cmd in np.arange(-90, 91, 10):
          self._set_prev_desired_angle(angle_cmd)

          # engaged: angle (held at prev) is allowed; disengaged: only a zero angle passes
          should_tx = controls_allowed or (angle_cmd == 0)
          self.assertEqual(should_tx, self._tx(self._angle_cmd_msg(angle_cmd, True)))

  def test_angle_cmd_when_enabled(self):
    # when controls are allowed, angle cmd rate limit is enforced
    speeds = [0., 1., 5., 10., 15., 50.]
    angles = np.concatenate((np.arange(-self.STEER_ANGLE_MAX * 2, self.STEER_ANGLE_MAX * 2, 5), [0]))
    for a in angles:
      for s in speeds:
        max_delta_up = np.interp(s, self.ANGLE_RATE_BP, self.ANGLE_RATE_UP)
        max_delta_down = np.interp(s, self.ANGLE_RATE_BP, self.ANGLE_RATE_DOWN)

        self._reset_angle_measurement(a)
        self._reset_speed_measurement(s)
        self._set_prev_desired_angle(a)
        self.safety.set_controls_allowed(1)

        # Stay within limits
        self.assertTrue(self._tx(self._angle_cmd_msg(a + common.sign_of(a) * max_delta_up, True)))
        self.assertTrue(self._tx(self._angle_cmd_msg(a, True)))
        self.assertTrue(self._tx(self._angle_cmd_msg(a - common.sign_of(a) * max_delta_down, True)))

        # Inject too high rates
        self.assertFalse(self._tx(self._angle_cmd_msg(a + common.sign_of(a) * (max_delta_up + 1.1), True)))
        self.safety.set_controls_allowed(1)
        self._set_prev_desired_angle(a)
        self.assertTrue(self._tx(self._angle_cmd_msg(a, True)))
        self.assertFalse(self._tx(self._angle_cmd_msg(a - common.sign_of(a) * (max_delta_down + 1.1), True)))

        # When disabled, only a zero angle is allowed
        self.safety.set_controls_allowed(0)
        self.assertEqual(a == 0, self._tx(self._angle_cmd_msg(a, False)))


class TestVolvoEUCDAol(unittest.TestCase):
  """The optional AOL profile: the vehicle's states grant, the host's request only narrows."""

  def setUp(self):
    self.packer = CANPackerSafety("volvo_v60_2015_pt")
    self.safety = libsafety_py.libsafety
    self.safety.set_alternative_experience(AOL)
    self.assertEqual(0, self.safety.set_safety_hooks(CarParams.SafetyModel.volvo, VolvoSafetyFlags.EUCD.value))
    self.safety.init_tests()
    self.time = 1000
    self.safety.set_timer(self.time)
    self.safety.set_aol_test_heartbeat(True)

  def tearDown(self):
    self.safety.set_alternative_experience(0)

  def _rx(self, name, bus, values):
    return self.safety.safety_rx_hook(self.packer.make_can_msg_safety(name, bus, values))

  def _healthy(self, *, main=False, lka=False, gear=DRIVE, engaged=False):
    # every required RX within its bound; the request itself is renewed last
    self.time += 20000
    self.safety.set_timer(self.time)
    self._rx("AccPedal", 0, {"AccPedal": 0})
    self._rx("FSM0", 2, {"ACC_Available": int(main)})
    self._rx("EngineData", 0, {"ACC_State_ECM": 3 if engaged else 2})
    self._rx("CCButtons", 0, {"LKA_State": int(lka), "ACCOnOffBtnInv": 1, "ACCResumeBtnInv": 1})
    self._rx("VehicleSpeed1", 0, {"VehicleSpeed": 40})
    self._rx("Brake_Info", 0, {"BrakePedal": 0})
    self._rx("PSCM1", 0, {"SteeringAngleServo": 0})
    self._rx("GearSelector", 0, {"GearLever": gear})

  def _permission(self, request=LATERAL | LONGITUDINAL, **state):
    self._healthy(**state)
    self.safety.aol_set_host_request(request)
    return self.safety.aol_get_permission_mask()

  def test_lateral_needs_the_lka_or_the_acc_main(self):
    self.assertEqual(0, self._permission(request=LATERAL))
    self.assertEqual(LATERAL, self._permission(request=LATERAL, main=True))
    self.assertEqual(LATERAL, self._permission(request=LATERAL, lka=True))
    self.assertEqual(0, self._permission(request=LATERAL))

  def test_host_request_only_narrows(self):
    self.assertEqual(0, self._permission(request=0, main=True, lka=True))
    self.assertEqual(0, self._permission(request=LONGITUDINAL, main=True, lka=True))

  def test_only_in_drive(self):
    self.assertEqual(0, self._permission(request=LATERAL, main=True, gear=REVERSE))
    self.assertEqual(LATERAL, self._permission(request=LATERAL, main=True))

  def test_stale_gear_withdraws(self):
    self.assertEqual(LATERAL, self._permission(request=LATERAL, main=True))
    self.safety.set_timer(self.time + 300001)
    self.assertEqual(0, self.safety.aol_get_permission_mask())

  def test_longitudinal_needs_the_ecm_engaged(self):
    self.assertEqual(LATERAL, self._permission(main=True))
    self.assertEqual(LATERAL | LONGITUDINAL, self._permission(main=True, engaged=True))

  def test_gas_override_keeps_the_longitudinal_permission(self):
    # Gas is an override: the permission stays (route 0000000a), while OP's longitudinal frames stay blocked
    self.assertEqual(LATERAL | LONGITUDINAL, self._permission(main=True, engaged=True))
    self.safety.set_controls_allowed(True)
    self._rx("AccPedal", 0, {"AccPedal": 20})
    self._rx("AccPedal", 0, {"AccPedal": 20})
    self.assertTrue(self.safety.get_gas_pressed_prev())
    self.assertEqual(LATERAL | LONGITUDINAL, self.safety.aol_get_permission_mask())
    brake = {"ACC_BrakeRequest": 1, "ACC_BrakeTarget": 12900, "Byte_5_b7": 1}
    self.assertFalse(self.safety.safety_tx_hook(self.packer.make_can_msg_safety("FSM4", VOLVO_MAIN_BUS, brake)))

  def test_brake_held_at_a_stop_keeps_the_longitudinal_permission(self):
    # Engaged with the brake held at a stop (preEnable): the permission follows controls_allowed (route 00000027)
    self._healthy(main=True, engaged=True)
    self._rx("Brake_Info", 0, {"BrakePedal": 2})
    self._rx("VehicleSpeed1", 0, {"VehicleSpeed": 0})
    self.safety.set_controls_allowed(True)
    self.safety.aol_set_host_request(LATERAL | LONGITUDINAL)
    self.assertEqual(LATERAL | LONGITUDINAL, self.safety.aol_get_permission_mask())
    # A new press clears controls_allowed, and the permission with it
    self._rx("Brake_Info", 0, {"BrakePedal": 1})
    self._rx("Brake_Info", 0, {"BrakePedal": 2})
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(LATERAL, self.safety.aol_get_permission_mask())

  def test_stock_frames_do_not_depend_on_the_host_request(self):
    # The camera's frames are blocked from the ECM while engaged, so OP's relay must keep passing without an AOL request
    self._healthy(main=True, engaged=True)
    self.safety.set_controls_allowed(True)
    self.assertEqual(0, self.safety.aol_get_permission_mask())
    self.assertEqual(-1, self.safety.safety_fwd_hook(VOLVO_PARTY_BUS, 0x270))
    fsm3 = self.packer.make_can_msg_safety("FSM3", VOLVO_MAIN_BUS, {"ACC_AccelerationRequest": 1.0, "ACC_Check": 0})
    self.assertTrue(self.safety.safety_tx_hook(fsm3))

  def test_steering_uses_the_lateral_axis(self):
    angle = self.packer.make_can_msg_safety("FSM2", VOLVO_MAIN_BUS, {"LKAAngleReq": 0.1, "LKASteerDirection": 2})
    self._healthy()
    self.safety.aol_set_host_request(LATERAL)
    self.assertFalse(self.safety.safety_tx_hook(angle))
    self.assertEqual(LATERAL, self._permission(request=LATERAL, main=True))
    self.assertTrue(self.safety.safety_tx_hook(angle))


if __name__ == "__main__":
  unittest.main()
