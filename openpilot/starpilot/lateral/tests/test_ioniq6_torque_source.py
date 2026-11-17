"""Actual Ioniq 6 CP through the optional saved torque source and controller."""

import os
import json
import struct
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from opendbc.car.car_helpers import interfaces
from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.ioniq6_handoff import build_ioniq6_hda2_long_candidate
from opendbc.car.hyundai.values import CAR as HYUNDAI
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.locationd.torqued import TorqueEstimator
from openpilot.starpilot.lateral.torque_runtime import TorqueHost, development_enabled, read_settings, runtime_enabled, supported_cp
from openpilot.starpilot.lateral.torque_settings import FieldChoice, PlatformProfile, bounds, serialize_document
from openpilot.starpilot.lateral.torque_tuning import TorqueSource
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change


def cp_for(car):
  return interfaces[car].get_non_essential_params(car)


def ioniq_long_candidate(*, alternate=False):
  fingerprint = gen_empty_fingerprint()
  fingerprint[2].update({0x110: 32, 0x362: 32} if alternate else {0x50: 16, 0x2A4: 24})
  fingerprint[1].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                         0x1BA: 24, 0x1E5: 16, 0x36A: 16})
  fingerprint[0][0x3A5] = 24
  fingerprint[0][0x100] = 24
  stock = CarInterface.get_params(HYUNDAI.HYUNDAI_IONIQ_6, fingerprint, [], False, False, False)
  return stock, build_ioniq6_hda2_long_candidate(stock, fingerprint)


class LearnedFrame:
  def __init__(self, now_ns, *, factor, offset, friction):
    self.logMonoTime = {'lateralTorqueParameters': now_ns}
    self.recv_time = {'lateralTorqueParameters': now_ns / 1e9}
    self.state = SimpleNamespace(useParams=True, valid=True, version=1, latAccelFactorFiltered=factor,
                                 latAccelOffsetFiltered=offset, frictionCoefficientFiltered=friction)

  def all_checks(self, _names):
    return True

  def __getitem__(self, _name):
    return self.state


class Ioniq6TorqueSourceTests(unittest.TestCase):
  def test_normal_startup_requires_exact_ioniq_and_valid_saved_opt_in(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'TORQUE_REPLAY_RUNTIME': '0'}):
      params = Params()
      for alternate in (False, True):
        with self.subTest(alternate=alternate):
          stock, tagged = ioniq_long_candidate(alternate=alternate)
          self.assertFalse(runtime_enabled(stock, params))
          self.assertFalse(runtime_enabled(tagged, params))
          params.put_bool('AdvancedLateralTune', True, block=True)
          # Global legacy scalars apply across supported torque vehicles. Use
          # the per-vehicle document to test this Ioniq-only saved selection.
          tune = tagged.lateralTuning.torque
          basis = (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
          profile = PlatformProfile(basis, FieldChoice('source'), FieldChoice('custom', basis[2] * 1.1))
          params.put('TorqueOverrideDocument', json.loads(serialize_document({str(tagged.carFingerprint): profile})), block=True)
          self.assertTrue(runtime_enabled(tagged, params))
          self.assertTrue(runtime_enabled(stock, params))
          self.assertFalse(runtime_enabled(cp_for(TOYOTA.TOYOTA_COROLLA_TSS2), params))
          stock.brand = 'toyota'
          self.assertFalse(runtime_enabled(stock, params))
          params.put_bool('AdvancedLateralTune', False, block=True)
          params.remove('TorqueOverrideDocument')
      _, tagged = ioniq_long_candidate()
      params.put_bool('AdvancedLateralTune', True, block=True)
      Path(params.get_param_path('SteerFriction')).write_bytes(b'nan')
      self.assertFalse(runtime_enabled(tagged, params))

  def test_saved_startup_connects_stock_and_tagged_controls_and_skips_old_learner_cache(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'TORQUE_REPLAY_RUNTIME': '0', 'REPLAY': '1',
                                                         'AOL_REPLAY_RUNTIME': '0', 'LANE_CENTERING_REPLAY_RUNTIME': '0'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'), \
         mock.patch('openpilot.selfdrive.locationd.torqued.get_cache', return_value=None) as get_cache:
      params = Params()
      stock, tagged = ioniq_long_candidate()
      params.put_bool('AdvancedLateralTune', True, block=True)
      basis = (float(stock.lateralTuning.torque.latAccelFactor), float(stock.lateralTuning.torque.latAccelOffset),
               float(stock.lateralTuning.torque.friction))
      profile = PlatformProfile(basis, FieldChoice('custom', 3.3), FieldChoice('custom', 0.12))
      params.put('TorqueOverrideDocument', json.loads(serialize_document({str(stock.carFingerprint): profile})), block=True)
      for cp in (stock, tagged):
        with self.subTest(long=cp.openpilotLongitudinalControl):
          params.put('CarParams', cp.as_reader().as_builder().to_bytes(), block=True)
          selected = Controls()
          self.assertIsNotNone(selected.torque_host)
          learner = LearnedFrame(1_000_000_000, factor=3.15, offset=0.03, friction=0.10)
          tune = selected.torque_host.sample(learner, now_ns=1_000_000_000, lat_active=True)
          self.assertEqual(selected.torque_host.selected.source, TorqueSource.USER)
          self.assertEqual(selected.torque_host.selected.upstream_update(), (3.3, 0.0, 0.12))
          self.assertTrue(selected.torque_host.apply(selected.LaC, tune))
          expected_factor = struct.unpack('f', struct.pack('f', tune.lat_accel_factor * 1.22))[0]
          self.assertEqual(selected.LaC.torque_params.latAccelFactor, expected_factor)
          TorqueEstimator(cp.as_reader())
          self.assertEqual(get_cache.call_count, 0)
      params.put_bool('AdvancedLateralTune', False, block=True)
      params.remove('TorqueOverrideDocument')
      params.put('CarParams', stock.as_reader().as_builder().to_bytes(), block=True)
      baseline = Controls()
      self.assertIsNone(baseline.torque_host)
      TorqueEstimator(stock.as_reader())
      self.assertEqual(get_cache.call_count, 2)

  def test_exact_cp_and_development_gate(self):
    cp = cp_for(HYUNDAI.HYUNDAI_IONIQ_6)
    self.assertEqual(cp.lateralTuning.torque.latAccelFactor, 3.0)
    self.assertAlmostEqual(cp.lateralTuning.torque.friction, 0.09)
    self.assertTrue(supported_cp(cp))
    with mock.patch.dict(os.environ, {'TORQUE_REPLAY_RUNTIME': '0'}):
      self.assertFalse(development_enabled(cp))
    with mock.patch.dict(os.environ, {'TORQUE_REPLAY_RUNTIME': '1'}):
      self.assertTrue(development_enabled(cp))
      self.assertTrue(development_enabled(cp_for(HYUNDAI.HYUNDAI_IONIQ_5)))
      angle = cp_for(HYUNDAI.HYUNDAI_IONIQ_5).as_reader().as_builder()
      angle.steerControlType = 'angle'
      self.assertFalse(development_enabled(angle))
      self.assertTrue(development_enabled(cp_for(TOYOTA.TOYOTA_COROLLA_TSS2)))
      cp.brand = 'toyota'
      self.assertFalse(development_enabled(cp))

  def test_vehicle_learned_custom_force_off_and_once_only_controller_shaping(self):
    with OpenpilotPrefix():
      cp = cp_for(HYUNDAI.HYUNDAI_IONIQ_6)
      params = Params()
      host = TorqueHost(params, cp)
      basis = (host.vehicle.lat_accel_factor, host.vehicle.lat_accel_offset, host.vehicle.friction)
      self.assertEqual(basis[0], 3.0)
      self.assertAlmostEqual(basis[2], 0.09)
      self.assertEqual(bounds(basis, 'friction')[1], 2 * basis[2])
      now = 1_000_000_000
      learner = LearnedFrame(now, factor=3.15, offset=0.03, friction=0.10)
      learner.logMonoTime['lateralTorqueParameters'] = 1
      self.assertEqual(host.sample(learner, now_ns=now, lat_active=True), host.vehicle)
      learner.logMonoTime['lateralTorqueParameters'] = now
      host.settings = read_settings(params, host.vehicle)
      self.assertEqual(host._select(learner, now).source, TorqueSource.LEARNED)
      self.assertEqual(host._select(learner, now).lat_accel_factor, 3.15)
      params.put_bool('AdvancedLateralTune', True, block=True)
      profile = PlatformProfile(basis, FieldChoice('custom', 3.3), FieldChoice('custom', 0.12))
      params.put('TorqueOverrideDocument', json.loads(serialize_document({str(cp.carFingerprint): profile})), block=True)
      host.settings = read_settings(params, host.vehicle)
      selected = host._select(learner, now)
      self.assertEqual(selected.source, TorqueSource.USER)
      self.assertEqual(selected.upstream_update(), (3.3, 0.0, 0.12))
      controller = LatControlTorque(cp.as_reader(), interfaces[HYUNDAI.HYUNDAI_IONIQ_6](cp), DT_CTRL)
      for _ in range(3):
        controller.update_torque_parameters(*selected.upstream_update())
        self.assertAlmostEqual(controller.torque_params.latAccelFactor, 3.3 * 1.22)
        self.assertAlmostEqual(controller.torque_params.friction, 0.12)
      params.put_bool('ForceAutoTuneOff', True, block=True)
      host.settings = read_settings(params, host.vehicle)
      self.assertEqual(host._select(learner, now).source, TorqueSource.USER)
      source_profile = PlatformProfile(basis, FieldChoice(), FieldChoice())
      params.put('TorqueOverrideDocument', json.loads(serialize_document({str(cp.carFingerprint): source_profile})), block=True)
      host.settings = read_settings(params, host.vehicle)
      self.assertEqual(host._select(learner, now), host.vehicle)
      params.put_bool('ForceAutoTuneOff', False, block=True)
      host.settings = read_settings(params, host.vehicle)
      self.assertEqual(host._select(learner, now).source, TorqueSource.LEARNED)
      learner.logMonoTime['lateralTorqueParameters'] = 1
      self.assertEqual(host._select(learner, now), host.vehicle)

  def test_strict_legacy_friction_rejects_without_rewriting(self):
    with OpenpilotPrefix():
      cp = cp_for(HYUNDAI.HYUNDAI_IONIQ_6)
      params = Params()
      host = TorqueHost(params, cp)
      params.put_bool('AdvancedLateralTune', True, block=True)
      params.put('SteerFriction', 0.3, block=True)
      self.assertFalse(read_settings(params, host.vehicle).valid)
      self.assertEqual(params.get('SteerFriction'), 0.3)

  def test_native_owner_qualified_action_and_stale_vehicle_rejection(self):
    with OpenpilotPrefix():
      params = Params()
      cp = cp_for(HYUNDAI.HYUNDAI_IONIQ_6)
      owner = FeatureSettingsOwner(params, lambda _: True, vehicle_fingerprint=lambda: str(cp.carFingerprint),
                                   vehicle_params=lambda: cp)
      page = owner.snapshot('torque/developer', parked=True, system_long=False, lateral_context=True, metric=False)
      friction = next(row for row in page.rows if row.key == 'torque:friction:value')
      self.assertTrue(friction.available)
      request = row_change(friction)
      if request is None:
        self.fail('Ioniq 6 friction row did not produce an action')
      cp.carFingerprint = HYUNDAI.HYUNDAI_IONIQ_5
      self.assertFalse(owner.apply(request))
      self.assertIsNone(params.get('TorqueOverrideDocument'))
      cp.carFingerprint = HYUNDAI.HYUNDAI_IONIQ_6
      self.assertTrue(owner.apply(request))
      self.assertTrue(read_settings(params, TorqueHost(params, cp).vehicle).valid)
      self.assertIsNotNone(params.get('TorqueOverrideDocument'))


if __name__ == '__main__':
  unittest.main()
