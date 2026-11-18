"""Shared total-delay control for native settings and Galaxy."""
from openpilot.starpilot.lateral.delay_preferences import KEY, LIMIT, scope, read_delay, replace_delay
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.ui.feature_settings_state import FeatureRow


class DelayFeature:
  def __init__(self, owner):
    self.owner = owner

  def row(self):
    owner, cp = self.owner, self.owner.vehicle_params()
    try:
      capability = scope(cp)
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None
    selected = read_delay(owner.params, cp)
    value = selected.value if selected.value is not None else capability[-1]
    return FeatureRow('steering_delay', 'Actuator delay', f'{value:.2f}', selected.raw,
      step=.01, minimum=.01, maximum=1., unit='s', capability=capability,
      vehicle_fingerprint=capability[1],
      available=selected.valid and owner.authority('parked_preferences') and owner.authority('preferences'),
      dependencies=owner._dependents('TorqueOverrideDocument'),
      default_key='steering_delay_reset', default_value='Automatic',
      reason=('Vehicle/controller changed; saved delay is paused. Reset uses automatic delay.' if selected.source == 'changed' else
              'Saved delay is unreadable; no override applied.' if not selected.valid else
              'Manual actuator delay. Reset restores automatic delay.' if selected.value is not None else
              'Automatic delay. Shown value is the vehicle default.'))

  def apply(self, request):
    row = self.row()
    if (row is None or not row.available or request.key not in ('steering_delay', 'steering_delay_reset') or
        request.expected != row.source or request.capability != row.capability or request.dependencies != row.dependencies or
        request.vehicle_fingerprint != self.owner.vehicle_fingerprint() or request.related_source is not None):
      return False
    try:
      if request.key == 'steering_delay_reset':
        if request.value != 'Automatic':
          return False
        value = None
      else:
        value = float(request.value)
        if abs(value - row.capability[-1]) < 1e-8:
          value = None
      encoded = replace_delay(row.source, self.owner.vehicle_params(), value)
    except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
      return False

    def authorized():
      current = self.row()
      return (current is not None and current.available and current.capability == row.capability and
              current.dependencies == row.dependencies and self.owner.vehicle_fingerprint() == request.vehicle_fingerprint)

    return commit_exact(self.owner.params, key=KEY, max_bytes=LIMIT, raw=encoded, expected=row.source,
                        authorized=authorized, temp_prefix='.steering-delay-').verified
