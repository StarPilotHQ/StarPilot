import itertools
import numpy as np

from openpilot.common.test import OpenpilotTestCase
from openpilot.common.parameterized import parameterized_class
from openpilot.cereal import log
from openpilot.selfdrive.car.cruise import VCruiseHelper, V_CRUISE_MIN, V_CRUISE_MAX, V_CRUISE_INITIAL, IMPERIAL_INCREMENT
from opendbc.car.structs import car
from openpilot.common.constants import CV
from openpilot.selfdrive.test.longitudinal_maneuvers.maneuver import Maneuver

ButtonEvent = car.CarState.ButtonEvent
ButtonType = car.CarState.ButtonEvent.Type


def run_cruise_simulation(cruise, e2e, personality, t_end=20.):
  man = Maneuver(
    '',
    duration=t_end,
    initial_speed=max(cruise - 1., 0.0),
    lead_relevancy=True,
    initial_distance_lead=100,
    cruise_values=[cruise],
    prob_lead_values=[0.0],
    breakpoints=[0.],
    e2e=e2e,
    personality=personality,
  )
  valid, output = man.evaluate()
  assert valid
  return output[-1, 3]


@parameterized_class(("e2e", "personality", "speed"), itertools.product(
                      [True, False], # e2e
                      log.LongitudinalPersonality.schema.enumerants, # personality
                      [5,35])) # speed
class TestCruiseSpeed(OpenpilotTestCase):
  def test_cruise_speed(self):
    print(f'Testing {self.speed} m/s')
    cruise_speed = float(self.speed)

    simulation_steady_state = run_cruise_simulation(cruise_speed, self.e2e, self.personality)
    self.assertAlmostEqual(simulation_steady_state, cruise_speed, delta=.01, msg=f'Did not reach {self.speed} m/s')


# TODO: test pcmCruise
@parameterized_class(('pcm_cruise',), [(False,)])
class TestVCruiseHelper(OpenpilotTestCase):
  def setup_method(self):
    self.CP = car.CarParams(pcmCruise=self.pcm_cruise)
    self.v_cruise_helper = VCruiseHelper(self.CP)
    self.reset_cruise_speed_state()

  def reset_cruise_speed_state(self):
    # Two resets previous cruise speed
    for _ in range(2):
      self.v_cruise_helper.update_v_cruise(car.CarState(cruiseState={"available": False}), enabled=False, is_metric=False)

  def enable(self, v_ego, experimental_mode):
    # Simulates user pressing set with a current speed
    self.v_cruise_helper.initialize_v_cruise(car.CarState(vEgo=v_ego), experimental_mode)

  def test_adjust_speed(self):
    """
    Asserts speed changes on falling edges of buttons.
    """

    self.enable(V_CRUISE_INITIAL * CV.KPH_TO_MS, False)

    for btn in (ButtonType.accelCruise, ButtonType.decelCruise):
      for pressed in (True, False):
        CS = car.CarState(cruiseState={"available": True})
        CS.buttonEvents = [ButtonEvent(type=btn, pressed=pressed)]

        self.v_cruise_helper.update_v_cruise(CS, enabled=True, is_metric=False)
        assert pressed == (self.v_cruise_helper.v_cruise_kph == self.v_cruise_helper.v_cruise_kph_last)

  def test_rising_edge_enable(self):
    """
    Some car interfaces may enable on rising edge of a button,
    ensure we don't adjust speed if enabled changes mid-press.
    """

    # NOTE: enabled is always one frame behind the result from button press in controlsd
    for enabled, pressed in ((False, False),
                             (False, True),
                             (True, False)):
      CS = car.CarState(cruiseState={"available": True})
      CS.buttonEvents = [ButtonEvent(type=ButtonType.decelCruise, pressed=pressed)]
      self.v_cruise_helper.update_v_cruise(CS, enabled=enabled, is_metric=False)
      if pressed:
        self.enable(V_CRUISE_INITIAL * CV.KPH_TO_MS, False)

      # Expected diff on enabling. Speed should not change on falling edge of pressed
      assert (not pressed) == (self.v_cruise_helper.v_cruise_kph == self.v_cruise_helper.v_cruise_kph_last)

  def test_resume_in_standstill(self):
    """
    Asserts we don't increment set speed if user presses resume/accel to exit cruise standstill.
    """

    self.enable(0, False)

    for standstill in (True, False):
      for pressed in (True, False):
        CS = car.CarState(cruiseState={"available": True, "standstill": standstill})
        CS.buttonEvents = [ButtonEvent(type=ButtonType.accelCruise, pressed=pressed)]
        self.v_cruise_helper.update_v_cruise(CS, enabled=True, is_metric=False)

        # speed should only update if not at standstill and button falling edge
        should_equal = standstill or pressed
        assert should_equal == (self.v_cruise_helper.v_cruise_kph == self.v_cruise_helper.v_cruise_kph_last)

  def test_set_gas_pressed(self):
    """
    Asserts pressing set while enabled with gas pressed sets
    the speed to the maximum of vEgo and current cruise speed.
    """

    for v_ego in np.linspace(0, 100, 101):
      self.reset_cruise_speed_state()
      self.enable(V_CRUISE_INITIAL * CV.KPH_TO_MS, False)

      # first decrement speed, then perform gas pressed logic
      expected_v_cruise_kph = self.v_cruise_helper.v_cruise_kph - IMPERIAL_INCREMENT
      expected_v_cruise_kph = max(expected_v_cruise_kph, v_ego * CV.MS_TO_KPH)  # clip to min of vEgo
      expected_v_cruise_kph = float(np.clip(round(expected_v_cruise_kph, 1), V_CRUISE_MIN, V_CRUISE_MAX))

      CS = car.CarState(vEgo=float(v_ego), gasPressed=True, cruiseState={"available": True})
      CS.buttonEvents = [ButtonEvent(type=ButtonType.decelCruise, pressed=False)]
      self.v_cruise_helper.update_v_cruise(CS, enabled=True, is_metric=False)

      # TODO: fix skipping first run due to enabled on rising edge exception
      if v_ego == 0.0:
        continue
      assert expected_v_cruise_kph == self.v_cruise_helper.v_cruise_kph

  def test_initialize_v_cruise(self):
    """
    Asserts allowed cruise speeds on enabling with SET.
    """

    for experimental_mode in (True, False):
      for v_ego in np.linspace(0, 100, 101):
        self.reset_cruise_speed_state()
        assert not self.v_cruise_helper.v_cruise_initialized

        self.enable(float(v_ego), experimental_mode)
        assert V_CRUISE_INITIAL <= self.v_cruise_helper.v_cruise_kph <= V_CRUISE_MAX
        assert self.v_cruise_helper.v_cruise_initialized


class TestResumeInitialization(OpenpilotTestCase):
  def test_card_preserves_resume_across_intervening_state(self):
    from unittest.mock import patch
    from types import SimpleNamespace
    from typing import Any, cast
    from openpilot.selfdrive.car.card import Car

    for stored in (92.8, 65 * CV.MPH_TO_KPH):
      for explicit in (None, False, True):
        owner = cast(Any, Car.__new__(Car))
        owner.v_cruise_helper = VCruiseHelper(car.CarParams(pcmCruise=False))
        owner.v_cruise_helper.v_cruise_kph = stored
        owner.sm = {'carControl': SimpleNamespace(enabled=False)}
        owner.CC_prev = SimpleNamespace(enabled=False)
        owner.experimental_mode = False
        owner.initial_cruise_speed = V_CRUISE_INITIAL
        owner.is_metric = False
        owner.vehicle_startup = SimpleNamespace(consume_cruise_resume=lambda value=explicit: value)
        release = car.CarState(cruiseState={'available': True}, buttonEvents=[
          ButtonEvent(type=ButtonType.accelCruise, pressed=False)])
        empty = car.CarState(cruiseState={'available': True})
        with patch('openpilot.selfdrive.car.cruise.monotonic_ns', return_value=1_000_000_000):
          owner.update_cruise_speed(release)
          owner.CS_prev = release
          owner.update_cruise_speed(empty)
          owner.CS_prev = empty
          owner.sm['carControl'].enabled = True
          owner.update_cruise_speed(empty)
        expected = V_CRUISE_INITIAL if explicit is False else stored
        self.assertAlmostEqual(owner.v_cruise_helper.v_cruise_kph, expected)
        self.assertEqual(owner.v_cruise_helper.pending_resume_ns, 0)

  def test_pending_resume_is_bounded_and_invalidated(self):
    from unittest.mock import patch
    for invalidation in ('expired', 'unavailable', 'set', 'cancel', 'main'):
      helper = VCruiseHelper(car.CarParams(pcmCruise=False))
      helper.v_cruise_kph = 65 * CV.MPH_TO_KPH
      release = car.CarState(cruiseState={'available': True}, buttonEvents=[
        ButtonEvent(type=ButtonType.accelCruise, pressed=False)])
      with patch('openpilot.selfdrive.car.cruise.monotonic_ns', return_value=1_000_000_000):
        helper.update_v_cruise(release, False, False)
      state = car.CarState(cruiseState={'available': invalidation != 'unavailable'})
      buttons = {'set': ButtonType.decelCruise, 'cancel': ButtonType.cancel, 'main': ButtonType.mainCruise}
      if invalidation in buttons:
        state.buttonEvents = [ButtonEvent(type=buttons[invalidation], pressed=True)]
      with patch('openpilot.selfdrive.car.cruise.monotonic_ns',
                 return_value=1_600_000_000 if invalidation == 'expired' else 1_010_000_000):
        helper.update_v_cruise(state, False, False)
        helper.initialize_v_cruise(car.CarState(), False)
      self.assertEqual(helper.v_cruise_kph, V_CRUISE_INITIAL)


class TestConfiguredInitialCruiseSpeed(OpenpilotTestCase):
  def test_optional_low_speed_start_is_set_only(self):
    software = VCruiseHelper(car.CarParams(pcmCruise=False, openpilotLongitudinalControl=True))
    stopped = car.CarState(vEgo=0)
    assert not software.low_speed_on_set
    software.initialize_v_cruise(stopped, False, resume=False)
    assert software.v_cruise_kph == V_CRUISE_INITIAL
    software.low_speed_on_set = True
    software.initialize_v_cruise(stopped, False, resume=False, initial_speed=65)
    assert software.v_cruise_kph == software.v_cruise_cluster_kph == V_CRUISE_MIN
    software.CP.minEnableSpeed = 6.5
    software.initialize_v_cruise(stopped, False, resume=False)
    assert software.v_cruise_kph == 24
    software.initialize_v_cruise(car.CarState(vEgo=25 * CV.KPH_TO_MS), False, resume=False)
    assert software.v_cruise_kph == 25
    software.v_cruise_kph_last = 65
    software.initialize_v_cruise(stopped, False, resume=True)
    assert software.v_cruise_kph == 65
    assert software.initialize_v_cruise(stopped, False, resume=False, slc_target_mps=50 * CV.KPH_TO_MS, is_metric=True)
    assert software.v_cruise_kph == 50
    software.low_speed_on_set = False
    software.initialize_v_cruise(stopped, False, resume=False, initial_speed=65)
    assert software.v_cruise_kph == 65
    factory = VCruiseHelper(car.CarParams(pcmCruise=True))
    factory.low_speed_on_set = True
    factory.v_cruise_kph = 48
    factory.initialize_v_cruise(stopped, False, resume=False)
    assert factory.v_cruise_kph == 48

  def test_initial_speed_floor(self):
    for experimental_mode in (True, False):
      for initial_speed in (40, 65, 100, 145):
        for v_ego in (0.0, 15.0, 40.0, 60.0):
          self.v_cruise_helper = VCruiseHelper(car.CarParams(pcmCruise=False))
          self.v_cruise_helper.initialize_v_cruise(car.CarState(vEgo=v_ego), experimental_mode,
                                                 resume=False, initial_speed=initial_speed)
          expected = round(np.clip(max(v_ego * CV.MS_TO_KPH, initial_speed), V_CRUISE_INITIAL, V_CRUISE_MAX))
          assert self.v_cruise_helper.v_cruise_kph == expected

  def test_configured_floor_does_not_override_resume(self):
    self.v_cruise_helper = VCruiseHelper(car.CarParams(pcmCruise=False))
    self.v_cruise_helper.initialize_v_cruise(car.CarState(vEgo=70 * CV.KPH_TO_MS), False)
    self.v_cruise_helper.v_cruise_kph_last = 70
    self.v_cruise_helper.initialize_v_cruise(car.CarState(vEgo=0), False, resume=True, initial_speed=100)
    assert self.v_cruise_helper.v_cruise_kph == 70

  def test_invalid_initial_speed_preserves_safe_floor(self):
    for invalid in (None, True, False, "80", 80.0, 0, 39, 146):
      helper = VCruiseHelper(car.CarParams(pcmCruise=False))
      helper.initialize_v_cruise(car.CarState(vEgo=0), False, resume=False, initial_speed=invalid)
      assert helper.v_cruise_kph == V_CRUISE_INITIAL
