"""One parked Subaru curvature scalar shared by native settings and Galaxy."""
import hashlib
from opendbc.car.subaru.stock_aol import qualified
from openpilot.starpilot.car.subaru.smoothing import KEY, selected
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import SettingLevel, FeatureRow
from openpilot.starpilot.vehicle_selection import read_selection


class SubaruSmoothingFeature:
  def __init__(self, owner):
    self.owner = owner

  def capability(self):
    cp = self.owner.vehicle_params()
    selection = read_selection(self.owner.params)
    if not qualified(cp) or not selection.readable or not selection.valid:
      return None
    return (str(cp.carFingerprint), hashlib.sha256(repr(cp.to_dict()).encode('utf-8')).hexdigest(), selection.raw)

  def safe_dependency(self):
    safe = read_saved(self.owner.params, 'SafeMode', 8)
    return (('SafeMode', safe[0]),) if safe[1] and safe[0] in (None, b'0') else None

  def dependencies(self):
    developer = read_saved(self.owner.params, 'DeveloperUI', 8)
    safe = self.safe_dependency()
    return (('DeveloperUI', developer[0]), *safe) if developer == (b'1', True) and safe is not None else None

  def choice(self):
    raw, readable = read_saved(self.owner.params, KEY, 64)
    try:
      value = selected(raw) if readable else None
      valid = readable
    except ValueError:
      value, valid = None, False
    return raw, readable, valid, value

  def rows(self, parked):
    capability = self.capability()
    if capability is None:
      return ()
    raw, readable, valid, value = self.choice()
    cp = self.owner.vehicle_params()
    default = .4 if cp.carFingerprint == 'SUBARU_OUTBACK_2023' else 0.
    shown = format(default if value is None else value, '.3f').rstrip('0').rstrip('.') or '0'
    dependencies = self.dependencies()
    developer, dev_readable = read_saved(self.owner.params, 'DeveloperUI', 8)
    dev_valid = developer in (None, b'0', b'1')
    toggle = FeatureRow('DeveloperUI', 'Developer smoothing controls', ('On' if developer == b'1' else 'Off') if dev_valid else
                        'Invalid saved choice', developer, choices=('Off', 'On') if dev_valid else (),
                        available=bool(parked and dev_readable and self.safe_dependency() is not None and
                                       self.owner.authority('parked_preferences')), capability=capability,
                        dependencies=self.safe_dependency() or (), vehicle_fingerprint=self.owner.vehicle_fingerprint(),
                        repair_value='' if dev_valid else 'Off',
                        reason='Use an explicit saved Subaru smoothing value next drive. Off uses the vehicle default.', level=SettingLevel.ADVANCED)
    return (toggle, FeatureRow(KEY, 'Subaru model curvature smoothing', shown if valid else 'Invalid saved choice', raw,
                       step=.005 if valid else 0., minimum=0., maximum=2., unit='s',
                       available=bool(parked and readable and dependencies is not None and self.owner.authority('parked_preferences')),
                       capability=capability, dependencies=dependencies or (), vehicle_fingerprint=self.owner.vehicle_fingerprint(),
                       repair_value='' if valid else '0', presets=(('0', 'Off'), ('.4', '0.4 s')),
                       reason='Applies next drive. Maximum below 2 m/s, fades to zero at 8 m/s. Zero turns smoothing off.', level=SettingLevel.ADVANCED),)

  def apply(self, request):
    if request.key == 'DeveloperUI':
      return self.apply_developer(request)
    if (request.key != KEY or request.related_source is not None or request.direction or request.display_unit):
      return False
    try:
      value = selected(request.value)
    except ValueError:
      return False
    if value is None:
      return False

    def authorized():
      raw, readable, valid, _ = self.choice()
      capability = self.capability()
      dependencies = self.dependencies()
      return bool(self.owner.authority('parked_preferences') and request.vehicle_fingerprint and
                  self.owner.vehicle_fingerprint() == request.vehicle_fingerprint and capability is not None and
                  capability == request.capability and dependencies is not None and dependencies == request.dependencies and
                  readable and raw == request.expected and (valid or value == 0.))

    raw = (format(value, '.3f').rstrip('0').rstrip('.') or '0').encode('ascii')
    result = commit_exact(self.owner.params, key=KEY, max_bytes=64, raw=raw, expected=request.expected,
                          authorized=authorized, temp_prefix='.subaru-smoothing-')
    return result.committed and result.verified


  def apply_developer(self, request):
    if (not request.confirmation or request.value not in ('Off', 'On') or request.related_source is not None or
        request.direction or request.display_unit):
      return False

    def authorized():
      raw, readable = read_saved(self.owner.params, 'DeveloperUI', 8)
      capability = self.capability()
      safe = self.safe_dependency()
      return bool(self.owner.authority('parked_preferences') and request.vehicle_fingerprint and
                  self.owner.vehicle_fingerprint() == request.vehicle_fingerprint and capability is not None and
                  request.capability == capability and safe is not None and request.dependencies == safe and
                  readable and raw == request.expected and (raw in (None, b'0', b'1') or request.value == 'Off'))

    result = commit_exact(self.owner.params, key='DeveloperUI', max_bytes=8, expected=request.expected,
                          raw=b'1' if request.value == 'On' else b'0', authorized=authorized, temp_prefix='.subaru-smoothing-')
    return result.committed and result.verified
