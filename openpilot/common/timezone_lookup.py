"""Offline GPS to IANA timezone lookup from a run-length-encoded 0.02 degree grid."""
import lzma
import math
import struct
from array import array
from bisect import bisect_right
from functools import cache
from pathlib import Path

MAGIC = b"TZG1"
COLS, ROWS = 18000, 9000
RES = 360. / COLS
DEFAULT_PATH = Path(__file__).with_name("timezone_grid.bin")


class TimezoneGrid:
  def __init__(self, data: bytes):
    try:
      raw = lzma.decompress(data)
    except lzma.LZMAError as e:
      raise ValueError("invalid compressed timezone grid") from e
    if len(raw) < 12:
      raise ValueError("truncated timezone grid")
    if raw[:4] != MAGIC:
      raise ValueError("bad timezone grid")
    cols, rows, name_len = struct.unpack_from("<HHI", raw, 4)
    if (cols, rows) != (COLS, ROWS):
      raise ValueError("unexpected timezone grid size")
    pos = 12
    self.names = [""] + raw[pos:pos + name_len].decode().split("\n")
    pos += name_len
    if pos + (rows + 1) * 4 > len(raw):
      raise ValueError("truncated timezone offsets")
    self.offsets = array("I", raw[pos:pos + (rows + 1) * 4])
    pos += (rows + 1) * 4
    runs = self.offsets[-1]
    if pos + runs * 4 != len(raw):
      raise ValueError("invalid timezone run data")
    self.ends = array("H", raw[pos:pos + runs * 2])
    self.zones = array("H", raw[pos + runs * 2:pos + runs * 4])

    if self.offsets[0] != 0 or any(a >= b for a, b in zip(self.offsets, self.offsets[1:], strict=False)):
      raise ValueError("invalid timezone offsets")
    if any(z >= len(self.names) for z in self.zones):
      raise ValueError("invalid timezone index")
    for row in range(rows):
      ends = self.ends[self.offsets[row]:self.offsets[row + 1]]
      if not ends or ends[0] == 0 or ends[-1] != COLS or any(a >= b for a, b in zip(ends, ends[1:], strict=False)):
        raise ValueError("invalid timezone row")

  def _cell(self, row: int, col: int) -> str | None:
    if not 0 <= row < ROWS:
      return None
    col %= COLS
    lo, hi = self.offsets[row], self.offsets[row + 1]
    return self.names[self.zones[bisect_right(self.ends, col, lo, hi)]] or None

  def candidates(self, lat: float, lon: float) -> tuple[str | None, set[str]]:
    """Zone at the point, plus every zone within one cell of it."""
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
      return None, set()
    row = min(int((90. - lat) / RES), ROWS - 1)
    col = int((lon + 180.) / RES)
    center = self._cell(row, col)
    near = {self._cell(row + dr, col + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)}
    return center, {z for z in near if z}


@cache
def load_grid(path: Path = DEFAULT_PATH) -> TimezoneGrid:
  return TimezoneGrid(path.read_bytes())


def pick_timezone(grid: TimezoneGrid, lat: float, lon: float, current: str | None) -> str | None:
  """Keep the current zone while it is still plausible near a border."""
  center, near = grid.candidates(lat, lon)
  if current in near:
    return current
  return center
