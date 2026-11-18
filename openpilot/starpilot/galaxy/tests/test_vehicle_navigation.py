"""Exercise Android Auto's production page projection without loading the UI runtime."""
import ast
import hashlib
from pathlib import Path
import secrets
from types import SimpleNamespace
from typing import Any
import threading
import unittest


class TestVehicleNavigation(unittest.TestCase):
  def gateway(self, *, supported=True, parked=True, numeric_only=False):
    source = ast.parse((Path(__file__).parents[1] / 'settings.py').read_text())
    constants = [node for node in source.body if isinstance(node, ast.Assign) and
                 any(isinstance(target, ast.Name) and target.id in ('PAGES', 'MAX_VIEWS', 'TTL', 'CAR_CONTROL_SECTIONS')
                     for target in node.targets)]
    gateway = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'SettingsGateway')
    page = next(node for node in gateway.body if isinstance(node, ast.FunctionDef) and node.name == 'page')
    namespace: dict[str, Any] = {'hashlib': hashlib, 'secrets': secrets, 'SettingsUnavailable': RuntimeError,
                 '_session_key': lambda token: token.encode(), '_projection': lambda row: {}, '_View': lambda *args: args}
    exec(compile(ast.Module(body=constants + [page], type_ignores=[]), 'settings.py', 'exec'), namespace)
    control = SimpleNamespace(choices=('Off', 'On'), page='')
    states = {name: SimpleNamespace(title=name, subtitle='', parked=parked, rows=()) for name in namespace['PAGES']}
    pages = ('aol', 'lane_change', 'lane', 'torque', 'torque/developer')
    for name in pages:
      states[name].rows = (control,)
    states['torque/developer'].rows = (SimpleNamespace(choices=(), page=''),) if numeric_only else (control,) if supported else ()
    states['hub'].rows = tuple(SimpleNamespace(choices=(), page=name) for name in pages)
    owner = SimpleNamespace(views={}, lock=threading.Lock(), clock=lambda: 100., _clean=lambda: None,
                            _context=lambda page: SimpleNamespace(cp=SimpleNamespace(carFingerprint='TESLA_MODEL_Y'), cp_raw=b'cp'),
                            _state=lambda page, ctx: states[page])
    return lambda: namespace['page'](owner, 'hub', 'session', b'generation')

  def test_supported_binary_developer_controls_follow_the_normal_lateral_pages(self):
    for parked in (False, True):
      with self.subTest(parked=parked):
        result = self.gateway(parked=parked)()
        lateral = next(section for section in result['sections'] if section['id'] == 'lateral')
        self.assertEqual([page['id'] for page in lateral['pages']], ['aol', 'lane_change', 'lane', 'torque', 'torque/developer'])

  def test_unsupported_developer_controls_remain_hidden(self):
    result = self.gateway(supported=False)()
    lateral = next(section for section in result['sections'] if section['id'] == 'lateral')
    self.assertEqual([page['id'] for page in lateral['pages']], ['aol', 'lane_change', 'lane', 'torque'])

  def test_android_auto_does_not_expose_numeric_only_developer_settings(self):
    result = self.gateway(numeric_only=True)()
    lateral = next(section for section in result['sections'] if section['id'] == 'lateral')
    self.assertEqual([page['id'] for page in lateral['pages']], ['aol', 'lane_change', 'lane', 'torque'])
