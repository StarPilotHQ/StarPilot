"""Shared loading for the reduced drive logs written by extract.py."""
import json
from collections import defaultdict
from pathlib import Path

MATCHED = ('matchedLimit', 'matchedNoLimit')


def load_routes(root, only=''):
  """{route: rows in time order} from every <route>--<segment>/nav.jsonl under root."""
  segments = defaultdict(list)
  for path in Path(root).glob('*/nav.jsonl'):
    route, segment = path.parent.name.rsplit('--', 1)
    if only in route:
      segments[route].append((int(segment), path))
  routes = {}
  for route in sorted(segments):
    rows = []
    for _, path in sorted(segments[route]):
      rows += [json.loads(line) for line in path.read_text().splitlines() if line]
    rows.sort(key=lambda row: row.get('t', 0))
    if rows:
      routes[route] = rows
  return routes


def is_phone(row):
  return row['src'].startswith('phone:')


def usable_map(frame, now_ns):
  """mapd evidence navigationd would take: a matched road, or navigation-only junctions on an ambiguous fix."""
  return (frame is not None and now_ns - frame['t'] < 1_200_000_000 and frame['ver'] == 2 and
          (frame['st'] in MATCHED or (frame['st'] == 'unknown' and frame['way'] == 0)))


def branch_rows(frame):
  """mapdOut branches in LocalMapCorroborator's row shape."""
  return tuple(((b[1], b[2]), b[3], b[4], b[6], b[0], b[5]) for b in frame['br'])


def phone_gps(row):
  gps = row.get('pg', [])
  return gps if len(gps) == 4 and gps[0] else None


def heading_change(sources, before_ns, after_ns):
  """Degrees turned between two moments (negative left), from the first GPS source that was moving at both."""
  for source in sources:
    a = [bearing for t, bearing, speed in source if before_ns - 3e9 <= t <= before_ns and speed > 2]
    b = [bearing for t, bearing, speed in source if after_ns <= t <= after_ns + 3e9 and speed > 2]
    if a and b:
      return (b[len(b) // 2] - a[len(a) // 2] + 540) % 360 - 180
  return None
