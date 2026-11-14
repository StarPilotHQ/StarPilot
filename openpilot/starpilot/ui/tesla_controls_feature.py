"""Vehicle input assignments shared by native settings and Galaxy."""
import json
from openpilot.starpilot.common.car_control_config import (PARAM, INPUTS, MODELS, SPEED_UP, SPEED_DOWN, SPEED_SET, SPEED_RESUME,
  STALK_HOLD, STALK_TAP, STALK_TAP_FULL, DEFAULT_BINDINGS, FIXED_BINDINGS, NO_ACTION, effective_bindings, parse_assignments,
  vehicle_supported, screen_reserved)
from openpilot.starpilot.favorites.actions import (BOOKMARK, CYCLE_PERSONALITY, EXPERIMENTAL, FORCE_COAST, PULSE_GLIDE, DISENGAGE, SELFIE,
                                                   SCREEN_OFF, TRAFFIC, SWITCHBACK, FEATURE_KEYS, APPEARANCE_KEYS)
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.ui.feature_settings_state import FeatureRow

PREFIX = 'tesla_input:'
SCREEN = 'screen_three_finger_tap'
# Always On Lateral on/off is only offered on the three-finger tap: the panda's own safety check watches that tap, and the
# choice is set up when the car starts (TeslaAOLScreenTap).
AOL_ACTION = 'Always On Lateral on/off'
AOL_KEY = 'TeslaAOLScreenTap'
AOL_HELP = ('Tap the Tesla screen with three fingers. Always On Lateral on/off turns steering on and off with the tap, while ' +
  'Always On Lateral is turned on. It is set up when the car starts, so choosing it or changing it applies after the next ' +
  'startup.')
AOL_PENDING = 'Always On Lateral on/off stays on this tap until the next startup.'
# A Tesla shows only the controls StarPilot can use: the rest steer, signal, light, wipe or shift the car. The right
# stalk's plain down and up engage and disengage, so they show grayed; its hold is fixed (FIXED_BINDINGS).
TESLA_INPUTS = ('right_wheel_left', 'right_wheel_right', STALK_TAP, STALK_TAP_FULL, STALK_HOLD, SCREEN)
STALK_INFO = (('Down', 'Engage StarPilot', "Push the right stalk down to engage StarPilot and the Tesla's cruise."),
  ('Up', 'Disengage StarPilot', "Push the right stalk up to disengage StarPilot and the Tesla's cruise."))
# What each control is, shown as its description.
INPUT_HELP = {
  'right_wheel_left': 'Tilt the right scroll wheel to the left.',
  'right_wheel_right': 'Tilt the right scroll wheel to the right.',
  STALK_TAP: ('Push the right stalk down to the first click while StarPilot is already engaged; the press that engages it ' +
    'does not count. Starts on Resume Speed Limit + Offset.'),
  STALK_TAP_FULL: ('Push the right stalk all the way down while StarPilot is already engaged; the press that engages it does ' +
    'not count.'),
  STALK_HOLD: ("Holding the right stalk down for about a second sets the comma's speed to its limit and offset. The Tesla " +
    'also sets its own speed to the limit it detects.'),
  SCREEN: 'Tap the Tesla screen with three fingers.',
}
SET_SPEED_HELP = 'The speed this control sets.'
OPTIONS = {'Unassigned': '', 'Increase Comma Set Speed': SPEED_UP, 'Decrease Comma Set Speed': SPEED_DOWN,
 'Set Speed To': SPEED_SET, 'Resume Speed Limit + Offset': SPEED_RESUME, 'Cycle Driving Personality': CYCLE_PERSONALITY,
 'Bookmark': BOOKMARK, 'Experimental Mode': EXPERIMENTAL, 'Force Coasting': FORCE_COAST, 'Pulse and Glide': PULSE_GLIDE,
 'Disengage Openpilot': DISENGAGE, 'Take Comma Selfie': SELFIE, 'Toggle Screen Off': SCREEN_OFF, 'Traffic Mode': TRAFFIC, 'Switchback Mode': SWITCHBACK}
for key in (*[k for keys in FEATURE_KEYS.values() for k in keys], *APPEARANCE_KEYS):
  if key not in OPTIONS.values():
    OPTIONS['Toggle ' + key] = key


class TeslaControlsFeature:
  def __init__(self, owner):
    self.owner = owner

  def context(self):
    cp = self.owner.vehicle_params()
    raw, readable = read_saved(self.owner.params, PARAM, 16384)
    doc = parse_assignments(raw) if readable else None
    valid = bool(doc is not None and (not doc['vehicle'] or cp is not None and doc['vehicle'] == cp.carFingerprint))
    return cp, raw, doc, valid

  def allowed(self):
    return self.owner.authority('parked_preferences') and self.owner.authority('preferences') and vehicle_supported(self.owner.vehicle_params())

  def aol_supported(self, cp):
    from opendbc.car.tesla.screen_button import screen_tap_supported
    return screen_tap_supported(cp)

  def rows(self):
    cp, raw, doc, valid = self.context()
    if cp is None or cp.carFingerprint not in MODELS:
      return []
    from openpilot.starpilot.ui.tesla_set_speed_feature import CABLE_MISSING, addon_cable
    cable = addon_cable(cp) is not False
    rows = []
    metric, ok = read_saved(self.owner.params, 'IsMetric', 8)
    units_valid = ok and metric in (None, b'0', b'1')
    metric_on = metric == b'1'
    bindings = effective_bindings(doc) if valid else {}
    for name in TESLA_INPUTS:
      group, label = INPUTS[name]
      if name == STALK_TAP:
        for control, value, info in STALK_INFO:
          rows.append(FeatureRow('', 'Right stalk / ' + control, value, reason=info if cable else CABLE_MISSING + '.\n' + info))
      reserved = name == SCREEN and screen_reserved(cp)
      aol = name == SCREEN and self.aol_supported(cp)
      aol_on = aol and read_saved(self.owner.params, AOL_KEY, 8) == (b'1', True)
      fixed = name in FIXED_BINDINGS
      binding = bindings.get(name, {})
      key = binding.get('key', '')
      choice = next((name for name, value in OPTIONS.items() if value == key), 'Unavailable saved action')
      if aol_on:
        choice = AOL_ACTION
      help_text = (AOL_PENDING + ' ' + AOL_HELP if reserved and not aol_on else AOL_HELP if aol else
                   INPUT_HELP.get(name, ''))
      if not cable:
        help_text = CABLE_MISSING + '.\n' + help_text
      rows.append(FeatureRow(PREFIX + name, group + ' / ' + label, choice if valid else 'Invalid or other-vehicle assignments', raw,
        tuple(OPTIONS) + ((AOL_ACTION,) if aol else ()), available=self.allowed() and valid and not fixed, reason=help_text,
        vehicle_fingerprint=self.owner.vehicle_fingerprint()))
      if key == SPEED_SET:
        value = binding.get('value')
        low, high = (8, 145) if metric_on else (5, 90)
        rows.append(FeatureRow(PREFIX + name + ':speed', group + ' / ' + label + ': set speed', str(value), raw, step=1., minimum=low, maximum=high,
          unit='km/h' if metric_on else 'mph', display_unit='km/h' if metric_on else 'mph',
          dependencies=(('IsMetric', metric),), available=self.allowed() and units_valid and not reserved, reason=SET_SPEED_HELP,
          vehicle_fingerprint=self.owner.vehicle_fingerprint()))
    return rows

  def apply(self, request):
    cp, raw, doc, valid = self.context()
    name = request.key.removeprefix(PREFIX)
    numeric = name.endswith(':speed')
    if numeric:
      name = name[:-6]
    if not self.allowed() or not valid or name not in TESLA_INPUTS or name in FIXED_BINDINGS or request.expected != raw:
      return False
    fingerprint = self.owner.vehicle_fingerprint()
    if not request.vehicle_fingerprint or request.vehicle_fingerprint != fingerprint:
      return False
    updated = {**doc, 'vehicle': str(cp.carFingerprint), 'bindings': dict(doc['bindings'])}
    if numeric:
      metric, ok = read_saved(self.owner.params, 'IsMetric', 8)
      unit = 'km/h' if metric == b'1' else 'mph'
      low, high = (8, 145) if metric == b'1' else (5, 90)
      binding = updated['bindings'].get(name)
      if not ok or metric not in (None, b'0', b'1') or request.display_unit != unit or request.dependencies != (('IsMetric', metric),):
        return False
      try:
        value = float(request.value)
      except (TypeError, ValueError):
        return False
      if not low <= value <= high or binding is None or binding['key'] != SPEED_SET:
        return False
      updated['bindings'][name] = {**binding, 'value': value}
    else:
      aol = name == SCREEN and request.value == AOL_ACTION
      if aol and not self.aol_supported(cp):
        return False
      if (request.value not in OPTIONS and not aol) or request.dependencies or request.display_unit:
        return False
      key = '' if aol else OPTIONS[request.value]
      if key:
        updated['bindings'][name] = {'key': key, 'value': 30 if key == SPEED_SET else None, 'label': request.value}
      elif name in DEFAULT_BINDINGS:
        updated['bindings'][name] = {'key': NO_ACTION, 'value': None, 'label': 'Unassigned'}
      else:
        updated['bindings'].pop(name, None)

    def authorized():
      return (self.allowed() and self.owner.vehicle_fingerprint() == fingerprint and
        (not numeric or read_saved(self.owner.params, 'IsMetric', 8) == (metric, True)))
    result = commit_exact(self.owner.params, key=PARAM, max_bytes=16384, expected=raw,
      raw=json.dumps(updated, separators=(',', ':'), allow_nan=False).encode(), authorized=authorized, temp_prefix='.tesla-input-')
    if not (result.committed and result.verified):
      return False
    if name == SCREEN and not numeric:
      # The tap's Always On Lateral on/off is its own saved switch, read when the car starts.
      current, readable = read_saved(self.owner.params, AOL_KEY, 8)
      wanted = b'1' if request.value == AOL_ACTION else b'0'
      if readable and current != wanted and (current == b'1' or wanted == b'1'):
        aol = commit_exact(self.owner.params, key=AOL_KEY, max_bytes=8, expected=current, raw=wanted, authorized=authorized,
          temp_prefix='.tesla-screen-')
        return aol.committed and aol.verified
    return True
