"""Short-lived notification guidance. Only navigationd publishes the selected source."""
import json
import math
import re
from pathlib import Path
import tempfile
import threading
import time

OBSERVATION_NS = 90_000_000_000
LEASE_NS = 15_000_000_000
PATH = '/api/companion/navigation'
FIELDS = {'session', 'sequence', 'active', 'ageMs', 'instruction', 'maneuverType', 'maneuverModifier', 'distanceMeters',
          'roadName', 'exitNumber', 'destination', 'source'}
TYPES = {'', 'turn', 'continue', 'on ramp', 'off ramp', 'fork', 'merge', 'roundabout', 'arrive'}
MODIFIERS = {'', 'left', 'right', 'straight', 'slightLeft', 'slightRight', 'sharpLeft', 'sharpRight', 'uturn'}


def boot_offset_ns():
  # monotonic excludes suspend, while the phone's elapsedRealtime includes it.
  # Invalidate a pre-suspend lease instead of replaying it on comma's wakeup.
  return time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC)) - time.monotonic_ns()


def handoff_path():
  shm = Path('/dev/shm')
  return (shm if shm.is_dir() else Path(tempfile.gettempdir())) / 'starpilot_phone_navigation.json'


def _read(path):
  try:
    value = json.loads(path.read_text())
    if (isinstance(value, dict) and isinstance(value.get('session'), str) and type(value.get('sequence')) is int and
        type(value.get('active')) is bool and all(type(value.get(key)) is int for key in ('observed', 'received', 'bootOffset'))):
      retired = value.get('retiredSessions', [])
      if not isinstance(retired, list) or len(retired) > 16 or any(not isinstance(item, str) for item in retired):
        return None
      validate_payload({key: value[key] for key in FIELDS if key in value})
      return value
    return None
  except (OSError, ValueError):
    return None


def validate_payload(payload):
  if not isinstance(payload, dict) or set(payload) - FIELDS:
    raise ValueError('Unexpected navigation fields')
  source = payload.get('source', 'googleMaps')
  if not isinstance(source, str) or re.fullmatch(r'[a-z][a-zA-Z0-9]{0,31}', source) is None:
    raise ValueError('Invalid navigation source')
  session, sequence, active = (payload.get(key) for key in ('session', 'sequence', 'active'))
  if (not isinstance(session, str) or not 1 <= len(session) <= 64 or
      type(sequence) is not int or not 0 <= sequence < 2 ** 53 or type(active) is not bool):
    raise ValueError('Invalid navigation session')
  if any(key in payload and (not isinstance(payload[key], str) or len(payload[key]) > 500) for key in ('roadName', 'exitNumber', 'destination')):
    raise ValueError('Invalid road or exit')
  age = payload.get('ageMs')
  if type(age) not in (int, float) or not math.isfinite(age) or not 0 <= age <= OBSERVATION_NS / 1e6:
    raise ValueError('Navigation observation expired')
  text = payload.get('instruction', '')
  kind, modifier = payload.get('maneuverType', ''), payload.get('maneuverModifier', '')
  distance = payload.get('distanceMeters')
  if (not isinstance(text, str) or len(text) > 500 or (active and not text.strip()) or
      not isinstance(kind, str) or kind not in TYPES or not isinstance(modifier, str) or modifier not in MODIFIERS or
      (distance is not None and (type(distance) not in (int, float) or not math.isfinite(distance) or not 0 <= distance <= 1_000_000))):
    raise ValueError('Invalid navigation instruction')
  return session, sequence, age


class PhoneNavigationPublisher:
  def __init__(self, path=None, mono_clock=time.monotonic_ns):
    self.path = path or handoff_path()
    self.clock = mono_clock
    self.lock = threading.Lock()

  def publish(self, payload):
    session, sequence, age = validate_payload(payload)
    with self.lock:
      now = self.clock()
      observed = now - int(age * 1e6)
      old = _read(self.path)
      retired = old.get('retiredSessions', []) if old else []
      if session in retired:
        return {'ok': True, 'accepted': False}
      if old and old.get('session') != session:
        retired = [*retired[-15:], old['session']]
      if old and old.get('session') == session:
        if sequence < old['sequence'] or (sequence == old['sequence'] and payload.get('active') != old['active']):
          return {'ok': True, 'accepted': False}
        if sequence == old['sequence']:
          # A heartbeat may update age/transport receipt, never the maneuver.
          if any(payload.get(key) != old.get(key) for key in FIELDS - {'ageMs'}):
            return {'ok': True, 'accepted': False}
          # A retry/heartbeat cannot make the original observation younger.
          observed = min(observed, old['observed'])
      value = {**payload, 'observed': observed, 'received': now, 'bootOffset': boot_offset_ns(), 'retiredSessions': retired}
      temporary = self.path.with_name(self.path.name + '.tmp')
      temporary.write_text(json.dumps(value, allow_nan=False))
      temporary.replace(self.path)
    return {'ok': True, 'accepted': True}


def read_phone_navigation(now_ns, path=None):
  value = _read(path or handoff_path())
  try:
    if (abs(value['bootOffset'] - boot_offset_ns()) <= 1_000_000_000 and value['active'] and
        0 < value['observed'] <= now_ns <= value['observed'] + OBSERVATION_NS and
        0 < value['received'] <= now_ns <= value['received'] + LEASE_NS):
      return value
  except (TypeError, KeyError):
    pass
  return None


def phone_navigation_status(now_ns=None, path=None):
  now_ns = time.monotonic_ns() if now_ns is None else now_ns
  value = _read(path or handoff_path())
  if value is None:
    return None
  usable = read_phone_navigation(now_ns, path) is not None
  return {'state': 'receiving' if usable else 'ended' if not value['active'] else 'expired',
          'source': value.get('source', 'googleMaps'), 'receivedAgeMs': max(0, (now_ns - value['received']) / 1e6),
          'observationAgeMs': max(0, (now_ns - value['observed']) / 1e6),
          'instruction': value.get('instruction', '') if usable else '',
          'maneuverType': value.get('maneuverType', '') if usable else '',
          'maneuverModifier': value.get('maneuverModifier', '') if usable else '',
          'distanceMeters': value.get('distanceMeters') if usable else None}


def select_navigation(mapbox, phone, *, now_ns, drive_id, official_active=False, phone_enabled=True,
                      diagnostics=None, local_map=None):
  def rejected(reason):
    if diagnostics is not None:
      diagnostics['reason'] = reason
    return mapbox
  # Keep ownership while Mapbox is calculating/rerouting or waiting for GPS.
  if not phone_enabled:
    return rejected('disabled')
  if official_active or mapbox['status'] not in ('disabled', 'needsKey', 'noDestination'):
    return rejected('mapboxPriority')
  from openpilot.starpilot.navigation.notification import notification_instruction
  instruction, reason = notification_instruction(phone, now_ns=now_ns)
  if diagnostics is not None:
    diagnostics['reason'] = reason
  if instruction is None:
    return mapbox
  map_status = local_map.status(phone) if local_map is not None else 'mapUnavailable'
  if diagnostics is not None:
    diagnostics['mapStatus'] = map_status
  from openpilot.starpilot.navigation.wire import PHONE_CONTROL_AGE_NS, PHONE_SOURCE_PREFIX
  # Turn speed and turn assist need a known distance, a fresh observation and map agreement.
  distance = phone.get('distanceMeters')
  controlled = bool(map_status == 'mapAligned' and distance is not None and distance > 0 and drive_id > 0 and
                    drive_id <= phone['observed'] <= now_ns <= phone['observed'] + PHONE_CONTROL_AGE_NS)
  source = PHONE_SOURCE_PREFIX + phone.get('source', 'googleMaps')
  return {**mapbox, 'sessionId': source + ':' + phone['session'], 'revision': str(phone['sequence']),
          'enabled': True, 'status': 'guiding', 'source': source, 'frameMonoTime': now_ns,
          'startedMonoTime': drive_id, 'locationMonoTime': phone['observed'] if controlled else 0, 'observationMonoTime': phone['observed'],
          'distanceKnown': phone.get('distanceMeters') is not None, 'totalsKnown': False,
          'destinationName': '', 'route': [], 'nextManeuver': {}, 'controlValid': controlled,
          'instruction': instruction}
