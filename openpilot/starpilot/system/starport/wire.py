"""Bounded protobuf wire helpers shared by the Android Auto session and wireless bootstrap.

No generated protobuf runtime is needed: only varint and length-delimited fields
appear in the subset of messages StarPilot sends or reads. Unknown fields are
preserved by ``parse_fields`` so diagnostics can report them without guessing.

Adapted from yummydirtx/openpilot ``tools/starport/session.py`` (MIT),
pinned at 672a16f6183567c0ada53654f8527d97e1a483fa.
"""

from __future__ import annotations

from typing import overload


def varint(value: int) -> bytes:
  if not 0 <= value < 1 << 64:
    raise ValueError("Expected an unsigned 64-bit protobuf integer")
  out = bytearray()
  while value > 127:
    out.append((value & 127) | 128)
    value >>= 7
  out.append(value)
  return bytes(out)


def field(number: int, value: int | str | bytes) -> bytes:
  """Encode one protobuf field. Negative ints use proto2 int32/int64 sign extension."""
  if isinstance(value, str):
    value = value.encode()
  if isinstance(value, bytes):
    return varint(number << 3 | 2) + varint(len(value)) + value
  if value < 0:
    value += 1 << 64
  return varint(number << 3) + varint(value)


def parse_fields(data: bytes) -> dict[int, list[int | bytes]]:
  """Decode protobuf wire fields into ``{number: [values]}`` with strict bounds."""
  pos = 0

  def integer() -> int:
    nonlocal pos
    result = 0
    for shift in range(0, 70, 7):
      if pos >= len(data):
        raise ValueError("Truncated protobuf varint")
      byte = data[pos]
      pos += 1
      if shift == 63 and byte > 1:
        raise ValueError("Overflowing protobuf varint")
      result |= (byte & 127) << shift
      if byte < 128:
        return result
    raise ValueError("Overlong protobuf varint")

  result: dict[int, list[int | bytes]] = {}
  while pos < len(data):
    tag = integer()
    number, wire = tag >> 3, tag & 7
    if not 0 < number < 1 << 29:
      raise ValueError("Invalid protobuf field number")
    value: int | bytes
    if wire == 0:
      value = integer()
    else:
      if wire == 2:
        size = integer()
      elif wire in (1, 5):
        size = 8 if wire == 1 else 4
      else:
        raise ValueError(f"Unsupported protobuf wire type {wire}")
      if size > len(data) - pos:
        raise ValueError("Truncated protobuf field")
      value = bytes(data[pos:pos + size])
      pos += size
    result.setdefault(number, []).append(value)
  return result


def one(fields: dict, number: int, default=None):
  return fields.get(number, [default])[0]


@overload
def signed(value: int) -> int: ...


@overload
def signed(value: None) -> None: ...


def signed(value: int | None) -> int | None:
  """Interpret a decoded varint as a two's-complement int64 (proto2 int32 negatives)."""
  if value is None:
    return None
  return value - (1 << 64) if value >= 1 << 63 else value


def text(fields: dict, number: int, default: str = "") -> str:
  value = one(fields, number)
  if not isinstance(value, bytes):
    return default
  return value.decode("utf-8", "replace")


def json_fields(fields: dict) -> dict:
  """Keep unknown protobuf bytes inspectable without guessing their schema."""
  return {number: [{"hex": value.hex()} if isinstance(value, bytes) else value for value in values]
          for number, values in fields.items()}


DESCRIBE_TEXT = 512
DESCRIBE_DEPTH = 4
DESCRIBE_REPEATED = 32  # values kept per field; a head unit lists at most a few dozen keycodes or sensors
DESCRIBE_HEX = 64       # bytes of an opaque value kept as hex


def _describe_value(value, depth: int):
  if not isinstance(value, bytes):
    return value
  try:
    decoded = value.decode("utf-8")
    if decoded and decoded.isprintable():
      return decoded[:DESCRIBE_TEXT] if len(decoded) <= DESCRIBE_TEXT else decoded[:DESCRIBE_TEXT] + "…"
  except UnicodeDecodeError:
    pass
  if depth > 0 and value:
    try:
      return describe_fields(parse_fields(value), depth - 1)
    except ValueError:
      pass
  described = {"hex": value[:DESCRIBE_HEX].hex()}
  if len(value) > DESCRIBE_HEX:
    described["bytes"] = len(value)
  return described


def describe_fields(fields: dict, depth: int = DESCRIBE_DEPTH) -> dict:
  """A bounded, readable view of a message whose schema we may not know, for diagnostics.

  Text stays text and nested messages are decoded, so a head unit's make, model and
  capabilities read directly in a log; anything else is a short hex prefix.
  """
  return {number: [_describe_value(value, depth) for value in values[:DESCRIBE_REPEATED]]
          for number, values in fields.items()}


def describe(data: bytes, depth: int = DESCRIBE_DEPTH):
  """``describe_fields`` of raw bytes, or their hex prefix when they are not protobuf."""
  try:
    return describe_fields(parse_fields(data), depth)
  except ValueError:
    return _describe_value(data, 0)
