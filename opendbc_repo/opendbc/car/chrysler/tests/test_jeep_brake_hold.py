"""Real Jeep parser/controller stock-stop retention; no system speed authority."""

import os
import unittest
from unittest.mock import patch
from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.chrysler.brake_hold import apply_brake_hold, brake_hold_supported
from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR, DBC


def params(candidate=CAR.JEEP_GRAND_CHEROKEE, *, enabled=True):
  cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], False, False, False)
  apply_brake_hold(cp, enabled)
  return cp


class PhysicalCan:
  def __init__(self, ci):
    ci.update([])
    self.ci = ci
    self.packers = {bus: CANPacker(parser.dbc.name) for bus, parser in ci.can_parsers.items()}

  def frames(self, tick, *, active=True, decel=-2.0, main=True, gas=0.0, brake=0, speed=0.0, gear=4, fault=0, omit=()):
    values = {
      'DAS_3': {
        'ACC_ACTIVE': int(active),
        'ACC_AVAILABLE': int(main),
        'ACC_DECEL': decel,
        'ACC_STANDSTILL': 1,
        'ACC_FAULTED': fault,
        'ENGINE_TORQUE_REQUEST': 13.25,
        'DISABLE_FUEL_SHUTOFF': 1,
        'COLLISION_BRK_PREP': 1,
        'COUNTER': (14 + tick // 2) % 16,
      },
      'DAS_4': {'ACC_SET_SPEED_KPH': 100, 'ACC_STATE': 3},
      'GEAR': {'PRNDL': gear},
      'ECM_5': {'Accelerator_Position': gas},
      'ESP_1': {'Brake_Pedal_State': brake},
      'SPEED_1': {'SPEED_LEFT': speed, 'SPEED_RIGHT': speed},
    }
    return [
      self.packers[bus].make_can_msg(parser.dbc.addr_to_msg[address].name, parser.bus, values.get(parser.dbc.addr_to_msg[address].name, {}))
      for bus, parser in self.ci.can_parsers.items()
      for address in sorted(parser.addresses)
      if parser.dbc.addr_to_msg[address].name not in omit
    ]


def apply_at_boot(ci, command, now):
  with patch('opendbc.car.chrysler.carcontroller.time.clock_gettime_ns', return_value=now):
    return ci.apply(command.as_reader(), now)


class TestJeepBrakeHold(unittest.TestCase):
  def setUp(self):
    prior = os.environ.pop('REPLAY', None)

    def restore():
      if prior is None:
        os.environ.pop('REPLAY', None)
      else:
        os.environ['REPLAY'] = prior

    self.addCleanup(restore)

  def ready(self, candidate=CAR.JEEP_GRAND_CHEROKEE):
    ci = CarInterface(params(candidate))
    stream = PhysicalCan(ci)
    cc = structs.CarControl(enabled=True)
    for tick in range(20):
      ci.update([(100_000_000_000 + tick * 10_000_000, stream.frames(tick))])
      apply_at_boot(ci, cc, 100_000_000_000 + tick * 10_000_000)
    self.assertTrue(ci.CS.out.canValid and ci.CS.brake_hold)
    return ci, stream, cc

  def test_two_exact_platforms_stock_ownership_default_off_and_siblings(self):
    for candidate in CAR:
      for alpha_long, release in ((False, False), (False, True), (True, False), (True, True)):
        cp = CarInterface.get_params(candidate, gen_empty_fingerprint(), [], alpha_long, release, False)
        # WK2 2019 alpha-long (non-release) takes DAS_3 ownership; hold stays stock-ACC-only.
        wk2_alpha = (candidate == CAR.JEEP_GRAND_CHEROKEE_2019 and alpha_long and not release)
        jeep = candidate in (CAR.JEEP_GRAND_CHEROKEE, CAR.JEEP_GRAND_CHEROKEE_2019)
        expected_hold = jeep and not wk2_alpha
        self.assertEqual(brake_hold_supported(cp), expected_hold, (candidate, alpha_long, release))
        self.assertEqual(cp.openpilotLongitudinalControl, wk2_alpha)
        self.assertEqual(cp.pcmCruise, not wk2_alpha)
        before = cp.to_dict()
        apply_brake_hold(cp, True)
        self.assertEqual(cp.safetyConfigs[0].safetyParam,
                         4 if expected_hold else before['safetyConfigs'][0]['safetyParam'])
        apply_brake_hold(cp, False)
        self.assertEqual(cp.to_dict(), before)

  def test_real_parser_copied_fields_decel_counter_wrap_and_repeat(self):
    for candidate in (CAR.JEEP_GRAND_CHEROKEE, CAR.JEEP_GRAND_CHEROKEE_2019):
      ci, stream, cc = self.ready(candidate)
      decoder = CANParser(DBC[candidate][Bus.pt], [('DAS_3', 0)], 0)
      for tick in range(20, 55):
        now = 100_000_000_000 + tick * 10_000_000
        previous = ci.CC.last_das_3_counter
        ci.update([(now, stream.frames(tick, active=False))])
        _, frames = apply_at_boot(ci, cc, now)
        hold = [frame for frame in frames if frame[0] == 0x1F4]
        self.assertEqual(len(hold), 1)
        decoder.update([(now, hold)])
        parsed = decoder.vl['DAS_3']
        stock = ci.CS.das_3
        self.assertEqual(parsed['COUNTER'], (int(stock['COUNTER']) + (2 if int(stock['COUNTER']) != previous else 3)) % 16)
        for name in ('ENGINE_TORQUE_REQUEST', 'DISABLE_FUEL_SHUTOFF', 'COLLISION_BRK_PREP', 'ACC_FAULTED'):
          self.assertEqual(parsed[name], stock[name])
        for name, expected in {
          'ACC_ACTIVE': 1,
          'ACC_AVAILABLE': 1,
          'ACC_GO': 0,
          'ACC_STANDSTILL': 0,
          'ACC_DECEL_REQ': 1,
          'GR_MAX_REQ': 2,
          'ACC_BRK_PREP': 0,
          'ENGINE_TORQUE_REQUEST_MAX': 0,
        }.items():
          self.assertEqual(parsed[name], expected)
        self.assertAlmostEqual(parsed['ACC_DECEL'], -2.0, delta=0.004885)
        resumes = [frame for frame in frames if frame[0] == 0x23B]
        self.assertEqual(bool(resumes), tick % 10 == 0)

  def test_driver_main_fault_and_source_withdrawal(self):
    for stimulus in ('gas', 'brake', 'speed', 'gear', 'main', 'fault', 'cancel', 'stale'):
      ci, stream, cc = self.ready()
      args = (
        {stimulus: {'gas': 1.0, 'brake': 1, 'speed': 1.0, 'gear': 1, 'main': False, 'fault': 1}.get(stimulus)} if stimulus not in ('cancel', 'stale') else {}
      )
      now = 100_200_000_000
      if stimulus == 'cancel':
        cc.cruiseControl.cancel = True
      ci.update([(now, stream.frames(20, active=False, **args))])
      if stimulus == 'stale':
        now += 100_000_001
      _, frames = apply_at_boot(ci, cc, now)
      self.assertFalse(ci.CS.brake_hold, stimulus)
      self.assertFalse(any(frame[0] == 0x1F4 for frame in frames), stimulus)

  def test_stock_braking_strength_clamped_to_original_envelope(self):
    ci, stream, cc = self.ready()
    for tick in range(20, 30):
      now = 100_000_000_000 + tick * 10_000_000
      ci.update([(now, stream.frames(tick, decel=-4.0))])
      apply_at_boot(ci, cc, now)
    self.assertEqual(ci.CC.jeep_decel, -3.0)

  def test_real_parser_crc_fault_withdraws_hold(self):
    ci, stream, cc = self.ready()
    for tick in range(20, 32):
      now = 100_000_000_000 + tick * 10_000_000
      frames = stream.frames(tick, active=False)
      for index, (address, data, bus) in enumerate(frames):
        if address == 0x1F4:
          invalid = bytearray(data)
          invalid[-1] ^= 1
          frames[index] = (address, bytes(invalid), bus)
      ci.update([(now, frames)])
      _, emitted = apply_at_boot(ci, cc, now)
    self.assertGreater(now - ci.CS.das_3_timestamp, 100_000_000)
    self.assertFalse(ci.CS.brake_hold)
    self.assertFalse(any(frame[0] == 0x1F4 for frame in emitted))

  def test_commanded_cancel_requires_fresh_stock_acc_engagement(self):
    ci, stream, cc = self.ready()
    cc.cruiseControl.cancel = True
    ci.update([(100_200_000_000, stream.frames(20))])
    apply_at_boot(ci, cc, 100_200_000_000)
    cc.cruiseControl.cancel = False
    for tick in range(21, 25):
      now = 100_000_000_000 + tick * 10_000_000
      ci.update([(now, stream.frames(tick))])
      apply_at_boot(ci, cc, now)
      self.assertFalse(ci.CS.brake_hold)
    ci.update([(100_250_000_000, stream.frames(25, active=False))])
    apply_at_boot(ci, cc, 100_250_000_000)
    ci.update([(100_260_000_000, stream.frames(26))])
    apply_at_boot(ci, cc, 100_260_000_000)
    self.assertTrue(ci.CS.brake_hold)

  def test_actual_factory_default_off_and_siblings_emit_no_hold(self):
    for candidate in CAR:
      for requested in (False, True):
        if requested and candidate in (CAR.JEEP_GRAND_CHEROKEE, CAR.JEEP_GRAND_CHEROKEE_2019):
          continue
        ci = CarInterface(params(candidate, enabled=requested))
        stream = PhysicalCan(ci)
        cc = structs.CarControl()
        for tick in range(20):
          now = 100_000_000_000 + tick * 10_000_000
          ci.update([(now, stream.frames(tick, active=False))])
          _, frames = apply_at_boot(ci, cc, now)
          self.assertFalse(ci.CS.brake_hold)
          self.assertFalse(any(frame[0] == 0x1F4 for frame in frames))

  def test_actual_parser_reverse_then_drive_does_not_restore_disabled_stock_long(self):
    ci, stream, cc = self.ready()
    ci.update([(100_200_000_000, stream.frames(20, active=False, gear=2))])
    _, frames = apply_at_boot(ci, cc, 100_200_000_000)
    self.assertEqual(ci.CS.out.gearShifter, structs.CarState.GearShifter.reverse)
    self.assertFalse(ci.CS.brake_hold)
    self.assertFalse(any(frame[0] == 0x1F4 for frame in frames))
    ci.update([(100_210_000_000, stream.frames(21, active=False, gear=4))])
    _, frames = apply_at_boot(ci, cc, 100_210_000_000)
    self.assertFalse(ci.CS.brake_hold)
    self.assertFalse(any(frame[0] == 0x1F4 for frame in frames))

  def test_original_can_boot_age_is_independent_of_controller_monotonic_time(self):
    ci, stream, cc = self.ready()
    boot_ns = 100_200_000_000
    ci.update([(boot_ns, stream.frames(20, active=False))])
    with patch('opendbc.car.chrysler.carcontroller.time.clock_gettime_ns', return_value=boot_ns):
      _, frames = ci.apply(cc.as_reader(), boot_ns - 9_000_000_000)
    self.assertTrue(ci.CS.brake_hold)
    self.assertTrue(any(frame[0] == 0x1F4 for frame in frames))

  def test_original_boot_source_future_and_stale_are_not_restamped(self):
    for offset in (-1, 100_000_001):
      ci, stream, cc = self.ready()
      source = 100_200_000_000
      ci.update([(source, stream.frames(20, active=False))])
      with patch('opendbc.car.chrysler.carcontroller.time.clock_gettime_ns', return_value=source + offset):
        _, frames = ci.apply(cc.as_reader(), source)
      self.assertFalse(ci.CS.brake_hold)
      self.assertFalse(any(frame[0] == 0x1F4 for frame in frames))

  def test_replay_uses_original_can_time_in_the_explicit_replay_domain(self):
    with patch.dict(os.environ, {'REPLAY': '1'}):
      ci, stream, cc = self.ready()
      source = 100_200_000_000
      ci.update([(source, stream.frames(20, active=False))])
      with patch('opendbc.car.chrysler.carcontroller.time.clock_gettime_ns', side_effect=AssertionError('no host clock in original-time replay')):
        _, frames = ci.apply(cc.as_reader(), source)
      self.assertTrue(any(frame[0] == 0x1F4 for frame in frames))
