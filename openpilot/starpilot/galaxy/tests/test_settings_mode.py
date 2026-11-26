from types import SimpleNamespace

import pytest

from openpilot.starpilot.galaxy.settings_mode import KEY, read_mode, save_mode


def params_at(root):
  (root / "d").mkdir(exist_ok=True)
  return SimpleNamespace(get_param_path=lambda key: str(root / "d" / key))


def test_default_and_persistence_across_clients(tmp_path):
  first = params_at(tmp_path)
  assert read_mode(first) == {"mode": "simple"}
  assert save_mode(first, "advanced", authorized=lambda: True) == {"mode": "advanced"}
  assert (tmp_path / "d" / KEY).read_bytes() == b"1"
  second = params_at(tmp_path)
  assert read_mode(second) == {"mode": "advanced"}
  save_mode(second, "simple", authorized=lambda: True)
  assert read_mode(first) == {"mode": "simple"}


def test_invalid_and_unauthorized_requests_do_not_write(tmp_path):
  params = params_at(tmp_path)
  for mode in ("developer", "", True, None, {}):
    with pytest.raises(ValueError):
      save_mode(params, mode, authorized=lambda: True)
  with pytest.raises(RuntimeError):
    save_mode(params, "advanced", authorized=lambda: False)
  assert not (tmp_path / "d" / KEY).exists()


def test_corrupt_preference_defaults_to_simple(tmp_path):
  params = params_at(tmp_path)
  (tmp_path / "d" / KEY).write_bytes(b"corrupt")
  assert read_mode(params) == {"mode": "simple"}
