"""The first below-minimum-steering-speed episode in an onroad session."""

import math


def advisory_eligible(CP, CS, *, active: bool, intent) -> bool:
  armed = intent is not None and intent.allowedLatch and intent.lateralArmed and not intent.pauseLateral
  return bool((active or armed) and (CP.brand != 'gm' or CS.cruiseState.available))


class LowSpeedAdvisory:
  def __init__(self):
    self.drive_id = 0
    self.has_been_above = False
    self.shown = False
    self.showing = False

  def update(self, speed: float, minimum: float, *, drive_id: int = 0, eligible: bool = True) -> bool:
    # Unknown/transient device state does not restart the advisory lifecycle.
    if drive_id > 0 and drive_id != self.drive_id:
      self.drive_id = drive_id
      self.has_been_above = False
      self.shown = False
      self.showing = False
    if not eligible:
      self.showing = False
      return False
    if not math.isfinite(speed) or not math.isfinite(minimum) or minimum <= 0:
      return False
    under = speed < minimum
    if not under:
      self.has_been_above = True
      self.showing = False
    elif self.has_been_above and not self.shown:
      self.shown = True
      self.showing = True
    return self.showing and under
