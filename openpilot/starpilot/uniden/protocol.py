"""R-series alert snapshots based on AegisX86/UnidenR8wlink PROTOCOL.md (MIT)."""
import math
import re
from typing import Any, NotRequired, TypedDict

ALERT_UUID = '6eb675ab-8bd1-1b9a-7444-621e52ec6823'
WRITE_UUID = '2c86686a-53dc-25b3-0c4a-f0e10c8dee20'
GATT = 'org.bluez.GattCharacteristic1'
FREQUENCY_BANDS = {'X', 'K', 'KA', 'K POP', 'KA POP'}
BANDS = {'X', 'K', 'KA', 'LASER', 'MRCD', 'MRCT', 'RT3', 'RT4', 'K POP', 'KA POP'}
LOCK_BANDS = ('X', 'K', 'KA', 'LASER')
BORDER_BANDS = ('K', 'KA', 'LASER')
TIMED_BANDS = ('X', 'K', 'KA')
BORDER_SWATCHES = {'amber': '#FFBA4E', 'coral': '#FF5B65', 'magenta': '#FF1FCE', 'ice': '#9FE8FF', 'lime': '#A6FF00',
                   'white': '#FFFFFF', 'ultraviolet': '#7A1FFF'}
LEGACY_COLORS = {'purple': 'ultraviolet', 'violet': 'ultraviolet', 'yellow': 'lime'}
LEGACY_KEYS = ('transparency', 'hide_when_no_alert', 'border_styles', 'unmute_on_breakthrough')
EDGE_PARTS = ('line', 'dashes', 'glow', 'veil', 'chevrons')
RULE_BOUNDS = {'X': (10.0, 11.0), 'K': (23.0, 25.0), 'KA': (33.0, 36.0)}
DEFAULT_CONFIG: dict[str, Any] = {'enabled': False, 'address': '', 'model': 'R8',
                  'auto_mute': False, 'hide_filtered': False, 'hide_below_limit': False, 'hide_q_ride': False, 'hide_mute_memory': False,
                  'frequency_rules': [],
                  'quiet_ride': False, 'quiet_margin': 0, 'quiet_bands': ['X', 'K'],
                  'auto_learn': False, 'learn_bands': ['X', 'K'], 'border_bands': ['KA', 'LASER'],
                  'border_colors': {'KA': 'coral', 'K': 'amber', 'LASER': 'coral'},
                  'border_edge': {'line': True, 'dashes': True, 'glow': False, 'veil': False, 'chevrons': True},
                  'border_muted_percent': 50,
                  'timed_mute': False, 'timed_mute_seconds': 5, 'timed_mute_strength': 6, 'timed_mute_bands': ['X', 'K', 'KA'],
                  'timed_mute_settings': {}}
OPTIONAL_KEYS = set(DEFAULT_CONFIG) - {'enabled', 'address', 'model'}
# Mute mode, field 7 per PROTOCOL.md.
MUTE_MODES = {'1': '', '2': 'manual', '3': 'memory', '4': 'auto_memory', '5': 'blocked', '6': 'quiet_ride'}


class Alert(TypedDict):
  band: str
  strength: int
  frequency: float | None
  direction: str
  muted: bool
  mute_mode: str
  instant_on: NotRequired[bool]
  timed: NotRequired[bool]
  breakthrough: NotRequired[bool]
  ruled: NotRequired[bool]
  locked: NotRequired[bool]
  filtered: NotRequired[bool]
  quiet: NotRequired[bool]


def model_for_name(name):
  match = re.search(r'(?:^|[^A-Z0-9])(R[48])W?(?=$|[^A-Z0-9])', str(name).upper())
  return match[1] if match else None


def migrate_config(value):
  """Strips retired keys and maps retired colors before validation."""
  if type(value) is not dict:
    return value
  config = {key: item for key, item in value.items() if key not in LEGACY_KEYS}
  if type(colors := config.get('border_colors')) is dict:
    config['border_colors'] = {band: LEGACY_COLORS.get(name, name) if type(name) is str else name for band, name in colors.items()}
  return config


def normalize_config(value):
  value = migrate_config(value)
  if type(value) is not dict or set(value) - set(DEFAULT_CONFIG) or not (set(DEFAULT_CONFIG) - OPTIONAL_KEYS) <= set(value):
    raise ValueError('Invalid detector configuration')
  config = {**DEFAULT_CONFIG, **value}
  if type(config['enabled']) is not bool or config['model'] not in ('R4', 'R8'):
    raise ValueError('Choose R4 or R8')
  if type(config['address']) is not str or (config['address'] and not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', config['address'])):
    raise ValueError('Invalid detector address')
  if config['enabled'] and not config['address']:
    raise ValueError('Choose a paired detector')
  for key in ('auto_mute', 'hide_filtered', 'hide_below_limit', 'hide_q_ride', 'hide_mute_memory'):
    if type(config[key]) is not bool:
      raise ValueError('Filter toggles must be boolean')
  config['frequency_rules'] = normalize_rules(config['frequency_rules'])
  for key in ('quiet_ride', 'auto_learn'):
    if type(config[key]) is not bool:
      raise ValueError('Quiet Ride and learning toggles must be boolean')
  if type(config['quiet_margin']) is not int or not 0 <= config['quiet_margin'] <= 15:
    raise ValueError('Quiet Ride margin must be 0–15')
  if type(config['timed_mute']) is not bool:
    raise ValueError('Timed mute toggles must be boolean')
  for key, low, high, message in (('timed_mute_seconds', 1, 60, 'Mute delay must be 1–60 seconds'),
                                  ('timed_mute_strength', 2, 8, 'Warn again at strength 2–8, or never')):
    # 0 for the strength means never warn again.
    if type(config[key]) is not int or not (low <= config[key] <= high or (key == 'timed_mute_strength' and config[key] == 0)):
      raise ValueError(message)
  settings = config['timed_mute_settings']
  if type(settings) is not dict or set(settings) - set(TIMED_BANDS):
    raise ValueError('Choose timed mute settings for X, K or Ka')
  normalized = {}
  for band in TIMED_BANDS:
    item = settings.get(band, {'seconds': config['timed_mute_seconds'], 'strength': config['timed_mute_strength']})
    if type(item) is not dict or set(item) != {'seconds', 'strength'}:
      raise ValueError('Each band needs a mute delay and warning strength')
    if type(item['seconds']) is not int or not 1 <= item['seconds'] <= 60:
      raise ValueError('Mute delay must be 1–60 seconds')
    if type(item['strength']) is not int or item['strength'] not in (0, 2, 3, 4, 5, 6, 7, 8):
      raise ValueError('Warn again at strength 2–8, or never')
    normalized[band] = dict(item)
  config['timed_mute_settings'] = normalized
  for key, allowed in (('quiet_bands', LOCK_BANDS), ('learn_bands', LOCK_BANDS), ('border_bands', BORDER_BANDS),
                       ('timed_mute_bands', TIMED_BANDS)):
    config[key] = normalize_bands(config[key], allowed)
  colors = config['border_colors']
  if type(colors) is not dict or set(colors) != set(BORDER_BANDS) or any(type(v) is not str or v not in BORDER_SWATCHES for v in colors.values()):
    raise ValueError('Choose a border color from the list')
  edge = config['border_edge']
  if type(edge) is not dict or set(edge) != set(EDGE_PARTS) or any(type(v) is not bool for v in edge.values()):
    raise ValueError('Edge parts must be on or off')
  if type(config['border_muted_percent']) is not int or not 0 <= config['border_muted_percent'] <= 100:
    raise ValueError('Muted brightness must be 0–100%')
  config['border_edge'] = dict(edge)
  config['address'] = config['address'].upper()
  return config


def normalize_bands(bands, allowed):
  if type(bands) is not list or any(type(band) is not str or band not in allowed for band in bands):
    raise ValueError('Choose from ' + ', '.join(allowed))
  return [band for band in allowed if band in bands]


def parse_alerts(payload, model='R8'):
  text = bytes(payload).decode('ascii').strip('\x00\r\n ')
  if not text or len(text) > 4096:
    raise ValueError('Empty or oversized alert snapshot')
  alerts: list[Alert] = []
  for segment in text.split('&'):
    if segment == '0':
      continue
    f = segment.split(',')
    if len(f) not in (8, 9) or f[0] != '1' or f[2].upper() not in BANDS:
      raise ValueError('Unsupported alert packet')
    strength = int(f[3])
    if not 1 <= strength <= 8:
      raise ValueError('Invalid signal strength')
    band = f[2].upper()
    frequency = None
    if band in FREQUENCY_BANDS:
      frequency = float(f[5])
      if not math.isfinite(frequency) or not 0 < frequency < 100:
        raise ValueError('Invalid frequency')
    alerts.append({'band': band, 'strength': strength, 'frequency': frequency,
                   'direction': {'F': 'front', 'S': 'side', 'R': 'rear'}.get(f[6], '') if model == 'R8' else '',
                   'muted': f[7] in ('2', '3', '4', '5', '6'), 'mute_mode': MUTE_MODES.get(f[7], '')})
  return sorted(alerts, key=lambda a: (a['band'] != 'LASER', -a['strength']))


def alert_key(alerts):
  """Stable across strength changes; never use the detector's usually-zero alert ID."""
  return sorted([[a['band'], a['frequency'], a['direction']] for a in alerts], key=str)


def normalize_rules(rules):
  if type(rules) is not list or len(rules) > 32:
    raise ValueError('Use at most 32 frequency rules')
  result = []
  for rule in rules:
    if type(rule) is not dict or set(rule) != {'enabled', 'label', 'band', 'frequency', 'tolerance_mhz'}:
      raise ValueError('Invalid frequency rule')
    if type(rule['enabled']) is not bool or type(rule['label']) is not str or len(rule['label']) > 60:
      raise ValueError('Use a label of at most 60 characters')
    bounds = RULE_BOUNDS
    band = rule['band']
    if type(band) is not str or band not in bounds:
      raise ValueError('Choose X, K or KA band')
    frequency, tolerance = rule['frequency'], rule['tolerance_mhz']
    if type(frequency) not in (int, float) or not math.isfinite(frequency) or not bounds[band][0] <= frequency <= bounds[band][1]:
      raise ValueError('Frequency must be in the selected band, in GHz')
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 <= tolerance <= 10:
      raise ValueError('Tolerance must be 0–10 MHz')
    result.append(dict(rule))
  return result


def matches_rule(alert, rules):
  frequency = alert.get('frequency')
  return frequency is not None and any(rule['enabled'] and alert['band'] == rule['band'] and
    abs(frequency - rule['frequency']) <= rule['tolerance_mhz'] / 1000 + 1e-9 for rule in rules)
