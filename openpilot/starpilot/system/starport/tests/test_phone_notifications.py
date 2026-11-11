import json
import stat
from types import SimpleNamespace

import pytest

from openpilot.starpilot.system.starport.phone_notifications import (MAX_APPS, TEST_TEXT, TEST_TITLE, TOAST_NS, PhoneNotifications, current,
                                                                     read_settings, validate_payload)

NOW = 500_000_000_000
SECRET_SENDER, SECRET_TEXT = 'Alice Secret', 'meet at the hidden place'


@pytest.fixture
def owner(tmp_path):
  clock = [NOW]
  boot = ['boot-a']
  offset = [0]
  timers = []

  class Timer:
    def __init__(self, delay, function, args):
      self.delay, self.function, self.args, self.daemon = delay, function, args, False
      timers.append(self)

    def start(self):
      assert self.daemon

  publisher = PhoneNotifications(tmp_path / 'data' / 'phone_notifications.json', tmp_path / 'shm.json', lambda: clock[0],
                                 boot_id=lambda: boot[0], boot_offset=lambda: offset[0], timer=Timer)
  publisher.clock_value, publisher.boot_value, publisher.offset_value, publisher.timers = clock, boot, offset, timers
  return publisher


def note(**values):
  return {'source': 'android', 'app': 'com.whatsapp', 'appName': 'WhatsApp', 'sender': SECRET_SENDER, 'text': SECRET_TEXT,
          'category': 'message', 'ageMs': 0, **values}


def read_current(owner, now=None):
  return current(NOW if now is None else now, owner.handoff, boot_id=lambda: owner.boot_value[0], boot_offset=lambda: owner.offset_value[0])


@pytest.mark.parametrize('bad', [
  {'extra': 1}, {'source': 'web'}, {'app': ''}, {'app': 'a' * 129}, {'app': 'bad id'}, {'appName': 'x' * 81},
  {'sender': 'x' * 121}, {'text': 'x' * 501}, {'sender': ' ', 'text': ''}, {'category': 'alarm'}, {'ageMs': 60001},
  {'ageMs': -1}, {'ageMs': True}, {'ageMs': float('nan')}, {'ageMs': '0'}, {'text': 5},
])
def test_validation_rejects_bad_payloads(bad):
  with pytest.raises(ValueError) as error:
    validate_payload(note(**bad))
  assert SECRET_SENDER not in str(error.value) and SECRET_TEXT not in str(error.value)


def test_validation_requires_core_fields():
  payload = note()
  del payload['category']
  with pytest.raises(ValueError):
    validate_payload(payload)
  assert validate_payload(note(sender='', appName='', ageMs=59_999.5))
  minimal = {key: note()[key] for key in ('source', 'app', 'category', 'ageMs')}
  with pytest.raises(ValueError):
    validate_payload(minimal)  # sender and text both blank
  assert validate_payload({**minimal, 'text': 'hi'})


def test_default_filter_shows_messages_only_and_records_apps(owner):
  assert owner.publish(note()) == {'ok': True, 'accepted': True, 'enabled': True}
  shown = read_current(owner)
  assert shown['sender'] == SECRET_SENDER and shown['text'] == SECRET_TEXT and shown['appName'] == 'WhatsApp'
  assert owner.publish(note(app='com.google.android.gm', appName='Gmail', category='email'))['accepted'] is False
  assert read_current(owner)['app'] == 'com.whatsapp'
  apps = {row['id']: row for row in owner.snapshot()['apps']}
  assert apps['com.whatsapp'] == {'id': 'com.whatsapp', 'name': 'WhatsApp', 'source': 'android', 'messaging': True,
                                  'allowed': True, 'chosen': False}
  assert apps['com.google.android.gm']['allowed'] is False and not apps['com.google.android.gm']['chosen']
  assert [row['name'] for row in owner.snapshot()['apps']] == ['Gmail', 'WhatsApp']


def test_explicit_allow_and_block(owner):
  owner.publish(note(app='com.google.android.gm', appName='Gmail', category='email'))
  owner.publish(note())
  snapshot = owner.update({'apps': {'com.google.android.gm': True, 'com.whatsapp': False}})
  assert {row['id']: (row['allowed'], row['chosen']) for row in snapshot['apps']} == {
    'com.google.android.gm': (True, True), 'com.whatsapp': (False, True)}
  assert owner.publish(note())['accepted'] is False
  assert owner.publish(note(app='com.google.android.gm', category='email'))['accepted'] is True
  assert read_current(owner)['app'] == 'com.google.android.gm'
  snapshot = owner.update({'apps': {'com.whatsapp': None}})
  assert {row['id']: row['chosen'] for row in snapshot['apps']}['com.whatsapp'] is False
  assert owner.publish(note())['accepted'] is True
  with pytest.raises(ValueError):
    owner.update({'apps': {'com.unknown': True}})
  for bad in ({}, {'enabled': 1}, {'hideWhileMoving': None}, {'apps': []}, {'apps': {'com.whatsapp': 'yes'}}, {'other': True}):
    with pytest.raises(ValueError):
      owner.update(bad)


def test_disabled_accepts_nothing_and_clears_the_handoff(owner):
  owner.publish(note())
  assert owner.update({'enabled': False, 'hideWhileMoving': True})['hideWhileMoving'] is True
  assert SECRET_TEXT not in owner.handoff.read_text() and SECRET_SENDER not in owner.handoff.read_text()
  assert owner.publish(note()) == {'ok': True, 'accepted': False, 'enabled': False}
  assert read_current(owner) is None
  assert owner.update({'enabled': True})['enabled'] is True
  assert owner.publish(note())['accepted'] is True
  assert read_current(owner)['sequence'] == 2  # the sequence survives clearing


def test_content_is_cleared_after_the_toast_window(owner):
  owner.publish(note())
  first = owner.timers[-1]
  assert first.delay > TOAST_NS / 1e9
  owner.publish(note(text='second'))
  first.function(*first.args)  # an older timer leaves the newer toast alone
  assert read_current(owner)['text'] == 'second'
  last = owner.timers[-1]
  last.function(*last.args)
  saved = owner.handoff.read_text()
  assert 'second' not in saved and SECRET_SENDER not in saved
  assert read_current(owner) is None
  assert owner.publish(note())['accepted'] and read_current(owner)['sequence'] == 3


def test_blank_app_name_falls_back_to_a_short_id(owner):
  app = 'a' * 100
  owner.publish(note(app=app, appName=''))
  inode = owner.settings_path.stat().st_ino
  owner.publish(note(app=app, appName=''))
  assert owner.settings_path.stat().st_ino == inode
  row = owner.snapshot()['apps'][0]
  assert row['id'] == app and row['name'] == app[:80]
  assert owner.update({'apps': {app: False}})['apps'][0]['chosen']


def test_eviction_keeps_user_choices(owner):
  owner.publish(note(app='app.chosen', appName='Chosen'))
  owner.update({'apps': {'app.chosen': False}})
  for index in range(MAX_APPS - 1):
    owner.publish(note(app=f'app.n{index}', appName=f'N{index}'))
  assert len(owner.snapshot()['apps']) == MAX_APPS
  owner.publish(note(app='app.new', appName='New'))
  ids = {row['id'] for row in owner.snapshot()['apps']}
  assert len(ids) == MAX_APPS and 'app.chosen' in ids and 'app.new' in ids and 'app.n0' not in ids and 'app.n1' in ids


def test_settings_written_only_on_change(owner):
  owner.publish(note())
  inode = owner.settings_path.stat().st_ino  # atomic replacement would change it
  owner.publish(note(text='another'))
  owner.publish(note(category='call'))  # still a messaging app, nothing changes
  assert owner.settings_path.stat().st_ino == inode
  owner.publish(note(appName='WhatsApp Business'))
  assert owner.settings_path.stat().st_ino != inode


def test_expiry_and_old_observations(owner):
  owner.publish(note(ageMs=2000))
  assert read_current(owner, NOW)['sequence'] == 1
  assert read_current(owner, NOW - 2_000_000_000 + TOAST_NS)
  assert read_current(owner, NOW - 2_000_000_000 + TOAST_NS + 1) is None
  assert owner.publish(note(ageMs=TOAST_NS / 1e6))['accepted'] is False
  assert read_current(owner, NOW)['sequence'] == 1
  owner.publish(note(text='newer'))
  assert read_current(owner)['sequence'] == 2 and read_current(owner)['text'] == 'newer'


def test_reboot_or_suspend_makes_the_handoff_stale(owner):
  owner.publish(note())
  assert read_current(owner)
  owner.boot_value[0] = 'boot-b'
  assert read_current(owner) is None
  owner.boot_value[0] = 'boot-a'
  owner.offset_value[0] = 5_000_000_000
  assert read_current(owner) is None


def test_sequence_survives_a_restart(owner, tmp_path):
  owner.publish(note())
  owner.publish(note())
  again = PhoneNotifications(owner.settings_path, owner.handoff, lambda: NOW, boot_id=lambda: 'boot-a', boot_offset=lambda: 0,
                             timer=lambda *a, **k: SimpleNamespace(start=lambda: None))
  again.publish(note())
  assert read_current(owner)['sequence'] == 3


def test_files_are_private_and_settings_never_hold_content(owner):
  owner.publish(note())
  owner.publish(note(app='com.google.android.gm', appName='Gmail', category='email'))
  owner.update({'hideWhileMoving': True, 'apps': {'com.google.android.gm': True}})
  owner.publish(note(app='com.google.android.gm', category='email'))
  saved = owner.settings_path.read_text()
  assert SECRET_SENDER not in saved and SECRET_TEXT not in saved
  assert set(json.loads(saved)) == {'enabled', 'hideWhileMoving', 'apps'}
  for entry in json.loads(saved)['apps'].values():
    assert set(entry) <= {'name', 'source', 'messaging', 'allowed'}
  assert stat.S_IMODE(owner.settings_path.stat().st_mode) == 0o600
  assert stat.S_IMODE(owner.handoff.stat().st_mode) == 0o600
  assert stat.S_IMODE(owner.settings_path.parent.stat().st_mode) == 0o700


def test_corrupt_settings_fall_back_to_defaults(tmp_path):
  path = tmp_path / 'settings.json'
  path.write_text('{"enabled": "yes", "apps": {"bad id": {"name": "x", "source": "android", "messaging": true}}}')
  assert read_settings(path) == {'enabled': True, 'hideWhileMoving': False, 'apps': {}}
  path.write_text('not json')
  assert read_settings(path)['enabled'] is True


@pytest.mark.parametrize('source,app', [('ios', 'link.galaxy.bluetooth.dannyirwin'), ('ios', 'link.galaxy.bluetooth.natehuby76'),
                                        ('android', 'link.firestar.companion'), ('android', 'com.starpilot.galaxy')])
def test_companion_test_notification_shows_fixed_text_and_skips_the_app_list(owner, source, app):
  result = owner.publish(note(source=source, app=app, sender=TEST_TITLE, text=SECRET_TEXT, category='other'))
  assert result == {'ok': True, 'accepted': True, 'enabled': True, 'test': True}
  shown = read_current(owner)
  assert (shown['sender'], shown['text']) == (TEST_TITLE, TEST_TEXT[source])
  assert read_settings(owner.settings_path)['apps'] == {}


def test_test_title_from_another_app_is_an_ordinary_notification(owner):
  assert owner.publish(note(app='com.example.spam', sender=TEST_TITLE, category='other'))['accepted'] is False


def test_test_notification_respects_the_main_switch_and_age(owner):
  owner.update({'enabled': False})
  assert owner.publish_test('galaxy') == {'ok': True, 'accepted': False, 'enabled': False, 'test': True}
  assert read_current(owner) is None
  owner.update({'enabled': True})
  assert owner.publish_test('android', age_ms=TOAST_NS / 1e6)['accepted'] is False
  assert owner.publish_test('galaxy')['accepted'] and read_current(owner)['text'] == TEST_TEXT['galaxy']
  with pytest.raises(ValueError):
    owner.publish_test('web')


def test_recent_outcomes_hold_no_content_and_showing_needs_permission(owner):
  owner.publish(note())
  owner.publish(note(app='com.google.android.gm', appName='Gmail', category='email'))
  owner.update({'apps': {'com.whatsapp': False}})
  owner.publish(note())
  owner.update({'apps': {'com.whatsapp': True}})
  owner.publish(note(ageMs=9000))
  owner.publish_test('android')
  owner.clock_value[0] += 3_000_000_000
  hidden = owner.snapshot()
  assert [row['outcome'] for row in hidden['recent']] == ['shown', 'tooOld', 'appOff', 'notMessage', 'shown']
  assert hidden['recent'][0]['appName'] == 'Test from Android' and hidden['recent'][0]['ageSeconds'] == 3
  assert hidden['showing'] == {'appName': 'StarPilot', 'remainingSeconds': 5} and hidden['contentVisible'] is False
  assert SECRET_SENDER not in json.dumps(hidden) and SECRET_TEXT not in json.dumps(hidden)
  shown = owner.snapshot(content=True)
  assert (shown['showing']['sender'], shown['showing']['text']) == (TEST_TITLE, TEST_TEXT['android'])
  owner.update({'enabled': False})
  owner.publish(note())
  after = owner.snapshot(content=True)
  assert after['showing'] is None and after['recent'][0]['outcome'] == 'disabled'
  assert SECRET_TEXT not in json.dumps(after)
