
from unittest.mock import patch
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from opendbc.car import gen_empty_fingerprint
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import DOCUMENT_KEY, ControllerMode, parse_document, read_selection, replace_mode
from openpilot.starpilot.lateral.torque_extension import selected_policy
from openpilot.starpilot.lateral.volt_policy import VOLT_IDENTITIES, supported_cp
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change


def params(identity, alpha=True, release=False):
  fp = gen_empty_fingerprint()
  fp[2][0x320] = 6
  fp[1][0x460] = 8
  fp[0].update({0xbe: 6, 0x3d1: 8, 0xc9: 8, 0x1e1: 7, 0x1f5: 8, 0x34a: 5, 0x1c4: 8, 0xbd: 7})
  return CarInterface.get_params(identity, fp, [], alpha, release, False)


class TestVoltTorquePolicy(unittest.TestCase):
  def test_calibration_geometry_and_exact_admission(self):
    for identity in VOLT_IDENTITIES:
      cp = params(identity)
      self.assertEqual(cp.lateralTuning.which(), 'torque')
      self.assertEqual(cp.lateralTuning.torque.latAccelFactor, float(np.float32(1.5961527626411784)))
      self.assertEqual(cp.lateralTuning.torque.friction, float(np.float32(.1572393918005158)))
      self.assertEqual(cp.steerRatio, float(np.float32(15.7)))
      self.assertFalse(cp.dashcamOnly)
      self.assertTrue(supported_cp(cp))
      for field, value in (('brand', 'toyota'), ('passive', True), ('notCar', True), ('dashcamOnly', True)):
        bad = cp.as_reader().as_builder()
        setattr(bad, field, value)
        self.assertFalse(supported_cp(bad))
    self.assertEqual(params(CAR.CHEVROLET_VOLT).minEnableSpeed, -1.)

  def test_real_saved_choice_owner_preserves_other_vehicle(self):
    with tempfile.TemporaryDirectory() as root:
      storage = Params(root)
      cp = params(CAR.CHEVROLET_VOLT_CAMERA)
      other = params(CAR.CHEVROLET_VOLT)
      storage.put(DOCUMENT_KEY, json.loads(replace_mode(None, other, ControllerMode.STANDARD)), block=True)
      owner = FeatureSettingsOwner(storage, lambda group: group == 'preferences',
                                   vehicle_fingerprint=lambda: str(cp.carFingerprint), vehicle_params=lambda: cp)
      def row():
        state = owner.snapshot('torque', parked=False, system_long=False, lateral_context=False, metric=False)
        return next(item for item in state.rows if item.key == DOCUMENT_KEY)
      self.assertEqual(row().value, 'StarPilot Controller')
      request = row_change(row())
      assert request is not None
      self.assertTrue(owner.apply(request))
      self.assertEqual(read_selection(storage, cp).mode, ControllerMode.STANDARD)
      choices = parse_document(Path(storage.get_param_path(DOCUMENT_KEY)).read_bytes())['vehicles']
      self.assertEqual(choices[str(other.carFingerprint)]['mode'], 'standard')
      standard = LatControlTorque(cp.as_reader(), CarInterface(cp), .01, controller_mode=read_selection(storage, cp).mode)
      self.assertIsNone(selected_policy(standard))
      self.assertEqual(standard.pid._k_i[1], [.15])
      request = row_change(row())
      assert request is not None
      self.assertTrue(owner.apply(request))
      self.assertEqual(read_selection(storage, cp).mode, ControllerMode.STARPILOT)
      self.assertFalse(storage.get_bool('AdvancedLateralTune'))

  def test_gain_callback_limits_reset_and_saturation_lifecycle(self):
    cp = params(CAR.CHEVROLET_VOLT_CAMERA)
    control = LatControlTorque(cp.as_reader(), CarInterface(cp), .01)
    policy = selected_policy(control)
    self.assertEqual(control.pid._k_p, [[0.0], [.6]])
    self.assertEqual(control.pid._k_i[1], [.35])
    limits = (control.pid.pos_limit, control.pid.neg_limit)
    conversion = [control.torque_from_lateral_accel(x, control.torque_params) for x in (-2., 0., 2.)]
    control.update_torque_parameters(1.1, .02, .05)
    self.assertEqual((control.pid.pos_limit, control.pid.neg_limit), limits)
    self.assertEqual([control.torque_from_lateral_accel(x, control.torque_params) for x in (-2., 0., 2.)], conversion)
    state = SimpleNamespace(vEgo=20., steeringAngleDeg=0., steeringPressed=False, steeringRateDeg=0., standstill=False)
    live = SimpleNamespace(angleOffsetDeg=0., roll=0.)
    vm = VehicleModel(cp)
    for _ in range(30):
      output, _, log = control.update(True, state, vm, live, False, .01, False, .2)
      self.assertLessEqual(abs(output), 1.)
    control.reset()
    self.assertEqual(control.pid.i, 0.)
    control.update(False, state, vm, live, False, 0., False, .2)
    self.assertEqual(control.pid._k_p, [[0.0], [.6]])
    self.assertEqual(policy.previous_measurement, 0.)
    self.assertEqual(control.pid.i, 0.)

  def test_actual_controls_selection_learning_and_unscaled_vehicle_model(self):
    class Updated(Exception):
      pass
    for identity in VOLT_IDENTITIES:
      cp = params(identity)
      self.assertFalse(cp.dashcamOnly)
      for mode in (ControllerMode.STARPILOT, ControllerMode.STANDARD):
        with self.subTest(identity=identity, mode=mode), OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
          storage = Params()
          storage.put('CarParams', cp.to_bytes(), block=True)
          cp.clear_write_flag()
          storage.put(DOCUMENT_KEY, json.loads(replace_mode(None, cp, mode)), block=True)
          controls = Controls()
          self.assertEqual(controls.LaC.controller_mode, mode)
          self.assertEqual(selected_policy(controls.LaC) is not None, mode == ControllerMode.STARPILOT)
          # Native scalar estimation remains outside the GM brand scope.
          self.assertFalse(controls.torque_learning_allowed)
          real_update = controls.VM.update_params
          for speed, live_ratio in ((0., 15.7), (20., 15.7), (20., 15.7), (35., 16.2)):
            self.enterContext(patch.object(controls, 'sm', {'carState': SimpleNamespace(vEgo=speed),
                           'vehicleParameters': SimpleNamespace(stiffnessFactor=1.1, steerRatio=live_ratio)}, create=True))
            observed = []
            def capture(stiffness, ratio, real_update=real_update, observed=observed):
              real_update(stiffness, ratio)
              observed.append((stiffness, ratio))
              raise Updated
            with patch.object(controls.VM, 'update_params', side_effect=capture):
              with self.assertRaises(Updated):
                controls.state_control()
            self.assertEqual(observed, [(1.1, live_ratio)])
