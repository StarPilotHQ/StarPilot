from openpilot.starpilot.sentry_mode import relay_enrollment as relay


def test_enrollment_recovers_saved_candidate_without_redeeming_twice():
  state = relay.begin('https://relay.example/', 'a' * 64)
  original = state['secret']
  calls = []
  def send(state, method, path, data=None):
    calls.append(path)
    return 200
  relay.step(state, [], 100, send)
  assert calls == ['installation']
  assert state['state'] == 'enrolled'
  assert state['secret'] == original
  assert state['invitation'] == ''
  relay.validate(state)


def test_offline_revoke_retries_and_retains_credential_until_acknowledged():
  state = relay.begin('https://relay.example', 'a' * 64)
  state['state'] = 'revoking'
  relay.step(state, [], 100, lambda *args: 503)
  assert state['state'] == 'revoking' and state['secret']
  assert state['next'] > 100
  relay.step(state, [], state['next'], lambda *args: 200)
  assert state['state'] == 'revoked' and not state['secret']


def test_subscription_reconciliation_removes_before_adding():
  state = relay.begin('https://relay.example', 'a' * 64)
  state.update(state='enrolled', invitation='', registered={'0123456789ab': 'b' * 64})
  sub = {'id': 'abcdef012345', 'token': 'phone', 'appId': 'com.starpilot.galaxy'}
  calls = []
  def send(state, method, path, data=None):
    calls.append((method, path))
    return 200
  relay.step(state, [sub], 100, send)
  relay.step(state, [sub], 101, send)
  assert calls == [('DELETE', 'subscriptions/0123456789ab'), ('PUT', 'subscriptions/abcdef012345')]
  assert state['registered'] == {sub['id']: relay.fingerprint(sub)}


def test_owner_persists_candidate_redacts_secrets_and_recovers_restart(tmp_path, monkeypatch):
  import json
  from types import SimpleNamespace
  from openpilot.starpilot.sentry_mode.notifications import NotificationOwner
  root = tmp_path / 'notifications'
  store = SimpleNamespace(snapshot=lambda: {'events': []})
  owner = NotificationOwner(root, store)
  result = owner.action({'action': 'relayEnroll', 'url': 'https://relay.example', 'invitation': 'a' * 64, 'confirmed': True})
  assert result['relayEnrollment']['state'] == 'enrolling'
  saved = owner.state['relayEnrollment'].copy()
  assert saved['secret'] not in json.dumps(result) and 'a' * 64 not in json.dumps(result)
  owner.close()
  owner = NotificationOwner(root, store)
  assert owner.state['relayEnrollment'] == saved
  original_step = relay.step
  monkeypatch.setattr(relay, 'step', lambda state, subs, now: original_step(state, subs, now, lambda *args: 200))
  owner.tick()
  assert owner.snapshot()['appPushAvailable']
  owner.action({'action': 'relayRevoke', 'confirmed': True})
  assert not owner.snapshot()['appPushAvailable']
  owner.tick()
  assert owner.snapshot()['relayEnrollment']['state'] == 'revoked'
  owner.close()


def test_owner_rejects_silent_enrollment_and_host_changes(tmp_path):
  import pytest
  from types import SimpleNamespace
  from openpilot.starpilot.sentry_mode.notifications import NotificationOwner
  owner = NotificationOwner(tmp_path / 'notifications', SimpleNamespace(snapshot=lambda: {'events': []}))
  try:
    with pytest.raises(ValueError):
      owner.action({'action': 'relayEnroll', 'url': 'https://relay.example', 'invitation': 'a' * 64, 'confirmed': False})
    owner.action({'action': 'relayEnroll', 'url': 'https://relay.example', 'invitation': 'a' * 64, 'confirmed': True})
    with pytest.raises(ValueError):
      owner.action({'action': 'configure', 'channel': 'appPush', 'enabled': True, 'url': 'https://other.example', 'token': ''})
  finally:
    owner.close()


def test_rotation_recovers_after_old_credential_overlap_expired():
  state = relay.begin('https://relay.example', 'a' * 64)
  state.update(state='rotating', invitation='', candidate='b' * 64)
  calls = []
  def send(current, method, path, data=None):
    calls.append((current['secret'], method, path))
    return 200 if current['secret'] == 'b' * 64 else 401
  relay.step(state, [], 100, send)
  assert state['state'] == 'confirming' and state['secret'] == 'b' * 64
  assert calls == [('b' * 64, 'GET', 'installation')]
  relay.step(state, [], 101, send)
  assert state['state'] == 'enrolled'


def test_v2_sender_binds_bearer_to_host_and_disables_redirects(tmp_path, monkeypatch):
  from types import SimpleNamespace
  from openpilot.starpilot.sentry_mode.notifications import NotificationOwner
  from openpilot.starpilot.sentry_mode import notifications
  owner = NotificationOwner(tmp_path / 'notifications', SimpleNamespace(snapshot=lambda: {'events': []}))
  state = relay.begin('https://relay.example', 'a' * 64)
  state.update(state='enrolled', invitation='')
  owner.state['relayEnrollment'] = state
  calls = []
  monkeypatch.setattr(notifications.requests, 'post', lambda url, **kwargs: calls.append((url, kwargs)) or SimpleNamespace(status_code=200, close=lambda: None))
  sub = {'token': 'phone', 'appId': 'com.starpilot.galaxy', 'label': 'Phone'}
  job = {'kind': 'test', 'eventId': 'b' * 32, 'created': 100}
  try:
    assert owner._send('appPush', {'url': state['url']}, sub, job, 'key', '') == 200
    assert calls[0][0] == 'https://relay.example/v2/sentry'
    assert calls[0][1]['headers']['Authorization'] == 'Bearer ' + state['id'] + '.' + state['secret']
    assert calls[0][1]['allow_redirects'] is False
    assert owner._send('appPush', {'url': 'https://elsewhere.example'}, sub, job, 'key', '') == 401
    assert len(calls) == 1
    state['state'] = 'revoking'
    assert owner._send('appPush', {'url': state['url']}, sub, job, 'key', '') == 401
    assert len(calls) == 1
  finally:
    owner.close()


def test_one_tap_enrollment_uses_release_host_and_preserves_candidate_on_retry(tmp_path, monkeypatch):
  from types import SimpleNamespace
  from openpilot.starpilot.sentry_mode import notifications
  owner = notifications.NotificationOwner(tmp_path / 'notifications', SimpleNamespace(snapshot=lambda: {'events': []}))
  monkeypatch.setattr(notifications, 'DEFAULT_RELAY_URL', 'https://hosted.example')
  try:
    status = owner.action({'action': 'relayEnroll', 'confirmed': True})
    saved = owner.state['relayEnrollment'].copy()
    assert status['relayEnrollment']['automaticConfigured']
    assert saved['url'] == 'https://hosted.example' and saved['invitation'] == ''
    owner.action({'action': 'relayEnroll', 'confirmed': True})
    assert owner.state['relayEnrollment'] == saved
  finally:
    owner.close()


def test_unconfigured_release_does_not_claim_automatic_alerts(tmp_path, monkeypatch):
  import pytest
  from types import SimpleNamespace
  from openpilot.starpilot.sentry_mode import notifications
  monkeypatch.setattr(notifications, 'DEFAULT_RELAY_URL', '')
  owner = notifications.NotificationOwner(tmp_path / 'notifications', SimpleNamespace(snapshot=lambda: {'events': []}))
  try:
    assert not owner.snapshot()['relayEnrollment']['automaticConfigured']
    with pytest.raises(ValueError, match='not configured'):
      owner.action({'action': 'relayEnroll', 'confirmed': True})
    assert owner.state['relayEnrollment']['state'] == 'unenrolled'
  finally:
    owner.close()
