from dataclasses import replace
from pathlib import Path

import pytest

from openpilot.starpilot.soloist import phone_audio, phone_profile, preferences, supervisor
from openpilot.starpilot.soloist.paths import Layout
from openpilot.starpilot.soloist.tests.test_runtime import make_supervisor

PHONE = '11:22:33:44:55:66'
CAR = 'AA:BB:CC:DD:EE:FF'


class Phone:
  on = connected = True
  def __init__(self): self.paused, self.controls, self.pages, self.disconnects = [], [], 0, []
  def powered(self): return self.on
  def ready(self, address, now):
    self.pages += 1
    return self.connected
  def control(self, address, action):
    self.controls.append((address, action))
    return 'pause' if action == 'play_pause' else action
  def disconnect(self, address):
    self.disconnects.append(address)
    was, self.connected = self.connected, False
    return was
  def track(self, address): return None
  def close(self): pass


@pytest.fixture
def music(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'music')
  preferences.save(preferences.Preferences(source='phone', phone_address=PHONE, output='starport'), layout)
  processes, profiles, stopped = [], [], []
  sup, now = make_supervisor(layout, processes)
  sup.phone = Phone()
  monkeypatch.setattr(phone_audio, 'profile', profiles.append)
  monkeypatch.setattr(supervisor, '_kill_group', lambda p: stopped.append(p) if p else None)
  return sup, now, processes, profiles, stopped


def test_phone_without_spotify_package_or_key_and_pause_control(music):
  sup, _, processes, profiles, _ = music
  sup.tick()
  assert profiles == ['acquire']
  assert sup.state == 'phone_ready' and sup.client is None
  assert len(processes) == 2 and processes[0].argv[0].endswith('/pulseaudio')
  assert processes[1].argv[1:3] == ['-m', 'openpilot.starpilot.soloist.phone_audio']
  assert processes[1].kwargs['env']['PULSE_SERVER'].endswith('/pulse/native')
  assert processes[1].kwargs['env']['PYTHONPATH'] == str(Path(supervisor.__file__).resolve().parents[3])
  for action in ('pause', 'next', 'previous', 'play_pause'):
    assert sup.handle({'command': 'control', 'action': action, 'args': {}})['ok']
  assert sup.phone.controls == [(PHONE, 'pause'), (PHONE, 'next'), (PHONE, 'previous'), (PHONE, 'play_pause')]
  assert not sup.handle({'command': 'control', 'action': 'volume', 'args': {'volume': 5}})['ok']
  assert not sup.handle({'command': 'control', 'action': 'next', 'args': {'x': 1}})['ok']
  sup.stop_children()
  assert profiles == ['acquire', 'release']


def test_disabled_radio_and_missing_phone_never_start_profiles(music):
  sup, now, processes, profiles, _ = music
  sup.phone.on = False
  sup.tick()
  assert sup.state == 'waiting_bluetooth' and profiles == [] and processes == []
  preferences.save(replace(preferences.load(sup.layout), phone_address=''), sup.layout)
  now[0] += 20
  sup.tick()
  assert sup.state == 'no_phone' and profiles == []


def test_disconnect_and_worker_failure_retry_without_restarting_bluetooth(music):
  sup, now, processes, profiles, stopped = music
  sup.tick()
  processes[1].code = 1
  sup.tick()
  assert sup.state == 'phone_retrying' and len(stopped) == 2
  sup.tick()
  assert len(processes) == 2
  now[0] += 21
  sup.tick()
  assert len(processes) == 4 and profiles == ['acquire']
  sup.phone.connected = False
  sup.tick()
  assert sup.state == 'waiting_phone' and sup.player is None and sup.server is None
  sup.phone.connected = True
  now[0] += 6
  sup.tick()
  assert sup.state == 'phone_ready'


def test_source_switch_stops_phone_and_restores_profile_even_without_spotify(music):
  sup, _, _, profiles, stopped = music
  sup.tick()
  preferences.save(replace(preferences.load(sup.layout), source='spotify'), sup.layout)
  sup.tick()
  assert sup.state == 'missing_package'
  assert len(stopped) == 2 and profiles == ['acquire', 'release']
  assert sup.phone_prefs is None


def test_profile_failure_is_reported_and_backed_off(music, monkeypatch):
  sup, now, processes, _, _ = music
  calls = []
  def denied(action):
    calls.append(action)
    raise RuntimeError('Permission denied')
  monkeypatch.setattr(phone_audio, 'profile', denied)
  sup.tick()
  assert sup.state == 'phone_retrying' and 'Permission denied' in sup.message and processes == []
  sup.tick()
  assert calls == ['acquire']
  now[0] += 21
  sup.tick()
  assert calls == ['acquire', 'acquire']


def test_phone_settings_migrate_validate_and_prevent_loop(tmp_path):
  layout = Layout(tmp_path)
  layout.config.write_text('{"output": "android_auto"}')
  assert preferences.load(layout).source == 'spotify'
  saved = preferences.save(preferences.Preferences(source='phone', phone_address=PHONE, output='starport'), layout)
  assert preferences.load(layout) == saved
  for change in ({'source': 'bad'}, {'phone_address': 'bad'}, {'output': 'bluetooth', 'output_address': PHONE}):
    with pytest.raises(ValueError):
      preferences.validated({**saved.__dict__, **change})
  text = phone_audio.capture_config(PHONE)
  assert 'type plug' in text and PHONE in text and 'profile "a2dp"' in text
  with pytest.raises(ValueError):
    phone_audio.capture_config('";evil')


def test_profile_override_is_temporary_idempotent_and_restored(tmp_path):
  path = tmp_path / 'bluealsa.service.d' / '90-starpilot-phone.conf'
  calls = []
  def run(*args):
    calls.append(args)
    if args[0] == 'show':
      return '{ argv[]=/usr/bin/bluealsa -p a2dp-source' + (' -p a2dp-sink' if path.exists() else '') + ' ; }'
    return ''
  phone_profile.configure('acquire', path, run)
  assert path.read_text() == phone_profile.CONTENTS
  phone_profile.configure('acquire', path, run)
  assert calls.count(('restart', 'bluealsa.service')) == 1
  phone_profile.configure('release', path, run)
  assert not path.exists() and calls[-1] == ('try-restart', 'bluealsa.service')
  count = len(calls)
  phone_profile.configure('release', path, run)
  assert len(calls) == count


def test_profile_rejects_unknown_service_and_rolls_back_failed_start(tmp_path):
  path = tmp_path / 'override.conf'
  with pytest.raises(RuntimeError, match='Unsupported'):
    phone_profile.configure('acquire', path, lambda *a: '{ argv[]=/custom/bluealsa -p hfp-ag ; }')
  assert not path.exists()
  calls = []
  def run(*args):
    calls.append(args)
    if args[0] == 'show':
      return '{ argv[]=/usr/bin/bluealsa -p a2dp-source ; }'
    if args[0] == 'restart':
      raise RuntimeError('failed')
    return ''
  with pytest.raises(RuntimeError, match='failed'):
    phone_profile.configure('acquire', path, run)
  assert not path.exists() and calls[-1] == ('try-restart', 'bluealsa.service')
  path.write_text('someone else owns this')
  with pytest.raises(RuntimeError, match='externally'):
    phone_profile.configure('release', path, run)
  assert path.read_text() == 'someone else owns this'


def test_phone_connects_only_paired_selected_audio_source_and_pauses_its_player():
  from openpilot.starpilot.bluetooth.owner import ADAPTER, DEVICE
  path = '/org/bluez/hci0/dev_11_22_33_44_55_66'
  props = {'Address': PHONE, 'Paired': True, 'Connected': True, 'UUIDs': [phone_audio.A2DP_SOURCE]}
  objects: dict = {'/org/bluez/hci0': {ADAPTER: {'Powered': True}}, path: {DEVICE: props},
             path + '/player0': {phone_audio.MEDIA_PLAYER: {}}, '/other/player0': {phone_audio.MEDIA_PLAYER: {}}}
  class BlueZ:
    def __init__(self): self.calls = []
    def _objects(self): return objects
    def _call(self, *args, **kwargs): self.calls.append(args)
    def close(self): pass
  phone = phone_audio.PhoneInput()
  phone.client = BlueZ()  # ty: ignore[invalid-assignment]  # test double
  assert phone.powered() and phone.ready(PHONE, 100)
  assert phone.client.calls == [(path, DEVICE, 'ConnectProfile', 's', (phone_audio.A2DP_SOURCE,))]
  phone.ready(PHONE, 101)
  assert len(phone.client.calls) == 1
  transport_path = path + '/fd0'
  for state in ('pending', 'active'):
    objects[transport_path] = {phone_audio.MEDIA_TRANSPORT: {'Device': path, 'State': state}}
    assert phone.ready(PHONE, 200)
    assert len(phone.client.calls) == 1
  # Another device's stream must not suppress recovery of this phone's A2DP.
  objects[transport_path][phone_audio.MEDIA_TRANSPORT]['Device'] = '/other'
  assert phone.ready(PHONE, 200)
  assert len(phone.client.calls) == 2
  del objects[transport_path]
  assert phone.ready(PHONE, 221)  # transport disappeared: normal retry resumes
  assert len(phone.client.calls) == 3
  phone.pause(PHONE)
  assert phone.client.calls[-1] == (path + '/player0', phone_audio.MEDIA_PLAYER, 'Pause')
  phone.control(PHONE, 'next')
  phone.control(PHONE, 'previous')
  assert [call[2] for call in phone.client.calls[-2:]] == ['Next', 'Previous']
  phone.control(PHONE, 'play_pause')
  assert phone.client.calls[-1][2] == 'Play'
  objects[path + '/player0'][phone_audio.MEDIA_PLAYER]['Status'] = 'playing'
  phone.control(PHONE, 'play_pause')
  assert phone.client.calls[-1][2] == 'Pause'
  with pytest.raises(ValueError):
    phone.control(PHONE, 'eject')
  props['Paired'] = False
  assert not phone.ready(PHONE, 200)
  props['Paired'] = True
  props['UUIDs'] = []
  with pytest.raises(RuntimeError, match='does not offer'):
    phone.ready(PHONE, 200)


@pytest.mark.parametrize('failure', ['', 'frame', 'output'])
def test_relay_closes_capture_and_output_on_disconnect_or_bad_audio(tmp_path, monkeypatch, failure):
  import io
  writes, closed = [], []
  class Capture:
    returncode = 0
    stdout = io.BytesIO(b'bad' if failure == 'frame' else b'\x01\x00\x02\x00' * 1000)
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def terminate(self): closed.append('capture')
    def wait(self, timeout): return self.returncode
  class Writer:
    def __init__(self, server): assert server == 'unix:/private/socket'
    def write(self, data):
      if failure == 'output':
        raise RuntimeError('output closed')
      writes.append(data)
    def close(self): closed.append('output')
  calls = []
  def spawn(argv, **kwargs):
    calls.append((argv, kwargs))
    return Capture()
  monkeypatch.setattr(phone_audio, 'PulseWriter', Writer)
  monkeypatch.setattr(phone_audio.subprocess, 'Popen', spawn)
  if failure:
    with pytest.raises(RuntimeError):
      phone_audio.relay(PHONE, 'unix:/private/socket', tmp_path / 'alsa.conf')
  else:
    phone_audio.relay(PHONE, 'unix:/private/socket', tmp_path / 'alsa.conf')
    assert b''.join(writes) == b'\x01\x00\x02\x00' * 1000
  assert closed == ['capture', 'output']
  assert calls[0][0][0] == '/usr/bin/arecord'
  assert calls[0][1]['env']['ALSA_CONFIG_PATH'] == str(tmp_path / 'alsa.conf')


def test_car_controls_flush_queued_phone_audio_only_for_pause_and_skip(music, monkeypatch):
  import signal
  sup, now, processes, profiles, stopped = music
  sup.tick()
  player = next(p for p in processes if 'phone_audio' in ' '.join(p.argv))
  sent = []
  monkeypatch.setattr(supervisor.os, 'kill', lambda pid, sig: sent.append((pid, sig)))
  for action in ('play', 'pause', 'next', 'previous', 'play_pause'):
    assert sup.handle({'command': 'control', 'action': action, 'args': {}})['ok']
  assert sent == [(player.pid, signal.SIGUSR1), (player.pid, signal.SIGUSR2), (player.pid, signal.SIGUSR2),
                  (player.pid, signal.SIGUSR1)]
  player.code = 1  # a dead worker is never signaled
  sent.clear()
  assert sup.handle({'command': 'control', 'action': 'pause', 'args': {}})['ok']
  assert sent == []


def test_relay_flushes_and_mutes_phone_audio_after_a_flush_request(tmp_path, monkeypatch):
  import io
  events = []
  def chunk(n):
    return bytes([n]) * 3840
  class Capture:
    returncode = 0
    stdout = io.BytesIO(b''.join(chunk(n) for n in range(1, 7)))
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def terminate(self): pass
    def wait(self, timeout): return 0
  class Writer:
    def __init__(self, server): pass
    def write(self, data): events.append(('write', data[0]))
    def flush(self): events.append(('flush',))
    def close(self): pass
  clock = [100.0]
  real_read = Capture.stdout.read
  def read(n):
    data = real_read(n)
    clock[0] += 0.1  # 100 ms per chunk
    if data and data[0] == 3:
      phone_audio.request_flush('skip')  # the car pressed next while chunk 3 was in flight
    return data
  monkeypatch.setattr(Capture.stdout, 'read', read)
  monkeypatch.setattr(phone_audio, 'PulseWriter', Writer)
  monkeypatch.setattr(phone_audio.subprocess, 'Popen', lambda *a, **k: Capture())
  monkeypatch.setattr(phone_audio.time, 'monotonic', lambda: clock[0])
  phone_audio.FlushRequest.kind = None
  monkeypatch.setenv('STARPILOT_SOLOIST_CONFIG', str(tmp_path / 'missing.json'))
  phone_audio.relay(PHONE, 'unix:/private/socket', tmp_path / 'alsa.conf')
  # Chunks 1-2 play. The request arrives with chunk 3: flush, then 3-5 fall inside the 250 ms mute and 6 plays.
  assert events == [('write', 1), ('write', 2), ('flush',), ('write', 6)]


def test_mute_windows_start_at_defaults_and_follow_the_tuning_file(tmp_path):
  path = tmp_path / 'tuning.json'
  assert phone_audio.mute_seconds('pause', path) == 0.5 and phone_audio.mute_seconds('skip', path) == 0.25
  path.write_text('{"pause_mute": 0.1, "skip_mute": 0}')
  assert phone_audio.mute_seconds('pause', path) == 0.1 and phone_audio.mute_seconds('skip', path) == 0.0
  for bad in ('not json', '{"pause_mute": "x"}', '{"pause_mute": -1}', '{"pause_mute": 99}', '[]'):
    path.write_text(bad)
    assert phone_audio.mute_seconds('pause', path) == 0.5
  path.write_text('{"pause_mute": 2}')
  assert phone_audio.mute_seconds('pause', path) == 0.5  # above the 1.5 s ceiling


def test_pause_flush_request_outranks_a_skip():
  phone_audio.FlushRequest.kind = None
  phone_audio.request_flush('skip')
  phone_audio.request_flush('pause')
  phone_audio.request_flush('skip')
  assert phone_audio.FlushRequest.kind == 'pause'
  phone_audio.FlushRequest.kind = None


def test_profile_upgrades_a_legacy_override_to_raise_bluealsa_priority(tmp_path):
  path = tmp_path / 'bluealsa.service.d' / '90-starpilot-phone.conf'
  path.parent.mkdir()
  path.write_text(phone_profile.LEGACY_CONTENTS)
  calls = []
  phone_profile.configure('acquire', path, lambda *args: calls.append(args) or '')
  assert 'Nice=-5' in path.read_text() and ('restart', 'bluealsa.service') in calls
  phone_profile.configure('release', path, lambda *args: '')
  assert not path.exists()


def test_profile_changes_touch_only_bluealsa_never_bluetooth_or_the_phone_bridge(tmp_path):
  path = tmp_path / 'bluealsa.service.d' / '90-starpilot-phone.conf'
  calls = []
  def run(*args):
    calls.append(args)
    if args[0] == 'show':
      return '{ argv[]=/usr/bin/bluealsa -p a2dp-source' + (' -p a2dp-sink' if path.exists() else '') + ' ; }'
    return ''
  for action in ('acquire', 'acquire', 'release'):
    phone_profile.configure(action, path, run)
  units = {arg for call in calls for arg in call[1:] if arg.endswith('.service')}
  assert units == {'bluealsa.service'}
  assert {call[0] for call in calls} <= {'show', 'daemon-reload', 'restart', 'start', 'try-restart'}


def test_pairing_window_advertises_the_receiver_then_releases_it_unless_phone_is_the_source(music):
  sup, now, _, profiles, _ = music
  preferences.save(preferences.Preferences(), sup.layout)
  assert sup.handle({'command': 'prepare_phone_pairing'}) == {'ok': True}
  assert profiles == ['acquire']
  assert sup.handle({'command': 'prepare_phone_pairing'}) == {'ok': True}
  assert profiles == ['acquire']
  now[0] += supervisor.PAIRING_PROFILE_SECONDS - 1
  sup.tick()
  assert profiles == ['acquire']
  now[0] += 100
  sup.tick()
  assert profiles == ['acquire', 'release'] and not sup.phone_profile
  preferences.save(preferences.Preferences(source='phone', phone_address=PHONE, output='starport'), sup.layout)
  sup.handle({'command': 'prepare_phone_pairing'})
  now[0] += 1000
  sup.tick()
  assert profiles[-1] == 'acquire' and sup.phone_profile


def test_phone_audio_waits_for_ignition_then_pages_at_once(music):
  sup, now, processes, profiles, _ = music
  car_off = [True]
  sup.parked = lambda: car_off[0]
  for _ in range(5):  # started while parked: no grace, nothing advertised or paged
    sup.tick()
    now[0] += 60
  assert sup.state == 'parked' and 'resumes when the car is on' in sup.message
  assert profiles == [] and processes == [] and sup.phone.pages == 0
  car_off[0] = False
  sup.phone.connected = True  # the phone answers the page
  sup.tick()
  assert sup.state == 'phone_ready' and profiles == ['acquire'] and len(processes) == 2


def test_ignition_off_keeps_audio_through_the_grace_then_drops_the_phone_once(music):
  sup, now, processes, profiles, stopped = music
  car_off = [False]
  sup.parked = lambda: car_off[0]
  sup.tick()
  assert sup.state == 'phone_ready'
  car_off[0] = True
  sup.tick()  # the grace counts from the first tick that sees ignition off
  now[0] += supervisor.PHONE_PARKED_GRACE_SECONDS - 1
  sup.tick()
  assert sup.state == 'phone_ready' and sup.phone.disconnects == [] and stopped == []
  now[0] += 1
  sup.tick()
  assert sup.state == 'parked' and sup.phone.disconnects == [PHONE] and len(stopped) == 2
  assert profiles == ['acquire', 'release'] and sup.player is None and sup.server is None
  assert any('disconnected' in line and 'car off' in line for line in sup.log)
  pages = sup.phone.pages
  for _ in range(10):
    now[0] += 60
    sup.tick()
  assert sup.phone.disconnects == [PHONE] and sup.phone.pages == pages and profiles == ['acquire', 'release']
  # Ignition on again: the sink is advertised and the phone paged on the very next tick.
  car_off[0] = False
  sup.phone.connected = True
  sup.tick()
  assert sup.state == 'phone_ready' and profiles == ['acquire', 'release', 'acquire'] and len(processes) == 4


def test_a_short_stop_keeps_the_phone_audio(music):
  sup, now, _, profiles, stopped = music
  car_off = [False]
  sup.parked = lambda: car_off[0]
  sup.tick()
  car_off[0] = True
  now[0] += 20
  sup.tick()
  car_off[0] = False
  now[0] += 1
  sup.tick()
  assert sup.state == 'phone_ready' and stopped == [] and sup.phone.disconnects == [] and profiles == ['acquire']


def test_parked_phone_keeps_the_receiver_advertised_during_a_pairing_window(music):
  sup, now, _, profiles, _ = music
  sup.parked = lambda: True
  sup.tick()
  assert profiles == []
  assert sup.handle({'command': 'prepare_phone_pairing'}) == {'ok': True}
  sup.tick()
  assert profiles == ['acquire'] and sup.phone_profile
  now[0] += supervisor.PAIRING_PROFILE_SECONDS - 1
  sup.tick()
  assert profiles == ['acquire'] and sup.phone_profile
  now[0] += 2
  sup.tick()
  assert profiles == ['acquire', 'release'] and not sup.phone_profile and sup.state == 'parked'


def test_phone_disconnect_drops_only_the_audio_profile_and_never_raises():
  from openpilot.starpilot.bluetooth.owner import ADAPTER, DEVICE
  path = '/org/bluez/hci0/dev_11_22_33_44_55_66'
  props = {'Address': PHONE, 'Paired': True, 'Connected': True, 'UUIDs': [phone_audio.A2DP_SOURCE]}
  objects: dict = {'/org/bluez/hci0': {ADAPTER: {'Powered': True}}, path: {DEVICE: props}}
  class BlueZ:
    def __init__(self): self.calls = []
    def _objects(self): return objects
    def _call(self, *args, **kwargs): self.calls.append(args)
    def close(self): pass
  phone = phone_audio.PhoneInput()
  phone.client = BlueZ()  # ty: ignore[invalid-assignment]  # test double
  assert phone.disconnect(PHONE)
  assert phone.client.calls == [(path, DEVICE, 'DisconnectProfile', 's', (phone_audio.A2DP_SOURCE,))]
  props['Connected'] = False
  assert not phone.disconnect(PHONE) and len(phone.client.calls) == 1
  phone.client = None
  assert not phone.disconnect(PHONE)  # Bluetooth unreachable: a parked comma has nothing to drop


def test_pairing_window_failure_is_reported_without_holding_the_profile(music, monkeypatch):
  sup, _, _, _, _ = music
  def fail(_action):
    raise RuntimeError('Unsupported BlueALSA service configuration')
  monkeypatch.setattr(phone_audio, 'profile', fail)
  result = sup.handle({'command': 'prepare_phone_pairing'})
  assert result['ok'] is False and 'Unsupported' in result['error'] and not sup.phone_profile
