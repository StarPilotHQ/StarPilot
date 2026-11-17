"""Request-driven Linux telemetry; adapted StarPilot producer (see LICENSE)."""

import math
import os
import pwd
import shutil
import threading
import time
from pathlib import Path

from openpilot.common.hardware import HARDWARE
from openpilot.common.hardware.hw import Paths


class SystemMonitor:
  def __init__(self, root=Path('/proc'), *, storage=Path('/data'), recording_storage=None, device_type=None, stat=os.stat, clock=time.monotonic,
               wall_clock=time.time, disk_usage=shutil.disk_usage, page_size=None, messages=None,  # noqa: TID251 - Display capture date; intervals use monotonic.
               boot_clock=lambda: time.clock_gettime(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))):
    self.messages, self.boot_clock = messages, boot_clock
    self.root = Path(root)
    self.storage = Path(storage)
    self.recording_storage = Path(Paths.log_root() if recording_storage is None else recording_storage)
    self.device_type = HARDWARE.get_device_type() if device_type is None else device_type
    self.stat = stat
    self.clock, self.wall_clock, self.disk_usage = clock, wall_clock, disk_usage
    self.page_size = os.sysconf('SC_PAGE_SIZE') if page_size is None else page_size
    self.lock = threading.Lock()
    self.previous = {}
    self.cpu_previous = {}
    self.previous_time = None

  def sample(self):
    with self.lock:
      try:
        return self._sample()
      except (OSError, ValueError, IndexError):
        # A failed sample breaks the comparison interval, including PID identity.
        self.previous, self.cpu_previous, self.previous_time = {}, {}, None
        raise OSError('System telemetry is unavailable') from None

  def _battery_voltage(self):
    if self.messages is None:
      return None
    try:
      sm = self.messages.snapshot()
      if sm is None or not sm.seen['peripheralState'] or not sm.valid['peripheralState']:
        return None
      age = self.boot_clock() - int(sm.logMonoTime['peripheralState']) / 1e9
      received_age = self.clock() - sm.recv_time['peripheralState']
      state = sm['peripheralState']
      if not (0 <= age < 5 and 0 <= received_age < 5) or state.pandaType == 0:
        return None
      volts = state.voltage / 1000
      return round(volts, 2) if math.isfinite(volts) and 0 < volts < 60 else None
    except (OSError, RuntimeError, AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return None

  def _storage_usage(self, path):
    try:
      disk = self.disk_usage(path)
      return {'usedGiB': round(disk.used / 1073741824, 1), 'totalGiB': round(disk.total / 1073741824, 1),
              'freeGiB': round(disk.free / 1073741824, 1)}
    except OSError:
      return {'usedGiB': None, 'totalGiB': None, 'freeGiB': None}

  def _recording_storage_usage(self):
    # Only C3 supports an added recording drive. A directory on userdata is
    # not a second drive, and mount changes must not leave a stale SSD reading.
    if self.device_type != 'tici':
      return None
    try:
      internal = self.stat(self.storage).st_dev
      recording = self.stat(self.recording_storage).st_dev
      if internal == recording:
        return None
      usage = self._storage_usage(self.recording_storage)
      if self.stat(self.storage).st_dev != internal or self.stat(self.recording_storage).st_dev != recording:
        return None
      return usage
    except OSError:
      return None

  def _sample(self):
    now = self.clock()
    comparable = self.previous_time is not None and now > self.previous_time
    cpu_now, cores = {}, []
    overall = capacity = None
    for line in (self.root / 'stat').read_text().splitlines():
      values = line.split()
      if not values or not (values[0] == 'cpu' or values[0].startswith('cpu') and values[0][3:].isdigit()):
        continue
      ticks = [int(value) for value in values[1:9]]
      if len(ticks) < 5 or any(value < 0 for value in ticks):
        raise ValueError('Invalid CPU counters')
      pair = (sum(ticks), ticks[3] + ticks[4])
      key = values[0]
      cpu_now[key] = pair
      old = self.cpu_previous.get(key) if comparable else None
      percent = None
      if old and pair[0] > old[0] and 0 <= pair[1] - old[1] <= pair[0] - old[0]:
        percent = round(100 * (1 - (pair[1] - old[1]) / (pair[0] - old[0])), 1)
        if key == 'cpu':
          capacity = pair[0] - old[0]
      if key == 'cpu':
        overall = percent
      else:
        cores.append({'name': key, 'percent': percent})
    if 'cpu' not in cpu_now:
      raise ValueError('Missing CPU counters')

    rows, process_ticks = [], {}
    for path in sorted(self.root.iterdir()):
      if not path.name.isdigit() or int(path.name) <= 0:
        continue
      try:
        raw = (path / 'stat').read_text()
        end = raw.rindex(')')
        fields = raw[end + 2:].split()
        identity = (int(path.name), int(fields[19]))
        current = int(fields[11]) + int(fields[12])
        if current < 0:
          continue
        args = [part for part in (path / 'cmdline').read_bytes().decode(errors='replace').split('\0') if part]
        name = raw[raw.index('(') + 1:end]
        if args:
          name = args[0]
          if 'python' in Path(name).name and len(args) > 1:
            name = args[2] if args[1] == '-m' and len(args) > 2 else args[1] if not args[1].startswith('-') else Path(name).name
        uid = path.stat().st_uid
        try:
          user = pwd.getpwuid(uid).pw_name
        except KeyError:
          user = str(uid)
        cpu = None
        previous = self.previous.get(identity) if comparable else None
        if capacity and previous is not None and current >= previous:
          cpu = round(min(100, (current - previous) / capacity * 100), 1)
        rows.append({'pid': identity[0], 'name': name.removeprefix('/data/openpilot/'), 'user': user,
                     'kernel': not args, 'state': fields[0], 'cpu': cpu,
                     'memoryMiB': round(max(0, int(fields[21])) * self.page_size / 1048576, 1)})
        process_ticks[identity] = current
      except (OSError, ValueError, IndexError):
        # Processes may disappear between reading stat, cmdline and ownership.
        continue

    memory = {line.split(':')[0]: int(line.split()[1])
              for line in (self.root / 'meminfo').read_text().splitlines() if ':' in line}
    total = memory.get('MemTotal', 0)
    available = memory.get('MemAvailable')
    if total <= 0:
      raise ValueError('Missing total memory')
    # MemFree excludes reclaimable cache and cannot stand in for MemAvailable.
    if available is not None and not 0 <= available <= total:
      available = None
    used = total - available if available is not None else None
    # Keep the internal storage fields for older clients. Recordings can live
    # on a separately mounted C3 SSD; do not add the two filesystem capacities.
    storage = self._storage_usage(self.storage)
    recording_storage = self._recording_storage_usage()
    uptime = float((self.root / 'uptime').read_text().split()[0])
    sampled_at = self.wall_clock()
    if not math.isfinite(uptime) or uptime < 0 or not math.isfinite(now) or not math.isfinite(sampled_at) or sampled_at <= 0:
      raise ValueError('Invalid sample time')
    result = {
      'schemaVersion': 1, 'mode': 'local-runtime', 'source': 'Local Linux system telemetry',
      'deviceType': self.device_type,
      'sampledAt': sampled_at, 'cpuPercent': overall, 'cores': cores,
      'memory': {'totalMiB': round(total / 1024, 1),
                 'usedMiB': round(used / 1024, 1) if used is not None else None,
                 'availableMiB': round(available / 1024, 1) if available is not None else None,
                 'percent': round(100 * used / total, 1) if used is not None else None},
      'batteryVoltage': self._battery_voltage(),
      'storage': storage, 'recordingStorage': recording_storage,
      'uptimeSeconds': uptime, 'processCount': len(rows), 'processes': rows,
    }
    self.previous, self.cpu_previous, self.previous_time = process_ticks, cpu_now, now
    return result
