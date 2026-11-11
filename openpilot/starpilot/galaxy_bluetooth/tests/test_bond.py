import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor"))
from dbus_next import Variant
import claim
from bond import BOND_PATH, READY_PATH, BondAcceptance, readiness_error
from protocol import Assembler, BOND_UUID, open_message, packets, seal
from proxy import GalaxyProxy
from server import Characteristic, Gateway

KEY = bytes(range(32))
PHONE = "/org/bluez/hci0/dev_4A_11_22_33_44_55"
OTHER = "/org/bluez/hci0/dev_11_12_13_14_15_16"


class FakeBluez:
  def __init__(self):
    self.pairable = False
    self.agent = False
    self.devices = {PHONE: {"Connected": True, "Paired": False}, OTHER: {"Connected": True, "Paired": False}}
    self.trusted = []
    self.log = []
    self.fail = set()

  def _step(self, name):
    self.log.append(name)
    if name in self.fail:
      raise OSError(name)

  async def register_agent(self):
    self._step("register")
    self.agent = True

  async def unregister_agent(self):
    self._step("unregister")
    self.agent = False

  async def get_pairable(self):
    self._step("get")
    return self.pairable

  async def set_pairable(self, value):
    self._step("set")
    self.pairable = value

  async def device(self, path):
    props = self.devices.get(path)
    return dict(props) if props is not None else None

  async def set_trusted(self, path):
    self._step("trust")
    self.trusted.append(path)


class Clock:
  def __init__(self):
    self.now = 1000.0
  def __call__(self):
    return self.now


class BondTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.claim = Path(self.tmp.name) / "claim.json"
    self.bluez = FakeBluez()
    self.clock = Clock()
    self.hook = None

    async def sleep(seconds):
      self.clock.now += seconds
      if self.hook:
        self.hook()
      await asyncio.sleep(0)
    self.bond = BondAcceptance(self.bluez, self.claim, clock=self.clock, sleep=sleep)
    self.problem = None
    self.asked = 0

  async def ready(self):
    self.asked += 1
    return self.problem

  def open(self, device):
    return self.bond.open(device, self.ready)

  def tearDown(self):
    self.tmp.cleanup()

  async def test_scoped_window_ends_on_paired_and_restores(self):
    status, body = await self.open(PHONE)
    self.assertEqual((status, body), (200, {"ok": True, "seconds": 60}))
    self.assertTrue(self.bluez.pairable and self.bluez.agent)
    self.assertTrue(self.bond.allows(PHONE))
    self.assertFalse(self.bond.allows(OTHER))
    self.assertEqual(await self.open(OTHER), (409, {"error": "Another phone is pairing for notifications", "code": "busy"}))
    self.assertEqual((await self.open(PHONE))[0], 200)
    self.bluez.devices[PHONE]["Paired"] = True
    await self.bond.task
    self.assertEqual(self.bluez.trusted, [PHONE])
    self.assertFalse(self.bluez.pairable)
    self.assertFalse(self.bluez.agent)
    self.assertFalse(self.bond.allows(PHONE))

  async def test_timeout_and_disconnect_restore(self):
    await self.open(PHONE)
    await self.bond.task
    self.assertGreaterEqual(self.clock.now, 1060)
    self.assertFalse(self.bluez.pairable or self.bluez.agent)
    self.assertFalse(self.bond.allows(PHONE))
    await self.open(PHONE)
    self.bluez.devices[PHONE]["Connected"] = False
    await self.bond.task
    self.assertFalse(self.bluez.pairable or self.bluez.agent)
    self.assertEqual(self.bluez.trusted, [])

  async def test_previously_pairable_is_left_pairable(self):
    self.bluez.pairable = True
    await self.open(PHONE)
    self.assertNotIn("set", self.bluez.log)
    await self.bond.task
    self.assertTrue(self.bluez.pairable)
    self.assertFalse(self.bluez.agent)

  async def test_refused_while_owner_window_open_even_after_claim(self):
    claim.arm("AA:BB:CC:DD:EE:FF", "comma", self.claim)
    self.assertEqual((await self.open(PHONE))[0], 409)
    claim.finish("Phone", "abcd0123", self.claim)  # window() is now None but owner still pairs music
    self.assertIsNone(claim.window(self.claim))
    self.assertEqual((await self.open(PHONE))[0], 409)
    self.assertEqual(self.bluez.log, [])

  async def test_owner_window_mid_acceptance_leaves_pairable_then_repairs(self):
    await self.open(PHONE)
    claim.arm("AA:BB:CC:DD:EE:FF", "comma", self.claim, seconds=30, now=time.monotonic())
    await self.bond.task
    self.assertFalse(self.bluez.agent)
    self.assertTrue(self.bluez.pairable, "owner holds Pairable during its window")
    self.assertEqual(self.bluez.log.count("set"), 1)
    claim.clear(self.claim)  # owner window over; it restored the True it read from us
    await self.bond.repair
    self.assertFalse(self.bluez.pairable)

  async def test_already_bonded_trusts_without_pairable(self):
    self.bluez.devices[PHONE]["Paired"] = True
    self.assertEqual(await self.open(PHONE), (200, {"ok": True, "seconds": 0}))
    self.assertEqual(self.bluez.trusted, [PHONE])
    self.assertNotIn("set", self.bluez.log)
    self.assertNotIn("register", self.bluez.log)

  async def test_adapter_failure_is_503_and_restores(self):
    self.bluez.fail.add("set")
    self.assertEqual((await self.open(PHONE))[0], 503)
    self.assertFalse(self.bluez.agent)
    self.assertFalse(self.bond.busy)
    self.bluez.fail = {"register"}
    self.assertEqual((await self.open(PHONE))[0], 503)
    self.assertNotIn("unregister", self.bluez.log[-1:])
    self.assertEqual(await self.open("/org/bluez/hci0/dev_gone"), (409, {"error": "Phone is not connected", "code": "disconnected"}))

  async def test_not_ready_refuses_before_touching_bluez(self):
    self.problem = ("park_required", "Park the car or turn it off to pair a phone.")
    self.assertEqual(await self.open(PHONE), (409, {"error": "Park the car or turn it off to pair a phone.", "code": "park_required"}))
    self.assertEqual(self.bluez.log, [])
    self.assertFalse(self.bond.busy)
    self.assertEqual((await self.bond.open(PHONE))[1]["code"], "not_ready")  # no readiness check: fail closed
    self.assertEqual(self.bluez.log, [])
    self.bluez.devices[PHONE]["Paired"] = True
    asked = self.asked
    self.assertEqual(await self.open(PHONE), (200, {"ok": True, "seconds": 0}))  # no window, so no readiness needed
    self.assertEqual(self.asked, asked)

  async def test_owner_window_codes(self):
    claim.arm("AA:BB:CC:DD:EE:FF", "comma", self.claim)
    self.assertEqual(await self.open(PHONE), (409, {"error": "Finish pairing on comma first", "code": "pairing_window"}))

  def test_readiness_reply_parsing_fails_closed(self):
    def reply(value, status=200):
      raw = value if isinstance(value, bytes) else json.dumps(value).encode()
      return {"status": status, "body": base64.b64encode(raw).decode()}
    ok = {"schema": 1, "ready": True, "code": "ready", "message": "Ready to pair."}
    self.assertIsNone(readiness_error(reply(ok)))
    self.assertEqual(readiness_error(reply({**ok, "ready": False, "code": "bluetooth_off", "message": "Bluetooth is off"})),
                     ("bluetooth_off", "Bluetooth is off"))
    fallback = ("not_ready", "Comma is not ready to pair")
    for bad in (reply(ok, status=401), reply(b"not json"), reply([1]), reply({"ready": "true"}), reply({"ready": 1}),
                {"status": 200, "body": "%%%"}, {"status": 200}, {}, {"status": 502, "body": ""}):
      self.assertEqual(readiness_error(bad), fallback)
    self.assertEqual(readiness_error(reply({"ready": False, "code": 3, "message": ""})), fallback)

  async def test_cancel_restores(self):
    await self.open(PHONE)
    await self.bond.close()
    self.assertFalse(self.bluez.pairable or self.bluez.agent)
    self.assertFalse(self.bond.busy)

  async def test_concurrent_requests_only_one_wins(self):
    results = await asyncio.gather(self.open(PHONE), self.open(OTHER))
    self.assertEqual(sorted(r[0] for r in results), [200, 409])
    self.assertEqual(self.bluez.log.count("register"), 1)
    await self.bond.close()


class CountingProxy:
  def __init__(self):
    self.calls = 0
    self.ready: dict | Exception = {"schema": 1, "ready": True, "code": "ready", "message": "Ready to pair."}
    self.ready_calls = 0
  def handle(self, request):
    if request["path"] == READY_PATH and request["method"] == "GET":
      self.ready_calls += 1
      if isinstance(self.ready, Exception):
        raise self.ready
      return {"id": request["id"], "status": 200, "headers": {}, "body": base64.b64encode(json.dumps(self.ready).encode()).decode()}
    self.calls += 1
    return {"id": request["id"], "status": 200, "headers": {}, "body": ""}


class GatewayBondTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.claim = Path(self.tmp.name) / "claim.json"
    self.proxy = CountingProxy()
    self.gateway = Gateway(KEY, self.proxy)
    self.bluez = FakeBluez()
    self.gateway.bonder = BondAcceptance(self.bluez, self.claim)
    self.options = {"device": Variant("o", PHONE), "mtu": Variant("q", 185)}
    self.state = self.gateway.session(self.options, fresh=True)

  async def asyncTearDown(self):
    await self.gateway.bonder.close()
    self.tmp.cleanup()

  async def submit(self, key=KEY, counter=1, path=BOND_PATH, method="POST"):
    request = {"id": "bond", "session": self.state.challenge.hex(), "counter": counter, "path": path, "method": method, "body": ""}
    for packet in packets(seal(request, key, "request")):
      self.gateway.write(packet, self.options)
    await self.state.task
    if not self.state.response:
      return None
    assembler = Assembler()
    decoded = None
    while self.state.response:
      packet = self.gateway.read(self.options)
      decoded = assembler.add(packet)
      self.gateway.write(b"\x02" + packet[1:5], self.options)
    reply = open_message(decoded, KEY, "response")
    body = base64.b64decode(reply["body"])
    return reply["status"], json.loads(body) if body else None

  async def test_authenticated_session_opens_scoped_window_without_galaxy(self):
    self.assertEqual(self.state.device, PHONE)
    self.assertEqual(await self.submit(), (200, {"ok": True, "seconds": 60}))
    self.assertEqual(self.proxy.calls, 0)
    self.assertTrue(self.gateway.bonder.allows(PHONE))
    self.assertFalse(self.gateway.bonder.allows(OTHER))
    self.assertTrue(self.bluez.pairable)

  async def test_unauthenticated_or_replayed_never_reaches_bluez(self):
    self.assertIsNone(await self.submit(key=bytes(32)))
    self.assertEqual(self.bluez.log, [])
    await self.submit()
    self.bluez.log.clear()
    self.assertIsNone(await self.submit())  # same counter replayed
    self.assertEqual(self.bluez.log, [])

  async def test_owner_window_conflict_and_get_is_proxied(self):
    claim.arm("AA:BB:CC:DD:EE:FF", "comma", self.claim)
    self.assertEqual((await self.submit())[0], 409)
    self.assertEqual(self.bluez.log, [])
    await self.submit(counter=2, method="GET")
    self.assertEqual(self.proxy.calls, 1)  # only POST is the bond request

  async def test_bond_asks_galaxy_readiness_through_session_proxy(self):
    self.proxy.ready = {"schema": 1, "ready": False, "code": "park_required", "message": "Park the car or turn it off to pair a phone."}
    self.assertEqual(await self.submit(), (409, {"error": "Park the car or turn it off to pair a phone.", "code": "park_required"}))
    self.assertEqual(self.proxy.ready_calls, 1)
    self.assertEqual(self.bluez.log, [])
    self.proxy.ready = OSError("Galaxy down")
    self.assertEqual(await self.submit(counter=2), (409, {"error": "Comma is not ready to pair", "code": "not_ready"}))
    self.assertEqual(self.bluez.log, [])
    self.proxy.ready = {"schema": 1, "ready": True, "code": "ready", "message": "Ready to pair."}
    self.assertEqual(await self.submit(counter=3), (200, {"ok": True, "seconds": 60}))
    self.assertEqual(self.proxy.calls, 0)

  async def test_without_bonder_is_503(self):
    bonder, self.gateway.bonder = self.gateway.bonder, None
    self.assertEqual((await self.submit())[0], 503)
    self.assertEqual(self.proxy.calls, 0)
    self.gateway.bonder = bonder

  def test_bond_characteristic_is_encrypt_read(self):
    characteristic = Characteristic(BOND_UUID, ["encrypt-read"], self.gateway, "bond")
    self.assertEqual(BOND_UUID, "bd490006-6dc1-4de7-a7d0-6cdb441f7650")
    self.assertEqual(characteristic.props()["Flags"].value, ["encrypt-read"])
    self.assertEqual(Characteristic.ReadValue.__wrapped__(characteristic, {}), b"\x01")


class HealthTests(unittest.TestCase):
  def test_health_advertises_notification_bond(self):
    reply = GalaxyProxy(8082).handle({"id": "h", "method": "GET", "path": "/_bridge/health"})
    self.assertEqual(reply["status"], 200)
    self.assertIs(json.loads(base64.b64decode(reply["body"]))["notificationBond"], True)


if __name__ == "__main__":
  unittest.main()
