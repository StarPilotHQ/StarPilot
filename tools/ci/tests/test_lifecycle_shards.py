import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('private_lifecycle_shards', Path(__file__).parents[1] / 'lifecycle_shards.py')
assert spec is not None and spec.loader is not None
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class LifecycleShardTests(unittest.TestCase):
  def catalog(self):
    targets = ['a.py', 'b.py', 'c.py', 'd.py']
    identities = [f'{name}.Test.test_{i}' for name in 'abcd' for i in range(3)]
    return targets, identities

  def test_partition_preserves_whole_modules_and_all_cases(self):
    targets, identities = self.catalog()
    shards = helper.partition(targets, identities, 4)
    self.assertEqual(sorted(target for shard in shards for target in shard), targets)
    self.assertEqual([len(shard) for shard in shards], [1] * 4)
    self.assertEqual(shards, helper.partition(list(reversed(targets)), identities, 4))

  def test_invalid_catalogs_are_rejected(self):
    targets, identities = self.catalog()
    for bad_targets, bad_ids, count in [(targets, identities, 0), (targets + targets, identities, 4),
                                        (targets, identities + identities, 4), (targets, ['foreign.test'], 4),
                                        (targets + ['empty.py'], identities, 4)]:
      with self.subTest(targets=bad_targets, count=count), self.assertRaises(ValueError):
        helper.partition(bad_targets, bad_ids, count)

  def test_measured_native_callers_keep_whole_module_and_outweigh_case_count(self):
    module = 'opendbc_repo/opendbc/car/mazda/tests/test_native_engagement_callers.py'
    policy = helper.workload_policy()
    measured = policy['modules'][module]
    self.assertNotIn(module, policy['function_groups'])
    self.assertEqual(measured['cases'], 48)
    self.assertGreater(measured['seconds'], 56 * policy['unknown_seconds_per_case'])
    self.assertEqual(len(measured['source_sha256']), 64)
    self.assertEqual(measured['reference']['artifact_id'], 12520491388)
    prefix = module.removesuffix('.py').replace('/', '.') + '.'
    identities = [prefix + f'test_case[{i}]' for i in range(48)]
    identities += [f'other.test_case[{i}]' for i in range(56)]
    shards = helper.partition([module, 'other.py'], identities, 2)
    self.assertEqual(shards, [[module], ['other.py']])
    selected = [identity for shard in shards for identity in identities
                if any(helper.matches(identity, target) for target in shard)]
    self.assertCountEqual(selected, identities)

  def test_measured_pedal_callers_keep_complete_module(self):
    module = 'openpilot/starpilot/car/toyota/tests/test_pedal_parent_callers.py'
    policy = helper.workload_policy()
    measured = policy['modules'][module]
    self.assertNotIn(module, policy['function_groups'])
    self.assertEqual(measured['cases'], 92)
    self.assertGreater(measured['seconds'], 100 * policy['unknown_seconds_per_case'])
    self.assertEqual(len(measured['source_sha256']), 64)
    self.assertEqual(measured['reference']['artifact_id'], 12574163616)
    prefix = module.removesuffix('.py').replace('/', '.') + '.'
    identities = [prefix + f'test_case[{index}]' for index in range(92)]
    identities += [f'other.test_case[{index}]' for index in range(100)]
    targets = [module, 'other.py']
    shards = helper.partition(targets, identities, 2)
    self.assertEqual(shards, [[module], ['other.py']])
    self.assertCountEqual([target for shard in shards for target in shard], targets)
    selected = [identity for shard in shards for identity in identities
                if any(helper.matches(identity, target) for target in shard)]
    self.assertCountEqual(selected, identities)
    self.assertEqual(shards, helper.partition(list(reversed(targets)), list(reversed(identities)), 2))

  def test_measured_bluepilot_lifecycle_is_not_packed_as_unknown_work(self):
    module = 'openpilot/starpilot/tests/test_ford_bluepilot_library_lifecycle.py'
    policy = helper.workload_policy()
    measured = policy['modules'][module]
    self.assertNotIn(module, policy['function_groups'])
    self.assertEqual(measured['cases'], 169)
    self.assertGreater(measured['seconds'], 300 * policy['unknown_seconds_per_case'])
    self.assertEqual(len(measured['source_sha256']), 64)
    self.assertEqual(measured['reference']['artifact_id'], 13071624813)
    prefix = module.removesuffix('.py').replace('/', '.') + '.'
    identities = [prefix + f'test_case[{index}]' for index in range(169)]
    identities += [f'other.test_case[{index}]' for index in range(300)]
    shards = helper.partition([module, 'other.py'], identities, 2)
    self.assertEqual(shards, [[module], ['other.py']])
    selected = [identity for shard in shards for identity in identities
                if any(helper.matches(identity, target) for target in shard)]
    self.assertCountEqual(selected, identities)

  def test_only_large_reviewed_heavy_functions_split_into_exact_parameter_leaves(self):
    heavy = 'openpilot/starpilot/car/toyota/tests/test_legacy_advert_callers.py'
    prefix = heavy.removesuffix('.py').replace('/', '.') + '.'
    full = [prefix + f'test_nominal[{index}]' for index in range(126)]
    full += [prefix + f'test_veto[{index}]' for index in range(32)]
    full += ['other.Test.test_one', 'other.Test.test_two']
    targets = [heavy, 'other.py']
    shards = helper.partition(targets, full, 4)
    selectors = [target for shard in shards for target in shard]
    self.assertIn(heavy + '::test_veto', selectors)
    self.assertIn('other.py', selectors)
    self.assertEqual(len(selectors), 128)
    self.assertTrue(all(heavy + f'::test_nominal[{index}]' in selectors for index in range(126)))
    selected = [[identity for identity in full if any(helper.matches(identity, target) for target in shard)] for shard in shards]
    self.assertEqual(sorted(identity for shard in selected for identity in shard), sorted(full))
    self.assertEqual(shards, helper.partition(list(reversed(targets)), list(reversed(full)), 4))
    self.assertFalse(helper.matches(prefix + 'test_nominal_extra[1]', heavy + '::test_nominal'))
    self.assertFalse(helper.matches('foreign.test_nominal[1]', heavy + '::test_nominal'))
    self.assertTrue(helper.matches(prefix + 'test_nominal[1]', heavy + '::test_nominal[1]'))
    self.assertFalse(helper.matches(prefix + 'test_nominal[10]', heavy + '::test_nominal[1]'))
    self.assertTrue(helper.matches(prefix + 'test_nominal[::complex/parameter]', heavy + '::test_nominal[::complex/parameter]'))

  def test_current_ford_and_stock_location_cases_split_without_omission(self):
    for module, size in (('openpilot/starpilot/tests/test_ford_configuration_parity.py', 112),
                         ('openpilot/starpilot/aol/tests/test_stock_location_recovery.py', 135)):
      with self.subTest(module=module):
        prefix = module.removesuffix('.py').replace('/', '.') + '.'
        full = [prefix + f'test_actual_lifecycle[{index}]' for index in range(size)]
        full += [prefix + 'test_neighbor']
        shards = helper.partition([module], full, 8)
        selected = [[identity for identity in full if any(helper.matches(identity, target) for target in shard)]
                    for shard in shards]
        self.assertEqual(sorted(identity for shard in selected for identity in shard), sorted(full))
        self.assertEqual(len({identity for shard in selected for identity in shard}), len(full))
        self.assertTrue(all(len(shard) >= 1 for shard in selected))
        selectors = [target for shard in shards for target in shard]
        self.assertEqual(len(selectors), len(full))
        self.assertEqual(shards, helper.partition([module], list(reversed(full)), 8))

  def test_heavy_function_rule_never_splits_classes_or_accepts_forged_targets(self):
    heavy = 'openpilot/starpilot/car/toyota/tests/test_legacy_advert_callers.py'
    prefix = heavy.removesuffix('.py').replace('/', '.') + '.'
    for targets, ids in (([heavy], [prefix + 'TestClass.test_case']),
                         ([heavy + '::test_case'], [prefix + 'test_case']),
                         ([heavy], [prefix + 'test_case', prefix + 'test_case'])):
      with self.subTest(targets=targets), self.assertRaises(ValueError):
        helper.partition(targets, ids, 4)
    with self.assertRaises(ValueError):
      helper.partition(['other.py'], ['other.test_case'], 17)

  def test_future_declared_module_does_not_add_any_cases(self):
    targets, identities = self.catalog()
    shards = helper.partition(targets, identities, 8)
    self.assertEqual(sorted(target for shard in shards for target in shard), targets)
    camera = 'openpilot/starpilot/car/toyota/tests/test_camera_2aa_callers.py'
    self.assertFalse(any(camera in target for shard in shards for target in shard))

  def documents(self, suite="aol-lifecycle"):
    targets, full = self.catalog()
    partitioner = helper.release_partition if suite == "safety-release" else helper.partition
    matcher = helper.release_matches if suite == "safety-release" else helper.matches
    partition = partitioner(targets, full, 4)
    source: dict = dict.fromkeys(('revision', 'inputs', 'manifest_sha256', 'dependencies', 'runner_sha256',
                                    'suite_runner_sha256', 'pytest_runner_sha256', 'shard_runner_sha256'), 'same')
    inventory_bytes = Path(__file__).parents[1].joinpath('lifecycle_build_outputs.json').read_bytes()
    inventory = json.loads(inventory_bytes)
    source['build_identity'] = {'inputs': {'revision': 'same', 'tree': 'same', 'tracked_inputs': {'source.c': {'sha256': 'c' * 64, 'mode': 420}},
                                           'changes': {}, 'untracked': {}, 'recipes': {'SConstruct': {'sha256': 'd' * 64, 'mode': 420}},
                                           'output_inventory_sha256': hashlib.sha256(inventory_bytes).hexdigest()},
                                'declared_outputs': {name: {'sha256': 'a' * 64, 'mode': 420} for name in inventory}}
    can_sources = dict.fromkeys(('opendbc_repo/opendbc/can/packer.py', 'opendbc_repo/opendbc/can/parser.py'), 'e' * 64)
    source['build_identity']['inputs']['tracked_inputs'].update({name: {'sha256': checksum, 'mode': 420}
                                                               for name, checksum in can_sources.items()})
    docs = []
    for index, assigned in enumerate(partition):
      ids = [identity for identity in full if any(matcher(identity, target) for target in assigned)]
      doc = {'suite': suite, 'status': 'completed', 'exit_code': 0, 'shard': {'index': index, 'count': 4},
             'targets': assigned, 'selected_test_ids': ids, 'full_catalog_targets': targets, 'full_catalog_test_ids': full,
             'collection_errors': [], 'gate_errors': [], 'source': source, 'source_after': source,
             'native': {'can_sources': can_sources, 'pycapnp': {'sha256': 'b' * 64}, 'safety': {'sha256': '1' * 64,
                        'variant': 'release', 'all_output_hook_available': False}, 'aol_protocol': {'sha256': '2' * 64}}}
      result = {'selected_test_ids': ids, 'exit_code': 0, 'collection_errors': [], 'gate_errors': [],
                'results': [{'id': identity, 'status': 'passed'} for identity in ids]}
      docs.append((doc, result))
    return docs

  def check(self, docs, artifact_mutation=None):
    with tempfile.TemporaryDirectory() as directory:
      for index, (doc, result) in enumerate(docs):
        path = Path(directory) / str(index)
        path.mkdir()
        digest = hashlib.sha256(json.dumps(doc['source']['build_identity']['inputs'], sort_keys=True).encode()).hexdigest()
        for kind in ('safety', 'aol_protocol'):
          native = doc['native'][kind]
          name = 'libsafety-debug.so' if kind == 'safety' else 'test_aol_protocol'
          artifact = path / name
          artifact.write_bytes(kind.encode())
          native.update(path=str(artifact), sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                        build_inputs_sha256=digest, commands=[['cc', 'actual.c']] if kind == 'safety' else None,
                        command=['c++', 'actual.cc'])
        (path / 'results.json').write_text(json.dumps(result))
        doc['result_sha256'] = hashlib.sha256((path / 'results.json').read_bytes()).hexdigest()
        (path / 'coverage.json').write_text(json.dumps(doc))
      if artifact_mutation is not None:
        artifact_mutation(Path(directory))
      return helper.aggregate(directory, 4, docs[0][0]["suite"])

  def test_release_partition_accepts_real_class_selectors_and_import_aliases(self):
    targets = ['opendbc_repo/opendbc/safety/tests/a.py::TestRelease', 'b.py::test_case[one]', 'c.py']
    ids = ['opendbc.safety.tests.a.TestRelease.test_denied', 'b.test_case[one]', 'c.Test.test_one']
    shards = helper.release_partition(targets, ids, 4)
    self.assertCountEqual([target for shard in shards for target in shard], targets)
    self.assertEqual(sum(sum(any(helper.release_matches(identity, target) for target in shard) for identity in ids) for shard in shards), len(ids))
    for bad_targets, bad_ids in ((targets, ids + ids), (targets + ['c.py::Test'], ids), (targets, ids + ['foreign.test']),
                                (targets + ['empty.py'], ids), (['../escape.py'], ['escape.test'])):
      with self.subTest(targets=bad_targets), self.assertRaises(ValueError):
        helper.release_partition(bad_targets, bad_ids, 4)

  def test_release_aggregate_retains_all_outcome_and_native_guards(self):
    docs = self.documents('safety-release')
    self.assertEqual(self.check(copy.deepcopy(docs))['cases_passed'], 12)
    for change in ('missing', 'duplicate', 'debug', 'allOutput', 'wrong_suite'):
      bad = json.loads(json.dumps(docs))
      if change == 'missing':
        bad[0][1]['results'].pop()
      elif change == 'duplicate':
        bad[0][1]['results'].append(bad[0][1]['results'][0])
      elif change == 'skip':
        bad[0][1]['results'][0]['status'] = 'skipped'
      elif change == 'debug':
        bad[0][0]['native']['safety']['variant'] = 'ALLOW_DEBUG'
      elif change == 'allOutput':
        bad[0][0]['native']['safety']['all_output_hook_available'] = True
      else:
        bad[0][0]['suite'] = 'interfaces'
      with self.subTest(change=change), self.assertRaises((ValueError, KeyError, StopIteration)):
        self.check(bad)

  def test_release_preserves_existing_skips_without_counting_them_as_passed(self):
    docs = self.documents('safety-release')
    docs[0][1]['results'][0].update(status='skipped', detail='DEBUG classic Bosch owner')
    verdict = self.check(copy.deepcopy(docs))
    self.assertEqual(verdict['cases_passed'], 11)
    self.assertEqual(verdict['cases_skipped'], 1)
    self.assertEqual(verdict['cases_total'], 12)
    for status in ('failed', 'error', 'xfailed', 'xpassed', 'not_run', 'timed_out'):
      bad = copy.deepcopy(docs)
      bad[0][1]['results'][0]['status'] = status
      with self.subTest(status=status), self.assertRaises(ValueError):
        self.check(bad)
    for doc, _ in docs:
      doc['suite'] = 'aol-lifecycle'
    with self.assertRaises(ValueError):
      self.check(docs)

  def test_complete_catalog_passes(self):
    self.assertEqual(self.check(self.documents())['cases_passed'], 12)

  def test_parameter_leaf_aggregate_requires_exact_complete_passed_outcomes(self):
    heavy = 'openpilot/starpilot/car/toyota/tests/test_legacy_advert_callers.py'
    prefix = heavy.removesuffix('.py').replace('/', '.') + '.'
    full = [prefix + f'test_nominal[{index}]' for index in range(126)] + ['other.Test.test_case']
    with patch.object(self, 'catalog', return_value=([heavy, 'other.py'], full)):
      docs = self.documents()
    self.assertEqual(self.check(copy.deepcopy(docs))['cases_passed'], 127)
    for change in ('missing', 'duplicate', 'skip', 'forged_catalog', 'forged_leaf'):
      bad = json.loads(json.dumps(docs))
      if change == 'missing':
        bad[0][1]['results'].pop()
      elif change == 'duplicate':
        bad[0][1]['results'].append(bad[0][1]['results'][0])
      elif change == 'skip':
        bad[0][1]['results'][0]['status'] = 'skipped'
      elif change == 'forged_catalog':
        bad[0][0]['full_catalog_test_ids'].pop()
      else:
        bad[0][0]['targets'][0] += '-forged'
      with self.subTest(change=change), self.assertRaises((ValueError, KeyError, StopIteration)):
        self.check(bad)

  def test_missing_modified_or_forged_native_artifacts_are_denied(self):
    def remove(root):
      (root / '0/libsafety-debug.so').unlink()
    def modify(root):
      (root / '0/test_aol_protocol').write_bytes(b'foreign binary')
    def forge(root):
      path = root / '0/coverage.json'
      data = json.loads(path.read_text())
      data['native']['safety']['build_inputs_sha256'] = 'f' * 64
      path.write_text(json.dumps(data))
    for mutate in (remove, modify, forge):
      with self.subTest(mutation=mutate.__name__), self.assertRaises(ValueError):
        self.check(self.documents(), mutate)

  def test_bad_outcomes_and_provenance_are_denied(self):
    mutations = [lambda d: d.pop(),
                 lambda d: d[0][0]['shard'].update(index=1),
                 lambda d: d[0][0].update(exit_code=1),
                 lambda d: d[0][0].update(gate_errors=['failed gate']),
                 lambda d: d[0][0]['full_catalog_test_ids'].pop(),
                 lambda d: d[0][0].update(targets=['foreign.py']),
                 lambda d: d[0][0]['source'].update(revision='foreign'),
                 lambda d: d[0][0]['source_after'].update(inputs='changed'),
                 lambda d: d[0][0]['source']['build_identity']['declared_outputs'].clear(),
                 lambda d: d[0][0]['native']['can_sources'].clear(),
                 lambda d: d[0][0]['native']['pycapnp'].update(sha256='f' * 64),
                 lambda d: d[0][1]['results'].pop(),
                 lambda d: d[0][1]['results'].append(d[0][1]['results'][0]),
                 lambda d: d[0][1]['results'][0].update(status='skipped'),
                 lambda d: d[0][1]['results'][0].update(status='failed'),
                 lambda d: d[0][1].update(exit_code=1)]
    for index, mutate in enumerate(mutations):
      docs = copy.deepcopy(self.documents())
      # Break intentional shared catalog/source aliases to exercise genuine cross-shard disagreement.
      docs = json.loads(json.dumps(docs))
      mutate(docs)
      with self.subTest(index=index), self.assertRaises((ValueError, KeyError, StopIteration)):
        self.check(docs)


if __name__ == '__main__':
  unittest.main()
