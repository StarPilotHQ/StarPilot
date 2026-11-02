"""Stream a local source log without retaining the rest of its events."""

import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

DECOMPRESS = """
import bz2, shutil, sys
with open(sys.argv[1], 'rb') as source:
  if sys.argv[2] == 'zst':
    import zstandard
    reader = zstandard.ZstdDecompressor().stream_reader(source)
  else:
    reader = bz2.BZ2File(source)
  with reader:
    shutil.copyfileobj(reader, sys.stdout.buffer, 65536)
"""


@contextmanager
def local_events(path):
  from openpilot.cereal import log

  path = Path(path)
  with path.open('rb') as source:
    magic = source.read(4)
  codec = 'zst' if path.suffix == '.zst' or magic == b'\x28\xb5\x2f\xfd' else (
    'bz2' if path.suffix == '.bz2' or magic.startswith(b'BZh') else None)
  if codec is None:
    with path.open('rb') as source:
      yield log.Event.read_multiple(source)
    return
  child = subprocess.Popen([sys.executable, '-B', '-c', DECOMPRESS, str(path), codec],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
  try:
    yield log.Event.read_multiple(child.stdout)
  finally:
    child.stdout.close()
    if child.poll() is None:
      child.terminate()
    try:
      child.wait(timeout=5)
    except subprocess.TimeoutExpired:
      child.kill()
      child.wait(timeout=5)
