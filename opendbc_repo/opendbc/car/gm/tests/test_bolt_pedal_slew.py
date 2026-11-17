import unittest
from types import SimpleNamespace

from opendbc.car import structs
from opendbc.car.gm.carcontroller import CarController, bolt_pedal_fraction, bolt_pedal_slew
from opendbc.car.gm.tests.test_bolt_pedal import params
from opendbc.car.gm.values import CAR, DBC, PEDAL_BOLT_CAR


def linear(x, xs, ys):
  if x <= xs[0]:
    return ys[0]
  for i in range(1, len(xs)):
    if x <= xs[i]:
      return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / (xs[i] - xs[i - 1])
  return ys[-1]


def positive_target(accel, speed):
  accel = 0.0 if abs(accel) < .04 else accel
  offset = linear(speed, [0, 1, 3, 6, 15, 30], [.085, .11, .17, .23, .235, .23])
  gain = linear(speed, [0, 3, 8, 20], [.47, .52, .57, .61])
  scale = linear(accel, [0, .35, .8, 1.5, 2.5], [.58, .68, .82, .93, 1])
  ceiling = linear(speed, [0, 1, 2.5, 4.5, 6, 8, 12], [.20, .235, .29, .365, .52, .78, 1])
  return max(0, min(ceiling, offset + accel * scale * gain))


def pedal_step(target, previous, accel, speed):
  urgency = min(abs(accel) / 2, 1)
  up = linear(speed, [0, 3, 8, 20], [.007, .012, .022, .036]) + .011 * urgency
  if accel > 0 and speed > 6:
    up *= linear(abs(accel), [0, .12, .25, .45, .8], [.55, .58, .68, .82, 1])
  if accel > 1.2:
    up += linear(speed, [0, 4, 12, 25], [.006, .005, .003, .002])
  down = linear(speed, [0, 3, 8, 20], [.008, .014, .026, .045]) + .015 * urgency
  return max(previous - down, min(previous + up, target))


def pedal_wire(fraction, counter):
  enabled = fraction > .001
  command = fraction * 255.
  first = int((command + 75.909) / .125677 + .5) if enabled else 0
  second = int((command + 76.601) / .251976 + .5) if enabled else 0
  payload = bytearray(first.to_bytes(2, "big") + second.to_bytes(2, "big") + bytes([(int(enabled) << 7) | (counter & 15), 0]))
  crc = 255
  for byte in payload[:5][::-1]:
    crc ^= byte
    for _ in range(8):
      crc = ((crc << 1) ^ (213 if crc & 128 else 0)) & 255
  payload[5] = crc
  return bytes(payload)


class TestBoltPedalSlew(unittest.TestCase):
  def test_paddle_switching_matches_original_scalar_outputs(self):
    import json
    from pathlib import Path
    reference = json.loads(Path(__file__).with_name('bolt_paddle_original_oracle.json').read_text())
    for candidate in PEDAL_BOLT_CAR:
      for history in reference['histories']:
        cp = params(candidate, setting=True, pedal=True)
        cc = CarController(DBC[candidate], cp)
        speed = history['speed']
        for expected in history['frames']:
          with self.subTest(candidate=candidate, speed=speed, tick=expected['tick']):
            cc.maneuver_paddle_mode = expected['selected']
            cc.bolt_regen_hold = expected['planner_hold']
            pressed = cc.update_bolt_paddle(expected['accel'], expected['measured'], speed, expected['active'])
            if expected['active']:
              target = bolt_pedal_fraction(expected['accel'], speed, pressed)
              cc.pedal_steady = (bolt_pedal_slew(target, cc.pedal_steady, expected['accel'], speed)
                                 if cc.pedal_active_last and not (cc.bolt_paddle_switched and speed > 1.) else target)
              cc.pedal_active_last = True
            else:
              cc.pedal_steady = 0.
              cc.pedal_active_last = False
            self.assertEqual(pressed, expected['pressed'])
            self.assertAlmostEqual(cc.pedal_steady, expected['pedal'], places=12)
            self.assertEqual(cc.regen_press_count, expected['press_count'])
            self.assertEqual(cc.regen_release_count, expected['release_count'])
            self.assertEqual(cc.regen_min_on_frames, expected['min_on'])
            self.assertEqual(cc.regen_min_off_frames, expected['min_off'])

  def test_paddle_modes_preserve_automatic_counters_and_switch_law(self):
    cp = params(next(iter(PEDAL_BOLT_CAR)), setting=True, pedal=True)
    cc = CarController(DBC[cp.carFingerprint], cp)
    cc.maneuver_paddle_mode = "force"
    cc.regen_min_off_frames = 20
    self.assertTrue(cc.update_bolt_paddle(-.03, 0., 10., True))
    self.assertFalse(cc.bolt_paddle_switched)
    self.assertEqual(cc.regen_min_off_frames, 19)
    cc.maneuver_paddle_mode = "off"
    self.assertFalse(cc.update_bolt_paddle(-2., -2., 10., True))
    self.assertEqual((cc.regen_press_count, cc.regen_release_count, cc.regen_min_on_frames), (0, 0, 0))
    self.assertEqual(cc.regen_min_off_frames, 18)
    cc.maneuver_paddle_mode = "force"
    self.assertFalse(cc.update_bolt_paddle(-.02, -2., 10., True))
    self.assertFalse(cc.update_bolt_paddle(-2., -2., 10., False))
    self.assertEqual(cc.regen_min_off_frames, 0)

  def test_rate_boundaries_and_falling_invariance(self):
    for speed in (0, 3, 6, 6.0001, 8, 12, 20, 25):
      for accel in (-3, -.35, 0, .12, .25, .35, .45, .8, 1.2, 1.2001, 1.5, 2):
        for target in (0., .3, 1.):
          with self.subTest(speed=speed, accel=accel, target=target):
            self.assertAlmostEqual(bolt_pedal_slew(target, .3, accel, speed), pedal_step(target, .3, accel, speed), places=12)

  def test_actual_all_pedal_controllers_and_admission_resets(self):
    for candidate in PEDAL_BOLT_CAR:
      for alpha in (False, True):
        for speed in (6., 6.0001, 8., 12., 25.):
          with self.subTest(candidate=candidate, alpha=alpha, speed=speed):
            cp = params(candidate, True, True, alpha)
            gen2_cc_drive = candidate == CAR.CHEVROLET_BOLT_CC_2022_2023 and cp.safetyConfigs[0].safetyParam == 0x19D
            controller = CarController(DBC[candidate], cp)
            cc = structs.CarControl()
            cc.enabled = True
            cc.longActive = True
            state = structs.CarState()
            state.vEgo = speed
            speed = state.as_reader().vEgo
            state.gearShifter = structs.CarState.GearShifter.low
            state.cruiseState.available = True
            cs = SimpleNamespace(out=state.as_reader(), pedal_sensor_healthy=True,
                                 pedal_sensor_ts_nanos=1_000_000_000, stock_acc_status_ts_nanos=1_000_000_000,
                                 bolt_pedal_gear_ts_nanos=1_000_000_000, bolt_pedal_main_ts_nanos=1_000_000_000,
                                 cam_lka_steering_cmd_counter=0, loopback_lka_steering_cmd_updated=False,
                                 loopback_lka_steering_cmd_ts_nanos=1_000_000_000, pt_lka_steering_cmd_counter=0,
                                 buttons_counter=0,
                                 pscm_status={k: 0 for k in ("HandsOffSWDetectionMode", "HandsOffSWlDetectionStatus",
                                                           "LKATorqueDeliveredStatus", "LKADriverAppldTrq", "LKATorqueDelivered",
                                                           "LKATotalTorqueDelivered", "RollingCounter", "PSCMStatusChecksum")})
            expected = None
            tick = 0

            def update(controller=controller, cs=cs, state=state, cc=cc):
              nonlocal tick
              tick += 1
              now = 1_000_000_000 + tick * 40_000_000
              controller.frame = tick * 4
              controller.last_steer_frame = controller.frame
              cs.pedal_sensor_ts_nanos = now - getattr(cs, "sensor_age", 0)
              cs.stock_acc_status_ts_nanos = now
              cs.bolt_pedal_gear_ts_nanos = now
              cs.bolt_pedal_main_ts_nanos = now
              cs.out = state.as_reader()
              return controller.update(cc.as_reader(), cs, now)[1]

            for requested in (0., .35, .35, 1.2, 1.2001, 1.5, 0.):
              cc.actuators.accel = requested
              accel = cc.as_reader().actuators.accel
              target = positive_target(accel, speed)
              expected = target if expected is None else pedal_step(target, expected, accel, speed)
              messages = update()
              self.assertAlmostEqual(controller.pedal_steady, expected, places=10)
              self.assertEqual(sum(m[0] == 0x200 for m in messages), 1)
              self.assertEqual(next(m[1] for m in messages if m[0] == 0x200), pedal_wire(expected, tick))
              self.assertNotIn(0x2CB, [m[0] for m in messages])

            for override in ('inactive', 'sensor', 'stale', 'future', 'drive', 'neutral', 'gas', 'brake', 'regen', 'stock_acc'):
              if override == 'stock_acc' and candidate.name != 'CHEVROLET_BOLT_ACC_2022_2023_PEDAL':
                continue
              cc.longActive = override != 'inactive'
              cs.pedal_sensor_healthy = override != 'sensor'
              state.gearShifter = (structs.CarState.GearShifter.drive if override == 'drive' else
                                  structs.CarState.GearShifter.neutral if override == 'neutral' else structs.CarState.GearShifter.low)
              cs.sensor_age = 100_000_001 if override == 'stale' else -1 if override == 'future' else 0
              state.regenBraking = override == 'regen'
              state.gasPressed = override == 'gas'
              state.brakePressed = override == 'brake'
              state.cruiseState.enabled = override == 'stock_acc'
              previous = controller.pedal_steady
              messages = update()
              admitted_drive = override == 'drive' and (gen2_cc_drive or candidate == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL)
              if admitted_drive:
                expected_drive = pedal_step(positive_target(cc.as_reader().actuators.accel, speed), previous,
                                           cc.as_reader().actuators.accel, speed)
                self.assertAlmostEqual(controller.pedal_steady, expected_drive, places=10)
                self.assertEqual(next(m[1] for m in messages if m[0] == 0x200), pedal_wire(expected_drive, tick))
                if gen2_cc_drive:
                  self.assertEqual([(m[1], m[2]) for m in messages if m[0] == 0x1F5], [(bytes.fromhex('0c0c000400000100'), 0)])
                  self.assertEqual([(m[1], m[2]) for m in messages if m[0] == 0xBD], [(bytes(7), 0)])
                else:
                  self.assertFalse(any(m[0] in (0x1F5, 0xBD) for m in messages))
              else:
                self.assertEqual(controller.pedal_steady, 0., override)
                self.assertEqual(next(m[1][:4] for m in messages if m[0] == 0x200), b'\x00' * 4)
              cc.longActive = True
              cs.pedal_sensor_healthy = True
              state.gearShifter = structs.CarState.GearShifter.low
              cs.sensor_age = 0
              state.regenBraking = False
              state.gasPressed = False
              state.brakePressed = False
              state.cruiseState.enabled = False
              cc.actuators.accel = .35
              target = positive_target(cc.as_reader().actuators.accel, speed)
              expected_recovery = (pedal_step(target, controller.pedal_steady, cc.as_reader().actuators.accel, speed)
                                   if admitted_drive else target)
              messages = update()
              self.assertAlmostEqual(controller.pedal_steady, expected_recovery, places=10)
              self.assertEqual(next(m[1] for m in messages if m[0] == 0x200), pedal_wire(expected_recovery, tick))


if __name__ == '__main__':
  unittest.main()
