from types import SimpleNamespace

import pytest

from opendbc.car import DT_CTRL
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR, HondaFlags
from opendbc.car.lateral import get_friction
from openpilot.common.constants import CV
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.starpilot.lateral.controller_selection import ControllerMode


@pytest.mark.parametrize("speed, setpoint, override, expected", [
  (61., 0.2, False, 0.20),
  (54., 0.2, False, 0.60),
  (61., 0.450, False, 0.60),
  (50., 0.2, False, 1.0),
  (61., 0.60, False, 1.0),
  (61., 0.2, True, 1.0),
])
def test_accord_highway_friction_envelope(speed, setpoint, override, expected):
  cp = CarInterface.get_non_essential_params(CAR.HONDA_ACCORD_9G)
  cp.flags |= int(HondaFlags.GAS_INTERCEPTOR)
  cp.openpilotLongitudinalControl = True
  CarInterface.configure_torque_tune(cp.carFingerprint, cp.lateralTuning)
  ci = CarInterface(cp)
  controller = LatControlTorque(cp.as_reader(), ci, DT_CTRL, controller_mode=ControllerMode.STARPILOT)
  cs = SimpleNamespace(vEgo=speed * CV.MPH_TO_MS, steeringPressed=override)
  baseline = get_friction(0.12, 0.0, 0.30, controller.torque_params)
  assert controller.friction(0.12, 0.0, 0.30, cs, setpoint) == pytest.approx(baseline * expected)


@pytest.mark.parametrize("mode, flags, long_active", [
  (ControllerMode.STANDARD, HondaFlags.GAS_INTERCEPTOR, True),
  (ControllerMode.STARPILOT, 0, True),
  (ControllerMode.STARPILOT, HondaFlags.GAS_INTERCEPTOR | HondaFlags.EPS_MODIFIED, True),
  (ControllerMode.STARPILOT, HondaFlags.GAS_INTERCEPTOR, False),
])
def test_accord_highway_friction_preserves_other_configurations(mode, flags, long_active):
  cp = CarInterface.get_non_essential_params(CAR.HONDA_ACCORD_9G)
  cp.flags |= int(flags)
  cp.openpilotLongitudinalControl = long_active
  CarInterface.configure_torque_tune(cp.carFingerprint, cp.lateralTuning)
  ci = CarInterface(cp)
  controller = LatControlTorque(cp.as_reader(), ci, DT_CTRL, controller_mode=mode)
  cs = SimpleNamespace(vEgo=61. * CV.MPH_TO_MS, steeringPressed=False)
  baseline = get_friction(-0.12, 0.0, 0.30, controller.torque_params)
  assert controller.friction(-0.12, 0.0, 0.30, cs, -0.2) == pytest.approx(baseline)


def test_accord_highway_feedforward_is_bounded_and_yields():
  cp = CarInterface.get_non_essential_params(CAR.HONDA_ACCORD_9G)
  cp.flags |= int(HondaFlags.GAS_INTERCEPTOR)
  cp.openpilotLongitudinalControl = True
  CarInterface.configure_torque_tune(cp.carFingerprint, cp.lateralTuning)
  controller = LatControlTorque(cp.as_reader(), CarInterface(cp), DT_CTRL, controller_mode=ControllerMode.STARPILOT)
  policy = controller.starpilot_extension.policy
  cs = SimpleNamespace(vEgo=59. * CV.MPH_TO_MS, steeringPressed=False, leftBlinker=False, rightBlinker=False)
  smooth = policy._accord_feedforward
  assert smooth(0., 0., cs, False) == 0.
  assert 0.12 - 1e-8 <= smooth(0.20, 0.20, cs, False) < 0.20
  assert smooth(-0.001, 0., cs, False) <= 0.
  for _ in range(100):
    on_curve = smooth(0.18, 0.18, cs, False)
  assert on_curve == pytest.approx(0.18, abs=1e-5)
  assert smooth(0.55, 0., cs, False) == 0.55
  assert smooth(0.04, 0., cs, False) == 0.04
  assert smooth(0.06, 0.55, cs, False) == 0.06
  assert smooth(0.01, 0., cs, False) == 0.01

  for name in ('steeringPressed', 'leftBlinker', 'rightBlinker'):
    setattr(cs, name, True)
    assert smooth(0.18, 0., cs, False) == 0.18
    setattr(cs, name, False)
    assert smooth(-0.08, 0., cs, False) == -0.08
  assert smooth(0.10, 0., cs, True) == 0.10
  assert smooth(0.02, 0., cs, False) == 0.02
  cs.vEgo = 50. * CV.MPH_TO_MS
  assert smooth(-0.05, 0., cs, False) == -0.05


def test_accord_feedforward_checkpoint_preserves_filter_and_signal_bypass():
  from opendbc.car import structs
  from opendbc.car.vehicle_model import VehicleModel
  from openpilot.starpilot.flm.controller_replay import _car_state, capture_snapshot, frame_input, restore_snapshot

  cp = CarInterface.get_non_essential_params(CAR.HONDA_ACCORD_9G)
  cp.flags |= int(HondaFlags.GAS_INTERCEPTOR)
  cp.openpilotLongitudinalControl = True
  CarInterface.configure_torque_tune(cp.carFingerprint, cp.lateralTuning)
  ci = CarInterface(cp)
  source = LatControlTorque(cp.as_reader(), ci, DT_CTRL, controller_mode=ControllerMode.STARPILOT)
  target = LatControlTorque(cp.as_reader(), ci, DT_CTRL, controller_mode=ControllerMode.STARPILOT)
  vm = VehicleModel(cp)
  cs = structs.CarState.new_message(vEgo=59. * CV.MPH_TO_MS)
  source_policy = source.starpilot_extension.policy
  target_policy = target.starpilot_extension.policy
  source_policy._accord_feedforward(0.0, 0.0, cs, False)
  source_policy._accord_feedforward(0.19, 0.19, cs, False)
  restore_snapshot(target, vm, capture_snapshot(source, vm))
  assert target_policy._accord_feedforward(0.15, 0.15, cs, False) == source_policy._accord_feedforward(0.15, 0.15, cs, False)

  cs.rightBlinker = True
  inputs = frame_input(cs, vm, SimpleNamespace(angleOffsetDeg=0., roll=0.), active=True, safety_limited=False,
                       desired_curvature=0., curvature_limited=False, lat_delay=0.35, capture_turn_signals=True)
  assert target_policy._accord_feedforward(0.21, 0.21, _car_state(dict(inputs)), False) == 0.21
