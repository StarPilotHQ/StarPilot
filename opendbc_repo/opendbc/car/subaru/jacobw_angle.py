"""StarPilot adapter for jacobw's jul-angle-based Crosstrek control.

Source: jacobwaller/opendbc 8acbeccdab45a47aa7af170d2d17b61f0eb5e4f0.
Original low-speed filtering: lukasloetkolben; target-first limiting: elkoled.
Jacob Waller integrated the implementation and corrected its VM limit type.
Keep StarPilot's model, stricter rate, and withdrawal guards. See CREDITS.md.
"""
import numpy as np

from opendbc.car import rate_limit
from opendbc.car.lateral import get_max_angle_delta_vm, get_max_angle_vm

MAX_ANGLE = 190.0


def limit_angle(target, previous, speed, measured, active, limits, model):
  if not active:
    return float(np.clip(measured, -MAX_ANGLE, MAX_ANGLE)), False
  speed = max(speed, 1.0)
  maximum = min(MAX_ANGLE, get_max_angle_vm(speed, model, limits))
  delta = min(get_max_angle_delta_vm(speed, model, limits), limits.ANGLE_LIMITS.MAX_ANGLE_RATE)
  # Clip the target before applying jerk limits, as in the source fork.
  target = float(np.clip(target, -maximum, maximum))
  # An abrupt input change can leave no intersection between accel and jerk
  # limits. Withdraw instead of publishing an active command native must reject.
  if previous - delta > maximum or previous + delta < -maximum:
    return float(np.clip(measured, -MAX_ANGLE, MAX_ANGLE)), False
  return float(np.clip(rate_limit(target, previous, -delta, delta), -MAX_ANGLE, MAX_ANGLE)), True
