import time

from opendbc.car import structs
from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
from opendbc.car.hyundai.values import CAR, HyundaiFlags


MAX_AGE_NS = 50_000_000
OWNERSHIP_RESPONSE_NS = 100_000_000 + MAX_AGE_NS
DISPLAY_FAULT_HOLD_NS = 2_100_000_000
DISPLAY_RELEASE_NS = 150_000_000


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


def preserves_stock_lkas_damping(cp):
  try:
    factory = CAR(cp.carFingerprint)
  except (ValueError, TypeError):
    return False
  excluded = HyundaiFlags.CANFD_ANGLE_STEERING | HyundaiFlags.NON_SCC
  return (cp.brand == 'hyundai' and bool(factory.config.flags & HyundaiFlags.CANFD) and
          not factory.config.flags & excluded and bool(cp.flags & HyundaiFlags.CANFD) and
          bool(cp.flags & HyundaiFlags.CANFD_LKA_STEER_MSG) and
          not cp.flags & excluded and not cp.openpilotLongitudinalControl and
          cp.steerControlType == structs.CarParams.SteerControlType.torque and
          not (cp.passive or cp.dashcamOnly or cp.notCar) and factory not in (
            CAR.KIA_CARNIVAL_4TH_GEN, CAR.KIA_CARNIVAL_2025,
            CAR.KIA_CARNIVAL_HEV_4TH_GEN, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN))


class CANFDLfaStatus:
  ADDRESS = 0x12A
  LENGTH = 16
  MAX_AGE_NS = MAX_AGE_NS

  def __init__(self, bus):
    self.bus = bus
    self.last_packet_ns = 0
    self.counter = None
    self.body = None
    self.observed_ns = 0
    self.eps_packet_ns = 0
    self.eps_counter = None
    self.eps_observed_ns = 0
    self.eps_active = False
    self.eps_healthy = False
    self.eps_active_since_ns = 0
    self.camera_integrity = False
    self.camera_healthy_ns = 0
    self.camera_fault_since_ns = 0
    self.camera_fault_same_batch_healthy = False
    self.request_active = False
    self.request_since_ns = 0
    self.display_fault_since_ns = 0
    self.display_last_active_ns = 0
    self.popup_last_applied_ns = 0

  def update(self, can_packets):
    boot_clock = getattr(time, 'CLOCK_BOOTTIME', None)
    if boot_clock is None:
      return
    try:
      now_ns = time.clock_gettime_ns(boot_clock)
    except OSError:
      return
    for stamp, frames in can_packets:
      if type(stamp) is not int or not 0 < stamp <= now_ns:
        continue
      camera_eligible = self.last_packet_ns < stamp
      eps_eligible = self.ADDRESS == 0x12A and self.eps_packet_ns < stamp
      for address, data, bus in frames:
        if eps_eligible and address == 0xEA and bus == self.bus - 2:
          self.eps_packet_ns = stamp
          if len(data) != 24 or int.from_bytes(data[:2], 'little') != hkg_can_fd_checksum(address, None, bytearray(data)):
            self.eps_healthy = False
            continue
          advancing = self.eps_counter is None or data[2] == (self.eps_counter + 1) % 256
          self.eps_counter = data[2]
          if not advancing:
            self.eps_healthy = False
            continue
          active = data[6] & 3
          if active == 1 and not self.eps_active:
            self.eps_active_since_ns = stamp
          self.eps_active = active == 1
          self.eps_healthy = active in (0, 1) and data[5] >> 6 in (0, 1) and not (data[3] & 7 or data[6] & 0xFC)
          self.eps_observed_ns = stamp
        if not camera_eligible:
          continue
        if address == self.ADDRESS and bus == self.bus and len(data) != self.LENGTH:
          self.camera_integrity = False
        if address != self.ADDRESS or bus != self.bus or len(data) != self.LENGTH:
          continue
        self.last_packet_ns = stamp
        if int.from_bytes(data[:2], 'little') != hkg_can_fd_checksum(address, None, bytearray(data)):
          self.camera_integrity = False
          continue
        counter = data[2]
        advancing = self.counter is None or counter == (self.counter + 1) % 256
        self.counter = counter
        if not advancing:
          self.camera_integrity = False
          continue
        self.camera_integrity = True
        body = bytes(data)
        if self._ownership_warning(body):
          if not self.camera_fault_since_ns:
            self.camera_fault_same_batch_healthy = (self.camera_healthy_ns == stamp and self.observed_ns == stamp and
                                                    self.body is not None and self.body[2] == (counter - 1) % 256)
            self.camera_fault_since_ns = stamp
        else:
          self.camera_fault_since_ns = 0
          self.camera_fault_same_batch_healthy = False
          indication = (body[4] >> 6) | ((body[5] & 1) << 2)
          self.camera_healthy_ns = stamp if indication < 4 and not (body[6] & 0xF0 or body[7] >> 4 == 15) else 0
        self.body, self.observed_ns = body, stamp

  def current(self, now_ns):
    if type(now_ns) is int and 0 < self.observed_ns <= now_ns and now_ns - self.observed_ns <= self.MAX_AGE_NS:
      return self.body
    return None


  @staticmethod
  def _ownership_warning(body):
    return (((body[4] >> 6) | ((body[5] & 1) << 2)) == 4 and body[7] >> 4 == 15 and
            not body[6] & 0xF0)

  def owns_display_status(self, active, now_ns, *, healthy=False):
    if active and not self.request_active:
      self.request_since_ns = now_ns
    self.request_active = active
    body = self.current(now_ns)
    eps_fresh = 0 < self.eps_observed_ns <= now_ns and now_ns - self.eps_observed_ns <= MAX_AGE_NS
    if not healthy or body is None or not self.camera_integrity or not eps_fresh or not self.eps_healthy:
      self.request_since_ns = self.display_fault_since_ns = self.display_last_active_ns = 0
      return False
    if not self._ownership_warning(body):
      self.display_fault_since_ns = self.display_last_active_ns = 0
      return False
    established = self.display_fault_since_ns == self.camera_fault_since_ns and self.display_fault_since_ns > 0
    deadline = max(self.display_fault_since_ns + DISPLAY_FAULT_HOLD_NS, self.display_last_active_ns + DISPLAY_RELEASE_NS)
    if active:
      if established and self.eps_active:
        self.display_last_active_ns = now_ns
        return True
      qualified = (self.eps_active and 0 < self.request_since_ns <= self.eps_active_since_ns <= self.camera_fault_since_ns and
                   ((0 < self.camera_fault_since_ns - self.camera_healthy_ns <= MAX_AGE_NS) or
                    (self.camera_fault_since_ns == self.camera_healthy_ns and self.camera_fault_same_batch_healthy)) and
                   self.camera_fault_since_ns - self.eps_active_since_ns <= OWNERSHIP_RESPONSE_NS)
      if qualified:
        self.display_fault_since_ns = self.camera_fault_since_ns
        self.display_last_active_ns = now_ns
        return True
      pending = 0 < self.request_since_ns <= now_ns <= self.request_since_ns + OWNERSHIP_RESPONSE_NS
      return established and pending and now_ns <= deadline
    if established and now_ns <= deadline:
      return True
    self.display_fault_since_ns = self.display_last_active_ns = 0
    return False

  def owns_disengage_popup(self, active, now_ns, *, healthy=False, qualified_warning=False):
    body = self.current(now_ns)
    eps_fresh = 0 < self.eps_observed_ns <= now_ns and now_ns - self.eps_observed_ns <= MAX_AGE_NS
    indication = ((body[4] >> 6) | ((body[5] & 1) << 2)) if body is not None else 7
    normal = body is not None and indication < 4 and not (body[6] & 0xF0 or body[7] >> 4 == 15)
    if not healthy or not self.camera_integrity or not eps_fresh or not self.eps_healthy or not (normal or qualified_warning):
      self.popup_last_applied_ns = 0
      return False
    if active and self.eps_active and 0 < self.request_since_ns <= self.eps_active_since_ns:
      self.popup_last_applied_ns = now_ns
      return True
    return 0 < self.popup_last_applied_ns <= now_ns <= self.popup_last_applied_ns + DISPLAY_RELEASE_NS

  def last_fault(self):
    body = self.body
    if body is not None and (body[6] & 0xC0 or body[7] & 0xF0 or ((body[4] >> 6) | ((body[5] & 1) << 2)) >= 4):
      return body
    return None


class CANFDLkasDamping(CANFDLfaStatus):
  def __init__(self, bus, alternate):
    super().__init__(bus)
    self.ADDRESS = 0x110 if alternate else 0x50
    self.LENGTH = 32 if alternate else 16

  def current_damping(self, now_ns):
    body = self.current(now_ns)
    return body[8] if body is not None else None

  def last_fault(self):
    return None


class CANFDClusterStatus(CANFDLfaStatus):
  ADDRESS = 0x1E0
  MAX_AGE_NS = 100_000_000

  def last_fault(self):
    return None
