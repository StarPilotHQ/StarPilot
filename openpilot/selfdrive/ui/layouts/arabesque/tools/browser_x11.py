"""Small XCB binding for the isolated Firefox worker. No GUI imports or pixel loops."""

import ctypes as C
import os
import select
import struct
import time

VERIFY_COLORS = (0xff0000, 0x00ff00, 0x0000ff, 0xffffff)


def verification_frame(buffer, width, height):
  """Find the local page's bands below chrome, allowing sidebars to shift the content viewport."""
  if len(buffer) != width * height * 4:
    return False
  row = bytearray(buffer[(height - 32) * width * 4:(height - 31) * width * 4])
  # Normalize only the scratch row's unused byte; the sentinel keeps C-level searches pixel-aligned.
  row[3::4] = b'\x7f' * width
  cursor = 0
  for color in VERIFY_COLORS:
    pixel = color.to_bytes(4, 'little')[:3]
    start = row.find((pixel + b'\x7f') * 16, cursor)
    if start < 0:
      return False
    x, cursor = start // 4 + 8, start + 64
    for y in (height - 40, height - 32, height - 24):
      offset = (y * width + x) * 4
      if buffer[offset:offset + 3] != pixel:
        return False
  return True


class Cookie(C.Structure):
  _fields_ = [('sequence', C.c_uint)]


class Iterator(C.Structure):
  _fields_ = [('data', C.c_void_p), ('rem', C.c_int), ('index', C.c_int)]


class XError(RuntimeError):
  def __init__(self, code):
    self.code = code
    super().__init__(f'XCB request failed ({code})')


class Screen(C.Structure):
  _fields_ = [('root', C.c_uint32), ('colormap', C.c_uint32), ('white', C.c_uint32), ('black', C.c_uint32), ('masks', C.c_uint32),
              ('width', C.c_uint16), ('height', C.c_uint16), ('mm_width', C.c_uint16), ('mm_height', C.c_uint16),
              ('min_maps', C.c_uint16), ('max_maps', C.c_uint16), ('visual', C.c_uint32),
              ('backing', C.c_uint8), ('save', C.c_uint8), ('depth', C.c_uint8), ('depths', C.c_uint8)]


def bind(lib, name, result, *args):
  fn = getattr(lib, 'xcb_' + name)
  fn.restype, fn.argtypes = result, args
  return fn


class X11:
  def __init__(self, display, library_dir=None):
    self.lib = C.CDLL('libxcb.so.1')
    self.shm = C.CDLL('libxcb-shm.so.0')
    self.damage = C.CDLL(str(library_dir / 'libxcb-damage.so.0') if library_dir else 'libxcb-damage.so.0')
    self.xtest = C.CDLL(str(library_dir / 'libxcb-xtest.so.0') if library_dir else 'libxcb-xtest.so.0')
    self.free = C.CDLL(None).free
    self.free.argtypes = [C.c_void_p]
    ptr, u32, u16, u8, i16 = C.c_void_p, C.c_uint32, C.c_uint16, C.c_uint8, C.c_int16
    declarations = {
      'connect': (ptr, C.c_char_p, ptr), 'disconnect': (None, ptr), 'connection_has_error': (C.c_int, ptr),
      'get_file_descriptor': (C.c_int, ptr), 'get_setup': (ptr, ptr), 'setup_roots_iterator': (Iterator, ptr),
      'setup_pixmap_formats_iterator': (Iterator, ptr), 'screen_allowed_depths_iterator': (Iterator, ptr),
      'depth_visuals_iterator': (Iterator, ptr), 'depth_next': (None, C.POINTER(Iterator)),
      'poll_for_reply': (C.c_int, ptr, C.c_uint, ptr, ptr), 'poll_for_event': (ptr, ptr), 'flush': (C.c_int, ptr),
      'generate_id': (u32, ptr), 'get_input_focus': (Cookie, ptr),
      'query_extension': (Cookie, ptr, u16, C.c_char_p),
      'get_image': (Cookie, ptr, u8, u32, i16, i16, u16, u16, u32),
      'get_image_data': (ptr, ptr), 'get_image_data_length': (C.c_int, ptr),
      'change_gc': (Cookie, ptr, u32, u32, ptr), 'create_gc': (Cookie, ptr, u32, u32, u32, ptr),
      'poly_fill_rectangle': (Cookie, ptr, u32, u32, u32, ptr), 'free_gc': (Cookie, ptr, u32),
      'get_keyboard_mapping': (Cookie, ptr, u8, u8),
      'get_keyboard_mapping_keysyms': (ptr, ptr),
      'change_keyboard_mapping': (Cookie, ptr, u8, u8, u8, ptr),
      'query_tree': (Cookie, ptr, u32), 'query_tree_children': (ptr, ptr), 'query_tree_children_length': (C.c_int, ptr),
      'get_geometry': (Cookie, ptr, u32), 'configure_window': (Cookie, ptr, u32, u16, ptr),
      'get_window_attributes': (Cookie, ptr, u32),
      'set_input_focus': (Cookie, ptr, u8, u32, u32),
      'intern_atom': (Cookie, ptr, u8, u16, C.c_char_p),
      'get_property': (Cookie, ptr, u8, u32, u32, u32, u32, u32),
      'get_property_value': (ptr, ptr), 'get_property_value_length': (C.c_int, ptr),
    }
    for name, signature in declarations.items():
      setattr(self, name, bind(self.lib, name, *signature))
    self.query_shm = bind(self.shm, 'shm_query_version', Cookie, ptr)
    self.attach_fd = bind(self.shm, 'shm_attach_fd', Cookie, ptr, u32, C.c_int32, u8)
    self.shm_image = bind(self.shm, 'shm_get_image', Cookie, ptr, u32, i16, i16, u16, u16, u32, u8, u32, u32)
    self.damage_query = bind(self.damage, 'damage_query_version', Cookie, ptr, u32, u32)
    self.damage_create = bind(self.damage, 'damage_create', Cookie, ptr, u32, u32, u8)
    self.damage_subtract = bind(self.damage, 'damage_subtract', Cookie, ptr, u32, u32, u32)
    self.test_query = bind(self.xtest, 'test_get_version', Cookie, ptr, u8, u16)
    self.fake_input = bind(self.xtest, 'test_fake_input', Cookie, ptr, u8, u8, u32, u32, i16, i16, u8)
    self.connection = self.connect(display.encode(), None)
    if not self.connection or self.connection_has_error(self.connection):
      self.close()
      raise RuntimeError('Cannot connect to private Xvfb')
    self.fd = self.get_file_descriptor(self.connection)
    setup = self.get_setup(self.connection)
    self.screen = Screen.from_buffer_copy(C.string_at(self.setup_roots_iterator(setup).data, C.sizeof(Screen)))
    self.width, self.height = self.screen.width, self.screen.height
    self.size = self.width * self.height * 4
    # Qualify exactly one layout; a guessed stride or alpha can corrupt the UI upload.
    raw = C.string_at(setup, 40)
    formats = self.setup_pixmap_formats_iterator(setup)
    fmt = [C.string_at(formats.data + i * 8, 3) for i in range(formats.rem)]
    depths, visual = self.screen_allowed_depths_iterator(self.setup_roots_iterator(setup).data), None
    while depths.rem:
      visuals = self.depth_visuals_iterator(depths.data)
      for i in range(visuals.rem):
        candidate = C.string_at(visuals.data + i * 24, 24)
        if struct.unpack_from('=I', candidate)[0] == self.screen.visual:
          visual = candidate
      self.depth_next(C.byref(depths))
    if (raw[30] != 0 or self.screen.depth != 24 or bytes((24, 32, 32)) not in fmt or visual is None or
        visual[4] != 4 or struct.unpack_from('=III', visual, 8) != (0xff0000, 0xff00, 0xff)):
      self.close()
      raise RuntimeError('Unsupported Xvfb pixels (requires little-endian 24-bit BGRX)')
    self.damage_event = self.extension(b'DAMAGE')
    self.extension(b'XTEST')
    for cookie in (self.damage_query(self.connection, 1, 1), self.test_query(self.connection, 2, 2)):
      self.free(self.wait(cookie))
    self.damage_id = self.generate_id(self.connection)
    self.damage_create(self.connection, self.damage_id, self.screen.root, 3)  # NonEmpty
    self.sync()
    self.keycodes = self.keyboard_mapping(raw[34], raw[35])
    name = b'_NET_WM_NAME'
    reply = self.wait(self.intern_atom(self.connection, 0, len(name), name))
    try:
      self.title_atom = struct.unpack_from('=I', C.string_at(reply, 12), 8)[0]
    finally:
      self.free(reply)

  def extension(self, name):
    reply = self.wait(self.query_extension(self.connection, len(name), name))
    try:
      present, _, event = struct.unpack_from('=BBB', C.string_at(reply, 12), 8)
      if not present:
        raise RuntimeError(f'Xvfb extension unavailable: {name.decode()}')
      return event
    finally:
      self.free(reply)

  def poll(self, cookie):
    reply, error = C.c_void_p(), C.c_void_p()
    done = self.poll_for_reply(self.connection, cookie.sequence, C.byref(reply), C.byref(error))
    if error.value:
      code = C.string_at(error, 2)[1]
      self.free(error)
      self.free(reply)
      raise XError(code)
    if self.connection_has_error(self.connection):
      raise RuntimeError('Private X display disconnected')
    if done and not reply.value:
      raise RuntimeError('XCB reply missing')
    return reply.value if done else None

  def wait(self, cookie):
    self.flush(self.connection)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
      if reply := self.poll(cookie):
        return reply
      select.select([self.fd], [], [], 0.02)
    raise TimeoutError('Xvfb reply timed out')

  def sync(self):
    self.free(self.wait(self.get_input_focus(self.connection)))
    self.damaged()  # Unchecked void errors arrive as events.

  def damaged(self):
    dirty = False
    while event := self.poll_for_event(self.connection):
      kind = C.string_at(event, 2)
      self.free(event)
      if kind[0] == 0:
        raise XError(kind[1])
      dirty |= kind[0] & 127 == self.damage_event
    return dirty

  def attach(self, fd):
    reply = self.wait(self.query_shm(self.connection))
    try:
      version = struct.unpack_from('=HH', C.string_at(reply, 12), 8)
      if version < (1, 2):
        raise RuntimeError('MIT-SHM FD requires version 1.2')
    finally:
      self.free(reply)
    segment = self.generate_id(self.connection)
    self.attach_fd(self.connection, segment, os.dup(fd), 0)  # XCB consumes this duplicate.
    self.sync()
    return segment

  def capture(self, segment=0):
    self.damage_subtract(self.connection, self.damage_id, 0, 0)
    if segment:
      cookie = self.shm_image(self.connection, self.screen.root, 0, 0, self.width, self.height, 0xffffffff, 2, segment, 0)
    else:
      cookie = self.get_image(self.connection, 2, self.screen.root, 0, 0, self.width, self.height, 0xffffffff)
    self.flush(self.connection)
    return cookie

  def finish(self, reply, buffer, shared):
    try:
      header = C.string_at(reply, 32)
      size = struct.unpack_from('=I', header, 12)[0] if shared else self.get_image_data_length(reply)
      if header[1] != 24 or size != self.size:
        raise RuntimeError('Invalid Xvfb frame dimensions')
      if not shared:
        C.memmove(C.addressof(C.c_char.from_buffer(buffer)), self.get_image_data(reply), self.size)
    finally:
      self.free(reply)

  def reference(self, buffers):
    """Check known BGRX pixels through GetImage before enabling the browser."""
    gc = self.generate_id(self.connection)
    self.create_gc(self.connection, gc, self.screen.root, 0, None)
    for i, color in enumerate(VERIFY_COLORS):
      value, rect = C.c_uint32(color), struct.pack('=hhHH', i * 8, 0, 8, 8)
      self.change_gc(self.connection, gc, 4, C.byref(value))
      self.poly_fill_rectangle(self.connection, self.screen.root, gc, 1, rect)
    self.free_gc(self.connection, gc)
    self.finish(self.wait(self.capture()), buffers[0], False)
    for i, color in enumerate(VERIFY_COLORS):
      if bytes(buffers[0][i * 32:i * 32 + 3]) != color.to_bytes(4, 'little')[:3]:
        raise RuntimeError('Reference-frame color check failed')

  def keyboard_mapping(self, minimum, maximum):
    reply = self.wait(self.get_keyboard_mapping(self.connection, minimum, maximum - minimum + 1))
    try:
      count = C.string_at(reply, 2)[1]
      values = C.cast(self.get_keyboard_mapping_keysyms(reply), C.POINTER(C.c_uint32))
      result, self._unused_keys = {}, []
      for code in range(minimum, maximum + 1):
        if not any(values[(code - minimum) * count + i] for i in range(count)):
          self._unused_keys.append(code)
        for shift in range(min(2, count)):
          symbol = values[(code - minimum) * count + shift]
          if symbol and symbol not in result:
            result[symbol] = code, shift
      return result
    finally:
      self.free(reply)

  def clear_reference(self):
    gc = self.generate_id(self.connection)
    color = C.c_uint32(0)
    self.create_gc(self.connection, gc, self.screen.root, 4, C.byref(color))
    self.poly_fill_rectangle(self.connection, self.screen.root, gc, 1, struct.pack('=hhHH', 0, 0, self.width, self.height))
    self.free_gc(self.connection, gc)
    self.sync()

  def motion(self, x, y):
    self.fake_input(self.connection, 6, 0, 0, self.screen.root, x, y, 0)

  def window_title(self, window):
    reply = self.wait(self.get_property(self.connection, 0, window, self.title_atom, 0, 0, 256))
    try:
      length = self.get_property_value_length(reply)
      if C.string_at(reply, 2)[1] != 8 or not 0 < length <= 1024:
        return ''
      return C.string_at(self.get_property_value(reply), length).decode('utf-8', errors='replace')
    finally:
      self.free(reply)

  def focus_browser(self, marker=None):
    """Place/focus the initial Firefox window once; never fight transient dialogs with per-frame focus."""
    reply = self.wait(self.query_tree(self.connection, self.screen.root))
    try:
      children = C.cast(self.query_tree_children(reply), C.POINTER(C.c_uint32))
      windows = [children[i] for i in range(self.query_tree_children_length(reply))]
    finally:
      self.free(reply)
    for window in reversed(windows):
      try:
        reply = self.wait(self.get_window_attributes(self.connection, window))
        try:
          if C.string_at(reply, 28)[26:28] != b'\x02\x00':  # Viewable, not override-redirect.
            continue
        finally:
          self.free(reply)
        reply = self.wait(self.get_geometry(self.connection, window))
        try:
          width, height = struct.unpack_from('=HH', C.string_at(reply, 24), 16)
        finally:
          self.free(reply)
        if width >= 320 and height >= 240:
          if marker is not None and marker not in self.window_title(window):
            continue
          values = (C.c_uint32 * 4)(0, 0, self.width, self.height)
          self.configure_window(self.connection, window, 15, values)
          self.set_input_focus(self.connection, 1, window, 0)
          self.sync()
          self.browser_window = window
          return True
      except XError as e:
        if e.code not in (3, 8):  # A startup window can disappear/unmap between the query and focus.
          raise
    return False

  def button(self, button, down):
    self.fake_input(self.connection, 4 if down else 5, button, 0, 0, 0, 0, 0)

  def key(self, symbol, ctrl=False, alt=False, shift=False):
    if symbol not in self.keycodes:
      if not self._unused_keys:
        raise ValueError('X keyboard character map is full; reopen Firefox')
      # shortcut: each extra Unicode symbol keeps a keycode for this session; add an IME if this pool becomes insufficient.
      code = self._unused_keys.pop()
      value = C.c_uint32(symbol)
      self.change_keyboard_mapping(self.connection, 1, code, 1, C.byref(value))
      self.keycodes[symbol] = code, 0
    code, shifted = self.keycodes[symbol]
    modifiers = [self.keycodes[value][0] for value, enabled in ((0xffe3, ctrl), (0xffe9, alt), (0xffe1, shift or shifted)) if enabled]
    for key in (*modifiers, code):
      self.fake_input(self.connection, 2, key, 0, 0, 0, 0, 0)
    for key in (code, *reversed(modifiers)):
      self.fake_input(self.connection, 3, key, 0, 0, 0, 0, 0)

  def close(self):
    if getattr(self, 'connection', None):
      self.disconnect(self.connection)
      self.connection = None
