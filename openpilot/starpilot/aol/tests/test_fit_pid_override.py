from types import SimpleNamespace

import pytest

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID


@pytest.mark.parametrize('identity', (CAR.HONDA_FIT, CAR.HONDA_HRV))
@pytest.mark.parametrize('transition', ('driver_override', 'inactive'))
def test_fit_pid_override_discards_stale_integral_without_changing_authority(identity, transition):
  cp = CarInterface.get_non_essential_params(identity)
  control = LatControlPID(cp, CarInterface(cp), 0.01)
  model = VehicleModel(cp)
  params = SimpleNamespace(angleOffsetDeg=0., roll=0.)
  state = SimpleNamespace(vEgo=13., steeringAngleDeg=0., steeringRateDeg=0., steeringPressed=False)
  control.pid.i = 0.43
  if transition == 'driver_override':
    state.steeringPressed = True
    steer, _, status = control.update(True, state, model, params, False, 0., False, 0.)
    assert status.active
    assert steer == pytest.approx(0. if identity == CAR.HONDA_FIT else 0.43)
  else:
    control.reset()
    steer, _, status = control.update(False, state, model, params, False, 0., False, 0.)
    assert not status.active and steer == 0.
  state.steeringPressed = False
  steer, _, status = control.update(True, state, model, params, False, 0., False, 0.)
  assert status.active
  assert steer == pytest.approx(0. if identity == CAR.HONDA_FIT else 0.43)
  assert status.i == pytest.approx(steer)
  steer, _, _ = control.update(True, state, model, params, False, 0.05, False, 0.)
  assert abs(steer) <= control.steer_max
