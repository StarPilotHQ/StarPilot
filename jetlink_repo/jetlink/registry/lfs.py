"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

Getting a large model's ONNX by its oid, the one the model index names
(jetlink.registry.index).

The bytes are on comma's LFS servers. Since openpilot #39206 comma's own LFS
client stores objects in a HuggingFace bucket, read by oid; the older LFS
servers, and comma's model repo for the exports behind a precompiled pkl,
speak the LFS batch protocol, and which one has an object varies with the
model's age, so each is asked in turn.

The zoompilot fork downloads through this module too, asking its own
.lfsconfig endpoint first. No openpilot imports.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import urllib.request
from collections.abc import Callable
from pathlib import Path

from jetlink.registry.index import NetworkError, Pointer, RegistryError, VerifyError

__all__ = ['BUCKET', 'LFS_ENDPOINTS', 'Pointer', 'lfs_download', 'lfs_hrefs', 'lfs_resolve']

log = logging.getLogger('jetlink.registry')

# where comma's LFS client keeps objects since openpilot #39206. A HuggingFace
# bucket is not an LFS server: an object is read by its oid under it (lfs_resolve)
BUCKET = 'https://huggingface.co/buckets/commaai/openpilot-lfs'
DRIVING_MODELS_REPO = 'commaai/openpilot_driving_models'
LFS_ENDPOINTS = (
  BUCKET,
  'https://gitlab.com/commaai/openpilot-lfs.git/info/lfs',      # every object, older and PR-branch models included
  'https://huggingface.co/commaai/openpilot-lfs.git/info/lfs',  # the current ones
  f'https://huggingface.co/{DRIVING_MODELS_REPO}.git/info/lfs',  # the exports behind a precompiled pkl
)
LFS_MEDIA_TYPE = 'application/vnd.git-lfs+json'
CONNECT_TIMEOUT = 30.0
CHUNK = 4 << 20
FREE_SLACK = 64 << 20

ProgressFn = Callable[[float], None]
StopFn = Callable[[], bool]


def _bucket_href(bucket: str, pointer: Pointer, timeout: float, opener) -> str | None:
  """A bucket's address for the object, or None when it does not have it or
  cannot be reached: its first byte is asked for, as cheap as a HEAD."""
  url = f"{bucket}/resolve/sha256/{pointer.oid}"
  try:
    with opener(urllib.request.Request(url, headers={'Range': 'bytes=0-0'}), timeout=timeout) as response:
      response.read(1)
  except (OSError, ValueError) as e:
    log.warning("%s has no %s (%s)", bucket, pointer.oid[:16], e)
    return None
  return url


def lfs_resolve(endpoint: str, pointer: Pointer, timeout: float = CONNECT_TIMEOUT, opener=None) -> str | None:
  """Ask one LFS server for a download href, or None if it does not have it.
  A HuggingFace bucket, comma's (BUCKET) or one a checkout's .lfsconfig
  names, is asked for the object itself.

  A server that is down is not different from a server that lacks the object:
  either way the caller moves to the next one, so nothing raises here.
  """
  opener = opener or urllib.request.urlopen
  if '/buckets/' in endpoint:
    return _bucket_href(endpoint, pointer, timeout, opener)
  body = json.dumps({
    'operation': 'download',
    'transfers': ['basic'],
    'objects': [{'oid': pointer.oid, 'size': pointer.size}],
  }).encode()
  request = urllib.request.Request(f"{endpoint}/objects/batch", data=body, method='POST',
                                   headers={'Accept': LFS_MEDIA_TYPE, 'Content-Type': LFS_MEDIA_TYPE})
  try:
    with opener(request, timeout=timeout) as response:
      payload = json.loads(response.read().decode())
  except (OSError, ValueError) as e:
    log.warning("lfs batch failed at %s: %s", endpoint, e)
    return None

  for obj in (payload.get('objects') or []) if isinstance(payload, dict) else []:
    if not isinstance(obj, dict) or obj.get('oid') != pointer.oid:
      continue
    if 'error' in obj:
      log.warning("%s has no %s (%s)", endpoint, pointer.oid[:16], (obj['error'] or {}).get('message'))
      return None
    href = ((obj.get('actions') or {}).get('download') or {}).get('href')
    if href:
      return str(href)
  return None


def lfs_hrefs(endpoints, pointer: Pointer, timeout: float = CONNECT_TIMEOUT, opener=None):
  """Each of `endpoints` that has the object, in order, with its download href."""
  for endpoint in endpoints:
    href = lfs_resolve(endpoint, pointer, timeout=timeout, opener=opener)
    if href is not None:
      yield endpoint, href


def lfs_download(href: str, pointer: Pointer, dest: Path, progress: ProgressFn | None = None,
                 should_stop: StopFn | None = None, opener=None) -> Path:
  """Stream to a .part file, hashing as we go, and only then take the name.

  A half-written model must never sit where the next start would hand it to a
  backend to build from. A transfer that stalls or is stopped keeps its .part,
  and the next call carries on from it with a Range request: 755 MB over a
  car's connection need not arrive in one go. Only bytes that prove wrong are dropped.
  """
  opener = opener or urllib.request.urlopen
  dest = Path(dest)
  dest.parent.mkdir(parents=True, exist_ok=True)
  part = dest.with_name(dest.name + '.part')
  digest = hashlib.sha256()
  written = _resume_from(part, pointer.size, digest)
  free = shutil.disk_usage(dest.parent).free
  if free < pointer.size - written + FREE_SLACK:
    raise RegistryError(f"need {(pointer.size - written) >> 20} MB more for the model, {free >> 20} MB free")

  # Whole percent only: a gigabyte at 4 MB a chunk would call this a few
  # hundred times and the callback may write a param or a socket line.
  reported = -1
  if written < pointer.size:
    request = urllib.request.Request(href, headers={'Range': f'bytes={written}-'}) if written else href
    try:
      with opener(request, timeout=CONNECT_TIMEOUT) as response:
        if written and response.status != 206:
          # the server ignored the Range and sends the whole object
          log.warning("%s: no partial content, downloading from the start", pointer.oid[:16])
          digest, written = hashlib.sha256(), 0
        with open(part, 'ab' if written else 'wb') as out:
          while True:
            if should_stop is not None and should_stop():
              raise RegistryError('download stopped')
            chunk = response.read(CHUNK)
            if not chunk:
              break
            out.write(chunk)
            digest.update(chunk)
            written += len(chunk)
            if progress is not None and pointer.size:
              percent = int(100 * written / pointer.size)
              if percent != reported:
                reported = percent
                progress(min(1.0, written / pointer.size))
    except RegistryError:
      raise
    except Exception as e:
      raise NetworkError(f"could not download {pointer.oid[:16]}: {e}") from e

  if written != pointer.size:
    part.unlink(missing_ok=True)
    raise VerifyError(f"{pointer.oid[:16]} is {written} bytes, expected {pointer.size}")
  if digest.hexdigest() != pointer.oid:
    part.unlink(missing_ok=True)
    raise VerifyError(f"downloaded bytes hash to {digest.hexdigest()[:16]}, expected {pointer.oid[:16]}")

  part.replace(dest)
  if progress is not None:
    progress(1.0)
  return dest


def _resume_from(part: Path, size: int, digest) -> int:
  """How much of the model an earlier attempt left in `part`, hashed into
  `digest`; 0, with the file gone, for anything that cannot be a prefix."""
  try:
    have = part.stat().st_size
  except OSError:
    return 0
  if have > size:
    part.unlink(missing_ok=True)
    return 0
  buf = bytearray(CHUNK)
  view = memoryview(buf)
  with open(part, 'rb') as f:
    while n := f.readinto(buf):
      digest.update(view[:n])
  return have
