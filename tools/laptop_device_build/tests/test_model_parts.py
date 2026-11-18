import ast
import contextlib
import hashlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('model_parts_owner', SOURCE / 'openpilot/common/file_chunker.py')
parts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parts)


class TestModelParts(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.source = self.root / 'compiled.pkl'
    self.target = self.root / 'package/driving_tinygrad.pkl'
    self.data = bytes(range(256)) * 4 + b'pickle arena tail'
    self.source.write_bytes(self.data)

  def package(self):
    with patch.object(parts, 'CHUNK_SIZE', 256):
      return parts.package_file(self.source, self.target)

  def materialize(self, **kwargs):
    with patch.object(parts, 'CHUNK_SIZE', 256):
      return parts.materialize_file_chunked(self.target, **kwargs)

  def test_normal_file_reads_exact_selected_payload(self):
    sha = hashlib.sha256(self.data).hexdigest()
    self.assertEqual(parts.materialize_file_chunked(self.source, sha), self.source)
    with parts.open_file_chunked(self.source, sha) as f:
      self.assertEqual(f.read(9), self.data[:9])
      f.seek(1017)
      self.assertEqual(f.read(), self.data[1017:])

  def test_release_package_contains_only_cheddar_and_fresh_driver_monitoring(self):
    from tools.laptop_device_build import package_model_chunks

    source, destination = self.root / 'source', self.root / 'release'
    source.mkdir()
    dm = source / 'dmonitoring_model_tinygrad.pkl'
    dm.write_bytes(b'old dm')
    parts.package_file(dm, dm)
    dm.write_bytes(b'new dm')
    rdf = source / 'cheddar_driving_tinygrad.pkl'
    rdf.write_bytes(b'verified cheddar')
    parts.package_file(rdf, rdf)
    rdf.unlink()
    (source / 'driving_tinygrad.pkl').write_bytes(b'obsolete stock')
    with patch.object(sys, 'argv', ['package-models', '--source', str(source), '--destination', str(destination)]):
      package_model_chunks.main()
    self.assertEqual(parts.materialize_file_chunked(destination / dm.name).read_bytes(), b'new dm')
    self.assertEqual(parts.materialize_file_chunked(destination / rdf.name).read_bytes(), b'verified cheddar')
    self.assertEqual({path.name for path in destination.glob('*.chunkmanifest')},
                     {dm.name + '.chunkmanifest', rdf.name + '.chunkmanifest'})

  def test_numbered_parts_reconstruct_exact_bytes_without_removing_compiled_output(self):
    sha = self.package()
    self.assertEqual(Path(f'{self.target}.chunkmanifest').read_text(), '5')
    self.assertTrue(self.target.with_name('driving_tinygrad.pkl.chunk01of05').exists())
    cache = self.materialize(expected_sha256=sha)
    self.assertEqual(cache.read_bytes(), self.data)
    self.assertEqual(self.source.read_bytes(), self.data)
    with cache.open('rb') as f:
      f.seek(251)
      self.assertEqual(f.read(19), self.data[251:270])

  def test_legacy_count_only_manifest_with_pinned_digest(self):
    sha = self.package()
    Path(f'{self.target}.chunksha256').unlink()
    self.assertEqual(self.materialize(expected_sha256=sha).read_bytes(), self.data)

  def test_missing_part_refused_even_with_previous_cache(self):
    self.package()
    self.materialize()
    self.target.with_name('driving_tinygrad.pkl.chunk02of05').unlink()
    with self.assertRaises(FileNotFoundError):
      self.materialize()

  def test_same_size_corrupt_part_refused_and_cache_not_replaced(self):
    self.package()
    cache = self.materialize()
    part = self.target.with_name('driving_tinygrad.pkl.chunk02of05')
    part.write_bytes(b'X' * 256)
    with self.assertRaisesRegex(ValueError, 'part digest'):
      self.materialize()
    self.assertEqual(cache.read_bytes(), self.data)

  def test_invalid_count_or_short_middle_part_refused(self):
    self.package()
    Path(f'{self.target}.chunkmanifest').write_text('0')
    with self.assertRaises(ValueError):
      self.materialize()
    self.package()
    self.target.with_name('driving_tinygrad.pkl.chunk02of05').write_bytes(b'x')
    with self.assertRaisesRegex(ValueError, 'part size'):
      self.materialize()

  def test_corrupt_total_digest_and_cache_recovery(self):
    self.package()
    cache = self.materialize()
    cache.write_bytes(b'bad cache')
    self.assertEqual(self.materialize().read_bytes(), self.data)
    sidecar = Path(f'{self.target}.chunksha256')
    record = json.loads(sidecar.read_text())
    record['sha256'] = '0' * 64
    sidecar.write_text(json.dumps(record))
    with self.assertRaisesRegex(ValueError, 'model digest'):
      self.materialize()

  def test_matching_cache_is_reused_without_another_disk_copy(self):
    self.package()
    cache = self.materialize()
    identity = cache.stat().st_ino
    with patch.object(parts.tempfile, 'mkstemp', side_effect=AssertionError('unexpected copy')):
      self.assertEqual(self.materialize().stat().st_ino, identity)

  def test_real_runtime_path_and_oob_dispatch_read_selected_parts(self):
    self.package()
    helper = SOURCE / 'openpilot/selfdrive/modeld/helpers.py'
    tree = ast.parse(helper.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ('modeld_pkl_path', 'load_oob')]
    namespace = {'MODELS_DIR': self.target.parent, 'Path': Path, 'file_chunked_exists': parts.file_chunked_exists,
                 'materialize_file_chunked': parts.materialize_file_chunked, 'AGNOS': True, 'sys': sys}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(helper), 'exec'), namespace)
    captured = []
    def load_pickle(path, out_of_band):
      self.assertTrue(out_of_band)
      captured.append(Path(path))
      with Path(path).open('rb') as f:
        f.seek(251)
        return f.read(19)
    modules = {'tinygrad': types.SimpleNamespace(Context=lambda **kwargs: contextlib.nullcontext()),
               'tinygrad_repo.examples.openpilot.helpers': types.SimpleNamespace(load_pickle=load_pickle),
               'openpilot.starpilot.models.runtime_bridge.runtime': types.SimpleNamespace(
                 selected_legacy=lambda: True, load_oob=lambda path, chestnut: load_pickle(path, out_of_band=True))}
    with patch.object(parts, 'CHUNK_SIZE', 256), patch.dict(sys.modules, modules):
      resolved = namespace['modeld_pkl_path'](False)
      self.assertTrue(resolved.is_file())  # actual receipt capture happens after this resolver
      self.assertEqual(namespace['load_oob'](self.target), self.data[251:270])
      self.assertEqual(captured, [resolved])
      with patch.object(modules['openpilot.starpilot.models.runtime_bridge.runtime'], 'selected_legacy', return_value=False):
        with self.assertRaisesRegex(RuntimeError, 'Prepare the model runtime'):
          namespace['load_oob'](self.target)
      self.assertEqual(captured, [resolved])
    self.assertFalse((self.target.parent / 'big_driving_tinygrad.pkl').exists())

  def test_tracked_package_takes_priority_over_stale_canonical_output(self):
    self.package()
    self.target.write_bytes(b'stale ignored compiler output')
    self.assertEqual(self.materialize().read_bytes(), self.data)

  def test_invalid_tracked_parts_never_fall_back_to_stale_canonical(self):
    for failure in ('missing', 'corrupt'):
      with self.subTest(failure=failure):
        self.package()
        self.target.write_bytes(b'stale ignored compiler output')
        part = self.target.with_name('driving_tinygrad.pkl.chunk02of05')
        if failure == 'missing':
          part.unlink()
          expected = FileNotFoundError
        else:
          part.write_bytes(b'X' * 256)
          expected = ValueError
        with self.assertRaises(expected):
          self.materialize()

  def test_repackaging_uses_fresh_raw_output_not_previous_tracked_parts(self):
    self.package()
    self.materialize()
    fresh = b'newly compiled model arena'
    self.target.write_bytes(fresh)
    with patch.object(parts, 'CHUNK_SIZE', 256):
      parts.package_file(self.target, self.target)
      resolved = parts.materialize_file_chunked(self.target)
    self.assertEqual(resolved.read_bytes(), fresh)
    self.assertEqual(self.target.read_bytes(), fresh)
    self.assertFalse(self.target.with_name('driving_tinygrad.pkl.chunk02of05').exists())

  def test_runtime_resolver_refuses_invalid_manifest_with_stale_raw(self):
    self.package()
    self.target.write_bytes(b'stale ignored compiler output')
    Path(f'{self.target}.chunkmanifest').write_text('0')
    helper = SOURCE / 'openpilot/selfdrive/modeld/helpers.py'
    tree = ast.parse(helper.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'modeld_pkl_path']
    namespace = {'MODELS_DIR': self.target.parent, 'Path': Path, 'materialize_file_chunked': parts.materialize_file_chunked}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(helper), 'exec'), namespace)
    with self.assertRaises(ValueError):
      namespace['modeld_pkl_path'](False)
    self.assertEqual(namespace['modeld_pkl_path'](True), self.target.parent / 'big_driving_tinygrad.pkl')

  def test_packaging_refuses_excessive_count_before_creating_parts(self):
    with patch.object(parts, 'CHUNK_SIZE', 1):
      with self.assertRaisesRegex(ValueError, 'part count limit'):
        parts.package_file(self.source, self.target)
    self.assertFalse(self.target.parent.exists())

  def test_packaging_refuses_source_changed_during_read(self):
    original_open = Path.open
    source = self.source
    replacement = b'Y' * len(self.data)
    class ChangingReader:
      def __init__(self, reader):
        self.reader, self.changed = reader, False
      def __enter__(self):
        return self
      def __exit__(self, *args):
        self.reader.close()
      def read(self, size):
        block = self.reader.read(size)
        if not self.changed:
          self.changed = True
          with original_open(source, 'wb') as out:
            out.write(replacement)
        return block
    def open_file(path, mode='r', *args, **kwargs):
      reader = original_open(path, mode, *args, **kwargs)
      return ChangingReader(reader) if path == source and mode == 'rb' else reader
    with patch.object(Path, 'open', open_file), patch.object(parts, 'CHUNK_SIZE', 256):
      with self.assertRaisesRegex(ValueError, 'changed while packaging'):
        parts.package_file(self.source, self.target)
    self.assertFalse(Path(f'{self.target}.chunkmanifest').exists())


if __name__ == '__main__':
  unittest.main()
