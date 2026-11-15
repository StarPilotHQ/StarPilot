"""Source-bound provisional symptoms; these spans never supply native roles."""
from bisect import bisect_left, bisect_right
import hashlib

from openpilot.starpilot.flm.offroad_surface import REVISION, ROUTE_NAMES


def observed_sources(windows, timeline, metadata_for):
  from openpilot.starpilot.flm.offroad_processor import _control_source_current

  times = tuple(stamp for stamp, _ in timeline)
  groups = {}
  for window in windows:
    start, end = window.points[0].mono_ns, window.points[-1].mono_ns
    rows = timeline[bisect_left(times, start):bisect_right(times, end)]
    known = [(stamp, control) for stamp, control in rows if control is not None]
    # Use actual source bookends, never synthesize metadata at missing ticks.
    # The complete continuous reader still owns every automatic/native role.
    if not known or known[0][0] - start > 200_000_000 or end - known[-1][0] > 200_000_000:
      continue
    context, baselines = None, {}
    for stamp, control in known:
      if not _control_source_current(control, stamp):
        break
      metadata = metadata_for(control, window.domain)
      values = metadata.pop('route_values')
      revision = metadata.pop('route_revision')
      if revision != REVISION or tuple(name for name, _ in values) != ROUTE_NAMES or context is not None and context != metadata:
        break
      context = metadata
      for name, value in values:
        baselines.setdefault(name, set()).add(value)
    else:
      key = hashlib.sha256(repr(context).encode()).hexdigest()
      group = groups.setdefault(key, {'metadata': context, 'windows': [], 'coordinate_baselines': {}, 'missing_metadata_rows': 0})
      group['windows'].append({
        'identity': window.identity, 'route': window.points[0].route, 'segment': window.points[0].segment,
        'start_ns': start, 'end_ns': end, 'original_metadata_bookends_ns': (known[0][0], known[-1][0]),
      })
      group['missing_metadata_rows'] += len(rows) - len(known)
      for name, values in baselines.items():
        group['coordinate_baselines'].setdefault(name, set()).update(values)
  for group in groups.values():
    group['coordinate_baselines'] = {name: next(iter(values)) for name, values in group['coordinate_baselines'].items() if len(values) == 1}
  return list(groups.values())


def merge_sources(sources):
  """Pool segments with the same recorded setup, preserving unknown coordinates."""
  groups = {}
  for source in sources:
    key = hashlib.sha256(repr(source['metadata']).encode()).hexdigest()
    if key not in groups:
      groups[key] = dict(source, windows=list(source['windows']), coordinate_baselines=dict(source['coordinate_baselines']))
      continue
    group = groups[key]
    if {window['identity'] for window in group['windows']} & {window['identity'] for window in source['windows']}:
      raise ValueError('Original advisory windows cannot be counted twice')
    group['windows'].extend(source['windows'])
    group['missing_metadata_rows'] += source['missing_metadata_rows']
    group['coordinate_baselines'] = {
      name: value for name, value in group['coordinate_baselines'].items() if source['coordinate_baselines'].get(name) == value
    }
  return list(groups.values())
