"""Route0a13721d7a3cb316/00000024--f79244cffe: Mach-E stock ACC, flags3, AE32.

Captured configuration and event order; synthetic CAN/perception/USB transport.
No VIN, location or native permission is injected. Physical acceptance is separate.
"""

import pytest
from opendbc.car.ford.values import CAR
from openpilot.starpilot.tests.ford_lifecycle_helpers import ford_joined as ford_joined, run_actual_producer


@pytest.mark.parametrize('identity', [(CAR.FORD_MUSTANG_MACH_E_MK1, 2)])
@pytest.mark.parametrize('alpha', [False])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('overlap', [False, True], ids=['isolated-eps', 'braking-during-eps'])
def test_route24_accelerator_at_engagement_full_brake_stock_off_fault_reverse_and_main_off(ford_joined, monkeypatch, overlap):
  run_actual_producer(ford_joined, monkeypatch, scenario='route24-mixed-eps' if overlap else 'route24')


@pytest.mark.parametrize('identity', [(CAR.FORD_MUSTANG_MACH_E_MK1, 2)])
@pytest.mark.parametrize('alpha', [False])
@pytest.mark.parametrize('hands_free', [False, True])
def test_route2a_new_engagement_after_previous_native_session_retains_aol_on_stock_off(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='route2a')


@pytest.mark.parametrize('identity', [(CAR.FORD_MUSTANG_MACH_E_MK1, 2)])
@pytest.mark.parametrize('alpha', [False])
@pytest.mark.parametrize('hands_free', [False, True])
def test_ford_main_only_rearm_after_previous_session_uses_current_feedback(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='route2a-main')


@pytest.mark.parametrize('identity', [(CAR.FORD_MUSTANG_MACH_E_MK1, 2)])
@pytest.mark.parametrize('alpha', [False])
@pytest.mark.parametrize('hands_free', [False, True])
def test_fault_at_main_edge_cannot_replay_after_native_health_recovers(ford_joined, monkeypatch):
  run_actual_producer(ford_joined, monkeypatch, scenario='route2a-main-fault')
