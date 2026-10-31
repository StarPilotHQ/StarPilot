"""Run omitted native pytest cases serially against the gate's instrumented library."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest import mock

from tools.ci import vehicle_pytest

ROOT = Path(__file__).resolve().parents[2]
# These denial classes belong to the existing mandatory RELEASE vehicle plan.
RELEASE_ONLY = frozenset((
  'opendbc_repo/opendbc/safety/tests/test_hyundai_main_rearm.py::TestHkgMainRelease',
  'opendbc_repo/opendbc/safety/tests/test_hyundai_classic_cancel_joined.py::TestClassicCancelReleaseDenied',
))


def targets_for_coverage(root=ROOT):
  native_root = root / 'opendbc_repo/opendbc/safety/tests'
  selected = set()
  # unittest already owns its TestCase subclasses, including decorated classes.
  for path in sorted(native_root.glob('test_*.py')):
    relative = path.relative_to(root).as_posix()
    for node in ast.parse(path.read_text(), filename=str(path)).body:
      function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_')
      plain_class = isinstance(node, ast.ClassDef) and node.name.startswith('Test') and not node.bases
      if function or plain_class:
        selected.add(f'{relative}::{node.name}')
  # These existing end-to-end callers bind the supplied library. Other vehicle suites
  # deliberately compile distinct DEBUG/RELEASE artifacts and retain their own gates.
  for relative in ('openpilot/starpilot/car/nissan/tests/test_aol.py',
                   'openpilot/starpilot/car/rivian/tests/test_aol_native.py'):
    if (root / relative).is_file():
      selected.add(relative)
  return sorted(selected - RELEASE_ONLY)



class InstrumentedLibraries:
  """Admit only the supplied library and authentic source-matched coverage builds."""
  def __init__(self, native, module, root=ROOT):
    self.native = Path(native).resolve()
    self.module = module
    self.root = root
    self.original_build = module._build_libsafety
    self.original_load = module.load
    self.registered = {str(self.native): hashlib.sha256(self.native.read_bytes()).hexdigest()}
    self.builds = []
    self.source_sha256 = self.source_signature()

  def source_signature(self):
    files = {}
    for relative in ('opendbc_repo/opendbc/safety', 'opendbc_repo/opendbc/bluepilot_lateral'):
      for path in sorted((self.root / relative).rglob('*')):
        if path.is_file() and path.suffix in ('.h', '.c', '.py'):
          files[path.relative_to(self.root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not files:
      raise RuntimeError('Missing native coverage source inputs')
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()

  def build(self, release=False):
    if type(release) is not bool or os.environ.get('SAFETY_COVERAGE') != '1':
      raise RuntimeError('Variant builds require genuine coverage instrumentation')
    before = self.source_signature()
    if before != self.source_sha256:
      raise RuntimeError('Native coverage sources changed before a variant build')
    path = Path(self.original_build(release=release)).resolve()
    if self.source_signature() != before or not path.is_file():
      raise RuntimeError('Native coverage sources changed during a variant build')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if str(path) in self.registered:
      raise RuntimeError('Native coverage builder must return a fresh artifact')
    self.registered[str(path)] = digest
    self.builds.append({'path': str(path), 'sha256': digest, 'source_sha256': before,
                        'variant': 'release' if release else 'debug', 'instrumented': True})
    return str(path)

  def load(self, path):
    if self.source_signature() != self.source_sha256:
      raise RuntimeError('Native coverage sources changed before loading')
    path = Path(path).resolve()
    expected = self.registered.get(str(path))
    if expected is None or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
      raise RuntimeError('Native coverage cases must use a registered unchanged instrumented library')
    return self.original_load(path)


def report_complete(report, targets, native_sha256):
  if (not isinstance(report, dict) or type(report.get('schema_version')) is not int or
      report.get('schema_version') != 1 or report.get('full_catalog') is not True):
    return False
  builds = report.get('native_builds')
  source = report.get('native_source_sha256')
  if (not isinstance(builds, list) or not isinstance(source, str) or len(source) != 64 or
      any(char not in '0123456789abcdef' for char in source)):
    return False
  for row in builds:
    if (not isinstance(row, dict) or row.get('instrumented') is not True or row.get('variant') not in ('debug', 'release') or
        not isinstance(row.get('path'), str) or not Path(row['path']).is_absolute() or
        not all(isinstance(row.get(key), str) and len(row[key]) == 64 and all(char in '0123456789abcdef' for char in row[key])
                for key in ('sha256', 'source_sha256')) or row['source_sha256'] != report.get('native_source_sha256')):
      return False
  if len({row['path'] for row in builds}) != len(builds):
    return False
  ids, records = report.get('nodeids'), report.get('records')
  if (report.get('targets') != targets or not targets or report.get('native_sha256') != native_sha256 or
      report.get('native_bound_before') is not True or report.get('native_bound_after') is not True or
      report.get('collection_errors') != [] or report.get('execution_errors') != [] or
      not isinstance(ids, list) or not ids or not all(type(nodeid) is str for nodeid in ids) or len(set(ids)) != len(ids) or not isinstance(records, list)):
    return False
  if not all(isinstance(row, dict) and row.get('status') in ('passed', 'skipped') for row in records):
    return False
  actual = [row.get('id') for row in records]
  return (all(type(identity) is str for identity in actual) and len(actual) == len(ids) and len(set(actual)) == len(actual) and
          set(actual) == {vehicle_pytest.test_id(nodeid) for nodeid in ids} and any(row['status'] == 'passed' for row in records))


def run(native, output, targets=None):
  from opendbc.safety.tests.libsafety import libsafety_py
  native = Path(native).resolve()
  selected = targets_for_coverage()
  full_catalog = targets is None
  targets = selected if targets is None else list(targets)
  if not targets or any(not any(target == item or target.startswith(item + '::') for item in selected) for target in targets):
    raise ValueError('Coverage subset must name existing native catalog cases')
  digest = hashlib.sha256(native.read_bytes()).hexdigest()
  libsafety_py.load(native)
  libraries = InstrumentedLibraries(native, libsafety_py)

  report = {'schema_version': 1, 'full_catalog': full_catalog, 'targets': targets, 'native_sha256': digest,
            'native_path': str(native), 'native_bound_before': Path(libsafety_py.libpath).resolve() == native,
            'nodeids': [], 'records': [], 'collection_errors': [], 'execution_errors': []}
  with (mock.patch.object(libsafety_py, 'load', side_effect=libraries.load),
        mock.patch.object(libsafety_py, '_build_libsafety', side_effect=libraries.build)):
    nodeids, errors = vehicle_pytest.collect(targets)
    report.update(nodeids=nodeids, collection_errors=errors)
    if not errors:
      records, errors = vehicle_pytest.run(nodeids)
      report.update(records=records, execution_errors=errors)
  report['native_builds'] = libraries.builds
  report['native_source_sha256'] = libraries.source_sha256
  report['native_bound_after'] = (Path(libsafety_py.libpath).resolve() == native and hashlib.sha256(native.read_bytes()).hexdigest() == digest and
                                    libraries.source_signature() == libraries.source_sha256)
  Path(output).write_text(json.dumps(report, indent=2) + '\n')
  # The root's bounded native subset is a diagnostic; production always requires the complete catalog.
  checked = dict(report, full_catalog=True)
  return report_complete(checked, targets, digest)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--native', required=True)
  parser.add_argument('--results-json', required=True)
  parser.add_argument('--target', action='append')
  args = parser.parse_args()
  return 0 if run(args.native, args.results_json, args.target) else 1


if __name__ == '__main__':
  sys.exit(main())
