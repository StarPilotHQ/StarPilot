"""Tesla input worker: private receiver protocol; no authority is granted by the worker."""
import hashlib
import json
import math

from openpilot.starpilot.conditional_mode.ui_action import fresh_service

MAX_PACKET = 2048
MAX_EVENTS = 4
MAX_AGE = .25
# The worker's first message: it is up and reading CAN.
READY = b'TESLA_INPUT_READY'


def drive_id(sm, now):
  try:
    now_ns = int(now * 1e9)
    drive = int(sm['deviceState'].startedMonoTime)
    if (sm['deviceState'].started and 0 < drive < now_ns
        and fresh_service(sm, 'deviceState', drive, now_ns, 1_000_000_000)
        and fresh_service(sm, 'carState', drive, now_ns)
        and sm['carState'].canValid and not sm['carState'].canTimeout):
      return drive
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
    pass
  return None


def document_hash(raw):
  return hashlib.sha256(raw).hexdigest()


def encode_event(sequence, name, fast, stamp, drive, document, vehicle, *, provenance=None):
  value = {'version': 1, 'sequence': sequence, 'name': name, 'fast': bool(fast),
               'stamp': stamp, 'drive': drive, 'document': document, 'vehicle': vehicle}
  if provenance is not None:
    from openpilot.starpilot.controllers.tesla_gesture_context import source_from_event
    value.update(version=2, provenance=provenance)
    if source_from_event(value) is None:
      raise ValueError("Invalid Tesla gesture provenance")
  return json.dumps(value, separators=(',', ':')).encode()


def decode_event(raw, now):
  try:
    if not raw or len(raw) > MAX_PACKET:
      return None
    value = json.loads(raw)
    keys = {'version', 'sequence', 'name', 'fast', 'stamp', 'drive', 'document', 'vehicle'}
    if isinstance(value, dict) and value.get('version') == 2:
      keys.add('provenance')
    if (not isinstance(value, dict) or set(value) != keys
        or type(value['version']) is not int or value['version'] not in (1, 2)
        or type(value['sequence']) is not int or value['sequence'] <= 0
        or type(value['name']) is not str or type(value['fast']) is not bool
        or type(value['stamp']) not in (int, float) or not math.isfinite(value['stamp'])
        or not 0 <= now - value['stamp'] <= MAX_AGE
        or type(value['drive']) is not int or not 0 < value['drive'] < value['stamp'] * 1e9
        or any(type(value[k]) is not str or len(value[k]) != 64
               or any(c not in '0123456789abcdef' for c in value[k]) for k in ('document', 'vehicle'))):
      return None
    if value['version'] == 2:
      from openpilot.starpilot.controllers.tesla_gesture_context import source_from_event
      if source_from_event(value) is None:
        return None
    return value
  except (ValueError, TypeError, UnicodeError, OverflowError):
    return None

