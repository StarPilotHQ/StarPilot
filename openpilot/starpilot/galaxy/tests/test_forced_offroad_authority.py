"""Effective offroad authority for configuration and operations."""

from pathlib import Path
import json

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import LiveContextSource, PAGES, SettingsGateway
from openpilot.starpilot.galaxy.tests.test_borrowed_authority import Messages
from openpilot.starpilot.vehicle_selection import encode


@pytest.fixture
def powered(tmp_path):
  params, messages = Params(str(tmp_path / 'params')), Messages()
  params.put_bool('IsOffroad', True, block=True)
  messages.data['pandaStates'][0].ignitionLine = True
  for service in ('carState', 'selfdriveState'):
    messages.seen[service] = messages.alive[service] = messages.valid[service] = False
  clocks = {'mono': 1_000_000_000, 'offset': 10_000_000_000}
  authority = LiveContextSource(params, messages=messages, borrowed_messages=True, evidence_wait_ms=0,
                               mono_clock=lambda: clocks['mono'], boot_clock=lambda: clocks['mono'] + clocks['offset'])
  clocks['mono'] = 2_100_000_000
  assert authority.configuration_allowed()
  yield authority, params, messages, clocks
  authority.close()
  messages.update.assert_not_called()
  messages.sock['carState'].close.assert_not_called()


@pytest.mark.parametrize('service', ['deviceState', 'pandaStates'])
@pytest.mark.parametrize('field', ['seen', 'alive', 'valid'])
def test_powered_offroad_requires_healthy_publishers(powered, service, field):
  authority, _, messages, _ = powered
  getattr(messages, field)[service] = False
  assert not authority.configuration_allowed()


@pytest.mark.parametrize('offroad', [b'0', b'', b'corrupt'])
def test_powered_offroad_requires_effective_manager_mode(powered, offroad):
  authority, params, _, _ = powered
  Path(params.get_param_path('IsOffroad')).write_bytes(offroad)
  assert not authority.configuration_allowed()


def test_powered_offroad_rejects_started_and_missing_panda(powered):
  authority, _, messages, _ = powered
  messages.data['deviceState'].started = True
  assert not authority.configuration_allowed()
  messages.data['deviceState'].started = False
  messages.data['pandaStates'] = []
  assert not authority.configuration_allowed()


@pytest.mark.parametrize('service', ['deviceState', 'pandaStates'])
def test_powered_offroad_rejects_future_and_stale_publisher_stamps(powered, service):
  authority, _, messages, clocks = powered
  original = messages.logMonoTime[service]
  messages.logMonoTime[service] = original + 1_000_000_000
  assert not authority.configuration_allowed()
  messages.logMonoTime[service] = original
  clocks['mono'] += 2_000_000_000
  assert not authority.configuration_allowed()


def test_powered_offroad_resume_requires_new_evidence(powered):
  authority, _, messages, clocks = powered
  assert authority.parked()
  clocks['offset'] += 1_000_000_000
  assert not authority.configuration_allowed()
  clocks['mono'] += 100_000_000
  for service in ('deviceState', 'pandaStates'):
    messages.logMonoTime[service] = clocks['mono'] - 10_000_000 + (clocks['offset'] if service == 'pandaStates' else 0)
    messages.recv_time[service] = (clocks['mono'] - 10_000_000) / 1e9
  assert authority.configuration_allowed()
  assert authority.parked()
  authority.close()
  assert not authority.configuration_allowed()


@pytest.mark.parametrize('vehicle', ['HYUNDAI_IONIQ_6', 'TOYOTA_HIGHLANDER_TSS2'])
def test_all_settings_match_ignition_off_in_forced_offroad(powered, vehicle, request):
  authority, params, messages, _ = powered
  params.put_bool('OpenpilotEnabledToggle', True, block=True)
  params.put_bool('AlphaLongitudinalEnabled', False, block=True)
  params.put('VehicleSelection', json.loads(encode(vehicle)), block=True)
  gateway = SettingsGateway(params, authority)
  request.addfinalizer(gateway.close)

  def rows(page):
    return [(row['label'], row['available'], row['choices'], row['resetAvailable'])
            for row in gateway.page(page, 'session', b'generation')['rows']]

  for page in sorted(PAGES):
    messages.data['pandaStates'][0].ignitionLine = False
    ordinary = rows(page)
    messages.data['pandaStates'][0].ignitionLine = True
    assert rows(page) == ordinary, page
  assert next(row for row in rows('aol') if row[0] == 'Enable Always On Lateral')[1]
  page = gateway.page('slc', 'session', b'generation')
  index = next(i for i, row in enumerate(page['rows']) if row['label'].endswith('offset'))
  intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value=3)
  assert gateway.confirm(intent['intent'], 'session', b'generation')
  assert next(row for row in rows('slc') if row[0] == 'Speed Limit Controller')[1]
