import unittest

from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car.gm.values import CAR, ORDINARY_SDGM_CAR, SDGM_CANCEL_PT_CAR, CarControllerParams, is_ordinary_sdgm_profile
from opendbc.car.gm.longitudinal import sdgm_policy_for
from opendbc.car.gm.ordinary import demands


class TestOrdinarySdgm(unittest.TestCase):
  def test_exact_final_configuration(self):
    self.assertEqual(len(ORDINARY_SDGM_CAR), 8)
    for candidate in ORDINARY_SDGM_CAR:
      for release in (False, True):
        for alpha in (False, True):
          for sascm in (False, True):
            for c9 in (False, True):
              cp = params(candidate, sascm=sascm, accelerator=not c9, alpha=alpha, release=release)
              long = alpha and sascm and not release and candidate != CAR.BUICK_ENVISION_SDGM
              expected = 0x1001 | (2 if long else 0) | (0x400 if c9 else 0)
              if not long and candidate in SDGM_CANCEL_PT_CAR:
                expected |= 0x2000
              if candidate == CAR.BUICK_ENVISION_SDGM:
                expected = 0xE900
              self.assertEqual(cp.safetyConfigs[0].safetyParam, expected)
              self.assertTrue(is_ordinary_sdgm_profile(cp, longitudinal=long))
              self.assertEqual(sdgm_policy_for(cp) is not None, long)
              if long:
                limits = CarControllerParams(cp)
                self.assertEqual((limits.MAX_GAS, limits.MAX_ACC_REGEN, limits.INACTIVE_REGEN), (2698, -540, -500))

  def test_blazer_and_xt4_final_overrides(self):
    cp = params(CAR.CHEVROLET_BLAZER, sascm=True, alpha=True)
    self.assertAlmostEqual(cp.longitudinalActuatorDelay, .7, places=6)
    self.assertAlmostEqual(cp.stopAccel, -.30, places=6)
    for actual, expected in zip(sdgm_policy_for(cp).kp[1], (.09, .075, .055, .040), strict=True):
      self.assertAlmostEqual(actual, expected, places=6)
    xt4 = params(CAR.CADILLAC_XT4)
    self.assertAlmostEqual(xt4.minSteerSpeed, 30 * .44704, places=6)
    self.assertEqual(xt4.safetyConfigs[0].safetyParam, 0x3001)

  def test_camera_endpoints_and_braking_inactive(self):
    cp = params(CAR.CHEVROLET_TRAVERSE, sascm=True, alpha=True)
    self.assertEqual(demands(-4, 0, (), cp, min_gas=-540, max_gas=2698, inactive_gas=-500, brake_threshold=0), (-500, 400))
    self.assertEqual(demands(2, 100, (), cp, min_gas=-540, max_gas=2698, inactive_gas=-500, brake_threshold=0), (2698, 0))

  def test_actual_packed_required_sources_and_selected_brake(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus
    from opendbc.car.gm.carstate import CarState
    from opendbc.car.gm.values import DBC
    for candidate in ORDINARY_SDGM_CAR:
      for c9 in ((False,) if candidate == CAR.BUICK_ENVISION_SDGM else (False, True)):
        cp = params(candidate, sascm=True, accelerator=not c9, alpha=True)
        state = CarState(cp)
        parsers = state.get_can_parsers(cp)
        packer = CANPacker(DBC[candidate][Bus.pt])
        required = ('PSCMStatus', 'ESPStatus', 'EBCMWheelSpdFront', 'EBCMWheelSpdRear',
                    'EBCMFrictionBrakeStatus', 'PSCMSteeringAngle', 'ECMPRDNL2', 'AcceleratorPedal2',
                    'ECMEngineStatus', 'BCMTurnSignals', 'BCMDoorBeltStatus', 'BCMGeneralPlatformStatus', 'ASCMSteeringButton')
        if not c9:
          required += ('ECMAcceleratorPos',)
        for index in range(30):
          now = 1_000_000_000 + index * 10_000_000
          frames = [packer.make_can_msg(name, 0, {'BrakePressed': 1} if name == 'ECMEngineStatus' and c9 else
                                        {'BrakePedalPos': 10} if name == 'ECMAcceleratorPos' else {}) for name in required]
          parsers[Bus.pt].update([(now, frames)])
          parsers[Bus.cam].update([(now, [packer.make_can_msg('ASCMLKASteeringCmd', 2, {}),
                                                packer.make_can_msg('ASCMActiveCruiseControlStatus', 2, {'FCWAlert': 2})])])
          result = state.update(parsers)
        self.assertTrue(parsers[Bus.pt].can_valid)
        self.assertTrue(parsers[Bus.cam].can_valid)
        self.assertTrue(result.brakePressed)
        self.assertEqual(state.stock_fcw_alert, 2)

  def test_shared_feature_lane_and_aol_finalized_owners(self):
    from opendbc.car.gm.feature_capabilities import longitudinal_supported
    from opendbc.car.gm.lateral import lane_centering_supported
    from opendbc.car.gm.aol import qualified_gm
    from openpilot.starpilot.lateral.controller_selection import policy_for
    for candidate in ORDINARY_SDGM_CAR:
      for release in (False, True):
        for alpha in (False, True):
          cp = params(candidate, sascm=True, alpha=alpha, release=release)
          self.assertEqual(longitudinal_supported(cp), alpha and not release and candidate != CAR.BUICK_ENVISION_SDGM)
          self.assertTrue(lane_centering_supported(cp))
          self.assertTrue(qualified_gm(cp))
          self.assertEqual(policy_for(cp), 'ordinary_sdgm')
          cp.safetyConfigs[0].safetyParam |= 4
          self.assertFalse(longitudinal_supported(cp))
          self.assertFalse(qualified_gm(cp))

  def test_scoped_geometry_preserves_cc_neighbor(self):
    from opendbc.car.gm.values import CAR
    for candidate in (CAR.CADILLAC_XT5, CAR.CHEVROLET_BLAZER, CAR.BUICK_BABYENCLAVE):
      self.assertEqual(candidate.config.specs.tireStiffnessFactor, 1.0)
    self.assertEqual(CAR.CADILLAC_XT5_CC.config.specs.tireStiffnessFactor, 1.0)
