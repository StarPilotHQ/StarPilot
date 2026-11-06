"""Completed-drive tracking analysis for the parked backend, with original provenance."""

from dataclasses import asdict, dataclass, field, replace
from bisect import bisect_left
import hashlib

from openpilot.starpilot.flm.offroad_analysis import analyze, MAX_WINDOWS, cohort, _spread, MAX_COHORT_WINDOWS
from openpilot.starpilot.flm.offroad_baseline_windows import baseline_windows
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES as AMPLITUDE_NAMES
from openpilot.starpilot.flm.offroad_residual import analyze_stable_windows
from openpilot.starpilot.flm.offroad_evidence import read_point, whole_windows, eligible_points, tracking_metrics
from openpilot.starpilot.flm.offroad_route import latest_route, walk_completed_route, RouteReceipt
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
      'source_role': 'Historical diagnostic alignment, not a statement of actual Controls consumption or current permission.',
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


def analyze_latest(params, root, previous, *, permitted):
  from openpilot.starpilot.flm.offroad_control_metadata import read_control_metadata as read_control

  route = latest_route(params, root, permitted=permitted)
  state = {'cp': None, 'cp_sha': None, 'context': None}
  windows = {}
  stable_windows = {}
  baseline_tracking = {}
  bookends = {}
  raw_windows = {}
  context_metadata = {}
  counts = {
    'recorded_tracking': 0,
    'joined_tracking': 0,
    'missing_join': 0,
    'invalid_control_metadata': 0,
    'original_control_metadata_rows': 0,
    'original_applied_envelopes': 0,
    'controller_fit_refusals': 0,
    'omitted_control_metadata': 0,
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
          okay = bool(
            control is not None
            and abs(control.control_ns - point.mono_ns) <= 30_000_000
            and control.enabled
            and control.active
            and control.valid
            and not (control.manual or control.override or control.fault or control.saturated or control.limited)
            and 0 < control.state_ns <= control.control_ns <= control.state_ns + 100_000_000
            and 0 < control.source_receipt_ns <= control.control_ns <= control.source_receipt_ns + 500_000_000
            and (
              not control.native_required
              or control.native_allowed
              and 0 < control.native_observed_ns <= control.control_ns <= control.native_expires_ns <= control.native_observed_ns + 200_000_000
            )
          )
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
            windows.setdefault(context, []).extend(whole_windows(tuple(qualified)))
            # The original Stable chatter window needs genuine multi-second
            # context. Keep the existing two-second path and fast gross guard.
            stable_windows.setdefault(context, []).extend(whole_windows(tuple(qualified), duration_ns=6_000_000_000))

    if sum(map(len, windows.values())) + sum(map(len, stable_windows.values())) + sum(map(len, baseline_tracking.values())) > MAX_WINDOWS:
      raise ValueError('Whole-drive tracking window bound exceeded')

  receipt = walk_completed_route(root, route, permitted=permitted, consume=consume)
  if not permitted():
    raise ValueError('Parked authority lost after analysis')
  from openpilot.starpilot.flm.offroad_baseline_windows import stable_proposal_windows

  stable_windows, stable_grades = stable_proposal_windows(stable_windows, baseline_tracking)
  counts['qualified_whole_windows'] = sum(map(len, windows.values()))
  prior = previous(state['cp']) if callable(previous) else previous
  amplitude_prior = {name: prior[name] for name in AMPLITUDE_NAMES}
  reports = []
  for key, values in sorted(windows.items()):
    if not values:
      continue
    report = analyze(tuple(values), amplitude_prior, permitted=permitted)
    selected_ids = {wid for cohort_row in report['cohorts'] for role in ('training', 'heldout') for wid in cohort_row.get(role, {}).get('windows', ())}
    report.update(
      original_context_sha256=key,
      original_metadata=context_metadata[key],
      selected_original_windows=[
        {
          'identity': w.identity,
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
    report = analyze_stable_windows(tuple(values), permitted=permitted)
    grade = stable_grades.get(key, 'continuous_original_control_metadata')
    report['tracking_source_grade'] = grade
    qualification = []
    if grade == 'bookended_zero_route_tracking':
      for c in report['cohorts']:
        native = [w for w in windows.get(key, ()) if cohort(w) == c['cohort']]
        chosen = _spread(sorted(native, key=lambda w: (w.points[0].segment, w.points[0].mono_ns)), MAX_COHORT_WINDOWS)
        c['qualification_roles'] = {role: {'windows': tuple(w.identity for w in chosen[offset::2])} for role, offset in (('training', 0), ('heldout', 1))}
        qualification.extend(chosen)
      unique = {w.identity: w for w in qualification}
      report['qualification_original_windows'] = [
        {
          'identity': w.identity,
          'segment': w.points[0].segment,
          'start_ns': w.points[0].mono_ns,
          'end_ns': w.points[-1].mono_ns,
          'metrics': tracking_metrics(w.points),
          'demand': window_demand(w),
        }
        for w in unique.values()
      ]
    selected = {wid for c in report['cohorts'] for role in ('training', 'heldout') for wid in c.get(role, {}).get('windows', ())}
    report.update(
      original_context_sha256=key,
      original_metadata=context_metadata[key],
      selected_original_windows=[
        {
          'identity': w.identity,
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
  return CompletedAssessment(receipt, reports, counts, state['cp'], (), route, tuple(quality_stops), tuple(diagnostic), tuple(stable))
