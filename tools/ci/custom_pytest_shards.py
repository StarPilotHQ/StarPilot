"""Run serial custom pytest shards and require exact passed or audited-unavailable outcomes."""
import argparse
from collections import Counter, defaultdict
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

from tools.ci.lifecycle_shards import build_identity, same_build_inputs

ROOT = Path(__file__).resolve().parents[2]
HEAVY_MODULES = {
  'openpilot/starpilot/tests/test_ford_bluepilot_library_lifecycle.py',
  'openpilot/starpilot/car/toyota/tests/test_camera_pedal_extension_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_eps8_sdsu_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_legacy_advert_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_camera_2aa_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_advert_filter_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_sdsu_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_prius_advert_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_legacy_sdsu_callers.py',
  'openpilot/starpilot/car/toyota/tests/test_2aa_forwarded_copy_callers.py',
}

CAPABILITY_NODES = {
  'openpilot/starpilot/galaxy_bluetooth/vendor/ifaddr/test_ifaddr.py::test_netifaces_compatibility',
  'openpilot/starpilot/system/starport/tests/test_apk_identity.py::test_real_starport_17_6_xapk',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_composed_car_frame_converts_to_the_same_picture[False-32-18-0-0]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_composed_car_frame_converts_to_the_same_picture[False-32-18-4-2]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_composed_car_frame_converts_to_the_same_picture[False-32-20-3-5]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_composed_car_frame_converts_to_the_same_picture[False-40-18-0-6]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_composed_car_frame_converts_to_the_same_picture[True-32-18-0-0]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_composed_car_frame_converts_to_the_same_picture[True-32-18-4-2]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_composed_car_frame_converts_to_the_same_picture[True-32-20-3-5]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_composed_car_frame_converts_to_the_same_picture[True-40-18-0-6]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_fused_projection_conversion_matches_rgba_composition[False-32-18-0-0]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_fused_projection_conversion_matches_rgba_composition[False-32-18-4-2]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_fused_projection_conversion_matches_rgba_composition[False-32-20-3-5]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_fused_projection_conversion_matches_rgba_composition[True-32-18-0-0]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_fused_projection_conversion_matches_rgba_composition[True-32-18-4-2]',
  'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py::test_fused_projection_conversion_matches_rgba_composition[True-32-20-3-5]',
  'openpilot/starpilot/ui/tests/test_onroad_map.py::test_overlay_draws_heading_up_roads_route_and_soft_edges',
  'openpilot/starpilot/ui/tests/test_onroad_map.py::test_overlay_opacity_fades_map_background',
  'openpilot/starpilot/ui/tests/test_onroad_map.py::test_overlay_without_gps_waits_without_drawing_roads',
  'openpilot/starpilot/ui/tests/test_onroad_map.py::test_route_stays_bright_at_minimum_map_opacity',
}
MAP_CAPABILITY_NODES = {
  'openpilot/starpilot/ui/tests/test_onroad_map.py::test_overlay_draws_heading_up_roads_route_and_soft_edges',
  'openpilot/starpilot/ui/tests/test_onroad_map.py::test_overlay_opacity_fades_map_background',
  'openpilot/starpilot/ui/tests/test_onroad_map.py::test_overlay_without_gps_waits_without_drawing_roads',
  'openpilot/starpilot/ui/tests/test_onroad_map.py::test_route_stays_bright_at_minimum_map_opacity',
}


def checksum(data):
  return hashlib.sha256(data).hexdigest()


def write_json(path, value):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = path.with_suffix(path.suffix + '.tmp')
  temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
  temporary.replace(path)


def policy():
  value = json.loads(Path(__file__).with_name('custom_pytest_workloads.json').read_text())
  if (value['version'] != 1 or set(value['independent_functions']) != HEAVY_MODULES or
      set(value['capability_skips']) != CAPABILITY_NODES):
    raise ValueError('Unreviewed custom pytest grouping policy')
  for node in MAP_CAPABILITY_NODES:
    row = value['capability_skips'][node]
    if (row.get('guard_source_path') != 'openpilot/starpilot/system/starport/tests/test_gpu_nv12.py' or
        len(row.get('guard_source_blob', '')) != 40 or row['guard'] != 'gpu_env'):
      raise ValueError('Map capability requires exact shared GPU fixture definition')
  if type(value['independent_max_cases']) is not int or not 1 <= value['independent_max_cases'] <= 60:
    raise ValueError('Invalid independent parameter group bound')
  numbers = [value['unknown_seconds_per_case'], value['independent_max_seconds']]
  numbers.extend(row['seconds_per_case'] for row in value['independent_functions'].values())
  numbers.extend(row['seconds'] for row in value['modules'].values())
  if any(type(n) not in (int, float) or not math.isfinite(n) or n <= 0 for n in numbers):
    raise ValueError('Invalid custom pytest workload estimate')
  if any(type(row['cases']) is not int or row['cases'] < 1 or len(row['blob']) != 40 for row in value['modules'].values()):
    raise ValueError('Invalid measured custom pytest workload')
  return value


def blob_id(path):
  data = path.read_bytes()
  return hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()


def partition(catalog, count, root=ROOT):
  """Whole modules, except explicitly reviewed independent heavy parameter cases."""
  if type(count) is not int or not 1 <= count <= 32 or not catalog or len(catalog) != len(set(catalog)):
    raise ValueError('Empty, duplicate or invalid custom pytest catalog')
  grouped = defaultdict(list)
  for node in catalog:
    if not isinstance(node, str) or '::' not in node:
      raise ValueError('Invalid custom pytest node ID')
    module = node.split('::', 1)[0]
    if not module.endswith('.py') or Path(module).is_absolute() or '..' in Path(module).parts:
      raise ValueError('Invalid custom pytest module path')
    grouped[module].append(node)
  config = policy()
  units = []
  for module, nodes in sorted(grouped.items()):
    if module in config['independent_functions']:
      rate = config['independent_functions'][module]['seconds_per_case']
      functions = defaultdict(list)
      for node in nodes:
        function = node.split('::', 1)[1].split('[', 1)[0]
        if not function.isidentifier() or not function.startswith('test_'):
          raise ValueError('Heavy grouping requires reviewed top-level pytest functions')
        functions[function].append(node)
      limit = min(config['independent_max_cases'], max(1, int(config['independent_max_seconds'] // rate)))
      for function, function_nodes in sorted(functions.items()):
        for start in range(0, len(function_nodes), limit):
          selected = sorted(function_nodes)[start:start + limit]
          units.append((len(selected) * rate, module + '::' + function + ':' + str(start), selected))
    else:
      rate = config['unknown_seconds_per_case']
      measured = config['modules'].get(module)
      if measured and measured['cases'] == len(nodes) and blob_id(Path(root) / module) == measured['blob']:
        rate = measured['seconds'] / measured['cases']
      units.append((rate * len(nodes), module, nodes))
  selected, loads = [set() for _ in range(count)], [0.0] * count
  for cost, _name, nodes in sorted(units, key=lambda unit: (-unit[0], unit[1])):
    index = min(range(count), key=lambda i: (loads[i], i))
    selected[index].update(nodes)
    loads[index] += cost
  return [[node for node in catalog if node in shard] for shard in selected], loads


def source_snapshot(root=ROOT):
  root = Path(root)
  return {'build': build_identity(root),
          'runners': {name: checksum((root / 'tools/ci' / name).read_bytes()) for name in (
            'run_host_tests.py', 'custom_pytest_shards.py', 'custom_pytest_workloads.json',
            'pytest_progress.py', 'worker_diagnostics.py', 'lifecycle_shards.py', 'lifecycle_build_outputs.json')}}


REAL_XAPK = 'openpilot/.cache/starport/android-auto-17-6-663454-release.xapk'


def guard_observation(guard, root):
  if guard == 'gpu_env':
    return {'STARPILOT_AUTO_GL_TEST': os.environ.get('STARPILOT_AUTO_GL_TEST')}
  if guard == 'real_xapk':
    return {'path': REAL_XAPK, 'exists': (Path(root) / REAL_XAPK).exists()}
  if guard == 'netifaces':
    try:
      importlib.import_module('netifaces')
    except ImportError:
      return {'importable': False}
    return {'importable': True}
  raise ValueError('Unreviewed capability guard')


def guard_unavailable(guard, observation):
  if guard == 'gpu_env' and set(observation) == {'STARPILOT_AUTO_GL_TEST'}:
    value = observation['STARPILOT_AUTO_GL_TEST']
    if value is None or isinstance(value, str):
      return value != '1'
  if (guard == 'real_xapk' and set(observation) == {'path', 'exists'} and
      observation['path'] == REAL_XAPK and type(observation['exists']) is bool):
    return not observation['exists']
  if guard == 'netifaces' and set(observation) == {'importable'} and type(observation['importable']) is bool:
    return not observation['importable']
  raise ValueError('Forged capability condition evidence')


def capability_state(selected, root=ROOT):
  inventory = policy()['capability_skips']
  state = {}
  for node in selected:
    if node in inventory:
      row = inventory[node]
      state[node] = {'source_blob': blob_id(Path(root) / node.split('::')[0]),
                     'guard': row['guard'], 'observation': guard_observation(row['guard'], root)}
      if node in MAP_CAPABILITY_NODES:
        state[node]['guard_source_blob'] = blob_id(Path(root) / row['guard_source_path'])
  return state


def validate_capabilities(selected, state, root=ROOT):
  inventory = policy()['capability_skips']
  if set(state) != set(selected) & set(inventory):
    raise ValueError('Missing or forged audited capability nodes')
  for node, record in state.items():
    row = inventory[node]
    fields = {'source_blob', 'guard', 'observation'} | ({'guard_source_blob'} if node in MAP_CAPABILITY_NODES else set())
    if set(record) != fields or record['guard'] != row['guard']:
      raise ValueError('Forged audited capability guard')
    if record['source_blob'] != blob_id(Path(root) / node.split('::')[0]):
      raise ValueError('Audited capability source differs from checkout')
    if node in MAP_CAPABILITY_NODES and record['guard_source_blob'] != blob_id(Path(root) / row['guard_source_path']):
      raise ValueError('Shared GPU capability guard source differs from checkout')
    guard_unavailable(record['guard'], record['observation'])


def validate_outcomes(selected, reports, capabilities=None):
  capabilities = {} if capabilities is None else capabilities
  inventory = policy()['capability_skips']
  if not selected or set(reports) != set(selected):
    raise ValueError('Missing or unexpected custom pytest outcomes')
  skipped = []
  for node in selected:
    phases = reports[node]
    if (Counter(row['phase'] for row in phases) == Counter({'setup': 1, 'call': 1, 'teardown': 1}) and
        all(row['status'] == 'passed' for row in phases)):
      continue
    row = inventory.get(node)
    record = capabilities.get(node)
    if (row is None or record is None or record['source_blob'] != row['source_blob'] or
        (node in MAP_CAPABILITY_NODES and record.get('guard_source_blob') != row['guard_source_blob']) or
        record['guard'] != row['guard'] or not guard_unavailable(record['guard'], record['observation'])):
      raise ValueError('Failed, skipped or erroneous nonexempt custom pytest node')
    if Counter(report['phase'] for report in phases) != Counter({'setup': 1, 'teardown': 1}):
      raise ValueError('Audited capability skip has wrong phases')
    setup = next(report for report in phases if report['phase'] == 'setup')
    teardown = next(report for report in phases if report['phase'] == 'teardown')
    if setup['status'] != 'skipped' or setup.get('reason') != row['reason'] or teardown['status'] != 'passed':
      raise ValueError('Audited capability skip has wrong reason or outcome')
    shared_guard = {key: row[key] for key in ('guard_source_path', 'guard_source_blob')} if node in MAP_CAPABILITY_NODES else {}
    skipped.append({'id': node, 'status': 'skipped', 'disposition': 'capability-unavailable',
                    'reason': row['reason'], 'source_blob': row['source_blob'], 'guard': row['guard'],
                    'observation': record['observation'], **shared_guard})
  return skipped



def junit_address(node):
  path, bracket, parameters = node.partition('[')
  names = path.split('::')
  names[0] = names[0].replace('/', '.').removesuffix('.py')
  names[-1] += bracket + parameters
  return '.'.join(names[:-1]), names[-1]


def validate_junit(path, selected, skipped):
  cases = ElementTree.parse(path).getroot().findall('.//testcase')
  expected = {junit_address(node): node for node in selected}
  if len(expected) != len(selected) or Counter((row.get('classname'), row.get('name')) for row in cases) != Counter(expected.keys()):
    raise ValueError('JUnit must contain every selected node exactly once')
  unavailable = {row['id']: row for row in skipped}
  for case in cases:
    node = expected[(case.get('classname'), case.get('name'))]
    if case.find('failure') is not None or case.find('error') is not None:
      raise ValueError('JUnit contains failed or erroneous custom pytest case')
    skips = case.findall('skipped')
    if node not in unavailable:
      if skips:
        raise ValueError('JUnit contains an unaudited skip')
    elif (len(skips) != 1 or skips[0].get('type') != 'pytest.skip' or
          skips[0].get('message') != unavailable[node]['reason'].removeprefix('Skipped: ')):
      raise ValueError('JUnit audited capability reason differs from actual phase reports')


# Explicit pytest plugin. Selection happens after real collection, never from an invented node list.
def pytest_addoption(parser):
  group = parser.getgroup('custom-pytest-shards')
  group.addoption('--custom-shard-index', type=int)
  group.addoption('--custom-shard-count', type=int)
  group.addoption('--custom-shard-output')


def pytest_configure(config):
  output = config.getoption('--custom-shard-output')
  if output:
    plugin = ShardPlugin(config)
    config.pluginmanager.register(plugin, 'custom-pytest-shard-evidence')


class ShardPlugin:
  def __init__(self, config):
    self.index = config.getoption('--custom-shard-index')
    self.count = config.getoption('--custom-shard-count')
    self.output = Path(config.getoption('--custom-shard-output'))
    self.errors = []
    self.catalog = []
    self.selected = []
    self.reports = defaultdict(list)
    self.capabilities = {}

  def pytest_collectreport(self, report):
    if report.failed or report.skipped:
      self.errors.append({'id': report.nodeid, 'failure': str(report.longrepr)[:4096]})

  def pytest_collection_modifyitems(self, session, config, items):
    self.catalog = [item.nodeid for item in items]
    assignment, loads = partition(self.catalog, self.count)
    if type(self.index) is not int or not 0 <= self.index < self.count:
      raise ValueError('Invalid custom pytest shard index')
    self.selected = assignment[self.index]
    self.capabilities = capability_state(self.selected)
    selected = set(self.selected)
    rejected = [item for item in items if item.nodeid not in selected]
    items[:] = [item for item in items if item.nodeid in selected]
    config.hook.pytest_deselected(items=rejected)
    write_json(self.output / 'plan.json', {'catalog': self.catalog, 'selected': self.selected,
               'index': self.index, 'count': self.count, 'estimated_seconds': loads[self.index],
               'estimate_only': True})

  def pytest_runtest_logreport(self, report):
    row = {'phase': report.when, 'status': report.outcome, 'duration': report.duration}
    if report.skipped:
      row['reason'] = str(report.longrepr[2]) if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3 else str(report.longrepr)
    self.reports[report.nodeid].append(row)

  def pytest_sessionfinish(self, session, exitstatus):
    errors = list(self.errors)
    skipped = []
    try:
      validate_capabilities(self.selected, self.capabilities)
      skipped = validate_outcomes(self.selected, self.reports, self.capabilities)
    except ValueError as exc:
      errors.append({'outcomes': str(exc)})
    value = {'catalog': self.catalog, 'selected': self.selected, 'index': self.index, 'count': self.count,
             'reports': dict(self.reports), 'capabilities': self.capabilities, 'audited_skips': skipped,
             'collection_errors': self.errors, 'gate_errors': errors,
             'pytest_exit': int(exitstatus), 'status': 'completed' if not errors and int(exitstatus) == 0 else 'failed'}
    write_json(self.output / 'outcomes.json', value)
    if errors and int(exitstatus) == 0:
      session.exitstatus = 1


def run(index, count, output):
  output = Path(output).resolve()
  output.mkdir(parents=True, exist_ok=False)
  before = source_snapshot()
  write_json(output / 'source-before.json', before)
  env = dict(os.environ, STARPILOT_TEST_DIAGNOSTICS=str(output / 'diagnostics'))
  command = [sys.executable, str(ROOT / 'tools/ci/run_host_tests.py'), '--pytest', '-p',
             'tools.ci.custom_pytest_shards', f'--custom-shard-index={index}', f'--custom-shard-count={count}',
             f'--custom-shard-output={output}']
  child = subprocess.run(command, cwd=ROOT, env=env, check=False)
  after = source_snapshot()
  write_json(output / 'source-after.json', after)
  plan_path = output / 'plan.json'
  selected = json.loads(plan_path.read_text())['selected'] if plan_path.is_file() else []
  result = {'capabilities': capability_state(selected), 'index': index, 'count': count, 'child_exit': child.returncode, 'command': command,
            'source_before': before, 'source_after': after,
            'outcomes_sha256': checksum((output / 'outcomes.json').read_bytes()) if (output / 'outcomes.json').is_file() else None,
            'junit_sha256': checksum((output / 'diagnostics/pytest.xml').read_bytes()) if (output / 'diagnostics/pytest.xml').is_file() else None,
            'plan_sha256': checksum((output / 'plan.json').read_bytes()) if (output / 'plan.json').is_file() else None,
            'selected_collection_sha256': checksum((output / 'diagnostics/pytest-collection.json').read_bytes())
            if (output / 'diagnostics/pytest-collection.json').is_file() else None}
  success = child.returncode == 0 and before == after and all(result[key] for key in (
    'outcomes_sha256', 'junit_sha256', 'plan_sha256', 'selected_collection_sha256'))
  result['status'] = 'completed' if success else 'failed'
  write_json(output / 'receipt.json', result)
  return 0 if success else 1


def aggregate(directory, count, root=ROOT):
  paths = sorted(Path(directory).rglob('receipt.json'))
  if len(paths) != count:
    raise ValueError('Missing or duplicate custom pytest shard jobs')
  expected_source = source_snapshot(root)
  full = None
  seen = []
  audited_skips = []
  indices = set()
  for path in paths:
    doc = json.loads(path.read_text())
    index = doc['index']
    if type(index) is not int or not 0 <= index < count or index in indices or doc['count'] != count:
      raise ValueError('Invalid or duplicate custom pytest shard index')
    indices.add(index)
    if doc['status'] != 'completed' or doc['child_exit'] != 0:
      raise ValueError('Failed, cancelled or incomplete custom pytest job')
    before, after = doc['source_before'], doc['source_after']
    if before != after or before['runners'] != expected_source['runners']:
      raise ValueError('Source or runner changed during execution')
    if not same_build_inputs(before['build'], expected_source['build']):
      raise ValueError('Custom pytest source HEAD, input closure or build recipe mismatch')
    outcome_path = path.with_name('outcomes.json')
    junit = path.parent / 'diagnostics/pytest.xml'
    if not outcome_path.is_file() or checksum(outcome_path.read_bytes()) != doc['outcomes_sha256']:
      raise ValueError('Missing or changed custom pytest outcomes artifact')
    if not junit.is_file() or checksum(junit.read_bytes()) != doc['junit_sha256']:
      raise ValueError('Missing or changed custom pytest JUnit artifact')
    for name, key in (('plan.json', 'plan_sha256'), ('diagnostics/pytest-collection.json', 'selected_collection_sha256')):
      artifact = path.parent / name
      if not artifact.is_file() or checksum(artifact.read_bytes()) != doc[key]:
        raise ValueError('Missing or changed actual collection witness')
    plan = json.loads(path.with_name('plan.json').read_text())
    outcome = json.loads(outcome_path.read_text())
    if any(plan[key] != outcome[key] for key in ('index', 'count', 'catalog', 'selected')):
      raise ValueError('Actual collection plan and outcomes disagree')
    actual_selected = json.loads((path.parent / 'diagnostics/pytest-collection.json').read_text())
    if actual_selected != outcome['selected']:
      raise ValueError('Actual pytest progress collection differs from selected outcomes')
    if outcome['status'] != 'completed' or outcome['pytest_exit'] != 0 or outcome['collection_errors'] or outcome['gate_errors']:
      raise ValueError('Custom pytest collection, setup or execution failed')
    if outcome['index'] != index or outcome['count'] != count:
      raise ValueError('Custom pytest outcome job identity differs')
    catalog = outcome['catalog']
    if full is None:
      full = catalog
    if catalog != full:
      raise ValueError('Custom pytest full catalogs disagree')
    assignment, _ = partition(catalog, count, root)
    if outcome['selected'] != assignment[index]:
      raise ValueError('Forged custom pytest case assignment')
    validate_capabilities(outcome['selected'], outcome['capabilities'], root)
    if doc['capabilities'] != outcome['capabilities']:
      raise ValueError('Wrapper and pytest capability observations disagree')
    skipped = validate_outcomes(outcome['selected'], outcome['reports'], outcome['capabilities'])
    if skipped != outcome['audited_skips']:
      raise ValueError('Forged audited capability skip disposition')
    validate_junit(junit, outcome['selected'], skipped)
    audited_skips.extend(skipped)
    seen.extend(outcome['selected'])
  if not full or len(full) != len(set(full)) or Counter(seen) != Counter(full):
    raise ValueError('Custom pytest aggregate must cover every collected case exactly once')
  return {'status': 'completed', 'shards': count, 'cases_passed': len(full) - len(audited_skips),
          'cases_collected': len(full), 'cases_audited_skipped': len(audited_skips), 'audited_skips': audited_skips,
          'modules': len({node.split('::', 1)[0] for node in full}),
          'revision': expected_source['build']['inputs']['revision']}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('action', choices=('run', 'aggregate'))
  parser.add_argument('--index', type=int)
  parser.add_argument('--count', type=int, default=32)
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--directory', type=Path)
  args = parser.parse_args()
  if args.action == 'run':
    if args.index is None:
      parser.error('--index is required')
    return run(args.index, args.count, args.output)
  if args.directory is None:
    parser.error('--directory is required')
  result = aggregate(args.directory, args.count)
  write_json(args.output, result)
  print(json.dumps(result))
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
