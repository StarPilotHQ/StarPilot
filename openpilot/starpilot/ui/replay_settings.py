"""Native route picker and passive replay viewer; the shared owner runs playback."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import math
import time
import uuid

from openpilot.starpilot.ui.raylib import rl

from openpilot.selfdrive.ui.mici.widgets.button import BigButton, GreyBigButton
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.starpilot.ui.clip import placed_at
from openpilot.starpilot.ui.feature_settings import FeatureSettingsView
from openpilot.starpilot.ui.feature_settings_state import (FEATURE_ROW_HEIGHT, FeatureInput, FeatureRow, FeatureSettingsState,
                                                         FeatureUiAction, feature_scroll)
from openpilot.starpilot.ui.onroad_customization import read_customization
from openpilot.starpilot.ui.presentation import FontRole, Profile
from openpilot.starpilot.replay.timeline import LEGEND, TimelineHistory
from openpilot.starpilot.ui.settings_geometry import (ACCENT, CONTROL_BG, CONTROL_BORDER, CONTROL_PRESSED_BG,
                                                     PANEL_BG, ROW_SEPARATOR, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY, draw_rounded_fill,
                                                     draw_rounded_stroke, draw_settings_header, elide_text)
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.scroll_panel2 import MIN_DRAG_PIXELS
from openpilot.system.ui.lib.wrap_text import wrap_text
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.nav_widget import NavWidget
from openpilot.system.ui.widgets.scroller import Scroller


ACTIVE_STATES = frozenset(('preparing', 'playing', 'paused', 'seeking', 'buffering', 'ended', 'stopping'))
TIMELINE_NEUTRAL = rl.Color(54, 60, 71, 255)


@dataclass(frozen=True)
class ReplayRouteRow(FeatureRow):
  segment_count: int = 0


def _request(payload):
  from openpilot.starpilot.replay.client import request_operation
  return request_operation(payload)


def _reader(path, session_id, profile):
  from openpilot.starpilot.replay.frames import ReplayFrameReader
  return ReplayFrameReader(path, session_id, profile=profile)


def duration(value):
  if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
    return 'Unavailable'
  minutes, seconds = divmod(round(value), 60)
  return f'{minutes}:{seconds:02d}'


def route_date(route, profile, clock24_hour=False):
  for key in ('startTime', 'fileTime'):
    value = route.get(key)
    if type(value) not in (int, float):
      continue
    try:
      if not math.isfinite(value) or value <= 0:
        continue
      date = datetime.fromtimestamp(value).astimezone()
      calendar = date.strftime('%a, %b %-d, %Y' if profile == Profile.LARGE else '%b %-d, %Y')
      clock = date.strftime('%H:%M') if clock24_hour else date.strftime('%I:%M %p').lstrip('0')
      return calendar, clock, date.tzname() or '', 'File date' if key == 'fileTime' else ''
    except (OSError, OverflowError, ValueError):
      continue
  return 'Date unavailable', '', '', ''


class ReplayController:
  """One bounded worker for IPC and frame copies; graphics stay on the UI thread."""

  def __init__(self, requester=_request, reader_factory=_reader, clock=time.monotonic):
    self.requester, self.reader_factory, self.clock = requester, reader_factory, clock
    self.client_id = uuid.uuid4().hex
    self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='native-replay')
    self.pending = None
    self.pending_op = ''
    self.queued = None
    self.status = {}
    self.routes = []
    self.detail = None
    self.error = ''
    self.frame = None
    self.autoplay_at = None
    self.own_session = None
    self.selected_route = None
    self.reader = None
    self.reader_key = None
    self.reader_generation = 0
    self.closed = False
    self.catalog_loaded = False
    self.next_status = self.next_catalog = self.next_frame = 0.0
    self.status_at = None
    self.timeline = None
    self.timeline_error = ''
    self.next_timeline = 0.0

  @property
  def busy(self):
    return self.queued is not None or self.pending is not None and self.pending_op in ('detail', 'start', 'control', 'stop')

  @property
  def active(self):
    return bool(self.own_session and self.status.get('sessionId') == self.own_session and
                self.status.get('state') in ACTIVE_STATES)

  @property
  def available(self):
    return bool(self.status.get('ownerId') and self.status.get('offroad') is True and self.status_at is not None and
                0 <= self.clock() - self.status_at < 2.0)

  def submit(self, payload):
    if self.closed:
      return False
    if self.pending is not None:
      if self.pending_op in ('frame', 'status', 'catalog', 'timeline') and self.queued is None:
        self.queued = payload
        return True
      return False
    self.pending_op = payload['op']
    self.pending = self.executor.submit(self.requester, payload)
    return True

  def select(self, route):
    if self.busy:
      return False
    self.selected_route = route['routeId']
    self.detail = route
    self.error = ''
    return self.submit({'op': 'detail', 'routeId': self.selected_route})

  def start(self, segment=None):
    if (not self.available or self.status.get('state') in ACTIVE_STATES or self.selected_route is None or
        not (self.detail or {}).get('playable')):
      return False
    self.error = ''
    payload = {'op': 'start', 'routeId': self.selected_route, 'target': 'native',
               'clientId': self.client_id, 'requestId': uuid.uuid4().hex, 'expectedOwnerId': self.status['ownerId']}
    if segment is not None:
      payload['segment'] = segment
    submitted = self.submit(payload)
    if submitted:
      self.autoplay_at = math.inf  # Wait for the first accepted frame before starting the delay.
    return submitted

  def control(self, action, **value):
    if not self.available or not self.active or self.status.get('state') not in ('playing', 'paused', 'buffering', 'ended'):
      return False
    submitted = self.submit({'op': 'control', 'sessionId': self.own_session, 'clientId': self.client_id,
                             'expectedRevision': self.status['revision'], 'requestId': uuid.uuid4().hex,
                             'action': action, **value})
    if submitted:
      self.autoplay_at = None
    return submitted

  def seek(self, position):
    if type(position) not in (int, float) or not math.isfinite(position):
      return False
    start, end = self.status.get('start'), self.status.get('end')
    if start is None or end is None or type(start) not in (int, float) or type(end) not in (int, float) or end <= start:
      return False
    position = max(start, min(end, position))
    ranges = self.status.get('seekRanges')
    if ranges and not any(left <= position <= right for left, right in ranges):
      position = min((point for bounds in ranges for point in bounds), key=lambda point: abs(point - position))
      self.error = 'Unavailable gap; seeking to available footage'
    return self.control('seek', position=position)

  def stop(self):
    self.autoplay_at = None
    if not self.own_session:
      return False
    return self.submit(self._stop_payload(self.own_session))

  def _stop_payload(self, session):
    return {'op': 'stop', 'sessionId': session, 'clientId': self.client_id, 'requestId': uuid.uuid4().hex}

  def _read_frame(self, key):
    if key != self.reader_key:
      self._close_reader()
      self.reader = self.reader_factory(*key[:3])
      self.reader_key = key
    return self.reader.latest()

  def _close_reader(self):
    if self.reader is not None:
      self.reader.close()
    self.reader = self.reader_key = None

  def tick(self):
    if self.closed:
      return
    now = self.clock()
    if self.pending is not None and self.pending.done():
      future, operation = self.pending, self.pending_op
      self.pending, self.pending_op = None, ''
      try:
        response = future.result()
        if operation == 'frame':
          if response is not None:
            if response.session_id == self.own_session and response.epoch == self.status.get('epoch'):
              self.frame = response
              if self.autoplay_at == math.inf:
                self.autoplay_at = now + 1.0
            else:
              self.frame = None
              self.next_status = 0.0
        elif response.get('ok') is not True:
          if operation == 'timeline':
            self.timeline_error = 'History unavailable'
            self.timeline = None
          else:
            self.error = response.get('error', {}).get('message', 'Replay unavailable')
            self.next_status = 0.0
        else:
          result = response['result']
          if operation == 'catalog':
            self.routes = result['routes']
            self.catalog_loaded = True
          elif operation == 'detail':
            self.detail = result
          elif operation == 'timeline':
            if self.timeline is not None:
              self.timeline.append(result, self.status.get('seekRanges') or [[self.status['start'], self.status['end']]])
          else:
            if result.get('schemaVersion') != 1 or type(result.get('revision')) is not int:
              raise ValueError('Invalid replay status')
            if operation == 'start' or result.get('owned') is True:
              self.own_session = result['sessionId']
            if (result.get('sessionId'), result.get('epoch'), result.get('frameReady')) != (
                self.status.get('sessionId'), self.status.get('epoch'), self.status.get('frameReady')):
              self.frame = None
              self.reader_generation += 1
            prior_identity = self.status.get('ownerId'), self.status.get('sessionId')
            self.status, self.status_at = result, now
            if (prior_identity != (result['ownerId'], result['sessionId']) or
                not self.active or not result.get('owned') or not result.get('offroad') or result.get('state') == 'stopping' or
                self.timeline is not None and (self.timeline.owner, self.timeline.session) != (result['ownerId'], result['sessionId'])):
              self.timeline = None
              self.timeline_error = ''
            if self.active and result.get('owned') and result.get('offroad') and result.get('timelineState') == 'unavailable':
              self.timeline = None
              self.timeline_error = 'History unavailable'
            elif (self.active and result.get('owned') and result.get('offroad') and result.get('state') != 'stopping' and
                  result.get('timelineState') == 'ready' and self.timeline is None and not self.timeline_error):
              self.timeline = TimelineHistory(result['ownerId'], result['sessionId'])
            self.error = result.get('errorCode') or ''
            if result.get('paused') is False or result.get('state') in ('ended', 'failed'):
              self.autoplay_at = None
            if not self.active or result.get('offroad') is not True or not result.get('frameReady'):
              self.frame = None
      except Exception as error:
        if operation == 'timeline':
          self.timeline, self.timeline_error = None, 'History unavailable'
        elif operation == 'frame':
          self.error = 'Replay frame unavailable'
          self.frame = None
        else:
          self.error = str(error) or 'Replay service unavailable'
          self.status_at = None
    if self.pending is not None:
      return
    if self.queued is not None:
      payload, self.queued = self.queued, None
      self.submit(payload)
    elif (self.autoplay_at is not None and now >= self.autoplay_at and self.frame is not None and
          self.available and self.active and self.status.get('state') == 'paused' and not self.error):
      self.control('pause', paused=False)
    elif now >= self.next_catalog and not self.active:
      self.next_catalog = now + 5.0
      self.submit({'op': 'catalog'})
    elif now >= self.next_status:
      self.next_status = now + 0.25
      self.submit({'op': 'status', 'clientId': self.client_id})
    elif self.active and self.timeline is not None and self.timeline.rows is None and now >= self.next_timeline:
      self.next_timeline = now + .05
      self.submit({'op': 'timeline', 'clientId': self.client_id, 'sessionId': self.own_session, 'cursor': self.timeline.cursor})
    elif self.active and self.status.get('frameReady') and self.status.get('framePath') and now >= self.next_frame:
      # Poll below the 60 Hz UI interval to avoid skipping frames.
      self.next_frame = now + 0.01
      key = (self.status['framePath'], self.own_session, self.status.get('profile'), self.reader_generation)
      self.pending_op = 'frame'
      self.pending = self.executor.submit(self._read_frame, key)

  def close(self):
    if self.closed:
      return
    self.closed = True
    self.autoplay_at = None
    if self.pending_op == 'start' or self.pending_op == 'status' and not self.own_session:
      def stop_late_start(future):
        try:
          response = future.result()
          if (response.get('ok') is True and response['result'].get('sessionId') and
              response['result'].get('owned') is True):
            self.requester(self._stop_payload(response['result']['sessionId']))
        except Exception:
          pass  # The owner also expires the native lease after disconnect.
      self.pending.add_done_callback(stop_late_start)
    elif self.own_session:
      self.executor.submit(self.requester, self._stop_payload(self.own_session))
    self.executor.submit(self._close_reader)
    self.executor.shutdown(wait=False)
    self.frame = None
    self.timeline = None


class ReplayPage(Widget):
  def __init__(self, session, on_close, controller=None):
    super().__init__()
    self.fonts, self.profile = session.fonts, session.profile
    self.controller = controller or ReplayController()
    self.on_close = on_close
    self.page = 'routes'
    self.scroll = 0
    self.view = FeatureSettingsView(self.fonts) if self.profile == Profile.LARGE else None
    self.input = FeatureInput(self._action)
    if self.view:
      self.view.pressed_action = self.input.pressed_action
    self.cards = self._child(Scroller(snap_items=True)) if self.profile == Profile.COMPACT else None
    if self.cards:
      self.cards.set_enabled(lambda: self.enabled)
    self.card_signature = None
    self.route_rows = ()
    self.route_source = None
    self.clock24_hour = read_customization(ui_state.params).get('clock24Hour', False)
    self.detail_source = None
    self.detail_text = ('Recorded route', 'Date unavailable', '')
    self.detail_message = ''
    self.texture = None
    self.rail_texture = self.rail_key = None
    self.frame_key = None
    self.press = None
    self.seek_preview = None
    self.controls = True
    self.last_touch = time.monotonic()
    self.stopping = False
    self._epoch = None
    self.stack_widget: Widget = self

  def show_event(self):
    super().show_event()
    gui_app.add_nav_stack_tick(self._tick)
    gui_app.add_render_prepare(self._prepare_rail, self._clear_rail)

  def hide_event(self):
    gui_app.remove_nav_stack_tick(self._tick)
    gui_app.remove_render_prepare(self._prepare_rail)
    self.controller.close()
    self._clear_frame()
    self._clear_rail()
    self.input.cancel()
    self.press = self.seek_preview = None
    super().hide_event()

  def _tick(self):
    self.controller.tick()
    if self.controller.active and self.controller.available and self.controller.status.get('paused') is False:
      device._reset_interactive_timeout()
    if ui_state.started and gui_app.widget_in_stack(self.stack_widget):
      self.on_close()

  def _clear_frame(self):
    if self.texture is not None:
      rl.unload_texture(self.texture)
    self.texture = self.frame_key = None

  def _clear_rail(self):
    if getattr(self, 'rail_texture', None) is not None:
      rl.unload_render_texture(self.rail_texture)
    self.rail_texture = self.rail_key = None

  def _rail_size(self):
    return (28, 6, 2) if self.profile == Profile.LARGE else (16, 4, 2)

  def _prepare_rail(self):
    history = getattr(self.controller, 'timeline', None)
    large = self.profile == Profile.LARGE
    (_, _, width, _), _buttons = self._transport()
    start, end = (self.controller.status.get(key) or 0 for key in ('start', 'end'))
    ranges = self.controller.status.get('seekRanges') or ((start, end),)
    rail, tick, gap = self._rail_size()
    if history is None or history.rows is None or end <= start:
      self._clear_rail()
      return
    scale = max(.1, gui_app._scale)
    pixels = max(1, round(width * scale))
    paint_h = max(1, round((rail + tick + gap) * scale))
    key = id(history), start, end, tuple(map(tuple, ranges)), pixels, paint_h, large
    if getattr(self, 'rail_key', None) != key:
      self._clear_rail()
      target = rl.load_render_texture(pixels, paint_h)
      if not target.id:
        return
      self.rail_texture, self.rail_key = target, key
      rl.begin_texture_mode(target)
      rl.clear_background(rl.BLANK)
      line_y, line_h = round((tick + gap) * scale), max(1, round(rail * scale))
      def rect(left, right, color, bookmark=False):
        lo = max(0, min(pixels - 1, math.floor(pixels * (left - start) / (end - start))))
        minimum = max(1, round((4 if large else 3) * scale)) if bookmark else 1
        hi = min(pixels, max(lo + minimum, math.ceil(pixels * (right - start) / (end - start))))
        if bookmark:
          lo = max(0, hi - minimum)
        rl.draw_rectangle(lo, 0 if bookmark else line_y, hi - lo, max(1, round(tick * scale)) if bookmark else line_h, color)
      for left, right in ranges:
        rect(left, right, TIMELINE_NEUTRAL)
      for layer in ('mode', 'error', 'bookmark'):
        for left, right, kind in history.rows:
          if (kind if kind in ('error', 'bookmark') else 'mode') == layer:
            rect(left, right, rl.Color(*LEGEND[kind]['rgb'], 255), layer == 'bookmark')
      rl.end_texture_mode()

  def _timeline_rail(self, x, y, width, height, start, end, ranges):
    history = getattr(self.controller, 'timeline', None)
    rail, tick, gap = self._rail_size()
    top = y + (height - rail - tick - gap) / 2
    target = getattr(self, 'rail_texture', None)
    if target is not None and history is not None and self.rail_key is not None and self.rail_key[0] == id(history):
      rl.draw_texture_pro(target.texture, rl.Rectangle(0, 0, target.texture.width, -target.texture.height),
                          rl.Rectangle(x, top, width, rail + tick + gap), rl.Vector2(0, 0), 0, rl.WHITE)
    elif end > start:
      for left, right in ranges:
        rl.draw_rectangle(round(x + width * (left - start) / (end - start)), round(top + tick + gap),
                          max(1, round(width * (right - left) / (end - start))), rail, TIMELINE_NEUTRAL)
    return top + tick + gap, rail

  def _scrub_preview(self, head_x, position, label, entry):
    large = self.profile == Profile.LARGE
    size, padding, swatch = (28, 22, 14) if large else (18, 16, 10)
    text = elide_text(self.fonts, f'{duration(position)} - {label}', FontRole.MEDIUM, size, 460)
    measured = self.fonts.measure(text, FontRole.MEDIUM, size)
    width = measured.width + padding * 3 + swatch if large else 520
    left = max(100, min(2060 - width, head_x - width / 2)) if large else 8
    top, height = (784, 62) if large else (8, 42)
    bounds = rl.Rectangle(left, top, width, height)
    radius = 14 if large else 10
    draw_rounded_fill(bounds, rl.Color(8, 8, 10, 245), radius_px=radius)
    draw_rounded_stroke(bounds, TEXT_MUTED, radius_px=radius)
    if large:
      rl.draw_line(round(head_x), top + height + 4, round(head_x), 868, TEXT_MUTED)
    color = rl.Color(*entry['rgb'], 255) if entry else TEXT_MUTED
    rl.draw_rectangle(round(left + padding), round(top + (height - swatch) / 2), swatch, swatch, color)
    self.fonts.draw(text, FontRole.MEDIUM, size, left + padding * 2 + swatch, top + (height - measured.height) / 2, TEXT_PRIMARY)

  def _update_state(self):
    self.controller.tick()
    epoch = (self.controller.status.get('sessionId'), self.controller.status.get('epoch'))
    if epoch != self._epoch:
      self.press = self.seek_preview = None
      self.input.cancel()
      self._epoch = epoch
    if self.controller.active and not self.stopping:
      self.page = 'viewer'
    elif self.page == 'viewer' and self.controller.own_session and not self.controller.busy:
      self.page = 'detail'
      self.stopping = False
    if not self.controller.active and not self.controller.busy:
      self.stopping = False
    if self.controller.frame is None or not self.controller.available or ui_state.started:
      self._clear_frame()

  def _rows(self):
    owner = self.controller
    if self.route_source is not owner.routes:
      self.route_source = owner.routes
      rows = []
      for route in sorted(owner.routes, key=lambda route: route.get('startTime') or route.get('fileTime') or 0, reverse=True):
        calendar, clock, _, source = route_date(route, self.profile, self.clock24_hour)
        identifier = route['routeId']
        name = route.get('displayName') or identifier
        named = name != identifier
        count = route.get('segmentCount', 0)
        segments = f'{count} segment' + ('s' if count != 1 else '')
        label = name if named or not clock else calendar + (f' - {clock}' if self.profile == Profile.LARGE else '')
        if self.profile == Profile.LARGE:
          label = name if named else calendar + (f' - {clock}' if clock else '')
          summary = ' - '.join(part for part in (calendar, clock, identifier) if part) if named else identifier
          value = ' - '.join(part for part in (summary, source) if part)
        else:
          if named and clock:
            date = f'{source}: {calendar}' if source else calendar
            value = f'{date}\n{clock} - {segments}'
          else:
            value = f'{clock or calendar}\n' + ' - '.join(part for part in (source, segments) if part)
        reason = route.get('reason') or (route.get('availability', '') if route.get('availability') != 'ready' else '')
        if self.profile == Profile.COMPACT and named and source and not route.get('reason'):
          reason = ''  # Keep the date source visible within the card's two subtitle lines.
        rows.append(ReplayRouteRow(identifier, label, value, available=True, reason=reason, segment_count=count,
                                   page='detail' if self.profile == Profile.LARGE else '',
                                   actions=() if self.profile == Profile.LARGE else (('DETAILS', True),)))
      self.route_rows = tuple(rows)
    return self.route_rows or (FeatureRow('', 'No recorded routes' if owner.catalog_loaded else 'Loading recorded routes',
                                         'Drives recorded on this device will appear here.' if owner.catalog_loaded else 'Reading routes on this device...'),)

  def _render_route_row(self, row, y, left, pressed):
    x, right = left + 55, 2094
    if row.page and pressed == FeatureUiAction('open', row):
      draw_rounded_fill(rl.Rectangle(left + 25, y + 2, 2100 - left, FEATURE_ROW_HEIGHT - 8), CONTROL_PRESSED_BG, radius_px=12)
    rl.draw_line(x, y + FEATURE_ROW_HEIGHT - 3, right, y + FEATURE_ROW_HEIGHT - 3, ROW_SEPARATOR)
    width = right - x - (360 if row.page else 0)
    detail = ' - '.join(part for part in (row.value, row.reason) if part)
    label = elide_text(self.fonts, row.label, FontRole.MEDIUM, 46, width)
    detail = elide_text(self.fonts, detail, FontRole.NORMAL, 30, width)
    top, bottom = self.fonts.vertical_ink(label, FontRole.MEDIUM, 46)
    detail_top, detail_bottom = self.fonts.vertical_ink(detail, FontRole.NORMAL, 30) if detail else (0, 0)
    height = bottom - top + (14 + detail_bottom - detail_top if detail else 0)
    label_y = y + (FEATURE_ROW_HEIGHT - height) / 2 - top
    self.fonts.draw(label, FontRole.MEDIUM, 46, x, label_y, TEXT_PRIMARY)
    if detail:
      self.fonts.draw(detail, FontRole.NORMAL, 30, x, label_y + bottom + 14 - detail_top, TEXT_SECONDARY)
    if row.page:
      count = f'{row.segment_count} segment' + ('s' if row.segment_count != 1 else '')
      measured = self.fonts.measure(count, FontRole.NORMAL, 32)
      top, bottom = self.fonts.vertical_ink(count, FontRole.NORMAL, 32)
      self.fonts.draw(count, FontRole.NORMAL, 32, right - 76 - measured.width,
                      y + (FEATURE_ROW_HEIGHT - top - bottom) / 2, TEXT_SECONDARY)
      cx, cy = right - 12, y + FEATURE_ROW_HEIGHT / 2
      rl.draw_line_ex(rl.Vector2(cx - 12, cy - 12), rl.Vector2(cx, cy), 3, TEXT_MUTED)
      rl.draw_line_ex(rl.Vector2(cx, cy), rl.Vector2(cx - 12, cy + 12), 3, TEXT_MUTED)

  def _play_available(self):
    owner = self.controller
    return owner.available and not owner.busy and bool((owner.detail or {}).get('playable')) and owner.status.get('state') not in ACTIVE_STATES

  def _segments(self):
    return [segment for segment in (self.controller.detail or {}).get('segments', [])
            if (segment['files']['rlog'] or segment['files']['qlog']) and (segment['files']['fcamera'] or segment['files']['qcamera'])]

  def _detail_lines(self, message, width):
    # Match the bitmap renderer, independent of the native app's fallback font.
    remaining, lines = ' '.join(message.split()), []
    for _ in range(2):
      if not remaining:
        break
      if self.fonts.measure(remaining, FontRole.NORMAL, 18).width <= width:
        lines.append(remaining)
        remaining = ''
        break
      line = elide_text(self.fonts, remaining, FontRole.NORMAL, 18, width)[:-3]
      if ' ' in line:
        line = line.rsplit(' ', 1)[0]
      lines.append(line)
      remaining = remaining[len(line):].lstrip()
    return lines, remaining

  def _show_detail_message(self):
    if not self.detail_message:
      return
    from openpilot.selfdrive.ui.mici.widgets.dialog import SettingDescriptionDialog
    font = gui_app.font(FontWeight.ROMAN)
    paragraphs = []
    for paragraph in self.detail_message.splitlines():
      # Native cards cannot paginate inside one oversized path or error token.
      words = ['\n'.join(wrap_text(font, word, 36, 396)) for word in paragraph.split()]
      paragraphs.append(' '.join(words))
    dialog = SettingDescriptionDialog('replay', '\n'.join(paragraphs))
    dialog._scroller.items.pop(0)  # Open directly on the message instead of the help cover.
    gui_app.push_widget(dialog)

  def _choose_segment(self):
    if not self._play_available():
      return
    options = {f"Segment {segment['number']}": segment['number'] for segment in self._segments()}
    if not options:
      return
    route_id = self.controller.selected_route
    def play_segment(number):
      if (self.page == 'detail' and self.controller.selected_route == route_id and
          gui_app.get_active_widget() is self.stack_widget):
        self.controller.start(segment=number)
    if self.profile == Profile.COMPACT:
      from openpilot.system.ui.widgets.scroller import NavScroller
      picker = NavScroller()
      def selected_segment(number):
        if gui_app.get_active_widget() is picker:
          gui_app.pop_widget()
          play_segment(number)
      calendar = route_date(self.controller.detail, self.profile, self.clock24_hour)[0]
      for label, number in options.items():
        button = BigButton(label.lower(), f'{calendar.lower()}\nstart here')
        button._sub_label.set_font_size(30)
        button.set_click_callback(lambda number=number: selected_segment(number))
        picker._scroller.add_widgets([button])
      gui_app.push_widget(picker)
      return
    from openpilot.system.ui.widgets import DialogResult
    from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
    def selected(result):
      if result == DialogResult.CONFIRM and picker.selection in options:
        play_segment(options[picker.selection])
    picker = MultiOptionDialog('Play from segment', list(options), callback=selected)
    picker.select_button.set_text('Play')
    gui_app.push_widget(picker)

  def _detail_buttons(self):
    available = self._play_available()
    if self.profile == Profile.COMPACT:
      message = (('More', (420, 112, 104, 44), True, self._show_detail_message),) if self.detail_message else ()
      return (('< Back', (12, 22, 88, 48), True, self._back), *message,
              ('Choose segment', (12, 164, 200, 64), available and bool(self._segments()), self._choose_segment),
              ('Play from beginning', (224, 164, 300, 64), available, self.controller.start))
    return (('< Back', (20, 12, 208, 88), True, self._back),
            ('Choose segment', (72, 900, 640, 144), available and bool(self._segments()), self._choose_segment),
            ('Play from beginning', (744, 900, 1344, 144), available, self.controller.start))

  def _render_detail(self):
    owner = self.controller
    route = owner.detail or {}
    large = self.profile == Profile.LARGE
    if large:
      draw_settings_header(self.fonts, False, 'Route details', 'Replay', back=True)
    if self.detail_source is not owner.detail:
      self.detail_source = owner.detail
      calendar, clock, zone, source = route_date(route, self.profile, self.clock24_hour)
      identifier = route.get('routeId') or owner.selected_route or ''
      name = route.get('displayName') or identifier
      named = name != identifier
      date_line = ' - '.join(part for part in (source, calendar if named or not clock else '',
                                               f'{clock} {zone}'.strip()) if part)
      title = name if named else calendar if clock else 'Recorded route'
      self.detail_text = title, date_line, identifier
    title, date_line, identifier = self.detail_text
    if large:
      self._render_large_detail(title, date_line, identifier)
      return
    size, width, x, y = 26, 412, 112, 24
    self.fonts.draw(elide_text(self.fonts, title, FontRole.SEMI_BOLD, size, width), FontRole.SEMI_BOLD, size, x, y, TEXT_PRIMARY)
    size, width, x, y = 18, 412, 112, 57
    self.fonts.draw(elide_text(self.fonts, date_line, FontRole.NORMAL, size, width), FontRole.NORMAL, size, x, y, TEXT_MUTED)
    size, width, x, y = 16, 340, 12, 91
    self.fonts.draw(elide_text(self.fonts, identifier, FontRole.NORMAL, size, width), FontRole.NORMAL, size, x, y, TEXT_MUTED)
    count = route.get('segmentCount', 0)
    segments = f'{count} segment' + ('s' if count != 1 else '')
    width = self.fonts.measure(segments, FontRole.NORMAL, 18).width
    self.fonts.draw(segments, FontRole.NORMAL, 18, 524 - width, 91, TEXT_MUTED)
    reason = owner.error or ('Loading route details...' if owner.busy else route.get('reason'))
    if reason and not owner.error and not owner.busy and not route.get('playable') and 'Choose another route' not in reason:
      reason = reason.rstrip('. ') + '. Choose another route.'
    if not reason and not self._play_available():
      reason = 'Replay is available while offroad and no other player is active'
    if not reason and route.get('segments') is not None and len(self._segments()) != count:
      reason = f'{len(self._segments())} of {count} available to play'
    reason = reason or ''
    self.detail_message = reason.rstrip() + '\n\nReplay timeline: green Chill; cyan AOL; orange Experimental; red alerts; yellow bookmarks.'
    lines, _ = self._detail_lines(reason, 392)
    for index, line in enumerate(lines[:2]):
      if index == 1:
        line = elide_text(self.fonts, line + ' ...', FontRole.NORMAL, 18, 392)
      self.fonts.draw(line, FontRole.NORMAL, 18, 12, 114 + index * 21, TEXT_PRIMARY)
    for label, bounds, enabled, _ in self._detail_buttons():
      self._button(label, bounds, enabled)

  def _render_large_detail(self, title, date_line, identifier):
    owner = self.controller
    route = owner.detail or {}
    def text(value, size, x, y, width=2016, role=FontRole.NORMAL, color=TEXT_PRIMARY):
      self.fonts.draw(elide_text(self.fonts, value, role, size, width), role, size, x, y, color)
    text(title, 64, 72, 180, role=FontRole.SEMI_BOLD)
    text(date_line, 36, 72, 274, color=TEXT_SECONDARY)
    rl.draw_line(72, 366, 2088, 366, ROW_SEPARATOR)
    text('Route ID', 30, 72, 418, color=TEXT_MUTED)
    text(identifier, 38, 72, 472, width=1320, role=FontRole.MEDIUM)
    text('Segments', 30, 1512, 418, color=TEXT_MUTED)
    text(str(route.get('segmentCount', 0)), 38, 1512, 472, width=576, role=FontRole.MEDIUM)
    if not owner.busy and route.get('segments') is not None:
      text(f'{len(self._segments())} available to play', 30, 1512, 532, width=576, color=TEXT_SECONDARY)
    rl.draw_line(72, 624, 2088, 624, ROW_SEPARATOR)
    text('Play the route from the beginning, or choose a segment to start partway through.', 32, 72, 682, color=TEXT_SECONDARY)
    reason = owner.error or ('Loading route details...' if owner.busy else route.get('reason'))
    if not reason and not self._play_available():
      reason = 'Replay is available while offroad and no other player is active'
    if reason:
      text(reason, 32, 72, 800, color=TEXT_MUTED)
    for label, bounds, enabled, _ in self._detail_buttons()[1:]:
      self._button(label, bounds, enabled)

  def _state(self):
    rows = self._rows()
    self.scroll = feature_scroll(self.scroll, 0, len(rows))
    return FeatureSettingsState(page=f'replay:{self.page}:{self.controller.selected_route}', title='Replay',
                                parent_title='Device' if self.page == 'routes' else 'Recorded Routes', rows=rows,
                                subtitle=self.controller.error or (f'{len(self.controller.routes)} recorded route' +
                                                                   ('s' if len(self.controller.routes) != 1 else '') + ' - Newest first'
                                                                   if self.controller.routes else ''),
                                sidebar_expanded=False, scroll=self.scroll,
                                save_hint='Recorded driving routes on this device')

  def _action(self, action):
    if action.kind == 'back':
      self._back()
    elif action.kind == 'scroll':
      self.scroll = feature_scroll(self.scroll, action.direction, len(self._rows()))
    elif action.row in self._rows() and action.kind in ('action', 'open'):
      route = next((route for route in self.controller.routes if route['routeId'] == action.row.key), None)
      if route is not None and self.controller.select(route):
        self.page = 'detail'
    self.input.cancel()

  def _back(self):
    self.press = self.seek_preview = None
    self.detail_message = ''
    self.input.cancel()
    if self.page == 'viewer':
      if self.controller.stop():
        self.stopping = True
        self.page = 'detail'
        self._clear_frame()
    elif self.page == 'detail':
      self.page = 'routes'
    else:
      self.on_close()

  def _compact_cards(self):
    rows = self._rows()
    signature = (self.page, rows, self.controller.busy, self.controller.error)
    if signature == self.card_signature or any(event.left_down for event in gui_app.mouse_events):
      return
    self.card_signature = signature
    cards = []
    for row in rows:
      detail = ' - '.join(part for part in (row.value, row.reason) if part).lower()
      if row.actions:
        button = BigButton(row.label.lower(), detail, scroll=True)
        size = 30
        while size > 24 and any(self.fonts.measure(line, FontRole.NORMAL, size).width > 322 for line in detail.splitlines()):
          size -= 1
        button._sub_label.set_font_size(size)
        button.set_enabled(row.available and not self.controller.busy)
        button.set_click_callback(lambda row=row: self._action(FeatureUiAction('action', row=row)))
      else:
        button = GreyBigButton(row.label.lower(), detail)
      cards.append(button)
    if self.controller.error:
      cards.append(GreyBigButton('replay unavailable', self.controller.error))
    self.cards._scroller.items.clear()
    self.cards._scroller.add_widgets(cards)

  def _render(self, rect):
    if self.profile == Profile.COMPACT and self.page == 'routes':
      self._compact_cards()
      self.cards.render(rect)
      return
    with placed_at(rect, self._parent_rect):
      rl.draw_rectangle(0, 0, *self.profile.size, PANEL_BG)
      if self.page == 'viewer':
        self._render_viewer()
      elif self.page == 'detail':
        self._render_detail()
      elif self.profile == Profile.LARGE:
        self.view.render(self._state(), self.input.drag_x, draw_row=self._render_route_row)

  def _button(self, label, bounds, enabled=True):
    rect = rl.Rectangle(*bounds)
    held = enabled and self.press is not None and self.press[2] == label
    compact = self.profile == Profile.COMPACT
    video = compact and self.page == 'viewer'
    primary = enabled and (self.page == 'detail' and label == 'Play from beginning' or video and label in ('Play', 'Pause'))
    fill = CONTROL_PRESSED_BG if held else rl.Color(8, 8, 10, 235) if video else CONTROL_BG
    if primary:
      if compact:
        fill = rl.color_lerp(ACCENT, PANEL_BG, 0.3 if held else 0.2)
      else:
        fill = rl.color_lerp(ACCENT, PANEL_BG, 0.2) if held else ACCENT
    if self.page == 'viewer' and not self.controls and not compact:
      fill = rl.Color(8, 8, 10, 210)
    draw_rounded_fill(rect, fill)
    draw_rounded_stroke(rect, ACCENT if primary or held else CONTROL_BORDER)
    size = (48 if self.page == 'detail' else 38) if self.profile == Profile.LARGE else 22
    size = min(size, size * (rect.width - 12) / max(1, self.fonts.measure(label, FontRole.MEDIUM, size).width))
    measured = self.fonts.measure(label, FontRole.MEDIUM, size)
    self.fonts.draw(label, FontRole.MEDIUM, size, rect.x + (rect.width - measured.width) / 2,
                    rect.y + (rect.height - measured.height) / 2, TEXT_PRIMARY if enabled else TEXT_MUTED)

  def _transport(self):
    large = self.profile == Profile.LARGE
    timeline = (100, 916, 1960, 48) if large else (16, 64, 504, 48)
    state = self.controller.status
    labels = ('-10 s' if large else '-10s', 'Play' if state.get('paused') else 'Pause',
              '+10 s' if large else '+10s', f"{state.get('rate', 1):g}x", 'Stop')
    return timeline, tuple((label, (100 + index * 400, 976, 360, 84) if large else
                                    (16 + index * 102, 132, 96, 76)) for index, label in enumerate(labels))

  def _collapsed_stop_bounds(self):
    return (1950, 16, 180, 76) if self.profile == Profile.LARGE else (448, 8, 80, 44)

  def _render_viewer(self):
    owner = self.controller
    large = self.profile == Profile.LARGE
    bounds = rl.Rectangle(0, 0, *self.profile.size)
    frame = owner.frame
    if frame is not None and owner.available and not ui_state.started:
      key = (frame.session_id, frame.epoch, frame.frame_id)
      if key != self.frame_key:
        if self.frame_key is None:
          self.last_touch = time.monotonic()
        if self.texture is None or (self.texture.width, self.texture.height) != (frame.width, frame.height):
          self._clear_frame()
          self.texture = rl.load_texture_from_image(rl.Image(None, frame.width, frame.height, 1,
                                                            rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8))
          rl.set_texture_filter(self.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
        rl.update_texture(self.texture, frame.data)
        self.frame_key = key
      rl.draw_texture_pro(self.texture, rl.Rectangle(0, 0, frame.width, frame.height), bounds, rl.Vector2(0, 0), 0, rl.WHITE)
    self.controls = ((large or frame is not None) and
                     (owner.status.get('state') != 'playing' or time.monotonic() - self.last_touch < 3))
    if self.press:
      self.controls = self.press[4]
    scrubbing = self.seek_preview is not None and self.press is not None and self.press[2] == 'seek'
    if not large and not scrubbing:
      draw_rounded_fill(rl.Rectangle(8, 8, 94, 28), rl.Color(8, 8, 10, 235))
    if large or not scrubbing:
      self.fonts.draw('REPLAY', FontRole.SEMI_BOLD, 36 if large else 18, 30 if large else 12, 30 if large else 12, ACCENT)
    if not self.controls:
      self._button('Stop', self._collapsed_stop_bounds(), not owner.busy)
      self._render_loading()
      return
    if large:
      rl.draw_rectangle(0, 872, self.profile.size[0], 208, rl.Color(8, 8, 10, 190))
    else:
      draw_rounded_fill(rl.Rectangle(8, 70, 520, 36), rl.Color(8, 8, 10, 225))
    timeline, buttons = self._transport()
    x, y, width, height = timeline
    start, end, position = (owner.status.get(key) or 0 for key in ('start', 'end', 'position'))
    displayed = self.seek_preview if self.seek_preview is not None else position
    amount = max(0, min(1, (displayed - start) / (end - start))) if end > start else 0
    ranges = owner.status.get('seekRanges') or ((start, end),)
    rail_y, rail_h = self._timeline_rail(x, y, width, height, start, end, ranges)
    head_x = round(x + width * amount)
    rl.draw_rectangle(head_x - 1, round(rail_y - 1), 3 if large else 2, rail_h + 2, rl.WHITE)
    rl.draw_rectangle(head_x - 4, round(rail_y + rail_h + 3), 9 if large else 8, 3, rl.WHITE)
    timing = f'{duration(displayed)} / {duration(end)}' if end > start else ''
    history = getattr(owner, 'timeline', None)
    entry = history.entry(displayed) if history is not None else None
    label = entry['label'] if entry else ''
    if history is not None and not label:
      label = 'Loading history' if history.rows is None else 'Neutral'
    if not label and owner.status.get('timelineState'):
      label = getattr(owner, 'timeline_error', '') or 'Loading history'
    if end > start and not any(left <= displayed <= right for left, right in ranges):
      label, entry = 'Unavailable gap', None
    if label:
      timing += ' - ' + label
    if not large and not scrubbing and timing:
      timing = elide_text(self.fonts, timing, FontRole.MEDIUM, 18, 504)
      width = self.fonts.measure(timing, FontRole.MEDIUM, 18).width + 16
      draw_rounded_fill(rl.Rectangle(8, 30, width, 30), rl.Color(8, 8, 10, 235))
    if large or not scrubbing:
      self.fonts.draw(timing, FontRole.MEDIUM, 26 if large else 18,
                      x, y - 26 if large else 36, TEXT_PRIMARY)
    if scrubbing:
      self._scrub_preview(head_x, displayed, label or 'History unavailable', entry)
    if large:
      for index, item in enumerate(LEGEND.values()):
        left = 1130 + index * 184
        rl.draw_rectangle(left, 890, 8, 18, rl.Color(*item['rgb'], 255))
        self.fonts.draw(item['label'], FontRole.MEDIUM, 22, left + 14, 888, TEXT_SECONDARY)
    enabled = owner.available and not owner.busy and owner.status.get('state') in ('playing', 'paused', 'buffering', 'ended')
    for label, button in buttons:
      self._button(label, button, not owner.busy if label == 'Stop' else enabled)
    self._render_loading()

  def _render_loading(self):
    owner = self.controller
    if owner.frame is not None and owner.available and not ui_state.started:
      return
    large = self.profile == Profile.LARGE
    size, radius = (32, 40) if large else (18, 14)
    center = rl.Vector2(self.profile.size[0] / 2, self.profile.size[1] / 2)
    loading = not owner.error and owner.available and owner.status.get('state') in ('preparing', 'seeking', 'buffering', 'playing', 'paused')
    message = owner.error or f"Replay {owner.status.get('state', 'unavailable')}"
    if loading:
      angle = time.monotonic() * 240 % 360
      rl.draw_ring(center, radius - (5 if large else 3), radius, angle, angle + 270, 48, TEXT_PRIMARY)
      message = 'Replay preparing' if owner.status.get('state') == 'preparing' else 'Replay loading'
    message = elide_text(self.fonts, message, FontRole.MEDIUM, size, self.profile.size[0] - 40)
    width = self.fonts.measure(message, FontRole.MEDIUM, size).width
    self.fonts.draw(message, FontRole.MEDIUM, size, center.x - width / 2,
                    center.y + radius + (20 if large else 8) if loading else center.y, TEXT_PRIMARY)

  @staticmethod
  def _inside(x, y, bounds):
    left, top, width, height = bounds
    return left <= x <= left + width and top <= y <= top + height

  def _target(self, x, y):
    if self.page == 'detail':
      return next((label for label, bounds, enabled, _ in self._detail_buttons() if enabled and self._inside(x, y, bounds)), None)
    if self.page != 'viewer':
      return None
    if not self.controls:
      return 'Stop' if self._inside(x, y, self._collapsed_stop_bounds()) else 'show'
    timeline, buttons = self._transport()
    if self._inside(x, y, timeline):
      return 'seek'
    return next((label for label, bounds in buttons if self._inside(x, y, bounds)), 'show')

  def _handle_mouse_press(self, pos):
    x, y = pos.x - self.rect.x, pos.y - self.rect.y
    self.last_touch = time.monotonic()
    if self.page in ('viewer', 'detail'):
      self.press = (x, y, self._target(x, y), self.controller.status.get('epoch'), self.controls)
      self._move_seek(x)
    elif self.view:
      state = self._state()
      block_tap = self.view.reset(state) and FeatureInput._in_body(x, y, state)
      self.input.press(x, y, state)
      if block_tap:
        self.input.cancel_tap()

  def _move_seek(self, x):
    if self.press and self.press[2] == 'seek':
      left, _, width, _ = self._transport()[0]
      start, end = self.controller.status.get('start') or 0, self.controller.status.get('end') or 0
      self.seek_preview = start + max(0, min(1, (x - left) / width)) * (end - start)

  def _handle_mouse_event(self, mouse_event):
    if mouse_event.left_released and not rl.check_collision_point_rec(mouse_event.pos, self._hit_rect):
      self.press = self.seek_preview = None
      self.input.cancel()
    if mouse_event.left_down:
      x, y = mouse_event.pos.x - self.rect.x, mouse_event.pos.y - self.rect.y
      if self.press:
        px, py, target, epoch, _ = self.press
        tolerance = 36 if self.profile == Profile.LARGE else MIN_DRAG_PIXELS
        if epoch != self.controller.status.get('epoch') or (target != 'seek' and math.hypot(x - px, y - py) > tolerance):
          self.press = self.seek_preview = None
        else:
          self._move_seek(x)
      elif self.view:
        self.input.move(x, y, self._state(), mouse_event.t)

  def _handle_mouse_release(self, pos):
    x, y = pos.x - self.rect.x, pos.y - self.rect.y
    if self.page == 'detail':
      held, self.press = self.press, None
      if held and held[3] == self.controller.status.get('epoch') and held[2] == self._target(x, y):
        for label, _, enabled, callback in self._detail_buttons():
          if enabled and label == held[2]:
            callback()
            break
      return
    if self.page != 'viewer':
      if self.view:
        self.input.release(x, y, self._state(), time.monotonic())
      return
    held, self.press = self.press, None
    if held and held[3] == self.controller.status.get('epoch'):
      self.last_touch = time.monotonic()
      target = held[2]
      if target == 'seek' and self.seek_preview is not None:
        self.controller.seek(self.seek_preview)
      elif target == 'show':
        self.last_touch = -math.inf if held[4] else time.monotonic()
      elif target == self._target(x, y):
        state = self.controller.status
        if target == 'Stop':
          self._back()
        elif target in ('Play', 'Pause'):
          self.controller.control('pause', paused=not bool(state.get('paused')))
        elif target.startswith(('-10', '+10')):
          self.controller.seek(state.get('position', 0) + (-10 if target.startswith('-') else 10))
        elif target.endswith('x'):
          rates = (0.5, 1, 2)
          self.controller.control('rate', rate=rates[(rates.index(state.get('rate', 1)) + 1) % len(rates)])
    self.seek_preview = None


class ReplayCompactPage(NavWidget):
  def __init__(self, session, controller=None):
    super().__init__()
    self.content = self._child(ReplayPage(session, self.dismiss, controller))
    self.content.set_enabled(lambda: self.enabled and not self.is_dismissing)
    self.content.stack_widget = self

  def _back_enabled(self):
    return self.content.page != 'viewer'

  def _handle_mouse_event(self, mouse_event):
    was_dismissing = self._playing_dismiss_animation
    super()._handle_mouse_event(mouse_event)
    if mouse_event.left_released and not was_dismissing and self._playing_dismiss_animation and self.content.page == 'detail':
      self._playing_dismiss_animation = False
      self.content._back()

  def _render(self, rect):
    self.content.render(rect)


def open_replay(session):
  page = ReplayCompactPage(session) if session.profile == Profile.COMPACT else ReplayPage(session, gui_app.pop_widget)
  page.set_rect(rl.Rectangle(0, 0, *session.profile.size))
  gui_app.push_widget(page)


def replay_entry(session):
  button = BigButton('replay', 'recorded driving routes on this device')
  button.set_click_callback(lambda: open_replay(session))
  return button
