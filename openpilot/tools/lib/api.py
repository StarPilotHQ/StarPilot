import os
import requests
from requests.adapters import HTTPAdapter, Retry
from openpilot.starpilot.connect.provider import active_provider, PROVIDER_NAMES

API_HOST = os.getenv('API_HOST', 'https://api.commadotai.com')

# TODO: this should be merged into common.api

class CommaApi:
  def __init__(self, token=None, *, provider_name=None):
    selected = active_provider()
    if selected.name not in PROVIDER_NAMES or provider_name not in (None, selected.name):
      raise APIError('Cloud provider is unavailable or changed')
    self.provider_name = selected.name
    self.api_host = API_HOST if selected.name == 'comma' else selected.api
    self.session = requests.Session()
    self.session.headers['User-agent'] = 'OpenpilotTools'
    if token:
      self.session.headers['Authorization'] = 'JWT ' + token

    retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    self.session.mount('https://', HTTPAdapter(max_retries=retries))

  def request(self, method, endpoint, **kwargs):
    if active_provider().name != self.provider_name:
      raise APIError('Cloud provider changed; sign in again')
    with self.session.request(method, self.api_host.rstrip('/') + '/' + endpoint.lstrip('/'), **kwargs) as resp:
      if resp.status_code in (401, 403):
        raise UnauthorizedError('Unauthorized. Authenticate with openpilot/tools/lib/auth.py')
      if resp.status_code >= 400:
        error = APIError(f'Cloud API returned HTTP {resp.status_code}')
        error.status_code = resp.status_code
        raise error
      resp_json = resp.json()
      if isinstance(resp_json, dict) and resp_json.get('error'):
        if resp.status_code in [401, 403]:
          raise UnauthorizedError('Unauthorized. Authenticate with openpilot/tools/lib/auth.py')

        e = APIError(str(resp.status_code) + ":" + resp_json.get('description', str(resp_json['error'])))
        e.status_code = resp.status_code
        raise e
      return resp_json

  def get(self, endpoint, **kwargs):
    return self.request('GET', endpoint, **kwargs)

  def post(self, endpoint, **kwargs):
    return self.request('POST', endpoint, **kwargs)

class APIError(Exception):
  pass

class UnauthorizedError(Exception):
  pass
