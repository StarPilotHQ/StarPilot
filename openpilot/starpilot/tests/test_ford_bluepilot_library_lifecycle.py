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


@pytest.mark.parametrize('identity,alpha,hands_free', [
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True),
])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
@pytest.mark.parametrize('mode', ['angle', 'curvature'])
@pytest.mark.parametrize('pinion', [False, True])
def test_gas_held_first_engagement(ford_joined, monkeypatch, mode, pinion):
  run_actual_producer(ford_joined, monkeypatch, scenario='bluepilot-gas')



@pytest.mark.parametrize('identity,alpha,hands_free,topology,mode,pinion', [
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True, 'automatic', 'angle', True),
])
def test_sustained_real_loop_lag_still_disables_ford(ford_joined, monkeypatch):
  monkeypatch.ford_sustained_lag = True
  run_actual_producer(ford_joined, monkeypatch, scenario='bluepilot-gas')


@pytest.mark.parametrize('identity,alpha,hands_free,topology,pinion', [
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True, 'automatic', False),
])
@pytest.mark.parametrize('mode', ['curvature', 'angle'])
@pytest.mark.parametrize('phase', range(10))
def test_cancel_at_every_native_receipt_phase(ford_joined, monkeypatch, phase):
  monkeypatch.ford_cancel_phase = phase
  run_actual_producer(ford_joined, monkeypatch, scenario='cancel-native-cadence')


@pytest.mark.parametrize('identity,alpha,hands_free,topology,pinion', [
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True, 'automatic', False),
])
@pytest.mark.parametrize('mode', ['curvature', 'angle'])
def test_manual_eps_refusal_keeps_aol_intent_without_steering_through_fault(ford_joined, monkeypatch):
  monkeypatch.ford_eps_alpha = True
  run_actual_producer(ford_joined, monkeypatch, scenario='route45')


@pytest.mark.parametrize('identity,alpha,hands_free,topology,pinion', [
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True, 'automatic', False),
])
@pytest.mark.parametrize('mode', ['curvature', 'angle'])
def test_cruise_resume_preserves_deliberate_lateral_pause(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='bluepilot-pause')


@pytest.mark.parametrize('identity,alpha,hands_free,topology,pinion', [
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True, 'automatic', False),
])
@pytest.mark.parametrize('mode', ['curvature', 'angle'])
def test_manual_steering_retires_held_turn_limit_alert(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='bluepilot-manual-alert')


@pytest.mark.parametrize('identity,alpha,hands_free,topology,pinion', [
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True, 'automatic', False),
])
@pytest.mark.parametrize('mode', ['curvature', 'angle'])
@pytest.mark.parametrize('brake', [False, True], ids=['cancel', 'brake'])
def test_fresh_cruise_withdrawal_after_previous_output_expires(ford_joined, monkeypatch, brake):
  monkeypatch.ford_gap_brake = brake
  run_actual_producer(ford_joined, monkeypatch, scenario='bluepilot-cancel-gap')


@pytest.mark.parametrize('identity,alpha,hands_free,topology,mode,pinion', [
  ((CAR.FORD_MUSTANG_MACH_E_MK1, 2), True, True, 'automatic', 'curvature', False),
])
@pytest.mark.parametrize('speed', [12.4, 13.6, 16., 25.])
@pytest.mark.parametrize('sign', [-1., 1.])
@pytest.mark.parametrize('gas', [False, True])
@pytest.mark.parametrize('manual', [False, True])
def test_reengaging_in_a_manual_curve_respects_native_convergence(ford_joined, monkeypatch, speed, sign, gas, manual):
  monkeypatch.ford_curve_speed = speed
  monkeypatch.ford_curve_sign = sign
  monkeypatch.ford_curve_gas = gas
  monkeypatch.ford_curve_manual = manual
  run_actual_producer(ford_joined, monkeypatch, scenario='curvature-handoff')
