"""Exact previous saved documents, for history only; never startup authority."""

import hashlib
import json
import zlib

from openpilot.starpilot.flm.offroad_cache import AssessmentCache, _pairs, directory_for_params
from openpilot.starpilot.flm.offroad_store import MAX_BYTES

MAX_PACKED = 8 * 1024 * 1024
MAX_UNPACKED = 64 * 1024 * 1024


class SavedHistory:
  def __init__(self, params):
    directory = directory_for_params(params)
    self.directory = directory.with_name(directory.name + '-saved')

  def read(self):
    if not self.directory.exists():
      return {}
    cache = AssessmentCache(self.directory)
    try:
      packed = cache._read('history.z', MAX_PACKED)
    except FileNotFoundError:
      return {}
    decoder = zlib.decompressobj()
    try:
      raw = decoder.decompress(packed, MAX_UNPACKED + 1)
    except zlib.error as error:
      raise ValueError('Saved tune history undecodable') from error
    if len(raw) > MAX_UNPACKED or not decoder.eof or decoder.unused_data:
      raise ValueError('Saved tune history bound or framing changed')
    try:
      document = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError) as error:
      raise ValueError('Saved tune history undecodable') from error
    if (type(document) is not dict or set(document) != {'version', 'snapshots'}
        or type(document['version']) is not int or document['version'] != 1):
      raise ValueError('Saved tune history format changed')
    snapshots = document['snapshots']
    if type(snapshots) is not dict:
      raise ValueError('Saved tune history snapshots required')
    for digest, text in snapshots.items():
      if type(text) is not str or not 0 < len(text.encode()) <= MAX_BYTES or hashlib.sha256(text.encode()).hexdigest() != digest:
        raise ValueError('Saved tune history bytes changed')
    return snapshots

  def preserve(self, raw):
    from openpilot.starpilot.flm.learned_lifecycle import decode

    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
      raise ValueError('Exact saved tune document required')
    decode(raw)
    digest = hashlib.sha256(raw).hexdigest()
    snapshots = self.read()
    if digest not in snapshots:
      snapshots[digest] = raw.decode()
      expanded = json.dumps({'version': 1, 'snapshots': snapshots}, separators=(',', ':'), allow_nan=False).encode()
      if len(expanded) > MAX_UNPACKED:
        raise ValueError('Saved tune history full; existing settings preserved')
      packed = zlib.compress(expanded, 6)
      if len(packed) > MAX_PACKED:
        raise ValueError('Saved tune history full; existing settings preserved')
      AssessmentCache(self.directory)._write('history.z', packed, maximum=MAX_PACKED)
    if self.read().get(digest, '').encode() != raw:
      raise ValueError('Saved tune history verification failed')
    return digest
