#!/usr/bin/env python3
import numpy as np

from openpilot.common.pid import PIDController
from openpilot.common.hardware import HARDWARE

IS_MICI = HARDWARE.get_device_type() == "mici"

# raise fan setpoint on tici/tizi to reduce noise
# after raising LMH threshold in AGNOS 18.1 to prevent CPU throttling
STOCK_CURVE = {"offset": 0, "k_p": 0., "ff_low": 60., "ff_high": 100.} if IS_MICI else \
              {"offset": 5, "k_p": 0., "ff_low": 65., "ff_high": 105.}
# tici/tizi opt-in: runs 10-16 C cooler than stock at the cost of fan noise
AGGRESSIVE_CURVE = {"offset": -5, "k_p": 1., "ff_low": 55., "ff_high": 80.}
K_I = 4e-3


class FanController:
  def __init__(self, rate: int) -> None:
    self.rate = rate
    self.last_ignition = False
    self.last_aggressive = False
    self.controller = PIDController(k_p=STOCK_CURVE["k_p"], k_i=K_I, rate=rate)

  def update(self, cur_temp: float, ignition: bool, aggressive_cooling: bool = False) -> int:
    aggressive = aggressive_cooling and not IS_MICI
    curve = AGGRESSIVE_CURVE if aggressive else STOCK_CURVE

    if ignition != self.last_ignition or aggressive != self.last_aggressive:
      self.controller = PIDController(k_p=curve["k_p"], k_i=K_I, rate=self.rate)
    self.last_ignition = ignition
    self.last_aggressive = aggressive

    self.controller.pos_limit = 100 if ignition else 30
    self.controller.neg_limit = 30 if ignition else 0

    return int(self.controller.update(
                 error=(cur_temp - (75 + curve["offset"])),  # temperature setpoint in C
                 feedforward=np.interp(cur_temp, [curve["ff_low"], curve["ff_high"]], [0, 100])
              ))
