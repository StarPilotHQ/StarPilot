"""Galaxy stored-record, layout-preview and drive-state HTTP routes."""

from openpilot.starpilot.galaxy.crash_reports import CrashChanged, CrashMissing, CrashUnavailable
from openpilot.starpilot.galaxy.settings import SettingsChanged, SettingsUnavailable
from openpilot.starpilot.galaxy.sentry_events import ID as SENTRY_EVENT_ID, SentryEventsUnavailable
from openpilot.starpilot.sentry_mode.notifications import NotificationUnavailable
from openpilot.starpilot.ui.layout_preview_transport import (PreviewBusy, PreviewDenied, PreviewInvalid, PreviewUnavailable,
                                                            PROFILES as PREVIEW_PROFILES, SCENES as PREVIEW_SCENES, request_preview,
                                                            validate_preview_document)


def delete_records(self, path, payload, sentry_events_lock, crash_reports_lock, notification_owner, motion_events, reports):
  identity = self.settings_session()
  if identity is None:
    self.json(401, {'error': 'Sign in to Galaxy'})
    return
  sentry = path == '/api/sentry/events/delete'
  key = 'eventId' if sentry else 'id'
  try:
    if type(payload) is not dict:
      raise ValueError('Invalid deletion selection')
    if set(payload) == {'all', 'confirmed'} and payload['all'] is True and payload['confirmed'] is True:
      selected = None
    elif set(payload) == {key} and type(payload[key]) is str and payload[key]:
      selected = payload[key]
      if sentry and SENTRY_EVENT_ID.fullmatch(selected) is None:
        raise ValueError('Invalid event ID')
    else:
      raise ValueError('Confirm deleting all stored records')

    def permitted():
      return self.settings_session() == identity

    with sentry_events_lock if sentry else crash_reports_lock:
      if not permitted():
        raise PermissionError('Galaxy session expired')
      if sentry and notification_owner is not None:
        result = notification_owner.delete_events(lambda: motion_events.delete(selected, permitted=permitted), selected)
      else:
        result = (motion_events if sentry else reports).delete(selected, permitted=permitted)
  except (ValueError, CrashMissing):
    self.json(400, {'error': 'Invalid deletion selection or confirmation'})
  except PermissionError:
    self.json(401, {'error': 'Sign in to Galaxy'})
  except CrashChanged:
    self.json(409, {'error': 'Report changed; refresh before deleting'})
  except (OSError, CrashUnavailable, SentryEventsUnavailable, NotificationUnavailable):
    self.json(503, {'error': 'Stored records could not be fully deleted; refresh and retry'})
  else:
    if permitted():
      self.json(200, result)
    else:
      self.json(401, {'error': 'Sign in to Galaxy'})


def widget_art_header(image):
  """Encode bounded PNG sprite metadata for the preview response header."""
  import base64
  import struct
  signature = b'starpilot-widget-art\0'
  offset = 8
  while offset + 12 <= len(image):
    size, kind = struct.unpack('!I4s', image[offset:offset + 8])
    if kind == b'tEXt' and image[offset + 8:offset + 8 + len(signature)] == signature:
      text = image[offset + 8 + len(signature):offset + 8 + size]
      return base64.b64encode(text).decode() if len(text) <= 8192 else ''
    offset += size + 12
  return ''


def preview_layout(self, payload, projection_layout_owner, layout_owner, layout_preview_socket):
  identity = self.settings_session()
  if identity is None:
    self.json(401, {'error': 'Sign in to Galaxy'})
    return
  if (type(payload) is not dict or set(payload) not in ({'document', 'profile', 'scene'}, {'document', 'profile', 'scene', 'art'}) or
      ('art' in payload and not (payload['art'] is True or payload['art'] == 'backdrop')) or
      type(payload['profile']) is not str or payload['profile'] not in (PREVIEW_PROFILES | {'projection'}) or
      type(payload['scene']) is not str or payload['scene'] not in PREVIEW_SCENES):
    self.json(400, {'error': 'Invalid layout preview request'})
    return
  try:
    if payload['profile'] == 'projection':
      from openpilot.starpilot.system.starport.projection_layout import validate_layout
      from openpilot.starpilot.ui.onroad_customization import read_customization
      snapshot = projection_layout_owner().snapshot()
      if not snapshot['available']:
        self.json(409, {'error': snapshot['reason'] or 'Connect Android Auto before previewing'})
        return
      document = {'layout': validate_layout(payload['document'], snapshot['screen']),
                  'base': read_customization(layout_owner().params)}
    else:
      document = validate_preview_document(payload['document'])
  except (ValueError, TypeError, OverflowError, RecursionError):
    self.json(400, {'error': 'Invalid layout document'})
    return
  try:
    if not layout_owner().parked():
      self.json(403, {'error': 'Turn off the vehicle to preview this layout'})
      return
    image = request_preview({'document': document, 'profile': payload['profile'], 'scene': payload['scene'],
                             **({'art': payload['art']} if 'art' in payload else {})},
                            layout_preview_socket)
    if self.settings_session() != identity:
      self.json(401, {'error': 'Sign in to Galaxy'})
    elif not layout_owner().parked():
      self.json(403, {'error': 'Turn off the vehicle to preview this layout'})
    else:
      headers, cache = {}, None
      if payload.get('art') is True:
        # Proxies that re-encode PNGs drop the tEXt chunk, so the sprite sheet also rides in a header.
        header = widget_art_header(image)
        if header:
          headers['X-Widget-Art'] = header
        cache = 'no-store, no-transform'
      self.respond(200, image, 'image/png', asset_headers=headers or None, cache_control=cache)
  except PreviewDenied:
    self.json(403, {'error': 'Turn off the vehicle to preview this layout'})
  except PreviewBusy:
    self.json(429, {'error': 'Preview is busy; try again shortly'})
  except (PreviewInvalid, PreviewUnavailable, OSError, RuntimeError):
    self.json(503, {'error': 'Onroad UI preview is unavailable'})
  return


def change_drive_state(self, payload, effect_lock, drive_owner):
  identity = self.settings_session()
  if identity is None:
    self.json(401, {'error': 'Sign in to Galaxy'})
    return
  if (type(payload) is not dict or set(payload) != {'mode', 'revision'} or
      type(payload['mode']) is not str or type(payload['revision']) is not str or
      len(payload['revision']) != 32):
    self.json(400, {'error': 'Invalid drive state request'})
    return
  from openpilot.starpilot.drive_state.owner import Rejected
  try:
    with effect_lock:
      drive_owner().change_galaxy(payload['mode'], payload['revision'], lambda: self.settings_session() == identity)
    result = drive_owner().snapshot()
  except Rejected as error:
    self.json(409, {'error': str(error)})
  except (OSError, RuntimeError):
    self.json(503, {'error': 'Drive state is unavailable'})
  else:
    if self.settings_session() == identity:
      self.json(200, result)
    else:
      self.json(401, {'error': 'Sign in to Galaxy'})
  return


def non_default_settings(self, settings_recovery):
  if not self.require_session():
    return
  identity = self.settings_session()
  if identity is None:
    self.json(401, {'error': 'Sign in to Galaxy'})
    return
  try:
    result = settings_recovery().snapshot(*identity)
  except SettingsChanged:
    self.json(409, {'error': 'Saved settings changed; refresh the page'})
  except (SettingsUnavailable, OSError, ValueError, RuntimeError):
    self.json(503, {'error': 'Saved settings are unavailable'})
  else:
    if self.settings_session() == identity:
      self.json(200, result)
    else:
      self.json(401, {'error': 'Sign in to Galaxy'})


def preview_settings_recovery(payload, settings_recovery, identity):
  if (not isinstance(payload, dict) or set(payload) != {'view', 'selected'} or
      not isinstance(payload['view'], str) or len(payload['view']) > 64 or
      not isinstance(payload['selected'], list) or not 1 <= len(payload['selected']) <= 128 or
      any(type(index) is not int for index in payload['selected'])):
    raise ValueError('Invalid settings recovery selection')
  return settings_recovery().preview(payload['view'], payload['selected'], *identity)
