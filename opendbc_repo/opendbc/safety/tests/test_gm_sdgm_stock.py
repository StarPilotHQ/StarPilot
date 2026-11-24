import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.carstate import CarState
from opendbc.car.gm.tests.test_sdgm_stock import params
from opendbc.car.gm.values import CAR, DBC, GMSafetyFlags, SDGM_STOCK_CAR, SDGM_CANCEL_PT_CAR
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py


class TestGmSdgmStock(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.release = self.safety.set_safety_hooks(CarParams.SafetyModel.allOutput, 0) != 0

  @staticmethod
  def packet(msg):
    return libsafety_py.make_CANPacket(msg[0], msg[2], msg[1])

  def mode(self, cp):
    self.safety.set_alternative_experience(0)
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, cp.safetyConfigs[0].safetyParam), 0)
    self.safety.init_tests()
    self.safety.set_timer(1_000_000)

  @staticmethod
  def source_frames(packer, brake_c9, brake_pressed=False):
    return [
      packer.make_can_msg("PSCMStatus", 0, {}),
      packer.make_can_msg("EBCMWheelSpdRear", 0, {}),
      packer.make_can_msg("ASCMSteeringButton", 0, {"ACCButtons": 1}),
      packer.make_can_msg("AcceleratorPedal2", 0, {"CruiseState": 1}),
      packer.make_can_msg("ECMEngineStatus" if brake_c9 else "ECMAcceleratorPos", 0,
                          {"BrakePressed": 1} if brake_pressed and brake_c9 else
                          {"BrakePedalPos": 10} if brake_pressed else {}),
    ]

  def feed(self, frames):
    for frame in frames:
      self.assertTrue(self.safety.safety_rx_hook(self.packet(frame)), hex(frame[0]))
    self.safety.safety_tick()
    self.assertTrue(self.safety.safety_config_valid())

  def controller_frames(self, cp, brake_c9, *, brake_pressed=False, lat_active=True):
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    state.update(parsers)
    sources = self.source_frames(packer, brake_c9, brake_pressed)
    source_by_name = {
      "PSCMStatus": {"LKADriverAppldTrq": 0},
      "EBCMWheelSpdFront": {"FLWheelSpd": 40, "FRWheelSpd": 40},
      "EBCMWheelSpdRear": {"RLWheelSpd": 40, "RRWheelSpd": 40},
      "ECMPRDNL2": {"PRNDL2": 4},
      "AcceleratorPedal2": {"CruiseState": 1},
      "ECMEngineStatus": {"CruiseMainOn": 1, "BrakePressed": int(brake_pressed) if brake_c9 else 0},
      "ECMAcceleratorPos": {"BrakePedalPos": 10 if brake_pressed and not brake_c9 else 0},
    }
    required = ("PSCMStatus", "ESPStatus", "EBCMWheelSpdFront", "EBCMWheelSpdRear",
                "EBCMFrictionBrakeStatus", "PSCMSteeringAngle", "ECMPRDNL2", "AcceleratorPedal2",
                "ECMEngineStatus", "BCMTurnSignals", "BCMDoorBeltStatus", "BCMGeneralPlatformStatus",
                "ASCMSteeringButton")
    if not brake_c9:
      required += ("ECMAcceleratorPos",)
    pt_frames = [packer.make_can_msg(name, 0, source_by_name.get(name, {})) for name in required]
    cam_frames = [packer.make_can_msg("ASCMLKASteeringCmd", 2, {}),
                  packer.make_can_msg("ASCMActiveCruiseControlStatus", 2,
                                      {"ACCCruiseState": 2, "ACCSpeedSetpoint": 65})]
    parsers[Bus.pt].update([(1_000_000_000, pt_frames)])
    parsers[Bus.cam].update([(1_000_000_000, cam_frames)])
    self.assertTrue(parsers[Bus.pt].can_valid)
    self.assertTrue(parsers[Bus.cam].can_valid)
    state.out = state.update(parsers).as_reader()
    self.assertTrue(state.out.cruiseState.enabled)
    self.assertGreater(state.out.vEgo, 0)
    self.assertEqual(state.out.gearShifter, structs.CarState.GearShifter.drive)
    self.assertEqual(state.out.brakePressed, brake_pressed)
    self.feed(sources)
    controller = CarController(DBC[cp.carFingerprint], cp)
    controller.frame = 20
    controller.cancel_counter = 11
    control = structs.CarControl()
    control.enabled = True
    control.latActive = lat_active
    control.cruiseControl.cancel = True
    control.actuators.torque = 0.03
    _, commands = controller.update(control.as_reader(), state, 1_100_000_000)
    return packer, sources, commands

  def test_actual_stock_controller_frames_all_five_brake_sources(self):
    for car in SDGM_STOCK_CAR:
      for brake_c9 in (False, True):
        for radar in (False, True):
          with self.subTest(car=car, brake_c9=brake_c9, radar=radar):
            cp = params(car, brake_c9=brake_c9, radar=radar)
            self.mode(cp)
            packer, sources, commands = self.controller_frames(cp, brake_c9)
            self.assertTrue(self.safety.get_controls_allowed())
            self.assertEqual({msg[0] for msg in commands}, {0x180, 0x1E1, 0x184})
            steer = next(msg for msg in commands if msg[0] == 0x180)
            self.assertNotEqual(((steer[1][0] & 0x7) << 8) | steer[1][1], 0)
            cancel = next(msg for msg in commands if msg[0] == 0x1E1)
            self.assertEqual(cancel[2], 0 if car in SDGM_CANCEL_PT_CAR else 2)
            self.assertFalse(self.safety.safety_tx_hook(self.packet(packer.make_can_msg(
              "ASCMSteeringButton", cancel[2], {"ACCButtons": 2}))))
            for msg in commands:
              self.assertTrue(self.safety.safety_tx_hook(self.packet(msg)), hex(msg[0]))
              self.assertFalse(self.safety.safety_tx_hook(self.packet((msg[0], msg[1], 1))))
              self.assertFalse(self.safety.safety_tx_hook(self.packet((msg[0], msg[1][:-1], msg[2]))))
            for addr, length in ((0x315, 5), (0x2CB, 8), (0x370, 6), (0x200, 6)):
              self.assertFalse(self.safety.safety_tx_hook(self.packet((addr, bytes(length), 0))))
            pressed = packer.make_can_msg("ECMEngineStatus" if brake_c9 else "ECMAcceleratorPos", 0,
                                          {"BrakePressed": 1} if brake_c9 else {"BrakePedalPos": 10})
            self.safety.safety_rx_hook(self.packet(pressed))
            self.assertFalse(self.safety.get_controls_allowed())
            self.assertFalse(self.safety.safety_tx_hook(self.packet(steer)))
            self.mode(cp)
            for missing in (0xC9 if brake_c9 else 0xBE, 0x1C4):
              self.mode(cp)
              for frame in sources:
                if frame[0] != missing:
                  self.safety.safety_rx_hook(self.packet(frame))
              self.safety.safety_tick()
              self.assertFalse(self.safety.safety_config_valid(), hex(missing))
            self.mode(cp)
            opposite = packer.make_can_msg("ECMAcceleratorPos" if brake_c9 else "ECMEngineStatus", 0, {})
            for frame in sources:
              if frame[0] != (0xC9 if brake_c9 else 0xBE):
                self.safety.safety_rx_hook(self.packet(frame))
            self.safety.safety_rx_hook(self.packet(opposite))
            self.safety.safety_tick()
            self.assertFalse(self.safety.safety_config_valid())
            self.mode(cp)
            for frame in sources:
              self.safety.safety_rx_hook(self.packet((frame[0], frame[1], 1 if frame[0] == (0xC9 if brake_c9 else 0xBE) else 0)))
            self.safety.safety_tick()
            self.assertFalse(self.safety.safety_config_valid())
            self.mode(cp)
            self.feed(sources)
            self.safety.set_timer(2_100_000)
            self.safety.safety_tick()
            self.assertFalse(self.safety.safety_config_valid())
            self.assertFalse(self.safety.safety_tx_hook(self.packet(steer)))

  def test_inactive_and_braked_parsed_state_emit_zero_steering(self):
    for car in SDGM_STOCK_CAR:
      for brake_c9 in (False, True):
        cp = params(car, brake_c9=brake_c9)
        for brake_pressed, lat_active in ((False, False), (True, False)):
          with self.subTest(car=car, brake_c9=brake_c9, brake_pressed=brake_pressed):
            self.mode(cp)
            _, _, commands = self.controller_frames(cp, brake_c9, brake_pressed=brake_pressed, lat_active=lat_active)
            steer = next(msg for msg in commands if msg[0] == 0x180)
            self.assertEqual(((steer[1][0] & 0x7) << 8) | steer[1][1], 0)
            self.assertTrue(self.safety.safety_tx_hook(self.packet(steer)))

  def test_forwarding_relay_and_raw_conflicts(self):
    for car in (CAR.CADILLAC_XT5, CAR.CHEVROLET_BLAZER):
      cp = params(car)
      self.mode(cp)
      for bus, addr, expected in ((0, 0x123, 2), (0, 0x184, -1), (2, 0x123, 0),
                                  (2, 0x180, -1), (2, 0x315, 0), (1, 0x123, -1)):
        self.assertEqual(self.safety.safety_fwd_hook(bus, addr), expected)
      self.assertFalse(self.safety.get_relay_malfunction())
      self.safety.safety_rx_hook(self.packet((0x184, bytes(8), 2)))
      self.assertFalse(self.safety.get_relay_malfunction())
      self.mode(cp)
      self.safety.safety_rx_hook(self.packet((0x180, bytes(4), 0)))
      self.assertTrue(self.safety.get_relay_malfunction())
      for conflict in (GMSafetyFlags.HW_CAM_LONG, GMSafetyFlags.ASCM_INTERCEPT, GMSafetyFlags.PEDAL_LONG,
                       GMSafetyFlags.NO_ACC, GMSafetyFlags.BOLT_ACC_PEDAL, GMSafetyFlags.ASCM_RADAR):
        raw = cp.safetyConfigs[0].safetyParam | int(conflict)
        self.safety.set_safety_hooks(CarParams.SafetyModel.gm, raw)
        self.safety.init_tests()
        self.assertEqual(self.safety.safety_tx_hook(self.packet((0x184, bytes(8), 2))),
                         conflict == GMSafetyFlags.HW_CAM_LONG and raw in (0x1003, 0x1403) and not self.release)
        self.assertFalse(self.safety.safety_tx_hook(self.packet((0x1E1, bytes(7), 0))))


if __name__ == "__main__":
  unittest.main()
