"""Receive-only Ioniq 6 wheel media evidence, independent of CAN validity.

Only exact physical-bus 0x448/8 packets establish a sample. Times come from
the CAN receive timeline; consumers must not compare them to MONOTONIC.
No gesture, saved assignment, effective mode or CAN transmission lives here.
"""

from dataclasses import dataclass


MEDIA_ADDRESS = 0x448
MEDIA_MAX_GAP_NS = 300_000_000


@dataclass(frozen=True)
class MediaSample:
  mode_held: bool
  custom_held: bool
  source_boot_ns: int


@dataclass(frozen=True)
class MediaObservation:
  samples: tuple[MediaSample, ...]
  parser_boot_ns: int
  source_epoch: int
  valid: bool


class Ioniq6MediaButtons:
  """Preserve packet order without counting a cached held bit as new input.

  The recorded Ioniq neutral source runs at about 5 Hz. A 300 ms bound allows
  normal cadence but clears a held sequence across a missing source. Press
  transitions and their physical cadence still require vehicle qualification.
  The caller must restrict construction to the reviewed Ioniq configuration.
  """

  def __init__(self, bus: int):
    if type(bus) is not int or bus not in (0, 1, 2, 3, 5):
      raise ValueError("physical CAN bus required")
    self.bus = bus
    self.epoch = 0
    self.last_batch_ns = 0
    self.last_source_ns = 0

  def _invalid(self, batch_ns: int) -> MediaObservation:
    self.epoch += 1
    self.last_source_ns = 0
    self.last_batch_ns = batch_ns
    return MediaObservation((), batch_ns, self.epoch, False)

  def update(self, packets) -> MediaObservation:
    samples = []
    batch_ns = self.last_batch_ns
    source_ns = self.last_source_ns
    if not packets:
      return self._invalid(batch_ns)
    for stamp, frames in packets:
      if type(stamp) is not int or stamp <= 0:
        return self._invalid(batch_ns)
      if stamp < batch_ns:
        return self._invalid(stamp)
      batch_ns = stamp
      for address, data, bus in frames:
        if address != MEDIA_ADDRESS or bus != self.bus:
          continue
        if (type(address) is not int or type(bus) is not int or not isinstance(data, (bytes, bytearray)) or
            len(data) != 8 or stamp <= source_ns or
            source_ns and stamp - source_ns > MEDIA_MAX_GAP_NS):
          return self._invalid(batch_ns)
        # Motorola one-bit signals 22 and 44 in STEERING_WHEEL_MEDIA_BUTTONS.
        samples.append(MediaSample(bool(data[2] & 0x40), bool(data[5] & 0x10), stamp))
        source_ns = stamp
    if source_ns <= 0 or batch_ns - source_ns > MEDIA_MAX_GAP_NS:
      return self._invalid(batch_ns)
    self.last_batch_ns = batch_ns
    self.last_source_ns = source_ns
    return MediaObservation(tuple(samples), batch_ns, self.epoch, True)
