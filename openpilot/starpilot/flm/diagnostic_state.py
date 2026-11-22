"""Bounded support history; no tuning values, timestamps or runtime authority."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from openpilot.starpilot.flm.offroad_cache import directory_for_params, _pairs
from openpilot.starpilot.saved_source import read_saved

MAX_BYTES = 65_536
MAX_EVENTS = 12
ACTIVE = ('starting_analysis', 'reading_recording', 'verifying_recording', 'checking_recommendations')
FIELDS = {'stage', 'reason', 'build', 'recording_sha256', 'analysis_counts', 'recommendation_count',
          'saved_adjustments', 'configuration_state', 'qualification', 'interrupted_stage', 'enabled', 'skipped_reasons'}


def redact(value):
  text = str(value)[:512]
  text = re.sub(r'https?://\S+|/(?:data|home|Users|tmp)/\S+', '[private location]', text)
  text = re.sub(r'\b[0-9a-f]{16}/[0-9a-f]{8}--[0-9a-f]{10}\b', '[private recording]', text)
  text = re.sub(r'\b[0-9a-f]{16}\b', '[private device]', text)
  text = re.sub(r'\b[A-HJ-NPR-Z0-9]{17}\b', '[private vehicle]', text)
  return re.sub(r'(?i)(?:token|password|secret|authorization)[=: ]+\S+', '[private credential]', text)


def qualification(value):
  if type(value) is not dict:
    return None
  counts: dict = {key: value[key] for key in ('passed', 'required') if type(value.get(key)) is int and 0 <= value[key] < 2**63}
  rows = value.get('refusals')
  counts['refusals'] = [[redact(row[0])[:160], row[1]] for row in (rows[:16] if type(rows) in (tuple, list) else ())
                        if type(row) in (tuple, list) and len(row) == 2 and type(row[1]) is int and 0 <= row[1] < 2**63]
  return counts


def path_for_params(params):
  directory = directory_for_params(params)
  return directory.with_name(directory.name + '-diagnostics.json')


def _validate(value):
  if (type(value) is not dict or set(value) != {'version', 'current', 'last_attempt', 'last_result', 'events'} or
      type(value['version']) is not int or value['version'] != 1):
    raise ValueError('Diagnostic history format changed')
  if type(value['events']) is not list or len(value['events']) > MAX_EVENTS:
    raise ValueError('Diagnostic history bound changed')
  for event in [value['current'], value['last_attempt'], value['last_result'], *value['events']]:
    if event is None:
      continue
    if (type(event) is not dict or not {'stage', 'reason', 'build'} <= event.keys() or not set(event) <= FIELDS or
        any(type(event.get(key)) is not str or len(event[key]) > 512 for key in ('stage', 'reason', 'build'))):
      raise ValueError('Invalid diagnostic event')
    for key, item in event.items():
      if key == 'analysis_counts':
        valid = type(item) is dict and len(item) <= 64 and all(
          re.fullmatch(r'[a-z_]{1,64}', name) and type(count) is int and 0 <= count < 2**63 for name, count in item.items())
      elif key == 'qualification':
        valid = type(item) is dict and item == qualification(item)
      elif key == 'skipped_reasons':
        valid = type(item) is list and len(item) <= 16 and all(type(reason) is str and len(reason) <= 512 for reason in item)
      elif key in ('recommendation_count', 'saved_adjustments'):
        valid = type(item) is int and 0 <= item < 2**63
      elif key == 'enabled':
        valid = type(item) is bool
      else:
        valid = type(item) is str and len(item) <= 512
      if not valid:
        raise ValueError('Invalid diagnostic field: ' + key)
  return value


def read_state(params):
  """Reading a download never creates or modifies a support or analysis store."""
  fd = None
  try:
    path = path_for_params(params)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    before = os.fstat(fd)
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_mode & 0o022 or
        not 0 < before.st_size <= MAX_BYTES):
      raise ValueError('Invalid diagnostic history file')
    raw = os.read(fd, MAX_BYTES + 1)
    after = os.fstat(fd)
    current = path.lstat()
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
        (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino) or len(raw) != before.st_size):
      raise ValueError('Diagnostic history changed while reading')
    return {'status': 'recorded', **_validate(json.loads(raw, object_pairs_hook=_pairs))}
  except FileNotFoundError:
    return {'status': 'not_recorded', 'reason': 'No persistent analysis diagnostic record is available in this settings store.'}
  except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as error:
    return {'status': 'unavailable', 'reason': redact(error)}
  finally:
    if fd is not None:
      os.close(fd)


def _stage(value):
  reason = str(value.get('reason', ''))
  if value.get('status') == 'fitting':
    return {'loading_parked_analysis': 'starting_analysis', 'analyzing_last_completed_drive': 'reading_recording',
            'verifying_recorded_drive': 'verifying_recording'}.get(reason, 'checking_recommendations')
  if value.get('status') == 'unavailable':
    return 'analysis_error'
  if reason.startswith('waiting_for_'):
    return 'waiting_for_parked'
  if value.get('completed_route'):
    return 'analysis_result'
  return 'recording' if reason == 'recording_for_offroad_analysis' else 'paused'


class DiagnosticState:
  def __init__(self, params):
    self.params, self.path = params, path_for_params(params)
    stored = read_state(params)
    self.error = stored.get('reason', '') if stored['status'] == 'unavailable' else ''
    self.writable = stored['status'] != 'unavailable'
    self.state = {key: stored.get(key) for key in ('current', 'last_attempt', 'last_result')}
    self.state.update(version=1, events=stored.get('events', []))
    self.last_observed = None
    raw, readable = read_saved(params, 'GitCommit', 128)
    self.build = raw.decode('ascii') if readable and raw is not None and re.fullmatch(rb'[0-9a-f]{40}', raw) else 'unknown'
    if self.state['current'] is not None and self.state['current']['stage'] in ACTIVE:
      self.observe({'status': 'unavailable', 'reason': 'previous_analysis_interrupted_before_completion'})

  def observe(self, value):
    """Record coarse state changes only; repeated onroad ticks cause no writes."""
    event = {'stage': _stage(value), 'reason': redact(value.get('reason', 'not_reported')), 'build': self.build}
    if value.get('completed_route'):
      event['recording_sha256'] = hashlib.sha256(str(value['completed_route']).encode()).hexdigest()
    counts = value.get('analysis_counts')
    if type(counts) is dict:
      event['analysis_counts'] = {key: count for key, count in list(counts.items())[:64]
                                  if type(key) is str and re.fullmatch(r'[a-z_]{1,64}', key) and type(count) is int and 0 <= count < 2**63}
    for key in ('recommendation_count', 'saved_adjustments'):
      if type(value.get(key)) is int and 0 <= value[key] < 2**63:
        event[key] = value[key]
    if type(value.get('enabled')) is bool:
      event['enabled'] = value['enabled']
    skipped = value.get('skipped_routes')
    if type(skipped) is list:
      event['skipped_reasons'] = [redact(row[1]) for row in skipped[:16] if type(row) in (tuple, list) and len(row) == 2]
    if type(value.get('configuration_state')) is str:
      event['configuration_state'] = redact(value['configuration_state'])
    checks = qualification(value.get('qualification_summary'))
    if checks is not None:
      event['qualification'] = checks
    prior = self.state['current']
    if event['stage'] == 'analysis_error' and prior is not None and prior['stage'] in ACTIVE:
      event['interrupted_stage'] = prior['stage']
    if event == self.last_observed or event == prior:
      return not self.error
    self.last_observed = event
    if not self.writable:
      return False
    state = {**self.state, 'current': event, 'events': [*self.state['events'], event][-MAX_EVENTS:]}
    if event['stage'] in ACTIVE:
      state['last_attempt'] = event
    elif event['stage'] in ('analysis_result', 'analysis_error'):
      state['last_result'] = event
    temporary = None
    try:
      raw = json.dumps(_validate(state), separators=(',', ':'), allow_nan=False).encode()
      while len(raw) > MAX_BYTES and len(state['events']) > 1:
        state['events'] = state['events'][1:]
        raw = json.dumps(state, separators=(',', ':'), allow_nan=False).encode()
      if len(raw) > MAX_BYTES:
        raise ValueError('Diagnostic history size bound')
      try:
        info = self.path.lstat()
      except FileNotFoundError:
        info = None
      if info is not None:
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
          raise ValueError('Diagnostic history ownership changed')
      with tempfile.NamedTemporaryFile(prefix='.flm-diagnostics-', dir=self.path.parent, delete=False) as stream:
        temporary = stream.name
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
      os.replace(temporary, self.path)
      temporary = None
      self.state = state
      directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
      try:
        os.fsync(directory)
      finally:
        os.close(directory)
      self.error = ''
      return True
    except (OSError, ValueError, TypeError) as error:
      self.error = redact(error)
      return False
    finally:
      if temporary is not None:
        try:
          Path(temporary).unlink(missing_ok=True)
        except OSError:
          pass
