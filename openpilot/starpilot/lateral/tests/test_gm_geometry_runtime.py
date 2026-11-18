"""Canonical geometry and original ForceAuto precedence at actual producer/consumer seams."""
from dataclasses import replace
import json
from pathlib import Path
import unittest

from opendbc.car.gm.values import CAR
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.locationd.paramsd import VehicleParamsLearner
from openpilot.selfdrive.locationd.lagd import LateralLagEstimator
from openpilot.selfdrive.locationd.torqued import TorqueEstimator
from openpilot.starpilot.lateral.controller_selection import ControllerMode, learning_allowed, replace_mode
from openpilot.starpilot.lateral.gm_geometry_runtime import GeometryPublicationOwner, geometry_basis, read_geometry
from openpilot.starpilot.lateral.tests.test_gm_manual_torque import finalized_cases, stored
from openpilot.starpilot.lateral.tests.test_torque_runtime import LearnedFrame
from openpilot.starpilot.lateral.tests.test_volt_torque_policy import params as volt_params
from openpilot.starpilot.lateral.torque_runtime import TorqueHost, read_settings, runtime_enabled
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, FieldChoice, parse_document, replace_field, replace_geometry, serialize_document
from openpilot.starpilot.lateral.torque_tuning import TorqueSource
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest


def profile(cp, learning='source'):
  tune = cp.lateralTuning.torque
  basis = (tune.latAccelFactor, tune.latAccelOffset, tune.friction)
  return replace_geometry({}, str(cp.carFingerprint), basis, geometry_basis(cp), 'learning', learning)


def edit(cp, profiles, field, value):
  tune = cp.lateralTuning.torque
  return replace_geometry(profiles, str(cp.carFingerprint), (tune.latAccelFactor, tune.latAccelOffset, tune.friction),
                          geometry_basis(cp), field, value)


class TestGmGeometryRuntime(unittest.TestCase):
  def test_document_backward_compatibility_exact_basis_and_rejection(self):
    cp = volt_params(CAR.CHEVROLET_VOLT_CAMERA)
    profiles = profile(cp)
    identity = str(cp.carFingerprint)
    raw = serialize_document(profiles)
    self.assertEqual(parse_document(raw), profiles)
    self.assertEqual(json.loads(raw)['schemaVersion'], 3)
    old = {identity: replace(profiles[identity], geometry=None)}
    self.assertEqual(json.loads(serialize_document(old))['schemaVersion'], 1)
    self.assertEqual(parse_document(serialize_document(old)), old)
    for field, value in [('ratio', FieldChoice('custom', cp.steerRatio*1.51)),
                         ('ratio', FieldChoice('custom', float('nan'))),
                         ('full_delay', FieldChoice('custom', 1.01)), ('automatic_delay', 1), ('learning', 'unknown')]:
      with self.assertRaises(ValueError):
        edit(cp, profiles, field, value)
    with OpenpilotPrefix():
      saved = Params()
      stored(saved, profiles)
      self.assertIsNotNone(read_geometry(saved, cp).profile)
      bad = cp.as_reader().as_builder()
      bad.steerActuatorDelay += .01
      self.assertIsNone(read_geometry(saved, bad).profile)
      bad = cp.as_reader().as_builder()
      bad.safetyConfigs[0].safetyParam = 0xffff
      self.assertIsNone(read_geometry(saved, bad).profile)
      for raw in (b'{', json.dumps({'schemaVersion':3,'vehicles':{}}).encode()):
        Path(saved.get_param_path(DOCUMENT_KEY)).write_bytes(raw)
        self.assertIsNone(read_geometry(saved, cp).profile)
        self.assertEqual(Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes(), raw)

  def test_force_auto_admits_default_custom_and_standard_only_on_exact_intent(self):
    for policy, cp in finalized_cases():
      with OpenpilotPrefix():
        saved = Params()
        saved.put('CarParams', cp.to_bytes(), block=True)
        default = Controls()
        self.assertFalse(default.torque_learning_allowed, (policy, 'default unchanged'))
        self.assertIsNone(default.torque_host)
        profiles = profile(cp, 'force_auto')
        stored(saved, profiles)
        for mode in ControllerMode:
          saved.put('LateralControllerSelection', json.loads(replace_mode(None,cp,mode)), block=True)
          self.assertTrue(learning_allowed(saved, cp), (policy, mode))
          controls = Controls()
          self.assertEqual(controls.lateral_controller_selection.mode, mode)
          self.assertTrue(controls.torque_learning_allowed)
          self.assertIsNotNone(controls.torque_host)
          estimator = TorqueEstimator(cp.as_reader(), allow_learning=learning_allowed(saved, cp))
          self.assertTrue(estimator.learning_allowed)
          self.assertFalse(estimator.use_params, 'brand eligibility semantics remain unchanged')
          self.assertFalse(estimator.get_msg().lateralTorqueParameters.valid, 'empty fit is never fabricated valid')
        saved.put_bool('ForceAutoTuneOff', True, block=True)
        self.assertFalse(learning_allowed(saved, cp))
        self.assertFalse(read_geometry(saved, cp).force_auto)
        self.assertFalse(runtime_enabled(cp, saved))
        saved.put_bool('ForceAutoTuneOff', False, block=True)
        stored(saved, edit(cp, profiles, 'learning', 'force_off'))
        self.assertFalse(learning_allowed(saved, cp))

  def test_force_auto_validity_and_original_custom_suppression_resume(self):
    cp = volt_params(CAR.CHEVROLET_VOLT_CAMERA)
    with OpenpilotPrefix():
      saved = Params()
      host = TorqueHost(saved, cp, allow_learning=True)
      vehicle = host.vehicle
      profiles = replace_field(profile(cp,'force_auto'), str(cp.carFingerprint),
                               (vehicle.lat_accel_factor,vehicle.lat_accel_offset,vehicle.friction), 'friction','custom',vehicle.friction*1.2)
      stored(saved, profiles)
      now = 1_000_000_000
      frame = LearnedFrame(now, factor=vehicle.lat_accel_factor*1.1, offset=.02, friction=vehicle.friction*1.1)
      frame.state.useParams = False
      host.settings = read_settings(saved, vehicle, allow_learning=True, CP=cp)
      self.assertTrue(host.settings.force_auto)
      self.assertIsNone(host.settings.user_friction)
      self.assertEqual(host._select(frame, now).source, TorqueSource.LEARNED)
      for attribute, value in [('valid',False),('version',2),('latAccelFactorFiltered',float('nan')),
                               ('latAccelFactorFiltered',vehicle.lat_accel_factor*2),('latAccelOffsetFiltered',1.),
                               ('frictionCoefficientFiltered',vehicle.friction*2)]:
        good = getattr(frame.state, attribute)
        setattr(frame.state, attribute, value)
        self.assertIsNone(host._learned(frame, now), attribute)
        setattr(frame.state, attribute, good)
      frame.checked = False
      self.assertIsNone(host._learned(frame, now))
      frame.checked = True
      for stamp in (0, now+1, now-3_000_000_000):
        frame.logMonoTime['lateralTorqueParameters'] = stamp
        self.assertIsNone(host._learned(frame, now))
      frame.logMonoTime['lateralTorqueParameters'] = now
      source = edit(cp, profiles, 'learning', 'source')
      stored(saved, source)
      host.settings = read_settings(saved, vehicle, allow_learning=True, CP=cp)
      self.assertFalse(host.settings.force_auto)
      self.assertEqual(host._select(frame, now).source, TorqueSource.USER)
      self.assertAlmostEqual(host._select(frame, now).friction, vehicle.friction*1.2)
      self.assertEqual(parse_document(Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes())[str(cp.carFingerprint)].friction,
                       profiles[str(cp.carFingerprint)].friction)
      stored(saved, edit(cp, profiles, 'learning', 'force_off'))
      host.settings = read_settings(saved, vehicle, allow_learning=True, CP=cp)
      self.assertTrue(host.settings.force_auto_off)
      self.assertEqual(host._select(frame, now).source, TorqueSource.USER)

  def test_actual_paramsd_and_lagd_project_copy_preserve_estimator_cache_and_validity(self):
    cp = volt_params(CAR.CHEVROLET_VOLT_CAMERA)
    with OpenpilotPrefix():
      saved = Params()
      ratio = cp.steerRatio*1.1
      profiles = edit(cp, profile(cp), 'ratio', FieldChoice('custom',ratio))
      profiles = edit(cp, profiles, 'full_delay', FieldChoice('custom',.7))
      profiles = edit(cp, profiles, 'automatic_delay', False)
      stored(saved, profiles)
      owner = GeometryPublicationOwner(saved, cp)
      learner = VehicleParamsLearner(cp.as_reader(), cp.steerRatio, .8, 0.)
      original = learner.get_msg(False, debug=True)
      lag = LateralLagEstimator(cp.as_reader(), .05)
      delay = lag.get_msg(False, debug=True)
      raw, lag_raw = original.to_bytes(), delay.to_bytes()
      state = learner.kf.x.copy()
      p = learner.kf.P.copy()
      projected = owner.parameters(original)
      delayed = owner.delay(delay)
      self.assertAlmostEqual(projected.vehicleParameters.steerRatio,ratio,delta=1e-5)
      self.assertEqual(projected.vehicleParameters.stiffnessFactor, original.vehicleParameters.stiffnessFactor)
      self.assertAlmostEqual(delayed.lateralDelay.lateralDelay,.7)
      self.assertFalse(projected.valid or delayed.valid)
      expected = original.to_dict()
      expected['vehicleParameters']['steerRatio'] = projected.vehicleParameters.steerRatio
      self.assertEqual(projected.to_dict(),expected)
      expected_delay = delay.to_dict()
      expected_delay['lateralDelay']['lateralDelay'] = delayed.lateralDelay.lateralDelay
      self.assertEqual(delayed.to_dict(),expected_delay)
      self.assertEqual(original.as_reader().as_builder().to_bytes(), raw)
      self.assertEqual(delay.as_reader().as_builder().to_bytes(), lag_raw)
      self.assertTrue((learner.kf.x==state).all() and (learner.kf.P==p).all())
      self.assertAlmostEqual(lag.initial_lag, cp.steerActuatorDelay+.2)
      stored(saved, edit(cp,profiles,'learning','force_auto'))
      owner.last_read_ns = owner.delay_read_ns = None
      self.assertIs(owner.parameters(original),original,'ForceAuto suppresses custom ratio')
      self.assertAlmostEqual(owner.delay(delay).lateralDelay.lateralDelay,.7,msg='full delay independent')
      stored(saved, edit(cp,profiles,'learning','force_off'))
      owner.last_read_ns = owner.delay_read_ns = None
      off = owner.parameters(original)
      self.assertAlmostEqual(off.vehicleParameters.steerRatio,ratio,delta=1e-5)
      self.assertEqual(off.vehicleParameters.stiffnessFactor,1.)
      reset = edit(cp,profiles,'ratio',FieldChoice('source',ratio))
      reset = edit(cp,reset,'full_delay',FieldChoice('source',.7))
      stored(saved, reset)
      owner.last_read_ns = owner.delay_read_ns = None
      self.assertIs(owner.parameters(original),original)
      self.assertAlmostEqual(owner.delay(delay).lateralDelay.lateralDelay,cp.steerActuatorDelay+.2)
      self.assertEqual(original.as_reader().as_builder().to_bytes(),raw)
      self.assertEqual(delay.as_reader().as_builder().to_bytes(),lag_raw)

  def test_ui_guarded_geometry_choices_and_source_staleness(self):
    cp = volt_params(CAR.CHEVROLET_VOLT_CAMERA)
    with OpenpilotPrefix():
      saved=Params()
      owner=FeatureSettingsOwner(saved,lambda _:True,vehicle_fingerprint=lambda:cp.carFingerprint,vehicle_params=lambda:cp)
      def rows():
        return {row.key:row for row in owner.snapshot('torque/developer',parked=True,system_long=True,lateral_context=True,metric=False).rows}
      for learning in ('source', 'force_auto', 'force_off'):
        stored(saved, profile(cp, learning))
        before = Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes()
        self.assertNotIn('torque:learning:value', rows())
        self.assertEqual(Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes(), before)
      stored(saved, profile(cp, 'force_auto'))
      self.assertTrue(read_geometry(saved,cp).force_auto)
      ratio=rows()['torque:ratio:value']
      self.assertIn('temporarily ignored',ratio.reason)
      self.assertEqual(Path(saved.get_param_path('LateralControllerSelection')).exists(),False)
      request=FeatureSettingsRequest(ratio.key,ratio.source,str(cp.steerRatio*1.1),vehicle_fingerprint=cp.carFingerprint,
                                    capability=ratio.capability,dependencies=ratio.dependencies,related_source=ratio.related_source)
      changed=cp.as_reader().as_builder()
      changed.steerRatio+=.1
      owner.vehicle_params=lambda:changed
      self.assertFalse(owner.apply(request))

  def test_ui_geometry_custom_source_roundtrip_and_narrow_stale_basis_reset(self):
    from openpilot.cereal import messaging
    from openpilot.starpilot.lateral.gain_runtime import gain_basis
    from openpilot.starpilot.lateral.torque_settings import replace_gain
    from openpilot.starpilot.ui.feature_settings_state import row_default

    cp = volt_params(CAR.CHEVROLET_VOLT_CAMERA)
    identity = str(cp.carFingerprint)
    tune = cp.lateralTuning.torque
    torque_basis = (tune.latAccelFactor, tune.latAccelOffset, tune.friction)
    with OpenpilotPrefix():
      saved = Params()
      saved.put('CarParams', cp.to_bytes(), block=True)
      controller = Controls()
      profiles = replace_field({}, identity, torque_basis, 'factor', 'custom', torque_basis[0] * 1.1)
      profiles = replace_field(profiles, identity, torque_basis, 'friction', 'custom', torque_basis[2] * 1.1)
      profiles = replace_gain(profiles, identity, gain_basis(cp, controller.LaC), 'custom', .7)
      stored(saved, profiles)
      original_manual = profiles[identity]
      owner = FeatureSettingsOwner(saved, lambda _: True, vehicle_fingerprint=lambda: cp.carFingerprint,
                                   vehicle_params=lambda: cp)

      def rows():
        return {row.key: row for row in owner.snapshot('torque/developer', parked=True, system_long=True,
                                                       lateral_context=True, metric=False).rows}

      def apply_value(key, value):
        row = rows()[key]
        request = FeatureSettingsRequest(row.key, row.source, value, vehicle_fingerprint=cp.carFingerprint,
                                         capability=row.capability, dependencies=row.dependencies,
                                         related_source=row.related_source)
        self.assertTrue(owner.apply(request), key)

      ratio = cp.steerRatio * 1.1
      apply_value('torque:ratio:value', str(ratio))
      apply_value('steering_delay', '.7')
      stored_profile = parse_document(Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes())[identity]
      self.assertEqual(stored_profile.geometry.ratio, FieldChoice('custom', ratio))
      self.assertEqual(stored_profile.geometry.full_delay, FieldChoice())
      self.assertTrue(stored_profile.geometry.automatic_delay)
      parameters = messaging.new_message('vehicleParameters', valid=True)
      parameters.vehicleParameters.steerRatio = cp.steerRatio
      delay = messaging.new_message('lateralDelay', valid=True)
      delay.lateralDelay.lateralDelay = .6
      publisher = GeometryPublicationOwner(saved, cp)
      self.assertAlmostEqual(publisher.parameters(parameters).vehicleParameters.steerRatio, ratio, delta=1e-5)
      self.assertAlmostEqual(publisher.delay(delay).lateralDelay.lateralDelay, .7, delta=1e-5)
      for field in ('ratio', 'delay'):
        row = rows()['steering_delay' if field == 'delay' else 'torque:ratio:value']
        request = row_default(row)
        self.assertIsNotNone(request, field)
        assert request is not None
        self.assertEqual(request.key, row.default_key)
        self.assertTrue(owner.apply(request), field)
      publisher.last_read_ns = publisher.delay_read_ns = None
      self.assertIs(publisher.parameters(parameters), parameters, 'source ratio withdraws custom projection')
      self.assertIs(publisher.delay(delay), delay, 'reset restores the actual learned publication')
      source = parse_document(Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes())[identity]
      self.assertEqual(source.geometry.ratio, FieldChoice('source', ratio))
      self.assertEqual(source.geometry.full_delay, FieldChoice())
      self.assertTrue(source.geometry.automatic_delay)
      apply_value('torque:ratio:value', str(ratio))
      apply_value('steering_delay', '.7')
      before = parse_document(Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes())
      changed = cp.as_reader().as_builder()
      changed.steerActuatorDelay += .01
      owner.vehicle_params = lambda: changed
      self.assertIsNone(read_geometry(saved, changed).profile, 'changed basis pauses saved geometry')
      stale_rows = rows()
      self.assertNotIn('torque:ratio:value', stale_rows)
      reset = stale_rows['torque:geometry_reset:value']
      self.assertTrue(owner.apply(FeatureSettingsRequest(reset.key, reset.source, 'Reset',
                         vehicle_fingerprint=cp.carFingerprint, capability=reset.capability,
                         dependencies=reset.dependencies, related_source=reset.related_source)))
      after = parse_document(Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes())
      self.assertEqual(after, {**before, identity: replace(before[identity], geometry=None)})
      self.assertEqual(after[identity], original_manual, 'narrow reset retains factor, friction and KP basis/value')
      self.assertFalse(Path(saved.get_param_path('LateralControllerSelection')).exists())
      self.assertFalse(Path(saved.get_param_path('AdvancedLateralTune')).exists())

  def test_absent_geometry_preserves_ioniq_and_five_bolt_consumers(self):
    from opendbc.car.car_helpers import interfaces
    from opendbc.car.hyundai.values import CAR as HYUNDAI
    from opendbc.car.gm.tests.test_bolt_cc import params as bolt_params
    from openpilot.starpilot.lateral.torque_supported import BOLT_VEHICLES
    from openpilot.cereal import messaging
    cps = [interfaces[HYUNDAI.HYUNDAI_IONIQ_6].get_non_essential_params(HYUNDAI.HYUNDAI_IONIQ_6)]
    cps += [bolt_params(identity,alpha=True) for identity in sorted(BOLT_VEHICLES)]
    for cp in cps:
      with OpenpilotPrefix():
        saved = Params()
        tune = cp.lateralTuning.torque
        document = replace_field({},str(cp.carFingerprint),(tune.latAccelFactor,tune.latAccelOffset,tune.friction),
                                 'friction','custom',tune.friction*1.1)
        raw = serialize_document(document)
        stored(saved,document)
        saved_raw=Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes()
        saved.put_bool('ForceAutoTuneOff',True,block=True)
        owner = GeometryPublicationOwner(saved,cp)
        parameters=messaging.new_message('vehicleParameters',valid=False)
        parameters.vehicleParameters.steerRatio=cp.steerRatio*1.01
        parameters.vehicleParameters.stiffnessFactor=.8
        delay=messaging.new_message('lateralDelay',valid=False)
        delay.lateralDelay.lateralDelay=.6
        self.assertIs(owner.parameters(parameters),parameters)
        self.assertIs(owner.delay(delay),delay)
        self.assertFalse(read_geometry(saved,cp).force_auto)
        self.assertEqual(serialize_document(parse_document(raw)),raw)
        self.assertEqual(Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes(),saved_raw)
