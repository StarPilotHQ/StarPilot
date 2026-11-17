"""Startup ownership and transmit families across Bolt and Volt installations."""

import unittest

from opendbc.can import CANParser, CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.gm.carcontroller import CarController, fixed_stopping_brake
from opendbc.car.gm.carstate import CarState
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.radar_interface import RADAR_HEADER_MSG
from opendbc.car.gm.tests.test_bolt_acc_pedal_friction import brake_fields
from opendbc.car.gm.tests.test_bolt_pedal import params as pedal_params
from opendbc.car.gm.values import CAR, DBC, CanBus, CruiseButtons, GMFlags, GMSafetyFlags, PEDAL_BOLT_CAR, VOLT_BSM_CAR, BOLT_CC_WORDS


def ordinary_params(candidate, *, alpha=False, sascm=False, radar=False, accelerator=True):
  fingerprint = gen_empty_fingerprint()
  if accelerator:
    fingerprint[CanBus.POWERTRAIN][0xbe] = 6
  if sascm:
    fingerprint[0][0x2ff] = 8
  if radar:
    fingerprint[CanBus.OBSTACLE][RADAR_HEADER_MSG] = 8
  return CarInterface.get_params(candidate, fingerprint, [], alpha, False, False)


def controller_messages(cp, frame, controller=None, *, stock_acc_enabled=False, accel=1.0,
                        speed=12.0, stopping=False, resume=False, long_active=True, buttons_counter=0, standstill=False,
                        gear_age_ns=50_000_000):
  controller = controller or CarController(DBC[cp.carFingerprint], cp)
  controller.frame = frame
  controller.last_steer_frame = frame
  now_nanos = 1_000_000_000 + (frame - 4) * 10_000_000
  control = structs.CarControl()
  control.enabled = True
  control.longActive = long_active
  control.cruiseControl.resume = resume
  control.actuators.accel = accel
  if stopping:
    control.actuators.longControlState = structs.CarControl.Actuators.LongControlState.stopping
  state = structs.CarState()
  state.vEgo = speed
  state.standstill = standstill
  state.cruiseState.available = True
  state.cruiseState.enabled = stock_acc_enabled
  state.gearShifter = structs.CarState.GearShifter.low
  cs = CarState(cp)
  cs.out = state.as_reader()
  cs.pedal_sensor_healthy = True
  cs.pedal_sensor_ts_nanos = now_nanos - 50_000_000
  cs.stock_acc_status_ts_nanos = now_nanos - 50_000_000
  cs.bolt_pedal_gear_ts_nanos = now_nanos - gear_age_ns
  cs.bolt_pedal_main_ts_nanos = now_nanos - 50_000_000
  cs.cam_lka_steering_cmd_counter = 0
  cs.loopback_lka_steering_cmd_updated = False
  cs.loopback_lka_steering_cmd_ts_nanos = now_nanos
  cs.pt_lka_steering_cmd_counter = 0
  cs.buttons_counter = buttons_counter
  cs.pscm_status = dict.fromkeys(("HandsOffSWDetectionMode", "HandsOffSWlDetectionStatus",
                                       "LKATorqueDeliveredStatus", "LKADriverAppldTrq",
                                       "LKATorqueDelivered", "LKATotalTorqueDelivered",
                                       "RollingCounter", "PSCMStatusChecksum"), 0)
  _, messages = controller.update(control.as_reader(), cs, now_nanos)
  return controller, messages


class TestBoltVoltConfigurations(unittest.TestCase):
  def test_bolt_steering_counter_tracks_real_loopback_values(self):
    for identity in PEDAL_BOLT_CAR:
      for alpha in (False, True):
        for removed in (False, True):
          with self.subTest(identity=identity, alpha=alpha, removed=removed):
            cp = pedal_params(identity, setting=True, pedal=True, alpha_long=alpha, removed=removed)
            if identity == CAR.CHEVROLET_BOLT_CC_2018_2021 and not removed:
              self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x9D)
            ci = CarInterface(cp)
            controller = ci.CC
            packer = CANPacker(DBC[identity][Bus.pt])
            control = structs.CarControl()
            for step, (received, expected) in enumerate((([3], 0), ([0], 1), ([], None),
                                                        ([1], 2), ([2, 2], 3), ([3], 0), ([0], 1))):
              now = 1_000_000_000 + step * 100_000_000
              packets = [packer.make_can_msg("ASCMLKASteeringCmd", 128, {"RollingCounter": value}) for value in received]
              ci.update([(now - 20_000_000, packets)])
              self.assertEqual(ci.CS.loopback_lka_steering_cmd_updated, bool(received))
              controller.frame = 20 + step * 10
              _, commands = ci.apply(control.as_reader(), now)
              steering = [message for message in commands if message[0] == 0x180 and message[2] == 0]
              self.assertEqual(len(steering), int(expected is not None))
              if expected is None:
                continue
              data = steering[0][1]
              self.assertEqual((data[0] >> 4) & 3, expected)
              self.assertFalse(data[0] & 8)

  def test_nonpedal_gm_preserves_legacy_loopback_counter_progression(self):
    for identity in (CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_CAMERA, CAR.CHEVROLET_VOLT_2019,
                     CAR.CHEVROLET_BOLT_EUV):
      for alpha in (False, True):
        with self.subTest(identity=identity, alpha=alpha):
          cp = ordinary_params(identity, alpha=alpha, radar=True)
          cs = CarState(cp)
          parsers = cs.get_can_parsers(cp)
          packer = CANPacker(DBC[identity][Bus.pt])
          controller = CarController(DBC[identity], cp)
          self.assertFalse(controller.bolt_steering_ack_counter)
          control = structs.CarControl()
          expected = 0
          for step, received in enumerate(([3], [0, 1], [0, 0], [1], [], [2, 3], [0])):
            now = 1_000_000_000 + step * 100_000_000
            packets = [packer.make_can_msg("ASCMLKASteeringCmd", 128, {"RollingCounter": value}) for value in received]
            for parser in parsers.values():
              parser.update([(now - 20_000_000, packets)])
            cs.out = cs.update(parsers).as_reader()
            expected += bool(received)
            controller.frame = 20 + step * 10
            _, commands = controller.update(control.as_reader(), cs, now)
            steering = [message for message in commands if message[0] == 0x180 and message[2] == 0]
            self.assertEqual(len(steering), 1)
            self.assertEqual((steering[0][1][0] >> 4) & 3, expected % 4)
            self.assertFalse(steering[0][1][0] & 8)

  def test_all_bolt_pedal_generations_withdraw_on_missing_stale_or_future_gear(self):
    for candidate in PEDAL_BOLT_CAR:
      for alpha in (False, True):
        cp = pedal_params(candidate, setting=True, pedal=True, alpha_long=alpha)
        for age in (0, 100_000_000, 100_000_001, -1, 1_000_000_000):
          with self.subTest(candidate=candidate, alpha=alpha, gear_age_ns=age):
            controller, messages = controller_messages(cp, 4, accel=.5, gear_age_ns=age)
            pedal = next(message for message in messages if message[0] == 0x200)
            admitted = 0 <= age <= 100_000_000
            self.assertEqual(bool(pedal[1][4] & 0x80), admitted)
            self.assertEqual(controller.apply_gas > 0, admitted)
            if not admitted:
              self.assertEqual(pedal[1][:4], bytes(4))
              self.assertEqual(controller.apply_brake, 0)
            # Recovery needs a fresh physical gear receipt, not a MAIN cycle.
            controller, recovered = controller_messages(cp, 8, controller, accel=.5, gear_age_ns=0)
            self.assertTrue(next(message for message in recovered if message[0] == 0x200)[1][4] & 0x80)

  def test_volt_bsm_survives_absent_startup_fingerprint_and_decodes_pt_frame(self):
    self.assertEqual(len(VOLT_BSM_CAR), 4)
    for candidate in VOLT_BSM_CAR:
      for alpha in (False, True):
        with self.subTest(candidate=candidate, alpha=alpha):
          cp = ordinary_params(candidate, alpha=alpha, sascm=candidate == CAR.CHEVROLET_VOLT_ASCM)
          self.assertTrue(cp.flags & GMFlags.HAS_BSM.value)
          state = CarState(cp)
          parsers = state.get_can_parsers(cp)
          packer = CANPacker(DBC[candidate][Bus.pt])
          for left, right in ((1, 0), (0, 1), (0, 0)):
            frame = packer.make_can_msg("BCMBlindSpotMonitor", 0, {"LeftBSM": left, "RightBSM": right})
            parsers[Bus.pt].update([(1_000_000_000, [frame])])
            out = state.update(parsers)
            self.assertEqual((out.leftBlindspot, out.rightBlindspot), (bool(left), bool(right)))

  def test_fixed_stop_brake_is_owned_by_non_pedal_longitudinal(self):
    for candidate, alpha, sascm, stop_accel, expected in ((CAR.CHEVROLET_VOLT, False, False, -1.5, 150),
                                                        (CAR.CHEVROLET_VOLT, True, False, -1.5, 150),
                                                        (CAR.CHEVROLET_VOLT_ASCM, True, True, -0.25, 25)):
      with self.subTest(candidate=candidate, alpha=alpha):
        cp = ordinary_params(candidate, alpha=alpha, sascm=sascm, radar=True)
        self.assertTrue(cp.openpilotLongitudinalControl)
        self.assertFalse(cp.dashcamOnly)
        self.assertFalse(cp.flags & GMFlags.PEDAL_LONG)
        self.assertEqual(cp.stopAccel, stop_accel)
        for demand in (-1.5, 0.0, 1.0):
          controller, messages = controller_messages(cp, 4, accel=demand, speed=0.1, stopping=True)
          self.assertEqual(controller.apply_brake, expected)
          self.assertEqual(controller.apply_gas, controller.params.INACTIVE_REGEN)
          brake = next(message for message in messages if message[0] == 0x315)
          self.assertEqual(brake[2], 0 if cp.networkLocation == structs.CarParams.NetworkLocation.fwdCamera else 2)
          self.assertEqual(brake_fields(brake)[1], expected)
        for change in ({'resume': True}, {'speed': 0.5}, {'speed': 1.0}, {'stopping': False}, {'long_active': False}):
          controller, _ = controller_messages(cp, 4, **({'accel': 1.0, 'speed': 0.1, 'stopping': True} | change))
          self.assertEqual(controller.apply_brake, 0)

    pedal_cp = pedal_params(CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, setting=True, pedal=True)
    self.assertTrue(pedal_cp.flags & GMFlags.PEDAL_LONG)
    self.assertEqual(fixed_stopping_brake(True, True, True, False, -0.25, 400), 25)
    stock_cp = ordinary_params(CAR.CHEVROLET_VOLT_ASCM, alpha=False, sascm=True)
    self.assertFalse(stock_cp.openpilotLongitudinalControl)
    _, stock_messages = controller_messages(stock_cp, 4, accel=-1.5, speed=0.1, stopping=True)
    self.assertFalse(any(message[0] == 0x315 for message in stock_messages))

  def test_gateway_volt_fixed_stop_wire_request_matches_original(self):
    # Original gateway stopAccel=-1.5 gives 150 units (12-bit -150 = 0xf6a).
    # Literal wire oracle is independent of the candidate CP and CAN encoder.
    near_stop_payloads = tuple(bytes.fromhex(value) for value in
                               ('af6a509600', 'af6a509501', 'af6a509402', 'af6a509303'))
    full_stop_payloads = tuple(bytes.fromhex(value) for value in
                               ('df6a209600', 'df6a209501', 'df6a209402', 'df6a209303'))
    for alpha in (False, True):
      cp = ordinary_params(CAR.CHEVROLET_VOLT, alpha=alpha, radar=True)
      self.assertEqual(cp.stopAccel, -1.5)
      self.assertEqual(list(cp.longitudinalTuning.kiBP), [5.0, 35.0])
      self.assertEqual(list(cp.longitudinalTuning.kiV), [0.5, 0.5])
      self.assertEqual(cp.longitudinalActuatorDelay, 0.5)
      self.assertEqual(cp.networkLocation, structs.CarParams.NetworkLocation.gateway)
      self.assertTrue(cp.openpilotLongitudinalControl)
      self.assertFalse(cp.pcmCruise)
      self.assertFalse(cp.dashcamOnly)
      for standstill, speed, payloads in ((False, 0.1, near_stop_payloads), (True, 0.0, full_stop_payloads)):
        with self.subTest(alpha=alpha, standstill=standstill):
          controller = None
          for frame in range(4, 20):
            controller, messages = controller_messages(cp, frame, controller, accel=0.0, speed=speed,
                                                       stopping=True, standstill=standstill)
            brake = [message for message in messages if message[0] == 0x315]
            if frame % 4:
              self.assertEqual(brake, [])
            else:
              self.assertEqual(brake, [(0x315, payloads[(frame // 4) % 4], 2)])
              self.assertEqual(controller.apply_brake, 150)
              self.assertEqual(controller.apply_gas, controller.params.INACTIVE_REGEN)
          controller, messages = controller_messages(cp, 20, controller, accel=0.0, speed=speed,
                                                     stopping=True, standstill=standstill, long_active=False)
          self.assertEqual(controller.apply_brake, 0)
          self.assertEqual(brake_fields(next(message for message in messages if message[0] == 0x315))[1], 0)

  def test_gateway_volt_stop_tune_does_not_change_other_volt_owners(self):
    for candidate, stop_accel in ((CAR.CHEVROLET_VOLT_ASCM, -0.25),
                                 (CAR.CHEVROLET_VOLT_CAMERA, -2.0), (CAR.CHEVROLET_VOLT_2019, -0.25)):
      for alpha in (False, True):
        for sascm in (False, True):
          with self.subTest(candidate=candidate, alpha=alpha, sascm=sascm):
            cp = ordinary_params(candidate, alpha=alpha, sascm=sascm, radar=True)
            self.assertEqual(cp.stopAccel, stop_accel)
            self.assertEqual(list(cp.longitudinalTuning.kiV), [0.5, 0.5])
            self.assertEqual(cp.longitudinalActuatorDelay, 0.5)
            system_long = candidate in (CAR.CHEVROLET_VOLT_ASCM, CAR.CHEVROLET_VOLT_2019) and alpha and sascm
            self.assertEqual(cp.openpilotLongitudinalControl, system_long)
            self.assertEqual(cp.pcmCruise, not system_long)
            if not system_long:
              _, messages = controller_messages(cp, 4, accel=-1.5, speed=0.1, stopping=True)
              self.assertFalse(any(message[0] in (0x315, 0x2CB, 0x200) for message in messages))

  def test_acc_pedal_cancels_stock_acc_before_actuating_in_both_alpha_modes(self):
    candidate = CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL
    for alpha in (False, True):
      for counter in range(4):
        with self.subTest(alpha=alpha, counter=counter):
          cp = pedal_params(candidate, setting=True, pedal=True, alpha_long=alpha)
          self.assertFalse(cp.pcmCruise)
          controller, messages = controller_messages(cp, 8, stock_acc_enabled=True, buttons_counter=counter)
          self.assertEqual(controller.pedal_steady, 0.0)
          pedal = next(message for message in messages if message[0] == 0x200)
          self.assertEqual(pedal[1][:4], b"\x00" * 4)
          self.assertFalse(any(message[0] == 0x315 for message in messages))
          buttons = [message for message in messages if message[0] == 0x1E1]
          self.assertEqual(len(buttons), 1)
          self.assertEqual(buttons[0][2], 2)
          parser = CANParser(DBC[candidate][Bus.pt], [("ASCMSteeringButton", 0)], 2)
          parser.update([(1_040_000_000, buttons)])
          self.assertEqual(parser.vl["ASCMSteeringButton"]["ACCButtons"], CruiseButtons.CANCEL)
          self.assertEqual(parser.vl["ASCMSteeringButton"]["RollingCounter"], (counter + 1) % 4)
          controller, _ = controller_messages(cp, 12, controller, stock_acc_enabled=False)
          self.assertGreater(controller.pedal_steady, 0.0)

  def test_four_pedal_identities_require_detected_hardware_independent_of_saved_opt_in_and_alpha(self):
    self.assertEqual(len(PEDAL_BOLT_CAR), 4)
    for candidate in PEDAL_BOLT_CAR:
      for alpha in (False, True):
        for saved, observed in ((False, False), (False, True), (True, False), (True, True)):
          with self.subTest(candidate=candidate, alpha=alpha, saved=saved, observed=observed):
            cp = pedal_params(candidate, setting=saved, pedal=observed, alpha_long=alpha)
            active = observed
            flags = GMSafetyFlags.HW_CAM | GMSafetyFlags.EV
            if candidate != CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL:
              flags |= GMSafetyFlags.NO_ACC
            if candidate == CAR.CHEVROLET_BOLT_CC_2017:
              flags |= GMSafetyFlags.BOLT_2017
            if candidate in (CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL):
              flags |= GMSafetyFlags.BOLT_GEN2
            if active:
              flags |= GMSafetyFlags.PEDAL_LONG | GMSafetyFlags.PADDLE_SCHED
              if candidate == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL:
                flags |= GMSafetyFlags.BOLT_ACC_PEDAL
            fallback = not active and candidate in BOLT_CC_WORDS and BOLT_CC_WORDS[candidate][1] is not None
            self.assertEqual(cp.safetyConfigs[0].safetyParam, BOLT_CC_WORDS[candidate][1] if fallback else flags)
            self.assertEqual(cp.openpilotLongitudinalControl, active or fallback)
            self.assertEqual(bool(cp.flags & GMFlags.PEDAL_LONG), active)
            if active or fallback:
              self.assertFalse(cp.pcmCruise)
            self.assertFalse(cp.alphaLongitudinalAvailable)
            self.assertEqual(DBC[candidate][Bus.pt], "gm_global_a_powertrain_generated")

  def test_bolt_stock_acc_and_volt_modes_keep_distinct_longitudinal_owners(self):
    for candidate, expected in ((CAR.CHEVROLET_BOLT_EUV, (False, True)),
                                (CAR.CHEVROLET_BOLT_ACC_2022_2023, (False, True)),
                                (CAR.CHEVROLET_VOLT, (True, True)),
                                (CAR.CHEVROLET_VOLT_ASCM, (False, True)),
                                (CAR.CHEVROLET_VOLT_2019, (False, False)),
                                (CAR.CHEVROLET_VOLT_CAMERA, (False, False))):
      for alpha in (False, True):
        with self.subTest(candidate=candidate, alpha=alpha):
          cp = ordinary_params(candidate, alpha=alpha, sascm=candidate == CAR.CHEVROLET_VOLT_ASCM)
          op_long = expected[alpha]
          self.assertEqual(cp.openpilotLongitudinalControl, op_long)
          self.assertEqual(cp.pcmCruise, not op_long)
          self.assertEqual(bool(cp.safetyConfigs[0].safetyParam & GMSafetyFlags.HW_CAM_LONG),
                           op_long and candidate != CAR.CHEVROLET_VOLT)
          self.assertFalse(cp.flags & GMFlags.PEDAL_LONG)

  def test_pedal_tx_is_25hz_on_powertrain_with_counter(self):
    for candidate in PEDAL_BOLT_CAR:
      for alpha in (False, True):
        with self.subTest(candidate=candidate, alpha=alpha):
          cp = pedal_params(candidate, setting=True, pedal=True, alpha_long=alpha)
          controller = CarController(DBC[candidate], cp)
          for frame in range(4, 69):
            controller, messages = controller_messages(cp, frame, controller)
            longitudinal = [message for message in messages if message[0] in (0x200, 0x1F5, 0xBD, 0x2CB, 0x315)]
            self.assertNotIn(0x2CB, [message[0] for message in longitudinal])
            if candidate != CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL:
              self.assertNotIn(0x315, [message[0] for message in longitudinal])
            pedal = [message for message in longitudinal if message[0] != 0x315]
            if frame % 4:
              self.assertEqual(pedal, [])
            else:
              self.assertEqual([(message[0], message[2]) for message in pedal],
                               [(0x200, 0), (0x1F5, 0), (0xBD, 0)])
              self.assertEqual(pedal[0][1][4] & 0xF, (frame // 4) % 16)
              self.assertGreater(controller.pedal_steady, 0.0)


if __name__ == "__main__":
  unittest.main()
