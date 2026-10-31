"""StarPilot's actual callers qualify the unchanged BluePilot library and native safety."""
import pytest

from opendbc.car.ford.values import CAR, FordFlags
from openpilot.starpilot.tests.ford_lifecycle_helpers import ford_joined as ford_joined, run_actual_producer


@pytest.fixture(autouse=True)
def library_settings(monkeypatch, mode, pinion, topology):
  monkeypatch.ford_bluepilot = True
  monkeypatch.ford_angle = mode == 'angle'
  monkeypatch.ford_pinion = pinion
  monkeypatch.ford_topology = topology
  monkeypatch.ford_nonzero = True
  monkeypatch.ford_native_first_pedal = True
  monkeypatch.ford_native_10hz = True


@pytest.mark.parametrize('identity', [(CAR.FORD_MUSTANG_MACH_E_MK1, 2), (CAR.FORD_F_150_MK14, 2), (CAR.FORD_ESCAPE_MK4, 0)])
@pytest.mark.parametrize('alpha', [False, True])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
@pytest.mark.parametrize('mode', ['curvature', 'angle'])
@pytest.mark.parametrize('pinion', [False, True])
def test_library_angle_curvature_aol_native_lifecycle(ford_joined, monkeypatch):
  assert ford_joined.host.CI.CC.bp.bp_lateral_allowed
  run_actual_producer(ford_joined, monkeypatch, scenario='configuration-parity')
  assert ford_joined.nonzero_after_brake > 0
  if ford_joined.host.CP.flags & FordFlags.CANFD:
    assert ford_joined.active_ui_frames > 0


@pytest.mark.parametrize('identity,alpha,hands_free,topology,pinion', [
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True, 'automatic', False),
])
@pytest.mark.parametrize('mode', ['curvature', 'angle'])
def test_model_loss_still_withdraws_library_output_through_actual_callers(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='model-loss')
