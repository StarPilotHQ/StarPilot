"""Ordinary factory ACC Bolt: independent original physical-demand/wire oracle."""
import math
from types import SimpleNamespace
import unittest

import numpy as np
from opendbc.car import structs
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car.gm.values import CAR, DBC, is_bolt_euv_longitudinal


def original_demand(cp, active, speed, accel, state, resume, pitch, *, standstill=False):
  # Original 6cae0ce camera path, with the hold demand as a floor while rolling.
  if not active:
    return 5650, 0
  hold = speed < 0.25 and state == 'stopping' and not resume
  if hold and standstill:
    return 5650, 25
  grade = math.sin(pitch) * 9.81 if speed > 0.25 else 0.0
  if grade > 0 and accel > 0:
    grade = 0.0
  grade = min(grade, 0.20)
  radius = 0.075 * cp.wheelbase + 0.1453
  drag = 0.5 * 0.30 * (1.05 * cp.wheelbase + 0.0679) * 1.225 * speed ** 2
  torque = radius * (cp.mass * np.clip(accel + grade, -4.0, 2.0) + drag) + 6150
  raw = int(round(np.clip(torque, 5610, 8848)))
  switch = int(round(np.interp(speed, [0.5, 10.0], [6150, 5610])))
  brake_accel = min((torque - switch) / (radius * cp.mass), 0.0)
  brake = int(round(np.interp(brake_accel, [-4.0, 0.0], [400, 0])))
  if hold:
    brake = max(25, brake)
  return (5650 if brake > 0 or state == 'stopping' else raw), brake


def original_frames(raw, brake, idx, enabled, full):
  gas = bytearray(8)
  gas[0] = (idx << 6) | enabled
  gas[1] = 0x42 | (0x20 if full else 0) | ((raw >> 13) & 1)
  gas[2], gas[3] = ((raw << 3) >> 8) & 255, (raw << 3) & 255
  gas[4] = 0 if enabled else 1
  gas[5], gas[6], gas[7] = 255 - gas[1], 255 - gas[2], (256 - gas[3] - idx) & 255
  mode = 9 if enabled else 1
  if brake:
    mode = 13 if full else 10
  brake_raw = (-brake) & 4095
  checksum = (65536 - (mode << 12) - brake_raw - idx) & 65535
  friction = bytes([(mode << 4) | (brake_raw >> 8), brake_raw & 255,
                    checksum >> 8, checksum & 255, idx])
  return [(0x2cb, bytes(gas), 0), (0x315, friction, 0),
          (0x2cd, bytes([idx << 6, 0x2c, 3, 0xd3, 0xfd - idx]), 0)]


# Persistent transitions; all four initial counter/25 Hz scheduling alignments.
PHASES = [(False, False, 12., 1., 'off', False, False, 0.),
          (True, True, 12., 1., 'pid', False, False, 0.),
          (True, True, 12., 0., 'pid', False, False, 0.),
          (True, True, 12., -0.5, 'pid', False, False, 0.),
          (True, True, 12., -2., 'pid', False, False, 0.),
          (True, False, 12., 2., 'pid', False, False, -0.04),
          (True, True, 12., 1., 'pid', False, False, 0.),
          (True, True, 7., -1.5, 'pid', False, False, -0.04)]
PHASES += [(True, True, speed, -2., 'stopping', False, False, 0.) for speed in (0.6, 0.49, 0.3, 0.25, 0.24, 0.1)]
PHASES += [(True, True, .213, -.665, 'stopping', False, False, 0.),
           (True, True, 0., -.665, 'stopping', False, True, 0.),
           (True, True, 0., 1., 'stopping', True, True, 0.),
           (True, True, 0., 0.5, 'starting', True, True, 0.),
           (True, True, 0.3, 0.5, 'starting', True, False, 0.),
           (True, True, 2., 2., 'pid', False, False, 0.),
           (False, False, 2., -4., 'off', False, False, 0.)]
PHASES += [(True, True, speed, accel, 'pid', False, False, pitch)
           for speed in (0.1, 0.25, 0.5, 5., 10., 20., 35., 100.)
           for accel in (-4., -1., 0., 1., 2.) for pitch in (-0.04, 0., 0.04)]


class TestBoltEuvControl(unittest.TestCase):
  def test_final_tuning_and_identity(self):
    cp = params(CAR.CHEVROLET_BOLT_EUV, alpha=True)
    self.assertEqual(cp.stopAccel, -0.25)
    self.assertEqual(list(cp.longitudinalTuning.kiBP), [5., 35., 60.])
    self.assertEqual(list(cp.longitudinalTuning.kiV), [0.5, 0.5, 0.5])
    self.assertFalse(cp.pcmCruise)
    self.assertTrue(cp.openpilotLongitudinalControl)
    self.assertEqual(cp.safetyConfigs[0].safetyParam, 7)
    stock = params(CAR.CHEVROLET_BOLT_ACC_2022_2023, alpha=True)
    self.assertFalse(stock.pcmCruise)
    self.assertTrue(stock.openpilotLongitudinalControl)

  def test_strict_owner_release_and_alpha_off(self):
    cp = params(CAR.CHEVROLET_BOLT_EUV, alpha=True)
    self.assertTrue(is_bolt_euv_longitudinal(cp))
    for field, value in (('passive', True), ('dashcamOnly', True), ('notCar', True),
                         ('pcmCruise', True), ('flags', 1), ('radarUnavailable', False),
                         ('alphaLongitudinalAvailable', False), ('openpilotLongitudinalControl', False),
                         ('carFingerprint', CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL)):
      bad = cp.as_reader().as_builder()
      setattr(bad, field, value)
      self.assertFalse(is_bolt_euv_longitudinal(bad), field)
    for word in (3, 5, 7 | 8, 7 | 512, 7 | 16384, 7 | 32768):
      bad = cp.as_reader().as_builder()
      bad.safetyConfigs[0].safetyParam = word
      self.assertFalse(is_bolt_euv_longitudinal(bad), word)
    for alpha, release in ((False, False), (False, True), (True, True)):
      stock = params(CAR.CHEVROLET_BOLT_EUV, alpha=alpha, release=release)
      self.assertTrue(stock.pcmCruise)
      self.assertFalse(stock.openpilotLongitudinalControl)
      self.assertFalse(is_bolt_euv_longitudinal(stock))
      self.assertEqual(stock.stopAccel, -2.)
      self.assertEqual(list(stock.longitudinalTuning.kiV), [2., 1.5])
      self.assertEqual(stock.safetyConfigs[0].safetyParam, 5)
      self.assertEqual(stock.alphaLongitudinalAvailable, not release)

  def test_continuous_original_physical_demand_and_bytes(self):
    for alpha in (False, True):
      for alignment in range(4):
        cp = params(CAR.CHEVROLET_BOLT_EUV, alpha=alpha)
        controller = CarController(DBC[cp.carFingerprint], cp)
        controller.frame = alignment
        for phase, (enabled, active, speed, accel, state, resume, still, pitch) in enumerate(PHASES):
          for _ in range(7 + phase % 3):
            frame = controller.frame
            now = 1_000_000_000 + frame * 10_000_000
            cc = structs.CarControl(enabled=enabled, longActive=alpha and active, orientationNED=[0., pitch, 0.])
            cc.cruiseControl.resume = resume
            cc.actuators.accel = accel
            cc.actuators.longControlState = getattr(structs.CarControl.Actuators.LongControlState, state)
            car = structs.CarState(vEgo=speed, standstill=still)
            car.cruiseState.available = True
            car.cruiseState.enabled = enabled and not alpha
            car.gearShifter = structs.CarState.GearShifter.drive
            cs = SimpleNamespace(out=car.as_reader(), cam_lka_steering_cmd_counter=0,
              loopback_lka_steering_cmd_updated=False, loopback_lka_steering_cmd_ts_nanos=now,
              pt_lka_steering_cmd_counter=0, buttons_counter=0,
              pscm_status={key: 0 for key in ('HandsOffSWDetectionMode', 'HandsOffSWlDetectionStatus',
                'LKATorqueDeliveredStatus', 'LKADriverAppldTrq', 'LKATorqueDelivered',
                'LKATotalTorqueDelivered', 'RollingCounter', 'PSCMStatusChecksum')})
            _, frames = controller.update(cc.as_reader(), cs, now)
            actual = [tuple(msg) for msg in frames if msg[0] in (0x2cb, 0x315, 0x2cd)]
            self.assertFalse(any(msg[0] in (0x200, 0x1f5, 0xbd) for msg in frames))
            if not alpha or frame % 4:
              self.assertFalse(actual)
              continue
            raw, brake = original_demand(cp, active, speed, accel, state, resume, pitch, standstill=still)
            expected = original_frames(raw, brake, (frame // 4) % 4, enabled, active and still and state == 'stopping')
            self.assertEqual(actual, expected, (alpha, alignment, phase, frame))
            self.assertEqual((controller.apply_gas, controller.apply_brake), (raw - 6150, brake))
