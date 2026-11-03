"""Offline, disposable checks for the opt-in Mapd shadow owner."""

import hashlib
import json
from multiprocessing import Process
import os
from pathlib import Path
import struct
import tempfile
import unittest
from typing import cast
from unittest import mock

from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.starpilot.maps.artifact import elf_arm64_static, source_digest
from openpilot.starpilot.maps import shadow_lifecycle as shadow
from openpilot.starpilot.maps import package_shadow
from openpilot.starpilot.speed_limits.runtime_settings import SOURCE_PRIORITY_KEYS


def fake_static_elf() -> bytes:
  header = bytearray(64)
  header[:6] = b'\x7fELF\x02\x01'
  struct.pack_into('<H', header, 18, 183)
  struct.pack_into('<Q', header, 32, 64)
  struct.pack_into('<H', header, 54, 56)
  struct.pack_into('<H', header, 56, 1)
  segment = bytearray(56)
  struct.pack_into('<I', segment, 0, 1)  # PT_LOAD only
  return bytes(header + segment)


class FakeParams(Params):
  def __init__(self, path: Path):
    self.path = path

  def get_param_path(self, key: str = '') -> str:
    assert key in (shadow.OPT_IN_KEY, *SOURCE_PRIORITY_KEYS)
    return str(self.path if key == shadow.OPT_IN_KEY else self.path.parent / key)


class FakeCar:
  notCar = False


class TestShadowLifecycle(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.base = Path(self.temp.name).resolve()
    self.anchor = self.base / 'storage'
    self.root = shadow.map_offline_root(self.anchor)
    self.root.mkdir(parents=True)
    self.receipt = {'version': 1, 'sourceKind': 'local-pbf', 'sourceVerified': False,
                    'tileSchema': 'offline.capnp:0xda3a0d9284ca402f',
                    'attribution': '© OpenStreetMap contributors; https://www.openstreetmap.org/copyright (ODbL)',
                    'inputs': [{'name': 'synthetic.osm.pbf', 'size': 1, 'sha256': 'a' * 64}],
                    'tiles': [{'path': '34/-98/35.000000_-98.000000_35.250000_-97.750000',
                               'size': 1, 'sha256': 'b' * 64, 'empty': False}]}
    self.write_snapshot()
    self.source = self.base / 'source'
    self.source.mkdir()
    (self.source / 'main.go').write_text('pinned fixture')
    self.binary = self.base / 'mapd'
    self.binary.write_bytes(fake_static_elf())
    self.binary.chmod(0o755)
    self.manifest = self.base / 'manifest.json'
    pin = json.loads((Path(BASEDIR) / 'upstream-sync.json').read_text())
    revision = next(item['commit'] for item in pin['dependencies'] if item['path'] == 'mapd_repo')
    self.contents = {'schemaVersion': 1, 'goVersion': 'go1.25.1', 'target': 'linux-arm64-static',
                     'sourceDigest': source_digest(self.source), 'upstreamRevision': revision,
                     'sourceRevision': 'f' * 40}
    self.binary.write_bytes(self.binary.read_bytes() + '\n'.join((self.contents['sourceDigest'], revision, 'f' * 40)).encode())
    self.contents['binarySha256'] = hashlib.sha256(self.binary.read_bytes()).hexdigest()
    self.write_manifest()

  def write_manifest(self):
    self.manifest.write_text(json.dumps(self.contents))

  def write_snapshot(self):
    raw = json.dumps(self.receipt, separators=(',', ':')).encode()
    generation = hashlib.sha256(raw).hexdigest()
    directory = self.root / 'generations' / generation
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'receipt.json').write_bytes(raw)
    (self.root / 'current.json').write_text(json.dumps({'version': 1, 'generation': generation}, separators=(',', ':')))
    return directory

  def preflight(self):
    return shadow.preflight(self.binary, self.manifest, self.root, self.source, self.anchor, self.base, self.base)

  def test_preflight_checks_real_binary_and_owned_root(self):
    self.assertTrue(elf_arm64_static(self.binary))
    self.assertTrue(self.preflight())
    self.contents['binarySha256'] = '0' * 64
    self.write_manifest()
    self.assertFalse(self.preflight())
    self.contents['binarySha256'] = hashlib.sha256(self.binary.read_bytes()).hexdigest()
    self.write_manifest()
    self.binary.chmod(0o644)
    self.assertFalse(self.preflight())

  def test_rejects_manifest_fifo_symlink_oversize_and_dynamic_elf(self):
    self.manifest.unlink()
    os.mkfifo(self.manifest)
    self.assertFalse(self.preflight())  # must not block
    self.manifest.unlink()
    self.manifest.symlink_to(self.binary)
    self.assertFalse(self.preflight())
    self.manifest.unlink()
    self.manifest.write_bytes(b' ' * (shadow.MAX_MANIFEST_BYTES + 1))
    self.assertFalse(self.preflight())
    self.write_manifest()
    data = bytearray(fake_static_elf())
    struct.pack_into('<I', data, 64, 3)  # PT_INTERP
    self.binary.write_bytes(data)
    self.assertFalse(self.preflight())

  def test_malformed_manifest_metadata_never_crashes_preflight(self):
    for key, bad in (('sourceRevision', 7), ('sourceRevision', []), ('sourceDigest', 'bad'),
                     ('binarySha256', None), ('schemaVersion', True), ('target', 'linux-amd64')):
      original = self.contents[key]
      self.contents[key] = bad
      self.write_manifest()
      self.assertFalse(self.preflight(), key)
      self.contents[key] = original
    self.manifest.write_text('[' * 1500 + '0' + ']' * 1500)
    self.assertFalse(self.preflight())
    self.write_manifest()

  def test_rejects_symlinked_owned_parent(self):
    other = self.base / 'elsewhere'
    other.mkdir()
    (other / 'maps/offline').mkdir(parents=True)
    self.root.parent.parent.rename(self.anchor / 'old-starpilot')
    self.root.parent.parent.symlink_to(other, target_is_directory=True)
    self.assertFalse(self.preflight())

  def test_snapshot_selector_and_receipt_fail_closed(self):
    selector = self.root / 'current.json'
    original = selector.read_bytes()
    selector.unlink()
    os.mkfifo(selector)
    self.assertFalse(self.preflight())
    selector.unlink()
    selector.symlink_to(self.manifest)
    self.assertFalse(self.preflight())
    selector.unlink()
    selector.write_bytes(b' ' * (shadow.MAX_SELECTOR_BYTES + 1))
    self.assertFalse(self.preflight())
    selector.write_bytes(original)
    selected = json.loads(original)['generation']
    receipt = self.root / 'generations' / selected / 'receipt.json'
    saved = receipt.read_bytes()
    receipt.unlink()
    os.mkfifo(receipt)
    self.assertFalse(self.preflight())
    receipt.unlink()
    receipt.write_bytes(b' ' * (shadow.MAX_RECEIPT_BYTES + 1))
    self.assertFalse(self.preflight())
    receipt.write_bytes(saved + b' ')
    self.assertFalse(self.preflight())
    receipt.write_bytes(saved)
    self.assertTrue(self.preflight())

  def test_rejects_symlinked_package_parent(self):
    package = self.base / 'package'
    package.symlink_to(self.base, target_is_directory=True)
    self.assertFalse(shadow.preflight(package / 'mapd', package / 'manifest.json', self.root,
                                      self.source, self.anchor, package, self.base))

  def test_strict_raw_opt_in_rejects_special_and_bad_bytes(self):
    path = self.base / 'MapdShadowEnabled'
    params = FakeParams(path)
    cp = FakeCar()
    (self.base / 'SLCPriority2').write_bytes(b'Dashboard')
    self.assertFalse(shadow.enabled(True, params, cp))
    path.write_bytes(b'1')
    self.assertTrue(shadow.enabled(True, params, cp))
    self.assertFalse(shadow.enabled(False, params, cp))
    cp.notCar = True
    self.assertFalse(shadow.enabled(True, params, cp))
    cp.notCar = False
    for raw in (b'0', b'true', b'1\n', b'11', b'\xff'):
      path.write_bytes(raw)
      self.assertFalse(shadow.enabled(True, params, cp))
    path.unlink()
    os.mkfifo(path)
    self.assertFalse(shadow.enabled(True, params, cp))
    path.unlink()
    target = self.base / 'other'
    target.write_bytes(b'1')
    path.symlink_to(target)
    self.assertFalse(shadow.enabled(True, params, cp))

  def test_actual_disposable_params_registry_defaults_off(self):
    with mock.patch.dict(os.environ, {'PARAMS_ROOT': str(self.base / 'params')}):
      params = Params()
      self.assertFalse(params.get_default_value(shadow.OPT_IN_KEY))
      params.put('SLCPriority2', 'Dashboard', block=True)
      self.assertFalse(shadow.enabled(True, params, FakeCar()))
      params.put_bool(shadow.OPT_IN_KEY, True, block=True)
      self.assertTrue(shadow.enabled(True, params, FakeCar()))

  def test_registered_manager_uses_fixed_shadow_child(self):
    from openpilot.system.manager.process_config import managed_processes
    process = managed_processes['mapd_shadow']
    self.assertIsInstance(process, shadow.MapdShadowProcess)
    self.assertEqual(process.cmdline, [str(shadow.PROVIDER_BINARY), '--shadow', '--offline-root', str(shadow.OFFLINE_ROOT)])

  def test_child_alone_runs_below_normal_priority_with_bounded_go_processors(self):
    import sys
    self.assertIs(shadow.MapdShadowProcess().launcher, shadow.shadow_launcher)
    parent_nice = os.nice(0)
    with tempfile.TemporaryDirectory() as directory:
      out = Path(directory) / 'policy'
      env = "{os.environ.get('GOMAXPROCS')} {os.environ.get('GOMEMLIMIT')}"
      script = f'import os; open({str(out)!r}, "w").write(f"{{os.nice(0)}} {env}")'
      child = Process(target=shadow.shadow_launcher, args=([sys.executable, '-c', script], directory, 'mapd_shadow'))
      child.start()
      child.join(10)
      self.assertEqual(child.exitcode, 0)
      nice, procs, limit = out.read_text().split()
    self.assertEqual(int(nice), min(parent_nice + shadow.SHADOW_NICE, 19))
    self.assertEqual(procs, shadow.SHADOW_GOMAXPROCS)
    self.assertEqual(limit, shadow.SHADOW_GOMEMLIMIT)
    self.assertEqual(os.nice(0), parent_nice)
    self.assertNotEqual(os.environ.get('GOMAXPROCS'), shadow.SHADOW_GOMAXPROCS)
    self.assertNotEqual(os.environ.get('GOMEMLIMIT'), shadow.SHADOW_GOMEMLIMIT)

  def test_exited_child_backoff_and_pending_shutdown(self):
    now = [10.0]
    process = shadow.MapdShadowProcess(clock=lambda: now[0])
    spawned = []
    class Child:
      exitcode = None
    def fake_start(_):
      child = Child()
      process.proc = cast(Process, child)
      spawned.append(child)
    def fake_stop(_, **kwargs):
      process.proc = None
      process.shutting_down = False
    with mock.patch.object(shadow, 'preflight', return_value=True), \
         mock.patch.object(shadow.NativeProcess, 'start', fake_start), \
         mock.patch.object(shadow.NativeProcess, 'stop', fake_stop):
      process.start()
      self.assertEqual(len(spawned), 1)
      spawned[-1].exitcode = 1
      process.start()  # reap and schedule 1 second retry
      self.assertIsNone(process.proc)
      process.start()
      self.assertEqual(len(spawned), 1)
      now[0] += 1
      process.start()
      self.assertEqual(len(spawned), 2)
      process.shutting_down = True
      process.start()  # finish ordinary pending stop before any new start
      self.assertFalse(process.shutting_down)
      self.assertEqual(len(spawned), 3)
      process.stop()
      self.assertEqual(process._failures, 0)

  def test_package_inserts_generated_binary_into_explicit_staging_tree(self):
    output = self.base / 'release-stage/openpilot/starpilot/maps/provider'
    command = []
    def fake_output(args, **kwargs):
      if args[1] == 'version':
        return 'go version go1.25.1 linux/arm64\n'
      return 'fixture-revision\n'
    def fake_build(args, **kwargs):
      command.extend(args)
      result = Path(args[args.index('-o') + 1])
      result.write_bytes(fake_static_elf() + b'fixture-revision' + self.contents['upstreamRevision'].encode() + b'fixture-digest')
    with mock.patch.object(package_shadow.shutil, 'which', return_value='/pinned/go'), \
         mock.patch.object(package_shadow.subprocess, 'check_output', side_effect=fake_output), \
         mock.patch.object(package_shadow.subprocess, 'run', side_effect=fake_build), \
         mock.patch.object(package_shadow, 'source_digest', return_value='fixture-digest'):
      manifest = package_shadow.package(go='/pinned/go', output=output)
    self.assertIn('-mod=readonly', command)
    self.assertIn('netgo,osusergo', command)
    self.assertTrue((output / 'mapd').is_file())
    self.assertTrue((output / 'manifest.json').is_file())
    self.assertEqual(manifest, json.loads((output / 'manifest.json').read_text()))


if __name__ == '__main__':
  unittest.main()
