"""Shared captured ADRV startup for explicitly selected first-generation cars."""
import time

from opendbc.car import Bus
from opendbc.can.parser import MAX_BAD_COUNTER
from opendbc.car.hyundai.ecu_startup import Outcome
from opendbc.car.hyundai.ecu_continuity import ContinuousECUStartup
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.hyundai.canfd_owner import topology_index

# Integer masks avoid constructing IntFlag objects in live 100 Hz admission checks.
_FLAG_CANFD = HyundaiFlags.CANFD.value
_FLAG_CANFD_ALT_BUTTONS = HyundaiFlags.CANFD_ALT_BUTTONS.value
_FLAG_CANFD_ANGLE_STEERING = HyundaiFlags.CANFD_ANGLE_STEERING.value
_FLAG_CANFD_CAMERA_SCC = HyundaiFlags.CANFD_CAMERA_SCC.value
_FLAG_CANFD_LKA_STEER_MSG = HyundaiFlags.CANFD_LKA_STEER_MSG.value
_FLAG_CANFD_LKA_STEER_MSG_ALT = HyundaiFlags.CANFD_LKA_STEER_MSG_ALT.value
_FLAG_CCNC = HyundaiFlags.CCNC.value
_FLAG_EV = HyundaiFlags.EV.value
_FLAG_HYBRID = HyundaiFlags.HYBRID.value

_EXCLUDED = (_FLAG_CANFD_ANGLE_STEERING | _FLAG_CANFD_LKA_STEER_MSG_ALT |
             _FLAG_CANFD_ALT_BUTTONS | _FLAG_CANFD_CAMERA_SCC | _FLAG_CCNC |
             _FLAG_HYBRID)


def first_generation(cp):
  owner = topology_index(cp)
  if owner is None:
    return False
  buses = CanBus(cp)
  offset = 4 * owner
  return (int(cp.flags) & _FLAG_CANFD and
          int(cp.flags) & _FLAG_EV and int(cp.flags) & _FLAG_CANFD_LKA_STEER_MSG and
          not int(cp.flags) & _EXCLUDED and (buses.ACAN, buses.ECAN, buses.CAM) == (offset, offset + 1, offset + 2) and
          not cp.passive and not cp.dashcamOnly and not cp.notCar)


class CapturedADRVStartup(ContinuousECUStartup):
  def __init__(self, cp, callbacks, *, label, template_type):
    buses = CanBus(cp)
    super().__init__(cp, callbacks, address=0x730, bus=buses.ECAN, label=label)
    self.template_bus = buses.ACAN
    self.camera_bus = buses.CAM
    self.template_type = template_type
    self.template = None

  def continuity_required(self):
    return (self.cp.carFingerprint in (CAR.KIA_EV6, CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN) and
            self.cp.openpilotLongitudinalControl and first_generation(self.cp))

  def _capture(self):
    floor = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    deadline = time.monotonic() + 1.
    previous = None
    while time.monotonic() < deadline:
      for packet in self.callbacks[0]() or []:
        stamp = int(getattr(packet, 'log_mono_time_ns', 0))
        now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        if not floor < stamp <= now or now - stamp > 150_000_000:
          continue
        for msg in packet:
          if msg.src != self.template_bus or msg.address != 0x51:
            continue
          try:
            candidate = self.template_type.capture(bytes(msg.dat))
          except ValueError:
            previous = None
            continue
          if previous is not None and stamp > previous[0] and candidate.counter == (previous[1].counter + 1) % 256:
            return candidate
          previous = stamp, candidate
      time.sleep(.005)
    return None

  def prepare(self, *, admission):
    # No ECU request is issued when current-session capture is unavailable.
    self.template = self._capture()
    return super().prepare(admission=admission if self.template is not None else lambda: False)

  def configure(self, ci):
    super().configure(ci)
    if self.outcome is Outcome.SENT_UNCONFIRMED:
      if self.template is None:
        raise RuntimeError(f'{self.label} sent startup lacks its current-instance template')
      ci.CC.adrv_template = self.template

  def _sources(self, ci, now, *, floor):
    state = ci.CS.out
    if not state.canValid or state.canTimeout:
      return False
    pt = ci.can_parsers[Bus.pt]
    cam = ci.can_parsers[Bus.cam]
    if pt.bus != self.bus or cam.bus != self.camera_bus:
      return False
    names = ['ACCELERATOR', 'TCS', 'WHEEL_SPEEDS', 'MDPS', 'CRUISE_BUTTONS', ci.CS.gear_msg_canfd]
    if not ci.CP.openpilotLongitudinalControl:
      names.append('SCC_CONTROL')
    for parser, sources in ((pt, set(names)), (cam, {'CAM_0x2a4'})):
      for name in sources:
        source = parser.message_states.get(parser.dbc.name_to_msg[name].address)
        if source is None or not source.timestamps or source.counter_fail >= MAX_BAD_COUNTER:
          return False
        stamp = int(source.timestamps[-1])
        # Existing ordinary buttons can pause ~0.5s: retain the 1Hz host policy.
        if not floor < stamp <= now or now - stamp > source.timeout_threshold:
          return False
    return True

  def sources_current(self, ci, now_ns):
    return self.outcome is not Outcome.SENT_UNCONFIRMED or self._sources(ci, now_ns, floor=self.floor_ns)

  def _warm_stock(self, ci):
    deadline = time.monotonic() + 3.
    while time.monotonic() < deadline:
      packets = self.callbacks[0]()
      stamped = [(int(getattr(packet, 'log_mono_time_ns', 0)), list(packet)) for packet in packets]
      if any(stamp <= 0 for stamp, _ in stamped):
        raise RuntimeError(f'{self.label} stock warmup requires actual timestamped CAN')
      ci.update(stamped)
      if self._sources(ci, time.clock_gettime_ns(time.CLOCK_BOOTTIME), floor=self.floor_ns):
        self.ready = True
        return
      time.sleep(.005)
    self.outcome = Outcome.ABORT_UNCERTAIN
    raise RuntimeError(f'{self.label} stock CANFD sources did not become fresh before publication')
