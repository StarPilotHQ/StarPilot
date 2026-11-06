"""Observed baseline tracking, kept separate from native-qualified windows.

Only the ZERO route map can use categorical context bookends. No permission,
source clock, missing controller input or application observation is filled.
Nonzero-map outcome assessment retains the original continuous evidence rule.
"""

from bisect import bisect_left
from dataclasses import replace
import hashlib
from openpilot.starpilot.flm.offroad_evidence import RouteWindow, whole_windows, window_identity


def stable_proposal_windows(continuous, observed):
  """Use the complete known-zero baseline for proposals, never for permission.

  A surviving continuous cohort must not suppress other observed maneuvers or
  restrict the route analyzer to unusually interruption-free driving. The
  caller still selects separate continuous controller/native check windows.
  Nonzero maps never enter ``observed`` and retain continuous outcome evidence.
  """
  selected = dict(continuous)
  grades = {}
  for context, windows in observed.items():
    if windows:
      selected[context] = windows
      grades[context] = 'bookended_zero_route_tracking'
  return selected, grades


def tracking_proposal_windows(continuous, observed):
  # Two-second statistics use the same known-zero observations as the Stable
  # reader. These children supply no missing controller/native evidence.
  children = {key: [child for parent in parents for child in whole_windows(parent.points)] for key, parents in observed.items()}
  return stable_proposal_windows(continuous, children)


def baseline_windows(points, timeline, metadata_for):
  times = tuple(t for t, _ in timeline)
  result = []

  def nearest(point):
    at = bisect_left(times, point.mono_ns)
    choices = [i for i in (at - 1, at) if 0 <= i < len(timeline)]
    index = min(choices, key=lambda i: abs(times[i] - point.mono_ns)) if choices else None
    if index is None:
      return None
    stamp, control = timeline[index]
    if control is None or max(abs(stamp - point.mono_ns), abs(control.control_ns - point.mono_ns)) > 30_000_000:
      return None
    return stamp, metadata_for(control, point.domain)

  for window in whole_windows(points, duration_ns=6_000_000_000):
    first, last = nearest(window.points[0]), nearest(window.points[-1])
    if first is None or last is None or first[1] != last[1]:
      continue
    metadata = first[1]
    if any(v for _, v in metadata['route_values']):
      continue
    known = [
      metadata_for(control, window.domain)
      for stamp, control in timeline
      if control is not None and window.points[0].mono_ns <= stamp <= window.points[-1].mono_ns
    ]
    if not known or any(value != metadata for value in known):
      continue
    comparison = dict(metadata)
    comparison.pop('route_values')
    comparison.pop('route_revision')
    key = hashlib.sha256(repr(comparison).encode()).hexdigest()
    original = tuple(replace(p, context_key=key) for p in window.points)
    result.append((metadata, RouteWindow(window_identity(original), original), (first[0], last[0])))
  return tuple(result)
