"""Persistent configuration for automatic, resident City/Highway model switching."""
from openpilot.starpilot.models.catalog import BUNDLED_CURRENT, DEFAULT_SMALL
from openpilot.starpilot.models.conditional_policy import DEFAULT_THRESHOLD_MPH

CONDITIONS = ('city', 'highway')


def defaults() -> dict:
  return {'enabled': False, 'city': DEFAULT_SMALL, 'highway': DEFAULT_SMALL, 'thresholdMph': DEFAULT_THRESHOLD_MPH}


def validate(value: object, entries) -> bool:
  return (isinstance(value, dict) and set(value) == set(defaults()) and type(value['enabled']) is bool and
          type(value['thresholdMph']) is int and 10 <= value['thresholdMph'] <= 100 and
          all(isinstance(value[name], str) and value[name] != BUNDLED_CURRENT and value[name] in entries and
              not entries[value[name]].uses_external_gpu for name in CONDITIONS))


def configuration(saved: dict, entries) -> dict:
  if 'conditionalModel' in saved:
    value = saved['conditionalModel']
  else:
    # Compatibility boundary only. Obsolete manual intent and cooldown values
    # never enter the runtime; the next configuration save writes the new shape.
    legacy = saved.get('smallProfiles')
    allowed = {'enabled', 'highway', 'city', 'selected', 'automatic', 'thresholdMph', 'cityDelaySeconds'}
    if not isinstance(legacy, dict) or not {'enabled', 'city', 'highway'} <= set(legacy) or set(legacy) - allowed:
      return defaults()
    value = {name: legacy.get(name, default) for name, default in defaults().items()}
  if not validate(value, entries):
    return defaults()
  if value['enabled'] and saved.get('randomizer') is True:
    return value | {'enabled': False}
  return dict(value)


def starting_model(saved: dict) -> str:
  """Every normal Conditional Model startup uses City; recovery may override it."""
  return saved['conditionalModel']['city'] if saved['conditionalModel']['enabled'] else saved['small']
