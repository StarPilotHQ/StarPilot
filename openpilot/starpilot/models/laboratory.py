from pathlib import Path

from openpilot.starpilot.models.catalog import model_entries, BUNDLED_CURRENT, GENERATION
from openpilot.starpilot.models.manager import (
  ModelError, ModelManager, ARTIFACT_ABI, COMPILER_REVISION, artifact_entry, artifact_path, atomic_json, catalog, read_json, state_lock, verified_artifact,
)


def laboratory_variant(model_id: str, entries: dict) -> str:
  row = entries[model_id]
  return "amd" if not row.get("uses_external_gpu", False) and row.get("accelerator_artifacts", {}).get("amd") else "standard"


def freeze_request(root: Path) -> dict | None:
  if not RUNTIME_QUALIFIED:
    return None
  config = configuration(root)
  if not config['enabled']:
    return None
  entries = catalog(root)
  roles = {}
  for role in ('lateralModel', 'longitudinalModel'):
    mid = config[role]
    row = entries[mid]
    variant = laboratory_variant(mid, entries)
    artifact = artifact_entry(mid, entries, variant)
    dom = artifact.get('_source_contract') == 'dom_v25_protocol5'
    legacy = (artifact.get('artifact_format') == ARTIFACT_ABI and
              artifact.get('compiler_revision', COMPILER_REVISION if variant == 'standard' else None) == COMPILER_REVISION)
    if not (dom or legacy):
      raise ModelError('Laboratory requires qualified GPU model source contracts')
    if dom and (artifact.get('source_format') != 'tinygrad_single_v1' or artifact.get('source_compiler_revision') is not None or
                'compiler_revision' in artifact or artifact.get('target_runtime_revision') != COMPILER_REVISION):
      raise ModelError('Invalid original Dom source/runtime identity')
    path = verified_artifact(mid, entries, root, variant)
    if path is None:
      raise ModelError('Laboratory artifact is missing or corrupt')
    roles[role] = {'model_id': mid, 'version': row['version'], 'path': str(path),
                   'sha256': artifact['artifact_sha256'], 'source_contract': dict(artifact), 'variant': variant,
                   'chestnut': bool(row.get('uses_external_gpu') or variant == 'amd')}
  for key in ('lateralModel', 'longitudinalModel'):
    if any(not role['chestnut'] for role in roles.values()):
      break
    mid = config[key]
    if entries[mid].get('uses_external_gpu'):
      continue
    native = verified_artifact(mid, entries, root, 'standard')
    if native is not None:
      artifact = artifact_entry(mid, entries, 'standard')
      roles[key] = {'model_id': mid, 'version': entries[mid]['version'], 'path': str(native),
                    'sha256': artifact['artifact_sha256'], 'source_contract': dict(artifact),
                    'variant': 'standard', 'chestnut': False}
      break
  if roles['lateralModel']['sha256'] == roles['longitudinalModel']['sha256']:
    raise ModelError('Laboratory roles must use distinct artifacts')
  return {**config, 'root': str(root), 'roles': roles, 'target_runtime_revision': COMPILER_REVISION, 'target_arena_abi': ARTIFACT_ABI}



def configuration(root: Path) -> dict:
  default = {"enabled": False, "lateralModel": "", "longitudinalModel": ""}
  try:
    saved = read_json(root / "laboratory.json", 4096)
    validate_configuration(saved, root)
    return saved
  except (OSError, ValueError, TypeError):
    return default


def validate_configuration(value: dict, root: Path | None = None) -> None:
  entries = model_entries(root)
  if (not isinstance(value, dict) or set(value) != {"enabled", "lateralModel", "longitudinalModel"} or
      type(value["enabled"]) is not bool):
    raise ModelError("Invalid laboratory configuration")
  for key in ("lateralModel", "longitudinalModel"):
    mid = value[key]
    if not isinstance(mid, str) or (mid and (mid not in entries or mid == BUNDLED_CURRENT or entries[mid].version not in {f"v{i}" for i in range(8, 17)})):
      raise ModelError("Choose supported models for the laboratory pair")
  if value["enabled"] and (not value["lateralModel"] or not value["longitudinalModel"] or
                           value["lateralModel"] == value["longitudinalModel"]):
    raise ModelError("Choose two different models")
  if value['enabled']:
    rows = catalog(root) if root is not None else catalog()
    if not any(rows[value[key]].get('uses_external_gpu') or laboratory_variant(value[key], rows) == 'amd'
               for key in ('lateralModel', 'longitudinalModel')):
      raise ModelError('Choose at least one model with a Chestnut variant')


RUNTIME_QUALIFIED = True


class ModelLaboratory:
  def __init__(self, manager: ModelManager, *, runtime_supported: bool | None = None, runtime_status=None):
    self.manager = manager
    self.runtime_status = runtime_status
    self.runtime_supported = RUNTIME_QUALIFIED if runtime_supported is None else runtime_supported

  @property
  def runtime_unavailable_reason(self) -> str:
    return "" if self.runtime_supported else "Pair inference is not available in this build yet."

  def snapshot(self) -> dict:
    owner = self.manager
    with owner.lock:
      owner._refresh_if_needed()
      entries = catalog(owner.root)
      config = configuration(owner.root)
      parked, gpu = owner.parked(), owner.gpu_present()
      job = owner._job_status()
      models = []
      for mid, row in entries.items():
        small = not row.get("uses_external_gpu", False)
        eligible = row.get("version") in {f"v{i}" for i in range(8, 17)}
        artifact = artifact_entry(mid, entries, laboratory_variant(mid, entries)) if eligible else {}
        published = "artifact_sha256" in artifact
        installed, checking = owner._snapshot_installed(mid, row, laboratory_variant(mid, entries)) if published else (False, False)
        if not eligible:
          state, reason = "unsupported", "This model is not available for combining."
        elif not published:
          state, reason = "unpublished", "A compatible artifact has not been published for this model."
        elif checking:
          state, reason = "checking", "Checking the downloaded Chestnut version."
        elif not installed:
          state, reason = "missing", "Download this model to prepare it for combining."
        elif not self.runtime_supported:
          state, reason = "runtime-unavailable", "Download verified. Running two models together is unavailable in this build."
        elif not gpu:
          state, reason = "gpu-unavailable", "Download verified. Connect a ready Chestnut to use it."
        else:
          state, reason = "installed", "Download verified. Choose another compatible model to form a pair."
        models.append({"value": mid, "label": row["name"], "version": row["version"], "series": row.get("series", ""),
                       "small": small, "modelSize": "small" if small else "big", "modelLabEligible": eligible,
                       "modelLabChestnut": bool(row.get("uses_external_gpu") or laboratory_variant(mid, entries) == "amd"),
                       "modelLabArtifactAvailable": published, "modelLabArtifactInstalled": installed, "checking": checking,
                       "modelLabStatus": state, "modelLabReason": reason,
                       "artifactSize": artifact.get("artifact_size", 0)})
      return {"schemaVersion": 1, "chestnutReady": gpu, "isOnroad": not parked, "configuration": config,
              "configurationError": self.runtime_unavailable_reason if config["enabled"] else "",
              "runtimeSupported": self.runtime_supported, "runtimeUnavailableReason": self.runtime_unavailable_reason,
              "runtime": (self.runtime_status(config) if self.runtime_status is not None else
                          {"requested": config["enabled"], "active": False, "lateralModel": "", "longitudinalModel": "",
                           "error": "", "health": "unavailable"}),
              "download": {"model": job.get("model", ""), "progress": job.get("progress", ""),
                           "downloading": job["downloading"], "jobId": job.get("jobId", ""),
                           "variant": job.get("variant", "standard")},
              "summary": {"catalog": len(models), "eligible": sum(row["modelLabEligible"] is True for row in models),
                          "ready": sum(row["modelLabArtifactInstalled"] is True for row in models),
                          "published": sum(row["modelLabArtifactAvailable"] is True for row in models),
                          "declaredSize": sum(int(row["artifactSize"]) for row in models)},
              "models": models, "manifest": {"version": GENERATION},
              "capabilities": {"configure": parked, "download": parked,
                               "delete": parked, "cancel": True, "refresh": parked}}

  def action(self, action: str, payload: dict) -> dict:
    owner = self.manager
    if not isinstance(payload, dict):
      raise ModelError("Invalid laboratory request")
    with owner.lock:
      owner._require_parked()
      if action == "download":
        if set(payload) != {"model"} or not isinstance(payload["model"], str):
          raise ModelError("Unknown laboratory model")
        mid, entries = payload["model"], catalog(owner.root)
        if mid not in entries:
          raise ModelError("Unknown laboratory model")
        return owner.action("download", {"model": mid, "variant": laboratory_variant(mid, entries), "allowGpuWithoutGpu": True})
      if action not in ("configure", "delete"):
        raise ModelError("Unknown laboratory action")
      with state_lock(owner.root):
        owner._require_parked()
        if owner._job_busy():
          raise ModelError("Wait for the model download to finish")
        if action == "configure":
          validate_configuration(payload, owner.root)
          if payload["enabled"]:
            if not self.runtime_supported:
              raise ModelError(self.runtime_unavailable_reason)
            if not owner.gpu_present():
              raise ModelError("Connect a firmware-ready Chestnut to enable this pair")
            entries = catalog(owner.root)
            for role in ("lateralModel", "longitudinalModel"):
              if verified_artifact(payload[role], entries, owner.root, laboratory_variant(payload[role], entries)) is None:
                raise ModelError("Download and verify both model artifacts first")
            owner._require_parked()
          atomic_json(owner.root / "laboratory.json", payload)
          message = "Model Laboratory configuration saved"
        else:
          if set(payload) != {"model"} or not isinstance(payload["model"], str):
            raise ModelError("Unknown laboratory model")
          mid = payload["model"]
          entries = catalog(owner.root)
          if mid not in entries:
            raise ModelError("Unknown laboratory model")
          if laboratory_variant(mid, entries) != "amd":
            raise ModelError("Manage standard model downloads from the model catalog")
          destination = artifact_path(mid, owner.root, "amd")
          config = configuration(owner.root)
          if config["enabled"] and mid in (config["lateralModel"], config["longitudinalModel"]):
            raise ModelError("Disable the laboratory pair before deleting either eGPU variant")
          destination.unlink(missing_ok=True)
          owner.checked.pop(f"{mid}:amd", None)
          message = "eGPU variant deleted"
      return {**self.snapshot(), "message": message}
