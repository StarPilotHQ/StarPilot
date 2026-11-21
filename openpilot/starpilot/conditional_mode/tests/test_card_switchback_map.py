import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.starpilot.conditional_mode.manual import WheelMapCache, read_button_map

NOW = 10_000_000_000


def dispatch(cache, params):
  spec = importlib.util.find_spec('openpilot.selfdrive.car.card')
  assert spec is not None and spec.origin is not None
  tree = ast.parse(Path(spec.origin).read_text())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Car')
  method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'state_update')
  block = next(n for n in method.body if isinstance(n, ast.If) and
               ast.unparse(n.test) == 'self.switchback_button_tracker is not None and self.switchback_settings_owner is not None')
  consumer = Mock()
  host = SimpleNamespace(ioniq_media_map=cache, switchback_button_tracker=object(), switchback_settings_owner=object(),
                         params=params, CP=object(), sm=object())
  env = {'self': host, 'CS': object(), 'now_ns': NOW, 'media_observation': object(), 'conditional_traffic_candidate': consumer}
  exec(compile(ast.Module(body=[block], type_ignores=[]), spec.origin, 'exec'), env)
  consumer.assert_called_once()
  return consumer.call_args.kwargs


@pytest.mark.parametrize('action', (0, 7))
def test_switchback_uses_the_exact_same_frame_wheel_map_once(tmp_path, action):
  from openpilot.common.params import Params
  params = Params(str(tmp_path))
  params.put('ModeButtonControl', action, block=True)
  cache = WheelMapCache()
  cache.buttons = read_button_map(params, include_ioniq_media=True)
  cache.checked_ns = NOW
  result = dispatch(cache, params)
  assert result.get('media_map_captured', False) is True
  assert result['media_buttons'] is cache.buttons
  assert result['media_buttons'].mode == action
  assert result['action'] == 7 and result['now_ns'] == NOW


@pytest.mark.parametrize('stamp', (0, NOW - 1, NOW + 1))
def test_stale_future_and_unchecked_maps_still_require_a_new_read(stamp):
  cache = WheelMapCache()
  cache.checked_ns = stamp
  result = dispatch(cache, object())
  assert result.get('media_map_captured', False) is False
  assert result.get('media_buttons') is None


def test_missing_cache_retains_the_original_reader():
  result = dispatch(None, object())
  assert result.get('media_map_captured', False) is False
  assert result.get('media_buttons') is None


def test_same_frame_failed_read_is_not_retried_into_a_different_map():
  cache = WheelMapCache()
  cache.checked_ns = NOW
  assert cache.buttons is None
  result = dispatch(cache, object())
  assert result.get('media_map_captured', False) is True
  assert result['media_buttons'] is None
