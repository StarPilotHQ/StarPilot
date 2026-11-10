from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from openpilot.cereal import log
from openpilot.starpilot.models.laboratory import validate_configuration
from openpilot.starpilot.models.laboratory_outputs import compose_model_outputs, hybrid_action_values
from openpilot.starpilot.models.manager import ROOT
from openpilot.starpilot.models.receipt import PreparedArtifact
from openpilot.starpilot.models.catalog import ARTIFACT_ABI as LEGACY_ABI, COMPILER_REVISION as LEGACY_REVISION
from openpilot.starpilot.models.runner import CatalogModelState, action_from_outputs, load_verified_model


@dataclass(frozen=True)
class LaboratoryRole:
  model_id: str
  behavior_version: str
  artifact: PreparedArtifact
  source_format: str = LEGACY_ABI
  source_compiler_revision: str | None = LEGACY_REVISION
  source_manifest_sha256: str = ""


@dataclass(frozen=True)
class LaboratoryFrame:
  outputs: dict[str, np.ndarray]
  action: log.ModelDataV2.Action
  producer: tuple[LaboratoryRole, LaboratoryRole] | None = None


class LaboratoryPair:
  lat_delay: float
  frame_drop_ratio: float

  def __init__(self, lateral: CatalogModelState, longitudinal: CatalogModelState,
               lateral_artifact: PreparedArtifact, longitudinal_artifact: PreparedArtifact,
               source_contracts: tuple[dict, dict] | None = None):
    if lateral is longitudinal or lateral.model_id == longitudinal.model_id:
      raise ValueError("Model Laboratory requires two distinct models")
    if not lateral.chestnut and not longitudinal.chestnut:
      raise ValueError("Model Laboratory requires at least one AMD variant")
    self.shared_camera = (lateral.camera_warp_descriptor is not None and
                          lateral.camera_warp_descriptor == longitudinal.camera_warp_descriptor)
    self.lateral = lateral
    self.longitudinal = longitudinal
    contracts = source_contracts if source_contracts is not None else ({}, {})
    self.lateral_role = LaboratoryRole(lateral.model_id, lateral.behavior_version, lateral_artifact,
      contracts[0].get('source_format', LEGACY_ABI), contracts[0].get('source_compiler_revision', LEGACY_REVISION),
      contracts[0].get('source_manifest_sha256', ''))
    self.longitudinal_role = LaboratoryRole(longitudinal.model_id, longitudinal.behavior_version, longitudinal_artifact,
      contracts[1].get('source_format', LEGACY_ABI), contracts[1].get('source_compiler_revision', LEGACY_REVISION),
      contracts[1].get('source_manifest_sha256', ''))
    self.failed = False
    self.chestnut = True
    self.model_id = f"{lateral.model_id}+{longitudinal.model_id}"
    self._last_outputs = None

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    if self.failed:
      raise RuntimeError("Model Laboratory pair failed; load a fallback before continuing")
    try:
      first, second = ((self.longitudinal, self.lateral) if not self.lateral.chestnut else
                       (self.lateral, self.longitudinal))
      first_queued = first.enqueue(bufs, transforms, inputs)
      shared = first.last_warp
      if self.shared_camera and shared is None:
        raise ValueError("Model Laboratory camera warp unavailable")
      second_queued = second.enqueue(bufs, transforms, inputs, after_enqueue, shared_warp=shared if self.shared_camera else None)
      first_output, second_output = first.finish(first_queued), second.finish(second_queued)
      lateral, longitudinal = ((first_output, second_output) if first is self.lateral else (second_output, first_output))
      self._last_outputs = lateral, longitudinal
      return compose_model_outputs(lateral, longitudinal)
    except Exception:
      self.failed = True
      self.lateral.last_warp = self.longitudinal.last_warp = None
      raise

  def get_action_from_model(self, outputs, previous, lat_action_t, long_action_t, v_ego):
    from openpilot.selfdrive.modeld.constants import LAT_SMOOTH_SECONDS
    LONG_SMOOTH_SECONDS = 0.3
    if self.failed or self._last_outputs is None:
      raise RuntimeError("Model Laboratory has no complete current frame")
    try:
      actions = [action_from_outputs(value, model.behavior_version, previous, lat_action_t, long_action_t, v_ego,
                                     LAT_SMOOTH_SECONDS, LONG_SMOOTH_SECONDS)
                 for value, model in zip(self._last_outputs, (self.lateral, self.longitudinal), strict=True)]
      return log.ModelDataV2.Action(**hybrid_action_values(*actions))
    except Exception:
      self.failed = True
      raise

  def run_frame(self, bufs: dict, transforms: dict[str, np.ndarray], inputs: dict[str, np.ndarray],
                previous: log.ModelDataV2.Action, lat_action_t: float, long_action_t: float, v_ego: float,
                lat_smooth_seconds: float = 0.0, long_smooth_seconds: float = 0.3,
                after_enqueue: Callable[[], None] | None = None) -> LaboratoryFrame:
    """Run both roles before releasing the camera frame; any failure requires fallback."""
    if self.failed:
      raise RuntimeError("Model Laboratory pair failed; load a fallback before continuing")
    try:
      composed = self.run(bufs, transforms, inputs, after_enqueue)
      assert self._last_outputs is not None
      lateral_output, longitudinal_output = self._last_outputs
      actions = [action_from_outputs(output, model.behavior_version, previous, lat_action_t, long_action_t, v_ego,
                                     lat_smooth_seconds, long_smooth_seconds)
                 for model, output in ((self.lateral, lateral_output), (self.longitudinal, longitudinal_output))]
      action = log.ModelDataV2.Action(**hybrid_action_values(*actions))
      return LaboratoryFrame(composed, action,
                             (self.lateral_role, self.longitudinal_role))
    except Exception:
      self.failed = True
      self.lateral.last_warp = self.longitudinal.last_warp = None
      raise


def load_laboratory_pair(cam_w: int, cam_h: int, config: dict, *, root: Path = ROOT) -> LaboratoryPair:
  from openpilot.starpilot.models.runtime_bridge.runtime import frozen_laboratory_request
  validate_configuration(config, root)
  request = frozen_laboratory_request()
  if not config["enabled"] or request is None or any(config[key] != request[key] for key in config):
    raise ValueError("Model Laboratory requires the exact frozen startup request")
  loaded = []
  contracts = []
  for key in ('lateralModel', 'longitudinalModel'):
    role = request['roles'][key]
    loaded.append(load_verified_model(cam_w, cam_h, Path(role['path']), role['version'], role['chestnut'],
                                      role['model_id'], role['sha256'], root=root))
    contracts.append(role['source_contract'])
  return LaboratoryPair(loaded[0][0], loaded[1][0], loaded[0][1], loaded[1][1], tuple(contracts))
