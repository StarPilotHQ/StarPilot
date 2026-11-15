from dataclasses import dataclass
from pathlib import Path

from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.vehicle_selection import read_selection


KEYS = ('RemoteStartBootsComma', 'HKGRemoteStartBootsComma', 'IgnoreIgnitionLine', 'TeslaWakeOnCAN')


def supported_keys(cp):
  from opendbc.car.gm.values import CAR as GM
  from opendbc.car.hyundai.values import CAR as HKG, HyundaiFlags
  from opendbc.car.tesla.values import CAR as TESLA
  try:
    if cp is None or cp.notCar:
      return ()
    if cp.brand == 'gm' and cp.carFingerprint in tuple(GM):
      return ('RemoteStartBootsComma', 'IgnoreIgnitionLine')
    if (cp.brand == 'hyundai' and cp.carFingerprint in tuple(HKG) and cp.flags & HyundaiFlags.CANFD):
      return ('HKGRemoteStartBootsComma',)
    if cp.brand == 'tesla' and cp.carFingerprint in (TESLA.TESLA_MODEL_3, TESLA.TESLA_MODEL_Y, TESLA.TESLA_MODEL_X):
      return ('TeslaWakeOnCAN',)
  except (AttributeError, TypeError, ValueError):
    pass
  return ()


def snapshot(params):
  return tuple((key, *read_saved(params, key, 8)) for key in KEYS)


@dataclass(frozen=True)
class FirmwareChoice:
  gm: bool = False
  hkg: bool = False
  can_only: bool = False
  tesla: bool = False

  @property
  def alternate(self):
    return self.gm or self.hkg or self.can_only or self.tesla

  def filename(self, app_fn):
    if not self.alternate:
      return app_fn
    if app_fn not in ('panda.bin.signed', 'panda_h7.bin.signed'):
      raise ValueError('Unsupported Panda for vehicle wake firmware')
    parts = ['panda_h7' if app_fn == 'panda_h7.bin.signed' else 'panda']
    if self.tesla:
      parts.extend(('tesla', 'wake'))
    elif self.hkg:
      parts.extend(('hkg', 'remote'))
    elif self.gm:
      parts.append('remote')
    if self.can_only:
      parts.append('can_ignition_only')
    return '_'.join(parts) + '.bin.signed'


def resolve(cp, sources):
  supported = supported_keys(cp)
  enabled = {key for key, raw, readable in sources if readable and raw == b'1' and key in supported}
  return FirmwareChoice('RemoteStartBootsComma' in enabled, 'HKGRemoteStartBootsComma' in enabled,
                        'IgnoreIgnitionLine' in enabled, 'TeslaWakeOnCAN' in enabled)


def startup_choice(params):
  from openpilot.starpilot.schema_cache import inspect_cache
  from openpilot.cereal import messaging
  from opendbc.car.structs import car
  selection = read_selection(params)
  sources = snapshot(params)
  if any(not readable or raw not in (None, b'0', b'1') for _, raw, readable in sources):
    return FirmwareChoice()
  raw, readable = read_saved(params, 'CarParamsPersistent', 1_048_576)
  if not selection.readable or not selection.valid or not readable or raw is None:
    return FirmwareChoice()
  try:
    inspected = inspect_cache('CarParamsPersistent', raw)
    if inspected.status != 'valid' or inspected.payload is None:
      return FirmwareChoice()
    cp = messaging.log_from_bytes(inspected.payload, car.CarParams)
    if selection.platform is not None and selection.platform != cp.carFingerprint:
      return FirmwareChoice()
    if (read_selection(params) != selection or snapshot(params) != sources or
        read_saved(params, 'CarParamsPersistent', 1_048_576) != (raw, True)):
      return FirmwareChoice()
    return resolve(cp, sources)
  except (AttributeError, OSError, RuntimeError, TypeError, ValueError, OverflowError):
    return FirmwareChoice()


def firmware_path(directory, app_fn, choice):
  path = Path(directory) / choice.filename(app_fn)
  if not path.is_file() or path.stat().st_size == 0:
    raise FileNotFoundError(f'Required Panda firmware missing or empty: {path.name}')
  return str(path)


def firmware_ready(directory, choice):
  try:
    for app_fn in ('panda.bin.signed', 'panda_h7.bin.signed'):
      firmware_path(directory, app_fn, choice)
    return True
  except (OSError, ValueError):
    return False
