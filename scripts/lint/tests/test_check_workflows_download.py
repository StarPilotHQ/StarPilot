import hashlib
import importlib.util
import io
import tarfile
import time
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def downloader(tmp_path, monkeypatch):
  spec = importlib.util.spec_from_file_location('workflow_download', Path(__file__).parents[1] / 'check_workflows.py')
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  monkeypatch.setattr(module, 'ROOT', tmp_path)
  monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
  monkeypatch.setattr(module.platform, 'machine', lambda: 'x86_64')
  output = io.BytesIO()
  with tarfile.open(fileobj=output, mode='w:gz') as release:
    info = tarfile.TarInfo('actionlint')
    info.size = len(b'validated binary')
    release.addfile(info, io.BytesIO(b'validated binary'))
  payload = output.getvalue()
  monkeypatch.setitem(module.ARCHIVE_SHA256, 'linux_amd64', hashlib.sha256(payload).hexdigest())
  sleep = Mock()
  monkeypatch.setattr(time, 'sleep', sleep)
  return module, payload, sleep


@pytest.mark.parametrize('failure', [urllib.error.HTTPError('url', 504, 'Gateway timeout', Message(), None),
  urllib.error.URLError('temporary connection failure'), TimeoutError('read timeout')])
def test_transient_download_recovers_without_bypassing_validation(downloader, monkeypatch, failure):
  module, payload, sleep = downloader
  fetch = Mock(side_effect=[failure, failure, io.BytesIO(payload)])
  monkeypatch.setattr(module.urllib.request, 'urlopen', fetch)
  binary = module.actionlint()
  assert binary.read_bytes() == b'validated binary'
  assert binary.stat().st_mode & 0o777 == 0o755
  assert fetch.call_count == 3 and sleep.call_count == 2
  assert module.actionlint() == binary and fetch.call_count == 3


@pytest.mark.parametrize('code,count', [(404, 1), (403, 1), (504, 3)])
def test_permanent_or_exhausted_failure_is_not_ignored(downloader, monkeypatch, code, count):
  module, _, sleep = downloader
  fetch = Mock(side_effect=urllib.error.HTTPError('url', code, 'failure', Message(), None))
  monkeypatch.setattr(module.urllib.request, 'urlopen', fetch)
  with pytest.raises(urllib.error.HTTPError):
    module.actionlint()
  assert fetch.call_count == count and sleep.call_count == count - 1
  assert not list(module.ROOT.rglob('release.tar.gz'))


def test_download_checksum_mismatch_is_not_retried_or_cached(downloader, monkeypatch):
  module, _, sleep = downloader
  fetch = Mock(return_value=io.BytesIO(b'corrupt archive'))
  monkeypatch.setattr(module.urllib.request, 'urlopen', fetch)
  with pytest.raises(RuntimeError, match='release checksum mismatch'):
    module.actionlint()
  assert fetch.call_count == 1 and sleep.call_count == 0
  assert not list(module.ROOT.rglob('release.tar.gz'))
