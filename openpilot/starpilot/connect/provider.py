"""Cloud-provider identity and a reboot-bound switch with recoverable state."""

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from openpilot.common.hardware.hw import Paths
from openpilot.starpilot.storage import starpilot_storage_root


@dataclass(frozen=True)
class Provider:
  name: str
  label: str
  api: str
  athena: str
  web: str
  upload_attribute: str


PROVIDERS = {
  'comma': Provider('comma', 'comma connect', 'https://api.commadotai.com', 'wss://athena.comma.ai',
                    'https://connect.comma.ai', 'user.upload'),
  'konik': Provider('konik', 'Konik', 'https://api.konik.ai', 'wss://athena.konik.ai',
                    'https://stable.konik.ai', 'user.upload.konik'),
}
PROVIDER_NAMES = ('comma', 'konik', 'custom')
CUSTOM_UPLOAD_ATTRIBUTE = 'user.upload.custom'
MAX_CUSTOM_URL_LEN = 256
STATE_KEYS = ('DongleId', 'PrimeType', 'PairingProvider', 'PairingEmail', 'AthenadUploadQueue', 'AthenadRecentlyViewedRoutes',
              'ApiCache_Device', 'ApiCache_FirehoseStats')
TRANSIENT_KEYS = ('AccessToken', 'LastAthenaPingTime')
MAX_STATE_BYTES = 8 * 1024 * 1024
ROUTE_MARKER = '.cloud-provider'
OFFLINE = Provider('offline', 'Cloud Unavailable', '', '', '', 'user.upload.disabled')


def validate_custom_url(raw_url: str, *, websocket: bool = False) -> str:
  if type(raw_url) is not str:
    raise ValueError('Enter a valid custom server URL')
  cleaned = raw_url.strip()
  if not cleaned or len(cleaned) > MAX_CUSTOM_URL_LEN or any(ord(ch) <= 32 or ord(ch) == 127 for ch in cleaned):
    raise ValueError('Enter a valid custom server URL')
  if '://' not in cleaned:
    cleaned = ('wss://' if websocket else 'https://') + cleaned
  parts = urlsplit(cleaned)
  expected_scheme = 'wss' if websocket else 'https'
  if (parts.scheme.lower() != expected_scheme or not parts.hostname or parts.username is not None or
      parts.password is not None or parts.query or parts.fragment or parts.path.startswith('//')):
    raise ValueError(f'Custom URL must use {expected_scheme}:// with a valid host and no query or fragment')
  port = parts.port  # Validates integer port in 1..65535 when specified.
  host = parts.hostname.lower()
  if ':' in host:
    host = f'[{host}]'
  netloc = f'{host}:{port}' if port is not None else host
  path = parts.path.rstrip('/')
  return f'{expected_scheme}://{netloc}{path}'


def build_custom_config(custom) -> dict[str, str]:
  if isinstance(custom, str):
    custom = {'url': custom}
  if type(custom) is not dict or not custom.keys() <= {'url', 'api', 'athena', 'web'}:
    raise ValueError('Invalid custom cloud configuration')
  if any(value is not None and type(value) is not str for value in custom.values()):
    raise ValueError('Invalid custom cloud configuration')
  base = (custom.get('api') or '').strip() or (custom.get('url') or '').strip()
  if not base:
    raise ValueError('Enter a custom server URL')
  api = validate_custom_url(base, websocket=False)
  web_raw = (custom.get('web') or '').strip()
  athena_raw = (custom.get('athena') or '').strip()
  web = validate_custom_url(web_raw, websocket=False) if web_raw else api
  if athena_raw:
    athena = validate_custom_url(athena_raw, websocket=True)
  else:
    split_api = urlsplit(api)
    athena = f'wss://{split_api.netloc}{split_api.path}'
  return {'api': api, 'athena': athena, 'web': web}


def custom_provider(config) -> Provider:
  if type(config) is not dict or set(config) != {'api', 'athena', 'web'}:
    raise ValueError('Custom cloud configuration is invalid')
  api = validate_custom_url(config['api'], websocket=False)
  athena = validate_custom_url(config['athena'], websocket=True)
  web = validate_custom_url(config['web'], websocket=False)
  return Provider('custom', 'Custom', api, athena, web, CUSTOM_UPLOAD_ATTRIBUTE)


def configured_provider(name: str, *, root=None) -> Provider | None:
  if name in PROVIDERS:
    return PROVIDERS[name]
  if name == 'custom':
    try:
      config = configuration(root).get('custom')
      return custom_provider(config) if config is not None else None
    except (OSError, ValueError, TypeError, KeyError):
      return None
  return None


def root_path():
  return starpilot_storage_root() / 'connect'


def _read(path, default=None):
  try:
    with path.open('rb') as stream:
      raw = stream.read(MAX_STATE_BYTES + 1)
    if len(raw) > MAX_STATE_BYTES:
      raise ValueError('Cloud state is too large')
    return json.loads(raw)
  except FileNotFoundError:
    return default


def _write(path, value):
  path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  fd, temporary = tempfile.mkstemp(prefix='.connect-', dir=path.parent)
  try:
    with os.fdopen(fd, 'w') as stream:
      json.dump(value, stream, separators=(',', ':'))
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(parent)
    finally:
      os.close(parent)
  finally:
    Path(temporary).unlink(missing_ok=True)


def configuration(root=None):
  value = _read((root or root_path()) / 'provider.json', {'active': 'comma', 'selected': 'comma', 'requestedBoot': ''})
  required = {'active', 'selected', 'requestedBoot'}
  allowed = required | {'custom', 'pendingCustom'}
  if (type(value) is not dict or not required <= value.keys() <= allowed or
      value['active'] not in PROVIDER_NAMES or value['selected'] not in PROVIDER_NAMES or
      type(value['requestedBoot']) is not str):
    raise ValueError('Cloud provider configuration is invalid')
  for key in ('custom', 'pendingCustom'):
    if key in value and value[key] is not None:
      custom_provider(value[key])
  if (value['active'] == 'custom' or value['selected'] == 'custom') and value.get('custom') is None:
    raise ValueError('Custom cloud configuration is missing')
  return value


def active_provider(root=None):
  try:
    value = configuration(root)
    return PROVIDERS[value['active']] if value['active'] in PROVIDERS else custom_provider(value['custom'])
  except (OSError, ValueError, TypeError, KeyError):
    return OFFLINE


def state_revision(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def boot_id():
  try:
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
  except FileNotFoundError:
    import psutil
    return f'host-{psutil.boot_time():.3f}'


@contextmanager
def _exclusive(root):
  root.mkdir(mode=0o700, parents=True, exist_ok=True)
  fd = os.open(root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
  try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    yield
  finally:
    os.close(fd)


def select_provider(name, revision, authorized, *, custom=None, root=None, boot=boot_id):
  root = root or root_path()
  if name not in PROVIDER_NAMES:
    raise ValueError('Unknown cloud provider')
  with _exclusive(root):
    value = configuration(root)
    if not authorized() or state_revision(value) != revision:
      raise ValueError('Cloud selection changed. Refresh and try again.')
    if name == 'custom':
      if custom is not None:
        target_custom = build_custom_config(custom)
      elif value.get('custom') is not None:
        custom_provider(value['custom'])
        target_custom = dict(value['custom'])
      else:
        raise ValueError('Enter a custom server URL')
      if value['active'] == 'custom':
        if target_custom != value.get('custom'):
          value['pendingCustom'] = target_custom
        else:
          value.pop('pendingCustom', None)
      else:
        value.pop('pendingCustom', None)
        value['custom'] = target_custom
    else:
      value.pop('pendingCustom', None)
    value.update(selected=name, requestedBoot=boot())
    if not authorized():
      raise ValueError('Turn off the vehicle before changing cloud providers')
    _write(root / 'provider.json', value)
  return status(root)


def status(root=None):
  value = configuration(root)
  pending_custom = value.get('pendingCustom')
  shown_custom = pending_custom if pending_custom is not None else value.get('custom')
  providers = [{'id': p.name, 'label': p.label, 'url': p.web} for p in PROVIDERS.values()]
  providers.append({'id': 'custom', 'label': 'Custom', 'url': shown_custom['web'] if shown_custom else ''})
  return {
    'active': value['active'],
    'selected': value['selected'],
    'requestedBoot': value['requestedBoot'],
    'revision': state_revision(value),
    'restartRequired': value['active'] != value['selected'] or pending_custom is not None,
    'custom': shown_custom,
    'providers': providers,
  }


def _capture(params):
  values = {}
  for key in STATE_KEYS:
    path = Path(params.get_param_path(key))
    try:
      raw = path.read_bytes()
      if len(raw) > MAX_STATE_BYTES // 2:
        raise ValueError('Cloud preference is too large')
      values[key] = base64.b64encode(raw).decode()
    except FileNotFoundError:
      values[key] = None
  if len(json.dumps(values)) > MAX_STATE_BYTES:
    raise ValueError('Cloud snapshot is too large')
  return values


def _restore(params, values):
  if type(values) is not dict or set(values) != set(STATE_KEYS):
    raise ValueError('Cloud identity snapshot is invalid')
  decoded = {key: None if value is None else base64.b64decode(value, validate=True) for key, value in values.items()}
  for key, raw in decoded.items():
    params.check_key(key)
    path = Path(params.get_param_path(key))
    if raw is None:
      params.remove(key)
    else:
      fd, temporary = tempfile.mkstemp(prefix='.cloud-', dir=path.parent)
      try:
        with os.fdopen(fd, 'wb') as stream:
          stream.write(raw)
          stream.flush()
          os.fsync(stream.fileno())
        os.replace(temporary, path)
      finally:
        Path(temporary).unlink(missing_ok=True)
  for key in TRANSIENT_KEYS:
    params.remove(key)
  fd = os.open(Path(params.get_param_path(STATE_KEYS[0])).parent, os.O_RDONLY | os.O_DIRECTORY)
  try:
    os.fsync(fd)
  finally:
    os.close(fd)


def activate_at_boot(params, *, root=None, boot=boot_id):
  """Called before managed services start; never changes provider in a live session."""
  root = root or root_path()
  if not (root / 'provider.json').exists() and not (root / 'switch.json').exists():
    params.put('ConnectProvider', 'comma', block=True)
    return
  with _exclusive(root):
    value = configuration(root)
    journal = _read(root / 'switch.json')
    if journal is None:
      pending_custom = value.get('pendingCustom')
      if (value['active'] == value['selected'] and pending_custom is None) or value['requestedBoot'] == boot():
        params.put('ConnectProvider', value['active'], block=True)
        return
      _write(root / value['active'] / 'params.json', _capture(params))
      if value['active'] == 'custom' and value['selected'] == 'custom' and pending_custom is not None:
        target = dict.fromkeys(STATE_KEYS)
      else:
        target = _read(root / value['selected'] / 'params.json', dict.fromkeys(STATE_KEYS))
      journal = {'target': value['selected'], 'values': target}
      if pending_custom is not None and value['selected'] == 'custom':
        journal['custom'] = pending_custom
      _write(root / 'switch.json', journal)
    required = {'target', 'values'}
    if (type(journal) is not dict or not required <= journal.keys() <= (required | {'custom'}) or
        journal['target'] not in PROVIDER_NAMES):
      raise ValueError('Cloud switch journal is invalid')
    if 'custom' in journal and journal['custom'] is not None:
      custom_provider(journal['custom'])
    _restore(params, journal['values'])
    params.put('ConnectProvider', journal['target'], block=True)
    value['active'] = journal['target']
    if 'custom' in journal and journal['custom'] is not None:
      value['custom'] = journal['custom']
    value.pop('pendingCustom', None)
    _write(root / 'provider.json', value)
    (root / 'switch.json').unlink()
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(fd)
    finally:
      os.close(fd)


def provider_key_pair(provider_name='konik'):
  """Non-comma clouds never receive the factory comma private key."""
  if provider_name not in ('konik', 'custom'):
    raise ValueError('Unknown non-comma cloud provider')
  root = root_path()
  with _exclusive(root):
    path = root / provider_name / 'identity.json'
    identity = _read(path)
    if identity is None:
      from cryptography.hazmat.primitives import serialization
      from cryptography.hazmat.primitives.asymmetric import rsa
      key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
      identity = {
        'private': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                     serialization.NoEncryption()).decode(),
        'public': key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
      }
      _write(path, identity)
    return 'RS256', identity['private'], identity['public']


def konik_key_pair():
  """Konik never receives the factory comma private key."""
  return provider_key_pair('konik')


def galaxy_device_id(params):
  """Galaxy pairing belongs to the physical device, independent of its cloud."""
  if active_provider().name == 'comma':
    return params.get('DongleId') or ''
  try:
    saved = _read(root_path() / 'comma' / 'params.json', {})
    raw = saved.get('DongleId')
    if raw is not None:
      value = base64.b64decode(raw, validate=True).decode()
      if re.fullmatch(r'[a-f0-9]{16}', value):
        return value
    return (Path(Paths.persist_root()) / 'comma/dongle_id').read_text().strip()
  except (OSError, ValueError, KeyError, TypeError):
    return (params.get('DongleId') or '') if params.get('ConnectProvider') == 'comma' else ''


def route_url(dongle, route, provider=None):
  provider = provider or active_provider()
  if not re.fullmatch(r'[a-f0-9]{16}', dongle or '') or not re.fullmatch(r'[a-f0-9]{8}--[a-f0-9]{10}|[0-9-]{20}', route or ''):
    return None
  return f'{provider.web}/{dongle}/{route}'


def mark_recording(directory, provider_name):
  """Commit ownership before creating derived recording data."""
  if provider_name not in PROVIDER_NAMES:
    raise ValueError('Cloud provider is unavailable')
  directory = Path(directory)
  directory.mkdir(parents=True, exist_ok=True)
  marker = directory / ROUTE_MARKER
  if marker.exists():
    if recording_provider(directory) != provider_name:
      raise ValueError('Recording belongs to another cloud provider')
    return
  fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
  with os.fdopen(fd, 'wb') as stream:
    stream.write(provider_name.encode('ascii'))
    stream.flush()
    os.fsync(stream.fileno())
  parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
  try:
    os.fsync(parent)
  finally:
    os.close(parent)


def recording_provider(path, *, dir_fd=None):
  """Untagged recordings predate provider selection and belong to comma."""
  marker = ROUTE_MARKER if dir_fd is not None else Path(path) / ROUTE_MARKER
  try:
    fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    with os.fdopen(fd, 'rb') as stream:
      value = stream.read(32).decode('ascii')
    return value if value in PROVIDER_NAMES else None
  except FileNotFoundError:
    return 'comma'
  except (OSError, UnicodeError):
    return None


def owns_recording(path, log_root, provider=None):
  provider = provider or active_provider().name
  try:
    relative = Path(path).resolve().relative_to(Path(log_root).resolve())
    if not relative.parts:
      return False
    directory = Path(log_root) / relative.parts[0]
    if len(relative.parts) > 1:
      marker = Path(path).parent / ('.cloud-' + Path(path).name)
      if marker.exists() or marker.is_symlink():
        fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
          return stream.read(32) == provider.encode('ascii')
    return recording_provider(directory) == provider
  except (OSError, ValueError):
    return False


def cloudlog_root():
  base = Path(Paths.swaglog_root())
  provider = active_provider().name
  return str(base if provider == 'comma' else base / provider)


def recording_device_id(name, params):
  if name not in PROVIDER_NAMES:
    return None
  try:
    if active_provider().name == name:
      identity = params.get('DongleId')
    else:
      saved = _read(root_path() / name / 'params.json', {})
      raw = saved.get('DongleId')
      identity = base64.b64decode(raw, validate=True).decode() if raw else None
    if not identity and name == 'comma':
      identity = (Path(Paths.persist_root()) / 'comma/dongle_id').read_text().strip()
    return identity if re.fullmatch(r'[a-f0-9]{16}', identity or '') else None
  except (OSError, ValueError, TypeError, KeyError):
    return None
