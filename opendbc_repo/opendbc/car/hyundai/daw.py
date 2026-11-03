from opendbc.car import CanData
from opendbc.car.hyundai.hyundaicanfd import CanBus, hkg_can_fd_checksum
from opendbc.car.hyundai.values import CAR, HyundaiFlags, is_blended


CANFD_DAW_WORDS = frozenset((0x11, 0x91, 0x811, 0x891, 0x8015, 0x8095, 0x8815, 0x8895))


class DawSource:
  def __init__(self, cp):
    self.canfd = bool(cp.flags & HyundaiFlags.CANFD)
    self.enabled = (cp.brand == 'hyundai' and not cp.passive and not cp.dashcamOnly and not cp.notCar and
                    not is_blended(cp) and (not self.canfd or cp.carFingerprint == CAR.HYUNDAI_IONIQ_6))
    if self.canfd:
      self.enabled = bool(self.enabled and cp.safetyConfigs and
                          int(cp.safetyConfigs[-1].safetyParam) in CANFD_DAW_WORDS and CanBus(cp).ECAN % 4 == 1)
    self.address = 0x11A if self.canfd else 0x53E
    self.source_bus = CanBus(cp).ECAN if self.canfd else 2
    self.target_bus = CanBus(cp).ECAN if self.canfd else 0
    self.length = 16 if self.canfd else 6
    self.data = None
    self.stamp = 0

  def update(self, packets):
    if not self.enabled:
      return
    for stamp, frames in packets:
      for address, data, bus in frames:
        if address != self.address or bus != self.source_bus:
          continue
        if stamp <= self.stamp or len(data) != self.length or (self.canfd and
            int.from_bytes(data[:2], 'little') != hkg_can_fd_checksum(address, None, data)):
          self.data = None
          continue
        self.data, self.stamp = bytes(data), stamp

  def command(self, now_ns):
    if (not self.enabled or self.source_bus == self.target_bus or self.data is None or
        not 0 < self.stamp <= now_ns <= self.stamp + 100_000_000):
      return []
    data = bytearray(self.data)
    if self.canfd:
      if (data[7] >> 3) & 7 == 1:
        data[7] &= ~0x38
      data[:2] = hkg_can_fd_checksum(self.address, None, data).to_bytes(2, 'little')
    elif 1 <= data[5] & 7 <= 3:
      data[5] &= ~7
    return [CanData(self.address, bytes(data), self.target_bus)]
