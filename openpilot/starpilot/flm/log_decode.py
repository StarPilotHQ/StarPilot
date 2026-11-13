"""Bounded, all-or-nothing decode of an already admitted local log segment."""

import bz2
import io
from collections.abc import Callable
from typing import Any

import zstandard as zstd

from openpilot.cereal import log


MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_FULL_RLOG_BYTES = 128 * 1024 * 1024
MAX_MESSAGES = 250_000
EVENT_TRAVERSAL_WORDS = 1_000_000
CHUNK_BYTES = 64 * 1024


class LogDecodeError(ValueError):
  """The whole segment is unavailable; callers must discard every event."""


class LogDecodeCancelled(LogDecodeError):
  """Lost permission is not evidence that the recording is corrupt."""


def _not_cancelled(cancelled: Callable[[], bool]) -> None:
  try:
    if cancelled():
      raise LogDecodeCancelled("segment decode cancelled")
  except LogDecodeError:
    raise
  except Exception as error:
    raise LogDecodeCancelled("segment cancellation check unavailable") from error


def _bounded_read(reader: Any, cancelled: Callable[[], bool], expanded_limit: int = MAX_EXPANDED_BYTES) -> bytes:
  chunks: list[bytes] = []
  total = 0
  while True:
    _not_cancelled(cancelled)
    chunk = reader.read(min(CHUNK_BYTES, expanded_limit + 1 - total))
    if not chunk:
      break
    total += len(chunk)
    if total > expanded_limit:
      raise LogDecodeError(f"expanded segment exceeds {expanded_limit // (1024 * 1024)} MiB")
    chunks.append(chunk)
  return b"".join(chunks)


def _expand(data: bytes, codec: str, cancelled: Callable[[], bool], expanded_limit: int = MAX_EXPANDED_BYTES) -> bytes:
  try:
    if codec == "zst":
      with zstd.ZstdDecompressor(max_window_size=expanded_limit).stream_reader(io.BytesIO(data)) as reader:
        expanded = _bounded_read(reader, cancelled, expanded_limit)
      # stream_reader in pinned zstandard 0.25 returns short data for a
      # truncated frame. Validate the complete frame after proving its output
      # fits our bound, and reject a concatenated/trailing second frame.
      decoder = zstd.ZstdDecompressor(max_window_size=expanded_limit).decompressobj()
      verified = decoder.decompress(data)
      if not decoder.eof or decoder.unused_data or verified != expanded:
        raise LogDecodeError("incomplete or trailing zstd frame")
    elif codec == "bz2":
      with bz2.BZ2File(io.BytesIO(data), "rb") as reader:
        expanded = _bounded_read(reader, cancelled, expanded_limit)
      decoder = bz2.BZ2Decompressor()
      verified = decoder.decompress(data)
      if not decoder.eof or decoder.unused_data or verified != expanded:
        raise LogDecodeError("incomplete or trailing bzip2 stream")
    else:
      raise LogDecodeError("unsupported segment compression")
    _not_cancelled(cancelled)
    return expanded
  except LogDecodeError:
    raise
  except (OSError, EOFError, ValueError, zstd.ZstdError) as error:
    raise LogDecodeError("corrupt or incomplete compressed segment") from error


def _decode_segment(data: bytes, codec: str, *, cancelled: Callable[[], bool], retain: frozenset[str] | None, expanded_limit: int) -> tuple[Any, ...]:
  """Return all complete Event readers, or reject the entire segment."""
  if type(data) is not bytes or len(data) > MAX_COMPRESSED_BYTES or type(codec) is not str or codec not in ("zst", "bz2"):
    raise LogDecodeError("unsupported or oversized compressed segment")
  _not_cancelled(cancelled)
  expanded = _expand(data, codec, cancelled, expanded_limit)
  events: list[Any] = []
  try:
    for count, event in enumerate(log.Event.read_multiple_bytes(expanded, traversal_limit_in_words=EVENT_TRAVERSAL_WORDS)):
      # Cancellation remains all-or-nothing. Poll at a bounded record
      # interval instead of performing physical/Params/resource I/O for every
      # logged message. Envelope, union and count checks still run EVERY time.
      if count % 128 == 0:
        _not_cancelled(cancelled)
      if count >= MAX_MESSAGES:
        raise LogDecodeError("segment message count exceeded")
      # Force envelope and union decoding before a reader can escape; a later
      # malformed record invalidates the entire segment, never a prefix.
      int(event.logMonoTime)
      kind = event.which()
      if retain is None or kind in retain:
        events.append(event)
  except LogDecodeError:
    raise
  except Exception as error:
    raise LogDecodeError("corrupt Cereal Event segment") from error
  _not_cancelled(cancelled)
  return tuple(events)


def decode_segment(data: bytes, codec: str, *, cancelled: Callable[[], bool] = lambda: False, retain: frozenset[str] | None = None) -> tuple[Any, ...]:
  """Existing interactive analysis retains its original 64 MiB bound."""
  return _decode_segment(data, codec, cancelled=cancelled, retain=retain, expanded_limit=MAX_EXPANDED_BYTES)


def decode_full_rlog_segment(data: bytes, codec: str, *, cancelled: Callable[[], bool], retain: frozenset[str]) -> tuple[Any, ...]:
  """Offroad full-rlog reader; complete input, cancellation and event limits stay strict.

  Recorded full rlogs exceed 64 MiB after expansion. This separate backend
  entry has a fixed 128 MiB ceiling; callers cannot raise either ceiling.
  """
  if type(retain) is not frozenset or not retain or any(type(name) is not str for name in retain):
    raise LogDecodeError("explicit retained offroad services required")
  return _decode_segment(data, codec, cancelled=cancelled, retain=retain, expanded_limit=MAX_FULL_RLOG_BYTES)
