import signal
import subprocess
import sys

from opendbc.safety.tests.libsafety import libsafety_py


def assert_native_callback_finishes(test, model, param, address, bus, data, *, receive=True):
  try:
    mutant = libsafety_py.libsafety.mutation_get_active_mutant()
  except AttributeError:
    mutant = None
  script = """import sys
from opendbc.safety.tests.libsafety import libsafety_py
libsafety_py.load(sys.argv[1])
safety = libsafety_py.libsafety
if sys.argv[2] != 'None':
  safety.mutation_set_active_mutant(int(sys.argv[2]))
packet = libsafety_py.make_CANPacket(int(sys.argv[5]), int(sys.argv[6]), bytes.fromhex(sys.argv[7]))
print('NATIVE_SETUP_BEGIN', flush=True)
status = safety.set_safety_hooks(int(sys.argv[3]), int(sys.argv[4]))
print('NATIVE_SETUP_RETURN', flush=True)
if status != 0:
  print('NATIVE_SETUP_ASSERTION', flush=True)
  raise SystemExit(1)
safety.init_tests()
print('NATIVE_CALLBACK_BEGIN', flush=True)
receive = sys.argv[8] == 'True'
accepted = safety.safety_rx_hook(packet) if receive else safety.safety_tx_hook(packet)
print('NATIVE_CALLBACK_RETURN', flush=True)
raise SystemExit(0 if bool(accepted) == receive else 1)
"""
  try:
    result = subprocess.run([sys.executable, '-c', script, libsafety_py.libpath, str(mutant), str(model),
                             str(param), str(address), str(bus), bytes(data).hex(), str(receive)],
                            capture_output=True, text=True, timeout=10)
  except subprocess.TimeoutExpired as exc:
    output = exc.stdout or b''
    if isinstance(output, bytes):
      output = output.decode(errors='replace')
    setup_entered = 'NATIVE_SETUP_BEGIN' in output and 'NATIVE_SETUP_RETURN' not in output
    callback_entered = 'NATIVE_CALLBACK_BEGIN' in output and 'NATIVE_CALLBACK_RETURN' not in output
    if not (setup_entered or callback_entered):
      raise RuntimeError('Native probe timed out outside the checked native calls') from exc
    test.fail('Native initialization or valid CAN callback did not finish within ten seconds')
  setup_entered = 'NATIVE_SETUP_BEGIN' in result.stdout and 'NATIVE_SETUP_RETURN' not in result.stdout
  callback_entered = 'NATIVE_CALLBACK_BEGIN' in result.stdout and 'NATIVE_CALLBACK_RETURN' not in result.stdout
  if result.returncode == -signal.SIGSEGV and (setup_entered or callback_entered):
    test.fail('Native initialization or valid CAN callback faulted: ' + result.stderr)
  asserted = 'NATIVE_SETUP_ASSERTION' in result.stdout or 'NATIVE_CALLBACK_RETURN' in result.stdout
  if result.returncode not in (0, 1) or not asserted:
    raise RuntimeError(f'Native probe infrastructure failure ({result.returncode}): {result.stdout}{result.stderr}')
  test.assertEqual(result.returncode, 0, result.stdout + result.stderr)
