import math
import pytest
from unittest.mock import patch
from openpilot.starpilot.car.subaru.tests.native_fixture import subaru_native_pair as subaru_native_pair
import unittest
from types import SimpleNamespace
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.values import CAR
from openpilot.selfdrive.controls.lib.lateral_controller_factory import create_lateral_controller
from openpilot.starpilot.lateral.controller_selection import ControllerMode


class TestSubaruAngleFactoryTune(unittest.TestCase):
  def test_actual_factory_selects_ascent_shaping_and_preserves_siblings(self):
    for car in (CAR.SUBARU_ASCENT_2023, CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_IMPREZA_2025, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_OUTBACK_2023):
      cp = CarInterface.get_params(car, gen_empty_fingerprint(), [], False, False, False)
      controller = create_lateral_controller(cp, None, .01, mode=ControllerMode.STANDARD)
      state = structs.CarState(vEgo=5., steeringAngleDeg=0.)
      vm = SimpleNamespace(get_steer_from_curvature=lambda *args: math.radians(10.))
      params = SimpleNamespace(roll=0., angleOffsetDeg=0.)
      _, target, _ = controller.update(True, state.as_reader(), vm, params, False, 0., False, .1)
      if car == CAR.SUBARU_ASCENT_2023:
        self.assertGreater(target, 0.)
        self.assertLess(target, 10.)
        controller.update(False, state.as_reader(), vm, params, False, 0., False, .1)
        self.assertEqual(controller.ascent_angle_target, 0.)
        state.vEgo = 25.
        _, target, _ = controller.update(True, state.as_reader(), vm, params, False, 0., False, .1)
        self.assertAlmostEqual(target, 12.5)
      else:
        self.assertEqual(target, 10.)



def test_frozen_dom_vectors_and_driver_bypass():
  from openpilot.starpilot.car.subaru.angle_tune import _ascent_angle_tracking_target as tracking
  from openpilot.starpilot.car.subaru.angle_tune import _ascent_low_speed_angle_target as low_speed
  assert tracking(10., 0., 25., False) == 12.5
  assert tracking(100., 0., 25., False) == 108.
  assert tracking(-100., 0., 25., False) == -108.
  assert tracking(10., 0., 5., False) == 10.
  assert tracking(10., 0., 25., True) == 10.
  assert low_speed(10., 0., 0., False, .01) == pytest.approx(0.7216494845360825)
  assert low_speed(10., 0., 5., False, .01) == pytest.approx(1.3461538461538463)
  assert low_speed(10., 0., 10., False, .01) == 10.
  assert low_speed(10., 0., 0., True, .01) == 10.


@pytest.mark.parametrize('low_speed', [False, True])
def test_actual_caller_to_native_with_ascent_target_shaping(low_speed, subaru_native_pair, tmp_path):
  from openpilot.starpilot.car.subaru.tests import test_stock_aol_callers as actual
  from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle
  original_frames = actual.CompleteCan.frames
  original_update = LatControlAngle.update
  seen = []

  def frames(stream, values, *, tick, **kwargs):
    values = {name: dict(fields) for name, fields in values.items()}
    if low_speed:
      values['Wheel_Speeds'] = dict.fromkeys(('FL', 'FR', 'RL', 'RR'), 18)
    return original_frames(stream, values, tick=tick, **kwargs)

  def observe(controller, active, CS, VM, params, *args, **kwargs):
    previous = controller.ascent_angle_target
    result = original_update(controller, active, CS, VM, params, *args, **kwargs)
    if active:
      raw_curvature = args[1] if len(args) > 1 else kwargs['desired_curvature']
      raw = math.degrees(VM.get_steer_from_curvature(-raw_curvature, CS.vEgo, params.roll)) + params.angleOffsetDeg
      seen.append((CS.vEgo, raw, result[1], previous, controller.ascent_angle_target))
    else:
      assert controller.ascent_angle_target == float(CS.steeringAngleDeg)
    return result

  with patch.object(actual.CompleteCan, 'frames', new=frames), patch.object(LatControlAngle, 'update', new=observe):
    actual.test_actual_full_engage_brake_reverse_temporary_recovery_mainoff(CAR.SUBARU_ASCENT_2023, subaru_native_pair, tmp_path)
  assert len(seen) > 100
  assert any(abs(raw - shaped) > 1e-5 for _, raw, shaped, _, _ in seen)
  if low_speed:
    assert any(abs(raw - filtered) > 1e-5 for _, raw, _, _, filtered in seen)
  else:
    assert all(filtered == pytest.approx(raw) for _, raw, _, _, filtered in seen)
