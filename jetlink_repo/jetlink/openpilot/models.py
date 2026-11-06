"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

Which large model runs, and where its bytes are.

The large model is the model manager's big-model pick, the same slot a
chestnut runs from (Keys.big_model). It names a comma commit, and what the
Jetson runs is the ONNX that commit was built from: the model index
(jetlink.registry.index) says which, for every big-model commit, and lists
what can be picked. The comma keeps the last index that parsed under
model_dir(), so it names, sizes and lists its models with no network; a pick
the copy does not know fetches it again (resolve).

Without a chestnut fitted the model manager's big-model list is the index's
(big_catalog), so what the comma lists is what the Jetson runs. With one, the
list is sunnypilot's own, and jetlink stays out of it.

Without a model manager (Keys.big_model and Keys.catalog None) there is no
pick and no catalog, and the large model is the one picked on the server, else
jetlink's own default, named by its ref.

A fork with a picker of its own but no catalog jetlink reads (Keys.big_model
without Keys.catalog, StarPilot's) may name its pick by the ONNX's sha256
instead of a ref, which is all its catalog knows of a model; the index sizes
it. With no catalog no model manager owns the slot either, so jetlink writes a
newer pick from the server into it itself, and the fork's picker shows what
drives.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from jetlink.registry.index import (DEFAULT_BIG_MODEL_NAME, DEFAULT_BIG_MODEL_REF, ModelIndex, NetworkError, Pointer, RegistryError,
                                    is_ref, is_sha256, parse_index)

# the build date a catalog name ends in, " (September 17, 2026)"; any other parenthesis stays
_TRAILING_DATE = re.compile(r' \([A-Za-z]+ \d{1,2}, \d{4}\)$')
# the index and the slot are a file and a JSON param, and the UI names the
# active model every frame; the status line can lag a new pick by this long
INDEX_TTL = 2.0
# the comma's copy of the model index, under model_dir()
INDEX_FILE = 'index.json'
# a pick the comma's copy does not know fetches the index again, at most this often
INDEX_MISS_RETRY = 60.0
# A big model's download over a car's connection: the waits between the
# attempts of one provisioning run, one more attempt than waits. Each carries
# on from the .part the last left, so a stall costs the wait, not the bytes
# (lfs.lfs_download)
DOWNLOAD_RETRY_DELAYS = (5.0, 15.0, 30.0, 60.0, 120.0)
# server_pick() before this process has heard a hello
_UNHEARD = object()
# heard() told nothing of the hello's own pick: an older caller
_UNTOLD = object()
# the slot as jetlink saw it last, under model_dir(): to tell a slot the user
# emptied (the default, chosen) from one never filled
SLOT_STATE = 'slot.json'


def _dated(pick) -> dict | None:
  """A server's pick as its hello carries it, dated by this clock:
  {ref, sha256, nbytes, name, at}. None for none, or one naming no model."""
  if not isinstance(pick, dict) or not isinstance(pick.get('age_s'), (int, float)):
    return None
  ref = pick['ref'] if is_ref(pick.get('ref')) else None
  sha256 = pick['sha256'] if is_sha256(pick.get('sha256')) else None
  if ref is None and sha256 is None:
    return None
  return {'ref': ref, 'sha256': sha256, 'nbytes': pick.get('nbytes'), 'name': pick.get('name'),
          'at': time.time() - float(pick['age_s'])}


class Models:
  def __init__(self, op):
    self.op = op
    # (looked at, the file's (mtime, size), the index it held and its rows)
    self._index_cache: tuple[float, tuple | None, ModelIndex | None, list[dict]] | None = None
    self._refresh_after = 0.0
    self._slot_cache: tuple[float, dict | None, float | None] | None = None
    # SLOT_STATE as this process last read or wrote it
    self._slot_state: dict | None = None
    # the pick of the last hello this process heard, and the owner's record
    # of the last hello any process heard
    self._heard: dict | None | object = _UNHEARD
    self._record_cache: tuple[float, dict | None] | None = None

  # -- the model index --------------------------------------------------------

  def index_path(self) -> Path:
    return self.model_dir() / INDEX_FILE

  def index(self) -> ModelIndex | None:
    """The comma's copy of the model index, None before the first fetch.
    Looked at once per INDEX_TTL and parsed again only when the file
    changed. Never raises."""
    return self._read_index()[0]

  def model_index(self) -> list[dict]:
    """Every model the index lists, {name, ref, oid, size}, in its order. No network."""
    return self._read_index()[1]

  def _read_index(self) -> tuple[ModelIndex | None, list[dict]]:
    now = time.monotonic()
    cache = self._index_cache
    if cache is not None and now - cache[0] < INDEX_TTL:
      return cache[2], cache[3]
    path = self.index_path()
    try:
      st = path.stat()
      seen = (st.st_mtime_ns, st.st_size)
    except OSError:
      seen = None
    if cache is not None and cache[1] == seen:
      # the same file, parsed or not: a copy that does not parse is told of once
      index = cache[2]
    else:
      index = None
      if seen is not None:
        try:
          index = parse_index(json.loads(path.read_text()))
        except (OSError, ValueError, RegistryError):
          self.op.log.exception("jetlink: the comma's model index does not parse")
    self._keep_index(now, seen, index)
    return self._index_cache[2], self._index_cache[3]

  def _keep_index(self, now: float, seen: tuple | None, index: ModelIndex | None) -> None:
    rows = self._index_cache[3] if self._index_cache is not None and self._index_cache[2] is index else \
      [{'name': m.name, 'ref': m.ref, 'oid': m.oid, 'size': m.size} for m in index.models] if index else []
    self._index_cache = (now, seen, index, rows)

  def refresh_index(self) -> ModelIndex:
    """The model index, fetched and kept as the comma's copy, which is left
    alone when nothing changed so no reader parses it again. Raises a
    RegistryError for one that cannot be had or used, and keeps the copy."""
    from jetlink.comma import gadget
    from jetlink.registry.index import fetch_index
    data, index = fetch_index()
    path = self.index_path()
    if gadget.read_record(path) != data:
      gadget.write_record(path, data)
    st = path.stat()
    self._keep_index(time.monotonic(), (st.st_mtime_ns, st.st_size), index)
    return index

  def _row(self, name: str, ref: str | None, sha256: str | None = None) -> dict:
    """A model as a row: its ONNX by its ref, from the index, else by its hash
    and sized from the index. oid or size is None where the index does not know."""
    index = self.index()
    pointer = index.pointer(ref) if index is not None and ref else None
    if pointer is not None:
      return {'name': name, 'ref': ref, 'oid': pointer.oid, 'size': pointer.size}
    return {'name': name, 'ref': ref, 'oid': sha256, 'size': index.size(sha256) if index is not None and sha256 else None}

  def resolve(self, ref: str | None, sha256: str | None) -> tuple[str, int]:
    """A pick's oid and size, from the index; one the comma's copy does not
    know fetches it again, at most once per INDEX_MISS_RETRY. Raises a
    RegistryError for a model the index does not know, or no index at all."""
    if (found := self._identity(ref, sha256)) is None and time.monotonic() >= self._refresh_after:
      self._refresh_after = time.monotonic() + INDEX_MISS_RETRY
      self.refresh_index()
      found = self._identity(ref, sha256)
    if found is None:
      raise RegistryError(f"the model index does not know {(ref or sha256 or 'the model')[:16]}")
    return found

  def _identity(self, ref: str | None, sha256: str | None) -> tuple[str, int] | None:
    row = self._row('', ref, sha256)
    return (row['oid'], int(row['size'])) if row['oid'] and row['size'] else None

  def selected_slot(self) -> dict | None:
    """The big-model slot's pick as {name, ref, sha256}, from the raw param
    rather than a parsed bundle, and memoised with when it was made
    (picked_at): the UI asks every frame. It names its model by the catalog's
    ref, by the ONNX's sha256 (a fork whose catalog knows only that), or by
    both; the one it lacks is None."""
    now = time.monotonic()
    if self._slot_cache is not None and now - self._slot_cache[0] < INDEX_TTL:
      return self._slot_cache[1]
    key = self.op.keys.big_model
    slot = self.op.get(key) if key else None
    slot = slot if isinstance(slot, dict) else {}
    ref = slot['ref'] if isinstance(slot.get('ref'), str) and slot['ref'] else None
    sha256 = slot['sha256'] if is_sha256(slot.get('sha256')) else None
    pick, at = None, None
    if ref or sha256:
      pick = {'name': str(slot.get('displayName') or (ref or sha256)[:10]), 'ref': ref, 'sha256': sha256}
      try:
        at = os.stat(Path(self.op.params_dir()) / key).st_mtime
      except OSError:
        pass
    self._note_slot(pick)
    self._slot_cache = (now, pick, at)
    return pick

  def _note_slot(self, pick: dict | None) -> None:
    """Keep SLOT_STATE in step with the slot: what is in it, and when it was
    emptied. A pick taken out is the default chosen (the model manager's
    Default, here or on sunnylink), a pick like any other, which the hello
    names and a server's older pick must not undo. Read again only when the
    slot no longer matches what this process knows, so per process it costs
    a file read at a change. Never raises."""
    last = {'ref': pick['ref'], 'sha256': pick['sha256']} if pick else None
    state = self._slot_state
    if state is not None and state.get('last') == last:
      return
    path = self.model_dir() / SLOT_STATE
    try:
      state = json.loads(path.read_text())
      state = state if isinstance(state, dict) else {}
    except (OSError, ValueError):
      state = {}
    if state and state.get('last') == last:
      self._slot_state = state
      return
    if last is None and not state:
      # empty with nothing known before it: never picked, as far as we know
      self._slot_state = {'last': None, 'cleared_at': None}
      return
    state = {'last': last, 'cleared_at': time.time() if last is None else None}
    self._slot_state = state
    from jetlink.comma import gadget
    try:
      gadget.write_record(path, state)
    except OSError:
      self.op.log.exception("jetlink: could not keep the big-model slot's state")

  def default_chosen_at(self) -> float | None:
    """When the slot was emptied by this clock, the default chosen; None while
    it holds a pick, or was never filled."""
    if self.selected_slot() is not None or self._slot_state is None:
      return None
    at = self._slot_state.get('cleared_at')
    return float(at) if isinstance(at, (int, float)) else None

  def default_model(self) -> dict | None:
    """What runs with no pick: the index's default, else the first it lists.
    Not the chestnut's default, which is the model in the tree. Before the
    comma has an index, jetlink's own by its ref, which a provisioning run
    resolves by fetching one."""
    if (index := self.index()) is None:
      return self._row(DEFAULT_BIG_MODEL_NAME, DEFAULT_BIG_MODEL_REF)
    models = self.model_index()
    return next((m for m in models if m['ref'] == index.default), models[0] if models else None)

  def default_model_name(self) -> str | None:
    """default_model()'s name without the catalog's trailing build date, the
    form the chestnut's DEFAULT_BIG_MODEL has. Per frame from the UI, off the
    index's cache."""
    model = self.default_model()
    return _TRAILING_DATE.sub('', model['name']) if model else None

  def selected_model(self) -> dict | None:
    """The model picked last, here or on the server: the slot's pick, listed
    or not (the index knows every ref it ever saw), unless the server's last
    hello named a newer one. With neither, default_model()."""
    if (theirs := self._newer_server_pick()) is not None:
      return self._server_row(theirs)
    if (own := self.selected_slot()) is not None:
      return self._own_row(own)
    return self.default_model()

  def _own(self) -> tuple[dict | None, float | None]:
    """Our pick as a row, and when it was made by this clock: the slot's, or
    the default when it was chosen by emptying the slot. (None, None) with
    neither."""
    if (own := self.selected_slot()) is not None:
      return self._own_row(own), self.picked_at()
    if (at := self.default_chosen_at()) is not None and (default := self.default_model()) is not None:
      return default, at
    return None, None

  def _own_row(self, own: dict) -> dict:
    return self._row(own['name'], own['ref'], own['sha256'])

  # -- picks on both ends -----------------------------------------------------
  # A pick is made on either end: in the comma's list (the default included,
  # chosen by emptying the slot), or on the server (its web page, an app's Use
  # Model). Each hello carries the comma's own pick, when it was made by this
  # clock and how long ago, and the server answers with the newer of that and
  # its own (PickStore in JetlinkServer), which it settles by what the comma
  # named before rather than by its clock where it can: a Jetson's clock
  # starts again behind at every power-on. The answer is kept with the rest of
  # the hello (gadget.SERVER, through the owner) along with the pick the hello
  # carried, and selected_model() takes the server's when that is still our
  # pick: the server chose knowing it. So a model picked on the server drives
  # without being picked here too, and one picked here goes to the server at
  # the next hello.

  def picked_at(self) -> float | None:
    """When the slot was last written, by this clock: when the pick was made.
    None with no pick of our own, or no date for it."""
    return self._slot_cache[2] if self.selected_slot() is not None and self._slot_cache else None

  def hello_pick(self) -> dict | None:
    """Our own pick, for the hello: {ref, sha256, nbytes, name, age_s, at},
    its oid and size where the index knows them (no network here), `at` when it
    was made by this clock. None with nothing picked here, or no date for it:
    the server's pick, if any, stands."""
    row, at = self._own()
    if row is None or at is None or not (row['ref'] or row['oid']):
      return None
    return {'ref': row['ref'], 'sha256': row['oid'], 'nbytes': row['size'], 'name': row['name'],
            'age_s': round(max(0.0, time.time() - at), 1), 'at': round(at, 3)}

  def heard(self, hello: dict, asked=_UNTOLD) -> dict:
    """The server's hello with its `pick` dated by this clock (_dated), and
    `asked`, the hello_pick() the hello carried, kept with it: which is how
    the owner keeps it (lending.SERVER_FIELDS) and server_pick() reads it.
    This process goes by it at once, whatever the owner does with it."""
    self._heard = _dated(hello.get('pick'))
    if self._heard is not None and asked is not _UNTOLD:
      self._heard['asked'] = {k: asked.get(k) for k in ('ref', 'sha256', 'at')} if isinstance(asked, dict) else None
    return {**hello, 'pick': self._heard} if self._heard is not None else hello

  def server_pick(self) -> dict | None:
    """The pick the server last answered a hello with, {ref, sha256, nbytes,
    name, at}, or None: a server too old to say, or one with no pick. What
    this process heard last, else the owner's record of the last hello: the
    UI says no hello, and reads it every frame."""
    if self._heard is not _UNHEARD:
      return self._heard
    now = time.monotonic()
    if self._record_cache is None or now - self._record_cache[0] >= INDEX_TTL:
      from jetlink.comma import gadget
      pick = (gadget.read_record(gadget.SERVER) or {}).get('pick')
      self._record_cache = (now, pick if isinstance(pick, dict) and isinstance(pick.get('at'), (int, float)) else None)
    return self._record_cache[1]

  def _newer_server_pick(self) -> dict | None:
    """The server's last pick, when it is another model than ours and was
    picked after it, or we have none. The server settled that when the hello
    carried this pick of ours; a pick made here since that hello is newer.
    An answer kept without the pick it was asked with (an older jetlink) goes
    by its date."""
    theirs = self.server_pick()
    if theirs is None:
      return None
    own, at = self._own()
    if own is None:
      return theirs
    if self._same(theirs, own):
      return None
    if 'asked' in theirs:
      asked = theirs['asked']
      fresh = isinstance(asked, dict) and self._same(asked, own) and isinstance(asked.get('at'), (int, float)) \
        and at is not None and abs(asked['at'] - at) < 1.0
      return theirs if fresh else None
    return None if theirs['at'] <= (at or 0.0) else theirs

  @staticmethod
  def _same(pick: dict, row: dict) -> bool:
    """One model: by the catalog ref where both have one, since two refs can
    share one export, else by the hash. The server's PickStore.sameModel."""
    if pick.get('ref') and row['ref']:
      return pick['ref'] == row['ref']
    return bool(pick.get('sha256')) and pick['sha256'] == row['oid']

  def _server_row(self, pick: dict) -> dict:
    """The server's pick as a row: named as we list it, with the server's oid
    and size, which name the bytes it has."""
    listed = next((m for m in self.model_index() if pick['ref'] and m['ref'] == pick['ref']), None)
    row = self._row(listed['name'] if listed else pick.get('name') or (pick['ref'] or pick['sha256'])[:16],
                    pick['ref'], pick['sha256'])
    if pick['sha256'] and pick.get('nbytes'):
      row['oid'], row['size'] = pick['sha256'], int(pick['nbytes'])
    return row

  def adopt(self) -> None:
    """Make a newer pick from the server ours. With a catalog, ask the model
    manager for it (Keys.select), so the comma's own list shows what drives:
    only a model the catalog lists, which is all the model manager takes; any
    other drives all the same (selected_model). Without one, no model manager
    owns the slot, so the pick is written into it, whatever the model. Never
    raises."""
    try:
      if (theirs := self.server_pick()) is None or self._newer_server_pick() is None:
        return
      model, keys = self._server_row(theirs), self.op.keys
      if keys.catalog is None:
        if keys.big_model:
          self.op.log.warning("jetlink: %s was picked on the server, picking it here too", model['name'])
          slot = {k: v for k, v in (('displayName', model['name']), ('ref', model['ref']), ('sha256', model['oid'])) if v}
          self.op.put(keys.big_model, slot, block=True)
          self._slot_cache = None
        return
      # a download the user asked for that the model manager has not taken
      # yet is left alone: the next hello asks again
      key = keys.select
      if key and model['ref'] and self.op.get(key) is None and model['ref'] in self._catalog_refs():
        self.op.log.warning("jetlink: %s was picked on the server, picking it here too", model['name'])
        self.op.put(key, model['ref'])
        self._slot_cache = None
    except Exception:
      self.op.log.exception("jetlink: could not take the server's pick")

  def _catalog_refs(self) -> set[str]:
    """The refs the model manager's catalog lists: the index's without a
    chestnut fitted (big_catalog), else sunnypilot's."""
    key = self.op.keys.catalog
    bundles = ((self.op.get(key) if key else None) or {}).get('bundles')
    return {b['ref'] for b in bundles if isinstance(b, dict) and is_ref(b.get('ref'))} if isinstance(bundles, list) else set()

  def selected_model_name(self) -> str | None:
    selected = self.selected_model()
    return selected['name'] if selected else None

  def model_dir(self) -> Path:
    """Ours, under the model manager's root: its cache clear removes every file
    it does not recognise and leaves directories alone."""
    return Path(self.op.model_root()) / 'jetlink'

  @staticmethod
  def model_file_name(model: dict) -> str:
    """One file per model, so switching back does not re-download."""
    return f"{model['oid'][:16]}.onnx"

  def model_path(self, model: dict) -> Path:
    """Where a model's ONNX lives, whether or not it is there yet."""
    return self.model_dir() / self.model_file_name(model)

  def shipped_model_path(self) -> Path | None:
    """The chosen large model, if it has been fetched. Keyed on the oid, not
    the in-tree pointer, which moves with upstream syncs; the size is the cheap
    check that the file is the one we mean."""
    model = self.selected_model()
    if model is None or not self.has_file(model):
      return None
    return self.model_path(model)

  def lfs_endpoints(self) -> list[str]:
    """Where a model's bytes are asked for, nearest first: the LFS server this
    checkout's .lfsconfig names (a release ships none), then the ones the Jetson
    asks."""
    from jetlink.registry.lfs import LFS_ENDPOINTS
    out = []
    try:
      for line in (Path(self.op.basedir) / '.lfsconfig').read_text().splitlines():
        key, sep, value = line.strip().partition('=')
        if sep and key.strip() == 'url' and value.strip():
          out.append(value.strip().removesuffix('/'))
          break
    except OSError:
      pass
    return out + [e for e in LFS_ENDPOINTS if e not in out]

  def fetch_shipped_model(self, progress=None, should_stop=None, retrying=None) -> Path | None:
    """Download the chosen large model if it is not here yet; None when nothing
    is chosen. The registry streams it to a .part file and hashes it on the way,
    so only the whole model ever takes the name.

    A transfer that fails is tried again after each of DOWNLOAD_RETRY_DELAYS,
    carrying on from the .part it left; once they are spent the .part outlives
    the run for the next one. Each attempt asks the LFS server again:
    the address it hands out expires. `retrying(error, delay)` hears of each
    wait; a run that is stopped stops waiting."""
    model = self.selected_model()
    if model is None or not model['oid']:
      return None
    dest = self.model_path(model)
    if self.has_file(model):
      return dest
    pointer = Pointer(model['oid'], int(model['size']))
    for delay in (*DOWNLOAD_RETRY_DELAYS, None):
      try:
        return self._fetch_once(pointer, dest, progress, should_stop)
      except NetworkError as e:
        if delay is None:
          raise
        self.op.log.warning("jetlink: download of %s failed (%s), trying again in %.0f s", pointer.oid[:16], e, delay)
        if retrying is not None:
          retrying(e, delay)
        end = time.monotonic() + delay
        while time.monotonic() < end:
          if should_stop is not None and should_stop():
            raise
          time.sleep(min(1.0, end - time.monotonic()))

  def _fetch_once(self, pointer, dest: Path, progress, should_stop) -> Path:
    from jetlink.registry.lfs import lfs_download, lfs_hrefs
    for endpoint, href in lfs_hrefs(self.lfs_endpoints(), pointer):
      self.op.log.warning("jetlink: fetching the large model (%d MB) from %s", pointer.size >> 20, endpoint)
      return lfs_download(href, pointer, dest, progress=progress, should_stop=should_stop)
    raise NetworkError(f"no LFS server has {pointer.oid[:16]}")

  def name_for(self, oid: str) -> str:
    """The index's name for the model with this oid, or the oid's first 16
    characters when it lists none."""
    index = self.index()
    listed = index.by_oid.get(oid) if index is not None else None
    return listed.name if listed is not None else oid[:16]

  def size_for(self, oid: str) -> int | None:
    """The byte count of the ONNX with this oid, as the index knows it."""
    index = self.index()
    return index.size(oid) if index is not None else None

  def has_file(self, model: dict) -> bool:
    """Is this model's ONNX on the comma, whole? Its size is the cheap check."""
    if not model.get('oid') or not model.get('size'):
      return False
    try:
      return self.model_path(model).stat().st_size == model['size']
    except OSError:
      return False

  # -- the model manager's catalog --------------------------------------------

  def big_catalog(self, selector: int) -> dict:
    """The index's models as a catalog in sunnypilot's shape, for the model
    manager's big-model list when no chestnut is fitted: what the comma lists
    is then what the Jetson runs, each with the index's fields, in its folder
    and order. Only `selector` is the fork's (its model manager's
    REQUIRED_JSON_VERSION, which it parses), and no artifacts: a chestnut
    has nothing to download, and an accelerator runs the ONNX. The index is
    fetched again; raises a RegistryError when it cannot be, so the model
    manager keeps the list it has."""
    models = self.refresh_index().models
    return {'bundles': [{'ref': m.ref, 'display_name': m.name, 'short_name': m.short_name or m.ref[:7], 'index': len(models) - i,
                         'is_big': True, 'is_20hz': m.is_20hz, 'generation': m.generation, 'environment': m.environment,
                         'runner': m.runner, 'minimum_selector_version': str(selector), 'build_time': m.build_time,
                         'overrides': {'folder': m.folder}, 'models': []} for i, m in enumerate(models)]}

  def listed_ref(self, ref: str) -> str | None:
    """The ref the index lists a model under: its own, or the newest one with
    the same ONNX (a recompile is listed once). None for a model it does not
    list. No network."""
    index = self.index()
    listed = index.listed(ref) if index is not None and is_ref(ref) else None
    return listed.ref if listed is not None else None

