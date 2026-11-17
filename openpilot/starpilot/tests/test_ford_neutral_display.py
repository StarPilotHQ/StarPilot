import pytest
from openpilot.starpilot.tests.ford_lifecycle_helpers import FORD_FACTORIES, ford_joined as ford_joined, run_actual_producer


@pytest.fixture(autouse=True)
def configuration(monkeypatch, request):
  monkeypatch.ford_topology = request.node.callspec.params['topology']


@pytest.mark.parametrize('identity', [FORD_FACTORIES[11]])
@pytest.mark.parametrize('alpha', [True])
@pytest.mark.parametrize('hands_free', [False, True])
@pytest.mark.parametrize('topology', ['automatic', 'manual-multi'])
def test_main_off_neutral_cluster_and_actual_native_reengagement(ford_joined, monkeypatch, topology):
  monkeypatch.ford_native_10hz = True
  run_actual_producer(ford_joined, monkeypatch, scenario='neutral-main-cadence')
