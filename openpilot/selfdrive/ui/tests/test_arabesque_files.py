"""Drive Files through real Widget touches and temporary filesystem operations."""

from threading import Event
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.selfdrive.ui.layouts.arabesque import desktop
from openpilot.selfdrive.ui.layouts.arabesque.tools import files
from openpilot.selfdrive.ui.layouts.arabesque.tools.filesystem import FileSystem
from openpilot.system.ui.lib.application import gui_app, MouseEvent, MousePos


@pytest.fixture
def view(tmp_path, monkeypatch):
  import openpilot.system.ui.widgets as widgets
  from openpilot.system.ui.widgets import inputbox
  monkeypatch.setattr(widgets, 'device', NS(awake=True))
  monkeypatch.setattr(gui_app, '_mouse_events', [])
  monkeypatch.setattr(gui_app, '_show_touches', False)
  monkeypatch.setattr(gui_app, 'font', lambda *args: NS(texture=NS(id=1)))
  monkeypatch.setattr(inputbox, 'measure_text_cached', lambda font, text, size: rl.Vector2(len(text) * size / 2, size))
  monkeypatch.setattr(files, 'gui_label', Mock())
  monkeypatch.setattr(desktop, 'gui_label', Mock())
  monkeypatch.setattr(files, 'wrap_text', lambda font, text, size, width: text.splitlines())
  for name in ('draw_rectangle_rec', 'draw_rectangle_rounded', 'draw_rectangle_rounded_lines_ex', 'draw_line_ex', 'draw_ring',
               'draw_circle_v', 'draw_triangle', 'draw_ellipse', 'begin_scissor_mode', 'end_scissor_mode', 'draw_text_ex',
               'draw_line', 'set_exit_key'):
    monkeypatch.setattr(rl, name, Mock())
  for name in ('get_key_pressed', 'get_char_pressed', 'get_mouse_wheel_move'):
    monkeypatch.setattr(rl, name, lambda: 0)
  monkeypatch.setattr(rl, 'is_key_down', lambda key: False)
  tool = files.Files(lambda: True)
  tool._path = tmp_path
  tool._places = (('Home', tmp_path),)
  tool._fs = FileSystem((tmp_path,), (tmp_path / 'managed',), tool._permitted)
  tool.set_rect(rl.Rectangle(0, 104, 2160, 976))
  tool.show_event()
  settle(tool)
  yield tool
  tool.hide_event()


def frame(view, *events):
  gui_app._mouse_events = list(events)
  view.render()


def settle(view):
  deadline = time.monotonic() + 3
  while view._future is not None and time.monotonic() < deadline:
    frame(view)
    time.sleep(.001)
  assert view._future is None
  frame(view)


def event(x, y, *, pressed=False, released=False, slot=0):
  return MouseEvent(MousePos(x, y), slot, pressed, released, pressed, time.monotonic())


def tap(view, action):
  frame(view)
  rect = next(rect for rect, value in view._buttons if value == action)
  x, y = rect.x + rect.width / 2, rect.y + rect.height / 2
  frame(view, event(x, y, pressed=True))
  frame(view, event(x, y, released=True))
  frame(view)


def item(view, name):
  return next(entry for entry in view._entries if entry.path.name == name)


def test_touch_create_rename_copy_move_and_confirm_delete(view, tmp_path):
  tap(view, 'mkdir')
  tap(view, ('key', 'a'))
  tap(view, 'submit')
  settle(view)
  assert (tmp_path / 'a').is_dir()
  tap(view, ('actions', item(view, 'a')))
  tap(view, 'rename')
  tap(view, ('key', files.BACKSPACE_KEY))
  tap(view, ('key', 'b'))
  tap(view, ('key', files.ENTER_KEY))
  settle(view)
  assert (tmp_path / 'b').is_dir() and not (tmp_path / 'a').exists()
  (tmp_path / 'note.txt').write_text('hello')
  tap(view, 'refresh')
  settle(view)
  tap(view, ('actions', item(view, 'note.txt')))
  tap(view, 'copy')
  tap(view, ('open', item(view, 'b')))
  settle(view)
  tap(view, 'paste')
  settle(view)
  assert (tmp_path / 'b' / 'note.txt').read_text() == 'hello'
  tap(view, ('actions', item(view, 'note.txt')))
  tap(view, 'rename')
  view._input.text = 'moved.txt'
  tap(view, 'submit')
  settle(view)
  tap(view, ('actions', item(view, 'moved.txt')))
  tap(view, 'move')
  tap(view, 'up')
  settle(view)
  tap(view, 'paste')
  settle(view)
  assert (tmp_path / 'moved.txt').read_text() == 'hello'
  assert not (tmp_path / 'b' / 'moved.txt').exists()
  tap(view, ('actions', item(view, 'moved.txt')))
  tap(view, 'delete')
  assert (tmp_path / 'moved.txt').exists()
  tap(view, 'close')
  assert (tmp_path / 'moved.txt').exists()
  tap(view, ('actions', item(view, 'moved.txt')))
  tap(view, 'delete')
  tap(view, 'confirm-delete')
  settle(view)
  assert not (tmp_path / 'moved.txt').exists()


def test_preview_hidden_toggle_and_permission_errors(view, tmp_path):
  (tmp_path / '.hidden').touch()
  (tmp_path / 'note').write_text('Hello\nworld')
  (tmp_path / 'managed').mkdir()
  tap(view, 'refresh')
  settle(view)
  assert ('open', item(view, '.hidden')) not in [action for _, action in view._buttons]
  tap(view, 'hidden')
  assert ('open', item(view, '.hidden')) in [action for _, action in view._buttons]
  tap(view, ('open', item(view, 'note')))
  settle(view)
  assert view._preview == 'Hello\nworld' and view._panel == 'preview'
  assert gui_app._nav_stack == []
  tap(view, 'close')
  tap(view, ('open', item(view, 'managed')))
  settle(view)
  assert 'mkdir' not in [action for _, action in view._buttons]
  view._places += (('Missing', tmp_path / 'missing'),)
  tap(view, 'places')
  tap(view, ('place', tmp_path / 'missing'))
  settle(view)
  assert view._path == tmp_path / 'managed' and view._error


def test_drag_and_second_finger_do_not_open_files(view, tmp_path):
  (tmp_path / 'folder').mkdir()
  tap(view, 'refresh')
  settle(view)
  selected = item(view, 'folder')
  row = next(rect for rect, action in view._buttons if action == ('open', selected))
  x, y = row.x + 100, row.y + 20
  frame(view, event(x, y, pressed=True, slot=1), event(x, y, released=True, slot=1))
  assert view._path == tmp_path
  frame(view, event(x, y, pressed=True))
  frame(view, event(x + 70, y))
  frame(view, event(x + 70, y, released=True))
  assert view._path == tmp_path


def test_tapping_to_stop_inertia_does_not_open_a_file(view, tmp_path):
  (tmp_path / 'folder').mkdir()
  tap(view, 'refresh')
  settle(view)
  selected = item(view, 'folder')
  row = next(rect for rect, action in view._buttons if action == ('open', selected))
  view._scroll._velocity_filter_y.x = 2000
  x, y = row.x + 100, row.y + 20
  frame(view, event(x, y, pressed=True))
  frame(view, event(x, y, released=True))
  assert view._path == tmp_path and view._future is None


def test_shell_menu_blocks_inline_keyboard_and_dismissal_does_not_save(view, tmp_path, monkeypatch):
  view.hide_event()
  shell = desktop.ArabesqueLayout(Mock(), lambda: True)
  shell.set_rect(rl.Rectangle(0, 0, 2160, 1080))
  shell._open_tool('Files', view)
  settle(view)
  tap(view, 'mkdir')
  view._input.text = 'blocked'
  shell._menu_open = True
  keys = iter([rl.KEY_ENTER, 0])
  monkeypatch.setattr(rl, 'get_key_pressed', lambda: next(keys, 0))
  frame(shell)
  assert not view.enabled and view._future is None
  assert not (tmp_path / 'blocked').exists()
  # A tap on Save dismisses the shell menu without creating a directory.
  button = next(rect for rect, action in view._buttons if action == 'submit')
  x, y = button.x + 10, button.y + 10
  frame(shell, event(x, y, pressed=True), event(x, y, released=True))
  assert not shell._menu_open
  assert not (tmp_path / 'blocked').exists()
  shell.hide_event()


def test_close_cancels_worker_and_drops_results_without_blocking(view):
  started, stopped = Event(), Event()

  def work(cancelled):
    started.set()
    cancelled.wait(2)
    stopped.set()

  view._run('copy', work)
  assert started.wait(1)
  view.hide_event()
  assert stopped.wait(1)
  assert view._executor is None and view._future is None


def test_physical_text_and_enter_in_one_frame_save_the_complete_name(view, tmp_path, monkeypatch):
  tap(view, 'mkdir')
  characters = iter([ord('o'), ord('k'), 0])
  keys = iter([rl.KEY_ENTER, 0])
  monkeypatch.setattr(rl, 'get_char_pressed', lambda: next(characters, 0))
  monkeypatch.setattr(rl, 'get_key_pressed', lambda: next(keys, 0))
  frame(view)
  settle(view)
  assert (tmp_path / 'ok').is_dir()


def test_loss_of_offroad_authority_blocks_confirmation(view, tmp_path):
  tap(view, 'mkdir')
  view._input.text = 'do-not-create'
  view._permitted = lambda: False
  tap(view, 'submit')
  settle(view)
  assert not (tmp_path / 'do-not-create').exists()


@pytest.mark.parametrize('bounds', [(0, 104, 2160, 976), (45, 100, 1280, 616), (0, 104, 2560, 976)])
def test_controls_fit_scaled_tool_and_long_lists_only_draw_visible_rows(view, tmp_path, bounds):
  for index in range(150):
    (tmp_path / f'{index:03}.txt').touch()
  tap(view, 'refresh')
  settle(view)
  view.set_rect(rl.Rectangle(*bounds))
  frame(view)
  assert len(view._buttons) < 30
  for rect, _ in view._buttons:
    assert rect.x >= view.rect.x and rect.y >= view.rect.y
    assert rect.x + rect.width <= view.rect.x + view.rect.width + 1
    assert rect.y + rect.height <= view.rect.y + view.rect.height + 1


@pytest.mark.parametrize('keyword', [False, True])
def test_name_input_accepts_base_render_signature_with_unchanged_theme(view, monkeypatch, keyword):
  render = Mock()
  monkeypatch.setattr(files.InputBox, '_render', render)
  rect = rl.Rectangle(0, 0, 400, 104)
  if keyword:
    view._input._render(rect, color=rl.RED, border_color=rl.GREEN, text_color=rl.BLUE, font_size=12)
  else:
    view._input._render(rect, rl.RED, rl.GREEN, rl.BLUE, 12)
  theme = view._input._theme
  render.assert_called_once_with(rect, color=theme.surface, border_color=theme.surface, text_color=theme.text, font_size=40)


def test_mouse_event_accepts_base_keyword_and_preserves_drag(view):
  view._press_pos = MousePos(10, 10)
  view._dragged = False
  view._handle_mouse_event(mouse_event=event(10 + files.DRAG_THRESHOLD + 1, 10))
  assert view._dragged
