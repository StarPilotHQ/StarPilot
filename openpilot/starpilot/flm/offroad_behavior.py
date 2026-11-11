"""Observed behavior recommendations, never an automatic trial or approval.

The denominator is input-selected, nonoverlapping recorded sections. Adjacent
windows join before counting; a section is explicitly NOT a distinct maneuver.
Missing original inputs withhold the count rather than improve its hit rate.
"""

import hashlib
from statistics import fmean

from openpilot.starpilot.flm.offroad_coordinate_policy import coordinate_candidates, recorded_observation, _symptom_steps
from openpilot.starpilot.flm.offroad_stable_policy import cadenced_runs, classify_current_stable

SYMPTOMS = {
  'understeer': 'steering response was weaker than requested',
  'oversteer': 'steering response was stronger than requested',
  'late_turn_in': 'steering built too slowly on entry',
  'early_turn_in': 'steering built too quickly on entry',
  'unwind_too_slow': 'steering unwound too slowly',
  'unwind_too_fast': 'steering unwound too quickly',
  'low_speed_unwillingness': 'low-speed steering lagged the request',
  'center_chatter': 'repeated steering oscillation was measured',
}
PHASE_BUCKETS = {
  'steady': ('understeer', 'oversteer'),
  'turn_in': ('late_turn_in', 'early_turn_in', 'low_speed_unwillingness'),
  'unwind': ('unwind_too_slow', 'unwind_too_fast'),
  'center': (),
}


def behavior_candidates(windows, rows, contexts, previous, cohort, *, stable=False, permitted=lambda: True):
  from openpilot.starpilot.flm.offroad_stable_bridge import profile_sections

  if not 1 <= len(windows) <= 24 or len(set(windows)) != len(windows):
    raise ValueError('Bounded distinct behavior observation windows required')
  if rows is None or any(not rows.get(key) for key in windows):
    return []
  band, side, phase = cohort.split(':')
  allowed = ('center_chatter',) if stable else PHASE_BUCKETS.get(phase, ())
  if not allowed:
    return []
  sections, _ = profile_sections(windows, rows, contexts)
  observations, measured, routes = [], [], set()
  for section in sections:
    if not permitted():
      raise ValueError('Parked behavior analysis cancelled')
    # One contiguous source section earns at most one vote per symptom, even
    # if the classifier reports several bursts or the section spans windows.
    key = hashlib.sha256(repr(tuple((s.route, s.segment, s.t) for s in section)).encode()).hexdigest()
    measured.append(key)
    routes.add(section[0].route)
    found = {}
    for run in cadenced_runs(section):
      summaries, _ = classify_current_stable(list(run))
      for summary in summaries:
        if summary['bucket'] not in allowed:
          continue
        for event in summary['events']:
          if event['direction'] != side and summary['bucket'] != 'center_chatter':
            continue
          samples = run[event['startIdx']:event['endIdx'] + 1]
          observation = recorded_observation(summary['bucket'], event['direction'], event['peakScore'], fmean(s.v_ego for s in samples))
          pair = (summary['bucket'], event['direction'])
          if pair not in found or found[pair]['severity'] < observation['severity']:
            found[pair] = observation
    observations.extend((key, observation) for observation in found.values())
  # Require repeated support across sections, not many correlated samples.
  # Directional conflicts are retained in the vote set so they cannot create
  # a deceptively precise coordinate recommendation.
  candidates = coordinate_candidates(observations, previous)
  result = []
  for candidate in candidates:
    votes = set(candidate['original_windows'])
    if len(votes) * 5 < len(measured) * 3:
      continue
    name = candidate['parameter'].removeprefix('stable:')
    supporting = [(key, row) for key, row in observations if key in votes and name in _symptom_steps(row, previous)]
    buckets = {row['bucket'] for _, row in supporting}
    # Each displayed ratio describes one symptom, not a sum of unlike events.
    for bucket in sorted(buckets):
      matched = {key for key, row in supporting if row['bucket'] == bucket}
      if len(matched) < 3 or len(matched) * 5 < len(measured) * 3:
        continue
      speed = {'0': 'low-speed', '1': 'medium-speed', '2': 'higher-speed'}[band]
      condition = f'{speed} {side} ' + {'turn_in': 'turn-entry', 'unwind': 'turn-exit', 'steady': 'curve', 'center': 'center'}[phase]
      evidence = (
        f'{len(matched)} of {len(measured)} retained {condition} sections across {len(routes)} drive(s): {SYMPTOMS[bucket]}. '
        + 'These are recorded sections, not a count of distinct turns. This is a possible adjustment, not a confirmed cause or improvement.'
      )
      result.append(dict(candidate, evidence=evidence, measured_sections=len(measured), matched_sections=len(matched),
                         evidence_drives=len(routes), behavior=bucket, cohort=cohort, whole_windows=len(windows)))
      break
  return result
