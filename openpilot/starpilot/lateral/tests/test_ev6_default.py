"""EV6 vehicle-policy defaults and steering command boundaries."""

import unittest

from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix

from opendbc.can import CANParser
from opendbc.car import Bus, DT_CTRL, gen_empty_fingerprint, structs
from opendbc.car.hyundai.carcontroller import CarController, MAX_ANGLE_FRAMES
from opendbc.car.hyundai.carstate import CarState
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags, CarControllerParams
from opendbc.car.lateral import apply_driver_steer_torque_limits
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.lateral.hkg_vehicle_policy import HKGVehicleTorquePolicy
from openpilot.starpilot.lateral.torque_runtime import runtime_enabled


def ev6(lka_steering: bool):
  fingerprint = gen_empty_fingerprint()
  if lka_steering:
    fingerprint[CanBus(None, fingerprint).CAM][0x50] = 16
  return CarInterface.get_params(CAR.KIA_EV6, fingerprint, [], False, False, False)


class TestEV6DefaultLateral(unittest.TestCase):
  def test_hda_topologies_keep_vehicle_tune_and_vehicle_policy(self):
    for lka_steering in (False, True):
      with self.subTest(lka_steering=lka_steering), OpenpilotPrefix():
        cp = ev6(lka_steering)
        self.assertEqual(bool(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG), lka_steering)
        self.assertEqual(cp.lateralTuning.which(), "torque")
        self.assertAlmostEqual(cp.lateralTuning.torque.latAccelFactor, 3.2)
        self.assertAlmostEqual(cp.lateralTuning.torque.friction, 0.005)
        # Global custom torque admission reads the saved numeric choices. An
        # empty real store must still select the exact vehicle/default tune.
        original_tune = cp.lateralTuning.to_dict()
        self.assertFalse(runtime_enabled(cp, Params()))
        self.assertEqual(cp.lateralTuning.to_dict(), original_tune)
        lateral = LatControlTorque(cp.as_reader(), CarInterface(cp), DT_CTRL)
        self.assertIsInstance(selected_policy(lateral), HKGVehicleTorquePolicy)
        self.assertEqual(selected_policy(lateral).profile, "kia_ev6")
        self.assertAlmostEqual(lateral.torque_params.latAccelFactor, cp.lateralTuning.torque.latAccelFactor)
        controller = CarController(DBC[cp.carFingerprint], cp)
        self.assertIsNone(controller.ioniq6_longitudinal)
        self.assertEqual(controller.params.STEER_MAX, 409)
        self.assertEqual(controller.params.STEER_DRIVER_ALLOWANCE, 250)

  def test_opposing_driver_torque_limits_can_command_and_angle_request(self):
    for lka_steering in (False, True):
      with self.subTest(lka_steering=lka_steering):
        cp = ev6(lka_steering)
        state = CarState(cp)
        state.out = state.update(state.get_can_parsers(cp))
        control = structs.CarControl()
        control.enabled = control.latActive = True
        control.actuators.torque = 1.0
        controller = CarController(DBC[cp.carFingerprint], cp)
        can = CanBus(cp)
        name = "LKAS" if lka_steering else "LFA"
        bus = can.ACAN if lka_steering else can.ECAN
        parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [(name, 0)], bus)

        state.out.steeringTorque = 0
        state.out.vEgoRaw = 15.0
        controller.apply_torque_last = 60
        _, sent = controller.update(control.as_reader(), state, 1_000_000_000)
        self.assertLessEqual(controller.apply_torque_last, 62)
        self.assertEqual(parser.update((1_000_000_000, sent)), {next(msg[0] for msg in sent if msg[2] == bus)})
        self.assertEqual(parser.vl[name]["ActToiSta"], 1)

        state.out.steeringTorque = -500
        for frame in range(30):
          previous = controller.apply_torque_last
          _, sent = controller.update(control.as_reader(), state, 1_010_000_000 + frame * 10_000_000)
          self.assertLessEqual(controller.apply_torque_last, previous)
        self.assertEqual(controller.apply_torque_last, 0)
        parser.update((1_310_000_000, sent))
        self.assertEqual(parser.vl[name]["StrTqReqVal"], 0)

        state.out.steeringAngleDeg = 90.0
        state.out.steeringTorque = 0
        requests = []
        for frame in range(MAX_ANGLE_FRAMES + 2):
          _, sent = controller.update(control.as_reader(), state, 1_320_000_000 + frame * 10_000_000)
          parser.update((1_320_000_000 + frame * 10_000_000, sent))
          requests.append(parser.vl[name]["ActToiSta"])
        self.assertIn(0, requests[-2:])


  def test_torque_speed_transition_unwind_and_zero_crossing(self):
    cp = ev6(True)
    for speed, up, down in ((0., 10, 8), (14.999, 10, 8), (15., 2, 3), (30., 2, 3),
                            (-1., 2, 3), (float('nan'), 2, 3), (float('inf'), 2, 3)):
      with self.subTest(speed=speed):
        limits = CarControllerParams(cp, speed)
        self.assertEqual((limits.STEER_MAX, limits.STEER_DELTA_UP, limits.STEER_DELTA_DOWN), (409, up, down))
        for sign in (-1, 1):
          first = sign * (min(up, down) if sign > 0 else up)
          self.assertEqual(apply_driver_steer_torque_limits(sign * 409, 0, 0, limits), first)
          self.assertEqual(apply_driver_steer_torque_limits(sign * 409, sign, 0, limits), sign * (1 + up))
          self.assertEqual(apply_driver_steer_torque_limits(0, sign * 409, 0, limits), sign * (409 - down))
          self.assertEqual(apply_driver_steer_torque_limits(-sign * 409, sign, 0, limits), sign * (1 - down))
    state = CarState(cp)
    state.out = state.update(state.get_can_parsers(cp))
    control = structs.CarControl()
    control.enabled = control.latActive = True
    control.actuators.torque = 1.0
    controller = CarController(DBC[cp.carFingerprint], cp)
    for tick, (speed, previous, expected) in enumerate(((14., 399, 409), (15., 409, 409), (15., 399, 401))):
      state.out.vEgoRaw = speed
      controller.apply_torque_last = previous
      result, _ = controller.update(control.as_reader(), state, 1_000_000_000 + tick * 10_000_000)
      self.assertEqual(result.torqueOutputCan, expected)

  def test_angle_carnival_and_classic_limits_are_unchanged(self):
    cp = ev6(True).as_reader().as_builder()
    cp.flags |= int(HyundaiFlags.CANFD_ANGLE_STEERING)
    for speed in (0., 15.):
      limits = CarControllerParams(cp, speed)
      self.assertEqual((limits.STEER_MAX, limits.STEER_DELTA_UP, limits.STEER_DELTA_DOWN, limits.STEER_THRESHOLD), (270, 2, 3, 175))
    cp.flags &= ~int(HyundaiFlags.CANFD_ANGLE_STEERING)
    cp.carFingerprint = CAR.KIA_CARNIVAL_HEV_4TH_GEN
    for speed in (0., 15.):
      limits = CarControllerParams(cp, speed)
      self.assertEqual((limits.STEER_MAX, limits.STEER_DELTA_UP, limits.STEER_DELTA_DOWN), (409, 2, 3))
    for vehicle, flags, maximum in ((CAR.HYUNDAI_ELANTRA, 0, 255), (CAR.GENESIS_G70_2020, HyundaiFlags.ALT_LIMITS, 270),
                                  (CAR.GENESIS_G70_2020, HyundaiFlags.ALT_LIMITS_2, 170)):
      cp.carFingerprint, cp.flags = vehicle, int(flags)
      for speed in (0., 15.):
        self.assertEqual(CarControllerParams(cp, speed).STEER_MAX, maximum)


if __name__ == "__main__":
  unittest.main()
