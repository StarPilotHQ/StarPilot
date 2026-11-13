"""Saved display-unit increments whenever Card owns the cruise set speed."""

from dataclasses import dataclass
import math

from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.controllers.toyota_cruise import capability as toyota_capability, read_reverse_cruise


@dataclass(frozen=True)
class CruiseIntervals:
  short: float = 1.0
  held: float = 5.0


def _increment(raw: bytes | None, fallback: float) -> float:
  if raw is None:
    return fallback
  try:
    value = float(raw)
  except (ValueError, OverflowError):
    return fallback
  return value if math.isfinite(value) and 1.0 <= value <= 150.0 else fallback


def step_units(intervals: CruiseIntervals, fast: bool) -> int:
  """Whole display units one Tesla scroll moves the comma speed: Short scroll, or Fast scroll for a fast scroll."""
  return max(1, min(99, int(round(intervals.held if fast else intervals.short))))


def read_cruise_intervals(params, cp) -> CruiseIntervals:
  if toyota_capability(cp) is not None:
    return CruiseIntervals(5, 1) if read_reverse_cruise(cp, params) else CruiseIntervals()
  short, short_readable = read_saved(params, "CustomCruise", 16)
  held, held_readable = read_saved(params, "CustomCruiseLong", 16)
  return CruiseIntervals(_increment(short, 1.0) if short_readable else 1.0,
                         _increment(held, 5.0) if held_readable else 5.0)
