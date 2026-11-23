"""Pure car-control configuration shared by the input daemon and cruise owner."""
import json
import math

INPUTS = {}
for action, label in [('left','Nudge left'),('right','Nudge right'),('up','Scroll up'),('down','Scroll down')]:
  INPUTS[f'right_wheel_{action}'] = ('Right scroll wheel', label)
for direction in ('up', 'down'):
  for stage in ('half', 'full'):
    INPUTS[f'right_stalk_{direction}_{stage}'] = ('Right stalk', f'{direction.title()} — {stage} press')
# A quick press down while cruise is already on; a press that engages cruise is not one. The half press keeps its
# original key (it was the only "tap while engaged" before the full press had its own).
STALK_TAP = 'right_stalk_down_tap_engaged'
INPUTS[STALK_TAP] = ('Right stalk', 'Down — half press while engaged')
STALK_TAP_FULL = 'right_stalk_down_full_engaged'
INPUTS[STALK_TAP_FULL] = ('Right stalk', 'Down — full press while engaged')
# Holding the stalk down also makes the Tesla set its own speed to the limit it detects (after about a second).
STALK_HOLD = 'right_stalk_down_hold'
INPUTS[STALK_HOLD] = ('Right stalk', 'Down — hold')
INPUTS['park_press'] = ('Right stalk', 'Park button')
# The right scroll wheel nudged left or right and held: each plain nudge -> its hold control.
WHEEL_HOLDS = {}
for _base, _label in (('right_wheel_left', 'Nudge left'), ('right_wheel_right', 'Nudge right')):
  WHEEL_HOLDS[_base] = _base + '_hold'
  INPUTS[_base + '_hold'] = ('Right scroll wheel', _label + ' — hold')
INPUTS['screen_three_finger_tap'] = ('Tesla screen', 'Three-finger tap')


PARAM = 'CarControlAssignments'
MAX_ASSIGNMENT_BYTES = 16384  # the Wheel Controls editor writes no more than this
MODELS = ('TESLA_MODEL_3', 'TESLA_MODEL_Y')


def parse_assignments(raw):
  if raw in (None, '', b'') or raw == {}:
    return {'version':1, 'vehicle':'', 'bindings':{}}
  try:
    data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    if data == {}:
      return {'version':1, 'vehicle':'', 'bindings':{}}
    if not isinstance(data, dict) or set(data) != {'version','vehicle','bindings'}:
      return None
    if type(data['version']) is not int or data['version'] != 1 or data['vehicle'] not in MODELS or not isinstance(data['bindings'], dict):
      return None
    for name, binding in data['bindings'].items():
      if name not in INPUTS or not isinstance(binding, dict) or set(binding) != {'key','value','label'}:
        return None
      if not isinstance(binding['key'], str) or not binding['key'] or not isinstance(binding['label'], str):
        return None
      value = binding['value']
      if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):
        return None
    return data
  except (ValueError, TypeError, UnicodeError):
    return None


SPEED_UP = '__starpilot_controller_action__:comma_speed_up'
SPEED_DOWN = '__starpilot_controller_action__:comma_speed_down'
SPEED_SET = '__starpilot_controller_action__:set_speed'
SPEED_RESUME = '__starpilot_controller_action__:resume_speed_limit'
SPEED_ACTIONS = frozenset((SPEED_UP, SPEED_DOWN, SPEED_SET, SPEED_RESUME))
# Why Resume is unavailable while SLC runs but has no live limit (the map source drops out for a second or two).
NO_LIMIT_REASON = 'No current speed limit'
# An explicit "Unassigned" saved over a default binding.
NO_ACTION = '__starpilot_controller_action__:none'
_RESUME = {'key': SPEED_RESUME, 'value': None, 'label': 'Resume Speed Limit + Offset'}
# Holding the right stalk down always sets the comma speed to its limit (the Tesla sets its own limit too).
FIXED_BINDINGS = {STALK_HOLD: _RESUME}
# The favorites catalog's Cycle Driving Personality (favorites/actions.py CYCLE_PERSONALITY; this module imports nothing).
CYCLE_PERSONALITY = '__starpilot_controller_action__:cycle_driving_personality'
# What a control starts on until it is changed: the half press while engaged on the hold's action, and the right scroll
# wheel's nudge right on Cycle Driving Personality.
DEFAULT_BINDINGS = {STALK_TAP: _RESUME,
                    'right_wheel_right': {'key': CYCLE_PERSONALITY, 'value': None, 'label': 'Cycle Driving Personality'}}


# The right scroll wheel's scroll is never assignable. While the comma owns the set speed (Comma can set speed on Tesla
# dashboard is on) a scroll moves the comma's set speed, by 1 or to the next multiple of 5 for a fast scroll, as the Tesla's
# own cruise does; otherwise the scroll is left to the Tesla.
SCROLL_BINDINGS = {'right_wheel_up': {'key': SPEED_UP, 'value': None, 'label': 'Increase Comma Set Speed'},
                   'right_wheel_down': {'key': SPEED_DOWN, 'value': None, 'label': 'Decrease Comma Set Speed'}}
# The Tesla controls StarPilot uses: the right scroll wheel, the right stalk's presses while engaged and its hold, and the
# screen tap. The others steer, signal, light, wipe or shift the car.
ACTIVE_INPUTS = frozenset(('right_wheel_up', 'right_wheel_down', 'right_wheel_left', 'right_wheel_right', *WHEEL_HOLDS.values(),
                           STALK_TAP, STALK_TAP_FULL, STALK_HOLD, 'screen_three_finger_tap'))


def effective_bindings(document, *, comma_speed=False):
  """The bindings that act: saved ones over the defaults, with the fixed ones always, explicit Unassigned removed, and only
  on the controls StarPilot uses. comma_speed: the comma owns the set speed on this car, so the scroll sets it."""
  bindings = {**DEFAULT_BINDINGS, **(document['bindings'] if document else {}), **FIXED_BINDINGS}
  bindings = {name: binding for name, binding in bindings.items() if name not in SCROLL_BINDINGS}
  if comma_speed:
    bindings.update(SCROLL_BINDINGS)
  return {name: binding for name, binding in bindings.items() if binding['key'] != NO_ACTION and name in ACTIVE_INPUTS}


def comma_speed(params, cp):
  """Comma can set speed on Tesla dashboard is on for this car: the comma owns the set speed."""
  from openpilot.starpilot.car.tesla.set_speed import active
  return active(params, cp)


def vehicle_supported(cp):
  from opendbc.car.tesla.values import TeslaFlags
  return bool(cp is not None and cp.carFingerprint in MODELS and cp.brand=='tesla' and
              cp.flags & TeslaFlags.HAS_VEHICLE_BUS and not cp.passive and not cp.notCar and not cp.dashcamOnly)

def screen_reserved(cp):
  from opendbc.car.tesla.values import TeslaFlags,TeslaSafetyFlags
  return bool(cp.flags & TeslaFlags.AOL_SCREEN_BUTTON or
              any(s.safetyParam & TeslaSafetyFlags.AOL_SCREEN_BUTTON for s in cp.safetyConfigs))
