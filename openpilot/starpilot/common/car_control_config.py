"""Pure car-control configuration shared by the input daemon and cruise owner."""
import json
import math

INPUTS = {}
for side in ('left', 'right'):
  for action, label in [('left','Nudge left'),('right','Nudge right'),('up','Scroll up'),('down','Scroll down'),('press','Press inward')]:
    INPUTS[f'{side}_wheel_{action}'] = (f'{side.title()} scroll wheel', label)
for direction in ('left', 'right'):
  for stage in ('half', 'full'):
    INPUTS[f'indicator_{direction}_{stage}'] = ('Left stalk', f'Indicator {direction} — {stage} press')
for key, label in [('beam_pull','Pull (flash beams)'),('beam_push','Push (high beams)'),
                   ('wiper_half','Wiper button — first detent'),('wiper_full','Wiper button — second detent')]:
  INPUTS[key] = ('Left stalk', label)
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
INPUTS['screen_three_finger_tap'] = ('Tesla screen', 'Three-finger tap')


PARAM = 'CarControlAssignments'
MAX_ASSIGNMENT_BYTES = 32768
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
# The half press while engaged starts on the same action and can be changed.
DEFAULT_BINDINGS = {STALK_TAP: _RESUME}


# The Tesla controls StarPilot uses: the right scroll wheel (its scroll is the comma speed with Match), the right stalk's
# presses while engaged and its hold, and the screen tap. The others steer, signal, light, wipe or shift the car, so a
# binding saved on them earlier no longer acts.
ACTIVE_INPUTS = frozenset(('right_wheel_up', 'right_wheel_down', 'right_wheel_left', 'right_wheel_right', STALK_TAP,
                           STALK_TAP_FULL, STALK_HOLD, 'screen_three_finger_tap'))


def effective_bindings(document):
  """The bindings that act: saved ones over the defaults, with the fixed ones always, explicit Unassigned removed, and only
  on the controls StarPilot uses."""
  bindings = {**DEFAULT_BINDINGS, **(document['bindings'] if document else {}), **FIXED_BINDINGS}
  return {name: binding for name, binding in bindings.items() if binding['key'] != NO_ACTION and name in ACTIVE_INPUTS}


def scroll_speed_directions(raw, fingerprint):
  config = parse_assignments(raw)
  if not config or config['vehicle'] != fingerprint:
    return ()
  return tuple(direction for name, direction in (('right_wheel_up', 1), ('right_wheel_down', -1))
               if config['bindings'].get(name, {}).get('key') in SPEED_ACTIONS)



def vehicle_supported(cp):
  from opendbc.car.tesla.values import TeslaFlags
  return bool(cp is not None and cp.carFingerprint in MODELS and cp.brand=='tesla' and
              cp.flags & TeslaFlags.HAS_VEHICLE_BUS and not cp.passive and not cp.notCar and not cp.dashcamOnly)

def screen_reserved(cp):
  from opendbc.car.tesla.values import TeslaFlags,TeslaSafetyFlags
  return bool(cp.flags & TeslaFlags.AOL_SCREEN_BUTTON or
              any(s.safetyParam & TeslaSafetyFlags.AOL_SCREEN_BUTTON for s in cp.safetyConfigs))


def match_speed_owner(params, cp):
  """Match the Tesla's set speed is on with SLC off: the comma still owns its set speed, so the right scroll wheel sets
  it (in the Short scroll / Fast scroll steps) and the Tesla is matched to it."""
  from openpilot.starpilot.saved_source import read_saved
  try:
    if (not vehicle_supported(cp) or not cp.pcmCruise or not cp.openpilotLongitudinalControl or
        read_saved(params, 'SafeMode', 8) not in ((None, True), (b'0', True)) or
        read_saved(params, 'SpeedLimitController', 8) == (b'1', True)):
      return False
    from openpilot.starpilot.ui.tesla_set_speed_feature import INDEPENDENT, SYNC, saved, sync_admitted, wheel_assigned
    return bool(saved(params, SYNC) and saved(params, INDEPENDENT) and sync_admitted(cp) and wheel_assigned(params, cp))
  except (AttributeError, TypeError, ValueError, OSError, ImportError):
    return False


def software_cruise_requested(params, cp):
  """Saved Tesla speed mappings request software ownership while SLC is on; with SLC off only Match the Tesla's set
  speed does (match_speed_owner)."""
  from openpilot.starpilot.saved_source import read_saved
  try:
    if not vehicle_supported(cp) or not cp.pcmCruise or not cp.openpilotLongitudinalControl:
      return False
    if read_saved(params, 'SafeMode', 8) not in ((None, True), (b'0', True)):
      return False
    if read_saved(params, 'SpeedLimitController', 8) != (b'1', True):
      return match_speed_owner(params, cp)
    raw, readable = read_saved(params, PARAM, MAX_ASSIGNMENT_BYTES)
    config = parse_assignments(raw) if readable else None
    return bool(config and config['vehicle'] == cp.carFingerprint and any(
      b['key'] in (SPEED_UP, SPEED_DOWN, SPEED_SET) for b in config['bindings'].values()))
  except (AttributeError, TypeError, ValueError, OSError):
    return False
