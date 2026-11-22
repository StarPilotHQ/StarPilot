"""Detected Volvo CP uses the actual Galaxy gateway without manual selection."""
import pytest
from openpilot.starpilot.galaxy.tests.test_auto_vehicle_population import detected as detected, publish
from openpilot.starpilot.galaxy.vehicle_selection import VehicleSelectionGateway
from openpilot.starpilot.aol.vehicle import policy_for


@pytest.mark.parametrize('identity', ['VOLVO_V40', 'VOLVO_XC40_RECHARGE', 'VOLVO_S60_RECHARGE', 'POLESTAR_2', 'VOLVO_V60'])
def test_actual_detected_gateway_preserves_auto_and_no_false_torque_learning(detected, identity):
  params, _, _, source, gateway = detected
  params.put_bool('ForceAutoTuneOff', True, block=True)
  cp = publish(detected, identity)
  assert source.sample().cp.carFingerprint == identity
  selection = VehicleSelectionGateway(params, source)
  page = selection.page('session', b'generation')
  assert page['selectionMode'] == 'auto'
  assert page['reported']['platform'] == identity
  assert page['reported']['origin'] == 'detected'
  assert params.get('VehicleSelection') is None
  torque = gateway.page('torque', 'session', b'generation')
  assert all(row['label'] != 'Use StarPilot Auto Learn' for row in torque['rows'])
  assert params.get_bool('ForceAutoTuneOff')
  assert policy_for(cp).intent_supported == (identity == 'VOLVO_V60')
