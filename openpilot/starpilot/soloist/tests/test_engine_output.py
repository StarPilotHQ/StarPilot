import subprocess

import pytest

from openpilot.starpilot.soloist import phone_audio, preferences, supervisor
from openpilot.starpilot.soloist.paths import Layout
from openpilot.starpilot.soloist.tests.test_runtime import make_supervisor, ready_layout

PHONE = '11:22:33:44:55:66'
CAR = 'AA:BB:CC:DD:EE:FF'


class Phone:
  on = True
  connected = False
  def powered(self): return self.on
  def ready(self, address, now): return self.connected
  def track(self, address): return None
  def close(self): pass


class Speaker:
  connected = False
  def ready(self, address): return self.connected


@pytest.fixture
def held(tmp_path, monkeypatch):
  layout = Layout(tmp_path / 'music')
  processes, profiles, stopped = [], [], []
  sup, now = make_supervisor(layout, processes)
  sup.engine = lambda: True
  sup.phone, sup.output = Phone(), Speaker()
  monkeypatch.setattr(supervisor.backend, 'bundle_ready', lambda: True)
  monkeypatch.setattr(phone_audio, 'profile', profiles.append)
  monkeypatch.setattr(supervisor, '_kill_group', lambda p: stopped.append(p) if p else None)
  return sup, now, processes, profiles, stopped


def servers(processes):
  return [p for p in processes if p.argv[0].endswith('/pulseaudio')]


def test_engine_alone_opens_the_output_and_never_starts_a_disabled_music_player(held, monkeypatch):
  sup, now, processes, _, _ = held
  ready_layout(sup.layout, monkeypatch)
  preferences.save(preferences.Preferences(output='starport'), sup.layout)
  for _ in range(3):
    sup.tick()
    now[0] += 30
  assert sup.state == 'engine_only'
  assert len(processes) == 1 and 'module-pipe-sink' in ' '.join(processes[0].argv)
  assert sup.player is None and sup.client is None


def test_output_survives_a_missing_phone_and_the_player_joins_the_same_server(held):
  sup, now, processes, _, stopped = held
  preferences.set_enabled(True, sup.layout)
  preferences.save(preferences.Preferences(source='phone', phone_address=PHONE, output='starport'), sup.layout)
  for _ in range(3):
    sup.tick()
    now[0] += 30
  assert sup.state == 'waiting_phone'
  assert len(servers(processes)) == 1 and stopped == []
  sup.phone.connected = True
  sup.tick()
  assert sup.state == 'phone_ready'
  assert len(servers(processes)) == 1 and len(processes) == 2


def test_bluetooth_output_follows_the_speaker_and_keeps_the_profile_until_engine_is_off(held):
  sup, now, processes, profiles, stopped = held
  preferences.save(preferences.Preferences(output='bluetooth', output_address=CAR), sup.layout)
  sup.tick()
  assert profiles == ['acquire']
  assert 'sink_name=spotify_silent' in ' '.join(processes[-1].argv)
  now[0] += 5
  sup.output.connected = True
  sup.tick()
  assert len(processes) == 1
  now[0] += supervisor.OUTPUT_RETRY_SECONDS
  sup.tick()
  assert len(processes) == 2 and stopped == [processes[0]]
  assert 'module-alsa-sink' in ' '.join(processes[-1].argv)
  sup.stop_children()
  assert profiles == ['acquire'] and sup.server is processes[-1]

  sup.engine = lambda: False
  sup.tick()
  assert sup.server is None and stopped[-1] is processes[-1]
  sup.stop_children()
  assert profiles == ['acquire', 'release']


def test_no_output_chosen_starts_nothing(held):
  sup, _, processes, profiles, _ = held
  sup.tick()
  assert processes == [] and profiles == []


def test_server_without_a_sink_gets_the_car_output_loaded_once_the_car_is_there(held, monkeypatch):
  sup, now, processes, _, _ = held
  preferences.save(preferences.Preferences(output='bluetooth', output_address=CAR), sup.layout)
  sup.output.connected = True
  sinks, switched = [False], []
  monkeypatch.setattr(supervisor.backend, 'can_switch', lambda: True)
  monkeypatch.setattr(supervisor.backend, 'has_sink', lambda layout: sinks[0])
  monkeypatch.setattr(supervisor.backend, 'switch_output', lambda layout, speaker: (switched.append(speaker), sinks.__setitem__(0, True)))
  sup.tick()
  assert 'module-alsa-sink' in ' '.join(processes[-1].argv) and switched == []
  now[0] += supervisor.SINK_CHECK_SECONDS - 1
  sup.tick()
  assert switched == []
  now[0] += 2
  sup.tick()
  assert switched == [True]
  for _ in range(3):
    now[0] += supervisor.SINK_CHECK_SECONDS
    sup.tick()
  assert switched == [True]


def test_no_healing_while_the_car_is_away_or_when_the_check_fails(held, monkeypatch):
  sup, now, _, _, _ = held
  preferences.save(preferences.Preferences(output='bluetooth', output_address=CAR), sup.layout)
  calls = []
  monkeypatch.setattr(supervisor.backend, 'can_switch', lambda: True)
  monkeypatch.setattr(supervisor.backend, 'has_sink', lambda layout: calls.append('check') or False)
  monkeypatch.setattr(supervisor.backend, 'switch_output', lambda layout, speaker: calls.append('switch'))
  for _ in range(3):
    sup.tick()
    now[0] += supervisor.SINK_CHECK_SECONDS + 1
  assert calls == []

  def failing(layout, speaker):
    raise subprocess.CalledProcessError(1, 'pactl')
  sup.output.connected = True
  monkeypatch.setattr(supervisor.backend, 'switch_output', failing)
  for _ in range(3):
    now[0] += supervisor.OUTPUT_RETRY_SECONDS + 1
    sup.tick()
  assert any('could not load the car output yet' in line for line in sup.log)
