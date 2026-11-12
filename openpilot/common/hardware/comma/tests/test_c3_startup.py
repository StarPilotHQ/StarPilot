import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "hardware.py"


@pytest.mark.parametrize("device,usb", [("tici", True), ("tizi", False), ("mici", False)])
def test_internal_panda_transport_irq_affinity(device, usb):
  tree = ast.parse(SOURCE.read_text())
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "HardwareComma")
  method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "initialize_hardware")
  start = next(i for i, node in enumerate(method.body) if isinstance(node, ast.Expr) and
               ast.unparse(node.value) == "affine_irq(3, 'spi_geni')")
  affinity = Mock()
  code = compile(ast.fix_missing_locations(ast.Module(body=method.body[start:start + 2], type_ignores=[])), str(SOURCE), "exec")
  exec(code, {"self": SimpleNamespace(get_device_type=lambda: device), "affine_irq": affinity})
  expected = [(3, "spi_geni")]
  if usb:
    expected += [(3, "xhci-hcd:usb3"), (3, "xhci-hcd:usb1")]
  assert [call.args for call in affinity.call_args_list] == expected


@pytest.mark.parametrize('model,right_volume,eq_enabled', [('tici', 0x1c, 1), ('tizi', 0x17, 0)])
def test_amplifier_profile_registers_and_shutdown_order(model, right_volume, eq_enabled):
  from collections import namedtuple

  path = SOURCE.with_name('amplifier.py')
  tree = ast.parse(path.read_text())
  selected: list[ast.stmt] = [node for node in tree.body if isinstance(node, (ast.Assign, ast.FunctionDef, ast.ClassDef))]
  namespace: dict = {'namedtuple': namedtuple}
  exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(path), 'exec'), namespace)
  amp = namespace['Amplifier'].__new__(namespace['Amplifier'])
  amp.set_configs = Mock(return_value=True)
  assert amp.initialize_configuration(model)
  configs = amp.set_configs.call_args.args[0]
  assert configs[0].register == configs[-1].register == 0x51
  assert configs[0].value == 0 and configs[-1].value == 1
  assert next(config.value for config in configs if config.register == 0x3e) == right_volume
  eq = [config.value for config in configs if config.register == 0x49 and config.offset == 1]
  assert eq[-1] == eq_enabled
  assert sum(config.register >= 0x84 for config in configs) == (50 if model == 'tici' else 0)


def test_hardware_startup_and_wake_pass_actual_amplifier_model():
  tree = ast.parse(SOURCE.read_text())
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'HardwareComma')
  for name in ('initialize_hardware', 'set_power_save'):
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call) and
             ast.unparse(node.func) == 'self.amplifier.initialize_configuration']
    assert len(calls) == 1
    assert ast.unparse(calls[0].args[0]) == 'self.get_device_type()'


@pytest.mark.parametrize('model', ['tici', 'tizi'])
@pytest.mark.parametrize('path_name', ['initialize_hardware', 'set_power_save'])
def test_startup_and_wake_write_model_registers(model, path_name):
  from collections import namedtuple

  registers = dict.fromkeys(range(256), 0xa5)
  writes = []

  class Bus:
    def __init__(self, bus):
      assert bus == 0

    def __enter__(self):
      return self

    def __exit__(self, *args):
      return False

    def read_byte_data(self, address, register, force):
      assert address == 0x10 and force
      return registers[register]

    def write_byte_data(self, address, register, value, force):
      assert address == 0x10 and force
      registers[register] = value
      writes.append((register, value))

  amp_path = SOURCE.with_name('amplifier.py')
  tree = ast.parse(amp_path.read_text())
  selected: list[ast.stmt] = [node for node in tree.body if isinstance(node, (ast.Assign, ast.FunctionDef, ast.ClassDef))]
  namespace: dict = {'namedtuple': namedtuple, 'SMBus': Bus}
  exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(amp_path), 'exec'), namespace)
  amp = namespace['Amplifier']()
  tree = ast.parse(SOURCE.read_text())
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'HardwareComma')
  method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == path_name)
  code = compile(ast.fix_missing_locations(ast.Module(body=[method.body[0]], type_ignores=[])), str(SOURCE), 'exec')
  exec(code, {'self': SimpleNamespace(amplifier=amp, get_device_type=lambda: model), 'powersave_enabled': False})
  expected_registers = dict.fromkeys(range(256), 0xa5)
  configs = [amp._get_shutdown_config(True), *namespace['BASE_CONFIG'], *namespace['CONFIGS'][model], amp._get_shutdown_config(False)]
  if path_name == 'set_power_save':
    configs.insert(0, amp._get_shutdown_config(False))
  expected = []
  for config in configs:
    value = (expected_registers[config.register] & ~config.mask) | ((config.value << config.offset) & config.mask)
    expected_registers[config.register] = value
    expected.append((config.register, value))
  assert writes == expected
  assert registers == expected_registers
  assert registers[0x3e] & 0x1f == (0x1c if model == 'tici' else 0x17)
  assert registers[0x49] & 2 == (2 if model == 'tici' else 0)
  assert len([register for register, _ in writes if register >= 0x84]) == (50 if model == 'tici' else 0)


@pytest.mark.parametrize('device,reset_hold,recover_hold', [('tici', 1, 0.5), ('tizi', 0.01, 0.01), ('mici', 0.01, 0.01)])
def test_internal_panda_reset_and_recovery_timing(device, reset_hold, recover_hold):
  tree = ast.parse(SOURCE.read_text())
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'HardwareComma')
  methods: list[ast.stmt] = [node for node in cls.body if isinstance(node, ast.FunctionDef) and
             node.name in {'reset_internal_panda', 'recover_internal_panda'}]
  events = Mock()
  namespace: dict = {'gpio_init': Mock(), 'gpio_set': events.gpio, 'time': SimpleNamespace(sleep=events.sleep),
               'GPIO': SimpleNamespace(STM_RST_N='reset', STM_BOOT0='boot0')}
  exec(compile(ast.Module(body=methods, type_ignores=[]), str(SOURCE), 'exec'), namespace)
  hardware = SimpleNamespace(get_device_type=lambda: device)
  namespace['reset_internal_panda'](hardware)
  assert events.mock_calls == [call.gpio('reset', True), call.gpio('boot0', False), call.sleep(reset_hold), call.gpio('reset', False)]
  events.reset_mock()
  namespace['recover_internal_panda'](hardware)
  assert events.mock_calls == [call.gpio('reset', True), call.gpio('boot0', True), call.sleep(recover_hold),
                               call.gpio('reset', False), call.sleep(recover_hold), call.gpio('boot0', False)]


@pytest.mark.parametrize('device', ['tici', 'tizi', 'mici'])
@pytest.mark.parametrize('failure', [None, 'missing', 'usb', 'protocol', 'multiple', 'dfu', 'wait', 'initial_wait', 'heartbeat'])
def test_pandad_enumeration_and_recovery(device, failure):
  pandad_path = SOURCE.parents[3] / 'selfdrive/pandad/pandad.py'
  tree = ast.parse(pandad_path.read_text())
  functions: list[ast.stmt] = [node for node in tree.body if isinstance(node, ast.FunctionDef) and
                              node.name == 'main']
  events = Mock()
  first_discovery = {'missing': [], 'usb': OSError(), 'multiple': ['serial', 'serial'], 'wait': []}.get(failure, ['serial'])
  initial_ready = failure not in ('wait', 'initial_wait')
  initial_discovery = ['serial'] if failure == 'heartbeat' else []
  events.list.side_effect = [*([initial_discovery] if initial_ready else []), first_discovery,
                             *([] if failure == 'multiple' else [['serial']]), KeyboardInterrupt()]
  events.health.return_value = {'heartbeat_lost': True}
  panda = MagicMock(list=events.list)
  panda.return_value.__enter__.return_value = SimpleNamespace(health=events.health, is_internal=lambda: True,
    get_type=lambda: b'\x06', get_usb_serial=lambda: 'serial')
  events.dfu_list.return_value = ['dfu'] if failure == 'dfu' else []
  events.ready.return_value = failure != 'wait'
  if failure == 'initial_wait':
    events.ready.side_effect = [False, True]
  dfu = Mock(return_value=SimpleNamespace(recover=events.dfu_recover), list=events.dfu_list)
  flash = Mock(side_effect=[RuntimeError(), None] if failure == 'protocol' else None)
  process = Mock()
  process.wait.side_effect = KeyboardInterrupt()
  launch = Mock(return_value=process)
  namespace: dict = {'Panda': panda, 'PandaDFU': dfu,
               'HARDWARE': SimpleNamespace(get_device_type=lambda: device, reset_internal_panda=events.reset,
                                           recover_internal_panda=events.recover),
               'time': SimpleNamespace(sleep=events.sleep), 'cloudlog': Mock(), 'signal': Mock(),
               'usb1': SimpleNamespace(USBErrorNoDevice=OSError, USBErrorPipe=BrokenPipeError), 'PandaProtocolMismatch': RuntimeError,
               'Params': Mock(return_value=SimpleNamespace(put_bool=events.put_bool)),
               'flash_panda': flash, 'subprocess': SimpleNamespace(Popen=launch), 'BASEDIR': 'base',
               'wait_for_internal_panda': events.ready, 'startup_choice': Mock(return_value='choice'),
               'firmware_environment': Mock(return_value={'verified': True}),
               'os': SimpleNamespace(environ={}, path=SimpleNamespace(join=lambda *parts: '/'.join(parts)))}
  exec(compile(ast.Module(body=functions, type_ignores=[]), str(pandad_path), 'exec'), namespace)
  with pytest.raises(KeyboardInterrupt):
    namespace['main']()

  wait = [call.ready()]
  expected = [call.ready(timeout=0)]
  if initial_ready:
    expected += [call.list()]  # Initial health inspection.
    if failure == 'heartbeat':
      expected += [call.health(), call.put_bool('PandaHeartbeatLost', True, block=True)]
  for reset in [call.reset()] if failure in (None, 'dfu', 'initial_wait', 'heartbeat') else [call.reset(), call.recover()]:
    expected += [reset, *wait, call.dfu_list()]
    if failure == 'dfu':
      expected += [call.dfu_recover(), call.sleep(1), *wait]
    expected += [call.list()]
  assert events.mock_calls == expected
  if failure == 'dfu':
    dfu.assert_called_once_with('dfu')
  if failure == 'multiple':
    flash.assert_not_called()
    launch.assert_not_called()
  else:
    assert flash.call_args_list == [call('serial', 'choice')] * (2 if failure == 'protocol' else 1)
    launch.assert_called_once_with(['./pandad', 'serial'], cwd='base/openpilot/selfdrive/pandad', env={'verified': True})


@pytest.mark.parametrize('device', ['tici', 'tizi', 'mici'])
@pytest.mark.parametrize('ids', [(0xbbaa, 0xddee), (0xbbaa, 0xddcc), (0x3801, 0xddee), (0x0483, 0xdf11)])
def test_internal_panda_ready_without_udev(tmp_path, device, ids):
  tree = ast.parse((SOURCE.parents[3] / 'selfdrive/pandad/pandad.py').read_text())
  method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'wait_for_internal_panda')
  usb = tmp_path / 'usb'
  usb.mkdir()
  for name, value in {'idVendor': f'{ids[0]:04x}', 'idProduct': f'{ids[1]:04x}', 'busnum': '1', 'devnum': '7'}.items():
    (usb / name).write_text(value)
  access = Mock(return_value=True)
  clock = Mock(monotonic=Mock(return_value=0), sleep=Mock())
  namespace = {'HARDWARE': SimpleNamespace(get_device_type=lambda: device), 'Path': lambda _: usb,
               'Panda': SimpleNamespace(USB_VIDS=(0xbbaa, 0x3801), USB_PIDS=(0xddee, 0xddcc)),
               'os': SimpleNamespace(access=access, R_OK=4, W_OK=2), 'time': clock,
               'subprocess': SimpleNamespace(run=Mock(side_effect=FileNotFoundError('udevadm')))}
  exec(compile(ast.Module(body=[method], type_ignores=[]), 'pandad.py', 'exec'), namespace)
  assert namespace['wait_for_internal_panda'](timeout=0)
  namespace['subprocess'].run.assert_not_called()
  if device == 'tici':
    access.assert_called_once_with('/dev/bus/usb/001/007', 6)
  else:
    access.assert_not_called()
  clock.sleep.assert_not_called()


@pytest.mark.parametrize('failure', ['absent', 'wrong_vendor', 'wrong_product', 'malformed', 'no_access', 'zero_device'])
def test_internal_panda_readiness_refuses_unready_device(tmp_path, failure):
  tree = ast.parse((SOURCE.parents[3] / 'selfdrive/pandad/pandad.py').read_text())
  method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'wait_for_internal_panda')
  usb = tmp_path / 'usb'
  usb.mkdir()
  values = {'idVendor': 'bbaa', 'idProduct': 'ddee', 'busnum': '1', 'devnum': '7'}
  if failure == 'wrong_vendor':
    values['idVendor'] = '1234'
  elif failure == 'wrong_product':
    values['idProduct'] = '1234'
  elif failure == 'malformed':
    values['devnum'] = 'broken'
  elif failure == 'zero_device':
    values['devnum'] = '0'
  if failure != 'absent':
    for name, value in values.items():
      (usb / name).write_text(value)
  clock = Mock(monotonic=Mock(side_effect=[0, 0, 0.01, 0.02]), sleep=Mock())
  namespace = {'HARDWARE': SimpleNamespace(get_device_type=lambda: 'tici'), 'Path': lambda _: usb,
               'Panda': SimpleNamespace(USB_VIDS=(0xbbaa, 0x3801), USB_PIDS=(0xddee, 0xddcc)),
               'os': SimpleNamespace(access=Mock(return_value=failure != 'no_access'), R_OK=4, W_OK=2), 'time': clock,
               'subprocess': SimpleNamespace(run=Mock(side_effect=FileNotFoundError('udevadm')))}
  exec(compile(ast.Module(body=[method], type_ignores=[]), 'pandad.py', 'exec'), namespace)
  assert not namespace['wait_for_internal_panda'](timeout=0.02)
  assert clock.sleep.call_args_list == [call(0.01), call(0.01)]


def test_internal_panda_readiness_waits_for_read_write_permission(tmp_path):
  tree = ast.parse((SOURCE.parents[3] / 'selfdrive/pandad/pandad.py').read_text())
  method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'wait_for_internal_panda')
  usb = tmp_path / 'usb'
  usb.mkdir()
  for name, value in {'idVendor': 'bbaa', 'idProduct': 'ddee', 'busnum': '1', 'devnum': '7'}.items():
    (usb / name).write_text(value)
  clock = Mock(monotonic=Mock(return_value=0), sleep=Mock())
  access = Mock(side_effect=[False, True])
  namespace = {'HARDWARE': SimpleNamespace(get_device_type=lambda: 'tici'), 'Path': lambda _: usb,
               'Panda': SimpleNamespace(USB_VIDS=(0xbbaa, 0x3801), USB_PIDS=(0xddee, 0xddcc)),
               'os': SimpleNamespace(access=access, R_OK=4, W_OK=2), 'time': clock}
  exec(compile(ast.Module(body=[method], type_ignores=[]), 'pandad.py', 'exec'), namespace)
  assert namespace['wait_for_internal_panda']()
  assert access.call_count == 2
  clock.sleep.assert_called_once_with(0.01)
