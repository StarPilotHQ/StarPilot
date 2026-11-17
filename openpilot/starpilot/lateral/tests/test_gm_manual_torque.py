"""Exact finalized GM manual consumers; default controllers and nonlinear maps remain vehicle-owned."""
from dataclasses import replace
from pathlib import Path
import json
import os
import unittest
from unittest.mock import patch

from opendbc.car import gen_empty_fingerprint
from opendbc.car.vehicle_model import VehicleModel
from types import SimpleNamespace
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.values import CAR
from opendbc.car.gm.tests.test_ascm_intercept import params as intercept_params
from opendbc.car.gm.tests.test_ordinary_camera import params as camera_params
from opendbc.car.gm.tests.test_ordinary_cc import malibu_f1_params, malibu_hybrid_params
from opendbc.car.gm.tests.test_silverado_cc_pedal import params as silverado_params
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import ControllerMode, policy_for, replace_mode
from openpilot.starpilot.lateral.gain_runtime import create_gain_owner, gain_basis
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
from openpilot.starpilot.lateral.tests.test_torque_runtime import LearnedFrame
from openpilot.starpilot.lateral.tests.test_volt_torque_policy import params as volt_params
from openpilot.starpilot.lateral.torque_runtime import TorqueHost, factor_edit_supported, production_supported_cp, read_settings
from openpilot.starpilot.lateral.torque_settings import (DOCUMENT_KEY, bounds, gain_bounds, parse_document,
                                                       replace_field, replace_gain, serialize_document)
from openpilot.starpilot.lateral.torque_tuning import TorqueSource
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest


def finalized_cases():
  fp = gen_empty_fingerprint()
  fp[1][0x460] = 8
  yield 'suburban', CarInterface.get_params(CAR.CHEVROLET_SUBURBAN, fp, [], False, False, False)
  yield 'ordinary_ascm', intercept_params(CAR.CHEVROLET_MALIBU_ASCM)
  yield 'ordinary_ascm', intercept_params(CAR.BUICK_LACROSSE_ASCM_19US)
  yield 'ordinary_sdgm', intercept_params(CAR.CHEVROLET_MALIBU_SDGM)
  yield 'ordinary_sdgm', intercept_params(CAR.CADILLAC_XT4)
  yield 'ordinary_cc', malibu_f1_params()
  yield 'ordinary_cc', malibu_hybrid_params()
  yield 'ordinary_camera', camera_params(CAR.CHEVROLET_EQUINOX)
  yield 'ordinary_camera', camera_params(CAR.CHEVROLET_TRAX)
  yield 'ordinary_camera', camera_params(CAR.CHEVROLET_SILVERADO)
  yield 'silverado_cc', silverado_params()
  yield 'volt', volt_params(CAR.CHEVROLET_VOLT_CAMERA)


def stored(params, profiles):
  params.put(DOCUMENT_KEY, json.loads(serialize_document(profiles)), block=True)


class TestGmManualTorque(unittest.TestCase):
  def test_final_factory_admission_and_invalid_owners(self):
    for policy, cp in finalized_cases():
      self.assertEqual(policy_for(cp), policy, cp.carFingerprint)
      self.assertTrue(production_supported_cp(cp), cp.carFingerprint)
      for field, value in (('brand', 'toyota'), ('passive', True), ('dashcamOnly', True), ('notCar', True)):
        bad = cp.as_reader().as_builder()
        setattr(bad, field, value)
        self.assertFalse(production_supported_cp(bad), (cp.carFingerprint, field))
      bad = cp.as_reader().as_builder()
      bad.safetyConfigs[0].safetyParam = 0xffff
      self.assertFalse(production_supported_cp(bad), (cp.carFingerprint, 'invalid final owner'))
      bad = cp.as_reader().as_builder()
      bad.lateralTuning.init('pid')
      self.assertFalse(production_supported_cp(bad))
    pid_cp = intercept_params(CAR.GMC_ACADIA_ASCM)
    self.assertEqual(pid_cp.lateralTuning.which(), 'pid')
    self.assertFalse(production_supported_cp(pid_cp))

  def test_partial_factor_friction_precedence_offset_and_bad_document(self):
    for _, cp in finalized_cases():
      with OpenpilotPrefix():
        saved = Params()
        host = TorqueHost(saved, cp, allow_learning=True)
        base = host.vehicle
        basis = (base.lat_accel_factor, base.lat_accel_offset, base.friction)
        frame = LearnedFrame(1_000_000_000, factor=base.lat_accel_factor * 1.1,
                             offset=.02, friction=base.friction * 1.1)
        factor = replace_field({}, str(cp.carFingerprint), basis, 'factor', 'custom', basis[0] * 1.2)
        stored(saved, factor)
        host.settings = read_settings(saved, base, allow_learning=True)
        chosen = host._select(frame, 1_000_000_000)
        self.assertEqual(chosen.source, TorqueSource.USER)
        self.assertAlmostEqual(chosen.lat_accel_factor, basis[0] * 1.2)
        self.assertEqual(chosen.lat_accel_offset, basis[1])
        self.assertAlmostEqual(chosen.friction, frame.state.frictionCoefficientFiltered)
        friction = replace_field({}, str(cp.carFingerprint), basis, 'friction', 'custom', basis[2] * 1.2)
        stored(saved, friction)
        host.settings = read_settings(saved, base, allow_learning=True)
        chosen = host._select(frame, 1_000_000_000)
        self.assertAlmostEqual(chosen.lat_accel_factor, frame.state.latAccelFactorFiltered)
        self.assertAlmostEqual(chosen.lat_accel_offset, .02)
        self.assertAlmostEqual(chosen.friction, basis[2] * 1.2)
        changed = dict(friction)
        changed[str(cp.carFingerprint)] = replace(friction[str(cp.carFingerprint)], basis=(basis[0] * 1.01, basis[1], basis[2]))
        stored(saved, changed)
        host.settings = read_settings(saved, base, allow_learning=True)
        self.assertFalse(host.settings.valid)
        self.assertEqual(host._select(frame, 1_000_000_000), base)

  def test_actual_constructor_stale_unscoped_and_wrong_vehicle_settings_leave_defaults(self):
    cp=intercept_params(CAR.CHEVROLET_MALIBU_ASCM)
    other=volt_params(CAR.CHEVROLET_VOLT_CAMERA)
    tune=other.lateralTuning.torque
    other_profile=replace_field({},str(other.carFingerprint),(tune.latAccelFactor,tune.latAccelOffset,tune.friction),'friction','custom',.8)
    for mode in ControllerMode:
      for document in (None,b'{',serialize_document(other_profile)):
        with OpenpilotPrefix(),patch.dict(os.environ,{'SIMULATION':'1','REPLAY':'1','TORQUE_REPLAY_RUNTIME':'0'}):
          saved=Params()
          saved.put('CarParams',cp.as_reader().as_builder().to_bytes(),block=True)
          saved.put('LateralControllerSelection',json.loads(replace_mode(None,cp,mode)),block=True)
          baseline=Controls()
          saved.put('SteerLatAccel',cp.lateralTuning.torque.latAccelFactor*1.1,block=True)
          saved.put('SteerFriction',.8,block=True)
          saved.put_bool('AdvancedLateralTune',True,block=True)
          if document is not None:
            Path(saved.get_param_path(DOCUMENT_KEY)).write_bytes(document)
          keys=('AdvancedLateralTune','SteerLatAccel','SteerFriction',DOCUMENT_KEY,'LateralControllerSelection')
          before={key:Path(saved.get_param_path(key)).read_bytes() if Path(saved.get_param_path(key)).exists() else None for key in keys}
          controls=Controls()
          self.assertIsNone(controls.torque_host)
          self.assertIsNone(controls.lateral_gain_owner)
          self.assertEqual(controls.LaC.torque_params.to_dict(),baseline.LaC.torque_params.to_dict())
          self.assertEqual(controls.LaC.pid._k_p,baseline.LaC.pid._k_p)
          self.assertEqual(controls.CP.to_dict(),baseline.CP.to_dict())
          self.assertEqual(controls.torque_learning_allowed,baseline.torque_learning_allowed)
          self.assertEqual(controls.lateral_controller_selection.mode,mode)
          after={key:Path(saved.get_param_path(key)).read_bytes() if Path(saved.get_param_path(key)).exists() else None for key in keys}
          self.assertEqual(after,before)

  def test_restored_friction_range_and_canonical_intent_ignore_retired_global_flag(self):
    cp = intercept_params(CAR.CHEVROLET_MALIBU_ASCM)
    with OpenpilotPrefix():
      saved=Params()
      host=TorqueHost(saved,cp,allow_learning=False)
      basis=(host.vehicle.lat_accel_factor,host.vehicle.lat_accel_offset,host.vehicle.friction)
      self.assertEqual(bounds(basis,'friction',fingerprint=str(cp.carFingerprint)),(0.,1.))
      self.assertLess(bounds(basis,'friction',fingerprint='HYUNDAI_IONIQ_6')[1],1.)
      self.assertEqual(bounds(basis,'friction',fingerprint='CHEVROLET_BOLT_CC_2018_2021'),bounds(basis,'friction'))
      profiles=replace_field({},str(cp.carFingerprint),basis,'friction','custom',.8)
      lateral=LatControlTorque(cp.as_reader(),CarInterface(cp),.01)
      gain=gain_basis(cp,lateral)
      profiles=replace_gain(profiles,str(cp.carFingerprint),gain,'custom',.7)
      stored(saved,profiles)
      native_cp=cp.as_reader().as_builder().to_bytes()
      owner=None
      for legacy in (None,b'0',b'1',b'malformed'):
        lateral=LatControlTorque(cp.as_reader(),CarInterface(cp),.01)
        self.assertEqual(gain_basis(cp,lateral),gain)
        path=Path(saved.get_param_path('AdvancedLateralTune'))
        if legacy is None:
          path.unlink(missing_ok=True)
        else:
          path.write_bytes(legacy)
        settings=read_settings(saved,host.vehicle,allow_learning=False)
        self.assertTrue(settings.valid)
        self.assertEqual(settings.user_friction,.8)
        owner=create_gain_owner(saved,cp,lateral)
        self.assertIsNotNone(owner)
        self.assertEqual(lateral.pid._k_p,[[0.],[.7]])
        self.assertEqual(cp.as_reader().as_builder().to_bytes(),native_cp)
      raw=serialize_document(profiles)
      for invalid in (raw.replace(b'CHEVROLET_MALIBU_ASCM',b'UNKNOWN_GM'),b'{',raw.replace(b'0.8',b'NaN')):
        with self.assertRaises((ValueError,UnicodeError)):
          parse_document(invalid)
      Path(saved.get_param_path(DOCUMENT_KEY)).write_bytes(b'{')
      owner.refresh(now_ns=1_000_000_000)
      self.assertEqual(tuple(tuple(row) for row in lateral.pid._k_p),gain.source_table)
      self.assertEqual(Path(saved.get_param_path(DOCUMENT_KEY)).read_bytes(),b'{')

  def test_actual_controls_gain_only_preserves_torque_and_controller_learning(self):
    for _, cp in finalized_cases():
      for mode in ControllerMode:
        with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION':'1', 'REPLAY':'1', 'TORQUE_REPLAY_RUNTIME':'0'}):
          saved = Params()
          saved.put('CarParams', cp.as_reader().as_builder().to_bytes(), block=True)
          saved.put('LateralControllerSelection', json.loads(replace_mode(None, cp, mode)), block=True)
          baseline = Controls()
          self.assertIsNone(baseline.torque_host)
          self.assertIsNone(baseline.lateral_gain_owner)
          basis = gain_basis(cp, baseline.LaC)
          low, high = gain_bounds(str(cp.carFingerprint), basis)
          custom = low + .2 * (high - low)
          saved.put_bool('AdvancedLateralTune', False, block=True)
          stored(saved, replace_gain({}, str(cp.carFingerprint), basis, 'custom', custom))
          selected = Controls()
          self.assertIsNotNone(selected.lateral_gain_owner)
          self.assertIsNone(selected.torque_host)
          self.assertEqual(selected.torque_learning_allowed, baseline.torque_learning_allowed)
          self.assertEqual(selected.lateral_controller_selection.mode, mode)
          self.assertEqual(selected.LaC.torque_params.to_dict(), baseline.LaC.torque_params.to_dict())
          selected.LaC.pid.i = .123
          stored(saved, replace_gain({}, str(cp.carFingerprint), basis, 'source', None))
          selected.lateral_gain_owner.refresh(now_ns=1_000_000_000)
          self.assertEqual(tuple(tuple(row) for row in selected.LaC.pid._k_p), basis.source_table)
          self.assertEqual(selected.LaC.pid.i, .123)
          self.assertEqual(saved.get('LateralControllerSelection'), json.loads(replace_mode(None, cp, mode)))

  def test_actual_conversion_factor_effect_and_limits_follow_selected_law(self):
    for _, cp in finalized_cases():
      for mode in ControllerMode:
        lateral = LatControlTorque(cp.as_reader(), CarInterface(cp), .01, controller_mode=mode)
        tune = cp.lateralTuning.torque
        before = [float(lateral.torque_from_lateral_accel(value, lateral.torque_params)) for value in (-.8, .8)]
        limits = (lateral.pid.neg_limit, lateral.pid.pos_limit)
        lateral.update_torque_parameters(tune.latAccelFactor * 1.1, tune.latAccelOffset, tune.friction)
        after = [float(lateral.torque_from_lateral_accel(value, lateral.torque_params)) for value in (-.8, .8)]
        if factor_edit_supported(cp, mode):
          self.assertGreater(max(abs(a-b) for a,b in zip(after,before,strict=True)), 1e-6, (cp.carFingerprint, mode))
        else:
          self.assertEqual(after, before, (cp.carFingerprint, mode))
          self.assertEqual((lateral.pid.neg_limit, lateral.pid.pos_limit), limits)

  def test_actual_controls_partial_manual_gain_and_withdrawal_keep_preferences(self):
    cp = intercept_params(CAR.CHEVROLET_MALIBU_ASCM)
    for mode in ControllerMode:
      with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION':'1','REPLAY':'1','TORQUE_REPLAY_RUNTIME':'0'}):
        saved = Params()
        saved.put('CarParams', cp.as_reader().as_builder().to_bytes(), block=True)
        choice = json.loads(replace_mode(None, cp, mode))
        saved.put('LateralControllerSelection', choice, block=True)
        baseline = Controls()
        basis = gain_basis(cp, baseline.LaC)
        profiles = replace_field({}, str(cp.carFingerprint), basis.torque_basis, 'factor','custom',basis.torque_basis[0]*1.1)
        profiles = replace_field(profiles,str(cp.carFingerprint),basis.torque_basis,'friction','custom',basis.torque_basis[2]*1.2)
        profiles = replace_gain(profiles,str(cp.carFingerprint),basis,'custom',gain_bounds(str(cp.carFingerprint),basis)[0])
        saved.put_bool('AdvancedLateralTune', False, block=True)
        stored(saved, profiles)
        selected = Controls()
        self.assertIsNotNone(selected.torque_host)
        self.assertIsNotNone(selected.lateral_gain_owner)
        values=[]
        for tick in range(120):
          now=1_000_000_000+tick*10_000_000
          feed(selected,now,tick)
          with patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns',return_value=now+1000):
            command, _ = selected.state_control()
          values.append(command.actuators.torque)
        self.assertGreater(max(abs(value) for value in values), 1e-6)
        self.assertEqual(selected.torque_host.selected.source, TorqueSource.USER)
        self.assertEqual(selected.torque_host.selected.lat_accel_offset,basis.torque_basis[1])
        stored(saved, {})
        saved.put_bool('AdvancedLateralTune', False, block=True)
        feed(selected,3_000_000_000,200)
        with patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns',return_value=3_000_001_000):
          selected.state_control()
        self.assertEqual(selected.torque_host.selected,selected.torque_host.vehicle)
        self.assertEqual(tuple(tuple(row) for row in selected.LaC.pid._k_p),basis.source_table)
        self.assertEqual(saved.get('LateralControllerSelection'),choice)
        self.assertEqual(selected.lateral_controller_selection.mode,mode)

  def test_rich_fixed_maps_still_consume_manual_friction_and_gain(self):
    for cp in (volt_params(CAR.CHEVROLET_VOLT_CAMERA), intercept_params(CAR.CADILLAC_XT4),
               camera_params(CAR.CHEVROLET_TRAX), silverado_params()):
      with OpenpilotPrefix():
        saved = Params()
        baseline = LatControlTorque(cp.as_reader(), CarInterface(cp), .01, controller_mode=ControllerMode.STARPILOT)
        selected = LatControlTorque(cp.as_reader(), CarInterface(cp), .01, controller_mode=ControllerMode.STARPILOT)
        basis = gain_basis(cp, selected)
        profiles = replace_field({},str(cp.carFingerprint),basis.torque_basis,'friction','custom',basis.torque_basis[2]*1.2)
        profiles = replace_gain(profiles,str(cp.carFingerprint),basis,'custom',.7)
        saved.put_bool('AdvancedLateralTune',True,block=True)
        stored(saved,profiles)
        host=TorqueHost(saved,cp,allow_learning=False)
        host.settings=read_settings(saved,host.vehicle,allow_learning=False)
        chosen=host._select(LearnedFrame(1_000_000_000),1_000_000_000)
        self.assertEqual(chosen.source,TorqueSource.USER)
        self.assertTrue(host.apply(selected,chosen))
        self.assertIsNotNone(create_gain_owner(saved,cp,selected))
        cs=SimpleNamespace(vEgo=20.,steeringAngleDeg=0.,steeringPressed=False,steeringRateDeg=0.,standstill=False)
        live=SimpleNamespace(angleOffsetDeg=0.,roll=0.)
        vm=VehicleModel(cp)
        output=[]
        for _ in range(80):
          a,_,la=baseline.update(True,cs,vm,live,False,.0005,False,.2)
          b,_,lb=selected.update(True,cs,vm,live,False,.0005,False,.2)
          output.append(abs(float(a)-float(b)))
        self.assertGreater(abs(la.p-lb.p),1e-6,(cp.carFingerprint,'actual selected gain effect'))
        self.assertGreater(abs(la.f-lb.f),1e-6,(cp.carFingerprint,'actual selected friction effect'))
        self.assertGreater(max(output),1e-6)
        selected.update(False,cs,vm,live,False,.0005,False,.2)
        self.assertEqual(selected.pid.i,0.)

  def test_ui_fixed_conversion_is_honest_and_controller_change_stales_factor_edit(self):
    cp = volt_params(CAR.CHEVROLET_VOLT_CAMERA)
    with OpenpilotPrefix():
      saved=Params()
      saved.put('CarParams',cp.as_reader().as_builder().to_bytes(),block=True)
      saved.put_bool('AdvancedLateralTune',True,block=True)
      owner=FeatureSettingsOwner(saved,lambda _:True,vehicle_fingerprint=lambda:cp.carFingerprint,vehicle_params=lambda:cp)
      def rows():
        return {r.key:r for r in owner.snapshot('torque/developer',parked=True,system_long=True,lateral_context=True,metric=False).rows}
      factor=rows()['torque:factor:value']
      self.assertFalse(factor.available)
      self.assertIn('fixed torque conversion',factor.reason)
      self.assertTrue(rows()['torque:friction:value'].available)
      self.assertIn('torque:gain:mode',rows())
      saved.put('LateralControllerSelection',json.loads(replace_mode(None,cp,ControllerMode.STANDARD)),block=True)
      factor=rows()['torque:factor:value']
      self.assertTrue(factor.available)
      request=FeatureSettingsRequest(factor.key,factor.source,str(cp.lateralTuning.torque.latAccelFactor*1.1),vehicle_fingerprint=cp.carFingerprint,capability=factor.capability,dependencies=factor.dependencies)
      saved.put('LateralControllerSelection',json.loads(replace_mode(None,cp,ControllerMode.STARPILOT)),block=True)
      self.assertFalse(owner.apply(request))
