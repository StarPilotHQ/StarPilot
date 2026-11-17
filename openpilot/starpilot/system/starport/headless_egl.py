"""EGL pbuffer context for rendering the StarPilot UI without a window or display ownership.

The Qualcomm EGL on AGNOS needs a GBM native display (``EGL_DEFAULT_DISPLAY`` is
not supported). Opening the DRM *render* node does not take DRM master or touch
the comma's own scanout, so the normal UI keeps the physical screen.

Adapted from yummydirtx/openpilot ``tools/starport/native_egl.py`` (MIT),
pinned at 672a16f6183567c0ada53654f8527d97e1a483fa.
"""

from __future__ import annotations

import ctypes as C
import os
import time

RENDER_NODE = "/dev/dri/renderD128"
EGL_OPENGL_ES_API = 0x30A0
EGL_CONTEXT_CLIENT_VERSION = 0x3098
EGL_NONE = 0x3038
# A projection context must confirm LOW priority before issuing GL work.
EGL_CONTEXT_PRIORITY_LEVEL_IMG = 0x3100
EGL_CONTEXT_PRIORITY_LOW_IMG = 0x3103
MIN_FRAME_TIME, MAX_FRAME_TIME = 0.001, 0.1  # seconds, for the headless raylib clock


GL_FRAMEBUFFER = 0x8D40
GL_RGBA, GL_UNSIGNED_BYTE = 0x1908, 0x1401
GL_PIXEL_PACK_BUFFER = 0x88EB
GL_STREAM_READ = 0x88E1
GL_MAP_READ_BIT = 0x0001
GL_SYNC_GPU_COMMANDS_COMPLETE = 0x9117
GL_SYNC_FLUSH_COMMANDS_BIT = 0x0001
GL_TIMEOUT_EXPIRED, GL_WAIT_FAILED = 0x911B, 0x911D
FENCE_TIMEOUT_NS = 200_000_000


# ANDROID AUTO VIDEO PERFORMANCE GUARDRAIL: The async PBO/fence lifecycle overlaps GPU readback with CPU work without queuing frames.
# Altering this can degrade video smoothness or introduce lag on Android Auto.
# Do not change it autonomously; require an explicit user request specifically for this video optimization.
class FrameReadback:
  """Read RGBA framebuffers into one packed frame, optionally without stalling on the GPU.

  ``start`` reads each region (framebuffer, width, height, byte offset) of the
  frame; ``finish`` returns the pixels, valid until ``release``.

  Synchronous: ``start`` is glReadPixels into memory reused for the session;
  the call waits for the GPU to finish the frame. (Raylib's own texture readback
  creates a framebuffer and allocates an image every call.)

  Asynchronous: ``start`` queues the reads into a pixel-pack buffer behind a
  fence and returns at once. ``finish`` waits for the fence, normally long
  signaled because the caller does the next frame's CPU work in between, and
  maps the buffer. One frame is in flight at a time.
  """

  def __init__(self, size: int, asynchronous: bool = False):
    self.size, self.asynchronous = size, asynchronous
    self.pending = self._mapped = False
    self._fence = None
    gl = self.gl = C.CDLL("libGLESv2.so")
    signatures = {
      "glBindFramebuffer": (None, [C.c_uint, C.c_uint]),
      "glReadPixels": (None, [C.c_int] * 4 + [C.c_uint, C.c_uint, C.c_void_p]),
      "glGenBuffers": (None, [C.c_int, C.POINTER(C.c_uint)]),
      "glDeleteBuffers": (None, [C.c_int, C.POINTER(C.c_uint)]),
      "glBindBuffer": (None, [C.c_uint, C.c_uint]),
      "glBufferData": (None, [C.c_uint, C.c_ssize_t, C.c_void_p, C.c_uint]),
      "glMapBufferRange": (C.c_void_p, [C.c_uint, C.c_ssize_t, C.c_ssize_t, C.c_uint]),
      "glUnmapBuffer": (C.c_ubyte, [C.c_uint]),
      "glFenceSync": (C.c_void_p, [C.c_uint, C.c_uint]),
      "glClientWaitSync": (C.c_uint, [C.c_void_p, C.c_uint, C.c_uint64]),
      "glDeleteSync": (None, [C.c_void_p]),
      "glFlush": (None, []),
      "glFinish": (None, []),
      "glGetError": (C.c_uint, []),
    }
    for name, (result, args) in signatures.items():
      function = getattr(gl, name)
      function.restype, function.argtypes = result, args
    self._buffer = C.c_uint(0)
    if asynchronous:
      gl.glGenBuffers(1, C.byref(self._buffer))
      gl.glBindBuffer(GL_PIXEL_PACK_BUFFER, self._buffer)
      gl.glBufferData(GL_PIXEL_PACK_BUFFER, size, None, GL_STREAM_READ)
      gl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
      self.pixels = None
    else:
      self._storage = (C.c_ubyte * size)()
      self.pixels = memoryview(self._storage).cast("B")

  def start(self, regions: list[tuple[int, int, int, int]]) -> None:
    # Called with the default framebuffer bound (after end_texture_mode()). RGBA
    # rows are multiples of four bytes, so every GLES pack alignment works.
    gl = self.gl
    if self.pending:
      raise RuntimeError("Previous frame was not finished")
    if self.asynchronous:
      gl.glBindBuffer(GL_PIXEL_PACK_BUFFER, self._buffer)
    base = 0 if self.asynchronous else C.addressof(self._storage)
    try:
      for framebuffer, width, height, offset in regions:
        if offset + width * height * 4 > self.size:
          raise ValueError("Readback region outside the frame")
        gl.glBindFramebuffer(GL_FRAMEBUFFER, framebuffer)
        gl.glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, C.c_void_p(base + offset))
    finally:
      gl.glBindFramebuffer(GL_FRAMEBUFFER, 0)
      if self.asynchronous:
        gl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
    if self.asynchronous:
      self._fence = gl.glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
      gl.glFlush()  # start the GPU on it now, not at the next implicit flush
    self.pending = True

  def ready(self) -> bool:
    """Never waits: True when ``finish`` would return at once (always, for synchronous readback).

    The GPU context is LOW priority, so a frame can queue behind the driving
    model for ~30 ms. Polling this while waiting for the next camera frame
    publishes the frame as soon as it lands without blocking the next frame's
    CPU work behind it."""
    if not self.pending:
      return False
    if not self.asynchronous or self._fence is None:
      return True
    # A failed wait also counts as ready: finish() then reports it and drops the frame.
    return self.gl.glClientWaitSync(self._fence, 0, 0) != GL_TIMEOUT_EXPIRED

  def gpu_finish(self) -> None:
    """Wait for all queued GPU work (diagnostics only; it defeats asynchronous readback)."""
    self.gl.glFinish()

  def finish(self) -> memoryview:
    if not self.pending:
      raise RuntimeError("No frame to finish")
    if not self.asynchronous:
      assert self.pixels is not None
      return self.pixels
    gl = self.gl
    fence, self._fence = self._fence, None
    result = gl.glClientWaitSync(fence, GL_SYNC_FLUSH_COMMANDS_BIT, FENCE_TIMEOUT_NS)
    gl.glDeleteSync(fence)
    if result in (GL_TIMEOUT_EXPIRED, GL_WAIT_FAILED):
      self.pending = False
      raise RuntimeError(f"GPU readback did not complete ({result:#x}, GL error {gl.glGetError():#x})")
    gl.glBindBuffer(GL_PIXEL_PACK_BUFFER, self._buffer)
    address = gl.glMapBufferRange(GL_PIXEL_PACK_BUFFER, 0, self.size, GL_MAP_READ_BIT)
    if not address:
      gl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
      self.pending = False
      raise RuntimeError("Could not map the readback buffer")
    self._mapped = True
    return memoryview((C.c_ubyte * self.size).from_address(address)).cast("B")

  def release(self) -> None:
    """Done with the pixels from ``finish`` (or drop an unfinished frame)."""
    gl = self.gl
    if self._mapped:
      gl.glUnmapBuffer(GL_PIXEL_PACK_BUFFER)
      gl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
      self._mapped = False
    if self._fence is not None:
      gl.glDeleteSync(self._fence)
      self._fence = None
    self.pending = False

  def fall_back_to_sync(self) -> None:
    """Read back synchronously from now on, dropping any frame in flight."""
    self.release()
    if self._buffer.value:
      self.gl.glDeleteBuffers(1, C.byref(self._buffer))
      self._buffer = C.c_uint(0)
    self.asynchronous = False
    self._storage = (C.c_ubyte * self.size)()
    self.pixels = memoryview(self._storage).cast("B")

  def close(self) -> None:
    self.release()
    if self._buffer.value:
      self.gl.glDeleteBuffers(1, C.byref(self._buffer))
      self._buffer = C.c_uint(0)


class HeadlessContext:
  def __init__(self, width: int, height: int):
    self.display = self.context = self.surface = self.gbm_device = self.drm_fd = None
    self._rlgl_ready = False
    try:
      self._initialize(width, height)
    except BaseException:
      self.close()
      raise

  def _initialize(self, width: int, height: int):
    self.egl = C.CDLL("libEGL.so")
    signatures = {
      "eglGetDisplay": (C.c_void_p, [C.c_void_p]),
      "eglInitialize": (C.c_uint, [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]),
      "eglBindAPI": (C.c_uint, [C.c_uint]),
      "eglChooseConfig": (C.c_uint, [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_void_p), C.c_int, C.POINTER(C.c_int)]),
      "eglCreatePbufferSurface": (C.c_void_p, [C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]),
      "eglCreateContext": (C.c_void_p, [C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]),
      "eglQueryContext": (C.c_uint, [C.c_void_p, C.c_void_p, C.c_int, C.POINTER(C.c_int)]),
      "eglMakeCurrent": (C.c_uint, [C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]),
      "eglDestroySurface": (C.c_uint, [C.c_void_p, C.c_void_p]),
      "eglDestroyContext": (C.c_uint, [C.c_void_p, C.c_void_p]),
      "eglTerminate": (C.c_uint, [C.c_void_p]),
      "eglGetError": (C.c_int, []),
    }
    for name, (result, args) in signatures.items():
      function = getattr(self.egl, name)
      function.restype, function.argtypes = result, args
    self.gbm = C.CDLL("libgbm.so")
    self.gbm.gbm_create_device.argtypes = [C.c_int]
    self.gbm.gbm_create_device.restype = C.c_void_p
    self.gbm.gbm_device_destroy.argtypes = [C.c_void_p]
    self.display = self.context = self.surface = None
    self.drm_fd = os.open(RENDER_NODE, os.O_RDWR | os.O_CLOEXEC)
    self.gbm_device = self.gbm.gbm_create_device(self.drm_fd)
    self._check(self.gbm_device, "GBM device")
    self.display = self.egl.eglGetDisplay(self.gbm_device)
    major, minor, count, config = C.c_int(), C.c_int(), C.c_int(), C.c_void_p()
    self._check(self.egl.eglInitialize(self.display, C.byref(major), C.byref(minor)), "initialize")
    self._check(self.egl.eglBindAPI(EGL_OPENGL_ES_API), "bind GLES")
    # SURFACE_TYPE=PBUFFER, RENDERABLE_TYPE=ES3, RGBA8888
    attrs = (C.c_int * 13)(0x3033, 1, 0x3040, 0x0040, 0x3024, 8, 0x3023, 8, 0x3022, 8, 0x3021, 8, 0x3038)
    self._check(self.egl.eglChooseConfig(self.display, attrs, C.byref(config), 1, C.byref(count)) and count.value, "config")
    self.surface = self.egl.eglCreatePbufferSurface(self.display, config, (C.c_int * 5)(0x3057, width, 0x3056, height, 0x3038))
    self._check(self.surface, "pbuffer")
    self.context = self.egl.eglCreateContext(self.display, config, None, (C.c_int * 5)(
      EGL_CONTEXT_CLIENT_VERSION, 3, EGL_CONTEXT_PRIORITY_LEVEL_IMG, EGL_CONTEXT_PRIORITY_LOW_IMG, EGL_NONE))
    self._check(self.context, "LOW-priority context")
    actual_priority = C.c_int(-1)
    self._check(self.egl.eglQueryContext(self.display, self.context, EGL_CONTEXT_PRIORITY_LEVEL_IMG,
                                         C.byref(actual_priority)), "query context priority")
    if actual_priority.value != EGL_CONTEXT_PRIORITY_LOW_IMG:
      raise RuntimeError(f"Headless EGL context priority is not LOW ({actual_priority.value:#x})")
    self._check(self.egl.eglMakeCurrent(self.display, self.surface, self.surface, self.context), "make current")

    from openpilot.starpilot.ui.raylib import rl
    self.rl = rl
    rl.rl_load_extensions(C.cast(self.egl.eglGetProcAddress, C.c_void_p).value)
    rl.rlgl_init(width, height)
    self._rlgl_ready = True
    rl.rl_set_framebuffer_width(width)
    rl.rl_set_framebuffer_height(height)
    texture = rl.Texture(rl.rl_get_texture_id_default(), 1, 1, 1, 7)
    rl.set_shapes_texture(texture, rl.Rectangle(0, 0, 1, 1))
    # No raylib window means no raylib clock. The caller reports each frame to
    # begin_frame() so animations advance by the real interval: the renderer runs
    # at the camera's 20 Hz onroad, the encoder's rate otherwise.
    started = time.monotonic()
    self.frame_time = 1 / 30
    self._fps_frame_time = self.frame_time
    self._last_frame: float | None = None
    rl.get_time = lambda: time.monotonic() - started
    rl.get_frame_time = lambda: self.frame_time
    rl.get_fps = lambda: max(1, round(1 / self._fps_frame_time))

  def begin_frame(self, now: float) -> None:
    """Start a frame at ``now`` (time.monotonic()): what raylib's EndDrawing/BeginDrawing would time."""
    if self._last_frame is not None:
      # Clamped like a paused animation: after a stall, move on by at most MAX_FRAME_TIME.
      self.frame_time = min(max(now - self._last_frame, MIN_FRAME_TIME), MAX_FRAME_TIME)
      # raylib's GetFPS is an average too, not a per-frame reading
      self._fps_frame_time += 0.1 * (self.frame_time - self._fps_frame_time)
    self._last_frame = now

  def pause(self) -> None:
    """No frames for a while (the car shows its own screen); the next one keeps the last interval."""
    self._last_frame = None

  def _check(self, value, operation: str) -> None:
    if not value:
      raise RuntimeError(f"Headless EGL {operation} failed: {self.egl.eglGetError():#x}")

  def close(self) -> None:
    if self._rlgl_ready:
      try:
        self.rl.rlgl_close()
      except (OSError, RuntimeError):
        pass
      finally:
        self._rlgl_ready = False
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
    if self.gbm_device:
      self.gbm.gbm_device_destroy(self.gbm_device)
      self.gbm_device = None
    if self.drm_fd is not None:
      os.close(self.drm_fd)
      self.drm_fd = None
