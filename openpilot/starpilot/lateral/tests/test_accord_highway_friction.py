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
  (61., 0.2, False, 0.65),
  (54., 0.2, False, 0.825),
  (61., 0.425, False, 0.825),
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
