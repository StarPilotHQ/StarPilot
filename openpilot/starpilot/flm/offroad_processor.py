"""Completed-drive tracking analysis for the parked backend, with original provenance."""

from dataclasses import asdict, dataclass, field, replace
from bisect import bisect_left
import hashlib

from openpilot.starpilot.flm.offroad_analysis import analyze, MAX_WINDOWS, cohort, _spread, MAX_COHORT_WINDOWS
from openpilot.starpilot.flm.offroad_baseline_windows import baseline_windows
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES as AMPLITUDE_NAMES
from openpilot.starpilot.flm.offroad_residual import analyze_stable_windows
from openpilot.starpilot.flm.offroad_evidence import read_point, whole_windows, eligible_points, tracking_metrics
from openpilot.starpilot.flm.offroad_route import latest_route, walk_completed_route, RouteReceipt, SelectedSourceReceipt
from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest


@dataclass(frozen=True)
class CompletedAssessment:
  receipt: RouteReceipt
  groups: tuple
  counts: dict
  cp: object = field(repr=False, compare=False)
  # Legacy diagnostic slot. Actual qualification rereads every preselected
  # original train/held-out span independently; no diagnostic probe is consumed.
  controller_windows: tuple = field(repr=False, compare=False)
  source_inventory: dict = field(repr=False, compare=False)
  quality_stop_markers: tuple = ()
  diagnostic_groups: tuple = ()
  stable_groups: tuple = ()
  recorded_tracking: dict | None = None
  evidence_sources: dict = field(default_factory=dict, repr=False, compare=False)
  single_drive: object = field(default=None, repr=False, compare=False)

  def report(self):
    return {
      'version': 1,
      'route': asdict(self.receipt),
      'groups': list(self.groups),
      'counts': dict(self.counts),
      'automatically_saved': False,
      'road_improvement_confirmed': False,
      'quality_stop_markers': list(self.quality_stop_markers),
      'diagnostic_groups': list(self.diagnostic_groups),
      'stable_groups': list(self.stable_groups),
      'recorded_tracking': self.recorded_tracking,
      'evidence_sources': [asdict(source.receipt) for _, source in sorted(self.evidence_sources.items())],
      'source_role': (
        'Input-selected original drive sections; not a whole-drive assessment or live permission.'
        if type(self.receipt) is SelectedSourceReceipt
        else 'Historical diagnostic alignment, not a statement of actual Controls consumption or current permission.'
      ),
    }


def window_demand(window):
  import math

  points = window.points
  rms = math.sqrt(sum(p.desired**2 for p in points) / len(points))
  return {
    'domain': window.domain,
    'duration_ns': points[-1].mono_ns - points[0].mono_ns,
    'speed_low': min(p.speed_mps for p in points),
    'speed_high': max(p.speed_mps for p in points),
    'goal_rms': rms,
    'goal_shape': [
      sum(p.desired for p in points[i * len(points) // 8 : (i + 1) * len(points) // 8]) / len(points[i * len(points) // 8 : (i + 1) * len(points) // 8]) / rms
      if rms > 0
      else 0.0
      for i in range(8)
    ],
  }


def _separate_qualification(report, continuous, *, proposal_windows=None, route_order=None, role_windows=None):
  qualification = {}
  spans = {} if proposal_windows is None else {w.identity: w for w in proposal_windows}
  for c in report['cohorts']:
    native = [w for w in continuous if cohort(w) == c['cohort']]
    ordered = sorted(native, key=lambda w: (w.points[0].segment, w.points[0].mono_ns))
    if route_order is not None and role_windows is not None and all(role in c for role in ('training', 'heldout')):
      owners = {w.identity: w.points[0].route for w in role_windows}
      routes = {role: {owners[key] for key in c[role]['windows']} for role in ('training', 'heldout')}
      if routes['training'].isdisjoint(routes['heldout']):
        c['qualification_roles'] = {}
        for role in ('training', 'heldout'):
          chosen = _spread([w for w in ordered if w.points[0].route in routes[role]], MAX_COHORT_WINDOWS // 2)
          c['qualification_roles'][role] = {'windows': tuple(w.identity for w in chosen)}
          qualification.update((w.identity, w) for w in chosen)
        continue
    if proposal_windows is None:
      from openpilot.starpilot.flm.offroad_history import partition_windows

      train, heldout = partition_windows(ordered, route_order)
      c['qualification_roles'] = {role: {'windows': tuple(w.identity for w in rows)} for role, rows in (('training', train), ('heldout', heldout))}
      qualification.update((w.identity, w) for w in (*train, *heldout))
    elif all(role in c for role in ('training', 'heldout')):
      # A native check must stay in the SAME original data role as the
      # corresponding profile. Joining observations never joins holdout data.
      c['qualification_roles'] = {}
      routes = {role: {spans[key].points[0].route for key in c[role]['windows']} for role in ('training', 'heldout')}
      if routes['training'].isdisjoint(routes['heldout']):
        for role in ('training', 'heldout'):
          chosen = _spread([w for w in ordered if w.points[0].route in routes[role]], MAX_COHORT_WINDOWS // 2)
          c['qualification_roles'][role] = {'windows': tuple(w.identity for w in chosen)}
          qualification.update((w.identity, w) for w in chosen)
        continue
      training_end = max((spans[key].points[-1].segment, spans[key].points[-1].mono_ns) for key in c['training']['windows'])
      heldout_start = min((spans[key].points[0].segment, spans[key].points[0].mono_ns) for key in c['heldout']['windows'])
      if training_end >= heldout_start:
        raise ValueError('Original Stable profile roles must not overlap')
      for role in ('training', 'heldout'):
        chosen = _spread([
          w for w in ordered if (
            (w.points[-1].segment, w.points[-1].mono_ns) <= training_end if role == 'training'
            else (w.points[0].segment, w.points[0].mono_ns) >= heldout_start
          )
        ], MAX_COHORT_WINDOWS // 2)
        c['qualification_roles'][role] = {'windows': tuple(w.identity for w in chosen)}
        qualification.update((w.identity, w) for w in chosen)
  report['qualification_original_windows'] = [
    {
      'identity': w.identity,
      'route': w.points[0].route,
      'segment': w.points[0].segment,
      'start_ns': w.points[0].mono_ns,
      'end_ns': w.points[-1].mono_ns,
      'metrics': tracking_metrics(w.points),
      'demand': window_demand(w),
    }
    for w in qualification.values()
  ]


def _recorded_tracking(windows):
  """Measured route summary and one input-ordered window, never a trial result."""
  import math

  ordered = sorted((w for rows in windows.values() for w in rows), key=lambda w: (w.points[0].segment, w.points[0].mono_ns))
  if not ordered or len({w.domain for w in ordered}) != 1:
    return None
  samples = sum(len(w.points) for w in ordered)
  squared_error = sum((p.desired - p.actual) ** 2 for w in ordered for p in w.points)
  first = ordered[0]
  return {
    'version': 1,
    'domain': first.domain,
    'samples': samples,
    'windows': len(ordered),
    'error_rms': math.sqrt(squared_error / samples),
    'route': first.points[0].route,
    'segment': first.points[0].segment,
    'source_start_ns': first.points[0].mono_ns,
    'source_end_ns': first.points[-1].mono_ns,
    'points': [[p.mono_ns, p.desired, p.actual] for p in _spread(first.points, 64)],
    'sampling': 'input_ordered_original_endpoints_no_interpolation',
  }


def _control_source_current(control, stamp):
  return bool(
    control is not None
    and abs(control.control_ns - stamp) <= 30_000_000
    and control.enabled
    and control.active
    and control.valid
    and not (control.manual or control.override or control.fault or control.saturated)
    and 0 < control.state_ns <= control.control_ns <= control.state_ns + 100_000_000
    and 0 < control.source_receipt_ns <= control.control_ns <= control.source_receipt_ns + 500_000_000
    and (
      not control.native_required
      or control.native_allowed
      and 0 < control.native_observed_ns <= control.control_ns <= control.native_expires_ns <= control.native_observed_ns + 200_000_000
    )
  )


def _control_eligible(control, stamp):
  return _control_source_current(control, stamp) and not control.limited


def _control_reconstructible(control, stamp):
  if not _control_source_current(control, stamp):
    return False
  if not control.limited:
    return True
  inputs = dict(getattr(control, 'replay_inputs', ()))
  from openpilot.starpilot.flm.route_recording import SCHEMAS

  schema = getattr(control, 'stage_schema_revision', '')
  return bool(SCHEMAS.get(schema, schema) == 'torque-selected-stage-snapshot-v1' and inputs.get('active') == 1.0 and inputs.get('safety_limited') in (0.0, 1.0))


def continuous_control_window(window, timeline, metadata_for, expected):
  """Check ALL original envelopes, including those between tracking samples."""
  start, end = window.points[0].mono_ns, window.points[-1].mono_ns
  rows = [(stamp, control) for stamp, control in timeline if start - 30_000_000 <= stamp <= end + 30_000_000]
  return bool(
    rows
    and rows[0][0] <= start
    and rows[-1][0] >= end
    and all(b[0] - a[0] <= 50_000_000 for a, b in zip(rows, rows[1:], strict=False))
    and all(_control_reconstructible(control, stamp) and metadata_for(control, window.domain) == expected for stamp, control in rows)
  )


def baseline_control_windows(window, timeline, metadata_for, expected):
  """Check the exact observed children; a second clock grid changes maneuvers."""
  if any(value for _, value in expected['route_values']):
    return ()
  return tuple(child for child in whole_windows(window.points) if continuous_control_window(child, timeline, metadata_for, expected))


@dataclass
class RecordedInputs:
  receipt: RouteReceipt
  state: dict
  windows: dict
  stable_windows: dict
  baseline_tracking: dict
  bookends: dict
  raw_windows: dict
  context_metadata: dict
  counts: dict
  quality_stops: list


def collect_route_inputs(root, route, *, permitted, walker=None):
  from openpilot.starpilot.flm.offroad_control_metadata import read_control_metadata as read_control

  state = {'cp': None, 'cp_sha': None, 'context': None}
  windows = {}
  stable_windows = {}
  baseline_tracking = {}
  baseline_qualified = {}
  bookends = {}
  raw_windows = {}
  context_metadata = {}
  counts = {
    'recorded_lateral_request_rows': 0,
    'active_lateral_request_rows': 0,
    'invalid_lateral_request_rows': 0,
    'recorded_tracking': 0,
    'joined_tracking': 0,
    'missing_join': 0,
    'invalid_control_metadata': 0,
    'original_control_metadata_rows': 0,
    'original_applied_envelopes': 0,
    'controller_fit_refusals': 0,
    'omitted_control_metadata': 0,
    'whole_control_window_refusals': 0,
  }
  quality_stops = []

  def metadata_for(control, domain):
    return {
      'cp_sha256': state['cp_sha'],
      'controller_mode': control.controller_mode,
      'controller_revision': control.controller_revision,
      'baseline_revision': control.baseline_revision,
      'domain': domain,
      'baseline_values': control.baseline_values,
      'vehicle_model_anchor': control.vehicle_model_anchor,
      'route_scope': getattr(control, 'route_configuration_scope', ''),
      'route_revision': getattr(control, 'route_effect_revision', ''),
      'route_values': getattr(control, 'route_applied_coordinates', ()),
    }

  def consume(segment, events):
    companions = {}
    rows = {}
    controls = []
    applied_count = 0
    timeline = []
    point_count = 0
    for index, event in enumerate(events):
      if index % 128 == 0 and not permitted():
        raise ValueError('Parked analysis cancelled')
      kind = event.which()
      if kind == 'carControl':
        counts['recorded_lateral_request_rows'] += 1
        counts['active_lateral_request_rows'] += bool(event.carControl.latActive)
        counts['invalid_lateral_request_rows'] += not event.valid
      if kind == 'carParams':
        # Own the actual CP reader independently of this segment's byte buffer.
        cp = event.carParams.as_builder().as_reader()
        actual_cp_sha = car_params_digest(cp)
        if state['cp_sha'] is not None and actual_cp_sha != state['cp_sha']:
          raise ValueError('Original vehicle changed')
        state['cp'] = cp
        state['cp_sha'] = actual_cp_sha
      elif kind in ('carState', 'carControl', 'carOutput'):
        companions[kind] = event
      elif kind == 'starpilotLateralState':
        timeline.append((int(event.logMonoTime), None))
        if event.valid and event.starpilotLateralState.learningEvidence.version == 1:
          try:
            control = read_control(event)
            state['context'] = (control.controller_mode, control.controller_revision, control.baseline_revision)
            controls.append(control)
            timeline[-1] = (int(event.logMonoTime), control)
            counts['original_control_metadata_rows'] += 1
          except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            counts['invalid_control_metadata'] += 1
        else:
          counts['omitted_control_metadata'] += 1
      elif kind == 'starpilotCarState':
        if event.valid and event.starpilotCarState.appliedEvidence.version == 1:
          applied_count += 1
          counts['original_applied_envelopes'] += 1
          # Full delivery projections are validated by the unchanged selected-window native reader, not statistics.
      elif kind == 'autoSteeringLearning' and event.valid:
        reason = str(event.autoSteeringLearning.learning.reason)
        if reason.startswith('route_quality_stop:') and reason not in [r['reason'] for r in quality_stops]:
          if len(quality_stops) >= 8:
            raise ValueError('Route quality marker bound')
          quality_stops.append({'original_ns': int(event.logMonoTime), 'reason': reason})
      elif kind == 'controlsState':
        counts['recorded_tracking'] += 1
        if state['cp'] is None:
          counts['missing_join'] += 1
          continue
        subtype = event.controlsState.lateralControlState.which()
        context = state['context']
        if subtype == 'pidState' and state['cp'].lateralTuning.which() == 'pid':
          context = ('native', 'original_revision_unrecorded', 'original_baseline_unrecorded') if context is None else context
        if context is None:
          context = ('original_mode_unrecorded', 'original_revision_unrecorded', 'original_baseline_unrecorded')
        key = hashlib.sha256(repr((state['cp_sha'], subtype, context)).encode()).hexdigest()
        point = read_point(event, companions, route=route['routeId'], segment=segment, context_key=key)
        if point is None:
          counts['missing_join'] += 1
          continue
        counts['joined_tracking'] += 1
        groups = rows.setdefault(key, [])
        if not groups or point.mono_ns <= groups[-1][-1].mono_ns:
          groups.append([])
        groups[-1].append(point)
        point_count += 1
        if point_count > 60000:
          raise ValueError('Original segment tracking bound exceeded')
      if len(controls) > 12000 or applied_count > 12000:
        raise ValueError('Original segment controller input bound exceeded')
    if any(b.control_ns <= a.control_ns for a, b in zip(controls, controls[1:], strict=False)):
      raise ValueError('Original controller metadata order changed')
    if any(b[0] <= a[0] for a, b in zip(timeline, timeline[1:], strict=False)):
      raise ValueError('Original controller envelope order changed')
    times = tuple(header for header, _ in timeline)
    for key, groups in rows.items():
      for points in groups:
        raw_windows.setdefault(key, []).extend(whole_windows(tuple(points)))
        for metadata, window, stamps in baseline_windows(tuple(points), timeline, metadata_for):
          context = hashlib.sha256(repr(metadata).encode()).hexdigest()
          context_metadata[context] = metadata
          baseline_tracking.setdefault(context, []).append(window)
          baseline_qualified.setdefault(context, []).extend(baseline_control_windows(window, timeline, metadata_for, metadata))
          bookends[window.identity] = stamps
        # Apply override buffers to ALL original rows before metadata eligibility
        # can remove any override. Match closest original metadata, never closest
        # passing metadata, and never interpolate missing controller ticks.
        selected = {}
        run_context = None
        eligible = {p.mono_ns for p in eligible_points(tuple(points))}
        for point in points:
          if point.mono_ns not in eligible:
            run_context = None
            continue
          at = bisect_left(times, point.mono_ns)
          candidates = tuple(i for i in (at - 1, at) if 0 <= i < len(timeline))
          nearest = min(candidates, key=lambda i: abs(times[i] - point.mono_ns)) if candidates else None
          control = None if nearest is None else timeline[nearest][1]
          okay = _control_reconstructible(control, point.mono_ns)
          if not okay:
            counts['controller_fit_refusals'] += 1
            run_context = None
            continue
          # Exact original recorded scope/actual native baseline, not a rebound
          # live identity. Old routes without new metadata retain their old key.
          metadata = metadata_for(control, point.domain)
          comparison = dict(metadata)
          comparison.pop('route_values')
          comparison.pop('route_revision')
          context = hashlib.sha256(repr(metadata).encode()).hexdigest()
          comparison_key = hashlib.sha256(repr(comparison).encode()).hexdigest()
          context_metadata[context] = metadata
          if run_context != context:
            selected.setdefault(context, []).append([])
          selected[context][-1].append(replace(point, context_key=comparison_key))
          run_context = context
        for context, runs in selected.items():
          for qualified in runs:
            for duration, destination in ((2_000_000_000, windows), (6_000_000_000, stable_windows)):
              for window in whole_windows(tuple(qualified), duration_ns=duration):
                if continuous_control_window(window, timeline, metadata_for, context_metadata[context]):
                  destination.setdefault(context, []).append(window)
                else:
                  counts['whole_control_window_refusals'] += 1

    # Known-zero proposals and their controller checks use the same original
    # endpoints. Every intervening envelope still has to pass independently.
    for context in baseline_tracking:
      # Keep the published collection separate from the next segment's
      # continuous-window appends; aliasing duplicates original identities.
      windows[context] = list(baseline_qualified[context])
    if sum(map(len, windows.values())) + sum(map(len, stable_windows.values())) + sum(map(len, baseline_tracking.values())) > MAX_WINDOWS:
      raise ValueError('Whole-drive tracking window bound exceeded')

  receipt = (walk_completed_route if walker is None else walker)(root, route, permitted=permitted, consume=consume)
  counts['original_segments_reviewed'] = len(getattr(receipt, 'decoded_segments', receipt.segments))
  counts['original_segments_available'] = len(receipt.segments)
  return RecordedInputs(receipt, state, windows, stable_windows, baseline_tracking, bookends, raw_windows, context_metadata, counts, quality_stops)


def collect_latest_steering_inputs(params, root, *, permitted, selected_route=None):
  route = latest_route(params, root, permitted=permitted) if selected_route is None else selected_route
  import time
  from openpilot.starpilot.flm.offroad_route import MAX_ANALYSIS_SECONDS, MAX_SEGMENTS, LOCAL_ID
  from openpilot.starpilot.galaxy.drive_history import DriveHistory

  started = time.monotonic()

  def allowed():
    return permitted() and time.monotonic() - started <= MAX_ANALYSIS_SECONDS

  def walk(actual_root, original_route, *, permitted, consume):
    if len(original_route.get('segments', ())) <= 30:
      return walk_completed_route(actual_root, original_route, permitted=permitted, consume=consume)
    from openpilot.starpilot.flm.offroad_prior_inputs import _selected_sources

    return _selected_sources(actual_root, original_route, permitted=permitted, consume=consume, cp_sha256=None)

  collected = collect_route_inputs(root, route, permitted=allowed, walker=walk)
  current_cp = collected.receipt.cp_sha256
  skipped = 0

  def inactive(inputs):
    c = inputs.counts
    return (
      c['recorded_lateral_request_rows'] >= 40 and c['active_lateral_request_rows'] == 0 and c['invalid_lateral_request_rows'] == 0 and not inputs.quality_stops
    )

  if selected_route is None and inactive(collected) and LOCAL_ID.fullmatch(route['routeId']):
    snapshot = DriveHistory(root, max_segments_per_route=MAX_SEGMENTS).snapshot()
    if snapshot['scanIncomplete']:
      raise ValueError('Latest steering drive inventory incomplete')
    counter = int(route['routeId'].split('--')[0], 16)
    candidates = []
    for candidate in snapshot['routes']:
      if LOCAL_ID.fullmatch(candidate['routeId']):
        distance = (counter - int(candidate['routeId'].split('--')[0], 16)) % 2**32
        if 1 <= distance <= 3 and candidate['segments'] and all(s['files']['rlog'] for s in candidate['segments']):
          candidates.append((distance, candidate['routeId'], candidate))
    for _, _, candidate in sorted(candidates):
      earlier = collect_route_inputs(root, candidate, permitted=allowed, walker=walk)
      if earlier.receipt.cp_sha256 != current_cp:
        continue
      skipped += 1
      if not inactive(earlier):
        route, collected = candidate, earlier
        collected.counts['inactive_recordings_skipped'] = skipped
        break
  return route, collected


def analyze_latest(params, root, previous, *, permitted, selected_route=None, evidence_bank=None, history_identity=None):
  if selected_route is None:
    route, collected = collect_latest_steering_inputs(params, root, permitted=permitted)
  else:
    route, collected = collect_latest_steering_inputs(params, root, permitted=permitted, selected_route=selected_route)
  single = _assess_inputs(route, collected, previous, permitted=permitted)
  if evidence_bank is None:
    return single
  if not callable(history_identity):
    raise ValueError('Current source/settings history identity required')
  from openpilot.starpilot.flm.offroad_history import combine

  identity = history_identity(collected.state['cp'])
  history = evidence_bank.retain(collected, route, identity, permitted=permitted)
  pooled, sources, order = combine(collected, route, history)
  result = _assess_inputs(route, pooled, previous, permitted=permitted, sources=sources, route_order=order)
  return replace(result, single_drive=single, recorded_tracking=single.recorded_tracking)


def _assess_inputs(route, collected, previous, *, permitted, sources=None, route_order=None):
  sources = {} if sources is None else sources
  comparisons = {}
  if len(sources) > 1:
    from openpilot.starpilot.flm.offroad_history import statistical_inputs

    collected, comparisons, _ = statistical_inputs(collected)
  receipt, state = collected.receipt, collected.state
  if type(receipt) not in (RouteReceipt, SelectedSourceReceipt):
    raise ValueError('An original whole-drive or explicit input-selected receipt is required')
  windows, stable_windows, baseline_tracking = collected.windows, collected.stable_windows, collected.baseline_tracking
  bookends, raw_windows, context_metadata = collected.bookends, collected.raw_windows, collected.context_metadata
  counts, quality_stops = collected.counts, collected.quality_stops
  if sources:
    driven = {w.points[0].route for kind in (windows, stable_windows, baseline_tracking) for rows in kind.values() for w in rows}
    counts = dict(counts, evidence_drives=len(driven), evidence_windows=sum(map(len, windows.values())))
  if not permitted():
    raise ValueError('Parked authority lost after analysis')
  from openpilot.starpilot.flm.offroad_baseline_windows import stable_proposal_windows, tracking_proposal_windows

  proposal_windows, proposal_grades = tracking_proposal_windows(windows, baseline_tracking)
  stable_windows, stable_grades = stable_proposal_windows(stable_windows, baseline_tracking)
  if sum(map(len, proposal_windows.values())) + sum(map(len, stable_windows.values())) > MAX_WINDOWS:
    raise ValueError('Whole-drive proposal window bound exceeded')
  counts['qualified_whole_windows'] = sum(map(len, windows.values()))
  counts['proposal_tracking_whole_windows'] = sum(map(len, proposal_windows.values()))
  prior = previous(state['cp']) if callable(previous) else previous
  amplitude_prior = {name: prior[name] for name in AMPLITUDE_NAMES}
  reports = []
  for key, values in sorted(proposal_windows.items()):
    if not values:
      continue
    report = analyze(tuple(values), amplitude_prior, permitted=permitted, route_order=route_order, comparison=comparisons.get(key))
    if key in proposal_grades:
      report['tracking_source_grade'] = proposal_grades[key]
      _separate_qualification(report, windows.get(key, ()), route_order=route_order, role_windows=values)
    selected_ids = {wid for cohort_row in report['cohorts'] for role in ('training', 'heldout') for wid in cohort_row.get(role, {}).get('windows', ())}
    report.update(
      original_context_sha256=key,
      original_metadata=context_metadata[key],
      selected_original_windows=[
        {
          'identity': w.identity,
          'route': w.points[0].route,
          'segment': w.points[0].segment,
          'start_ns': w.points[0].mono_ns,
          'end_ns': w.points[-1].mono_ns,
          'metrics': tracking_metrics(w.points),
          'demand': window_demand(w),
        }
        for w in values
        if w.identity in selected_ids
      ],
    )
    incomplete = {c['cohort'] for c in report['cohorts'] if not all(role in c for role in ('training', 'heldout'))}
    report['observed_original_windows'] = [
      {
        'identity': w.identity, 'route': w.points[0].route, 'cohort': condition,
        'segment': w.points[0].segment, 'start_ns': w.points[0].mono_ns, 'end_ns': w.points[-1].mono_ns,
      }
      for condition in sorted(incomplete)
      for w in _spread([w for w in values if cohort(w) == condition], MAX_COHORT_WINDOWS // 2)
    ]
    if key in comparisons:
      from openpilot.starpilot.flm.offroad_history import bind_original_contexts

      bind_original_contexts(report, [*values, *windows.get(key, ())], comparisons[key])
    reports.append(report)
  reports = tuple(reports)
  stable = []

  def gross_reference(window):
    # Choose by INPUT only. No error or outcome selects a two-second child.
    target = cohort(window)
    for child in whole_windows(window.points):
      if cohort(child) == target:
        return {
          'identity': child.identity,
          'route': child.points[0].route,
          'parent_identity': window.identity,
          'segment': child.points[0].segment,
          'start_ns': child.points[0].mono_ns,
          'end_ns': child.points[-1].mono_ns,
          'metrics': tracking_metrics(child.points),
          'demand': window_demand(child),
        }
    return None

  for key, values in sorted(stable_windows.items()):
    if not values:
      continue
    report = analyze_stable_windows(tuple(values), permitted=permitted, route_order=route_order, comparison=comparisons.get(key))
    grade = stable_grades.get(key, 'continuous_original_control_metadata')
    report['tracking_source_grade'] = grade
    if grade == 'bookended_zero_route_tracking':
      _separate_qualification(report, windows.get(key, ()), proposal_windows=values, route_order=route_order)
    selected = {wid for c in report['cohorts'] for role in ('training', 'heldout') for wid in c.get(role, {}).get('windows', ())}
    report.update(
      original_context_sha256=key,
      original_metadata=context_metadata[key],
      selected_original_windows=[
        {
          'identity': w.identity,
          'route': w.points[0].route,
          'segment': w.points[0].segment,
          'start_ns': w.points[0].mono_ns,
          'end_ns': w.points[-1].mono_ns,
          'metrics': tracking_metrics(w.points),
          'demand': window_demand(w),
          'gross_reference': gross_reference(w),
          'categorical_bookends_original_ns': bookends.get(w.identity, ()),
        }
        for w in values
        if w.identity in selected
      ],
    )
    if key in comparisons:
      from openpilot.starpilot.flm.offroad_history import bind_original_contexts

      bind_original_contexts(report, [*values, *windows.get(key, ())], comparisons[key])
    stable.append(report)
  counts['stable_qualified_whole_windows'] = sum(map(len, stable_windows.values()))
  diagnostic = []
  for key, values in sorted(raw_windows.items()):
    if not values:
      continue
    diagnostic.append(
      {
        'original_tracking_context_sha256': key,
        **analyze(tuple(values), amplitude_prior, permitted=permitted),
        'qualification': 'recorded_tracking_only_not_controller_or_native_qualification',
        'automatic_application_allowed': False,
      }
    )
  counts['diagnostic_whole_windows'] = sum(map(len, raw_windows.values()))
  return CompletedAssessment(
    receipt, reports, counts, state['cp'], (), route, tuple(quality_stops), tuple(diagnostic), tuple(stable),
    _recorded_tracking(proposal_windows), sources,
  )

