"""Run catalog models using their declared history and output contracts."""

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any
import weakref

import numpy as np
from tinygrad import Tensor, dtypes

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.drive_helpers import MIN_SPEED, smooth_value
from openpilot.selfdrive.modeld.constants import ModelConstants, Plan
from openpilot.selfdrive.modeld.helpers import load_oob
from openpilot.starpilot.models.parser import Parser
from openpilot.starpilot.models.catalog import ARTIFACT_ABI, COMPILER_REVISION
from openpilot.starpilot.models.receipt import PreparedArtifact, artifact_identity, prepare_artifact
from openpilot.system.camerad.cameras.nv12_info import get_nv12_info


SUPPORTED_VERSIONS = frozenset(f"v{version}" for version in range(8, 17))
REQUIRED_OUTPUTS = frozenset(("plan", "lane_lines", "lane_lines_prob", "road_edges", "lead", "lead_prob",
                              "meta", "desire_pred", "pose", "wide_from_device_euler", "road_transform"))


@dataclass(frozen=True)
class CameraWarpDescriptor:
  camera_size: tuple[int, int]
  nv12_layout: tuple[int, int, int, int]
  image_layouts: tuple[tuple[int, ...], tuple[int, ...]]
  output_shape: tuple[int, ...]
  dtype: object
  device: str


@dataclass(frozen=True)
class SharedCameraWarp:
  tensor: Tensor
  descriptor: CameraWarpDescriptor
  frame_pointers: tuple[int, int]
  transforms: tuple[bytes, bytes]
  producer: weakref.ReferenceType


def load_verified_model(cam_w: int, cam_h: int, artifact_path: Path, behavior_version: str, chestnut: bool,
                        model_id: str, expected_sha256: str | None, *, root: Path | None = None,
                        coherent_warp: Callable[[Any], None] | None = None) -> tuple["CatalogModelState", PreparedArtifact]:
  from openpilot.starpilot.models.manager import ROOT, state_lock

  if expected_sha256 is None:
    raise ValueError("selected catalog model has no verified digest")
  with state_lock(ROOT if root is None else root):
    identity = artifact_identity(artifact_path)
    prepared = prepare_artifact(artifact_path, identity)
    if prepared.sha256 != expected_sha256:
      raise ValueError("selected catalog model changed before loading")
    model = CatalogModelState(cam_w, cam_h, artifact_path, behavior_version, chestnut)
    if coherent_warp is not None:
      model.move_warp_output(coherent_warp)
    model.warmup()
    if artifact_identity(artifact_path) != identity:
      raise ValueError("selected catalog model changed while loading")
    model.model_id = model_id
    return model, prepared


def validate_artifact(artifact: dict, version: str, camera_size: tuple[int, int]) -> None:
  if isinstance(artifact, dict) and artifact.get("artifact_abi") == "dom_v25_protocol5":
    from openpilot.starpilot.models.runtime_bridge.runtime import frozen_laboratory_request
    from openpilot.starpilot.models.runtime_bridge.dom_admission import require_admitted_dom
    request = frozen_laboratory_request()
    matches = [role for role in request['roles'].values()
               if role['sha256'] == artifact.get('migration', {}).get('source_sha256')] if request is not None else []
    if len(matches) != 1:
      raise ValueError("Original laboratory artifact lacks its frozen source identity")
    require_admitted_dom(artifact, expected_contract=matches[0])
  elif (not isinstance(artifact, dict) or artifact.get("artifact_abi") != ARTIFACT_ABI or
        artifact.get("compiler_revision") != COMPILER_REVISION or artifact.get("format_version") != 1):
    raise ValueError("catalog model compiler/ABI mismatch")
  if version not in SUPPORTED_VERSIONS or artifact.get("behavior_version") != version:
    raise ValueError("catalog model behavior version mismatch")
  if artifact.get("model_type") not in ("supercombo", "vision_policy", "vision_multi_policy"):
    raise ValueError("unsupported catalog model layout")
  if artifact.get("image_history_pipeline") not in ("policy", "warp"):
    raise ValueError("unsupported catalog model history")
  if type(artifact.get("frame_skip")) is not int or not 1 <= artifact["frame_skip"] <= 20:
    raise ValueError("unsupported catalog model frame interval")
  if camera_size not in artifact or not callable(artifact.get("run_policy")) or not callable(artifact[camera_size]):
    raise ValueError("catalog model camera/runner unavailable")
  metadata = artifact.get("metadata")
  if not isinstance(metadata, dict):
    raise ValueError("missing catalog model metadata")
  keys = ["model"] if artifact["model_type"] == "supercombo" else ["vision", *artifact.get("policy_order", [])]
  if not keys or (keys[0] == "vision" and not any(key in keys for key in ("on_policy", "policy"))):
    raise ValueError("catalog model has no primary policy")
  names = set()
  for key in keys:
    entry = metadata.get(key)
    if not isinstance(entry, dict) or not isinstance(entry.get("input_shapes"), dict) or not isinstance(entry.get("output_slices"), dict):
      raise ValueError("invalid catalog model metadata")
    shape = entry.get("output_shapes", {}).get("outputs")
    if (not isinstance(shape, (tuple, list)) or len(shape) != 2 or shape[0] != 1 or
        type(shape[1]) is not int or shape[1] <= 0):
      raise ValueError("catalog model output width unavailable")
    width = shape[1]
    for name, section in entry["output_slices"].items():
      if (not isinstance(section, slice) or section.step not in (None, 1) or
          any(bound is not None and (type(bound) is not int or not -width <= bound <= width)
              for bound in (section.start, section.stop))):
        raise ValueError("catalog model output slice exceeds output")
      # Metadata padding aliases use ordinary Python negative/open endpoints.
      # Check explicit bounds before indices() so its clipping cannot hide a bad slice.
      start, stop, _ = section.indices(width)
      if not 0 <= start < stop <= width:
        raise ValueError("catalog model output slice is empty or reversed")
      names.add(name)
  if not REQUIRED_OUTPUTS <= names or (version in ("v14", "v15", "v16") and "action" not in names):
    raise ValueError("catalog model is missing required driving outputs")


def action_from_outputs(outputs: dict[str, np.ndarray], version: str, previous: log.ModelDataV2.Action,
                        lat_action_t: float, long_action_t: float, v_ego: float,
                        lat_smooth_seconds: float = 0.0, long_smooth_seconds: float = 0.3) -> log.ModelDataV2.Action:
  """Preserve the catalog's generation-specific action units and v9 curvature."""
  if version not in SUPPORTED_VERSIONS or min(lat_action_t, long_action_t) <= 0:
    raise ValueError("invalid catalog model action contract")
  if version in ("v14", "v15", "v16"):
    curvature, acceleration = outputs["action"][0]
    curvature = curvature / (100.0 if version == "v14" else max(1.0, v_ego) ** 2)
    stop = v_ego < 0.3 and acceleration < 0.1
  else:
    plan = outputs["plan"][0]
    if "planplus" in outputs:
      plan = plan + outputs["planplus"][0]
    speeds, accelerations = plan[:, Plan.VELOCITY][:, 0], plan[:, Plan.ACCELERATION][:, 0]
    acceleration = 2 * (np.interp(long_action_t, ModelConstants.T_IDXS, speeds) - speeds[0]) / long_action_t - accelerations[0]
    stop = speeds[0] < 0.3 and acceleration < 0.1
    if version == "v9":
      curvature = float(outputs["desired_curvature"][0, 0]) if "desired_curvature" in outputs else previous.desiredCurvature
    else:
      yaw = np.interp(lat_action_t, ModelConstants.T_IDXS, plan[:, Plan.T_FROM_CURRENT_EULER][:, 2])
      yaw_rate = plan[0, Plan.ORIENTATION_RATE][2]
      curvature = 2 * yaw / (max(MIN_SPEED, v_ego) * lat_action_t) - yaw_rate / max(MIN_SPEED, v_ego)
  acceleration = smooth_value(float(acceleration), previous.desiredAcceleration, long_smooth_seconds)
  curvature = (smooth_value(float(curvature), previous.desiredCurvature, lat_smooth_seconds)
               if v_ego > 0.3 else previous.desiredCurvature)
  if not np.isfinite((acceleration, curvature)).all():
    raise ValueError("non-finite catalog model action")
  return log.ModelDataV2.Action(desiredCurvature=float(curvature), desiredAcceleration=float(acceleration), shouldStop=bool(stop))


class CatalogModelState:
  """Current modeld interface around a current-pin unified catalog artifact."""

  lat_delay: float
  frame_drop_ratio: float
  vision_input_names = ("img", "big_img")

  def __init__(self, cam_w: int, cam_h: int, artifact_path: Path, behavior_version: str, chestnut: bool):
    from openpilot.starpilot.models.compile import _detect_vision_keys, stateful_image_shapes, stateful_host_shapes

    artifact = load_oob(artifact_path, chestnut)
    validate_artifact(artifact, behavior_version, (cam_w, cam_h))
    self.chestnut = chestnut
    self.behavior_version = behavior_version
    self.model_type = artifact["model_type"]
    self.metadata = artifact["metadata"]
    self.policy_order = artifact.get("policy_order", [])
    self.frame_skip = artifact["frame_skip"]
    self.image_history_pipeline = artifact["image_history_pipeline"]
    self.warp_input_keys = tuple(artifact["warp_input_keys"])
    self.policy_input_keys = tuple(artifact["policy_input_keys"])
    self.run_policy = artifact["run_policy"]
    self.warp_enqueue = artifact[(cam_w, cam_h)]
    self.onnx_history = self.model_type == "supercombo" and bool(self.metadata["model"].get("state_pairs"))
    self.queue_device = artifact["execution_device"]
    self.warp_device = artifact["warp_device"]
    if (not isinstance(self.queue_device, str) or not isinstance(self.warp_device, str) or
        chestnut != (self.queue_device.rsplit("+", 1)[-1].split(":", 1)[0] == "AMD")):
      raise ValueError("catalog model execution hardware mismatch")
    if self.onnx_history:
      metadata = self.metadata["model"]
      image_shapes = stateful_image_shapes(metadata)
      self.policy_input_shapes = stateful_host_shapes(metadata)
      self.output_slices = metadata["output_slices"]
    elif self.model_type == "supercombo":
      image_shapes = self.policy_input_shapes = self.metadata["model"]["input_shapes"]
      self.output_slices = self.metadata["model"]["output_slices"]
    else:
      image_shapes = self.metadata["vision"]["input_shapes"]
      primary = "on_policy" if "on_policy" in self.policy_order else "policy"
      self.policy_input_shapes = self.metadata[primary]["input_shapes"]
    self.road_key, self.wide_key = _detect_vision_keys(image_shapes)
    self.desire_key = next(key for key in self.policy_input_shapes if key.startswith("desire"))
    self.parser = Parser()
    self.aux_parser = Parser(ignore_missing=True)
    self.frame_buf_size = get_nv12_info(cam_w, cam_h)[3]
    image_layouts = (tuple(image_shapes[self.road_key][-2:]), tuple(image_shapes[self.wide_key][-2:]))
    self.camera_warp_descriptor = (CameraWarpDescriptor((cam_w, cam_h), get_nv12_info(cam_w, cam_h), image_layouts,
                                                       (2, 6, *image_layouts[0]), dtypes.uint8, self.warp_device)
                                   if self.image_history_pipeline == "policy" else None)
    # why Jetlink's link copies this warp's output out every frame (jetlink_warp); None once it is moved
    self.warp_copied: str | None = "Jetlink was off when the model loaded"
    self._reset_state()

  def move_warp_output(self, coherent_warp: Callable[[Any], None]) -> None:
    """On a comma, move this warp's output to memory the CPU reads cached with `coherent_warp` (Jetlink's
    make_coherent), for the link to send from. Before warmup(), whose first call links the warp to its address."""
    buffer = self.warp_enqueue.captured.ret.uop.base.buffer
    if not buffer.device.startswith("QCOM"):
      self.warp_copied = f"a {buffer.device} buffer"
      return
    try:
      coherent_warp(buffer)
    except RuntimeError as e:   # the buffer stays where it was
      self.warp_copied = str(e)
      return
    self.warp_copied = None

  def _reset_state(self) -> None:
    from openpilot.starpilot.models.compile import make_stateful_input_queues, make_supercombo_input_queues, make_split_input_queues

    if self.onnx_history:
      self.input_queues, self.npy = make_stateful_input_queues(self.metadata["model"], self.queue_device)
    elif self.model_type == "supercombo":
      self.input_queues, self.npy = make_supercombo_input_queues(self.policy_input_shapes, self.frame_skip, self.queue_device)
    else:
      self.input_queues, self.npy = make_split_input_queues(self.metadata["vision"]["input_shapes"], self.policy_input_shapes,
                                                         self.frame_skip, self.queue_device)
    self.prev_desire = np.zeros(ModelConstants.DESIRE_LEN, dtype=np.float32)
    self._blob_cache: dict[tuple[str, int], Tensor] = {}
    self.last_warp: SharedCameraWarp | None = None
    self._pending_frame: tuple | None = None
    self._last_shared_warp: SharedCameraWarp | None = None

  def _validate_shared_warp(self, shared: SharedCameraWarp, pointers: tuple[int, int], transforms: tuple[bytes, bytes]) -> None:
    if (not isinstance(shared, SharedCameraWarp) or self.camera_warp_descriptor is None or
        shared.descriptor != self.camera_warp_descriptor):
      raise ValueError("catalog models have incompatible camera warp descriptors/history")
    tensor = shared.tensor
    if (tuple(tensor.shape) != shared.descriptor.output_shape or tensor.dtype != shared.descriptor.dtype or
        tensor.device != shared.descriptor.device):
      raise ValueError("catalog shared camera warp tensor contract mismatch")
    producer = shared.producer()
    if producer is None or producer.last_warp is not shared or shared is self._last_shared_warp:
      raise ValueError("catalog shared camera warp is stale or already consumed")
    if shared.frame_pointers != pointers or shared.transforms != transforms:
      raise ValueError("catalog shared camera warp frame/transforms mismatch")

  @staticmethod
  def slice_outputs(output: np.ndarray, sections: dict[str, slice]) -> dict[str, np.ndarray]:
    return {key: output[np.newaxis, section].copy() for key, section in sections.items()}

  def _parse(self, outputs: list[np.ndarray]) -> dict[str, np.ndarray]:
    if self.model_type == "supercombo":
      parsed = self.parser.parse_outputs(self.slice_outputs(outputs[0], self.output_slices))
      if "prev_feat" in self.npy and "hidden_state" in self.output_slices:
        self.npy["prev_feat"][:] = outputs[0][self.output_slices["hidden_state"]]
      return parsed
    vision_output, *policies = outputs
    parsed = self.parser.parse_vision_outputs(self.slice_outputs(vision_output, self.metadata["vision"]["output_slices"]))
    results = {}
    for key, output in zip(self.policy_order, policies, strict=True):
      slices = self.slice_outputs(output, self.metadata[key]["output_slices"])
      results[key] = self.aux_parser.parse_off_policy_outputs(slices) if key == "off_policy" else self.parser.parse_policy_outputs(slices)
    for key in self.policy_order:
      if key not in ("on_policy", "policy"):
        parsed.update(results[key])
    parsed.update(results["on_policy" if "on_policy" in results else "policy"])
    return parsed

  def get_action_from_model(self, outputs: dict[str, np.ndarray], previous: log.ModelDataV2.Action,
                            lat_action_t: float, long_action_t: float, v_ego: float) -> log.ModelDataV2.Action:
    """modeld's action call: this catalog generation's units, with modeld's smoothing."""
    from openpilot.selfdrive.modeld.modeld import LAT_SMOOTH_SECONDS, LONG_SMOOTH_SECONDS
    return action_from_outputs(outputs, self.behavior_version, previous, lat_action_t, long_action_t, v_ego,
                               LAT_SMOOTH_SECONDS, LONG_SMOOTH_SECONDS)

  def warmup(self) -> None:
    frames = {name: np.zeros(self.frame_buf_size, dtype=np.uint8) for name in self.vision_input_names}
    self._blob_cache.update({(name, frame.ctypes.data): Tensor(frame, device=self.warp_device).realize()
                             for name, frame in frames.items()})
    inputs = {"desire_pulse": np.zeros(ModelConstants.DESIRE_LEN, dtype=np.float32)}
    self.run(frames, dict.fromkeys(self.vision_input_names, np.eye(3, dtype=np.float32)), inputs)
    self._reset_state()

  def warp_frames(self, pointers: tuple[int, int], tfm: np.ndarray, big_tfm: np.ndarray) -> Tensor:
    """Enqueue the camera warp of the frames at these addresses, on the inputs its JIT was captured with.
    run() calls it, and so does Jetlink's link (jetlink_warp) on the frames the large model drives."""
    self.npy["tfm"][:] = tfm
    self.npy["big_tfm"][:] = big_tfm
    frames = {}
    for name, pointer in zip(self.vision_input_names, pointers, strict=True):
      key = (name, pointer)
      if key not in self._blob_cache:
        self._blob_cache[key] = Tensor.from_blob(pointer, (self.frame_buf_size,), dtype="uint8", device=self.warp_device)
      frames[name] = self._blob_cache[key]
    return self.warp_enqueue(**{key: self.input_queues[key] for key in self.warp_input_keys},
                             frame=frames["img"], big_frame=frames["big_img"])

  def enqueue(self, bufs: dict, transforms: dict[str, np.ndarray], inputs: dict[str, np.ndarray],
          after_enqueue: Callable[[], None] | None = None, *, shared_warp: SharedCameraWarp | None = None) -> tuple:
    if self._pending_frame is not None:
      raise RuntimeError("Finish the current model frame before enqueueing another")
    self.last_warp = None
    road_pointer, wide_pointer = (np.frombuffer(bufs[name].data, dtype=np.uint8).ctypes.data for name in self.vision_input_names)
    pointers = (road_pointer, wide_pointer)
    road_transform, wide_transform = (np.asarray(transforms[name], dtype=np.float32).tobytes() for name in self.vision_input_names)
    transform_signature = (road_transform, wide_transform)
    if shared_warp is not None:
      self._validate_shared_warp(shared_warp, pointers, transform_signature)
      self._last_shared_warp = shared_warp
    desire = inputs["desire_pulse"].copy()
    desire[0] = 0
    self.npy["desire"][:] = np.where(desire - self.prev_desire > 0.99, desire, 0)
    self.prev_desire[:] = desire
    for name, value in self.npy.items():
      if name not in ("desire", "tfm", "big_tfm", "prev_feat") and name in inputs:
        value[:] = inputs[name]
    if shared_warp is None:
      warped = self.warp_frames(pointers, transforms["img"], transforms["big_img"])
    else:
      warped = shared_warp.tensor
    policy_inputs = {key: self.input_queues[key] for key in self.policy_input_keys}
    output_tensors = (self.run_policy(**policy_inputs, warped=warped) if self.image_history_pipeline == "policy" else
                      self.run_policy(**policy_inputs, img=warped[0], big_img=warped[1]))
    if after_enqueue is not None:
      after_enqueue()
    if self.camera_warp_descriptor is not None:
      self.last_warp = (shared_warp if shared_warp is not None else
                        SharedCameraWarp(warped, self.camera_warp_descriptor, pointers, transform_signature, weakref.ref(self)))
    self._pending_frame = (output_tensors, self.last_warp)
    return self._pending_frame

  def finish(self, queued: tuple) -> dict[str, np.ndarray]:
    if queued is not self._pending_frame:
      raise ValueError("Model frame belongs to a different or completed enqueue")
    self._pending_frame = None
    output_tensors = queued[0]
    try:
      outputs = [output.numpy().flatten() for output in output_tensors]
      if not all(np.isfinite(output).all() for output in outputs):
        raise ValueError("catalog model output not finite")
      parsed = self._parse(outputs)
      if not REQUIRED_OUTPUTS <= parsed.keys() or not all(np.isfinite(value).all() for value in parsed.values()):
        raise ValueError("catalog model parsed outputs invalid")
      if os.getenv("SEND_RAW_PRED"):
        parsed["raw_pred"] = np.concatenate(outputs)
      return parsed
    except Exception:
      self.last_warp = None
      raise

  def run(self, bufs: dict, transforms: dict[str, np.ndarray], inputs: dict[str, np.ndarray],
          after_enqueue: Callable[[], None] | None = None, *, shared_warp: SharedCameraWarp | None = None) -> dict[str, np.ndarray]:
    try:
      return self.finish(self.enqueue(bufs, transforms, inputs, after_enqueue, shared_warp=shared_warp))
    except Exception:
      self._pending_frame = None
      self.last_warp = None
      raise
