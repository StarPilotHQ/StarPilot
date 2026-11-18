"""Display-only copies of the selected controller's actual torque parameters."""
import math


def write_applied_tuning(parent, controller, host):
  tune = getattr(controller, 'torque_params', None)
  if tune is None:
    return
  factor, friction = float(tune.latAccelFactor), float(tune.friction)
  if not (math.isfinite(factor) and factor > 0 and math.isfinite(friction) and friction >= 0):
    return
  settings = getattr(host, 'settings', None)
  valid = settings is not None and settings.valid
  parent.torqueTuning = {
    'version': 1, 'factor': factor, 'friction': friction,
    'manualFactor': bool(valid and settings.user_factor is not None),
    'manualFriction': bool(valid and settings.user_friction is not None),
    'learned': (getattr(getattr(host, 'applied', None), 'source', None) == 'learned' if host is not None else
                getattr(controller, 'native_learning_source', None) is not None),
  }
