"""Galaxy BLE v1: bounded framing and authenticated, direction-bound messages."""
import json
import base64
import hashlib
import os
import struct
import zlib

from Crypto.Cipher import AES

SERVICE_UUID = "bd490001-6dc1-4de7-a7d0-6cdb441f7650"
RX_UUID = "bd490002-6dc1-4de7-a7d0-6cdb441f7650"
TX_UUID = "bd490003-6dc1-4de7-a7d0-6cdb441f7650"
INFO_UUID = "bd490004-6dc1-4de7-a7d0-6cdb441f7650"
NOTIFY_UUID = "bd490005-6dc1-4de7-a7d0-6cdb441f7650"
BOND_UUID = "bd490006-6dc1-4de7-a7d0-6cdb441f7650"
NOTIFICATION_WINDOW = 8
MAX_FRAME = 2 * 1024 * 1024
MAX_BODY = 1024 * 1024
AAD_PREFIX = b"galaxy-ble-v1/"


def seal(value, key, direction, nonce=None, compact_body=False):
  cipher = AES.new(key, AES.MODE_GCM, nonce=nonce or os.urandom(12))
  cipher.update(AAD_PREFIX + direction.encode())
  plaintext = json.dumps(value, separators=(",", ":")).encode()
  if len(plaintext) > MAX_FRAME:
    raise ValueError("Decoded message exceeds limit")
  compressed = zlib.compress(plaintext, wbits=-15)
  encoded = (b"\x01" + struct.pack(">I", len(plaintext)) + compressed
             if len(compressed) < len(plaintext) else b"\x00" + struct.pack(">I", len(plaintext)) + plaintext)
  # Compress HTTP bytes before base64 encoding.
  if compact_body and isinstance(value.get("body"), str):
    body = base64.b64decode(value["body"], validate=True)
    if len(body) > MAX_BODY:
      raise ValueError("Body exceeds limit")
    metadata = json.dumps({k: v for k, v in value.items() if k != "body"}, separators=(",", ":")).encode()
    payload = struct.pack(">I", len(metadata)) + metadata + body
    if len(payload) > MAX_FRAME:
      raise ValueError("Decoded message exceeds limit")
    compact = b"\x02" + struct.pack(">I", len(payload)) + zlib.compress(payload, wbits=-15)
    if len(compact) < len(encoded):
      encoded = compact
  ciphertext, tag = cipher.encrypt_and_digest(encoded)
  data = bytes(cipher.nonce) + ciphertext + tag
  if len(data) > MAX_FRAME:
    raise ValueError("Message exceeds BLE frame limit")
  return struct.pack(">I", len(data)) + data


def open_message(data, key, direction):
  if not 28 <= len(data) <= MAX_FRAME:
    raise ValueError("Invalid message length")
  cipher = AES.new(key, AES.MODE_GCM, nonce=data[:12])
  cipher.update(AAD_PREFIX + direction.encode())
  encoded = cipher.decrypt_and_verify(data[12:-16], data[-16:])
  if len(encoded) < 5:
    raise ValueError("Invalid compressed envelope")
  length = struct.unpack(">I", encoded[1:5])[0]
  if not 1 <= length <= MAX_FRAME:
    raise ValueError("Invalid decoded message length")
  if encoded[0] in (1, 2):
    decoder = zlib.decompressobj(wbits=-15)
    plaintext = decoder.decompress(encoded[5:], length + 1)
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
      raise ValueError("Invalid compressed message")
  elif encoded[0] == 0:
    plaintext = encoded[5:]
  else:
    raise ValueError("Unknown compression codec")
  if len(plaintext) != length:
    raise ValueError("Incorrect decoded message length")
  if encoded[0] == 2:
    if len(plaintext) < 4:
      raise ValueError("Invalid body envelope")
    metadata_length = struct.unpack(">I", plaintext[:4])[0]
    if not 1 <= metadata_length <= len(plaintext) - 4:
      raise ValueError("Invalid metadata length")
    value = json.loads(plaintext[4:4 + metadata_length])
    body = plaintext[4 + metadata_length:]
    if not isinstance(value, dict) or "body" in value or len(body) > MAX_BODY:
      raise ValueError("Invalid body envelope")
    value["body"] = base64.b64encode(body).decode()
    return value
  value = json.loads(plaintext)
  if not isinstance(value, dict):
    raise ValueError("Expected an object")
  return value


class Assembler:
  def __init__(self):
    self.reset()

  def reset(self):
    self.buffer = bytearray()
    self.sequence = 0
    self.length = None

  def add(self, packet):
    if len(packet) < 6 or packet[0] != 1:
      raise ValueError("Invalid data packet")
    sequence = struct.unpack(">I", packet[1:5])[0]
    if sequence != self.sequence:
      raise ValueError("Out-of-order fragment")
    self.sequence += 1
    self.buffer.extend(packet[5:])
    if self.length is None and len(self.buffer) >= 4:
      self.length = struct.unpack(">I", self.buffer[:4])[0]
      if not 28 <= self.length <= MAX_FRAME:
        self.reset()
        raise ValueError("Invalid frame length")
    if len(self.buffer) > MAX_FRAME + 4:
      self.reset()
      raise ValueError("Frame limit exceeded")
    if self.length is not None and len(self.buffer) >= self.length + 4:
      if len(self.buffer) != self.length + 4:
        self.reset()
        raise ValueError("Trailing frame bytes")
      result = bytes(self.buffer[4:])
      self.reset()
      return result
    return None


def packets(frame, size=180):
  if not 20 <= size <= 512:
    raise ValueError("Invalid ATT payload size")
  for sequence, start in enumerate(range(0, len(frame), size - 5)):
    yield b"\x01" + struct.pack(">I", sequence) + frame[start:start + size - 5]


def stream_tag(session, counter):
  return hashlib.sha256(bytes.fromhex(session) + struct.pack(">Q", counter)).digest()[:8]


def notification_packets(frame, tag, size):
  if len(tag) != 8 or not 20 <= size <= 512:
    raise ValueError("Invalid notification framing")
  for sequence, start in enumerate(range(0, len(frame), size - 13)):
    yield b"\x03" + tag + struct.pack(">I", sequence) + frame[start:start + size - 13]
