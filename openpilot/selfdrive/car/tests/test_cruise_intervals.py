"""Saved cruise intervals follow software SET ownership, including SLC on PCM cars."""

import itertools
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.structs import car
from opendbc.car.values import BRANDS
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.selfdrive.car.cruise import CRUISE_LONG_PRESS, IMPERIAL_INCREMENT, SlcPendingConfirmation, VCruiseHelper, slc_target_kph
from openpilot.starpilot.longitudinal.cruise_intervals import CruiseIntervals, read_cruise_intervals


Button = car.CarState.ButtonEvent.Type


def state(button=None, pressed=False, *, standstill=False, native=0.):
  events = [] if button is None else [car.CarState.ButtonEvent(type=button, pressed=pressed)]
  return car.CarState(canValid=True, standstill=standstill,
                      cruiseState={"available": True, "enabled": True, "standstill": standstill,
                                   "speed": native / 3.6, "speedCluster": native / 3.6}, buttonEvents=events)


class TestCruiseIntervals(unittest.TestCase):
  def setUp(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    self.params = Params(temp.name)
    self.cp = car.CarParams()

  def test_saved_children_ignore_retired_parent_and_pcm_engagement(self):
    self.params.put("CustomCruise", 5.0, block=True)
    self.params.put("CustomCruiseLong", 1.0, block=True)
    self.assertEqual(read_cruise_intervals(self.params, self.cp), CruiseIntervals(5, 1))
    self.params.put_bool("QOLLongitudinal", True, block=True)
    self.assertEqual(read_cruise_intervals(self.params, self.cp), CruiseIntervals(5, 1))
    self.cp.pcmCruise = True
    self.assertEqual(read_cruise_intervals(self.params, self.cp), CruiseIntervals(5, 1))
    self.params.put_bool("QOLLongitudinal", False, block=True)
    self.assertEqual(read_cruise_intervals(self.params, self.cp), CruiseIntervals(5, 1))

  def test_invalid_children_fall_back_without_enabling_other_features(self):
    self.params.put_bool("QOLLongitudinal", True, block=True)
    Path(self.params.get_param_path("CustomCruise")).write_bytes(b"nan")
    Path(self.params.get_param_path("CustomCruiseLong")).write_bytes(b"200")
    self.assertEqual(read_cruise_intervals(self.params, self.cp), CruiseIntervals())

  def test_slc_preserves_software_intervals_and_native_pcm_set(self):
    brands = [brand.__module__.split('.')[-2] for brand in BRANDS if brand.__module__.split('.')[-2] != 'toyota'] + ['future']
    self.params.put("CustomCruise", 3.0, block=True)
    self.params.put("CustomCruiseLong", 7.0, block=True)
    self.params.put_bool("ReverseCruise", True, block=True)
    for brand, pcm, metric, button, held in itertools.product(
      brands, (False, True), (False, True), (Button.accelCruise, Button.decelCruise), (False, True),
    ):
      with self.subTest(brand=brand, pcm=pcm, metric=metric, button=button, held=held):
        cp = car.CarParams(brand=brand, carFingerprint='software-set-test', pcmCruise=pcm, openpilotLongitudinalControl=True)
        helper = VCruiseHelper(cp)
        initial = 60. if metric else 50 * IMPERIAL_INCREMENT
        helper.v_cruise_kph = helper.v_cruise_cluster_kph = initial
        helper.intervals = read_cruise_intervals(self.params, cp)
        helper.update_v_cruise(state(button, True, native=140.), True, metric, slc_enabled=True)
        if pcm:
          helper.update_v_cruise(state(button, False, native=140.), True, metric, slc_enabled=True)
          self.assertAlmostEqual(helper.v_cruise_kph, 140., places=4)
          self.assertIsNone(helper.slc_cruise_change)
          continue
        if held:
          for _ in range(CRUISE_LONG_PRESS):
            helper.update_v_cruise(state(native=140.), True, metric, slc_enabled=True)
          self.assertIsNotNone(helper.slc_cruise_change)
        helper.update_v_cruise(state(button, False, native=140.), True, metric, slc_enabled=True)
        increment = (7 if held else 3) * (1 if metric else IMPERIAL_INCREMENT)
        target = initial + (increment if button == Button.accelCruise else -increment)
        self.assertAlmostEqual(helper.v_cruise_kph, target)
        self.assertAlmostEqual(helper.v_cruise_cluster_kph, target)
        if held:
          self.assertIsNone(helper.slc_cruise_change)

  def test_pcm_stock_longitudinal_keeps_factory_set_speed_on_every_make(self):
    self.params.put("CustomCruise", 3.0, block=True)
    self.params.put("CustomCruiseLong", 7.0, block=True)
    self.params.put_bool("ReverseCruise", True, block=True)
    for brand in BRANDS:
      with self.subTest(brand=brand):
        cp = car.CarParams(brand=brand.__module__.split('.')[-2], carFingerprint='stock-set-test', pcmCruise=True)
        helper = VCruiseHelper(cp)
        helper.intervals = read_cruise_intervals(self.params, cp)
        for pressed in (True, False):
          helper.update_v_cruise(state(Button.decelCruise, pressed, native=100.), True, True, slc_enabled=True)
          self.assertAlmostEqual(helper.v_cruise_kph, 100., places=4)
          self.assertAlmostEqual(helper.v_cruise_cluster_kph, 100., places=4)

  def test_card_worker_refreshes_toyota_and_other_pcm_preferences(self):
    from openpilot.selfdrive.car.card import Car
    from openpilot.starpilot.controllers.toyota_cruise import ToyotaCruisePreference

    for brand in ('toyota', 'hyundai'):
      cp = car.CarParams(brand=brand, carFingerprint='software-set-test', pcmCruise=True, openpilotLongitudinalControl=True)
      host = Car.__new__(Car)
      host.CP, host.params = cp, self.params
      host.CI = cast(CarInterfaceBase, SimpleNamespace(CC=None))
      host.aol_card_intent = None
      host.v_cruise_helper = VCruiseHelper(cp)
      for swapped in (False, True, False):
        with self.subTest(brand=brand, swapped=swapped):
          self.params.put_bool('ReverseCruise', swapped, block=True)
          self.params.put('CustomCruise', 3. if swapped else 2., block=True)
          self.params.put('CustomCruiseLong', 7. if swapped else 4., block=True)
          stop = threading.Event()
          with patch.object(host, 'read_slc_configuration', return_value=None), \
               patch('openpilot.selfdrive.car.card.time.sleep', side_effect=lambda _, stop=stop: stop.set()):
            host.params_thread(stop)
          expected = CruiseIntervals(5, 1) if swapped else CruiseIntervals()
          if brand != 'toyota':
            expected = CruiseIntervals(3, 7) if swapped else CruiseIntervals(2, 4)
          self.assertEqual(host.v_cruise_helper.intervals, expected)
          if brand == 'toyota':
            self.assertEqual(ToyotaCruisePreference(cp, self.params).update(), swapped)

  def helper(self, *, metric=True, custom=True, initial=67.0, pcm=False):
    helper = VCruiseHelper(car.CarParams(pcmCruise=pcm))
    helper.v_cruise_kph = initial
    if custom:
      helper.intervals = CruiseIntervals(5, 1)
    return helper

  @staticmethod
  def step(helper, button=None, pressed=False, *, metric=True, pending=None, standstill=False):
    helper.update_v_cruise(state(button, pressed, standstill=standstill), True, metric, pending)

  def test_metric_short_and_held_accel_and_decel(self):
    for button, short_target, held_target in ((Button.accelCruise, 70, 71), (Button.decelCruise, 65, 64)):
      with self.subTest(button=button):
        helper = self.helper()
        self.step(helper, button, True)
        self.step(helper, button, False)
        self.assertEqual(helper.v_cruise_kph, short_target)
        self.assertEqual(helper.slc_cruise_change[2:], ("accel" if button == Button.accelCruise else "decel", False))
        self.step(helper, button, True)
        for _ in range(CRUISE_LONG_PRESS - 1):
          self.step(helper)
        self.step(helper)
        self.assertEqual(helper.v_cruise_kph, held_target)
        self.step(helper, button, False)
        self.assertEqual(helper.v_cruise_kph, held_target)
        self.assertIsNone(helper.slc_cruise_change)

  def test_imperial_display_unit_increments(self):
    helper = self.helper(initial=50 * IMPERIAL_INCREMENT)
    self.step(helper, Button.accelCruise, True, metric=False)
    self.step(helper, Button.accelCruise, False, metric=False)
    self.assertAlmostEqual(helper.v_cruise_kph, 55 * IMPERIAL_INCREMENT)
    self.step(helper, Button.accelCruise, True, metric=False)
    for _ in range(CRUISE_LONG_PRESS):
      self.step(helper, metric=False)
    self.assertAlmostEqual(helper.v_cruise_kph, 56 * IMPERIAL_INCREMENT)

  def test_five_unit_steps_from_adopted_whole_mph_set_speed(self):
    initial = slc_target_kph(50 * CV.MPH_TO_MS, False)
    for intervals, button, held in itertools.product(
      (CruiseIntervals(), CruiseIntervals(5, 1)), (Button.accelCruise, Button.decelCruise), (False, True),
    ):
      with self.subTest(intervals=intervals, button=button, held=held):
        helper = self.helper(initial=initial)
        helper.intervals = intervals
        self.step(helper, button, True, metric=False)
        if held:
          for _ in range(CRUISE_LONG_PRESS):
            self.step(helper, metric=False)
        self.step(helper, button, False, metric=False)
        interval = intervals.held if held else intervals.short
        sign = 1 if button == Button.accelCruise else -1
        self.assertAlmostEqual(helper.v_cruise_kph, initial + sign * interval * IMPERIAL_INCREMENT)
        self.assertEqual(round(helper.v_cruise_kph * CV.KPH_TO_MPH), 50 + sign * interval)

  def test_partial_five_unit_steps_reach_the_next_display_boundary(self):
    for mph, button, target in ((53, Button.accelCruise, 55), (53, Button.decelCruise, 50),
                                (89, Button.accelCruise, 90), (89, Button.decelCruise, 85)):
      with self.subTest(mph=mph, button=button):
        helper = self.helper(initial=slc_target_kph(mph * CV.MPH_TO_MS, False))
        self.step(helper, button, True, metric=False)
        self.step(helper, button, False, metric=False)
        self.assertEqual(helper.v_cruise_kph, slc_target_kph(target * CV.MPH_TO_MS, False))
        self.assertEqual(round(helper.v_cruise_kph * CV.KPH_TO_MPH), target)

  def test_default_pcm_standstill_and_slc_ownership(self):
    default = self.helper(custom=False, initial=60)
    self.step(default, Button.accelCruise, True)
    self.step(default, Button.accelCruise, False)
    self.assertEqual(default.v_cruise_kph, 61)

    pcm = self.helper(pcm=True, initial=60)
    self.step(pcm, Button.accelCruise, True)
    self.step(pcm, Button.accelCruise, False)
    self.assertEqual(pcm.v_cruise_kph, 255)
    for pressed in (True, False):
      pcm_state = state(Button.accelCruise, pressed)
      pcm_state.cruiseState.speed = 20.0
      pcm_state.cruiseState.speedCluster = 21.0
      pcm.update_v_cruise(pcm_state, True, True)
      self.assertEqual(pcm.v_cruise_kph, 72.0)
      self.assertAlmostEqual(pcm.v_cruise_cluster_kph, 75.6)

    standstill = self.helper(initial=60)
    self.step(standstill, Button.accelCruise, True, standstill=True)
    self.step(standstill, Button.accelCruise, False)
    self.assertEqual(standstill.v_cruise_kph, 60)

    slc = self.helper(initial=60)
    pending = SlcPendingConfirmation("s", 1, 1)
    self.step(slc, Button.accelCruise, True, pending=pending)
    self.step(slc, Button.accelCruise, False)
    self.assertEqual(slc.v_cruise_kph, 60)
    self.assertTrue(slc.slc_released_suppressed)
