"""SLC settings shared by native and Galaxy, with exact saved-value writes.

Offsets: a type (fixed bands or a percentage). Limit changes: higher and lower limits each have a mode choosing when
the speed change starts, the one setting that mode uses (the others are left out until their mode is picked) and a
warning lead time.
"""

from openpilot.starpilot.speed_limits.custom_settings import SPECS, BOOLS, MODES
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.ui.feature_settings_state import FeatureRow
from dataclasses import replace
import math

OVER = 'SLCHigherLimitOver'  # saved in m/s, shown in the comma's speed units
HIGHER_MODE, LOWER_MODE, OFFSET_TYPE = 'SLCHigherLimitMode', 'SLCLowerLimitMode', 'SLCOffsetType'
MEMORY, FALLBACK, RESTORE, KEEP_FASTER = 'SLCManualSpeedMemory', 'SLCFallbackDelay', 'SLCReengageSpeed', 'SLCReengageKeepFaster'
MODE_LABELS = {
  MEMORY: ('Forget', 'Remember this drive'),
  OFFSET_TYPE: ('Fixed', 'Percentage'),
  HIGHER_MODE: ('At the limit', 'Distance', 'Time', 'Speed target at new limit'),
  LOWER_MODE: ('At the limit', 'Distance', 'Time', 'Reach by'),
}
# The setting each choice uses; choices without one (At the limit, Fixed) have none here.
MODE_OFFSETS = {
  OFFSET_TYPE: {'percentage': 'SLCOffsetPercent'},
  HIGHER_MODE: {'distance': 'SLCHigherLimitDistance', 'time': 'SLCHigherLimitTime', 'speed': OVER},
  LOWER_MODE: {'distance': 'SLCLowerLimitDistance', 'time': 'SLCLowerLimitTime', 'reach': 'SLCLowerLimitTiming'},
}
OFFSETS = (OFFSET_TYPE, 'SLCOffsetPercent')
HIGHER = (HIGHER_MODE, 'SLCHigherLimitDistance', 'SLCHigherLimitTime', OVER, 'SLCHigherWarningLeadTime')
LOWER = (LOWER_MODE, 'SLCLowerLimitDistance', 'SLCLowerLimitTime', 'SLCLowerLimitTiming', 'SLCLowerWarningLeadTime')
SETS_SET_SPEED = 'SLCSetsSetSpeed'
MANUAL = (MEMORY, 'SLCPedalSetsSpeed', RESTORE, KEEP_FASTER)  # need SLC to set the set speed
SET_SPEED_OFF = 'Turn on SLC sets the set speed first'
ADOPTS_EARLY = 'Not used while an Adopt limits early time is set for these limits, because that time is used instead'
LIMIT_SWITCHES = ('SLCLimitAcceleration', 'SLCLimitBraking')  # the limits themselves are on each personality's pages
ORDER = (*OFFSETS, FALLBACK, *HIGHER, *LOWER, *LIMIT_SWITCHES, SETS_SET_SPEED, *MANUAL)
# A warning lead is unused while Confirm limits asks for that direction (SLCConfirmation: 1 Both, 3 Higher only, 4 Lower only).
LEADS = {'SLCHigherWarningLeadTime': (b'1', b'3'), 'SLCLowerWarningLeadTime': (b'1', b'4')}
CONFIRM_ON = 'Not used while Confirm limits asks you about these limits, because you are asked instead'
FRACTIONAL = frozenset(
  ('SLCHigherLimitTime', 'SLCHigherWarningLeadTime', 'SLCLowerLimitTime', 'SLCLowerLimitTiming', 'SLCLowerWarningLeadTime')
)  # half-second steps; everything else saves whole numbers
KEYS = frozenset(SPECS) | frozenset(BOOLS) | frozenset(MODES)
# Limit sources: what each source is (shared by the comma's rows and Galaxy's descriptions).
SOURCE_HELP = {
  'SLCPriority1': ('The source StarPilot checks first. Dashboard: the speed limit your car shows on its own display, on cars ' +
                   "that share it. Vision: the comma's camera reads speed limit signs as you pass them, so it only knows a limit " +
                   'once it has seen a sign. Maps: offline map data and GPS, which know the limit before you reach the sign; the ' +
                   'start choices in Limit changes need this. Download maps in Maps, Offline Maps.'),
  'SLCPriority2': ('Used when the primary source has no speed limit, for example on a road without map data or before the camera ' +
                   'has seen a sign. If it shows a different limit from the one in use, StarPilot waits for the fallback delay ' +
                   'before switching, in case the primary comes back. Off: only the primary source is used.'),
}
MAPS = 'Needs a speed limit from Maps, which knows where the next sign is; other sources change as the car passes the sign.'
LABELS = {
  'SLCLimitAcceleration': (
    'Use SLC acceleration limits',
    '',
    '',
    'On: when Speed Limit Controller raises the set speed, the car speeds up no harder than the SLC acceleration limit '
    + "set on each driving personality's page. Off: the normal acceleration applies.",
  ),
  'SLCLimitBraking': (
    'Use SLC braking limits',
    '',
    '',
    'On: when Speed Limit Controller lowers the set speed, the car slows down no harder than the SLC braking limit '
    + "set on each driving personality's page. Braking for cars, curves and stops is never limited. Off: the normal braking applies.",
  ),
  OFFSET_TYPE: (
    'Offset type',
    '',
    '',
    'How the offset is set. Fixed: a set speed above or below the limit, chosen for each speed range. ' +
    'Percentage: one percentage of the limit, used at every speed.',
  ),
  'SLCOffsetPercent': (
    'Percentage offset',
    '%',
    1.0,
    'How far above (+) or below (−) the speed limit to drive, as a percentage of the limit. At +10%, a 30 limit sets 33 and a 60 limit sets 66.',
  ),
  HIGHER_MODE: (
    'Higher-limit start',
    '',
    '',
    'When to start speeding up for a higher limit. At the limit: as you pass the sign. Distance: a set distance before the sign. ' +
    'Time: a set number of seconds before the sign. Speed target at new limit: early enough to be at a set speed as you pass ' +
    'the sign. ' + MAPS,
  ),
  'SLCHigherLimitDistance': ('Higher-limit distance', 'm', 10.0, 'Start speeding up this far before the sign.'),
  'SLCHigherLimitTime': ('Higher-limit time', 's', 0.5, 'Start speeding up this many seconds before you reach the sign, at your current speed.'),
  OVER: (
    'Higher-limit speed at the sign',
    '',
    1.0,
    'The speed to be at as you pass the sign, as an amount over the old limit. From 30 to 40 at 5, the car reaches 35 ' +
    'at the sign, then carries on to 40. StarPilot works out where to start from your acceleration. 0 starts at the sign.',
  ),
  'SLCHigherWarningLeadTime': (
    'Higher-limit warning lead time',
    's',
    0.5,
    'Warn with a chime this many seconds before speeding up starts. 0 warns as it starts.',
  ),
  LOWER_MODE: (
    'Lower-limit start',
    '',
    '',
    'When to start slowing for a lower limit. At the limit: as you pass the sign, so you slow down after it. Distance: a set ' +
    'distance before the sign. Time: a set number of seconds before the sign. Reach by: StarPilot works out where to start ' +
    'from your braking so you are down to the new limit by the sign. ' + MAPS,
  ),
  'SLCLowerLimitDistance': ('Lower-limit distance', 'm', 10.0, 'Start slowing this far before the sign.'),
  'SLCLowerLimitTime': ('Lower-limit time', 's', 0.5, 'Start slowing this many seconds before you reach the sign, at your current speed.'),
  'SLCLowerLimitTiming': (
    'Lower-limit reach by',
    's',
    0.5,
    'When to be down to the new limit. 0: as you pass the sign. Above 0: that many seconds before the sign. ' +
    'Below 0: that many seconds after it, for gentler slowing.',
  ),
  'SLCLowerWarningLeadTime': ('Lower-limit warning lead time', 's', 0.5, 'Warn with a chime this many seconds before slowing starts. 0 warns as it starts.'),
  FALLBACK: (
    'Fallback delay',
    's',
    1.0,
    'When the primary source loses its speed limit, keep using the last limit for this long before switching to the ' +
    'secondary source. If the primary comes back in time, nothing changes. 0 switches straight away.',
  ),
  MEMORY: (
    'Manual speed memory',
    '',
    '',
    'What happens to a speed you set yourself when the limit changes. Forget: each new limit sets the limit with your ' +
    'offset. Remember this drive: StarPilot remembers your speed for each posted limit until the drive ends. Example: in ' +
    'a 30 zone you set 33, drive through a 40 zone, then come back to a 30 zone. Forget sets 30; Remember this drive sets 33.',
  ),
  SETS_SET_SPEED: (
    'SLC sets the set speed',
    '',
    '',
    'Off: the speed limit with your offset is a maximum under your own set speed, and your set speed never changes. ' +
    'On: the speed limit with your offset becomes your set speed, so the set speed shown follows each new limit and the ' +
    'speed buttons change it from there. Needed for the settings below. Applies after the next startup.',
  ),
  'SLCPedalSetsSpeed': (
    'Set new speed with pedal override',
    '',
    '',
    'On: when you speed up past the set speed with the accelerator, the speed you let go at becomes the new set speed. ' +
    'The brake works the same way on cars where braking does not disengage.',
  ),
  RESTORE: (
    'Restore SLC speed on re-engagement',
    '',
    '',
    'On: when you engage, the set speed goes to the speed limit with your offset, or to your remembered speed for that ' +
    'limit (with Remember this drive). Off: engaging keeps the speed cruise starts at.',
  ),
  KEEP_FASTER: (
    'Engaging faster keeps current speed',
    '',
    '',
    'On: if you engage faster than the limit target, your current speed becomes the set speed. Off: the set speed goes ' +
    'to the limit target, even if that means slowing down.',
  ),
}


class SlcCustomFeature:
  def __init__(self, owner):
    self.owner = owner

  def authorized(self):
    return self.owner.authority('slc') and self.owner.longitudinal_available()

  def rows(self, allowed):
    rows = []
    for key in (*ORDER, *(k for k in (*SPECS, *BOOLS) if k not in ORDER)):
      if key not in KEYS or not self.shown(key):
        continue
      raw, ok = read_saved(self.owner.params, key, 32)
      label, unit, step, help_text = LABELS[key]
      if key == OVER:
        rows.append(self._over_row(allowed, raw, ok, label, help_text))
      elif key in MODES:
        rows.append(self._mode_row(key, allowed, raw, ok, label, help_text))
      else:
        rows.append(self._row(key, allowed, raw, ok, label, unit, step, help_text))
      grayed = self.grayed(key)
      if grayed and rows[-1].available:
        # Grayed while confirmation asks instead; Restore defaults can still reset it.
        rows[-1] = replace(rows[-1], available=False, reason=f'{grayed}. {help_text}', default_reset_available=True)
    return rows

  def mode(self, key):
    """The saved mode's name, or the default when the saved value is missing or unreadable."""
    names, default = MODES[key]
    raw, ok = read_saved(self.owner.params, key, 8)
    try:
      index = int(raw) if ok and raw is not None else default
    except ValueError:
      index = default
    return names[index] if 0 <= index < len(names) else names[default]

  def shown(self, key):
    """A mode's setting shows only while that mode is picked."""
    for mode_key, offsets in MODE_OFFSETS.items():
      if key in offsets.values():
        return offsets.get(self.mode(mode_key)) == key
    return True

  def adopts_early(self, key):
    """Whether this start-mode row's direction has an Adopt limits early time, which is then used instead."""
    advance = 'SLCAdvanceHigherSeconds' if key in HIGHER[:4] else 'SLCAdvanceLowerSeconds' if key in LOWER[:4] else None
    if advance is None or self.sets_set_speed():  # the timer is not offered where SLC sets the set speed
      return False
    raw, ok = read_saved(self.owner.params, advance, 2)
    return bool(ok and raw is not None and raw.isdigit() and 1 <= int(raw) <= 10)

  def sets_set_speed(self):
    """SLC sets the set speed on this car (cruise.slc_sets_set_speed): the saved choice, where StarPilot owns the set speed."""
    from openpilot.selfdrive.car.cruise import slc_sets_set_speed

    cp = self.owner.vehicle_params()
    try:
      return cp is not None and slc_sets_set_speed(cp, self._bool(SETS_SET_SPEED))
    except (AttributeError, TypeError):
      return False

  def grayed(self, key):
    if key in MANUAL and not self.sets_set_speed():
      return SET_SPEED_OFF
    if self.adopts_early(key):
      return ADOPTS_EARLY
    confirm = LEADS.get(key)
    if confirm is not None and read_saved(self.owner.params, 'SLCConfirmation', 1)[0] in confirm:
      return CONFIRM_ON
    if key == KEEP_FASTER and not self._bool(RESTORE):
      return f'Turn on {LABELS[RESTORE][0]} first'
    if key == FALLBACK:
      primary, secondary = (self._source(name) for name in ('SLCPriority1', 'SLCPriority2'))
      if secondary in (None, 'None', 'Off') or secondary == primary:
        return 'Not used without a different secondary source'
    return ''

  def _source(self, key):
    raw, ok = read_saved(self.owner.params, key, 32)
    if not ok or raw is None:
      return {'SLCPriority1': 'Vision', 'SLCPriority2': 'Maps'}[key]  # the saved defaults
    value = raw.decode('utf-8', 'replace')
    return 'Maps' if value == 'Map Data' else value

  def _bool(self, key):
    raw, ok = read_saved(self.owner.params, key, 8)
    return ok and raw == b'1'

  def units(self):
    """The comma's speed units for the speed offset: (IsMetric raw, valid, unit, m/s factor, maximum)."""
    raw, ok = read_saved(self.owner.params, 'IsMetric', 8)
    metric = raw == b'1'
    return raw, ok and raw in (None, b'0', b'1'), 'km/h' if metric else 'mph', 3.6 if metric else 1 / 0.44704, 25 if metric else 15

  def _over_row(self, allowed, raw, ok, label, help_text):
    unit_raw, unit_ok, unit, factor, maximum = self.units()
    low, high, default = SPECS[OVER]
    try:
      numeric = float(raw) if raw is not None else default
      if not ok or not math.isfinite(numeric) or not low <= numeric <= high:
        raise ValueError()
      value, valid = str(round(numeric * factor)), True
    except (ValueError, OverflowError):
      value, valid = 'Invalid saved value', False
    return FeatureRow(
      OVER,
      label,
      value,
      raw,
      (),
      step=1.0 if valid else 0.0,
      minimum=0.0,
      maximum=float(maximum),
      unit=unit,
      available=allowed and valid and unit_ok,
      reason=help_text,
      vehicle_fingerprint=self.owner.vehicle_fingerprint(),
      default_value=str(round(default * factor)),
      dependencies=(('IsMetric', unit_raw),),
      display_unit=unit,
    )

  def _mode_row(self, key, allowed, raw, ok, label, help_text):
    names, default = MODES[key]
    choices = MODE_LABELS[key]
    try:
      index = int(raw) if raw is not None else default
      if not ok or not 0 <= index < len(names):
        raise ValueError()
      value, valid = choices[index], True
    except (ValueError, OverflowError):
      value, valid = 'Invalid saved value', False
    return FeatureRow(
      key,
      label,
      value,
      raw,
      choices if valid else (choices[default],),
      available=allowed and ok,
      reason=help_text,
      vehicle_fingerprint=self.owner.vehicle_fingerprint(),
      default_value=choices[default],
      repair_value=choices[default] if not valid and ok else '',
    )

  def _row(self, key, allowed, raw, ok, label, unit, step, help_text):
    boolean = key not in SPECS
    valid = ok
    try:
      if boolean:
        if raw not in (None, b'0', b'1'):
          raise ValueError()
        value = 'On' if raw == b'1' else 'Off'
      else:
        low, high, default = SPECS[key]
        numeric = float(raw) if raw is not None else default
        if not math.isfinite(numeric) or not low <= numeric <= high:
          raise ValueError()
        value = f'{numeric:g}'
    except (ValueError, OverflowError):
      valid = False
      value = 'Invalid saved value'
    low, high, default = SPECS.get(key, (0, 0, 0))
    lead = key in LEADS
    return FeatureRow(
      key,
      label,
      value,
      raw,
      ('Off', 'On') if boolean else (),
      step=0.0 if boolean else step,
      minimum=low,
      maximum=high,
      unit=unit,
      available=allowed and (valid or (lead and ok)),
      reason=help_text,
      vehicle_fingerprint=self.owner.vehicle_fingerprint(),
      default_value='Off' if boolean else f'{default:g}',
      repair_value=f'{default:g}' if lead and not valid and ok else '',
    )

  def apply(self, request, *, reset_default=False):
    if request.key not in KEYS or request.related_source is not None:
      return False
    if request.key != OVER and (request.dependencies or request.display_unit):
      return False

    def allowed():
      return self.authorized() and bool(request.vehicle_fingerprint) and request.vehicle_fingerprint == self.owner.vehicle_fingerprint()

    if not allowed():
      return False
    # A hidden offset (its mode is not picked) or a grayed lead time only takes its default.
    blocked = not self.shown(request.key) or bool(self.grayed(request.key))
    if request.key == OVER:
      unit_raw, unit_ok, unit, factor, maximum = self.units()
      if not unit_ok or request.dependencies != (('IsMetric', unit_raw),) or request.display_unit != unit:
        return False
      try:
        value = float(request.value)
      except (ValueError, TypeError):
        return False
      default = SPECS[OVER][2]
      if not math.isfinite(value) or not value.is_integer() or not 0 <= value <= maximum:
        return False
      if blocked and value != round(default * factor):
        return False
      mps = default if value == round(default * factor) else value / factor
      raw = f'{mps:.6g}'.encode()
    elif request.key in MODES:
      choices = MODE_LABELS[request.key]
      if request.value not in choices:
        return False
      raw = str(choices.index(request.value)).encode()
    elif request.key in SPECS:
      low, high, default = SPECS[request.key]
      try:
        value = float(request.value)
      except (ValueError, TypeError):
        return False
      if not math.isfinite(value) or not low <= value <= high:
        return False
      if request.key in FRACTIONAL:
        if (value * 2) % 1:
          return False
      elif not value.is_integer():
        return False
      if blocked and value != default:
        return False
      raw = f'{value:g}'.encode()
    else:
      if request.value not in ('On', 'Off'):
        return False
      if blocked and request.value != 'Off':  # a grayed switch may only go back to its default (Off)
        return False
      raw = b'1' if request.value == 'On' else b'0'
    result = commit_exact(self.owner.params, key=request.key, max_bytes=32, expected=request.expected, raw=raw, authorized=allowed, temp_prefix='.slc-custom-')
    return result.committed and result.verified
