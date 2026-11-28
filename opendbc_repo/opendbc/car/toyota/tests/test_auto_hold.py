import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.toyota import toyotacan
from opendbc.car.toyota.carcontroller import CarController
from opendbc.car.toyota.carstate import CarState
from opendbc.car.toyota.interface import CarInterface, apply_toyota_auto_hold, toyota_auto_hold_supported
from opendbc.car.toyota.values import CAR, DBC, ToyotaFlags, ToyotaSafetyFlags, TOYOTA_AUTO_HOLD_CARS, uses_toyota_auto_hold_aeb
from opendbc.safety import ALTERNATIVE_EXPERIENCE as AE


def setup_hold(car=CAR.TOYOTA_COROLLA_TSS2, enabled=True, *, car_fw=()):
  cp = CarInterface.get_params(car, {0: {}, 1: {}, 2: {}}, list(car_fw), False, False, False)
  apply_toyota_auto_hold(cp, enabled)
  state = CarState(cp)
  state.out = structs.CarState()
  state.out.canValid = True
  state.out.standstill = True
  state.out.brakePressed = True
  state.out.cruiseState.available = True
  state.out.gearShifter = structs.CarState.GearShifter.drive
  return cp, CarController(DBC[car], cp), structs.CarControl(), state


def step_hold(controller, command, state):
  return controller.update(command.as_reader(), state, controller.frame * 10_000_000)[1]


def decode(cp, frame, name):
  parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [(name, 0)], frame[2])
  parser.update([(1, [frame])])
  return parser.vl[name]


def tick_acc(cp, controller, command, state):
  # Cover a complete send interval through the production PID/standstill path.
  frames = [frame for _ in range(3) for frame in step_hold(controller, command, state) if frame[0] == 0x343]
  assert len(frames) == 1
  return decode(cp, frames[0], 'ACC_CONTROL')


def setup_cruise_hold(car=CAR.TOYOTA_COROLLA_TSS2, enabled=True, *, car_fw=()):
  cp, controller, command, state = setup_hold(car, enabled, car_fw=car_fw)
  command.enabled = command.longActive = True
  command.actuators.accel = -0.7
  command.actuators.longControlState = structs.CarControl.Actuators.LongControlState.stopping
  state.out.brakePressed = False
  state.out.cruiseState.enabled = state.out.cruiseState.standstill = True
  return cp, controller, command, state


class TestToyotaAutoHold(unittest.TestCase):
  def test_invalid_can_withdraws_hold_and_requires_fresh_stop(self):
    for car, hybrid in ((CAR.TOYOTA_CAMRY_TSS2, False), (CAR.TOYOTA_RAV4_TSS2, True),
                         (CAR.TOYOTA_COROLLA_TSS2, False)):
      for timeout in (False, True):
        with self.subTest(car=car, timeout=timeout):
          fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)] if hybrid else []
          cp, owner, command, state = setup_hold(car, car_fw=fw)
          for _ in range(101):
            step_hold(owner, command, state)
          self.assertTrue(owner.brake_hold_active)
          if uses_toyota_auto_hold_aeb(cp):
            frames = [frame for _ in range(2) for frame in step_hold(owner, command, state)]
            held = next(frame for frame in frames if frame[0] == 0x344)
            self.assertEqual(decode(cp, held, 'PRE_COLLISION_2')['DSS1GDRV'], -1.0)
          else:
            self.assert_acc_hold(tick_acc(cp, owner, command, state))
          state.out.canValid = timeout
          state.out.canTimeout = timeout
          frames = [frame for _ in range(3) for frame in step_hold(owner, command, state)]
          self.assertFalse(owner.brake_hold_active)
          self.assertEqual(owner._brake_hold_counter, 0)
          self.assertFalse(any(frame[0] == 0x344 for frame in frames))
          for frame in frames:
            if frame[0] == 0x343:
              self.assertNotEqual(decode(cp, frame, 'ACC_CONTROL')['ACCEL_CMD'], -1.0)
          state.out.canValid, state.out.canTimeout = True, False
          state.out.brakePressed = False
          for _ in range(105):
            step_hold(owner, command, state)
          self.assertFalse(owner.brake_hold_active)
          state.out.brakePressed = True
          for _ in range(100):
            step_hold(owner, command, state)
          self.assertFalse(owner.brake_hold_active)
          step_hold(owner, command, state)
          self.assertTrue(owner.brake_hold_active)

  def test_detected_early_hybrid_uses_manual_aeb_hold(self):
    for car, hybrid, aeb in ((CAR.TOYOTA_COROLLA_TSS2, True, True),
                             (CAR.TOYOTA_COROLLA_TSS2, False, False),
                             (CAR.TOYOTA_RAV4_TSS2, True, True),
                             (CAR.TOYOTA_RAV4_TSS2, False, False),
                             (CAR.TOYOTA_RAV4_TSS2_2022, True, False),
                             (CAR.TOYOTA_CAMRY_TSS2, False, True)):
      with self.subTest(car=car, hybrid=hybrid):
        fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)] if hybrid else []
        cp = CarInterface.get_params(car, {0: {0x3F6: 8}, 1: {}, 2: {}}, fw, True, False, False)
        self.assertEqual(bool(cp.flags & ToyotaFlags.HYBRID), hybrid)
        self.assertTrue(cp.flags & ToyotaFlags.HAS_BSM)
        self.assertEqual(uses_toyota_auto_hold_aeb(cp), aeb)
        admitted = apply_toyota_auto_hold(cp, True)
        if car == CAR.TOYOTA_RAV4_TSS2_2022:
          self.assertFalse(admitted)
          self.assertFalse(cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
          self.assertEqual(cp.alternativeExperience, 0)
          self.assertNotIn('PRE_COLLISION_2', CarState.get_can_parsers(cp)[Bus.cam].vl)
          continue
        self.assertTrue(admitted)
        self.assertEqual(cp.alternativeExperience, AE.TOYOTA_AEB_HOLD if aeb else AE.TOYOTA_AUTO_HOLD)
        state = CarState(cp)
        parsers = state.get_can_parsers(cp)
        self.assertEqual('PRE_COLLISION_2' in parsers[Bus.cam].vl, aeb)
        if not aeb:
          continue
        camera = CANPacker(DBC[car][Bus.pt]).make_can_msg('PRE_COLLISION_2', 2,
          {'DSS1GDRV': -0.5, 'PBRTRGR': 1, 'DS1STAT2': 3})
        parsers[Bus.cam].update([(1_000_000_000, [camera])])
        state.update(parsers)
        self.assertEqual(toyotacan.create_brake_hold_command(
          CANPacker(DBC[car][Bus.pt]), 0, state.pre_collision_2, False)[1], camera[1])
        state.out = structs.CarState(canValid=True)
        state.out.standstill = state.out.brakePressed = state.out.cruiseState.available = True
        state.out.gearShifter = structs.CarState.GearShifter.drive
        owner = CarController(DBC[car], cp)
        command = structs.CarControl()
        state.out.cruiseState.enabled = command.longActive = True
        for _ in range(105):
          step_hold(owner, command, state)
        self.assertFalse(owner.brake_hold_active)
        state.out.cruiseState.enabled = command.longActive = False
        for _ in range(100):
          step_hold(owner, command, state)
        self.assertFalse(owner.brake_hold_active)
        frames = [frame for _ in range(2) for frame in step_hold(owner, command, state)]
        self.assertTrue(owner.brake_hold_active)
        frame = next(frame for frame in frames if frame[0] == 0x344)
        self.assertEqual(decode(cp, frame, 'PRE_COLLISION_2')['DSS1GDRV'], -1.0)
        state.out.brakePressed = False
        released = [frame for _ in range(2) for frame in step_hold(owner, command, state) if frame[0] == 0x344]
        self.assertTrue(owner.brake_hold_active)
        self.assertEqual(decode(cp, released[0], 'PRE_COLLISION_2')['DSS1GDRV'], -1.0)
        state.out.gasPressed = True
        step_hold(owner, command, state)
        self.assertFalse(owner.brake_hold_active)

  def assert_acc_hold(self, values):
    self.assertEqual(values['ACCEL_CMD'], -1.0)
    self.assertEqual(values['PERMIT_BRAKING'], 1)
    self.assertEqual(values['RELEASE_STANDSTILL'], 0)
    self.assertEqual(values['CANCEL_REQ'], 0)

  def test_admission_and_default_off_preserve_unrelated_bits(self):
    for car in CAR:
      with self.subTest(car=car):
        cp = CarInterface.get_params(car, {0: {}, 1: {}, 2: {}}, [], False, False, False)
        self.assertFalse(cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
        cp.alternativeExperience = AE.ALLOW_AEB | AE.DISABLE_STOCK_AEB
        expected = car in TOYOTA_AUTO_HOLD_CARS and cp.openpilotLongitudinalControl and not cp.dashcamOnly
        self.assertEqual(apply_toyota_auto_hold(cp, True), expected)
        if expected:
          bit = AE.TOYOTA_AEB_HOLD if car == CAR.TOYOTA_CAMRY_TSS2 else AE.TOYOTA_AUTO_HOLD
          self.assertEqual(cp.alternativeExperience, bit | AE.ALLOW_AEB | AE.DISABLE_STOCK_AEB)
        apply_toyota_auto_hold(cp, False)
        self.assertEqual(cp.alternativeExperience, AE.ALLOW_AEB | AE.DISABLE_STOCK_AEB)
        self.assertFalse(cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
    for field in ('passive', 'notCar', 'dashcamOnly'):
      cp, _, _, _ = setup_hold(enabled=False)
      setattr(cp, field, True)
      self.assertFalse(toyota_auto_hold_supported(cp))
    cp, _, _, _ = setup_hold(enabled=False)
    cp.brand = 'subaru'
    cp.flags = 4096
    cp.alternativeExperience = 128
    self.assertFalse(apply_toyota_auto_hold(cp, True))
    self.assertEqual((cp.flags, cp.alternativeExperience), (4096, 128))
    cp, _, _, _ = setup_hold(enabled=False)
    cp.safetyConfigs[0].safetyParam |= int(ToyotaSafetyFlags.STOCK_LONGITUDINAL)
    self.assertFalse(apply_toyota_auto_hold(cp, True))

  def test_acc_activation_brake_release_and_cancel(self):
    cp, controller, command, state = setup_hold()
    for _ in range(100):
      step_hold(controller, command, state)
      self.assertFalse(controller.brake_hold_active)
    step_hold(controller, command, state)
    self.assertTrue(controller.brake_hold_active)
    state.out.brakePressed = False
    for _ in range(5):
      sends = step_hold(controller, command, state)
      for frame in sends:
        if frame[0] == 0x343:
          values = decode(cp, frame, 'ACC_CONTROL')
          self.assertEqual(values['ACCEL_CMD'], -1.0)
          self.assertEqual(values['PERMIT_BRAKING'], 1)
          self.assertEqual(values['RELEASE_STANDSTILL'], 0)
          self.assertEqual(values['CANCEL_REQ'], 0)
    command.cruiseControl.cancel = True
    step_hold(controller, command, state)
    self.assertFalse(controller.brake_hold_active)
    self.assertEqual(controller._brake_hold_counter, 0)

  def test_acc_and_camry_sensor_revocations(self):
    for car in (CAR.TOYOTA_COROLLA_TSS2, CAR.TOYOTA_CAMRY_TSS2):
      for cause in ('gas', 'moving', 'main', 'cruise', 'park', 'reverse'):
        with self.subTest(car=car, cause=cause):
          _, controller, command, state = setup_hold(car)
          for _ in range(101):
            step_hold(controller, command, state)
          self.assertTrue(controller.brake_hold_active)
          if cause == 'gas':
            state.out.gasPressed = True
          elif cause == 'moving':
            state.out.standstill = False
          elif cause == 'main':
            state.out.cruiseState.available = False
          elif cause == 'cruise':
            state.out.cruiseState.enabled = True
          else:
            state.out.gearShifter = getattr(structs.CarState.GearShifter, cause)
          step_hold(controller, command, state)
          self.assertFalse(controller.brake_hold_active)
          self.assertEqual(controller._brake_hold_counter, 0)

  def test_interrupted_acc_brake_dwell_restarts(self):
    cp, controller, command, state = setup_hold()
    for _ in range(50):
      step_hold(controller, command, state)
    state.out.brakePressed = False
    for _ in range(10):
      step_hold(controller, command, state)
    self.assertEqual(controller._brake_hold_counter, 0)
    state.out.brakePressed = True
    for _ in range(50):
      step_hold(controller, command, state)
    self.assertFalse(controller.brake_hold_active)
    for _ in range(51):
      step_hold(controller, command, state)
    self.assertTrue(controller.brake_hold_active)
    self.assert_acc_hold(tick_acc(cp, controller, command, state))

  def test_auto_hold_preserves_normal_cruise_stop_and_go(self):
    for car, hybrid in ((CAR.TOYOTA_COROLLA_TSS2, False), (CAR.TOYOTA_RAV4_TSS2, False),
                        (CAR.TOYOTA_RAV4_TSS2, True), (CAR.TOYOTA_CAMRY_TSS2, False)):
      with self.subTest(car=car, hybrid=hybrid):
        fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)] if hybrid else []
        cp, controller, command, state = setup_cruise_hold(car, car_fw=fw)
        base_cp, baseline, base_command, base_state = setup_cruise_hold(car, enabled=False, car_fw=fw)
        self.assertTrue(cp.openpilotLongitudinalControl)
        self.assertTrue(cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
        self.assertEqual(bool(cp.flags & ToyotaFlags.HYBRID), hybrid)
        for _ in range(50):
          actual = tick_acc(cp, controller, command, state)
          expected = tick_acc(base_cp, baseline, base_command, base_state)
          self.assertFalse(controller.brake_hold_active)
          self.assertEqual(dict(actual), dict(expected))
        for owner_command in (command, base_command):
          owner_command.actuators.accel = 1.5
          owner_command.actuators.longControlState = structs.CarControl.Actuators.LongControlState.starting
          owner_command.cruiseControl.resume = True
        for _ in range(30):
          actual = tick_acc(cp, controller, command, state)
          expected = tick_acc(base_cp, baseline, base_command, base_state)
          self.assertFalse(controller.brake_hold_active)
          self.assertFalse(state.out.gasPressed)
          self.assertEqual(dict(actual), dict(expected))
        self.assertGreater(actual['ACCEL_CMD'], 0)
        self.assertEqual(actual['RELEASE_STANDSTILL'], 1)

  def test_engaged_cruise_never_acquires_manual_hold(self):
    for long_state in ('off', 'pid', 'starting', 'stopping'):
      for brake_pressed in (False, True):
        with self.subTest(long_state=long_state, brake_pressed=brake_pressed):
          cp, controller, command, state = setup_cruise_hold()
          command.actuators.longControlState = getattr(structs.CarControl.Actuators.LongControlState, long_state)
          state.out.brakePressed = brake_pressed
          for _ in range(105):
            step_hold(controller, command, state)
            self.assertFalse(controller.brake_hold_active)

  def test_manual_hold_releases_when_conditions_change(self):
    for cause in ('capability', 'cancel', 'main', 'park', 'reverse', 'moving', 'cruise'):
      with self.subTest(cause=cause):
        cp, controller, command, state = setup_hold()
        for _ in range(35):
          values = tick_acc(cp, controller, command, state)
        self.assert_acc_hold(values)
        if cause == 'capability':
          apply_toyota_auto_hold(cp, False)
        elif cause == 'cancel':
          command.cruiseControl.cancel = True
        elif cause == 'main':
          state.out.cruiseState.available = False
        elif cause in ('park', 'reverse'):
          state.out.gearShifter = getattr(structs.CarState.GearShifter, cause)
        elif cause == 'moving':
          state.out.standstill = state.out.cruiseState.standstill = False
        else:
          state.out.cruiseState.enabled = command.longActive = True
        state.out.brakePressed = False
        values = tick_acc(cp, controller, command, state)
        self.assertFalse(controller.brake_hold_active)
        self.assertEqual(controller._brake_hold_counter, 0)
        self.assertEqual(values['RELEASE_STANDSTILL'], 1)
        self.assertEqual(values['CANCEL_REQ'], cause == 'cancel')
        if cause == 'cruise':
          command.actuators.accel = 1.5
          command.actuators.longControlState = structs.CarControl.Actuators.LongControlState.starting
          command.cruiseControl.resume = True
          for _ in range(20):
            values = tick_acc(cp, controller, command, state)
            self.assertFalse(controller.brake_hold_active)
          self.assertGreater(values['ACCEL_CMD'], 0)
          self.assertEqual(values['RELEASE_STANDSTILL'], 1)

  def test_manual_hold_with_lateral_only_and_gas_release(self):
    cp, controller, command, state = setup_hold()
    command.latActive = True
    for _ in range(35):
      values = tick_acc(cp, controller, command, state)
    self.assert_acc_hold(values)
    state.out.brakePressed = False
    self.assert_acc_hold(tick_acc(cp, controller, command, state))
    state.out.gasPressed = True
    values = tick_acc(cp, controller, command, state)
    self.assertFalse(controller.brake_hold_active)
    self.assertEqual(values['ACCEL_CMD'], 0)
    self.assertEqual(values['RELEASE_STANDSTILL'], 1)

    state.out.gasPressed = False
    for _ in range(50):
      tick_acc(cp, controller, command, state)
      self.assertFalse(controller.brake_hold_active)
    state.out.brakePressed = True
    for _ in range(35):
      values = tick_acc(cp, controller, command, state)
    self.assertTrue(controller.brake_hold_active)
    self.assert_acc_hold(values)

  def test_camry_interrupted_dwell_retains_manual_aeb_behavior(self):
    cp, controller, command, state = setup_hold(CAR.TOYOTA_CAMRY_TSS2)
    for _ in range(50):
      step_hold(controller, command, state)
    state.out.brakePressed = False
    for _ in range(10):
      step_hold(controller, command, state)
    self.assertEqual(controller._brake_hold_counter, 50)
    state.out.brakePressed = True
    for _ in range(51):
      step_hold(controller, command, state)
    self.assertTrue(controller.brake_hold_active)
    frames = [frame for _ in range(2) for frame in step_hold(controller, command, state) if frame[0] == 0x344]
    frame = frames[0]
    self.assertEqual(decode(cp, frame, 'PRE_COLLISION_2')['DSS1GDRV'], -1.0)

  def test_camry_source_cancel_distinction_and_pulse(self):
    cp, controller, command, state = setup_hold(CAR.TOYOTA_CAMRY_TSS2)
    command.cruiseControl.cancel = True
    for _ in range(101):
      sends = step_hold(controller, command, state)
    self.assertTrue(controller.brake_hold_active)
    frame = next(frame for frame in sends if frame[0] == 0x344)
    values = decode(cp, frame, 'PRE_COLLISION_2')
    self.assertEqual(values['DSS1GDRV'], -1.0)
    self.assertEqual(values['PBRTRGR'], 1)
    self.assertEqual(frame[2], 0)
    self.assertFalse(any(frame[0] == 0x344 for frame in step_hold(controller, command, state)))
    for tick in (726, 728, 730):
      frame = toyotacan.create_brake_hold_command(controller.packer, tick, {}, True)
      self.assertEqual(decode(cp, frame, 'PRE_COLLISION_2')['PBRTRGR'], tick % 730 < 727)

  def test_camry_camera_capture_and_inactive_pass_through(self):
    cp, controller, command, state = setup_hold(CAR.TOYOTA_CAMRY_TSS2)
    camera = CANPacker(DBC[cp.carFingerprint][Bus.pt]).make_can_msg('PRE_COLLISION_2', 2,
      {'DSS1GDRV': -0.5, 'PBRTRGR': 1, 'DS1STAT2': 3, 'PCSWAR': 1, 'PCSABK': 1, 'BRKHLD': 1, 'PCSDIS': 1})
    parsers = state.get_can_parsers(cp)
    state.update(parsers)
    parsers[Bus.cam].update([(1_000_000_000, [camera])])
    state.update(parsers)
    output = toyotacan.create_brake_hold_command(controller.packer, 0, state.pre_collision_2, False)
    self.assertEqual(output[1], camera[1])
    self.assertIn('PRE_COLLISION_2', parsers[Bus.cam].vl)
    cp, _, _, _ = setup_hold(CAR.TOYOTA_CAMRY_TSS2, enabled=False)
    self.assertNotIn('PRE_COLLISION_2', CarState.get_can_parsers(cp)[Bus.cam].vl)

  def test_feature_off_has_no_hold_or_camera_messages(self):
    for car in (CAR.TOYOTA_COROLLA_TSS2, CAR.TOYOTA_CAMRY_TSS2):
      cp, controller, command, state = setup_hold(car, False)
      packer = CANPacker(DBC[car][Bus.pt])
      for _ in range(105):
        sends = step_hold(controller, command, state)
        self.assertFalse(controller.brake_hold_active)
        self.assertFalse(any(frame[0] == 0x344 for frame in sends))
        for frame in sends:
          if frame[0] == 0x343:
            self.assertEqual(frame, toyotacan.create_accel_command(packer, 0, False, True, False, True, 1, False, 0))
