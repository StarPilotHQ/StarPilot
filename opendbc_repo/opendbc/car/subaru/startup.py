"""One bounded opt-in Stop/Start OFF request; stock payload and source clocks retained."""
from opendbc.car import structs
from opendbc.car.subaru.values import CAR, SubaruFlags, SubaruSafetyFlags
from opendbc.car.subaru.stock_aol import qualified

KEY = 'SubaruStopStartOff'
ADDRESSES = (0x390, 0x48, 0x174, 0x221)
BUSES = {0x390: 1, 0x48: 0, 0x174: 1, 0x221: 1}


def checksum(address, data):
  return ((address & 255) + (address >> 8) + sum(data[1:])) & 255


def supported(cp):
  try:
    return (qualified(cp) and cp.brand == 'subaru' and cp.carFingerprint in (CAR.SUBARU_LEGACY_2025, CAR.SUBARU_OUTBACK_2023) and
            not cp.notCar and not cp.passive and not cp.dashcamOnly and not cp.openpilotLongitudinalControl and
            cp.pcmCruise and len(cp.safetyConfigs) == 1 and
            cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.subaru and
            cp.safetyConfigs[0].safetyParam in (145, 209, 401, 465, 1169, 1425))
  except (AttributeError, IndexError, TypeError):
    return False


def selected(cp):
  return bool(supported(cp) and cp.flags & SubaruFlags.STOP_START_OFF and cp.safetyConfigs[0].safetyParam in (401, 465, 1425))


def prepare(cp, enabled):
  if enabled and supported(cp):
    cp.flags |= int(SubaruFlags.STOP_START_OFF)
    cp.safetyConfigs[0].safetyParam |= int(SubaruSafetyFlags.STOP_START_BUTTON)
  return cp


class StartupSources:
  def __init__(self, cp=None):
    self.buses = dict(BUSES)
    self.state_zero_supported = cp is not None and selected(cp) and cp.carFingerprint == CAR.SUBARU_OUTBACK_2023
    if cp is not None and cp.carFingerprint == CAR.SUBARU_OUTBACK_2023:
      self.buses[0x48] = 1
    self.blocked = False
    self.frames = {}
    self.sequential = {}
    self.source_ns = {}

  def observe(self, packets, *, boot_ns, mono_ns):
    for stamp, frames in packets:
      for address, data, bus in frames:
        if address not in ADDRESSES or bus != self.buses[address]:
          continue
        data = bytes(data)
        if (len(data) != 8 or stamp <= 0 or not 0 <= boot_ns - stamp <= 300_000_000 or
            checksum(address, data) != data[0]):
          self.blocked = True
          self.frames.pop(address, None)
          self.sequential[address] = False
          continue
        if ((address == 0x390 and data[6] & 0x40) or (address == 0x174 and data[4] >> 6 == 3) or
            (address == 0x221 and data[1] & 0x80)):
          self.blocked = True
        old = self.frames.get(address)
        counter = data[1] & 15
        if old is not None and (old[1][1] & 15) == counter:
          if data != old[1]:
            self.blocked = True
            self.frames.pop(address, None)
            self.sequential[address] = False
          continue
        source_mono = mono_ns - (boot_ns - stamp)
        if old is not None and source_mono <= self.source_ns[address]:
          self.frames.pop(address, None)
          self.sequential[address] = False
          continue
        self.sequential[address] = bool(old is not None and counter == ((old[1][1] + 1) & 15))
        self.source_ns[address] = source_mono
        self.frames[address] = source_mono / 1e9, data

  def fresh(self, now):
    return all(address in self.frames and self.sequential.get(address, False) and
               0 <= now - self.frames[address][0] <= .3 for address in ADDRESSES)


class StopStartRequest:
  def __init__(self):
    self.configured_since = None
    self.configured_last = None
    self.start = None
    self.pulse_start = None
    self.pulse_start_ns = 0
    self.last_send = None
    self.last_counter = None
    self.done = False

  def observe_configured(self, configured, now):
    continuous = self.configured_last is not None and 0 <= now - self.configured_last <= .15
    if not configured or not continuous:
      self.configured_since = None
      if self.pulse_start is not None:
        self.done = True
    if configured:
      if self.configured_since is None:
        self.configured_since = now
      self.configured_last = now
    else:
      self.configured_last = None

  def update(self, command, state, now, *, now_nanos=None):
    now_nanos = round(now * 1e9) if now_nanos is None else now_nanos
    if self.start is None:
      self.start = now
    if self.done:
      return None
    if (now < self.start or now - self.start > 10 or command.enabled or command.latActive or command.longActive or
        state.out.gasPressed or not state.out.standstill or state.out.steerFaultPermanent or
        state.out.steerFaultTemporary or state.out.accFaulted):
      self.done = True
      return None
    source = state.startup_sources
    if source.blocked:
      self.done = True
      return None
    out = state.out
    if (not out.canValid or out.canTimeout or not source.fresh(now) or out.steerFaultPermanent or out.steerFaultTemporary or out.accFaulted or
        not out.standstill or out.gasPressed or
        out.gearShifter not in (structs.CarState.GearShifter.park, structs.CarState.GearShifter.neutral)):
      if self.pulse_start is not None:
        self.done = True
      return None
    if (self.configured_since is None or self.configured_last is None or
        not 0 <= now - self.configured_last <= .15 or now - self.configured_since < 1.):
      return None
    # State3 is the Dom OFF acknowledgement; never toggle an already OFF system.
    engine_state = source.frames[0x174][1][4] >> 6
    # This Outback reports state0 before an OEM OFF press, then acknowledges with state3.
    # Other qualified platforms retain their existing state1/2-only contract.
    if engine_state == 0 and not source.state_zero_supported:
      return None
    if engine_state == 3:
      self.done = True
      return None
    if now - self.start < 1.:
      return None
    if self.pulse_start is None:
      self.pulse_start = now
      self.pulse_start_ns = now_nanos
    if now_nanos - self.pulse_start_ns >= 300_000_000:
      self.done = True
      return None
    if self.last_send is not None and now - self.last_send < .05:
      return None
    template = source.frames[0x390][1]
    if template[6] & 0x40:
      self.done = True
      return None
    counter = template[1] & 15
    if counter == self.last_counter:
      return None
    data = bytearray(template)
    data[1] = (data[1] & 0xf0) | ((counter + 1) & 15)
    data[6] |= 0x40
    data[0] = checksum(0x390, data)
    self.last_counter = counter
    self.last_send = now
    return 0x390, bytes(data), 1
