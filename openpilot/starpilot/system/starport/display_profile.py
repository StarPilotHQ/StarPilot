"""Last successfully negotiated projection screen; no receiver identity."""
import fcntl
import json
import os
from pathlib import Path
import tempfile

from openpilot.starpilot.system.starport.identity import DATA_DIR
from openpilot.starpilot.system.starport.projection_geometry import projection_geometry

SCREEN_PATH = DATA_DIR / 'screen.json'
MAX_BYTES = 2048
FIELDS = {'width', 'height', 'margin_width', 'margin_height', 'fps', 'config_index'}


def validate_screen(value):
  if type(value) is not dict or set(value) != FIELDS | {'version'} or type(value['version']) is not int or value['version'] != 1:
    raise ValueError('Invalid projection screen')
  if any(type(value[key]) is not int for key in FIELDS):
    raise ValueError('Invalid projection dimensions')
  if ((value['width'], value['height']) not in ((800, 480), (1280, 720), (1920, 1080), (720, 1280)) or
      value['fps'] not in (30, 60) or not 0 <= value['config_index'] <= 255):
    raise ValueError('Unsupported projection screen')
  projection_geometry(value['width'], value['height'], value['margin_width'], value['margin_height'])
  return dict(value)


def screen_geometry(screen):
  screen = validate_screen(screen)
  return projection_geometry(screen['width'], screen['height'], screen['margin_width'], screen['margin_height'])


def read_screen(path=None):
  from openpilot.starpilot.saved_source import read_saved
  class Source:
    def get_param_path(self, _key): return str(path or SCREEN_PATH)
  raw, readable = read_saved(Source(), 'screen', MAX_BYTES)
  if not readable or raw is None:
    return None
  try:
    def unique(pairs):
      result = {}
      for key, value in pairs:
        if key in result:
          raise ValueError('Duplicate projection screen field')
        result[key] = value
      return result
    return validate_screen(json.loads(raw, object_pairs_hook=unique))
  except (ValueError, TypeError, UnicodeError, RecursionError):
    return None


def record_screen(mode, path=None):
  """Failure is reported to the caller; it must not interrupt projection."""
  data = validate_screen({'version': 1, **{key: mode[key] for key in FIELDS}})
  path = Path(path or SCREEN_PATH)
  path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  lock_fd = os.open(path.parent / '.lock', os.O_CREAT | os.O_RDONLY, 0o775)
  try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return _record_screen(data, path)
  finally:
    os.close(lock_fd)


def _record_screen(data, path):
  fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.screen-')
  try:
    with os.fdopen(fd, 'w') as handle:
      json.dump(data, handle, separators=(',', ':'), allow_nan=False)
      handle.flush()
      os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
  finally:
    if os.path.exists(temporary):
      os.unlink(temporary)
  return data


def read_display_profile(base_dir=None):
  return read_screen(Path(base_dir) / 'screen.json' if base_dir is not None else None)
