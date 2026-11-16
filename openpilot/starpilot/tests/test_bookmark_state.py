import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from openpilot.starpilot.bookmark_state import BookmarkCheckpoint, checkpoint_root, clear_bookmark_checkpoint


def test_atomic_checkpoint_survives_recreation_and_clear(tmp_path):
  root = tmp_path / 'state'
  first = BookmarkCheckpoint(root, boot='boot-a')
  first.write({'drive_id': 10, 'count': 2})
  second = BookmarkCheckpoint(root, boot='boot-a')
  assert second.read() == {'boot': 'boot-a', 'drive_id': 10, 'count': 2}
  assert BookmarkCheckpoint(root, boot='boot-b').read() is None
  second.clear()
  assert first.read() is None
  second.clear()


def test_atomic_failure_leaves_previous_value_and_removes_partial_temp(tmp_path):
  store = BookmarkCheckpoint(tmp_path / 'state', boot='boot')
  store.write({'drive_id': 10, 'count': 1})
  with patch('openpilot.starpilot.bookmark_state.os.replace', side_effect=OSError('interrupted')):
    with pytest.raises(OSError):
      store.write({'drive_id': 10, 'count': 2})
  assert store.read()['count'] == 1
  assert [path.name for path in store.root.iterdir()] == ['count.json']
  # A process killed before rename can leave a temporary, but it is never read.
  (store.root / '.interrupted').write_text('{"count":')
  assert store.read()['count'] == 1


@pytest.mark.parametrize('raw', [b'{"count":', b'x' * 1025, b'[]', b'null', b'\xff'])
def test_corrupt_or_oversized_checkpoint_is_not_restored(tmp_path, raw):
  store = BookmarkCheckpoint(tmp_path / 'state', boot='boot')
  store.write({'drive_id': 10, 'count': 1})
  (store.root / 'count.json').write_bytes(raw)
  assert store.read() is None


def test_namespace_includes_user_prefix_and_params_root(monkeypatch):
  monkeypatch.setenv('OPENPILOT_PREFIX', 'first')
  monkeypatch.setenv('PARAMS_ROOT', '/tmp/first')
  one = checkpoint_root()
  monkeypatch.setenv('OPENPILOT_PREFIX', '../../escape')
  two = checkpoint_root()
  assert one != two and one.parent == two.parent
  monkeypatch.setenv('PARAMS_ROOT', '/tmp/second')
  assert checkpoint_root() != two


def test_unsafe_root_and_file_symlinks_are_rejected(tmp_path):
  target = tmp_path / 'target'
  target.mkdir(mode=0o700)
  root = tmp_path / 'link'
  root.symlink_to(target, target_is_directory=True)
  with pytest.raises(OSError):
    BookmarkCheckpoint(root, boot='boot').write({'count': 1})
  store = BookmarkCheckpoint(target, boot='boot')
  (target / 'count.json').symlink_to(tmp_path / 'outside')
  with pytest.raises(OSError):
    store.read()
  store.clear()
  assert not (tmp_path / 'outside').exists()


def test_manager_cleanup_retires_late_write(tmp_path):
  store = BookmarkCheckpoint(tmp_path / 'state', boot='boot')
  with patch('openpilot.starpilot.bookmark_state.BookmarkCheckpoint', return_value=store):
    clear_bookmark_checkpoint()
    store.write({'drive_id': 10, 'count': 3})  # child completes after nonblocking stop
    clear_bookmark_checkpoint()  # next offroad manager iteration
  assert store.read() is None


def test_manager_clears_at_start_and_each_offroad_iteration_after_stop():
  source = Path(__file__).parents[2] / 'system' / 'manager' / 'manager.py'
  tree = ast.parse(source.read_text())
  init = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'manager_init')
  assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'clear_bookmark_checkpoint'
             for node in ast.walk(init))
  manager = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'manager_thread')
  loop = next(node for node in manager.body if isinstance(node, ast.While))
  stop_index = next(i for i, node in enumerate(loop.body) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and
                    isinstance(node.value.func, ast.Name) and node.value.func.id == 'ensure_running')
  cleanup = loop.body[stop_index + 1]
  assert isinstance(cleanup, ast.If) and ast.unparse(cleanup.test) == 'not started'
  assert ast.unparse(cleanup.body[0]) == 'clear_bookmark_checkpoint()'
