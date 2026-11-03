"""Reviewed saved-setting mapping for the opt-in system-longitudinal SLC runtime."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import offset_document as od
from openpilot.starpilot.speed_limits import selection as sel
from openpilot.starpilot.speed_limits import speed_domain as sd

LABELS = {"Dashboard": sel.Source.DASHBOARD, "Map Data": sel.Source.MAP, "Maps": sel.Source.MAP, "Vision": sel.Source.VISION}
SOURCE_PRIORITY_KEYS = ('SLCPriority1', 'SLCPriority2', 'SLCPriority3')
SOURCE_PRIORITY_DEFAULTS = ('Vision', 'Map Data', 'Off')
SOURCE_PRIORITY_MAX_BYTES = 32
CONFIRMATION_CHOICES = {0: 'Off', 1: 'Both', 3: 'Higher only', 4: 'Lower only'}
CONFIRMATION_BYTES = tuple(str(mode).encode() for mode in CONFIRMATION_CHOICES)
ADVANCE_KEYS = ('SLCAdvanceHigherSeconds', 'SLCAdvanceLowerSeconds')
ADVANCE_SECONDS = range(11)
ADVANCE_BYTES = tuple(str(seconds).encode() for seconds in ADVANCE_SECONDS)

PEDAL_OVERRIDE_KEY = 'SLCOverrideMode'
PEDAL_OVERRIDE_CHOICES = ('Resume the adjusted limit', 'Hold the speed you reached', 'Use your SET speed')
PEDAL_OVERRIDE_BYTES = (b'0', b'1', b'2')
WHEEL_OVERRIDE_KEY = 'SLCWheelOverride'


class PedalOverride(IntEnum):
  OFF = 0
  SPEED_REACHED = 1
  SET_SPEED = 2


@dataclass(frozen=True)
class Settings:
  enabled: bool
  display: bool
  selection: sel.SelectionPolicy
  acceptance: acc.Policy
  offsets: sd.OffsetSchedule
  errors: tuple[str, ...] = ()
  # Saved intent must still retire pending work when vehicle capabilities disable control.
  requested: bool = False
  advance_higher_seconds: int = 0
  advance_lower_seconds: int = 0
  pedal_override: PedalOverride = PedalOverride.OFF
  wheel_override: bool = False


def _bool(values: Mapping[str, object], key: str, default: bool = False) -> bool:
  value = values.get(key, default)
  if type(value) is not bool:
    raise ValueError(f"{key} must be Boolean")
  return value


def _disabled(reason: str) -> Settings:
  selection = sel.SelectionPolicy(sel.SelectionMode.ORDERED, (sel.Source.DASHBOARD, sel.Source.MAP), False)
  return Settings(False, False, selection, acc.Policy(display_only=True),
                  sd.OffsetSchedule((sd.OffsetBand(0.0, None, 0.0),)), (reason,))


def parse(values: Mapping[str, object]) -> Settings:
  """Translate saved labels without changing their meaning or enabling unsupported output."""
  try:
    control = _bool(values, "SpeedLimitController")
    display = control or _bool(values, "ShowSpeedLimits", True)
    document = (od.validate(values['SLCOffsetSchedule']) if 'SLCOffsetSchedule' in values else
                od.default_document(_bool(values, 'IsMetric')))
    if not isinstance(document, od.OffsetDocument):
      raise ValueError('saved SLC offsets need review')
    first, second, third = (values.get(key, default) for key, default in
                            zip(SOURCE_PRIORITY_KEYS, SOURCE_PRIORITY_DEFAULTS, strict=True))
    if any(type(source) is not str for source in (first, second, third)):
      raise ValueError("invalid saved SLC source priority")
    mode = {"Highest": sel.SelectionMode.HIGHEST, "Lowest": sel.SelectionMode.LOWEST}.get(first, sel.SelectionMode.ORDERED)
    if first not in (*LABELS, "Highest", "Lowest") or any(source not in (*LABELS, 'Off') for source in (second, third)):
      raise ValueError("invalid saved SLC source priority")
    slots = (LABELS.get(first), LABELS.get(second), LABELS.get(third))
    confirmation = values.get('SLCConfirmation', 0)
    if type(confirmation) is not int or confirmation not in CONFIRMATION_CHOICES:
      raise ValueError('invalid saved SLC confirmation mode')
    higher = values.get('SLCAdvanceHigherSeconds', 0)
    lower = values.get('SLCAdvanceLowerSeconds', 0)
    if type(higher) is not int or higher not in ADVANCE_SECONDS:
      higher = 0
    if type(lower) is not int or lower not in ADVANCE_SECONDS:
      lower = 0
    policy = acc.Policy(confirm_lower=confirmation in (1, 4),
                        confirm_higher=confirmation in (1, 3),
                        display_only=not control)
    offsets = document.schedule()
    pedal_override = values.get(PEDAL_OVERRIDE_KEY, PedalOverride.SPEED_REACHED.value)
    if type(pedal_override) is not int or pedal_override not in (0, 1, 2):
      pedal_override = PedalOverride.OFF.value
    return Settings(control, display, sel.SelectionPolicy(mode, slots, False), policy, offsets,
                    requested=control, advance_higher_seconds=higher, advance_lower_seconds=lower,
                    pedal_override=PedalOverride(pedal_override), wheel_override=values.get(WHEEL_OVERRIDE_KEY) is True)
  except (ValueError, OverflowError) as error:
    return _disabled(str(error))


SAVED_KEYS = ("SpeedLimitController", "ShowSpeedLimits", "IsMetric", *SOURCE_PRIORITY_KEYS,
              "SLCConfirmation")


def read_params(params) -> Settings:
  values = {WHEEL_OVERRIDE_KEY: read_saved(params, WHEEL_OVERRIDE_KEY, 1) == (b'1', True)}
  raw_override, readable_override = read_saved(params, PEDAL_OVERRIDE_KEY, 1)
  if raw_override is not None or not readable_override:
    values[PEDAL_OVERRIDE_KEY] = int(raw_override) if readable_override and raw_override in PEDAL_OVERRIDE_BYTES else 0
  for key in ADVANCE_KEYS:
    raw, readable = read_saved(params, key, 2)
    if readable and raw in ADVANCE_BYTES:
      values[key] = int(raw)
  try:
    with Path(params.get_param_path('SLCOffsetSchedule')).open('rb') as stream:
      raw_document = stream.read(od.MAX_DOCUMENT_BYTES + 1)
  except FileNotFoundError:
    raw_document = None
  except OSError:
    return _disabled('Invalid saved SLCOffsetSchedule')
  if raw_document is not None:
    try:
      values['SLCOffsetSchedule'] = od.decode(raw_document)
    except (ValueError, OverflowError):
      return _disabled('Invalid saved SLCOffsetSchedule')
  for key in SAVED_KEYS:
    if raw_document is not None and key == 'IsMetric':
      continue
    # Only missing files use defaults. Invalid bytes must not change confirmation or units.
    try:
      if key in (*SOURCE_PRIORITY_KEYS, 'SLCConfirmation'):
        raw, readable = read_saved(params, key, 1 if key == 'SLCConfirmation' else SOURCE_PRIORITY_MAX_BYTES)
        if not readable:
          return _disabled(f'Invalid saved {key}')
        if raw is None:
          continue
      else:
        with Path(params.get_param_path(key)).open('rb') as stream:
          raw = stream.read()
      if key in SOURCE_PRIORITY_KEYS:
        value = raw.decode('utf-8')
      elif key == 'SLCConfirmation':
        if raw not in CONFIRMATION_BYTES:
          raise ValueError("invalid confirmation mode")
        value = int(raw)
      else:
        if raw not in (b'0', b'1'):
          raise ValueError('invalid Boolean')
        value = raw == b'1'
      values[key] = value
    except FileNotFoundError:
      continue
    except (OSError, ValueError, OverflowError):
      return _disabled(f'Invalid saved {key}')
  return parse(values)
