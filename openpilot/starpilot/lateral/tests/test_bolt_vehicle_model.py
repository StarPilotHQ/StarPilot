
from unittest.mock import patch
import math
import os
from types import SimpleNamespace
import unittest

from opendbc.car.gm.values import CAR
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.longitudinal.tests.test_bolt_mode_transition import params


class VehicleModelUpdated(Exception):
  pass


class TestBoltVehicleModel(unittest.TestCase):
  def test_actual_controls_ratio_is_scoped_and_recomputed_from_live_input(self):
    identities = (CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021,
                  CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023,
                  CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, CAR.CHEVROLET_BOLT_EUV)
    for identity in identities:
      for mode in ('starpilot', 'standard'):
        with self.subTest(identity=identity, mode=mode), OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
          cp = params(identity, alpha=True)
          settings = Params()
          settings.put('CarParams', cp.to_bytes(), block=True)
          if mode == 'standard':
            document = {'version': 1, 'vehicles': {str(identity): {'brand': 'gm', 'mode': 'standard'}}}
            settings.put('LateralControllerSelection', document, block=True)
          controls = Controls()
          self.assertEqual(controls.LaC.controller_mode.value, mode)
          real_update = controls.VM.update_params
          for index, (speed, live_ratio) in enumerate(((0., 16.8), (8., 16.8), (25., 16.8),
                                                      (25., 16.8), (5., 17.2), (35., 17.2), (0., 16.8))):
            if index == 4:
              controls.LaC.reset()
            self.enterContext(patch.object(controls, 'sm', {'carState': SimpleNamespace(vEgo=speed),
                           'vehicleParameters': SimpleNamespace(stiffnessFactor=1.1, steerRatio=live_ratio)}, create=True))
            observed = []

            def capture(stiffness, ratio, real_update=real_update, observed=observed):
              real_update(stiffness, ratio)
              observed.append((stiffness, ratio))
              raise VehicleModelUpdated

            with patch.object(controls.VM, 'update_params', side_effect=capture):
              with self.assertRaises(VehicleModelUpdated):
                controls.state_control()
            scale = 1.0
            if mode == 'starpilot' and identity == CAR.CHEVROLET_BOLT_CC_2017:
              # Literal original controlsd caller and vehicle_tunes speed law.
              onset, width = 20 * .44704, 4 * .44704
              scale += .045 / (1 + math.exp(-(max(speed, 0.) - onset) / width))
            elif mode == 'starpilot' and identity == CAR.CHEVROLET_BOLT_CC_2018_2021:
              scale = 1.01
            self.assertEqual(observed[0][0], 1.1)
            self.assertAlmostEqual(observed[0][1], live_ratio * scale, places=10)
            self.assertEqual(len(observed), 1)
