"""Stream-owned native sample delivery; Python retains all alert and settings decisions."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import numpy as np

from openpilot.starpilot.audio.alert_volume import effective_volume

LIBRARY = Path(__file__).with_name('native') / 'libaudio_mixer.so'
TIMING_HEADER = ('abi', 'row_words', 'capacity', 'total_recorded', 'first_underflow', 'frozen',
                 'start_ordinal', 'row_count', 'callback_aborts', 'clock_error_rows')
TIMING_COLUMNS = ('ordinal', 'entry_mono_ns', 'entry_cpu_ns', 'exit_mono_ns', 'exit_cpu_ns',
                  'pa_input_bits', 'pa_current_bits', 'pa_output_bits', 'frames', 'flags', 'command',
                  'restart', 'slot', 'cursor', 'bank_revision', 'status_callbacks', 'underflows',
                  'last_flags', 'clock_error_mask', 'timeinfo_present', 'pending_bank', 'retired_bank')
# sounddevice marks its pointer closed even when Pa_CloseStream returns an error.
# Keep code, userdata and PCM alive until process exit if that fence fails.
_unclosed_streams = []


def request_alsa_realtime(stream, sd) -> bool:
  """Request PortAudio's FIFO-1 callback thread; the Python owner stays ordinary."""
  if sys.platform != 'linux':
    return False
  try:
    device = sd.query_devices(stream.device, 'output')
    if sd.query_hostapis(device['hostapi'])['name'] != 'ALSA':
      return False
    enable = ctypes.CDLL(sd._libname).PaAlsa_EnableRealtimeScheduling
    pointer = int(sd._ffi.cast('uintptr_t', stream._ptr))
  except (AttributeError, KeyError, OSError, TypeError, ValueError):
    return False
  if not pointer:
    return False
  enable.argtypes = [ctypes.c_void_p, ctypes.c_int]
  enable.restype = None
  enable(pointer, 1)
  return True


def start_native_stream(stream, sd) -> bool:
  if sys.platform != 'linux':
    stream.start()
    return False
  original = os.sched_getaffinity(0)
  callback_cores = original - {4, 5, 6}
  if callback_cores:
    try:
      os.sched_setaffinity(0, callback_cores)
    except OSError:
      pass
    else:
      try:
        if request_alsa_realtime(stream, sd):
          stream.start()
          return True
      finally:
        os.sched_setaffinity(0, original)
  stream.start()
  return False


class NativePlayback:
  def __init__(self, owner, sd, library=LIBRARY):
    from openpilot.selfdrive.ui.soundd import ALERT_VOLUME_KEYS, AudibleAlert, SAMPLE_BUFFER, SAMPLE_RATE, STARPILOT_AUTO_MIN, sound_list
    self.owner, self.sd = owner, sd
    self.volume_keys, self.specs = ALERT_VOLUME_KEYS, sound_list
    self.critical_alerts = (AudibleAlert.warningSoft, AudibleAlert.warningImmediate)
    self.buffer, self.rate, self.auto_min = SAMPLE_BUFFER, SAMPLE_RATE, STARPILOT_AUTO_MIN
    self.keys = tuple(sound_list)
    if len(self.keys) > 16:
      raise ValueError('Too many native sound slots')
    self.slots = {key: i for i, key in enumerate(self.keys)}
    self.lib = ctypes.CDLL(str(library))
    self.lib.audio_mixer_abi.restype = ctypes.c_uint
    if self.lib.audio_mixer_abi() != 3:
      raise ValueError('Native audio ABI mismatch')
    self.lib.audio_create.restype = ctypes.c_void_p
    self.lib.audio_callback_address.restype = ctypes.c_size_t
    self.lib.audio_can_publish.argtypes = [ctypes.c_void_p]
    self.lib.audio_can_publish.restype = ctypes.c_int
    self.lib.audio_collect.argtypes = [ctypes.c_void_p]
    self.lib.audio_prepare.argtypes = [ctypes.c_void_p]
    self.lib.audio_prepare.restype = ctypes.c_int
    self.lib.audio_publish_bank.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
                                          ctypes.POINTER(ctypes.c_uint), ctypes.c_uint, ctypes.c_uint64]
    self.lib.audio_publish_bank.restype = ctypes.c_int
    self.lib.audio_command.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_float, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64]
    self.lib.audio_command.restype = ctypes.c_uint64
    self.lib.audio_status.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)]
    self.lib.audio_status.restype = ctypes.c_int
    self.lib.audio_destroy.argtypes = [ctypes.c_void_p]
    self.handle = self.lib.audio_create()
    if not self.handle:
      raise MemoryError('Native audio allocation failed')
    self._close_failed = False
    self.realtime_requested = False
    self.signature = None
    self.sounds = {}
    self.builtin = {}
    self.bank_sequence = 0
    self.restart = self.command_sequence = 0
    self.last_command = None
    self.selection_sequence = 0
    self.stop_frame = 0
    self.previous_alert = self.previous_sound = None
    self.status_count = 0
    self.timing_reported = False
    self.timing_getter = getattr(self.lib, 'audio_diagnostic_snapshot', None)
    if self.timing_getter is not None:
      self.timing_getter.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint]
      self.timing_getter.restype = ctypes.c_uint
    try:
      if not self.publish():
        raise ValueError('Native sounds were not admitted')
      if not self.lib.audio_prepare(self.handle):
        raise ValueError('Native initial bank was not admitted')
    except BaseException:
      self.close()
      raise

  def publish(self):
    owner = self.owner
    # Publish a newly selected safety alert using the last admitted bank before
    # optional validation/copying of a replacement pack.
    if self.sounds:
      self.publish_command()
    signature = tuple(id(owner.loaded_sounds[key]) for key in self.keys)
    if signature != self.signature and self.lib.audio_can_publish(self.handle):
      arrays = [np.ascontiguousarray(owner.loaded_sounds[key], dtype=np.float32) for key in self.keys]
      if any(a.ndim != 1 or not 0 < len(a) <= 1_440_000 or not np.isfinite(a).all() for a in arrays):
        raise ValueError('Invalid native waveform')
      pointers = (ctypes.POINTER(ctypes.c_float) * len(arrays))(
        *(a.ctypes.data_as(ctypes.POINTER(ctypes.c_float)) for a in arrays))
      lengths = (ctypes.c_uint * len(arrays))(*(len(a) for a in arrays))
      if self.lib.audio_publish_bank(self.handle, pointers, lengths, len(arrays), self.bank_sequence + 1):
        self.bank_sequence += 1
        self.signature = signature
        self.builtin = {key: owner.pack_loader.is_builtin(owner.loaded_sounds[key]) for key in self.keys}
        self.sounds = dict(owner.loaded_sounds)
    if not self.sounds:
      return False
    self.lib.audio_collect(self.handle)
    self.publish_command()
    return True

  def publish_command(self):
    owner = self.owner
    alert = owner.current_alert
    sound = owner.current_sound
    if (alert != self.previous_alert or sound != self.previous_sound) and owner.current_sound_frame == 0:
      self.restart += 1
    self.previous_alert, self.previous_sound = alert, sound
    slot = self.slots.get(sound, 0)
    loops = self.specs[alert][1] if alert else None
    gain = owner.current_volume
    samples = self.sounds.get(sound)
    if samples is not None and self.builtin.get(sound, False):
      gain = max(gain, self.auto_min)
    key = self.volume_keys.get(alert)
    if key is not None:
      gain = effective_volume(key, owner.saved_volumes.get(key), gain,
                              immediate_ramp=owner.current_volume if alert in self.critical_alerts else None)
    length = len(samples) if samples is not None else 1
    if owner.pending_stop:
      if not self.stop_frame or (self.last_command is not None and self.last_command[6] != self.bank_sequence):
        self.stop_frame = (owner.current_sound_frame // length + 1) * length
    else:
      self.stop_frame = 0
    command = (int(alert), slot, -1 if loops is None else loops, int(owner.pending_stop),
               gain, self.restart, self.bank_sequence, self.stop_frame)
    if command == self.last_command:
      return
    sequence = self.lib.audio_command(self.handle, *command)
    if not sequence:
      raise ValueError('Invalid native alert command')
    self.command_sequence = sequence
    if self.last_command is None or command[:4] + command[5:] != self.last_command[:4] + self.last_command[5:]:
      self.selection_sequence = sequence
    self.last_command = command

  def snapshot(self):
    values = (ctypes.c_uint64 * 10)()
    return list(values) if self.lib.audio_status(self.handle, values) else None

  def sync_owner(self):
    value = self.snapshot()
    if value is None or not value[6]:
      return
    owner = self.owner
    # Volume-only publications must not hide cursor progress. A new selection
    # epoch still prevents an old callback from overwriting a newer alert.
    if value[8] == self.restart and value[9] == self.slots.get(owner.current_sound, 0):
      owner.current_sound_frame = value[0]
      if value[7] >= self.selection_sequence:
        owner.current_alert, owner.pending_stop = value[1], bool(value[2])
        # Native dismissal completion is a selection transition too.
        self.previous_alert = owner.current_alert
    if value[3] != self.status_count:
      owner.pending_stream_status = self.sd.CallbackFlags(value[5])
      self.status_count = value[3]
    owner.stream_status_count, owner.output_underflow_count = value[3], value[4]

  def report_underflow_timing(self, *, closing=False):
    """Read one fixed native trace on the service thread, after four recovery callbacks."""
    if self.timing_reported or self.timing_getter is None or (not closing and not self.owner.output_underflow_count):
      return
    values = (ctypes.c_uint64 * 714)()
    words = self.timing_getter(self.handle, values, len(values))
    if not words or (not closing and not values[5]):
      return
    if not values[4] and not values[8]:
      return
    self.timing_reported = True
    from openpilot.common.swaglog import cloudlog
    # Integer clock values and raw PortAudio double bits keep invalid clocks
    # explicit and make JSON finite. Decode host stream clocks separately.
    count = values[7]
    if values[0] != 1 or values[1] != 22 or values[2] != 32 or count > 32 or words != 10 + 22 * count:
      cloudlog.error({'event': 'soundd_underflow_timing', 'error': 'invalid_native_diagnostic'})
      return
    cloudlog.error({'event': 'soundd_underflow_timing', 'schema': 1, 'sample_rate': self.rate,
                    'block_frames': self.buffer, 'header_columns': TIMING_HEADER, 'columns': TIMING_COLUMNS,
                    'header': list(values[:10]),
                    'rows': [list(values[i:i + 22]) for i in range(10, words, 22)]})

  def stream(self):
    # Public OutputStream/RawOutputStream wrap callbacks in Python; only the existing base CData path avoids the GIL.
    callback = self.sd._ffi.cast('PaStreamCallback*', self.lib.audio_callback_address())
    userdata = self.sd._ffi.cast('void*', self.handle)
    base = self.sd._OutputStreamBase
    stream = base.__new__(base)
    wrapper = NativeStream(stream, self)
    try:
      base.__init__(stream, kind='output', wrap_callback=None, channels=1, dtype='float32',
                    samplerate=self.rate, blocksize=self.buffer, callback=callback, userdata=userdata)
    except BaseException as constructor_error:
      pointer = getattr(stream, '_ptr', self.sd._ffi.NULL)
      # Before a successful open, sounddevice holds PaStream**; after open it
      # holds PaStream*. Only the latter requires the explicit close fence.
      if self.sd._ffi.typeof(pointer) == self.sd._ffi.typeof('PaStream*') and pointer:
        try:
          wrapper.close()
        except BaseException as close_error:
          constructor_error.add_note(f'Native audio close also failed: {close_error}')
      raise
    return wrapper

  def close(self):
    if self.handle and not self._close_failed:
      self.lib.audio_destroy(self.handle)
      self.handle = None


class NativeStream:
  def __init__(self, stream, playback):
    self.stream, self.playback = stream, playback

  def __getattr__(self, name):
    return getattr(self.stream, name)

  def __enter__(self):
    try:
      self.playback.realtime_requested = start_native_stream(self.stream, self.playback.sd)
    except BaseException as start_error:
      try:
        self.close()
      except Exception as close_error:
        # The original start failure remains the caller-visible error; record
        # the close failure on that exception without freeing stream memory.
        start_error.add_note(f'Native audio close also failed: {close_error}')
      raise
    return self

  def __exit__(self, *args):
    try:
      self.stream.stop(ignore_errors=False)
    finally:
      self.close()
    return False

  def close(self):
    try:
      self.stream.close(ignore_errors=False)
    except BaseException:
      self.playback._close_failed = True
      _unclosed_streams.append(self)
      raise
    else:
      self.playback.close()
