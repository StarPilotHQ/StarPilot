"""Exact Ford release opt-in before parser/controller construction."""
from opendbc.car.ford.aol import qualified, aol_safety_config
from openpilot.starpilot.saved_source import read_saved


def prepare_release_long(cp, params, *, is_release, enabled):
  if not is_release or not enabled or cp.brand != 'ford':
    return cp
  try:
    if (cp.alternativeExperience != 0 or not qualified(cp) or not cp.alphaLongitudinalAvailable or
        cp.openpilotLongitudinalControl or read_saved(params, 'AlphaLongitudinalEnabled', 8) != (b'1', True) or
        read_saved(params, 'SafeMode', 8) not in ((None, True), (b'0', True))):
      return cp
    candidate = cp.as_reader().as_builder()
    aol_safety_config(candidate).safetyParam |= 1
    candidate.openpilotLongitudinalControl = True
    return candidate if qualified(candidate) else cp
  except (AttributeError, OSError, TypeError, ValueError, OverflowError, IndexError):
    return cp


def release_long_available(cp):
  try:
    return bool(cp is not None and cp.brand == 'ford' and cp.alphaLongitudinalAvailable and qualified(cp))
  except (AttributeError, TypeError, ValueError, OverflowError, IndexError):
    return False
