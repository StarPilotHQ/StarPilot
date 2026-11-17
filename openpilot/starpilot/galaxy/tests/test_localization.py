from importlib.resources import as_file
from pathlib import Path

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.galaxy.localization import language_snapshot
from openpilot.starpilot.ui.translation_strings import MESSAGES


@pytest.mark.parametrize('code', ['ja', 'ko', 'es', 'fr', 'de', 'vi'])
def test_shared_catalog_and_persistent_language(tmp_path, code):
  params = Params(str(tmp_path))
  params.put('LanguageSetting', code, block=True)
  before = Path(params.get_param_path('LanguageSetting')).read_bytes()
  snapshot = language_snapshot(params)
  assert snapshot['language'] == code
  assert snapshot['messages']['Home'] != 'Home'
  assert snapshot['messages']['Language'] != 'Language'
  assert language_snapshot(params, code) == {'language': code}
  assert Path(params.get_param_path('LanguageSetting')).read_bytes() == before


def test_unknown_language_and_english_fallback(tmp_path):
  params = Params(str(tmp_path))
  for code in ['en', 'unknown', '../../etc/passwd']:
    params.put('LanguageSetting', code, block=True)
    assert language_snapshot(params) == {'language': 'en', 'messages': {}}
  params.put('LanguageSetting', 'main_ja', block=True)
  assert language_snapshot(params)['language'] == 'ja'


def test_shared_messages_survive_standard_catalog_extraction():
  from openpilot.selfdrive.ui.translations.potools import extract_strings
  from openpilot.common.basedir import BASEDIR
  entries = extract_strings(['openpilot/starpilot/ui/translation_strings.py'], BASEDIR)
  assert {entry.msgid for entry in entries} == set(MESSAGES)


def test_http_catalog_requires_existing_session(tmp_path, monkeypatch):
  import http.client
  import json
  import threading
  from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
  from openpilot.starpilot.galaxy.server import make_server

  params = Params(str(tmp_path / 'params'))
  params.put('LanguageSetting', 'vi', block=True)
  monkeypatch.setattr('openpilot.common.params.Params', lambda: params)
  access = GalaxyAccessOwner(tmp_path / 'access')
  access.configure('localization-test-password', lambda: True)
  server = make_server(port=0, owner=access, parked=lambda: True)
  worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
  worker.start()

  def request(path, cookie=''):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    try:
      connection.request('GET', path, headers={'Cookie': cookie})
      response = connection.getresponse()
      return response.status, json.loads(response.read()), response.getheader('Set-Cookie', '').split(';')[0]
    finally:
      connection.close()

  try:
    assert request('/api/localization')[0] == 401
    status, _, cookie = request('/api/auth/session')
    assert status == 200 and cookie
    status, result, _ = request('/api/localization', cookie)
    assert status == 200 and result['language'] == 'vi' and result['messages']['Home'] == 'Trang chủ'
    assert request('/api/localization?language=vi', cookie)[1] == {'language': 'vi'}
  finally:
    server.shutdown()
    worker.join(2)
    server.server_close()


@pytest.mark.parametrize('code', ['ja', 'ko', 'es', 'fr', 'de', 'vi'])
def test_shared_messages_have_translations(tmp_path, code):
  from openpilot.selfdrive.ui.translations.potools import parse_po
  from openpilot.system.ui.lib.multilang import TRANSLATIONS_DIR
  with as_file(TRANSLATIONS_DIR.joinpath(f'app_{code}.po')) as path:
    _, entries = parse_po(path)
  translations = {entry.msgid: entry.msgstr for entry in entries}
  assert all(translations.get(message) for message in MESSAGES)


@pytest.mark.parametrize('code', ['ja', 'ko', 'es', 'fr', 'de', 'vi'])
def test_all_registered_display_messages_have_catalog_values(code):
  import json
  from openpilot.common.basedir import BASEDIR
  from openpilot.selfdrive.ui.translations.potools import parse_po
  from openpilot.system.ui.lib.multilang import TRANSLATIONS_DIR
  registry = json.loads((Path(BASEDIR) / 'openpilot/starpilot/ui/translation_messages.json').read_text())
  with as_file(TRANSLATIONS_DIR.joinpath(f'app_{code}.po')) as path:
    _, entries = parse_po(path)
  messages = {entry.msgid: entry.msgstr for entry in entries}
  assert all(messages.get(row['source']) for row in registry['messages'])
  assert all(entry.msgstr or entry.msgstr_plural for entry in entries)


def test_display_registry_is_part_of_standard_catalog_update():
  import json
  from openpilot.common.basedir import BASEDIR
  from openpilot.selfdrive.ui.translations.update_translations import display_catalog_entries
  registry = json.loads((Path(BASEDIR) / 'openpilot/starpilot/ui/translation_messages.json').read_text())
  assert {entry.msgid for entry in display_catalog_entries()} == {row['source'] for row in registry['messages']}
