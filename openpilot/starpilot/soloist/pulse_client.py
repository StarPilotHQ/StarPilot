"""Blocking playback stream into the private music server, through the bundled libpulse-simple."""
from __future__ import annotations

import ctypes as C

from openpilot.starpilot.soloist import backend

UNSET = 0xffffffff
PA_STREAM_PLAYBACK = 1
PA_SAMPLE_S16LE = 3


class SampleSpec(C.Structure):
  _fields_ = [('format', C.c_int), ('rate', C.c_uint32), ('channels', C.c_uint8)]


class BufferAttr(C.Structure):
  _fields_ = [(name, C.c_uint32) for name in ('maxlength', 'tlength', 'prebuf', 'minreq', 'fragsize')]


class PulseWriter:
  def __init__(self, server: str, *, client: str = 'StarPilot phone', stream: str = 'Phone music',
               sink: str | None = backend.SINK_NAME, channels: int = 2,
               # 500 ms maximum, 200 ms target, refill before playing: the phone's stream stalls up to ~90 ms at a time.
               buffer: tuple[int, int, int, int] = (96000, 38400, UNSET, 3840), library: str = 'libpulse-simple.so.0'):
    self.lib = C.CDLL(library)
    self.lib.pa_simple_new.argtypes = [C.c_char_p, C.c_char_p, C.c_int, C.c_char_p, C.c_char_p,
                                     C.POINTER(SampleSpec), C.c_void_p, C.POINTER(BufferAttr), C.POINTER(C.c_int)]
    self.lib.pa_simple_new.restype = C.c_void_p
    self.lib.pa_simple_write.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_int)]
    self.lib.pa_simple_write.restype = C.c_int
    self.lib.pa_simple_free.argtypes = [C.c_void_p]
    self.lib.pa_simple_free.restype = None
    self.lib.pa_simple_flush.argtypes = [C.c_void_p, C.POINTER(C.c_int)]
    self.lib.pa_simple_flush.restype = C.c_int
    self.lib.pa_simple_drain.argtypes = [C.c_void_p, C.POINTER(C.c_int)]
    self.lib.pa_simple_drain.restype = C.c_int
    spec = SampleSpec(PA_SAMPLE_S16LE, 48000, channels)
    attr = BufferAttr(*buffer, UNSET)
    error = C.c_int()
    self.stream = self.lib.pa_simple_new(server.encode(), client.encode(), PA_STREAM_PLAYBACK, sink.encode() if sink else None,
                                         stream.encode(), C.byref(spec), None, C.byref(attr), C.byref(error))
    if not self.stream:
      raise RuntimeError(f'Audio output unavailable ({error.value})')

  def write(self, data: bytes):
    error = C.c_int()
    if self.lib.pa_simple_write(self.stream, data, len(data), C.byref(error)) < 0:
      raise RuntimeError(f'Audio output closed ({error.value})')

  def flush(self):
    error = C.c_int()
    self.lib.pa_simple_flush(self.stream, C.byref(error))

  def drain(self):
    error = C.c_int()
    self.lib.pa_simple_drain(self.stream, C.byref(error))

  def close(self):
    self.lib.pa_simple_free(self.stream)
