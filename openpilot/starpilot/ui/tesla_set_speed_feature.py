"""Tesla Model 3/Y set speed: independent of the car's own set speed or capped by it, matched on the car, or followed.

Vehicle settings, shared by native settings and Galaxy. Also reports whether the Tesla CAN add-on cable (Vehicle CAN)
was detected when the car started, for the features that need it.
"""
import json

from openpilot.starpilot.common.car_control_config import MAX_ASSIGNMENT_BYTES, PARAM, SPEED_DOWN, SPEED_UP, parse_assignments
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureRow

INDEPENDENT, SYNC, OVERRIDE = 'TeslaIndependentSetSpeed', 'TeslaSpeedSync', 'TeslaOverrideSetSpeed'
KEYS = frozenset((INDEPENDENT, SYNC, OVERRIDE))
DEFAULTS = {INDEPENDENT: True, SYNC: False, OVERRIDE: True}
MODELS = ('TESLA_MODEL_3', 'TESLA_MODEL_Y')
CABLE_MISSING = 'Needs the Tesla CAN add-on cable, detected when the car starts'
# Match the Tesla's set speed needs these right scroll wheel actions: turning it on sets them, Wheel Controls locks them.
WHEEL = (('right_wheel_up', SPEED_UP, 'Increase Comma Set Speed'), ('right_wheel_down', SPEED_DOWN, 'Decrease Comma Set Speed'))
MATCH_BINDINGS = WHEEL
CABLE_LINKS = (('Buy the cable', 'https://xnor.shop/products/model-3-y-hw3-hw4-vehicle-can-addon-cable'),
               ('Install guide', 'https://wiki.xnor.shop/docs/tesla/hw3-hw4/vehicle-can-bus-addon/'))
CABLE_HELP = ('Connects the comma to the Tesla\'s Vehicle CAN through the diagnostic connector in the A-pillar. It lets ' +
              'StarPilot use the right scroll wheel, the right stalk and a three-finger tap on the Tesla screen as controls ' +
              'in Wheel Controls, including Always On Lateral on/off, and keep the Tesla\'s set speed matched to the ' +
              'comma\'s. For Model 3 and Model Y with HW3 or HW4. Checked when the car starts.')
EDITOR_BYTES = 16384  # the Wheel Controls editor's limit
LABELS = {
  INDEPENDENT: ('Independent set speed',
    'On: StarPilot keeps its own set speed, so Speed Limit Controller can go above the Tesla\'s set speed. ' +
    'Off: the Tesla\'s set speed is the maximum; StarPilot can go below it but never above it, so raise it on the ' +
    'Tesla to go faster. Used while Speed Limit Controller is on.'),
  SYNC: ('Match the Tesla\'s set speed',
    'On: StarPilot scrolls the Tesla\'s set speed to match the comma\'s, so the car shows the same speed. The comma ' +
    'speed is the master: if the Tesla changes its own set speed, it is scrolled back. While it is on, the right scroll ' +
    'wheel sets the comma\'s speed by the Short scroll and Fast scroll steps, with or without Speed Limit Controller. ' +
    'Off: the right scroll wheel is the Tesla\'s own. Experimental.'),
  OVERRIDE: ('Override Tesla set-speed changes',
    'On: when the Tesla\'s set speed changes without StarPilot asking, the comma keeps its own speed and scrolls the ' +
    'Tesla back to it. Off: the comma takes the Tesla\'s new speed as your own. A change within 1.5 seconds of the comma ' +
    'setting its own speed, such as the Tesla\'s reply to a stalk hold, is always overridden.'),
}


def addon_cable(cp) -> bool | None:
  """True/False for a Tesla Model 3/Y (cable detected at startup or not); None for other cars."""
  try:
    from opendbc.car.tesla.values import TeslaFlags
    if cp is None or cp.brand != 'tesla' or cp.carFingerprint not in MODELS:
      return None
    return bool(cp.flags & TeslaFlags.HAS_VEHICLE_BUS)
  except (AttributeError, TypeError, ImportError):
    return None


def sync_admitted(cp) -> bool:
  try:
    from opendbc.car.tesla.speed_sync import SYNC_FLAG
    return any(int(config.safetyParam) & SYNC_FLAG for config in cp.safetyConfigs)
  except (AttributeError, TypeError, ImportError):
    return False


def saved(params, key) -> bool:
  raw, readable = read_saved(params, key, 8)
  return DEFAULTS[key] if not readable or raw not in (b'0', b'1') else raw == b'1'


def capped(params, cp) -> bool:
  """Independent set speed Off on a Tesla Model 3/Y with StarPilot longitudinal: the car's set speed is the ceiling."""
  return bool(addon_cable(cp) is not None and cp.openpilotLongitudinalControl and cp.pcmCruise and not saved(params, INDEPENDENT))


def assignments(params, cp):
  """This car's Wheel Controls bindings, {} when none are saved, None when unreadable or saved for another car."""
  raw, readable = read_saved(params, PARAM, MAX_ASSIGNMENT_BYTES)
  document = parse_assignments(raw) if readable else None
  if document is None or cp is None or document['vehicle'] not in ('', cp.carFingerprint):
    return None
  return document['bindings']


def wheel_assigned(params, cp) -> bool:
  bindings = assignments(params, cp) or {}
  return all((bindings.get(name) or {}).get('key') == key for name, key, _ in WHEEL)


def match_locked(params, cp) -> frozenset:
  """The Wheel Controls inputs Match the Tesla's set speed holds fixed: the right wheel's scroll while it is on comma
  speed (Match needs it)."""
  if not (addon_cable(cp) and cp.openpilotLongitudinalControl and saved(params, SYNC)):
    return frozenset()
  return frozenset(name for name, _, _ in WHEEL) if wheel_assigned(params, cp) else frozenset()


class TeslaSetSpeedFeature:
  def __init__(self, owner):
    self.owner = owner

  def eligible(self):
    cp = self.owner.vehicle_params()
    return cp if addon_cable(cp) is not None else None

  def allowed(self):
    return self.owner.authority('parked_preferences')

  def grayed(self, key, cp):
    if key == SYNC:
      if not addon_cable(cp):
        return CABLE_MISSING
      if not sync_admitted(cp):
        return 'Not available on this software build or car'
      if not saved(self.owner.params, INDEPENDENT):
        return 'Turn on Independent set speed first'
      if assignments(self.owner.params, cp) is None:
        return 'Wheel Controls has unreadable assignments or ones saved for another car'
    if key == OVERRIDE:
      # A sub-setting of Match the Tesla's set speed: grayed whenever Match is off or unavailable.
      match = self.grayed(SYNC, cp)
      if match:
        return match
      if not saved(self.owner.params, SYNC):
        return "Turn on Match the Tesla's set speed first"
    return ''

  def rows(self):
    cp = self.eligible()
    if cp is None:
      return ()
    cable = addon_cable(cp)
    rows = [FeatureRow('', 'Tesla CAN add-on cable', 'Detected' if cable else 'Not detected', reason=CABLE_HELP,
                       status=True, links=CABLE_LINKS)]
    if not cp.openpilotLongitudinalControl:
      return tuple(rows)
    for key in (INDEPENDENT, SYNC, OVERRIDE):
      label, help_text = LABELS[key]
      raw, readable = read_saved(self.owner.params, key, 8)
      valid = readable and raw in (None, b'0', b'1')
      value = ('On' if saved(self.owner.params, key) else 'Off') if valid else 'Invalid saved choice'
      grayed = self.grayed(key, cp) if valid else ''
      if key == SYNC and valid and not grayed and saved(self.owner.params, SYNC) and not wheel_assigned(self.owner.params, cp):
        help_text = ('Paused: the right scroll wheel is no longer on comma speed. Turn this off and on again to set it.\n' +
                     help_text)
      row = FeatureRow(key, label, value, source=raw, choices=('Off', 'On') if valid else (),
                       default_value='On' if DEFAULTS[key] else 'Off',
                       available=self.allowed() and valid and not grayed,
                       reason=f'{grayed}.\n{help_text}' if grayed else help_text,
                       vehicle_fingerprint=self.owner.vehicle_fingerprint(),
                       repair_value='' if valid else ('On' if DEFAULTS[key] else 'Off'),
                       default_reset_available=bool(grayed) and self.allowed())
      rows.append(row)
    return tuple(rows)

  def apply(self, request, *, reset_default=False):
    cp = self.eligible()
    if cp is None or request.key not in KEYS or request.value not in ('Off', 'On') or request.related_source is not None:
      return False

    def authorized():
      return (self.allowed() and bool(request.vehicle_fingerprint) and
              request.vehicle_fingerprint == self.owner.vehicle_fingerprint())
    if not authorized():
      return False
    default = 'On' if DEFAULTS[request.key] else 'Off'
    if self.grayed(request.key, cp) and not (reset_default or request.value == default):
      return False
    if request.key == SYNC and request.value == 'On' and not self.assign_controls(cp, authorized):
      return False
    if request.key == SYNC and request.value == 'Off' and not self.release_wheel(cp, authorized):
      return False
    result = commit_exact(self.owner.params, key=request.key, max_bytes=8, expected=request.expected,
                          raw=b'1' if request.value == 'On' else b'0', authorized=authorized, temp_prefix='.tesla-set-speed-')
    return result.committed and result.verified

  def release_wheel(self, cp, authorized):
    """Turning Match off gives the right scroll wheel back to the Tesla: its comma speed bindings are removed."""
    raw, readable = read_saved(self.owner.params, PARAM, EDITOR_BYTES)
    document = parse_assignments(raw) if readable else None
    if document is None or document['vehicle'] not in ('', cp.carFingerprint):
      return True  # nothing of ours to remove
    bindings = dict(document['bindings'])
    for name, key, _ in WHEEL:
      if (bindings.get(name) or {}).get('key') == key:
        bindings.pop(name)
    if bindings == document['bindings']:
      return True
    updated = {'version': 1, 'vehicle': str(cp.carFingerprint), 'bindings': bindings}

    def allowed():
      return authorized() and self.owner.authority('preferences')
    result = commit_exact(self.owner.params, key=PARAM, max_bytes=EDITOR_BYTES, expected=raw,
                          raw=json.dumps(updated, separators=(',', ':'), allow_nan=False).encode(), authorized=allowed,
                          temp_prefix='.tesla-input-')
    return result.committed and result.verified

  def assign_controls(self, cp, authorized):
    """Turning Match on puts the right scroll wheel on comma speed; match_locked() then keeps it there."""
    raw, readable = read_saved(self.owner.params, PARAM, EDITOR_BYTES)
    document = parse_assignments(raw) if readable else None
    if document is None or document['vehicle'] not in ('', cp.carFingerprint):
      return False
    wanted = {name: {'key': key, 'value': None, 'label': label} for name, key, label in MATCH_BINDINGS}
    if all((document['bindings'].get(name) or {}).get('key') == binding['key'] for name, binding in wanted.items()):
      return True
    updated = {'version': 1, 'vehicle': str(cp.carFingerprint), 'bindings': {**document['bindings'], **wanted}}

    def allowed():
      return authorized() and self.owner.authority('preferences')
    result = commit_exact(self.owner.params, key=PARAM, max_bytes=EDITOR_BYTES, expected=raw,
                          raw=json.dumps(updated, separators=(',', ':'), allow_nan=False).encode(), authorized=allowed,
                          temp_prefix='.tesla-input-')
    return result.committed and result.verified
