"""Test-only Mesa surfaceless GLES context; never used by the projection service."""

import ctypes as C


class ContextGL:
  """Resolve readback entry points from the same EGL implementation as Raylib."""

  def __init__(self, egl):
    self.get_address = egl.eglGetProcAddress
    self.get_address.restype = C.c_void_p
    self.get_address.argtypes = [C.c_char_p]

  def __getattr__(self, name):
    address = self.get_address(name.encode())
    if not address:
      raise AttributeError(f"EGL did not provide {name}")
    function = C.CFUNCTYPE(None)(address)
    setattr(self, name, function)
    return function


class SoftwareEGLContext:
  def __init__(self, rl, width=64, height=64):
    self.rl = rl
    self.display = self.context = self.surface = None
    self.ready = False
    self.egl = C.CDLL("libEGL.so.1")
    signatures = {
      "eglGetPlatformDisplay": (C.c_void_p, [C.c_uint, C.c_void_p, C.c_void_p]),
      "eglInitialize": (C.c_uint, [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]),
      "eglBindAPI": (C.c_uint, [C.c_uint]),
      "eglChooseConfig": (C.c_uint, [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_void_p), C.c_int, C.POINTER(C.c_int)]),
      "eglCreatePbufferSurface": (C.c_void_p, [C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]),
      "eglCreateContext": (C.c_void_p, [C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]),
      "eglMakeCurrent": (C.c_uint, [C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]),
      "eglDestroySurface": (C.c_uint, [C.c_void_p, C.c_void_p]),
      "eglDestroyContext": (C.c_uint, [C.c_void_p, C.c_void_p]),
      "eglTerminate": (C.c_uint, [C.c_void_p]),
      "eglGetError": (C.c_uint, []),
    }
    for name, (result, args) in signatures.items():
      function = getattr(self.egl, name)
      function.restype, function.argtypes = result, args
    try:
      # EGL_PLATFORM_SURFACELESS_MESA: no DRM device, display server or hardware GPU.
      self.display = self.egl.eglGetPlatformDisplay(0x31DD, None, None)
      self.check(self.display, "surfaceless display")
      major, minor, count, config = C.c_int(), C.c_int(), C.c_int(), C.c_void_p()
      self.check(self.egl.eglInitialize(self.display, C.byref(major), C.byref(minor)), "initialize")
      self.check(self.egl.eglBindAPI(0x30A0), "bind GLES")
      attrs = (C.c_int * 13)(0x3033, 1, 0x3040, 0x0040, 0x3024, 8, 0x3023, 8, 0x3022, 8, 0x3021, 8, 0x3038)
      self.check(self.egl.eglChooseConfig(self.display, attrs, C.byref(config), 1, C.byref(count)) and count.value, "config")
      self.surface = self.egl.eglCreatePbufferSurface(self.display, config, (C.c_int * 5)(0x3057, width, 0x3056, height, 0x3038))
      self.check(self.surface, "pbuffer")
      self.context = self.egl.eglCreateContext(self.display, config, None, (C.c_int * 3)(0x3098, 3, 0x3038))
      self.check(self.context, "GLES3 context")
      self.check(self.egl.eglMakeCurrent(self.display, self.surface, self.surface, self.context), "make current")
      self.gl = ContextGL(self.egl)
      rl.rl_load_extensions(C.cast(self.egl.eglGetProcAddress, C.c_void_p).value)
      rl.rlgl_init(width, height)
      self.ready = True
      rl.rl_set_framebuffer_width(width)
      rl.rl_set_framebuffer_height(height)
      rl.set_shapes_texture(rl.Texture(rl.rl_get_texture_id_default(), 1, 1, 1, 7), rl.Rectangle(0, 0, 1, 1))
    except BaseException:
      self.close()
      raise

  def check(self, value, operation):
    if not value:
      raise RuntimeError(f"Test software EGL {operation} failed: {self.egl.eglGetError():#x}")

  def close(self):
    if self.ready:
      self.rl.rlgl_close()
      self.ready = False
    if self.display:
      self.egl.eglMakeCurrent(self.display, None, None, None)
      if self.context:
        self.egl.eglDestroyContext(self.display, self.context)
        self.context = None
      if self.surface:
        self.egl.eglDestroySurface(self.display, self.surface)
        self.surface = None
      self.egl.eglTerminate(self.display)
      self.display = None
