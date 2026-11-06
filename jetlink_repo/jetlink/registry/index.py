"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

Which big models a Jetson can run, and which ONNX each one is: the model
index, zoompilot/jetlink-model-index.

A big model is a comma openpilot commit (its ref), and what a Jetson runs is
the ONNX that commit was built from. Finding that ONNX takes reading comma's
trees, its precompiled pkls' weights and its model repo, so the index repo does
it once an hour for every commit any catalog, comma master or comma pull
request has named, and publishes the answers:

  default the model a Jetson runs with nothing picked
  refs    every big-model commit ever seen -> {oid, size}: an old pick still resolves
  onnx    every ONNX comma has published -> {size}: a pick by hash alone is sized
  models  what to list, one entry per ONNX, in display order, with its folder
          and the catalog fields a list in sunnypilot's shape needs

The comma and the server each keep the last index that parsed, so both work
offline with what they had, and neither looks anything up itself.

Stdlib only, and every network call takes an `opener` so tests stay offline.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field

INDEX_URL = 'https://raw.githubusercontent.com/zoompilot/jetlink-model-index/main/index/v1.json'
# the index's own version: another one describes fields we would misread
SCHEMA = 1
INDEX_TIMEOUT = 10.0
# a listed model with no folder of its own
OTHER_FOLDER = 'Other Models'
# the runtime fields of a listed model the index has none for (an index older
# than them): what sunnypilot's big models are built for
RUNTIME = {'generation': '12', 'environment': 'development', 'runner': 'tinygrad'}
# what runs with nothing picked before the comma or server has an index, which
# names its own (ModelIndex.default): Cinque Terre V3 Model (September 17, 2026)
DEFAULT_BIG_MODEL_REF = 'bf3e3631b3f91d92a1020a5e0dd4298b93ff4244'
# its name without the build date: what a comma with no index to name it from
# shows (jetlink.openpilot, on a fork without sunnypilot's model manager)
DEFAULT_BIG_MODEL_NAME = 'Cinque Terre V3 Model'

_REF = re.compile(r'[0-9a-f]{40}')
_SHA256 = re.compile(r'[0-9a-f]{64}')


class RegistryError(Exception):
  """Anything the registry refuses to do."""


class NetworkError(RegistryError):
  """A server could not be reached or did not answer sensibly."""


class VerifyError(RegistryError):
  """Bytes arrived, but not the bytes that were asked for."""


class IndexInvalid(RegistryError):
  """An index that cannot be used: another schema, or nothing listed."""


def is_ref(value) -> bool:
  """A comma openpilot commit: 40 lowercase hex characters."""
  return isinstance(value, str) and _REF.fullmatch(value) is not None


def is_sha256(value) -> bool:
  """A model identity: 64 lowercase hex characters, which is also the LFS oid."""
  return isinstance(value, str) and _SHA256.fullmatch(value) is not None


@dataclass(frozen=True)
class Pointer:
  oid: str    # the ONNX SHA-256
  size: int


@dataclass(frozen=True)
class IndexModel:
  ref: str
  oid: str
  size: int
  name: str          # as sunnypilot's catalog names it, build date and all
  short_name: str    # '' when the index has none
  folder: str
  build_time: str    # ISO-8601, or ''
  pr: int | None     # the comma pull request a model listed only as one is
  is_20hz: bool
  generation: str    # the runtime it was built for, as sunnypilot's catalog says
  environment: str
  runner: str


@dataclass(frozen=True)
class ModelIndex:
  models: tuple[IndexModel, ...]   # in display order
  refs: dict[str, Pointer]         # every ref the index resolves, the listed ones included
  sizes: dict[str, int]            # every ONNX the index knows, by oid
  default: str | None = None       # the listed ref that runs with nothing picked
  # the listed models by their ONNX: one entry per ONNX, so one each
  by_oid: dict[str, IndexModel] = field(init=False, repr=False, compare=False)

  def __post_init__(self):
    object.__setattr__(self, 'by_oid', {m.oid: m for m in reversed(self.models)})

  def pointer(self, ref: str) -> Pointer | None:
    return self.refs.get(ref)

  def size(self, oid: str) -> int | None:
    return self.sizes.get(oid)

  def listed(self, ref: str) -> IndexModel | None:
    """The listed model for a ref's ONNX: its own entry, or for a recompile of a
    listed model, the newest ref's (each ONNX is listed once)."""
    pointer = self.refs.get(ref)
    return self.by_oid.get(pointer.oid) if pointer is not None else None


def _pointer(value) -> Pointer | None:
  if not isinstance(value, dict) or not is_sha256(value.get('oid')):
    return None
  size = _size(value.get('size'))
  return Pointer(value['oid'], size) if size else None


def _size(value) -> int | None:
  # bool is an int; a size is not
  return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _text(value) -> str:
  return value if isinstance(value, str) else ''


def parse_index(data) -> ModelIndex:
  """The index, or IndexInvalid naming why it cannot be used. No IO. An entry
  that is malformed costs that entry and nothing else; an index of another
  schema, or one listing nothing, is refused whole, so a caller keeps the
  last one that parsed rather than an empty list."""
  if not isinstance(data, dict):
    raise IndexInvalid('the index is not a JSON object')
  if data.get('schema') != SCHEMA:
    raise IndexInvalid(f"the index is schema {data.get('schema')!r}, not {SCHEMA}")
  refs = {ref: p for ref, value in _items(data.get('refs')) if is_ref(ref) and (p := _pointer(value))}
  sizes = {oid: s for oid, value in _items(data.get('onnx')) if is_sha256(oid) and isinstance(value, dict) and (s := _size(value.get('size')))}
  models: dict[str, IndexModel] = {}
  for entry in data.get('models') if isinstance(data.get('models'), list) else []:
    if not isinstance(entry, dict) or not is_ref(entry.get('ref')) or entry['ref'] in models or (p := _pointer(entry)) is None:
      continue
    ref = entry['ref']
    models[ref] = IndexModel(ref=ref, oid=p.oid, size=p.size, name=_text(entry.get('name')) or ref[:10],
                             short_name=_text(entry.get('short_name')), folder=_text(entry.get('folder')) or OTHER_FOLDER,
                             build_time=_text(entry.get('build_time')), pr=_size(entry.get('pr')),
                             is_20hz=entry['is_20hz'] if isinstance(entry.get('is_20hz'), bool) else True,
                             **{k: _text(entry.get(k)) or v for k, v in RUNTIME.items()})
  if not models:
    raise IndexInvalid('the index lists no model')
  # a listed model is what is shown and run, so its own entry names its ONNX
  for m in models.values():
    refs[m.ref] = Pointer(m.oid, m.size)
  for p in refs.values():
    sizes.setdefault(p.oid, p.size)
  default = data.get('default')
  return ModelIndex(models=tuple(models.values()), refs=refs, sizes=sizes, default=default if default in models else None)


def _items(value) -> list[tuple]:
  return list(value.items()) if isinstance(value, dict) else []


def fetch_index(url: str = INDEX_URL, timeout: float = INDEX_TIMEOUT, opener=None) -> tuple[dict, ModelIndex]:
  """The index as served, and parsed: the raw JSON is what a caller keeps.
  A transport failure is a NetworkError, an index that cannot be used
  IndexInvalid; both are RegistryErrors, and either way nothing is kept."""
  opener = opener or urllib.request.urlopen
  try:
    with opener(url, timeout=timeout) as response:
      body = response.read()
  except (OSError, ValueError) as e:   # HTTPError is an OSError
    raise NetworkError(f"could not fetch {url}: {e}") from e
  try:
    data = json.loads(body.decode())
  except ValueError as e:
    raise NetworkError(f"{url} did not serve JSON: {e}") from e
  return data, parse_index(data)
