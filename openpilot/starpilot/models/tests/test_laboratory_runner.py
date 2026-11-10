import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from openpilot.cereal import log
from openpilot.starpilot.models.catalog import ARTIFACT_ABI, CATALOG_PATH, COMPILER_REVISION
from openpilot.starpilot.models.laboratory_runner import LaboratoryPair, load_laboratory_pair
from openpilot.starpilot.models.manager import artifact_path, atomic_json
from openpilot.starpilot.models import laboratory
from openpilot.starpilot.models.receipt import PreparedArtifact
from openpilot.starpilot.models.tests.test_shared_camera_warp import frame_inputs, make_runner


def make_pair(versions=("v14", "v15")):
  models = [make_runner(), make_runner()]
  for model, mid, version in zip(models, ("lateral", "longitudinal"), versions, strict=True):
    model.model_id, model.behavior_version, model.chestnut = mid, version, True
    model._parse = Mock(return_value={
      **model._parse([model.run_policy.return_value[0].numpy()]),
      "plan": np.full((1, 33, 15), 1. if mid == "lateral" else 2.),
      "pose": np.full((1, 6), 3. if mid == "lateral" else 4.),
      "action": np.array([[4., .5 if mid == "longitudinal" else 1.]]),
    })
  prepared = PreparedArtifact((0, 0, 1, 0, 0), "a" * 64)
  return LaboratoryPair(models[0], models[1], prepared, prepared)


class TestLaboratoryFrame(unittest.TestCase):
  @staticmethod
  def run_frame(pair, **kwargs):
    return pair.run_frame(*frame_inputs(pair.lateral), log.ModelDataV2.Action(), .2, .2, 20.,
                          long_smooth_seconds=0., **kwargs)

  def test_sequential_frame_one_warp_independent_actions_and_current_frame_pose(self):
    pair = make_pair()
    callback = Mock()
    frame = self.run_frame(pair, after_enqueue=callback)
    pair.lateral.warp_enqueue.assert_called_once()
    pair.longitudinal.warp_enqueue.assert_not_called()
    callback.assert_called_once_with()
    self.assertIs(pair.longitudinal.run_policy.call_args.kwargs["warped"], pair.lateral.last_warp.tensor)
    self.assertAlmostEqual(frame.action.desiredCurvature, .04)
    self.assertAlmostEqual(frame.action.desiredAcceleration, .5)
    self.assertFalse(frame.action.shouldStop)
    np.testing.assert_array_equal(frame.outputs["plan"][0, 0], [2, 1, 2, 2, 1, 2, 2, 1, 2, 2, 2, 1, 2, 2, 1])
    np.testing.assert_array_equal(frame.outputs["pose"], pair.longitudinal._parse.return_value["pose"])
    self.assertNotIn("action", frame.outputs)
    frame.outputs["plan"][:] = 9
    self.assertEqual(pair.lateral._parse.return_value["plan"][0, 0, 1], 1)

  def test_enqueue_both_before_readback_and_each_ticket_is_single_use(self):
    pair = make_pair()
    events = []
    for role in ('lateral', 'longitudinal'):
      model = getattr(pair, role)
      for name in ('enqueue', 'finish'):
        original = getattr(model, name)
        def measured(*args, original=original, label=f'{role}.{name}', **kwargs):
          events.append(label)
          return original(*args, **kwargs)
        setattr(model, name, measured)
    self.run_frame(pair)
    self.assertEqual(events, ['lateral.enqueue', 'longitudinal.enqueue', 'lateral.finish', 'longitudinal.finish'])
    args = frame_inputs(pair.lateral)
    ticket = pair.lateral.enqueue(*args)
    with self.assertRaisesRegex(RuntimeError, 'Finish'):
      pair.lateral.enqueue(*args)
    with self.assertRaisesRegex(ValueError, 'different'):
      pair.lateral.finish((ticket[0], ticket[1]))
    pair.lateral.finish(ticket)
    with self.assertRaisesRegex(ValueError, 'completed'):
      pair.lateral.finish(ticket)

  def test_qcom_role_runs_after_amd_enqueue_without_swapping_axis_ownership(self):
    pair = make_pair()
    pair.lateral.chestnut = False
    frame = self.run_frame(pair)
    pair.longitudinal.warp_enqueue.assert_called_once()
    pair.lateral.warp_enqueue.assert_not_called()
    self.assertAlmostEqual(frame.action.desiredCurvature, .04)
    self.assertAlmostEqual(frame.action.desiredAcceleration, .5)
    self.assertIs(pair.lateral.run_policy.call_args.kwargs['warped'], pair.longitudinal.last_warp.tensor)

  def test_mixed_generations_decode_before_combining_axes(self):
    pair = make_pair(("v15", "v14"))
    self.assertAlmostEqual(self.run_frame(pair).action.desiredCurvature, .01)
    pair = make_pair(("v9", "v15"))
    pair.lateral._parse.return_value.pop("desired_curvature")
    previous = log.ModelDataV2.Action(desiredCurvature=.03, desiredAcceleration=.8)
    frame = pair.run_frame(*frame_inputs(pair.lateral), previous, .2, .2, .2, long_smooth_seconds=0.)
    self.assertAlmostEqual(frame.action.desiredCurvature, .03)
    self.assertAlmostEqual(frame.action.desiredAcceleration, .5)
    self.assertFalse(frame.action.shouldStop)
    pair.longitudinal._parse.return_value["action"][0, 1] = -.5
    frame = pair.run_frame(*frame_inputs(pair.lateral), previous, .2, .2, .2, long_smooth_seconds=0.)
    self.assertTrue(frame.action.shouldStop)

  def test_each_role_failure_latches_without_publishing_a_partial_frame(self):
    for role in ("lateral", "longitudinal"):
      with self.subTest(role=role):
        pair = make_pair()
        model = getattr(pair, role)
        model.run_policy.side_effect = RuntimeError("execution failed")
        with self.assertRaisesRegex(RuntimeError, "execution failed"):
          self.run_frame(pair)
        self.assertTrue(pair.failed)
        self.assertIsNone(pair.lateral.last_warp)
        self.assertIsNone(pair.longitudinal.last_warp)
        if role == "lateral":
          pair.longitudinal.run_policy.assert_not_called()
        model.run_policy.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "fallback"):
          self.run_frame(pair)

  def test_action_and_output_failures_are_inside_the_same_failure_boundary(self):
    for key, invalid in (("plan", np.zeros((1, 32, 15))), ("action", np.zeros((1, 3))),
                         ("pose", np.full((1, 6), np.nan))):
      with self.subTest(key=key):
        pair = make_pair()
        pair.longitudinal._parse.return_value[key] = invalid
        with self.assertRaises(ValueError):
          self.run_frame(pair)
        self.assertTrue(pair.failed)
        with self.assertRaisesRegex(RuntimeError, "fallback"):
          self.run_frame(pair)

  def test_absent_warp_cannot_silently_run_second_preprocessor(self):
    pair = make_pair()
    pair.lateral.enqueue = Mock(return_value=pair.lateral._parse.return_value)
    with self.assertRaisesRegex(ValueError, "warp unavailable"):
      self.run_frame(pair)
    pair.longitudinal.run_policy.assert_not_called()
    self.assertTrue(pair.failed)

  def test_different_preprocessors_run_independently(self):
    pair = make_pair()
    pair.longitudinal.camera_warp_descriptor = None
    pair = LaboratoryPair(pair.lateral, pair.longitudinal, pair.lateral_role.artifact, pair.longitudinal_role.artifact)
    self.run_frame(pair)
    pair.lateral.warp_enqueue.assert_called_once()
    pair.longitudinal.warp_enqueue.assert_called_once()

  def test_pair_admission_rejects_same_runner_model_wrong_hardware_and_history(self):
    pair = make_pair()
    prepared = pair.lateral_role.artifact
    with self.assertRaisesRegex(ValueError, "distinct"):
      LaboratoryPair(pair.lateral, pair.lateral, prepared, prepared)
    for key, value, message in (("model_id", "lateral", "distinct"), ("chestnut", False, "AMD")):
      with self.subTest(key=key):
        pair = make_pair()
        setattr(pair.longitudinal, key, value)
        if key == "chestnut":
          pair.lateral.chestnut = False
        with self.assertRaisesRegex(ValueError, message):
          LaboratoryPair(pair.lateral, pair.longitudinal, prepared, prepared)


class TestLaboratoryLoad(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.root = Path(directory.name)
    self.config = {"enabled": True, "lateralModel": "gwm8223", "longitudinalModel": "sc23"}
    manifest = json.loads(CATALOG_PATH.read_text())
    self.rows = {}
    for mid in ("gwm8223", "sc23"):
      content = f"verified AMD {mid}".encode()
      row = next(row for row in manifest["models"] if row["id"] == mid)
      row["accelerator_artifacts"] = {"amd": {
        "artifact_format": ARTIFACT_ABI, "execution_device": "AMD", "compiler_revision": COMPILER_REVISION,
        "artifact_sha256": hashlib.sha256(content).hexdigest(), "artifact_size": len(content), "artifact_chunk_count": 0,
      }}
      self.rows[mid] = row
    self.manifest = manifest
    atomic_json(self.root / "catalog.json", manifest)
    for mid in self.rows:
      path = artifact_path(mid, self.root, "amd")
      path.parent.mkdir(parents=True)
      path.write_bytes(f"verified AMD {mid}".encode())
    atomic_json(self.root / 'laboratory.json', self.config)
    self.addCleanup(patch.stopall)
    patch('openpilot.starpilot.models.laboratory.RUNTIME_QUALIFIED', True).start()
    patch('openpilot.starpilot.models.runtime_bridge.runtime.frozen_laboratory_request',
          side_effect=lambda: laboratory.freeze_request(self.root)).start()

  def factory(self, width, height, path, version, chestnut):
    self.assertEqual((width, height), (8, 8))
    self.assertTrue(chestnut)
    self.assertIn("_amd_", path.name)
    model = make_runner()
    model.chestnut, model.behavior_version = chestnut, version
    model.warmup = Mock()
    return model

  def test_separate_verified_loads_preserve_both_identities_and_warmup(self):
    with patch("openpilot.starpilot.models.runner.CatalogModelState", side_effect=self.factory) as loader:
      pair = load_laboratory_pair(8, 8, self.config, root=self.root)
    self.assertEqual(loader.call_count, 2)
    self.assertIsNot(pair.lateral, pair.longitudinal)
    for mid, model, role in (("gwm8223", pair.lateral, pair.lateral_role), ("sc23", pair.longitudinal, pair.longitudinal_role)):
      self.assertEqual(role.model_id, mid)
      self.assertEqual(role.behavior_version, self.rows[mid]["version"])
      self.assertEqual(role.artifact.sha256, self.rows[mid]["accelerator_artifacts"]["amd"]["artifact_sha256"])
      model.warmup.assert_called_once_with()

  def test_small_lateral_big_longitudinal_keeps_role_artifacts_distinct(self):
    row = next(row for row in self.manifest["models"] if row["id"] == "cinquev3")
    content = b"verified normal Big artifact"
    row.update(artifact_sha256=hashlib.sha256(content).hexdigest(), artifact_size=len(content), artifact_chunk_count=0)
    atomic_json(self.root / "catalog.json", self.manifest)
    big_path = artifact_path("cinquev3", self.root)
    big_path.parent.mkdir(parents=True)
    big_path.write_bytes(content)
    def factory(width, height, path, version, chestnut):
      model = make_runner()
      model.chestnut, model.behavior_version = chestnut, version
      model.warmup = Mock()
      return model
    config = self.config | {"longitudinalModel": "cinquev3"}
    atomic_json(self.root / "laboratory.json", config)
    with patch("openpilot.starpilot.models.runner.CatalogModelState", side_effect=factory) as loader:
      pair = load_laboratory_pair(8, 8, config, root=self.root)
    self.assertEqual(loader.call_args_list[0].args[2], artifact_path("gwm8223", self.root, "amd"))
    self.assertEqual(loader.call_args_list[1].args[2], big_path)
    self.assertEqual(pair.lateral_role.model_id, "gwm8223")
    self.assertEqual(pair.longitudinal_role.model_id, "cinquev3")
    self.assertEqual(pair.longitudinal_role.artifact.sha256, row["artifact_sha256"])

  def test_existing_native_small_is_used_without_changing_the_other_gpu_role(self):
    content = b'verified native Small'
    self.rows['gwm8223'].update(artifact_sha256=hashlib.sha256(content).hexdigest(), artifact_size=len(content), artifact_chunk_count=0)
    atomic_json(self.root / 'catalog.json', self.manifest)
    native = artifact_path('gwm8223', self.root)
    native.write_bytes(content)
    def factory(width, height, path, version, chestnut):
      model = make_runner()
      model.chestnut, model.behavior_version = chestnut, version
      model.warmup = Mock()
      return model
    with patch('openpilot.starpilot.models.runner.CatalogModelState', side_effect=factory) as loader:
      pair = load_laboratory_pair(8, 8, self.config, root=self.root)
    self.assertEqual(loader.call_args_list[0].args[2], native)
    self.assertFalse(pair.lateral.chestnut)
    self.assertTrue(pair.longitudinal.chestnut)
    self.assertTrue(pair.chestnut)

  def test_native_only_new_model_keeps_the_other_role_on_chestnut(self):
    content = b'verified new Small'
    self.manifest['models'].append({'id': 'new-small', 'name': 'New Small', 'version': 'v16',
      'artifact_format': ARTIFACT_ABI, 'artifact_sha256': hashlib.sha256(content).hexdigest(),
      'artifact_size': len(content), 'artifact_chunk_count': 0})
    atomic_json(self.root / 'catalog.json', self.manifest)
    path = artifact_path('new-small', self.root)
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    config = self.config | {'longitudinalModel': 'new-small'}
    atomic_json(self.root / 'laboratory.json', config)
    request = laboratory.freeze_request(self.root)
    assert request is not None
    self.assertTrue(request['roles']['lateralModel']['chestnut'])
    self.assertFalse(request['roles']['longitudinalModel']['chestnut'])
    self.assertEqual(request['roles']['longitudinalModel']['path'], str(path))
    self.manifest['models'].append({**self.manifest['models'][-1], 'id': 'new-small-two', 'name': 'Second'})
    atomic_json(self.root / 'catalog.json', self.manifest)
    with self.assertRaisesRegex(ValueError, 'at least one.*Chestnut'):
      laboratory.validate_configuration(config | {'lateralModel': 'new-small-two'}, self.root)

  def test_big_pair_is_refused_before_either_artifact_load(self):
    config = {'enabled': True, 'lateralModel': 'cinquev3', 'longitudinalModel': 'resfit'}
    with patch('openpilot.starpilot.models.runner.CatalogModelState') as loader:
      with self.assertRaisesRegex(ValueError, 'Two Big models cannot'):
        load_laboratory_pair(8, 8, config, root=self.root)
      loader.assert_not_called()

  def test_both_variants_must_be_declared_before_either_load(self):
    del self.rows["sc23"]["accelerator_artifacts"]
    atomic_json(self.root / "catalog.json", self.manifest)
    with patch("openpilot.starpilot.models.runner.CatalogModelState") as loader:
      with self.assertRaisesRegex(ValueError, "missing or corrupt"):
        load_laboratory_pair(8, 8, self.config, root=self.root)
      loader.assert_not_called()

  def test_changed_second_artifact_cannot_return_a_half_loaded_pair(self):
    artifact_path("sc23", self.root, "amd").write_bytes(b"changed bytes")
    with patch("openpilot.starpilot.models.runner.CatalogModelState", side_effect=self.factory) as loader:
      with self.assertRaisesRegex(ValueError, "missing or corrupt"):
        load_laboratory_pair(8, 8, self.config, root=self.root)
      self.assertEqual(loader.call_count, 0)

  def test_disabled_duplicate_and_big_requests_cannot_load(self):
    for config in ({**self.config, "enabled": False}, {**self.config, "longitudinalModel": "gwm8223"},
                   {**self.config, "lateralModel": "cinquev3"}):
      with self.subTest(config=config), patch("openpilot.starpilot.models.runner.CatalogModelState") as loader:
        with self.assertRaises(ValueError):
          load_laboratory_pair(8, 8, config, root=self.root)
        loader.assert_not_called()
