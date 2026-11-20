"""Provisional review compatibility; never automatic tune qualification."""

import hashlib
import json

from openpilot.starpilot.flm.manual_coordinates import baseline_settings


def review_settings(cp, params):
  from openpilot.starpilot.flm.vehicle_identity import identity_keys
  from openpilot.starpilot.lateral.torque_settings import LEGACY_KEYS, MAX_DOCUMENT_BYTES
  from openpilot.starpilot.saved_source import read_saved

  try:
    settings = []
    for key in ('SteeringLearningPreferences', 'ForceAutoTuneOff', 'AutoSteeringLearningReset', 'LateralControllerSelection',
                'OpenpilotEnabledToggle', 'SafeMode', 'TurnAssist', 'SteeringDelayPreferences', *LEGACY_KEYS, *identity_keys(cp)):
      raw, okay = read_saved(params, key, MAX_DOCUMENT_BYTES)
      if not okay:
        return None
      settings.append((key, None if raw is None else hashlib.sha256(raw).hexdigest()))
    return hashlib.sha256(json.dumps((baseline_settings(cp, params), settings), separators=(',', ':')).encode()).hexdigest()
  except (AttributeError, KeyError, OSError, TypeError, ValueError):
    return None
