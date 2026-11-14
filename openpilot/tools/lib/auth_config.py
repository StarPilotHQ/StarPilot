"""Tools credentials are isolated by the active cloud provider."""
import json
import os

from openpilot.common.hardware.hw import Paths
from openpilot.common.utils import atomic_write
from openpilot.starpilot.connect.provider import active_provider, PROVIDER_NAMES


class MissingAuthConfigError(Exception):
  pass


def _provider(provider_name=None):
  name = active_provider().name
  if name not in PROVIDER_NAMES or provider_name not in (None, name):
    raise MissingAuthConfigError('Cloud provider is unavailable or changed')
  return name


def auth_path(provider_name=None):
  name = _provider(provider_name)
  filename = 'auth.json' if name == 'comma' else f'auth-{name}.json'
  return os.path.join(Paths.config_root(), filename)


def get_token(provider_name=None):
  try:
    name = _provider(provider_name)
    with open(auth_path(name)) as stream:
      auth = json.load(stream)
    # Historical untagged credentials belong only to comma.
    if not isinstance(auth, dict) or auth.get('provider', 'comma') != name:
      return None
    token = auth.get('access_token')
    return token if isinstance(token, str) and token.strip() else None
  except (OSError, ValueError, TypeError, MissingAuthConfigError):
    return None


def set_token(token, provider_name=None):
  name = _provider(provider_name)
  if not isinstance(token, str) or not token.strip():
    raise ValueError('A nonempty provider token is required')
  os.makedirs(Paths.config_root(), exist_ok=True)
  with atomic_write(auth_path(name), overwrite=True) as stream:
    if _provider(name) != name:
      raise MissingAuthConfigError('Cloud provider changed')
    json.dump({'provider': name, 'access_token': token}, stream)


def clear_token(provider_name=None):
  try:
    os.unlink(auth_path(provider_name))
  except FileNotFoundError:
    pass
