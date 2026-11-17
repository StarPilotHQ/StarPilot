"""Supervise the phone bridge without changing StarPilot settings. Its dependencies are vendored; nothing is downloaded."""
import fcntl
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import time

DATA = Path('/data/galaxy-ble')
BRIDGE = Path(__file__).resolve().parent / 'bridge'
UNIT = 'galaxy-ble-fork.service'
LAN_UNIT = 'galaxy-lan-discovery.service'
RADIO_UNIT = 'starpilot-bluetooth-radio.service'
RADIO_WAIT_UNTIL = 45.  # seconds since boot; past this, never hold setup for the radio
STATUS_HEARTBEAT = 30
STATUS_STALE = 90  # Longer than the slowest step interval (60 s) plus the heartbeat.
LOG = logging.getLogger('galaxy.bluetooth.setup')


def write_json(path, value):
  path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
  fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.setup-')
  try:
    with os.fdopen(fd, 'w') as stream:
      json.dump(value, stream)
    os.replace(temporary, path)
  finally:
    if os.path.exists(temporary):
      os.unlink(temporary)


def setup_state(data=DATA):
  """(state, message); the state is 'ready' only while a fresh status file says the bridge is ready."""
  try:
    value = json.loads((data / 'setup-status.json').read_text())
    # Monotonic timestamps: a file left from a previous boot reads as stale.
    if not 0 <= time.monotonic() - value['updated'] <= STATUS_STALE:
      return 'pending', 'Bridge startup pending'
    return str(value.get('state', 'pending')), value['message']
  except (OSError, ValueError, KeyError, TypeError):
    return 'pending', 'Bridge startup pending'


def setup_status(data=DATA):
  return setup_state(data)[1]


def retry_setup(data=DATA, *, restart=True):
  filename = 'retry-setup.json' if restart else 'wake-setup.json'
  write_json(data / filename, {'requested': time.monotonic()})


class Bootstrap:
  def __init__(self, params, data=DATA, bridge=BRIDGE, run=subprocess.run):
    self.params, self.data, self.bridge, self.run = params, data, bridge, run
    self.next_attempt = 0
    self.restart = False
    self.registered = False
    self.discovery_registered = False
    self.ready = False
    self.last_status = None
    self.last_write = 0.

  def status(self, state, message):
    self.ready = state == 'ready'
    now = time.monotonic()
    if (state, message) == self.last_status and now - self.last_write < STATUS_HEARTBEAT:
      return
    write_json(self.data / 'setup-status.json', {'state': state, 'message': message, 'updated': time.monotonic()})
    self.last_status, self.last_write = (state, message), now

  def command(self, args, timeout=20, **kwargs):
    return self.run(args, timeout=timeout, check=True, **kwargs)

  def systemctl(self, *args, **kwargs):
    return self.command(['sudo', '-n', 'systemctl', *args], **kwargs)

  def radio_starting(self):
    """True while the comma Bluetooth radio unit is still starting after boot."""
    if time.monotonic() > RADIO_WAIT_UNTIL:
      return False
    try:
      result = self.run(['systemctl', 'show', RADIO_UNIT, '--property=ActiveState', '--value'],
                        timeout=5, check=False, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
      return False
    return result.stdout.strip() == 'activating'

  def register_units(self):
    """Link the bridge and discovery units with a single systemd reload.

    A reload stalls every other systemd job for 1-3 s at boot, including the Bluetooth radio unit's own
    steps (starting btattach and bluetoothd), so wait for that unit first and reload once for both units.
    A bridge failure raises; a discovery failure only leaves discovery unregistered."""
    if self.registered and self.discovery_registered:
      return
    while self.radio_starting():
      time.sleep(0.5)
    linked = []
    if not self.registered:
      self.systemctl('link', '--runtime', '--force', '--no-reload', str(self.bridge / UNIT), stdout=subprocess.DEVNULL)
      linked.append(UNIT)
    if not self.discovery_registered:
      try:
        self.systemctl('link', '--runtime', '--force', '--no-reload', str(self.bridge / LAN_UNIT), stdout=subprocess.DEVNULL)
        linked.append(LAN_UNIT)
      except (OSError, subprocess.SubprocessError):
        pass
    if not linked:
      return
    try:
      self.systemctl('daemon-reload', stdout=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
      if UNIT in linked:
        raise
      return
    self.registered = self.registered or UNIT in linked
    self.discovery_registered = self.discovery_registered or LAN_UNIT in linked

  def step(self):
    wake = self.data / 'wake-setup.json'
    if wake.exists():
      wake.unlink(missing_ok=True)
      self.next_attempt = 0
    requested = self.data / 'retry-setup.json'
    if requested.exists():
      requested.unlink(missing_ok=True)
      self.next_attempt = 0
      self.registered = False
      self.restart = True
    if time.monotonic() < self.next_attempt:
      self.status('error', 'Phone bridge setup failed. Tap Retry to try again.')
      return
    try:
      # The bridge runs before any phone is paired; an empty phone list accepts no one.
      from openpilot.starpilot.galaxy_bluetooth.bridge.pairing import ensure_store
      ensure_store(self.data / 'pairing.json')
      self.register_units()
      # Discovery remains available when Bluetooth is off or the BLE service retries.
      # An optional discovery failure must not prevent Bluetooth startup.
      if self.discovery_registered:
        try:
          self.systemctl('start', LAN_UNIT, stdout=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
          pass
      from openpilot.starpilot.bluetooth.radio_preference import RadioPreference
      if not RadioPreference().enabled():
        self.run(['sudo', '-n', 'systemctl', 'stop', UNIT], timeout=20, check=False,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.status('waiting', 'Turn Bluetooth on in comma Settings to pair your phone')
        return
      # A newly paired phone needs no restart: the bridge reloads the phone list itself.
      if self.restart:
        LOG.info('Restarting phone bridge: retry requested')
        self.systemctl('restart', UNIT, stdout=subprocess.DEVNULL)
        self.restart = False
      started = self.run(['sudo', '-n', 'systemctl', 'start', UNIT], timeout=20, check=False,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
      if started.returncode != 0:
        self.status('waiting', 'Waiting for the comma Bluetooth service')
        return
      try:
        pid = json.loads((self.data / 'bridge-ready.json').read_text())['pid']
        result = self.systemctl('show', UNIT, '--property=MainPID', '--value', capture_output=True, text=True)
        running = pid == int(result.stdout.strip()) and pid > 0
      except (OSError, ValueError, KeyError, TypeError):
        running = False
      self.status('ready' if running else 'waiting', 'Ready to pair' if running else 'Waiting for Bluetooth. Enable Bluetooth on comma.')
    except (OSError, subprocess.SubprocessError, ValueError):
      LOG.exception('Phone bridge setup failed')
      self.next_attempt = time.monotonic() + 60
      self.status('error', 'Phone bridge setup failed. Tap Retry to try again.')


def main():
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
  from openpilot.common.params import Params
  DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
  os.chmod(DATA, 0o700)
  with (DATA / 'bootstrap.lock').open('w') as lock:
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
      return
    worker = Bootstrap(Params())
    while True:
      worker.step()
      # Once the bridge is up, systemd keeps it running; check back slowly unless the user acts.
      for _ in range(60 if worker.ready else 10):
        time.sleep(1)
        if any((DATA / name).exists() for name in ('retry-setup.json', 'wake-setup.json')):
          break


if __name__ == '__main__':
  main()
