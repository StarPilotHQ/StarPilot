import hashlib

from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.vehicle_selection import read_selection


STARTUP_KEYS = ('SubaruAvhStartup', 'SubaruStopStartOff', 'AlphaLongitudinalEnabled', 'IsReleaseBranch', 'OpenpilotEnabledToggle', 'SafeMode',
                'DisableOpenpilotLongitudinal', 'ToyotaAutoHold', 'JeepBrakeHold',
                'VoltSNG', 'GMAutoHold', 'VoltOnePedalMode', 'TurnAssist', 'LongPitch',
                'HondaBoschARadar', 'NAPPedalEnabled', 'NAPRadarEnabled', 'NAPRadarBehindNosecone')


def _editor_cp(cp):
  if cp is None or not getattr(cp, 'passive', False):
    return cp
  from opendbc.car.structs import car
  copied = car.CarParams(**cp.to_dict())
  copied.passive = False
  return copied.as_reader()


def configuration_context(params, physical_cp, physical_raw):
  selection = read_selection(params)
  if not selection.readable or not selection.valid:
    return None, None
  if selection.platform is None:
    return _editor_cp(physical_cp), physical_raw
  saved = tuple((key, *read_saved(params, key, 512)) for key in STARTUP_KEYS)
  revision = hashlib.sha256(repr((selection.raw, saved)).encode()).digest()
  if physical_cp is not None and physical_cp.carFingerprint == selection.platform:
    return _editor_cp(physical_cp), b'vehicle-configuration:' + revision + (physical_raw or b'')
  cp, raw = _factory_context(params, selection.platform, saved)
  if read_selection(params) != selection or _startup_snapshot(params) != saved:
    return None, None
  return (cp, b'vehicle-configuration:' + revision + raw) if cp is not None else (None, None)


def _startup_snapshot(params):
  return tuple((key, *read_saved(params, key, 512)) for key in STARTUP_KEYS)


def _factory_context(params, platform, saved):
  try:
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.car_helpers import interfaces
    from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences
    from openpilot.cereal import messaging
    from opendbc.car.structs import car
    values = {key: (raw, readable) for key, raw, readable in saved}
    release = values['IsReleaseBranch'] == (b'1', True)
    alpha = not release and values['AlphaLongitudinalEnabled'] == (b'1', True)
    cp = interfaces[platform].get_params(platform, gen_empty_fingerprint(), [], alpha, release, False)
    VehicleStartupPreferences.read(params, enabled=values['OpenpilotEnabledToggle'] == (b'1', True)).prepare(cp)
    raw = cp.to_bytes()
    reader = messaging.log_from_bytes(raw, car.CarParams)
    return reader, raw
  except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError, OverflowError):
    return None, None


def _saved_torque_identities(params):
  from openpilot.starpilot.lateral import controller_selection, torque_settings
  controller_raw, controller_readable = read_saved(params, controller_selection.DOCUMENT_KEY,
                                                   controller_selection.MAX_DOCUMENT_BYTES)
  torque_raw, torque_readable = read_saved(params, torque_settings.DOCUMENT_KEY, torque_settings.MAX_DOCUMENT_BYTES)
  if not controller_readable or not torque_readable or controller_raw is None or torque_raw is None:
    raise ValueError('Saved torque profiles unavailable')
  controller = controller_selection.parse_document(controller_raw)
  torque = torque_settings.parse_document(torque_raw)
  controller_ids = set(controller['vehicles'])
  torque_ids = set(torque)
  identities = controller_ids | torque_ids
  if len(identities) != 1 or controller_ids != torque_ids:
    raise ValueError('Saved torque vehicle is ambiguous')
  return tuple(sorted(identities))


def saved_torque_context(params):
  selection = read_selection(params)
  if not selection.readable or not selection.valid or selection.platform is not None:
    return None, None
  try:
    identities = _saved_torque_identities(params)
    saved = _startup_snapshot(params)
    cp, raw = _factory_context(params, identities[0], saved)
    if cp is None:
      return None, None
    if read_selection(params) != selection or _startup_snapshot(params) != saved or _saved_torque_identities(params) != identities:
      return None, None
    revision = hashlib.sha256(repr((selection.raw, saved, identities)).encode()).digest()
    return cp, b'saved-torque-configuration:' + revision + raw
  except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError, OverflowError):
    return None, None


def settings_configuration(params, physical_cp, physical_raw):
  saved = _startup_snapshot(params)
  cp, token = configuration_context(params, physical_cp, physical_raw)
  editing_saved = False
  if cp is None:
    cp, token = saved_torque_context(params)
    editing_saved = cp is not None
  if cp is None or _startup_snapshot(params) != saved:
    return None, token, False, False
  from openpilot.starpilot.car.hyundai.settings import supports_long_configuration
  configurable_long = bool(supports_long_configuration(cp))
  revision = hashlib.sha256(repr(saved).encode()).digest()
  return cp, b'settings-configuration:' + revision + (token or b''), editing_saved, configurable_long
