import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.gm.carstate import CarState
from opendbc.car.gm import gmcan
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
from opendbc.car.gm.tests.test_cc_gateway_stock import params, pt_frames
from opendbc.car.gm.values import CC_GATEWAY_STOCK_CAR, DBC, GMSafetyFlags, is_ordinary_cc_profile
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py


class TestGmCcGatewayStock(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety

  def tearDown(self):
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(CarParams.SafetyModel.noOutput, 0)

  @staticmethod
  def packet(msg):
    return libsafety_py.make_CANPacket(msg[0], msg[2], msg[1])

  def mode(self, param=16):
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, param), 0)
    self.safety.init_tests()
    self.safety.set_timer(1_000_000)

  def feed(self, frames):
    for frame in frames:
      self.assertTrue(self.safety.safety_rx_hook(self.packet(frame)), hex(frame[0]))
    self.safety.safety_tick()
    self.assertTrue(self.safety.safety_config_valid())

  def joined(self, car, *, cruise=True, main=True, brake=False, gas=False, cancel=True, active=True, acc_cruise=0,
             now_nanos=1_050_000_000, disabled=False):
    cp = params(car)
    prepare_disable_longitudinal(cp, disabled)
    self.assertEqual(cp.openpilotLongitudinalControl, is_ordinary_cc_profile(cp))
    packer = CANPacker(DBC[car][Bus.pt])
    interface = CarInterface(cp)
    interface.update([])
    frames = pt_frames(packer, cruise=cruise, main=main, brake=brake, gas=gas,
                       counter=1, acc_cruise=acc_cruise)
    interface.update([(1_000_000_000, frames)])
    replacements = [gmcan.create_buttons(packer, 0, 2, 1),
                    packer.make_can_msg("ECMEngineStatus", 0, {"CruiseMainOn": int(main), "BrakePressed": int(brake)}),
                    packer.make_can_msg("EBCMWheelSpdRear", 0, {"RLWheelSpd": 60, "RRWheelSpd": 60, "RLWheelDir": 1, "RRWheelDir": 1})]
    replacement_by_address = {frame[0]: frame for frame in replacements}
    frames = [replacement_by_address.get(frame[0], frame) for frame in frames]
    state_out = interface.update([(1_010_000_000, frames)])
    self.assertTrue(state_out.canValid)
    state = interface.CS
    self.mode(cp.safetyConfigs[0].safetyParam)
    # Establish required-source health before an advancing physical neutral
    # observation earns cancel credit; cold incomplete RX must not grant it.
    initial_frames = [gmcan.create_buttons(packer, 0, 1, 1) if frame[0] == 0x1E1 else frame for frame in frames]
    self.feed(initial_frames)
    self.safety.set_timer(1_010_000)
    self.feed(frames)
    controller = interface.CC
    controller.frame = 20
    controller.cancel_counter = 11
    control = structs.CarControl()
    control.enabled = True
    if is_ordinary_cc_profile(cp) and cancel and cruise and main and not brake and not gas:
      controller.volt_cc_prev_enabled = True
      control.enabled = False
    control.latActive = active
    control.longActive = True  # A caller request must never change stock ownership.
    control.cruiseControl.cancel = cancel
    control.actuators.torque = 0.03
    control.actuators.accel = 1.5
    _, commands = controller.update(control.as_reader(), state, now_nanos)
    self.joined_cp, self.joined_interface, self.joined_state = cp, interface, state_out
    return packer, frames, commands

  def test_real_packed_controller_into_native_all_nine(self):
    for car in sorted(CC_GATEWAY_STOCK_CAR):
      with self.subTest(car=car):
        packer, frames, commands = self.joined(car)
        self.assertTrue(self.safety.get_controls_allowed())
        self.assertEqual({m[0] for m in commands}, {0x180, 0x1E1})
        steer = next(m for m in commands if m[0] == 0x180)
        cancel = next(m for m in commands if m[0] == 0x1E1)
        self.assertNotEqual(((steer[1][0] & 7) << 8) | steer[1][1], 0)
        self.assertEqual(cancel[2], 0)
        self.assertTrue(self.safety.safety_tx_hook(self.packet(steer)))
        self.assertTrue(self.safety.safety_tx_hook(self.packet(cancel)), (car, self.joined_cp.safetyConfigs[0].safetyParam, cancel))
        self.assertFalse(self.safety.safety_tx_hook(self.packet(cancel)))  # replay
        self.assertFalse(self.safety.safety_tx_hook(self.packet((cancel[0], cancel[1], 2))))
        self.assertFalse(self.safety.safety_tx_hook(self.packet((cancel[0], cancel[1][:-1], 0))))
        for index in (0, 1, 2, 3, 4, 5, 6):
          mutated = bytearray(cancel[1])
          mutated[index] ^= 1
          self.assertFalse(self.safety.safety_tx_hook(self.packet((cancel[0], bytes(mutated), 0))), index)
        for button in (2, 3, 5):
          self.assertFalse(self.safety.safety_tx_hook(self.packet(packer.make_can_msg(
            "ASCMSteeringButton", 0, {"ACCButtons": button, "RollingCounter": 1}))))
        for addr, bus, length in ((0x3D1, 0, 8), (0x409, 0, 7), (0x40A, 0, 7),
                                  (0x2CB, 0, 8), (0x315, 2, 5), (0x200, 0, 6), (0x370, 0, 6)):
          if addr in (0x409, 0x40A) and is_ordinary_cc_profile(params(car)):
            self.assertTrue(self.safety.safety_tx_hook(self.packet((addr, bytes(length), bus))))
            self.assertFalse(self.safety.safety_tx_hook(self.packet((addr, b"\x01" + bytes(length - 1), bus))))
            self.assertFalse(self.safety.safety_tx_hook(self.packet((addr, bytes(length), 2))))
          else:
            self.assertFalse(self.safety.safety_tx_hook(self.packet((addr, bytes(length), bus))), hex(addr))

  def test_disabled_speed_control_uses_stock_native_owner(self):
    for car in sorted(CC_GATEWAY_STOCK_CAR):
      with self.subTest(car=car):
        _, _, commands = self.joined(car, disabled=True)
        self.assertFalse(self.joined_cp.openpilotLongitudinalControl)
        self.assertTrue(self.joined_cp.pcmCruise)
        self.assertEqual(self.joined_cp.safetyConfigs[0].safetyParam, 16)
        for command in commands:
          self.assertTrue(self.safety.safety_tx_hook(self.packet(command)))
        for address, length in ((0x409, 7), (0x40A, 7), (0x200, 6), (0x2CB, 8)):
          self.assertFalse(self.safety.safety_tx_hook(self.packet((address, bytes(length), 0))))

  def test_cruise_brake_gas_main_and_host_neutrality(self):
    for car in sorted(CC_GATEWAY_STOCK_CAR):
      for cruise, main, brake, gas, active in ((False, True, False, False, True),
                                               (True, False, False, False, True),
                                               (True, True, True, False, True),
                                               (True, True, False, True, True),
                                               (True, True, False, False, False)):
        _, _, commands = self.joined(car, cruise=cruise, main=main,
                                     brake=brake, gas=gas, active=active)
        steer = next(m for m in commands if m[0] == 0x180)
        if active and main and cruise and is_ordinary_cc_profile(params(car)):
          # C160 consumes the caller axis; native independently rejects brake.
          # Arbitrary active requests are retained here as adversarial probes.
          self.assertGreater(((steer[1][0] & 7) << 8) | steer[1][1], 0, (car, cruise, main, brake, gas, active))
        else:
          self.assertEqual(((steer[1][0] & 7) << 8) | steer[1][1], 0, (car, cruise, main, brake, gas, active))
        self.assertEqual(self.safety.safety_tx_hook(self.packet(steer)),
                         not (brake and active and main and cruise and is_ordinary_cc_profile(params(car))))
        if not cruise or not main or brake:
          self.assertFalse(self.safety.get_controls_allowed())

  def test_cancel_integrity_freshness_and_counter_wrap(self):
    car = next(iter(CC_GATEWAY_STOCK_CAR))
    packer = CANPacker(DBC[car][Bus.pt])
    self.mode()
    self.feed(pt_frames(packer, counter=3))
    for counter in (3, 0, 1):
      if counter != 3:
        self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
          "ASCMSteeringButton", 0, {"ACCButtons": 1, "RollingCounter": counter})))
      cancel = packer.make_can_msg("ASCMSteeringButton", 0, {
        "ACCButtons": 6, "RollingCounter": counter, "ACCAlwaysOne": 1,
        "SteeringButtonChecksum": 0xFF + counter * 0x4EF - 80,
      })
      for index in range(7):
        corrupted = bytearray(cancel[1])
        corrupted[index] ^= 1
        self.assertFalse(self.safety.safety_tx_hook(self.packet((cancel[0], bytes(corrupted), 0))), index)
      self.assertTrue(self.safety.safety_tx_hook(self.packet(cancel)))
      self.assertFalse(self.safety.safety_tx_hook(self.packet(cancel)))
    self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "ASCMSteeringButton", 0, {"ACCButtons": 1, "RollingCounter": 2})))
    self.safety.set_timer(1_200_000)
    stale = packer.make_can_msg("ASCMSteeringButton", 0, {
      "ACCButtons": 6, "RollingCounter": 2, "ACCAlwaysOne": 1,
      "SteeringButtonChecksum": 0xFF + 2 * 0x4EF - 80,
    })
    self.assertFalse(self.safety.safety_tx_hook(self.packet(stale)))

  def test_host_rejects_stale_source_before_native(self):
    _, _, commands = self.joined(next(iter(CC_GATEWAY_STOCK_CAR)), now_nanos=1_400_000_000)
    self.assertEqual({m[0] for m in commands}, {0x180})
    steer = commands[0]
    self.assertEqual(((steer[1][0] & 7) << 8) | steer[1][1], 0)

  def test_stock_buttons_do_not_arm_without_pcm_cruise(self):
    car = next(iter(CC_GATEWAY_STOCK_CAR))
    packer = CANPacker(DBC[car][Bus.pt])
    self.mode()
    self.feed(pt_frames(packer, cruise=False))
    for button in (2, 3, 5):
      self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
        "ASCMSteeringButton", 0, {"ACCButtons": button, "RollingCounter": 2})))
      self.assertFalse(self.safety.get_controls_allowed())
    self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "ECMCruiseControl", 0, {"CruiseActive": 1})))
    self.assertTrue(self.safety.get_controls_allowed())
    self.safety.safety_rx_hook(self.packet(packer.make_can_msg(
      "ECMCruiseControl", 0, {"CruiseActive": 0})))
    self.assertFalse(self.safety.get_controls_allowed())

  def test_acc_status_cannot_arm_conventional_cruise(self):
    car = next(iter(CC_GATEWAY_STOCK_CAR))
    packer = CANPacker(DBC[car][Bus.pt])
    for cruise, main in ((False, True), (True, False)):
      _, _, commands = self.joined(car, cruise=cruise, main=main, acc_cruise=1)
      self.assertFalse(self.safety.get_controls_allowed())
      steer = next(msg for msg in commands if msg[0] == 0x180)
      self.assertEqual(((steer[1][0] & 7) << 8) | steer[1][1], 0)
      self.safety.safety_rx_hook(self.packet(packer.make_can_msg("AcceleratorPedal2", 0, {"CruiseState": 1})))
      self.assertFalse(self.safety.get_controls_allowed())
      self.assertFalse(any(msg[0] == 0x1E1 for msg in commands))

  def test_required_health_wrong_source_stale_and_raw_conflicts(self):
    car = next(iter(CC_GATEWAY_STOCK_CAR))
    packer = CANPacker(DBC[car][Bus.pt])
    frames = pt_frames(packer)
    for brake_length in (6, 7, 8):
      self.mode()
      sized = [(addr, data.ljust(brake_length, b"\x00"), bus) if addr == 0xBE else (addr, data, bus)
               for addr, data, bus in frames]
      self.feed(sized)
      state = CarState(params(car))
      parser = state.get_can_parsers(state.CP)[Bus.pt]
      parser.update([(1_000_000_000, sized)])
      self.assertTrue(parser.can_valid, brake_length)
    for missing in (0x3D1, 0x1E1, 0xBE, 0xC9, 0x1C4, 0x184, 0x34A):
      self.mode()
      for frame in frames:
        if frame[0] != missing:
          self.safety.safety_rx_hook(self.packet(frame))
      self.safety.safety_tick()
      self.assertFalse(self.safety.safety_config_valid(), hex(missing))
    self.mode()
    for frame in frames:
      self.safety.safety_rx_hook(self.packet((frame[0], frame[1], 1 if frame[0] == 0x3D1 else 0)))
    self.safety.safety_tick()
    self.assertFalse(self.safety.safety_config_valid())
    self.mode()
    for frame in frames:
      self.safety.safety_rx_hook(self.packet((frame[0], frame[1][:-1] if frame[0] == 0x3D1 else frame[1], 0)))
    self.safety.safety_tick()
    self.assertFalse(self.safety.safety_config_valid())
    self.mode()
    self.feed(frames)
    self.safety.set_timer(2_100_000)
    self.safety.safety_tick()
    self.assertFalse(self.safety.safety_config_valid())
    for conflict in (GMSafetyFlags.HW_CAM_LONG, GMSafetyFlags.PEDAL_LONG,
                     GMSafetyFlags.ASCM_INTERCEPT, GMSafetyFlags.SDGM, GMSafetyFlags.BOLT_2017,
                     GMSafetyFlags.ASCM_RADAR, 0x4000):
      self.mode(16 | int(conflict))
      self.safety.set_controls_allowed(True)
      self.assertFalse(self.safety.safety_tx_hook(self.packet((0x180, bytes(4), 0))))
      self.assertFalse(self.safety.safety_tx_hook(self.packet((0x1E1, bytes(7), 0))))
    # Raw 17 is the pre-existing camera/Bolt NO_ACC mode, not this gateway profile.
    self.mode(16 | int(GMSafetyFlags.HW_CAM))
    self.assertTrue(self.safety.safety_tx_hook(self.packet((0x184, bytes(8), 2))))
    self.assertFalse(self.safety.safety_tx_hook(self.packet((0x1E1, bytes(7), 0))))

  def test_forwarding_and_relay(self):
    self.mode()
    for bus, addr, expected in ((0, 0x123, -1), (2, 0x123, -1), (1, 0x123, -1)):
      self.assertEqual(self.safety.safety_fwd_hook(bus, addr), expected)
    self.safety.safety_rx_hook(self.packet((0x180, bytes(4), 0)))
    self.assertTrue(self.safety.get_relay_malfunction())


if __name__ == "__main__":
  unittest.main()
