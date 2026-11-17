"""Terminal styling, real Widget input, and a private native shell session."""
from collections import deque
import ctypes
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, call

from openpilot.starpilot.ui.raylib import rl
import pytest

from openpilot.selfdrive.ui.layouts.arabesque.tools import terminal
from openpilot.system.ui.lib.application import gui_app, MouseEvent, MousePos


def test_styles_preserve_positions_colors_reverse_and_hidden_text():
  rows = terminal._styled_rows(['a\x1b[31;44mred\x1b[7mR\x1b[27;8msecret\x1b[0m界e\u0301',
                                '\x1b[38;5;196;48;2;10;20;30mrgb\x1b[39;49mdefault'])
  assert rows[0][1][0:4] == (1, 'red', terminal._PALETTE[1], terminal._PALETTE[4])
  assert rows[0][2][4] is True
  assert rows[0][3][5] is True
  assert rows[0][4][2:6] == (None, None, False, False)
  assert rows[0][4][6] == 3
  assert rows[1][0][2:4] == ((255, 0, 0), (10, 20, 30))
  assert rows[1][1][2:4] == (None, None)


def test_screen_text_cannot_impersonate_a_control_reply():
  session = terminal._Session.__new__(terminal._Session)
  session._block = None
  session._pending = deque([('screen', 2), ('cursor', 1)])
  for line in (b'%begin 123 4 1', b'%end 123 4 1', 'café'.encode(), b'%end 123 4 1',
               b'%begin 123 5 1', b'3 1 1', b'%end 123 5 1'):
    session._line(line)
  assert session.rows[0][0][1] == '%end 123 4 1'
  assert session.rows[1][0][1] == 'café'
  assert session.cursor == (3, 1, True)
  assert not session._pending


@pytest.fixture
def tool(monkeypatch):
  import openpilot.system.ui.widgets as widgets
  monkeypatch.setattr(widgets, 'device', NS(awake=True))
  monkeypatch.setattr(gui_app, '_mouse_events', [])
  monkeypatch.setattr(gui_app, '_render_prepare', {})
  monkeypatch.setattr(gui_app, '_show_touches', False)
  monkeypatch.setattr(terminal, 'gui_label', Mock())
  monkeypatch.setattr(terminal, 'device', NS(_reset_interactive_timeout=Mock()))
  monkeypatch.setattr(gui_app, 'font', Mock(return_value=NS(texture=NS(id=2), baseSize=16, glyphs=[NS(advanceX=8, value=63)])))
  monkeypatch.setattr(rl, 'get_font_default', lambda: NS(texture=NS(id=1)))
  monkeypatch.setattr(rl, 'load_font_ex', Mock(return_value=NS(texture=NS(id=3), baseSize=64, glyphs=[NS(advanceX=29, value=63)])))
  monkeypatch.setattr(rl, 'gen_texture_mipmaps', Mock())
  monkeypatch.setattr(rl, '_orig_draw_text_ex', Mock(), raising=False)
  monkeypatch.setattr(rl, 'get_glyph_index', lambda *a: 0)
  for name in ('unload_font', 'set_texture_filter', 'draw_rectangle_rec', 'draw_rectangle_rounded',
               'draw_rectangle_lines_ex', 'rl_draw_render_batch_active', 'begin_scissor_mode', 'end_scissor_mode', 'draw_text_ex'):
    monkeypatch.setattr(rl, name, Mock())
  monkeypatch.setattr(rl, 'set_exit_key', Mock())
  monkeypatch.setattr(rl, 'get_key_pressed', lambda: 0)
  monkeypatch.setattr(rl, 'get_char_pressed', lambda: 0)
  monkeypatch.setattr(rl, 'is_key_down', lambda key: False)
  monkeypatch.setattr(rl, 'is_key_pressed_repeat', lambda key: False)
  session = NS(rows=[], cursor=(0, 0, False), error='', update=Mock(), close=Mock(), resize=Mock(), keys=Mock())
  monkeypatch.setattr(terminal, '_Session', Mock(return_value=session))
  widget = terminal.Terminal()
  widget.set_rect(rl.Rectangle(0, 104, 2160, 768))
  widget.show_event()
  yield widget, session
  widget.hide_event()


def touch(widget, key):
  bounds = next(rect for rect, _, value in widget._buttons if value == key)
  pos = MousePos(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)
  for down in (True, False):
    gui_app._mouse_events = [MouseEvent(pos, 0, down, not down, down, 1)]
    widget.render()
  gui_app._mouse_events = []


def test_touch_keyboard_sends_bytes_and_control_keys_and_resizes(tool):
  widget, session = tool
  initial_rows = widget._size[1]
  touch(widget, 'keyboard')
  assert widget._keyboard and widget._size[1] < initial_rows
  for key in ('l', 's', terminal.ENTER_KEY, 'ctrl', 'c', 'Up'):
    touch(widget, key)
  assert [call.args[0] for call in session.keys.call_args_list] == [b'l', b's', 'Enter', b'\x03', 'Up']
  assert not widget._ctrl
  assert min(rect.height for rect, _, _ in widget._buttons) >= 44
  session.resize.assert_called_with(*widget._size)


def test_disabled_tool_drops_keyboard_input_without_sending_it(tool, monkeypatch):
  widget, session = tool
  widget.set_enabled(False)
  keys = iter([rl.KEY_ENTER, 0])
  chars = iter([ord('x'), 0])
  monkeypatch.setattr(rl, 'get_key_pressed', lambda: next(keys, 0))
  monkeypatch.setattr(rl, 'get_char_pressed', lambda: next(chars, 0))
  widget._update_state()
  session.keys.assert_not_called()


def test_physical_control_and_escape_are_terminal_input(tool, monkeypatch):
  widget, session = tool
  keys = iter([rl.KEY_C, rl.KEY_ESCAPE, 0])
  chars = iter([ord('c'), 0])
  monkeypatch.setattr(rl, 'get_key_pressed', lambda: next(keys, 0))
  monkeypatch.setattr(rl, 'get_char_pressed', lambda: next(chars, 0))
  monkeypatch.setattr(rl, 'is_key_down', lambda key: key == rl.KEY_LEFT_CONTROL)
  widget._update_state()
  assert [call.args[0] for call in session.keys.call_args_list] == [b'\x03', 'Escape']
  rl.set_exit_key.assert_called_with(0)


def test_prepare_caches_rows_and_hide_releases_only_owned_resources(tool):
  widget, session = tool
  owned = widget._font
  lifecycle = Mock()
  lifecycle.attach_mock(rl.rl_draw_render_batch_active, 'flush')
  lifecycle.attach_mock(rl.unload_font, 'unload')
  session.rows = terminal._styled_rows(['\x1b[7minverse'])
  widget._prepare()
  spans = widget._draw_rows
  assert spans[0][0][2] is widget._theme.background
  assert spans[0][0][3] is widget._theme.text
  widget._prepare()
  assert widget._draw_rows is spans
  assert widget._prepare in gui_app._render_prepare
  widget.hide_event()
  widget.hide_event()
  assert not gui_app._render_prepare
  session.close.assert_called_once()
  assert lifecycle.mock_calls == [call.flush(), call.unload(owned)]
  rl.set_exit_key.assert_called_with(rl.KEY_ESCAPE)


def test_font_is_loaded_once_per_entry_and_not_during_render(tool):
  widget, session = tool
  args = rl.load_font_ex.call_args.args
  assert Path(args[0]).name == 'JetBrainsMono-Medium.ttf'
  assert args[1:] == (64, None, 0)
  rl.set_texture_filter.assert_called_once_with(widget._font.texture, rl.TextureFilter.TEXTURE_FILTER_TRILINEAR)
  session.rows = terminal._styled_rows(['local shell'])
  widget.show_event()
  for _ in range(3):
    widget.render()
  rl.load_font_ex.assert_called_once()
  rl.gen_texture_mipmaps.assert_called_once()
  assert len(gui_app._render_prepare) == 1


def test_grid_matches_selected_c_preview_and_sizes_from_actual_glyphs(tool, monkeypatch):
  widget, session = tool
  # Calibrate the large-UI preview independently of the surrounding suite's UI mode.
  monkeypatch.setattr(terminal, 'FONT_SCALE', 1.242)
  widget.set_rect(rl.Rectangle(0, 104, 2160, 976))
  assert widget._size == (109, 18)
  for font, size in ((widget._font, widget._font_size), (widget._fallback_font, widget._fallback_size)):
    assert font.glyphs[0].advanceX * size / font.baseSize == pytest.approx(widget._cell_w)
  session.resize.assert_called_with(109, 18)


def test_unicode_fallback_keeps_colors_columns_cursor_and_explicit_font(tool, monkeypatch):
  widget, session = tool
  shared, owned = widget._fallback_font, widget._font
  shared.glyphs += [NS(advanceX=8, value=ord('ü')), NS(advanceX=16, value=ord('日'))]
  monkeypatch.setattr(rl, 'get_glyph_index', lambda font, value: next((i for i, g in enumerate(font.glyphs) if g.value == value), 0))
  session.rows = terminal._styled_rows(['A\x1b[31;44mü日B\x1b[7mC'])
  session.cursor = (6, 0, True)
  widget._prepare()
  spans = widget._draw_rows[0]
  assert [(s[0], s[1], s[5], s[6]) for s in spans] == [
    (0, 'A', 1, owned), (1, 'ü日', 3, shared), (4, 'B', 1, owned), (5, 'C', 1, owned)]
  assert spans[1][2:4] == spans[2][2:4]
  assert (spans[3][2].r, spans[3][2].g, spans[3][2].b) == terminal._PALETTE[4]
  assert (spans[3][3].r, spans[3][3].g, spans[3][3].b) == terminal._PALETTE[1]
  widget._render(widget.rect)
  draws = rl._orig_draw_text_ex.call_args_list
  assert [draw.args[0] for draw in draws] == [owned, shared, owned, owned]
  for draw, column in zip(draws, (0, 1, 4, 5), strict=True):
    assert draw.args[2].x == pytest.approx(widget._screen.x + column * widget._cell_w)
  assert draws[1].args[3] == widget._fallback_size
  assert rl.draw_rectangle_lines_ex.call_args.args[0].x == pytest.approx(widget._screen.x + 6 * widget._cell_w)
  rl.draw_text_ex.assert_not_called()


@pytest.mark.parametrize('failure', ['default', 'oserror'])
def test_font_load_failure_uses_shared_font_without_unloading_it(tool, failure):
  widget, session = tool
  widget.hide_event()
  rl.unload_font.reset_mock()
  rl.rl_draw_render_batch_active.reset_mock()
  if failure == 'default':
    rl.load_font_ex.return_value = rl.get_font_default()
  else:
    rl.load_font_ex.side_effect = OSError('font unavailable')
  widget.show_event()
  assert widget._font is widget._fallback_font
  session.rows = terminal._styled_rows(['still usable'])
  widget._prepare()
  widget._render(widget.rect)
  widget.hide_event()
  rl.unload_font.assert_not_called()
  rl.rl_draw_render_batch_active.assert_not_called()


@pytest.fixture
def real_session(tmp_path, monkeypatch):
  if sys.platform != 'linux' or shutil.which('tmux') is None:
    raise pytest.skip.Exception('requires the Linux OS tmux binary')
  # Own/reap test servers even under a container PID 1; production uses the OS init.
  assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
  monkeypatch.setenv('HOME', str(tmp_path))
  monkeypatch.setenv('HISTFILE', '/dev/null')
  before = len(list(Path('/proc/self/fd').iterdir()))
  children = Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children')
  before_children = set(children.read_text().split())
  session = terminal._Session(80, 24)
  yield session
  session.close()
  deadline = time.monotonic() + .5
  while time.monotonic() < deadline:
    try:
      if not os.waitpid(-1, os.WNOHANG)[0]:
        time.sleep(.005)
    except ChildProcessError:
      break
  assert len(list(Path('/proc/self/fd').iterdir())) == before
  assert not set(children.read_text().split()) - before_children


def wait_screen(session, predicate):
  deadline = time.monotonic() + 3
  while time.monotonic() < deadline:
    session.update()
    assert not session.error, session.error
    text = [''.join(span[1] for span in row).rstrip() for row in session.rows]
    if predicate(text):
      return text
    time.sleep(.003)
  raise AssertionError('shell did not produce expected output')


def test_real_shell_state_unicode_resize_interrupt_and_exit(real_session):
  session = real_session
  wait_screen(session, lambda rows: bool(rows))
  session.keys("cd /tmp; printf 'READY_CAFE:café\\n'\r".encode())
  wait_screen(session, lambda rows: 'READY_CAFE:café' in rows)
  session.resize(100, 30)
  wait_screen(session, lambda rows: len(rows) == 30)
  session.keys(b"pwd; stty size\r")
  wait_screen(session, lambda rows: '/tmp' in rows and '30 100' in rows)
  session.keys(b'sleep 30\r')
  for _ in range(15):
    session.update()
    time.sleep(.003)
  session.keys(b'\x03')
  session.keys(b"printf 'INTERRUPTED\\n'\r")
  wait_screen(session, lambda rows: 'INTERRUPTED' in rows)
  session.keys(b'exit\r')
  deadline = time.monotonic() + 2
  while session._process is not None and time.monotonic() < deadline:
    session.update()
    time.sleep(.003)
  assert session._process is None and 'exited' in session.error


def test_real_output_flood_and_atomic_input_limit(real_session):
  session = real_session
  wait_screen(session, lambda rows: bool(rows))
  session.keys(b'yes flood\r')
  for _ in range(50):
    session.update()
    assert len(session._buffer) <= 262144 and len(session._pending) <= 64
    time.sleep(.003)
  session.keys(b'\x03')
  session.keys(b"printf 'FLOOD_STOPPED\\n'\r")
  wait_screen(session, lambda rows: 'FLOOD_STOPPED' in rows)
  session.keys(b'x' * 2049)
  assert session._process is None and 'input limit' in session.error
  session.update()


def test_exported_links_and_legacy_borders_do_not_occupy_text_cells():
  rows = terminal._styled_rows(['\x1b]8;;https://example.org\x1b\\link\x1b]8;;\x1b\\ \x0elqqk\x0f'])
  assert ''.join(span[1] for span in rows[0]) == 'link ┌──┐'
  assert sum(span[6] for span in rows[0]) == 9


def test_prepare_cannot_flush_commands_before_main_checks_offroad(tool):
  widget, session = tool
  widget._prepare()
  session.update.assert_not_called()
  widget._update_state()
  session.update.assert_called_once()


def test_shared_font_fallback_preserves_columns(tool):
  widget, session = tool
  session.rows = terminal._styled_rows(['─界e\u0301'])
  widget._prepare()
  assert widget._draw_rows[0][0][1] == '-? ?'
  assert widget._draw_rows[0][0][5] == 4


def test_underline_color_does_not_change_foreground():
  spans = terminal._styled_rows(['\x1b[58:2::1:2:31mtext'])[0]
  assert spans[0][2:4] == (None, None)


@pytest.mark.parametrize('key,modifier,expected', [(rl.KEY_LEFT, rl.KEY_LEFT_CONTROL, 'C-Left'),
                                                   (rl.KEY_TAB, rl.KEY_LEFT_SHIFT, 'BTab')])
def test_physical_navigation_modifiers(tool, monkeypatch, key, modifier, expected):
  widget, session = tool
  keys = iter([key])
  monkeypatch.setattr(rl, 'get_key_pressed', lambda: next(keys, 0))
  monkeypatch.setattr(rl, 'is_key_down', lambda value: value == modifier)
  widget._update_state()
  session.keys.assert_called_once_with(expected)


def test_text_and_enter_in_one_frame_keep_their_order(tool, monkeypatch):
  widget, session = tool
  keys = iter([rl.KEY_L, rl.KEY_S, rl.KEY_ENTER])
  chars = iter([ord('l'), ord('s')])
  monkeypatch.setattr(rl, 'get_key_pressed', lambda: next(keys, 0))
  monkeypatch.setattr(rl, 'get_char_pressed', lambda: next(chars, 0))
  widget._update_state()
  assert [call.args[0] for call in session.keys.call_args_list] == [b'l', b's', 'Enter']


def test_cleanup_waits_are_bounded_even_after_forced_termination(monkeypatch):
  session = terminal._Session.__new__(terminal._Session)
  process = Mock()
  process.wait.side_effect = terminal.subprocess.TimeoutExpired('tmux', .02)
  session._process, session._folder = process, Mock()
  session._out, session._buffer, session._pending = bytearray(), bytearray(), deque()
  monkeypatch.setattr(os, 'write', Mock())
  session.close()
  process.kill.assert_called_once()
  assert [call.kwargs for call in process.wait.call_args_list] == [{'timeout': .02}, {'timeout': .02}]


def test_repeated_character_before_enter_is_not_sent_after_enter(tool, monkeypatch):
  widget, session = tool
  keys = iter([rl.KEY_ENTER])
  chars = iter([ord('s')])
  monkeypatch.setattr(rl, 'get_key_pressed', lambda: next(keys, 0))
  monkeypatch.setattr(rl, 'get_char_pressed', lambda: next(chars, 0))
  widget._update_state()
  assert [call.args[0] for call in session.keys.call_args_list] == [b's', 'Enter']


@pytest.mark.parametrize('failure', ['process', 'directory'])
def test_startup_failure_is_visible_and_can_be_closed(monkeypatch, failure):
  target = terminal.subprocess if failure == 'process' else terminal.tempfile
  monkeypatch.setattr(target, 'Popen' if failure == 'process' else 'TemporaryDirectory', Mock(side_effect=OSError('unavailable')))
  session = terminal._Session(80, 24)
  assert session.error and session._process is None
  if session._folder:
    assert not Path(session._folder.name).exists()
  session.update()
  session.close()


def test_real_client_failure_cleans_up_private_server(real_session):
  session = real_session
  wait_screen(session, lambda rows: bool(rows))
  session._process.terminate()
  deadline = time.monotonic() + 2
  while session._process is not None and time.monotonic() < deadline:
    session.update()
    time.sleep(.003)
  assert session._process is None and session.error


def test_real_startup_can_be_cancelled_immediately(real_session):
  real_session.close()
  assert real_session._process is None


def test_keyboard_activity_resets_the_existing_idle_timer(tool):
  widget, session = tool
  widget._send('x')
  terminal.device._reset_interactive_timeout.assert_called_once()
  session.keys.assert_called_once_with(b'x')
