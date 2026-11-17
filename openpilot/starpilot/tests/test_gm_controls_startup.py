"""Native GM Card publication and disabled Controls startup boundary."""

import itertools
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from openpilot.cereal import messaging
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.gm.tests import test_bolt_pedal as pedal_fixture
from opendbc.car.gm.values import CAR
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car.card import Car
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, FieldChoice, PlatformProfile, serialize_document


class TestGMControlsStartup(unittest.TestCase):
  def startup(self, identity, *, alpha=False, disable=False, camera=True, aol=False, torque='absent'):
    params = Params()
    for key, value in [('OpenpilotEnabledToggle', True), ('SafeMode', False), ('IsReleaseBranch', False),
                       ('AlphaLongitudinalEnabled', alpha), ('GMPedalLongitudinal', True),
                       ('DisableOpenpilotLongitudinal', disable), ('AlwaysOnLateral', aol)]:
      params.put_bool(key, value, block=True)
    fingerprint = gen_empty_fingerprint()
    fingerprint[0][0x201] = 6
    if camera:
      fingerprint[2][0x180] = 4
    result = (identity, fingerprint, '0' * 17, [], structs.CarParams.FingerprintSource.can, True)
    with patch('opendbc.car.car_helpers.fingerprint', return_value=result), \
         patch('openpilot.selfdrive.car.card.messaging.recv_one_retry',
               return_value=messaging.new_message('can', 1)):
      card = Car()
    if torque != 'absent':
      params.put_bool('AdvancedLateralTune', True, block=True)
      tune = card.CP.lateralTuning.torque
      basis = (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
      raw = serialize_document({str(identity): PlatformProfile(basis, FieldChoice(), FieldChoice('custom', .14))})
      Path(params.get_param_path(DOCUMENT_KEY)).write_bytes(raw if torque == 'valid' else b'{invalid')
    controls = Controls()
    self.assertEqual(controls.CP.to_dict(), card.CP.to_dict())
    self.assertEqual(controls.torque_host is not None, torque == 'valid' and not card.CP.passive)
    self.assertFalse(params.get_bool('ControlsReady'))
    command, lateral_log = controls.state_control()
    self.assertFalse(command.enabled)
    self.assertFalse(command.latActive)
    self.assertFalse(command.longActive)
    deadline = time.monotonic() + 1
    while not card.sm.seen['carControl'] and time.monotonic() < deadline:
      controls.publish(command, lateral_log)
      card.sm.update(10)
    self.assertTrue(card.sm.seen['carControl'])
    self.assertFalse(card.sm['carControl'].enabled)
    invalid = structs.CarState.new_message()
    card.controls_update(invalid, card.sm['carControl'])
    self.assertFalse(params.get_bool('ControlsReady'))
    return card, controls

  def test_final_card_configuration_and_controls_constructor(self):
    identities = (CAR.CHEVROLET_BOLT_CC_2018_2021, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL)
    for identity, alpha, disable, camera in itertools.product(identities, (False, True), (False, True), (False, True)):
      with self.subTest(identity=identity, alpha=alpha, disable=disable, camera=camera), \
           OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
        self.startup(identity, alpha=alpha, disable=disable, camera=camera)

  def test_saved_torque_and_aol_disabled_initial_output(self):
    for identity, aol, torque in itertools.product(
        (CAR.CHEVROLET_BOLT_CC_2018_2021, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL),
        (False, True), ('absent', 'valid', 'malformed')):
      with self.subTest(identity=identity, aol=aol, torque=torque), \
           OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
        self.startup(identity, alpha=True, aol=aol, torque=torque)

  def test_restored_gateway_discovery_card_publication_and_controls(self):
    from opendbc.car.gm.fingerprints import FINGERPRINTS
    from opendbc.car.gm.values import ORDINARY_CC_WORD, is_ordinary_cc_profile, is_ordinary_cc_stock_profile
    identities = (CAR.CADILLAC_CT6_CC, CAR.CADILLAC_XT5_CC, CAR.CHEVROLET_EQUINOX_CC,
                  CAR.CHEVROLET_SUBURBAN_CC, CAR.CHEVROLET_TRAILBLAZER_CC, CAR.CHEVROLET_MALIBU_CC)
    for identity, disable in itertools.product(identities, (False, True)):
      with self.subTest(identity=identity, disable=disable), OpenpilotPrefix(), \
           patch.dict(os.environ, {'SIMULATION': '1'}):
        params = Params()
        for key, value in [('OpenpilotEnabledToggle', True), ('SafeMode', False), ('IsReleaseBranch', False),
                           ('AlphaLongitudinalEnabled', False), ('GMPedalLongitudinal', False),
                           ('DisableOpenpilotLongitudinal', disable), ('AlwaysOnLateral', False)]:
          params.put_bool(key, value, block=True)
        fingerprint = gen_empty_fingerprint()
        fingerprint[0].update(FINGERPRINTS[identity][0])
        result = (identity, fingerprint, '0' * 17, [], structs.CarParams.FingerprintSource.can, True)
        with patch('opendbc.car.car_helpers.fingerprint', return_value=result), \
             patch('openpilot.selfdrive.car.card.messaging.recv_one_retry',
                   return_value=messaging.new_message('can', 1)):
          card = Car()
        self.assertEqual(is_ordinary_cc_profile(card.CP), not disable)
        self.assertEqual(is_ordinary_cc_stock_profile(card.CP), disable)
        self.assertEqual(card.CP.openpilotLongitudinalControl, not disable)
        self.assertEqual(card.CP.safetyConfigs[0].safetyParam, 16 if disable else ORDINARY_CC_WORD)
        self.assertEqual(card.CP.pcmCruise, disable)
        controls = Controls()
        self.assertEqual(controls.CP.to_dict(), card.CP.to_dict())
        self.assertIsNone(controls.torque_host)
        command, lateral_log = controls.state_control()
        self.assertFalse(command.enabled)
        self.assertFalse(command.latActive)
        self.assertFalse(command.longActive)
        controls.publish(command, lateral_log)
        self.assertFalse(params.get_bool('ControlsReady'))

  def test_live_disabled_controls_and_real_parser_unlock_initialization(self):
    with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1'}):
      card, controls = self.startup(CAR.CHEVROLET_BOLT_CC_2018_2021, camera=True)
      fixture = pedal_fixture.TestBoltPedalStartupParser()
      with patch('opendbc.car.gm.tests.test_bolt_pedal.CarInterface', return_value=card.CI):
        _, state = fixture.stream(card.CP)
      self.assertTrue(state.canValid)
      card.controls_update(state, card.sm['carControl'])
      self.assertFalse(Params().get_bool('ControlsReady'))
      deadline = time.monotonic() + 1
      while not controls.sm.seen['carState'] and time.monotonic() < deadline:
        event = messaging.new_message('carState')
        event.valid = True
        event.carState = state
        card.pm.send('carState', event)
        controls.sm.update(10)
      self.assertTrue(controls.sm.seen['carState'])
      # The fixture feeds the production parser, not Panda safety/permission.
      command, lateral_log = controls.state_control()
      controls.publish(command, lateral_log)
      card.sm.update(100)
      self.assertTrue(card.sm.valid['carControl'])
      self.assertTrue(card.sm.alive['carControl'])
      card.controls_update(state, card.sm['carControl'])
      self.assertTrue(card.ci_initialized)
      deadline = time.monotonic() + 1
      while not Params().get_bool('ControlsReady') and time.monotonic() < deadline:
        time.sleep(.001)
      self.assertTrue(Params().get_bool('ControlsReady'))
      self.assertFalse(card.sm['carControl'].enabled)


if __name__ == '__main__':
  unittest.main()
