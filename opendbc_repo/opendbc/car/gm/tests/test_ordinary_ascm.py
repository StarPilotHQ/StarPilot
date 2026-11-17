import unittest

from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car.gm.values import CAR, ORDINARY_ASCM_CAR, CarControllerParams, is_ordinary_ascm_profile
from opendbc.car.gm.longitudinal import GMAscmLongitudinalPolicy, ascm_policy_for
from opendbc.car.gm.ascm import demands


class TestOrdinaryAscm(unittest.TestCase):
  def test_exact_configuration_and_source_choices(self):
    self.assertEqual(len(ORDINARY_ASCM_CAR), 7)
    for candidate in ORDINARY_ASCM_CAR:
      for release in (False, True):
        for alpha in (False, True):
          for c9 in (False, True):
            for radar in (False, True):
              cp = params(candidate, sascm=True, accelerator=not c9, radar=radar, alpha=alpha, release=release)
              long = alpha and not release
              self.assertTrue(is_ordinary_ascm_profile(cp, longitudinal=long))
              self.assertFalse(is_ordinary_ascm_profile(cp, longitudinal=not long))
              self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x201 | (2 if long else 0) | (0x400 if c9 else 0) | (0x800 if radar else 0))
              self.assertEqual(ascm_policy_for(cp) is not None, long)
              if long:
                self.assertIsInstance(ascm_policy_for(cp), GMAscmLongitudinalPolicy)
                limits = CarControllerParams(cp)
                self.assertEqual((limits.MAX_GAS, limits.MAX_ACC_REGEN, limits.INACTIVE_REGEN), (2041, -650, -650))
                self.assertEqual(limits.NEAR_STOP_BRAKE_PHASE, .25)

  def test_original_lateral_and_speed_contracts(self):
    for candidate in ORDINARY_ASCM_CAR:
      cp = params(candidate)
      self.assertEqual(cp.lateralTuning.which(), "pid" if candidate == CAR.GMC_ACADIA_ASCM else "torque")
      self.assertAlmostEqual(cp.minSteerSpeed, (28 if candidate == CAR.BUICK_LACROSSE_ASCM_19US else 7) * .44704, places=6)
      self.assertAlmostEqual(cp.minEnableSpeed, -1 if candidate == CAR.CADILLAC_ESCALADE_ESV_2019_ASCM else 5 / 3.6, places=6)

  def test_exact_identity_and_word_isolation(self):
    for candidate in (CAR.CHEVROLET_VOLT_ASCM, CAR.GMC_ACADIA, CAR.CHEVROLET_BOLT_EUV):
      self.assertFalse(is_ordinary_ascm_profile(params(candidate, sascm=True, alpha=True), longitudinal=True))
    cp = params(CAR.CHEVROLET_MALIBU_ASCM, sascm=True, alpha=True)
    cp.safetyConfigs[0].safetyParam |= 4
    self.assertFalse(is_ordinary_ascm_profile(cp, longitudinal=True))

  def test_physical_demand_endpoints_and_braking_inactive(self):
    cp = params(CAR.CHEVROLET_MALIBU_ASCM, sascm=True, alpha=True)
    self.assertEqual(demands(2., 100., None, cp), (2041, 0))
    self.assertEqual(demands(-4., 0., None, cp), (-650, 400))
    self.assertEqual(demands(0., 0., None, cp), (0, 0))
    self.assertEqual(demands(1., 12., [0., .08, 0.], cp), demands(1., 12., None, cp))

  def test_default_torque_gain_and_explicit_standard_choice(self):
    from opendbc.car.gm.interface import CarInterface
    from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque, KP_INTERP, INTERP_SPEEDS
    from openpilot.starpilot.lateral.controller_selection import default_selection, ControllerMode
    for candidate in ORDINARY_ASCM_CAR:
      cp = params(candidate).as_reader()
      if candidate == CAR.GMC_ACADIA_ASCM:
        self.assertEqual(default_selection(cp).mode, ControllerMode.STANDARD)
        continue
      ci = CarInterface(cp)
      owner = LatControlTorque(cp, ci, .01)
      self.assertEqual(owner.controller_mode, ControllerMode.STARPILOT)
      self.assertEqual(owner.controller_policy, 'ordinary_ascm')
      self.assertEqual(owner.pid._k_p, [[0.0], [.6]])
      self.assertEqual(owner.pid._k_i, ([0], [.35]))
      standard = LatControlTorque(cp, ci, .01, controller_mode=ControllerMode.STANDARD)
      self.assertEqual(standard.pid._k_p, [INTERP_SPEEDS, KP_INTERP])
      self.assertIsNone(standard.starpilot_extension)

  def test_suburban_geometry_and_existing_cc_are_separate(self):
    from opendbc.car.gm.values import CAR
    cp = params(CAR.CHEVROLET_SUBURBAN_ASCM)
    self.assertEqual(CAR.CHEVROLET_SUBURBAN_ASCM.config.specs.tireStiffnessFactor, 1.0)
    self.assertEqual(CAR.CHEVROLET_SUBURBAN_CC.config.specs.tireStiffnessFactor, 1.0)
    from opendbc.car import scale_tire_stiffness
    for identity, factor in ((CAR.CHEVROLET_SUBURBAN_ASCM, 1.0), (CAR.CHEVROLET_SUBURBAN_CC, 1.0)):
      actual = params(identity)
      front, rear = scale_tire_stiffness(actual.mass, actual.wheelbase, actual.centerToFront, factor)
      self.assertAlmostEqual(actual.tireStiffnessFront, front, delta=abs(front) * 1e-6)
      self.assertAlmostEqual(actual.tireStiffnessRear, rear, delta=abs(rear) * 1e-6)
    self.assertAlmostEqual(cp.lateralTuning.torque.latAccelFactor, 1.2, places=6)
    self.assertAlmostEqual(cp.lateralTuning.torque.friction, .26, places=6)

  def test_shared_features_follow_exact_finalized_modes(self):
    from opendbc.car.gm.feature_capabilities import longitudinal_supported, display_supported
    from opendbc.car.gm.lateral import lane_centering_supported
    from openpilot.starpilot.lateral.lane_runtime import runtime_supported
    for candidate in ORDINARY_ASCM_CAR:
      for release in (False, True):
        for alpha in (False, True):
          cp = params(candidate, sascm=True, alpha=alpha, release=release)
          self.assertEqual(longitudinal_supported(cp), alpha and not release)
          self.assertTrue(display_supported(cp))
          self.assertTrue(lane_centering_supported(cp))
          self.assertTrue(runtime_supported(cp))
          cp.safetyConfigs[0].safetyParam |= 4
          self.assertFalse(longitudinal_supported(cp))
          self.assertFalse(display_supported(cp))
          self.assertFalse(lane_centering_supported(cp))

  def test_actual_stopping_release_requires_qualified_evidence(self):
    from opendbc.car.structs import CarState, CarControl
    from opendbc.car.gm.longitudinal import AscmStopEvidence
    from openpilot.selfdrive.controls.lib.longcontrol import LongControl
    from openpilot.starpilot.longitudinal.extension import LongitudinalContext
    cp = params(CAR.CHEVROLET_MALIBU_ASCM, sascm=True, alpha=True).as_reader()
    cs = CarState.new_message(vEgo=0., canValid=True, canTimeout=False)
    cs.cruiseState.standstill = True
    controller = LongControl(cp)
    state = CarControl.Actuators.LongControlState
    controller.update(True, cs.as_reader(), -.5, True, (-4., 2.))
    self.assertEqual(controller.long_control_state, state.stopping)
    for _ in range(40):
      controller.update(True, cs.as_reader(), .2, False, (-4., 2.))
      self.assertEqual(controller.long_control_state, state.stopping)
    for tick in range(35):
      context = LongitudinalContext(vehicle_stop_evidence=AscmStopEvidence(1, 1_000_000_000 + tick * 10_000_000, False))
      controller.update(True, cs.as_reader(), .2, False, (-4., 2.), context=context)
      self.assertEqual(controller.long_control_state, state.pid if tick == 34 else state.stopping)

  def test_actual_inputs_context_rejects_stale_or_disagreeing_sources(self):
    from unittest.mock import patch
    from openpilot.starpilot.longitudinal.inputs import LongitudinalInputs
    from openpilot.starpilot.longitudinal.tests.test_gm_volt_long_policy import controls_fixture
    from opendbc.car.gm.longitudinal import AscmStopEvidence
    controls, now, offset = controls_fixture()
    cp = params(CAR.CHEVROLET_MALIBU_ASCM, sascm=True, alpha=True).as_reader()
    owner = LongitudinalInputs(cp, controls.params, lambda: controls.sm)
    owner.gm_ascm_boot_offset_ns = offset
    self.assertEqual(owner.optional_services, ['radarState', 'deviceState'])
    controls.sm['radarState'].leadOne.present = controls.sm['longitudinalPlan'].hasLead = True
    with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + offset)), \
         patch.dict('os.environ', {'REPLAY': '0'}):
      self.assertIsInstance(owner.context(True).vehicle_stop_evidence, AscmStopEvidence)
      self.assertFalse(owner.faster_takeoffs)
      controls.sm['longitudinalPlan'].hasLead = False
      self.assertIsNone(owner.context(True).vehicle_stop_evidence)
      controls.sm['longitudinalPlan'].hasLead = True
      controls.sm.logMonoTime['radarState'] = now - 2_100_000_000
      self.assertIsNone(owner.context(True).vehicle_stop_evidence)
