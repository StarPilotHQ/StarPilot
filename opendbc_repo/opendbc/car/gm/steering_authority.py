from opendbc.car import structs
from opendbc.car.config_cache import cached_config
from opendbc.car.gm.values import (GMFlags, camera_acc_pedal_profile, is_volt_ascm_longitudinal,
                                   is_volt_ascm_stock, is_volt_sdgm_accepted_envelope)

# PSCMStatus is expected at 10 Hz. Three periods bound first acquisition and missing feedback.
EPS_STATUS_TIMEOUT_NS = 300_000_000
STEERING_COMMAND_TIMEOUT_NS = 100_000_000
ButtonType = structs.CarState.ButtonEvent.Type


@cached_config
def monitored_profile(CP: structs.CarParams) -> bool:
  return ((is_volt_ascm_longitudinal(CP) or is_volt_ascm_stock(CP) or is_volt_sdgm_accepted_envelope(CP)) and
          camera_acc_pedal_profile(CP) is None and
          not CP.flags & GMFlags.PEDAL_LONG)


class SteeringAuthority:
  def __init__(self, CP: structs.CarParams):
    self.enabled = monitored_profile(CP)
    self.min_speed = CP.minSteerSpeed
    self.latched = False
    self.command_active = False
    self.neutral_since_ns = 0
    self.seen_active = False
    self.last_active_ns = 0
    self.active_since_ns = 0
    self.command_edge_ns = 0
    self.acquire_since_ns = 0
    self.status_ns = 0
    self.status = -1
    self.bad_since_ns = 0
    self.bad_samples = 0
    self.last_sample_ns = 0
    self.disable_ns = 0
    self.neutral_ns = 0
    self.main_off = False
    self.source_ns: dict[int, int] = {}

  def observe(self, can_packets):
    if not self.enabled:
      return
    self.source_ns.clear()
    malformed = any(bus == 0 and address == 0x184 and (stamp <= 0 or len(data) != 8)
                    for stamp, packets in can_packets for address, data, bus in packets)
    lengths = {0xC9: 8, 0xBE: 6, 0xBD: 7, 0x1E1: 7}
    invalid = {address for stamp, packets in can_packets for address, data, bus in packets
               if bus == 0 and address in lengths and (stamp <= 0 or len(data) != lengths[address])}
    for address in invalid:
      self.source_ns.pop(address, None)
    for stamp, packets in can_packets:
      for address, data, bus in packets:
        if bus != 0 or stamp <= 0:
          continue
        if address in lengths and address not in invalid:
          self.source_ns[address] = max(stamp, self.source_ns.get(address, 0))
        if address == 0x184 and stamp > self.status_ns:
          if self.active_since_ns and stamp - max(self.status_ns, self.active_since_ns) > EPS_STATUS_TIMEOUT_NS:
            self.latched = True
          self.status_ns = stamp
          self.status = -1 if malformed else (data[0] >> 3) & 7

  def command(self, now_ns: int, active: bool):
    if not self.enabled:
      return
    if not active and self.command_active:
      self.neutral_since_ns = now_ns
    if active:
      if not self.command_active:
        self.command_edge_ns = now_ns
        # A measured inactive response to emitted neutral requires active acquisition.
        # Retain episode/feed history, and never renew an acquisition on brief edges.
        if (not self.acquire_since_ns and self.neutral_since_ns and
            self.neutral_since_ns < self.status_ns <= now_ns and
            now_ns - self.status_ns <= EPS_STATUS_TIMEOUT_NS and self.status == 0):
          self.acquire_since_ns = now_ns
      # A sustained emitted neutral with healthy inactive feedback is a new acquisition.
      # Brief withdrawals retain the confirmed episode; a fault latch never clears here.
      if (not self.latched and self.neutral_since_ns and
          now_ns - self.neutral_since_ns >= EPS_STATUS_TIMEOUT_NS and
          0 < self.status_ns <= now_ns and now_ns - self.status_ns <= EPS_STATUS_TIMEOUT_NS and
          self.status in (0, 1)):
        self.seen_active = False
        self.active_since_ns = 0
        self.acquire_since_ns = now_ns
        self.bad_since_ns = self.bad_samples = 0
        self.last_sample_ns = self.status_ns
      self.neutral_since_ns = 0
    self.command_active = active
    if active:
      self.last_active_ns = now_ns
      if not self.active_since_ns:
        self.active_since_ns = now_ns
    elif self.disable_ns and now_ns > self.disable_ns:
      self.neutral_ns = now_ns

  def update(self, CS: structs.CarState, now_ns: int):
    if not self.enabled:
      return
    healthy = CS.canValid and not CS.canTimeout

    def fresh(address):
      stamp = self.source_ns.get(address, 0)
      return 0 < stamp <= now_ns and now_ns - stamp <= STEERING_COMMAND_TIMEOUT_NS

    main_fresh = fresh(0xC9)
    buttons_fresh = fresh(0x1E1)
    pedal_fresh = (CS.brakePressed and (main_fresh or fresh(0xBE)) or
                   CS.regenBraking and fresh(0xBD))
    cancel = buttons_fresh and any(b.type == ButtonType.cancel and b.pressed for b in CS.buttonEvents)
    if self.latched and healthy:
      if pedal_fresh or cancel or main_fresh and not CS.cruiseState.available:
        if not self.disable_ns:
          self.disable_ns = now_ns
        self.main_off |= main_fresh and not CS.cruiseState.available
      physical_enable = buttons_fresh and self.source_ns[0x1E1] > self.neutral_ns and CS.buttonEnable
      main_enable = self.main_off and main_fresh and self.source_ns[0xC9] > self.neutral_ns and CS.cruiseState.available
      if (self.neutral_ns > self.disable_ns > 0 and (physical_enable or main_enable) and
          CS.cruiseState.available and not CS.brakePressed and not CS.regenBraking):
        self.latched = False
        self.disable_ns = self.neutral_ns = 0
        self.main_off = False
        self.last_active_ns = self.active_since_ns = self.acquire_since_ns = 0
        self.seen_active = False
        self.bad_since_ns = self.bad_samples = 0

    deliberate_pause = (pedal_fresh or cancel or main_fresh and not CS.cruiseState.available or
                        CS.standstill or CS.vEgo < self.min_speed or CS.steeringPressed or
                        self.status in (2, 3) and 0 < self.status_ns <= now_ns and
                        now_ns - self.status_ns <= EPS_STATUS_TIMEOUT_NS)
    if healthy and deliberate_pause and not self.latched:
      self.seen_active = False
      self.command_active = False
      self.last_active_ns = self.active_since_ns = self.acquire_since_ns = 0
      self.bad_since_ns = self.bad_samples = 0
      self.last_sample_ns = self.status_ns
    # A missing EPS feed must retain a fault even after host health gates emit neutral.
    if (self.active_since_ns and not self.latched and
        now_ns - max(self.status_ns, self.active_since_ns) > EPS_STATUS_TIMEOUT_NS):
      self.latched = True

    recent_command = 0 < self.last_active_ns <= now_ns and now_ns - self.last_active_ns <= STEERING_COMMAND_TIMEOUT_NS
    eligible = ((healthy or self.status == -1) and self.command_active and recent_command and CS.cruiseState.available and
                not CS.brakePressed and not CS.regenBraking and not cancel and
                not CS.standstill and CS.vEgo >= self.min_speed and not CS.steeringPressed)
    fresh_status = 0 < self.status_ns <= now_ns and now_ns - self.status_ns <= EPS_STATUS_TIMEOUT_NS
    if eligible and fresh_status and self.status_ns > max(self.active_since_ns, self.command_edge_ns) and self.status_ns != self.last_sample_ns:
      self.last_sample_ns = self.status_ns
      if self.status == 1:
        self.acquire_since_ns = 0
        self.seen_active = True
        self.bad_since_ns = self.bad_samples = 0
      elif self.status not in (2, 3):
        if not self.bad_since_ns:
          self.bad_since_ns = self.status_ns
        self.bad_samples += 1
        if self.seen_active and (self.status != 0 or not self.acquire_since_ns):
          self.latched = True
      else:
        self.bad_since_ns = self.bad_samples = 0
    # A confirmed neutral acquisition has a fixed deadline, even if repeated edges
    # place every inactive sample before the latest active command.
    if eligible and self.acquire_since_ns and now_ns - self.acquire_since_ns >= EPS_STATUS_TIMEOUT_NS:
      self.latched = True
    if (eligible and self.bad_samples >= 2 and self.bad_since_ns and
        now_ns - max(self.active_since_ns, self.acquire_since_ns) >= EPS_STATUS_TIMEOUT_NS):
      self.latched = True
    if not recent_command or CS.steeringPressed or CS.standstill or CS.vEgo < self.min_speed:
      self.bad_since_ns = self.bad_samples = 0
    if self.latched:
      CS.steerFaultTemporary = True
