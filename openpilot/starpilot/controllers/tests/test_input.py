import hashlib
import os
from pathlib import Path
import stat
import struct
from types import SimpleNamespace

import pytest

from openpilot.starpilot.controllers import input as source


NOW = 10_000_000_000


def bitmap(codes):
  width = struct.calcsize("L") * 8
  words = [0] * (max(codes, default=0) // width + 1)
  for code in codes:
    words[code // width] |= 1 << (code % width)
  return " ".join(f"{word:x}" for word in reversed(words))


def event(typ, code, value, stamp=NOW):
  return source.EVENT.pack(stamp // 1_000_000_000, stamp % 1_000_000_000 // 1_000, typ, code, value)


class Kernel:
  def __init__(self, monkeypatch, tmp_path):
    self.dev = tmp_path / "dev"
    self.sys = tmp_path / "sys"
    self.dev.mkdir()
    self.sys.mkdir()
    self.next_fd = 100
    self.queue = {}
    self.path_by_fd = {}
    self.closed = []
    self.fail_read = set()
    self.clocks = []
    self.held_keys = {}
    self.hats = {}
    self.unsupported_clock = set()
    self.real_open = os.open
    self.real_read = os.read
    self.real_close = os.close
    self.real_fstat = os.fstat
    monkeypatch.setattr(source, "DEV_ROOT", self.dev)
    monkeypatch.setattr(source, "SYS_ROOT", self.sys)
    monkeypatch.setattr(source.os, "open", self.open)
    monkeypatch.setattr(source.os, "read", self.read)
    monkeypatch.setattr(source.os, "close", self.close)
    monkeypatch.setattr(source.os, "fstat", self.fstat)
    monkeypatch.setattr(source.fcntl, "ioctl", self.ioctl)

  def device(self, number, *, bus=3, vendor=0x1234, product=0x5678, name="Pad", uniq="01", keys=(304,), axes=(), rel=(), props=()):
    path = self.dev / f"event{number}"
    path.touch()
    root = self.sys / path.name / "device"
    (root / "capabilities").mkdir(parents=True)
    (root / "modalias").write_text(f"input:b{bus:04X}v{vendor:04X}p{product:04X}e0001")
    (root / "name").write_text(name)
    (root / "uniq").write_text(uniq)
    for kind, codes in (("key", keys), ("abs", axes), ("rel", rel)):
      (root / "capabilities" / kind).write_text(bitmap(codes))
    (root / "properties").write_text(bitmap(props))
    self.queue[path] = bytearray()
    self.held_keys[path] = set()
    self.hats[path] = {}
    return path

  def push(self, path, *events):
    self.queue[path].extend(b"".join(events))

  def open(self, path, flags, mode=0o777, *, dir_fd=None):
    if Path(path) not in self.queue:
      return self.real_open(path, flags, mode, dir_fd=dir_fd)
    assert flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW and flags & os.O_CLOEXEC
    assert Path(path) in self.queue
    self.next_fd += 1
    self.path_by_fd[self.next_fd] = Path(path)
    return self.next_fd

  def read(self, fd, count):
    if fd not in self.path_by_fd:
      return self.real_read(fd, count)
    if fd in self.fail_read:
      raise OSError("disconnected")
    data = self.queue[self.path_by_fd[fd]]
    if not data:
      raise BlockingIOError()
    chunk = bytes(data[:count])
    del data[:count]
    return chunk

  def close(self, fd):
    if fd not in self.path_by_fd:
      return self.real_close(fd)
    self.closed.append(fd)
    self.path_by_fd.pop(fd, None)

  def fstat(self, fd):
    return SimpleNamespace(st_mode=stat.S_IFCHR) if fd in self.path_by_fd else self.real_fstat(fd)

  def ioctl(self, fd, code, value):
    if code == source.EVIOCSCLOCKID:
      if self.path_by_fd[fd] in self.unsupported_clock:
        raise OSError("clock unavailable")
      assert struct.unpack("i", value)[0] == source.time.CLOCK_MONOTONIC
      self.clocks.append(fd)
    elif code == source.EVIOCGKEY:
      state = bytearray(source.KEY_STATE_BYTES)
      for key in self.held_keys[self.path_by_fd[fd]]:
        state[key // 8] |= 1 << (key % 8)
      return bytes(state)
    elif 0x80184540 + 16 <= code <= 0x80184540 + 23:
      axis = code - 0x80184540
      value = self.hats[self.path_by_fd[fd]].get(axis, 0)
      return source.ABS_INFO.pack(value, 0, 0, 0, 0, 0)
    else:
      raise AssertionError(code)


@pytest.fixture
def kernel(monkeypatch, tmp_path):
  return Kernel(monkeypatch, tmp_path)


def test_external_identity_capability_and_duplicates(kernel):
  kernel.device(0, bus=5, name="Road Pad", uniq="ABC")
  kernel.device(1, bus=0x18, name="Internal")
  kernel.device(2, keys=(0x110,), name="Mouse")
  kernel.device(3, keys=(0x14A,), name="Touch")
  kernel.device(4, keys=(115,), name="Media keys")
  kernel.device(6, keys=(115,), rel=(0, 1), name="Mouse with media")
  kernel.device(7, keys=(115,), axes=(0x35,), name="Touch with media")
  kernel.device(8, keys=(115,), props=(1,), name="Direct touch")
  reader = source.InputReader()
  reader.poll(NOW)
  devices = reader.devices()
  expected = hashlib.sha256(b"0005:1234:5678:road pad:abc").hexdigest()[:20]
  assert devices == [{"id": expected, "name": "Road Pad", "bus": 5},
                     {"id": devices[1]["id"], "name": "Media keys", "bus": 3}]
  assert len(kernel.clocks) == 2
  duplicate = kernel.device(5, bus=5, name="Road Pad", uniq="ABC")
  reader.poll(NOW + source.RESCAN_NS)
  assert [item["name"] for item in reader.devices()] == ["Media keys"]
  assert kernel.closed
  duplicate.unlink()
  reader.poll(NOW + source.RESCAN_NS * 2)
  assert [item["name"] for item in reader.devices()] == ["Road Pad", "Media keys"]
  reader.close()
  assert len(kernel.closed) == len(kernel.clocks)
  assert reader.poll(NOW + source.RESCAN_NS * 3) == []


def test_release_required_repeats_and_fresh_timestamps(kernel):
  path = kernel.device(0)
  kernel.held_keys[path].add(304)
  reader = source.InputReader()
  reader.poll(NOW)
  kernel.push(path, event(1, 304, 1), event(1, 304, 2), event(1, 304, 0), event(1, 304, 1))
  assert reader.poll(NOW) == [source.Press(reader.devices()[0]["id"], 304, NOW)]
  kernel.push(path, event(1, 304, 1), event(1, 304, 2))
  assert reader.poll(NOW) == []
  kernel.push(path, event(1, 304, 0, NOW - 300_000_000), event(1, 304, 1))
  assert reader.poll(NOW) == []
  kernel.push(path, event(1, 304, 0, NOW + 1_000_000), event(1, 304, 0), event(1, 304, 1))
  assert reader.poll(NOW) == [source.Press(reader.devices()[0]["id"], 304, NOW)]
  reader.close()


def test_initial_neutral_key_accepts_first_press(kernel):
  path = kernel.device(0)
  reader = source.InputReader()
  reader.poll(NOW)
  kernel.push(path, event(1, 304, 1))
  assert reader.poll(NOW) == [source.Press(reader.devices()[0]["id"], 304, NOW)]
  reader.close()


def test_unsupported_monotonic_clock_never_opens_device(kernel):
  path = kernel.device(0)
  kernel.unsupported_clock.add(path)
  reader = source.InputReader()
  kernel.push(path, event(1, 304, 1))
  assert reader.poll(NOW) == []
  assert reader.devices() == []
  assert kernel.closed == [101]
  reader.close()


def test_hats_need_neutral_and_emit_edges(kernel):
  path = kernel.device(0, keys=(), axes=(16,))
  kernel.hats[path][16] = 1
  reader = source.InputReader()
  reader.poll(NOW)
  kernel.push(path, event(3, 16, 1), event(3, 16, 0), event(3, 16, 1), event(3, 16, 1), event(3, 16, -1))
  identity = reader.devices()[0]["id"]
  assert reader.poll(NOW) == [source.Press(identity, 0x10001, NOW), source.Press(identity, 0x10000, NOW)]
  reader.close()


def test_dropped_and_overflow_close_until_reopened_and_neutral(kernel):
  path = kernel.device(0)
  kernel.held_keys[path].add(304)
  reader = source.InputReader()
  reader.poll(NOW)
  kernel.push(path, event(1, 304, 0), event(1, 304, 1), event(0, 3, 0), event(1, 304, 1))
  assert reader.poll(NOW) == []
  assert reader.devices() == []
  reader.poll(NOW + source.RESCAN_NS)
  assert reader.devices()
  kernel.held_keys[path].add(304)
  assert reader.poll(NOW + source.RESCAN_NS) == []
  kernel.push(path, event(1, 304, 0, NOW + source.RESCAN_NS), event(1, 304, 1, NOW + source.RESCAN_NS))
  assert len(reader.poll(NOW + source.RESCAN_NS)) == 1
  kernel.push(path, *(event(1, 304, 0, NOW + source.RESCAN_NS) for _ in range(65)))
  assert reader.poll(NOW + source.RESCAN_NS) == []
  assert reader.devices() == []
  reader.close()


def test_one_device_error_and_disconnect_do_not_block_other(kernel):
  a = kernel.device(0, name="A")
  b = kernel.device(1, name="B")
  reader = source.InputReader()
  reader.poll(NOW)
  kernel.push(a, event(1, 304, 0), event(1, 304, 1))
  kernel.push(b, event(1, 304, 0), event(1, 304, 1))
  fd_a = next(fd for fd, path in kernel.path_by_fd.items() if path == a)
  kernel.fail_read.add(fd_a)
  assert [item["name"] for item in reader.devices()] == ["A", "B"]
  assert [press.device_id for press in reader.poll(NOW)] == [reader.devices()[0]["id"]]
  assert [item["name"] for item in reader.devices()] == ["B"]
  b.unlink()
  reader.poll(NOW + source.RESCAN_NS)
  assert reader.devices() == [{"id": reader.devices()[0]["id"], "name": "A", "bus": 3}]
  reader.close()


def test_per_device_and_global_event_limits(kernel):
  paths = [kernel.device(index, name=f"Pad {index}", uniq=str(index)) for index in range(5)]
  reader = source.InputReader()
  reader.poll(NOW)
  for path in paths:
    kernel.push(path, *(event(1, 304, value) for _ in range(32) for value in (0, 1)))
  assert len(reader.poll(NOW)) == 128
  assert len(reader.devices()) == 4
  reader.close()


def test_device_and_scan_candidate_caps(kernel):
  for index in range(17):
    kernel.device(index, name=f"Pad {index}", uniq=str(index))
  reader = source.InputReader()
  reader.poll(NOW)
  assert len(reader.devices()) == source.MAX_DEVICES
  for index in range(17, source.MAX_CANDIDATES + 1):
    kernel.device(index, name=f"Pad {index}", uniq=str(index))
  reader.poll(NOW + source.RESCAN_NS)
  assert reader.devices() == []
  reader.close()


def test_report_includes_filtered_and_duplicate_external_interfaces(monkeypatch, tmp_path):
  kernel = Kernel(monkeypatch, tmp_path)
  kernel.device(1, bus=5)
  kernel.device(2, bus=5)
  kernel.device(3, bus=5, keys=(115,), rel=(0, 1))
  kernel.device(4, bus=5, name="Media", keys=(115,))
  kernel.device(5, bus=1)
  report = source.controller_input_report()
  rows = report["externalInputInterfaces"]
  assert [row["discovery"] for row in rows] == ["duplicate identity", "duplicate identity", "filtered", "eligible"]
  assert rows[0]["matchingEvents"] == ["event1", "event2"]
  assert rows[2]["capabilities"]["rel"] == "3"
  assert not report["truncated"]
  assert not kernel.path_by_fd


def test_report_masks_names(monkeypatch, tmp_path):
  kernel = Kernel(monkeypatch, tmp_path)
  kernel.device(1, bus=5, name="Danny's Pad")
  kernel.device(2, bus=5, name="Danny's Pad", uniq="02")
  kernel.device(3, bus=5, name="Media", keys=(115,))
  report = source.controller_input_report()
  names = [row["name"] for row in report["externalInputInterfaces"]]
  assert all(name.startswith("masked-") for name in names)
  assert names[0] == names[1] != names[2]
  assert "Danny" not in str(report) and "Media" not in str(report)
  assert source.controller_input_report()["externalInputInterfaces"][0]["name"] != names[0]


def test_report_is_capped(monkeypatch, tmp_path):
  kernel = Kernel(monkeypatch, tmp_path)
  for index in range(source.MAX_CANDIDATES + 1):
    kernel.device(index, bus=5, uniq=str(index))
  report = source.controller_input_report()
  assert len(report["externalInputInterfaces"]) == source.MAX_CANDIDATES
  assert report["truncated"]
