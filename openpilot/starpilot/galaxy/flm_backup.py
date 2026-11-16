"""User-controlled FLM files. Imported history never becomes new qualification."""

from datetime import UTC, datetime
import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any

from openpilot.starpilot.analytics.settings import _eligible, _value
from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
from openpilot.starpilot.flm.learned_lifecycle import decode, encode
from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params
from openpilot.starpilot.flm.offroad_runtime import current_scope
from openpilot.starpilot.flm.offroad_saved_history import SavedHistory
from openpilot.starpilot.flm.offroad_store import KEY, MAX_BYTES as STORE_MAX, MAX_SCOPES, NAMESPACE, validate_rows
from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
from openpilot.starpilot.galaxy.settings import SettingsChanged
from openpilot.starpilot.galaxy.toggle_backup import ToggleBackup
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved

FORMAT = 'starpilot-flm-tune'
MAX_BYTES = 64 * 1024
PREFIX = 'torque:surface:'
TIMESTAMPS = ('source_start_ns', 'source_end_ns', 'completed_wall_ns')


def digest(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class BackupRefused(ValueError):
  pass


class FlmBackup:
  def __init__(self, settings, *, clock=time.monotonic):
    self.settings = settings
    self.clock = clock
    self.lock = threading.Lock()
    self.pending = {}

  def _manual(self, ctx):
    return [{'page': 'tuning', 'key': row.key, 'value': row.value}
            for row in self.settings._state('tuning', ctx).rows
            if row.key in {PREFIX + name for name in NAMES} and _eligible(row) and _value(row, row.value) is not None]

  def _mode(self, cp):
    from openpilot.starpilot.lateral.controller_selection import read_selection

    if cp.lateralTuning.which() == 'pid':
      return 'native'
    selected = read_selection(self.settings.params, cp)
    if selected.source == 'invalid':
      raise BackupRefused('The saved controller selection is unreadable. Nothing was restored.')
    return str(selected.mode)

  def export(self):
    ctx = self.settings.context.sample()
    if ctx.cp is None or ctx.cp.notCar:
      raise BackupRefused('Connect the vehicle before backing up its tune.')
    identity, mode = car_params_digest(ctx.cp), self._mode(ctx.cp)
    raw, readable = read_saved(self.settings.params, KEY, STORE_MAX)
    if not readable:
      raise BackupRefused('Saved tuning is unavailable. Existing settings were left unchanged.')
    # Decimal strings survive browser JSON parsing without losing nanoseconds or float spelling.
    learned = {key: dict(row, **{name: str(row[name]) for name in TIMESTAMPS},
                        **{name: {coordinate: str(value) for coordinate, value in row[name].items()} for name in ('values', 'previous')})
               for key, row in decode(raw).get(NAMESPACE, {}).items() if row['scope']['cp_sha256'] == identity}
    manual = self._manual(ctx)
    fresh = self.settings.context.sample()
    if (fresh.cp_raw != ctx.cp_raw or fresh.metric != ctx.metric or self._mode(fresh.cp) != mode or self._manual(fresh) != manual or
        read_saved(self.settings.params, KEY, STORE_MAX) != (raw, readable)):
      raise SettingsChanged('Settings changed while preparing the backup. Try again.')
    body = {'format': FORMAT, 'version': 1, 'createdAt': datetime.now(UTC).isoformat(), 'vehicle': str(ctx.cp.carFingerprint),
            'controllerMode': mode,
            'cpSha256': identity, 'metric': ctx.metric, 'manual': manual, 'learned': learned}
    result = dict(body, checksum=digest(body))
    if len(json.dumps(result, allow_nan=False).encode()) > MAX_BYTES:
      raise BackupRefused('This tune exceeds the backup size limit.')
    return result

  def _validate(self, backup):
    fields = {'format', 'version', 'createdAt', 'vehicle', 'controllerMode', 'cpSha256', 'metric', 'manual', 'learned', 'checksum'}
    if (type(backup) is not dict or set(backup) != fields or backup['format'] != FORMAT or
        type(backup['version']) is not int or backup['version'] != 1 or type(backup['metric']) is not bool or
        type(backup['createdAt']) is not str or len(backup['createdAt']) > 64 or
        type(backup['vehicle']) is not str or len(backup['vehicle']) > 128 or
        type(backup['controllerMode']) is not str or not 1 <= len(backup['controllerMode']) <= 32 or
        type(backup['cpSha256']) is not str or len(backup['cpSha256']) != 64 or backup['cpSha256'].strip('0123456789abcdef') or
        type(backup['manual']) is not list or len(backup['manual']) > 30 or
        len(json.dumps(backup, allow_nan=False).encode()) > MAX_BYTES):
      raise BackupRefused('Choose a supported FLM tune backup.')
    if backup['checksum'] != digest({key: value for key, value in backup.items() if key != 'checksum'}):
      raise BackupRefused('The backup checksum does not match. The file may be damaged.')
    try:
      if type(backup['learned']) is not dict or len(backup['learned']) > MAX_SCOPES:
        raise ValueError('Bounded tune history required')
      learned: dict[str, dict[str, Any]] = {}
      for key, row in backup['learned'].items():
        if any(type(row[name]) is not str or not row[name].isascii() or not row[name].isdigit() or
               not 1 <= len(row[name]) <= 19 for name in TIMESTAMPS):
          raise ValueError('Exact decimal timestamps required')
        if any(type(value) is not str or len(value) > 32 for name in ('values', 'previous') for value in row[name].values()):
          raise ValueError('Exact decimal coordinates required')
        learned[key] = dict(row, **{name: int(row[name]) for name in TIMESTAMPS},
                            **{name: {coordinate: float(value) for coordinate, value in row[name].items()} for name in ('values', 'previous')})
      validate_rows(learned)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
      raise BackupRefused('The saved Auto Learn setup is invalid.') from error
    seen = set()
    for row in backup['manual']:
      if (type(row) is not dict or set(row) != {'page', 'key', 'value'} or row['page'] != 'tuning' or
          type(row['key']) is not str or row['key'] not in {PREFIX + name for name in NAMES} or row['key'] in seen or
          type(row['value']) is not str or len(row['value']) > 128):
        raise BackupRefused('The file contains an unsupported manual setting.')
      seen.add(row['key'])
    if any(row['scope']['cp_sha256'] != backup['cpSha256'] for row in learned.values()):
      raise BackupRefused('The backup mixes different vehicle configurations.')
    return learned

  def _plan(self, backup, mode, identity, authorized):
    learned = self._validate(backup)
    if mode not in ('manual', 'learned'):
      raise BackupRefused('Choose manual settings or an Auto Learn setup.')
    ctx = self.settings.context.sample()
    if (not authorized() or not ctx.parked or ctx.cp is None or type(ctx.cp_raw) is not bytes or
        read_saved(self.settings.params, 'IsOffroad', 1) != (b'1', True)):
      raise SettingsChanged('Park and switch off the vehicle before restoring a tune.')
    if str(ctx.cp.carFingerprint) != backup['vehicle'] or car_params_digest(ctx.cp) != backup['cpSha256']:
      raise BackupRefused('This tune is for a different vehicle or vehicle configuration. Nothing was changed.')
    if self._mode(ctx.cp) != backup['controllerMode']:
      raise BackupRefused('Select the same lateral controller as the backup before restoring.')
    if ctx.metric != backup['metric']:
      raise BackupRefused('Use the backup’s metric or imperial units before restoring.')
    raw, readable = read_saved(self.settings.params, KEY, STORE_MAX)
    if not readable:
      raise BackupRefused('The existing saved tune is unreadable. It will not be overwritten.')
    doc = decode(raw)
    changes = []
    if mode == 'manual':
      available = {row.key: row for row in self.settings._state('tuning', ctx).rows if row.available}
      helper = ToggleBackup(self.settings, None)
      for row in backup['manual']:
        if row['key'] not in available:
          raise BackupRefused('A backed-up slider is unavailable for this controller. Nothing was changed.')
        preview = helper._preview(row, identity, ctx)
        with self.settings.lock:
          self.settings.intents.pop(preview['intent'], None)
        if available[row['key']].value != row['value']:
          changes.append([available[row['key']].label, available[row['key']].value, row['value']])
      desired = None
    else:
      if any(float(row['value']) != 0 for row in self._manual(ctx)):
        raise BackupRefused('Manual overrides are saved. Review them before restoring an Auto Learn setup.')
      from openpilot.starpilot.flm.runtime_adapter_registry import implementation_revision
      from openpilot.starpilot.flm.native_primitives import native_identity

      implementation_revision.cache_clear()
      native_identity.cache_clear()
      scope = current_scope(ctx.cp, self.settings.params)
      selected = learned.get(scope.key())
      if selected is None:
        raise BackupRefused('No Auto Learn setup in this file matches the current controller and settings. It remains available for review.')
      known = [doc, *(decode(text.encode()) for text in SavedHistory(self.settings.params).read().values())]
      rows = [value[NAMESPACE][scope.key()] for value in known if scope.key() in value.get(NAMESPACE, {})]
      rank = {'ready_for_next_drive': 0, 'accepted_after_drive': 1, 'retained_after_drive': 1, 'rolled_back': 2}
      newest = max(rows, key=lambda row: (row['sequence'], row['completed_wall_ns'], rank[row['state']]), default=None)
      if newest != selected:
        raise BackupRefused('This device cannot verify the latest qualified setup. Backups cannot replace qualification or rollback history.')
      if (selected['state'] == 'ready_for_next_drive' and
          os.path.lexists(directory_for_params(self.settings.params) / ('trial-' + selected['analysis_sha256'] + '.json'))):
        raise BackupRefused('This trial has already started. Restoring a backup cannot start it again.')
      rows = doc.setdefault(NAMESPACE, {})
      if scope.key() not in rows and len(rows) >= MAX_SCOPES:
        raise BackupRefused('The saved setup history is full. No existing setup was removed.')
      before = rows.get(scope.key())
      rows[scope.key()] = selected
      if doc['version'] != 3:
        doc = dict(doc, version=3, fullSurfaces=doc.get('fullSurfaces', {}))
      desired = encode(doc)
      if before != selected:
        changes = [[name, None, value] for name, value in selected['values'].items() if value != 0]
    fresh = self.settings.context.sample()
    if (not authorized() or not fresh.parked or fresh.cp_raw != ctx.cp_raw or self._mode(fresh.cp) != backup['controllerMode'] or
        read_saved(self.settings.params, KEY, STORE_MAX) != (raw, readable)):
      raise SettingsChanged('The vehicle or saved tune changed. Review the backup again.')
    return {'mode': mode, 'cp': hashlib.sha256(ctx.cp_raw).hexdigest(), 'expected': raw, 'desired': desired, 'changes': changes}

  def preview(self, backup, mode, identity, *, authorized):
    plan = self._plan(backup, mode, identity, authorized)
    with self.lock:
      self.pending = {key: value for key, value in self.pending.items() if value['expires'] > self.clock()}
      if len(self.pending) >= 8:
        self.pending.pop(next(iter(self.pending)))
      intent = secrets.token_urlsafe(24)
      self.pending[intent] = {'backup': backup, 'mode': mode, 'identity': identity, 'expires': self.clock() + 90, 'plan': plan}
    return {'intent': intent, 'mode': mode, 'changes': plan['changes'], 'count': len(plan['changes']),
            'question': 'Restore these manual slider values? Manual overrides pause Auto Learn.' if mode == 'manual' else
                        'Restore this locally verified Auto Learn setup? Current use is still confirmed by the controller while driving.'}

  def confirm(self, intent, identity, *, authorized):
    with self.lock:
      pending = self.pending.get(intent)
      if pending is None or pending['identity'] != identity or pending['expires'] <= self.clock():
        raise SettingsChanged('Restore preview expired. Review the file again.')
      del self.pending[intent]
    plan = self._plan(pending['backup'], pending['mode'], identity, authorized)
    if plan != pending['plan']:
      raise SettingsChanged('Saved settings changed after the preview. Nothing was restored.')
    if not plan['changes']:
      return {'complete': True, 'restored': 0, 'unchanged': len(pending['backup']['manual']) if plan['mode'] == 'manual' else 1, 'skipped': []}
    directory = directory_for_params(self.settings.params)
    AssessmentCache(directory.with_name(directory.name + '-backups')).put({'kind': 'before_user_restore', 'backup': self.export()})
    if plan['mode'] == 'manual':
      backup = pending['backup']

      def permitted_manual():
        try:
          ctx = self.settings.context.sample()
          return (authorized() and ctx.parked and type(ctx.cp_raw) is bytes and
                  hashlib.sha256(ctx.cp_raw).hexdigest() == plan['cp'] and ctx.metric == backup['metric'] and
                  self._mode(ctx.cp) == backup['controllerMode'] and read_saved(self.settings.params, 'IsOffroad', 1) == (b'1', True))
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
          return False

      return ToggleBackup(self.settings, None).restore({'format': 'galaxy-toggles', 'version': 1,
        'createdAt': backup['createdAt'], 'metric': backup['metric'], 'vehicle': backup['vehicle'],
        'settings': backup['manual'], 'layout': None}, identity, authorized=permitted_manual)
    SavedHistory(self.settings.params).preserve(plan['expected'] or encode(decode(None)))

    def permitted():
      return authorized() and self._plan(pending['backup'], 'learned', identity, authorized) == plan

    result = commit_exact(self.settings.params, key=KEY, max_bytes=STORE_MAX, raw=plan['desired'], expected=plan['expected'],
                          authorized=permitted, temp_prefix='.flm-import-')
    if not result.committed or not result.verified:
      raise SettingsChanged('Restore was not confirmed. Refresh the saved tune before retrying.')
    return {'complete': True, 'restored': len(plan['changes']), 'unchanged': 0, 'skipped': []}
