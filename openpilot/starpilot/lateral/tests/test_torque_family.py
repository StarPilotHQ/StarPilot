"""Real Toyota CarParams and native controller coverage for the replay-only selector."""

import math
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.toyota.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.tests.test_torque_tuning import frame
from openpilot.starpilot.lateral.torque_runtime import TorqueHost, development_enabled, read_settings, supported_cp
from openpilot.starpilot.lateral.torque_supported import TOYOTA_VEHICLES
from openpilot.starpilot.lateral.torque_tuning import TorqueSource


def params_for(car_id):
  return interfaces[car_id].get_non_essential_params(car_id)


def learner(now_ns, base):
  state = SimpleNamespace(useParams=True, valid=True, version=1, latAccelFactorFiltered=base.lat_accel_factor * 1.1,
                          latAccelOffsetFiltered=0.1, frictionCoefficientFiltered=base.friction * 1.1)

  class Sample:
    logMonoTime = {'lateralTorqueParameters': now_ns}
    recv_time = {'lateralTorqueParameters': now_ns / 1e9}

    def all_checks(self, _names):
      return True

    def __getitem__(self, _name):
      return state

  return Sample()


class ToyotaTorqueFamilyTests(unittest.TestCase):
  def test_exact_registry_cohort_and_cp_guard(self):
    registry = {str(car.value) for car in CAR if (cp := params_for(str(car.value))).brand == 'toyota' and
                cp.steerControlType == structs.CarParams.SteerControlType.torque and cp.lateralTuning.which() == 'torque'}
    self.assertEqual(len(TOYOTA_VEHICLES), 38)
    self.assertEqual(registry - TOYOTA_VEHICLES, {'TOYOTA_PRIUS_RETROFIT', 'TOYOTA_MATRIX_RETROFIT'})
    self.assertTrue(TOYOTA_VEHICLES <= registry)
    for identity in registry - TOYOTA_VEHICLES:
      self.assertTrue(supported_cp(params_for(identity)))
    self.assertFalse(supported_cp(params_for('TOYOTA_RAV4_TSS2_2023')))
    self.assertFalse(supported_cp(params_for('CHEVROLET_BOLT_EUV')))
    with mock.patch.dict(os.environ, {'TORQUE_REPLAY_RUNTIME': '1'}):
      for car_id in sorted(TOYOTA_VEHICLES):
        cp = params_for(car_id)
        with self.subTest(car_id=car_id):
          self.assertTrue(development_enabled(cp))
          cp.dashcamOnly = True
          self.assertFalse(development_enabled(cp))
          cp.dashcamOnly = False
          cp.steerControlType = structs.CarParams.SteerControlType.angle
          self.assertFalse(development_enabled(cp))
          cp.steerControlType = structs.CarParams.SteerControlType.torque
          cp.lateralTuning.torque.latAccelOffset = float('nan')
          self.assertFalse(development_enabled(cp))
      cp = params_for('TOYOTA_COROLLA_TSS2')
      cp.brand = 'gm'
      self.assertFalse(development_enabled(cp))
    with mock.patch.dict(os.environ, {'TORQUE_REPLAY_RUNTIME': '0'}):
      self.assertFalse(development_enabled(params_for('TOYOTA_COROLLA_TSS2')))

  def test_each_cp_selects_complete_tuple_and_drives_real_controller(self):
    for car_id in sorted(TOYOTA_VEHICLES):
      with self.subTest(car_id=car_id), OpenpilotPrefix():
        cp = params_for(car_id)
        saved = Params()
        host = TorqueHost(saved, cp.as_reader())
        base = host.vehicle
        self.assertEqual(base.vehicle, car_id)
        self.assertEqual(base.upstream_update(), (cp.lateralTuning.torque.latAccelFactor,
                                                 cp.lateralTuning.torque.latAccelOffset, cp.lateralTuning.torque.friction))
        now = 1_000_000_000
        sm = learner(now, base)
        host.settings = read_settings(saved, base)
        selected = host._select(sm, now)
        self.assertEqual(selected.source, TorqueSource.LEARNED)
        self.assertAlmostEqual(selected.lat_accel_factor, base.lat_accel_factor * 1.1)
        self.assertAlmostEqual(selected.lat_accel_offset, 0.1)
        self.assertAlmostEqual(selected.friction, base.friction * 1.1)

        saved.put_bool('AdvancedLateralTune', True, block=True)
        saved.put('SteerLatAccel', base.lat_accel_factor * 1.2, block=True)
        host.settings = read_settings(saved, base)
        factor_only = host._select(sm, now)
        self.assertEqual(factor_only.source, TorqueSource.USER)
        self.assertAlmostEqual(factor_only.lat_accel_factor, base.lat_accel_factor * 1.2)
        self.assertAlmostEqual(factor_only.lat_accel_offset, base.lat_accel_offset)
        self.assertAlmostEqual(factor_only.friction, base.friction * 1.1)
        saved.remove('SteerLatAccel')
        saved.put('SteerFriction', min(1.0, max(0.01, base.friction * 1.2)), block=True)
        host.settings = read_settings(saved, base)
        friction_only = host._select(sm, now)
        self.assertEqual(friction_only.source, TorqueSource.USER)
        self.assertAlmostEqual(friction_only.lat_accel_factor, base.lat_accel_factor * 1.1)
        self.assertAlmostEqual(friction_only.lat_accel_offset, 0.1)
        self.assertAlmostEqual(friction_only.friction, min(1.0, max(0.01, base.friction * 1.2)))
        saved.put_bool('ForceAutoTuneOff', True, block=True)
        host.settings = read_settings(saved, base)
        self.assertAlmostEqual(host._select(sm, now).lat_accel_factor, base.lat_accel_factor)
        saved.remove('SteerFriction')
        Path(saved.get_param_path('SteerLatAccel')).write_bytes(b'nan')
        self.assertFalse(read_settings(saved, base).valid)
        self.assertEqual(host.sample(sm, now_ns=now, lat_active=False), base)

        interface = interfaces[car_id]
        controller = LatControlTorque(cp.as_reader(), interface(cp), DT_CTRL)
        initial_limit = controller.pid.pos_limit
        controller.update_torque_parameters(*factor_only.upstream_update())
        self.assertAlmostEqual(controller.torque_params.latAccelFactor, factor_only.lat_accel_factor, delta=1e-6)
        self.assertAlmostEqual(controller.torque_params.latAccelOffset, factor_only.lat_accel_offset, delta=1e-6)
        self.assertAlmostEqual(controller.torque_params.friction, factor_only.friction, delta=1e-6)
        self.assertAlmostEqual(controller.pid.pos_limit, initial_limit * 1.2, delta=1e-6)
        vm = VehicleModel(cp)
        for _ in range(50):
          output, _, state = frame(controller, vm, curvature=0.001)
        self.assertTrue(state.active)
        self.assertTrue(math.isfinite(output))
        self.assertLessEqual(abs(output), controller.steer_max + 1e-6)
        output, _, state = frame(controller, vm, active=False, curvature=0.001)
        self.assertEqual(output, 0.0)
        self.assertFalse(state.active)

  def test_stock_markers_do_not_carry_previous_vehicle_tune(self):
    first = params_for('TOYOTA_COROLLA_TSS2')
    second = params_for('TOYOTA_RAV4_TSS2')
    with OpenpilotPrefix():
      saved = Params()
      old = TorqueHost(saved, first).vehicle
      new = TorqueHost(saved, second).vehicle
      self.assertNotEqual(old.lat_accel_factor, new.lat_accel_factor)
      saved.put_bool('AdvancedLateralTune', True, block=True)
      saved.put('SteerLatAccel', old.lat_accel_factor, block=True)
      saved.put('SteerLatAccelStock', old.lat_accel_factor, block=True)
      saved.put('SteerFriction', old.friction, block=True)
      saved.put('SteerFrictionStock', old.friction, block=True)
      settings = read_settings(saved, new)
      self.assertTrue(settings.valid)
      self.assertIsNone(settings.user_factor)
      self.assertIsNone(settings.user_friction)
      host = TorqueHost(saved, second)
      host.settings = settings
      self.assertEqual(host._select(learner(1_000_000_000, new), 1_000_000_000).source, TorqueSource.LEARNED)

  def test_prius_eps_firmware_deadzone_variant_keeps_cp_and_selector(self):
    car_id = 'TOYOTA_PRIUS'
    interface = interfaces[car_id]
    eps = structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.eps, fwVersion=b'other-prius-eps')
    cp = interface.get_params(car_id, gen_empty_fingerprint(), [eps], False, False, False)
    self.assertTrue(supported_cp(cp))
    self.assertAlmostEqual(cp.lateralTuning.torque.steeringAngleDeadzoneDeg, 0.3)
    with OpenpilotPrefix():
      host = TorqueHost(Params(), cp)
      self.assertEqual(host.vehicle.upstream_update(), (cp.lateralTuning.torque.latAccelFactor,
                                                       cp.lateralTuning.torque.latAccelOffset, cp.lateralTuning.torque.friction))
    controller = LatControlTorque(cp.as_reader(), interface(cp), DT_CTRL)
    self.assertAlmostEqual(controller.steering_angle_deadzone_deg, 0.3)


if __name__ == '__main__':
  unittest.main()
