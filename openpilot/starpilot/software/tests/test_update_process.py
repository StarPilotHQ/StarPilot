import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from openpilot.starpilot.software.fast_update import FastUpdateError, run


class TestUpdateProcess(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.directory = Path(self.temporary.name)

  def command(self, source):
    script = self.directory / 'command.py'
    script.write_text(source)
    return [sys.executable, '-u', str(script)]

  def test_output_and_failure(self):
    command = self.command("import sys\nprint('first')\nprint('last', file=sys.stderr)\nsys.exit(7)\n")
    with self.assertRaises(subprocess.CalledProcessError) as error:
      run(command, self.directory)
    self.assertEqual(error.exception.returncode, 7)
    self.assertEqual(error.exception.output, b'first\nlast\n')

  def test_progress_and_success(self):
    command = self.command("print('Receiving objects: 50%\\rReceiving objects: 100%')\nprint('done')\n")
    progress = []
    result = run(command, self.directory, progress.append)
    self.assertEqual(result, 'Receiving objects: 50%\rReceiving objects: 100%\ndone\n')
    self.assertEqual(progress, ['Receiving objects: 50%', 'Receiving objects: 100%'])

  def test_timeout_allows_lock_cleanup(self):
    command = self.command("""import signal
import time
from pathlib import Path

def terminate(*_):
  raise SystemExit(1)

signal.signal(signal.SIGTERM, terminate)
lock = Path('shallow.lock')
lock.touch()
print('locked')
try:
  time.sleep(60)
finally:
  lock.unlink()
""")
    with self.assertRaises(subprocess.TimeoutExpired) as error:
      run(command, self.directory, timeout=0.5)
    self.assertEqual(error.exception.output, b'locked\n')
    self.assertFalse((self.directory / 'shallow.lock').exists())

  def test_progress_burst_is_coalesced_without_losing_output(self):
    command = self.command("for i in range(1000): print(f'Receiving objects: {i}\\r', end='')\n")
    progress = []
    result = run(command, self.directory, progress.append)
    self.assertIn('Receiving objects: 500\r', result)
    self.assertEqual(progress[0], 'Receiving objects: 0')
    self.assertEqual(progress[-1], 'Receiving objects: 999')
    self.assertLess(len(progress), 20)

  def test_phone_uplink_interrupts_implicit_downloads(self):
    command = self.command("from pathlib import Path\nimport time\nPath('started').touch()\ntime.sleep(60)\n")
    with patch('openpilot.starpilot.software.fast_update.phone_uplink_active',
               side_effect=lambda: (self.directory / 'started').exists()):
      with self.assertRaisesRegex(FastUpdateError, 'software download stopped'):
        run(command, self.directory, download=True, timeout=5)

  def test_pending_progress_is_delivered_while_command_is_quiet(self):
    command = self.command("""import time
from pathlib import Path
print('Receiving objects: 1%\\rReceiving objects: 99%', flush=True)
deadline = time.monotonic() + 2
while not Path('progress_received').exists() and time.monotonic() < deadline:
  time.sleep(0.01)
print('received while running' if Path('progress_received').exists() else 'progress stalled')
""")

    def progress(detail):
      if detail == 'Receiving objects: 99%':
        (self.directory / 'progress_received').touch()

    result = run(command, self.directory, progress)
    self.assertIn('received while running', result)

  def test_stubborn_command_is_killed(self):
    command = self.command("""import os
import signal
import time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print(os.getpid())
time.sleep(60)
""")
    started = time.monotonic()
    with self.assertRaises(subprocess.TimeoutExpired) as error:
      run(command, self.directory, timeout=0.5)
    self.assertLess(time.monotonic() - started, 8)
    with self.assertRaises(ProcessLookupError):
      os.kill(int(error.exception.output.strip()), 0)

  def test_exited_parent_with_child_pipe_is_bounded(self):
    command = self.command("""import subprocess
import sys
import time
from pathlib import Path
subprocess.Popen([sys.executable, '-u', '-c', '''import signal
import time
from pathlib import Path
def terminate(*_):
  Path('child_stopped').touch()
  raise SystemExit(0)
signal.signal(signal.SIGTERM, terminate)
Path('child_ready').touch()
time.sleep(60)
'''])
while not Path('child_ready').exists():
  time.sleep(0.01)
print('parent exited')
""")
    started = time.monotonic()
    with self.assertRaises(subprocess.TimeoutExpired):
      run(command, self.directory, timeout=0.5)
    self.assertLess(time.monotonic() - started, 8)
    self.assertTrue((self.directory / 'child_stopped').exists())


if __name__ == '__main__':
  unittest.main()
