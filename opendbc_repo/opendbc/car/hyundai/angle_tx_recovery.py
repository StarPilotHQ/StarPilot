"""Correlate recent local steering rejection echoes; never authorize output."""
from collections import deque
import time


class AngleTxRecovery:
  def __init__(self, address, bus, clock=None):
    self.clock = time.monotonic_ns if clock is None else clock
    self.address = address
    self.bus = bus
    self.length = 24 if address == 0xCB else 32
    self.active_index = 3 if address == 0xCB else 9
    self.active_mask = 0xF if address == 0xCB else 3
    self.sent = deque(maxlen=4)
    self.last_packet_ns = 0
    self.pending_frame = -1
    self.pending_sent_ns = -1

  def record(self, frames, frame):
    now_ns = self.clock()
    for address, data, bus in frames:
      if address == self.address and bus == self.bus and len(data) == self.length and (data[self.active_index] >> 4 & self.active_mask) == 2:
        self.sent.append((frame, now_ns, bytes(data)))

  def observe(self, packets, frame):
    now_ns = self.clock()
    for stamp, frames in packets:
      if stamp <= self.last_packet_ns:
        continue
      self.last_packet_ns = stamp
      for address, data, bus in frames:
        if address != self.address or bus != self.bus + 0xC0 or len(data) != self.length:
          continue
        # Consume an exact recent command once. The original local send clock
        # also bounds delayed echoes when controller updates have been paused.
        match = next(((sent_frame, sent_ns, sent_data) for sent_frame, sent_ns, sent_data in self.sent
                      if 0 < frame - sent_frame <= 3 and 0 <= now_ns - sent_ns <= 30_000_000 and sent_data == bytes(data)), None)
        if match is not None:
          self.sent.remove(match)
          self.pending_frame = frame
          self.pending_sent_ns = match[1]

  def take_rejection(self, frame):
    pending = self.pending_frame == frame and 0 <= self.clock() - self.pending_sent_ns <= 30_000_000
    self.pending_frame = -1
    return pending
