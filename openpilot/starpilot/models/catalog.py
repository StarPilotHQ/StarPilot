"""Reviewed local driving-model catalog. This module never loads model artifacts."""

from dataclasses import dataclass, replace
from functools import lru_cache
import json
from pathlib import Path


BUNDLED_CURRENT = "bundled-current"
DEFAULT_SMALL = "cheddar"
DEFAULT_SMALL_SHA256 = "e72274cbb666f1e81c4750de8593c7d9409e13751339f9fd3e74febd36bc96bd"
DEFAULT_SMALL_SIZE = 86376298
GENERATION = "v26"
COMPILER_REVISION = "9d0446a4ba8a532c8b674fb6ad795af015cd9dcf"
ARTIFACT_ABI = "tinygrad_single_v1_arena"
CATALOG_PATH = Path(__file__).with_name("catalog-v26.json")


@dataclass(frozen=True)
class ModelEntry:
  model_id: str
  name: str
  source_sha256: str
  source_path: str
  compiler_revision: str
  artifact_abi: str
  selectable: bool
  version: str = "current"
  uses_external_gpu: bool = False


BUNDLED = (
  ModelEntry(
    model_id=BUNDLED_CURRENT,
    name="Cheddar",
    source_sha256=DEFAULT_SMALL_SHA256,
    source_path="openpilot/selfdrive/modeld/models/cheddar_driving_tinygrad.pkl",
    compiler_revision="9d0446a4ba8a532c8b674fb6ad795af015cd9dcf",
    artifact_abi=ARTIFACT_ABI,
    selectable=True,
    version="v16",
  ),
)

CATALOG = BUNDLED + tuple(ModelEntry(
  model_id=m["id"], name=m["name"],
  source_sha256=DEFAULT_SMALL_SHA256 if m["id"] == DEFAULT_SMALL else "",
  source_path="openpilot/selfdrive/modeld/models/cheddar_driving_tinygrad.pkl" if m["id"] == DEFAULT_SMALL else "",
  compiler_revision=COMPILER_REVISION, artifact_abi=ARTIFACT_ABI, selectable=True,
  version=m["version"], uses_external_gpu=bool(m.get("uses_external_gpu", False)),
) for m in json.loads(CATALOG_PATH.read_text())["models"])

BY_ID = {entry.model_id: entry for entry in CATALOG}


def _document_identity(path: Path) -> tuple:
  try:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
  except OSError:
    return ()


@lru_cache(maxsize=8)
def _cached_entries(root: Path, identities: tuple) -> tuple[ModelEntry, ...]:
  from openpilot.starpilot.models.manager import catalog
  entries = dict(BY_ID)
  for model_id, row in catalog(root).items():
    entries[model_id] = (replace(BY_ID[model_id], name=row["name"]) if model_id in BY_ID else
                         ModelEntry(model_id, row["name"], "", "", COMPILER_REVISION, ARTIFACT_ABI,
                                    True, row["version"], row.get("uses_external_gpu", False)))
  return tuple(entries.values())


def model_entries(root: Path | None = None) -> dict[str, ModelEntry]:
  """Resolve validated cached metadata without mutating the bundled catalog."""
  from openpilot.starpilot.models.manager import ROOT
  root = ROOT if root is None else root
  identities = (_document_identity(CATALOG_PATH), _document_identity(root / "catalog.json"))
  return {entry.model_id: entry for entry in _cached_entries(root, identities)}


def resolve_selection(requested_id: str | None, *, root: Path | None = None) -> ModelEntry:
  """Resolve a saved request; absence means the bundled model, never an unknown ID."""
  model_id = DEFAULT_SMALL if requested_id in (None, BUNDLED_CURRENT) else requested_id
  entries = model_entries(root)
  if not isinstance(model_id, str) or model_id not in entries or not entries[model_id].selectable:
    raise ValueError("unqualified driving-model selection")
  return entries[model_id]
