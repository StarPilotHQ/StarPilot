"""Bounded external Linux button observations for a separate action owner."""

from dataclasses import dataclass, field
import fcntl
import hashlib
from itertools import islice
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import time


DEV_ROOT = Path("/dev/input")
SYS_ROOT = Path("/sys/class/input")
EVENT_NAME = re.compile(r"event[0-9]+\Z")
MODALIAS = re.compile(r"input:b([0-9a-f]{4})v([0-9a-f]{4})p([0-9a-f]{4})e([0-9a-f]{4})", re.IGNORECASE)
EVENT = struct.Struct("@llHHi")
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_DROPPED = 3
ABS_HAT0X, ABS_HAT3Y = 16, 23
HAT_EVENT_BASE = 0x10000
EXTERNAL_BUSES = frozenset((0x0003, 0x0005))
RESCAN_NS = 2_000_000_000
MAX_AGE_NS = 250_000_000
MAX_DEVICES = 16
MAX_CANDIDATES = 64
MAX_EVENTS_DEVICE = 64
MAX_EVENTS_POLL = 256
EVIOCSCLOCKID = 0x400445A0
KEY_STATE_BYTES = 64
EVIOCGKEY = 0x80004518 | (KEY_STATE_BYTES << 16)
ABS_INFO = struct.Struct("iiiiii")


@dataclass(frozen=True)
class Press:
  device_id: str
  code: int
  timestamp_ns: int


@dataclass(frozen=True)
class _Identity:
  path: Path
  device_id: str
  name: str
  bus: int


@dataclass
class _Open:
  identity: _Identity
  fd: int
  armed_keys: set[int] = field(default_factory=set)
  pressed_keys: set[int] = field(default_factory=set)
  neutral_hats: set[int] = field(default_factory=set)
  hat_values: dict[int, int] = field(default_factory=dict)
  buffer: bytearray = field(default_factory=bytearray)


def _text(path: Path) -> str:
  try:
    return path.read_text(encoding="utf-8", errors="replace").strip()
  except OSError:
    return ""


def _bits(raw: str) -> set[int]:
  try:
    words = raw.split()
    if not words or any(not re.fullmatch(r"[0-9a-fA-F]{1,16}", word) for word in words):
      return set()
    width = struct.calcsize("L") * 8
    return {index * width + bit for index, word in enumerate(reversed(words))
            for bit in range(width) if int(word, 16) & (1 << bit)}
  except (TypeError, ValueError):
    return set()


def _button_capable(sysfs: Path) -> bool:
  capabilities = sysfs / "capabilities"
  keys = _bits(_text(capabilities / "key"))
  axes = _bits(_text(capabilities / "abs"))
  relative = _bits(_text(capabilities / "rel"))
  props = _bits(_text(sysfs / "properties"))
  if 1 in props or 2 in props or 3 in props or 0x14A in keys:
    return False
  if 0 in relative or 1 in relative or any(0x2F <= axis <= 0x3D for axis in axes):
    return False
  if any(0x110 <= code <= 0x117 for code in keys):
    return False
  return any(0 < code < 0x110 or 0x120 <= code <= 0x14F for code in keys) or \
    any(ABS_HAT0X <= axis <= ABS_HAT3Y for axis in axes)


def _external(name: str) -> tuple[Path, re.Match[str]] | None:
  """The sysfs device and modalias of a USB or Bluetooth event node."""
  if not EVENT_NAME.fullmatch(name):
    return None
  sysfs = SYS_ROOT / name / "device"
  match = MODALIAS.match(_text(sysfs / "modalias"))
  if match is None or int(match[1], 16) not in EXTERNAL_BUSES:
    return None
  return sysfs, match


def _inspect(path: Path) -> _Identity | None:
  external = _external(path.name)
  if external is None or not _button_capable(external[0]):
    return None
  sysfs, match = external
  bus, vendor, product, _version = (int(token, 16) for token in match.groups())
  name = _text(sysfs / "name") or "External input"
  uniq = _text(sysfs / "uniq")
  identity = f"{bus:04x}:{vendor:04x}:{product:04x}:{name.casefold()}:{uniq.casefold()}"
  return _Identity(path, hashlib.sha256(identity.encode()).hexdigest()[:20], name[:80], bus)


def controller_input_report() -> dict:
  """Snapshot external input interfaces, including ones omitted from Attached Controllers; names are masked."""
  devices = []
  groups: dict[str, list[str]] = {}
  salt = secrets.token_bytes(8)  # equal names match within one report only
  truncated = False
  for entry in sorted(SYS_ROOT.iterdir()):
    if (external := _external(entry.name)) is None:
      continue
    if len(devices) == MAX_CANDIDATES:
      truncated = True
      break
    device, match = external
    identity = _inspect(DEV_ROOT / entry.name)
    if identity is not None:
      groups.setdefault(identity.device_id, []).append(entry.name)
    name = _text(device / "name")
    devices.append({
      "event": entry.name, "name": f"masked-{hashlib.sha256(salt + name.encode()).hexdigest()[:8]}" if name else "", "inputId": match[0],
      "properties": _text(device / "properties"),
      "capabilities": {key: _text(device / "capabilities" / key) for key in ("ev", "key", "rel", "abs")},
      "discovery": "eligible" if identity else "filtered",
      "eventNodePresent": (DEV_ROOT / entry.name).exists(),
    })
  duplicates = {event: events for events in groups.values() if len(events) > 1 for event in events}
  for row in devices:
    if row["event"] in duplicates:
      row.update(discovery="duplicate identity", matchingEvents=duplicates[row["event"]])
  return {"externalInputInterfaces": devices, "truncated": truncated}


def _fresh(seconds: int, microseconds: int, now_ns: int) -> int | None:
  if seconds < 0 or not 0 <= microseconds < 1_000_000:
    return None
  stamp = seconds * 1_000_000_000 + microseconds * 1_000
  return stamp if 0 <= now_ns - stamp <= MAX_AGE_NS else None


def _hat_code(axis: int, value: int) -> int:
  return HAT_EVENT_BASE + (axis - ABS_HAT0X) * 2 + int(value > 0)


class InputReader:
  def __init__(self):
    self._opened: dict[Path, _Open] = {}
    self._next_scan_ns = 0
    self._closed = False

  def _remove(self, path: Path) -> None:
    opened = self._opened.pop(path, None)
    if opened is not None:
      try:
        os.close(opened.fd)
      except OSError:
        pass

  def _open(self, identity: _Identity) -> None:
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
      fd = os.open(identity.path, flags)
      try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
          return
        fcntl.ioctl(fd, EVIOCSCLOCKID, struct.pack("i", time.CLOCK_MONOTONIC))
        opened = _Open(identity, fd)
        try:
          state = fcntl.ioctl(fd, EVIOCGKEY, bytes(KEY_STATE_BYTES))
          if len(state) == KEY_STATE_BYTES:
            opened.armed_keys = {code for code in range(1, 0x150) if not state[code // 8] & (1 << (code % 8))}
        except OSError:
          pass
        for axis in range(ABS_HAT0X, ABS_HAT3Y + 1):
          try:
            state = fcntl.ioctl(fd, 0x80184540 + axis, bytes(ABS_INFO.size))
            if len(state) == ABS_INFO.size and ABS_INFO.unpack(state)[0] == 0:
              opened.neutral_hats.add(axis)
              opened.hat_values[axis] = 0
          except OSError:
            pass
        self._opened[identity.path] = opened
        fd = -1
      finally:
        if fd >= 0:
          os.close(fd)
    except OSError:
      return

  def _rescan(self, now_ns: int) -> None:
    self._next_scan_ns = now_ns + RESCAN_NS
    try:
      candidates = list(islice((path for path in DEV_ROOT.glob("event*") if EVENT_NAME.fullmatch(path.name)), MAX_CANDIDATES + 1))
      if len(candidates) > MAX_CANDIDATES:
        candidates = []
      else:
        candidates.sort()
    except OSError:
      candidates = []
    identities = [identity for path in candidates if (identity := _inspect(path)) is not None]
    counts: dict[str, int] = {}
    for identity in identities:
      counts[identity.device_id] = counts.get(identity.device_id, 0) + 1
    allowed = {identity.path: identity for identity in identities if counts[identity.device_id] == 1}
    for path, opened in list(self._opened.items()):
      if path not in allowed or opened.identity != allowed[path]:
        self._remove(path)
    for path, identity in allowed.items():
      if len(self._opened) >= MAX_DEVICES:
        break
      if path not in self._opened:
        self._open(identity)

  def _decode(self, opened: _Open, raw: bytes, now_ns: int) -> Press | None:
    seconds, microseconds, event_type, code, value = EVENT.unpack(raw)
    if event_type == EV_SYN and code == SYN_DROPPED:
      self._remove(opened.identity.path)
      return None
    stamp = _fresh(seconds, microseconds, now_ns)
    if stamp is None:
      return None
    if event_type == EV_KEY:
      if value == 0:
        opened.pressed_keys.discard(code)
        opened.armed_keys.add(code)
      elif value == 1 and code in opened.armed_keys and code not in opened.pressed_keys:
        opened.pressed_keys.add(code)
        return Press(opened.identity.device_id, code, stamp)
    elif event_type == EV_ABS and ABS_HAT0X <= code <= ABS_HAT3Y and value in (-1, 0, 1):
      if value == 0:
        opened.neutral_hats.add(code)
        opened.hat_values[code] = 0
      elif code in opened.neutral_hats and value != opened.hat_values.get(code):
        opened.hat_values[code] = value
        return Press(opened.identity.device_id, _hat_code(code, value), stamp)
    return None

  def poll(self, now_ns: int) -> list[Press]:
    if self._closed:
      return []
    if type(now_ns) is not int or now_ns < 0:
      raise ValueError("Monotonic timestamp required")
    if now_ns >= self._next_scan_ns or now_ns + RESCAN_NS < self._next_scan_ns:
      self._rescan(now_ns)
    presses: list[Press] = []
    remaining = MAX_EVENTS_POLL
    for path, opened in list(self._opened.items()):
      if remaining == 0:
        self._remove(path)
        continue
      try:
        chunk = os.read(opened.fd, EVENT.size * (min(MAX_EVENTS_DEVICE, remaining) + 1))
      except BlockingIOError:
        continue
      except OSError:
        self._remove(path)
        continue
      if not chunk:
        self._remove(path)
        continue
      opened.buffer.extend(chunk)
      count = len(opened.buffer) // EVENT.size
      if count > min(MAX_EVENTS_DEVICE, remaining) or len(opened.buffer) > EVENT.size * MAX_EVENTS_DEVICE:
        self._remove(path)
        continue
      device_presses: list[Press] = []
      for _ in range(count):
        raw = bytes(opened.buffer[:EVENT.size])
        del opened.buffer[:EVENT.size]
        remaining -= 1
        press = self._decode(opened, raw, now_ns)
        if press is not None:
          device_presses.append(press)
        if path not in self._opened:
          break
      if path in self._opened:
        presses.extend(device_presses)
    return presses

  def devices(self) -> list[dict]:
    return [{"id": item.identity.device_id, "name": item.identity.name, "bus": item.identity.bus}
            for _, item in sorted(self._opened.items())]

  def close(self) -> None:
    if self._closed:
      return
    self._closed = True
    for path in list(self._opened):
      self._remove(path)
