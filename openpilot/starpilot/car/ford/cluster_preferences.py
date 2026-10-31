"""Dom-compatible opt-in cluster display, independent of lateral authority."""
from openpilot.starpilot.saved_source import read_saved

KEY = "FordHandsFreeCluster"
CHOICES = {0: "Off", 1: "On"}


def qualified(cp):
  """Dom's CAN-FD/non-LKA scope: the CAN FD Fords and the Mondeo."""
  from opendbc.car.ford.values import CAR, FORD_CANFD_CARS, FordFlags
  return bool(cp.brand == "ford" and cp.flags & FordFlags.CANFD and not cp.flags & FordFlags.LKA_STEERING and
              not cp.dashcamOnly and not cp.passive and cp.carFingerprint in FORD_CANFD_CARS | {CAR.FORD_MONDEO_MK5})


def read_choice(params):
  raw, readable = read_saved(params, KEY, 8)
  valid = readable and raw in (None, b"0", b"1")
  return int(valid and raw == b"1"), raw, readable, valid


class FordClusterPreference:
  def __init__(self, params):
    self.params = params
    self.enabled = bool(read_choice(params)[0])
    self.frames = 0

  def update(self):
    if self.frames % 100 == 0:
      self.enabled = bool(read_choice(self.params)[0])
    self.frames += 1
