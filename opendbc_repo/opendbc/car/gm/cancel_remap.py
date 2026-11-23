"""Exact optional Bolt Cancel secondary-input configuration, selected at startup."""
from opendbc.car.gm.values import BOLT_PEDAL_WORDS, is_bolt_pedal_profile

GM_REMAP_CANCEL_TO_DISTANCE = 64
KEY = "RemapCancelToDistance"


def supported(cp):
  try:
    return bool(cp.carFingerprint in BOLT_PEDAL_WORDS and is_bolt_pedal_profile(cp))
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def valid_alternative_experience(cp):
  try:
    value = int(cp.alternativeExperience)
    return value in (0, 32) or value in (64, 96) and supported(cp)
  except (AttributeError, TypeError, ValueError):
    return False


def enabled(cp):
  try:
    return bool(supported(cp) and int(cp.alternativeExperience) in (64, 96))
  except (AttributeError, TypeError, ValueError):
    return False


def aol_enabled(cp):
  return valid_alternative_experience(cp) and int(cp.alternativeExperience) in (32, 96)


def prepare(cp, selected):
  if cp.brand != 'gm' or cp.carFingerprint not in BOLT_PEDAL_WORDS:
    return
  cp.alternativeExperience &= ~GM_REMAP_CANCEL_TO_DISTANCE
  if selected and supported(cp):
    cp.alternativeExperience |= GM_REMAP_CANCEL_TO_DISTANCE


def migrate_assignments(params, cp):
  from openpilot.starpilot.saved_source import read_saved
  if not enabled(cp):
    return False
  marker, readable = read_saved(params, 'CancelButtonControlsMigrated', 8)
  if not readable or marker not in (None, b'0'):
    return False
  pairs = (('DistanceButtonControl', 'CancelButtonControl', 1),
           ('LongDistanceButtonControl', 'LongCancelButtonControl', 5),
           ('VeryLongDistanceButtonControl', 'VeryLongCancelButtonControl', 6))
  pending = []
  valid = tuple(str(value).encode() for value in range(16))
  for source, target, default in pairs:
    raw, readable = read_saved(params, target, 8)
    previous, source_readable = read_saved(params, source, 8)
    if not readable or raw is not None and raw not in valid or not source_readable or previous is not None and previous not in valid:
      return False
    if raw is None:
      pending.append((target, int(previous) if previous is not None else default))
  for key, value in pending:
    params.put(key, value, block=True)
  params.put_bool('CancelButtonControlsMigrated', True, block=True)
  return True
