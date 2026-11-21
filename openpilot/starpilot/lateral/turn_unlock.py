"""Private turn-assist admission state; speed never commands steering."""
import math

from openpilot.common.constants import CV
from openpilot.common.realtime import DT_CTRL
from openpilot.starpilot.lateral.model_turn_assist import CURVATURE_HOLD_CONFIRM_MIN, CURVATURE_HOLD_SWEPT_EXIT

UNLOCK_SPEED = 2 * CV.MPH_TO_MS


class TurnUnlock:
  def __init__(self):
    self.reset()

  def reset(self):
    self.direction = 0
    self.unlocked = False
    self.driver_initiated = False
    self.ended = False
    self.swept = 0.0

  def sample(self, cs, *, valid, driver_confirmed=False, lateral_active=True, curvature=0.0, ended=False):
    direction = int(cs.rightBlinker) - int(cs.leftBlinker)
    if direction != self.direction or direction == 0:
      self.reset()
      self.direction = direction
    if direction == 0:
      return False
    if ended:
      self.ended = True
      self.unlocked = self.driver_initiated = False
    if not valid or self.ended or not math.isfinite(cs.vEgo) or cs.vEgo < 0:
      return False
    if self.unlocked and math.isfinite(curvature):
      self.swept += max(cs.vEgo * curvature * direction, 0.0) * DT_CTRL
      if self.swept > CURVATURE_HOLD_SWEPT_EXIT and abs(curvature) < CURVATURE_HOLD_CONFIRM_MIN:
        self.ended = True
        self.unlocked = self.driver_initiated = False
        return False
    if cs.vEgo <= UNLOCK_SPEED:
      self.unlocked = True
    if not lateral_active:
      self.driver_initiated = False
    elif driver_confirmed:
      self.driver_initiated = True
    return self.unlocked


  def permits(self, *, unlocked, automatic, lateral_ready, stopping, driver_braking, blindspot):
    return bool(unlocked and lateral_ready and (self.driver_initiated or automatic) and
                not stopping and not driver_braking and not blindspot)
