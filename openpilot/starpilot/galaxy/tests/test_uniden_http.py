"""Real authenticated HTTP with only detector IO and configuration storage replaced."""
import time
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

from openpilot.starpilot.galaxy.tests import test_bluetooth_http as bluetooth_http
from openpilot.starpilot.uniden.service import DetectorService
from openpilot.starpilot.uniden.protocol import DEFAULT_CONFIG, alert_key, normalize_config, parse_alerts

ADDRESS = 'AA:BB:CC:DD:EE:FF'
CONFIG = {**DEFAULT_CONFIG, 'enabled': True, 'address': ADDRESS, 'model': 'R8'}


class UnidenHttpTest(unittest.TestCase):
  def setUp(self):
    self.aa = Mock()
    self.aa_enabled = False
    original = bluetooth_http.make_server
    def server(**kwargs):
      kwargs.update(starport_setup=SimpleNamespace(enabled=lambda: self.aa_enabled), starport_client=self.aa)
      return original(**kwargs)
    with patch.object(bluetooth_http, 'make_server', side_effect=server):
      bluetooth_http.BluetoothHttpTest.setUp(cast(Any, self))

  def stop(self):
    bluetooth_http.BluetoothHttpTest.stop(cast(Any, self))

  def request(self, path, payload=None, cookie=''):
    return bluetooth_http.BluetoothHttpTest.request(cast(Any, self), path, payload, cookie)

  def test_auth_configuration_and_onroad_mute(self):
    payload = {'operation': 'status'}
    assert self.request('/api/bluetooth/uniden', payload)[0] == 401
    _, _, headers = self.request('/api/auth/login', {'password': 'password123'})
    cookie = headers['Set-Cookie'].split(';', 1)[0]
    params = Mock()
    params.get.return_value = CONFIG
    service = DetectorService(params, lambda: False)
    state = {'connected': True, 'address': ADDRESS, 'model': 'R8', 'observed_at': time.monotonic(),
             'alerts': parse_alerts(b'1,00,KA,3,0,33.785,R,1'), 'can_mute': True}
    with patch('openpilot.starpilot.uniden.service.DetectorService', return_value=service), \
         patch('openpilot.starpilot.uniden.transport.exchange', return_value=state) as exchange:
      status, result, _ = self.request('/api/bluetooth/uniden', payload, cookie)
      assert status == 200 and result['config'] == normalize_config(CONFIG)
      assert self.request('/api/bluetooth/uniden', {'operation': 'configure', 'config': CONFIG}, cookie)[0] == 409
      params.put.assert_not_called()
      mute = {'operation': 'mute', 'address': ADDRESS, 'observed_at': state['observed_at'], 'alerts': alert_key(state['alerts'])}
      assert self.request('/api/bluetooth/uniden', mute, cookie)[0] == 200
      assert exchange.call_args.kwargs['mute'] == mute
      assert self.request('/api/bluetooth/uniden', {'operation': 'write', 'command': 'arbitrary'}, cookie)[0] == 400
      assert self.request('/api/bluetooth/uniden', {**mute, 'address': 'another'}, cookie)[0] == 409

  def test_projection_delegates_and_uncertain_mute_is_never_replayed_locally(self):
    self.aa_enabled = True
    _, _, headers = self.request('/api/auth/login', {'password': 'password123'})
    cookie = headers['Set-Cookie'].split(';', 1)[0]
    params = Mock()
    params.get.return_value = CONFIG
    service = DetectorService(params, lambda: True)
    self.aa.call.return_value = {'handled': True, 'detector': {'connected': True, 'alerts': []}}
    with patch('openpilot.starpilot.uniden.service.DetectorService', return_value=service), \
         patch('openpilot.starpilot.uniden.transport.exchange') as local:
      status, result, _ = self.request('/api/bluetooth/uniden', {'operation': 'status'}, cookie)
      assert status == 200 and result['connected']
      assert self.aa.call.call_args.args == ('uniden',)
      assert self.aa.call.call_args.kwargs['address'] == ADDRESS
      assert self.aa.call.call_args.kwargs['auto_mute'] is False
      assert self.aa.call.call_args.kwargs['frequency_rules'] == []
      self.aa.call.side_effect = TimeoutError('Uncertain delivery')
      mute = {'operation': 'mute', 'address': ADDRESS, 'observed_at': time.monotonic(), 'alerts': []}
      assert self.request('/api/bluetooth/uniden', mute, cookie)[0] == 503
      local.assert_not_called()
      assert self.request('/api/bluetooth/uniden', {'operation': 'configure', 'config': {**CONFIG, 'enabled': False}}, cookie)[0] == 200
      params.put.assert_called_once_with('UnidenConfig', normalize_config({**CONFIG, 'enabled': False}))
