"""First-generation EV6 physical authorization for the existing torque lease."""
from opendbc.car.hyundai.canfd_stock_aol import _base_word
from opendbc.car.hyundai.canfd_owner import config_index
from opendbc.car.hyundai.ev6_startup import eligible
from opendbc.car.hyundai.values import Buttons


def qualified(cp, *, marked_only=False):
  if not eligible(cp) or not cp.openpilotLongitudinalControl or cp.pcmCruise:
    return False
  if _base_word(cp, experiences=(0, 32)) != 0x11:
    return False
  word = cp.safetyConfigs[config_index(cp)].safetyParam
  return (word == 0x815 and cp.alternativeExperience == 32) or (
    not marked_only and word == 0x15 and cp.alternativeExperience == 0)


class Ev6PhysicalAuthorization:
  def __init__(self, *, lkas_on_engage):
    self.lkas_on_engage = bool(lkas_on_engage)
    self.main_on = self.lkas_on = False
    self.previous_main = self.previous_lkas = False
    self.previous_cruise = Buttons.NONE

  def revoke(self):
    # Retain actual held-button history: withdrawal is not a synthetic new edge.
    self.main_on = self.lkas_on = False

  def observe(self, main, lkas, cruise, *, main_available, lkas_toggle=True):
    if main and not self.previous_main:
      self.main_on = not self.main_on
    if lkas_toggle and lkas and not self.previous_lkas:
      self.lkas_on = not self.lkas_on
    if self.lkas_on_engage and cruise != self.previous_cruise and self.previous_cruise in (Buttons.SET_DECEL, Buttons.RES_ACCEL):
      self.lkas_on = True
    self.previous_main, self.previous_lkas, self.previous_cruise = bool(main), bool(lkas), cruise
    if not main_available:
      self.main_on = False

  @property
  def authorized(self):
    return self.main_on or self.lkas_on
