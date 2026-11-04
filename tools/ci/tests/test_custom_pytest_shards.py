"""Pure tests for custom pytest selection, durable outcomes and mandatory completion."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

import pytest

from tools.ci import custom_pytest_shards as shards


def source():
  return {'build': {'inputs': {'revision': 'commit'}, 'declared_outputs': {}}, 'runners': {'runner': 'a' * 64}}


def reports(nodes):
  return {node: [{'phase': phase, 'status': 'passed', 'duration': .01} for phase in ('setup', 'call', 'teardown')] for node in nodes}



def save_junit(path, selected, skipped):
  root = ElementTree.Element('testsuites')
  suite = ElementTree.SubElement(root, 'testsuite')
  by_node = {row['id']: row for row in skipped}
  for node in selected:
    classname, name = shards.junit_address(node)
    case = ElementTree.SubElement(suite, 'testcase', classname=classname, name=name)
    if node in by_node:
      ElementTree.SubElement(case, 'skipped', type='pytest.skip', message=by_node[node]['reason'].removeprefix('Skipped: '))
  path.parent.mkdir(parents=True, exist_ok=True)
  ElementTree.ElementTree(root).write(path)


def save_job(directory, index, count, catalog, selected):
  root = directory / str(index)
  root.mkdir(parents=True)
  outcome = {'index': index, 'count': count, 'catalog': catalog, 'selected': selected, 'reports': reports(selected),
             'status': 'completed', 'pytest_exit': 0, 'collection_errors': [], 'gate_errors': [], 'capabilities': {}, 'audited_skips': []}
  shards.write_json(root / 'outcomes.json', outcome)
  shards.write_json(root / 'plan.json', {key: outcome[key] for key in ('index', 'count', 'catalog', 'selected')})
  shards.write_json(root / 'diagnostics/pytest-collection.json', selected)
  save_junit(root / 'diagnostics/pytest.xml', selected, [])
  receipt = {'capabilities': {}, 'index': index, 'count': count, 'status': 'completed', 'child_exit': 0,
             'source_before': source(), 'source_after': source(),
             'outcomes_sha256': shards.checksum((root / 'outcomes.json').read_bytes()),
             'junit_sha256': shards.checksum((root / 'diagnostics/pytest.xml').read_bytes()),
             'plan_sha256': shards.checksum((root / 'plan.json').read_bytes()),
             'selected_collection_sha256': shards.checksum((root / 'diagnostics/pytest-collection.json').read_bytes())}
  shards.write_json(root / 'receipt.json', receipt)
  return root


@pytest.fixture
def jobs(tmp_path, monkeypatch):
  monkeypatch.setattr(shards, 'source_snapshot', lambda root=shards.ROOT: source())
  monkeypatch.setattr(shards, 'same_build_inputs', lambda a, b: a == b)
  catalog = ['a.py::test_a[1]', 'a.py::test_a[10]', 'b.py::test_b']
  assigned, _ = shards.partition(catalog, 2)
  paths = [save_job(tmp_path, index, 2, catalog, selected) for index, selected in enumerate(assigned)]
  return tmp_path, paths, catalog


def rewrite(path, name, change):
  target = path / name
  value = json.loads(target.read_text())
  change(value)
  shards.write_json(target, value)
  if name == 'outcomes.json':
    receipt = json.loads((path / 'receipt.json').read_text())
    receipt['outcomes_sha256'] = shards.checksum(target.read_bytes())
    shards.write_json(path / 'receipt.json', receipt)


def test_complete_exact_catalog(jobs):
  directory, _, catalog = jobs
  result = shards.aggregate(directory, 2)
  assert result['cases_passed'] == len(catalog)
  assert result['shards'] == 2


@pytest.mark.parametrize('mutation', [
  lambda d: d.update(status='failed'),
  lambda d: d.update(status='cancelled'),
  lambda d: d.update(child_exit=1),
  lambda d: d.update(count=3),
  lambda d: d.update(index=1),
  lambda d: d['source_after']['runners'].update(runner='b' * 64),
  lambda d: d['source_before']['runners'].update(runner='b' * 64),
  lambda d: d['source_before']['build']['inputs'].update(revision='other'),
  lambda d: d.update(outcomes_sha256='0' * 64),
  lambda d: d.update(junit_sha256='0' * 64),
])
def test_failed_cancelled_forged_or_changed_job_denied(jobs, mutation):
  directory, paths, _ = jobs
  rewrite(paths[0], 'receipt.json', mutation)
  with pytest.raises(ValueError):
    shards.aggregate(directory, 2)


@pytest.mark.parametrize('name', ['receipt.json', 'outcomes.json', 'diagnostics/pytest.xml', 'plan.json', 'diagnostics/pytest-collection.json'])
def test_missing_job_or_artifact_denied(jobs, name):
  directory, paths, _ = jobs
  (paths[0] / name).unlink()
  with pytest.raises(ValueError):
    shards.aggregate(directory, 2)


@pytest.mark.parametrize('mutation', [
  lambda d: d.update(status='failed'),
  lambda d: d.update(pytest_exit=2),
  lambda d: d['collection_errors'].append({'id': 'bad.py'}),
  lambda d: d['gate_errors'].append({'error': 'setup'}),
  lambda d: d['catalog'].append('invented.py::test_fake'),
  lambda d: d['catalog'].append(d['catalog'][0]),
  lambda d: d['selected'].append(d['selected'][0]),
  lambda d: d.update(selected=[]),
  lambda d: d['reports'].clear(),
  lambda d: next(iter(d['reports'].values())).pop(),
  lambda d: next(iter(d['reports'].values())).append({'phase': 'call', 'status': 'passed'}),
  lambda d: next(iter(d['reports'].values()))[1].update(status='skipped'),
  lambda d: next(iter(d['reports'].values()))[1].update(status='failed'),
  lambda d: next(iter(d['reports'].values()))[0].update(status='failed'),
  lambda d: next(iter(d['reports'].values()))[2].update(status='failed'),
])
def test_collection_assignment_outcome_errors_denied(jobs, mutation):
  directory, paths, _ = jobs
  rewrite(paths[0], 'outcomes.json', mutation)
  with pytest.raises(ValueError):
    shards.aggregate(directory, 2)


def test_duplicate_job_denied(jobs):
  directory, paths, _ = jobs
  import shutil
  shutil.copytree(paths[0], directory / 'duplicate')
  with pytest.raises(ValueError):
    shards.aggregate(directory, 2)


def test_changed_source_in_all_jobs_denied_against_checkout(jobs):
  directory, paths, _ = jobs
  for path in paths:
    def change(d):
      for name in ('source_before', 'source_after'):
        d[name]['build']['inputs']['revision'] = 'forged'
    rewrite(path, 'receipt.json', change)
  with pytest.raises(ValueError, match='source HEAD'):
    shards.aggregate(directory, 2)


@pytest.mark.parametrize('mutation', [None, 'revision', 'tree', 'source', 'source_mode', 'recipe',
                                     'extra_input', 'missing_output', 'native_bytes', 'native_mode'])
def test_real_build_comparison_keeps_checkout_and_native_guards(jobs, monkeypatch, mutation):
  from tools.ci.lifecycle_shards import same_build_inputs
  directory, paths, catalog = jobs
  name = 'openpilot/selfdrive/pandad/tests/test_panda_faults'
  recipe = 'openpilot/selfdrive/pandad/SConscript'
  inventory_path = Path(shards.__file__).with_name('lifecycle_build_outputs.json')
  inventory = json.loads(inventory_path.read_text())
  record = {'sha256': 'a' * 64, 'mode': 0o644}
  expected = source()
  expected['build'] = {
    'inputs': {'revision': 'commit', 'tree': 'tree', 'tracked_inputs': {name + '.cc': copy.deepcopy(record), recipe: copy.deepcopy(record)},
               'changes': {}, 'untracked': {}, 'recipes': {recipe: copy.deepcopy(record)},
               'output_inventory_sha256': shards.checksum(inventory_path.read_bytes())},
    'declared_outputs': {path: copy.deepcopy(record) if row['required_tracked'] else None for path, row in inventory.items()},
  }
  monkeypatch.setattr(shards, 'source_snapshot', lambda root=shards.ROOT: copy.deepcopy(expected))
  monkeypatch.setattr(shards, 'same_build_inputs', same_build_inputs)
  for path in paths:
    built = copy.deepcopy(expected)
    built['build']['declared_outputs'][name] = {'sha256': 'b' * 64, 'mode': 0o755}
    if mutation in ('revision', 'tree'):
      built['build']['inputs'][mutation] = 'different'
    elif mutation == 'source':
      built['build']['inputs']['tracked_inputs'][name + '.cc']['sha256'] = 'c' * 64
    elif mutation == 'source_mode':
      built['build']['inputs']['tracked_inputs'][name + '.cc']['mode'] = 0o755
    elif mutation == 'recipe':
      built['build']['inputs']['recipes'][recipe] = {'sha256': 'c' * 64, 'mode': 0o644}
    elif mutation == 'extra_input':
      built['build']['inputs']['untracked'][name + '_other'] = record
    elif mutation == 'missing_output':
      del built['build']['declared_outputs'][name]
    after = copy.deepcopy(built)
    if mutation == 'native_bytes':
      after['build']['declared_outputs'][name]['sha256'] = 'c' * 64
    elif mutation == 'native_mode':
      after['build']['declared_outputs'][name]['mode'] = 0o644
    rewrite(path, 'receipt.json', lambda d, before=built, after=after: d.update(source_before=before, source_after=after))
  if mutation is None:
    assert shards.aggregate(directory, 2)['cases_passed'] == len(catalog)
  else:
    with pytest.raises(ValueError):
      shards.aggregate(directory, 2)


@pytest.mark.parametrize('count', [24, 32])
def test_exact_parameter_nodes_whole_other_modules_and_bounded_heavy_groups(count):
  heavy = 'openpilot/starpilot/car/toyota/tests/test_legacy_advert_callers.py'
  catalog = [f'{heavy}::test_independent[{i}]' for i in range(126)] + ['other.py::test_case[1]', 'other.py::test_case[10]']
  assigned, loads = shards.partition(catalog, count)
  assert sorted(node for group in assigned for node in group) == sorted(catalog)
  assert max(loads) <= 240
  assert any('other.py::test_case[1]' in group and 'other.py::test_case[10]' in group for group in assigned)
  assert [sum(node.startswith(heavy) for node in group) for group in assigned].count(60) == 2
  assert shards.partition(catalog, count) == (assigned, loads)


@pytest.mark.parametrize('catalog', [[], ['a.py::test_a', 'a.py::test_a'], ['../a.py::test_a'], ['/a.py::test_a'], ['a.py']])
def test_invalid_catalog_denied(catalog):
  with pytest.raises(ValueError):
    shards.partition(catalog, 24)


def test_unreviewed_heavy_class_not_split():
  with pytest.raises(ValueError, match='top-level'):
    shards.partition(['openpilot/starpilot/car/toyota/tests/test_legacy_advert_callers.py::TestShared::test_x'], 24)


def test_changed_source_invalidates_measured_duration(tmp_path, monkeypatch):
  p = tmp_path / 'a.py'
  p.write_text('original')
  config = copy.deepcopy(shards.policy())
  config['modules'] = {'a.py': {'cases': 1, 'seconds': 99., 'blob': shards.blob_id(p)}}
  monkeypatch.setattr(shards, 'policy', lambda: config)
  assert shards.partition(['a.py::test_a'], 1, tmp_path)[1] == [99.]
  p.write_text('changed')
  assert shards.partition(['a.py::test_a'], 1, tmp_path)[1] == [1.]


@pytest.mark.parametrize('body,expected', [
  ('def test_a():\n assert True\ndef test_b():\n assert True\n', 'completed'),
  ('def test_a():\n assert False\ndef test_b():\n assert True\n', 'failed'),
  ('import pytest\n@pytest.mark.skip(reason="visible skip")\ndef test_a():\n pass\ndef test_b():\n assert True\n', 'failed'),
  ('raise RuntimeError("collection failure")\n', 'failed'),
])
def test_real_pytest_plugin_durable_results(tmp_path, body, expected):
  (tmp_path / 'test_cases.py').write_text(body)
  output = tmp_path / 'evidence'
  env = dict(os.environ, PYTHONPATH=str(shards.ROOT), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
             STARPILOT_TEST_DIAGNOSTICS=str(output / 'diagnostics'))
  command = [sys.executable, '-m', 'pytest', '-q', '-p', 'tools.ci.custom_pytest_shards',
             '-p', 'tools.ci.pytest_progress', '--custom-shard-index=0', '--custom-shard-count=1',
             f'--custom-shard-output={output}', f'--junitxml={output}/diagnostics/pytest.xml', 'test_cases.py']
  child = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=20, check=False)
  outcome = json.loads((output / 'outcomes.json').read_text())
  assert outcome['status'] == expected, child.stdout.decode() + child.stderr.decode()
  assert (child.returncode == 0) == (expected == 'completed')
  assert (output / 'diagnostics/pytest.xml').is_file()
  if expected == 'completed':
    assert len(outcome['catalog']) == len(outcome['selected']) == 2
    assert list((output / 'diagnostics').glob('worker-*.jsonl'))


def test_real_child_death_leaves_failed_receipt_and_durable_node(tmp_path, monkeypatch):
  wrapper = tmp_path / 'tools/ci/run_host_tests.py'
  wrapper.parent.mkdir(parents=True)
  wrapper.write_bytes((Path(__file__).parents[1] / 'run_host_tests.py').read_bytes())
  module = tmp_path / 'openpilot/starpilot/tests/test_signal.py'
  module.parent.mkdir(parents=True)
  module.write_text('import os, signal\ndef test_child_signal():\n os.kill(os.getpid(), signal.SIGTERM)\n')
  monkeypatch.setattr(shards, 'ROOT', tmp_path)
  monkeypatch.setattr(shards, 'source_snapshot', lambda: source())
  monkeypatch.setenv('PYTHONPATH', str(Path(__file__).parents[3]))
  output = tmp_path / 'evidence'
  assert shards.run(0, 1, output) == 1
  receipt = json.loads((output / 'receipt.json').read_text())
  assert receipt['status'] == 'failed'
  assert receipt['child_exit'] == 143
  assert receipt['outcomes_sha256'] is None
  workers = list((output / 'diagnostics').glob('worker-*.json'))
  assert workers
  assert any(json.loads(path.read_text())['active_test'].endswith('::test_child_signal') for path in workers)


@pytest.mark.parametrize('module,cases,shard_count', [('test_legacy_advert_callers.py', 126, 3),
                                                      ('test_legacy_sdsu_callers.py', 240, 3),
                                                      ('test_2aa_forwarded_copy_callers.py', 60, 2)])
def test_real_plugin_exact_parameter_assignment_across_shards(tmp_path, module, cases, shard_count):
  path = tmp_path / 'openpilot/starpilot/car/toyota/tests' / module
  path.parent.mkdir(parents=True)
  path.write_text(f'import pytest\n@pytest.mark.parametrize("value", range({cases}))\ndef test_independent(value):\n assert 0 <= value < {cases}\n')
  outcomes = []
  for index in range(shard_count):
    output = tmp_path / f'shard-{index}'
    env = dict(os.environ, PYTHONPATH=str(shards.ROOT), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    command = [sys.executable, '-m', 'pytest', '-q', '-p', 'tools.ci.custom_pytest_shards',
               f'--custom-shard-index={index}', f'--custom-shard-count={shard_count}', f'--custom-shard-output={output}', str(path)]
    child = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=20, check=False)
    assert child.returncode == 0, child.stdout.decode() + child.stderr.decode()
    outcomes.append(json.loads((output / 'outcomes.json').read_text()))
  seen = [node for outcome in outcomes for node in outcome['selected']]
  assert len(seen) == len(set(seen)) == cases
  assert set(seen) == set(outcomes[0]['catalog'])
  for outcome in outcomes:
    shards.validate_outcomes(outcome['selected'], outcome['reports'])


def test_forged_catalog_in_all_outcomes_denied_by_actual_collection_witness(jobs):
  directory, paths, _ = jobs
  for path in paths:
    rewrite(path, 'outcomes.json', lambda d: d['catalog'].append('invented.py::test_fake'))
  with pytest.raises(ValueError, match='collection plan'):
    shards.aggregate(directory, 2)


def test_changed_progress_collection_denied(jobs):
  directory, paths, _ = jobs
  shards.write_json(paths[0] / 'diagnostics/pytest-collection.json', ['invented.py::test_fake'])
  with pytest.raises(ValueError, match='collection witness'):
    shards.aggregate(directory, 2)


def test_real_wrapper_shards_and_aggregate_keep_every_case(tmp_path, monkeypatch):
  wrapper = tmp_path / 'tools/ci/run_host_tests.py'
  wrapper.parent.mkdir(parents=True)
  wrapper.write_bytes((Path(__file__).parents[1] / 'run_host_tests.py').read_bytes())
  tests = tmp_path / 'openpilot/starpilot/tests'
  tests.mkdir(parents=True)
  for name in ('a', 'b'):
    (tests / f'test_{name}.py').write_text(f'def test_{name}():\n assert True\n')
  monkeypatch.setattr(shards, 'ROOT', tmp_path)
  monkeypatch.setattr(shards, 'source_snapshot', lambda root=tmp_path: source())
  monkeypatch.setattr(shards, 'same_build_inputs', lambda a, b: a == b)
  monkeypatch.setenv('PYTHONPATH', str(Path(__file__).parents[3]))
  for index in range(2):
    assert shards.run(index, 2, tmp_path / 'jobs' / str(index)) == 0
  result = shards.aggregate(tmp_path / 'jobs', 2)
  assert result['cases_passed'] == 2
  assert result['status'] == 'completed'


def test_real_module_collection_skip_denied_even_when_other_cases_pass(tmp_path):
  (tmp_path / 'test_skip.py').write_text('import pytest\npytest.skip("module skip must remain failure", allow_module_level=True)\n')
  (tmp_path / 'test_pass.py').write_text('def test_pass():\n assert True\n')
  output = tmp_path / 'evidence'
  env = dict(os.environ, PYTHONPATH=str(shards.ROOT), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
  child = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'tools.ci.custom_pytest_shards',
                          '--custom-shard-index=0', '--custom-shard-count=1', f'--custom-shard-output={output}',
                          'test_skip.py', 'test_pass.py'], cwd=tmp_path, env=env, capture_output=True, timeout=20, check=False)
  outcome = json.loads((output / 'outcomes.json').read_text())
  assert child.returncode != 0
  assert outcome['status'] == 'failed'
  assert outcome['collection_errors'][0]['id'] == 'test_skip.py'
  assert len(outcome['selected']) == 1
  shards.validate_outcomes(outcome['selected'], outcome['reports'])



def unavailable_observation(guard):
  return {'gpu_env': {'STARPILOT_AUTO_GL_TEST': None}, 'netifaces': {'importable': False},
          'real_xapk': {'path': shards.REAL_XAPK, 'exists': False}}[guard]


def test_audited_capability_fingerprints_match_current_sources():
  """A binding migration must re-audit pins without broadening any skip guard."""
  for node, row in shards.policy()['capability_skips'].items():
    assert shards.blob_id(shards.ROOT / node.split('::')[0]) == row['source_blob'], node
    if 'guard_source_path' in row:
      assert shards.blob_id(shards.ROOT / row['guard_source_path']) == row['guard_source_blob'], node


def skipped_reports(node, reason):
  return {node: [{'phase': 'setup', 'status': 'skipped', 'duration': .01, 'reason': reason},
                 {'phase': 'teardown', 'status': 'passed', 'duration': .01}]}


@pytest.fixture
def capability_guards(monkeypatch):
  inventory = shards.policy()['capability_skips']
  blobs = {node.split('::')[0]: row['source_blob'] for node, row in inventory.items()}
  monkeypatch.setattr(shards, 'blob_id', lambda path: blobs[path.relative_to(shards.ROOT).as_posix()])
  monkeypatch.setattr(shards, 'guard_observation', lambda guard, root: unavailable_observation(guard))
  return inventory


@pytest.mark.parametrize('node', sorted(shards.CAPABILITY_NODES))
def test_each_original_capability_skip_remains_unavailable_never_passed(capability_guards, node):
  row = capability_guards[node]
  state = shards.capability_state([node])
  shards.validate_capabilities([node], state)
  result = shards.validate_outcomes([node], skipped_reports(node, row['reason']), state)
  assert result == [{'id': node, 'status': 'skipped', 'disposition': 'capability-unavailable',
                     'reason': row['reason'], 'source_blob': row['source_blob'], 'guard': row['guard'],
                     'observation': unavailable_observation(row['guard']),
                     **({key: row[key] for key in ('guard_source_path', 'guard_source_blob')} if node in shards.MAP_CAPABILITY_NODES else {})}]
  assert shards.validate_outcomes([node], reports([node]), state) == []


@pytest.mark.parametrize('node', sorted(shards.CAPABILITY_NODES))
def test_each_capability_wrong_reason_or_source_denied(capability_guards, node):
  row = capability_guards[node]
  state = shards.capability_state([node])
  with pytest.raises(ValueError):
    shards.validate_outcomes([node], skipped_reports(node, row['reason'] + ' changed'), state)
  state[node]['source_blob'] = '0' * 40
  with pytest.raises(ValueError):
    shards.validate_outcomes([node], skipped_reports(node, row['reason']), state)
  with pytest.raises(ValueError):
    shards.validate_capabilities([node], state)


@pytest.mark.parametrize('node', sorted(shards.CAPABILITY_NODES))
def test_each_available_capability_cannot_be_skipped(capability_guards, node):
  row = capability_guards[node]
  state = shards.capability_state([node])
  observation = {'gpu_env': {'STARPILOT_AUTO_GL_TEST': '1'}, 'netifaces': {'importable': True},
                 'real_xapk': {'path': shards.REAL_XAPK, 'exists': True}}[row['guard']]
  state[node]['observation'] = observation
  with pytest.raises(ValueError):
    shards.validate_outcomes([node], skipped_reports(node, row['reason']), state)


def test_forged_capability_node_and_condition_denied(capability_guards):
  node = sorted(capability_guards)[0]
  state = shards.capability_state([node])
  with pytest.raises(ValueError):
    shards.validate_outcomes([node + '-forged'], skipped_reports(node + '-forged', capability_guards[node]['reason']), state)
  state[node]['observation'] = {'unavailable': True}
  with pytest.raises(ValueError):
    shards.validate_capabilities([node], state)


def test_audited20_counts_are_skipped_not_passed_in_exact_aggregate(tmp_path, monkeypatch, capability_guards):
  monkeypatch.setattr(shards, 'source_snapshot', lambda root=shards.ROOT: source())
  monkeypatch.setattr(shards, 'same_build_inputs', lambda a, b: a == b)
  catalog = sorted(capability_guards) + ['required.py::test_required']
  assigned, _ = shards.partition(catalog, 2)
  for index, selected in enumerate(assigned):
    path = save_job(tmp_path, index, 2, catalog, selected)
    state = shards.capability_state(selected)
    phase_reports = reports(selected)
    for node in state:
      phase_reports.update(skipped_reports(node, capability_guards[node]['reason']))
    skipped = shards.validate_outcomes(selected, phase_reports, state)
    rewrite(path, 'outcomes.json', lambda d, phases=phase_reports, caps=state, skips=skipped:
            d.update(reports=phases, capabilities=caps, audited_skips=skips))
    save_junit(path / 'diagnostics/pytest.xml', selected, skipped)
    junit_hash = shards.checksum((path / 'diagnostics/pytest.xml').read_bytes())
    rewrite(path, 'receipt.json', lambda d, caps=state, digest=junit_hash: d.update(capabilities=caps, junit_sha256=digest))
  result = shards.aggregate(tmp_path, 2)
  assert result['cases_collected'] == 21
  assert result['cases_passed'] == 1
  assert result['cases_audited_skipped'] == 20
  assert len(result['audited_skips']) == 20
  assert all(row['status'] == 'skipped' for row in result['audited_skips'])


def test_actual_capability_guard_conditions(tmp_path, monkeypatch):
  monkeypatch.delenv('STARPILOT_AUTO_GL_TEST', raising=False)
  assert shards.guard_unavailable('gpu_env', shards.guard_observation('gpu_env', tmp_path))
  monkeypatch.setenv('STARPILOT_AUTO_GL_TEST', '1')
  assert not shards.guard_unavailable('gpu_env', shards.guard_observation('gpu_env', tmp_path))
  assert shards.guard_unavailable('real_xapk', shards.guard_observation('real_xapk', tmp_path))
  apk = tmp_path / shards.REAL_XAPK
  apk.parent.mkdir(parents=True)
  apk.write_bytes(b'presence-only guard witness; not a real APK')
  assert not shards.guard_unavailable('real_xapk', shards.guard_observation('real_xapk', tmp_path))
  def unavailable(name):
    assert name == 'netifaces'
    raise ImportError('optional dependency absent')
  monkeypatch.setattr(shards.importlib, 'import_module', unavailable)
  assert shards.guard_unavailable('netifaces', shards.guard_observation('netifaces', tmp_path))
  monkeypatch.setattr(shards.importlib, 'import_module', lambda name: object())
  assert not shards.guard_unavailable('netifaces', shards.guard_observation('netifaces', tmp_path))



@pytest.mark.parametrize('change', ['missing', 'duplicate', 'skip', 'failure', 'error'])
def test_junit_false_success_and_unaudited_skips_denied(jobs, change):
  directory, paths, _ = jobs
  path = paths[0] / 'diagnostics/pytest.xml'
  tree = ElementTree.parse(path)
  suite = tree.getroot().find('testsuite')
  assert suite is not None
  case = suite.find('testcase')
  assert case is not None
  if change == 'missing':
    suite.remove(case)
  elif change == 'duplicate':
    suite.append(copy.deepcopy(case))
  else:
    ElementTree.SubElement(case, 'skipped' if change == 'skip' else change)
  tree.write(path)
  rewrite(paths[0], 'receipt.json', lambda d: d.update(junit_sha256=shards.checksum(path.read_bytes())))
  with pytest.raises(ValueError, match='JUnit'):
    shards.aggregate(directory, 2)


def test_real_pytest_all20_audited_guards_have_skipped_junit_not_passes(tmp_path):
  """Real pytest phases/JUnit with controlled guard witnesses; no graphics or private APK is tested."""
  import shutil
  package = tmp_path / 'tools/ci'
  package.mkdir(parents=True)
  (tmp_path / 'tools/__init__.py').touch()
  (package / '__init__.py').touch()
  for name in ('custom_pytest_shards.py', 'lifecycle_shards.py', 'pytest_progress.py', 'worker_diagnostics.py'):
    shutil.copyfile(shards.ROOT / 'tools/ci' / name, package / name)
  config = shards.policy()
  by_module = {}
  for node in sorted(shards.CAPABILITY_NODES):
    by_module.setdefault(node.split('::')[0], []).append(node)
  for module, nodes in by_module.items():
    source_path = tmp_path / module
    source_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['import pytest', 'from unittest import SkipTest']
    groups = {}
    for node in nodes:
      tail = node.split('::', 1)[1]
      function, bracket, parameter = tail.partition('[')
      groups.setdefault(function, []).append(parameter.removesuffix(']') if bracket else None)
    for function, parameters in groups.items():
      reason = config['capability_skips'][nodes[0]]['reason'].removeprefix('Skipped: ')
      if parameters != [None]:
        lines.append(f'@pytest.mark.parametrize("case", range({len(parameters)}), ids={parameters!r})')
        signature = 'case'
      else:
        signature = ''
      lines.extend([f'def {function}({signature}):', f'  raise SkipTest({reason!r})'])
    # The original guards skip during setup; a fixture models that exact phase.
    lines = ['import pytest', 'from unittest import SkipTest', '@pytest.fixture(autouse=True)',
             'def original_capability_guard():', f'  raise SkipTest({reason!r})'] + [
               line.replace(f'  raise SkipTest({reason!r})', '  assert False, "body must not run when original guard is unavailable"')
               for line in lines[2:]]
    source_path.write_text('\n'.join(lines) + '\n')
    for node in nodes:
      config['capability_skips'][node]['source_blob'] = shards.blob_id(source_path)
  for node in shards.MAP_CAPABILITY_NODES:
    row = config['capability_skips'][node]
    row['guard_source_blob'] = shards.blob_id(tmp_path / row['guard_source_path'])
  shards.write_json(package / 'custom_pytest_workloads.json', config)
  (tmp_path / 'guard_witness.py').write_text('''def pytest_configure(config):
  from tools.ci import custom_pytest_shards as s
  def observe(guard, root):
    return {'gpu_env': {'STARPILOT_AUTO_GL_TEST': None}, 'netifaces': {'importable': False},
            'real_xapk': {'path': s.REAL_XAPK, 'exists': False}}[guard]
  s.guard_observation = observe
''')
  output = tmp_path / 'evidence'
  env = dict(os.environ, PYTHONPATH=str(tmp_path), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
             STARPILOT_TEST_DIAGNOSTICS=str(output / 'diagnostics'))
  child = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'tools.ci.custom_pytest_shards',
                          '-p', 'tools.ci.pytest_progress', '-p', 'guard_witness', '--custom-shard-index=0',
                          '--custom-shard-count=1', f'--custom-shard-output={output}',
                          f'--junitxml={output}/diagnostics/pytest.xml', *by_module], cwd=tmp_path,
                         env=env, capture_output=True, timeout=20, check=False)
  outcome = json.loads((output / 'outcomes.json').read_text())
  assert child.returncode == 0, child.stdout.decode() + child.stderr.decode()
  assert outcome['status'] == 'completed'
  assert len(outcome['catalog']) == len(outcome['audited_skips']) == 20
  assert all(row['status'] == 'skipped' for row in outcome['audited_skips'])
  shards.validate_junit(output / 'diagnostics/pytest.xml', outcome['selected'], outcome['audited_skips'])
  progress = json.loads((output / 'diagnostics/pytest-collection.json').read_text())
  assert progress == outcome['selected']


def test_workflow_uses_test_interpreter_for_helper_and_actual_wrapper(tmp_path):
  """Two real Python environments; optional module exists only in configured test environment."""
  import venv
  project = tmp_path / 'project'
  test_env = project / '.venv'
  parent_env = tmp_path / 'parent-python'
  builder = venv.EnvBuilder(with_pip=False, symlinks=True)
  builder.create(test_env)
  builder.create(parent_env)
  if os.name == 'nt':
    pytest.fail('The hosted custom shard workflow requires Linux Python paths')
  test_python = test_env / 'bin/python'
  parent_python = parent_env / 'bin/python'
  probe_file = tmp_path / 'worker-observation.json'
  for interpreter in (test_python, parent_python):
    result = subprocess.check_output([str(interpreter), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True)
    site = Path(result.strip())
    site.mkdir(parents=True, exist_ok=True)
    # This probe module records interpreter/capability only; it does not claim to execute pytest cases.
    (site / 'pytest.py').write_text('''import json, os, sys
from pathlib import Path
from tools.ci import custom_pytest_shards as s
Path(os.environ['OPTIONAL_PROBE']).write_text(json.dumps({
  'python': sys.executable, 'guard': s.guard_observation('netifaces', Path.cwd())}))
''')
    if interpreter == test_python:
      (site / 'netifaces.py').write_text('"""Pure optional-dependency witness; no native package imported."""\n')
  wrapper = project / 'tools/ci/run_host_tests.py'
  wrapper.parent.mkdir(parents=True)
  wrapper.write_bytes((shards.ROOT / 'tools/ci/run_host_tests.py').read_bytes())
  test_file = project / 'openpilot/starpilot/tests/test_probe.py'
  test_file.parent.mkdir(parents=True)
  test_file.write_text('def test_probe():\n assert True\n')
  probe = tmp_path / 'parent-probe.py'
  probe.write_text('''import json, os, subprocess, sys
from pathlib import Path
from tools.ci import custom_pytest_shards as s
project = Path(sys.argv[1])
parent = {'python': sys.executable, 'guard': s.guard_observation('netifaces', project)}
subprocess.run([sys.executable, str(project / 'tools/ci/run_host_tests.py'), '--pytest'], cwd=project, check=True)
worker = json.loads(Path(os.environ['OPTIONAL_PROBE']).read_text())
print(json.dumps({'parent': parent, 'worker': worker}))
''')
  environment = dict(os.environ, PYTHONPATH=str(shards.ROOT), OPTIONAL_PROBE=str(probe_file))
  environment.pop('PYTHONHOME', None)
  wrong = json.loads(subprocess.check_output([str(parent_python), str(probe), str(project)], env=environment, text=True, timeout=20))
  assert wrong['parent']['guard'] == {'importable': False}
  workflow = (shards.ROOT / '.github/workflows/tests.yaml').read_text()
  run_line = next(line for line in workflow.splitlines() if 'tools.ci.custom_pytest_shards run ' in line)
  interpreter = run_line.split('run: ', 1)[1].split()[0]
  assert interpreter == '.venv/bin/python'
  chosen = json.loads(subprocess.check_output([str(project / interpreter), str(probe), str(project)], env=environment, text=True, timeout=20))
  assert chosen['parent']['guard'] == chosen['worker']['guard'] == {'importable': True}
  assert chosen['parent']['python'] == chosen['worker']['python'] == str(test_python)
  aggregate_line = next(line for line in workflow.splitlines() if 'tools.ci.custom_pytest_shards aggregate ' in line)
  assert aggregate_line.split('run: ', 1)[1].startswith('python3 -m ')
  assert 'passed or audited capability-unavailable' in workflow


@pytest.mark.parametrize('node', sorted(shards.MAP_CAPABILITY_NODES))
def test_map_skip_requires_both_consumer_and_shared_fixture_blobs(capability_guards, node):
  row = capability_guards[node]
  state = shards.capability_state([node])
  shards.validate_capabilities([node], state)
  state[node]['guard_source_blob'] = '0' * 40
  with pytest.raises(ValueError, match='Shared GPU'):
    shards.validate_capabilities([node], state)
  with pytest.raises(ValueError):
    shards.validate_outcomes([node], skipped_reports(node, row['reason']), state)


def test_reviewed_advert_filter_functions_preserve_272_independent_nodes():
  module = 'openpilot/starpilot/car/toyota/tests/test_advert_filter_callers.py'
  counts = {'test_each_advertised_filter_actual_callers': 64,
            'test_physical_saved_off_and_cold_lateral': 48,
            'test_ordinary_cruise_without_aol': 16,
            'test_alpha_does_not_remove_default_long': 16,
            'test_active_hybrid_resume': 8,
            'test_required_sources_withdraw_and_recover_through_actual_can': 120}
  catalog = [f'{module}::{name}[{index}]' for name, count in counts.items() for index in range(count)]
  assignment, loads = shards.partition(catalog, 24)
  assert len(catalog) == 272
  assert sorted(node for group in assignment for node in group) == sorted(catalog)
  assert sorted(len(group) for group in assignment if group) == [4, 8, 16, 16, 48, 60, 60, 60]
  assert max(loads) <= 240
  reversed_assignment, reversed_loads = shards.partition(list(reversed(catalog)), 24)
  assert [set(group) for group in reversed_assignment] == [set(group) for group in assignment]
  assert reversed_loads == loads


@pytest.mark.parametrize('module,counts,expected_sizes,rate', [
  ('test_legacy_sdsu_callers.py', {'test_each_legacy_sdsu_actual_callers': 32,
                                'test_legacy_sdsu_faults_physical_and_saved_off': 80,
                                'test_legacy_ordinary_cruise_without_aol': 32,
                                'test_legacy_alpha_does_not_replace_nongated_owner': 12,
                                'test_legacy_hybrid_active_resume': 16,
                                'test_dsu_main_and_status_are_independent_sources': 36,
                                'test_legacy_lateral_below_stock_engagement_speed': 32},
   [12, 16, 20, 32, 32, 32, 36, 60], 4.),
  ('test_2aa_forwarded_copy_callers.py', {'test_forwarded_advertisement_actual_callers': 60}, [12, 48], 5.),
])
def test_measured_slow_module_exact_function_and_leaf_groups(module, counts, expected_sizes, rate):
  path = 'openpilot/starpilot/car/toyota/tests/' + module
  catalog = [f'{path}::{name}[{index}]' for name, count in counts.items() for index in range(count)]
  assignment, loads = shards.partition(catalog, 24)
  assert sorted(node for group in assignment for node in group) == sorted(catalog)
  assert sorted(len(group) for group in assignment if group) == expected_sizes
  assert max(loads) <= 240
  assert sum(loads) == len(catalog) * rate
  reverse, reverse_loads = shards.partition(list(reversed(catalog)), 24)
  assert [set(group) for group in reverse] == [set(group) for group in assignment]
  assert reverse_loads == loads


@pytest.mark.parametrize('count', [0, 33, True, 1.5])
def test_invalid_shard_count_still_denied(count):
  with pytest.raises(ValueError):
    shards.partition(['a.py::test_a'], count)


@pytest.mark.parametrize("count", [24, 32])
def test_ford_library_cases_keep_complete_timelines_in_bounded_groups(count):
  module = 'openpilot/starpilot/tests/test_ford_bluepilot_library_lifecycle.py'
  counts = {
      "test_library_angle_curvature_aol_native_lifecycle": 96,
      "test_model_loss_still_withdraws_library_output_through_actual_callers": 3,
      "test_gas_held_first_engagement": 8,
      "test_sustained_real_loop_lag_still_disables_ford": 1,
      "test_cancel_at_every_native_receipt_phase": 20,
      "test_manual_eps_refusal_keeps_aol_intent_without_steering_through_fault": 2,
      "test_cruise_resume_preserves_deliberate_lateral_pause": 2,
      "test_manual_steering_retires_held_turn_limit_alert": 2,
      "test_fresh_cruise_withdrawal_after_previous_output_expires": 4,
      "test_reengaging_in_a_manual_curve_respects_native_convergence": 32,
      "test_brake_retains_independent_lateral_intent": 48,
      "test_starpilot_tuning_uses_current_native_aol_path": 32,
      "test_starpilot_low_speed_assistance_survives_brake_with_native_limits": 8
  }
  catalog = [f"{module}::{name}[{index}]" for name, cases in counts.items() for index in range(cases)]
  assignment, loads = shards.partition(catalog, count)
  assert len(catalog) == 258
  assert sorted(node for group in assignment for node in group) == sorted(catalog)
  assert max(map(len, assignment)) <= 48
  assert max(loads) <= 240
  assert sum(loads) == 258 * 5
  reverse, reverse_loads = shards.partition(list(reversed(catalog)), count)
  assert [set(group) for group in reverse] == [set(group) for group in assignment]
  assert reverse_loads == loads
