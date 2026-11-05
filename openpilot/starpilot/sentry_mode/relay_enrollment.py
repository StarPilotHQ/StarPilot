"""Private relay credential lifecycle; the owner persists each transition."""
import hashlib
import re
import secrets
import uuid
from urllib.parse import urlsplit

import requests


def fresh():
  return {'version': 1, 'url': '', 'id': '', 'secret': '', 'invitation': '', 'requestId': '',
          'candidate': '', 'state': 'unenrolled', 'registered': {}, 'next': 0, 'attempts': 0}


def validate(value):
  if not isinstance(value, dict) or set(value) != set(fresh()) or value['version'] != 1:
    raise ValueError('Invalid relay enrollment')
  if value['state'] not in ('unenrolled', 'enrolling', 'enrolled', 'rotating', 'confirming', 'revoking', 'revoked', 'actionRequired'):
    raise ValueError('Invalid relay enrollment state')
  for key, size in (('id', 32), ('secret', 64), ('invitation', 64), ('requestId', 32), ('candidate', 64)):
    if not isinstance(value[key], str) or (value[key] and not re.fullmatch('[a-f0-9]{' + str(size) + '}', value[key])):
      raise ValueError('Invalid relay credential')
  if not isinstance(value['url'], str) or len(value['url']) > 2048:
    raise ValueError('Invalid relay address')
  if value['url']:
    normalized_url(value['url'])
  if (not isinstance(value['registered'], dict) or len(value['registered']) > 8 or
      any(not re.fullmatch(r'[a-f0-9]{12}', k) or not isinstance(v, str) or not re.fullmatch(r'[a-f0-9]{64}', v)
          for k, v in value['registered'].items()) or
      type(value['next']) not in (int, float) or not 0 <= value['next'] < 2**63 or
      type(value['attempts']) is not int or not 0 <= value['attempts'] <= 16):
    raise ValueError('Invalid relay recovery state')
  if value['state'] not in ('unenrolled', 'revoked') and not all(value[k] for k in ('url', 'id', 'secret')):
    raise ValueError('Missing relay credential')


def normalized_url(url):
  if not isinstance(url, str) or any(ord(c) <= 32 for c in url):
    raise ValueError('Use an HTTPS relay base URL')
  parts = urlsplit(url)
  if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
    raise ValueError('Use an HTTPS relay base URL without a secret or query')
  if parts.path.strip('/'):
    raise ValueError('Use the relay host URL without a path')
  port = parts.port
  return 'https://' + parts.hostname.lower() + (f':{port}' if port and port != 443 else '')


def begin(url, invitation=''):
  if not isinstance(invitation, str) or (invitation and not re.fullmatch(r'[a-f0-9]{64}', invitation)):
    raise ValueError('Enter the operator’s enrollment code')
  return {**fresh(), 'url': normalized_url(url), 'id': uuid.uuid4().hex, 'secret': secrets.token_hex(32),
          'invitation': invitation, 'requestId': uuid.uuid4().hex, 'state': 'enrolling'}


def fingerprint(sub):
  return hashlib.sha256((sub['appId'] + ':' + sub['token']).encode()).hexdigest()


def request(state, method, path, data=None, *, secret=None):
  headers = {'Authorization': 'Bearer ' + state['id'] + '.' + (secret or state['secret'])}
  with requests.request(method, state['url'] + '/v2/' + path, json=data, headers=headers,
                        timeout=(3, 10), allow_redirects=False, stream=True) as response:
    return response.status_code


def step(state, subscriptions, now, send=request):
  if state['state'] in ('unenrolled', 'revoked', 'actionRequired') or state['next'] > now:
    return
  phase = state['state']
  try:
    if phase == 'enrolling':
      code = send(state, 'GET', 'installation')
      if code == 401:
        code = send(state, 'POST', 'enroll', {'invitation': state['invitation'], 'installationId': state['id'],
                    'secretDigest': hashlib.sha256(state['secret'].encode()).hexdigest(), 'requestId': state['requestId']})
      if 200 <= code < 300:
        state.update(state='enrolled', invitation='')
    elif phase == 'rotating':
      code = send({**state, 'secret': state['candidate']}, 'GET', 'installation')
      if code == 401:
        code = send(state, 'POST', 'credentials/rotate', {'secretDigest': hashlib.sha256(state['candidate'].encode()).hexdigest(),
                                                         'requestId': state['requestId']})
      if 200 <= code < 300:
        state.update(secret=state['candidate'], candidate='', state='confirming')
    elif phase == 'confirming':
      code = send(state, 'POST', 'credentials/confirm', {})
      if 200 <= code < 300:
        state['state'] = 'enrolled'
    elif phase == 'revoking':
      code = send(state, 'DELETE', 'installation')
      if 200 <= code < 300 or code == 401:
        state.update(fresh()); state['state'] = 'revoked'
        return
    else:
      desired = {sub['id']: fingerprint(sub) for sub in subscriptions}
      removed = next((key for key in state['registered'] if key not in desired), None)
      pending = next((sub for sub in subscriptions if state['registered'].get(sub['id']) != desired[sub['id']]), None)
      if removed:
        code = send(state, 'DELETE', 'subscriptions/' + removed)
        if 200 <= code < 300:
          del state['registered'][removed]
      elif pending:
        code = send(state, 'PUT', 'subscriptions/' + pending['id'], {key: pending[key] for key in ('token', 'appId')})
        if 200 <= code < 300:
          state['registered'][pending['id']] = desired[pending['id']]
      else:
        return
    if 200 <= code < 300:
      state.update(attempts=0, next=0)
      return
    if code not in (408, 429) and code < 500:
      state.update(state='actionRequired', invitation='')
      return
  except requests.RequestException:
    pass
  state['attempts'] = min(16, state['attempts'] + 1)
  state['next'] = now + min(600, 5 * 2 ** min(7, state['attempts']))
