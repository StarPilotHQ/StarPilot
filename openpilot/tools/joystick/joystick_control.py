#!/usr/bin/env python3
import time
import argparse
import threading
import numpy as np
import inputs
from inputs import UnpluggedError, get_gamepad

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.hardware import HARDWARE, PC
from openpilot.tools.lib.kbhit import KBHit
from openpilot.tools.joystick.devices import refresh_gamepads

EXPO = 0.4

# Keyed on a substring of the `inputs` gamepad name. Use tools/joystick/joystick_probe.py to add one.
CONTROLLER_PROFILES = {
  'Stadia':    {'name': 'Stadia',    'steer': 'ABS_X', 'accel': 'ABS_RZ', 'lo': 0.,      'hi': 255.},
  'X-Box':     {'name': 'Xbox',      'steer': 'ABS_X', 'accel': 'ABS_RY', 'lo': -32768., 'hi': 32767.},
  'DualSense': {'name': 'DualSense', 'steer': 'ABS_X', 'accel': 'ABS_RY', 'lo': 0.,      'hi': 255.},
  'Wireless Controller': {'name': 'DualShock 4', 'steer': 'ABS_X', 'accel': 'ABS_RZ', 'lo': 0., 'hi': 255.},
}
DEFAULT_PROFILE = 'X-Box'


class Keyboard:
  def __init__(self):
    self.kb = KBHit()
    self.axis_increment = 0.05  # 5% of full actuation each key press
    self.axes_map = {'w': 'gb', 's': 'gb',
                     'a': 'steer', 'd': 'steer'}
    self.axes_values = {'gb': 0., 'steer': 0.}
    self.axes_order = ['gb', 'steer']
    self.cancel = False

  def update(self):
    key = self.kb.getch().lower()
    self.cancel = False
    if key == 'r':
      self.axes_values = dict.fromkeys(self.axes_values, 0.)
    elif key == 'c':
      self.cancel = True
    elif key in self.axes_map:
      axis = self.axes_map[key]
      incr = self.axis_increment if key in ['w', 'a'] else -self.axis_increment
      self.axes_values[axis] = float(np.clip(self.axes_values[axis] + incr, -1, 1))
    else:
      return False
    return True


class Joystick:
  def __init__(self):
    self.cancel_button = 'BTN_NORTH'  # BTN_NORTH=X/triangle
    self.is_pc = HARDWARE.get_device_type() == 'pc'
    self._last_scan = 0.
    if not self.is_pc:
      refresh_gamepads()
    self._load_profile()

  def _load_profile(self):
    if self.is_pc:
      # DualSense over a laptop for development
      accel_axis, steer_axis = 'ABS_Z', 'ABS_RX'
      self.flip_map = {'ABS_RZ': accel_axis}
      raw_min, raw_max, self.deadzone = 0., 255., 0.03
      name, prof_name = 'pc', 'pc'
    else:
      name = inputs.devices.gamepads[0].name if inputs.devices.gamepads else ''
      prof = next((p for key, p in CONTROLLER_PROFILES.items() if key in name), CONTROLLER_PROFILES[DEFAULT_PROFILE])
      accel_axis, steer_axis = prof['accel'], prof['steer']
      self.flip_map = {}
      raw_min, raw_max, self.deadzone = prof['lo'], prof['hi'], 0.10
      prof_name = prof['name']

    cloudlog.info(f"joystick_control: gamepad='{name}' using profile '{prof_name}'")
    self.min_axis_value = {accel_axis: raw_min, steer_axis: raw_min}
    self.max_axis_value = {accel_axis: raw_max, steer_axis: raw_max}
    self.axes_values = {accel_axis: 0., steer_axis: 0.}
    self.axes_order = [accel_axis, steer_axis]
    self.cancel = False

  def _rescan(self):
    # `inputs` enumerates /dev/input once at import, so a pad that wasn't ready at boot is never read without this
    now = time.monotonic()
    if now - self._last_scan < 1.0:
      return
    self._last_scan = now
    refresh_gamepads()
    if not self.is_pc and inputs.devices.gamepads:
      self._load_profile()

  def update(self):
    try:
      joystick_event = get_gamepad()[0]
    except (OSError, UnpluggedError):
      self.axes_values = dict.fromkeys(self.axes_values, 0.)
      self._rescan()
      time.sleep(0.1)
      return False

    event = (joystick_event.code, joystick_event.state)

    if event[0] in self.flip_map:
      event = (self.flip_map[event[0]], -event[1])

    if event[0] == self.cancel_button:
      if event[1] == 1:
        self.cancel = True
      elif event[1] == 0:   # state 0 is falling edge
        self.cancel = False
    elif event[0] in self.axes_values:
      norm = -float(np.interp(event[1], [self.min_axis_value[event[0]], self.max_axis_value[event[0]]], [-1., 1.]))
      norm = norm if abs(norm) > self.deadzone else 0.  # center can be noisy
      self.axes_values[event[0]] = EXPO * norm ** 3 + (1 - EXPO) * norm  # less action near center for fine control
    else:
      return False
    return True


def send_thread(joystick):
  pm = messaging.PubMaster(['testJoystick'])

  rk = Ratekeeper(100, print_delay_threshold=None)

  while True:
    if rk.frame % 20 == 0:
      print('\n' + ', '.join(f'{name}: {round(v, 3)}' for name, v in joystick.axes_values.items()))

    # _rescan() may swap the axis map from another thread
    values, order = joystick.axes_values, joystick.axes_order
    joystick_msg = messaging.new_message('testJoystick')
    joystick_msg.valid = True
    joystick_msg.testJoystick.axes = [values.get(ax, 0.) for ax in order]

    pm.send('testJoystick', joystick_msg)

    rk.keep_time()


def joystick_control_thread(joystick):
  Params().put_bool('JoystickDebugMode', True, block=True)
  threading.Thread(target=send_thread, args=(joystick,), daemon=True).start()
  while True:
    joystick.update()


def main():
  joystick_control_thread(Joystick())


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description='Publishes events from your joystick to control your car.\n' +
                                               'openpilot must be offroad before starting joystick_control. This tool supports ' +
                                               'a PlayStation 5 DualSense controller on the comma 3X.',
                                   formatter_class=argparse.ArgumentDefaultsHelpFormatter)
  parser.add_argument('--keyboard', action='store_true', help='Use your keyboard instead of a joystick')
  args = parser.parse_args()

  if not PC and not Params().get_bool("IsOffroad"):
    print("The car must be off before running joystick_control.")
    exit()

  print()
  if args.keyboard:
    print('Gas/brake control: `W` and `S` keys')
    print('Steering control: `A` and `D` keys')
    print('Buttons')
    print('- `R`: Resets axes')
    print('- `C`: Cancel cruise control')
  else:
    print('Using joystick, make sure to run openpilot/system/webrtc/bridge.py if running over the network!')
    print('If not running on a comma device, the mapping may need to be adjusted.')

  joystick = Keyboard() if args.keyboard else Joystick()
  joystick_control_thread(joystick)
