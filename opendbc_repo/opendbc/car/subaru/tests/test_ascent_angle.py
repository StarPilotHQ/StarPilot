import unittest
from math import isclose

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.subaru.carcontroller import CarController
from opendbc.car.subaru.carstate import CarState
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.values import CAR, DBC, SubaruFlags, SubaruSafetyFlags
from opendbc.safety.tests import test_subaru_gen2_angle_pair as pair


CAR_ID = CAR.SUBARU_ASCENT_2023


def setup_controller(car=CAR_ID):
  cp = CarInterface.get_params(car, {0: {}, 1: {}, 2: {}}, [], False, False, False)
  _, frames = pair.TestSubaruGen2AnglePair.sources(car)
  state = CarState(cp)
  parsers = state.get_can_parsers(cp)
  state.update(parsers)
  for parser in parsers.values():
    parser.update([(1_000_000_000, frames)])
  state.out = state.update(parsers)
  command = structs.CarControl()
  command.enabled = command.latActive = True
  command.actuators.steeringAngleDeg = -1.45
  return cp, CarController(DBC[car], cp), command, state


def step(controller, command, state):
  _, sends = controller.update(command.as_reader(), state, 1_000_000_000 + controller.frame * 10_000_000)
  if controller.frame % 2 == 0:
    _, sends = controller.update(command.as_reader(), state, 1_000_000_000 + controller.frame * 10_000_000)
  steer = next(frame for frame in sends if frame[0] == 0x124)
  parser = CANParser(DBC[controller.CP.carFingerprint][Bus.pt], [('ES_LKAS_ANGLE', 0)], steer[2])
  parser.update([(1, [steer])])
  return steer, parser.vl['ES_LKAS_ANGLE']


def receive_distance(cp, state, parsers, counter, now):
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  bus = 1 if cp.flags & SubaruFlags.GLOBAL_GEN2 else 2
  stock = packer.make_can_msg('ES_Distance', bus, {
    'COUNTER': counter, 'Cruise_Throttle': 2123, 'Car_Follow': 1,
    'Close_Distance': 4.0, 'Cruise_EPB': 1,
  })
  for parser in parsers.values():
    parser.update([(now, [stock])])
  state.out = state.update(parsers)
  assert int(state.es_distance_msg['COUNTER']) == counter, (counter, state.es_distance_msg)
  return stock


class TestSubaruAscentController(unittest.TestCase):
  def test_ascent_and_outback_angle_admission(self):
    cp, _, _, state = setup_controller()
    assert not cp.dashcamOnly
    assert not cp.openpilotLongitudinalControl
    assert not cp.alphaLongitudinalAvailable
    assert cp.steerControlType == structs.CarParams.SteerControlType.angle
    assert cp.safetyConfigs[0].safetyParam == (SubaruSafetyFlags.GEN2 | SubaruSafetyFlags.LKAS_ANGLE |
                                             SubaruSafetyFlags.FIXED_ANGLE_LIMITS | SubaruSafetyFlags.ANGLE_MAIN_BUS)
    assert state.out.cruiseState.enabled
    assert state.out.cruiseState.available
    outback = CarInterface.get_non_essential_params(CAR.SUBARU_OUTBACK_2023)
    assert not outback.dashcamOnly
    assert not outback.openpilotLongitudinalControl
    assert not outback.alphaLongitudinalAvailable
    assert outback.steerControlType == structs.CarParams.SteerControlType.angle
    assert outback.safetyConfigs[0].safetyParam == (SubaruSafetyFlags.GEN2 | SubaruSafetyFlags.LKAS_ANGLE |
                                                  SubaruSafetyFlags.FIXED_ANGLE_LIMITS | SubaruSafetyFlags.D_PLATFORM)
    _, controller, command, state = setup_controller(CAR.SUBARU_OUTBACK_2023)
    command.latActive = False
    _, sends = controller.update(command.as_reader(), state, 1_000_000_000)
    assert not any(frame[0] == 0x122 for frame in sends)
    angle = next(frame for frame in sends if frame[0] == 0x124)
    parser = CANParser(DBC[CAR.SUBARU_OUTBACK_2023][Bus.pt], [('ES_LKAS_ANGLE', 0)], angle[2])
    parser.update([(1, [angle])])
    assert angle[2] == 0
    assert parser.vl['ES_LKAS_ANGLE']['LKAS_Request'] == 0

    for unsupported in (CAR.SUBARU_CROSSTREK_HYBRID, CAR.SUBARU_FORESTER_PREGLOBAL):
      unsupported_cp = CarInterface.get_non_essential_params(unsupported)
      assert unsupported_cp.dashcamOnly
      assert not unsupported_cp.safetyConfigs[0].safetyParam & SubaruSafetyFlags.LKAS_ANGLE

  def test_first_entry_and_reentry_use_original_transmitted_history(self):
    _, controller, command, state = setup_controller()
    state.out.vEgoRaw = 30.3
    state.out.steeringAngleDeg = 0.78
    state.out.steeringRateDeg = -1.5
    controller.angle_handoff_active = True
    steer, decoded = step(controller, command, state)
    assert steer[2] == 0
    assert decoded['LKAS_Request'] == 0
    assert decoded['LKAS_Output'] == 0.78
    state.out.steeringAngleDeg = 0.74
    state.out.steeringRateDeg = -1.99
    _, decoded = step(controller, command, state)
    assert decoded['LKAS_Request'] == 1
    assert isclose(decoded['LKAS_Output'], 0.53, abs_tol=0.01)

    _, initial, command, state = setup_controller()
    state.out.vEgoRaw = 30.3
    state.out.steeringAngleDeg = 20.0
    state.out.steeringRateDeg = 0.0
    _, decoded = step(initial, command, state)
    assert decoded['LKAS_Request'] == 0
    assert decoded['LKAS_Output'] == 20.0
    _, decoded = step(initial, command, state)
    assert decoded['LKAS_Request'] == 1
    assert 19.7 < decoded['LKAS_Output'] < 20.0

  def test_driver_override_hysteresis_and_settled_handoff(self):
    _, controller, command, state = setup_controller()
    state.out.vEgoRaw = 30.3
    state.out.steeringAngleDeg = -25.06
    state.out.steeringTorque = -250
    state.out.steeringRateDeg = 35
    for _ in range(2):
      _, decoded = step(controller, command, state)
    assert decoded['LKAS_Request'] == 0
    state.out.steeringTorque = -150
    state.out.steeringRateDeg = 0
    _, decoded = step(controller, command, state)
    assert decoded['LKAS_Request'] == 0
    state.out.steeringTorque = -149
    state.out.steeringAngleDeg = -17.91
    _, decoded = step(controller, command, state)
    assert decoded['LKAS_Request'] == 0
    assert decoded['LKAS_Output'] == -17.91
    _, decoded = step(controller, command, state)
    assert decoded['LKAS_Request'] == 1
    assert -17.91 < decoded['LKAS_Output'] < -17.6

  def test_ascent_inactive_follows_measurement_and_requires_stock_permission(self):
    for revocation in ('brake', 'standstill', 'park', 'reverse', 'cruise', 'main', 'disabled', 'lateral'):
      with self.subTest(revocation=revocation):
        _, controller, command, state = setup_controller()
        state.out.steeringAngleDeg = 12.5
        if revocation == 'brake':
          state.out.brakePressed = True
        elif revocation == 'standstill':
          state.out.standstill = True
        elif revocation in ('park', 'reverse'):
          state.out.gearShifter = getattr(structs.CarState.GearShifter, revocation)
        elif revocation == 'cruise':
          state.out.cruiseState.enabled = False
        elif revocation == 'main':
          state.out.cruiseState.available = False
        elif revocation == 'disabled':
          command.enabled = False
        else:
          command.latActive = False
        _, decoded = step(controller, command, state)
        assert decoded['LKAS_Request'] == 0
        assert decoded['LKAS_Output'] == 12.5

  def test_inactive_ascent_preserves_fixed_angle_bound(self):
    for measured in (-600.0, 600.0):
      with self.subTest(measured=measured):
        _, controller, command, state = setup_controller()
        state.out.steeringAngleDeg = measured
        command.latActive = False
        _, decoded = step(controller, command, state)
        assert decoded['LKAS_Request'] == 0
        assert decoded['LKAS_Output'] == (545.0 if measured > 0 else -545.0)

  def test_cancel_waits_for_next_stock_counter_and_wraps(self):
    cp, controller, command, state = setup_controller()
    parsers = state.get_can_parsers(cp)
    # Register lazy parser subscriptions before the first source frame.
    state.out = state.update(parsers)
    command.enabled = command.latActive = False
    decoder = CANParser(DBC[CAR_ID][Bus.pt], [('ES_Distance', 0)], 1)
    observed = []
    for tick, (stock_counter, cancel) in enumerate((
        (14, False), (14, True), (15, True), (15, True), (0, True),
        (0, True), (1, False), (1, True), (2, True))):
      now = 1_000_000_000 + tick * 10_000_000
      receive_distance(cp, state, parsers, stock_counter, now)
      command.cruiseControl.cancel = cancel
      _, sends = controller.update(command.as_reader(), state, now)
      distance = [frame for frame in sends if frame[0] == 0x221]
      self.assertLessEqual(len(distance), 1)
      for frame in distance:
        self.assertEqual(frame[2], 1)
        decoder.update([(now, [frame])])
        values = decoder.vl['ES_Distance']
        observed.append(values['COUNTER'])
        self.assertEqual(values['Cruise_Cancel'], 1)
        self.assertEqual(values['Cruise_Throttle'], 1818)
        self.assertEqual(values['Car_Follow'], 1)
        self.assertEqual(values['Cruise_EPB'], 1)
        self.assertEqual(values['Close_Distance'], state.es_distance_msg['Close_Distance'])
    self.assertEqual(observed, [0, 1, 3])

  def test_repeated_stock_cancel_gate_cars(self):
    for car in (CAR.SUBARU_ASCENT_2023, CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025,
                CAR.SUBARU_LEGACY_2025, CAR.SUBARU_OUTBACK_2023, CAR.SUBARU_ASCENT):
      with self.subTest(car=car):
        cp, controller, command, state = setup_controller(car)
        command.enabled = command.latActive = False
        command.cruiseControl.cancel = True
        parsers = state.get_can_parsers(cp)
        # Register lazy parser subscriptions before the first source frame.
        state.out = state.update(parsers)
        receive_distance(cp, state, parsers, 11, 1_000_000_000)
        distance = []
        for tick in range(5):
          _, sends = controller.update(command.as_reader(), state, 1_000_000_000 + tick * 10_000_000)
          distance.extend(frame for frame in sends if frame[0] == 0x221)
        self.assertEqual(len(distance), 1 if car in (CAR.SUBARU_ASCENT_2023, CAR.SUBARU_LEGACY_2025) else 5)
        self.assertEqual(len(set(distance)), 1)
        self.assertEqual(distance[0][2], 1 if cp.flags & SubaruFlags.GLOBAL_GEN2 else 0)

  def test_ascent_dash_tracks_current_angle_request_through_withdrawal_and_reentry(self):
    for withdrawal in ('brake', 'reverse', 'main', 'cruise', 'lateral', 'driver'):
      with self.subTest(withdrawal=withdrawal):
        cp, controller, command, state = setup_controller()
        state.es_lkas_state_msg['LKAS_ACTIVE'] = 1
        decoder = CANParser(DBC[cp.carFingerprint][Bus.pt], [('ES_LKAS_State', 0), ('ES_LKAS_ANGLE', 0)], 0)
        observed = []
        for tick in range(41):
          withdraw = 12 <= tick < 30
          state.out.brakePressed = withdraw and withdrawal == 'brake'
          state.out.gearShifter = (structs.CarState.GearShifter.reverse if withdraw and withdrawal == 'reverse'
                                  else structs.CarState.GearShifter.drive)
          state.out.cruiseState.available = not (withdraw and withdrawal == 'main')
          state.out.cruiseState.enabled = not (withdraw and withdrawal == 'cruise')
          command.latActive = not (withdraw and withdrawal == 'lateral')
          state.out.steeringTorque = 250 if withdraw and withdrawal == 'driver' else 0
          now = 1_000_000_000 + tick * 10_000_000
          _, sends = controller.update(command.as_reader(), state, now)
          decoder.update([(now, sends)])
          if tick % 10 == 0:
            dash = decoder.vl['ES_LKAS_State']
            requested = decoder.vl['ES_LKAS_ANGLE']['LKAS_Request']
            self.assertEqual(dash['LKAS_ACTIVE'], requested)
            self.assertEqual(dash['LKAS_Dash_State'], 2 if requested else 0)
            observed.append(bool(requested))
        self.assertEqual(observed[:3], [False, True, False])
        self.assertTrue(observed[-1])
