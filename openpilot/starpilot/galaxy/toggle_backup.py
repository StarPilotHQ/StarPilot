"""Portable Galaxy preferences restored through the existing settings owners."""
from datetime import UTC, datetime
import json

from openpilot.starpilot.analytics.settings import _eligible, _value
from openpilot.starpilot.galaxy.settings import PAGES, SettingsChanged
from openpilot.starpilot.galaxy.onroad_layout import LayoutChanged
from openpilot.starpilot.ui.onroad_customization import validate_document
from openpilot.starpilot.ui.wheel_rotation import take_rotation

FORMAT = 'galaxy-toggles'
MAX_BYTES = 256 * 1024
MAX_SETTINGS = 512


class ToggleBackup:
  def __init__(self, settings, layout):
    self.settings = settings
    self.layout = layout

  def _rows(self, ctx):
    rows = {}
    for page in sorted(PAGES - {'hub'}):
      for row in self.settings._state(page, ctx).rows:
        if _eligible(row):
          previous = rows.get(row.key)
          priority = (_value(row, row.value) is not None, row.available)
          if previous is None or priority > (_value(previous[1], previous[1].value) is not None, previous[1].available):
            rows[row.key] = (page, row)
    return sorted(rows.values(), key=lambda item: (item[0], item[1].key))

  def export(self):
    ctx = self.settings.context.sample()
    values = [{'page': page, 'key': row.key, 'value': row.value}
              for page, row in self._rows(ctx) if _value(row, row.value) is not None]
    layout = self.layout.snapshot()
    if layout['valid']:
      take_rotation(layout['document'])
    result = {'format': FORMAT, 'version': 1, 'createdAt': datetime.now(UTC).isoformat(),
              'metric': ctx.metric, 'vehicle': getattr(ctx.cp, 'carFingerprint', None),
              'settings': values, 'layout': layout['document'] if layout['valid'] else None}
    if len(values) > MAX_SETTINGS or len(json.dumps(result).encode()) > MAX_BYTES:
      raise ValueError('Toggle backup exceeds the supported size')
    return result

  def _preview(self, entry, identity, ctx):
    view = self.settings.page(entry['page'], *identity)
    try:
      index = next((i for i, row in enumerate(self.settings._state(entry['page'], ctx).rows) if row.key == entry['key']), None)
      if index is None:
        raise SettingsChanged('Settings changed while preparing restore')
      return self.settings.preview(view['view'], index, 0, *identity, value=entry['value'])
    finally:
      with self.settings.lock:
        self.settings.views.pop(view['view'], None)

  def restore(self, payload, identity, *, authorized):
    if (type(payload) is not dict or set(payload) != {'format', 'version', 'createdAt', 'metric', 'vehicle', 'settings', 'layout'} or
        payload['format'] != FORMAT or type(payload['version']) is not int or payload['version'] != 1 or
        type(payload['metric']) is not bool or type(payload['createdAt']) is not str or len(payload['createdAt']) > 64 or
        payload['vehicle'] is not None and (type(payload['vehicle']) is not str or len(payload['vehicle']) > 128) or
        type(payload['settings']) is not list or len(payload['settings']) > MAX_SETTINGS or
        len(json.dumps(payload, allow_nan=False).encode()) > MAX_BYTES):
      raise ValueError('Invalid toggle backup')
    document = validate_document(payload['layout']) if payload['layout'] is not None else None
    if document is not None:
      take_rotation(document)  # Portable backups never apply device-local rotation.
    ctx = self.settings.context.sample()
    if not authorized() or not ctx.parked:
      raise SettingsChanged('Park the vehicle before restoring toggles')
    if payload['metric'] != ctx.metric:
      raise ValueError('Use the same metric or imperial units as the backup before restoring')
    rows = {(page, row.key): row for page, row in self._rows(ctx)}
    plan, seen, skipped = [], set(), []
    # Validate every editable value before the first write. Preview uses the same
    # enum/range/step checks and confirmation contracts as the Toggles page.
    for entry in payload['settings']:
      if (type(entry) is not dict or set(entry) != {'page', 'key', 'value'} or
          type(entry['page']) is not str or entry['page'] not in PAGES or type(entry['key']) is not str or
          len(entry['key']) > 128 or type(entry['value']) is not str or len(entry['value']) > 128):
        raise ValueError('Invalid setting in toggle backup')
      key = (entry['page'], entry['key'])
      if entry['key'] in seen:
        raise ValueError('Duplicate setting in toggle backup')
      seen.add(entry['key'])
      row = rows.get(key)
      different_vehicle = payload['vehicle'] != getattr(ctx.cp, 'carFingerprint', None)
      if row is None or not row.available or different_vehicle and (row.capability or row.vehicle_fingerprint):
        skipped.append(entry['key'])
        continue
      intent = self._preview(entry, identity, ctx)
      with self.settings.lock:
        self.settings.intents.pop(intent['intent'], None)
      plan.append(entry)
    if document is not None and not self.layout.snapshot()['editable']:
      raise SettingsChanged('Park the vehicle before restoring the layout')
    restored = unchanged = 0
    for entry in plan:
      try:
        fresh = self.settings.context.sample()
        if not authorized() or not fresh.parked or fresh.cp_raw != ctx.cp_raw or fresh.metric != ctx.metric:
          raise SettingsChanged('Session, vehicle or parked state changed')
        rows = self.settings._state(entry['page'], fresh).rows
        index = next((i for i, row in enumerate(rows) if row.key == entry['key']), None)
        if index is None:
          raise SettingsChanged('A setting is no longer available')
        if not rows[index].available:
          skipped.append(entry['key'])
          continue
        if rows[index].value == entry['value']:
          unchanged += 1
          continue
        intent = self._preview(entry, identity, fresh)
        if not self.settings.confirm(intent['intent'], *identity, session_valid=authorized):
          raise SettingsChanged('Saved settings changed')
        restored += 1
      except (SettingsChanged, OSError, RuntimeError) as error:
        return {'complete': False, 'restored': restored, 'unchanged': unchanged, 'skipped': skipped,
                'error': f'Restore stopped after {restored} changes: {error}. Review Toggles before retrying.'}
    if document is not None:
      try:
        revision = self.layout.snapshot()['revision']
        result = self.layout.save({'revision': revision, 'document': document}, session_valid=authorized)
        if result['failed']:
          raise LayoutChanged('Could not apply: ' + ', '.join(result['failed']))
      except (LayoutChanged, OSError, RuntimeError) as error:
        return {'complete': False, 'restored': restored, 'unchanged': unchanged, 'skipped': skipped,
                'error': f'Toggles restored, but the layout could not be restored: {error}'}
    return {'complete': True, 'restored': restored, 'unchanged': unchanged, 'skipped': skipped}
