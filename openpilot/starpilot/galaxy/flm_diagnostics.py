"""Small, read-only support download; no raw routes, CarParams, GPS or credentials."""

import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

from capnp import KjException

from openpilot.starpilot.connect.provider import cloudlog_root
from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params
from openpilot.starpilot.flm.offroad_store import KEY, MAX_BYTES, NAMESPACE
from openpilot.starpilot.flm.learned_lifecycle import decode
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.flm.diagnostic_state import read_state, qualification, redact


def _reason(value):
  return redact(value)


def _explanation(reason):
  explanations = {
    'recording_for_offroad_analysis': 'Recording while driving. Route analysis runs after parking; this is not an analysis result.',
    'loading_parked_analysis': 'Starting the parked analysis worker.',
    'analyzing_last_completed_drive': 'Reading recorded drive data.',
    'verifying_recorded_drive': 'The sections were read. FLM is verifying that the recording is unchanged.',
    'Parked route input collection cancelled': 'Reading was interrupted before the drive assessment finished. This report has no specific blocker.',
    'checking_recorded_steering_recommendations': 'Checking recommendations against recorded controller and steering-delivery evidence.',
    'previous_analysis_interrupted_before_completion': 'The learner restarted before its previous analysis reported an outcome.',
    'analysis_interrupted_by_shutdown': 'Analysis was interrupted by shutdown or the return to driving.',
    'waiting_for_vehicle_offroad': 'Analysis is waiting for the vehicle to be offroad.',
    'waiting_for_controls_to_disengage': 'Analysis is waiting for controls to disengage.',
    'waiting_for_vehicle_to_switch_off': 'The device still reports the vehicle started.',
    'waiting_for_ignition_off': 'The vehicle interface still reports ignition on.',
    'waiting_for_connected_panda_status': 'No connected vehicle-interface status is available.',
    'waiting_for_panda_controls_to_disarm': 'The vehicle interface still allows controls.',
    'waiting_for_panda_fault_to_clear': 'The vehicle interface reports a fault.',
    'Local drive inventory is incomplete': 'FLM could not obtain a complete list of local recordings.',
    'more_independent_eligible_tracking_required': 'Analysis needs more independent, eligible steering evidence before proposing a change.',
    'recorded_tracking_suggestion_not_yet_qualified_for_automatic_application':
      'Analysis produced suggestions; the evidence required for automatic saving is not complete.',
    'vehicle_identity_unavailable': 'This recording was reviewed on its own. A physical vehicle identity was unavailable, ' +
      'so no saved Auto Learn tune was loaded, no cross-drive evidence was combined and no adjustment was saved automatically.',
    'Known vehicle and no manual coordinates required': 'This older build rejected its vehicle/settings identity before route analysis. In the parked ' +
      'analysis path, missing or invalid recorded vehicle identity causes this refusal; it does not establish route corruption.',
    'Valid manual coordinate settings required': 'Saved manual steering settings require review; existing choices were preserved.',
    'saved_gm_flm_profile_blocks_learning': 'Auto Learn is paused because an older manual GM FLM tune is active.',
    'saved_gm_force_off_blocks_learning': 'A saved GM steering setting explicitly turns off learning for this vehicle.',
    'Manual or explicitly disabled tuning selected':
      'This older report did not distinguish a legacy GM FLM profile from a saved GM setting that turns learning off.',
    'Manual coordinate settings require review: context_changed':
      'The saved steering settings do not match the vehicle configuration or controller for this recording.',
    'partial_drive_provisional_only': 'Intact portions were analyzed. This partial recording can provide suggestions, not automatic changes.',
    'bounded_trial_configured_for_next_drive': 'A bounded adjustment was saved for the next drive.',
    'no_consistent_adjustment_in_completed_drive': 'The analyzed evidence did not support a consistent adjustment.',
  }
  if reason.startswith('waiting_for_valid_'):
    return 'Analysis is waiting for valid ' + reason.removeprefix('waiting_for_valid_') + ' status.'
  if reason.startswith('waiting_for_fresh_'):
    return 'Analysis is waiting for a fresh ' + reason.removeprefix('waiting_for_fresh_') + ' status message.'
  return explanations.get(reason, 'Reported reason: ' + reason)


def _result(value):
  result = {key: value[key] for key in ('status', 'configuration_changed', 'configuration_state', 'saved_adjustments',
            'analysis_counts', 'actual_application_proven', 'recommendation_count', 'route_skipped') if key in value}
  result['reason'] = _reason(value.get('reason', 'not_reported'))
  result['explanation'] = _explanation(result['reason'])
  checks = qualification(value.get('qualification_summary'))
  if checks is not None:
    result['qualification'] = checks
  skipped = value.get('skipped_routes')
  result['skipped_drives'] = [{'recording': _reason(row[0])[:180], 'reason': _reason(row[1])}
                             for row in (skipped[:16] if type(skipped) is list else ()) if type(row) is list and len(row) == 2]
  return result


def _finding_review(report, params, cache):
  """Compare retained findings to the cached parked configuration without exporting it."""
  from opendbc.car.structs import car
  from openpilot.starpilot.flm.manual_coordinates import load
  from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES
  from openpilot.starpilot.galaxy.flm_acceptance import review_binding_issue
  from openpilot.starpilot.lateral.controller_selection import read_selection
  from openpilot.starpilot.schema_cache import inspect_cache

  preference = report.get('preference') or {}
  if preference.get('valid') is not True or preference.get('parked') is not True:
    return {'status': 'parked_configuration_unavailable'}
  key = 'CarParamsPersistent'
  snapshot = read_saved(params, key, 1024 * 1024)
  raw, readable = snapshot
  payload = inspect_cache(key, raw).payload if readable and raw is not None else None
  if payload is None:
    return {'status': 'parked_configuration_unavailable'}
  with car.CarParams.from_bytes(payload, traversal_limit_in_words=1024 * 1024 // 8) as cp:
    if cp.notCar or cp.carFingerprint != preference.get('vehicle'):
      return {'status': 'parked_configuration_unavailable'}
    _, manual_state = load(cp, read_selection(params, cp).mode, params)
    details = (report.get('current') or {}).get('details') or {}
    findings, checked = {}, {}
    for name, review, source in details.get('Recorded suggestion assessments', ())[:len(ROUTE_NAMES)]:
      if name not in ROUTE_NAMES:
        continue
      binding = (review, source)
      if binding not in checked:
        try:
          _, issue = review_binding_issue(cache, review, cp, source)
          checked[binding] = issue or 'matches_parked_fingerprint'
        except (OSError, ValueError, TypeError, KeyError):
          checked[binding] = 'assessment_unavailable'
      findings[name] = checked[binding]
  if (read_saved(params, key, 1024 * 1024) != snapshot
      or read_saved(params, 'IsOffroad', 1) != (b'1', True)):
    return {'status': 'parked_configuration_unavailable'}
  return {'status': 'checked', 'scope': 'Stored parked configuration at download', 'manual_coordinates': manual_state, 'findings': findings}


def _hardwared_pauses(root):
  """Read only recent, already recorded slow phases. Never return raw log records."""
  try:
    paths = sorted(Path(root).glob('swaglog.[0-9]*'), reverse=True)[:8]
  except OSError:
    return []
  pauses = []
  for path in paths:
    try:
      with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
          continue
        offset = max(0, info.st_size - 256 * 1024)
        stream.seek(offset)
        data = stream.read(256 * 1024)
      lines = data.splitlines()
      for line in lines[1:] if offset else lines:
        if b'hardwared.slow_phase' not in line:
          continue
        try:
          entry = json.loads(line)
          msg = entry['msg']
          if entry['filename'] != 'hardware_timing.py' or msg['event$s'] != 'hardwared.slow_phase':
            continue
          phase, end = msg['phase$s'], entry['created']
          elapsed, cpu = msg['elapsedMs$f'], msg['threadCpuMs$f']
          if (re.fullmatch(r'[a-z_]{1,32}', phase) and type(end) in (int, float) and math.isfinite(end)
              and 500 <= elapsed <= 60_000 and 0 <= cpu <= elapsed + 1):
            pauses.append((end - elapsed / 1000, end, phase, round(elapsed), round(cpu)))
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
          continue
    except OSError:
      continue
  return pauses


def _explain_parked_interruptions(history):
  pauses = None
  for event in [history['current'], history['last_attempt'], history['last_result'], *history['events']]:
    if event is None:
      continue
    event['explanation'] = _explanation(event['reason'])
    counts = event.get('analysis_counts', {})
    interrupted = counts.pop('parked_interrupted_wall_ns', None)
    age_ms = counts.get('device_state_receive_age_ms')
    if interrupted is None or age_ms is None:
      continue
    if pauses is None:
      pauses = _hardwared_pauses(cloudlog_root())
    when = interrupted / 1e9
    matches = [row for row in pauses if row[0] <= when <= row[1] + 0.5 and
               row[1] >= when - age_ms / 1000 and row[1] <= when + 60]
    if matches:
      _, _, phase, elapsed, cpu = max(matches, key=lambda row: row[3])
      event['hardwared_pause'] = {'status': 'overlapping_logged_phase', 'phase': phase, 'elapsed_ms': elapsed, 'thread_cpu_ms': cpu}
    else:
      event['hardwared_pause'] = {'status': 'no_matching_phase_in_recent_logs'}


def diagnostics(report, params):
  current = report.get('current') or {}
  details = current.get('details') or {}
  result: dict[str, Any] = {'version': 1, 'privacy': 'No raw route data, location, VIN, dongle ID or credentials included.',
            'current': {key: current[key] for key in ('fresh', 'ageSeconds', 'enabled', 'status', 'progress', 'fit', 'application') if key in current},
            'preference': report.get('preference'), 'history': report.get('history', []), 'checkpoints': [], 'saved_profiles': []}
  result['current']['reason'] = _reason(current.get('reason', 'learner_report_unavailable'))
  result['current']['explanation'] = _explanation(result['current']['reason'])
  result['current']['analysis'] = {key: details[key] for key in (
    'Parked analysis progress', 'Earlier analysis shown while updating',
    'Recorded steering observations', 'Retained control windows', 'Retained tracking windows', 'Recorded drive coverage',
    'Drives in steering evidence history', 'Saved route adjustment count', 'Saved route configuration state',
            'Recorded coordinate recommendations', 'Recorded suggestion sources', 'Recorded suggestion assessments',
            'Controller and encoder check windows', 'Automatic tune refusal reasons',
            'Recorded vehicle identity', 'Drive analysis is partial', 'Partial drive segments', 'Omitted recorded segments') if key in details}
  result['current']['skipped_drives'] = _result({'skipped_routes': details.get('Skipped recorded drives', [])})['skipped_drives']
  if params is None:
    result['storage'] = {'status': 'unavailable'}
    result['learning_diagnostics'] = {'status': 'unavailable', 'reason': 'Settings storage is unavailable.'}
    return result
  result['learning_diagnostics'] = read_state(params)
  history = result['learning_diagnostics']
  if history['status'] == 'recorded':
    _explain_parked_interruptions(history)
  if details.get('Diagnostic persistence error'):
    result['learning_diagnostics']['write_error'] = _reason(details['Diagnostic persistence error'])
  for key in ('GitCommit', 'GitBranch'):
    raw, readable = read_saved(params, key, 128)
    if readable and raw is not None and re.fullmatch(rb'[\w./-]{1,128}', raw):
      result[key] = raw.decode('ascii')
  try:
    raw, readable = read_saved(params, KEY, MAX_BYTES)
    if not readable:
      raise ValueError('saved_profiles_unreadable')
    document = decode(raw)
    result['stored_profile_counts'] = {'legacy_stage_profiles': len(document['profiles']),
                                      'full_surface_profiles': len(document.get('fullSurfaces', {})),
                                      'parked_route_profiles': len(document.get(NAMESPACE, {}))}
    for row in document.get(NAMESPACE, {}).values():
      result['saved_profiles'].append({'state': row['state'], 'sequence': row['sequence'],
                                      'adjustments': sum(value != 0 for value in row['values'].values()),
                                      'evidence_drives': len(row['route_sha256s'])})
  except (OSError, ValueError, TypeError, KeyError) as error:
    result['profile_storage'] = {'status': 'unavailable', 'reason': _reason(error)}
  try:
    directory = directory_for_params(params)
    if not directory.exists():
      result['storage'] = {'status': 'not_created', 'documents': 0, 'bytes': 0}
    else:
      cache = AssessmentCache(directory)
      files = list(directory.iterdir())
      result['storage'] = {'status': 'readable', 'documents': len(files), 'bytes': sum(path.lstat().st_size for path in files)}
      result['checkpoints'] = [_result(row['result']) for row in cache.checkpoints()]
      if details.get('Recorded suggestion assessments'):
        try:
          result['finding_review'] = _finding_review(report, params, cache)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, KjException):
          result['finding_review'] = {'status': 'unavailable'}
  except (OSError, ValueError, TypeError, KeyError) as error:
    result['storage'] = {'status': 'unavailable', 'reason': _reason(error)}
  result['storage']['scope'] = 'Parked route analysis in the current settings store only'
  result['native_learning_caches'] = {}
  from openpilot.cereal import log
  from openpilot.starpilot.schema_cache import inspect_cache
  for key, service in (('LiveTorqueParameters', 'lateralTorqueParameters'), ('LiveDelay', 'lateralDelay')):
    try:
      raw, readable = read_saved(params, key, 2_097_152)
      inspected = inspect_cache(key, raw) if readable else None
      summary: dict[str, Any] = {'status': inspected.status if inspected is not None else 'unreadable',
                                 'scope': 'Stored estimator cache; not proof of current application'}
      if inspected is not None and inspected.reason:
        summary['reason'] = _reason(inspected.reason)
      if inspected is not None and inspected.payload is not None:
        with log.Event.from_bytes(inspected.payload) as event:
          value = getattr(event, service)
          summary['version'] = int(value.version)
          summary['estimate_valid'] = bool(event.valid and (value.valid if service == 'lateralTorqueParameters' else str(value.status) == 'estimated'))
      result['native_learning_caches'][key] = summary
    except (OSError, ValueError, TypeError, KeyError, KjException) as error:
      result['native_learning_caches'][key] = {'status': 'unavailable', 'reason': _reason(error)}
  try:
    from openpilot.starpilot.flm.offroad_saved_history import SavedHistory
    from openpilot.starpilot.flm.offroad_store import validate_row

    snapshots = SavedHistory(params).read()
    previous, seen = [], set()
    for text in reversed(list(snapshots.values())[-8:]):
      for key, row in json.loads(text).get(NAMESPACE, {}).items():
        if key in seen:
          continue
        seen.add(key)
        validate_row(row)
        previous.append({'controller_mode': row['scope']['controller_mode'], 'state': row['state'], 'values': row['values']})
    result['saved_history'] = {'snapshots': len(snapshots), 'historical_only': True, 'previous_setups': previous}
  except (OSError, ValueError, TypeError, KeyError) as error:
    result['saved_history'] = {'status': 'unavailable', 'reason': _reason(error), 'historical_only': True}
  return result


def download(report, params):
  def clean(value):
    if type(value) is dict:
      return {key: clean(item) for key, item in value.items()}
    if type(value) in (tuple, list):
      return [clean(item) for item in value]
    return _reason(value) if type(value) is str else value

  return 'starpilot-flm-logs.json', json.dumps(clean(diagnostics(report, params)), indent=2, allow_nan=False).encode()


def with_dashboard_diagnostics(body, owner, authority=None):
  """Attach privacy-limited existing dashboard state without starting its analyzer."""
  summary: dict[str, Any] = {'status': 'not_started'}
  if owner is not None:
    try:
      summary = owner.diagnostics()
    except (AttributeError, OSError, RuntimeError, ValueError, TypeError):
      summary = {'status': 'unavailable'}
  if authority is not None:
    try:
      state = authority.status()
      summary['vehicle_permission'] = {'allowed': state['allowed'], 'reason': _reason(state.get('reason') or 'available'),
                                       'effective_started': authority.effective()}
    except (AttributeError, OSError, RuntimeError, ValueError, TypeError, KeyError):
      summary['vehicle_permission'] = {'allowed': False, 'reason': 'Vehicle status unavailable'}
  result = json.loads(body)
  result['dashboard_statistics'] = summary
  return json.dumps(result, indent=2, allow_nan=False).encode()
