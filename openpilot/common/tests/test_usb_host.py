import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.common.hardware import usb


@pytest.mark.parametrize('states,expected', (
  ([], False), (['not attached'], False), (['configured'], True),
  (['suspended'], True), (['not attached', 'addressed'], True),
))
def test_usb_host_detection_uses_actual_controller_state(tmp_path, monkeypatch, states, expected):
  for number, state in enumerate(states):
    controller = tmp_path / str(number)
    controller.mkdir()
    (controller / 'state').write_text(state + '\n')
  monkeypatch.setattr(usb, 'UDC_PATH', tmp_path)
  assert usb.usb_host_connected() is expected


def test_missing_controller_state_is_not_a_host(tmp_path, monkeypatch):
  monkeypatch.setattr(usb, 'UDC_PATH', tmp_path / 'absent')
  assert not usb.usb_host_connected()


@pytest.mark.parametrize('present,cable,link,host,available', (
  (False, False, False, False, False),
  (False, True, False, False, True),
  (False, True, False, True, False),
  (False, True, True, False, False),
  (False, True, True, True, False),
  (True, True, False, True, True),
  (True, False, True, False, True),
))
def test_actual_modeld_startup_rejects_host_cable(monkeypatch, present, cable, link, host, available):
  from openpilot.starpilot.models import manager

  class SelectionReached(Exception):
    pass

  selection = Mock(side_effect=SelectionReached)
  monkeypatch.setattr(manager, 'resolve_runtime', selection)
  monkeypatch.delenv('STARPILOT_MODELD_RECOVERY_SMALL_ONLY', raising=False)
  source = Path(__file__).resolve().parents[2] / 'selfdrive/modeld/modeld.py'
  main = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'main')
  prefix = []
  for node in main.body:
    prefix.append(node)
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'selection' for t in node.targets):
      break
  namespace = {'cloudlog': Mock(), 'ModelReceiptOwner': Mock(), 'chestnut_present': lambda: present,
               'cable_connected': lambda: cable, 'usb_host_connected': lambda: host,
               'jetlink_adapter': SimpleNamespace(link_on=lambda: link), 'os': __import__('os')}
  with pytest.raises(SelectionReached):
    exec(compile(ast.Module(body=prefix, type_ignores=[]), str(source), 'exec'), namespace)
  selection.assert_called_once_with(chestnut_available=available, randomize=True)
