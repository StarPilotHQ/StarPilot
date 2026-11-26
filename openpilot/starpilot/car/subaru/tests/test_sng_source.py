"""capture() source validity when pandad batches a 100 Hz source message.

capture() stores at most one sample per CAN batch -- the CANParser's newest
accepted frame. When a batch carries two Throttle frames, the stored counter
therefore advances by 2, and validity must mean "the counter advanced", not
"advanced by exactly one" (the 2026-11-24 SNG standstill failure). The panda
still enforces the strict +1 sequence on the raw bus; this flag is only the
host-side health signal for the StopAndGo state machine.
"""

from types import SimpleNamespace

import pytest

from opendbc.car import Bus
from opendbc.car.subaru.sng_source import NAMES, capture


def frame(address, counter):
  data = bytearray(8)
  data[1] = counter & 15
  data[0] = ((address & 255) + (address >> 8) + sum(data[1:])) & 255
  return bytes(data)


class FakeParser:
  """Stand-in for the CANParser acceptance capture() keys on.

  vl/ts_nanos always hold the newest accepted sample per message, exactly as
  the real parser leaves them after consuming a batch.
  """

  def __init__(self):
    self.vl = {name: {'COUNTER': 0} for name in NAMES.values()}
    self.ts_nanos = {name: {'COUNTER': 0} for name in NAMES.values()}

  def update(self, packets):
    for timestamp, frames in packets:
      for address, data, bus in frames:
        if address in NAMES and bus == 0:
          name = NAMES[address]
          self.vl[name] = {'COUNTER': data[1] & 15}
          self.ts_nanos[name] = {'COUNTER': timestamp}


class Harness:
  def __init__(self):
    self.parser = FakeParser()
    self.interface = SimpleNamespace(CS=SimpleNamespace(), can_parsers={Bus.pt: self.parser})

  def feed_counters(self, timestamp, address, counters):
    packets = [(timestamp, [(address, frame(address, counter), 0) for counter in counters])]
    self.parser.update(packets)  # production order: the parser consumes the batch first
    capture(self.interface, packets)

  def stored(self, address):
    return self.interface.CS.sng_sources[address]

  def valid(self, address):
    return self.interface.CS.sng_source_valid[address]


@pytest.mark.parametrize('address', [0x40, 0x139])
def test_first_sample_is_not_valid(address):
  h = Harness()
  h.feed_counters(1_000_000, address, [5])
  assert h.stored(address)[1][1] & 15 == 5
  assert h.valid(address) is False


@pytest.mark.parametrize('address', [0x40, 0x139])
def test_sequential_advance_is_valid(address):
  h = Harness()
  h.feed_counters(1_000_000, address, [5])
  h.feed_counters(2_000_000, address, [6])
  assert h.valid(address) is True


@pytest.mark.parametrize('address', [0x40, 0x139])
def test_two_frames_in_one_batch_stay_valid(address):
  # One batch carries counters 6 and 7: the parser accepts both, capture can
  # only store the newest, and the stored counter jumps by 2.
  h = Harness()
  h.feed_counters(1_000_000, address, [5])
  h.feed_counters(2_000_000, address, [6, 7])
  assert h.stored(address)[1][1] & 15 == 7
  assert h.valid(address) is True
  h.feed_counters(3_000_000, address, [8, 9])
  assert h.stored(address)[1][1] & 15 == 9
  assert h.valid(address) is True


def test_stalled_counter_never_becomes_valid():
  h = Harness()
  h.feed_counters(1_000_000, 0x40, [5])
  first = h.stored(0x40)
  for timestamp in (2_000_000, 3_000_000, 4_000_000):
    h.feed_counters(timestamp, 0x40, [5])
  # No advance: nothing new is stored, so the sample's timestamp keeps aging
  # and the sender's freshness gate retires it; it never reads valid.
  assert h.stored(0x40) == first
  assert h.valid(0x40) is False


def test_stalled_counter_does_not_refresh_a_valid_source():
  h = Harness()
  h.feed_counters(1_000_000, 0x40, [5])
  h.feed_counters(2_000_000, 0x40, [6])
  assert h.valid(0x40) is True
  stored = h.stored(0x40)
  h.feed_counters(3_000_000, 0x40, [6])
  assert h.stored(0x40) == stored


def test_counter_wraparound():
  h = Harness()
  h.feed_counters(1_000_000, 0x40, [14])
  h.feed_counters(2_000_000, 0x40, [15])
  h.feed_counters(3_000_000, 0x40, [0])  # 15 -> 0 is an ordinary +1 step modulo 16
  assert h.valid(0x40) is True
  h.feed_counters(4_000_000, 0x40, [1, 2])  # batched across the wrap: stored counter jumps 0 -> 2
  assert h.stored(0x40)[1][1] & 15 == 2
  assert h.valid(0x40) is True
