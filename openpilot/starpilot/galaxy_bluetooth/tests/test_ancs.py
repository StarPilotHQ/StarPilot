import asyncio
import base64
import json
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor"))
from dbus_next import DBusError, Message, MessageType, Variant
import ancs
from ancs_dbus import BluezGatt, Poster, Watcher, find_ancs, supervise
from server import BondAgent

ADAPTER = "/org/bluez/hci0"
PHONE = ADAPTER + "/dev_4A_11_22_33_44_55"


class Clock:
  def __init__(self):
    self.now = 100.0
  def __call__(self):
    return self.now


def event(event_id=0, flags=0, category=4, count=1, uid=7):
  return struct.pack("<BBBBI", event_id, flags, category, count, uid)


def attr(attribute, value):
  return struct.pack("<BH", attribute, len(value)) + value


def notification_response(uid=7, app=b"com.apple.MobileSMS", title=b"Alex", subtitle=b"", message=b"On my way"):
  return (struct.pack("<BI", 0, uid) + attr(0, app) + attr(1, title) + attr(2, subtitle) + attr(3, message))


def app_response(app=b"com.apple.MobileSMS", name=b"Messages"):
  return b"\x01" + app + b"\x00" + attr(0, name)


class ParserTests(unittest.TestCase):
  def test_uuids_match_apple_spec(self):
    self.assertEqual(ancs.SERVICE_UUID, "7905F431-B5CE-4E99-A40F-4B1E122D00D0".lower())
    self.assertEqual(ancs.NOTIFICATION_SOURCE_UUID, "9FBF120D-6301-42D9-8C58-25E699A21DBD".lower())
    self.assertEqual(ancs.CONTROL_POINT_UUID, "69D1D8F3-45E1-49A8-9821-9BBDFDAAD9D9".lower())
    self.assertEqual(ancs.DATA_SOURCE_UUID, "22EAC6E9-24D6-4BB5-BE44-B36ACE7C7BFB".lower())

  def test_event_parsing_and_filter(self):
    parsed = ancs.parse_event(struct.pack("<BBBBI", 0, 2, 6, 3, 0x01020304))
    self.assertEqual(parsed, (0, 2, 6, 3, 0x01020304))
    self.assertTrue(ancs.wanted(parsed))  # Important (0x02) is still delivered
    self.assertFalse(ancs.wanted(ancs.parse_event(event(flags=ancs.FLAG_SILENT))))
    self.assertFalse(ancs.wanted(ancs.parse_event(event(flags=ancs.FLAG_PRE_EXISTING))))
    self.assertFalse(ancs.wanted(ancs.parse_event(event(event_id=1))))
    self.assertFalse(ancs.wanted(ancs.parse_event(event(event_id=2))))
    for bad in (b"", bytes(7), bytes(9)):
      with self.assertRaises(ValueError):
        ancs.parse_event(bad)

  def test_category_ids_from_apple_ancs_spec(self):
    # Apple ANCS CategoryID: 0 Other, 1 IncomingCall, 2 MissedCall, 3 Voicemail, 4 Social, 5 Schedule,
    # 6 Email, 7 News, 8 HealthAndFitness, 9 BusinessAndFinance, 10 Location, 11 Entertainment.
    expected = {0: "other", 1: "call", 2: "call", 3: "call", 4: "message", 5: "other", 6: "email", 7: "other",
                8: "other", 9: "other", 10: "other", 11: "other", 200: "other"}
    for category, name in expected.items():
      self.assertEqual(ancs.category_name(category), name)

  def test_commands(self):
    self.assertEqual(ancs.notification_attributes_command(0x0A0B0C0D),
                     bytes([0, 0x0D, 0x0C, 0x0B, 0x0A, 0, 1, 120, 0, 2, 80, 0, 3, 0xF4, 0x01]))
    self.assertEqual(ancs.app_attributes_command("com.a"), b"\x01com.a\x00\x00")

  def test_clean_utf8_and_controls(self):
    self.assertEqual(ancs.clean("Zo\u00eb".encode()[:-1], 10), "Zo\ufffd")  # iOS cut a multibyte tail
    self.assertEqual(ancs.clean(b"\xff\xfe", 10), "��")
    self.assertEqual(ancs.clean(b"a\x00b\x1bc\nd\te\x7f", 20), "abc d e")
    self.assertEqual(ancs.clean("\U0001F600".encode() * 3, 2), "\U0001F600\U0001F600")  # characters, not bytes
    self.assertEqual(ancs.clean(b"  hi  ", 10), "hi")


class ReassemblerTests(unittest.TestCase):
  def test_fragmented_including_split_header(self):
    r = ancs.Reassembler()
    r.expect(0, 7, (0, 1, 2, 3))
    data = notification_response()
    for i in range(len(data) - 1):
      self.assertIsNone(r.feed(data[i:i + 1]))
    found = r.feed(data[-1:])
    self.assertEqual(found[0], b"com.apple.MobileSMS")
    self.assertEqual(found[3], b"On my way")

  def test_app_header_without_nul_yet(self):
    r = ancs.Reassembler()
    r.expect(1, "com.a", (0,))
    self.assertIsNone(r.feed(b"\x01com"))
    self.assertEqual(r.feed(b".a\x00" + attr(0, b"A")), {0: b"A"})

  def test_mismatch_and_overflow_reset(self):
    r = ancs.Reassembler()
    for expected, data in ((7, notification_response(uid=8)), (7, b"\x01xx")):
      r.expect(0, expected, (0, 1, 2, 3))
      with self.assertRaises(ValueError):
        r.feed(data)
      self.assertIsNone(r.command)
    r.expect(1, "com.a", (0,))
    with self.assertRaises(ValueError):
      r.feed(b"\x01com.b\x00")
    r.expect(1, "a", (0,))
    with self.assertRaises(ValueError):
      r.feed(b"\x01abc")  # longer than the app id without a NUL
    small = ancs.Reassembler(limit=32)
    small.expect(0, 7, (0, 1, 2, 3))
    with self.assertRaises(ValueError):
      small.feed(notification_response(message=b"x" * 40))
    self.assertEqual(small.buffer, bytearray())

  def test_unexpected_attribute_and_idle(self):
    r = ancs.Reassembler()
    self.assertIsNone(r.feed(b"\x00"))  # nothing expected
    r.expect(0, 7, (0, 1))
    with self.assertRaises(ValueError):
      r.feed(struct.pack("<BI", 0, 7) + attr(3, b"x"))


class ClientTests(unittest.TestCase):
  def setUp(self):
    self.clock = Clock()
    self.writes = []
    self.emitted = []
    self.client = ancs.Client(self.writes.append, self.emitted.append, clock=self.clock)

  def test_full_flow_with_app_name_lookup_and_cache(self):
    self.client.on_notification_source(event(category=4, uid=7))
    self.assertEqual(self.writes, [ancs.notification_attributes_command(7)])
    self.clock.now += 0.2
    data = notification_response()
    self.client.on_data_source(data[:10])
    self.client.on_data_source(data[10:])
    self.assertEqual(self.writes[-1], ancs.app_attributes_command("com.apple.MobileSMS"))
    self.client.on_data_source(app_response())
    self.assertEqual(self.emitted, [{"source": "ios", "app": "com.apple.MobileSMS", "appName": "Messages", "sender": "Alex",
                                     "text": "On my way", "category": "message", "ageMs": 200}])
    self.client.on_notification_source(event(category=6, uid=8))
    self.client.on_data_source(notification_response(uid=8, message=b"", subtitle=b"Invoice"))
    self.assertEqual(len(self.writes), 3)  # cached name: no second app lookup
    self.assertEqual(self.emitted[-1]["appName"], "Messages")
    self.assertEqual(self.emitted[-1]["text"], "Invoice")
    self.assertEqual(self.emitted[-1]["category"], "email")

  def test_unknown_app_name_falls_back_after_timeout(self):
    self.client.on_notification_source(event(uid=1))
    self.client.on_data_source(notification_response(uid=1, app=b"com.x"))
    self.clock.now += 2.9
    self.client.tick()
    self.assertFalse(self.emitted)
    self.clock.now += 0.2
    self.client.tick()
    self.assertEqual(self.emitted[0]["appName"], "")
    self.assertEqual(self.emitted[0]["ageMs"], 3100)

  def test_notification_timeout_drops_and_moves_on(self):
    self.client.on_notification_source(event(uid=1))
    self.client.on_notification_source(event(uid=2))
    self.assertEqual(len(self.writes), 1)  # one outstanding request
    self.clock.now += 3
    self.client.tick()
    self.assertEqual(self.writes[-1], ancs.notification_attributes_command(2))
    self.assertFalse(self.emitted)
    self.client.on_data_source(notification_response(uid=1))  # late answer for the old uid is rejected
    self.assertEqual(len(self.writes), 2)  # mismatch aborts uid 2; nothing pending

  def test_silent_preexisting_modified_and_removed(self):
    self.client.on_notification_source(event(flags=1, uid=1))
    self.client.on_notification_source(event(flags=4, uid=2))
    self.client.on_notification_source(event(event_id=1, uid=3))
    self.client.on_notification_source(b"short")
    self.assertFalse(self.writes)
    self.client.on_notification_source(event(uid=10))
    self.client.on_notification_source(event(uid=11))
    self.client.on_notification_source(event(event_id=2, uid=11))
    self.client.on_data_source(notification_response(uid=10))
    self.client.on_data_source(app_response())
    self.assertEqual(len(self.emitted), 1)
    self.assertIsNone(self.client.current)

  def test_bounded_fifo_drops_oldest(self):
    for uid in range(1, 20):
      self.client.on_notification_source(event(uid=uid))
    self.assertEqual(len(self.client.pending), 16)
    self.assertEqual(self.client.pending[0][0], 4)  # uid 1 in flight; 2 and 3 dropped

  def test_stale_blank_and_missing_app_are_dropped(self):
    self.client.on_notification_source(event(uid=1))
    self.client.on_data_source(notification_response(uid=1, title=b"", message=b" \n", subtitle=b""))
    self.client.on_notification_source(event(uid=2))
    self.client.on_data_source(notification_response(uid=2, app=b""))
    self.assertFalse(self.emitted)
    self.client.on_notification_source(event(uid=3))
    self.client.on_notification_source(event(uid=4))
    self.clock.now += 61
    self.client.on_data_source(notification_response(uid=3))
    self.client.on_data_source(app_response())
    self.assertFalse(self.emitted)  # 3 was too old by the time it arrived; 4 never requested
    self.assertFalse(self.client.pending)

  def test_limits_and_control_point_error(self):
    self.client.on_notification_source(event(uid=1))
    self.client.abort()  # e.g. Control Point answered 0xA1
    self.assertIsNone(self.client.current)
    self.client.on_notification_source(event(uid=2))
    self.client.on_data_source(notification_response(uid=2, title="é".encode() * 200, message=b"m" * 600))
    self.client.on_data_source(app_response(name=b"N" * 100))
    payload = self.emitted[0]
    self.assertEqual(len(payload["sender"]), 120)
    self.assertEqual(len(payload["text"]), 500)
    self.assertEqual(len(payload["appName"]), 80)

  def test_app_cache_is_bounded(self):
    for uid in range(70):
      app = f"com.app{uid}".encode()
      self.client.on_notification_source(event(uid=uid))
      self.client.on_data_source(notification_response(uid=uid, app=app))
      self.client.on_data_source(app_response(app=app, name=b"A"))
    self.assertEqual(len(self.client.names), 64)
    self.assertEqual(len(self.emitted), 70)


def objects(connected=True, paired=True, chars=(ancs.NOTIFICATION_SOURCE_UUID, ancs.CONTROL_POINT_UUID, ancs.DATA_SOURCE_UUID)):
  service = PHONE + "/service0030"
  value = {PHONE: {"org.bluez.Device1": {"Connected": Variant("b", connected), "Paired": Variant("b", paired)}},
           service: {"org.bluez.GattService1": {"UUID": Variant("s", ancs.SERVICE_UUID.upper())}}}
  for index, uuid in enumerate(chars):
    value[f"{service}/char{index:04x}"] = {"org.bluez.GattCharacteristic1": {"UUID": Variant("s", uuid.upper()),
                                                                             "Service": Variant("o", service)}}
  return value


class FakeGatt:
  def __init__(self):
    self.objects_value = objects()
    self.callbacks = {}
    self.calls = []
    self.writes = []
    self.fail_start = False
    self.fail_write = False

  async def objects(self):
    return self.objects_value

  async def start_notify(self, path):
    self.calls.append(("start", path))
    if self.fail_start:
      raise DBusError("org.bluez.Error.Failed", "nope")

  async def stop_notify(self, path):
    self.calls.append(("stop", path))

  async def write_value(self, path, data):
    self.writes.append((path, bytes(data)))
    if self.fail_write:
      raise DBusError("org.bluez.Error.Failed", "0xa1")

  def subscribe(self, path, callback):
    self.callbacks[path] = callback
    return lambda: self.callbacks.pop(path, None)


class WatcherTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.gatt = FakeGatt()
    self.clock = Clock()
    self.emitted = []
    self.watcher = Watcher(self.gatt, ADAPTER, self.emitted.append, clock=self.clock)

  def chars(self):
    return find_ancs({p: {i: {k: v.value for k, v in props.items()} for i, props in ifs.items()}
                      for p, ifs in self.gatt.objects_value.items()}, ADAPTER)[PHONE]

  def test_find_requires_all_three_connected_and_adapter(self):
    plain = lambda value: {p: {i: {k: v.value for k, v in props.items()} for i, props in ifs.items()} for p, ifs in value.items()}  # noqa: E731
    self.assertIn(PHONE, find_ancs(plain(objects()), ADAPTER))
    self.assertEqual(find_ancs(plain(objects()), "/org/bluez/hci1"), {})
    self.assertEqual(find_ancs(plain(objects(connected=False)), ADAPTER), {})
    self.assertEqual(find_ancs(plain(objects(paired=False)), ADAPTER), {})
    self.assertEqual(find_ancs(plain(objects(chars=(ancs.NOTIFICATION_SOURCE_UUID, ancs.DATA_SOURCE_UUID))), ADAPTER), {})

  async def test_attach_subscribe_before_start_and_flow(self):
    await self.watcher.scan()
    chars = self.chars()
    data, source, control = chars[ancs.DATA_SOURCE_UUID], chars[ancs.NOTIFICATION_SOURCE_UUID], chars[ancs.CONTROL_POINT_UUID]
    self.assertEqual(self.gatt.calls, [("start", data), ("start", source)])
    self.assertEqual(set(self.gatt.callbacks), {data, source})
    self.gatt.callbacks[source](event(uid=7))
    await asyncio.sleep(0)
    self.assertEqual(self.gatt.writes, [(control, ancs.notification_attributes_command(7))])
    self.gatt.callbacks[data](notification_response())
    self.gatt.callbacks[data](app_response())
    self.assertEqual(self.emitted[0]["sender"], "Alex")
    await self.watcher.scan()  # unchanged: no second subscription
    self.assertEqual(len(self.gatt.calls), 2)

  async def test_unbonded_device_with_ancs_uuids_is_ignored(self):
    self.gatt.objects_value = objects(paired=False)
    await self.watcher.scan()
    self.assertEqual(self.watcher.phones, {})
    self.assertEqual((self.gatt.calls, self.gatt.writes, self.gatt.callbacks), ([], [], {}))

  async def test_late_write_failure_does_not_abort_next_request(self):
    await self.watcher.scan()
    client = self.watcher.phones[PHONE].client
    gate = asyncio.Event()
    write_value = self.gatt.write_value

    async def slow_fail(path, data):
      if data == ancs.notification_attributes_command(1):
        await gate.wait()
        raise DBusError("org.bluez.Error.Failed", "timeout")
      await write_value(path, data)
    self.gatt.write_value = slow_fail  # ty: ignore[invalid-assignment]
    source = self.chars()[ancs.NOTIFICATION_SOURCE_UUID]
    self.gatt.callbacks[source](event(uid=1))
    self.gatt.callbacks[source](event(uid=2))
    self.clock.now += 3
    self.watcher.tick()  # uid 1 timed out; uid 2 now outstanding
    current = client.current
    gate.set()
    for _ in range(5):
      await asyncio.sleep(0)
    self.assertIs(client.current, current)

  async def test_detach_on_disconnect_and_reattach(self):
    await self.watcher.scan()
    self.gatt.objects_value = objects(connected=False)
    await self.watcher.scan()
    self.assertEqual(self.watcher.phones, {})
    self.assertEqual(self.gatt.callbacks, {})
    self.assertEqual([c[0] for c in self.gatt.calls], ["start", "start", "stop", "stop"])
    self.gatt.objects_value = objects()
    await self.watcher.scan()
    self.assertIn(PHONE, self.watcher.phones)

  async def test_start_failure_backs_off_per_device(self):
    self.gatt.fail_start = True
    await self.watcher.scan()
    self.assertEqual(self.watcher.phones, {})
    self.assertEqual(self.gatt.callbacks, {})
    calls = len(self.gatt.calls)
    await self.watcher.scan()
    self.assertEqual(len(self.gatt.calls), calls)
    self.gatt.fail_start = False
    self.clock.now += 31
    await self.watcher.scan()
    self.assertIn(PHONE, self.watcher.phones)

  async def test_write_failure_aborts_request(self):
    self.gatt.fail_write = True
    await self.watcher.scan()
    chars = self.chars()
    self.gatt.callbacks[chars[ancs.NOTIFICATION_SOURCE_UUID]](event(uid=1))
    for _ in range(3):
      await asyncio.sleep(0)
    self.assertIsNone(self.watcher.phones[PHONE].client.current)

  async def test_run_detaches_on_failure_and_supervisor_retries(self):
    runs = []
    sleeps = []

    class Broken:
      async def run(self):
        runs.append(1)
        if len(runs) == 3:
          raise asyncio.CancelledError
        raise RuntimeError("bus gone")

    async def sleep(seconds):
      sleeps.append(seconds)
    with self.assertRaises(asyncio.CancelledError):
      await supervise(Broken, sleep=sleep)
    self.assertEqual(len(runs), 3)
    self.assertEqual(sleeps, [2, 4])

    async def stop(seconds):
      raise RuntimeError("stop")
    watcher = Watcher(self.gatt, ADAPTER, self.emitted.append, clock=self.clock, sleep=stop)
    with self.assertRaises(RuntimeError):
      await watcher.run()
    self.assertEqual(watcher.phones, {})
    self.assertEqual(self.gatt.callbacks, {})


class FakeBus:
  def __init__(self):
    self.handlers = []
  def add_message_handler(self, handler):
    self.handlers.append(handler)


class BluezGattTests(unittest.TestCase):
  def test_signal_dispatch_never_claims_message(self):
    bus = FakeBus()
    gatt = BluezGatt(bus)
    got = []
    unsubscribe = gatt.subscribe("/c", got.append)
    signal = Message(path="/c", interface="org.freedesktop.DBus.Properties", member="PropertiesChanged",
                     message_type=MessageType.SIGNAL, signature="sa{sv}as",
                     body=["org.bluez.GattCharacteristic1", {"Value": Variant("ay", b"\x01\x02")}, []])
    self.assertIsNone(bus.handlers[0](signal))
    self.assertEqual(got, [b"\x01\x02"])
    gatt.subscribe("/c", lambda value: 1 / 0)
    self.assertIsNone(bus.handlers[0](signal))  # a failing callback is swallowed
    unsubscribe()  # stale unsubscribe leaves the newer callback alone
    self.assertIn("/c", gatt.callbacks)
    call = Message(destination="org.bluez", path="/c", interface="org.bluez.GattCharacteristic1", member="ReadValue")
    self.assertIsNone(bus.handlers[0](call))


class PosterTests(unittest.IsolatedAsyncioTestCase):
  async def test_posts_contract_and_bounds_inflight(self):
    seen = []

    class Proxy:
      def handle(self, request):
        seen.append(request)
        return {"status": 200}
    poster = Poster(Proxy(), limit=2)
    for _ in range(3):
      poster.post({"source": "ios", "app": "a", "appName": "", "sender": "s", "text": "", "category": "other", "ageMs": 1})
    await asyncio.gather(*poster.tasks)
    self.assertEqual(len(seen), 2)
    self.assertEqual(poster.dropped, 1)
    request = seen[0]
    self.assertEqual((request["method"], request["path"]), ("POST", "/api/companion/notification"))
    self.assertEqual(request["headers"], {"content-type": "application/json"})
    self.assertEqual(json.loads(base64.b64decode(request["body"]))["source"], "ios")

  async def test_failure_is_silent(self):
    class Proxy:
      def handle(self, request):
        raise OSError("down")
    poster = Poster(Proxy())
    poster.post({})
    await asyncio.gather(*poster.tasks)
    self.assertEqual(poster.dropped, 1)


class AgentTests(unittest.TestCase):
  def test_only_scoped_device_and_no_pin_methods(self):
    agent = BondAgent(lambda device: device == PHONE)
    for name, args in (("RequestAuthorization", ()), ("RequestConfirmation", (123456,)), ("AuthorizeService", ("uuid",))):
      getattr(BondAgent, name).__wrapped__(agent, PHONE, *args)
      with self.assertRaises(DBusError):
        getattr(BondAgent, name).__wrapped__(agent, ADAPTER + "/dev_00_00_00_00_00_01", *args)
    for name, args in (("RequestPinCode", ()), ("RequestPasskey", ()), ("DisplayPasskey", (1, 0)), ("DisplayPinCode", ("0000",))):
      with self.assertRaises(DBusError) as raised:
        getattr(BondAgent, name).__wrapped__(agent, PHONE, *args)
      self.assertEqual(raised.exception.type, "org.bluez.Error.Rejected")


if __name__ == "__main__":
  unittest.main()
