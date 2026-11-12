import time

from opendbc.car import structs
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.car.hyundai.values import CAR, HyundaiFlags


MAX_AGE_NS = 50_000_000


def preserves_stock_lfa(cp):
  try:
    factory = CAR(cp.carFingerprint)
  except (ValueError, TypeError):
    return False
  excluded = HyundaiFlags.CANFD_ANGLE_STEERING | HyundaiFlags.NON_SCC
  return (cp.brand == 'hyundai' and bool(factory.config.flags & HyundaiFlags.CANFD) and
          not factory.config.flags & excluded and bool(cp.flags & HyundaiFlags.CANFD) and
          not cp.flags & (excluded | HyundaiFlags.CANFD_LKA_STEER_MSG) and
          cp.steerControlType == structs.CarParams.SteerControlType.torque and
          not (cp.passive or cp.dashcamOnly or cp.notCar) and
          factory not in (CAR.KIA_CARNIVAL_4TH_GEN, CAR.KIA_CARNIVAL_2025, CAR.KIA_CARNIVAL_HEV_4TH_GEN))


class CANFDLfaStatus:
  ADDRESS = 0x12A
  MAX_AGE_NS = MAX_AGE_NS

  def __init__(self, bus):
    self.bus = bus
    self.last_packet_ns = 0
    self.counter = None
    self.body = None
    self.observed_ns = 0

  def update(self, can_packets):
    boot_clock = getattr(time, 'CLOCK_BOOTTIME', None)
    if boot_clock is None:
      return
    try:
      now_ns = time.clock_gettime_ns(boot_clock)
    except OSError:
      return
    for stamp, frames in can_packets:
      if type(stamp) is not int or not self.last_packet_ns < stamp <= now_ns:
        continue
      for address, data, bus in frames:
        if address != self.ADDRESS or bus != self.bus or len(data) != 16:
          continue
        self.last_packet_ns = stamp
        if int.from_bytes(data[:2], 'little') != hkg_can_fd_checksum(address, None, bytearray(data)):
          continue
        counter = data[2]
        advancing = self.counter is None or counter == (self.counter + 1) % 256
        self.counter = counter
        if not advancing:
          continue
        self.body, self.observed_ns = bytes(data), stamp

  def current(self, now_ns):
    if type(now_ns) is int and 0 < self.observed_ns <= now_ns and now_ns - self.observed_ns <= self.MAX_AGE_NS:
      return self.body
    return None

  def last_fault(self):
    body = self.body
    if body is not None and (body[6] & 0xC0 or body[7] & 0xF0 or ((body[4] >> 6) | ((body[5] & 1) << 2)) >= 4):
      return body
    return None


class CANFDClusterStatus(CANFDLfaStatus):
  ADDRESS = 0x1E0
  MAX_AGE_NS = 100_000_000

  def last_fault(self):
    return None
