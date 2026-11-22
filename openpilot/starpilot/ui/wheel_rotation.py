"""Display-only rotation from freshness-qualified carState, matching Small UI's sign."""

from collections.abc import Callable
import math
from pathlib import Path
from typing import Any

from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved

_ROTATION_KEY = 'LargeWheelRotation'


class _RotationPath:
  def __init__(self, params: Any):
    namespace = Path(params.get_param_path('OnroadCustomizations')).parent
    self.root = namespace.parent
    self.destination = self.root / f'.starpilot-{namespace.name}-large-wheel-rotation'

  def get_param_path(self, key: str) -> Path:
    if key != _ROTATION_KEY:
      raise ValueError('Unknown wheel preference')
    return self.destination


def read_rotation(params: Any) -> tuple[bytes | None, bool]:
  try:
    return read_saved(_RotationPath(params), _ROTATION_KEY, 1)
  except (AttributeError, OSError, TypeError, ValueError):
    return None, False


def write_rotation(params: Any, rotate: bool, expected: bytes | None, authorized: Callable[[], bool]) -> bool:
  path = _RotationPath(params)
  result = commit_exact(path, key=_ROTATION_KEY, max_bytes=1, raw=b'1' if rotate else b'0',
                        expected=expected, authorized=authorized, temp_prefix='.wheel-rotation-', lock_root=path.root)
  return result.committed and result.verified


def take_rotation(document: dict) -> bool:
  """Remove the device-local option from a validated editor copy before writing/exporting."""
  return document['layouts']['large']['steering_wheel'].pop('rotate', False) is True


def observe_wheel_rotation(car_state: Any) -> float:
  """Caller owns transport freshness and drive identity; unusable samples stay upright."""
  if (car_state is None or getattr(car_state, 'canValid', None) is not True or
      getattr(car_state, 'canTimeout', None) is not False):
    return 0.0
  angle = getattr(car_state, 'steeringAngleDeg', None)
  return -float(angle) if (type(angle) is int or type(angle) is float) and math.isfinite(angle) else 0.0
