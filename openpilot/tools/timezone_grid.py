#!/usr/bin/env python3
"""Build timezone_grid.bin from timezone-boundary-builder's timezones.geojson: timezone_grid.py combined.json [out]"""
import json
import lzma
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from openpilot.common.timezone_lookup import COLS, ROWS, MAGIC, RES, DEFAULT_PATH


def polygons(geometry):
  if geometry["type"] == "Polygon":
    return [geometry["coordinates"]]
  return geometry["coordinates"]


def to_px(ring):
  return [((lon + 180.) / RES, (90. - lat) / RES) for lon, lat in ring]


def area(ring):
  xs, ys = np.asarray(ring).T
  return abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1))) / 2


def build(features):
  names = sorted({f["properties"]["tzid"] for f in features})
  index = {name: i + 1 for i, name in enumerate(names)}
  shapes = [(area(poly[0]), index[f["properties"]["tzid"]], poly)
            for f in features for poly in polygons(f["geometry"])]
  # Largest first so enclaves drawn later win over the zone around them.
  shapes.sort(key=lambda s: -s[0])

  img = Image.new("I", (COLS, ROWS), 0)
  draw = ImageDraw.Draw(img)
  for _, zone, poly in shapes:
    exterior = to_px(poly[0])
    draw.polygon(exterior, fill=zone)
    xs, ys = zip(*exterior, strict=True)
    if max(xs) - min(xs) < 3 or max(ys) - min(ys) < 3:
      draw.polygon(exterior, fill=zone, outline=zone)

  grid = np.asarray(img, dtype=np.int32)
  ends, zones, offsets = [], [], [0]
  for row in grid:
    change = np.flatnonzero(np.diff(row)) + 1
    ends.extend(np.append(change, COLS).tolist())
    zones.extend(row[np.insert(change, 0, 0)].tolist())
    offsets.append(len(ends))

  name_blob = "\n".join(names).encode()
  raw = b"".join([
    MAGIC, struct.pack("<HHI", COLS, ROWS, len(name_blob)), name_blob,
    np.asarray(offsets, dtype="<u4").tobytes(),
    np.asarray(ends, dtype="<u2").tobytes(),
    np.asarray(zones, dtype="<u2").tobytes(),
  ])
  return lzma.compress(raw, preset=9 | lzma.PRESET_EXTREME)


if __name__ == "__main__":
  out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PATH
  with open(sys.argv[1]) as f:
    data = build(json.load(f)["features"])
  out.write_bytes(data)
  print(f"wrote {out} ({len(data) / 1e6:.2f} MB)")
