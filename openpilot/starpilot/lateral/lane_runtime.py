"""Saved lane-centering preferences and fresh host authority, opt-in at startup."""

import math
import os
from pathlib import Path

from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.realtime import DT_CTRL
from openpilot.common.params import UnknownKeyName
from openpilot.starpilot.lateral.lane_centering import ControlMode, LaneCenteringRequest, LaneCenteringSettings, STRENGTH_BASELINE, STRENGTH_MAX
from openpilot.starpilot.feature_capabilities import lateral_supported


def read_settings(params) -> LaneCenteringSettings:
  def raw(key, default):
    try:
      return Path(params.get_param_path(key)).read_bytes()
    except FileNotFoundError:
      return default

  def boolean(key, default):
    value = raw(key, default)
    if value not in (b'0', b'1'):
      raise ValueError('Invalid lane preference')
    return value == b'1'

  try:
    enabled = boolean('LaneCentering', b'0')
    pause = boolean('LaneCenteringPauseOnSignal', b'1')
    offset = float(raw('LaneCenterOffset', b'0.0'))
    authority = float(raw('LaneCenteringE2EAuthority', b'0.5'))
    try:
      strength = float(raw('LaneCenteringStrength', str(STRENGTH_BASELINE).encode()))
    except UnknownKeyName:
      strength = STRENGTH_BASELINE
    if not math.isfinite(strength) or not 0.5 <= strength <= STRENGTH_MAX:
      raise ValueError('Invalid lane strength')
    if not math.isfinite(offset) or not -0.3 <= offset <= 0.3 or not math.isfinite(authority) or not 0 <= authority <= 1:
      raise ValueError('Invalid lane tuning')
    return LaneCenteringSettings(enabled, offset, authority, pause, strength)
  except (OSError, ValueError):
    return LaneCenteringSettings()


def runtime_supported(CP) -> bool:
  """Keep the preference reader available for every configured steering owner."""
  return os.getenv('LANE_CENTERING_REPLAY_RUNTIME') == '1' or lateral_supported(CP)


class LaneCenteringHost:
  REFRESH_NS = 1_000_000_000
  MAX_TICK_NS = 50_000_000
  REQUIRED = ('carState', 'selfdriveState', 'modelV2', 'vehicleParameters')
  FREQUENCY_CHECKED = ('carState', 'modelV2', 'vehicleParameters')

  def __init__(self, params):
    self.params = params
    self.settings = LaneCenteringSettings()
    self.last_refresh_ns: int | None = None
    self.last_tick_ns: int | None = None
    self.last_refusal = 'inactive'

  def inputs_need_refresh(self, sm, *, now_ns: int) -> bool:
    """Leave time to receive queued state before checking its original age."""
    return self.settings.enabled and any(
      0 < int(sm.logMonoTime[name]) < now_ns - 15_000_000 for name in ('carState', 'selfdriveState')
    )

  def sample(self, sm, *, now_ns: int, lateral_active: bool, longitudinal_active: bool) -> LaneCenteringRequest | None:
    self.last_refusal = 'inactive'
    previous = self.last_tick_ns
    self.last_tick_ns = now_ns
    discontinuity = previous is not None and not 0 < now_ns - previous <= self.MAX_TICK_NS
    if now_ns <= 0:
      self.settings = LaneCenteringSettings()
      self.last_refresh_ns = None
      self.last_refusal = 'host_clock'
      return None
    if self.last_refresh_ns is None or now_ns < self.last_refresh_ns or now_ns - self.last_refresh_ns >= self.REFRESH_NS:
      self.settings = read_settings(self.params)
      self.last_refresh_ns = now_ns
    if not self.settings.enabled:
      self.last_refusal = 'host_disabled'
      return None
    if not sm['carState'].canValid:
      self.last_refusal = 'host_can_invalid'
      return None
    if sm['carState'].canTimeout:
      self.last_refusal = 'host_can_timeout'
      return None
    # The polled subscriber's mean frequency measures this consumer loop.
    # Its source still requires a live, valid snapshot within the bound below.
    if not (sm.all_checks(list(self.FREQUENCY_CHECKED)) and
            sm.all_alive(['selfdriveState']) and sm.all_valid(['selfdriveState'])):
      self.last_refusal = self._unhealthy_reason(sm)
      return None
    for name in self.REQUIRED:
      stamp = int(sm.logMonoTime[name])
      if stamp <= 0:
        self.last_refusal = f'host_{name}_missing_time'
        return None
      if not 0 <= now_ns - stamp <= int(2.5e9 / SERVICE_LIST[name].frequency):
        self.last_refusal = f'host_{name}_future' if stamp > now_ns else f'host_{name}_stale'
        return None
    mode = (ControlMode.COMBINED if longitudinal_active else ControlMode.LATERAL_ONLY) if lateral_active else (
      ControlMode.LONGITUDINAL_ONLY if longitudinal_active else ControlMode.OFF)
    dt = DT_CTRL if previous is None or discontinuity else (now_ns - previous) / 1e9
    return LaneCenteringRequest(mode, self.settings, dt, discontinuity)

  def _unhealthy_reason(self, sm) -> str:
    # Reuse the subscriber checks so their ignored-service policy stays intact.
    for method, reason in (('all_alive', 'not_alive'), ('all_freq_ok', 'frequency'), ('all_valid', 'invalid')):
      check = getattr(sm, method, None)
      if check is not None:
        names = self.FREQUENCY_CHECKED if method == 'all_freq_ok' else self.REQUIRED
        for name in names:
          if not check([name]):
            return f'host_{name}_{reason}'
    return 'host_checks'
