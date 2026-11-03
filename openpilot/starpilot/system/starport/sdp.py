"""Minimal SDP client: find the head unit's Android Auto Wireless RFCOMM channel.

The request shape mirrors a real Android phone (ServiceSearchAttributeRequest for
the AA Wireless UUID, 0x03f0 maximum attribute bytes, the whole attribute range,
transaction id 0), which aa-proxy-rs found some head units require. Continuation
state is followed for receivers that split the response.

Protocol reference: aa-proxy/aa-proxy-rs ``src/bluetooth.rs`` (MIT), pinned at
d61ad375a669ede2f260ba65f0ccb48576e21885, and the Bluetooth Core SDP chapter.
"""

from __future__ import annotations

import struct
import time
import uuid

AA_WIRELESS_UUID = uuid.UUID("4de17a00-52cb-11e6-bdf4-0800200c9a66")
SDP_PSM = 0x0001
PDU_ERROR_RESPONSE = 0x01
PDU_SEARCH_ATTRIBUTE_REQUEST = 0x06
PDU_SEARCH_ATTRIBUTE_RESPONSE = 0x07
MAX_ATTRIBUTE_BYTES = 0x03F0
MAX_CONTINUATIONS = 8
MAX_RESPONSE_BYTES = 64 * 1024
RFCOMM_UUID16 = b"\x19\x00\x03"

ERRORS = {1: "invalid SDP version", 2: "invalid service record handle", 3: "invalid request syntax",
          4: "invalid PDU size", 5: "invalid continuation state", 6: "insufficient resources"}


class SdpError(RuntimeError):
  def __init__(self, message: str, raw: bytes = b""):
    super().__init__(message)
    self.raw = raw   # what the car sent, for the log when the record is not usable


def build_request(continuation: bytes, service: uuid.UUID = AA_WIRELESS_UUID, transaction_id: int = 0) -> bytes:
  if len(continuation) > 16:
    raise SdpError("SDP continuation state too long")
  pattern = b"\x35\x11\x1c" + service.bytes
  attributes = b"\x35\x05\x0a" + struct.pack(">I", 0x0000FFFF)
  params = pattern + struct.pack(">H", MAX_ATTRIBUTE_BYTES) + attributes + bytes([len(continuation)]) + continuation
  return struct.pack(">BHH", PDU_SEARCH_ATTRIBUTE_REQUEST, transaction_id, len(params)) + params


def parse_response(pdu: bytes) -> tuple[bytes, bytes]:
  """Return ``(attribute_list_bytes, continuation_state)`` from one response PDU."""
  if len(pdu) < 5:
    raise SdpError("Short SDP PDU")
  pdu_id, _tid, length = struct.unpack(">BHH", pdu[:5])
  params = pdu[5:5 + length]
  if len(params) != length:
    raise SdpError("Truncated SDP PDU")
  if pdu_id == PDU_ERROR_RESPONSE:
    code = struct.unpack(">H", params[:2])[0] if len(params) >= 2 else 0
    raise SdpError(f"Head unit returned SDP error {code:#06x} ({ERRORS.get(code, 'unknown')})")
  if pdu_id != PDU_SEARCH_ATTRIBUTE_RESPONSE or len(params) < 3:
    raise SdpError(f"Unexpected SDP PDU {pdu_id:#04x}")
  count = struct.unpack(">H", params[:2])[0]
  if len(params) < 2 + count + 1:
    raise SdpError("Truncated SDP attribute list")
  continuation_length = params[2 + count]
  continuation = params[3 + count:3 + count + continuation_length]
  if len(continuation) != continuation_length:
    raise SdpError("Truncated SDP continuation state")
  return params[2:2 + count], continuation


def rfcomm_channel(attributes: bytes) -> int | None:
  """Find the RFCOMM channel that follows the RFCOMM protocol UUID16 (0x0003)."""
  start = 0
  while (index := attributes.find(RFCOMM_UUID16, start)) >= 0:
    pos = index + len(RFCOMM_UUID16)
    tag = attributes[pos] if pos < len(attributes) else None
    value = None
    if tag == 0x08 and pos + 1 < len(attributes):
      value = attributes[pos + 1]
    elif tag == 0x09 and pos + 2 < len(attributes):
      value = struct.unpack(">H", attributes[pos + 1:pos + 3])[0]
    elif tag == 0x0A and pos + 4 < len(attributes):
      value = struct.unpack(">I", attributes[pos + 1:pos + 5])[0]
    if value is not None and 1 <= value <= 30:
      return value
    start = index + 1
  return None


def query_channel(sock, timeout: float = 10.0) -> int:
  """Run the continuation loop over a connected L2CAP (SEQPACKET) socket."""
  collected = bytearray()
  continuation = b""
  deadline = time.monotonic() + timeout
  for _ in range(MAX_CONTINUATIONS):
    sock.settimeout(max(0.1, deadline - time.monotonic()))
    sock.send(build_request(continuation))
    pdu = sock.recv(4096)
    if not pdu:
      raise SdpError("Head unit closed the SDP connection")
    attributes, continuation = parse_response(pdu)
    collected.extend(attributes)
    if len(collected) > MAX_RESPONSE_BYTES:
      raise SdpError("SDP response too large")
    if not continuation:
      break
  else:
    raise SdpError("Too many SDP continuation rounds")
  if not collected or collected in (b"\x35\x00",):
    raise SdpError("Head unit does not advertise Android Auto Wireless (SDP record missing)")
  channel = rfcomm_channel(bytes(collected))
  if channel is None:
    raise SdpError("Android Auto Wireless SDP record has no RFCOMM channel", bytes(collected))
  return channel
