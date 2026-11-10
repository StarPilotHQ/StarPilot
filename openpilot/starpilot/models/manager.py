"""Versioned model downloads and next-start Small/Chestnut selections."""

from __future__ import annotations

from contextlib import contextmanager
from collections import deque
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import random
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from urllib.request import urlopen

from openpilot.starpilot.models.catalog import (
  ARTIFACT_ABI, BUNDLED_CURRENT, BY_ID, CATALOG_PATH, COMPILER_REVISION, DEFAULT_SMALL, DEFAULT_SMALL_SHA256,
  DEFAULT_SMALL_SIZE, GENERATION, model_entries,
)

RESOURCE_URL = "https://huggingface.co/buckets/StarPilot-Driving/StarPilot-Resources/resolve"
ROOT = Path("/data/models") / GENERATION
MAX_MANIFEST = 2 * 1024 * 1024
MAX_ARTIFACT = 2 * 1024 * 1024 * 1024
MODEL_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
SUPPORTED_VERSIONS = {f"v{version}" for version in range(8, 17)}
SHA = re.compile(r"[0-9a-f]{64}\Z")
SHIPPED_MODELS = Path(__file__).resolve().parents[2] / "selfdrive/modeld/models"
DOM_CATALOG_PATH = Path(__file__).with_name("catalog-dom-v25.json")
DOM_CATALOG_SHA256 = "363dd6d29182186bdaf6a32f0cd6f0bbf7d7720ee77cfd1538e3b82b874d2821"
MODEL_RUNNER_REVISION = 2
RUNTIME_ARTIFACT_KEYS = {"min_runner_revision", "artifact_format", "artifact_sha256", "artifact_size",
                         "artifact_chunk_count", "conversion_provenance", "validation"}


class ModelError(ValueError):
  pass


def read_json(path: Path, limit: int = MAX_MANIFEST) -> dict:
  with path.open("rb") as stream:
    raw = stream.read(limit + 1)
  if len(raw) > limit:
    raise ModelError("Model document is too large")
  data = json.loads(raw)
  if not isinstance(data, dict):
    raise ModelError("Invalid model document")
  return data


def atomic_json(path: Path, value: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, name = tempfile.mkstemp(prefix=".model-", dir=path.parent)
  try:
    with os.fdopen(fd, "w") as out:
      json.dump(value, out, ensure_ascii=False, allow_nan=False, sort_keys=True)
      out.flush()
      os.fsync(out.fileno())
    os.replace(name, path)
  finally:
    Path(name).unlink(missing_ok=True)


@contextmanager
def state_lock(root: Path):
  root.mkdir(parents=True, exist_ok=True)
  with (root / ".state.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    yield


def validate_manifest(data: object) -> dict[str, dict]:
  if (not isinstance(data, dict) or data.get("generation") != GENERATION or data.get("compiler_revision") != COMPILER_REVISION or
      data.get("artifact_abi") != ARTIFACT_ABI or not isinstance(data.get("models"), list)):
    raise ModelError("The model catalog targets a different runtime")
  rows = {}
  for entry in data["models"]:
    if not isinstance(entry, dict):
      raise ModelError("Invalid model entry")
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not MODEL_ID.fullmatch(model_id) or model_id == BUNDLED_CURRENT or model_id in rows:
      raise ModelError("Unknown or duplicate model")
    name = entry.get("name")
    if (not isinstance(name, str) or not name.strip() or len(name) > 128 or
        any(ord(char) < 32 or ord(char) == 127 for char in name) or
        not isinstance(entry.get("version"), str) or entry["version"] not in SUPPORTED_VERSIONS or type(entry.get("uses_external_gpu", False)) is not bool):
      raise ModelError("Invalid model identity or behavior")
    for key in ("series", "released"):
      if key in entry and (not isinstance(entry[key], str) or len(entry[key]) > 128 or
                           any(ord(char) < 32 for char in entry[key])):
        raise ModelError("Invalid model display metadata")
    for key in ("community_favorite", "model_lab_eligible"):
      if key in entry and type(entry[key]) is not bool:
        raise ModelError("Invalid model capability metadata")
    known = BY_ID.get(model_id)
    if known is not None and (entry["version"] != known.version or
                              entry.get("uses_external_gpu", False) != known.uses_external_gpu):
      raise ModelError("Model behavior or hardware changed")
    variants = entry.get("accelerator_artifacts", {})
    if not isinstance(variants, dict) or set(variants) - {"amd"}:
      raise ModelError("Unknown model accelerator")
    amd = variants.get("amd")
    if amd is not None and (not isinstance(amd, dict) or entry.get("uses_external_gpu", False) or
                            entry.get("model_lab_eligible") is not True or amd.get("execution_device") != "AMD" or
                            amd.get("compiler_revision") != COMPILER_REVISION):
      raise ModelError("Invalid laboratory artifact hardware or runtime")
    runtime_artifacts = entry.get("runtime_artifacts", [])
    if not isinstance(runtime_artifacts, list):
      raise ModelError("Invalid runtime artifacts")
    revisions = set()
    for artifact in runtime_artifacts:
      if (not isinstance(artifact, dict) or set(artifact) - RUNTIME_ARTIFACT_KEYS or
          set(artifact) & {"artifact_format", "artifact_sha256", "artifact_size", "artifact_chunk_count"} !=
          {"artifact_format", "artifact_sha256", "artifact_size", "artifact_chunk_count"} or
          type(artifact.get("min_runner_revision")) is not int or artifact["min_runner_revision"] < 2 or
          artifact["min_runner_revision"] in revisions):
        raise ModelError("Invalid runtime artifact variant")
      revisions.add(artifact["min_runner_revision"])
    for artifact in (entry, *variants.values(), *runtime_artifacts):
      if "artifact_sha256" not in artifact:
        continue
      size, chunks = artifact.get("artifact_size"), artifact.get("artifact_chunk_count")
      if (not isinstance(artifact["artifact_sha256"], str) or not SHA.fullmatch(artifact["artifact_sha256"]) or
          type(size) is not int or not 0 < size <= MAX_ARTIFACT or type(chunks) is not int or not 0 <= chunks <= 99 or
          artifact.get("artifact_format") != ARTIFACT_ABI):
        raise ModelError("Invalid compiled artifact metadata")
    row = dict(entry)
    compatible = [artifact for artifact in runtime_artifacts if artifact["min_runner_revision"] <= MODEL_RUNNER_REVISION]
    if compatible:
      selected = max(compatible, key=lambda artifact: artifact["min_runner_revision"])
      row.update({key: value for key, value in selected.items() if key != "min_runner_revision"})
    if known is None and "artifact_sha256" not in row:
      raise ModelError("New models require a compatible compiled artifact")
    rows[model_id] = row
  return rows


def dom_laboratory_catalog() -> dict[str, dict]:
  # This is the authenticated original Dom publication, not a rebuilt v26 bank.
  if DOM_CATALOG_PATH.is_symlink() or not DOM_CATALOG_PATH.is_file():
    raise ModelError('Missing original Dom laboratory catalog')
  raw = DOM_CATALOG_PATH.read_bytes()
  if len(raw) > MAX_MANIFEST or hashlib.sha256(raw).hexdigest() != DOM_CATALOG_SHA256:
    raise ModelError('Original Dom laboratory catalog changed')
  document = json.loads(raw)
  if not isinstance(document, dict) or not isinstance(document.get('models'), list):
    raise ModelError('Invalid original Dom catalog')
  rows = {}
  seen = set()
  for row in document['models']:
    if not isinstance(row, dict):
      raise ModelError('Invalid original Dom row')
    mid = row.get('id')
    if not isinstance(mid, str) or not MODEL_ID.fullmatch(mid) or mid in seen:
      raise ModelError('Duplicate or invalid original Dom identity')
    seen.add(mid)
    artifact = row.get('accelerator_artifacts', {}).get('chestnut')
    if artifact is None:
      continue
    known = BY_ID.get(mid)
    if (known is None or known.uses_external_gpu or row.get('model_size') != 'small' or
        row.get('model_lab_eligible') is not True or row.get('version') != known.version or
        not isinstance(artifact, dict) or artifact.get('execution_device') != 'AMD' or
        artifact.get('artifact_format') != 'tinygrad_single_v1' or
        artifact.get('artifact_filename') != f'{mid}_driving_chestnut_tinygrad.pkl' or
        'compiler_revision' in artifact or not isinstance(artifact.get('artifact_sha256'), str) or
        not SHA.fullmatch(artifact['artifact_sha256']) or type(artifact.get('artifact_size')) is not int or
        not 0 < artifact['artifact_size'] <= MAX_ARTIFACT or type(artifact.get('artifact_chunk_count')) is not int or
        not 0 <= artifact['artifact_chunk_count'] <= 99):
      raise ModelError('Invalid original Dom AMD Small artifact')
    rows[mid] = {**artifact, 'artifact_format': 'dom_v25_protocol5', 'source_format': 'tinygrad_single_v1',
                 'source_compiler_revision': None, 'target_runtime_revision': COMPILER_REVISION,
                 'source_manifest_sha256': DOM_CATALOG_SHA256, '_generation': 'v25',
                 '_runtime_family': 'legacy9d', '_source_contract': 'dom_v25_protocol5'}
  return rows



def catalog(root: Path = ROOT) -> dict[str, dict]:
  base = validate_manifest(read_json(CATALOG_PATH))
  try:
    base.update(validate_manifest(read_json(root / "catalog.json")))
  except (OSError, ValueError, TypeError):
    pass
  for mid, artifact in dom_laboratory_catalog().items():
    if mid in base and 'amd' not in base[mid].get('accelerator_artifacts', {}):
      base[mid] = {**base[mid], 'model_lab_eligible': True,
                   'accelerator_artifacts': {**base[mid].get('accelerator_artifacts', {}), 'amd': artifact}}
  return base


def preferences(root: Path = ROOT) -> dict:
  default = {"small": DEFAULT_SMALL, "big": "", "userFavorites": [], "sortMode": "name",
             "randomizer": False, "blacklistedModels": []}
  try:
    saved = read_json(root / "preferences.json", 16384)
    entries = model_entries(root)
    for profile in ("small", "big"):
      mid = saved.get(profile)
      if isinstance(mid, str) and (mid == BUNDLED_CURRENT or (profile == "big" and mid == "") or (
          mid in entries and entries[mid].uses_external_gpu == (profile == "big"))):
        default[profile] = (DEFAULT_SMALL if profile == "small" else "") if mid == BUNDLED_CURRENT else mid
    for key in ("userFavorites", "blacklistedModels"):
      if isinstance(saved.get(key), list):
        default[key] = list(dict.fromkeys(x for x in saved[key] if isinstance(x, str) and x in entries))
    if type(saved.get("randomizer")) is bool:
      default["randomizer"] = saved["randomizer"]
    if saved.get("sortMode") in ("name", "date", "date_oldest", "community", "series", "favorites", "released"):
      default["sortMode"] = saved["sortMode"]
  except (OSError, ValueError, TypeError):
    pass
  return default


def artifact_path(model_id: str, root: Path = ROOT, variant: str = "standard") -> Path:
  entries = model_entries(root)
  if not isinstance(model_id, str) or model_id not in entries or model_id == BUNDLED_CURRENT:
    raise ModelError("Unknown downloadable model")
  if variant not in ("standard", "amd") or (variant == "amd" and entries[model_id].uses_external_gpu):
    raise ModelError("Unknown model artifact variant")
  if variant == 'amd':
    artifact = artifact_entry(model_id, catalog(root), variant)
    if artifact.get('_source_contract') == 'dom_v25_protocol5':
      return root / 'dom-v25' / model_id / artifact['artifact_filename']
  suffix = "amd_" if variant == "amd" else ""
  return root / model_id / f"{model_id}_driving_{suffix}tinygrad.pkl"


def artifact_entry(model_id: str, entries: dict[str, dict], variant: str = "standard") -> dict:
  row = entries.get(model_id, {})
  if variant == "standard":
    return row
  if variant == "amd" and not row.get("uses_external_gpu") and row.get("model_lab_eligible") is True:
    return row.get("accelerator_artifacts", {}).get("amd", {})
  return {}


def verified_artifact(model_id: str, entries: dict[str, dict], root: Path = ROOT, variant: str = "standard") -> Path | None:
  row = artifact_entry(model_id, entries, variant)
  if "artifact_sha256" not in row:
    return None
  path = artifact_path(model_id, root, variant)
  try:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != row["artifact_size"]:
      return None
    with path.open("rb") as stream:
      digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return path if digest == row["artifact_sha256"] else None
  except OSError:
    return None


def onnx_sha256(row: dict) -> str | None:
  """The ONNX a big model was built from, by the hash the catalog gives it: what Jetlink runs. None without one."""
  digests = row.get("source_sha256")
  if not isinstance(digests, dict) or len(digests) != 1:
    return None
  digest = next(iter(digests.values()))
  return digest if isinstance(digest, str) and SHA.fullmatch(digest) else None


def jetlink_active_big(entries: dict[str, dict], pick: dict | None) -> tuple[str, str]:
  """Active Big while Jetlink runs the big model: the model whose ONNX Jetlink was told to run, and what none
  means, the Jetlink app's choice, named when it is one StarPilot does not list."""
  sha = pick["sha256"] if pick else None
  mid = next((mid for mid, row in entries.items() if row.get("uses_external_gpu") and onnx_sha256(row) == sha), "") if sha else ""
  name = pick.get("displayName") if pick and not mid else None
  return mid, f"{name} · from the Jetlink app" if isinstance(name, str) and name else "Jetlink app's choice"


def shipped_default() -> Path | None:
  from openpilot.common.file_chunker import materialize_file_chunked

  try:
    path = materialize_file_chunked(SHIPPED_MODELS / f"{DEFAULT_SMALL}_driving_tinygrad.pkl",
                                    expected_sha256=DEFAULT_SMALL_SHA256)
    return path if path.stat().st_size == DEFAULT_SMALL_SIZE else None
  except (OSError, ValueError, TypeError, KeyError):
    return None


@dataclass(frozen=True)
class RuntimeSelection:
  small_id: str = BUNDLED_CURRENT
  small_path: Path | None = None
  small_version: str = "current"
  big_id: str = BUNDLED_CURRENT
  big_path: Path | None = None
  big_version: str = "current"
  allow_big: bool = True
  small_sha256: str | None = None
  big_sha256: str | None = None


def requested_runtime_id(chestnut_available: bool, *, root: Path = ROOT) -> str:
  prefs = preferences(root)
  return prefs["big"] if chestnut_available and prefs["big"] else prefs["small"]


def randomize_next_start(chestnut_available: bool, *, root: Path = ROOT, chooser=random.choice) -> dict:
  with state_lock(root):
    prefs = preferences(root)
    if not prefs["randomizer"]:
      return prefs
    profile = "big" if chestnut_available and prefs["big"] else "small"
    entries = catalog(root)
    blocked = set(prefs["blacklistedModels"])
    choices = [mid for mid in entries if mid not in blocked and
               entries[mid].get("uses_external_gpu", False) == (profile == "big") and verified_artifact(mid, entries, root) is not None]
    if profile == "small" and DEFAULT_SMALL not in blocked and DEFAULT_SMALL not in choices and shipped_default() is not None:
      choices.append(DEFAULT_SMALL)
    alternatives = [mid for mid in choices if mid != prefs[profile]]
    prefs[profile] = chooser(alternatives or choices) if choices else ("" if profile == "big" else DEFAULT_SMALL)
    atomic_json(root / "preferences.json", prefs)
    return prefs


def resolve_runtime(chestnut_available: bool, *, root: Path = ROOT, randomize: bool = False) -> RuntimeSelection:
  prefs = randomize_next_start(chestnut_available, root=root) if randomize else preferences(root)
  entries = catalog(root)
  small, big = prefs["small"], prefs["big"]
  small_path = verified_artifact(small, entries, root)
  shipped = small == DEFAULT_SMALL and small_path is None
  if shipped:
    small_path = shipped_default()
  if small_path is None:
    small = DEFAULT_SMALL
    shipped = True
    small_path = shipped_default()
  if small_path is None:
    raise ModelError("Shipped RDFv4 is missing or corrupt; reinstall the validated model package")
  big_path = verified_artifact(big, entries, root) if big not in ("", BUNDLED_CURRENT) and chestnut_available else None
  allow_big = chestnut_available and big_path is not None
  return RuntimeSelection(small, small_path, entries[small]["version"], big, big_path,
                          entries[big]["version"] if big in entries else "current", allow_big,
                          (DEFAULT_SMALL_SHA256 if shipped else entries[small]["artifact_sha256"]) if small_path is not None else None,
                          entries[big]["artifact_sha256"] if big_path is not None else None)


class ModelManager:
  def __init__(self, *, root: Path = ROOT, parked: Callable[[], bool] | None = None,
               gpu_present: Callable[[], bool] | None = None, opener=urlopen, jetlink=None, refresh_catalog: bool = False):
    self.root, self.opener = root, opener
    self.refresh_catalog = refresh_catalog
    self.last_refresh_attempt: float | None = None
    self.jetlink = jetlink
    self.parked = parked or (lambda: False)
    if gpu_present is None:
      from openpilot.selfdrive.modeld.helpers import chestnut_present
      gpu_present = chestnut_present
    self.gpu_present = gpu_present
    self.lock = threading.RLock()
    self.cancelled = threading.Event()
    self.worker: threading.Thread | None = None
    self.progress = ""
    self.downloading = ""
    self.download_all = False
    self.job_id = ""
    self.checked: dict[str, tuple[tuple, bool]] = {}
    self.snapshot_checked: dict[str, tuple[tuple, bool]] = {}
    self.verify_queue: deque[tuple[str, dict, tuple, str]] = deque()
    self.verifying: dict[str, tuple] = {}
    self.verify_worker: threading.Thread | None = None
    self.verify_closed = False

  def _refresh_if_needed(self) -> None:
    if not self.refresh_catalog or not self.parked():
      return
    now = time.monotonic()
    if self.last_refresh_attempt is not None and now - self.last_refresh_attempt < 300:
      return
    if self.worker is not None and self.worker.is_alive():
      return
    self.last_refresh_attempt = now
    try:
      self.action("refresh_manifest", {})
    except (OSError, ModelError):
      pass

  def _read_job(self) -> dict:
    try:
      job = read_json(self.root / ".download-job.json", 16384)
      if (job.get("schemaVersion") == 1 and isinstance(job.get("jobId"), str) and
          re.fullmatch(r"[0-9a-f]{32}", job["jobId"]) and job.get("state") in ("running", "completed", "failed", "interrupted") and
          job.get("model") in ("", *catalog(self.root)) and isinstance(job.get("progress"), str) and
          type(job.get("downloadAll")) is bool and type(job.get("cancelRequested")) is bool and
          job.get("variant", "standard") in ("standard", "amd")):
        return job
    except (OSError, ValueError, TypeError):
      pass
    return {}

  def _job_busy(self) -> bool:
    with (self.root / ".download.lock").open("a") as lock:
      try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      except BlockingIOError:
        return True
      return False

  def _job_status(self) -> dict:
    with state_lock(self.root):
      job, busy = self._read_job(), self._job_busy()
      if not busy and job.get("state") == "running":
        job.update(state="interrupted", progress="Previous model download was interrupted", model="", downloadAll=False, cancelRequested=False)
        atomic_json(self.root / ".download-job.json", job)
      return {**job, "downloading": busy,
              "refreshing": busy and job.get("state") == "running" and job.get("operation") == "refresh_manifest"}

  def _publish_job(self, *, progress: str | None = None, model: str | None = None) -> None:
    with state_lock(self.root):
      job = self._read_job()
      if job.get("jobId") != self.job_id or job.get("state") != "running":
        raise ModelError("Download job state is unavailable")
      if progress is not None:
        self.progress = progress[:1000]
        job["progress"] = self.progress
      if model is not None:
        self.downloading = model
        job["model"] = model
      atomic_json(self.root / ".download-job.json", job)

  def _check_cancelled(self) -> None:
    if self.cancelled.is_set():
      raise ModelError("Download cancelled")
    job = self._read_job()
    if job.get("jobId") != self.job_id or job.get("state") != "running":
      raise ModelError("Download job state is unavailable")
    if job["cancelRequested"]:
      raise ModelError("Download cancelled")

  def _finish_job(self, lock, failed: bool) -> None:
    self.downloading, self.download_all = "", False
    try:
      with state_lock(self.root):
        try:
          job = self._read_job()
          if job.get("jobId") == self.job_id:
            job.update(state="failed" if failed else "completed", progress=self.progress[:1000], model="", downloadAll=False)
            atomic_json(self.root / ".download-job.json", job)
        finally:
          lock.close()
    finally:
      if not lock.closed:
        lock.close()

  def _signature(self, mid: str, row: dict, variant: str = "standard") -> tuple | None:
    path = artifact_path(mid, self.root, variant)
    try:
      if row.get("shipped"):
        from openpilot.common.file_chunker import get_existing_chunks
        parts = get_existing_chunks(SHIPPED_MODELS / f"{DEFAULT_SMALL}_driving_tinygrad.pkl")
        sidecar = Path(f"{SHIPPED_MODELS / f'{DEFAULT_SMALL}_driving_tinygrad.pkl'}.chunksha256")
        if sidecar.exists():
          parts.append(sidecar)
        identity = tuple((str(part), part.stat().st_ino, part.stat().st_size,
                          part.stat().st_mtime_ns, part.stat().st_ctime_ns) for part in parts)
        return (0, 0, DEFAULT_SMALL_SIZE, 0, 0, DEFAULT_SMALL_SHA256, DEFAULT_SMALL_SIZE,
                any(part.is_symlink() for part in parts), identity)
      st = path.stat()
      return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns,
              row["artifact_sha256"], row["artifact_size"], path.is_symlink())
    except (OSError, ValueError, KeyError):
      return None

  def _installed(self, mid: str, entries: dict, variant: str = "standard") -> bool:
    if mid == BUNDLED_CURRENT:
      return shipped_default() is not None
    if mid == DEFAULT_SMALL and variant == "standard" and shipped_default() is not None:
      return True
    row = artifact_entry(mid, entries, variant)
    if "artifact_sha256" not in row:
      return False
    signature = self._signature(mid, row, variant)
    if signature is None:
      return False
    key = f"{mid}:{variant}"
    if self.checked.get(key, (None,))[0] != signature:
      self.checked[key] = (signature, verified_artifact(mid, entries, self.root, variant) is not None)
    return self.checked[key][1]

  def _verification_receipt(self, mid: str, variant: str) -> Path:
    return self.root / ".verified" / f"{mid}-{variant}.json"

  def _remember_verification(self, mid: str, variant: str, signature: tuple) -> None:
    if len(signature) != 8:
      return
    try:
      atomic_json(self._verification_receipt(mid, variant), {"version": 1, "signature": list(signature)})
    except OSError:
      logging.exception("Could not save model verification for %s", mid)

  def _snapshot_installed(self, mid: str, row: dict, variant: str = "standard") -> tuple[bool, bool]:
    if mid == DEFAULT_SMALL and variant == "standard" and self._signature(mid, row, variant) is None:
      row = {**row, "shipped": True, "artifact_sha256": DEFAULT_SMALL_SHA256, "artifact_size": DEFAULT_SMALL_SIZE}
    artifact = artifact_entry(mid, {mid: row}, variant)
    if "artifact_sha256" not in artifact:
      return False, False
    signature = self._signature(mid, artifact, variant)
    if signature is None or signature[7] or signature[2] != artifact["artifact_size"]:
      return False, False
    key = f"{mid}:{variant}"
    checked = self.checked.get(key)
    if checked is not None and checked[0] == signature:
      return checked[1], False
    if len(signature) == 8:
      if self.snapshot_checked.get(key, (None,))[0] != signature:
        try:
          receipt = read_json(self._verification_receipt(mid, variant), 4096)
          valid = receipt == {"version": 1, "signature": list(signature)}
        except (OSError, ValueError, TypeError):
          valid = False
        self.snapshot_checked[key] = (signature, valid)
      if self.snapshot_checked[key][1]:
        return True, False
    if self.verify_worker is not None and not self.verify_worker.is_alive():
      self.verify_worker = None
      self.verifying.clear()
      self.verify_queue.clear()
    if not self.verify_closed and self.verifying.get(key) != signature:
      self.verifying[key] = signature
      self.verify_queue.append((mid, row, signature, variant))
      if self.verify_worker is None:
        self.verify_worker = threading.Thread(target=self._verify_pending, daemon=True)
        self.verify_worker.start()
    return False, True

  def _verify_pending(self) -> None:
    while True:
      with self.lock:
        if self.verify_closed or not self.verify_queue:
          self.verify_worker = None
          return
        mid, row, signature, variant = self.verify_queue.popleft()
        key = f"{mid}:{variant}"
        if self.verifying.get(key) != signature:
          continue
      try:
        valid = (shipped_default() if row.get("shipped") else verified_artifact(mid, {mid: row}, self.root, variant)) is not None
      except Exception:
        logging.exception("Model verification failed for %s", mid)
        valid = False
      with self.lock:
        if self.verifying.get(key) == signature:
          self.verifying.pop(key)
          if self._signature(mid, artifact_entry(mid, {mid: row}, variant), variant) == signature:
            self.checked[key] = (signature, valid)
            if valid:
              self._remember_verification(mid, variant, signature)

  def selected_gpu_status(self) -> tuple[bool, bool]:
    """Verify the selected download asynchronously, without starting a model or download job."""
    with self.lock:
      mid = preferences(self.root)["big"]
      if not mid:
        return False, False
      return self._snapshot_installed(mid, catalog(self.root).get(mid, {}))

  def jetlink_owner(self):
    if self.jetlink is None:
      from openpilot.starpilot.models.jetlink_settings import JetlinkSettings
      self.jetlink = JetlinkSettings()
    return self.jetlink

  def snapshot(self) -> dict:
    with self.lock:
      self._refresh_if_needed()
      entries, prefs = catalog(self.root), preferences(self.root)
      parked, gpu = self.parked(), self.gpu_present()
      job = self._job_status()
      owner = self.jetlink_owner()
      jetlink = owner.snapshot()
      # with Jetlink running the big model, Active Big is its choice: a model whose ONNX the catalog names
      jetlink_big = owner.chooses_big(jetlink)
      active_big, big_none = (jetlink_active_big(entries, owner.pick()) if jetlink_big else
                              (prefs["big"], "None — always use Active Small"))
      models = []
      for mid, row in entries.items():
        installed, checking = self._snapshot_installed(mid, row)
        requires_gpu = bool(row.get("uses_external_gpu"))
        published = "artifact_sha256" in row
        models.append({"value": mid, "label": row["name"], "version": row["version"], "series": row.get("series", ""),
                       "released": row.get("released", ""), "communityFavorite": bool(row.get("community_favorite")),
                       "userFavorite": mid in prefs["userFavorites"], "blacklisted": mid in prefs["blacklistedModels"],
                       "installed": installed, "checking": checking,
                       "builtin": mid == DEFAULT_SMALL,
                       "requiresGpu": requires_gpu, "gpuAvailable": gpu or not requires_gpu, "small": not requires_gpu,
                       "selectable": onnx_sha256(row) is not None if requires_gpu and jetlink_big else installed,
                       "downloadAvailable": published, "artifactSize": row.get("artifact_size", 0),
                       "unavailableReason": "" if installed else "Checking artifact" if checking else
                       "Download required" if published else f"{GENERATION} download not published"})
      count = sum(bool(row["installed"]) for row in models)
      return {"schemaVersion": 1, "models": models, "activeSmallModel": prefs["small"], "activeBigModel": active_big,
              "jetlinkBig": jetlink_big, "bigNoneLabel": big_none,
              "currentModel": prefs["big"] if gpu and prefs["big"] else prefs["small"], "userFavorites": prefs["userFavorites"],
              "sortMode": prefs["sortMode"], "randomizer": prefs["randomizer"], "blacklistedModels": prefs["blacklistedModels"],
              "summary": {"total": len(models), "installed": count, "missing": len(models)-count},
              "jobId": job.get("jobId", ""), "cancelRequested": job.get("cancelRequested", False),
              "downloadVariant": job.get("variant", "standard"),
              "modelToDownload": job.get("model", ""), "downloadAll": job.get("downloadAll", False),
              "downloading": job["downloading"], "refreshing": job["refreshing"], "progress": job.get("progress", ""),
              "isOnroad": not parked, "gpuAvailable": gpu, "jetlink": jetlink,
              "capabilities": {"select": True, "download": parked, "downloadAll": parked, "cancel": True,
                               "jetlink": parked, "refresh": parked, "favorites": True, "delete": parked, "randomizer": parked, "exclusions": parked}}

  def _require_parked(self) -> None:
    if not self.parked():
      raise ModelError("Turn the vehicle off before changing driving models")

  def action(self, action: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
      raise ModelError("Invalid model request")
    with self.lock:
      if action == "jetlink":
        self._require_parked()
        return self.jetlink_owner().configure(payload)
      if action == "cancel":
        if set(payload) - {"jobId"} or ("jobId" in payload and not isinstance(payload["jobId"], str)):
          raise ModelError("Invalid cancel request")
        with state_lock(self.root):
          job, busy = self._read_job(), self._job_busy()
          if "jobId" in payload and payload["jobId"] != job.get("jobId"):
            raise ModelError("This download job is no longer running")
          if not busy:
            return {"message": "No model download is running"}
          if job.get("state") != "running":
            raise ModelError("Download job state is unavailable")
          job["cancelRequested"] = True
          atomic_json(self.root / ".download-job.json", job)
          if job["jobId"] == self.job_id:
            self.cancelled.set()
        return {"message": "Download cancellation requested"}
      if action == "preferences":
        if not payload or set(payload) - {"userFavorites", "sortMode", "randomizer", "blacklistedModels"}:
          raise ModelError("Invalid model preferences")
        for key in ("userFavorites", "blacklistedModels"):
          values = payload.get(key, [])
          if (not isinstance(values, list) or len(values) > len(model_entries(self.root)) or
              any(not isinstance(x, str) or x not in model_entries(self.root) for x in values)):
            raise ModelError("Unknown model preference")
        changes_selection = bool(set(payload) & {"randomizer", "blacklistedModels"})
        if "randomizer" in payload and type(payload["randomizer"]) is not bool:
          raise ModelError("Invalid randomizer preference")
        if changes_selection:
          self._require_parked()
        if "sortMode" in payload and payload["sortMode"] not in ("name", "date", "date_oldest", "community", "series", "favorites", "released"):
          raise ModelError("Invalid sort mode")
        with state_lock(self.root):
          if changes_selection:
            self._require_parked()
          saved = preferences(self.root)
          saved.update(payload)
          atomic_json(self.root / "preferences.json", saved)
        return {"message": "Model preferences saved"}
      if action != "active":
        self._require_parked()
      if action in ("download", "download_all", "refresh_manifest"):
        if self.worker is not None and self.worker.is_alive():
          raise ModelError("A model download is already running")
        if action == "download" and (set(payload) - {"model", "allowGpuWithoutGpu", "variant"} or
                                     not isinstance(payload.get("model"), str) or payload["model"] not in catalog(self.root)):
          raise ModelError("Unknown model download")
        if action != "download" and set(payload) - {"allowGpuWithoutGpu"}:
          raise ModelError("Invalid download request")
        if "allowGpuWithoutGpu" in payload and type(payload["allowGpuWithoutGpu"]) is not bool:
          raise ModelError("Invalid GPU download preference")
        variant = payload.get("variant", "standard")
        if variant not in ("standard", "amd"):
          raise ModelError("Unknown model artifact variant")
        if variant == "amd" and not artifact_entry(payload["model"], catalog(self.root), "amd").get("artifact_sha256"):
          raise ModelError("This model has no eGPU variant for this software version yet")
        if (action == "download" and catalog(self.root)[payload["model"]].get("uses_external_gpu", False) and
            not self.gpu_present() and not payload.get("allowGpuWithoutGpu")):
          raise ModelError("Connect Chestnut or choose to download for later")
        with state_lock(self.root):
          self._require_parked()
          lock = (self.root / ".download.lock").open("a")
          try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
          except BlockingIOError as error:
            lock.close()
            raise ModelError("Another model manager is downloading") from error
          except OSError:
            lock.close()
            raise
          self.cancelled.clear()
          self.job_id = uuid.uuid4().hex
          self.progress = "Refreshing catalog" if action == "refresh_manifest" else "Starting download"
          self.downloading = payload.get("model", "")
          self.download_all = action == "download_all"
          try:
            atomic_json(self.root / ".download-job.json", {
              "schemaVersion": 1, "jobId": self.job_id, "state": "running", "model": self.downloading,
              "progress": self.progress, "downloadAll": self.download_all, "cancelRequested": False, "variant": variant, "operation": action,
            })
          except BaseException:
            lock.close()
            raise
        message = self.progress
        self.worker = threading.Thread(target=self._work, args=(action, dict(payload), lock), daemon=True)
        try:
          self.worker.start()
        except BaseException:
          self.progress = "Model download could not start"
          self._finish_job(lock, True)
          raise
        return {"message": message}
      if action == "active":
        job = self._job_status()
        if job["downloading"] and not job["refreshing"]:
          raise ModelError("Wait for the model download to finish before selecting a model")
        if (set(payload) != {"profile", "model"} or payload["profile"] not in ("small", "big") or
            not isinstance(payload["model"], str)):
          raise ModelError("Invalid model selection")
        if preferences(self.root)["randomizer"]:
          raise ModelError("Turn Model Randomizer off before choosing a model")
        mid, profile, entries = payload["model"], payload["profile"], catalog(self.root)
        # Jetlink runs a big model's ONNX, so it needs no download here, only the catalog's hash for it
        jetlink = profile == "big" and self.jetlink_owner().chooses_big()
        if mid != BUNDLED_CURRENT and not (profile == "big" and mid == ""):
          if mid not in entries or entries[mid].get("uses_external_gpu", False) != (profile == "big"):
            raise ModelError("Model does not match the selected hardware profile")
          if jetlink:
            if onnx_sha256(entries[mid]) is None:
              raise ModelError("Jetlink cannot run this model yet: its catalog entry names no ONNX")
          elif not self._installed(mid, entries):
            raise ModelError("Download and verify this model first")
        with state_lock(self.root):
          if jetlink:
            self._require_parked()
          saved = preferences(self.root)
          if saved["randomizer"]:
            raise ModelError("Turn Model Randomizer off before choosing a model")
          job = self._read_job()
          if self._job_busy() and not (job.get("state") == "running" and job.get("operation") == "refresh_manifest"):
            raise ModelError("Wait for the model download to finish before selecting a model")
          if jetlink:
            # Chestnut's choice stays as it was; none leaves the model to the Jetlink app
            row = entries.get(mid, {})
            self.jetlink_owner().choose(onnx_sha256(row), row.get("name", ""))
            return {"message": "Model selected for Jetlink"}
          saved[profile] = mid
          atomic_json(self.root / "preferences.json", saved)
        return {"message": "Model selected for the next drive"}
      if action == "delete":
        if set(payload) != {"model"}:
          raise ModelError("Invalid delete request")
        mid = payload["model"]
        path = artifact_path(mid, self.root)
        with state_lock(self.root):
          saved = preferences(self.root)
          if self._job_busy():
            raise ModelError("Wait for the model download to finish before deleting a model")
          if mid in (saved["small"], saved["big"]):
            raise ModelError("Select another model before deleting this one")
          self._require_parked()
          path.unlink(missing_ok=True)
          self.checked.pop(f"{mid}:standard", None)
        return {"message": "Downloaded model deleted"}
      raise ModelError("Unknown model action")

  def _refresh(self) -> None:
    url = f"{RESOURCE_URL}/manifests/model_names_{GENERATION}.json"
    with self.opener(url, timeout=30) as response:
      raw = response.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
      raise ModelError("Model catalog is too large")
    data = json.loads(raw)
    validate_manifest(data)
    atomic_json(self.root / "catalog.json", data)

  def _work(self, action: str, payload: dict, lock) -> None:
    failed = True
    try:
      self._check_cancelled()
      self._refresh()
      self._check_cancelled()
      entries = catalog(self.root)
      variant = payload.get("variant", "standard")
      ids = [payload["model"]] if action == "download" else [mid for mid, row in entries.items()
            if "artifact_sha256" in row and (not row.get("uses_external_gpu") or self.gpu_present() or payload.get("allowGpuWithoutGpu"))]
      if action != "refresh_manifest":
        for mid in ids:
          self._check_cancelled()
          variants = ('standard', 'amd') if variant == 'amd' and 'artifact_sha256' in entries[mid] else (variant,)
          for current in variants:
            if not self._installed(mid, entries, current):
              self._publish_job(model=mid)
              self._download(mid, artifact_entry(mid, entries, current), current)
      self._check_cancelled()
      self.progress = "Catalog updated" if action == "refresh_manifest" else "Downloaded!"
      failed = False
    except (OSError, ValueError, KeyError, TimeoutError) as error:
      failed = True
      self.progress = str(error) or "Model download failed"
    finally:
      self._finish_job(lock, failed)

  def _download(self, mid: str, entry: dict, variant: str = "standard") -> None:
    if "artifact_sha256" not in entry:
      raise ModelError("This model is not available for this software version yet")
    destination = artifact_path(mid, self.root, variant)
    destination.parent.mkdir(parents=True, exist_ok=True)
    size, chunks = entry["artifact_size"], entry["artifact_chunk_count"]
    if shutil.disk_usage(self.root).free < size + 256 * 1024 * 1024:
      raise ModelError("Not enough storage for this model")
    filename = destination.name
    display_name = catalog(self.root)[mid]["name"]
    pieces = [filename] if chunks == 0 else [f"{filename}.chunk{i:02d}of{chunks:02d}" for i in range(1, chunks+1)]
    fd, temp = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
    try:
      digest, received = hashlib.sha256(), 0
      with os.fdopen(fd, "wb") as out:
        for piece in pieces:
          generation = 'v25' if entry.get('_source_contract') == 'dom_v25_protocol5' else GENERATION
          with self.opener(f"{RESOURCE_URL}/models/{generation}/{mid}/{piece}", timeout=30) as response:
            while data := response.read(1024 * 1024):
              self._check_cancelled()
              received += len(data)
              if received > size:
                raise ModelError("Model exceeds its declared size")
              out.write(data)
              digest.update(data)
              progress = f"{display_name}: {received * 100 // size}%"
              if progress != self.progress:
                self._publish_job(progress=progress)
        out.flush()
        os.fsync(out.fileno())
      if received != size or digest.hexdigest() != entry["artifact_sha256"]:
        raise ModelError("Model checksum did not match; download discarded")
      with state_lock(self.root):
        self._require_parked()
        self._check_cancelled()
        os.replace(temp, destination)
      self.checked.pop(f"{mid}:{variant}", None)
    finally:
      Path(temp).unlink(missing_ok=True)

  def close(self) -> None:
    self.cancelled.set()
    with self.lock:
      self.verify_closed = True
      self.verify_queue.clear()
      verify_worker = self.verify_worker
    if verify_worker is not None:
      verify_worker.join(timeout=2)
    if self.worker is not None:
      self.worker.join(timeout=2)
