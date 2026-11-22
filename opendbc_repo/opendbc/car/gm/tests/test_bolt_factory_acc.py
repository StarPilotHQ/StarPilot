import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.tests.test_bolt_cc import feed, native, setup
from opendbc.car.gm.tests.test_bolt_euv_control import original_demand, original_frames
from opendbc.car.gm.values import CAR, DBC, GMFlags, is_bolt_euv_longitudinal, uses_camera_stock_controls
from opendbc.safety.tests.libsafety import libsafety_py


def factory_params(alpha=True, release=False, present=False, pedal=False):
  fingerprint = gen_empty_fingerprint()
  fingerprint[2][0x180] = 4
  fingerprint[2][0x320] = 8
  if present:
    fingerprint[0][0x201] = 6
  return CarInterface.get_params(CAR.CHEVROLET_BOLT_ACC_2022_2023, fingerprint, [], alpha, release, False)


class TestBoltFactoryAcc(unittest.TestCase):
  def test_startup_matrix_retains_identity_tune_and_excludes_pedal(self):
    for alpha in (False, True):
      for release in (False, True):
        for present in (False,):
          for pedal in (False, True):
            cp = factory_params(alpha, release, present, pedal)
            active = alpha and not release
            self.assertEqual(cp.carFingerprint, CAR.CHEVROLET_BOLT_ACC_2022_2023)
            self.assertFalse(cp.dashcamOnly)
            self.assertEqual(cp.openpilotLongitudinalControl, active)
            self.assertEqual(cp.pcmCruise, not active)
            self.assertEqual(cp.alphaLongitudinalAvailable, not release)
            self.assertEqual(cp.safetyConfigs[0].safetyParam, 7 if active else 5)
            self.assertEqual(is_bolt_euv_longitudinal(cp), active)
            self.assertEqual(uses_camera_stock_controls(cp), not active)
            self.assertFalse(cp.flags & GMFlags.PEDAL_LONG.value)
            self.assertEqual(list(cp.longitudinalTuning.kiBP), [5., 35., 60.])
            self.assertEqual(list(cp.longitudinalTuning.kiV), [.5, .5, .5])
            self.assertEqual(cp.stopAccel, -.25)
            self.assertEqual(cp.maxLateralAccel, 2.)
            self.assertAlmostEqual(cp.lateralTuning.torque.latAccelFactor, 2.)
            self.assertAlmostEqual(cp.lateralTuning.torque.friction, .13)

  def test_actual_parser_controller_physical_bytes_and_native_ownership(self):
    safety = libsafety_py.libsafety
    release = safety.set_safety_hooks(int(structs.CarParams.SafetyModel.allOutput), 0) != 0
    for alpha in (False, True):
      cp = factory_params(alpha, release, present=False, pedal=False)
      ci = CarInterface(cp)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      setup(cp)
      cases = [(speed, accel, pitch, 'pid') for speed in (5., 20., 35.)
               for accel in (-4., -2., -.5, 0., 1., 2.) for pitch in (-.04, 0., .04)]
      cases += [(.213, -.665, 0., 'stopping'), (0., -.665, 0., 'stopping')]
      for tick, (speed, accel, pitch, state) in enumerate(cases):
        now = 1_000_000_000 + tick * 40_000_000
        out, sources = feed(ci, packer, now, counter=tick % 4, speed=speed)
        sources.append(packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'ACCCruiseState': 3}))
        out = ci.update([(now + 1_000, sources)])
        self.assertTrue(out.canValid)
        for source in sources:
          native('rx', source, now // 1000)
        safety.safety_tick()
        self.assertTrue(safety.safety_config_valid())
        if cp.openpilotLongitudinalControl:
          native('rx', packer.make_can_msg('ASCMSteeringButton', 0, {'ACCButtons': 2}), now // 1000)
          self.assertTrue(safety.get_controls_allowed())
        control = structs.CarControl(enabled=True, longActive=cp.openpilotLongitudinalControl,
                                     orientationNED=[0., pitch, 0.])
        control.actuators.accel = accel
        control.actuators.longControlState = state
        ci.CC.frame = tick * 4
        _, messages = ci.apply(control.as_reader(), now)
        long_messages = [m for m in messages if m[0] in (0x2CB, 0x315, 0x2CD)]
        self.assertFalse(any(m[0] in (0x200, 0x1F5, 0xBD) for m in messages))
        if cp.openpilotLongitudinalControl:
          raw, brake = original_demand(cp, True, out.vEgo, accel, state, False, pitch, standstill=out.standstill)
          full_stop = out.standstill and state == 'stopping'
          self.assertEqual(long_messages, original_frames(raw, brake, tick % 4, True, full_stop))
          for message in long_messages:
            self.assertTrue(native('tx', message, now // 1000), hex(message[0]))
        else:
          self.assertEqual(long_messages, [])
