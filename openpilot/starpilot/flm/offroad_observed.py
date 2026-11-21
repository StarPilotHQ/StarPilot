"""Source-bound provisional symptoms; these spans never supply native roles."""
from bisect import bisect_left, bisect_right
import hashlib
from statistics import median

from openpilot.starpilot.flm.offroad_surface import current_revision, ROUTE_NAMES


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
      if not current_revision(revision) or tuple(name for name, _ in values) != ROUTE_NAMES or context is not None and context != metadata:
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


def curve_candidates(profiles, previous):
  """Keep the original policy's curve steps only at observed speed points."""
  from openpilot.starpilot.flm.offroad_surface import CURVE_NAMES, CURVE_SPEEDS, route_bounds
  from openpilot.starpilot.flm.offroad_store import MAX_ROUTE_STEP

  votes, symptoms = {}, {}
  for proposed, observations in profiles:
    if proposed is None:
      continue
    for index, name in enumerate(CURVE_NAMES):
      delta = proposed['values'][name] - previous[name]
      if not delta:
        continue
      for window, observation in observations:
        bucket, speed = observation['bucket'], observation['speed_mps']
        if (delta > 0 and bucket not in ('center_chatter', 'notchy_mid_curve')
            or delta < 0 and bucket != 'low_speed_unwillingness'):
          continue
        supported = (
          index == 0 and speed < CURVE_SPEEDS[1] or index == 4 and speed > CURVE_SPEEDS[3]
          or 0 < index < 4 and CURVE_SPEEDS[index - 1] < speed < CURVE_SPEEDS[index + 1]
        )
        if supported:
          votes.setdefault(name, {}).setdefault(window, []).append(delta)
          symptoms.setdefault(name, set()).add((bucket, observation['direction']))
  candidates = []
  for name, windows in votes.items():
    steps = [value for values in windows.values() for value in values]
    if len(windows) < 3 or min(steps) * max(steps) <= 0:
      continue
    delta = median(median(values) for values in windows.values())
    target = round(previous[name] + delta, 12)
    low, high = route_bounds(name)
    if not low <= target <= high or not 1e-12 < abs(target - previous[name]) <= MAX_ROUTE_STEP + 1e-12:
      continue
    candidates.append({
      'parameter': 'stable:' + name, 'delta': target - previous[name], 'values': dict(previous, **{name: target}),
      'original_windows': sorted(windows), 'symptoms': [list(row) for row in sorted(symptoms[name])],
      'coordinate_policy': 'observed_original_threshold_curve_v1', 'automatic_application_allowed': False,
    })
  return candidates
