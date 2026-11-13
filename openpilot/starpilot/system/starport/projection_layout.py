"""Separate AA placement document, with screen-specific geometry and actions."""
import copy
import json
import math
from pathlib import Path

from openpilot.starpilot.system.starport.identity import DATA_DIR
from openpilot.starpilot.system.starport.display_profile import screen_geometry
from openpilot.starpilot.ui.onroad_customization import (CEM_GAUGE_WIDGET, CLOCK_WIDGET, JETLINK_WIDGET, MODE_WIDGET,
                                                         STOPPED_TIMER_WIDGET, WEATHER_WIDGET, CEM_GAUGE_FRAME, CEM_GAUGE_INSET, MAP_MIN_SIZE, NAV_CARD_SIZE,
                                                         SPEED_ALIGNMENTS, customization_metadata, validate_map_box, validate_widget_order)
from openpilot.starpilot.ui.onroad_stopped_timer import timer_size
from openpilot.starpilot.ui.onroad_torque_geometry import maximum_footprint
from openpilot.starpilot.ui.onroad_customization import (SPEED_LAYOUT_V7, migrate_speed_layout, migrate_large_sign_layout,
                                                        place_attached_slc_actions, migrate_confirmation_layout)
from openpilot.starpilot.ui.large_speed_geometry import sources_extent

MAX_BYTES = 16384
DOCUMENT_KEY = 'projection'
DOCUMENT_PATH = DATA_DIR / 'layouts/document.json'

# Layouts saved before these widgets existed gain them with their defaults.
NAV_CARD, NAV_MAP = 'nav_card', 'nav_map'
NAV_HOME, NAV_WORK = 'nav_home', 'nav_work'
FAVORITE_WIDGETS = (NAV_HOME, NAV_WORK)
BOOKMARK_WIDGET = 'bookmark'
DISPLAY_WIDGETS = (*FAVORITE_WIDGETS, BOOKMARK_WIDGET)
CAR_EXIT = 'car_exit'
ACTION_WIDGETS = (*DISPLAY_WIDGETS, CAR_EXIT)
FAVORITE_SIZE = (320, 110)
FAVORITE_ICON_SIZE = 110
CAR_EXIT_SIZE = (96, 96)
CAR_EXIT_MIN_OPACITY = 25
NOW_PLAYING_WIDGET = 'now_playing'
NOW_PLAYING_SIZE = (520, 96)
NOW_PLAYING_OPACITY = (0, 100, 85)
JETLINK_SIZE = (420, 84)
JETLINK_OPACITY = (0, 100, 85)
CARD_WIDGETS = {NOW_PLAYING_WIDGET: NOW_PLAYING_OPACITY, JETLINK_WIDGET: JETLINK_OPACITY}
# StarPort widgets the comma also draws as real editor sprites; the map stays a sketch.
SPRITE_WIDGETS = (NAV_CARD, *DISPLAY_WIDGETS, STOPPED_TIMER_WIDGET, NOW_PLAYING_WIDGET, JETLINK_WIDGET, CAR_EXIT)
PROJECTION_WIDGETS = (NAV_CARD, NAV_MAP, *DISPLAY_WIDGETS, STOPPED_TIMER_WIDGET, NOW_PLAYING_WIDGET, JETLINK_WIDGET, CAR_EXIT, 'following_distance')


class ProjectionLayoutSource:
  """Path adapter for existing read_saved/commit_exact file ownership semantics."""
  def __init__(self, path=None):
    self.path = Path(path or DOCUMENT_PATH)

  def get_param_path(self, key):
    if key != DOCUMENT_KEY:
      raise ValueError('Unknown projection document')
    return str(self.path)

  def prepare(self):
    self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)


def layout_metadata(screen):
  geometry = screen_geometry(screen)
  return layout_metadata_for_viewport((geometry.logical_width, geometry.logical_height))


def layout_metadata_for_viewport(viewport):
  width, height = viewport
  if any(type(n) is not int for n in viewport) or not 1860 <= width <= 4096 or not 1080 <= height <= 2160:
    raise ValueError('Invalid projection canvas')
  profile = copy.deepcopy(customization_metadata()['profiles']['large'])
  profile.update(label='Android Auto', width=width, height=height,
                 bounds={'x': 30, 'y': 30, 'width': width - 60, 'height': height - 60},
                 reservedZones=[], inputZones=[])
  profile.pop('inputZonePriority', None)
  widgets = profile['widgets']
  widgets.pop(JETLINK_WIDGET)  # Big-screen only for now
  profile['widgetOrder'] = [key for key in profile['widgetOrder'] if key != JETLINK_WIDGET]
  for widget in widgets.values():
    for field in ('bounds', 'anchorShift', 'expandable'):
      widget.pop(field, None)
  widgets['current_speed']['default']['x'] += (width - 1860) / 2
  widgets[MODE_WIDGET]['default']['x'] += (width - 1860) / 2
  widgets[CLOCK_WIDGET]['default']['x'] += (width - 1860) / 2
  widgets['steering_wheel']['default']['x'] += width - 1860
  widgets['driver_monitor']['default']['y'] += height - 1080
  widgets['following_distance']['default']['y'] += height - 1080
  x, y, w, h = maximum_footprint(30, 30, width - 60, height - 60, width)
  widgets['torque_bar'].update(width=w, height=h)
  widgets['torque_bar'].pop('confirmationObstacle', None)
  widgets['torque_bar']['default'].update(x=x, y=y)
  timer_w, _ = timer_size('large')
  widgets[STOPPED_TIMER_WIDGET]['default'].update(x=(width - timer_w) / 2, y=520)
  card_w, card_h = NAV_CARD_SIZE
  card_x, card_bottom = 1230 + width - 1860, 355 + card_h + 15
  widgets[NAV_CARD]['default'].update(x=card_x)
  # The map scales with the screen: about 30% of its width in a 4:3 frame,
  # right-aligned with the turn card and sitting just below it.
  map_w = max(MAP_MIN_SIZE[0], round(width * 0.3 / 10) * 10)
  map_h = max(MAP_MIN_SIZE[1], min(round(map_w * 0.75 / 10) * 10, (height - 30 - card_bottom) // 10 * 10))
  widgets[NAV_MAP].update(width=map_w, height=map_h,
                          box={'minWidth': MAP_MIN_SIZE[0], 'maxWidth': width - 60, 'minHeight': MAP_MIN_SIZE[1], 'maxHeight': height - 60})
  widgets[NAV_MAP]['default'].update(x=max(30, card_x + card_w - map_w), y=min(card_bottom, height - 30 - map_h), width=map_w, height=map_h)
  for index, key in enumerate(FAVORITE_WIDGETS):
    label = ('Home', 'Work')[index]
    widgets[key] = {'label': label, 'kind': key, 'width': FAVORITE_SIZE[0], 'height': FAVORITE_SIZE[1], 'colors': {},
                    'iconSize': FAVORITE_ICON_SIZE,
                    'note': f'Navigate to your saved {label} favorite. Tap again to end navigation. Set the address in The Galaxy.',
                    'default': {'x': width - 30 - 2 * FAVORITE_SIZE[0] - 15 + index * (FAVORITE_SIZE[0] + 15),
                                'y': 280, 'enabled': False, 'display': 'words'}}
  widgets[BOOKMARK_WIDGET] = {'label': 'Bookmark', 'kind': BOOKMARK_WIDGET,
                              'width': FAVORITE_SIZE[0], 'height': FAVORITE_SIZE[1], 'colors': {},
                              'iconSize': FAVORITE_ICON_SIZE,
                              'note': 'Save a bookmark in the current drive recording.',
                              'default': {'x': width - 30 - FAVORITE_SIZE[0], 'y': 155, 'enabled': False, 'display': 'words'}}
  widgets[NOW_PLAYING_WIDGET] = {'label': 'Now playing', 'kind': NOW_PLAYING_WIDGET,
                                 'width': NOW_PLAYING_SIZE[0], 'height': NOW_PLAYING_SIZE[1], 'colors': {},
                                 'note': 'The song playing from Spotify or your phone over Bluetooth. Opacity sets the card background.',
                                 'opacity': {'min': NOW_PLAYING_OPACITY[0], 'max': NOW_PLAYING_OPACITY[1], 'default': NOW_PLAYING_OPACITY[2]},
                                 'collapseToggle': True,
                                 'default': {'x': width - 30 - NOW_PLAYING_SIZE[0], 'y': height - 30 - NOW_PLAYING_SIZE[1],
                                             'enabled': True, 'opacity': NOW_PLAYING_OPACITY[2], 'collapse': False}}
  widgets[JETLINK_WIDGET] = {'label': 'Jetlink status', 'kind': JETLINK_WIDGET,
                             'width': JETLINK_SIZE[0], 'height': JETLINK_SIZE[1], 'colors': {},
                             'note': 'Shown while Jetlink is on: connection, device and host processing time. Opacity sets the card background.',
                             'opacity': {'min': JETLINK_OPACITY[0], 'max': JETLINK_OPACITY[1], 'default': JETLINK_OPACITY[2]},
                             'default': {'x': 30, 'y': 650, 'enabled': False, 'opacity': JETLINK_OPACITY[2]}}
  exit_w, exit_h = CAR_EXIT_SIZE
  widgets[CAR_EXIT] = {'label': 'Exit to car', 'kind': CAR_EXIT, 'width': exit_w, 'height': exit_h, 'colors': {},
                       'required': True, 'frontmost': True,
                       'note': "Returns to the car's own screen without disconnecting Android Auto. Always enabled and above other widgets.",
                       'default': {'x': 30, 'y': height - 30 - exit_h, 'enabled': True}}
  for key in ACTION_WIDGETS:
    opacity = 75 if key == CAR_EXIT else 100
    widgets[key].update(opacity={'min': CAR_EXIT_MIN_OPACITY if key == CAR_EXIT else 0, 'max': 100, 'default': opacity}, backgroundToggle=True)
    widgets[key]['default'].update(opacity=opacity, background=key != CAR_EXIT)
  # Background opacity for the cruise and speed-limit card; its text stays solid.
  widgets['cruise_limits'].update(opacity={'min': 0, 'max': 100, 'default': 100})
  widgets['cruise_limits']['default'].update(opacity=100)
  profile["widgetOrder"] = [NAV_MAP, *[key for key in profile["widgetOrder"] if key not in (NAV_MAP, NAV_CARD, STOPPED_TIMER_WIDGET)],
                            STOPPED_TIMER_WIDGET, NAV_CARD, *DISPLAY_WIDGETS, NOW_PLAYING_WIDGET, JETLINK_WIDGET, CAR_EXIT]
  return profile


# Android Auto display switches. The legacy personality field remains in saved layouts for compatibility;
# the shared comma preference controls that notice on every screen.
DISPLAY_DEFAULTS = {'blindSpotBorder': False, 'signalBorder': False, 'blindSpotCamera': False,
                    'hidePersonalityAlerts': False, 'wheelPedalFeedback': False,
                    'leadIndicator': False, 'leadInfo': 'Off', 'speedSources': False,
                    'speedSourcesCard': False, 'speedAlign': 'left'}
# Lead info shown above the lead marker, in LeadInfoMode order.
LEAD_INFO_CHOICES = ('Off', 'Distance', 'Speed')


def projection_appearance(appearance, document):
  """Apply screen-specific choices while retaining the shared personality-alert preference."""
  from dataclasses import replace
  from openpilot.starpilot.ui.appearance_preferences import LeadInfoMode
  lead_indicator = document.get('leadIndicator', False) is True
  lead_info = document.get('leadInfo', 'Off')
  # Like the comma, lead info only shows with the lead marker on.
  lead_info_mode = LeadInfoMode(LEAD_INFO_CHOICES.index(lead_info)) if lead_indicator and lead_info in LEAD_INFO_CHOICES else LeadInfoMode.OFF
  # Camera View remains the shared saved choice; this layout has no camera override.
  return replace(appearance,
                 show_lead_indicator=lead_indicator, lead_info_mode=lead_info_mode,
                 show_blindspot_border=document.get('blindSpotBorder', False) is True,
                 show_signal_border=document.get('signalBorder', False) is True,
                 wheel_pedal_feedback=document.get('wheelPedalFeedback', False) is True)


def comma_display(appearance):
  """The comma screen's display choices as AA switches, to start a car screen that has no saved layout."""
  lead_indicator = appearance.show_lead_indicator is True
  return {'blindSpotBorder': appearance.show_blindspot_border is True,
          'signalBorder': appearance.show_signal_border is True,
          'hidePersonalityAlerts': appearance.hide_personality_alerts is True,
          'wheelPedalFeedback': appearance.wheel_pedal_feedback is True,
          'leadIndicator': lead_indicator,
          'leadInfo': LEAD_INFO_CHOICES[appearance.lead_info_mode] if lead_indicator else 'Off'}


def default_layout(screen, display=None):
  geometry = screen_geometry(screen)
  return default_layout_for_viewport((geometry.logical_width, geometry.logical_height), display)


def default_layout_for_viewport(viewport, display=None):
  metadata = layout_metadata_for_viewport(viewport)
  display = {**DISPLAY_DEFAULTS, **{key: value for key, value in (display or {}).items() if key in DISPLAY_DEFAULTS}}
  result = {'version': 6, 'clock24Hour': False, 'largeUiGammaTrial': False, **display, 'canvas': {key: metadata[key] for key in ('width', 'height')},
          'widgets': {key: {**widget['default'], **({'size': 192} if key == 'steering_wheel' else {})}
                      for key, widget in metadata['widgets'].items()}}
  place_attached_slc_actions(result['widgets'], metadata['bounds'], sources_extent(result))
  return result


def placement_size(key, widget, placement):
  if key in DISPLAY_WIDGETS and placement.get('display') == 'icons':
    return FAVORITE_ICON_SIZE, FAVORITE_ICON_SIZE
  if widget.get('resizable'):
    return (placement.get('size', widget['resizable']['default']),) * 2
  if key == NAV_MAP:
    return placement['width'], placement['height']
  return widget['width'], widget['height']


def validate_layout(value, screen):
  geometry = screen_geometry(screen)
  return validate_layout_for_viewport(value, (geometry.logical_width, geometry.logical_height))


def validate_layout_for_viewport(value, viewport, *, _legacy_geometry=False):
  metadata = layout_metadata_for_viewport(viewport)
  if type(value) is dict and type(value.get('version')) is int and value['version'] == 1 and not _legacy_geometry:
    result = validate_layout_for_viewport(value, viewport, _legacy_geometry=True)
    card, actions = result['widgets']['cruise_limits'], result['widgets']['speed_limit_actions']
    gap = actions['y'] - card['y']
    migrate_speed_layout(result['widgets'], 'large', metadata['bounds'], gap if gap in (372, 425) else 232, widgets=metadata['widgets'])
    # The intermediate v2 validator still uses its historical wider action box.
    actions['x'] = min(actions['x'], metadata['bounds']['x'] + metadata['bounds']['width'] -
                       max(metadata['widgets']['speed_limit_actions']['width'], SPEED_LAYOUT_V7['large']['speed_limit_actions'][0]))
    result['version'] = 2
    return validate_layout_for_viewport(result, viewport)
  fields = {'version', 'canvas', 'widgets'}
  if type(value) is dict:
    fields |= {field for field in ('widgetOrder', 'clock24Hour', 'largeUiGammaTrial', *DISPLAY_DEFAULTS) if field in value}
  if (type(value) is not dict or set(value) != fields or
      type(value['version']) is not int or value['version'] not in (1, 2, 3, 4, 5, 6) or
      value['canvas'] != {key: metadata[key] for key in ('width', 'height')} or
      type(value['widgets']) is not dict or not set(value['widgets']) <= set(metadata['widgets']) or
      not set(metadata['widgets']) - set(value['widgets']) <= {CEM_GAUGE_WIDGET, MODE_WIDGET, CLOCK_WIDGET, WEATHER_WIDGET, *PROJECTION_WIDGETS}):
    raise ValueError('Projection layout does not match saved screen')
  result = copy.deepcopy(value)
  if 'clock24Hour' in value and type(value['clock24Hour']) is not bool:
    raise ValueError('Invalid projection clock format')
  result.setdefault('clock24Hour', False)
  if 'largeUiGammaTrial' in value and type(value['largeUiGammaTrial']) is not bool:
    raise ValueError('Invalid large UI gamma trial')
  result.setdefault('largeUiGammaTrial', False)
  for field, default in DISPLAY_DEFAULTS.items():
    if field in value and (type(value[field]) is not type(default) or
                           (field == 'leadInfo' and value[field] not in LEAD_INFO_CHOICES) or
                           (field == 'speedAlign' and value[field] not in SPEED_ALIGNMENTS)):
      raise ValueError('Invalid projection display setting')
    result.setdefault(field, default)
  for key in set(metadata['widgets']) - set(value['widgets']):
    result['widgets'][key] = dict(metadata['widgets'][key]['default'])
  if 'widgetOrder' in value:
    order = value['widgetOrder']
    if type(order) is list:
      order = [*order, *(key for key in (CEM_GAUGE_WIDGET, MODE_WIDGET, CLOCK_WIDGET, WEATHER_WIDGET, *PROJECTION_WIDGETS) if key not in order)]
    order = validate_widget_order(order, metadata['widgets'])
    # Older or hand-edited documents may put the escape control underneath
    # another widget. Preserve their order while restoring this safety layer.
    result['widgetOrder'] = [key for key in order if key != CAR_EXIT] + [CAR_EXIT]
  bounds = metadata['bounds']
  for key, widget in metadata['widgets'].items():
    placement = result['widgets'][key]
    if key in DISPLAY_WIDGETS and type(placement) is dict:
      placement.setdefault('display', 'words')
    if key in ACTION_WIDGETS and type(placement) is dict:
      placement.setdefault('opacity', 100)
      placement.setdefault('background', True)
    if key == 'cruise_limits' and type(placement) is dict:
      placement.setdefault('opacity', 100)
    if key in CARD_WIDGETS and type(placement) is dict:
      placement.setdefault('opacity', CARD_WIDGETS[key][2])
    if key == NOW_PLAYING_WIDGET and type(placement) is dict:
      placement.setdefault('collapse', False)
    if key == 'driver_monitor' and type(placement) is dict and 'size' not in placement:
      for axis in ('x', 'y'):
        if type(placement.get(axis)) in (int, float):
          placement[axis] += 32
      placement['size'] = widget['resizable']['default']
    if (key == 'driver_monitor' and 'following_distance' not in value['widgets'] and type(placement) is dict and
        (placement.get('x'), placement.get('y'), placement.get('size')) == (120, 840 + viewport[1] - 1080, 128)):
      placement['x'] = widget['default']['x']
    fields = ({'x', 'y', 'enabled'} | ({'size'} if widget.get('resizable') else set()) |
              ({'display'} if key in DISPLAY_WIDGETS else set()) |
              ({'opacity', 'background'} if key in ACTION_WIDGETS else set()) |
              ({'opacity'} if key == 'cruise_limits' or key in CARD_WIDGETS else set()) |
              ({'collapse'} if key == NOW_PLAYING_WIDGET else set()) |
              ({'width', 'height', 'opacity'} if key == NAV_MAP else set()))
    if type(placement) is not dict or set(placement) != fields or type(placement['enabled']) is not bool:
      raise ValueError('Invalid projection widget')
    if widget.get('required') is True and placement['enabled'] is not True:
      raise ValueError('Required projection widget is disabled')
    if key in DISPLAY_WIDGETS and placement['display'] not in ('words', 'icons'):
      raise ValueError('Invalid action widget display')
    if key in ACTION_WIDGETS and (type(placement['opacity']) is not int or not 0 <= placement['opacity'] <= 100 or
                                  type(placement['background']) is not bool):
      raise ValueError('Invalid action widget appearance')
    if key == 'cruise_limits' and (type(placement['opacity']) is not int or not 0 <= placement['opacity'] <= 100):
      raise ValueError('Invalid cruise card opacity')
    if key in CARD_WIDGETS and (type(placement['opacity']) is not int or
                                not CARD_WIDGETS[key][0] <= placement['opacity'] <= CARD_WIDGETS[key][1]):
      raise ValueError('Invalid card opacity')
    if key == NOW_PLAYING_WIDGET and type(placement['collapse']) is not bool:
      raise ValueError('Invalid now playing collapse setting')
    if key == CAR_EXIT and placement['opacity'] < CAR_EXIT_MIN_OPACITY:
      raise ValueError('Exit to car opacity must be at least 25%')
    if widget.get('resizable'):
      size, limits = placement['size'], widget['resizable']
      if type(size) is not int or not limits['min'] <= size <= limits['max']:
        raise ValueError('Invalid widget size')
    if key == NAV_MAP:
      validate_map_box(placement, widget)
    dimensions = placement_size(key, widget, placement)
    if value['version'] < 4 and key in SPEED_LAYOUT_V7['large']:
      dimensions = SPEED_LAYOUT_V7['large'][key][:2]
    elif value['version'] < 5 and key == 'cruise_limits':
      dimensions = (widget['width'], 132)
    elif value['version'] < 6 and key == 'cruise_limits':
      dimensions = (widget['width'], 168)
    # Version 2 and older saved the CEM gauge's full drawing frame; check that, then move to the inset box.
    old_gauge = key == CEM_GAUGE_WIDGET and value['version'] < 3 and key in value['widgets']
    if old_gauge:
      dimensions = CEM_GAUGE_FRAME
    if _legacy_geometry and key in ('cruise_limits', 'speed_limit_actions'):
      historic = {'cruise_limits': ((176, 483), (280, 412)), 'speed_limit_actions': ((176, 58), (280, 96))}
      # Both shapes were published as v1; validate the whole footprint before migrating.
      dimensions = next((pair for pair in historic[key] if all(
        type(placement[axis]) in (int, float) and math.isfinite(placement[axis]) and
        bounds[axis] <= placement[axis] <= bounds[axis] + bounds[extent] - dimension
        for (axis, extent), dimension in zip((('x', 'width'), ('y', 'height')), pair, strict=True))), dimensions)
    for (axis, extent), dimension in zip((('x', 'width'), ('y', 'height')), dimensions, strict=True):
      number = placement[axis]
      if (type(number) not in (int, float) or not math.isfinite(number) or
          not bounds[axis] <= number <= bounds[axis] + bounds[extent] - dimension):
        raise ValueError('Projection widget outside screen')
    if old_gauge and not _legacy_geometry:
      placement.update(x=placement['x'] + CEM_GAUGE_INSET[0], y=placement['y'] + CEM_GAUGE_INSET[1])
  if not _legacy_geometry:
    if value['version'] < 4:
      card, actions = result['widgets']['cruise_limits'], result['widgets']['speed_limit_actions']
      gap = actions['y'] - card['y']
      migrate_speed_layout(result['widgets'], 'large', bounds, gap if gap in (232, 492) else 232, widgets=metadata['widgets'])
    if value['version'] < 5:
      migrate_large_sign_layout(result['widgets'], bounds, widgets=metadata['widgets'])
    actions = result['widgets']['speed_limit_actions']
    if (actions['x'], actions['y']) == (100, 296):
      actions.update(x=metadata['widgets']['speed_limit_actions']['default']['x'], y=metadata['widgets']['speed_limit_actions']['default']['y'])
    result['version'] = 6
  if value['version'] == 5 and result.get('speedSources') is True:
    card, actions = result['widgets']['cruise_limits'], result['widgets']['speed_limit_actions']
    old_gap = 284 + (8 if result.get('speedSourcesCard') is True else 0)
    if actions['x'] == card['x'] + 24 and actions['y'] == card['y'] + old_gap:
      actions['y'] = card['y'] + 192
  migrate_confirmation_layout(result['widgets'], 'large', metadata['widgets'], bounds, extra_height=sources_extent(result))
  place_attached_slc_actions(result['widgets'], bounds, sources_extent(result))
  if len(json.dumps(result, separators=(',', ':'), allow_nan=False).encode()) > MAX_BYTES:
    raise ValueError('Projection layout too large')
  return result


def decode_layout(raw, screen):
  geometry = screen_geometry(screen)
  return decode_layout_for_viewport(raw, (geometry.logical_width, geometry.logical_height))


def decode_layout_for_viewport(raw, viewport):
  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError('Duplicate projection field')
      result[key] = value
    return result
  if len(raw) > MAX_BYTES:
    raise ValueError('Projection layout too large')
  value = json.loads(raw, object_pairs_hook=unique)
  # Lift older saved escape controls to the new minimum without losing the layout.
  widgets = value.get('widgets') if type(value) is dict else None
  exit_placement = widgets.get(CAR_EXIT) if type(widgets) is dict else None
  if type(exit_placement) is dict and type(exit_placement.get('opacity')) is int and 0 <= exit_placement['opacity'] < CAR_EXIT_MIN_OPACITY:
    exit_placement['opacity'] = CAR_EXIT_MIN_OPACITY
  try:
    return validate_layout_for_viewport(value, viewport)
  except ValueError:
    return _salvage_layout(value, viewport)


def _salvage_layout(value, viewport):
  """Keep every saved placement an update still accepts, so one stale widget does not reset the whole layout."""
  metadata = layout_metadata_for_viewport(viewport)
  if type(value) is not dict or value.get('canvas') != {key: metadata[key] for key in ('width', 'height')} or type(value.get('widgets')) is not dict:
    raise ValueError('Projection layout does not match saved screen')
  defaults = {key: dict(widget['default']) for key, widget in metadata['widgets'].items()}
  saved = {key: placement for key, placement in value['widgets'].items() if key in defaults}
  result: dict = {'version': value.get('version'), 'canvas': value['canvas'], 'widgets': dict(defaults)}

  def accepts(candidate):
    try:
      validate_layout_for_viewport(candidate, viewport)
      return True
    except (ValueError, TypeError, KeyError):
      return False

  if not accepts(result):
    result['version'] = 5
  for field in ('clock24Hour', 'largeUiGammaTrial', *DISPLAY_DEFAULTS):
    if field in value and accepts({**result, field: value[field]}):
      result[field] = value[field]
  for key, placement in saved.items():
    if type(placement) is not dict:
      continue
    merged = {**defaults[key], **{field: placement[field] for field in placement if field in defaults[key]}}
    clamped = dict(merged)
    try:
      bounds, size = metadata['bounds'], placement_size(key, metadata['widgets'][key], merged)
      for (axis, extent), dimension in zip((('x', 'width'), ('y', 'height')), size, strict=True):
        clamped[axis] = max(bounds[axis], min(clamped[axis], bounds[axis] + bounds[extent] - dimension))
    except (KeyError, TypeError, ValueError):
      pass
    for candidate in (placement, merged, clamped):
      trial = {**result, 'widgets': {**result['widgets'], key: candidate}}
      if accepts(trial):
        result = trial
        break
  if 'widgetOrder' in value and accepts({**result, 'widgetOrder': value['widgetOrder']}):
    result['widgetOrder'] = value['widgetOrder']
  return validate_layout_for_viewport(result, viewport)


def projection_customization(document, base_customization):
  """Convert validated AA placements to offsets consumed by the projected view.

  The view already applies its center/right/bottom anchors. Cancel those anchor
  shifts here so the independent saved coordinates are applied exactly once.
  Colors and native compact placement are copied from the caller's document.
  """
  from openpilot.starpilot.ui.onroad_customization import PROFILES
  width, height = document['canvas']['width'], document['canvas']['height']
  shifts = {'current_speed': ((width - 1860) / 2, 0),
            MODE_WIDGET: ((width - 1860) / 2, 0),
            CLOCK_WIDGET: ((width - 1860) / 2, 0),
            'steering_wheel': (width - 1860, 0), 'driver_monitor': (0, height - 1080),
            NAV_CARD: (width - 1860, 0), NAV_MAP: (width - 1860, 0)}
  native = PROFILES['large']['widgets']
  assert isinstance(native, dict)
  tx, ty, _, _ = maximum_footprint(30, 30, width - 60, height - 60, width)
  shifts['torque_bar'] = (tx - native['torque_bar']['default']['x'], ty - native['torque_bar']['default']['y'])
  result = copy.deepcopy(base_customization)
  result['clock24Hour'] = document.get('clock24Hour', False)
  # Match the editor's default layers, keeping PiP above the map.
  result.setdefault('widgetOrder', {})['large'] = list(document['widgetOrder'] if 'widgetOrder' in document else
                                                       layout_metadata_for_viewport((width, height))['widgetOrder'])
  result['layouts']['large'] = {}
  for key, placement in document['widgets'].items():
    dx, dy = shifts.get(key, (0, 0))
    result['layouts']['large'][key] = {**placement, 'x': placement['x'] - dx, 'y': placement['y'] - dy}
  from openpilot.starpilot.ui.onroad_customization import rgba
  # Card opacity is a color, not a placement: fold it into the fill and keep the native placement shape.
  opacity = result['layouts']['large'].get('cruise_limits', {}).pop('opacity', 100)
  if opacity != 100:
    red, green, blue, alpha = rgba(result, 'cardFill', 'large', 'cruise_limits')
    fill = f'#{red:02X}{green:02X}{blue:02X}{round(alpha * opacity / 100):02X}'
    result['widgetColors']['large'].setdefault('cruise_limits', {})['cardFill'] = fill
  for key in ('speedSources', 'speedSourcesCard', 'speedAlign'):
    if key in document:
      result[key] = document[key]
  return result
