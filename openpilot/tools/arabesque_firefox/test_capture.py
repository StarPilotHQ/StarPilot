"""Opt-in native check: run only under a disposable Xvfb, never a personal desktop."""

import ctypes as C
import importlib.util
import mmap
import os
from pathlib import Path
import struct

import pytest

TOOLS = Path(__file__).resolve().parents[2] / 'selfdrive/ui/layouts/arabesque/tools'


def module(name):
  spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
  assert spec is not None and spec.loader is not None
  result = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(result)
  return result


def test_verification_pattern_rejects_blank_and_incomplete_paint():
  x11 = module('browser_x11')
  width, height = 320, 240
  frame = bytearray(width * height * 4)
  assert not x11.verification_frame(frame, width, height)
  for index, color in enumerate(x11.VERIFY_COLORS):
    for y in range(height - 96, height):
      start = (y * width + index * width // 4) * 4
      frame[start:start + width] = color.to_bytes(4, 'little') * (width // 4)
    assert x11.verification_frame(frame, width, height) == (index == 3)
  assert not x11.verification_frame(frame[:-4], width, height)


def test_verification_pattern_handles_sidebar_and_unused_alpha_without_changing_frame():
  x11 = module('browser_x11')
  width, height = 320, 240
  frame = bytearray(width * height * 4)
  for index, color in enumerate(x11.VERIFY_COLORS):
    for y in range(height - 96, height):
      start = (y * width + 80 + index * 60) * 4
      frame[start:start + 240] = (color.to_bytes(4, 'little')[:3] + b'\xff') * 60
  before = bytes(frame)
  assert x11.verification_frame(frame, width, height)
  assert frame == before


@pytest.mark.skipif(os.environ.get('ARABESQUE_X11_TEST') != '1', reason='Needs an explicitly disposable Xvfb and XCB extensions')
def test_reference_damage_shm_and_input():
  x11, runtime = module('browser_x11'), module('browser_session')
  x = x11.X11('unix/' + os.environ['DISPLAY'])
  fds, buffers = [], []
  try:
    assert C.sizeof(x11.Screen) == 40
    for _ in range(2):
      fd = runtime.sealed_slot(x.size)
      fds.append(fd)
      buffers.append(mmap.mmap(fd, x.size))
    x.reference(buffers)
    segments = [x.attach(fd) for fd in fds]
    # Repeated reuse catches FD ownership, request/reply and stride mistakes.
    for _ in range(50):
      x.finish(x.wait(x.capture(segments[1])), buffers[1], True)
      assert buffers[0][:] == buffers[1][:]
    for text in ('a', 'A', '€', '•', 'é', '😀'):
      code = ord(text)
      x.key(code if code <= 255 else 0x01000000 | code)
    x.motion(20, 20)
    x.button(1, True)
    x.button(1, False)
    x.sync()
    x.damage_subtract(x.connection, x.damage_id, 0, 0)
    x.sync()
    x.reference(buffers)
    assert x.damaged()
    # The browser-page check must survive the same real GetImage/SHM byte layout.
    gc = x.generate_id(x.connection)
    x.create_gc(x.connection, gc, x.screen.root, 0, None)
    for index, color in enumerate(x11.VERIFY_COLORS):
      value = C.c_uint32(color)
      x.change_gc(x.connection, gc, 4, C.byref(value))
      rectangle = struct.pack('=hhHH', index * x.width // 4, x.height - 96, x.width // 4, 96)
      x.poly_fill_rectangle(x.connection, x.screen.root, gc, 1, rectangle)
    x.free_gc(x.connection, gc)
    x.finish(x.wait(x.capture(segments[1])), buffers[1], True)
    assert x11.verification_frame(buffers[1], x.width, x.height)
    # A bounded title-property read identifies the specific local verification page.
    ptr, u32, u16, u8, i16 = C.c_void_p, C.c_uint32, C.c_uint16, C.c_uint8, C.c_int16
    create = x11.bind(x.lib, 'create_window', x11.Cookie, ptr, u8, u32, u32, i16, i16, u16, u16, u16, u16, u32, u32, ptr)
    change = x11.bind(x.lib, 'change_property', x11.Cookie, ptr, u8, u32, u32, u32, u8, u32, ptr)
    mapped = x11.bind(x.lib, 'map_window', x11.Cookie, ptr, u32)
    window = x.generate_id(x.connection)
    create(x.connection, 0, window, x.screen.root, 0, 0, 320, 240, 0, 1, 0, 0, None)
    title = b'Arabesque verification test-nonce'
    change(x.connection, 0, window, x.title_atom, 31, 8, len(title), title)
    mapped(x.connection, window)
    x.sync()
    assert x.window_title(window) == title.decode()
    assert not x.focus_browser('wrong-nonce')
    assert x.focus_browser('test-nonce')
  finally:
    x.close()
    for buffer in buffers:
      buffer.close()
    for fd in fds:
      os.close(fd)
