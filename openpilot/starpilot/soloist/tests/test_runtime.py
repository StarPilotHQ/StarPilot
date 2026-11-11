import os
import subprocess
from pathlib import Path

import pytest

from openpilot.starpilot.soloist import backend, client, credentials, preferences, supervisor
from openpilot.starpilot.soloist.paths import Layout


@pytest.fixture
def layout(tmp_path):
  return Layout(tmp_path / 'soloist')


def test_credentials_are_private_and_never_echoed(layout):
  assert not credentials.present(layout)
  with pytest.raises(credentials.InvalidKey):
    credentials.store('short', layout)
  with pytest.raises(credentials.InvalidKey):
    credentials.store('abc def ghi jkl mno pqr', layout)
  credentials.store('  sk_live_0123456789abcdef  ', layout)
  assert credentials.load(layout) == 'sk_live_0123456789abcdef'
  assert layout.api_key.stat().st_mode & 0o777 == 0o600
  assert layout.api_key.parent.stat().st_mode & 0o777 == 0o700
  credentials.remove(layout)
  assert not credentials.present(layout)


def test_preferences_validate_and_round_trip(layout):
  assert preferences.load(layout) == preferences.Preferences()
  saved = preferences.save(preferences.Preferences(device_name=' Civic ', output_address='aa:bb:cc:dd:ee:ff', output_name='Car'), layout)
  assert saved.device_name == 'Civic' and saved.output_address == 'AA:BB:CC:DD:EE:FF'
  assert preferences.load(layout) == saved
  assert preferences.validated({**preferences.Preferences().__dict__, 'output': 'starport'}).output == 'starport'
  for bad in ({'device_name': ''}, {'cache_mb': 50}, {'initial_volume': 101}, {'output': 'speaker'}, {'output_address': 'nope'}):
    with pytest.raises(ValueError):
      preferences.validated({**preferences.Preferences().__dict__, **bad})
  layout.config.write_text('{"device_name": 7}')
  assert preferences.load(layout) == preferences.Preferences()


def test_enabled_flag(layout):
  from openpilot.starpilot.soloist.paths import enabled
  assert not enabled(layout)
  preferences.set_enabled(True, layout)
  assert enabled(layout)
  preferences.set_enabled(False, layout)
  assert not enabled(layout)


def test_alsa_config_is_process_local_bluealsa_pcm():
  text = backend.alsa_config('AA:BB:CC:DD:EE:FF', Path('/usr/share/alsa/alsa.conf'))
  assert text.startswith('</usr/share/alsa/alsa.conf>')
  assert 'type bluealsa' in text and 'device "AA:BB:CC:DD:EE:FF"' in text and 'profile "a2dp"' in text
  with pytest.raises(backend.BackendUnavailable):
    backend.alsa_config('AA:BB:CC:DD:EE:FF"\n}', Path('/usr/share/alsa/alsa.conf'))


def test_launch_commands_isolate_the_server_and_player(layout):
  prefs = preferences.Preferences(output_address='AA:BB:CC:DD:EE:FF', initial_volume=60)
  server = backend.server_launch(layout, prefs, alsa_base=Path('/usr/share/alsa/alsa.conf'))
  assert '-n' in server.argv and '--system=no' in server.argv
  assert '--realtime=no' in server.argv and '--high-priority=yes' in server.argv
  assert '--resample-method=speex-float-3' in server.argv
  assert (layout.runtime / 'daemon.conf').read_text() == 'nice-level = -5\n'
  assert server.env['PULSE_CONFIG'] == str(layout.runtime / 'daemon.conf')
  assert any(arg.startswith('module-alsa-sink device=starpilot_spotify') for arg in server.argv)
  assert server.env['ALSA_CONFIG_PATH'].startswith(str(layout.runtime))
  assert (layout.runtime / 'alsa.conf').stat().st_mode & 0o777 == 0o600
  test = backend.server_launch(layout, preferences.Preferences(output='test'))
  assert any(arg.startswith('module-null-sink') for arg in test.argv) and 'ALSA_CONFIG_PATH' not in test.env
  silent = backend.server_launch(layout, prefs, silent=True, alsa_base=Path('/usr/share/alsa/alsa.conf'))
  assert 'module-null-sink sink_name=spotify_silent' in silent.argv and 'ALSA_CONFIG_PATH' in silent.env
  player = backend.player_launch(layout, prefs, 'sk_live_0123456789abcdef')
  assert 'PULSE_SINK' not in player.env  # follows the server's default sink, so outputs can switch live
  assert player.argv[player.argv.index('--ws') + 1] == '127.0.0.1:0'
  assert player.argv[player.argv.index('--cache-size') + 1] == '256'
  assert player.argv[player.argv.index('--initial-volume') + 1] == '60'
  assert player.env['PULSE_SERVER'] == f'unix:{layout.runtime}/pulse/native'
  assert player.env['LD_LIBRARY_PATH'] == str(backend.SERVER_ROOT / 'lib')


def test_vendored_server_matches_its_checksums():
  import hashlib
  root = backend.SERVER_ROOT
  assert backend.bundle_ready()
  sums = [line.split() for line in (root / 'SHA256SUMS').read_text().splitlines() if line.strip()]
  assert {name for _, name in sums} >= {'bin/pulseaudio', 'lib/libpulsecore-16.1.so', 'modules/module-pipe-sink.so'}
  for digest, name in sums:
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
  for name in ('pulseaudio', 'libpulse0', 'libsndfile1', 'libltdl7', 'liborc-0.4-0t64', 'libsoxr0', 'libspeexdsp1', 'libtdb1'):
    assert (root / 'licenses' / f'{name}.copyright').is_file()
  # AGNOS 20 dropped libpulse0 and libsndfile1; Soloist and the phone relay load them from the bundle.
  for name in ('libpulse.so.0', 'libpulse-simple.so.0', 'libpulsecommon-16.1.so', 'libsndfile.so.1'):
    assert any(path == f'lib/{name}' for _, path in sums), name


def test_starport_output_is_a_real_time_pipe(layout):
  launch = backend.server_launch(layout, preferences.Preferences(output='starport'))
  sink = next(arg for arg in launch.argv if arg.startswith('module-pipe-sink'))
  assert f'file={backend.starport_pipe(layout)}' in sink
  assert 'rate=48000' in sink and 'channels=2' in sink and 'use_system_clock_for_timing=yes' in sink
  assert launch.env['LD_LIBRARY_PATH'] == str(backend.SERVER_ROOT / 'lib')


ENTITY = {'uri': 'spotify:track:1', 'entity_type': 'track', 'decorations': {
  'identity': {'name': 'My Song\x07'}, 'visual_identity': {'cover': [{'url': 'http://i.scdn.co/a', 'size': 'default'},
                                                                     {'url': 'https://i.scdn.co/image/x', 'size': 'large'},
                                                                     {'url': 'https://evil.example/x', 'size': 'default'}]},
  'parent': {'entity': {'decorations': {'identity': {'name': 'Album'}}}},
  'creators': [{'entity': {'decorations': {'identity': {'name': 'A'}}}}, {'entity': {'decorations': {'identity': {'name': 'B'}}}}],
  'playback': {'duration_ms': 210000}}}


def test_player_state_normalizes_untrusted_events():
  state = client.PlayerState()
  state.apply({'type': 'auth_state', 'logged_in': True, 'is_active': False, 'device_name': 'StarPilot'})
  state.apply({'type': 'playback_state', 'status': 'playing', 'item': ENTITY, 'volume': 65, 'is_active': True,
               'position': {'position_ms': 45000, 'timestamp_ms': 1747654321000, 'speed': 1.0},
               'options': {'shuffle': True, 'repeat': 'context'},
               'available_actions': {'pause': {}, 'skip_next': {}, 'seek_forward': {'step_ms': 15000}, 'bogus': {}}})
  value = state.value
  assert value['track'] == {'title': 'My Song', 'artists': 'A, B', 'album': 'Album', 'artwork': 'https://i.scdn.co/image/x',
                            'kind': 'track', 'durationMs': 210000}
  assert value['actions'] == ['pause', 'skip_next'] and value['shuffle'] and value['repeat'] == 'context'
  state.apply({'type': 'volume_changed', 'volume': 900})
  assert value['volume'] == 65
  state.apply({'type': 'playback_changed', 'status': 'paused'})
  assert value['status'] == 'paused'
  state.apply({'type': 'auth_state', 'logged_in': False})
  assert value['track'] is None and value['actions'] == []


def test_controls_map_to_documented_commands():
  assert client.commands('next', {}) == [{'type': 'command', 'command': 'skip_next'}]
  assert client.commands('volume', {'volume': 40}) == [{'type': 'command', 'command': 'set_volume', 'volume': 40}]
  assert [c['command'] for c in client.commands('repeat', {'mode': 'track'})] == ['set_repeat_context', 'set_repeat_track']
  assert [(c['command'], c['enabled']) for c in client.commands('repeat', {'mode': 'off'})] == \
         [('set_repeat_track', False), ('set_repeat_context', False)]
  for action, args in (('volume', {'volume': 101}), ('seek', {'positionMs': -1}), ('play', {'uri': 'x'}), ('shell', {}),
                       ('shuffle', {'enabled': 1})):
    with pytest.raises(ValueError):
      client.commands(action, args)


def test_endpoint_only_accepts_loopback(tmp_path):
  assert client.endpoint(tmp_path) is None
  (tmp_path / 'ws.addr').write_text('127.0.0.1\n')
  (tmp_path / 'ws.port').write_text('41103\n')
  assert client.endpoint(tmp_path) == 'ws://127.0.0.1:41103'
  (tmp_path / 'ws.addr').write_text('0.0.0.0')
  assert client.endpoint(tmp_path) is None


class FakeProcess:
  def __init__(self, argv, **kwargs):
    import io
    self.argv, self.kwargs = argv, kwargs
    self.pid = 99999999
    self.code = None
    self.stdout = io.BytesIO(b'I client expires in 42 days\nkey ' + b'sk_live_0123456789abcdef\n' if 'soloist' in argv[0] else b'')
  def poll(self): return self.code
  def wait(self, timeout=None): return self.code


class FakeClient:
  logged_in = True
  def __init__(self, _data):
    self.on_change = lambda: None
    self.started = self.stopped = False
    self.sent = []
  def start(self): self.started = True
  def stop(self): self.stopped = True
  def snapshot(self): return {'connected': True, 'loggedIn': self.logged_in, 'status': 'idle', 'active': False}
  def send(self, action, args): self.sent.append((action, args))


def ready_layout(layout, monkeypatch, *, expired=False):
  monkeypatch.setattr(supervisor.phone_audio, 'profile', lambda action: None)
  monkeypatch.setattr(supervisor.package, 'release_status', lambda _l: {'installed': True, 'expired': expired})
  monkeypatch.setattr(supervisor.backend, 'bundle_ready', lambda: True)
  layout.root.mkdir(parents=True, exist_ok=True)
  credentials.store('sk_live_0123456789abcdef', layout)
  preferences.save(preferences.Preferences(output='test'), layout)


def make_supervisor(layout, spawned, logged_in=True):
  def spawn(argv, **kwargs):
    process = FakeProcess(argv, **kwargs)
    spawned.append(process)
    if 'pulseaudio' in argv[0]:
      backend.runtime_paths(layout)['socket'].touch()
    return process
  now = [100.0]
  make_client = type('Client', (FakeClient,), {'logged_in': logged_in})
  sup = supervisor.Supervisor(layout, spawn=spawn, clock=lambda: now[0], make_client=make_client, network=lambda: True)  # ty: ignore[invalid-argument-type]
  sup.run_async = lambda fn: fn()
  return sup, now


def test_supervisor_reports_missing_setup_steps(layout, monkeypatch):
  sup, _ = make_supervisor(layout, [])
  sup.tick()
  assert sup.status()['state'] == 'missing_package'
  monkeypatch.setattr(supervisor.package, 'release_status', lambda _l: {'installed': True})
  sup.tick()
  assert sup.status()['state'] == 'missing_key'
  credentials.store('sk_live_0123456789abcdef', layout)
  sup.tick()
  assert sup.status()['state'] == 'no_output'


def test_supervisor_starts_server_then_player_and_redacts_key(layout, monkeypatch):
  ready_layout(layout, monkeypatch)
  spawned = []
  sup, _ = make_supervisor(layout, spawned, logged_in=False)
  sup.tick()
  assert [Path(p.argv[0]).name for p in spawned] == ['pulseaudio', 'soloist']
  assert all(p.kwargs['start_new_session'] for p in spawned)
  assert sup.status()['state'] == 'waiting'
  import time
  time.sleep(0.1)
  assert sup.days_left == 42
  assert all('sk_live' not in line for line in sup.log)
  assert sup.handle({'command': 'control', 'action': 'pause', 'args': {}}) == {'ok': True}
  assert sup.client.sent == [('pause', {})]
  assert sup.handle({'command': 'control', 'action': 'play_pause', 'args': {}}) == {'ok': True}
  assert sup.client.sent[-1] == ('play', {})
  sup.client.snapshot = lambda: {'status': 'playing'}
  sup.handle({'command': 'control', 'action': 'play_pause', 'args': {}})
  assert sup.client.sent[-1] == ('pause', {})


def test_supervisor_stops_retrying_an_expired_build(layout, monkeypatch):
  ready_layout(layout, monkeypatch)
  spawned = []
  sup, now = make_supervisor(layout, spawned)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  sup.tick()
  sup.player.code = 10
  now[0] += 1
  sup.tick()
  assert sup.status()['state'] == 'expired'
  now[0] += 1000
  sup.tick()
  assert len(spawned) == 2


def test_supervisor_backs_off_and_rolls_back_a_failing_build(layout, monkeypatch):
  ready_layout(layout, monkeypatch)
  rolled = []
  monkeypatch.setattr(supervisor.package, 'rollback', lambda _l: rolled.append(True) or True)
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  spawned = []
  sup, now = make_supervisor(layout, spawned)
  for _ in range(supervisor.MAX_FAST_FAILURES):
    sup.tick()
    sup.player.code = 1
    now[0] += 1
    sup.tick()
    assert sup.status()['state'] == 'retrying'
    now[0] += 100
  assert rolled == [True]


def _speaker_supervisor(layout, monkeypatch, *, can_switch):
  ready_layout(layout, monkeypatch)
  preferences.save(preferences.Preferences(output_address='AA:BB:CC:DD:EE:FF', output_name='Car'), layout)
  class Output:
    connected = False
    def ready(self, address):
      return self.connected
  switched = []
  monkeypatch.setattr(supervisor.backend, 'can_switch', lambda: can_switch)
  monkeypatch.setattr(supervisor.backend, 'switch_output', lambda _layout, speaker: switched.append(speaker))
  monkeypatch.setattr(supervisor, '_kill_group', lambda _p: None)
  spawned = []
  sup, now = make_supervisor(layout, spawned)
  sup.output = Output()
  sup.tick()
  assert sup.status()['state'] == 'ready' and sup.status()['outputWaiting'] == 'Car'
  assert len(spawned) == 2 and sup.client.started
  assert 'module-null-sink sink_name=spotify_silent' in spawned[0].argv
  sup.output.connected = True
  for _ in range(2):  # one check is not enough; the speaker has to stay connected
    now[0] += supervisor.OUTPUT_RETRY_SECONDS + 1
    sup.tick()
    assert len(spawned) == 2 and switched == []
  sup.tick()
  return sup, now, spawned, switched


def test_speaker_returning_switches_audio_without_restarting_soloist(layout, monkeypatch):
  sup, now, spawned, switched = _speaker_supervisor(layout, monkeypatch, can_switch=True)
  client = sup.client
  assert switched == [True] and len(spawned) == 2 and sup.client is client and not client.stopped
  assert sup.status()['outputWaiting'] == ''
  sup.output.connected = False
  for _ in range(3):
    now[0] += supervisor.OUTPUT_RETRY_SECONDS + 1
    sup.tick()
  assert switched == [True, False] and len(spawned) == 2
  assert sup.status()['outputWaiting'] == 'Car'


def test_speaker_switch_falls_back_to_a_restart(layout, monkeypatch):
  sup, _now, spawned, switched = _speaker_supervisor(layout, monkeypatch, can_switch=False)
  (layout.state / 'saved-session').write_text('retained')
  sup.tick()
  assert switched == [] and len(spawned) == 4
  assert any(arg.startswith('module-alsa-sink') for arg in spawned[2].argv)
  assert (layout.state / 'saved-session').read_text() == 'retained'
  assert sup.status()['outputWaiting'] == ''


def test_switch_output_moves_streams_and_drops_the_old_sink(layout):
  calls = []
  outputs = {('list', 'short', 'sink-inputs'): '7\t1\t3\tprotocol-native.c\ts16le 2ch 44100Hz\n',
             ('list', 'short', 'modules'): ('0\tmodule-native-protocol-unix\tsocket=/x\n' +
                                            '1\tmodule-null-sink\tsink_name=spotify_silent\n' +
                                            '2\tmodule-alsa-sink\tdevice=starpilot_spotify sink_name=spotify channels=2 tsched=0\n')}
  def run(argv, **kwargs):
    calls.append(tuple(argv[1:]))
    assert kwargs['env']['PULSE_SERVER'].startswith('unix:') and kwargs['timeout'] and kwargs['check']
    return subprocess.CompletedProcess(argv, 0, outputs.get(tuple(argv[1:]), ''))
  backend.switch_output(layout, True, run=run)
  assert calls == [('load-module', 'module-alsa-sink', 'device=starpilot_spotify', 'sink_name=spotify', 'channels=2', 'tsched=0'),
                   ('set-default-sink', 'spotify'), ('list', 'short', 'sink-inputs'), ('move-sink-input', '7', 'spotify'),
                   ('list', 'short', 'modules'), ('unload-module', '1')]
  calls.clear()
  backend.switch_output(layout, False, run=run)
  assert calls[0] == ('load-module', 'module-null-sink', 'sink_name=spotify_silent') and calls[-1] == ('unload-module', '2')

def test_control_socket_round_trip(layout, tmp_path):
  import threading
  from openpilot.starpilot.galaxy.spotify_setup import SpotifyClient
  sup, _ = make_supervisor(layout, [])
  import tempfile
  folder = tempfile.mkdtemp(prefix='sp-', dir='/tmp')  # AF_UNIX paths are short on macOS
  path = folder + '/s.sock'
  stop = threading.Event()
  thread = threading.Thread(target=supervisor.serve, args=(sup, path, stop), daemon=True)
  thread.start()
  try:
    import time
    client = SpotifyClient(path)
    result = None
    for attempt in range(50):
      if not Path(path).exists():
        time.sleep(0.02)
        continue
      try:
        result = client.call('status')
        break
      except (FileNotFoundError, ConnectionRefusedError):
        if attempt == 49:
          raise
        time.sleep(0.02)
    assert result is not None
    assert result['status']['state'] == 'starting'
    with pytest.raises(RuntimeError):
      client.call('control', action='pause', args={})
  finally:
    stop.set()
    thread.join(timeout=2)
    Path(path).unlink(missing_ok=True)
    os.rmdir(folder)
  assert not thread.is_alive()



def test_reap_orphans_only_targets_feature_binaries(layout, tmp_path, monkeypatch):
  proc = tmp_path / 'proc'
  (proc / '100').mkdir(parents=True)
  (proc / '200').mkdir()
  binary = layout.root / 'releases' / 'x' / 'soloist'
  binary.parent.mkdir(parents=True)
  binary.write_text('')
  (proc / '100' / 'exe').symlink_to(binary)
  (proc / '200' / 'exe').symlink_to('/usr/bin/bluealsa')
  killed = []
  monkeypatch.setattr(supervisor.os, 'kill', lambda pid, sig: killed.append(pid))
  assert supervisor.reap_orphans(layout, proc) == 1 and killed == [100]


def test_status_publishes_the_spotify_track_for_the_car_screen(layout):
  sup, _ = make_supervisor(layout, [])
  sup.client = type('Client', (), {'snapshot': lambda self: {
    'connected': True, 'loggedIn': True, 'active': True, 'status': 'playing', 'position': None,
    'track': {'title': 'Song', 'artists': 'Band', 'album': '', 'durationMs': 1000}}})()
  assert sup.status()['nowPlaying']['title'] == 'Song'
  sup.client = None
  assert sup.status()['nowPlaying'] is None


def test_status_publishes_the_phone_track_only_while_phone_audio_runs(layout):
  sup, _ = make_supervisor(layout, [])
  sup.phone_prefs = object()
  sup.phone_track = {'Status': 'playing', 'Track': {'Title': 'Phone Song', 'Artist': 'Band'}}
  assert sup.status()['nowPlaying'] is None
  sup.player = object()
  found = sup.status()['nowPlaying']
  assert found['source'] == 'phone' and found['title'] == 'Phone Song'


def test_supervisor_waits_for_network_before_starting(layout, monkeypatch):
  ready_layout(layout, monkeypatch)
  spawned = []
  sup, _ = make_supervisor(layout, spawned)
  up = [False]
  sup.network = lambda: up[0]
  sup.tick()
  assert spawned == []
  assert sup.status()['state'] == 'waiting_network'
  assert sup.fast_failures == 0
  up[0] = True
  sup.tick()
  assert [Path(p.argv[0]).name for p in spawned] == ['pulseaudio', 'soloist']


def test_changing_mute_values_never_restarts_the_player(layout, monkeypatch):
  from dataclasses import replace
  ready_layout(layout, monkeypatch)
  spawned = []
  sup, now = make_supervisor(layout, spawned)
  sup.tick()
  assert len(spawned) == 2
  preferences.save(replace(preferences.load(layout), pause_mute=1.2, skip_mute=0.0), layout)
  assert preferences.load(layout).pause_mute == 1.2
  now[0] += 1
  sup.tick()
  assert len(spawned) == 2 and not sup.client.stopped and all(p.poll() is None for p in spawned)
  preferences.save(replace(preferences.load(layout), cache_mb=512), layout)  # a real change still restarts
  sup.tick()
  assert len(spawned) == 4


def test_changing_mute_values_keeps_phone_audio_running(layout, monkeypatch):
  from dataclasses import replace
  ready_layout(layout, monkeypatch)
  preferences.save(preferences.Preferences(output='test', source='phone', phone_address='AA:BB:CC:DD:EE:FF'), layout)
  spawned = []
  sup, now = make_supervisor(layout, spawned)
  sup.phone.powered = lambda: True
  sup.phone.ready = lambda *_a: True
  sup.phone.track = lambda _a: None
  monkeypatch.setattr(supervisor.phone_audio, 'profile', lambda *_a: None)
  sup.tick()
  assert [Path(p.argv[0]).name for p in spawned][0] == 'pulseaudio' and len(spawned) == 2
  preferences.save(replace(preferences.load(layout), pause_mute=1.5, skip_mute=0.05), layout)
  now[0] += 1
  sup.tick()
  assert len(spawned) == 2 and all(p.poll() is None for p in spawned)
  preferences.save(replace(preferences.load(layout), cache_mb=512), layout)
  sup.tick()
  assert len(spawned) == 4


@pytest.mark.parametrize(('ipv4', 'ipv6', 'expected'), [
  ('', '', False),
  ('Iface Destination Gateway Flags', '', False),
  ('wlan0 00000000 0100000A 0003', '', True),
  ('', '0' * 32 + ' 00 ' + '0' * 32 + ' 00 ' + '0' * 32 + ' 00000001 00000000 00000000 00000003 wlan0', True),
  ('', '0' * 32 + ' 00 ' + '0' * 32 + ' 00 ' + '0' * 32 + ' 00000001 00000000 00000000 00200200 lo', False),
  ('wlan0 00000000 0100000A 0201', '', False),
  ('lo 00000000 00000000 0001', '', False),
])
def test_network_ready_accepts_either_address_family(monkeypatch, ipv4, ipv6, expected):
  from io import StringIO
  monkeypatch.setattr('builtins.open', lambda path: StringIO(ipv4 if path.endswith('/route') else ipv6))
  assert supervisor.network_ready() is expected
