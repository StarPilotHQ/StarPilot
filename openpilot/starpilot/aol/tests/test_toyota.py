import unittest

from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.can import CANPacker
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, DBC, ToyotaFlags
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.vehicle import policy_for
from openpilot.starpilot.car.toyota.aol import qualified, ToyotaCardIntent
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


CAMERA_TORQUE_CASES = (
  CAR.TOYOTA_ALPHARD_TSS2, CAR.TOYOTA_AVALON_TSS2, CAR.TOYOTA_CAMRY_TSS2, CAR.TOYOTA_COROLLA_TSS2,
  CAR.TOYOTA_HIGHLANDER_TSS2, CAR.TOYOTA_PRIUS_TSS2, CAR.TOYOTA_RAV4_TSS2, CAR.TOYOTA_MIRAI,
  CAR.LEXUS_ES_TSS2, CAR.LEXUS_NX_TSS2, CAR.LEXUS_LC_TSS2, CAR.LEXUS_RX_TSS2,
  CAR.LEXUS_IS_TSS2, CAR.LEXUS_RC_TSS2,
)


class TestHighlanderAol(unittest.TestCase):
  def test_corolla_controls_require_current_native_ack_even_with_stock_enable(self):
    import os
    from unittest.mock import patch
    from openpilot.common.params import Params
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.selfdrive.controls.controlsd import Controls
    from openpilot.starpilot.lateral.tests.test_lane_runtime import feed, publish_corolla_params

    with OpenpilotPrefix(), patch.dict(os.environ, {'REPLAY': '1', 'AOL_REPLAY_RUNTIME': '0'}), \
         patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      cp = publish_corolla_params(params)
      self.assertEqual(cp.carFingerprint, CAR.TOYOTA_COROLLA_TSS2)
      self.assertEqual((cp.safetyConfigs[0].safetyParam, cp.alternativeExperience), (73, 32))
      controls = Controls()
      self.assertTrue(controls.aol_replay)
      self.assertTrue(controls.CP.openpilotLongitudinalControl)
      conditions = ('missing', 'fresh', 'stale', 'stale_native', 'invalid', 'invalid_axis',
                    'missing_native', 'missing_axis', 'fresh')
      for tick, condition in enumerate(conditions):
        with self.subTest(condition=condition):
          now = 1_000_000_000 + tick * 50_000_000
          feed(controls, now, tick * 5, active=True, enabled=True)
          if condition in ('missing', 'missing_native'):
            controls.sm.seen['aolSafetyWire'] = False
          if condition in ('missing', 'missing_axis'):
            controls.sm.seen['aolAxisState'] = False
          if condition == 'stale':
            controls.sm.logMonoTime['aolAxisState'] = now - 30_000_001
          if condition == 'stale_native':
            controls.sm.logMonoTime['aolSafetyWire'] = now - 200_000_001
          if condition == 'invalid':
            controls.sm.valid['aolSafetyWire'] = False
          if condition == 'invalid_axis':
            controls.sm.valid['aolAxisState'] = False
          command, _ = controls.state_control()
          self.assertTrue(command.enabled)
          expected = condition == 'fresh'
          self.assertEqual((command.latActive, command.longActive), (expected, expected))
          if expected:
            self.assertNotEqual(command.actuators.torque, 0.)
          else:
            self.assertEqual((command.actuators.torque, command.actuators.accel), (0., 0.))

  def test_actual_factory_gas_hybrid_and_hold_composition(self):
    for hybrid in (False, True):
      for hold in (False, True):
        with self.subTest(hybrid=hybrid, hold=hold):
          firmware = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)] if hybrid else []
          cp = CarInterface.get_params(CAR.TOYOTA_HIGHLANDER_TSS2, gen_empty_fingerprint(), firmware, False, False, False)
          self.assertEqual(cp.safetyConfigs[0].safetyParam, 73)
          self.assertEqual(bool(cp.flags & ToyotaFlags.HYBRID), hybrid)
          self.assertTrue(cp.openpilotLongitudinalControl)
          preferences = VehicleStartupPreferences(toyota_auto_hold=hold)
          preferences.prepare(cp)
          self.assertTrue(qualified(cp))
          policy = policy_for(cp)
          self.assertTrue(policy.full_axis_runtime_required)
          cp.alternativeExperience |= policy.alternative_experience_addition
          preferences.finalize(cp)
          self.assertEqual(cp.alternativeExperience, 160 if hold else 32)
          self.assertTrue(qualified(cp, marked_only=True))
          cp.safetyConfigs[0].safetyParam = 585
          cp.openpilotLongitudinalControl = False
          self.assertFalse(qualified(cp))

  def test_prius_filter_exact_profile_and_startup_transport(self):
    from opendbc.car.toyota.prius_longitudinal import prepare_stock
    for car in (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT):
      fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.eps,
             fwVersion=b'8965B47070\x00\x00\x00\x00\x00\x00')]
      # This legacy EPS73 owner consumes eight-byte smartDSU traffic, not advertisements.
      cp = CarInterface.get_params(car, {0: {0x2FF: 8}, 1: {}, 2: {}}, fw, False, False, False)
      self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x8049)
      self.assertTrue(qualified(cp))
      for reduced in (False, True):
        selected = cp.as_reader().as_builder()
        if reduced:
          self.assertTrue(prepare_stock(selected))
        selected.alternativeExperience = 32
        self.assertTrue(qualified(selected, marked_only=True))
        self.assertEqual(selected.safetyConfigs[0].safetyParam, 0x8249 if reduced else 0x8049)
        for mutation in ('identity', 'flags', 'word', 'long', 'experience', 'passive', 'model'):
          bad = selected.as_reader().as_builder()
          if mutation == 'identity':
            bad.carFingerprint = CAR.TOYOTA_PRIUS_TSS2
          elif mutation == 'flags':
            bad.flags |= int(ToyotaFlags.TSS2)
          elif mutation == 'word':
            bad.safetyConfigs[0].safetyParam = 73
          elif mutation == 'long':
            bad.openpilotLongitudinalControl = not selected.openpilotLongitudinalControl
          elif mutation == 'experience':
            bad.alternativeExperience = 160
          elif mutation == 'passive':
            bad.passive = True
          else:
            bad.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.noOutput
          self.assertFalse(qualified(bad, marked_only=True), mutation)
      for disabled, late in ((False, False), (True, False), (False, True)):
        self._assert_card_full_axis_transport(car, True, prepared_cp=cp,
                                             filter_disabled=disabled, late_disable=late)

  def test_continental_radar_metadata_keeps_exact_stock_owner(self):
    for car in (CAR.TOYOTA_CHR_TSS2, CAR.TOYOTA_RAV4_TSS2_2022):
      for alpha in (False, True):
        with self.subTest(car=car, alpha=alpha):
          cp = CarInterface.get_params(car, gen_empty_fingerprint(), [], alpha, False, False)
          cp.flags |= int(ToyotaFlags.ARS510_RADAR)
          expected = car == CAR.TOYOTA_RAV4_TSS2_2022 and not alpha
          self.assertEqual(qualified(cp), expected)
          cp.alternativeExperience = 32
          self.assertEqual(qualified(cp, marked_only=True), expected)
          cp.flags |= int(ToyotaFlags.ARS510_FUSED)
          self.assertFalse(qualified(cp))

  def test_radar_torque_factory_stock_and_alpha_transport(self):
    from opendbc.car.toyota.carcontroller import CarController
    from opendbc.car.toyota.carstate import CarState
    from opendbc.car.toyota.tests.test_auto_hold import decode
    for car in (CAR.TOYOTA_CHR_TSS2, CAR.TOYOTA_RAV4_TSS2_2022):
      for hybrid in (False, True):
        for alpha in (False, True):
          with self.subTest(car=car, alpha=alpha, hybrid=hybrid):
            fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)] if hybrid else []
            cp = CarInterface.get_params(car, gen_empty_fingerprint(), fw, alpha, False, False)
            self.assertEqual(bool(cp.flags & ToyotaFlags.HYBRID), hybrid)
            self.assertEqual(cp.safetyConfigs[0].safetyParam, 73 if alpha else 585)
            self.assertEqual(cp.openpilotLongitudinalControl, alpha)
            self.assertEqual(bool(cp.flags & ToyotaFlags.DISABLE_RADAR), alpha)
            self.assertTrue(cp.pcmCruise and qualified(cp))
            cp.alternativeExperience = 32
            self.assertTrue(qualified(cp, marked_only=True))
            bad = cp.as_reader().as_builder()
            bad.flags ^= int(ToyotaFlags.RADAR_ACC)
            self.assertFalse(qualified(bad))
            bad = cp.as_reader().as_builder()
            bad.flags |= int(ToyotaFlags.AUTO_BRAKE_HOLD)
            bad.alternativeExperience = 160
            self.assertFalse(qualified(bad))
            if not alpha:
              state = CarState(cp)
              state.out = structs.CarState(canValid=True)
              command = structs.CarControl()
              command.cruiseControl.cancel = True
              frames = CarController(DBC[car], cp).update(command.as_reader(), state, 0)[1]
              cancel = next(frame for frame in frames if frame[0] == 0x343)
              decoded = decode(cp, cancel, 'ACC_CONTROL')
              self.assertEqual(decoded['ACCEL_CMD'], 0)
              self.assertEqual(decoded['CANCEL_REQ'], 1)
            self._assert_card_full_axis_transport(car, hybrid, alpha=alpha, radar=True)

  def test_camera_torque_factory_and_exact_hold_experience(self):
    for car in CAMERA_TORQUE_CASES:
      for hybrid in (False, True):
        for hold in (False, True):
          with self.subTest(car=car, hybrid=hybrid, hold=hold):
            fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)] if hybrid else []
            cp = CarInterface.get_params(car, gen_empty_fingerprint(), fw, False, False, False)
            self.assertEqual(cp.safetyConfigs[0].safetyParam, 73)
            self.assertTrue(cp.openpilotLongitudinalControl and cp.pcmCruise)
            self.assertEqual(bool(cp.flags & ToyotaFlags.HYBRID), hybrid)
            self.assertEqual(cp.steerControlType, structs.CarParams.SteerControlType.torque)
            self.assertEqual(DBC[car][Bus.pt], 'toyota_nodsu_pt_generated')
            preferences = VehicleStartupPreferences(toyota_auto_hold=hold)
            preferences.prepare(cp)
            self.assertTrue(qualified(cp))
            policy = policy_for(cp)
            self.assertTrue(policy.full_axis_runtime_required)
            cp.alternativeExperience |= policy.alternative_experience_addition
            preferences.finalize(cp)
            expected = 288 if hold and (car == CAR.TOYOTA_CAMRY_TSS2 or hybrid and car == CAR.TOYOTA_RAV4_TSS2) else 160 if hold else 32
            self.assertEqual(cp.alternativeExperience, expected)
            self.assertTrue(qualified(cp, marked_only=True))
            for experience in (33, 416, 288 if expected != 288 else 160):
              bad = cp.as_reader().as_builder()
              bad.alternativeExperience = experience
              self.assertFalse(qualified(bad, marked_only=True))
            for flag in (ToyotaFlags.RADAR_ACC, ToyotaFlags.SECOC, ToyotaFlags.ANGLE_CONTROL, ToyotaFlags.LONG_FILTER):
              bad = cp.as_reader().as_builder()
              bad.flags |= int(flag)
              self.assertFalse(qualified(bad, marked_only=True))
            bad = cp.as_reader().as_builder()
            bad.safetyConfigs[0].safetyParam = 585
            bad.openpilotLongitudinalControl = False
            self.assertFalse(qualified(bad))
    # The separately paired DSU port supports legacy IS with stock LONG.
    cp = CarInterface.get_params(CAR.LEXUS_IS, gen_empty_fingerprint(), [], True, False, False)
    self.assertTrue(qualified(cp))
    self.assertTrue(cp.pcmCruise)
    self.assertFalse(cp.openpilotLongitudinalControl)
    self.assertEqual(cp.safetyConfigs[0].safetyParam, 0x424D)
    bad = cp.as_reader().as_builder()
    bad.safetyConfigs[0].safetyParam ^= 0x4000
    self.assertFalse(qualified(bad))
    for car in (CAR.TOYOTA_RAV4_PRIME, CAR.TOYOTA_SIENNA_4TH_GEN):
      cp = CarInterface.get_params(car, gen_empty_fingerprint(), [], True, False, False)
      self.assertFalse(qualified(cp))

  def test_rav4_2023_angle_factory_and_exact_experience(self):
    for alpha, word in ((False, 0x649), (True, 0x449)):
      with self.subTest(alpha=alpha):
        cp = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2_2023, gen_empty_fingerprint(), [], alpha, False, False)
        self.assertEqual(cp.safetyConfigs[0].safetyParam, word)
        self.assertEqual(cp.steerControlType, structs.CarParams.SteerControlType.angle)
        self.assertTrue(qualified(cp))
        cp.alternativeExperience |= policy_for(cp).alternative_experience_addition
        self.assertEqual(cp.alternativeExperience, 32)
        self.assertTrue(qualified(cp, marked_only=True))
        cp.safetyConfigs[0].safetyParam ^= 1
        self.assertFalse(qualified(cp, marked_only=True))

  def test_camera_torque_group_consumes_same_physical_sources(self):
    for car in CAMERA_TORQUE_CASES:
      for hybrid in (False, True):
        with self.subTest(car=car, hybrid=hybrid):
          fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)] if hybrid else []
          cp = CarInterface.get_params(car, gen_empty_fingerprint(), fw, False, False, False)
          ci = CarInterface(cp)
          ci.update([])
          packer = CANPacker(DBC[car][Bus.pt])
          values: dict[str, dict[str, float]] = {
            'PCM_CRUISE': {'GAS_RELEASED': 1}, 'PCM_CRUISE_2': {'MAIN_ON': 1},
            'GEAR_PACKET': {'GEAR': 0}, 'EPS_STATUS': {'LKA_STATE': 1}, 'BODY_CONTROL_STATE': {},
          }
          tracked = {bus: list(parser.vl) for bus, parser in ci.can_parsers.items()}
          frames = [packer.make_can_msg(name, 0 if bus == Bus.pt else 2, values.get(name, {}))
                    for bus, names in tracked.items() for name in names]
          self.assertTrue({0x1D3, 0x3BC, 0x620, 0x262}.issubset({address for address, _, bus in frames if bus == 0}))
          for tick in range(8):
            state = ci.update([(1_000_000_000 + tick * 10_000_000, frames)])
          self.assertTrue(state.canValid)
          self.assertTrue(state.cruiseState.available)
          self.assertEqual(state.gearShifter, structs.CarState.GearShifter.drive)
          self.assertFalse(state.doorOpen or state.seatbeltUnlatched or state.steerFaultPermanent)

  def test_main_derived_intent_does_not_use_unreached_lkas_mapping(self):
    settings = AolSettings(True, 0., 9, 9, (0, 0, 0), (0, 0, 0))
    owner = ToyotaCardIntent(settings)
    state = structs.CarState(canValid=True, gearShifter=structs.CarState.GearShifter.drive)
    state.cruiseState.available = False
    state.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.lkas, pressed=True)]
    owner.update(state.as_reader())
    self.assertFalse(owner.allowed_latch)
    state.buttonEvents = []
    state.cruiseState.available = True
    owner.update(state.as_reader())
    self.assertTrue(owner.allowed_latch)
    state.cruiseState.available = False
    owner.update(state.as_reader())
    self.assertFalse(owner.allowed_latch)

  def test_actual_card_hold_and_master_off_preserve_full_axis_transport(self):
    for car in CAMERA_TORQUE_CASES:
      for hybrid in (False, True):
        with self.subTest(car=car, hybrid=hybrid):
          self._assert_card_full_axis_transport(car, hybrid)

  def _assert_card_full_axis_transport(self, car, hybrid, *, alpha=False, radar=False, prepared_cp=None,
                                       filter_disabled=False, late_disable=False):
    import os
    from types import SimpleNamespace
    from unittest.mock import patch
    from openpilot.common.params import Params
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.selfdrive.car.card import Car
    from openpilot.selfdrive.controls.controlsd import Controls
    from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
    for hold in ((False,) if radar or prepared_cp is not None else (False, True)):
      for enabled in (False, True):
        with self.subTest(hold=hold, enabled=enabled), OpenpilotPrefix(), \
             patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
          saved = Params()
          for key, value in (('OpenpilotEnabledToggle', True), ('AlwaysOnLateral', enabled),
                             ('ToyotaAutoHold', hold), ('SafeMode', False)):
            saved.put_bool(key, value, block=True)
          saved.put_bool('DisableOpenpilotLongitudinal', filter_disabled, block=True)
          cp = (prepared_cp.as_reader().as_builder() if prepared_cp is not None else
                CarInterface.get_params(car, gen_empty_fingerprint(),
                  [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)] if hybrid else [], alpha, False, False))

          def discover(*args, pre_create_hook, cp=cp, saved=saved, **kwargs):
            ci = CarInterface(pre_create_hook(cp.as_reader().as_builder(), cp.carFingerprint, {}, []))
            if late_disable:
              saved.put_bool('DisableOpenpilotLongitudinal', True, block=True)
            return ci
          with patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=SimpleNamespace(can=[1])), \
               patch('openpilot.selfdrive.car.card.get_car', side_effect=discover):
            selected = Car()
          aeb_hold = car == CAR.TOYOTA_CAMRY_TSS2 or hybrid and car == CAR.TOYOTA_RAV4_TSS2
          self.assertEqual(selected.CP.alternativeExperience, 288 if hold and aeb_hold else 160 if hold else 32)
          self.assertEqual(bool(selected.CP.flags & ToyotaFlags.AUTO_BRAKE_HOLD), hold)
          self.assertTrue(qualified(selected.CP, marked_only=True))
          if prepared_cp is not None:
            self.assertEqual(selected.CP.safetyConfigs[0].safetyParam, 0x8249 if filter_disabled or late_disable else 0x8049)
            self.assertEqual(selected.CP.openpilotLongitudinalControl, not (filter_disabled or late_disable))
            self.assertFalse(selected.CP.autoResumeSng)
          with structs.CarParams.from_bytes(saved.get('CarParams')) as published:
            self.assertEqual(published.to_dict(), selected.CP.to_dict())
          controls = Controls()
          with patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False):
            sd = SelfdriveD(CP=selected.CP)
          self.assertTrue(controls.aol_replay)
          self.assertTrue(sd.aol_replay)
          state = structs.CarState(canValid=True, gearShifter=structs.CarState.GearShifter.drive)
          state.cruiseState.available = True
          selected.aol_card_intent.update(state.as_reader())
          self.assertEqual(selected.aol_card_intent.allowed_latch, enabled)
          del controls, sd, selected
