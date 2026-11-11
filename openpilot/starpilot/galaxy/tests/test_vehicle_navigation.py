"""Exercise Android Auto's production page projection without loading the UI runtime."""
import ast
import hashlib
from pathlib import Path
import secrets
from types import SimpleNamespace
import threading
import unittest


class TestVehicleNavigation(unittest.TestCase):
  def gateway(self, *, supported=True, parked=True):
    source = ast.parse((Path(__file__).parents[1] / 'settings.py').read_text())
    constants = [node for node in source.body if isinstance(node, ast.Assign) and
                 any(isinstance(target, ast.Name) and target.id in ('PAGES', 'MAX_VIEWS', 'TTL', 'CAR_CONTROL_SECTIONS')
                     for target in node.targets)]
    gateway = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'SettingsGateway')
    page = next(node for node in gateway.body if isinstance(node, ast.FunctionDef) and node.name == 'page')
    namespace = {'hashlib': hashlib, 'secrets': secrets, 'SettingsUnavailable': RuntimeError,
                 '_session_key': lambda token: token.encode(), '_projection': lambda row: {}, '_View': lambda *args: args}
    exec(compile(ast.Module(body=constants + [page], type_ignores=[]), 'settings.py', 'exec'), namespace)
    control = SimpleNamespace(choices=('Off', 'On'), page='')
    states = {name: SimpleNamespace(title=name, subtitle='', parked=parked, rows=()) for name in namespace['PAGES']}
    states['aol'].rows = (control,)
    states['vehicle'].rows = (control,) if supported else ()
    states['hub'].rows = (SimpleNamespace(choices=(), page='aol'), SimpleNamespace(choices=(), page='vehicle'))
    owner = SimpleNamespace(views={}, lock=threading.Lock(), clock=lambda: 100., _clean=lambda: None,
                            _context=lambda page: SimpleNamespace(cp=SimpleNamespace(carFingerprint='TESLA_MODEL_Y'), cp_raw=b'cp'),
                            _state=lambda page, ctx: states[page])
    return lambda: namespace['page'](owner, 'hub', 'session', b'generation')

  def test_vehicle_controls_are_reachable_beside_aol(self):
    for parked in (False, True):
      with self.subTest(parked=parked):
        result = self.gateway(parked=parked)()
        lateral = next(section for section in result['sections'] if section['id'] == 'lateral')
        self.assertEqual([page['id'] for page in lateral['pages']], ['aol', 'vehicle'])

  def test_unsupported_vehicle_controls_remain_hidden(self):
    result = self.gateway(supported=False)()
    self.assertFalse(any(page['id'] == 'vehicle' for section in result['sections'] for page in section['pages']))
