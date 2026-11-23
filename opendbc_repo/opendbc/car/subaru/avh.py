"""One bounded Subaru AVH ON request; 0x32B is status, never a TX command."""

AVH_REQUEST = 0x6BB
AVH_STATUS = 0x32B
INPUTS = (AVH_REQUEST, AVH_STATUS, 0x40, 0x48, 0x13A, 0x174)


def checksum(address, data):
  return ((address & 0xFF) + (address >> 8) + sum(data[1:])) & 0xFF


def avh_request(template, step):
  if len(template) != 8 or checksum(AVH_REQUEST, template) != template[0] or template[2] & 3 or step not in (1, 2, 3, 4):
    raise ValueError("Invalid AVH template or counter step")
  data = bytearray(template)
  data[1] = (data[1] & 0xF0) | ((data[1] + step) & 0xF)
  data[2] |= 2
  data[0] = checksum(AVH_REQUEST, data)
  return AVH_REQUEST, bytes(data), 1


class AvhStartup:
  def __init__(self):
    self.configured_since = None
    self.configured_last = None
    self.started = None
    self.last_time = None
    self.stable_since = None
    self.frames = {}
    self.avh_req_gap = None
    self.avh_req_same_payload = False
    self.done = False
    self.followup = None

  def observe_configured(self, configured, now):
    continuous = self.configured_last is not None and 0 <= now - self.configured_last <= .15
    if not configured or not continuous:
      self.configured_since = None
      if self.followup is not None:
        self.done = True
    if configured:
      if self.configured_since is None:
        self.configured_since = now
      self.configured_last = now
    else:
      self.configured_last = None

  def configured(self, now):
    return (self.configured_since is not None and self.configured_last is not None and
            0 <= now - self.configured_last <= .15 and now - self.configured_since >= 1.)

  def update(self, now, frames, enabled, can_valid, controls_active):
    if self.started is None:
      self.started = now
    if self.last_time is not None and now < self.last_time:
      self.done = True
    self.last_time = now
    if self.done:
      return []
    if now - self.started > 30 or controls_active:
      self.done = True
      return []

    for address, (timestamp, data) in frames.items():
      if address not in INPUTS or timestamp <= 0:
        continue
      previous = self.frames.get(address)
      if previous and timestamp == previous[0]:
        if data != previous[1]:
          self.done = True
          return []
        continue
      if len(data) != 8 or checksum(address, data) != data[0] or timestamp > now or (previous and timestamp < previous[0]):
        self.done = True
        return []
      if (address == AVH_REQUEST and data[2] & 3) or (address == AVH_STATUS and data[5] & 0x20) or \
         (address == 0x48 and data[3] != 4) or (address == 0x40 and data[4]) or \
         (address == 0x13A and any((int.from_bytes(data, 'little') >> bit) & 0x1FFF for bit in (12, 25, 38, 51))):
        self.done = True
        return []
      if previous and (data[1] & 15) == (previous[1][1] & 15):
        if data != previous[1]:
          self.done = True
          return []
        continue  # duplicate counters cannot refresh freshness
      # Controller snapshots can skip 50/100 Hz samples between updates. Panda
      # checks their full counter stream; require consecutive head-unit frames here.
      sequential = bool(previous and (address not in (AVH_REQUEST, AVH_STATUS) or
                                     (data[1] & 15) == ((previous[1][1] + 1) & 15)))
      if address == AVH_REQUEST and previous is not None and timestamp > previous[0]:
        self.avh_req_gap = timestamp - previous[0]
        self.avh_req_same_payload = data[2:] == previous[1][2:]
      self.frames[address] = (timestamp, data, sequential)

    fresh = all(a in self.frames and self.frames[a][2] and
                0 <= now - self.frames[a][0] <= (1.5 if a == AVH_REQUEST else 0.3) for a in INPUTS)
    if not enabled or not can_valid or not fresh:
      self.stable_since = None
      if self.followup is not None:
        self.done = True
      return []
    throttle = self.frames[0x40][1]
    rpm = int.from_bytes(throttle[2:4], 'little') & 0x1FFF
    if rpm < 400 or not self.frames[0x174][1][2] & 8:
      self.stable_since = None
      if self.followup is not None:
        self.done = True
      return []
    if self.stable_since is None:
      self.stable_since = now
    if self.followup is not None:
      sent, timestamp, template, step = self.followup
      if now - sent > 0.075 or self.frames[AVH_REQUEST][0] != timestamp:
        self.done = True
      elif now - sent >= 0.05:
        next_step = step + 1
        if next_step == 4:
          self.done = True
          self.followup = None
        else:
          self.followup = (now, timestamp, template, next_step)
        return [avh_request(template, next_step)]
      return []
    if (now - self.started < 10 or now - self.stable_since < 3 or
        (self.configured_since is not None and now - self.configured_since < 10)):
      return []
    timestamp, template, _ = self.frames[AVH_REQUEST]
    if now - timestamp > 0.010 or self.avh_req_gap is None or self.avh_req_gap < 0.80 or not self.avh_req_same_payload:
      return []
    self.followup = (now, timestamp, template, 1)
    return [avh_request(template, 1)]


def eligible(cp):
  from opendbc.car.subaru.stock_aol import qualified
  from opendbc.car.subaru.values import CAR, FROGGER_ANGLE_SIBLINGS
  return bool(qualified(cp) and cp.carFingerprint in FROGGER_ANGLE_SIBLINGS | {CAR.SUBARU_LEGACY_2025})


def selected(cp):
  from opendbc.car.subaru.values import SubaruFlags
  return bool(eligible(cp) and cp.flags & SubaruFlags.AVH_STARTUP and cp.safetyConfigs[0].safetyParam in (1041, 1169, 1425, 3089))


def prepare(cp, enabled):
  from opendbc.car.subaru.values import SubaruFlags, SubaruSafetyFlags
  if enabled and eligible(cp):
    cp.flags |= int(SubaruFlags.AVH_STARTUP)
    cp.safetyConfigs[0].safetyParam |= int(SubaruSafetyFlags.AVH_STARTUP)
  return cp


class AvhSources:
  def __init__(self):
    self.frames = {}
    self.sequential = {}
    self.source_ns = {}
    self.blocked = False

  def observe(self, packets, *, boot_ns, mono_ns):
    for stamp, frames in packets:
      for address, data, bus in frames:
        if address not in INPUTS or bus != 1:
          continue
        data = bytes(data)
        lease = 1_500_000_000 if address == AVH_REQUEST else 300_000_000
        if (len(data) != 8 or stamp <= 0 or not 0 <= boot_ns - stamp <= lease or checksum(address, data) != data[0]):
          self.blocked = True
          continue
        source = mono_ns - (boot_ns - stamp)
        previous = self.frames.get(address)
        counter = data[1] & 15
        if previous is not None:
          if counter == previous[1][1] & 15:
            if data != previous[1]:
              self.blocked = True
            continue
          prev_ns = self.source_ns[address]
          if source < prev_ns or (source == prev_ns and counter != ((previous[1][1] + 1) & 15)):
            self.blocked = True
            continue
        self.sequential[address] = previous is not None and counter == ((previous[1][1] + 1) & 15)
        self.source_ns[address] = source
        self.frames[address] = source / 1e9, data

  def fresh(self, now):
    return not self.blocked and all(a in self.frames and self.sequential.get(a, False) and
                                   0 <= now - self.frames[a][0] <= (1.5 if a == AVH_REQUEST else .3) for a in INPUTS)
