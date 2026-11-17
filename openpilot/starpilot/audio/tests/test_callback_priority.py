from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.audio import native_playback as audio


def environment():
  enable = Mock()
  library = NS(PaAlsa_EnableRealtimeScheduling=enable)
  sd = NS(query_devices=Mock(return_value={'hostapi': 0}),
          query_hostapis=Mock(return_value={'name': 'ALSA'}),
          _libname='same-portaudio-library', _ffi=NS(cast=Mock(return_value=1234)))
  stream = NS(device=2, _ptr=object())
  return sd, stream, library, enable


def test_requests_only_alsa_callback_realtime():
  sd, stream, library, enable = environment()
  with patch.object(audio.sys, 'platform', 'linux'), patch.object(audio.ctypes, 'CDLL', return_value=library) as load:
    assert audio.request_alsa_realtime(stream, sd)
  load.assert_called_once_with(sd._libname)
  sd.query_devices.assert_called_once_with(2, 'output')
  sd._ffi.cast.assert_called_once_with('uintptr_t', stream._ptr)
  enable.assert_called_once_with(1234, 1)
  assert enable.argtypes == [audio.ctypes.c_void_p, audio.ctypes.c_int]
  assert enable.restype is None


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_other_platforms_do_not_call_alsa(platform):
  sd, stream, _, _ = environment()
  with patch.object(audio.sys, 'platform', platform), patch.object(audio.ctypes, 'CDLL') as load:
    assert not audio.request_alsa_realtime(stream, sd)
  sd.query_devices.assert_not_called()
  load.assert_not_called()


def test_other_host_api_does_not_pass_foreign_stream_to_alsa():
  sd, stream, _, _ = environment()
  sd.query_hostapis.return_value = {'name': 'JACK'}
  with patch.object(audio.sys, 'platform', 'linux'), patch.object(audio.ctypes, 'CDLL') as load:
    assert not audio.request_alsa_realtime(stream, sd)
  load.assert_not_called()


@pytest.mark.parametrize('error', [AttributeError, OSError, ValueError, TypeError, KeyError])
def test_unavailable_extension_preserves_audio(error):
  sd, stream, _, _ = environment()
  with patch.object(audio.sys, 'platform', 'linux'), patch.object(audio.ctypes, 'CDLL', side_effect=error):
    assert not audio.request_alsa_realtime(stream, sd)


def test_null_stream_is_never_passed_to_native_extension():
  sd, stream, library, enable = environment()
  sd._ffi.cast.return_value = 0
  with patch.object(audio.sys, 'platform', 'linux'), patch.object(audio.ctypes, 'CDLL', return_value=library):
    assert not audio.request_alsa_realtime(stream, sd)
  enable.assert_not_called()


def test_callback_inherits_only_unprotected_cores_and_owner_is_restored():
  sd, stream, _, _ = environment()
  calls = []
  stream.start = lambda: calls.append('start')
  def affinity(pid, cores):
    calls.append(set(cores))
  with patch.object(audio.sys, 'platform', 'linux'), \
       patch.object(audio.os, 'sched_getaffinity', create=True, return_value=set(range(8))), \
       patch.object(audio.os, 'sched_setaffinity', create=True, side_effect=affinity), \
       patch.object(audio, 'request_alsa_realtime', side_effect=lambda *args: calls.append('request') or True):
    assert audio.start_native_stream(stream, sd)
  assert calls == [{0, 1, 2, 3, 7}, 'request', 'start', set(range(8))]


@pytest.mark.parametrize('allowed', [{4}, {5, 6}, {6}])
def test_realtime_never_requested_when_only_driving_cores_are_available(allowed):
  sd, stream, _, _ = environment()
  stream.start = Mock()
  with patch.object(audio.sys, 'platform', 'linux'), \
       patch.object(audio.os, 'sched_getaffinity', create=True, return_value=allowed), \
       patch.object(audio, 'request_alsa_realtime') as request:
    assert not audio.start_native_stream(stream, sd)
  request.assert_not_called()
  stream.start.assert_called_once_with()


def test_start_failure_restores_parent_affinity():
  sd, stream, _, _ = environment()
  stream.start = Mock(side_effect=OSError('start failed'))
  with patch.object(audio.sys, 'platform', 'linux'), \
       patch.object(audio.os, 'sched_getaffinity', create=True, return_value=set(range(8))), \
       patch.object(audio.os, 'sched_setaffinity', create=True) as affinity, \
       patch.object(audio, 'request_alsa_realtime', return_value=True), pytest.raises(OSError, match='start failed'):
    audio.start_native_stream(stream, sd)
  assert affinity.call_args_list[-1].args == (0, set(range(8)))


def test_affinity_failure_never_enables_realtime():
  sd, stream, _, _ = environment()
  stream.start = Mock()
  with patch.object(audio.sys, 'platform', 'linux'), \
       patch.object(audio.os, 'sched_getaffinity', create=True, return_value=set(range(8))), \
       patch.object(audio.os, 'sched_setaffinity', create=True, side_effect=OSError), \
       patch.object(audio, 'request_alsa_realtime') as request:
    assert not audio.start_native_stream(stream, sd)
  request.assert_not_called()
  stream.start.assert_called_once_with()


def test_missing_extension_restores_affinity_before_ordinary_start():
  sd, stream, _, _ = environment()
  calls = []
  stream.start = lambda: calls.append('start')
  with patch.object(audio.sys, 'platform', 'linux'), \
       patch.object(audio.os, 'sched_getaffinity', create=True, return_value=set(range(8))), \
       patch.object(audio.os, 'sched_setaffinity', create=True, side_effect=lambda pid, cores: calls.append(set(cores))), \
       patch.object(audio, 'request_alsa_realtime', return_value=False):
    assert not audio.start_native_stream(stream, sd)
  assert calls == [{0, 1, 2, 3, 7}, set(range(8)), 'start']


def test_non_linux_start_does_not_change_scheduling():
  sd, stream, _, _ = environment()
  stream.start = Mock()
  with patch.object(audio.sys, 'platform', 'darwin'), patch.object(audio, 'request_alsa_realtime') as request:
    assert not audio.start_native_stream(stream, sd)
  request.assert_not_called()
  stream.start.assert_called_once_with()


def test_native_stream_requests_realtime_when_starting_and_closes_cleanly():
  sd, _, _, _ = environment()
  calls = []
  class Stream:
    device = 2
    _ptr = object()
    def __init__(self, **kwargs):
      calls.append('open')
    def start(self):
      calls.append('start')
    def stop(self, **kwargs):
      calls.append('stop')
    def close(self, **kwargs):
      calls.append('close')
  sd._OutputStreamBase = Stream
  sd._ffi.cast = lambda *args: 0
  native = audio.NativePlayback.__new__(audio.NativePlayback)
  native.sd = sd
  native.handle = 1234
  native.rate, native.buffer = 48000, 4096
  native.realtime_requested = False
  def start(stream, device):
    calls.append('request')
    stream.start()
    return True
  with patch.object(audio, 'start_native_stream', side_effect=start), \
       patch.object(native, 'lib', NS(audio_callback_address=lambda: 0), create=True), \
       patch.object(native, 'close', side_effect=lambda: calls.append('destroy')):
    stream = native.stream()
    assert calls == ['open']
    with stream:
      assert native.realtime_requested
      assert calls == ['open', 'request', 'start']
  assert calls == ['open', 'request', 'start', 'stop', 'close', 'destroy']
