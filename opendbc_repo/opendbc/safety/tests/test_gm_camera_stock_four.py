import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.carstate import CarState
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.fingerprints import FINGERPRINTS, FW_VERSIONS
from opendbc.car.gm.values import CAMERA_STOCK_CAR, CAR, CanBus, DBC, GMSafetyFlags, GMFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py


class TestGmCameraStockFour(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.release = self.safety.set_safety_hooks(CarParams.SafetyModel.allOutput, 0) != 0

  @staticmethod
  def packet(msg):
    return libsafety_py.make_CANPacket(msg[0], msg[2], msg[1])

  @staticmethod
  def params(car, *, alpha=False, release=False, pedal=False):
    fingerprint = {CanBus.POWERTRAIN: {0x201: 6} if pedal else {}, CanBus.CAMERA: {}, CanBus.OBSTACLE: {}}
    if car in (CAR.CHEVROLET_SUBURBAN_CAMERA, CAR.CHEVROLET_TRAX):
      fingerprint[CanBus.POWERTRAIN].update({0xF1: 6, 0xBE: 6})
      fingerprint[CanBus.CAMERA].update({0x320: 6, 0x180: 4})
    return CarInterface.get_params(car, fingerprint, [], alpha, release, False)

  @staticmethod
  def frames(packer, car, *, main=True, cruise=True, brake=False, gas=False, speed=54, acc_state=2):
    def make(name, bus, values):
      return packer.make_can_msg(name, bus, values)
    pt = [
      make('PSCMStatus', 0, {}),
      make('ESPStatus', 0, {'TractionControlOn': 1}),
      make('EBCMWheelSpdFront', 0, {'FLWheelSpd': speed, 'FRWheelSpd': speed}),
      make('EBCMWheelSpdRear', 0, {'RLWheelSpd': speed, 'RRWheelSpd': speed}),
      make('EBCMFrictionBrakeStatus', 0, {}),
      make('PSCMSteeringAngle', 0, {}),
      make('ECMAcceleratorPos', 0, {}),
      make('EBCMBrakePedalPosition', 0, {'BrakePedalPosition': 20 if brake else 0}),
      make('ECMPRDNL2', 0, {}),
      make('AcceleratorPedal2', 0, {'CruiseState': 1 if cruise else 0, 'AcceleratorPedal2': 20 if gas else 0}),
      make('ECMEngineStatus', 0, {'CruiseMainOn': int(main), 'BrakePressed': int(brake)}),
      make('BCMTurnSignals', 0, {}),
      make('BCMDoorBeltStatus', 0, {'LeftSeatBelt': 1}),
      make('BCMGeneralPlatformStatus', 0, {}),
      make('ASCMSteeringButton', 0, {'ACCButtons': 1, 'RollingCounter': 1}),
    ]
    if car in (CAR.CHEVROLET_BOLT_ACC_2022_2023, CAR.CHEVROLET_VOLT_CAMERA):
      pt.append(make('EBCMRegenPaddle', 0, {}))
    if car == CAR.CHEVROLET_SUBURBAN_CAMERA:
      pt.append(make('ECMCruiseControl', 0, {'CruiseSetSpeed': 64}))
    cam = [
      make('ASCMLKASteeringCmd', 2, {'RollingCounter': 0}),
      make('AEBCmd', 2, {}),
      make('ASCMActiveCruiseControlStatus', 2, {'ACCSpeedSetpoint': 88, 'ACCCruiseState': acc_state}),
    ]
    return pt, cam

  def mode(self, cp):
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, cp.safetyConfigs[0].safetyParam), 0)
    self.safety.init_tests()
    self.safety.set_timer(1_000_000)

  def joined(self, car, *, main=True, cruise=True, brake=False, gas=False, lat=True, cancel=True, now=1_050_000_000):
    cp = self.params(car, alpha=False)
    packer = CANPacker(DBC[car][Bus.pt])
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    pt, cam = self.frames(packer, car, main=main, cruise=cruise, brake=brake, gas=gas)
    parsers[Bus.pt].update([(1_000_000_000, pt)])
    parsers[Bus.cam].update([(1_000_000_000, cam)])
    self.assertTrue(parsers[Bus.pt].can_valid, car)
    self.assertTrue(parsers[Bus.cam].can_valid, car)
    state.out = state.update(parsers).as_reader()
    self.mode(cp)
    for msg in pt:
      self.assertTrue(self.safety.safety_rx_hook(self.packet(msg)), hex(msg[0]))
    self.safety.safety_tick()
    self.assertTrue(self.safety.safety_config_valid(), car)
    controller = CarController(DBC[car], cp)
    controller.frame = 20
    controller.cancel_counter = 11
    control = structs.CarControl()
    control.enabled = True
    control.latActive = lat
    control.longActive = True  # Caller intent cannot alter stock longitudinal ownership.
    control.cruiseControl.cancel = cancel
    control.actuators.torque = 0.03
    control.actuators.accel = 1.5
    _, commands = controller.update(control.as_reader(), state, now)
    return cp, packer, parsers, state, pt, cam, commands

  def test_params_stock_only_and_explicit_modes(self):
    for car in CAMERA_STOCK_CAR:
      for alpha in (False, True):
        for release in (False, True):
          with self.subTest(car=car, alpha=alpha, release=release):
            cp = self.params(car, alpha=alpha, release=release, pedal=False)
            expected = GMSafetyFlags.HW_CAM | (GMSafetyFlags.EV if car in
                       (CAR.CHEVROLET_BOLT_ACC_2022_2023, CAR.CHEVROLET_VOLT_CAMERA) else 0)
            promoted = car in (CAR.CHEVROLET_SUBURBAN_CAMERA, CAR.CHEVROLET_TRAX)
            longitudinal = car == CAR.CHEVROLET_TRAX and alpha and not release
            if promoted:
              expected = 0xC170 if longitudinal else 0xC171
            self.assertEqual(cp.safetyConfigs[0].safetyParam, expected)
            self.assertEqual(cp.pcmCruise, not longitudinal)
            self.assertEqual(cp.openpilotLongitudinalControl, longitudinal)
            self.assertEqual(cp.alphaLongitudinalAvailable, car == CAR.CHEVROLET_TRAX and not release)
            self.assertFalse(cp.flags & GMFlags.PEDAL_LONG)
            self.assertEqual(cp.dashcamOnly, not promoted)
            if car == CAR.CHEVROLET_TRAX:
              # The recorded layout is registered, but its CAN collision still requires VIN qualification.
              self.assertEqual(len(FINGERPRINTS[car]), 1)
              expected_layout = {0xBE: 6, 0xD3: 2, 0x130: 3, 0x1C4: 8, 0x1E1: 7}
              self.assertEqual({address: FINGERPRINTS[car][0].get(address) for address in expected_layout}, expected_layout)
            else:
              self.assertNotIn(car, FINGERPRINTS)
            self.assertNotIn(car, FW_VERSIONS)

  def test_incomplete_detected_interceptor_graph_does_not_grant_longitudinal(self):
    for car, word in ((CAR.CHEVROLET_SUBURBAN_CAMERA, 0xE110), (CAR.CHEVROLET_TRAX, 0xE110),
                      (CAR.CHEVROLET_VOLT_CAMERA, 0xE212)):
      for alpha in (False, True):
        for release in (False, True):
          cp = self.params(car, alpha=alpha, release=release, pedal=True)
          self.assertEqual(cp.safetyConfigs[0].safetyParam, word)
          self.assertFalse(cp.openpilotLongitudinalControl)
          self.assertTrue(cp.pcmCruise)
          self.assertTrue(cp.dashcamOnly)
          self.assertTrue(cp.flags & GMFlags.PEDAL_LONG)

  def test_joined_stock_steering_cancel_pscm_and_long_denial(self):
    for car in CAMERA_STOCK_CAR:
      with self.subTest(car=car):
        cp, packer, parsers, state, pt, cam, commands = self.joined(car)
        self.assertEqual({msg[0] for msg in commands}, {0x180, 0x1E1, 0x184})
        steer = next(msg for msg in commands if msg[0] == 0x180)
        cancel = next(msg for msg in commands if msg[0] == 0x1E1)
        pscm = next(msg for msg in commands if msg[0] == 0x184)
        self.assertNotEqual(((steer[1][0] & 7) << 8) | steer[1][1], 0)
        self.assertEqual(cancel[2], 2)
        self.assertEqual(pscm[2], 2)
        cancel_parser = CANParser(DBC[car][Bus.pt], [('ASCMSteeringButton', 10)], 2)
        cancel_parser.update([(1_050_000_000, [cancel])])
        self.assertEqual(cancel_parser.vl['ASCMSteeringButton']['ACCButtons'], 6)
        self.assertEqual(cancel_parser.vl['ASCMSteeringButton']['RollingCounter'], state.buttons_counter)
        self.assertEqual(cancel_parser.vl['ASCMSteeringButton']['ACCAlwaysOne'], 1)
        self.assertEqual(cancel_parser.vl['ASCMSteeringButton']['DistanceButton'], 0)
        self.assertTrue(self.safety.get_controls_allowed())
        for msg in commands:
          self.assertTrue(self.safety.safety_tx_hook(self.packet(msg)), hex(msg[0]))
        self.assertFalse(self.safety.safety_tx_hook(self.packet((cancel[0], cancel[1], 0))))
        self.assertFalse(self.safety.safety_tx_hook(self.packet((steer[0], steer[1][:-1], 0))))
        for addr, bus, length in ((0x315, 0, 5), (0x2CB, 0, 8), (0x370, 0, 6), (0x200, 0, 6), (0x409, 0, 7)):
          self.assertFalse(self.safety.safety_tx_hook(self.packet((addr, bytes(length), bus))), hex(addr))
        self.assertAlmostEqual(state.out.cruiseState.speed,
                               88 / 3.6, places=5)
        self.assertFalse(state.out.cruiseState.nonAdaptive)
        self.assertFalse(state.out.regenBraking)

  def test_volt_regen_is_observed_pt_state(self):
    car = CAR.CHEVROLET_VOLT_CAMERA
    cp = self.params(car)
    packer = CANPacker(DBC[car][Bus.pt])
    pt, cam = self.frames(packer, car)
    pt = [m for m in pt if m[0] != 0xBD]
    pt.append(packer.make_can_msg('EBCMRegenPaddle', 0, {'RegenPaddle': 1}))
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    parsers[Bus.pt].update([(1_000_000_000, pt)])
    parsers[Bus.cam].update([(1_000_000_000, cam)])
    self.assertTrue(parsers[Bus.pt].can_valid)
    state.out = state.update(parsers).as_reader()
    self.assertTrue(state.out.regenBraking)

  def test_ev_regen_handoff_neutralizes_real_steering(self):
    for car in (CAR.CHEVROLET_BOLT_ACC_2022_2023, CAR.CHEVROLET_VOLT_CAMERA):
      with self.subTest(car=car):
        cp = self.params(car)
        packer = CANPacker(DBC[car][Bus.pt])
        pt, cam = self.frames(packer, car)
        pt = [m for m in pt if m[0] != 0xBD]
        pt.append(packer.make_can_msg('EBCMRegenPaddle', 0, {'RegenPaddle': 1}))
        state = CarState(cp)
        parsers = state.get_can_parsers(cp)
        parsers[Bus.pt].update([(1_000_000_000, pt)])
        parsers[Bus.cam].update([(1_000_000_000, cam)])
        state.out = state.update(parsers).as_reader()
        self.assertTrue(state.out.regenBraking)
        self.mode(cp)
        for frame in pt:
          self.assertTrue(self.safety.safety_rx_hook(self.packet(frame)))
        self.safety.safety_tick()
        self.assertTrue(self.safety.safety_config_valid())
        self.assertFalse(self.safety.get_controls_allowed())
        controller = CarController(DBC[car], cp)
        controller.frame = 20
        control = structs.CarControl()
        control.enabled = True
        control.latActive = True
        control.actuators.torque = 0.03
        _, commands = controller.update(control.as_reader(), state, 1_050_000_000)
        steer = next(m for m in commands if m[0] == 0x180)
        self.assertEqual(((steer[1][0] & 7) << 8) | steer[1][1], 0)
        self.assertTrue(self.safety.safety_tx_hook(self.packet(steer)))

  def test_suburban_camera_set_speed_keeps_camera_adaptive_state(self):
    car = CAR.CHEVROLET_SUBURBAN_CAMERA
    cp = self.params(car)
    packer = CANPacker(DBC[car][Bus.pt])
    pt, cam = self.frames(packer, car, acc_state=1)
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    parsers[Bus.pt].update([(1_000_000_000, pt)])
    parsers[Bus.cam].update([(1_000_000_000, cam)])
    state.out = state.update(parsers).as_reader()
    self.assertAlmostEqual(state.out.cruiseState.speed, 88 / 3.6, places=5)
    self.assertTrue(state.out.cruiseState.nonAdaptive)

  def test_host_neutral_on_inactive_brake_and_gas(self):
    for car in CAMERA_STOCK_CAR:
      for scenario in ({'lat': False}, {'brake': True}, {'gas': True}, {'gas': True, 'lat': False}, {'main': False}, {'cruise': False}):
        with self.subTest(car=car, scenario=scenario):
          _, _, _, state, _, _, commands = self.joined(car, **scenario)
          steer = next(msg for msg in commands if msg[0] == 0x180)
          if scenario in ({'brake': True}, {'cruise': False}):
            self.assertFalse(self.safety.get_controls_allowed())
          if scenario == {'gas': True} and car in (CAR.CHEVROLET_TRAX, CAR.CHEVROLET_SUBURBAN_CAMERA):
            # These ordinary stock profiles preserve lateral steering on gas.
            self.assertGreater(((steer[1][0] & 7) << 8) | steer[1][1], 0)
          else:
            self.assertEqual(((steer[1][0] & 7) << 8) | steer[1][1], 0)
          self.assertTrue(self.safety.safety_tx_hook(self.packet(steer)))

  def test_missing_camera_or_pt_source_and_stale_status(self):
    for car in CAMERA_STOCK_CAR:
      with self.subTest(car=car):
        cp = self.params(car)
        packer = CANPacker(DBC[car][Bus.pt])
        pt, cam = self.frames(packer, car)
        for missing_bus, missing_addr in ((Bus.cam, 0x180), (Bus.cam, 0x320), (Bus.cam, 0x370),
                                          (Bus.pt, 0xC9), (Bus.pt, 0x1C4), (Bus.pt, 0xBE)):
          with self.subTest(missing_bus=missing_bus, missing_addr=hex(missing_addr)):
            state = CarState(cp)
            parsers = state.get_can_parsers(cp)
            parsers[Bus.pt].update([(1_000_000_000, [m for m in pt if m[0] != missing_addr or missing_bus != Bus.pt])])
            parsers[Bus.cam].update([(1_000_000_000, [m for m in cam if m[0] != missing_addr or missing_bus != Bus.cam])])
            self.assertFalse(parsers[missing_bus].can_valid)
            state.out = state.update(parsers).as_reader()
            self.assertFalse(state.camera_stock_sources_valid)
            controller = CarController(DBC[car], cp)
            controller.frame = 20
            control = structs.CarControl()
            control.enabled = True
            control.latActive = True
            control.actuators.torque = 0.03
            _, commands = controller.update(control.as_reader(), state, 1_050_000_000)
            steer = next(m for m in commands if m[0] == 0x180)
            self.assertEqual(((steer[1][0] & 7) << 8) | steer[1][1], 0)

        _, _, _, state, _, _, commands = self.joined(car, now=1_400_000_001)
        steer = next(m for m in commands if m[0] == 0x180)
        self.assertEqual(((steer[1][0] & 7) << 8) | steer[1][1], 0)
        self.safety.set_timer(2_100_000)
        self.safety.safety_tick()
        self.assertFalse(self.safety.safety_config_valid())

  def test_stock_forwarding_relay_and_release_raw_long(self):
    car = CAR.CHEVROLET_TRAX
    cp = self.params(car)
    self.mode(cp)
    for bus, addr, expected in ((0, 0x123, 2), (0, 0x184, -1), (2, 0x123, 0),
                                (2, 0x180, -1), (1, 0x123, -1)):
      self.assertEqual(self.safety.safety_fwd_hook(bus, addr), expected)
    self.assertFalse(self.safety.get_relay_malfunction())
    self.safety.safety_rx_hook(self.packet((0x184, bytes(8), 2)))
    self.assertTrue(self.safety.get_relay_malfunction())

    # The shared DEBUG camera-long profile serves other GM cars; this batch never
    # selects it. RELEASE must deny the raw active-long actuator address.
    self.assertEqual(self.safety.set_safety_hooks(CarParams.SafetyModel.gm, 3), 0)
    self.safety.init_tests()
    self.safety.set_controls_allowed(True)
    active_brake = (0x315, bytes(5), 0)
    self.assertEqual(self.safety.safety_tx_hook(self.packet(active_brake)), not self.release)
