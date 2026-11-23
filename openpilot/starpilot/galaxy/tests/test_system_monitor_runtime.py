"""Exercise the real Linux reader through the real loopback HTTP adapter."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.galaxy.system_monitor import SystemMonitor


class SystemMonitorRuntimeTest(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.root = Path(self.directory.name)
    self.time = 10.0
    self.monitor = SystemMonitor(self.root, device_type='pc', clock=lambda: self.time, wall_clock=lambda: 1_000_000 + self.time,
                                 disk_usage=lambda _: SimpleNamespace(used=2 ** 30, total=4 * 2 ** 30, free=3 * 2 ** 30), page_size=4096)
    self.write_snapshot(100, 100, 10)

  def write_snapshot(self, user, idle, ticks, *, start=1000):
    (self.root / 'stat').write_text(f'cpu {user} 0 0 {idle} 0 0 0 0\ncpu0 {user} 0 0 {idle} 0 0 0 0\n')
    (self.root / 'meminfo').write_text('MemTotal: 4096000 kB\nMemAvailable: 1024000 kB\n')
    (self.root / 'uptime').write_text('300 0\n')
    process = self.root / '42'
    process.mkdir(exist_ok=True)
    fields = ['0'] * 22
    fields[0], fields[11], fields[19], fields[21] = 'S', str(ticks), str(start), '256'
    (process / 'stat').write_text(f'42 (worker (test)) {" ".join(fields)}')
    (process / 'cmdline').write_bytes(b'python\0-m\0openpilot.selfdrive.controls.controlsd\0--private-argument\0')

  def test_cpu_capacity_pid_reuse_and_monotonic_reset(self):
    first = self.monitor.sample()
    self.assertIsNone(first['cpuPercent'])
    self.assertIsNone(first['processes'][0]['cpu'])
    self.assertEqual(first['processes'][0]['name'], 'openpilot.selfdrive.controls.controlsd')
    self.assertNotIn('private-argument', json.dumps(first))
    self.time += 2
    self.write_snapshot(160, 140, 30)
    second = self.monitor.sample()
    self.assertEqual(second['cpuPercent'], 60)
    self.assertEqual(second['processes'][0]['cpu'], 20)
    self.assertEqual(second['processes'][0]['memoryMiB'], 1)
    self.time += 2
    self.write_snapshot(220, 180, 40, start=2000)
    self.assertIsNone(self.monitor.sample()['processes'][0]['cpu'])
    self.time = 1
    self.write_snapshot(280, 220, 60, start=2000)
    self.assertIsNone(self.monitor.sample()['cpuPercent'])

  def test_unavailable_fields_and_failed_interval_are_not_zero_or_stale(self):
    self.monitor.sample()
    (self.root / 'meminfo').write_text('MemTotal: 4096000 kB\nMemFree: 1 kB\n')
    (self.root / '53').mkdir()  # Process disappears during enumeration.
    self.time += 2
    sample = self.monitor.sample()
    self.assertIsNone(sample['memory']['usedMiB'])
    self.assertIsNone(sample['memory']['percent'])
    self.assertEqual(sample['processCount'], 1)
    self.assertNotIn('vitals', sample)
    (self.root / 'stat').unlink()
    with self.assertRaises(OSError):
      self.monitor.sample()
    self.time += 2
    self.write_snapshot(300, 200, 50)
    self.assertIsNone(self.monitor.sample()['cpuPercent'])

  def test_tici_recording_storage_falls_back_to_media_mount(self):
    internal = Path('/data')
    media = internal / 'media'
    routes = media / '0' / 'realdata'
    devices = {internal: 1, media: 2}
    capacities = {internal: 30, media: 256, routes: 128}
    reads = []

    def stat(path):
      if path not in devices:
        raise FileNotFoundError(path)
      return SimpleNamespace(st_dev=devices[path])

    def disk_usage(path):
      reads.append(path)
      return SimpleNamespace(used=2 ** 30, total=capacities[path] * 2 ** 30, free=(capacities[path] - 1) * 2 ** 30)

    monitor = SystemMonitor(self.root, storage=internal, recording_storage=routes, device_type='tici', stat=stat, disk_usage=disk_usage)
    sample = monitor.sample()
    self.assertEqual(sample['storage']['totalGiB'], 30)
    self.assertEqual(sample['recordingStorage']['totalGiB'], 256)
    self.assertEqual(reads, [internal, media])
    devices[routes] = 3
    self.assertEqual(monitor.sample()['recordingStorage']['totalGiB'], 128)
    devices[media] = devices[routes] = devices[internal]
    self.assertIsNone(monitor.sample()['recordingStorage'])
    devices[media] = 2
    monitor.device_type = 'mici'
    self.assertIsNone(monitor.sample()['recordingStorage'])

  def test_real_http_source_origin_static_paths_and_read_only_boundary(self):
    owner = GalaxyAccessOwner(self.root / 'access')
    self.assertTrue(owner.configure('test-password', lambda: True))
    server = make_server(port=0, monitor=self.monitor, owner=owner)
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    worker.start()
    try:
      def request(path, *, method='GET', headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        try:
          connection.request(method, path, body=body, headers=headers or {})
          response = connection.getresponse()
          return response.status, response.read(), dict(response.getheaders())
        finally:
          connection.close()

      self.assertEqual(request('/api/system/monitor')[0], 401)
      origin = f'http://127.0.0.1:{server.server_port}'
      status, body, login_headers = request('/api/auth/login', method='POST', headers={
        'Content-Type': 'application/json', 'Origin': origin}, body='{"password":"test-password"}')
      self.assertEqual(status, 200)
      cookie = login_headers['Set-Cookie'].split(';', 1)[0]
      auth = {'Cookie': cookie}
      status, body, headers = request('/api/system/monitor', headers=auth)
      self.assertEqual(status, 200)
      self.assertEqual(json.loads(body)['mode'], 'local-runtime')
      self.assertEqual(json.loads(body)['storage'], {'usedGiB': 1, 'totalGiB': 4, 'freeGiB': 3})
      self.assertIsNone(json.loads(body)['recordingStorage'])
      self.assertEqual(headers['Cache-Control'], 'no-store')
      self.assertNotIn('Access-Control-Allow-Origin', headers)
      self.assertEqual(json.loads(request('/data/runtime.json')[1])['monitor'], 'local')
      self.assertIn(b'galaxy-app', request('/')[1])
      for path in ('/../server.py', '/%2e%2e/server.py'):
        self.assertEqual(request(path)[0], 404)
      for path in ('/api/params', '/api/vehicle'):
        self.assertEqual(request(path, headers=auth)[0], 404)
      self.assertEqual(request('/api/system/monitor', headers={'Host': 'other.example'})[0], 403)
      self.assertEqual(request('/api/system/monitor', headers={'Origin': 'https://other.example'})[0], 403)
      self.assertEqual(request('/api/system/monitor', method='POST', headers={'Origin': origin})[0], 405)
      (self.root / 'stat').unlink()
      status, body, _ = request('/api/system/monitor', headers=auth)
      self.assertEqual(status, 503)
      self.assertNotIn('sampledAt', json.loads(body))
    finally:
      server.shutdown()
      worker.join(timeout=2)
      server.server_close()


class BatteryVoltageTest(unittest.TestCase):
  def test_peripheral_millivolts_freshness_and_unavailable(self):
    state = SimpleNamespace(pandaType=1, voltage=12640)
    class Snapshot(SimpleNamespace):
      def __getitem__(self, key):
        return state
    sm = Snapshot(seen={'peripheralState':True}, valid={'peripheralState':True},
                  logMonoTime={'peripheralState':9_000_000_000}, recv_time={'peripheralState':9.})
    source = SimpleNamespace(snapshot=lambda:sm)
    monitor = SystemMonitor(messages=source, clock=lambda:10., boot_clock=lambda:10.)
    self.assertEqual(monitor._battery_voltage(),12.64)
    state.voltage=14420
    self.assertEqual(monitor._battery_voltage(),14.42)
    for voltage in (0, -1, 1000000):
      state.voltage=voltage
      self.assertIsNone(monitor._battery_voltage())
    state.voltage=12640
    for field,bad,good in (('seen',False,True),('valid',False,True),
                           ('logMonoTime',5_000_000_000,9_000_000_000),
                           ('logMonoTime',11_000_000_000,9_000_000_000),
                           ('recv_time',5.,9.),('recv_time',11.,9.)):
      getattr(sm,field)['peripheralState']=bad
      self.assertIsNone(monitor._battery_voltage())
      getattr(sm,field)['peripheralState']=good
    state.pandaType=0
    self.assertIsNone(monitor._battery_voltage())
    state.pandaType=1
    source.snapshot=lambda:None
    self.assertIsNone(monitor._battery_voltage())
    self.assertIsNone(SystemMonitor()._battery_voltage())


class ThermalVitalsTest(unittest.TestCase):
  def test_hottest_real_sensor_and_only_fresh_attached_gpu(self):
    class Snapshot(SimpleNamespace):
      def __getitem__(self, key):
        return self.data[key]

    device = SimpleNamespace(cpuTempC=[0., 43.5, 42.8, float('nan')], gpuTempC=[41.5, 42.2], chestnutPresent=True)
    names = ('deviceState', 'chestnutGpuState')
    sm = Snapshot(data={'deviceState': device, 'chestnutGpuState': SimpleNamespace(tempC=72)},
                  seen=dict.fromkeys(names, True), alive=dict.fromkeys(names, True), valid=dict.fromkeys(names, True),
                  logMonoTime=dict.fromkeys(names, 9_000_000_000), recv_time=dict.fromkeys(names, 9.),
                  after_mono_ns=8_000_000_000)
    monitor = SystemMonitor(messages=SimpleNamespace(snapshot=lambda: sm), clock=lambda: 10.)
    self.assertEqual(monitor._thermal_vitals(), {'cpuTempC': 43.5, 'gpuTempC': 42.2, 'onboardMaxAgeMs': 4000,
                                                'hotspotTempC': 72, 'maxAgeMs': 4000})
    device.chestnutPresent = False
    self.assertNotIn('hotspotTempC', monitor._thermal_vitals())
    device.chestnutPresent = True
    sm.valid['chestnutGpuState'] = False
    self.assertNotIn('hotspotTempC', monitor._thermal_vitals())
    sm.valid['chestnutGpuState'] = True
    sm.logMonoTime['chestnutGpuState'] = 10_000_000_001
    self.assertNotIn('hotspotTempC', monitor._thermal_vitals())
    sm.recv_time['deviceState'] = 5.
    self.assertEqual(monitor._thermal_vitals(), {})
    sm.recv_time['deviceState'] = 9.
    sm.after_mono_ns = 9_000_000_000
    self.assertEqual(monitor._thermal_vitals(), {})
