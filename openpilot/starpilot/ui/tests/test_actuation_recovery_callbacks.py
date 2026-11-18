"""Execute production UI callbacks without GPU/native drawing dependencies."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from openpilot.starpilot.ui.actuation_recovery import acknowledge, KEY

ROOT = Path(__file__).resolve().parents[3]


def methods(path, name, selected, env):
  tree = ast.parse((ROOT / path).read_text())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
  for node in cls.body:
    if isinstance(node, ast.FunctionDef) and node.name in selected:
      exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), env)
  return env


def setup():
  params = Mock()
  params.get.return_value = {'extra': 'longitudinal'}
  params.get_bool.return_value = True
  physical = Mock()
  physical.status.return_value = {'allowed': True}
  physical.effective.return_value = False
  return params, physical


def test_mici_alert_wires_explicit_slide_and_rechecks_at_confirmation():
  params, physical = setup()
  env = {'KEY': KEY, 'acknowledge': acknowledge, 'tr': lambda s: s, 'gui_app': Mock(),
             'BigConfirmationDialog': Mock(), 'BigDialog': Mock()}
  methods('selfdrive/ui/mici/layouts/offroad_alerts.py', 'MiciOffroadAlerts', ['_acknowledge_actuation'], env)
  obj = NS(params=params, _actuation_physical=physical)
  env['_acknowledge_actuation'](obj)
  params.remove.assert_not_called()
  confirmed = env['BigConfirmationDialog'].call_args.args[2]
  physical.status.return_value = {'allowed': False}
  confirmed()
  params.remove.assert_not_called()
  physical.status.return_value = {'allowed': True}
  confirmed()
  params.remove.assert_called_once_with(KEY)


def test_mici_build_registers_only_actuation_and_update_callbacks():
  env = {'KEY': KEY, 'AlertData': lambda **k: NS(**k), 'AlertItem': Mock(side_effect=lambda _: Mock()),
             'OFFROAD_ALERTS': {KEY: {'severity': 1}, 'Other': {'severity': 0}}, 'HARDWARE': Mock()}
  methods('selfdrive/ui/mici/layouts/offroad_alerts.py', 'MiciOffroadAlerts', ['_build_alerts'], env)
  obj = NS(alert_items=[], _scroller=Mock(), _acknowledge_actuation=Mock())
  env['_build_alerts'](obj)
  obj.alert_items[1].set_click_callback.assert_called_once_with(obj._acknowledge_actuation)
  obj.alert_items[2].set_click_callback.assert_not_called()


def test_large_confirmation_cancel_and_mode_change_do_not_clear():
  params, physical = setup()
  # Execute the actual nested callback extracted from the constructor.
  path = ROOT / 'selfdrive/ui/widgets/offroad_alerts.py'
  tree = ast.parse(path.read_text())
  callback = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'excessive_actuation_callback')
  env = {'self': NS(params=params, _actuation_physical=physical, dismiss_callback=Mock()),
             'KEY': KEY, 'acknowledge': acknowledge, 'DialogResult': NS(CONFIRM=1, CANCEL=0),
             'tr': lambda s: s, 'gui_app': Mock(), 'ConfirmDialog': Mock(), 'alert_dialog': Mock()}
  exec(compile(ast.Module(body=[callback], type_ignores=[]), str(path), 'exec'), env)
  env['excessive_actuation_callback']()
  confirmed = env['ConfirmDialog'].call_args.kwargs['callback']
  confirmed(0)
  params.remove.assert_not_called()
  params.get_bool.return_value = False
  confirmed(1)
  params.remove.assert_not_called()
  params.get_bool.return_value = True
  confirmed(1)
  params.remove.assert_called_once_with(KEY)
