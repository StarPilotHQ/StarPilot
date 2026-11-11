"""A short LE bond acceptance for one authenticated phone, so iOS exposes ANCS on the same link."""
import asyncio
import base64
import json
import logging
import time

import claim

BOND_PATH = "/_bridge/notification-bond"
READY_PATH = "/api/companion/bluetooth-ready"
BOND_SECONDS = 60
OWNER_REPAIR_LIMIT = 15 * 60
LOG = logging.getLogger("galaxy-ble")


def owner_window_open(claim_path):
  # view() stays open until the deadline even after a phone claims its key, while the owner still pairs music.
  return claim_path is not None and claim.view(claim_path)["open"]


def readiness_error(reply):
  """None when Galaxy's bluetooth-ready reply says comma can pair now, else (code, message). Fails closed."""
  try:
    value = json.loads(base64.b64decode(reply["body"], validate=True)) if reply.get("status") == 200 else None
  except (KeyError, TypeError, ValueError, AttributeError):
    value = None
  if not isinstance(value, dict):
    return "not_ready", "Comma is not ready to pair"
  if value.get("ready") is True:
    return None
  code, message = value.get("code"), value.get("message")
  return (code[:40] if isinstance(code, str) and code else "not_ready",
          message[:200] if isinstance(message, str) and message else "Comma is not ready to pair")


def refused(status, code, message):
  return status, {"error": message, "code": code}


class BondAcceptance:
  """bluez: async register_agent(), unregister_agent(), get_pairable(), set_pairable(bool), device(path) -> props or None, set_trusted(path)."""
  def __init__(self, bluez, claim_path, clock=time.monotonic, sleep=asyncio.sleep, seconds=BOND_SECONDS, poll=0.25):
    self.bluez = bluez
    self.claim_path = claim_path
    self.clock = clock
    self.sleep = sleep
    self.seconds = seconds
    self.poll = poll
    self.device = None
    self.deadline = 0.0
    self.busy = False
    self.task = None
    self.repair = None
    self.changed = False

  def allows(self, device_path):
    return self.busy and self.device is not None and device_path == self.device and self.clock() < self.deadline

  async def open(self, device_path, ready=None):
    """-> (status, body). ready: async () -> None or (code, message), asked before a window opens. Returns at once."""
    if not isinstance(device_path, str) or not device_path:
      return refused(400, "no_device", "A Bluetooth device is required")
    if owner_window_open(self.claim_path):
      return refused(409, "pairing_window", "Finish pairing on comma first")
    if self.busy:
      if device_path == self.device:
        return 200, {"ok": True, "seconds": max(1, int(self.deadline - self.clock() + 0.999))}
      return refused(409, "busy", "Another phone is pairing for notifications")
    self.busy = True
    self.device = device_path
    self.deadline = self.clock() + self.seconds
    registered = changed = False
    try:
      props = await self.bluez.device(device_path)
      if props is None or props.get("Connected") is not True:
        self._release()
        return refused(409, "disconnected", "Phone is not connected")
      if props.get("Paired") is True:
        # No window opens here, so readiness is not asked.
        await self.bluez.set_trusted(device_path)
        self._release()
        return 200, {"ok": True, "seconds": 0}
      problem = ("not_ready", "Comma is not ready to pair") if ready is None else await ready()
      if problem is not None:
        self._release()
        return refused(409, *problem)
      if owner_window_open(self.claim_path):
        self._release()
        return refused(409, "pairing_window", "Finish pairing on comma first")
      await self.bluez.register_agent()
      registered = True
      original = await self.bluez.get_pairable()
      if owner_window_open(self.claim_path):
        raise InterruptedError
      if original is not True:
        await self.bluez.set_pairable(True)
        changed = True
    except InterruptedError:
      await self._restore(registered, False, False)
      return refused(409, "pairing_window", "Finish pairing on comma first")
    except Exception as error:
      LOG.warning("Notification bond could not start (%s)", type(error).__name__)
      await self._restore(registered, changed, False)
      return refused(503, "unavailable", "Bluetooth pairing is unavailable")
    self.deadline = self.clock() + self.seconds
    LOG.info("Notification bond window open for %ds", self.seconds)
    self.changed = changed
    self.task = asyncio.create_task(self._run(changed))
    return 200, {"ok": True, "seconds": self.seconds}

  async def _run(self, changed):
    outcome = "timed out"
    try:
      while self.clock() < self.deadline:
        await self.sleep(self.poll)
        if owner_window_open(self.claim_path):
          outcome = "owner window"
          break
        props = await self.bluez.device(self.device)
        if props is None or props.get("Connected") is not True:
          outcome = "disconnected"
          break
        if props.get("Paired") is True:
          await self.bluez.set_trusted(self.device)
          outcome = "paired"
          break
    except asyncio.CancelledError:
      outcome = "cancelled"
      raise
    except Exception as error:
      outcome = type(error).__name__
    finally:
      await self._restore(True, changed, outcome == "owner window")
      LOG.info("Notification bond window closed: %s", outcome)

  async def _restore(self, registered, changed, owner):
    try:
      if registered:
        try:
          await self.bluez.unregister_agent()
        except Exception:
          LOG.warning("Could not unregister the notification bond agent")
      if changed and owner:
        # The owner now holds Pairable and will restore what it read (our True); fix that after its window.
        self.repair = asyncio.create_task(self._repair_after_owner())
      elif changed:
        try:
          if await self.bluez.get_pairable() is True and not owner_window_open(self.claim_path):
            await self.bluez.set_pairable(False)
        except Exception:
          LOG.warning("Could not restore Bluetooth pairable state")
    finally:
      self._release()

  async def _repair_after_owner(self):
    started = self.clock()
    try:
      while self.clock() - started < OWNER_REPAIR_LIMIT:
        await self.sleep(1)
        if owner_window_open(self.claim_path) or self.busy:
          continue
        await self.sleep(5)
        if owner_window_open(self.claim_path) or self.busy:
          continue
        if await self.bluez.get_pairable() is True:
          await self.bluez.set_pairable(False)
          LOG.info("Restored Bluetooth pairable state after the comma pairing window")
        return
    except asyncio.CancelledError:
      raise
    except Exception:
      LOG.warning("Could not restore Bluetooth pairable state after the comma pairing window")

  def _release(self):
    self.busy = False
    self.device = None
    self.deadline = 0.0

  async def close(self):
    for task in (self.task, self.repair):
      if task is not None and not task.done():
        task.cancel()
        try:
          await task
        except (asyncio.CancelledError, Exception):
          pass
    if self.busy:
      # The window task was cancelled before it first ran, so its finally never restored.
      await self._restore(True, self.changed, False)
