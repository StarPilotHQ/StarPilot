"""Real Toyota CP and LongControl composition, with optional radar transport."""
from openpilot.starpilot.longitudinal.tests.extension_helpers import empty_params


from openpilot.starpilot.longitudinal.extension import LongitudinalContext
from openpilot.starpilot.longitudinal.tests.extension_helpers import extension_state
from openpilot.starpilot.longitudinal.tests.extension_helpers import attach_inputs
import os
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import patch

from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.cereal import messaging
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.starpilot.longitudinal.toyota_output_policy import Lead


class ToyotaLongIntegrationTests(TestCase):
  @staticmethod
  def car_state(speed=1.0):
    event = messaging.new_message('carState')
    event.carState.vEgo = speed
    event.carState.aEgo = 0.0
    event.carState.brakePressed = False
    event.carState.cruiseState.standstill = False
    return messaging.log_from_bytes(event.to_bytes()).carState

  @staticmethod
  def controller(car, enabled=True):
    cp = CarInterface.get_non_essential_params(car)
    with patch.dict(os.environ, {'TOYOTA_LONG_OUTPUT_REPLAY_RUNTIME': '1' if enabled else '0'}), \
         patch('openpilot.starpilot.longitudinal.extension.production_enabled', return_value=False):
      return LongControl(cp)

  def test_production_corolla_actual_card_and_denied_profiles(self):
    from opendbc.car import structs, gen_empty_fingerprint
    from openpilot.common.params import Params
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.selfdrive.car.card import Car
    from openpilot.starpilot.longitudinal.toyota_output_policy import production_enabled

    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'TOYOTA_LONG_OUTPUT_REPLAY_RUNTIME': '0'}):
      saved = Params()
      for key, value in (('OpenpilotEnabledToggle', True), ('SafeMode', False), ('AlwaysOnLateral', True)):
        saved.put_bool(key, value, block=True)
      cp = CarInterface.get_params(CAR.TOYOTA_COROLLA_TSS2, gen_empty_fingerprint(), [], False, False, False)
      def discover(*args, pre_create_hook, **kwargs):
        return CarInterface(pre_create_hook(cp.as_reader().as_builder(), cp.carFingerprint, {}, []))
      with patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=NS(can=[1])), \
           patch('openpilot.selfdrive.car.card.get_car', side_effect=discover):
        selected = Car()
      for candidate in (cp, selected.CP):
        self.assertTrue(production_enabled(candidate))
        owner = LongControl(candidate)
        self.assertIsNotNone(extension_state(owner, 'toyota_output'))
        state = self.car_state(1.0)
        filtered = owner.update(True, state, 1.5, False, (-3.5, 2.0))
        reference = self.controller(CAR.TOYOTA_COROLLA_TSS2, enabled=False)
        self.assertLess(filtered, reference.update(True, state, 1.5, False, (-3.5, 2.0)))
        owner.update(True, state, -1.0, True, (-3.5, 2.0))
        self.assertFalse(extension_state(owner, 'toyota_output').initialized)
      for mutation in ('stock', 'passive', 'no_output', 'identity', 'foreign_flags'):
        bad = selected.CP.as_reader().as_builder()
        if mutation == 'stock':
          bad.openpilotLongitudinalControl = False
          bad.safetyConfigs[0].safetyParam = 585
        elif mutation == 'passive':
          bad.passive = True
        elif mutation == 'no_output':
          bad.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.noOutput
        elif mutation == 'identity':
          bad.carFingerprint = CAR.TOYOTA_PRIUS_RETROFIT
        else:
          bad.flags |= 1 << 30
        self.assertFalse(production_enabled(bad), mutation)
        self.assertIsNone(extension_state(LongControl(bad), 'toyota_output'), mutation)

  def test_production_sienna_requires_final_card_key_and_secoc_profile(self):
    from opendbc.car import structs, gen_empty_fingerprint
    from openpilot.common.params import Params
    from openpilot.common.prefix import OpenpilotPrefix
    from openpilot.selfdrive.car.card import Car
    from openpilot.starpilot.longitudinal.inputs import LongitudinalInputs
    from openpilot.starpilot.longitudinal.toyota_output_policy import production_enabled

    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'TOYOTA_LONG_OUTPUT_REPLAY_RUNTIME': '0'}):
      saved = Params()
      saved.put_bool('OpenpilotEnabledToggle', True, block=True)
      saved.put_bool('SafeMode', False, block=True)
      saved.put('SecOCKey', '11' * 16, block=True)
      cp = CarInterface.get_params(CAR.TOYOTA_SIENNA_4TH_GEN, gen_empty_fingerprint(), [], False, False, False)
      self.assertFalse(production_enabled(cp))
      def discover(*args, pre_create_hook, **kwargs):
        return CarInterface(pre_create_hook(cp.as_reader().as_builder(), cp.carFingerprint, {}, []))
      with patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=NS(can=[1])), \
           patch('openpilot.selfdrive.car.card.get_car', side_effect=discover):
        selected = Car()
      self.assertTrue(selected.CP.secOcKeyAvailable)
      self.assertTrue(production_enabled(selected.CP))
      owner = LongControl(selected.CP)
      self.assertFalse(extension_state(owner, 'toyota_stopped_lead'))
      inputs = LongitudinalInputs(selected.CP, empty_params(self), lambda: None)
      self.assertIn('radarState', inputs.optional_services)
      self.assertFalse(inputs.toyota_corolla_stop)
      self.assertFalse(inputs.toyota_sienna_replay)
      state = self.car_state(-0.238205)
      context = LongitudinalContext(leads=())
      output = owner.update(True, state, 1.5, False, (-3.5, 2.0), context=context)
      self.assertTrue(extension_state(owner, 'toyota_output').initialized)
      self.assertGreater(output, 0.0)
      self.assertLess(output, 1.5)
      owner.update(True, state, -1.0, True, (-3.5, 2.0), context=context)
      self.assertFalse(extension_state(owner, 'toyota_output').initialized)
      for mutation in ('missing_key', 'stock', 'release', 'no_output', 'foreign_flags', 'foreign_ae', 'sienna3'):
        bad = selected.CP.as_reader().as_builder()
        if mutation == 'missing_key':
          bad.secOcKeyAvailable = False
        elif mutation == 'stock':
          bad.openpilotLongitudinalControl = False
          bad.safetyConfigs[0].safetyParam = 2633
        elif mutation == 'release':
          bad = CarInterface.get_params(CAR.TOYOTA_SIENNA_4TH_GEN, gen_empty_fingerprint(), [], False, True, False)
          bad.secOcKeyAvailable = True
          self.assertTrue(bad.dashcamOnly)
        elif mutation == 'no_output':
          bad.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.noOutput
        elif mutation == 'foreign_flags':
          bad.flags |= 1 << 30
        elif mutation == 'foreign_ae':
          bad.alternativeExperience = 160
        else:
          bad.carFingerprint = CAR.TOYOTA_SIENNA
        self.assertFalse(production_enabled(bad), mutation)
        self.assertIsNone(extension_state(LongControl(bad), 'toyota_output'), mutation)

  def test_corolla_filtered_speed_undershoot_retains_pid_target_filter(self):
    from opendbc.car import gen_empty_fingerprint
    cp = CarInterface.get_params(CAR.TOYOTA_COROLLA_TSS2, gen_empty_fingerprint(), [], False, False, False)
    outputs = []
    with patch.dict(os.environ, {'TOYOTA_LONG_OUTPUT_REPLAY_RUNTIME': '0'}):
      for speed in (0.0, -0.238205):
        owner = LongControl(cp)
        outputs.append(owner.update(True, self.car_state(speed), 0.5, False, (-3.5, 2.0)))
        policy = extension_state(owner, 'toyota_output')
        self.assertTrue(policy.initialized)
        self.assertAlmostEqual(policy.filtered_target, 0.5 * 0.01 / (0.30 + 0.01))
    self.assertEqual(outputs[0], outputs[1])

  def test_corolla_stopped_lead_only_vetoes_stopping_release(self):
    from opendbc.car import gen_empty_fingerprint, structs
    states = structs.CarControl.Actuators.LongControlState
    cp = CarInterface.get_params(CAR.TOYOTA_COROLLA_TSS2, gen_empty_fingerprint(), [], False, False, False)
    for speed, leads, veto in (
        (0.5, (Lead(True, 8.0, 1.75, 0.35, 0.0),), True),
        (-0.238205, (Lead(True, 5.0, 0.0, 0.0, 0.0),), True),
        (0.5001, (Lead(True, 8.0, 0.0, 0.0, 0.0),), False),
        (0.0, (Lead(True, 8.001, 0.0, 0.0, 0.0),), False),
        (0.0, (Lead(True, 0.0, 0.0, 0.0, 0.0),), False),
        (0.0, (Lead(True, 8.0, 1.751, 0.0, 0.0),), False),
        (0.0, (Lead(True, 8.0, 0.0, 0.351, 0.0),), False),
        (0.0, (Lead(False, 8.0, 0.0, 0.0, 0.0),), False),
        (0.0, (), False), (0.0, None, False)):
      owner = LongControl(cp)
      cs = self.car_state(speed)
      context = LongitudinalContext(leads=leads)
      owner.update(True, cs, -1.0, True, (-3.5, 2.0), context=context)
      self.assertEqual(owner.long_control_state, states.stopping)
      owner.update(True, cs, 0.5, False, (-3.5, 2.0), context=context)
      self.assertEqual(owner.long_control_state, states.stopping if veto else states.pid)
      owner.update(False, cs, 0.5, False, (-3.5, 2.0), context=context)
      self.assertEqual(owner.long_control_state, states.off)
      owner.update(True, cs, 0.5, False, (-3.5, 2.0), context=context)
      self.assertEqual(owner.long_control_state, states.pid)
    owner = LongControl(cp)
    cs = self.car_state(0.0).as_builder()
    owner.update(True, cs, -1.0, True, (-3.5, 2.0))
    cs.brakePressed = True
    owner.update(True, cs, 0.5, False, (-3.5, 2.0), context=LongitudinalContext(leads=()))
    self.assertEqual(owner.long_control_state, states.stopping)

  def test_production_corolla_routes_fresh_stop_leads(self):
    from opendbc.car import gen_empty_fingerprint, structs
    from openpilot.starpilot.longitudinal.inputs import LongitudinalInputs
    cp = CarInterface.get_params(CAR.TOYOTA_COROLLA_TSS2, gen_empty_fingerprint(), [], False, False, False)
    radar_event = messaging.new_message('radarState', valid=True)
    radar_event.radarState.leadOne.present = True
    radar_event.radarState.leadOne.dRel = 5.0
    radar = messaging.log_from_bytes(radar_event.to_bytes()).radarState
    device = NS(started=True, startedMonoTime=900_000_000)
    car_state = self.car_state(0.0)

    class Messages(NS):
      def __getitem__(self, name):
        return {'radarState': radar, 'deviceState': device, 'carState': car_state}[name]
    now = [1_050_000_000, 500_000_000]
    sm = Messages(seen={'radarState': True, 'deviceState': True},
                  alive={'radarState': True, 'deviceState': True},
                  valid={'radarState': True, 'deviceState': True},
                  logMonoTime={}, recv_time={}, all_checks=lambda names: names == ['carState'])
    def advance_sources():
      for name in ('radarState', 'deviceState', 'carState'):
        sm.logMonoTime[name] = now[0] - 5_000_000
      for name in ('radarState', 'deviceState'):
        sm.recv_time[name] = (now[0] - 2_000_000) / 1e9
    advance_sources()
    with patch.dict(os.environ, {'TOYOTA_LONG_OUTPUT_REPLAY_RUNTIME': '0', 'REPLAY': '0'}), \
         patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', side_effect=lambda: (now[0], now[0] + now[1])):
      inputs = LongitudinalInputs(cp, empty_params(self), lambda: sm)
      self.assertIn('radarState', inputs.optional_services)
      self.assertIsNone(inputs.context(True).leads)
      now[0] += 20_000_000
      advance_sources()
      current = inputs.context(True)
      self.assertIsNotNone(current.leads)
      owner = LongControl(cp)
      cs = self.car_state(0.0)
      owner.update(True, cs, -1.0, True, (-3.5, 2.0), context=current)
      owner.update(True, cs, 0.5, False, (-3.5, 2.0), context=current)
      self.assertEqual(owner.long_control_state, structs.CarControl.Actuators.LongControlState.stopping)
      sm.logMonoTime['radarState'] = now[0] - 101_000_000
      stale = inputs.context(True)
      self.assertIsNone(stale.leads)
      owner.update(True, cs, 0.5, False, (-3.5, 2.0), context=stale)
      self.assertEqual(owner.long_control_state, structs.CarControl.Actuators.LongControlState.pid)
      advance_sources()
      self.assertIsNotNone(inputs.context(True).leads)
      now[1] += 9_000_000_000
      self.assertIsNone(inputs.context(True).leads)
      now[0] += 20_000_000
      self.assertIsNone(inputs.context(True).leads)
      advance_sources()
      self.assertIsNotNone(inputs.context(True).leads)
      device.startedMonoTime = now[0] - 1_000_000
      self.assertIsNone(inputs.context(True).leads)
      self.assertIsNone(inputs.context(False).leads)

  def test_tss2_stopping_ramp_uses_admitted_vehicle_rate(self):
    from opendbc.car import structs
    from opendbc.car.toyota.values import ToyotaFlags

    for car in CAR:
      if not car.config.flags & ToyotaFlags.TSS2:
        continue
      cp = CarInterface.get_params(car, {0: {}, 1: {}, 2: {}}, [], True, False, False)
      if not cp.openpilotLongitudinalControl or cp.dashcamOnly:
        continue
      variants = [(cp, 0.3)]
      for mutation in ('no_output', 'multiple', 'stock', 'passive'):
        denied = cp.as_reader().as_builder()
        if mutation == 'no_output':
          denied.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.noOutput
        elif mutation == 'multiple':
          denied.safetyConfigs = list(denied.safetyConfigs) + [structs.CarParams.SafetyConfig()]
        elif mutation == 'stock':
          denied.openpilotLongitudinalControl = False
        else:
          denied.passive = True
        variants.append((denied, 1.0))
      for candidate, rate in variants:
        for target in (-0.8, 0.8):
          with self.subTest(car=car, rate=rate, target=target, model=candidate.safetyConfigs[0].safetyModel):
            with patch.dict(os.environ, {'TOYOTA_LONG_OUTPUT_REPLAY_RUNTIME': '0'}):
              owner = LongControl(candidate)
            state = self.car_state(0.0)
            for tick in range(1, 6):
              result = owner.update(True, state, target, True, (-3.5, 2.0))
              self.assertEqual(owner.long_control_state, structs.CarControl.Actuators.LongControlState.stopping)
              self.assertAlmostEqual(result, -rate * 0.01 * tick)
            self.assertEqual(owner.update(False, state, target, True, (-3.5, 2.0)), 0.0)

  def test_real_controller_gate_and_native_missing_lead_fallback(self):
    sienna = self.controller(CAR.TOYOTA_SIENNA_4TH_GEN)
    native = self.controller(CAR.TOYOTA_SIENNA_4TH_GEN, enabled=False)
    self.assertIsNotNone(extension_state(sienna, 'toyota_output'))
    self.assertIsNone(extension_state(native, 'toyota_output'))
    for target, speed, stopping, active in ((1.0, 1.0, False, True), (1.5, 0.2, False, True),
                                             (-0.8, 0.2, True, True), (0.5, 0.0, False, False),
                                             (1.0, 1.0, False, True)):
      cs = self.car_state(speed)
      actual = sienna.update(active, cs, target, stopping, (-3.5, 2.0), context=LongitudinalContext(leads=None))
      expected = native.update(active, cs, target, stopping, (-3.5, 2.0))
      self.assertEqual(actual, expected)
      self.assertFalse(extension_state(sienna, 'toyota_output').initialized)

  def test_real_controller_output_changes_only_when_qualified(self):
    cs = self.car_state(0.0)
    for car, leads in ((CAR.TOYOTA_COROLLA_TSS2, None), (CAR.TOYOTA_SIENNA_4TH_GEN, ())):
      tuned = self.controller(car)
      native = self.controller(car, enabled=False)
      tuned_output = tuned.update(True, cs, 1.5, False, (-3.5, 2.0), context=LongitudinalContext(leads=leads))
      native_output = native.update(True, cs, 1.5, False, (-3.5, 2.0))
      self.assertLess(tuned_output, native_output)
      self.assertTrue(-3.5 <= tuned_output <= 2.0)
    sienna = self.controller(CAR.TOYOTA_SIENNA_4TH_GEN)
    lead = (Lead(True, 15.0, 0.0, 10.0, 0.0),)
    self.assertTrue(-3.5 <= sienna.update(True, cs, 2.0, False, (-3.5, 2.0), context=LongitudinalContext(leads=lead)) <= 2.0)

  def test_optional_radar_transport_does_not_change_stock_health(self):
    now = 1_050_000_000
    radar_event = messaging.new_message('radarState', valid=True)
    radar_event.radarState.leadOne.present = True
    radar_event.radarState.leadOne.dRel = 15.0
    radar = messaging.log_from_bytes(radar_event.to_bytes()).radarState
    device_event = messaging.new_message('deviceState', valid=True)
    device_event.deviceState.started = True
    device_event.deviceState.startedMonoTime = 900_000_000
    device = messaging.log_from_bytes(device_event.to_bytes()).deviceState
    class FakeSubMaster(NS):
      def __getitem__(self, name):
        return {'radarState': radar, 'deviceState': device}[name]

    sm = FakeSubMaster(seen={'radarState': True, 'deviceState': True},
            alive={'radarState': True, 'deviceState': True},
            valid={'radarState': True, 'deviceState': True},
            logMonoTime={'radarState': now - 30_000_000, 'deviceState': now - 50_000_000,
                         'carState': now - 20_000_000},
            recv_time={'radarState': (now - 10_000_000) / 1e9,
                       'deviceState': (now - 10_000_000) / 1e9},
            all_checks=lambda names: names == ['carState'])
    controls = Controls.__new__(Controls)
    attach_inputs(controls)
    controls.longitudinal_inputs.toyota_sienna_replay = True
    controls.longitudinal_inputs.toyota_boot_offset_ns = 500_000_000
    controls.longitudinal_inputs.toyota_source_floor_ns = 900_000_000
    self.enterContext(patch.object(controls, 'sm', sm, create=True))
    with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', return_value=(now, now + 500_000_000)):
      self.assertIsNotNone(controls.longitudinal_inputs._toyota_leads())
      sm.valid['radarState'] = False
      self.assertIsNone(controls.longitudinal_inputs._toyota_leads())
      sm.valid['radarState'] = True
      sm.logMonoTime['radarState'] = now - 101_000_000
      self.assertIsNone(controls.longitudinal_inputs._toyota_leads())
      sm.logMonoTime['radarState'] = now - 30_000_000
      device = NS(started=True, startedMonoTime=now - 5_000_000)
      self.assertIsNone(controls.longitudinal_inputs._toyota_leads())

  def test_optional_leads_require_new_producer_frames_after_suspend(self):
    now = 1_050_000_000
    offset = 500_000_000
    radar_event = messaging.new_message('radarState', valid=True)
    radar_event.radarState.leadOne.present = True
    radar_event.radarState.leadOne.dRel = 15.0
    radar = messaging.log_from_bytes(radar_event.to_bytes()).radarState
    device_event = messaging.new_message('deviceState', valid=True)
    device_event.deviceState.started = True
    device_event.deviceState.startedMonoTime = 900_000_000
    device = messaging.log_from_bytes(device_event.to_bytes()).deviceState

    class FakeSubMaster(NS):
      def __getitem__(self, name):
        return {'radarState': radar, 'deviceState': device}[name]

    sm = FakeSubMaster(seen={'radarState': True, 'deviceState': True},
                       alive={'radarState': True, 'deviceState': True},
                       valid={'radarState': True, 'deviceState': True},
                       logMonoTime={'radarState': now - 20_000_000, 'deviceState': now - 20_000_000,
                                    'carState': now - 20_000_000},
                       recv_time={'radarState': (now - 10_000_000) / 1e9,
                                  'deviceState': (now - 10_000_000) / 1e9},
                       all_checks=lambda names: names == ['carState'])
    controls = Controls.__new__(Controls)
    attach_inputs(controls)
    controls.longitudinal_inputs.toyota_sienna_replay = True
    controls.longitudinal_inputs.toyota_boot_offset_ns = None
    controls.longitudinal_inputs.toyota_source_floor_ns = 0
    self.enterContext(patch.object(controls, 'sm', sm, create=True))

    clock = [now, offset]
    with patch('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', side_effect=lambda: (clock[0], clock[0] + clock[1])):
      self.assertIsNone(controls.longitudinal_inputs._toyota_leads())  # initial epoch establishes a source floor
      self.assertIsNone(controls.longitudinal_inputs._toyota_leads())  # cached pre-floor messages cannot renew it
      clock[0] += 20_000_000
      for name in ('radarState', 'deviceState', 'carState'):
        sm.logMonoTime[name] = clock[0] - 5_000_000
      for name in ('radarState', 'deviceState'):
        sm.recv_time[name] = (clock[0] - 2_000_000) / 1e9
      self.assertIsNotNone(controls.longitudinal_inputs._toyota_leads())

      # MONOTONIC advances only 10 ms while BOOTTIME advances nine seconds.
      # The cached messages still look young to ordinary MONOTONIC freshness.
      clock[0] += 10_000_000
      clock[1] += 9_000_000_000
      self.assertIsNone(controls.longitudinal_inputs._toyota_leads())
      clock[0] += 10_000_000
      self.assertIsNone(controls.longitudinal_inputs._toyota_leads())
      sm.logMonoTime['radarState'] = clock[0] - 2_000_000
      sm.logMonoTime['deviceState'] = clock[0] - 2_000_000
      sm.recv_time['radarState'] = (clock[0] - 1_000_000) / 1e9
      sm.recv_time['deviceState'] = (clock[0] - 1_000_000) / 1e9
      self.assertIsNone(controls.longitudinal_inputs._toyota_leads())  # carState has not advanced yet
      sm.logMonoTime['carState'] = clock[0] - 1_000_000
      self.assertIsNotNone(controls.longitudinal_inputs._toyota_leads())


if __name__ == '__main__':
  import unittest
  unittest.main()
