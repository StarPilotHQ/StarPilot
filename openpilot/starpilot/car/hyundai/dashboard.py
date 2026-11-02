"""Optional Hyundai CAN-FD camera stop-sign observation; never a longitudinal control grant."""
from opendbc.car.hyundai.values import HyundaiFlags
from openpilot.starpilot.car.mazda.dashboard import write_stop_sign


def capable(cp):
  return cp.brand == 'hyundai' and bool(int(cp.flags) & HyundaiFlags.CANFD) and not cp.notCar


def compose(cp, parsed, state, message, *, car_state_stamp):
  if not capable(cp):
    return message
  return write_stop_sign(parsed, state, message, car_state_stamp=car_state_stamp)
