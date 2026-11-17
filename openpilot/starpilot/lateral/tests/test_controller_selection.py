"""The optional policy choice is latched at startup and separate from torque sources."""

from dataclasses import replace
import json
import os
import struct
from types import SimpleNamespace
import unittest
from unittest import mock

from opendbc.car import DT_CTRL, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR as HYUNDAI
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.lateral.controller_selection import (
  DOCUMENT_KEY, ControllerMode, default_selection, policy_for, read_selection, selection_from_bytes,
)
from openpilot.starpilot.lateral.torque_tuning import TorqueSource


def cp_for(vehicle):
  return interfaces[vehicle].get_non_essential_params(vehicle)


def document(**choices):
  brands = {'HYUNDAI_IONIQ_6': 'hyundai', 'GENESIS_G70_2020': 'hyundai', 'TOYOTA_COROLLA_TSS2': 'toyota'}
  return json.dumps({'version': 1, 'vehicles': {key: {'brand': brands[key], 'mode': value}
                                               for key, value in choices.items()}}, sort_keys=True).encode()


class TestControllerSelection(unittest.TestCase):
  def test_hybrid_existing_policy_saved_standard_roundtrip(self):
    from opendbc.car.gm.tests.test_ordinary_cc import malibu_hybrid_params
    from openpilot.starpilot.lateral.controller_selection import parse_document, replace_mode
    cp = malibu_hybrid_params()
    before = default_selection(cp)
    self.assertEqual(before.policy, 'ordinary_cc')
    self.assertEqual(before.mode, ControllerMode.STARPILOT)
    raw = replace_mode(None, cp, ControllerMode.STANDARD)
    self.assertEqual(parse_document(raw)['vehicles'][str(cp.carFingerprint)]['mode'], 'standard')
    self.assertEqual(selection_from_bytes(cp, raw).mode, ControllerMode.STANDARD)
    self.assertEqual(default_selection(cp), before)
    for bad in (raw.replace(b'CHEVROLET_MALIBU_HYBRID_CC', b'UNKNOWN_GM'), raw.replace(b'standard', b'unknown')):
      self.assertEqual(selection_from_bytes(cp, bad).source, 'invalid')

  def test_missing_choice_exact_policy_and_untuned_matrix(self):
    expected = ((HYUNDAI.HYUNDAI_IONIQ_6, 'ioniq6'),
                (HYUNDAI.GENESIS_G70_2020, 'genesis_g70_2020'),
                (TOYOTA.TOYOTA_COROLLA_TSS2, 'corolla_tss2'),
                (HYUNDAI.KIA_EV6, 'kia_ev6'),
                (HYUNDAI.HYUNDAI_SONATA, 'sonata'),
                (TOYOTA.TOYOTA_RAV4_TSS2, 'rav4_tss2'))
    for vehicle, policy in expected:
      with self.subTest(vehicle=vehicle):
        cp = cp_for(vehicle)
        self.assertEqual(policy_for(cp), policy)
        selected = selection_from_bytes(cp, None)
        mode = ControllerMode.STARPILOT
        self.assertEqual(selected.mode, mode)
        lateral = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), DT_CTRL)
        self.assertEqual(lateral.controller_mode, selected.mode)
        self.assertEqual(lateral.controller_policy, policy)
        self.assertEqual(bool(selected_policy(lateral)), mode == ControllerMode.STARPILOT)

  def test_saved_standard_is_per_vehicle_and_invalid_cannot_enable_policy(self):
    ioniq = cp_for(HYUNDAI.HYUNDAI_IONIQ_6)
    g70 = cp_for(HYUNDAI.GENESIS_G70_2020)
    ev6 = cp_for(HYUNDAI.KIA_EV6)
    raw = document(HYUNDAI_IONIQ_6='standard', GENESIS_G70_2020='starpilot')
    self.assertEqual(selection_from_bytes(ioniq, raw).mode, ControllerMode.STANDARD)
    self.assertEqual(selection_from_bytes(g70, raw).mode, ControllerMode.STARPILOT)
    self.assertEqual(selection_from_bytes(ev6, raw), default_selection(ev6))
    for invalid in (b'{', b'{"version":true,"vehicles":{}}', b'{"version":2,"vehicles":{}}',
                    b'{"version":1,"vehicles":{"HYUNDAI_IONIQ_6":{"brand":"toyota","mode":"standard"}}}',
                    b'{"version":1,"vehicles":{},"vehicles":{}}', b'x' * 4097):
      with self.subTest(raw=invalid[:50]):
        selected = selection_from_bytes(ioniq, invalid)
        self.assertEqual(selected.mode, ControllerMode.STARPILOT)
        self.assertEqual(selected.source, 'invalid')
    wrong = ioniq.as_reader().as_builder()
    wrong.brand = 'toyota'
    self.assertIsNone(policy_for(wrong))
    wrong = ioniq.as_reader().as_builder()
    wrong.lateralTuning.init('pid')
    self.assertIsNone(policy_for(wrong))
    wrong = ioniq.as_reader().as_builder()
    wrong.passive = True
    self.assertIsNone(policy_for(wrong))
    wrong = ioniq.as_reader().as_builder()
    wrong.notCar = True
    self.assertIsNone(policy_for(wrong))
    generic = cp_for(TOYOTA.TOYOTA_RAV4_TSS2_2022)
    controller = LatControlTorque(generic.as_reader(), interfaces[TOYOTA.TOYOTA_RAV4_TSS2_2022](generic), DT_CTRL,
                                  controller_mode=ControllerMode.STARPILOT)
    self.assertEqual(controller.controller_policy, 'generic')
    self.assertIsNotNone(selected_policy(controller))

  def test_standard_skips_policy_constructor_and_ioniq_factor_multiplier(self):
    for vehicle in (HYUNDAI.HYUNDAI_IONIQ_6, HYUNDAI.GENESIS_G70_2020, TOYOTA.TOYOTA_COROLLA_TSS2):
      with self.subTest(vehicle=vehicle):
        cp = cp_for(vehicle)
        standard = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), DT_CTRL,
                                    controller_mode=ControllerMode.STANDARD)
        policy = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), DT_CTRL,
                                  controller_mode=ControllerMode.STARPILOT)
        self.assertIsNone(selected_policy(standard))
        self.assertIsNotNone(selected_policy(policy))
        self.assertEqual(standard.pid._k_i[1], [0.15])
        self.assertAlmostEqual(standard.torque_params.latAccelFactor, cp.lateralTuning.torque.latAccelFactor)
        if vehicle == HYUNDAI.HYUNDAI_IONIQ_6:
          self.assertEqual(standard.pid.pos_limit, cp.lateralTuning.torque.latAccelFactor)
          self.assertEqual(policy.pid.pos_limit, policy.torque_params.latAccelFactor)
          self.assertEqual(policy.pid.neg_limit, -policy.torque_params.latAccelFactor)
          self.assertEqual(policy.torque_params.latAccelFactor,
                           struct.unpack('f', struct.pack('f', cp.lateralTuning.torque.latAccelFactor * 1.22))[0])
          for controller, multiplier in ((standard, 1.0), (policy, 1.22)):
            controller.update_torque_parameters(3.3, 0.0, 0.12)
            expected = struct.unpack('f', struct.pack('f', 3.3 * multiplier))[0]
            self.assertEqual(controller.torque_params.latAccelFactor, expected)

  def test_ioniq_startup_saturation_and_first_live_update_are_continuous(self):
    cp = cp_for(HYUNDAI.HYUNDAI_IONIQ_6)
    controller = LatControlTorque(cp.as_reader(), interfaces[HYUNDAI.HYUNDAI_IONIQ_6](cp), DT_CTRL,
                                  controller_mode=ControllerMode.STARPILOT)
    before = (controller.pid.pos_limit, controller.pid.neg_limit)
    for sign in (-1, 1):
      controller.pid.reset()
      accel = controller.pid.update(0., feedforward=sign * 100., freeze_integrator=True)
      self.assertEqual(accel, sign * controller.torque_params.latAccelFactor)
      self.assertAlmostEqual(controller.torque_from_lateral_accel(accel, controller.torque_params), sign)
    controller.update_torque_parameters(cp.lateralTuning.torque.latAccelFactor,
                                        cp.lateralTuning.torque.latAccelOffset, cp.lateralTuning.torque.friction)
    self.assertEqual((controller.pid.pos_limit, controller.pid.neg_limit), before)

  def test_explicit_policy_preserves_legacy_output_through_reset(self):
    sequence = ((False, 2.0, 4.0, -0.0005, False),
                (True, 8.0, 2.0, 0.0003, False),
                (True, 18.0, -1.0, -0.0004, True),
                (False, 0.0, 3.0, 0.0, False),
                (True, 5.0, 1.0, 0.0002, False))
    for vehicle in (HYUNDAI.HYUNDAI_IONIQ_6, HYUNDAI.GENESIS_G70_2020, TOYOTA.TOYOTA_COROLLA_TSS2):
      with self.subTest(vehicle=vehicle):
        cp = cp_for(vehicle)
        legacy = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), DT_CTRL)
        selected = LatControlTorque(cp.as_reader(), interfaces[vehicle](cp), DT_CTRL,
                                    controller_mode=ControllerMode.STARPILOT)
        vm = VehicleModel(cp)
        cs = structs.CarState.new_message()
        params = SimpleNamespace(angleOffsetDeg=0.0, roll=0.02)
        for active, speed, angle, curvature, pressed in sequence:
          cs.vEgo = speed
          cs.steeringAngleDeg = angle
          cs.steeringPressed = pressed
          before = legacy.update(active, cs, vm, params, False, curvature, False, 0.1)
          after = selected.update(active, cs, vm, params, False, curvature, False, 0.1)
          self.assertEqual(after[0], before[0])
          self.assertEqual(after[1], before[1])
          self.assertEqual(after[2].to_dict(), before[2].to_dict())
          self.assertEqual(selected.pid.i, legacy.pid.i)
          if not active:
            self.assertEqual(after[0], 0.0)

  def test_controlsd_latches_choice_while_manual_source_remains_independent(self):
    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'TORQUE_REPLAY_RUNTIME': '0', 'REPLAY': '1',
                                                         'AOL_REPLAY_RUNTIME': '0', 'LANE_CENTERING_REPLAY_RUNTIME': '0'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'):
      params = Params()
      cp = cp_for(HYUNDAI.HYUNDAI_IONIQ_6)
      params.put('CarParams', cp.to_bytes(), block=True)
      params.put_bool('AdvancedLateralTune', True, block=True)
      legacy = Controls()
      self.assertEqual(legacy.lateral_controller_selection, default_selection(cp))
      self.assertIsNotNone(selected_policy(legacy.LaC))
      self.assertEqual(legacy.LaC.pid.pos_limit, legacy.LaC.torque_params.latAccelFactor)
      self.assertEqual(legacy.LaC.pid.neg_limit, -legacy.LaC.torque_params.latAccelFactor)
      self.assertIsNone(legacy.torque_host)
      from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY as TORQUE_DOCUMENT_KEY, replace_field, serialize_document
      tune = cp.lateralTuning.torque
      basis = (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
      override = replace_field({}, str(cp.carFingerprint), basis, 'factor', 'custom', basis[0] * 1.1)
      params.put(TORQUE_DOCUMENT_KEY, json.loads(serialize_document(override)), block=True)
      params.put(DOCUMENT_KEY, json.loads(document(HYUNDAI_IONIQ_6='standard')), block=True)
      self.assertEqual(read_selection(params, cp).mode, ControllerMode.STANDARD)
      first = Controls()
      self.assertEqual(first.lateral_controller_selection.mode, ControllerMode.STANDARD)
      self.assertIsNone(selected_policy(first.LaC))
      self.assertIsNotNone(first.torque_host)
      source = replace(first.torque_host.vehicle, source=TorqueSource.USER, lat_accel_factor=3.3, friction=0.12)
      self.assertTrue(first.torque_host.apply(first.LaC, source))
      self.assertEqual(first.LaC.torque_params.latAccelFactor, struct.unpack('f', struct.pack('f', 3.3))[0])
      params.put(DOCUMENT_KEY, json.loads(document(HYUNDAI_IONIQ_6='starpilot')), block=True)
      self.assertEqual(first.LaC.controller_mode, ControllerMode.STANDARD)
      second = Controls()
      self.assertEqual(second.lateral_controller_selection.mode, ControllerMode.STARPILOT)
      self.assertIsNotNone(selected_policy(second.LaC))
      self.assertIsNotNone(second.torque_host)
      self.assertTrue(second.torque_host.apply(second.LaC, source))
      self.assertEqual(second.LaC.torque_params.latAccelFactor,
                       struct.unpack('f', struct.pack('f', 3.3 * 1.22))[0])
      self.assertEqual(first.torque_host.vehicle, second.torque_host.vehicle)


if __name__ == '__main__':
  unittest.main()
