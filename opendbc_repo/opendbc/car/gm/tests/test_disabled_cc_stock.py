import os
import unittest
from unittest.mock import patch

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.values import (CAR, DBC, ORDINARY_CC_CAR, GMFlags, CruiseButtons,
                                   is_ordinary_cc_profile, is_ordinary_cc_stock_profile)
from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal, disable_long_supported
from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car.gm.tests.test_cc_gateway_stock import pt_frames, silverado_stock_params
from opendbc.car.gm.tests.test_bolt_cc import native
from opendbc.car.gm.gmcan import create_buttons
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix


class TestDisabledCcStock(unittest.TestCase):
  def test_finalized_stock_owner_is_exact_and_idempotent(self):
    from opendbc.car.gm.aol import qualified_gm
    from opendbc.car.gm.lateral import lane_centering_supported
    from opendbc.car.gm.feature_capabilities import longitudinal_supported
    from openpilot.starpilot.lateral.controller_selection import policy_for
    for identity in ORDINARY_CC_CAR:
      for release in (False, True):
        cp = params(identity, release=release)
        before = cp.to_dict()
        prepare_disable_longitudinal(cp, False)
        self.assertEqual(cp.to_dict(), before)
        self.assertTrue(is_ordinary_cc_profile(cp))
        self.assertTrue(cp.openpilotLongitudinalControl)
        cp.flags |= int(GMFlags.HAS_BSM)
        prepare_disable_longitudinal(cp, True)
        self.assertTrue(is_ordinary_cc_stock_profile(cp), identity)
        self.assertFalse(is_ordinary_cc_profile(cp))
        self.assertTrue(cp.flags & GMFlags.HAS_BSM)
        self.assertTrue(cp.pcmCruise)
        self.assertFalse(cp.openpilotLongitudinalControl)
        self.assertEqual(cp.safetyConfigs[0].safetyParam, 16)
        self.assertEqual(cp.minEnableSpeed, -1.)
        self.assertTrue(qualified_gm(cp))
        self.assertTrue(lane_centering_supported(cp))
        self.assertEqual(policy_for(cp), 'ordinary_cc')
        self.assertFalse(longitudinal_supported(cp))
        self.assertTrue(disable_long_supported(cp))
        before = cp.to_dict()
        prepare_disable_longitudinal(cp, True)
        self.assertEqual(cp.to_dict(), before)
        for field in ('passive', 'dashcamOnly', 'notCar', 'openpilotLongitudinalControl', 'alphaLongitudinalAvailable'):
          denied = cp.as_reader().as_builder()
          setattr(denied, field, True)
          self.assertFalse(is_ordinary_cc_stock_profile(denied), field)
    silverado = silverado_stock_params()
    self.assertFalse(is_ordinary_cc_stock_profile(silverado))
    before = silverado.to_dict()
    prepare_disable_longitudinal(silverado, True)
    self.assertEqual(silverado.to_dict(), before)

  def test_actual_card_parser_native_own_cruise_and_cancel(self):
    from openpilot.starpilot.aol.tests.test_gm import TestGmAol
    safety = libsafety_py.libsafety
    for identity in (CAR.CADILLAC_XT5_CC, CAR.CADILLAC_XT4_CC):
      with self.subTest(identity=identity), OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
        settings = Params()
        settings.put_bool('DisableOpenpilotLongitudinal', True, block=True)
        settings.put_bool('AlwaysOnLateral', True, block=True)
        card = TestGmAol.card(params(identity), settings)
        cp, ci = card.CP, card.CI
        self.assertTrue(is_ordinary_cc_stock_profile(cp))
        self.assertFalse(card.volt_cc_selected)
        self.assertEqual(cp.alternativeExperience, 32)
        packer = CANPacker(DBC[identity][Bus.pt])
        safety.set_alternative_experience(cp.alternativeExperience)
        self.assertEqual(safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, 16), 0)
        safety.init_tests()
        control = structs.CarControl(enabled=True, latActive=True, longActive=True)
        control.actuators.accel = 2.
        control.hudControl.setSpeed = 35.
        saw_cancel = False
        for tick in range(420):
          now = 1_000_000_000 + tick * 10_000_000
          cruise = 150 <= tick < 350
          main = tick < 390
          brake = 320 <= tick < 340
          gas = 300 <= tick < 310
          frames = [f for f in pt_frames(packer, cruise=cruise, main=main, brake=brake, gas=gas,
                                        counter=tick % 4) if f[0] not in (0x1E1, 0x34A, 0x17D)]
          frames += [packer.make_can_msg('ESPStatus', 0, {'TractionControlOn': 1}), create_buttons(packer, 0, tick % 4,
                      CruiseButtons.DECEL_SET if tick in (90, 120) else CruiseButtons.RES_ACCEL if tick == 100 else CruiseButtons.UNPRESS),
                     packer.make_can_msg('EBCMWheelSpdRear', 0, {'RLWheelSpd': 60, 'RRWheelSpd': 60, 'RLWheelDir': 1, 'RRWheelDir': 1})]
          safety.set_timer(now // 1000)
          for frame in frames:
            self.assertTrue(native('rx', frame, now // 1000), (tick, frame))
          safety.safety_tick()
          state = ci.update([(now, frames)])
          self.assertFalse(state.buttonEnable, 'Physical SET/RES cannot enable host before PCM acknowledges cruise')
          self.assertEqual(state.cruiseState.enabled, cruise)
          self.assertFalse(state.cruiseState.nonAdaptive)
          card.aol_card_intent.update(state, now_ns=now)
          safety.set_aol_test_heartbeat(True)
          safety.aol_set_host_request(1 if card.aol_card_intent.allowed_latch else 0)
          safety.set_timer(now // 1000 + 1)
          if tick >= 70:
            self.assertTrue(state.canValid)
            self.assertTrue(safety.safety_config_valid())
            self.assertEqual(bool(safety.aol_get_permission_mask() & 1), main)
            self.assertEqual(card.aol_card_intent.allowed_latch, main)
          if tick < 150:
            self.assertFalse(safety.get_controls_allowed())
          for button in (CruiseButtons.RES_ACCEL, CruiseButtons.DECEL_SET):
            self.assertFalse(native('tx', create_buttons(packer, 0, (tick + 1) % 4, button), now // 1000 + 1))
          control.cruiseControl.cancel = 200 <= tick < 240
          _, commands = ci.apply(control.as_reader(), now + 2)
          for address, data, bus in commands:
            self.assertIn(address, (0x180, 0x1E1))
            if address == 0x1E1:
              saw_cancel = True
              self.assertEqual((data[5] >> 4) & 7, CruiseButtons.CANCEL)
              self.assertEqual(data[4], tick % 4, 'Stock cancel keeps the physical counter')
              self.assertTrue(native('tx', (address, data, bus), now // 1000 + 2))
        self.assertTrue(saw_cancel)
        safety.set_alternative_experience(0)

  def test_actual_selfdrived_stock_acknowledgment_and_override(self):
    from openpilot.starpilot.aol.tests.test_ioniq_availability import IoniqAvailabilityTests
    from openpilot.cereal import log, messaging
    fixture = IoniqAvailabilityTests()
    self.addCleanup(fixture.doCleanups)
    with OpenpilotPrefix():
      cp = params(CAR.CADILLAC_XT5_CC)
      prepare_disable_longitudinal(cp, True)
      sd, _, step = fixture._authority_callers(False, cp=cp, genuine_events=True)
      ci = CarInterface(cp)
      packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
      safety = libsafety_py.libsafety
      safety.set_alternative_experience(cp.alternativeExperience)
      self.assertEqual(safety.set_safety_hooks(structs.CarParams.SafetyModel.gm, 16), 0)
      safety.init_tests()
      for tick in range(420):
        now = 10_000_000_000 + tick * 10_000_000
        cruise = 150 <= tick < 320
        brake = 320 <= tick < 350
        gas = 290 <= tick < 300
        frames = [f for f in pt_frames(packer, cruise=cruise, brake=brake, gas=gas, counter=tick % 4)
                  if f[0] not in (0x1E1, 0x34A, 0x17D)]
        button = CruiseButtons.DECEL_SET if tick == 80 else CruiseButtons.RES_ACCEL if tick == 100 else CruiseButtons.UNPRESS
        frames += [packer.make_can_msg('ESPStatus', 0, {'TractionControlOn': 1}), create_buttons(packer, 0, tick % 4, button),
                   packer.make_can_msg('EBCMWheelSpdRear', 0, {'RLWheelSpd': 60, 'RRWheelSpd': 60, 'RLWheelDir': 1, 'RRWheelDir': 1})]
        for frame in frames:
          self.assertTrue(native('rx', frame, (now // 1000) % (2 ** 32)))
        safety.safety_tick()
        state = ci.update([(now, frames)])
        safety.set_aol_test_heartbeat(True)
        safety.aol_set_host_request(1)
        safety.set_timer((now // 1000 + 1) % (2 ** 32))
        mask = safety.aol_get_permission_mask()
        event = messaging.new_message('carState', valid=True, logMonoTime=now)
        event.carState = state
        axis, _, command = step(tick, car_state_message=event, allowed=bool(mask & 1),
                                long_allowed=False, native_request_long=False,
                                panda_controls_allowed=safety.get_controls_allowed())
        self.assertNotIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names, tick)
        self.assertFalse(command.longActive)
        if tick >= 80:
          self.assertTrue(command.latActive, (tick, sd.events.names, axis.faultReason))
        if 80 <= tick < 150:
          self.assertFalse(sd.enabled, 'SET/RES cannot create ordinary engagement without factory acknowledgment')
      safety.set_alternative_experience(0)
