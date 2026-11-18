"""Car touchscreen input: decode Android Auto input events and map them onto the projected UI.

The head unit reports touches in its touchscreen coordinate space (advertised in
service discovery). They are mapped to the encoded video frame, then to the
visible content area inside the receiver's margins, and delivered as normalized
``(0..1, 0..1)`` single-pointer events. Only the first pointer is used: the
StarPilot UI is a single-touch interface.

Wire facts: mrmees/open-android-auto ``oaa/input`` (reference only), pinned at
61eab61c5f9968154ff1a80faa8c0a427b208479.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass

from openpilot.starpilot.system.starport.wire import one, parse_fields

ACTION_PRESS = 0
ACTION_RELEASE = 1
ACTION_DRAG = 2
ACTION_POINTER_DOWN = 5
ACTION_POINTER_UP = 6
MAX_INPUT_BYTES = 16 * 1024
MAX_KEYCODES = 256
DEFAULT_TOUCH_SOCKET = "/tmp/starpilot-starport-touch.sock"
TOUCH_SETUP_DOT = (0.93, 0.90)  # where the car view asks for the one setup tap, normalized in the visible area
TOUCH_SETUP_REACH = 0.12
MEDIA_KEYS = {85: "play_pause", 87: "next", 88: "previous", 126: "play", 127: "pause"}


@dataclass(frozen=True)
class TouchEvent:
  kind: str   # "down", "move", "up", "cancel"
  x: float    # normalized within the visible content area
  y: float


@dataclass(frozen=True)
class InputConfig:
  keycodes: tuple[int, ...] = ()
  touch_width: int = 0
  touch_height: int = 0


def packed_varints(values) -> list[int]:
  """Protobuf repeated varints, packed or not."""
  result: list[int] = []
  for value in values:
    if isinstance(value, int):
      result.append(value)
      continue
    pos = 0
    while pos < len(value):
      number, shift = 0, 0
      while True:
        if pos >= len(value) or shift > 63:
          raise ValueError("Truncated packed varint")
        byte = value[pos]
        pos += 1
        number |= (byte & 127) << shift
        shift += 7
        if byte < 128:
          break
      result.append(number)
    if len(result) > MAX_KEYCODES:
      raise ValueError("Too many input keycodes")
  return result


def parse_input_config(data: bytes) -> InputConfig:
  fields = parse_fields(data)
  keycodes = tuple(packed_varints(fields.get(1, [])))[:MAX_KEYCODES]
  width = height = 0
  touch = one(fields, 2)
  if isinstance(touch, bytes):
    screen = parse_fields(touch)
    width, height = int(one(screen, 1, 0) or 0), int(one(screen, 2, 0) or 0)
  return InputConfig(keycodes, width, height)


class TouchMapper:
  """Stateful single-pointer decoder for one projection session."""

  def __init__(self, config: InputConfig, video_width: int, video_height: int, margin_width: int, margin_height: int, *,
               content_left: int | None = None, content_top: int | None = None):
    self.touch_width = config.touch_width or video_width
    self.touch_height = config.touch_height or video_height
    self.video_width, self.video_height = video_width, video_height
    self.left = margin_width // 2 if content_left is None else content_left
    self.top = margin_height // 2 if content_top is None else content_top
    self.content_width, self.content_height = video_width - margin_width, video_height - margin_height
    self.pointer: int | None = None
    self.content_space = bool(margin_width or margin_height) and self._same_shape(self.content_width, self.content_height) \
      and not self._same_shape(video_width, video_height)
    # Some head units report visible-video pixels, not their advertised touch size.
    self.content_pixels = self.content_space
    self.units_switched = False
    self.calibrating = False
    self.calibrated: str | None = None
    self.key = f"{self.touch_width}x{self.touch_height}@{video_width}x{video_height}-{margin_width}x{margin_height}"

  def _same_shape(self, width: int, height: int) -> bool:
    return abs(self.touch_width * height / (self.touch_height * width) - 1) < 0.02

  def _check_units(self, locations) -> None:
    if self.content_pixels and any(x > self.content_width or y > self.content_height for _, x, y in locations):
      self.content_pixels = False
      self.units_switched = True

  def _calibrate(self, x: int, y: int) -> None:
    """Read the setup tap both ways; the reading nearer the dot names the units."""
    dx, dy = TOUCH_SETUP_DOT
    pixels = ((x / self.content_width - dx) ** 2 + (y / self.content_height - dy) ** 2) ** 0.5
    panel = ((x / self.touch_width - dx) ** 2 + (y / self.touch_height - dy) ** 2) ** 0.5
    exceeded = x > self.content_width or y > self.content_height
    units = "touchscreen" if exceeded or panel < pixels else "pixels"
    if (panel if units == "touchscreen" else pixels) > TOUCH_SETUP_REACH:
      return  # not a tap on the dot: keep asking
    self.content_pixels = units == "pixels"
    self.calibrating, self.calibrated = False, units

  def _video_point(self, x: int, y: int) -> tuple[float, float]:
    if self.content_pixels:
      return self.left + x, self.top + y
    if self.content_space:
      return self.left + x * self.content_width / self.touch_width, self.top + y * self.content_height / self.touch_height
    return x * self.video_width / self.touch_width, y * self.video_height / self.touch_height

  def _normalize(self, x: int, y: int) -> tuple[float, float]:
    vx, vy = self._video_point(x, y)
    nx = (vx - self.left) / self.content_width
    ny = (vy - self.top) / self.content_height
    return min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0)

  def _inside(self, x: int, y: int) -> bool:
    vx, vy = self._video_point(x, y)
    return self.left <= vx < self.left + self.content_width and self.top <= vy < self.top + self.content_height

  def reset(self) -> list[TouchEvent]:
    """Focus loss or session end: withdraw a held touch rather than clicking."""
    if self.pointer is None:
      return []
    self.pointer = None
    return [TouchEvent("cancel", 0.0, 0.0)]

  def decode(self, data: bytes) -> list[TouchEvent]:
    if len(data) > MAX_INPUT_BYTES:
      raise ValueError("Input message exceeds limit")
    touch = one(parse_fields(data), 3)
    if not isinstance(touch, bytes):
      return []  # buttons, rotary and touchpad are not used by the projected UI
    fields = parse_fields(touch)
    locations = []
    for raw in fields.get(1, [])[:10]:
      if isinstance(raw, bytes):
        loc = parse_fields(raw)
        locations.append((int(one(loc, 3, 0) or 0), int(one(loc, 1, 0) or 0), int(one(loc, 2, 0) or 0)))
    action = one(fields, 3, ACTION_PRESS)
    index = int(one(fields, 2, 0) or 0)
    if not locations:
      return []
    if self.calibrating:
      if action == ACTION_PRESS:
        self._calibrate(locations[0][1], locations[0][2])
      return []
    self._check_units(locations)
    events: list[TouchEvent] = []
    if action == ACTION_PRESS and self.pointer is None:
      pointer, x, y = locations[0]
      if self._inside(x, y):
        self.pointer = pointer
        events.append(TouchEvent("down", *self._normalize(x, y)))
    elif self.pointer is not None:
      tracked = next(((x, y) for pointer, x, y in locations if pointer == self.pointer), None)
      lifted = action == ACTION_RELEASE or (action == ACTION_POINTER_UP and index < len(locations) and
                                            locations[index][0] == self.pointer)
      if tracked is None:
        events.extend(self.reset())
      elif lifted:
        self.pointer = None
        events.append(TouchEvent("up", *self._normalize(*tracked)))
      elif action in (ACTION_DRAG, ACTION_POINTER_DOWN, ACTION_POINTER_UP):
        events.append(TouchEvent("move", *self._normalize(*tracked)))
    return events


class KeyTracker:
  """Media button presses, one action per press however long the button is held."""

  def __init__(self):
    self.held: set[int] = set()

  def decode(self, data: bytes) -> list[str]:
    if len(data) > MAX_INPUT_BYTES:
      raise ValueError("Input message exceeds limit")
    buttons = one(parse_fields(data), 4)
    if not isinstance(buttons, bytes):
      return []
    actions = []
    for raw in parse_fields(buttons).get(1, [])[:16]:
      if not isinstance(raw, bytes):
        continue
      key = parse_fields(raw)
      code = int(one(key, 1, 0) or 0)
      if code not in MEDIA_KEYS:
        continue
      if not one(key, 2, 0):
        self.held.discard(code)
      elif code not in self.held:
        self.held.add(code)
        actions.append(MEDIA_KEYS[code])
    return actions


class TouchSender:
  """starportd side: best-effort datagrams to the car-UI renderer."""

  def __init__(self, path: str = DEFAULT_TOUCH_SOCKET):
    self.path = path
    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    self.sock.setblocking(False)

  def send(self, events: list[TouchEvent]) -> None:
    for event in events:
      try:
        self.sock.sendto(json.dumps({"kind": event.kind, "x": event.x, "y": event.y}).encode(), self.path)
      except OSError:
        pass  # renderer not listening (mirror view or restarting): input is dropped, never queued

  def close(self) -> None:
    self.sock.close()


class TouchReceiver:
  """Renderer side: drain queued touches without blocking the render loop."""

  def __init__(self, path: str = DEFAULT_TOUCH_SOCKET):
    self.path = path
    try:
      os.unlink(path)
    except FileNotFoundError:
      pass
    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    self.sock.bind(path)
    os.chmod(path, 0o600)
    self.sock.setblocking(False)

  def drain(self, limit: int = 64) -> list[TouchEvent]:
    events = []
    while len(events) < limit:
      try:
        data = self.sock.recv(512)
      except BlockingIOError:
        break
      try:
        raw = json.loads(data)
        kind, x, y = str(raw["kind"]), float(raw["x"]), float(raw["y"])
      except (ValueError, KeyError, TypeError):
        continue
      if kind in ("down", "move", "up", "cancel", "setup") and 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
        events.append(TouchEvent(kind, x, y))
    return events

  def close(self) -> None:
    self.sock.close()
    try:
      os.unlink(self.path)
    except FileNotFoundError:
      pass
