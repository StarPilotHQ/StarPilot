"""Optional Hyundai CAN-FD camera stop-sign and EV drive-mode observations; never a longitudinal control grant."""
import time

from opendbc.car.hyundai.values import HyundaiFlags
from openpilot.starpilot.car.mazda.dashboard import write_stop_sign

DRIVE_MODE_MAX_AGE_NS = 2_000_000_000
DRIVE_MODE_ECO = 4
DRIVE_MODE_SPORT = 5
ACCELERATOR_PEDAL_FULL = 254.0  # highest raw value seen at full travel on the Ioniq 6


def capable(cp):
  return cp.brand == 'hyundai' and bool(int(cp.flags) & HyundaiFlags.CANFD) and not cp.notCar


def compose(cp, parsed, state, message, *, car_state_stamp):
  if not capable(cp):
    return message
  message = write_stop_sign(parsed, state, message, car_state_stamp=car_state_stamp)
  message = write_drive_mode(parsed, state, message)
  return write_accelerator_pedal(parsed, state, message)


def _fresh(observation, state):
  if type(observation) is not tuple or len(observation) != 2 or not state.canValid or state.canTimeout:
    return None
  value, stamp = observation
  age = time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC)) - stamp
  return value if 0 <= age <= DRIVE_MODE_MAX_AGE_NS else None


def write_accelerator_pedal(parsed, state, message):
  pedal = _fresh(getattr(parsed, 'accelerator_pedal', None), state)
  if pedal is not None:
    message.starpilotCarState.acceleratorPedal = min(max(pedal / ACCELERATOR_PEDAL_FULL, 0.0), 1.0)
  return message


def write_drive_mode(parsed, state, message):
  mode = _fresh(getattr(parsed, 'drive_mode', None), state)
  if mode is not None:
    message.starpilotCarState.ecoGear = mode == DRIVE_MODE_ECO
    message.starpilotCarState.sportGear = mode == DRIVE_MODE_SPORT
  return message
