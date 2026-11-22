import struct
import time
from functools import reduce
from operator import xor
from pathlib import Path

from panda import McuType, Panda
from panda.python.base import TIMEOUT
from panda.python.spi import PandaSpiException, SpiDevice


FLASHER_MAGIC = b"\xde\xad\xd0\x0d"
MAX_RESPONSE = 64


class LegacySpiFlasher:
  """Use the previous SPI framing only to identify and migrate an internal TRES Panda."""

  def __init__(self, spi):
    self.spi = spi

  @staticmethod
  def _frame(data):
    return data + bytes([reduce(xor, data, 0xab)])

  def _wait_ack(self, expected, fill, length, timeout=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      reply = self.spi.xfer(bytes([fill]) * length)
      if reply[0] == expected:
        return reply
      if reply[0] == 0x1f:
        raise PandaSpiException("Legacy SPI request was rejected")
    raise PandaSpiException(f"Legacy SPI acknowledgment {expected:#x} timed out")

  def _exchange(self, endpoint, payload, expect_disconnect=False, reply_timeout=0.25):
    header = struct.pack("<BBHH", 0x5a, endpoint, len(payload), MAX_RESPONSE)
    self.spi.xfer(self._frame(header))
    self._wait_ack(0x79, 0x11, 1)
    self.spi.xfer(self._frame(payload))
    if expect_disconnect:
      return b""
    reply = self._wait_ack(0x85, 0x13, MAX_RESPONSE + 4, reply_timeout)
    size = int.from_bytes(reply[1:3], "little")
    if size > MAX_RESPONSE or reduce(xor, reply[:size + 4], 0xab):
      raise PandaSpiException("Legacy SPI response length or checksum invalid")
    return reply[3:size + 3]

  def controlRead(self, request_type, request, value, index, length):
    return self._exchange(0, struct.pack("<BHHH", request, value, index, length))

  def controlWrite(self, request_type, request, value, index, data, expect_disconnect=False):
    if data:
      raise ValueError("Unexpected legacy SPI control payload")
    # A legacy bootstub waits for flash erase to finish before acknowledging it.
    reply_timeout = TIMEOUT / 1000 if request == 0xb2 else 0.25
    reply = self._exchange(0, struct.pack("<BHHH", request, value, index, 0), expect_disconnect, reply_timeout)
    if request in (0xb1, 0xb2):
      if len(reply) != 12 or reply[4:8] != FLASHER_MAGIC or reply[2] != request or (request == 0xb2 and reply[1] != 0xff):
        raise PandaSpiException(f"Legacy bootstub failed flash operation {request:#x}")

  def bulkWrite(self, endpoint, data):
    if endpoint != 2:
      raise ValueError("Unexpected legacy SPI flash endpoint")
    # The old flash path advances its address after each write. Never resend a chunk if its ACK is lost.
    for offset in range(0, len(data), 16):
      self._exchange(endpoint, data[offset:offset + 16])
    return len(data)

  def is_tres(self):
    return self.controlRead(Panda.REQUEST_IN, 0xc1, 0, 0, 64) == Panda.HW_TYPE_TRES


def migrate_legacy_tres_firmware(filename, expected_serial=None) -> str | None:
  """Return the migrated TRES serial, or None if the previous SPI framing is absent."""
  device = SpiDevice()
  with device.acquire() as spi:
    flasher = LegacySpiFlasher(spi)
    try:
      if not flasher.is_tres():
        return None
    except PandaSpiException:
      return None
    serial = flasher.controlRead(Panda.REQUEST_IN, 0xc3, 0, 0, 12)
    if len(serial) != 12 or (expected_serial is not None and serial.hex() != expected_serial):
      raise PandaSpiException("Legacy TRES identity could not be verified")
    code = Path(filename).read_bytes()
    size = int.from_bytes(code[:4], "little")
    if size <= 8 or size + 128 != len(code) or len(code) % 4 or len(code) >= 6 * 0x20000:
      raise ValueError("Invalid legacy migration image layout")
    if not Panda.flasher_present(flasher):
      flasher.controlWrite(Panda.REQUEST_IN, 0xd1, 1, 0, b"", expect_disconnect=True)
      time.sleep(0.5)
    if not flasher.is_tres() or not Panda.flasher_present(flasher):
      raise PandaSpiException("Legacy TRES bootstub could not be verified")
    if flasher.controlRead(Panda.REQUEST_IN, 0xc3, 0, 0, 12) != serial:
      raise PandaSpiException("Legacy TRES identity changed before flashing")
    Panda.flash_static(flasher, code, McuType.H7)
  return serial.hex()
