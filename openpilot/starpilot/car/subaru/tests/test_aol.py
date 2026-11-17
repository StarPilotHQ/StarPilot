import unittest
from dataclasses import replace
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.stock_aol import ANGLE_CARS, TORQUE_CARS, qualified
from opendbc.car.subaru.tests.test_ascent_angle import setup_controller, step
from opendbc.car.subaru.values import CAR
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.vehicle import policy_for, native_matches_cp
from openpilot.starpilot.car.subaru.aol import SubaruStockCardIntent


class TestSubaruStockAol(unittest.TestCase):
  def test_exact_factory_profiles_and_stock_off_choices(self):
    for car in CAR:
      for alpha in (False, True):
        for source in ('fixed', 'fw', 'can'):
          cp = CarInterface.get_params(car, gen_empty_fingerprint(), [], alpha, False, False)
          cp.fingerprintSource = source
          admitted = car in ANGLE_CARS | TORQUE_CARS
          self.assertEqual(qualified(cp), admitted)
          policy = policy_for(cp)
          self.assertFalse(policy.full_axis_runtime_required)
          self.assertFalse(cp.openpilotLongitudinalControl)
          if admitted:
            cp.alternativeExperience |= policy.alternative_experience_addition
            self.assertTrue(qualified(cp, marked_only=True))
            self.assertTrue(policy_for(cp).full_axis_runtime_required)
            self.assertTrue(native_matches_cp(cp, int(cp.safetyConfigs[0].safetyModel.raw), cp.safetyConfigs[0].safetyParam))
            cp.safetyConfigs[0].safetyParam |= 2
            self.assertFalse(qualified(cp))

  def test_successful_full_engagement_brake_reverse_fault_main_and_off(self):
    settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
    intent = SubaruStockCardIntent(settings)
    cs = structs.CarState(canValid=True, vEgo=15, gearShifter=structs.CarState.GearShifter.drive)
    cs.cruiseState.available = cs.cruiseState.enabled = True
    def observe(enabled, active, stamp):
      intent.observe_full_engagement(cs.as_reader(), enabled=enabled, active=active, source_ns=stamp,
                                    observation_valid=True, now_ns=stamp, fault_active=False)
    intent.update(cs.as_reader(), now_ns=1)
    self.assertFalse(intent.allowed_latch)
    observe(False, False, 10)
    observe(True, False, 20)
    self.assertFalse(intent.allowed_latch)
    observe(True, True, 30)
    self.assertTrue(intent.allowed_latch)
    cs.brakePressed = True
    cs.cruiseState.enabled = False
    intent.update(cs.as_reader(), now_ns=40)
    self.assertTrue(intent.allowed_latch)
    cs.gearShifter = structs.CarState.GearShifter.reverse
    intent.update(cs.as_reader(), now_ns=50)
    self.assertTrue(intent.allowed_latch)
    cs.steerFaultTemporary = True
    intent.update(cs.as_reader(), now_ns=60)
    self.assertTrue(intent.allowed_latch)
    cs.gearShifter = structs.CarState.GearShifter.drive
    cs.steerFaultTemporary = False
    cs.steerFaultPermanent = True
    intent.update(cs.as_reader(), now_ns=70)
    self.assertFalse(intent.allowed_latch)
    cs.steerFaultPermanent = False
    intent.update(cs.as_reader(), fault_active=False, now_ns=80)
    observe(False, False, 90)
    observe(True, True, 100)
    self.assertTrue(intent.allowed_latch)
    cs.cruiseState.available = False
    intent.update(cs.as_reader(), now_ns=110)
    self.assertFalse(intent.allowed_latch)
    cs.cruiseState.available = True
    intent.update(cs.as_reader(), now_ns=120)
    self.assertFalse(intent.allowed_latch)
    intent.settings = replace(settings, enabled=False)
    observe(False, False, 130)
    observe(True, True, 140)
    self.assertFalse(intent.allowed_latch)

  def test_angle_controller_only_marked_profile_accepts_independent_lateral(self):
    for car in ANGLE_CARS:
      for enrolled in (False, True):
        cp, controller, command, state = setup_controller(car)
        cp.alternativeExperience = 32 if enrolled else 0
        command.enabled = False
        command.latActive = True
        state.out.brakePressed = True
        state.out.cruiseState.enabled = False
        step(controller, command, state)
        _, decoded = step(controller, command, state)
        self.assertEqual(bool(decoded['LKAS_Request']), enrolled)
        state.out.gearShifter = structs.CarState.GearShifter.reverse
        _, decoded = step(controller, command, state)
        self.assertEqual(decoded['LKAS_Request'], 0)



def pending_restricted_main():
  settings = AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0))
  intent = SubaruStockCardIntent(settings, main_arm=True)
  state = structs.CarState(canValid=True, vEgo=2., gearShifter='drive')
  now = 1_000_000_000
  intent.update(state, now_ns=now, fault_active=False)
  state.cruiseState.available = state.steerFaultTemporary = True
  now += 10_000_000
  observe_main_event(intent, state, now)
  intent.update(state, now_ns=now, fault_active=False)
  assert not intent.allowed_latch and intent._pending_main_ns == now
  return intent, state, now


def observe_main_event(intent, state, now):
  sm = SimpleNamespace(logMonoTime={'onroadEvents': now}, valid={'onroadEvents': True})
  intent.observe_startup_readiness(sm, state, now_ns=now)


def test_temporary_main_wait_preserves_original_gesture_and_requires_new_clear_event():
  intent, state, arm = pending_restricted_main()
  for tick in range(1, 351):
    now = arm + tick * 10_000_000
    observe_main_event(intent, state, now)
    intent.update(state, now_ns=now, fault_active=False)
    assert not intent.allowed_latch and intent._pending_main_ns == arm and intent.main_arm_ns == 0
  state.steerFaultTemporary = False
  intent.update(state, now_ns=now + 10_000_000, fault_active=False)
  assert not intent.allowed_latch and intent._pending_main_ns == arm
  observe_main_event(intent, state, now + 20_000_000)
  intent.update(state, now_ns=now + 20_000_000, fault_active=False)
  assert intent.allowed_latch and intent.main_arm_ns == intent._last_latch_edge_ns == arm
  assert intent._pending_main_ns == 0


@pytest.mark.parametrize('veto', ('main_off', 'invalid_can', 'timeout', 'permanent_eps', 'acc_fault',
  'reverse', 'cancel', 'settings_off', 'calibration', 'calibration_rearm', 'event_fault',
  'transport_fault_without_event', 'native_rejection'))
def test_pending_temporary_main_is_consumed_by_independent_veto(veto):
  intent, state, arm = pending_restricted_main()
  if veto == 'main_off':
    state.cruiseState.available = False
  elif veto in ('invalid_can', 'timeout', 'permanent_eps', 'acc_fault'):
    setattr(state, {'invalid_can': 'canValid', 'timeout': 'canTimeout',
                    'permanent_eps': 'steerFaultPermanent', 'acc_fault': 'accFaulted'}[veto], veto != 'invalid_can')
  elif veto == 'reverse':
    state.gearShifter = 'reverse'
  elif veto == 'cancel':
    state.buttonEvents = [structs.CarState.ButtonEvent(type='cancel', pressed=True)]
  elif veto == 'settings_off':
    intent.settings = replace(intent.settings, enabled=False)
  elif veto in ('calibration', 'calibration_rearm'):
    setattr(intent, '_calibration_inhibit' if veto == 'calibration' else '_calibration_rearm_required', True)
  if veto != 'transport_fault_without_event':
    observe_main_event(intent, state, arm + 10_000_000)
  else:
    intent._main_event_ns = 0
  intent.update(state, now_ns=arm + 10_000_000,
                fault_active=veto in ('event_fault', 'transport_fault_without_event'),
                native_rejection_ns=arm + 10_000_000 if veto == 'native_rejection' else 0)
  assert not intent.allowed_latch and intent._pending_main_ns == 0


@pytest.mark.parametrize('observation', ('expired', 'invalid_event', 'missing_fault_result'))
def test_pending_temporary_main_does_not_arm_from_unqualified_clear(observation):
  intent, state, arm = pending_restricted_main()
  state.steerFaultTemporary = False
  now = arm + (200_000_001 if observation == 'expired' else 10_000_000)
  if observation != 'invalid_event':
    observe_main_event(intent, state, now)
  else:
    sm = SimpleNamespace(logMonoTime={'onroadEvents': now}, valid={'onroadEvents': False})
    intent.observe_startup_readiness(sm, state, now_ns=now)
  intent.update(state, now_ns=now, fault_active=None if observation == 'missing_fault_result' else False)
  assert not intent.allowed_latch
