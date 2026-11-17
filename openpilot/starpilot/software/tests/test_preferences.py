from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.starpilot.software.preferences import AUTOMATIC_DOWNLOADS, automatic_downloads, download_permitted


@pytest.mark.parametrize('raw, expected', [(None, True), (b'1', True), (b'0', False), (b'', None), (b'yes', None), (b'2', None)])
def test_default_saved_and_invalid_values_preserve_manual_requests(tmp_path, raw, expected):
  params = SimpleNamespace(get_param_path=lambda key: str(tmp_path / key))
  if raw is not None:
    Path(params.get_param_path(AUTOMATIC_DOWNLOADS)).write_bytes(raw)
  assert automatic_downloads(params) is expected
  assert download_permitted(params, manual=False) is (expected is True)
  assert download_permitted(params, manual=True)


def test_nonregular_and_symlink_preferences_do_not_enable_automatic_downloads(tmp_path):
  params = SimpleNamespace(get_param_path=lambda key: str(tmp_path / key))
  path = tmp_path / AUTOMATIC_DOWNLOADS
  path.mkdir()
  assert automatic_downloads(params) is None
  path.rmdir()
  (tmp_path / 'value').write_bytes(b'1')
  path.symlink_to(tmp_path / 'value')
  assert automatic_downloads(params) is None
