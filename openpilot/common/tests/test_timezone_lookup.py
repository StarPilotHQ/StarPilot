import lzma
import struct

import pytest

from openpilot.common import time_helpers
from openpilot.common.timezone_lookup import COLS, ROWS, MAGIC, DEFAULT_PATH, TimezoneGrid, load_grid, pick_timezone
from openpilot.tools import timezone_grid


def test_timezone_lookup_regions():
  grid = load_grid()
  for lat, lon, zone in [(41.88, -87.63, "America/Chicago"), (52.13, -106.67, "America/Regina"),
                         (49.1, -116.51, "America/Creston"), (47.56, -52.71, "America/St_Johns"),
                         (53.28, -110.0, "America/Edmonton"), (31.76, -106.49, "America/Denver"),
                         (48.85, 2.35, "Europe/Paris"), (49.61, 6.13, "Europe/Luxembourg"), (60.17, 24.94, "Europe/Helsinki")]:
    assert grid.candidates(lat, lon)[0] == zone
  assert grid.candidates(0., -140.) == (None, set())


def test_pick_timezone_keeps_current_near_border():
  grid = load_grid()
  # Monaco cell borders France: an existing Paris zone is kept, anything else takes the cell's zone
  assert pick_timezone(grid, 43.73, 7.42, "Europe/Paris") == "Europe/Paris"
  assert pick_timezone(grid, 43.73, 7.42, "America/Los_Angeles") == "Europe/Monaco"
  assert pick_timezone(grid, 0., -140., "America/Chicago") is None


def test_timezone_lookup_rejects_invalid_coordinates():
  grid = load_grid()
  for lat, lon in [(float("nan"), 0), (0, float("inf")), (91, 0), (-91, 0), (0, 181), (0, -181)]:
    assert grid.candidates(lat, lon) == (None, set())


def test_grid_rejects_corrupt_data():
  for data in [b"", b"not xz", lzma.compress(b"TZG1"), lzma.compress(struct.pack("<4sHHI", MAGIC, COLS, ROWS, 0))]:
    with pytest.raises(ValueError):
      TimezoneGrid(data)


@pytest.mark.parametrize("background", [False, True])
def test_builder_preserves_holes(monkeypatch, background):
  monkeypatch.setattr(timezone_grid, "COLS", 10)
  monkeypatch.setattr(timezone_grid, "ROWS", 10)
  monkeypatch.setattr(timezone_grid, "RES", 1.)
  def ring(left, top, right, bottom):
    return [(left - 180, 90 - top), (right - 180, 90 - top), (right - 180, 90 - bottom), (left - 180, 90 - bottom)]
  features = [{"properties": {"tzid": "Test/Zone"}, "geometry": {"type": "Polygon", "coordinates": [
    ring(0, 0, 9, 9), ring(3, 3, 6, 6),
  ]}}]
  if background:
    features.append({"properties": {"tzid": "Test/Background"}, "geometry": {"type": "Polygon", "coordinates": [ring(0, 0, 10, 10)]}})
  raw = lzma.decompress(timezone_grid.build(features))
  _, cols, rows, name_len = struct.unpack_from("<4sHHI", raw)
  pos = 12 + name_len
  offsets = struct.unpack_from(f"<{rows + 1}I", raw, pos)
  pos += (rows + 1) * 4
  ends = struct.unpack_from(f"<{offsets[-1]}H", raw, pos)
  zones = struct.unpack_from(f"<{offsets[-1]}H", raw, pos + offsets[-1] * 2)
  assert (cols, rows) == (10, 10)
  assert ends[offsets[4]:offsets[5]] == (3, 7, 10)
  assert zones[offsets[4]:offsets[5]] == ((2, 1, 2) if background else (1, 0, 1))


def test_timezone_refresh_is_throttled(monkeypatch):
  calls = []
  now = iter([0., 1., 300.])
  monkeypatch.setattr(time_helpers, "_next_tzset", 0.)
  monkeypatch.setattr(time_helpers.time, "monotonic", lambda: next(now))
  monkeypatch.setattr(time_helpers.time, "tzset", lambda: calls.append(True))
  for _ in range(3):
    time_helpers.refresh_local_timezone()
  assert len(calls) == 2


@pytest.mark.parametrize("field", ["offset", "end", "zone"])
def test_grid_rejects_invalid_runs(field):
  raw = bytearray(lzma.decompress(DEFAULT_PATH.read_bytes()))
  name_len = struct.unpack_from("<I", raw, 8)[0]
  offsets_pos = 12 + name_len
  runs = struct.unpack_from("<I", raw, offsets_pos + ROWS * 4)[0]
  ends_pos = offsets_pos + (ROWS + 1) * 4
  if field == "offset":
    struct.pack_into("<I", raw, offsets_pos, 1)
  elif field == "end":
    struct.pack_into("<H", raw, ends_pos, 0)
  else:
    struct.pack_into("<H", raw, ends_pos + runs * 2, 65535)
  with pytest.raises(ValueError):
    TimezoneGrid(lzma.compress(raw))
