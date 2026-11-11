from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from tinygrad import Context, Device

from openpilot.cereal import log
from openpilot.starpilot.models.runner import (ARTIFACT_ABI, COMPILER_REVISION, CatalogModelState,
                                              action_from_outputs, load_verified_model, validate_artifact)
from openpilot.starpilot.models.parser import Parser
from openpilot.selfdrive.modeld.fill_model_msg import PublishState, fill_model_msg, fill_driving_model_data, fill_pose_msg


def output_fixture(mixture=False):
  lengths = {"lane_lines": 528, "lane_lines_prob": 8, "road_edges": 264, "meta": 55, "desire_pred": 32,
             "pose": 12, "wide_from_device_euler": 6, "road_transform": 12,
             "plan": 4955 if mixture else 990, "lead": 102 if mixture else 144,
             "lead_prob": 3, "desire_state": 8, "action": 4, "desired_curvature": 2}
  sections, end = {}, 0
  for name, width in lengths.items():
    sections[name] = slice(end, end + width)
    end += width
  return np.zeros(end, dtype=np.float32), sections


def artifact_fixture():
  outputs, sections = output_fixture()
  shapes = {"img": (1, 12, 2, 2), "big_img": (1, 12, 2, 2), "features_buffer": (1, 2, 4),
            "desire_pulse": (1, 3, 8), "traffic_convention": (1, 2), "prev_action": (1, 2),
            "lateral_control_params": (1, 2)}
  return {"artifact_abi": ARTIFACT_ABI, "compiler_revision": COMPILER_REVISION, "format_version": 1,
          "behavior_version": "v15", "model_type": "supercombo", "image_history_pipeline": "policy",
          "frame_skip": 4, "execution_device": "CPU", "warp_device": "CPU",
          "warp_input_keys": ("tfm", "big_tfm"),
          "policy_input_keys": ("img_q", "big_img_q", "feat_q", "desire_q", "packed_npy_inputs"),
          "metadata": {"model": {"input_shapes": shapes, "output_shapes": {"outputs": (1, len(outputs))}, "output_slices": sections}},
          "run_policy": Mock(return_value=(Mock(numpy=lambda: outputs.copy()),)), (8, 8): Mock(return_value="warped")}


class TestCatalogRunner(unittest.TestCase):
  def test_warmup_uses_declared_device_without_autoselection(self):
    runner = CatalogModelState.__new__(CatalogModelState)
    runner.frame_buf_size = 256
    runner.warp_device = "NPY"
    runner._blob_cache = {}
    runner._reset_state = Mock()

    def verify_frames(frames, transforms, inputs):
      for name, frame in frames.items():
        tensor = runner._blob_cache[name, frame.ctypes.data]
        self.assertEqual(tensor.device, "NPY")
        self.assertEqual(tensor.shape, (256,))
        np.testing.assert_array_equal(tensor.numpy(), np.zeros(256, dtype=np.uint8))
        np.testing.assert_array_equal(transforms[name], np.eye(3, dtype=np.float32))
      self.assertEqual(set(frames), {"img", "big_img"})
      np.testing.assert_array_equal(inputs["desire_pulse"], np.zeros(8, dtype=np.float32))

    def unexpected_default(_):
      raise AssertionError("warmup attempted device auto-selection")

    runner.run = Mock(side_effect=verify_frames)
    with Context(DEV=""), patch.object(type(Device), "_select_device", property(unexpected_default)):
      runner.warmup()
    runner.run.assert_called_once()
    runner._reset_state.assert_called_once()

  def test_artifact_identity_camera_version_and_output_contract(self):
    artifact = artifact_fixture()
    validate_artifact(artifact, "v15", (8, 8))
    for field, value in (("artifact_abi", "tinygrad_single_v1"), ("compiler_revision", "e" * 40),
                         ("behavior_version", "v14"), ("frame_skip", 0), ("image_history_pipeline", "guess")):
      with self.subTest(field=field), self.assertRaises(ValueError):
        validate_artifact({**artifact, field: value}, "v15", (8, 8))
    with self.assertRaises(ValueError):
      validate_artifact(artifact, "v15", (1928, 1208))
    changed = deepcopy(artifact)
    changed["metadata"]["model"]["output_slices"]["plan"] = slice(0, 1_000_000)
    with self.assertRaises(ValueError):
      validate_artifact(changed, "v15", (8, 8))

  def test_both_plan_and_lead_generations_normalize_for_current_messages(self):
    for mixture in (False, True):
      raw, sections = output_fixture(mixture)
      parsed = Parser().parse_outputs(CatalogModelState.slice_outputs(raw, sections))
      self.assertEqual(parsed["plan"].shape, (1, 33, 15))
      self.assertEqual(parsed["plan_stds"].shape, (1, 33, 15))
      self.assertEqual(parsed["lead"].shape, (1, 3, 6, 4))
      self.assertEqual(parsed["action"].shape, (1, 2))
      self.assertEqual(parsed["lane_lines"].shape, (1, 4, 33, 2))
      self.assertTrue(all(np.isfinite(value).all() for value in parsed.values()))
      model_msg = log.Event.new_message()
      model_msg.init("modelV2")
      driving_msg = log.Event.new_message()
      driving_msg.init("drivingModelData")
      pose_msg = log.Event.new_message()
      pose_msg.init("cameraOdometry")
      fill_model_msg(model_msg, parsed, log.ModelDataV2.Action(), PublishState(), 10, 10, 10, 0., 123, .02, True)
      fill_driving_model_data(driving_msg, model_msg)
      fill_pose_msg(pose_msg, parsed, 10, 0, 123, True)
      self.assertEqual(driving_msg.drivingModelData.frameId, 10)
      self.assertEqual(len(model_msg.modelV2.leadsV3), 3)
      self.assertTrue(pose_msg.valid)

  def test_missing_desire_state_uses_original_publisher_fallback(self):
    artifact = artifact_fixture()
    artifact["metadata"]["model"]["output_slices"].pop("desire_state")
    validate_artifact(artifact, "v15", (8, 8))
    raw, sections = output_fixture()
    sections.pop("desire_state")
    parsed = Parser().parse_outputs(CatalogModelState.slice_outputs(raw, sections))
    self.assertNotIn("desire_state", parsed)
    event = log.Event.new_message()
    event.init("modelV2")
    fill_model_msg(event, parsed, log.ModelDataV2.Action(), PublishState(), 10, 10, 10, 0., 123, .02, True)
    self.assertEqual(list(event.modelV2.meta.desireState), [0.0] * 8)

    parsed["desire_state"] = np.arange(8, dtype=np.float32)[np.newaxis, :]
    event = log.Event.new_message()
    event.init("modelV2")
    fill_model_msg(event, parsed, log.ModelDataV2.Action(), PublishState(), 10, 10, 10, 0., 123, .02, True)
    self.assertEqual(list(event.modelV2.meta.desireState), list(range(8)))

    for missing in ("plan", "lead", "pose", "desire_pred", "meta"):
      altered = deepcopy(artifact)
      altered["metadata"]["model"]["output_slices"].pop(missing)
      with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "missing required driving outputs"):
        validate_artifact(altered, "v15", (8, 8))

  def test_overlapping_slices_are_isolated_before_parser_mutation(self):
    raw = np.arange(8, dtype=np.float32)
    sections = CatalogModelState.slice_outputs(raw, {"state": slice(0, 8), "pad": slice(0, 8)})
    sections["state"][:] = 0
    np.testing.assert_array_equal(sections["pad"], [np.arange(8)])
    np.testing.assert_array_equal(raw, np.arange(8))

  def test_gwm_padding_aliases_use_declared_output_width(self):
    artifact = artifact_fixture()
    artifact.update(model_type="vision_policy", behavior_version="v11", policy_order=["policy"])
    artifact["metadata"] = {
      "vision": {"input_shapes": {"img": (1, 12, 128, 256), "big_img": (1, 12, 128, 256)},
                 "output_shapes": {"outputs": (1, 1576)}, "output_slices": {
                   "meta": slice(0, 55), "desire_pred": slice(55, 87), "pose": slice(87, 99),
                   "wide_from_device_euler": slice(99, 105), "road_transform": slice(105, 117),
                   "lane_lines": slice(117, 645), "lane_lines_prob": slice(645, 653), "road_edges": slice(653, 917),
                   "lead": slice(917, 1061), "lead_prob": slice(1061, 1064), "hidden_state": slice(1064, 1576),
                   "pad": slice(0, None)}},
      "policy": {"input_shapes": {"features_buffer": (1, 25, 512)}, "output_shapes": {"outputs": (1, 1000)},
                 "output_slices": {"plan": slice(0, 990), "desire_state": slice(990, 998), "pad": slice(-2, None)}},
    }
    validate_artifact(artifact, "v11", (8, 8))
    slices = artifact["metadata"]["policy"]["output_slices"]
    np.testing.assert_array_equal(CatalogModelState.slice_outputs(np.arange(1000), slices)["pad"], [[998, 999]])
    for invalid in (slice(-1001, None), slice(0, 1001), slice(None, -1001), slice(10, 9), slice(0, None, -1)):
      with self.subTest(invalid=invalid), self.assertRaises(ValueError):
        altered = deepcopy(artifact)
        altered["metadata"]["policy"]["output_slices"]["pad"] = invalid
        validate_artifact(altered, "v11", (8, 8))
    for shape in (None, (), (1, 0), (1, "1000"), (2, 1000)):
      with self.subTest(shape=shape), self.assertRaisesRegex(ValueError, "width unavailable"):
        altered = deepcopy(artifact)
        altered["metadata"]["policy"]["output_shapes"]["outputs"] = shape
        validate_artifact(altered, "v11", (8, 8))

  def test_split_primary_policy_wins_over_auxiliary(self):
    runner = CatalogModelState.__new__(CatalogModelState)
    runner.model_type = "vision_multi_policy"
    runner.policy_order = ["on_policy", "off_policy"]
    runner.metadata = {key: {"output_slices": {"plan": slice(0, 1)}} for key in ("vision", "on_policy", "off_policy")}
    runner.parser = Mock()
    runner.parser.parse_vision_outputs.return_value = {"pose": np.array([1])}
    runner.parser.parse_policy_outputs.return_value = {"plan": np.array([2])}
    runner.aux_parser = Mock()
    runner.aux_parser.parse_off_policy_outputs.return_value = {"plan": np.array([3]), "aux": np.array([4])}
    parsed = runner._parse([np.zeros(1)] * 3)
    np.testing.assert_array_equal(parsed["plan"], [2])
    np.testing.assert_array_equal(parsed["pose"], [1])
    np.testing.assert_array_equal(parsed["aux"], [4])

  def test_low_speed_action_matches_modeld(self):
    previous = log.ModelDataV2.Action(desiredCurvature=.01, desiredAcceleration=.1)
    runner = CatalogModelState.__new__(CatalogModelState)
    for version in ("v15", "v16"):
      runner.behavior_version = version
      for speed in (0., .3, .31, 1., 1.5, 2., 2.49, 2.5, 3., 20.):
        for lateral_accel in (-.25, .25):
          with self.subTest(version=version, speed=speed, lateral_accel=lateral_accel):
            outputs = {"action": np.array([[lateral_accel, .5]])}
            expected = lateral_accel / max(2.5, speed) ** 2 if speed > .3 else .01
            action = runner.get_action_from_model(outputs, previous, .2, .2, speed)
            self.assertAlmostEqual(action.desiredCurvature, expected)
            self.assertAlmostEqual(action.desiredAcceleration, .1 + (1 - np.exp(-.05 / .3)) * .4)
            self.assertFalse(action.shouldStop)
            smoothed = action_from_outputs(outputs, version, previous, .2, .2, speed, .2, .3)
            expected_smoothed = .01 + (1 - np.exp(-.05 / .2)) * (expected - .01) if speed > .3 else .01
            self.assertAlmostEqual(smoothed.desiredCurvature, expected_smoothed)
    for speed in (.31, 1., 2., 2.5, 20.):
      action = action_from_outputs({"action": np.array([[4., .5]])}, "v14", previous, .2, .2, speed)
      self.assertAlmostEqual(action.desiredCurvature, .04)

  def test_generation_action_units_and_v9_curvature(self):
    previous = log.ModelDataV2.Action(desiredCurvature=0.02)
    outputs = {"action": np.array([[4., 0.5]])}
    self.assertAlmostEqual(action_from_outputs(outputs, "v14", previous, .2, .2, 20).desiredCurvature, .04)
    for version in ("v15", "v16"):
      self.assertAlmostEqual(action_from_outputs(outputs, version, previous, .2, .2, 20).desiredCurvature, .01)
    plan = np.zeros((1, 33, 15))
    plan[0, :, 3] = 20
    outputs = {"plan": plan, "desired_curvature": np.array([[.03]])}
    self.assertAlmostEqual(action_from_outputs(outputs, "v9", previous, .2, .2, 20).desiredCurvature, .03)
    del outputs["desired_curvature"]
    self.assertAlmostEqual(action_from_outputs(outputs, "v9", previous, .2, .2, 20).desiredCurvature, .02)
    self.assertAlmostEqual(action_from_outputs(outputs, "v11", previous, .2, .2, 20).desiredCurvature, 0.)

  def test_runtime_inputs_desire_edges_reset_and_nonfinite_failure(self):
    artifact = artifact_fixture()
    with Context(DEV="CPU:LLVM"), patch("openpilot.starpilot.models.runner.load_oob", return_value=artifact):
      runner = CatalogModelState(8, 8, Path("verified.pkl"), "v15", False)
      frames = {name: np.zeros(runner.frame_buf_size, dtype=np.uint8) for name in runner.vision_input_names}
      transforms = dict.fromkeys(runner.vision_input_names, np.eye(3, dtype=np.float32))
      inputs = {"desire_pulse": np.array([0, 1, 0, 0, 0, 0, 0, 0], dtype=np.float32),
                "traffic_convention": np.array([1, 0]), "prev_action": np.array([2, 3]),
                "lateral_control_params": np.array([20, .2])}
      callback = Mock()
      runner.run(frames, transforms, inputs, callback)
      callback.assert_called_once_with()
      np.testing.assert_array_equal(runner.npy["desire"], inputs["desire_pulse"])
      np.testing.assert_array_equal(runner.npy["prev_action"], [[2, 3]])
      np.testing.assert_allclose(runner.npy["lateral_control_params"], [[20, .2]])
      runner.run(frames, transforms, inputs)
      np.testing.assert_array_equal(runner.npy["desire"], np.zeros(8))
      runner.warmup()
      np.testing.assert_array_equal(runner.prev_desire, np.zeros(8))
      np.testing.assert_array_equal(runner.npy["prev_action"], [[0, 0]])
      artifact["run_policy"].return_value = (Mock(numpy=lambda: np.array([np.nan])),)
      with self.assertRaisesRegex(ValueError, "not finite"):
        runner.run(frames, transforms, inputs)

  def test_verified_load_rejects_changed_bytes_before_deserialization(self):
    from openpilot.starpilot.models.receipt import artifact_digest, artifact_identity

    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      artifact = root / "model.pkl"
      artifact.write_bytes(b"trusted compiled artifact")
      digest = artifact_digest(artifact, artifact_identity(artifact))
      with patch("openpilot.starpilot.models.runner.CatalogModelState") as state:
        loaded, prepared = load_verified_model(8, 8, artifact, "v11", False, "sc23", digest, root=root)
        self.assertEqual(loaded.model_id, "sc23")
        self.assertEqual(prepared.sha256, digest)
        loaded.warmup.assert_called_once_with()
        state.reset_mock()
        artifact.write_bytes(b"changed artifact")
        with self.assertRaisesRegex(ValueError, "changed before loading"):
          load_verified_model(8, 8, artifact, "v11", False, "sc23", digest, root=root)
        state.assert_not_called()


if __name__ == "__main__":
  unittest.main()
