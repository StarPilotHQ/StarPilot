import pytest
from openpilot.starpilot.tests.ford_lifecycle_helpers import FORD_FACTORIES, ford_joined as ford_joined, run_actual_producer


@pytest.fixture(autouse=True)
def configuration(monkeypatch, request):
  monkeypatch.ford_topology = request.node.callspec.params['topology']
  mode = request.node.callspec.params.get('mode')
  if mode is not None:
    monkeypatch.ford_angle = mode != 'curvature'
    monkeypatch.ford_starpilot = mode == 'starpilot'
    monkeypatch.ford_pinion = True


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
def test_native_first_brake_while_already_stopped(ford_joined, monkeypatch, topology):
  monkeypatch.ford_standstill_brake = True
  monkeypatch.ford_native_first_pedal = True
  monkeypatch.ford_native_10hz = True
  run_actual_producer(ford_joined, monkeypatch, scenario='brake-native-cadence')


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha,hands_free,topology', [(True, True, 'automatic')])
@pytest.mark.parametrize('mode', ['angle', 'curvature', 'starpilot'])
@pytest.mark.parametrize('phase', range(10))
def test_brake_retains_lateral_with_health_before_native_axis_status(ford_joined, monkeypatch, mode, phase, topology):
  monkeypatch.ford_native_10hz = True
  monkeypatch.ford_native_health_first = True
  monkeypatch.ford_brake_phase = phase
  run_actual_producer(ford_joined, monkeypatch, scenario='brake-native-cadence')
