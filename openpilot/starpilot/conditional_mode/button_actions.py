"""Exact-source saved wheel assignments; no gesture or runtime authority."""

from dataclasses import dataclass
from opendbc.car.gm.profiles import profiles_supported as gm_profiles_supported
import hashlib

from openpilot.starpilot.conditional_mode.manual import EXPERIMENTAL_MODE_ACTION, TRAFFIC_MODE_ACTION, ioniq6_media_eligible
from openpilot.starpilot.conditional_mode.preferences import MAX_DOCUMENT_BYTES, PreferenceError, decode_preferences
from openpilot.starpilot.saved_document import WriteResult, commit_exact
from openpilot.starpilot.saved_source import read_saved


MEDIA_KEYS = ("ModeButtonControl", "LongModeButtonControl", "VeryLongModeButtonControl",
              "StarButtonControl", "LongStarButtonControl", "VeryLongStarButtonControl")
DISTANCE_KEYS = ("DistanceButtonControl", "LongDistanceButtonControl", "VeryLongDistanceButtonControl")
BUTTON_PREFIX = "conditional:button:"
RUNTIME_KEYS = ("LKASButtonControl", "DistanceButtonControl", "LongDistanceButtonControl",
                "VeryLongDistanceButtonControl", "CancelButtonControl", "LongCancelButtonControl",
                "VeryLongCancelButtonControl", *MEDIA_KEYS)
CONFIG_KEY = "ConditionalModeConfig"
SAFE_KEY = "SafeMode"
MAX_SCALAR_BYTES = 8
CHOICES = {"Off": b"0", "Cycle conditional mode": str(EXPERIMENTAL_MODE_ACTION).encode(),
           "Toggle traffic mode": str(TRAFFIC_MODE_ACTION).encode(), "Switchback Mode": b"7"}
_CANONICAL_ACTIONS = frozenset(str(value).encode() for value in range(16))


@dataclass(frozen=True)
class ButtonSources:
  dependencies: tuple[tuple[str, bytes | None], ...]
  readable: bool

  def raw(self, key: str) -> bytes | None:
    return next(raw for name, raw in self.dependencies if name == key)


def capture_sources(params, config: bytes | None, safe: bytes | None) -> ButtonSources:
  """Read each runtime map key once for a displayed settings snapshot."""
  values = tuple((key, *read_saved(params, key, MAX_SCALAR_BYTES)) for key in RUNTIME_KEYS)
  return ButtonSources(((CONFIG_KEY, config), (SAFE_KEY, safe),
                        *((key, raw) for key, raw, _ in values)),
                       all(readable for _, _, readable in values))


def media_capability(cp, *, switchback_only: bool = False) -> tuple | None:
  """Bind the exact qualified CP bytes, including the observed E-CAN topology."""
  try:
    if (cp is None or not ioniq6_media_eligible(cp) or (not switchback_only and not cp.openpilotLongitudinalControl) or
        cp.passive or cp.dashcamOnly or cp.notCar):
      return None
    reader = cp.as_reader() if hasattr(cp, "as_reader") else cp
    payload = reader.as_builder().to_bytes()
    return (str(cp.carFingerprint), hashlib.sha256(payload).hexdigest())
  except (AttributeError, TypeError, ValueError, OverflowError, RuntimeError):
    return None


def distance_capability(cp) -> tuple | None:
  try:
    if not gm_profiles_supported(cp):
      return None
    reader = cp.as_reader() if hasattr(cp, "as_reader") else cp
    return (str(cp.carFingerprint), hashlib.sha256(reader.as_builder().to_bytes()).hexdigest())
  except (AttributeError, TypeError, ValueError, RuntimeError):
    return None


def assignment_capability(cp, key):
  return distance_capability(cp) if key in DISTANCE_KEYS else media_capability(cp) if key in MEDIA_KEYS else None


def display_action(raw: bytes | None) -> tuple[str, tuple[str, ...], str]:
  if raw is None or raw == b"0":
    return "Off", tuple(CHOICES), ""
  if raw == CHOICES["Cycle conditional mode"]:
    return "Cycle conditional mode", tuple(CHOICES), ""
  if raw == b"7":
    return "Switchback Mode", tuple(CHOICES), ""
  if raw == CHOICES["Toggle traffic mode"]:
    return "Toggle traffic mode", tuple(CHOICES), ""
  return "Unsupported saved action", (), "Off"


def runtime_map_ready(sources: ButtonSources, *, cp=None) -> bool:
  """Mirror the runtime map's scalar admission using the already captured bytes."""
  if not sources.readable or any(raw is not None and raw not in _CANONICAL_ACTIONS
                                 for _, raw in sources.dependencies[2:]):
    return False
  from opendbc.car.gm.cancel_remap import enabled as gm_cancel_remap_enabled
  return gm_cancel_remap_enabled(cp) or all(sources.raw(key) != b"5" for key in
             ("CancelButtonControl", "LongCancelButtonControl", "VeryLongCancelButtonControl"))


def commit_assignment(params, *, key: str, choice: str, expected: bytes | None,
                      dependencies: tuple[tuple[str, bytes | None], ...], authorized) -> WriteResult:
  if key not in (*MEDIA_KEYS, *DISTANCE_KEYS) or choice not in CHOICES or \
     (key in DISTANCE_KEYS and choice == "Cycle conditional mode") or len(dependencies) != len(RUNTIME_KEYS) + 2 or \
     tuple(name for name, _ in dependencies) != (CONFIG_KEY, SAFE_KEY, *RUNTIME_KEYS) or \
     dict(dependencies)[key] != expected:
    return WriteResult(False, False)

  def valid_sources(sources: ButtonSources) -> bool:
    if not sources.readable or sources.raw(SAFE_KEY) not in (None, b"0", b"1"):
      return False
    try:
      config = sources.raw(CONFIG_KEY)
      if config is not None:
        decode_preferences(config)
    except PreferenceError:
      return False
    return choice == "Off" or expected in (None, b"0", b"5", b"6", b"7")

  if not valid_sources(ButtonSources(dependencies, True)):
    return WriteResult(False, False)

  def fresh() -> bool:
    if not authorized():
      return False
    config, config_readable = read_saved(params, CONFIG_KEY, MAX_DOCUMENT_BYTES)
    safe, safe_readable = read_saved(params, SAFE_KEY, MAX_SCALAR_BYTES)
    if not config_readable or not safe_readable:
      return False
    current = capture_sources(params, config, safe)
    return valid_sources(current) and current.dependencies == dependencies and authorized()

  return commit_exact(params, key=key, max_bytes=MAX_SCALAR_BYTES,
                      raw=CHOICES[choice], expected=expected, authorized=fresh,
                      temp_prefix=".tmp_conditional_button_")
