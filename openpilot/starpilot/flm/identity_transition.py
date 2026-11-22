"""One audited identity-only transition, never a generic source-hash bypass.

For an already identified VIN, registered-device fallback does not change the
controller, source epoch, numeric baseline or settings. Recompute its previous
compatibility key only while every reviewed identity implementation matches.
All other controller/interface/native sources are still hashed normally.
"""
import hashlib
import json
from pathlib import Path
import re

PARENT = '1469d5ba8fec3fab559e3e1eea3a5e69835f049a'
PREFIX = 'openpilot/starpilot/flm/'
REPLACED = frozenset(PREFIX + name for name in ('offroad_runtime.py', 'runtime_adapter_registry.py', 'steering_compatibility.py'))
EXTRA = frozenset(PREFIX + name for name in ('vehicle_identity.py', 'identity_transition.py', 'identity_transition.json'))
REVIEWED = REPLACED | (EXTRA - {PREFIX + 'identity_transition.json'})


def _bytes(path, limit):
  if path.is_symlink() or not path.is_file() or not 0 <= path.stat().st_size <= limit:
    raise ValueError('Unavailable reviewed identity source')
  raw = path.read_bytes()
  if not 0 <= len(raw) <= limit:
    raise ValueError('Identity source size changed')
  return raw


def _unique(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError('Duplicate identity transition field')
    result[key] = value
  return result


def known_vin_revision(root, controller_file, brand_file, names, native):
  """Caller must establish a genuine VIN. A mismatch uses the current full key."""
  from openpilot.starpilot.flm.steering_compatibility import source_paths, REVISION

  root = Path(root).resolve()
  try:
    document = json.loads(_bytes(root / (PREFIX + 'identity_transition.json'), 8192), object_pairs_hook=_unique)
    if (type(document) is not dict or set(document) != {'version', 'purpose', 'source_parent', 'old_sources', 'reviewed_sources'}
        or type(document['version']) is not int or document['version'] != 1
        or document['purpose'] != 'known-vin-registered-device-fallback-only'
        or document['source_parent'] != PARENT):
      return None
    before, reviewed = document['old_sources'], document['reviewed_sources']
    if type(before) is not dict or type(reviewed) is not dict or set(before) != REPLACED or set(reviewed) != REVIEWED:
      return None
    if any(type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None for value in (*before.values(), *reviewed.values())):
      return None
    if any(hashlib.sha256(_bytes(root / path, 2 * 1024 * 1024)).hexdigest() != expected for path, expected in reviewed.items()):
      return None
    paths = source_paths(root, controller_file, brand_file)
    relative = {str(path.relative_to(root)) for path in paths}
    if not 10 <= len(paths) <= 512 or not EXTRA <= relative:
      return None
    result = hashlib.sha256(repr((REVISION, names, native)).encode())
    for path in paths:
      name = str(path.relative_to(root))
      if name in EXTRA:
        continue
      actual = hashlib.sha256(_bytes(path, 2 * 1024 * 1024)).hexdigest()
      result.update(name.encode())
      result.update(bytes.fromhex(before.get(name, actual)))
    return REVISION + ':' + result.hexdigest()
  except (OSError, ValueError, TypeError, KeyError):
    return None
