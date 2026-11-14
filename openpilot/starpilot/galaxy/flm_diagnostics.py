"""Small, read-only support download; no raw routes, CarParams, GPS or credentials."""

import json
import re

from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params
from openpilot.starpilot.flm.offroad_store import KEY, MAX_BYTES, NAMESPACE
from openpilot.starpilot.flm.learned_lifecycle import decode
from openpilot.starpilot.saved_source import read_saved


def _reason(value):
  text = str(value)[:512]
  text = re.sub(r'https?://\S+|/(?:data|home|Users|tmp)/\S+', '[private location]', text)
  return re.sub(r'(?i)(?:token|password|secret|authorization)[=: ]+\S+', '[private credential]', text)


def _result(value):
  result = {key: value[key] for key in ('status', 'configuration_changed', 'configuration_state', 'saved_adjustments',
            'analysis_counts', 'actual_application_proven', 'recommendation_count', 'route_skipped') if key in value}
  result['reason'] = _reason(value.get('reason', 'not_reported'))
  skipped = value.get('skipped_routes')
  result['skipped_drives'] = [{'recording': _reason(row[0])[:180], 'reason': _reason(row[1])}
                             for row in (skipped[:16] if type(skipped) is list else ()) if type(row) is list and len(row) == 2]
  return result


def diagnostics(report, params):
  current = report.get('current') or {}
  details = current.get('details') or {}
  result = {'version': 1, 'privacy': 'No raw route data, location, VIN, dongle ID or credentials included.',
            'current': {key: current[key] for key in ('fresh', 'ageSeconds', 'enabled', 'status', 'progress', 'fit', 'application') if key in current},
            'preference': report.get('preference'), 'history': report.get('history', []), 'checkpoints': [], 'saved_profiles': []}
  result['current']['reason'] = _reason(current.get('reason', 'learner_report_unavailable'))
  result['current']['analysis'] = {key: details[key] for key in (
    'Recorded steering observations', 'Retained control windows', 'Retained tracking windows', 'Recorded drive coverage',
    'Drives in steering evidence history', 'Saved route adjustment count', 'Saved route configuration state',
    'Recorded coordinate recommendations', 'Controller and encoder check windows', 'Automatic tune refusal reasons') if key in details}
  result['current']['skipped_drives'] = _result({'skipped_routes': details.get('Skipped recorded drives', [])})['skipped_drives']
  if params is None:
    result['storage'] = {'status': 'unavailable'}
    return result
  for key in ('GitCommit', 'GitBranch'):
    raw, readable = read_saved(params, key, 128)
    if readable and raw is not None and re.fullmatch(rb'[\w./-]{1,128}', raw):
      result[key] = raw.decode('ascii')
  try:
    raw, readable = read_saved(params, KEY, MAX_BYTES)
    if not readable:
      raise ValueError('saved_profiles_unreadable')
    for row in decode(raw).get(NAMESPACE, {}).values():
      result['saved_profiles'].append({'state': row['state'], 'sequence': row['sequence'],
                                      'adjustments': sum(value != 0 for value in row['values'].values()),
                                      'evidence_drives': len(row['route_sha256s'])})
    directory = directory_for_params(params)
    if not directory.exists():
      result['storage'] = {'status': 'not_created', 'documents': 0, 'bytes': 0}
    else:
      cache = AssessmentCache(directory)
      files = list(directory.iterdir())
      result['storage'] = {'status': 'readable', 'documents': len(files), 'bytes': sum(path.lstat().st_size for path in files)}
      result['checkpoints'] = [_result(row['result']) for row in cache.checkpoints()]
  except (OSError, ValueError, TypeError, KeyError) as error:
    result['storage'] = {'status': 'unavailable', 'reason': _reason(error)}
  return result


def download(report, params):
  def clean(value):
    if type(value) is dict:
      return {key: clean(item) for key, item in value.items()}
    if type(value) is list:
      return [clean(item) for item in value]
    return _reason(value) if type(value) is str else value

  return 'starpilot-flm-logs.json', json.dumps(clean(diagnostics(report, params)), indent=2, allow_nan=False).encode()
