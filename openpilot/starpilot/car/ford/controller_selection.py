"""StarPilot-owned Ford selection; BluePilot receives only its original mode values."""
from enum import IntEnum

from opendbc.car.ford.values import CAR
from openpilot.starpilot.saved_source import read_saved

KEY = "FordPrefLateralControl"
CHOICES = ("BluePilot Curvature", "BluePilot Angle", "StarPilot Controller")
TUNED_CARS = frozenset((CAR.FORD_MUSTANG_MACH_E_MK1, CAR.FORD_EXPLORER_MK6))


class FordController(IntEnum):
  BLUEPILOT_CURVATURE = 0
  BLUEPILOT_ANGLE = 1
  STARPILOT = 2


def default_controller(cp):
  tuned = cp is not None and cp.brand == "ford" and cp.carFingerprint in TUNED_CARS
  return FordController.STARPILOT if tuned else FordController.BLUEPILOT_CURVATURE


def read_controller(cp, params):
  raw, readable = read_saved(params, KEY, 16)
  if not readable:
    return None, raw, False
  # Existing zero defaults become vehicle defaults; existing angle selections remain explicit.
  selected = default_controller(cp) if raw in (None, b"0") else {
    b"1": FordController.BLUEPILOT_ANGLE,
    b"2": FordController.BLUEPILOT_CURVATURE,
    b"3": FordController.STARPILOT,
  }.get(raw)
  return selected, raw, True


def encoded_choice(label):
  return dict(zip(CHOICES, (b"2", b"1", b"3"), strict=True)).get(label)
