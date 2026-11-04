import io
import json

import pytest

from openpilot.starpilot.soloist import client, supervisor
from openpilot.starpilot.soloist.tests.test_runtime import make_supervisor, ready_layout
from openpilot.starpilot.soloist.paths import Layout


def test_local_api_failure_recovers_without_rollback_or_erasing_login(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'soloist')
  ready_layout(layout, monkeypatch)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  spawned = []
  sup, now = make_supervisor(layout, spawned)
  sup.tick()
  sup.tick()  # the default fake answered once
  (layout.state / 'account').write_text('saved')
  sup.client.snapshot = lambda: {'connected': False}
  sup.tick()
  now[0] += 121
  sup.tick()
  assert sup.player is None and sup.fast_failures == 0
  now[0] += 11
  sup.tick()
  assert len(spawned) == 4
  assert (layout.state / 'account').read_text() == 'saved'


def test_auth_heartbeat_refreshes_state_after_relogin(tmp_path):
  now = [0.0]
  player = client.PlayerClient(tmp_path, clock=lambda: now[0])
  events = [True, False, True]
  class Connection:
    sent = []
    def settimeout(self, _timeout): pass
    def send(self, message): self.sent.append(json.loads(message)['command'])
    def recv(self):
      if not events:
        player.stop_event.set()
        return ''
      now[0] += 16
      return json.dumps({'type': 'auth_state', 'logged_in': events.pop(0)})
  connection = Connection()
  player._session(connection)
  assert connection.sent.count('get_auth_state') >= 3
  assert connection.sent.count('get_state') == 2


def test_unresponsive_socket_is_not_a_healthy_player(tmp_path):
  now = [0.0]
  player = client.PlayerClient(tmp_path, clock=lambda: now[0])
  class Connection:
    def settimeout(self, _timeout): pass
    def send(self, _message): pass
    def recv(self):
      now[0] += 16
      raise TimeoutError()
  with pytest.raises(TimeoutError, match='health check'):
    player._session(Connection())
  assert not player.snapshot()['connected']


def test_lost_login_recovers_but_first_pairing_waits_without_restart(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'soloist')
  ready_layout(layout, monkeypatch)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  spawned = []
  sup, now = make_supervisor(layout, spawned, logged_in=False)
  sup.tick()
  for _ in range(5):
    now[0] += 200
    sup.tick()
  assert len(spawned) == 2  # unlimited time for first pairing
  sup.client.snapshot = lambda: {'connected': True, 'loggedIn': True}
  sup.tick()
  sup.client.snapshot = lambda: {'connected': True, 'loggedIn': False}
  sup.tick()
  now[0] += 121
  sup.tick()
  assert sup.player is None
  now[0] += 11
  sup.tick()
  assert len(spawned) == 4
  sup.handle({'command': 'reset_session'})
  assert not sup.authenticated_once


LOGIN_FAILED_LINE = b'2026-07-28 08:06:23.046: login failed: make sure --api-key is valid\n'


def test_failed_saved_login_at_boot_retries_with_backoff(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'soloist')
  ready_layout(layout, monkeypatch)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  monkeypatch.setattr(supervisor.random, 'uniform', lambda _a, _b: 1.0)
  spawned = []
  sup, now = make_supervisor(layout, spawned, logged_in=False)
  sup.tick()
  assert len(spawned) == 2
  for attempt, delay in enumerate((5.0, 10.0, 20.0), start=1):
    sup._record(io.BytesIO(LOGIN_FAILED_LINE), 'soloist')
    sup.tick()
    assert sup.player is None and sup.status()['state'] == 'retrying'
    assert ('keeps refusing' in sup.status()['message']) == (attempt >= supervisor.LOGIN_REJECTED_AFTER)
    now[0] += delay - 0.1
    sup.tick()
    assert len(spawned) == 2 * attempt  # still waiting out the backoff
    now[0] += 0.2
    sup.tick()
    assert len(spawned) == 2 * (attempt + 1)
  sup.client.snapshot = lambda: {'connected': True, 'loggedIn': True, 'status': 'idle', 'active': False}
  sup.tick()
  assert sup.login_failures == 0 and sup.status()['state'] == 'ready'


def test_first_pairing_and_audio_lines_never_trigger_a_login_retry(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'soloist')
  ready_layout(layout, monkeypatch)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  spawned = []
  sup, now = make_supervisor(layout, spawned, logged_in=False)
  sup.tick()
  sup._record(io.BytesIO(b'W: [pulseaudio] login failed for unrelated reasons\n'), 'audio')
  for _ in range(5):
    now[0] += 200
    sup.tick()
  assert len(spawned) == 2 and sup.status()['state'] == 'waiting'


def test_lost_login_while_playing_gets_a_longer_grace(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'soloist')
  ready_layout(layout, monkeypatch)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  spawned = []
  sup, now = make_supervisor(layout, spawned)
  sup.tick()
  state = client.PlayerState()
  state.apply({'type': 'auth_state', 'logged_in': True, 'is_active': True})
  state.apply({'type': 'playback_changed', 'status': 'playing'})
  state.value['connected'] = True
  sup.client.snapshot = lambda: dict(state.value)
  sup.tick()
  state.apply({'type': 'auth_state', 'logged_in': False})  # the real client clears playback on logout
  for _ in range(5):
    now[0] += 100
    sup.tick()
  assert sup.player is not None and len(spawned) == 2
  now[0] += supervisor.PLAYING_GRACE_SECONDS
  sup.tick()
  assert sup.player is None


def test_silent_build_counts_toward_rollback(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'soloist')
  ready_layout(layout, monkeypatch)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  rolled = []
  monkeypatch.setattr(supervisor.package, 'rollback', lambda _l: rolled.append(True) or True)
  spawned = []
  sup, now = make_supervisor(layout, spawned)
  def silent(): return {'connected': False}
  for _ in range(supervisor.MAX_FAST_FAILURES):
    sup.tick()
    sup.client.snapshot = silent
    sup.tick()
    now[0] += supervisor.UNRESPONSIVE_SECONDS + 1
    sup.tick()
    assert sup.player is None
    now[0] += 120
  assert rolled == [True]


def test_speaker_check_runs_off_the_tick(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'soloist')
  ready_layout(layout, monkeypatch)
  supervisor.preferences.save(supervisor.preferences.Preferences(output_address='AA:BB:CC:DD:EE:FF', output_name='Car'), layout)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  class Output:
    calls = 0
    def ready(self, _address):
      self.calls += 1
      return self.calls % 2 == 0  # flaps on every check
  spawned, queued = [], []
  sup, now = make_supervisor(layout, spawned)
  sup.output = Output()
  sup.tick()
  sup.run_async = queued.append
  for _ in range(6):
    now[0] += supervisor.OUTPUT_RETRY_SECONDS + 1
    sup.tick()
    assert len(queued) <= 1  # the tick only queued the blocking check
    if queued:
      queued.pop()()
  assert len(spawned) == 2


def test_phone_source_never_runs_soloist(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'soloist')
  ready_layout(layout, monkeypatch)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  spawned = []
  sup, now = make_supervisor(layout, spawned)
  sup.tick()
  client = sup.client
  assert any('soloist' in process.argv[0] for process in spawned)
  supervisor.preferences.save(supervisor.preferences.Preferences(source='phone', output='test'), layout)
  spawned.clear()
  for _ in range(5):
    now[0] += 60
    sup.tick()
  assert client.stopped and sup.client is None
  assert not any('soloist' in process.argv[0] for process in spawned)
  assert sup.status()['state'] == 'no_phone'


def test_phone_page_never_shows_the_spotify_speaker(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'soloist')
  ready_layout(layout, monkeypatch)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  monkeypatch.setattr(supervisor.phone_audio, 'profile', lambda _action: None)
  supervisor.preferences.save(supervisor.preferences.Preferences(output_address='AA:BB:CC:DD:EE:FF', output_name='Car'), layout)
  class Output:
    connected = False
    def ready(self, _address): return self.connected
  class Phone:
    def powered(self): return True
    def ready(self, _address, _now): return True
    def track(self, _address): return None
    def close(self): pass
  spawned = []
  sup, now = make_supervisor(layout, spawned)
  sup.output, sup.phone = Output(), Phone()
  sup.tick()
  assert sup.status()['outputWaiting'] == 'Car'
  sup.output.connected = True
  supervisor.preferences.save(supervisor.preferences.Preferences(source='phone', phone_address='11:22:33:44:55:66',
                                                                 output_address='AA:BB:CC:DD:EE:FF', output_name='Car'), layout)
  now[0] += 1
  sup.tick()
  assert sup.player is not None and 'soloist' not in sup.player.argv[0]
  assert sup.status()['outputWaiting'] == ''
