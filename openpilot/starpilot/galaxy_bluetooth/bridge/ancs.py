"""Apple Notification Center Service client logic: parsing, commands and a per-phone state machine (no D-Bus)."""
from collections import OrderedDict, deque, namedtuple
import struct
import time
import unicodedata

SERVICE_UUID = "7905f431-b5ce-4e99-a40f-4b1e122d00d0"
NOTIFICATION_SOURCE_UUID = "9fbf120d-6301-42d9-8c58-25e699a21dbd"
CONTROL_POINT_UUID = "69d1d8f3-45e1-49a8-9821-9bbdfdaad9d9"
DATA_SOURCE_UUID = "22eac6e9-24d6-4bb5-be44-b36ace7c7bfb"

EVENT_ADDED, EVENT_MODIFIED, EVENT_REMOVED = 0, 1, 2
FLAG_SILENT, FLAG_IMPORTANT, FLAG_PRE_EXISTING = 0x01, 0x02, 0x04
COMMAND_NOTIFICATION_ATTRIBUTES, COMMAND_APP_ATTRIBUTES = 0, 1
ATTR_APP_IDENTIFIER, ATTR_TITLE, ATTR_SUBTITLE, ATTR_MESSAGE = 0, 1, 2, 3
APP_ATTR_DISPLAY_NAME = 0
TITLE_MAX, SUBTITLE_MAX, MESSAGE_MAX = 120, 80, 500

CATEGORY_OTHER, CATEGORY_INCOMING_CALL, CATEGORY_MISSED_CALL, CATEGORY_VOICEMAIL = 0, 1, 2, 3
CATEGORY_SOCIAL, CATEGORY_SCHEDULE, CATEGORY_EMAIL, CATEGORY_NEWS = 4, 5, 6, 7
CATEGORIES = {CATEGORY_SOCIAL: "message", CATEGORY_INCOMING_CALL: "call", CATEGORY_MISSED_CALL: "call",
              CATEGORY_VOICEMAIL: "call", CATEGORY_EMAIL: "email"}

LIMITS = {"app": 128, "appName": 80, "sender": 120, "text": 500}
MAX_AGE_MS = 60000
BUFFER_LIMIT = 4096

Event = namedtuple("Event", "event_id flags category count uid")


def parse_event(data):
  if len(data) != 8:
    raise ValueError("Notification Source events are 8 bytes")
  return Event(*struct.unpack("<BBBBI", bytes(data)))


def wanted(event):
  return event.event_id == EVENT_ADDED and not event.flags & (FLAG_SILENT | FLAG_PRE_EXISTING)


def category_name(category_id):
  return CATEGORIES.get(category_id, "other")


def notification_attributes_command(uid):
  return (struct.pack("<BI", COMMAND_NOTIFICATION_ATTRIBUTES, uid) + bytes([ATTR_APP_IDENTIFIER])
          + struct.pack("<BH", ATTR_TITLE, TITLE_MAX) + struct.pack("<BH", ATTR_SUBTITLE, SUBTITLE_MAX)
          + struct.pack("<BH", ATTR_MESSAGE, MESSAGE_MAX))


def app_attributes_command(app_id):
  return bytes([COMMAND_APP_ATTRIBUTES]) + app_id.encode() + b"\x00" + bytes([APP_ATTR_DISPLAY_NAME])


def clean(raw, limit):
  text = bytes(raw).decode("utf-8", "replace")
  text = "".join(" " if c in "\t\n\r\v\f  " else c for c in text)
  text = "".join(c for c in text if unicodedata.category(c) != "Cc").strip()
  return text[:limit].strip()


class Reassembler:
  """Collects one Data Source response that may arrive split over several notifications."""
  def __init__(self, limit=BUFFER_LIMIT):
    self.limit = limit
    self.reset()

  def reset(self):
    self.buffer = bytearray()
    self.command = None
    self.key = None
    self.attributes = ()

  def expect(self, command, key, attributes):
    self.reset()
    self.command, self.key, self.attributes = command, key, tuple(attributes)

  def feed(self, data):
    """-> {attribute id: bytes} once every requested attribute arrived, else None. Raises ValueError and resets on bad data."""
    if self.command is None:
      return None
    self.buffer.extend(data)
    try:
      if len(self.buffer) > self.limit:
        raise ValueError("Data Source response exceeds the buffer limit")
      return self._parse()
    except ValueError:
      self.reset()
      raise

  def _parse(self):
    buffer = self.buffer
    if not buffer:
      return None
    if buffer[0] != self.command:
      raise ValueError("Data Source command mismatch")
    if self.command == COMMAND_NOTIFICATION_ATTRIBUTES:
      if len(buffer) < 5:
        return None
      if struct.unpack("<I", buffer[1:5])[0] != self.key:
        raise ValueError("Data Source notification mismatch")
      offset = 5
    else:
      end = buffer.find(b"\x00", 1)
      if end < 0:
        if len(buffer) - 1 > len(self.key.encode()):
          raise ValueError("Data Source app mismatch")
        return None
      if bytes(buffer[1:end]) != self.key.encode():
        raise ValueError("Data Source app mismatch")
      offset = end + 1
    found = {}
    while len(found) < len(self.attributes):
      if len(buffer) < offset + 3:
        return None
      attribute, length = struct.unpack("<BH", buffer[offset:offset + 3])
      if attribute not in self.attributes or attribute in found:
        raise ValueError("Unexpected Data Source attribute")
      if len(buffer) < offset + 3 + length:
        return None
      found[attribute] = bytes(buffer[offset + 3:offset + 3 + length])
      offset += 3 + length
    self.reset()
    return found


class Client:
  """One phone: queue new notifications, fetch their attributes one request at a time and emit Galaxy payloads."""
  def __init__(self, write, emit, clock=time.monotonic, max_pending=16, timeout=3.0, cache_size=64):
    self.write = write
    self.emit = emit
    self.clock = clock
    self.timeout = timeout
    self.cache_size = cache_size
    self.pending = deque(maxlen=max_pending)
    self.names = OrderedDict()
    self.reassembler = Reassembler()
    self.current = None
    self.deadline = 0.0
    self.emitted = 0
    self.dropped = 0

  def on_notification_source(self, data):
    try:
      event = parse_event(data)
    except ValueError:
      return
    if event.event_id == EVENT_REMOVED:
      self.pending = deque((item for item in self.pending if item[0] != event.uid), maxlen=self.pending.maxlen)
    elif wanted(event):
      if len(self.pending) == self.pending.maxlen:
        self.dropped += 1
      self.pending.append((event.uid, event.category, self.clock()))
    self.pump()

  def on_data_source(self, data):
    if self.current is None:
      return
    try:
      found = self.reassembler.feed(data)
    except ValueError:
      self.abort()
      return
    if found is None:
      return
    stage, item, notification = self.current
    if stage == "notification":
      notification = found
      app = clean(found[ATTR_APP_IDENTIFIER], LIMITS["app"])
      if app and app not in self.names:
        self._request("app", item, notification, COMMAND_APP_ATTRIBUTES, app, (APP_ATTR_DISPLAY_NAME,), app_attributes_command(app))
        return
    else:
      app = clean(notification[ATTR_APP_IDENTIFIER], LIMITS["app"])
      self._remember(app, clean(found[APP_ATTR_DISPLAY_NAME], LIMITS["appName"]))
    self.current = None
    self._finish(item, notification)
    self.pump()

  def tick(self):
    if self.current is not None and self.clock() >= self.deadline:
      self.abort()
    else:
      self.pump()

  def abort(self):
    """The outstanding request failed (timeout, write error, bad response); a notification still emits without its app name."""
    current, self.current = self.current, None
    self.reassembler.reset()
    if current is not None and current[0] == "app":
      self._finish(current[1], current[2])
    elif current is not None:
      self.dropped += 1
    self.pump()

  def pump(self):
    while self.current is None and self.pending:
      item = self.pending.popleft()
      if self._age(item) > MAX_AGE_MS:
        self.dropped += 1
        continue
      self._request("notification", item, None, COMMAND_NOTIFICATION_ATTRIBUTES, item[0],
                    (ATTR_APP_IDENTIFIER, ATTR_TITLE, ATTR_SUBTITLE, ATTR_MESSAGE), notification_attributes_command(item[0]))

  def _request(self, stage, item, notification, command, key, attributes, data):
    self.current = (stage, item, notification)
    self.deadline = self.clock() + self.timeout
    self.reassembler.expect(command, key, attributes)
    self.write(data)

  def _remember(self, app, name):
    self.names[app] = name
    self.names.move_to_end(app)
    while len(self.names) > self.cache_size:
      self.names.popitem(last=False)

  def _age(self, item):
    return max(0, int((self.clock() - item[2]) * 1000))

  def _finish(self, item, notification):
    app = clean(notification[ATTR_APP_IDENTIFIER], LIMITS["app"])
    sender = clean(notification[ATTR_TITLE], LIMITS["sender"])
    text = clean(notification[ATTR_MESSAGE], LIMITS["text"]) or clean(notification[ATTR_SUBTITLE], LIMITS["text"])
    age = self._age(item)
    if not app or not (sender or text) or age > MAX_AGE_MS:
      self.dropped += 1
      return
    self.emitted += 1
    self.emit({"source": "ios", "app": app, "appName": self.names.get(app, ""), "sender": sender, "text": text,
               "category": category_name(item[1]), "ageMs": age})
