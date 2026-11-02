"""Published automatic fits must bind the real factory and respect saved intent."""

import json
from pathlib import Path
from types import SimpleNamespace
import struct
import unittest

from opendbc.car.car_helpers import interfaces
from opendbc.car.structs import car
from openpilot.starpilot.lateral.learning_preferences import KEY as LEARNING_KEY, LEGACY_KEY as LEARNING_LEGACY_KEY, replace_preference
from openpilot.system.manager.process_config import managed_processes
from opendbc.car.gm.values import CAR as GM
from opendbc.car.hyundai.values import CAR as HKG
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.flm.learning_source import (
  AdmittedPolicy, AutomaticLearningConsumer, MAX_AGE_NS, RESET_KEY, SERVICE, conversion_identity, identity_for, learning_payload,
)
from openpilot.starpilot.lateral.controller_selection import ControllerMode, DOCUMENT_KEY, policy_for, replace_mode
from openpilot.starpilot.lateral.torque_runtime import TorqueHost
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY as TORQUE_KEY, replace_field, serialize_document

VEHICLES = (GM.CHEVROLET_BOLT_CC_2018_2021, HKG.HYUNDAI_IONIQ_6, TOYOTA.TOYOTA_COROLLA_TSS2)
NOW = 20_000_000_000


def float32(value):
  """The controller's CarParams torque fields are Float32, not Python doubles."""
  return struct.unpack('<f', struct.pack('<f', value))[0]


def context(vehicle):
  cp = interfaces[vehicle].get_non_essential_params(vehicle).as_reader()
  params = Params()
  if policy_for(cp) is not None:
    params.put(DOCUMENT_KEY, json.loads(replace_mode(None, cp, ControllerMode.STANDARD)), block=True)
  ci = interfaces[vehicle](cp)
  source = AutomaticLearningConsumer(cp, ci, ControllerMode.STANDARD, params,
                                    policies={'test-contract': AdmittedPolicy(.1)})
  return cp, ci, params, source


def wire(cp, params, *, now=NOW, sequence=1, **changes):
  identity, enabled, manual = identity_for(cp, ControllerMode.STANDARD, params, 'opendbc.linear_inverse_v1')
  assert identity is not None and enabled and not manual
  event = messaging.new_message(SERVICE, valid=True)
  state = learning_payload(getattr(event, SERVICE))
  state.version, state.status, state.family = 1, 'qualifiedFit', 'linearFactor'
  state.identity = identity
  state.session, state.sequence = 'consumer-test', sequence
  state.observedMonoTime, state.validUntilMonoTime = now, now + MAX_AGE_NS
  state.sourceWindowStartMonoTime, state.sourceWindowEndMonoTime = now - 10_000_000_000, now - 1_000_000_000
  state.latAccelFactor = cp.lateralTuning.torque.latAccelFactor * 1.05
  state.acceptancePolicyId = 'test-contract'
  state.trainWindows, state.heldoutWindows = 10, 8
  state.trainBaselineLoss, state.trainCandidateLoss = .1, .09
  state.heldoutBaselineLoss, state.heldoutCandidateLoss = .1, .09
  for key, value in changes.items():
    setattr(state, key, value)
  # Exercise the actual wire reader; these declared transport flags are input
  # doubles, not production frequency/IPC qualification.
  reader = messaging.log_from_bytes(event.to_bytes())
  sm = {SERVICE: getattr(reader, SERVICE)}
  return SimpleNamespace(seen={SERVICE: True}, alive={SERVICE: True}, valid={SERVICE: True}, freq_ok={SERVICE: True},
                         recv_time={SERVICE: now / 1e9}), sm


class InputState(SimpleNamespace):
  def __getitem__(self, key):
    return self.states[key]

  def all_checks(self, names):
    return all(self.seen.get(name, False) and self.alive.get(name, False) and
               self.valid.get(name, False) and self.freq_ok.get(name, False) for name in names)


def input_state(cp, params, **kwargs):
  flags, states = wire(cp, params, **kwargs)
  return InputState(**vars(flags), states=states)


class TestAutomaticLearningSource(unittest.TestCase):
  def test_actual_linear_factories_and_slewed_numerical_application_without_manual_document(self):
    for vehicle in VEHICLES:
      with self.subTest(vehicle=vehicle), OpenpilotPrefix():
        cp, ci, params, source = context(vehicle)
        self.assertTrue(source.supported)
        self.assertIsNone(params.get(TORQUE_KEY))
        sm = input_state(cp, params)
        host = TorqueHost(params, cp, allow_learning=False, automatic_interface=ci, automatic_controller=ControllerMode.STANDARD)
        controller = LatControlTorque(cp, ci, .01, controller_mode=ControllerMode.STANDARD)
        previous = controller.torque_from_lateral_accel(1., controller.torque_params)
        for frame in range(20):
          now = NOW + frame * 10_000_000
          tune = source.sample(sm, now_ns=now, lat_active=True)
          self.assertIsNotNone(tune)
          host.apply(controller, host.sample(sm, now_ns=now, lat_active=True, automatic=tune))
        self.assertGreater(controller.torque_params.latAccelFactor, cp.lateralTuning.torque.latAccelFactor)
        self.assertLess(controller.torque_from_lateral_accel(1., controller.torque_params), previous)
        self.assertEqual(controller.torque_params.latAccelOffset, cp.lateralTuning.torque.latAccelOffset)
        self.assertEqual(controller.torque_params.friction, cp.lateralTuning.torque.friction)
        host.apply(controller, host.sample(sm, now_ns=NOW + 210_000_000, lat_active=False))
        self.assertEqual(controller.torque_params.latAccelFactor, cp.lateralTuning.torque.latAccelFactor)

  def test_lazy_adoption_slews_from_applied_native_tune_and_preserves_native_nonfactor_values(self):
    with OpenpilotPrefix():
      cp, ci, params, source = context(HKG.HYUNDAI_IONIQ_6)
      basis = cp.lateralTuning.torque
      controller = LatControlTorque(cp, ci, .01, controller_mode=ControllerMode.STANDARD)
      factor, offset, friction = float32(basis.latAccelFactor * 1.1), float32(.03), float32(basis.friction * 1.1)
      controller.update_torque_parameters(factor, offset, friction)
      host = TorqueHost(params, cp, allow_learning=True, automatic_interface=ci, automatic_controller=ControllerMode.STANDARD)
      host.adopt_linear_controller(controller)
      self.assertFalse(host.apply(controller, host.applied))
      sm = input_state(cp, params)
      for name in ('seen', 'alive', 'valid', 'freq_ok'):
        getattr(sm, name)['lateralTorqueParameters'] = True
      sm.logMonoTime = {'lateralTorqueParameters': NOW}
      sm.recv_time['lateralTorqueParameters'] = NOW / 1e9
      sm.states['lateralTorqueParameters'] = SimpleNamespace(useParams=True, valid=True, version=1, latAccelFactorFiltered=factor,
        latAccelOffsetFiltered=offset, frictionCoefficientFiltered=friction)
      step = .005 * basis.latAccelFactor
      for frame in range(55):
        now = NOW + frame * 10_000_000
        prior = host.applied.lat_accel_factor
        automatic = source.sample(sm, now_ns=now, lat_active=True)
        host.apply(controller, host.sample(sm, now_ns=now, lat_active=True, automatic=automatic))
        self.assertLessEqual(abs(host.applied.lat_accel_factor - prior), step + 1e-12)
        self.assertEqual(controller.torque_params.latAccelFactor, float32(host.applied.lat_accel_factor))
        self.assertEqual(controller.torque_params.latAccelOffset, offset)
        self.assertEqual(controller.torque_params.friction, friction)
        if frame == 0:
          self.assertEqual(controller.torque_params.latAccelFactor, float32(factor - step))
        if frame > 50:
          self.assertIsNone(automatic)
          self.assertGreater(controller.torque_params.latAccelFactor, prior)
      # Missing native updates retain the old owner's scalar base during fallback.
      sm.alive['lateralTorqueParameters'] = False
      prior = host.applied.lat_accel_factor
      host.apply(controller, host.sample(sm, now_ns=NOW + 550_000_000, lat_active=False))
      self.assertLessEqual(abs(host.applied.lat_accel_factor - prior), step + 1e-12)
      self.assertEqual(controller.torque_params.latAccelFactor, float32(host.applied.lat_accel_factor))
      self.assertEqual(controller.torque_params.latAccelOffset, offset)
      self.assertEqual(controller.torque_params.friction, friction)
      # A fresh native update may change its own nonfactor parameters normally.
      sm.alive['lateralTorqueParameters'] = True
      sm.logMonoTime['lateralTorqueParameters'] = NOW + 560_000_000
      sm.recv_time['lateralTorqueParameters'] = (NOW + 560_000_000) / 1e9
      sm.states['lateralTorqueParameters'].latAccelOffsetFiltered = float32(-.02)
      sm.states['lateralTorqueParameters'].frictionCoefficientFiltered = float32(basis.friction * 1.2)
      host.apply(controller, host.sample(sm, now_ns=NOW + 560_000_000, lat_active=False))
      self.assertEqual(controller.torque_params.latAccelOffset, float32(-.02))
      self.assertEqual(controller.torque_params.friction, float32(basis.friction * 1.2))

  def test_wrong_identity_expiry_sequence_and_unadmitted_policy_do_not_apply(self):
    with OpenpilotPrefix():
      cp, _, params, source = context(HKG.HYUNDAI_IONIQ_6)
      sm = input_state(cp, params)
      self.assertIsNotNone(source.sample(sm, now_ns=NOW, lat_active=True))
      wrong = learning_payload(sm[SERVICE]).identity.to_dict()
      wrong['vehicle'] = 'different-vehicle'
      for sequence, overrides in enumerate(({'identity': wrong}, {'version': 2}, {'latAccelFactor': float('nan')}, {'latAccelFactor': 100.},
                        {'acceptancePolicyId': 'unqualified'}, {'trainWindows': 7}, {'heldoutWindows': 7},
                        {'trainCandidateLoss': .1}, {'heldoutCandidateLoss': .1}, {'sequence': 0}, {'validUntilMonoTime': NOW - 1}), start=2):
        with self.subTest(overrides=overrides):
          bad = input_state(cp, params, **({'sequence': sequence} | overrides))
          self.assertIsNone(source.sample(bad, now_ns=NOW, lat_active=True))
      self.assertIsNone(source.sample(sm, now_ns=NOW + MAX_AGE_NS + 1, lat_active=True))

  def test_saved_off_malformed_manual_and_reset_invalidate_existing_result(self):
    for changed_key, raw in (('ForceAutoTuneOff', b'1'), (TORQUE_KEY, b'malformed'), (RESET_KEY, b'1234')):
      with self.subTest(key=changed_key), OpenpilotPrefix():
        cp, _, params, source = context(HKG.HYUNDAI_IONIQ_6)
        sm = input_state(cp, params)
        self.assertIsNotNone(source.sample(sm, now_ns=NOW, lat_active=True))
        Path(params.get_param_path(changed_key)).write_bytes(raw)
        self.assertIsNone(source.sample(sm, now_ns=NOW + 1_000_000_000, lat_active=True))
        self.assertNotEqual(source.identity, learning_payload(sm[SERVICE]).identity.to_dict())

  def test_saved_manual_factor_overrides_automatic_overlay_after_adoption(self):
    with OpenpilotPrefix():
      cp, ci, params, source = context(HKG.HYUNDAI_IONIQ_6)
      basis = cp.lateralTuning.torque
      sm = input_state(cp, params)
      controller = LatControlTorque(cp, ci, .01, controller_mode=ControllerMode.STANDARD)
      controller.update_torque_parameters(basis.latAccelFactor * 1.1, .03, basis.friction * 1.1)
      host = TorqueHost(params, cp, allow_learning=False, automatic_interface=ci, automatic_controller=ControllerMode.STANDARD)
      host.adopt_linear_controller(controller)
      automatic = source.sample(sm, now_ns=NOW, lat_active=True)
      assert automatic is not None
      host.apply(controller, host.sample(sm, now_ns=NOW, lat_active=True, automatic=automatic))
      selected = replace_field({}, str(cp.carFingerprint),
        (basis.latAccelFactor, basis.latAccelOffset, basis.friction), 'factor', 'custom', basis.latAccelFactor * 1.2)
      params.put(TORQUE_KEY, json.loads(serialize_document(selected)), block=True)
      # Refresh discovers the saved manual identity; the old automatic wire cannot match it.
      self.assertIsNone(source.sample(sm, now_ns=NOW + 1_000_000_000, lat_active=True))
      host.sample(sm, now_ns=NOW + 1_000_000_000, lat_active=True, automatic=automatic)
      self.assertAlmostEqual(host.selected.lat_accel_factor, basis.latAccelFactor * 1.2)
      self.assertEqual(host.selected.lat_accel_offset, basis.latAccelOffset)
      self.assertEqual(host.selected.source.value, 'user')
      host.sample(sm, now_ns=NOW + 1_010_000_000, lat_active=True, automatic=automatic)
      self.assertAlmostEqual(host.selected.lat_accel_factor, basis.latAccelFactor * 1.2)

  def test_malformed_source_at_host_refresh_cannot_bypass_with_previously_accepted_auto(self):
    with OpenpilotPrefix():
      cp, ci, params, source = context(GM.CHEVROLET_BOLT_CC_2018_2021)
      sm = input_state(cp, params)
      automatic = source.sample(sm, now_ns=NOW, lat_active=True)
      assert automatic is not None
      for adopted in (False, True):
        with self.subTest(adopted=adopted):
          controller = LatControlTorque(cp, ci, .01, controller_mode=ControllerMode.STANDARD)
          host = TorqueHost(params, cp, allow_learning=False, automatic_interface=ci, automatic_controller=ControllerMode.STANDARD)
          if adopted:
            host.adopt_linear_controller(controller)
          host.sample(sm, now_ns=NOW, lat_active=True, automatic=automatic)
          self.assertTrue(host.settings.valid)  # Genuine absence takes the strict default parser.
          Path(params.get_param_path(TORQUE_KEY)).write_bytes(b'malformed')
          # A due host refresh may precede the consumer's independently phased refresh.
          host.last_refresh_ns = NOW - 1_000_000_000
          selected = host.sample(sm, now_ns=NOW + 10_000_000, lat_active=True, automatic=automatic)
          self.assertFalse(host.settings.valid)
          self.assertEqual(host.selected.lat_accel_factor, cp.lateralTuning.torque.latAccelFactor)
          self.assertNotEqual(selected.lat_accel_factor, automatic.lat_accel_factor)
          params.remove(TORQUE_KEY)

  def test_fixed_custom_and_nonlinear_conversion_are_not_inferred_from_preference(self):
    with OpenpilotPrefix():
      cp, ci, params, _ = context(GM.CHEVROLET_BOLT_CC_2018_2021)
      self.assertIsNone(conversion_identity(cp, ci, ControllerMode.STARPILOT))
      previous_document = Path(params.get_param_path(DOCUMENT_KEY)).read_bytes()
      cp, ci, params, _ = context(GM.CHEVROLET_SILVERADO)
      self.assertEqual(Path(params.get_param_path(DOCUMENT_KEY)).read_bytes(), previous_document)
      self.assertNotIn(str(cp.carFingerprint), json.loads(previous_document)['vehicles'])
      self.assertIsNone(conversion_identity(cp, ci, ControllerMode.STANDARD))
      cp, ci, _, _ = context(HKG.HYUNDAI_IONIQ_6)
      self.assertIsNone(conversion_identity(cp, ci, ControllerMode.STARPILOT))


class TestDisabledLearnerProcess(unittest.TestCase):
  def setUp(self):
    self.prefix = OpenpilotPrefix()
    self.prefix.__enter__()
    self.addCleanup(self.prefix.__exit__, None, None, None)
    self.params = Params()
    self.cp = car.CarParams.new_message(brand='hyundai', carFingerprint='HYUNDAI_IONIQ_6')
    self.process = managed_processes['steerlearnerd']

  def test_default_still_runs_onroad(self):
    assert self.process.should_run(True, self.params, self.cp)
    assert not self.process.should_run(False, self.params, self.cp)
    assert self.process.restart_on_exit

  def test_explicit_off_does_not_start_learner(self):
    self.params.put(LEARNING_KEY, replace_preference(None, self.cp, False), block=True)
    assert not self.process.should_run(True, self.params, self.cp)

  def test_toggle_can_stop_and_restart_learner(self):
    for enabled in (False, True, False, True):
      self.params.put(LEARNING_KEY, replace_preference(self.params.get(LEARNING_KEY), self.cp, enabled), block=True)
      assert self.process.should_run(True, self.params, self.cp) is enabled

  def test_legacy_off_does_not_start_learner(self):
    self.params.put_bool(LEARNING_LEGACY_KEY, True, block=True)
    assert not self.process.should_run(True, self.params, self.cp)

  def test_scoped_on_retains_precedence_over_legacy_off(self):
    self.params.put_bool(LEARNING_LEGACY_KEY, True, block=True)
    self.params.put(LEARNING_KEY, replace_preference(None, self.cp, True), block=True)
    assert self.process.should_run(True, self.params, self.cp)

  def test_invalid_preference_does_not_start_learner(self):
    for raw in (b'', b'not json', b'{"version":1,"vehicles":{"hyundai":{"HYUNDAI_IONIQ_6":1}}}'):
      self.params.put(LEARNING_KEY, raw, block=True)
      assert not self.process.should_run(True, self.params, self.cp)

  def test_other_vehicle_preference_is_not_reused(self):
    self.params.put(LEARNING_KEY, replace_preference(None, self.cp, False), block=True)
    other = car.CarParams.new_message(brand='ford', carFingerprint='FORD_MUSTANG_MACH_E_MK1')
    assert self.process.should_run(True, self.params, other)

  def test_offroad_does_not_read_preferences(self):
    from unittest.mock import patch
    with patch('openpilot.starpilot.lateral.learning_preferences.read_preference',
               side_effect=AssertionError('Offroad learner should not read settings')):
      assert not self.process.should_run(False, self.params, self.cp)


class TestLearningProjectionPreference(unittest.TestCase):
  def make_car(self, vehicle, preference=None, legacy=False):
    from openpilot.selfdrive.car.card import Car
    params = Params()
    params.put_bool('OpenpilotEnabledToggle', True, block=True)
    params.put_bool(LEARNING_LEGACY_KEY, legacy, block=True)
    cp = interfaces[vehicle].get_non_essential_params(vehicle)
    if preference is not None:
      raw = replace_preference(None, cp, preference) if type(preference) is bool else preference
      params.put(LEARNING_KEY, raw, block=True)
    ci = interfaces[vehicle](cp)
    return Car(CI=ci, RI=interfaces[vehicle].RadarInterface(cp))

  def test_explicit_off_skips_actual_factory_observer(self):
    for vehicle in VEHICLES:
      with self.subTest(vehicle=vehicle), OpenpilotPrefix():
        host = self.make_car(vehicle, False)
        assert host.learning_stamp_observer is None
        assert getattr(host.CI.CC, 'learning_projection_observer', None) is None
        assert host.learning_applied_evidence is None

  def test_default_on_keeps_actual_factory_observer(self):
    for vehicle in VEHICLES:
      with self.subTest(vehicle=vehicle), OpenpilotPrefix():
        host = self.make_car(vehicle)
        assert host.learning_stamp_observer is not None
        assert host.CI.CC.learning_projection_observer is not None

  def test_scoped_on_still_overrides_legacy_off(self):
    with OpenpilotPrefix():
      host = self.make_car(HKG.HYUNDAI_IONIQ_6, True, legacy=True)
      assert host.learning_stamp_observer is not None

  def test_legacy_off_and_invalid_preference_do_not_install(self):
    for preference, legacy in ((None, True), (b'not json', False)):
      with self.subTest(preference=preference, legacy=legacy), OpenpilotPrefix():
        host = self.make_car(HKG.HYUNDAI_IONIQ_6, preference, legacy)
        assert host.learning_stamp_observer is None
        assert getattr(host.CI.CC, 'learning_projection_observer', None) is None


class TestControlsLearningPreference(unittest.TestCase):
  def make_controls(self, vehicle, preference=None, legacy=False):
    from openpilot.selfdrive.controls.controlsd import Controls
    params = Params()
    cp = interfaces[vehicle].get_non_essential_params(vehicle)
    params.put('CarParams', cp.to_bytes(), block=True)
    params.put_bool('OpenpilotEnabledToggle', True, block=True)
    params.put_bool(LEARNING_LEGACY_KEY, legacy, block=True)
    if preference is not None:
      params.put(LEARNING_KEY, replace_preference(None, cp, preference) if type(preference) is bool else preference, block=True)
    before = (params.get(LEARNING_KEY), params.get(TORQUE_KEY))
    host = Controls()
    assert before == (params.get(LEARNING_KEY), params.get(TORQUE_KEY))
    return host

  def test_saved_off_skips_actual_controls_learning_runtime(self):
    from unittest.mock import patch
    from openpilot.selfdrive.controls import controlsd
    for vehicle in VEHICLES:
      with self.subTest(vehicle=vehicle), OpenpilotPrefix(), patch.object(controlsd, 'GlobalRuntime', wraps=controlsd.GlobalRuntime) as runtime:
        host = self.make_controls(vehicle, False)
        assert host.global_learning is None
        runtime.assert_not_called()

  def test_default_and_explicit_on_keep_actual_runtime(self):
    from unittest.mock import patch
    from openpilot.selfdrive.controls import controlsd
    for vehicle, preference in ((HKG.HYUNDAI_IONIQ_6, None), (GM.CHEVROLET_BOLT_CC_2018_2021, True),
                                (TOYOTA.TOYOTA_COROLLA_TSS2, True)):
      with self.subTest(vehicle=vehicle, preference=preference), OpenpilotPrefix(), \
           patch.object(controlsd, 'GlobalRuntime', wraps=controlsd.GlobalRuntime) as runtime:
        host = self.make_controls(vehicle, preference)
        assert host.global_learning is not None and type(host.global_learning).__name__ == 'GlobalRuntime'
        runtime.assert_called_once()

  def test_scoped_on_wins_over_legacy_off(self):
    with OpenpilotPrefix():
      host = self.make_controls(HKG.HYUNDAI_IONIQ_6, True, legacy=True)
      assert host.global_learning is not None

  def test_legacy_off_and_invalid_preference_skip_runtime(self):
    for preference, legacy in ((None, True), (b'not json', False)):
      with self.subTest(preference=preference, legacy=legacy), OpenpilotPrefix():
        host = self.make_controls(HKG.HYUNDAI_IONIQ_6, preference, legacy)
        assert host.global_learning is None
